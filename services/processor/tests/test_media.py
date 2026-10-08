import copy
import shutil
import subprocess

import pytest

from framesearch_processor.media import (
    MAX_VIDEO_BYTES,
    InvalidVideo,
    MediaToolError,
    metadata_from_probe,
    probe_video,
)


def valid_metadata():
    return {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "6.2",
            "start_time": "0",
            "tags": {"major_brand": "isom"},
        },
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 320,
                "height": 180,
                "disposition": {"attached_pic": 0},
            }
        ],
    }


def test_probe_metadata():
    metadata = metadata_from_probe(valid_metadata())
    assert metadata.duration_seconds == 6.2
    assert metadata.stream_index == 0
    assert (metadata.width, metadata.height) == (320, 180)
    assert metadata.start_time_seconds == 0


@pytest.mark.parametrize("duration", ["0", "-1", "nan", "inf", "180.01", None, "invalid"])
def test_reject_invalid_duration(duration):
    document = valid_metadata()
    document["format"]["duration"] = duration
    with pytest.raises(InvalidVideo):
        metadata_from_probe(document)


def test_accept_exactly_three_minutes():
    document = valid_metadata()
    document["format"]["duration"] = "180"
    assert metadata_from_probe(document).duration_seconds == 180


@pytest.mark.parametrize("container", [None, {}, {"format_name": "matroska,webm"}])
def test_reject_invalid_container(container):
    document = valid_metadata()
    document["format"] = container
    with pytest.raises(InvalidVideo):
        metadata_from_probe(document)


@pytest.mark.parametrize("brand", ["qt  ", "3gp4", "unknown", None])
def test_reject_non_mp4_brands(brand):
    document = valid_metadata()
    document["format"]["tags"]["major_brand"] = brand
    with pytest.raises(InvalidVideo, match="MP4"):
        metadata_from_probe(document)


@pytest.mark.parametrize("streams", [[], None, [{"codec_type": "audio"}]])
def test_reject_non_video_media(streams):
    document = valid_metadata()
    document["streams"] = streams
    with pytest.raises(InvalidVideo):
        metadata_from_probe(document)


def test_cover_art_is_not_a_video():
    document = valid_metadata()
    document["streams"][0]["disposition"]["attached_pic"] = 1
    with pytest.raises(InvalidVideo, match="no playable video"):
        metadata_from_probe(document)


def test_skip_audio_and_cover_art_before_selecting_a_video_stream():
    document = valid_metadata()
    cover = copy.deepcopy(document["streams"][0])
    cover["disposition"]["attached_pic"] = 1
    video = document["streams"][0]
    video["index"] = 2
    document["streams"] = [{"codec_type": "audio"}, cover, video]
    assert metadata_from_probe(document).stream_index == 2


@pytest.mark.parametrize(
    "key,value",
    [
        ("width", 0),
        ("height", -1),
        ("index", -1),
        ("width", True),
        ("codec_name", None),
        ("codec_name", ""),
        ("codec_name", "unknown"),
    ],
)
def test_reject_broken_video_stream(key, value):
    document = valid_metadata()
    document["streams"][0][key] = value
    with pytest.raises(InvalidVideo, match="stream dimensions or codec"):
        metadata_from_probe(document)


def test_reject_malformed_video_stream_metadata():
    document = valid_metadata()
    document["streams"][0]["disposition"] = None
    with pytest.raises(InvalidVideo, match="stream metadata"):
        metadata_from_probe(document)


def test_reject_missing_or_nonfinite_timestamps():
    document = valid_metadata()
    document["format"]["start_time"] = "nan"
    with pytest.raises(InvalidVideo, match="start timestamp"):
        metadata_from_probe(document)


def test_reject_empty_missing_and_oversize_files_before_running_ffprobe(tmp_path):
    path = tmp_path / "clip.mp4"
    with pytest.raises(InvalidVideo, match="missing"):
        probe_video(path, ffprobe="missing-ffprobe")
    path.touch()
    with pytest.raises(InvalidVideo, match="bytes"):
        probe_video(path, ffprobe="missing-ffprobe")
    with path.open("wb") as file:
        file.truncate(MAX_VIDEO_BYTES + 1)
    with pytest.raises(InvalidVideo, match="bytes"):
        probe_video(path, ffprobe="missing-ffprobe")


def test_reject_download_size_mismatch(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"a nonempty upload")
    with pytest.raises(InvalidVideo, match="upload declaration"):
        probe_video(path, expected_size_bytes=10, ffprobe="missing-ffprobe")


def test_missing_ffprobe_is_a_tool_failure(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video bytes")
    with pytest.raises(MediaToolError, match="FFprobe is unavailable"):
        probe_video(path, ffprobe="framesearch-test-missing-ffprobe")


def test_ffprobe_timeout_is_a_tool_failure(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video bytes")

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(MediaToolError, match="timed out"):
        probe_video(path)


def test_ffprobe_malformed_output_is_a_tool_failure(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video bytes")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "not JSON", ""),
    )
    with pytest.raises(MediaToolError, match="malformed JSON"):
        probe_video(path)


requires_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="real FFmpeg/FFprobe required; run these tests in the processor container",
)


def create_video(path, duration="6.2", container="mp4"):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=160x90:r=5",
            "-t",
            duration,
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-f",
            container,
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


@requires_ffmpeg
def test_real_ffprobe_does_not_depend_on_filename_extension(tmp_path):
    path = tmp_path / "uploaded-media.bin"
    create_video(path)
    metadata = probe_video(path, path.stat().st_size)
    assert metadata.duration_seconds == pytest.approx(6.2)
    assert (metadata.width, metadata.height) == (160, 90)


@requires_ffmpeg
def test_real_ffprobe_rejects_a_corrupt_file(tmp_path):
    path = tmp_path / "corrupt.mp4"
    path.write_bytes(b"these bytes are not a video")
    with pytest.raises(InvalidVideo, match="corrupt or incomplete"):
        probe_video(path)


@requires_ffmpeg
def test_real_ffprobe_rejects_another_container_named_mp4(tmp_path):
    path = tmp_path / "pretend.mp4"
    create_video(path, container="matroska")
    with pytest.raises(InvalidVideo, match="not MP4"):
        probe_video(path)


@requires_ffmpeg
def test_real_ffprobe_rejects_mov_named_mp4(tmp_path):
    path = tmp_path / "quicktime.mp4"
    create_video(path, container="mov")
    with pytest.raises(InvalidVideo, match="not a supported MP4"):
        probe_video(path)


@requires_ffmpeg
def test_real_ffprobe_rejects_audio_only_mp4(tmp_path):
    path = tmp_path / "audio.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    with pytest.raises(InvalidVideo, match="no playable video"):
        probe_video(path)


@requires_ffmpeg
def test_real_ffprobe_rejects_a_long_clip(tmp_path):
    path = tmp_path / "long.mp4"
    create_video(path, duration="181")
    with pytest.raises(InvalidVideo, match="longer than 3 minutes"):
        probe_video(path)


@requires_ffmpeg
def test_real_ffprobe_accepts_exactly_three_minutes(tmp_path):
    path = tmp_path / "limit.mp4"
    create_video(path, duration="180")
    assert probe_video(path).duration_seconds == pytest.approx(180)
