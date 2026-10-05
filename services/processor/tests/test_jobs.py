"""Policy doubles are confined to tests; live indexing has separate real checks."""

from datetime import UTC, datetime
from threading import Event
from unittest.mock import Mock, call
from uuid import uuid4

import pytest

from framesearch_processor.database import (
    ClaimedJob,
    ClaimResult,
    Database,
    DatabaseError,
    JobStateError,
)
from framesearch_processor.events import MediaUploaded
from framesearch_processor.indexing import VideoIndexer
from framesearch_processor.jobs import JobProcessor, ProcessingStopped, UnfinishedJobError
from framesearch_processor.media import InvalidVideo, MediaToolError
from framesearch_processor.storage import StorageError


@pytest.fixture
def policy():
    event = MediaUploaded(uuid4(), uuid4(), uuid4(), datetime.now(UTC))
    claim = ClaimedJob(event.job_id, event.video_id, "source.mp4", 1234, 1)
    database = Mock(spec=Database)
    database.claim_job.return_value = ClaimResult("claimed", claim)
    indexer = Mock(spec=VideoIndexer)
    stop = Event()
    stop.wait = Mock(return_value=False)
    runner = JobProcessor(database, indexer, stop_event=stop)
    return runner, database, indexer, stop, event, claim


def test_success_returns_only_after_indexing_completes(policy):
    runner, database, indexer, stop, event, claim = policy
    assert runner.process(event) == "completed"
    database.claim_job.assert_called_once_with(event.job_id, event.video_id)
    indexer.index.assert_called_once_with(claim)
    database.fail_job.assert_not_called()
    stop.wait.assert_not_called()


def test_durable_duplicate_does_not_reindex(policy):
    runner, database, indexer, _, event, _ = policy
    database.claim_job.return_value = ClaimResult("terminal")
    assert runner.process(event) == "terminal"
    indexer.index.assert_not_called()
    database.fail_job.assert_not_called()


@pytest.mark.parametrize("outcome", ["busy", "missing", "claimed"])
def test_nonterminal_or_missing_receipt_cannot_be_acknowledged(policy, outcome):
    runner, database, indexer, _, event, _ = policy
    database.claim_job.return_value = ClaimResult(outcome)
    with pytest.raises(UnfinishedJobError, match=outcome):
        runner.process(event)
    indexer.index.assert_not_called()
    database.fail_job.assert_not_called()


@pytest.mark.parametrize("error", [StorageError, MediaToolError, RuntimeError, DatabaseError])
def test_transient_attempts_reuse_the_receipt_with_bounded_backoff(policy, error):
    runner, database, indexer, stop, event, claim = policy
    indexer.index.side_effect = [error("temporary failure"), error("temporary failure"), None]
    assert runner.process(event) == "completed"
    assert indexer.index.call_args_list == [call(claim)] * 3
    assert stop.wait.call_args_list == [call(1), call(2)]
    database.claim_job.assert_called_once()
    database.fail_job.assert_not_called()


@pytest.mark.parametrize("error", [StorageError, MediaToolError, RuntimeError, OSError, ValueError])
def test_exhausted_infrastructure_attempts_record_a_bounded_public_failure(policy, error):
    runner, database, indexer, stop, event, claim = policy
    indexer.index.side_effect = error("private exception details must not become public")
    assert runner.process(event) == "failed"
    assert indexer.index.call_count == 3 and stop.wait.call_count == 2
    database.fail_job.assert_called_once()
    written_claim, reason = database.fail_job.call_args.args
    assert written_claim == claim and 1 <= len(reason) <= 1000
    assert "3 attempts" in reason and "retry" in reason
    assert "private exception" not in reason


@pytest.mark.parametrize("message", ["", "Corrupt MP4; export again", "x" * 1100])
def test_invalid_media_records_failure_without_retry(policy, message):
    runner, database, indexer, stop, event, claim = policy
    indexer.index.side_effect = InvalidVideo(message)
    assert runner.process(event) == "failed"
    indexer.index.assert_called_once_with(claim)
    stop.wait.assert_not_called()
    reason = database.fail_job.call_args.args[1]
    assert 1 <= len(reason) <= 1000
    if message:
        assert reason == message[:1000]


def test_exhausted_database_failure_cannot_claim_a_terminal_outcome(policy):
    runner, database, indexer, stop, event, _ = policy
    indexer.index.side_effect = DatabaseError("DB commit failed or reply was lost")
    with pytest.raises(DatabaseError):
        runner.process(event)
    assert indexer.index.call_count == 3 and stop.wait.call_count == 2
    database.fail_job.assert_not_called()


def test_stale_or_inconsistent_claim_is_not_retried_or_failed(policy):
    runner, database, indexer, stop, event, _ = policy
    indexer.index.side_effect = JobStateError("stale processing claim")
    with pytest.raises(JobStateError):
        runner.process(event)
    assert indexer.index.call_count == 1
    database.fail_job.assert_not_called()
    stop.wait.assert_not_called()


def test_failed_failure_transaction_cannot_return_failed(policy):
    runner, database, indexer, _, event, _ = policy
    indexer.index.side_effect = InvalidVideo("Invalid MP4")
    database.fail_job.side_effect = DatabaseError("failure commit unavailable")
    with pytest.raises(DatabaseError):
        runner.process(event)


def test_shutdown_before_claim_does_not_change_database_state(policy):
    runner, database, indexer, stop, event, _ = policy
    stop.set()
    with pytest.raises(ProcessingStopped):
        runner.process(event)
    database.claim_job.assert_not_called()
    indexer.index.assert_not_called()
    database.fail_job.assert_not_called()


def test_shutdown_during_backoff_leaves_job_unfinished(policy):
    runner, database, indexer, stop, event, _ = policy
    indexer.index.side_effect = StorageError("temporary interruption")
    stop.wait.return_value = True
    with pytest.raises(ProcessingStopped, match="retry"):
        runner.process(event)
    assert indexer.index.call_count == 1
    database.fail_job.assert_not_called()


def test_shutdown_after_claim_preserves_processing_for_recovery(policy):
    runner, database, indexer, stop, event, claim = policy

    def claim_then_stop(*args):
        stop.set()
        return ClaimResult("claimed", claim)

    database.claim_job.side_effect = claim_then_stop
    with pytest.raises(ProcessingStopped):
        runner.process(event)
    indexer.index.assert_not_called()
    database.fail_job.assert_not_called()


def test_already_durable_completion_can_return_during_shutdown(policy):
    runner, database, indexer, stop, event, _ = policy
    indexer.index.side_effect = lambda claim: stop.set()
    assert runner.process(event) == "completed"
    database.fail_job.assert_not_called()
