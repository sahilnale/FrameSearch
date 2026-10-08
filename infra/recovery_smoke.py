"""Check public failed-video retry and stopped-worker stale recovery.

Run only on a dedicated test Compose project: this command stops its sole worker
and ages one test job to exercise the default 15-minute recovery threshold.
Uploaded test videos are retained. No existing video rows are changed.
"""

import argparse
import json
import subprocess
import time
from uuid import UUID

from smoke import (
    api,
    compose,
    request,
    sample_video,
    upload,
    verify_ready_video,
    wait_ready,
    wait_video,
)


def sql(statement):
    return (
        compose(
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -At',
            input=statement.encode(),
            stdout=subprocess.PIPE,
        )
        .stdout.decode()
        .strip()
    )


def retry_check(base, timeout):
    good = sample_video()
    uploaded = upload(base, b"x" * len(good), "recovery-corrupt.mp4")
    video_id = uploaded["video_id"]
    api(base, f"/api/v1/videos/{video_id}/complete", {})
    failed = wait_video(base, video_id, "failed", timeout)
    if not failed["processing_error"]:
        raise RuntimeError("Failed upload did not expose its processing error")
    api(base, f"/api/v1/videos/{video_id}/playback-url", status=409)
    # Repair the same object with equal-sized valid bytes using the original
    # unexpired signed upload URL; no metadata or queue rows are seeded.
    request(uploaded["upload_url"], method="PUT", data=good, headers={"Content-Type": "video/mp4"})
    api(base, f"/api/v1/videos/{video_id}/retry", {})
    ready = wait_video(base, video_id, "ready", timeout)
    if ready["processing_error"] is not None:
        raise RuntimeError("Successful retry retained the old processing error")
    verify_ready_video(base, video_id, good, "a red screen")
    return video_id


def stale_check(base, timeout):
    # Sixty genuine samples keep indexing active long enough to stop a real
    # claimed job. The source remains valid and within the 180-second limit.
    good = compose(
        "exec",
        "-T",
        "processor",
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x180:r=10",
        "-t",
        "179.9",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "frag_keyframe+empty_moov",
        "-f",
        "mp4",
        "pipe:1",
        stdout=subprocess.PIPE,
    ).stdout
    uploaded = upload(base, good, "recovery-interrupted-blue.mp4")
    video_id = str(UUID(uploaded["video_id"]))
    api(base, f"/api/v1/videos/{video_id}/complete", {})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = api(base, f"/api/v1/videos/{video_id}")["status"]
        if status == "processing":
            break
        if status in {"failed", "ready"}:
            raise RuntimeError(f"Could not interrupt an active job; it already became {status}")
        time.sleep(0.02)
    else:
        raise RuntimeError("Worker never claimed the interruption-test video")
    compose("stop", "--timeout", "0", "processor")
    try:
        if (
            sql(
                f"SELECT count(*) FROM processing_jobs WHERE video_id='{video_id}' AND status='processing';"
            )
            != "1"
        ):
            raise RuntimeError("Worker stop did not leave the expected claimed job")
        sql(
            f"UPDATE processing_jobs SET updated_at=now()-interval '16 minutes' WHERE video_id='{video_id}' AND status='processing';"
        )
        compose(
            "run",
            "--rm",
            "--no-deps",
            "api",
            "--reconcile",
            "--include-stale",
            "--processor-stopped",
        )
        if api(base, f"/api/v1/videos/{video_id}")["status"] != "queued":
            raise RuntimeError("Stale recovery did not requeue the interrupted video")
    finally:
        # Leave the test worker running even if an assertion/reconciliation fails.
        compose("start", "processor")
    wait_ready(base, timeout)
    wait_video(base, video_id, "ready", timeout)
    verify_ready_video(base, video_id, good, "a blue screen")
    state = sql(
        f"SELECT status || ':' || attempt_count FROM processing_jobs WHERE video_id='{video_id}';"
    )
    if state != "completed:2":
        raise RuntimeError(f"Expected exactly two claims of the interrupted job, got {state}")
    frames = sql(f"SELECT count(*) FROM video_frames WHERE video_id='{video_id}';")
    if frames != "60":
        raise RuntimeError(f"Expected sixty unique recovered frames, got {frames}")
    return video_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8080")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--allow-worker-stop",
        action="store_true",
        help="confirm this is a dedicated test project with one worker",
    )
    args = parser.parse_args()
    if not args.allow_worker_stop or args.timeout <= 0:
        parser.error("a positive timeout and --allow-worker-stop are required")
    base = args.api_url.rstrip("/")
    wait_ready(base, args.timeout)
    retry_id = retry_check(base, args.timeout)
    print(f"Public failed -> retry -> ready passed: {retry_id}", flush=True)
    stale_id = stale_check(base, args.timeout)
    print(
        json.dumps(
            {
                "status": "passed",
                "retry_video_id": retry_id,
                "interrupted_video_id": stale_id,
                "checks": [
                    "public_corrupt_upload",
                    "failed_error",
                    "public_retry",
                    "ready_playback",
                    "worker_interruption",
                    "stale_reconcile",
                    "same_job_two_claims",
                    "sixty_unique_frames",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
