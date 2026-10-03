"""Short-lived PostgreSQL connections using Developer 1's existing migration."""

import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from .media import MAX_DURATION_SECONDS
from .settings import EMBEDDING_DIMENSIONS, MAX_FRAMES, MODEL_VERSION, DatabaseSettings
from .storage import thumbnail_key

CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 15000
LOCK_TIMEOUT_MS = 5000


class DatabaseError(RuntimeError):
    """A database operation failed; the worker must not acknowledge its event."""


class JobStateError(DatabaseError):
    """The persisted job and video do not form a legal processing state."""


@dataclass(frozen=True)
class ClaimedJob:
    job_id: UUID
    video_id: UUID
    object_key: str
    size_bytes: int
    attempt_count: int


@dataclass(frozen=True)
class ClaimResult:
    outcome: Literal["claimed", "busy", "terminal", "missing"]
    job: ClaimedJob | None = None


@dataclass(frozen=True)
class FrameRecord:
    timestamp_ms: int
    thumbnail_key: str
    embedding: Sequence[float]
    model_version: str = MODEL_VERSION


def _vector_literal(embedding: Sequence[float]) -> str:
    if (
        not isinstance(embedding, Sequence)
        or isinstance(embedding, str | bytes | bytearray)
        or len(embedding) != EMBEDDING_DIMENSIONS
    ):
        raise ValueError("frame embedding must contain 512 coordinates")
    if any(isinstance(value, bool) or not isinstance(value, int | float) for value in embedding):
        raise ValueError("frame embedding coordinates must be finite numbers")
    try:
        values = tuple(float(value) for value in embedding)
    except OverflowError:
        raise ValueError("frame embedding coordinates must be finite numbers") from None
    if not all(math.isfinite(value) for value in values):
        raise ValueError("frame embedding coordinates must be finite numbers")
    if abs(math.hypot(*values) - 1.0) > 1e-6:
        raise ValueError("frame embedding must be L2 normalized")
    return "[" + ",".join(map(str, values)) + "]"


def _validate_claim(claim: ClaimedJob) -> None:
    if (
        not isinstance(claim, ClaimedJob)
        or not isinstance(claim.job_id, UUID)
        or not isinstance(claim.video_id, UUID)
        or type(claim.attempt_count) is not int
        or claim.attempt_count < 1
    ):
        raise ValueError("operation requires a valid ClaimedJob")


class Database:
    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        """Commit on success, roll back on errors, and always close the connection."""
        try:
            with psycopg.connect(
                self._settings.url,
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                application_name="framesearch-processor",
                options=(
                    f"-c statement_timeout={STATEMENT_TIMEOUT_MS} -c lock_timeout={LOCK_TIMEOUT_MS}"
                ),
                row_factory=dict_row,
            ) as connection:
                yield connection
        except psycopg.Error as exc:
            raise DatabaseError(
                "Database operation failed; check connection, permissions, and migration"
            ) from exc

    def check_schema(self) -> None:
        """Check connectivity, canonical public tables, and the pgvector extension."""
        with self.connection() as connection:
            result = connection.execute(
                """
                SELECT to_regclass('public.videos') IS NOT NULL
                   AND to_regclass('public.video_frames') IS NOT NULL
                   AND to_regclass('public.processing_jobs') IS NOT NULL
                   AND EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS ready
                """
            ).fetchone()
            if not result or not result["ready"]:
                raise DatabaseError(
                    "Database migration is unavailable; apply the shared database migration"
                )

    def claim_job(self, job_id: UUID, video_id: UUID) -> ClaimResult:
        """Claim only the event's queued job, locking its video before its job."""
        if not isinstance(job_id, UUID) or not isinstance(video_id, UUID):
            raise ValueError("job_id and video_id must be UUIDs")
        with self.connection() as connection:
            video = connection.execute(
                "SELECT status, object_key, size_bytes FROM videos WHERE id = %s FOR UPDATE",
                (video_id,),
            ).fetchone()
            if video is None:
                return ClaimResult("missing")
            job = connection.execute(
                "SELECT status FROM processing_jobs WHERE id = %s AND video_id = %s FOR UPDATE",
                (job_id, video_id),
            ).fetchone()
            if job is None:
                return ClaimResult("missing")
            if job["status"] == "failed" and video["status"] != "awaiting_upload":
                # Old failed jobs remain terminal after Go creates a new retry job.
                return ClaimResult("terminal")
            if job["status"] == "completed" and video["status"] == "ready":
                return ClaimResult("terminal")
            if job["status"] == "processing" and video["status"] == "processing":
                return ClaimResult("busy")
            if job["status"] != "queued" or video["status"] != "queued":
                raise JobStateError("Job and video states are inconsistent; reconcile the job")
            claimed = connection.execute(
                """
                UPDATE processing_jobs
                   SET status = 'processing', attempt_count = attempt_count + 1,
                       last_error = NULL, updated_at = now()
                 WHERE id = %s AND video_id = %s AND status = 'queued'
                 RETURNING id, attempt_count
                """,
                (job_id, video_id),
            ).fetchone()
            if claimed is None:
                raise JobStateError("Queued job could not be claimed")
            updated = connection.execute(
                """
                UPDATE videos SET status = 'processing', processing_error = NULL, updated_at = now()
                 WHERE id = %s AND status = 'queued'
                """,
                (video_id,),
            ).rowcount
            if updated != 1:
                raise JobStateError("Claimed job could not update its queued video")
            return ClaimResult(
                "claimed",
                ClaimedJob(
                    job_id=claimed["id"],
                    video_id=video_id,
                    object_key=video["object_key"],
                    size_bytes=video["size_bytes"],
                    attempt_count=claimed["attempt_count"],
                ),
            )

    def _require_processing_claim(
        self, connection: psycopg.Connection[dict[str, Any]], claim: ClaimedJob
    ) -> None:
        video = connection.execute(
            "SELECT id FROM videos WHERE id = %s AND status = 'processing' FOR UPDATE",
            (claim.video_id,),
        ).fetchone()
        if video is None:
            raise JobStateError("Operation requires a processing video")
        job = connection.execute(
            """SELECT id FROM processing_jobs
                WHERE id = %s AND video_id = %s AND status = 'processing'
                  AND attempt_count = %s FOR UPDATE""",
            (claim.job_id, claim.video_id, claim.attempt_count),
        ).fetchone()
        if job is None:
            raise JobStateError("Operation requires the current processing job claim")

    def upsert_frames(self, claim: ClaimedJob, frames: Sequence[FrameRecord]) -> tuple[UUID, ...]:
        """Persist a bounded batch after thumbnail uploads; never mark the video ready."""
        _validate_claim(claim)
        if not isinstance(frames, Sequence) or not 1 <= len(frames) <= MAX_FRAMES:
            raise ValueError("frame batch must contain 1–60 records")
        parameters = []
        timestamps = set()
        for frame in frames:
            if not isinstance(frame, FrameRecord):
                raise ValueError("frame batch must contain FrameRecord values")
            expected_key = thumbnail_key(claim.video_id, frame.timestamp_ms, frame.model_version)
            if frame.thumbnail_key != expected_key:
                raise ValueError("thumbnail key must match the video's timestamp and model")
            if frame.timestamp_ms in timestamps:
                raise ValueError("frame batch must not contain duplicate timestamps")
            timestamps.add(frame.timestamp_ms)
            parameters.append(
                (
                    uuid4(),
                    claim.video_id,
                    frame.timestamp_ms,
                    frame.thumbnail_key,
                    _vector_literal(frame.embedding),
                    frame.model_version,
                )
            )
        with self.connection() as connection:
            self._require_processing_claim(connection, claim)
            ids = []
            for values in parameters:
                result = connection.execute(
                    """
                    INSERT INTO video_frames
                        (id, video_id, timestamp_ms, thumbnail_key, embedding, model_version)
                    VALUES (%s, %s, %s, %s, %s::vector, %s)
                    ON CONFLICT (video_id, timestamp_ms, model_version)
                    DO UPDATE SET thumbnail_key = EXCLUDED.thumbnail_key,
                                  embedding = EXCLUDED.embedding
                    RETURNING id
                    """,
                    values,
                ).fetchone()
                if result is None:
                    raise DatabaseError("Frame write did not persist its record")
                ids.append(result["id"])
        return tuple(ids)

    def complete_job(
        self, claim: ClaimedJob, duration_seconds: float, expected_timestamps: Sequence[int]
    ) -> None:
        """Publish a complete frame set after all thumbnail uploads have succeeded."""
        _validate_claim(claim)
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int | float)
            or not 0 < duration_seconds <= MAX_DURATION_SECONDS
        ):
            raise ValueError("duration must be finite and within 0–180 seconds")
        if (
            not isinstance(expected_timestamps, Sequence)
            or not 1 <= len(expected_timestamps) <= MAX_FRAMES
            or any(type(value) is not int for value in expected_timestamps)
        ):
            raise ValueError("expected timestamps must contain 1–60 integer timestamps")
        expected = tuple(expected_timestamps)
        if len(set(expected)) != len(expected):
            raise ValueError("expected timestamps must be unique")
        expected_keys = {
            timestamp: thumbnail_key(claim.video_id, timestamp) for timestamp in expected
        }
        with self.connection() as connection:
            self._require_processing_claim(connection, claim)
            rows = connection.execute(
                """SELECT timestamp_ms, thumbnail_key, vector_norm(embedding) AS norm
                     FROM video_frames WHERE video_id = %s AND model_version = %s
                       AND timestamp_ms = ANY(%s) FOR UPDATE""",
                (claim.video_id, MODEL_VERSION, list(expected)),
            ).fetchall()
            if len(rows) != len(expected) or any(
                row["thumbnail_key"] != expected_keys[row["timestamp_ms"]]
                or not math.isfinite(row["norm"])
                or abs(row["norm"] - 1.0) > 1e-6
                for row in rows
            ):
                raise JobStateError(
                    "Expected frame set is missing or invalid; video cannot be ready"
                )
            # Remove a recovered attempt's obsolete rows before publishing the expected set.
            connection.execute(
                """DELETE FROM video_frames WHERE video_id = %s AND model_version = %s
                       AND NOT (timestamp_ms = ANY(%s))""",
                (claim.video_id, MODEL_VERSION, list(expected)),
            )
            updated_job = connection.execute(
                """UPDATE processing_jobs SET status = 'completed', last_error = NULL,
                          updated_at = now()
                     WHERE id = %s AND video_id = %s AND status = 'processing'
                       AND attempt_count = %s""",
                (claim.job_id, claim.video_id, claim.attempt_count),
            ).rowcount
            updated_video = connection.execute(
                """UPDATE videos SET status = 'ready', duration_seconds = %s,
                          processing_error = NULL, updated_at = now()
                     WHERE id = %s AND status = 'processing'""",
                (duration_seconds, claim.video_id),
            ).rowcount
            if updated_job != 1 or updated_video != 1:
                raise JobStateError("Completion could not update both job and video")
