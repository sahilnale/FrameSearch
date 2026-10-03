"""Complete real indexing attempts, including interrupted-upload recovery."""

import math
import os

import pytest
from test_frame_persistence import persisted
from test_job_claims import states
from test_minio import minio as minio
from test_sampling import make_video, requires_ffmpeg

from framesearch_processor.embeddings import OpenClipEmbedder
from framesearch_processor.indexing import VideoIndexer
from framesearch_processor.media import InvalidVideo
from framesearch_processor.settings import MODEL_VERSION, Settings
from framesearch_processor.storage import StorageError

pytestmark = [
    pytest.mark.database,
    pytest.mark.minio,
    pytest.mark.real_model,
    requires_ffmpeg,
    pytest.mark.skipif(
        os.getenv("FRAMESEARCH_MINIO_TEST") != "1"
        or os.getenv("FRAMESEARCH_REAL_MODEL_TEST") != "1",
        reason="opt-in real MinIO/model/database indexing",
    ),
]


@pytest.fixture(scope="module")
def embedder():
    return OpenClipEmbedder(Settings.from_env())


@pytest.fixture
def uploaded_video(database, queued_job, minio, tmp_path):
    store, settings = minio
    video_id, job_id, key = queued_job
    source = make_video(tmp_path)
    data = source.read_bytes()
    store._client.put_object(Bucket=settings.bucket, Key=key, Body=data, ContentType="video/mp4")
    with database.connection() as connection:
        connection.execute("UPDATE videos SET size_bytes = %s WHERE id = %s", (len(data), video_id))
    claim = database.claim_job(job_id, video_id).job
    return claim, store, settings, source


def assert_indexed(database, claim):
    status = states(database, claim.video_id, claim.job_id)
    assert status["video_status"] == "ready" and status["job_status"] == "completed"
    assert status["attempt_count"] == claim.attempt_count
    assert status["last_error"] is status["processing_error"] is None
    rows = persisted(database, claim.video_id)
    assert [row["timestamp_ms"] for row in rows] == [0, 3000, 6000]
    assert all(
        row["dimensions"] == 512 and row["norm"] == pytest.approx(1, abs=1e-6) for row in rows
    )
    assert all(row["model_version"] == MODEL_VERSION for row in rows)
    with database.connection() as connection:
        duration = connection.execute(
            "SELECT duration_seconds FROM videos WHERE id = %s", (claim.video_id,)
        ).fetchone()["duration_seconds"]
    assert duration == pytest.approx(6.2)
    return rows


def test_actual_video_indexes_to_ready_and_supports_real_text_vector_query(
    database, uploaded_video, embedder, tmp_path
):
    claim, store, storage, source = uploaded_video
    VideoIndexer(database, store, embedder, temp_root=tmp_path).index(claim)
    rows = assert_indexed(database, claim)
    assert list(tmp_path.iterdir()) == [source]
    assert database.claim_job(claim.job_id, claim.video_id).outcome == "terminal"
    assert persisted(database, claim.video_id) == rows
    for row in rows:
        info = store._client.head_object(Bucket=storage.bucket, Key=row["thumbnail_key"])
        assert info["ContentType"] == "image/jpeg"
        assert info["Metadata"]["timestamp-ms"] == str(row["timestamp_ms"])
    query = "[" + ",".join(map(str, embedder.embed_text("a colorful video test pattern"))) + "]"
    with database.connection() as connection:
        matches = connection.execute(
            """SELECT f.id, 1 - (f.embedding <=> %s::vector) AS similarity
                 FROM video_frames f JOIN videos v ON v.id = f.video_id
                 WHERE v.id = %s AND v.status = 'ready' AND f.model_version = %s
                 ORDER BY f.embedding <=> %s::vector, f.id""",
            (query, claim.video_id, MODEL_VERSION, query),
        ).fetchall()
    assert {match["id"] for match in matches} == {row["id"] for row in rows}
    assert all(math.isfinite(match["similarity"]) for match in matches)


def test_interrupted_thumbnail_upload_stays_processing_and_retries_cleanly(
    database, uploaded_video, embedder, tmp_path, monkeypatch
):
    claim, store, storage, source = uploaded_video
    indexer = VideoIndexer(database, store, embedder, temp_root=tmp_path)
    actual_upload = store.upload_thumbnail

    def interrupt(video_id, frame, model_version):
        if frame.timestamp_ms == 3000:
            raise StorageError("test-only interruption before the second thumbnail PUT")
        return actual_upload(video_id, frame, model_version)

    with monkeypatch.context() as patch:
        patch.setattr(store, "upload_thumbnail", interrupt)
        with pytest.raises(StorageError, match="interruption"):
            indexer.index(claim)
    status = states(database, claim.video_id, claim.job_id)
    assert status["video_status"] == status["job_status"] == "processing"
    assert persisted(database, claim.video_id) == []
    assert list(tmp_path.iterdir()) == [source]
    prefix = f"thumbnails/{claim.video_id}/"
    assert len(store._client.list_objects_v2(Bucket=storage.bucket, Prefix=prefix)["Contents"]) == 1
    indexer.index(claim)
    assert_indexed(database, claim)
    assert len(store._client.list_objects_v2(Bucket=storage.bucket, Prefix=prefix)["Contents"]) == 3
    assert list(tmp_path.iterdir()) == [source]
    assert (
        store._client.head_object(Bucket=storage.bucket, Key=claim.object_key)["ContentLength"]
        == claim.size_bytes
    )


def test_corrupt_uploaded_mp4_is_rejected_and_caller_can_record_terminal_failure(
    database, queued_job, minio, embedder, tmp_path
):
    video_id, job_id, key = queued_job
    store, storage = minio
    data = b"this is not an MP4 file"
    store._client.put_object(Bucket=storage.bucket, Key=key, Body=data, ContentType="video/mp4")
    with database.connection() as connection:
        connection.execute("UPDATE videos SET size_bytes = %s WHERE id = %s", (len(data), video_id))
    claim = database.claim_job(job_id, video_id).job
    with pytest.raises(InvalidVideo) as failure:
        VideoIndexer(database, store, embedder, temp_root=tmp_path).index(claim)
    assert list(tmp_path.iterdir()) == []
    assert persisted(database, video_id) == []
    status = states(database, video_id, job_id)
    assert status["video_status"] == status["job_status"] == "processing"
    database.fail_job(claim, str(failure.value))
    status = states(database, video_id, job_id)
    assert status["video_status"] == status["job_status"] == "failed"
    assert "MP4" in status["processing_error"]
