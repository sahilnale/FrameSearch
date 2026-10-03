"""Short-lived PostgreSQL connections using Developer 1's existing migration."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .settings import DatabaseSettings

CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 15000
LOCK_TIMEOUT_MS = 5000


class DatabaseError(RuntimeError):
    """A database operation failed; the worker must not acknowledge its event."""


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
