"""Short-lived PostgreSQL connections using Developer 1's existing migration."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .settings import DatabaseSettings

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
