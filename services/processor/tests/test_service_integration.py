"""Actual packaged Uvicorn socket plus Kafka, MinIO, CLIP, and PostgreSQL."""

import math
import os
import signal
import socket
import subprocess
import sys
from contextlib import contextmanager
from time import monotonic, sleep

import httpx
import pytest
from check_service import check_service
from confluent_kafka import OFFSET_INVALID
from test_frame_persistence import persisted
from test_indexing import pytestmark as indexing_marks
from test_job_claims import states
from test_job_processing import uploaded_event as uploaded_event
from test_kafka_integration import kafka_settings as kafka_settings
from test_minio import minio as minio
from test_worker_integration import envelope, observer, offset, publish

from framesearch_processor.settings import MODEL_VERSION

pytestmark = [
    *indexing_marks,
    pytest.mark.kafka,
    pytest.mark.skipif(
        os.getenv("FRAMESEARCH_KAFKA_TEST") != "1", reason="Enable real Kafka checks"
    ),
]


@contextmanager
def running_service(settings, storage, tmp_path, *, port=None):
    if port is None:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
    temporary = tmp_path / "processor-temp"
    temporary.mkdir()
    log = tmp_path / "processor.log"
    environment = os.environ.copy()
    environment.update(
        {
            "KAFKA_BROKERS": ",".join(settings.brokers),
            "KAFKA_TOPIC": settings.topic,
            "KAFKA_CONSUMER_GROUP": settings.group_id,
            "S3_BUCKET": storage.bucket,
            "TMPDIR": str(temporary),
            "PYTHONPATH": "/app",
        }
    )
    with log.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "framesearch_processor.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--log-level",
                "warning",
            ],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
            ) as client:
                yield client, process, log, temporary
        finally:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                pytest.fail("Processor did not shut down and clean up within 30 seconds")
            assert process.returncode in (0, -signal.SIGTERM), log.read_text()


def wait_for_ready_state(client, process, log, code=None):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        assert process.poll() is None, log.read_text()
        try:
            live = client.get("/health/live")
            assert live.status_code == 200
            response = client.get("/health/ready")
        except httpx.ConnectError:
            sleep(0.1)
            continue
        if code is None and response.status_code == 200:
            return response
        error_code = response.json().get("error", {}).get("code")
        if code is not None and error_code == code:
            assert response.status_code == 503
            return response
        assert error_code not in ("model_load_failed", "worker_failed"), log.read_text()
        sleep(0.1)
    pytest.fail("Packaged processor did not reach the expected readiness state: " + log.read_text())


def test_packaged_service_shares_real_model_for_http_and_ingestion(
    database, uploaded_event, kafka_settings, tmp_path
):
    event, store, storage, _ = uploaded_event
    payload = envelope(event)
    client_offsets = observer(kafka_settings)
    try:
        with running_service(kafka_settings, storage, tmp_path) as (
            client,
            process,
            log,
            temporary,
        ):
            check_service(str(client.base_url), timeout=60)
            assert (
                wait_for_ready_state(client, process, log).json()["model_version"] == MODEL_VERSION
            )
            publish(kafka_settings, event, [payload, payload])
            response = client.post("/embed/text", json={"text": "a colorful video test pattern"})
            assert response.status_code == 200
            vector = response.json()["embedding"]
            assert response.json()["model_version"] == MODEL_VERSION
            assert len(vector) == 512 and all(math.isfinite(value) for value in vector)
            assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1, abs=1e-6)
            deadline = monotonic() + 30
            while offset(client_offsets, kafka_settings) != 2:
                assert process.poll() is None and monotonic() < deadline, log.read_text()
                assert client.get("/health/live").status_code == 200
                assert client.get("/health/ready").status_code == 200
                sleep(0.1)
            status = states(database, event.video_id, event.job_id)
            assert status["video_status"] == "ready" and status["job_status"] == "completed"
            assert status["attempt_count"] == 3
            rows = persisted(database, event.video_id)
            assert [row["timestamp_ms"] for row in rows] == [0, 3000, 6000]
            for row in rows:
                assert row["dimensions"] == 512 and row["norm"] == pytest.approx(1, abs=1e-6)
                store._client.head_object(Bucket=storage.bucket, Key=row["thumbnail_key"])
            literal = "[" + ",".join(map(str, vector)) + "]"
            with database.connection() as connection:
                matches = connection.execute(
                    """SELECT 1 - (f.embedding <=> %s::vector) AS similarity
                       FROM video_frames f JOIN videos v ON v.id = f.video_id
                       WHERE v.id = %s AND v.status = 'ready' AND f.model_version = %s""",
                    (literal, event.video_id, MODEL_VERSION),
                ).fetchall()
            assert len(matches) == 3 and all(math.isfinite(row["similarity"]) for row in matches)
            assert list(temporary.iterdir()) == []
        assert log.read_text().count(f"Loading {MODEL_VERSION} on CPU") == 1
        assert offset(client_offsets, kafka_settings) == 2
    finally:
        client_offsets.close()


def test_packaged_service_reports_stopped_worker_without_committing_poison_event(
    database, uploaded_event, kafka_settings, tmp_path
):
    event, _, storage, _ = uploaded_event
    publish(kafka_settings, event, [b"{invalid JSON", envelope(event)])
    client_offsets = observer(kafka_settings)
    try:
        with running_service(kafka_settings, storage, tmp_path) as (
            client,
            process,
            log,
            temporary,
        ):
            response = wait_for_ready_state(client, process, log, "worker_failed")
            assert response.json()["error"]["code"] == "worker_failed"
            assert offset(client_offsets, kafka_settings) == OFFSET_INVALID
            status = states(database, event.video_id, event.job_id)
            assert status["video_status"] == status["job_status"] == "queued"
            assert status["attempt_count"] == 2 and persisted(database, event.video_id) == []
            assert client.post("/embed/text", json={"text": "a car"}).status_code == 200
            assert list(temporary.iterdir()) == []
        assert "Ingestion startup or worker failed" in log.read_text()
    finally:
        client_offsets.close()
