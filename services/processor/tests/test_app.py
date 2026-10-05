"""HTTP and lifecycle tests use test-only doubles; real inference has its own check."""

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from framesearch_processor.app import create_app
from framesearch_processor.settings import MODEL_VERSION, Settings


class TestOnlyEmbedder:
    __test__ = False
    model_version = MODEL_VERSION

    def __init__(self):
        self.inputs = []

    def embed_text(self, text):
        self.inputs.append(text)
        return [1 / math.sqrt(512)] * 512


def await_ready(client, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/health/ready")
        if response.status_code == 200:
            return response.json()
        if response.json()["error"]["code"] == "model_load_failed":
            pytest.fail("model load failed")
        time.sleep(0.01)
    pytest.fail("processor did not become ready within the test deadline")


@pytest.fixture
def ready_client():
    embedder = TestOnlyEmbedder()
    with TestClient(
        create_app(Settings(), model_loader=lambda _: embedder, worker_factory=None)
    ) as client:
        await_ready(client)
        yield client, embedder


def test_text_http_shape_and_model_version(ready_client):
    client, embedder = ready_client
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {
        "status": "ready",
        "model_version": MODEL_VERSION,
    }
    response = client.post("/embed/text", json={"text": "  a person walking  "})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"embedding", "model_version"}
    assert body["model_version"] == MODEL_VERSION
    assert len(body["embedding"]) == 512
    assert embedder.inputs == ["a person walking"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": ""},
        {"text": "  \n\t "},
        {"text": "x" * 501},
        {"text": 3},
        {"text": None},
        {"text": ["a car"]},
        {"text": "a car", "query": "other"},
    ],
)
def test_invalid_text_is_not_sent_to_the_model(ready_client, body):
    client, embedder = ready_client
    response = client.post("/embed/text", json=body)
    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "invalid_request"
    assert embedder.inputs == []


def test_malformed_json(ready_client):
    client, embedder = ready_client
    response = client.post(
        "/embed/text", content='{"text":', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert embedder.inputs == []


def test_query_length_is_unicode_characters_and_checked_after_trimming(ready_client):
    client, embedder = ready_client
    text = "車" * 500
    response = client.post("/embed/text", json={"text": f"   {text}  "})
    assert response.status_code == 200
    assert embedder.inputs == [text]


def test_live_http_responds_while_loading_and_model_loads_once():
    started = threading.Event()
    release = threading.Event()
    calls = []

    def load(settings):
        calls.append(settings)
        started.set()
        assert release.wait(5)
        return TestOnlyEmbedder()

    with TestClient(create_app(Settings(), model_loader=load, worker_factory=None)) as client:
        try:
            assert started.wait(2)
            assert client.get("/health/live").status_code == 200
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "model_loading"
            assert client.post("/embed/text", json={"text": "a car"}).status_code == 503
        finally:
            release.set()
        await_ready(client)
        for _ in range(3):
            assert client.post("/embed/text", json={"text": "a car"}).status_code == 200
        assert len(calls) == 1


def test_model_load_failure_keeps_liveness_and_fails_readiness(caplog):
    def fail(_):
        raise RuntimeError("checkpoint unavailable")

    with TestClient(create_app(Settings(), model_loader=fail, worker_factory=None)) as client:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = client.get("/health/ready")
            if response.json()["error"]["code"] == "model_load_failed":
                break
            time.sleep(0.01)
        else:
            pytest.fail("model failure was not reported")
        assert response.status_code == 503
        assert client.get("/health/live").status_code == 200
        assert client.post("/embed/text", json={"text": "a car"}).status_code == 503
        assert "checkpoint unavailable" not in response.text
    assert "OpenCLIP startup failed" in caplog.text


def test_inference_failure_has_consistent_error(caplog):
    class FailedEmbedder(TestOnlyEmbedder):
        def embed_text(self, _):
            raise RuntimeError("private implementation detail")

    with TestClient(
        create_app(Settings(), model_loader=lambda _: FailedEmbedder(), worker_factory=None)
    ) as client:
        await_ready(client)
        response = client.post("/embed/text", json={"text": "a car"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "embedding_failed"
    assert "private implementation detail" not in response.text
    assert "Text inference failed" in caplog.text


def test_health_http_remains_responsive_during_blocking_inference():
    started = threading.Event()
    release = threading.Event()

    class SlowEmbedder(TestOnlyEmbedder):
        def embed_text(self, text):
            started.set()
            assert release.wait(5)
            return super().embed_text(text)

    with TestClient(
        create_app(Settings(), model_loader=lambda _: SlowEmbedder(), worker_factory=None)
    ) as client:
        await_ready(client)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, "/embed/text", json={"text": "a car"})
            try:
                assert started.wait(2)
                # These requests must finish before the blocked encoder is released.
                assert client.get("/health/live").status_code == 200
                assert client.get("/health/ready").status_code == 200
            finally:
                release.set()
            assert pending.result(timeout=5).status_code == 200


def test_wrong_model_configuration_fails_startup():
    def unused(_):
        pytest.fail("invalid settings must not reach the model loader")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("MODEL_PRETRAINED", "openai")
        with pytest.raises(ValueError, match="must match"):
            with TestClient(create_app(model_loader=unused)):
                pass
