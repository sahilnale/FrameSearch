import os
from urllib.parse import urlsplit

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
