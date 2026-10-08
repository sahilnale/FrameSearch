"""Retry-safe frame writes against the shared schema and real media pipeline."""

import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from psycopg import sql
from test_job_claims import states
from test_minio import minio as minio
from test_sampling import make_video, requires_ffmpeg

from framesearch_processor.database import (
    ClaimedJob,
    Database,
    DatabaseError,
    FrameRecord,
    JobStateError,
)
from framesearch_processor.settings import MODEL_VERSION, DatabaseSettings
from framesearch_processor.storage import thumbnail_key

pytestmark = pytest.mark.database


def record(claim, timestamp_ms=0, *, embedding=None):
    # Unit vectors are database test inputs, never application/model substitutes.
    return FrameRecord(
        timestamp_ms,
        thumbnail_key(claim.video_id, timestamp_ms),
        [1.0] + [0.0] * 511 if embedding is None else embedding,
    )


@pytest.fixture
def claimed_job(database, queued_job):
    video_id, job_id, _ = queued_job
    return database.claim_job(job_id, video_id).job


def persisted(database, video_id):
    with database.connection() as connection:
        return connection.execute(
            """SELECT id, video_id, timestamp_ms, thumbnail_key, model_version, created_at,
                      embedding::text AS vector, vector_dims(embedding) AS dimensions,
                      vector_norm(embedding) AS norm
                 FROM video_frames WHERE video_id = %s ORDER BY model_version, timestamp_ms""",
            (video_id,),
        ).fetchall()


@pytest.fixture
def unconnected():
    claim = ClaimedJob(uuid4(), uuid4(), "source.mp4", 1234, 1)
    return Database(DatabaseSettings("postgresql://localhost/unused")), claim


@pytest.mark.parametrize(
    "embedding",
    [
        [],
        [1.0] * 511,
        [1.0] * 513,
        "1" * 512,
        b"1" * 512,
        [float("nan")] + [0.0] * 511,
        [float("inf")] + [0.0] * 511,
        [float("-inf")] + [0.0] * 511,
        [True] + [0.0] * 511,
        ["1"] + [0.0] * 511,
        [10**400] + [0.0] * 511,
        [0.0] * 512,
        [2.0] + [0.0] * 511,
    ],
    ids=[
        "empty",
        "short",
        "long",
        "text",
        "bytes",
        "nan",
        "inf",
        "negative-inf",
        "boolean",
        "string-coordinate",
        "overflow",
        "zero",
        "not-normalized",
    ],
)
def test_invalid_vectors_are_rejected_before_connecting(unconnected, embedding):
    database, claim = unconnected
    with pytest.raises(ValueError, match="embedding"):
        database.upsert_frames(claim, [record(claim, embedding=embedding)])


@pytest.mark.parametrize("timestamp", [True, 3.0, -1, 180000])
def test_invalid_timestamps_are_rejected_before_connecting(unconnected, timestamp):
    database, claim = unconnected
    with pytest.raises(ValueError, match="timestamp"):
        database.upsert_frames(claim, [replace(record(claim), timestamp_ms=timestamp)])


@pytest.mark.parametrize("change", ["foreign-video", "foreign-timestamp", "foreign-model"])
def test_thumbnail_identity_must_match_the_frame(unconnected, change):
    database, claim = unconnected
    frame = record(claim)
    if change == "foreign-video":
        frame = replace(frame, thumbnail_key=thumbnail_key(uuid4(), 0))
    elif change == "foreign-timestamp":
        frame = replace(frame, thumbnail_key=thumbnail_key(claim.video_id, 3000))
    else:
        frame = replace(frame, model_version="another-model")
    with pytest.raises(ValueError, match="thumbnail"):
        database.upsert_frames(claim, [frame])


@pytest.mark.parametrize("batch", ["empty", "too-many", "duplicate", "wrong-type", "none"])
def test_invalid_batches_are_rejected_before_connecting(unconnected, batch):
    database, claim = unconnected
    frames = {
        "empty": [],
        "too-many": [record(claim)] * 61,
        "duplicate": [record(claim)] * 2,
        "wrong-type": [object()],
        "none": None,
    }[batch]
    with pytest.raises(ValueError, match="frame batch"):
        database.upsert_frames(claim, frames)


@pytest.mark.parametrize(
    "changes",
    [
        {"job_id": "not-a-uuid"},
        {"video_id": None},
        {"attempt_count": 0},
        {"attempt_count": True},
        {"attempt_count": 1.0},
    ],
)
def test_invalid_claim_receipts_are_rejected_before_connecting(unconnected, changes):
    database, claim = unconnected
    with pytest.raises(ValueError, match="ClaimedJob"):
        database.upsert_frames(replace(claim, **changes), [record(claim)])


def test_retry_preserves_frame_identity_and_updates_vectors(database, claimed_job):
    claim = claimed_job
    before = states(database, claim.video_id, claim.job_id)
    frames = [record(claim, timestamp) for timestamp in (0, 3000)]
    ids = database.upsert_frames(claim, frames)
    first = persisted(database, claim.video_id)
    assert len(ids) == len(first) == 2 and all(isinstance(value, UUID) for value in ids)
    assert tuple(row["id"] for row in first) == ids
    assert [row["timestamp_ms"] for row in first] == [0, 3000]
    for row, frame in zip(first, frames, strict=True):
        assert row["video_id"] == claim.video_id
        assert row["thumbnail_key"] == frame.thumbnail_key
        assert row["model_version"] == MODEL_VERSION
        assert row["dimensions"] == 512 and row["norm"] == pytest.approx(1, abs=1e-6)
    assert database.upsert_frames(claim, frames) == ids
    assert persisted(database, claim.video_id) == first
    replacement = [
        record(claim, timestamp, embedding=[-1.0] + [0.0] * 511) for timestamp in (0, 3000)
    ]
    assert database.upsert_frames(claim, replacement) == ids
    after = persisted(database, claim.video_id)
    assert [row["created_at"] for row in after] == [row["created_at"] for row in first]
    assert all(row["vector"].startswith("[-1,0,") for row in after)
    assert states(database, claim.video_id, claim.job_id) == before
    with database.connection() as connection:
        visible = connection.execute(
            """SELECT count(*) AS count FROM video_frames f JOIN videos v ON v.id = f.video_id
                 WHERE v.id = %s AND v.status = 'ready' AND f.model_version = %s""",
            (claim.video_id, MODEL_VERSION),
        ).fetchone()["count"]
    assert visible == 0


def test_full_batch_supports_dense_normalized_vectors_and_boundary_timestamps(
    database, claimed_job
):
    vector = [1 / math.sqrt(512)] * 512
    frames = [
        record(claimed_job, timestamp, embedding=vector) for timestamp in range(0, 180000, 3000)
    ]
    ids = database.upsert_frames(claimed_job, frames)
    assert len(ids) == len(set(ids)) == 60
    rows = persisted(database, claimed_job.video_id)
    assert [row["timestamp_ms"] for row in rows] == list(range(0, 180000, 3000))
    assert all(row["norm"] == pytest.approx(1, abs=1e-6) for row in rows)


def test_concurrent_repeated_batches_do_not_create_duplicates(database, claimed_job):
    barrier = Barrier(2)
    frames = [record(claimed_job, timestamp) for timestamp in (0, 3000)]

    def write():
        barrier.wait(timeout=5)
        return database.upsert_frames(claimed_job, frames)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    assert len(persisted(database, claimed_job.video_id)) == 2


@pytest.mark.parametrize(
    "change", ["wrong-job", "wrong-video", "wrong-attempt", "queued", "terminal"]
)
def test_frame_writes_require_the_current_processing_claim(database, claimed_job, change):
    claim = claimed_job
    if change == "wrong-job":
        claim = replace(claim, job_id=uuid4())
    elif change == "wrong-video":
        claim = replace(claim, video_id=uuid4())
    elif change == "wrong-attempt":
        claim = replace(claim, attempt_count=claim.attempt_count + 1)
    else:
        with database.connection() as connection:
            connection.execute(
                "UPDATE videos SET status = %s WHERE id = %s",
                ("queued" if change == "queued" else "ready", claim.video_id),
            )
            connection.execute(
                "UPDATE processing_jobs SET status = %s WHERE id = %s",
                ("queued" if change == "queued" else "completed", claim.job_id),
            )
    before = states(database, claimed_job.video_id, claimed_job.job_id)
    with pytest.raises(JobStateError, match="processing"):
        database.upsert_frames(claim, [record(claim)])
    assert persisted(database, claimed_job.video_id) == []
    assert states(database, claimed_job.video_id, claimed_job.job_id) == before


@pytest.mark.parametrize("recovery", ["requeue", "manual-retry"])
def test_old_receipt_cannot_write_after_a_new_claim(database, claimed_job, recovery):
    old = claimed_job
    next_job_id = old.job_id if recovery == "requeue" else uuid4()
    with database.connection() as connection:
        connection.execute("UPDATE videos SET status = 'queued' WHERE id = %s", (old.video_id,))
        connection.execute(
            "UPDATE processing_jobs SET status = %s WHERE id = %s",
            ("queued" if recovery == "requeue" else "failed", old.job_id),
        )
        if recovery == "manual-retry":
            connection.execute(
                "INSERT INTO processing_jobs (id, video_id, status) VALUES (%s, %s, 'queued')",
                (next_job_id, old.video_id),
            )
    current = database.claim_job(next_job_id, old.video_id).job
    with pytest.raises(JobStateError):
        database.upsert_frames(old, [record(old)])
    assert persisted(database, old.video_id) == []
    assert len(database.upsert_frames(current, [record(current)])) == 1


def test_upsert_leaves_other_model_rows_untouched(database, claimed_job):
    frame = record(claimed_job)
    other_id = uuid4()
    with database.connection() as connection:
        connection.execute(
            """INSERT INTO video_frames
                      (id, video_id, timestamp_ms, thumbnail_key, embedding, model_version)
               VALUES (%s, %s, 0, 'other-thumbnail', %s::vector, 'other-model')""",
            (other_id, claimed_job.video_id, "[" + ",".join(map(str, frame.embedding)) + "]"),
        )
    before = persisted(database, claimed_job.video_id)
    database.upsert_frames(claimed_job, [frame])
    after = persisted(database, claimed_job.video_id)
    assert len(after) == 2
    assert next(row for row in after if row["id"] == other_id) == before[0]


@pytest.mark.parametrize("failure_mode", ["reject", "suppress"])
def test_later_frame_failure_rolls_back_the_entire_batch(database, claimed_job, failure_mode):
    claim = claimed_job
    database.upsert_frames(claim, [record(claim)])
    before = persisted(database, claim.video_id)
    name = sql.Identifier(f"processor_reject_frame_{uuid4().hex}")
    action = sql.SQL(
        "RAISE EXCEPTION 'test frame rejection'" if failure_mode == "reject" else "RETURN NULL"
    )
    with database.connection() as connection:
        connection.execute(
            sql.SQL("""CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
                       BEGIN IF NEW.video_id = {}::uuid AND NEW.timestamp_ms = 3000 THEN {};
                       END IF; RETURN NEW; END $$""").format(
                name, sql.Literal(str(claim.video_id)), action
            )
        )
        connection.execute(
            sql.SQL(
                "CREATE TRIGGER {} BEFORE INSERT ON video_frames FOR EACH ROW EXECUTE FUNCTION {}()"
            ).format(name, name)
        )
    try:
        with pytest.raises(DatabaseError):
            database.upsert_frames(
                claim, [record(claim, embedding=[-1.0] + [0.0] * 511), record(claim, 3000)]
            )
        assert persisted(database, claim.video_id) == before
    finally:
        with database.connection() as connection:
            connection.execute(sql.SQL("DROP TRIGGER {} ON video_frames").format(name))
            connection.execute(sql.SQL("DROP FUNCTION {}()").format(name))


@requires_ffmpeg
@pytest.mark.minio
@pytest.mark.real_model
@pytest.mark.skipif(
    os.getenv("FRAMESEARCH_MINIO_TEST") != "1" or os.getenv("FRAMESEARCH_REAL_MODEL_TEST") != "1",
    reason="opt-in real MinIO/model/database pipeline",
)
def test_real_minio_decode_embed_upload_and_pgvector_retry(database, queued_job, minio, tmp_path):
    from framesearch_processor.embeddings import OpenClipEmbedder
    from framesearch_processor.sampling import extracted_frames
    from framesearch_processor.settings import Settings

    store, storage = minio
    video_id, job_id, source_key = queued_job
    source = make_video(tmp_path)
    data = source.read_bytes()
    with database.connection() as connection:
        connection.execute("UPDATE videos SET size_bytes = %s WHERE id = %s", (len(data), video_id))
    store._client.put_object(
        Bucket=storage.bucket, Key=source_key, Body=data, ContentType="video/mp4"
    )
    claim = database.claim_job(job_id, video_id).job
    model = OpenClipEmbedder(Settings.from_env())
    with store.downloaded_video(
        claim.object_key, claim.size_bytes, temp_root=tmp_path
    ) as downloaded:
        with extracted_frames(downloaded, claim.size_bytes, temp_root=tmp_path) as clip:
            vectors = model.embed_images([frame.path for frame in clip.frames])
            frames = []
            for frame, vector in zip(clip.frames, vectors, strict=True):
                key = store.upload_thumbnail(video_id, frame, model.model_version)
                info = store._client.head_object(Bucket=storage.bucket, Key=key)
                assert info["ContentType"] == "image/jpeg"
                assert info["ContentLength"] == frame.path.stat().st_size
                frames.append(FrameRecord(frame.timestamp_ms, key, vector, model.model_version))
            ids = database.upsert_frames(claim, frames)
            assert database.upsert_frames(claim, frames) == ids
    assert list(tmp_path.iterdir()) == [source]
    rows = persisted(database, video_id)
    assert len(rows) == 3 and [row["timestamp_ms"] for row in rows] == [0, 3000, 6000]
    assert [row["thumbnail_key"] for row in rows] == [frame.thumbnail_key for frame in frames]
    assert all(
        row["dimensions"] == 512 and row["norm"] == pytest.approx(1, abs=1e-6) for row in rows
    )
    assert all(row["model_version"] == MODEL_VERSION for row in rows)
    query = "[" + ",".join(map(str, model.embed_text("a colorful video test pattern"))) + "]"
    with database.connection() as connection:
        similarities = connection.execute(
            """SELECT 1 - (embedding <=> %s::vector) AS similarity
                 FROM video_frames WHERE video_id = %s""",
            (query, video_id),
        ).fetchall()
    assert len(similarities) == 3
    assert all(
        math.isfinite(row["similarity"]) and -1 <= row["similarity"] <= 1 for row in similarities
    )
    status = states(database, video_id, job_id)
    assert status["video_status"] == status["job_status"] == "processing"
