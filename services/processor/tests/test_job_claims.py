"""Atomic claims verified against PostgreSQL, including concurrent events."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from framesearch_processor.database import Database, DatabaseError, JobStateError
from framesearch_processor.settings import DatabaseSettings

pytestmark = pytest.mark.database


@pytest.fixture
def queued_job(database):
    video_id, job_id = uuid4(), uuid4()
    object_key = f"videos/{video_id}/original.mp4"
    with database.connection() as connection:
        connection.execute(
            """
            INSERT INTO videos (id, filename, object_key, content_type, size_bytes, status,
                                processing_error, updated_at)
            VALUES (%s, 'claim-test.mp4', %s, 'video/mp4', 1234, 'queued',
                    'old video error', '2000-01-01T00:00:00Z')
            """,
            (video_id, object_key),
        )
        connection.execute(
            """
            INSERT INTO processing_jobs
                (id, video_id, status, attempt_count, last_error, updated_at)
            VALUES (%s, %s, 'queued', 2, 'old job error', '2000-01-01T00:00:00Z')
            """,
            (job_id, video_id),
        )
    try:
        yield video_id, job_id, object_key
    finally:
        with database.connection() as connection:
            connection.execute("DELETE FROM processing_jobs WHERE video_id = %s", (video_id,))
            connection.execute("DELETE FROM videos WHERE id = %s", (video_id,))


def states(database, video_id, job_id):
    with database.connection() as connection:
        return connection.execute(
            """
            SELECT v.status AS video_status, v.processing_error, v.updated_at AS video_updated,
                   j.status AS job_status, j.attempt_count, j.last_error,
                   j.updated_at AS job_updated
              FROM videos v JOIN processing_jobs j ON j.video_id = v.id
             WHERE v.id = %s AND j.id = %s
            """,
            (video_id, job_id),
        ).fetchone()


def test_claim_commits_both_states_and_returns_source_metadata(database, queued_job):
    video_id, job_id, object_key = queued_job
    before = states(database, video_id, job_id)
    result = database.claim_job(job_id, video_id)
    assert result.outcome == "claimed"
    assert result.job.job_id == job_id
    assert result.job.video_id == video_id
    assert result.job.object_key == object_key
    assert result.job.size_bytes == 1234
    assert result.job.attempt_count == 3
    after = states(database, video_id, job_id)
    assert after["video_status"] == after["job_status"] == "processing"
    assert after["attempt_count"] == 3
    assert after["processing_error"] is after["last_error"] is None
    assert after["video_updated"] == after["job_updated"] > before["video_updated"]
    assert database.claim_job(job_id, video_id).outcome == "busy"
    assert states(database, video_id, job_id) == after


@pytest.mark.parametrize("missing", ["video", "job", "ownership"])
def test_missing_or_mismatched_event_cannot_claim_another_job(database, queued_job, missing):
    video_id, job_id, _ = queued_job
    before = states(database, video_id, job_id)
    event_video, event_job = video_id, job_id
    if missing == "job":
        event_job = uuid4()
    elif missing == "video":
        event_video = uuid4()
    else:
        # Existing video with no ownership of the supplied job.
        event_video = uuid4()
        with database.connection() as connection:
            connection.execute(
                """INSERT INTO videos (id, filename, object_key, content_type, size_bytes, status)
                   VALUES (%s, 'other.mp4', %s, 'video/mp4', 10, 'awaiting_upload')""",
                (event_video, f"videos/{event_video}/original.mp4"),
            )
    try:
        result = database.claim_job(event_job, event_video)
        assert result.outcome == "missing" and result.job is None
        assert states(database, video_id, job_id) == before
    finally:
        if missing == "ownership":
            with database.connection() as connection:
                connection.execute("DELETE FROM videos WHERE id = %s", (event_video,))


@pytest.mark.parametrize("job_status", ["queued", "processing", "completed", "failed"])
@pytest.mark.parametrize(
    "video_status", ["awaiting_upload", "queued", "processing", "ready", "failed"]
)
def test_job_and_video_state_matrix(database, queued_job, job_status, video_status):
    video_id, job_id, _ = queued_job
    with database.connection() as connection:
        connection.execute("UPDATE videos SET status = %s WHERE id = %s", (video_status, video_id))
        connection.execute(
            "UPDATE processing_jobs SET status = %s WHERE id = %s", (job_status, job_id)
        )
    before = states(database, video_id, job_id)
    expected = {
        ("queued", "queued"): "claimed",
        ("processing", "processing"): "busy",
        ("completed", "ready"): "terminal",
        **{
            ("failed", status): "terminal" for status in ("failed", "queued", "processing", "ready")
        },
    }.get((job_status, video_status))
    if expected is None:
        with pytest.raises(JobStateError, match="inconsistent"):
            database.claim_job(job_id, video_id)
    else:
        result = database.claim_job(job_id, video_id)
        assert result.outcome == expected
        assert (result.job is not None) == (expected == "claimed")
    if expected != "claimed":
        assert states(database, video_id, job_id) == before


@pytest.mark.parametrize(
    "new_job_status,video_status",
    [("queued", "queued"), ("processing", "processing"), ("completed", "ready")],
)
def test_delayed_failed_event_leaves_the_new_retry_job_untouched(
    database, queued_job, new_job_status, video_status
):
    video_id, old_job_id, _ = queued_job
    new_job_id = uuid4()
    with database.connection() as connection:
        connection.execute(
            "UPDATE processing_jobs SET status = 'failed' WHERE id = %s", (old_job_id,)
        )
        connection.execute("UPDATE videos SET status = %s WHERE id = %s", (video_status, video_id))
        connection.execute(
            "INSERT INTO processing_jobs (id, video_id, status) VALUES (%s, %s, %s)",
            (new_job_id, video_id, new_job_status),
        )
    before = states(database, video_id, new_job_id)
    assert database.claim_job(old_job_id, video_id).outcome == "terminal"
    assert states(database, video_id, new_job_id) == before


def test_concurrent_events_have_exactly_one_claimant(database, queued_job):
    video_id, job_id, _ = queued_job
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return database.claim_job(job_id, video_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
        outcomes = [future.result(timeout=10).outcome for future in futures]
    assert sorted(outcomes) == ["busy", "claimed"]
    after = states(database, video_id, job_id)
    assert after["video_status"] == after["job_status"] == "processing"
    assert after["attempt_count"] == 3


def test_requeued_job_can_be_claimed_again_without_a_new_job(database, queued_job):
    video_id, job_id, _ = queued_job
    assert database.claim_job(job_id, video_id).job.attempt_count == 3
    # Simulate Go's documented recovery after the sole processor has stopped.
    with database.connection() as connection:
        connection.execute("UPDATE videos SET status = 'queued' WHERE id = %s", (video_id,))
        connection.execute(
            "UPDATE processing_jobs SET status = 'queued', last_error = 'requeued' WHERE id = %s",
            (job_id,),
        )
    result = database.claim_job(job_id, video_id)
    assert result.outcome == "claimed"
    assert result.job.job_id == job_id and result.job.attempt_count == 4
    assert states(database, video_id, job_id)["last_error"] is None


def test_claim_locks_video_before_job(database, queued_job):
    video_id, job_id, _ = queued_job
    with ThreadPoolExecutor(max_workers=1) as executor:
        with database.connection() as blocker:
            blocker.execute("SELECT id FROM videos WHERE id = %s FOR UPDATE", (video_id,))
            future = executor.submit(database.claim_job, job_id, video_id)
            with psycopg.connect(database._settings.url, autocommit=True) as monitor:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    waiting = monitor.execute(
                        """SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                             WHERE application_name = 'framesearch-processor'
                               AND wait_event_type = 'Lock')"""
                    ).fetchone()[0]
                    if waiting:
                        break
                    time.sleep(0.02)
                else:
                    pytest.fail("claim did not wait on the blocked video")
                # This fails immediately if the claimant took the job lock first.
                assert (
                    monitor.execute(
                        "SELECT id FROM processing_jobs WHERE id = %s FOR UPDATE NOWAIT", (job_id,)
                    ).fetchone()[0]
                    == job_id
                )
        assert future.result(timeout=10).outcome == "claimed"


@pytest.mark.parametrize("failure_mode", ["reject", "suppress"])
def test_video_update_failure_rolls_back_job_and_attempt_count(database, queued_job, failure_mode):
    video_id, job_id, _ = queued_job
    before = states(database, video_id, job_id)
    name = sql.Identifier(f"processor_reject_claim_{uuid4().hex}")
    action = (
        sql.SQL("RAISE EXCEPTION 'test claim rejection'")
        if failure_mode == "reject"
        else sql.SQL("RETURN NULL")
    )
    with database.connection() as connection:
        connection.execute(
            sql.SQL("""CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
                       BEGIN IF NEW.id = {}::uuid THEN {};
                       END IF; RETURN NEW; END $$""").format(
                name, sql.Literal(str(video_id)), action
            )
        )
        connection.execute(
            sql.SQL(
                "CREATE TRIGGER {} BEFORE UPDATE ON videos FOR EACH ROW EXECUTE FUNCTION {}()"
            ).format(name, name)
        )
    try:
        with pytest.raises(DatabaseError):
            database.claim_job(job_id, video_id)
        assert states(database, video_id, job_id) == before
    finally:
        with database.connection() as connection:
            connection.execute(sql.SQL("DROP TRIGGER {} ON videos").format(name))
            connection.execute(sql.SQL("DROP FUNCTION {}()").format(name))


@pytest.mark.parametrize("bad_job,bad_video", [("not-a-uuid", None), (None, "not-a-uuid")])
def test_event_ids_must_be_uuids_before_connecting(bad_job, bad_video):
    database = Database(DatabaseSettings("postgresql://localhost/unused"))
    with pytest.raises(ValueError, match="must be UUIDs"):
        database.claim_job(bad_job or uuid4(), bad_video or uuid4())
