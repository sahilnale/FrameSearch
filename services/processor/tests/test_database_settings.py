import traceback

import pytest

from framesearch_processor.settings import DatabaseSettings


def test_database_reads_shared_url_without_exposing_credentials(monkeypatch):
    url = "postgres://worker:private-password@postgres:5432/framesearch?sslmode=disable"
    monkeypatch.setenv("DATABASE_URL", url)
    settings = DatabaseSettings.from_env()
    assert settings.url == url
    assert repr(settings) == "DatabaseSettings()"


@pytest.mark.parametrize(
    "url",
    [
        "postgres://worker:secret@localhost:5432/framesearch?sslmode=disable",
        "postgresql://worker:secret@postgres/framesearch?sslmode=require",
        "postgresql://worker:encoded%40secret@localhost/framesearch",
        "postgresql:///framesearch?host=/tmp",
    ],
)
def test_database_accepts_valid_postgresql_urls(url):
    assert DatabaseSettings(url).url == url


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        " ",
        "sqlite:///framesearch",
        "host=localhost dbname=framesearch",
        "postgresql://",
        "postgresql://localhost",
        "postgresql:///framesearch",
        "postgresql://worker:secret@localhost/framesearch?unknown_parameter=yes",
    ],
)
def test_database_rejects_missing_or_invalid_urls_without_credentials(url):
    with pytest.raises(ValueError, match="DATABASE_URL") as failure:
        DatabaseSettings(url)
    diagnostic = "".join(traceback.format_exception(failure.value))
    assert "secret" not in diagnostic


def test_database_environment_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        DatabaseSettings.from_env()
