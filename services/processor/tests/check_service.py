"""Check a running real processor over HTTP, including its container deployment."""

import argparse
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from framesearch_processor.settings import MODEL_VERSION


def check_service(base_url: str, timeout: float) -> None:
    base_url = base_url.rstrip("/")
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urlopen(f"{base_url}/health/ready", timeout=3) as response:
                ready = json.load(response)
            assert ready == {"status": "ready", "model_version": MODEL_VERSION}
            break
        except HTTPError as exc:
            details = json.load(exc)
            if exc.code != 503 or details["error"]["code"] != "model_loading":
                raise
        except URLError:
            # The process may still be importing dependencies before listening.
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError("processor did not become ready before the deadline")
        time.sleep(0.5)
    with urlopen(f"{base_url}/health/live", timeout=3) as response:
        assert json.load(response) == {"status": "ok"}
    request = Request(
        f"{base_url}/embed/text",
        data=json.dumps({"text": "a car on a rainy street at night"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        result = json.load(response)
    assert result["model_version"] == MODEL_VERSION
    vector = result["embedding"]
    assert len(vector) == 512
    assert all(math.isfinite(value) for value in vector)
    assert abs(math.sqrt(sum(value * value for value in vector)) - 1) < 1e-6
    print(f"Verified live HTTP, readiness, and real normalized 512-vector: {MODEL_VERSION}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    check_service(args.url, args.timeout)
