"""Actual public Go API uploads, durable Kafka queue, processor, search and signed reads."""

import json
import math
import os
import signal
import socket
import subprocess
from contextlib import contextmanager
from time import monotonic, sleep
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx
import pytest
from confluent_kafka import OFFSET_INVALID, KafkaError, KafkaException, TopicPartition
from test_frame_persistence import persisted
from test_job_claims import states
from test_kafka_integration import kafka_settings as kafka_settings
from test_minio import minio as minio
from test_service_integration import pytestmark as service_marks
from test_service_integration import running_service, wait_for_ready_state
from test_visual_search import evaluation_dataset
from test_worker_integration import observer, offset

from framesearch_processor.events import parse_media_uploaded

pytestmark = [
    *service_marks,
    pytest.mark.skipif(
        os.getenv("FRAMESEARCH_API_TEST") != "1", reason="Enable real public API smoke"
    ),
]


def available_ports():
    with socket.socket() as api, socket.socket() as processor:
        api.bind(("127.0.0.1", 0))
        processor.bind(("127.0.0.1", 0))
        return api.getsockname()[1], processor.getsockname()[1]


@contextmanager
def running_api(settings, storage, port, processor_port, tmp_path):
    log = tmp_path / "api.log"
    environment = os.environ.copy()
    environment.update(
        {
            "API_ADDR": f"127.0.0.1:{port}",
            "PROCESSOR_URL": f"http://127.0.0.1:{processor_port}",
            "KAFKA_BROKERS": ",".join(settings.brokers),
            "KAFKA_TOPIC": settings.topic,
            "S3_BUCKET": storage.bucket,
            "S3_ENDPOINT_PUBLIC": storage.endpoint,
        }
    )
    with log.open("w") as output:
        process = subprocess.Popen(
            ["/usr/local/bin/framesearch-api"],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False
            ) as client:
                deadline = monotonic() + 20
                while monotonic() < deadline:
                    assert process.poll() is None, log.read_text()
                    try:
                        if client.get("/health/live").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    sleep(0.1)
                else:
                    pytest.fail("Go API did not start: " + log.read_text())
                yield client, process, log
        finally:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                pytest.fail("Go API did not shut down within 15 seconds")
            assert process.returncode in (0, -signal.SIGTERM), log.read_text()


def unsigned(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def committed_offset_when_ready(client, settings):
    # A fresh broker creates/loads __consumer_offsets on its first coordinator request.
    deadline = monotonic() + 20
    while monotonic() < deadline:
        try:
            return offset(client, settings)
        except KafkaException as error:
            if error.args[0].code() not in {
                KafkaError.NOT_COORDINATOR,
                KafkaError.COORDINATOR_NOT_AVAILABLE,
                KafkaError.COORDINATOR_LOAD_IN_PROGRESS,
            }:
                raise
            sleep(0.1)
    pytest.fail("Kafka coordinator did not finish startup within 20 seconds")


def test_go_uploads_queue_until_processor_starts_then_search_and_playback_work(
    database, minio, kafka_settings, tmp_path
):
    dataset_path, dataset = evaluation_dataset(tmp_path)
    assert dataset["dataset"] == "framesearch-commons-real-v1"
    _, storage = minio
    api_port, processor_port = available_ports()
    uploads = {}
    events = []
    offsets = observer(kafka_settings)
    inspection = observer(kafka_settings)
    try:
        with running_api(kafka_settings, storage, api_port, processor_port, tmp_path) as (
            api,
            api_process,
            api_log,
        ):
            assert api.get("/health/ready").status_code == 503  # Real processor is not started yet.
            for clip in dataset["clips"]:
                data = (dataset_path / clip["filename"]).read_bytes()
                response = api.post(
                    "/api/v1/videos/upload-url",
                    json={
                        "filename": clip["filename"],
                        "content_type": "video/mp4",
                        "size_bytes": len(data),
                    },
                )
                assert response.status_code == 201, response.text
                upload = response.json()
                video_id = upload["video_id"]
                uploads[video_id] = {"clip": clip, "data": data}
                assert api.get(f"/api/v1/videos/{video_id}").json()["status"] == "awaiting_upload"
                assert parse_qs(urlsplit(upload["upload_url"]).query)["X-Amz-Expires"] == ["900"]
                put = api.put(
                    upload["upload_url"], content=data, headers={"Content-Type": "video/mp4"}
                )
                assert put.status_code == 200, put.text
                for _ in range(2):
                    complete = api.post(f"/api/v1/videos/{video_id}/complete")
                    assert complete.status_code == 200, complete.text
                    assert complete.json() == {"video_id": video_id, "status": "queued"}
                assert api.get(f"/api/v1/videos/{video_id}/playback-url").status_code == 409
            assert committed_offset_when_ready(offsets, kafka_settings) == OFFSET_INVALID
            # Read actual Go events without joining a group or committing any offsets.
            partition = TopicPartition(kafka_settings.topic, 0, 0)
            inspection.assign([partition])
            assert inspection.get_watermark_offsets(partition, timeout=10) == (0, 3)
            deadline = monotonic() + 20
            while len(events) < len(uploads) and monotonic() < deadline:
                message = inspection.poll(1)
                if message is None:
                    continue
                assert message.error() is None
                event = parse_media_uploaded(message.value(), message.key())
                assert str(event.video_id) in uploads
                assert all(event.video_id != other.video_id for other in events)
                events.append(event)
                status = states(database, event.video_id, event.job_id)
                assert status["video_status"] == status["job_status"] == "queued"
                assert status["attempt_count"] == 0
                with database.connection() as connection:
                    assert (
                        connection.execute(
                            "SELECT count(*) AS count FROM processing_jobs WHERE video_id = %s",
                            (event.video_id,),
                        ).fetchone()["count"]
                        == 1
                    )
            assert len(events) == len(uploads) == 3
            print(
                "API_QUEUE_CHECK: three real uploads/jobs/events; duplicate completes add none",
                flush=True,
            )

            with running_service(kafka_settings, storage, tmp_path, port=processor_port) as (
                client,
                processor,
                processor_log,
                temporary,
            ):
                wait_for_ready_state(client, processor, processor_log)
                deadline = monotonic() + 90
                while committed_offset_when_ready(offsets, kafka_settings) != 3:
                    assert processor.poll() is None and monotonic() < deadline, (
                        processor_log.read_text()
                    )
                    assert api_process.poll() is None, api_log.read_text()
                    sleep(0.1)
                ready = api.get("/health/ready")
                assert ready.status_code == 200, ready.text
                assert set(ready.json()["dependencies"].values()) == {"ready"}
                for event in events:
                    status = states(database, event.video_id, event.job_id)
                    assert status["video_status"] == "ready" and status["job_status"] == "completed"
                    assert status["attempt_count"] == 1
                    rows = persisted(database, event.video_id)
                    assert [row["timestamp_ms"] for row in rows] == [
                        0,
                        3000,
                        6000,
                        9000,
                        12000,
                        15000,
                    ]
                    assert all(
                        row["dimensions"] == 512 and row["norm"] == pytest.approx(1, abs=1e-6)
                        for row in rows
                    )
                    detail = api.get(f"/api/v1/videos/{event.video_id}")
                    assert detail.status_code == 200
                    assert detail.json()["status"] == "ready"
                    assert detail.json()["duration_seconds"] == pytest.approx(18)
                    assert detail.json()["processing_error"] is None
                puppy_id = next(
                    key for key, value in uploads.items() if value["clip"]["id"] == "puppy-indoors"
                )
                search = api.post(
                    "/api/v1/search", json={"query": "a puppy playing indoors", "limit": 12}
                )
                assert search.status_code == 200, search.text
                results = search.json()["results"]
                assert len(results) == 12 and results[0]["video_id"] == puppy_id
                assert all(
                    row["video_id"] in uploads and math.isfinite(row["similarity"])
                    for row in results
                )
                for video_id, upload in uploads.items():
                    query = next(
                        item["text"]
                        for item in dataset["queries"]
                        if item["expected_clip"] == upload["clip"]["id"]
                    )
                    filtered = api.post(
                        "/api/v1/search", json={"query": query, "limit": 12, "video_id": video_id}
                    )
                    assert filtered.status_code == 200, filtered.text
                    rows = filtered.json()["results"]
                    assert len(rows) == 6 and all(row["video_id"] == video_id for row in rows)
                    assert sorted(row["timestamp_ms"] for row in rows) == [
                        0,
                        3000,
                        6000,
                        9000,
                        12000,
                        15000,
                    ]
                    assert all(row["filename"] == upload["clip"]["filename"] for row in rows)
                    thumbnail_url = rows[0]["thumbnail_url"]
                    thumbnail = api.get(thumbnail_url)
                    assert thumbnail.status_code == 200 and thumbnail.content.startswith(
                        b"\xff\xd8"
                    )
                    assert api.get(unsigned(thumbnail_url)).status_code == 403
                    playback = api.get(f"/api/v1/videos/{video_id}/playback-url")
                    assert playback.status_code == 200
                    signed = playback.json()
                    assert signed["expires_in_seconds"] == 900
                    part = api.get(signed["url"], headers={"Range": "bytes=0-1023"})
                    assert part.status_code == 206 and part.content == upload["data"][:1024]
                    assert part.headers["Content-Range"] == f"bytes 0-1023/{len(upload['data'])}"
                    assert api.get(signed["url"]).content == upload["data"]
                    assert api.get(unsigned(signed["url"])).status_code == 403
                assert list(temporary.iterdir()) == []
                assert (
                    processor_log.read_text().count("Loading ViT-B-32:laion2b_s34b_b79k on CPU")
                    == 1
                )
                print(
                    "API_QUEUE_RESULT "
                    + json.dumps(
                        {
                            "uploads": 3,
                            "jobs_completed": 3,
                            "frames": 18,
                            "committed_offset": 3,
                            "duplicate_complete_extra_jobs": 0,
                            "search_and_signed_reads": "passed",
                        }
                    ),
                    flush=True,
                )
    finally:
        inspection.close()
        offsets.close()
        # Remove only IDs allocated by this public API test, after both services stop.
        with database.connection() as connection:
            for video_id in uploads:
                connection.execute("DELETE FROM processing_jobs WHERE video_id = %s", (video_id,))
                connection.execute("DELETE FROM videos WHERE id = %s", (video_id,))
