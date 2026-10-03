"""Durable failure allows Kafka acknowledgment and Go's manual retry safely."""

from dataclasses import replace
from uuid import uuid4

import pytest
from psycopg import sql
from test_frame_persistence import claimed_job as claimed_job
from test_frame_persistence import persisted, record
from test_frame_persistence import unconnected as unconnected
from test_job_claims import states

from framesearch_processor.database import DatabaseError, JobStateError
from framesearch_processor.settings import MODEL_VERSION

pytestmark = pytest.mark.database


@pytest.mark.parametrize("reason", [None, 42, "", " \n\t", "x" * 1001])
def test_invalid_failure_reason_is_rejected_before_connecting(unconnected, reason):
    database, claim = unconnected
    with pytest.raises(ValueError, match="failure reason"):
        database.fail_job(claim, reason)


@pytest.mark.parametrize("partial_frames", [False, True])
def test_failure_commits_both_states_errors_and_timestamps(database, claimed_job, partial_frames):
    claim = claimed_job
    if partial_frames:
        database.upsert_frames(claim, [record(claim)])
    frames = persisted(database, claim.video_id)
    before = states(database, claim.video_id, claim.job_id)
    reason = "Video is corrupt; export it as MP4 again"
    database.fail_job(claim, f" \n{reason} \t")
    after = states(database, claim.video_id, claim.job_id)
    assert after["job_status"] == after["video_status"] == "failed"
    assert after["last_error"] == after["processing_error"] == reason
    assert after["attempt_count"] == claim.attempt_count
    assert after["video_updated"] == after["job_updated"] > before["video_updated"]
    assert persisted(database, claim.video_id) == frames
    with database.connection() as connection:
        visible = connection.execute(
            """SELECT count(*) AS count FROM video_frames f JOIN videos v ON v.id = f.video_id
                 WHERE v.id = %s AND v.status = 'ready' AND f.model_version = %s""",
            (claim.video_id, MODEL_VERSION),
        ).fetchone()["count"]
    assert visible == 0
    assert database.claim_job(claim.job_id, claim.video_id).outcome == "terminal"


def test_reason_accepts_unicode_at_the_character_bound(database, claimed_job):
    reason = "失" * 1000
    database.fail_job(claimed_job, reason)
    result = states(database, claimed_job.video_id, claimed_job.job_id)
    assert result["last_error"] == result["processing_error"] == reason


@pytest.mark.parametrize("change", ["wrong-job", "wrong-video", "stale-attempt", "queued", "ready"])
def test_failure_cannot_overwrite_a_different_or_terminal_claim(database, claimed_job, change):
    claim = claimed_job
    if change == "wrong-job":
        claim = replace(claim, job_id=uuid4())
    elif change == "wrong-video":
        claim = replace(claim, video_id=uuid4())
    elif change == "stale-attempt":
        claim = replace(claim, attempt_count=claim.attempt_count - 1)
    elif change == "ready":
        database.upsert_frames(claim, [record(claim)])
        database.complete_job(claim, 0.2, [0])
    else:
        with database.connection() as connection:
            connection.execute(
                "UPDATE videos SET status = 'queued' WHERE id = %s", (claim.video_id,)
            )
            connection.execute(
                "UPDATE processing_jobs SET status = 'queued' WHERE id = %s", (claim.job_id,)
            )
    before = states(database, claimed_job.video_id, claimed_job.job_id)
    frames = persisted(database, claimed_job.video_id)
    with pytest.raises(JobStateError, match="processing"):
        database.fail_job(claim, "This attempt failed")
    assert states(database, claimed_job.video_id, claimed_job.job_id) == before
    assert persisted(database, claimed_job.video_id) == frames


def test_manual_retry_can_reuse_frames_and_complete_without_changing_failed_history(
    database, claimed_job
):
    old = claimed_job
    ids = database.upsert_frames(old, [record(old)])
    database.fail_job(old, "Storage was unavailable; retry indexing")
    old_state = states(database, old.video_id, old.job_id)
    # Reproduce Go's existing retry transaction; this is not processor application logic.
    job_id = uuid4()
    with database.connection() as connection:
        connection.execute(
            "UPDATE videos SET status = 'queued', processing_error = NULL WHERE id = %s",
            (old.video_id,),
        )
        connection.execute(
            "INSERT INTO processing_jobs (id, video_id, status) VALUES (%s, %s, 'queued')",
            (job_id, old.video_id),
        )
    current = database.claim_job(job_id, old.video_id).job
    before = states(database, current.video_id, current.job_id)
    with pytest.raises(JobStateError):
        database.fail_job(old, "Late failure must not affect the new retry")
    assert states(database, current.video_id, current.job_id) == before
    assert database.upsert_frames(current, [record(current)]) == ids
    database.complete_job(current, 0.2, [0])
    assert database.claim_job(old.job_id, old.video_id).outcome == "terminal"
    with database.connection() as connection:
        job = connection.execute(
            """SELECT status, last_error, updated_at, attempt_count
                 FROM processing_jobs WHERE id = %s""",
            (old.job_id,),
        ).fetchone()
    assert job == {
        "status": "failed",
        "last_error": old_state["last_error"],
        "updated_at": old_state["job_updated"],
        "attempt_count": old_state["attempt_count"],
    }


@pytest.mark.parametrize("table", ["videos", "processing_jobs"])
@pytest.mark.parametrize("failure_mode", ["reject", "suppress"])
def test_failed_update_rolls_back_both_states_and_errors(
    database, claimed_job, table, failure_mode
):
    claim = claimed_job
    database.upsert_frames(claim, [record(claim)])
    before = states(database, claim.video_id, claim.job_id)
    frames = persisted(database, claim.video_id)
    name = sql.Identifier(f"processor_reject_failure_{uuid4().hex}")
    row_id = claim.video_id if table == "videos" else claim.job_id
    action = sql.SQL(
        "RAISE EXCEPTION 'test failure rejection'" if failure_mode == "reject" else "RETURN NULL"
    )
    with database.connection() as connection:
        connection.execute(
            sql.SQL("""CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.id = {}::uuid THEN {}; END IF; RETURN NEW; END $$""").format(
                name, sql.Literal(str(row_id)), action
            )
        )
        connection.execute(
            sql.SQL(
                "CREATE TRIGGER {} BEFORE UPDATE ON {} FOR EACH ROW EXECUTE FUNCTION {}()"
            ).format(name, sql.Identifier(table), name)
        )
    try:
        with pytest.raises(DatabaseError):
            database.fail_job(claim, "Storage retries exhausted; retry indexing")
        assert states(database, claim.video_id, claim.job_id) == before
        assert persisted(database, claim.video_id) == frames
    finally:
        with database.connection() as connection:
            connection.execute(sql.SQL("DROP TRIGGER {} ON {}").format(name, sql.Identifier(table)))
            connection.execute(sql.SQL("DROP FUNCTION {}()").format(name))
