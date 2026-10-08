"""Real Kafka-to-CLIP indexing; public Go HTTP and service lifespan come later."""

import json
import os
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

import pytest
from confluent_kafka import OFFSET_INVALID, Consumer, Producer, TopicPartition
from test_frame_persistence import persisted
from test_indexing import embedder as embedder
from test_indexing import pytestmark as indexing_marks
from test_job_claims import states
from test_job_processing import uploaded_event as uploaded_event
from test_kafka_integration import kafka_settings as kafka_settings
from test_minio import minio as minio

from framesearch_processor.events import EventValidationError
from framesearch_processor.indexing import VideoIndexer
from framesearch_processor.jobs import JobProcessor, UnfinishedJobError
from framesearch_processor.kafka import UploadConsumer
from framesearch_processor.settings import MODEL_VERSION
from framesearch_processor.worker import IngestionWorker

pytestmark = [
    *indexing_marks,
    pytest.mark.kafka,
    pytest.mark.skipif(
        os.getenv("FRAMESEARCH_KAFKA_TEST") != "1", reason="Enable real Kafka checks"
    ),
]


def envelope(event, *, job_id=None):
    return json.dumps(
        {
            "event_id": str(event.event_id),
            "event_type": "media.uploaded",
            "schema_version": 1,
            "video_id": str(event.video_id),
            "job_id": str(job_id or event.job_id),
            "created_at": event.created_at.isoformat(),
        }
    ).encode()


def publish(settings, event, payloads):
    producer = Producer(
        {"bootstrap.servers": ",".join(settings.brokers), "message.timeout.ms": 10_000}
    )
    failures = []
    for payload in payloads:
        producer.produce(
            settings.topic,
            key=str(event.video_id).encode(),
            value=payload,
            on_delivery=lambda error, message: failures.append(error) if error else None,
        )
    assert producer.flush(15) == 0 and failures == []


def start_worker(database, store, embedder, settings, tmp_path):
    stop = Event()
    jobs = JobProcessor(
        database, VideoIndexer(database, store, embedder, temp_root=tmp_path), stop_event=stop
    )
    worker = IngestionWorker(UploadConsumer(settings), jobs, stop)
    failures = []

    def run():
        try:
            worker.run()
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=run, name="test-real-ingestion")
    thread.start()
    return worker, stop, thread, failures


def observer(settings):
    return Consumer(
        {
            "bootstrap.servers": ",".join(settings.brokers),
            "group.id": settings.group_id,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
        }
    )


def offset(client, settings):
    result = client.committed([TopicPartition(settings.topic, 0)], timeout=10)
    assert len(result) == 1 and result[0].error is None
    return result[0].offset


def wait_for_offset(client, settings, expected, thread, failures):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        assert thread.is_alive(), failures
        if offset(client, settings) == expected:
            return
        sleep(0.1)
    pytest.fail(f"Expected committed offset {expected} within 30 seconds")


def finish(stop, thread, client):
    stop.set()
    thread.join(20)
    client.close()
    assert not thread.is_alive(), "Worker did not finish its bounded test job and close"


def test_real_worker_indexes_then_acknowledges_success_and_duplicate(
    database, uploaded_event, embedder, kafka_settings, tmp_path
):
    event, store, storage, source = uploaded_event
    payload = envelope(event)
    publish(kafka_settings, event, [payload, payload])
    worker, stop, thread, failures = start_worker(
        database, store, embedder, kafka_settings, tmp_path
    )
    client = observer(kafka_settings)
    try:
        wait_for_offset(client, kafka_settings, 2, thread, failures)
        status = states(database, event.video_id, event.job_id)
        assert status["video_status"] == "ready" and status["job_status"] == "completed"
        assert status["attempt_count"] == 3  # Prior fixture count 2 + exactly one claim.
        rows = persisted(database, event.video_id)
        assert [row["timestamp_ms"] for row in rows] == [0, 3000, 6000]
        assert all(
            row["dimensions"] == 512 and row["norm"] == pytest.approx(1, abs=1e-6) for row in rows
        )
        assert all(row["model_version"] == MODEL_VERSION for row in rows)
        for row in rows:
            assert (
                store._client.head_object(Bucket=storage.bucket, Key=row["thumbnail_key"])[
                    "ContentType"
                ]
                == "image/jpeg"
            )
        assert worker.ready.is_set()
        assert list(tmp_path.iterdir()) == [source]
    finally:
        finish(stop, thread, client)
    assert failures == [] and not worker.ready.is_set()


def test_real_corrupt_upload_is_failed_before_offset_commit(
    database, uploaded_event, embedder, kafka_settings, tmp_path
):
    event, store, storage, source = uploaded_event
    data = b"corrupt uploaded MP4"
    store._client.put_object(
        Bucket=storage.bucket,
        Key=f"videos/{event.video_id}/original.mp4",
        Body=data,
        ContentType="video/mp4",
    )
    with database.connection() as connection:
        connection.execute(
            "UPDATE videos SET size_bytes = %s WHERE id = %s", (len(data), event.video_id)
        )
    publish(kafka_settings, event, [envelope(event)])
    worker, stop, thread, failures = start_worker(
        database, store, embedder, kafka_settings, tmp_path
    )
    client = observer(kafka_settings)
    try:
        wait_for_offset(client, kafka_settings, 1, thread, failures)
        status = states(database, event.video_id, event.job_id)
        assert status["video_status"] == status["job_status"] == "failed"
        assert "MP4" in status["processing_error"]
        assert status["last_error"] == status["processing_error"]
        assert persisted(database, event.video_id) == []
        assert list(tmp_path.iterdir()) == [source]
    finally:
        finish(stop, thread, client)
    assert failures == [] and not worker.ready.is_set()


@pytest.mark.parametrize("blocked", ["malformed", "busy", "missing"])
def test_real_unresolved_event_stops_before_later_valid_event_or_offset(
    database, uploaded_event, embedder, kafka_settings, tmp_path, blocked
):
    event, store, _, source = uploaded_event
    expected_status, expected_attempts = "queued", 2
    if blocked == "malformed":
        payload = b"{invalid JSON"
    elif blocked == "busy":
        assert database.claim_job(event.job_id, event.video_id).outcome == "claimed"
        expected_status, expected_attempts = "processing", 3
        payload = envelope(event)
    else:
        payload = envelope(event, job_id=uuid4())
    publish(kafka_settings, event, [payload, envelope(event)])
    worker, stop, thread, failures = start_worker(
        database, store, embedder, kafka_settings, tmp_path
    )
    client = observer(kafka_settings)
    try:
        thread.join(20)
        assert not thread.is_alive(), "Unresolved event must stop the worker"
        assert len(failures) == 1
        assert isinstance(
            failures[0], EventValidationError if blocked == "malformed" else UnfinishedJobError
        )
        assert offset(client, kafka_settings) == OFFSET_INVALID
        status = states(database, event.video_id, event.job_id)
        assert status["video_status"] == status["job_status"] == expected_status
        assert status["attempt_count"] == expected_attempts
        assert persisted(database, event.video_id) == []
        assert list(tmp_path.iterdir()) == [source]
        assert not worker.ready.is_set()
    finally:
        finish(stop, thread, client)
