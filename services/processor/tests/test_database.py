"""Opt-in connection checks against an isolated PostgreSQL/pgvector server."""

import os
from dataclasses import replace
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from framesearch_processor.database import (
    LOCK_TIMEOUT_MS,
    STATEMENT_TIMEOUT_MS,
    Database,
    DatabaseError,
)
from framesearch_processor.settings import DatabaseSettings

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("FRAMESEARCH_DATABASE_TEST") != "1", reason="opt-in live PostgreSQL test"
    ),
]


@pytest.fixture
def database():
    url = os.environ["TEST_DATABASE_URL"]
    if urlsplit(url).hostname not in {"postgres", "localhost", "127.0.0.1", "::1"}:
        pytest.fail("Database tests require a local disposable server")
    database = Database(DatabaseSettings(url))
    database.check_schema()
    return database


def test_real_connection_checks_shared_schema_and_closes(database):
    with database.connection() as connection:
        assert not connection.closed
        row = connection.execute(
            "SELECT current_database() AS name, current_setting('application_name') AS app"
        ).fetchone()
        assert row["name"]
        assert row["app"] == "framesearch-processor"
        assert connection.execute("SHOW statement_timeout").fetchone() == {
            "statement_timeout": f"{STATEMENT_TIMEOUT_MS // 1000}s"
        }
        assert connection.execute("SHOW lock_timeout").fetchone() == {
            "lock_timeout": f"{LOCK_TIMEOUT_MS // 1000}s"
        }
        vector = connection.execute(
            "SELECT '[1,0,0]'::vector <=> '[1,0,0]'::vector AS distance"
        ).fetchone()
        assert vector["distance"] == 0
    assert connection.closed


def test_connections_are_separate(database):
    with database.connection() as first, database.connection() as second:
        assert first is not second
        assert first.info.backend_pid != second.info.backend_pid
    assert first.closed and second.closed


def test_connection_closes_and_preserves_consumer_exception(database):
    with pytest.raises(ValueError, match="consumer failed"):
        with database.connection() as connection:
            raise ValueError("consumer failed")
    assert connection.closed


def test_transaction_rolls_back_before_connection_closes(database):
    # Test-owned table, never mutate the application tables or existing data.
    table = sql.Identifier(f"processor_connection_test_{uuid4().hex}")
    with database.connection() as connection:
        connection.execute(sql.SQL("CREATE TABLE {} (value integer)").format(table))
    try:
        with pytest.raises(ValueError, match="abort transaction"):
            with database.connection() as connection:
                connection.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(table))
                raise ValueError("abort transaction")
        assert connection.closed
        with database.connection() as connection:
            assert connection.execute(
                sql.SQL("SELECT count(*) AS count FROM {}").format(table)
            ).fetchone() == {"count": 0}
    finally:
        with database.connection() as connection:
            connection.execute(sql.SQL("DROP TABLE {}").format(table))


def test_statement_timeout_rolls_back_and_is_reported(database):
    with pytest.raises(DatabaseError) as failure:
        with database.connection() as connection:
            connection.execute("SET LOCAL statement_timeout = '50ms'")
            connection.execute("SELECT pg_sleep(1)")
    assert isinstance(failure.value.__cause__, psycopg.errors.QueryCanceled)
    assert connection.closed
    database.check_schema()


def test_missing_migration_is_not_reported_as_ready(database):
    # A test-owned empty database lets this check leave the shared schema intact.
    name = f"processor_empty_test_{uuid4().hex}"
    url = database._settings.url
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            empty_url = urlsplit(url)._replace(path=f"/{name}").geturl()
            empty = Database(DatabaseSettings(empty_url))
            with pytest.raises(DatabaseError, match="migration is unavailable"):
                empty.check_schema()
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_bad_credentials_are_reported_without_url(database):
    parsed = urlsplit(database._settings.url)
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    bad_url = parsed._replace(
        netloc=f"invalid-test-user:incorrect-test-password@{host}:{parsed.port or 5432}"
    ).geturl()
    broken = Database(replace(database._settings, url=bad_url))
    with pytest.raises(DatabaseError) as failure:
        broken.check_schema()
    assert "incorrect-test-password" not in str(failure.value)
