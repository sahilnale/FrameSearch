"""Ready/completed is one durable transaction, after the full frame set exists."""

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


@pytest.mark.parametrize("duration", [0, -1, 181, True, float("nan"), float("inf"), "3", None])
def test_invalid_duration_cannot_begin_completion(unconnected, duration):
    database, claim = unconnected
    with pytest.raises(ValueError, match="duration"):
        database.complete_job(claim, duration, [0])


@pytest.mark.parametrize(
    "timestamps",
    [[], list(range(61)), [0, 0], [-1], [180000], [True], [3.0], "0", None],
)
def test_invalid_expected_timeline_cannot_begin_completion(unconnected, timestamps):
    database, claim = unconnected
    with pytest.raises(ValueError, match="timestamp"):
        database.complete_job(claim, 6.2, timestamps)


@pytest.mark.parametrize("timestamps,duration", [([0], 0.2), (list(range(0, 180000, 3000)), 180)])
def test_complete_commits_ready_and_completed_together(database, claimed_job, timestamps, duration):
    claim = claimed_job
    ids = database.upsert_frames(claim, [record(claim, time) for time in timestamps])
    frames = persisted(database, claim.video_id)
    before = states(database, claim.video_id, claim.job_id)
    database.complete_job(claim, duration, timestamps)
    after = states(database, claim.video_id, claim.job_id)
    assert after["video_status"] == "ready" and after["job_status"] == "completed"
    assert after["video_updated"] == after["job_updated"] > before["video_updated"]
    assert after["attempt_count"] == claim.attempt_count
    assert after["last_error"] is after["processing_error"] is None
    assert persisted(database, claim.video_id) == frames
    with database.connection() as connection:
        assert (
            connection.execute(
                "SELECT duration_seconds FROM videos WHERE id = %s", (claim.video_id,)
            ).fetchone()["duration_seconds"]
            == duration
        )
        visible = connection.execute(
            """SELECT f.id FROM video_frames f JOIN videos v ON v.id = f.video_id
                 WHERE v.id = %s AND v.status = 'ready' AND f.model_version = %s
                 ORDER BY f.timestamp_ms""",
            (claim.video_id, MODEL_VERSION),
        ).fetchall()
    assert tuple(row["id"] for row in visible) == ids
    assert database.claim_job(claim.job_id, claim.video_id).outcome == "terminal"


@pytest.mark.parametrize(
    "damage", ["missing-all", "missing-one", "wrong-model", "wrong-key", "bad-vector"]
)
def test_incomplete_or_invalid_frames_never_become_ready(database, claimed_job, damage):
    claim = claimed_job
    if damage != "missing-all":
        database.upsert_frames(claim, [record(claim, time) for time in (0, 3000)])
        with database.connection() as connection:
            if damage == "missing-one":
                connection.execute(
                    "DELETE FROM video_frames WHERE video_id = %s AND timestamp_ms = 3000",
                    (claim.video_id,),
                )
            elif damage == "wrong-model":
                connection.execute(
                    "UPDATE video_frames SET model_version = 'other-model' WHERE video_id = %s",
                    (claim.video_id,),
                )
            elif damage == "wrong-key":
                connection.execute(
                    "UPDATE video_frames SET thumbnail_key = %s WHERE video_id = %s",
                    ("not-the-uploaded-key", claim.video_id),
                )
            else:
                vector = "[2," + ",".join(["0"] * 511) + "]"
                connection.execute(
                    "UPDATE video_frames SET embedding = %s::vector WHERE video_id = %s",
                    (vector, claim.video_id),
                )
    before = states(database, claim.video_id, claim.job_id)
    frames = persisted(database, claim.video_id)
    with pytest.raises(JobStateError, match="frame set"):
        database.complete_job(claim, 6.2, [0, 3000])
    assert states(database, claim.video_id, claim.job_id) == before
    assert persisted(database, claim.video_id) == frames


def test_completion_prunes_obsolete_active_model_frames_only(database, claimed_job):
    claim = claimed_job
    ids = database.upsert_frames(claim, [record(claim, time) for time in (0, 3000, 6000)])
    other_id = uuid4()
    with database.connection() as connection:
        connection.execute(
            """INSERT INTO video_frames
                      (id, video_id, timestamp_ms, thumbnail_key, embedding, model_version)
               SELECT %s, video_id, timestamp_ms, thumbnail_key, embedding, 'other-model'
                 FROM video_frames WHERE id = %s""",
            (other_id, ids[-1]),
        )
    database.complete_job(claim, 6.2, [3000, 0])
    rows = persisted(database, claim.video_id)
    assert {row["id"] for row in rows} == {ids[0], ids[1], other_id}
    assert [row["timestamp_ms"] for row in rows if row["model_version"] == MODEL_VERSION] == [
        0,
        3000,
    ]


@pytest.mark.parametrize("change", ["stale-attempt", "wrong-job", "queued", "completed"])
def test_completion_requires_the_current_processing_claim(database, claimed_job, change):
    claim = claimed_job
    database.upsert_frames(claim, [record(claim)])
    if change == "stale-attempt":
        claim = replace(claim, attempt_count=claim.attempt_count - 1)
    elif change == "wrong-job":
        claim = replace(claim, job_id=uuid4())
    else:
        with database.connection() as connection:
            connection.execute(
                "UPDATE videos SET status = %s WHERE id = %s",
                ("queued" if change == "queued" else "ready", claim.video_id),
            )
            connection.execute(
                "UPDATE processing_jobs SET status = %s WHERE id = %s", (change, claim.job_id)
            )
    before = states(database, claim.video_id, claimed_job.job_id)
    frames = persisted(database, claim.video_id)
    with pytest.raises(JobStateError, match="processing"):
        database.complete_job(claim, 0.2, [0])
    assert states(database, claim.video_id, claimed_job.job_id) == before
    assert persisted(database, claim.video_id) == frames


@pytest.mark.parametrize("table", ["videos", "processing_jobs"])
@pytest.mark.parametrize("failure_mode", ["reject", "suppress"])
def test_completion_failure_rolls_back_statuses_duration_and_pruning(
    database, claimed_job, table, failure_mode
):
    claim = claimed_job
    database.upsert_frames(claim, [record(claim, time) for time in (0, 3000)])
    before = states(database, claim.video_id, claim.job_id)
    frames = persisted(database, claim.video_id)
    name = sql.Identifier(f"processor_reject_completion_{uuid4().hex}")
    row_id = claim.video_id if table == "videos" else claim.job_id
    action = sql.SQL(
        "RAISE EXCEPTION 'test completion rejection'" if failure_mode == "reject" else "RETURN NULL"
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
            database.complete_job(claim, 6.2, [0])
        assert states(database, claim.video_id, claim.job_id) == before
        assert persisted(database, claim.video_id) == frames
        with database.connection() as connection:
            assert (
                connection.execute(
                    "SELECT duration_seconds FROM videos WHERE id = %s", (claim.video_id,)
                ).fetchone()["duration_seconds"]
                is None
            )
    finally:
        with database.connection() as connection:
            connection.execute(sql.SQL("DROP TRIGGER {} ON {}").format(name, sql.Identifier(table)))
            connection.execute(sql.SQL("DROP FUNCTION {}()").format(name))
