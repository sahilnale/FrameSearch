"""Verify real upload, indexing, search and private media on a running Compose stack.

Creates a small original video unless --video is supplied. Keeps its uploaded
video in the library for inspection. No database rows or volumes are deleted.
"""

import argparse
import json
import math
import os
import shlex
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.request import Request, urlopen


def request(url, *, method="GET", data=None, headers=None, status=200):
    try:
        with urlopen(
            Request(url, data=data, headers=headers or {}, method=method), timeout=30
        ) as response:
            actual, body = response.status, response.read()
    except HTTPError as error:
        actual, body = error.code, error.read()
    if actual != status:
        raise RuntimeError(
            f"{method} {urlsplit(url).path}: expected {status}, got {actual}: {body[:300]!r}"
        )
    return body


def api(base, path, payload=None, *, status=200):
    return json.loads(
        request(
            base + path,
            method="POST" if payload is not None else "GET",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"} if payload is not None else {},
            status=status,
        )
    )


def wait_ready(base, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            api(base, "/health/ready")
            return
        except (RuntimeError, URLError, TimeoutError):
            time.sleep(2)
    raise RuntimeError("API did not become ready; inspect processor/model and dependency logs")


def wait_video(base, video_id, expected, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        video = api(base, f"/api/v1/videos/{video_id}")
        if video["status"] == expected:
            return video
        if video["status"] == "failed" and expected != "failed":
            raise RuntimeError(f"Indexing failed: {video.get('processing_error')}")
        time.sleep(1)
    raise RuntimeError(f"Video {video_id} did not reach {expected} within {timeout}s")


def unsigned(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def compose(*arguments, **kwargs):
    command = os.environ.get(
        "FRAMESEARCH_COMPOSE_COMMAND",
        "docker compose --env-file .env -f infra/docker-compose.yml --profile app",
    )
    return subprocess.run([*shlex.split(command), *arguments], check=True, **kwargs)


def sample_video():
    # Fragmented MP4 permits writing genuine FFmpeg output to stdout.
    return compose(
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
        "color=c=red:s=320x180:r=10",
        "-t",
        "6.2",
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


def upload(base, data, filename):
    result = api(
        base,
        "/api/v1/videos/upload-url",
        {
            "filename": filename,
            "content_type": "video/mp4",
            "size_bytes": len(data),
        },
        status=201,
    )
    if parse_qs(urlsplit(result["upload_url"]).query).get("X-Amz-Expires") != ["900"]:
        raise RuntimeError("Upload URL expiration differs from the shared contract")
    request(result["upload_url"], method="PUT", data=data, headers={"Content-Type": "video/mp4"})
    return result


def verify_ready_video(base, video_id, data, query):
    results = api(base, "/api/v1/search", {"query": query, "video_id": video_id, "limit": 5})[
        "results"
    ]
    if not results:
        raise RuntimeError("Ready video has no search results")
    for result in results:
        if result["video_id"] != video_id or not math.isfinite(result["similarity"]):
            raise RuntimeError("Search result violates the video filter or similarity contract")
        if not request(result["thumbnail_url"]).startswith(b"\xff\xd8"):
            raise RuntimeError("Thumbnail is not a JPEG")
        request(unsigned(result["thumbnail_url"]), status=403)
    playback = api(base, f"/api/v1/videos/{video_id}/playback-url")
    if playback["expires_in_seconds"] != 900 or request(playback["url"]) != data:
        raise RuntimeError("Playback bytes or expiry differ from the original upload")
    if request(playback["url"], headers={"Range": "bytes=0-31"}, status=206) != data[:32]:
        raise RuntimeError("Playback byte-range mismatch")
    request(unsigned(playback["url"]), status=403)
    return len(results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8080")
    parser.add_argument(
        "--video",
        type=Path,
        help="existing valid MP4; otherwise generate a red clip inside the processor",
    )
    parser.add_argument("--query", default="a red screen")
    parser.add_argument(
        "--timeout", type=int, default=600, help="seconds allowed for readiness and indexing"
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    base = args.api_url.rstrip("/")
    wait_ready(base, args.timeout)
    data = args.video.read_bytes() if args.video else sample_video()
    uploaded = upload(base, data, args.video.name if args.video else "compose-smoke-red.mp4")
    video_id = uploaded["video_id"]
    for _ in range(2):
        api(base, f"/api/v1/videos/{video_id}/complete", {})
    video = wait_video(base, video_id, "ready", args.timeout)
    count = verify_ready_video(base, video_id, data, args.query)
    print(
        json.dumps(
            {
                "status": "passed",
                "video_id": video_id,
                "duration_seconds": video["duration_seconds"],
                "search_results": count,
                "checks": [
                    "real_upload",
                    "indexing",
                    "repeat_complete",
                    "search",
                    "jpeg_thumbnails",
                    "private_objects",
                    "playback_bytes",
                    "range_206",
                    "expiry_900",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
