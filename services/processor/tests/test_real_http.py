import math
import os
import time

import pytest
from fastapi.testclient import TestClient

from framesearch_processor.app import create_app
from framesearch_processor.settings import MODEL_VERSION


@pytest.mark.real_model
@pytest.mark.skipif(
    os.getenv("FRAMESEARCH_REAL_MODEL_TEST") != "1",
    reason="set FRAMESEARCH_REAL_MODEL_TEST=1 to load the real OpenCLIP checkpoint",
)
def test_real_openclip_through_http():
    with TestClient(create_app(worker_factory=None)) as client:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            response = client.get("/health/ready")
            if response.status_code == 200:
                break
            assert response.json()["error"]["code"] != "model_load_failed"
            assert client.get("/health/live").status_code == 200
            time.sleep(0.1)
        else:
            pytest.fail("real model did not load within 10 minutes")
        assert response.json()["model_version"] == MODEL_VERSION
        response = client.post("/embed/text", json={"text": "a car on a rainy street at night"})
        assert response.status_code == 200
        data = response.json()
        assert data["model_version"] == MODEL_VERSION
        assert len(data["embedding"]) == 512
        assert all(math.isfinite(value) for value in data["embedding"])
        assert math.sqrt(sum(value * value for value in data["embedding"])) == pytest.approx(
            1, abs=1e-6
        )
