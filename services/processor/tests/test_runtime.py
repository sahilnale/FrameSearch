"""Lifecycle failure/concurrency checks with doubles confined to test factories."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from test_app import TestOnlyEmbedder, await_ready

from framesearch_processor.app import create_app
from framesearch_processor.database import Database, DatabaseError
from framesearch_processor.kafka import BrokerError, UploadConsumer
from framesearch_processor.runtime import build_worker
from framesearch_processor.settings import Settings
from framesearch_processor.storage import ObjectStorage, StorageError


class TestOnlyWorker:
    __test__ = False

    def __init__(self, stop):
        self.stop = stop
        self.ready = threading.Event()
        self.finished = threading.Event()

    def run(self):
        self.ready.set()
        try:
            assert self.stop.wait(5)
        finally:
            self.ready.clear()
            self.finished.set()


def wait_for_failure(client, code):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get("/health/ready")
        if response.json().get("error", {}).get("code") == code:
            assert response.status_code == 503
            return response
        time.sleep(0.01)
    pytest.fail(f"Runtime did not report {code} within five seconds")


def test_model_is_loaded_once_and_shared_with_worker_off_http_thread():
    model = TestOnlyEmbedder()
    loaded, created, cleanup = [], [], threading.Event()

    def load(settings):
        loaded.append((settings, threading.get_ident()))
        return model

    @contextmanager
    def factory(received_model, stop):
        worker = TestOnlyWorker(stop)
        created.append((received_model, stop, worker, threading.get_ident()))
        try:
            yield worker
        finally:
            cleanup.set()

    application = create_app(Settings(), model_loader=load, worker_factory=factory)
    with TestClient(application) as client:
        await_ready(client)
        state = application.state.model
        assert created[0][0] is state.embedder is model
        assert created[0][1] is state.stop
        assert len(loaded) == len(created) == 1
        assert loaded[0][1] == created[0][3] != threading.get_ident()
        assert client.post("/embed/text", json={"text": "a car"}).status_code == 200
        assert client.get("/health/live").status_code == 200
    assert cleanup.is_set() and created[0][2].finished.is_set()
    assert state.stop.is_set() and state.embedder is state.worker is None


def test_http_remains_responsive_during_worker_initialization():
    entered, release = threading.Event(), threading.Event()

    @contextmanager
    def factory(model, stop):
        entered.set()
        assert release.wait(5)
        yield TestOnlyWorker(stop)

    with TestClient(
        create_app(Settings(), model_loader=lambda _: TestOnlyEmbedder(), worker_factory=factory)
    ) as client:
        try:
            assert entered.wait(2)
            assert client.get("/health/live").status_code == 200
            response = client.get("/health/ready")
            assert (
                response.status_code == 503
                and response.json()["error"]["code"] == "worker_starting"
            )
            assert client.post("/embed/text", json={"text": "a car"}).status_code == 200
        finally:
            release.set()
        await_ready(client)


@pytest.mark.parametrize("failure", [DatabaseError, StorageError, BrokerError])
def test_worker_initialization_failure_is_publicly_generic_but_logged(failure, caplog):
    @contextmanager
    def factory(model, stop):
        raise failure("private infrastructure details")
        yield

    with TestClient(
        create_app(Settings(), model_loader=lambda _: TestOnlyEmbedder(), worker_factory=factory)
    ) as client:
        response = wait_for_failure(client, "worker_failed")
        assert "private infrastructure" not in response.text
        assert client.get("/health/live").status_code == 200
        assert client.post("/embed/text", json={"text": "a car"}).status_code == 200
    assert "private infrastructure details" in caplog.text


@pytest.mark.parametrize("exit_mode", ["exception", "unexpected-return"])
def test_worker_exit_removes_readiness_and_runs_cleanup(exit_mode):
    cleanup = threading.Event()

    class FailedWorker(TestOnlyWorker):
        def run(self):
            if exit_mode == "exception":
                raise BrokerError("unacknowledged event")

    @contextmanager
    def factory(model, stop):
        try:
            yield FailedWorker(stop)
        finally:
            cleanup.set()

    with TestClient(
        create_app(Settings(), model_loader=lambda _: TestOnlyEmbedder(), worker_factory=factory)
    ) as client:
        wait_for_failure(client, "worker_failed")
        assert cleanup.is_set()


def test_shutdown_waits_for_inflight_work_before_releasing_model():
    observed_stop, finish = threading.Event(), threading.Event()

    class FinishingWorker(TestOnlyWorker):
        def run(self):
            self.ready.set()
            assert self.stop.wait(5)
            observed_stop.set()
            assert finish.wait(5)
            self.ready.clear()

    @contextmanager
    def factory(model, stop):
        yield FinishingWorker(stop)

    application = create_app(
        Settings(), model_loader=lambda _: TestOnlyEmbedder(), worker_factory=factory
    )
    client = TestClient(application)
    client.__enter__()
    await_ready(client)
    state = application.state.model
    with ThreadPoolExecutor(max_workers=1) as executor:
        closing = executor.submit(client.__exit__, None, None, None)
        try:
            assert observed_stop.wait(2)
            assert not closing.done() and state.embedder is not None
        finally:
            finish.set()
        closing.result(timeout=5)
    assert state.embedder is state.worker is None


def test_shutdown_during_model_loading_never_constructs_a_worker():
    entered, release = threading.Event(), threading.Event()
    factory = Mock()

    def load(settings):
        entered.set()
        assert release.wait(5)
        return TestOnlyEmbedder()

    application = create_app(Settings(), model_loader=load, worker_factory=factory)
    client = TestClient(application)
    client.__enter__()
    assert entered.wait(2)
    state = application.state.model
    with ThreadPoolExecutor(max_workers=1) as executor:
        closing = executor.submit(client.__exit__, None, None, None)
        try:
            assert state.stop.wait(2)
            assert not closing.done()
        finally:
            release.set()
        closing.result(timeout=5)
    factory.assert_not_called()
    assert state.embedder is None


@pytest.fixture
def construction(monkeypatch):
    database, storage, consumer = (
        Mock(spec=Database),
        Mock(spec=ObjectStorage),
        Mock(spec=UploadConsumer),
    )
    database_factory, storage_factory = Mock(return_value=database), Mock(return_value=storage)
    monkeypatch.setattr("framesearch_processor.runtime.Database", database_factory)
    monkeypatch.setattr("framesearch_processor.runtime.ObjectStorage", storage_factory)
    monkeypatch.setattr("framesearch_processor.runtime.UploadConsumer", Mock(return_value=consumer))
    for name in ("DatabaseSettings", "StorageSettings", "KafkaSettings"):
        monkeypatch.setattr(
            f"framesearch_processor.runtime.{name}.from_env", Mock(return_value=object())
        )
    return database, storage, consumer, storage_factory


def test_real_builder_shares_model_stop_and_closes_storage(construction):
    database, storage, consumer, _ = construction
    model, stop = TestOnlyEmbedder(), threading.Event()
    with build_worker(model, stop) as worker:
        assert worker._jobs._indexer._embedder is model
        assert worker._jobs._stop is worker._stop is stop
        assert worker._consumer is consumer
        database.check_schema.assert_called_once()
        storage.check_bucket.assert_called_once()
        storage.close.assert_not_called()
    storage.close.assert_called_once()


def test_builder_checks_schema_before_storage_creation(construction):
    database, _, _, storage_factory = construction
    database.check_schema.side_effect = DatabaseError("invalid schema")
    with pytest.raises(DatabaseError):
        with build_worker(TestOnlyEmbedder(), threading.Event()):
            pass
    storage_factory.assert_not_called()


def test_builder_closes_storage_if_bucket_check_fails(construction):
    _, storage, _, _ = construction
    storage.check_bucket.side_effect = StorageError("bucket absent")
    with pytest.raises(StorageError):
        with build_worker(TestOnlyEmbedder(), threading.Event()):
            pass
    storage.close.assert_called_once()
