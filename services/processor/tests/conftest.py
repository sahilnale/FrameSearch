import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from framesearch_processor.database import Database
from framesearch_processor.settings import DatabaseSettings


@pytest.fixture
def database():
    if os.getenv("FRAMESEARCH_DATABASE_TEST") != "1":
        pytest.skip("opt-in live PostgreSQL test")
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL must identify a disposable local test database")
    if urlsplit(url).hostname not in {"postgres", "localhost", "127.0.0.1", "::1"}:
        pytest.fail("Database tests require a local disposable server")
    database = Database(DatabaseSettings(url))
    database.check_schema()
    return database


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
