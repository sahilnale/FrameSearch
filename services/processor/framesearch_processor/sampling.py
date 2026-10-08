"""Decode actual video frames and retain their playback timestamps."""

import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO

from PIL import Image

from .media import MAX_DURATION_SECONDS, InvalidVideo, MediaToolError, VideoMetadata, probe_video
from .settings import MAX_FRAMES

SAMPLE_INTERVAL_SECONDS = 3
THUMBNAIL_MAX_EDGE = 640
EXTRACTION_TIMEOUT_SECONDS = 600
SHOWINFO_NAME = "showinfo@framesearch"
TIME_BASE = re.compile(r"config in time_base:\s*(\d+)/(\d+)")
FRAME_PTS = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:")


@dataclass(frozen=True)
class VideoFrame:
    timestamp_ms: int
    path: Path


@dataclass(frozen=True)
class ExtractedVideo:
    metadata: VideoMetadata
    frames: tuple[VideoFrame, ...]


def _timestamps_from_log(log: TextIO) -> list[int]:
    """Use integer PTS and rational time base, not rounded pts_time log values."""
    timestamps = []
    time_base = None
    previous_bucket = -1
    for line in log:
        if SHOWINFO_NAME not in line:
            continue
        if match := TIME_BASE.search(line):
            numerator, denominator = map(int, match.groups())
            if numerator <= 0 or denominator <= 0:
                raise MediaToolError("FFmpeg returned an invalid frame time base")
            time_base = Fraction(numerator, denominator)
        elif " n:" in line:
            match = FRAME_PTS.search(line)
            if match is None or time_base is None:
                raise InvalidVideo("Video frame timestamps are missing; export the MP4 again")
            index, pts = map(int, match.groups())
            seconds = pts * time_base
            bucket = seconds // SAMPLE_INTERVAL_SECONDS
            if (
                index != len(timestamps)
                or not 0 <= seconds < MAX_DURATION_SECONDS
                or bucket <= previous_bucket
                or len(timestamps) >= MAX_FRAMES
            ):
                raise InvalidVideo("Video frame timeline is invalid; export the MP4 again")
            # Floor to the stored millisecond resolution; never label a frame in the future.
            timestamps.append(int(seconds * 1000))
            previous_bucket = bucket
    return timestamps


def _decode(source: Path, metadata: VideoMetadata, directory: Path, ffmpeg: str) -> None:
    # Select the first available frame at/after each 0,3,6,... grid point.
    # Anchoring to the grid avoids cumulative drift at fractional frame rates.
    # Empty intervals in sparse/VFR media are skipped rather than duplicating frames.
    select = (
        f"gte(t,0)*lt(t,{MAX_DURATION_SECONDS})*lt(selected_n,{MAX_FRAMES})*"
        f"(isnan(prev_selected_t)+gte(t,{SAMPLE_INTERVAL_SECONDS}*"
        f"(floor(prev_selected_t/{SAMPLE_INTERVAL_SECONDS})+1)))"
    )
    # Compute dimensions from display aspect ratio, including non-square pixels.
    # Scale only selected frames, within 640x640, then emit square-pixel JPEGs.
    factor = f"min(1,min({THUMBNAIL_MAX_EDGE}/(iw*sar),{THUMBNAIL_MAX_EDGE}/ih))"
    filters = (
        f"select='{select}',{SHOWINFO_NAME}=checksum=0,"
        f"scale=w='max(1,trunc(iw*sar*{factor}))':"
        f"h='max(1,trunc(ih*{factor}))',setsar=1"
    )
    # Preserve the source's playback timeline, including a nonzero start offset.
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-xerror",
        "-copyts",
        "-filter_threads",
        "1",
        "-threads",
        "2",
        "-err_detect",
        "explode",
        "-protocol_whitelist",
        "file",
        "-i",
        str(source.resolve()),
        "-map",
        f"0:{metadata.stream_index}",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        filters,
        "-fps_mode",
        "passthrough",
        "-c:v",
        "mjpeg",
        "-threads",
        "1",
        "-q:v",
        "3",
        "-start_number",
        "0",
        str(directory / "sample-%03d.jpg"),
    ]
    # Put potentially verbose decoder diagnostics on disk instead of retaining them in RAM.
    with (directory / "ffmpeg.log").open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=log,
                timeout=EXTRACTION_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise MediaToolError(
                "FFmpeg is unavailable; install FFmpeg or use the processor image"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaToolError("FFmpeg timed out while decoding the video") from exc
        except OSError as exc:
            raise MediaToolError("FFmpeg could not start") from exc
    if result.returncode != 0:
        raise InvalidVideo(
            "FFmpeg could not decode the video; it may be corrupt or use an unsupported codec. "
            "Export it as MP4 again"
        )


def _collect_frames(directory: Path) -> tuple[VideoFrame, ...]:
    with (directory / "ffmpeg.log").open(encoding="utf-8", errors="replace") as log:
        timestamps = _timestamps_from_log(log)
    paths = sorted(directory.glob("sample-*.jpg"))
    if not timestamps and not paths:
        raise InvalidVideo("Video produced no playable frames; export the MP4 again")
    if len(paths) != len(timestamps):
        raise MediaToolError("FFmpeg frames and their timestamps do not match")
    frames = []
    for index, (timestamp, path) in enumerate(zip(timestamps, paths, strict=True)):
        if path.name != f"sample-{index:03d}.jpg":
            raise MediaToolError("FFmpeg did not produce the complete frame sequence")
        try:
            with Image.open(path) as image:
                if image.format != "JPEG" or not all(
                    1 <= dimension <= THUMBNAIL_MAX_EDGE for dimension in image.size
                ):
                    raise ValueError("invalid thumbnail dimensions/format")
                image.load()
        except (OSError, ValueError) as exc:
            raise MediaToolError("FFmpeg produced an unreadable thumbnail") from exc
        destination = directory / f"frame-{timestamp:06d}.jpg"
        path.rename(destination)
        frames.append(VideoFrame(timestamp, destination))
    return tuple(frames)


@contextmanager
def extracted_frames(
    source: Path,
    expected_size_bytes: int | None = None,
    *,
    temp_root: Path | None = None,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
) -> Iterator[ExtractedVideo]:
    """Frame paths exist only inside this context; all outputs are always cleaned up."""
    metadata = probe_video(source, expected_size_bytes, ffprobe=ffprobe)
    with TemporaryDirectory(prefix="framesearch-frames-", dir=temp_root) as temporary:
        directory = Path(temporary)
        _decode(source, metadata, directory, ffmpeg)
        yield ExtractedVideo(metadata, _collect_frames(directory))
