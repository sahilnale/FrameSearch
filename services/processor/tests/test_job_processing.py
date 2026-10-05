"""Opt-in job policy with real storage, model, media and database components."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_frame_persistence import persisted
from test_indexing import embedder as embedder
from test_indexing import pytestmark as pytestmark
from test_job_claims import states
from test_minio import minio as minio
from test_sampling import make_video

from framesearch_processor.events import MediaUploaded
from framesearch_processor.indexing import VideoIndexer
from framesearch_processor.jobs import JobProcessor
from framesearch_processor.storage import StorageError


@pytest.fixture
def uploaded_event(database, queued_job, minio, tmp_path):
    video_id, job_id, key = queued_job
    store, storage = minio
    source = make_video(tmp_path)
    data = source.read_bytes()
    store._client.put_object(Bucket=storage.bucket, Key=key, Body=data, ContentType="video/mp4")
    with database.connection() as connection:
        connection.execute("UPDATE videos SET size_bytes = %s WHERE id = %s", (len(data), video_id))
    event = MediaUploaded(uuid4(), video_id, job_id, datetime.now(UTC))
    return event, store, storage, source


def test_real_queued_event_completes_and_duplicate_does_not_reindex(
    database, uploaded_event, embedder, tmp_path
):
    event, store, _, source = uploaded_event
    runner = JobProcessor(database, VideoIndexer(database, store, embedder, temp_root=tmp_path))
    assert runner.process(event) == "completed"
    result = database.claim_job(event.job_id, event.video_id)
    assert result.outcome == "terminal"
    rows = persisted(database, event.video_id)
    assert [row["timestamp_ms"] for row in rows] == [0, 3000, 6000]
    before = states(database, event.video_id, event.job_id)
    assert runner.process(event) == "terminal"
    assert states(database, event.video_id, event.job_id) == before
    assert persisted(database, event.video_id) == rows
    assert list(tmp_path.iterdir()) == [source]


def test_real_transient_upload_failure_retries_the_same_claim(
    database, uploaded_event, embedder, tmp_path, monkeypatch
):
    event, store, _, source = uploaded_event
    actual_upload = store.upload_thumbnail
    interrupted = False

    def interrupt_once(video_id, frame, model_version):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise StorageError("test-only interrupted first PUT")
        return actual_upload(video_id, frame, model_version)

    monkeypatch.setattr(store, "upload_thumbnail", interrupt_once)
    runner = JobProcessor(database, VideoIndexer(database, store, embedder, temp_root=tmp_path))
    assert runner.process(event) == "completed"
    assert interrupted
    status = states(database, event.video_id, event.job_id)
    assert status["video_status"] == "ready" and status["job_status"] == "completed"
    assert status["attempt_count"] == 3  # Fixture's prior count 2 + one DB claim.
    assert len(persisted(database, event.video_id)) == 3
    assert list(tmp_path.iterdir()) == [source]


def test_corrupt_real_upload_reaches_durable_failed_state(
    database, queued_job, minio, embedder, tmp_path
):
    video_id, job_id, key = queued_job
    store, storage = minio
    data = b"corrupt uploaded MP4"
    store._client.put_object(Bucket=storage.bucket, Key=key, Body=data, ContentType="video/mp4")
    with database.connection() as connection:
        connection.execute("UPDATE videos SET size_bytes = %s WHERE id = %s", (len(data), video_id))
    event = MediaUploaded(uuid4(), video_id, job_id, datetime.now(UTC))
    runner = JobProcessor(database, VideoIndexer(database, store, embedder, temp_root=tmp_path))
    assert runner.process(event) == "failed"
    status = states(database, video_id, job_id)
    assert status["video_status"] == status["job_status"] == "failed"
    assert (
        "MP4" in status["processing_error"] and status["last_error"] == status["processing_error"]
    )
    assert status["attempt_count"] == 3
    assert persisted(database, video_id) == [] and list(tmp_path.iterdir()) == []
    assert runner.process(event) == "terminal"
