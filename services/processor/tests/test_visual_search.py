"""Labeled generated/real-footage relevance through the genuine processor stack."""

import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from create_demo_clips import create_clips
from prepare_real_clips import SOURCES
from test_frame_persistence import persisted
from test_job_claims import states
from test_kafka_integration import kafka_settings as kafka_settings
from test_minio import minio as minio
from test_service_integration import pytestmark as pytestmark
from test_service_integration import running_service, wait_for_ready_state
from test_worker_integration import envelope, observer, offset, publish

from framesearch_processor.events import MediaUploaded
from framesearch_processor.settings import MODEL_VERSION


def evaluation_dataset(tmp_path):
    configured = os.getenv("FRAMESEARCH_REAL_FOOTAGE_DIR")
    if not configured:
        path = tmp_path / "dataset"
        return path, create_clips(path)
    path = Path(configured)
    dataset = json.loads((path / "sources.json").read_text())
    pinned = json.loads(SOURCES.read_text())
    labels = json.loads(SOURCES.with_name("real-footage-labels.json").read_text())
    assert dataset["dataset"] == pinned["dataset"] == labels["dataset"]
    assert dataset["duration_seconds"] == pinned["duration_seconds"]
    assert len(dataset["clips"]) == len(pinned["clips"])
    for actual, expected in zip(dataset["clips"], pinned["clips"], strict=True):
        assert {key: actual[key] for key in expected} == expected
        source = path / actual["filename"]
        assert source.stat().st_size == actual["prepared_size_bytes"]
        with source.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == actual["prepared_sha256"]
    dataset.update(labels)
    return path, dataset


def test_labeled_clips_rank_from_real_http_embeddings(database, minio, kafka_settings, tmp_path):
    dataset_path, dataset = evaluation_dataset(tmp_path)
    timestamps = list(range(0, math.ceil(dataset["duration_seconds"] * 1000), 3000))
    store, storage = minio
    video_ids = []
    clips_by_video = {}
    events = []
    offsets = observer(kafka_settings)
    try:
        for clip in dataset["clips"]:
            video_id, job_id = uuid4(), uuid4()
            video_ids.append(video_id)
            clips_by_video[video_id] = clip["id"]
            source = dataset_path / clip["filename"]
            data = source.read_bytes()
            key = f"videos/{video_id}/original.mp4"
            store._client.put_object(
                Bucket=storage.bucket, Key=key, Body=data, ContentType="video/mp4"
            )
            with database.connection() as connection:
                connection.execute(
                    """INSERT INTO videos
                        (id, filename, object_key, content_type, size_bytes, status)
                        VALUES (%s, %s, %s, 'video/mp4', %s, 'queued')""",
                    (video_id, clip["filename"], key, len(data)),
                )
                connection.execute(
                    "INSERT INTO processing_jobs (id, video_id, status) VALUES (%s, %s, 'queued')",
                    (job_id, video_id),
                )
            events.append(MediaUploaded(uuid4(), video_id, job_id, datetime.now(UTC)))
        with running_service(kafka_settings, storage, tmp_path) as (
            client,
            process,
            log,
            temporary,
        ):
            wait_for_ready_state(client, process, log)
            for event in events:
                publish(kafka_settings, event, [envelope(event)])
            deadline = monotonic() + 60
            while offset(offsets, kafka_settings) != len(events):
                assert process.poll() is None and monotonic() < deadline, log.read_text()
                assert client.get("/health/ready").status_code == 200, log.read_text()
                sleep(0.1)
            for event in events:
                status = states(database, event.video_id, event.job_id)
                assert status["video_status"] == "ready" and status["job_status"] == "completed"
                assert status["attempt_count"] == 1
                rows = persisted(database, event.video_id)
                assert [row["timestamp_ms"] for row in rows] == timestamps
                for row in rows:
                    assert row["dimensions"] == 512 and row["norm"] == pytest.approx(1, abs=1e-6)
                    store._client.head_object(Bucket=storage.bucket, Key=row["thumbnail_key"])

            results = []
            for query in dataset["queries"]:
                response = client.post("/embed/text", json={"text": query["text"]})
                assert response.status_code == 200
                body = response.json()
                assert body["model_version"] == MODEL_VERSION
                vector = body["embedding"]
                assert len(vector) == 512 and all(math.isfinite(value) for value in vector)
                assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(
                    1, abs=1e-6
                )
                literal = "[" + ",".join(map(str, vector)) + "]"
                with database.connection() as connection:
                    # Exact ranking over this tiny corpus; public Go search remains deferred.
                    connection.execute("SET LOCAL enable_indexscan = off")
                    connection.execute("SET LOCAL enable_bitmapscan = off")
                    matches = connection.execute(
                        """SELECT f.video_id, f.timestamp_ms,
                                  1 - (f.embedding <=> %s::vector) AS similarity
                           FROM video_frames f JOIN videos v ON v.id = f.video_id
                           WHERE v.id = ANY(%s) AND v.status = 'ready' AND f.model_version = %s
                           ORDER BY f.embedding <=> %s::vector, v.filename, f.timestamp_ms
                           LIMIT 5""",
                        (literal, video_ids, MODEL_VERSION, literal),
                    ).fetchall()
                assert len(matches) == 5
                ranked = [
                    {
                        "clip": clips_by_video[match["video_id"]],
                        "timestamp_ms": match["timestamp_ms"],
                        "similarity": match["similarity"],
                    }
                    for match in matches
                ]
                assert all(math.isfinite(match["similarity"]) for match in ranked)
                expected_times = query.get("expected_timestamp_ms", timestamps)
                assert expected_times and set(expected_times) <= set(timestamps)

                relevant = [
                    match["clip"] == query["expected_clip"]
                    and match["timestamp_ms"] in expected_times
                    for match in ranked
                ]

                results.append(
                    {
                        **query,
                        "top1_correct": ranked[0]["clip"] == query["expected_clip"],
                        "hit_at_5": any(
                            match["clip"] == query["expected_clip"] for match in ranked
                        ),
                        "top1_frame_correct": relevant[0],
                        "frame_hit_at_5": any(relevant),
                        "frame_recall_at_5": sum(relevant) / len(expected_times),
                        "frame_precision_at_5": sum(relevant) / len(ranked),
                        "matches": ranked,
                    }
                )
            report = {
                "dataset": dataset["dataset"],
                "model_version": MODEL_VERSION,
                "clip_count": len(events),
                "frame_count": len(timestamps) * len(events),
                "query_count": len(results),
                "top1_accuracy": sum(result["top1_correct"] for result in results) / len(results),
                "recall_at_5": sum(result["hit_at_5"] for result in results) / len(results),
                "recall_definition": "Expected clip present among the top five frame results",
                "top1_frame_accuracy": (
                    sum(result["top1_frame_correct"] for result in results) / len(results)
                ),
                "frame_hit_rate_at_5": (
                    sum(result["frame_hit_at_5"] for result in results) / len(results)
                ),
                "frame_recall_at_5": (
                    sum(result["frame_recall_at_5"] for result in results) / len(results)
                ),
                "frame_precision_at_5": (
                    sum(result["frame_precision_at_5"] for result in results) / len(results)
                ),
                "frame_hit_definition": "Any labeled relevant frame among top five results",
                "frame_recall_definition": "Mean fraction of labeled relevant frames in top five",
                "scope": dataset.get(
                    "scope", "Synthetic sanity check; no statistical claim about real footage"
                ),
                "results": results,
            }
            if "labeling" in dataset:
                report["labeling"] = dataset["labeling"]
                report["sources"] = dataset["clips"]
            print("VISUAL_SEARCH_RESULT " + json.dumps(report, sort_keys=True), flush=True)
            # Record misses honestly; this is an evaluation, not an accuracy threshold gate.
            assert list(temporary.iterdir()) == []
        assert log.read_text().count(f"Loading {MODEL_VERSION} on CPU") == 1
    finally:
        offsets.close()
        with database.connection() as connection:
            connection.execute("DELETE FROM processing_jobs WHERE video_id = ANY(%s)", (video_ids,))
            connection.execute("DELETE FROM videos WHERE id = ANY(%s)", (video_ids,))
