import io
import json
import math
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest
from PIL import Image

from framesearch_processor import sampling
from framesearch_processor.media import InvalidVideo, MediaToolError, VideoMetadata
from framesearch_processor.sampling import extracted_frames


def frame_log(pts=(0, 3000, 6000), time_base="1/1000"):
    lines = [f"[showinfo@framesearch @ test] config in time_base: {time_base}, frame_rate: 5/1"]
    lines.extend(
        f"[showinfo@framesearch @ test] n: {index:4d} pts: {value:6d} pts_time:rounded"
        for index, value in enumerate(pts)
    )
    return "\n".join(lines)


def test_timestamps_use_exact_pts_instead_of_rounded_log_time():
    log = frame_log((0, 90090, 180180), "1/30000")
    assert sampling._timestamps_from_log(io.StringIO(log)) == [0, 3003, 6006]


def test_timestamp_resolution_never_rounds_up_into_next_millisecond():
    log = frame_log((0, 30009, 1799999), "1/10000")
    assert sampling._timestamps_from_log(io.StringIO(log)) == [0, 3000, 179999]


def test_sparse_timeline_skips_empty_intervals():
    assert sampling._timestamps_from_log(io.StringIO(frame_log((0, 9500, 12000)))) == [
        0,
        9500,
        12000,
    ]


@pytest.mark.parametrize("pts", [(0, 2000), (0, 0), (-1,), (180000,), (6000, 3000)])
def test_invalid_or_duplicate_timeline_is_rejected(pts):
    with pytest.raises(InvalidVideo, match="timeline"):
        sampling._timestamps_from_log(io.StringIO(frame_log(pts)))


def test_missing_timestamp_is_rejected():
    with pytest.raises(InvalidVideo, match="timestamps are missing"):
        sampling._timestamps_from_log(io.StringIO(frame_log().replace("pts:      0", "pts: NOPTS")))


@pytest.mark.parametrize("time_base", ["0/1000", "1/0"])
def test_invalid_time_base_is_rejected(time_base):
    with pytest.raises(MediaToolError, match="time base"):
        sampling._timestamps_from_log(io.StringIO(frame_log(time_base=time_base)))


@pytest.fixture
def local_source(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"test-only input; real media tests generate MP4 below")
    monkeypatch.setattr(
        sampling, "probe_video", lambda *args, **kwargs: VideoMetadata(6.2, 0, 160, 90, 0)
    )
    return source


def test_output_names_and_cleanup_after_consuming_frames(local_source, tmp_path, monkeypatch):
    def decode(command, **kwargs):
        directory = Path(command[-1]).parent
        for index in range(3):
            Image.new("RGB", (160, 90), "red").save(directory / f"sample-{index:03d}.jpg")
        kwargs["stderr"].write(frame_log())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", decode)
    with extracted_frames(local_source, temp_root=tmp_path) as clip:
        assert clip.metadata.duration_seconds == 6.2
        assert [frame.timestamp_ms for frame in clip.frames] == [0, 3000, 6000]
        assert [frame.path.name for frame in clip.frames] == [
            "frame-000000.jpg",
            "frame-003000.jpg",
            "frame-006000.jpg",
        ]
        directory = clip.frames[0].path.parent
        assert all(frame.path.is_file() for frame in clip.frames)
    assert not directory.exists()
    assert list(tmp_path.iterdir()) == [local_source]


@pytest.mark.parametrize("failure", ["missing", "timeout", "decode", "consumer"])
def test_cleanup_after_tool_or_consumer_failure(local_source, tmp_path, monkeypatch, failure):
    def decode(command, **kwargs):
        directory = Path(command[-1]).parent
        Image.new("RGB", (160, 90)).save(directory / "sample-000.jpg")
        kwargs["stderr"].write(frame_log((0,)))
        if failure == "missing":
            raise FileNotFoundError("test-only missing tool")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 1 if failure == "decode" else 0)

    monkeypatch.setattr(subprocess, "run", decode)
    expected = {"missing": MediaToolError, "timeout": MediaToolError, "decode": InvalidVideo}
    with pytest.raises(expected.get(failure, RuntimeError)):
        with extracted_frames(local_source, temp_root=tmp_path):
            raise RuntimeError("downstream embedding/upload failed")
    assert list(tmp_path.iterdir()) == [local_source]


@pytest.mark.parametrize("output", ["none", "missing", "unreadable", "oversize"])
def test_incomplete_or_invalid_outputs_are_rejected(local_source, tmp_path, monkeypatch, output):
    def decode(command, **kwargs):
        directory = Path(command[-1]).parent
        if output != "none":
            kwargs["stderr"].write(frame_log((0,)))
        if output == "unreadable":
            (directory / "sample-000.jpg").write_bytes(b"not a JPEG")
        if output == "oversize":
            Image.new("RGB", (641, 90)).save(directory / "sample-000.jpg")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", decode)
    with pytest.raises(InvalidVideo if output == "none" else MediaToolError):
        with extracted_frames(local_source, temp_root=tmp_path):
            pytest.fail("invalid media must not reach the consumer")
    assert list(tmp_path.iterdir()) == [local_source]


requires_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="real FFmpeg/FFprobe required; run in the processor container",
)


def make_video(tmp_path, *, duration="6.2", rate="5", size="160x90", filters=None, extra=()):
    path = tmp_path / "generated.mp4"
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate={rate}",
        "-t",
        duration,
    ]
    if filters:
        command.extend(["-vf", filters])
    command.extend(["-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", *extra, str(path)])
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    return path


def decoded_timestamps(path):
    """Independent FFprobe reference, using every source frame's actual PTS."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_streams",
            "-show_entries",
            "stream=time_base:frame=best_effort_timestamp",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    document = json.loads(result.stdout)
    time_base = Fraction(document["streams"][0]["time_base"])
    return [int(frame["best_effort_timestamp"]) * time_base for frame in document["frames"]]


@requires_ffmpeg
@pytest.mark.parametrize(
    "duration,expected", [("0.2", [0]), ("3", [0]), ("3.2", [0, 3000]), ("6.2", [0, 3000, 6000])]
)
def test_real_sampling_boundaries(tmp_path, duration, expected):
    source = make_video(tmp_path, duration=duration)
    with extracted_frames(source, source.stat().st_size, temp_root=tmp_path) as clip:
        assert [frame.timestamp_ms for frame in clip.frames] == expected
        paths = [frame.path for frame in clip.frames]
        for path in paths:
            with Image.open(path) as image:
                assert image.format == "JPEG"
                assert image.size == (160, 90)
    assert all(not path.exists() for path in paths)
    assert source.is_file()


@requires_ffmpeg
def test_real_sampling_caps_three_minute_clip_at_60(tmp_path):
    source = make_video(tmp_path, duration="180")
    with extracted_frames(source) as clip:
        assert len(clip.frames) == 60
        assert [frame.timestamp_ms for frame in clip.frames] == list(range(0, 180000, 3000))


@requires_ffmpeg
def test_real_fractional_rate_stays_on_grid_without_relabeling_actual_frames(tmp_path):
    source = make_video(tmp_path, duration="39.1", rate="30000/1001")
    actual = decoded_timestamps(source)
    expected = [next(time for time in actual if time >= target) for target in range(0, 40, 3)]
    with extracted_frames(source) as clip:
        assert [frame.timestamp_ms for frame in clip.frames] == [
            int(time * 1000) for time in expected
        ]
        assert clip.frames[1].timestamp_ms == 3003
        # Sampling relative to the previous selected frame would drift to 36036 here.
        assert clip.frames[12].timestamp_ms == 36002


@requires_ffmpeg
def test_real_variable_rate_preserves_pts_and_does_not_fill_a_gap(tmp_path):
    source = make_video(
        tmp_path,
        duration="13",
        rate="10",
        filters="select='lt(t,1)+gte(t,9.5)'",
        extra=("-fps_mode", "vfr"),
    )
    actual = decoded_timestamps(source)
    assert not any(1 <= time < Fraction(19, 2) for time in actual)
    with extracted_frames(source) as clip:
        assert [frame.timestamp_ms for frame in clip.frames] == [0, 9500, 12000]


@requires_ffmpeg
@pytest.mark.parametrize(
    "offset,expected",
    [("0.25", [250, 3050, 6050]), ("2", [2000, 3000, 6000]), ("10", [10000, 12000, 15000])],
)
def test_real_sampling_preserves_container_start_offset(tmp_path, offset, expected):
    source = make_video(tmp_path, extra=("-output_ts_offset", offset))
    with extracted_frames(source) as clip:
        assert clip.metadata.start_time_seconds == pytest.approx(float(offset))
        assert [frame.timestamp_ms for frame in clip.frames] == expected


@requires_ffmpeg
def test_real_shifted_timestamps_match_the_actual_frame_content(tmp_path):
    source = make_video(
        tmp_path,
        filters="drawbox=c=red:t=fill,drawbox=c=blue:t=fill:enable='gte(t,3)',"
        "drawbox=c=green:t=fill:enable='gte(t,6)'",
        extra=("-output_ts_offset", "10"),
    )
    with extracted_frames(source) as clip:
        for frame, expected_color in zip(clip.frames, ("red", "red", "blue"), strict=True):
            with Image.open(frame.path) as image:
                red, green, blue = image.getpixel((80, 45))
            if expected_color == "red":
                assert red > 240 and green < 10 and blue < 10
            else:
                assert blue > 240 and red < 10 and green < 10
        assert [frame.timestamp_ms for frame in clip.frames] == [10000, 12000, 15000]


@requires_ffmpeg
def test_real_sampling_maps_video_after_an_audio_stream(tmp_path):
    source = tmp_path / "audio-first.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=6.2",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=5",
            "-t",
            "6.2",
            "-map",
            "0:a",
            "-map",
            "1:v",
            "-c:a",
            "aac",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    with extracted_frames(source) as clip:
        assert clip.metadata.stream_index == 1
        assert [frame.timestamp_ms for frame in clip.frames] == [0, 3000, 6000]


@requires_ffmpeg
def test_real_retry_extraction_is_deterministic(tmp_path):
    source = make_video(tmp_path)
    with extracted_frames(source) as first:
        first_outputs = [
            (frame.timestamp_ms, frame.path.name, frame.path.read_bytes()) for frame in first.frames
        ]
    with extracted_frames(source) as second:
        second_outputs = [
            (frame.timestamp_ms, frame.path.name, frame.path.read_bytes())
            for frame in second.frames
        ]
    assert first_outputs == second_outputs


@requires_ffmpeg
def test_real_cleanup_when_frame_consumer_fails(tmp_path):
    source = make_video(tmp_path)
    with pytest.raises(RuntimeError, match="downstream failed"):
        with extracted_frames(source, temp_root=tmp_path) as clip:
            paths = [frame.path for frame in clip.frames]
            assert all(path.is_file() for path in paths)
            raise RuntimeError("downstream failed")
    assert all(not path.exists() for path in paths)
    assert list(tmp_path.iterdir()) == [source]


@requires_ffmpeg
@pytest.mark.parametrize(
    "size,filters,expected",
    [
        ("1280x720", None, (640, 360)),
        ("720x1280", None, (360, 640)),
        ("160x90", "setsar=2", (320, 90)),
    ],
)
def test_real_thumbnails_preserve_display_aspect_ratio(tmp_path, size, filters, expected):
    source = make_video(tmp_path, duration="0.2", size=size, filters=filters)
    with extracted_frames(source) as clip:
        with Image.open(clip.frames[0].path) as image:
            assert image.size == expected


@requires_ffmpeg
def test_real_decode_failure_cleans_partial_thumbnails(tmp_path):
    source = make_video(tmp_path, extra=("-movflags", "+faststart"))
    # Keep the MP4 header/sample table readable, but destroy compressed packets.
    content = bytearray(source.read_bytes())
    packets = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_packets",
            "-show_entries",
            "packet=pos,pts_time",
            "-of",
            "json",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = next(
        int(packet["pos"])
        for packet in json.loads(packets.stdout)["packets"]
        if float(packet["pts_time"]) >= 4
    )
    content[payload:] = b"\x00" * (len(content) - payload)
    source.write_bytes(content)
    with pytest.raises(InvalidVideo, match="could not decode"):
        with extracted_frames(source, temp_root=tmp_path):
            pytest.fail("corrupt packets must not produce an indexing result")
    assert list(tmp_path.iterdir()) == [source]


@requires_ffmpeg
@pytest.mark.real_model
@pytest.mark.skipif(os.getenv("FRAMESEARCH_REAL_MODEL_TEST") != "1", reason="opt-in real model")
def test_real_decoded_frames_produce_real_clip_vectors(tmp_path):
    from framesearch_processor.embeddings import OpenClipEmbedder
    from framesearch_processor.settings import MODEL_VERSION, Settings

    source = make_video(tmp_path)
    model = OpenClipEmbedder(Settings.from_env())
    with extracted_frames(source) as clip:
        vectors = model.embed_images([frame.path for frame in clip.frames])
        assert len(vectors) == len(clip.frames) == 3
        assert model.model_version == MODEL_VERSION
        for vector in vectors:
            assert len(vector) == 512
            assert all(math.isfinite(value) for value in vector)
            assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1, abs=1e-6)
        assert vectors[0] != vectors[1]
