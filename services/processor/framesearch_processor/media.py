"""Validate local uploaded MP4 files using the real FFprobe binary."""

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 180
PROBE_TIMEOUT_SECONDS = 15

# Brands produced by common MP4 exporters, including fragmented MP4/CMAF.
# QuickTime and 3GP share FFprobe's demuxer name but are outside the MP4 contract.
MP4_BRANDS = {
    "isom",
    "mp41",
    "mp42",
    "avc1",
    "dash",
    "M4V",
    "M4VH",
    "M4VP",
    "MSNV",
    "mp71",
    "msdh",
    "msix",
    "cmfc",
    "cmfs",
    "f4v",
} | {f"iso{number}" for number in range(1, 10)}


class InvalidVideo(ValueError):
    """Terminal media error suitable for the video's processing_error."""


class MediaToolError(RuntimeError):
    """Missing/unresponsive media tooling, rather than a valid indexing result."""


@dataclass(frozen=True)
class VideoMetadata:
    duration_seconds: float
    stream_index: int
    width: int
    height: int
    start_time_seconds: float


def metadata_from_probe(document: dict[str, Any]) -> VideoMetadata:
    """Read only trusted FFprobe output; do not infer format from a filename."""
    if not isinstance(document, dict):
        raise InvalidVideo("FFprobe returned invalid metadata; export the video as MP4 again")
    container = document.get("format")
    streams = document.get("streams")
    if not isinstance(container, dict) or not isinstance(streams, list):
        raise InvalidVideo("Video metadata is missing; export the video as MP4 again")
    if "mp4" not in str(container.get("format_name", "")).split(","):
        raise InvalidVideo("The uploaded file is not MP4; export it as an MP4 video")
    tags = container.get("tags", {})
    brand = tags.get("major_brand", "") if isinstance(tags, dict) else ""
    if not isinstance(brand, str) or brand.strip() not in MP4_BRANDS:
        raise InvalidVideo("The container is not a supported MP4; export it as MP4 again")
    try:
        duration = float(container["duration"])
        start_time = float(container.get("start_time", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidVideo("Video duration/timestamps are missing or invalid") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise InvalidVideo("Video must have a finite duration greater than zero")
    if duration > MAX_DURATION_SECONDS:
        raise InvalidVideo("Video is longer than 3 minutes; upload a shorter MP4")
    if not math.isfinite(start_time):
        raise InvalidVideo("Video start timestamp is invalid; export it as MP4 again")
    candidates = []
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition", {})
        if not isinstance(disposition, dict):
            raise InvalidVideo("Video stream metadata is invalid; export it as MP4 again")
        if not disposition.get("attached_pic", 0):
            candidates.append(stream)
    if not candidates:
        raise InvalidVideo(
            "MP4 contains no playable video stream; audio/cover art is not supported"
        )
    stream = candidates[0]
    index, width, height = (stream.get(key) for key in ("index", "width", "height"))
    if (
        any(type(value) is not int for value in (index, width, height))
        or index < 0
        or width <= 0
        or height <= 0
        or not isinstance(stream.get("codec_name"), str)
        or stream["codec_name"] in ("", "unknown")
    ):
        raise InvalidVideo("Video stream dimensions or codec are invalid; export it as MP4 again")
    return VideoMetadata(duration, index, width, height, start_time)


def probe_video(
    source: Path,
    expected_size_bytes: int | None = None,
    *,
    ffprobe: str = "ffprobe",
) -> VideoMetadata:
    try:
        if not source.is_file():
            raise InvalidVideo("Source video is missing; upload the MP4 again")
        size = source.stat().st_size
    except OSError as exc:
        raise MediaToolError("Cannot read the downloaded video file") from exc
    if not 1 <= size <= MAX_VIDEO_BYTES:
        raise InvalidVideo("Video file must contain 1–104857600 bytes (maximum 100 MiB)")
    if expected_size_bytes is not None and size != expected_size_bytes:
        raise InvalidVideo(
            "Downloaded video size differs from the upload declaration; upload again"
        )
    command = [
        ffprobe,
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-show_entries",
        "format=format_name,duration,start_time:format_tags=major_brand:"
        "stream=index,codec_type,codec_name,width,height:stream_disposition=attached_pic",
        "-of",
        "json",
        str(source.resolve()),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS
        )
    except FileNotFoundError as exc:
        raise MediaToolError(
            "FFprobe is unavailable; install FFmpeg or use the processor image"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError("FFprobe timed out while inspecting the video") from exc
    except OSError as exc:
        raise MediaToolError("FFprobe could not start") from exc
    if result.returncode != 0:
        raise InvalidVideo("FFprobe could not read the video; the MP4 may be corrupt or incomplete")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaToolError("FFprobe returned malformed JSON") from exc
    return metadata_from_probe(document)
