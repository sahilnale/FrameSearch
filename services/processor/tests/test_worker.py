import json
from threading import Event
from unittest.mock import Mock, call
from uuid import uuid4

import pytest
from confluent_kafka import Message

from framesearch_processor.database import DatabaseError
from framesearch_processor.events import EventValidationError, parse_media_uploaded
from framesearch_processor.jobs import JobProcessor, ProcessingStopped, UnfinishedJobError
from framesearch_processor.kafka import BrokerError, UploadConsumer
from framesearch_processor.worker import IngestionWorker


@pytest.fixture
def pipeline():
    consumer = Mock(spec=UploadConsumer)
    jobs = Mock(spec=JobProcessor)
    stop = Event()
    worker = IngestionWorker(consumer, jobs, stop)
    video_id = str(uuid4())
    value = json.dumps(
        {
            "event_id": str(uuid4()),
            "event_type": "media.uploaded",
            "schema_version": 1,
            "video_id": video_id,
            "job_id": str(uuid4()),
            "created_at": "2026-01-01T00:00:00Z",
        }
    ).encode()
    message = Mock(spec=Message)
    message.value.return_value = value
    message.key.return_value = video_id.encode()
    consumer.poll.return_value = message
    consumer.acknowledge.side_effect = lambda _: stop.set()
    jobs.process.return_value = "completed"
    return worker, consumer, jobs, stop, message


@pytest.mark.parametrize("outcome", ["completed", "failed", "terminal"])
def test_only_durable_outcome_is_acknowledged_in_order(pipeline, outcome):
    worker, consumer, jobs, _, message = pipeline
    parent = Mock()
    parent.attach_mock(consumer, "consumer")
    parent.attach_mock(jobs, "jobs")
    event = parse_media_uploaded(message.value(), message.key())

    def process(event):
        assert worker.ready.is_set()
        return outcome

    jobs.process.side_effect = process
    worker.run()
    assert parent.method_calls == [
        call.consumer.start(),
        call.consumer.poll(),
        call.jobs.process(event),
        call.consumer.acknowledge(message),
        call.consumer.close(),
    ]
    assert not worker.ready.is_set()


def test_idle_poll_keeps_worker_ready_until_shutdown(pipeline):
    worker, consumer, jobs, stop, _ = pipeline

    def idle():
        assert worker.ready.is_set()
        stop.set()
        return None

    consumer.poll.side_effect = idle
    worker.run()
    jobs.process.assert_not_called()
    consumer.acknowledge.assert_not_called()
    consumer.close.assert_called_once()


def test_malformed_event_stops_before_any_job_or_offset(pipeline):
    worker, consumer, jobs, _, message = pipeline
    message.value.return_value = b"{invalid JSON"
    with pytest.raises(EventValidationError):
        worker.run()
    jobs.process.assert_not_called()
    consumer.acknowledge.assert_not_called()
    assert consumer.poll.call_count == 1
    assert not worker.ready.is_set()
    consumer.close.assert_called_once()


@pytest.mark.parametrize("failure", [UnfinishedJobError, DatabaseError, RuntimeError])
def test_unresolved_job_stops_without_acknowledgment(pipeline, failure):
    worker, consumer, jobs, _, _ = pipeline
    jobs.process.side_effect = failure("job is not durably terminal")
    with pytest.raises(failure):
        worker.run()
    consumer.acknowledge.assert_not_called()
    assert consumer.poll.call_count == 1 and not worker.ready.is_set()
    consumer.close.assert_called_once()


def test_unknown_outcome_cannot_commit_over_pending_work(pipeline):
    worker, consumer, jobs, _, _ = pipeline
    jobs.process.return_value = "busy"
    with pytest.raises(UnfinishedJobError):
        worker.run()
    consumer.acknowledge.assert_not_called()


def test_commit_failure_stops_before_reading_another_event(pipeline):
    worker, consumer, jobs, _, _ = pipeline
    consumer.acknowledge.side_effect = BrokerError("offset commit failed")
    with pytest.raises(BrokerError):
        worker.run()
    assert jobs.process.call_count == consumer.poll.call_count == 1
    consumer.close.assert_called_once()
    assert not worker.ready.is_set()


def test_startup_failure_keeps_readiness_unset_and_closes(pipeline):
    worker, consumer, jobs, _, _ = pipeline
    consumer.start.side_effect = BrokerError("unavailable topic")
    with pytest.raises(BrokerError):
        worker.run()
    assert not worker.ready.is_set()
    consumer.poll.assert_not_called()
    jobs.process.assert_not_called()
    consumer.close.assert_called_once()


def test_shutdown_before_start_only_closes(pipeline):
    worker, consumer, jobs, stop, _ = pipeline
    stop.set()
    worker.run()
    consumer.start.assert_not_called()
    jobs.process.assert_not_called()
    consumer.close.assert_called_once()


def test_shutdown_during_poll_does_not_claim_or_acknowledge(pipeline):
    worker, consumer, jobs, stop, message = pipeline

    def poll_then_stop():
        stop.set()
        return message

    consumer.poll.side_effect = poll_then_stop
    worker.run()
    jobs.process.assert_not_called()
    consumer.acknowledge.assert_not_called()
    consumer.close.assert_called_once()


def test_shutdown_during_job_backoff_leaves_offset_unacknowledged(pipeline):
    worker, consumer, jobs, stop, _ = pipeline

    def stop_job(event):
        stop.set()
        raise ProcessingStopped("interrupted retry")

    jobs.process.side_effect = stop_job
    worker.run()
    consumer.acknowledge.assert_not_called()
    consumer.close.assert_called_once()


def test_unexpected_processing_stopped_is_reported(pipeline):
    worker, consumer, jobs, _, _ = pipeline
    jobs.process.side_effect = ProcessingStopped("unexpected stop")
    with pytest.raises(ProcessingStopped):
        worker.run()
    consumer.acknowledge.assert_not_called()


def test_close_failure_does_not_hide_the_original_job_failure(pipeline):
    worker, consumer, jobs, _, _ = pipeline
    jobs.process.side_effect = DatabaseError("uncertain commit")
    consumer.close.side_effect = BrokerError("close failed")
    with pytest.raises(DatabaseError, match="uncertain"):
        worker.run()
    assert not worker.ready.is_set()


def test_close_failure_after_normal_shutdown_is_reported(pipeline):
    worker, consumer, _, stop, _ = pipeline
    stop.set()
    consumer.close.side_effect = BrokerError("close failed")
    with pytest.raises(BrokerError, match="close"):
        worker.run()
