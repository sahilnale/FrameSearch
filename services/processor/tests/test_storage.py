import base64
import hashlib
import io
from dataclasses import replace
from uuid import UUID

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from PIL import Image

from framesearch_processor.media import MAX_VIDEO_BYTES, InvalidVideo
from framesearch_processor.sampling import VideoFrame
from framesearch_processor.settings import MODEL_VERSION, StorageSettings
from framesearch_processor.storage import ObjectStorage, StorageError, thumbnail_key

VIDEO_ID = UUID("28f90b72-cdef-41bd-a04b-e1c3534a6a00")


@pytest.fixture
def settings():
    return StorageSettings("http://127.0.0.1:1", "test-access", "test-secret", "framesearch")


def test_storage_reads_shared_environment_and_hides_credentials(monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT_INTERNAL", "http://minio:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "local-test-access")
    monkeypatch.setenv("S3_SECRET_KEY", "local-test-secret")
    monkeypatch.setenv("S3_BUCKET", "framesearch")
    configuration = StorageSettings.from_env()
    assert configuration.endpoint == "http://minio:9000"
    assert configuration.access_key == "local-test-access"
    assert configuration.secret_key == "local-test-secret"
    assert "local-test-access" not in repr(configuration)
    assert "local-test-secret" not in repr(configuration)


@pytest.mark.parametrize("name", ["S3_ACCESS_KEY", "S3_SECRET_KEY"])
def test_storage_requires_credentials(monkeypatch, name):
    monkeypatch.setenv("S3_ACCESS_KEY", "test-access")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret")
    monkeypatch.delenv(name)
    with pytest.raises(ValueError, match=name):
        StorageSettings.from_env()


@pytest.mark.parametrize(
    "endpoint",
    [
        "minio:9000",
        "file:///tmp",
        "http://",
        "http://user:secret@minio:9000",
        "http://minio:9000/bucket",
        "http://minio:9000?secret=value",
        "http://minio:0",
        "http://minio:99999",
    ],
)
def test_storage_rejects_invalid_endpoints(settings, endpoint):
    with pytest.raises(ValueError, match="S3_ENDPOINT_INTERNAL"):
        replace(settings, endpoint=endpoint)


@pytest.mark.parametrize("bucket", ["", "ab", "UPPER", "has/slash", "has space", "a..b"])
def test_storage_rejects_invalid_buckets(settings, bucket):
    with pytest.raises(ValueError, match="S3_BUCKET"):
        replace(settings, bucket=bucket)


@pytest.fixture
def storage(settings):
    store = ObjectStorage(settings)
    with Stubber(store._client) as stub:
        yield store, stub
        stub.assert_no_pending_responses()
    store.close()


def add_download(stub, data, *, declared=None, content_type="video/mp4", key="video.mp4"):
    raw = io.BytesIO(data)
    body = StreamingBody(raw, len(data) if declared is None else declared)
    stub.add_response(
        "get_object",
        {
            "Body": body,
            "ContentLength": len(data) if declared is None else declared,
            "ContentType": content_type,
        },
        {"Bucket": "framesearch", "Key": key},
    )
    return raw


def test_download_streams_multiple_chunks_and_cleans_up(storage, tmp_path):
    store, stub = storage
    data = b"real stream test bytes" * 10000
    raw = add_download(stub, data, key="videos/../../untrusted-name.mp4")
    with store.downloaded_video(
        "videos/../../untrusted-name.mp4", len(data), temp_root=tmp_path
    ) as path:
        assert path.name == "source.mp4"
        assert path.parent.parent == tmp_path
        assert path.read_bytes() == data
        assert raw.closed
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_cleanup_when_consumer_fails(storage, tmp_path):
    store, stub = storage
    add_download(stub, b"video bytes")
    with pytest.raises(RuntimeError, match="downstream failed"):
        with store.downloaded_video("video.mp4", 11, temp_root=tmp_path) as path:
            assert path.is_file()
            raise RuntimeError("downstream failed")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("size", [0, -1, MAX_VIDEO_BYTES + 1, True, 1.5])
def test_invalid_declared_size_is_rejected_before_storage_access(storage, tmp_path, size):
    store, _ = storage
    with pytest.raises(ValueError, match="declared source size"):
        with store.downloaded_video("video.mp4", size, temp_root=tmp_path):
            pytest.fail("invalid declaration must not be downloaded")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key", ["", "x" * 1025, "α" * 600, None])
def test_invalid_key_is_rejected_before_storage_access(storage, key):
    store, _ = storage
    with pytest.raises(ValueError, match="source object key"):
        with store.downloaded_video(key, 1):
            pytest.fail("invalid key must not be downloaded")


@pytest.mark.parametrize(
    "problem", ["metadata-size", "content-type", "short-stream", "long-stream"]
)
def test_bad_metadata_or_incomplete_stream_closes_body_and_cleans_up(storage, tmp_path, problem):
    store, stub = storage
    raw = add_download(
        stub,
        b"1234"
        if problem == "short-stream"
        else b"123456"
        if problem == "long-stream"
        else b"12345",
        declared=6 if problem == "metadata-size" else 5,
        content_type="text/plain" if problem == "content-type" else "video/mp4",
    )
    with pytest.raises(StorageError if problem == "short-stream" else InvalidVideo):
        with store.downloaded_video("video.mp4", 5, temp_root=tmp_path):
            pytest.fail("invalid source must not reach the consumer")
    assert raw.closed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "code,error",
    [
        ("NoSuchKey", InvalidVideo),
        ("NoSuchBucket", StorageError),
        ("AccessDenied", StorageError),
        ("SlowDown", StorageError),
    ],
)
def test_source_errors_are_distinguished_from_storage_failures(storage, tmp_path, code, error):
    store, stub = storage
    stub.add_client_error(
        "get_object", code, http_status_code=404 if code.startswith("NoSuch") else 503
    )
    with pytest.raises(error):
        with store.downloaded_video("video.mp4", 5, temp_root=tmp_path):
            pytest.fail("failed storage request must not reach the consumer")
    assert list(tmp_path.iterdir()) == []


def test_missing_bucket_is_an_infrastructure_failure(storage):
    store, stub = storage
    stub.add_client_error("head_bucket", "NoSuchBucket", http_status_code=404)
    with pytest.raises(StorageError, match="bucket is unavailable"):
        store.check_bucket()


def test_thumbnail_keys_are_stable_and_scoped_to_video_and_model():
    key = thumbnail_key(VIDEO_ID, 3003)
    assert key == f"thumbnails/{VIDEO_ID}/{MODEL_VERSION}/003003.jpg"
    assert thumbnail_key(VIDEO_ID, 3003) == key
    assert thumbnail_key(UUID(int=1), 3003) != key
    assert thumbnail_key(VIDEO_ID, 3004) != key


@pytest.mark.parametrize("timestamp", [-1, 180000, True, 3.5])
def test_invalid_thumbnail_timestamps_are_rejected(timestamp):
    with pytest.raises(ValueError, match="timestamp"):
        thumbnail_key(VIDEO_ID, timestamp)


def test_thumbnail_model_version_must_match_embeddings():
    with pytest.raises(ValueError, match="model_version"):
        thumbnail_key(VIDEO_ID, 3000, "different-model")


def test_thumbnail_upload_has_integrity_and_model_metadata(storage, tmp_path):
    store, stub = storage
    path = tmp_path / "thumbnail.jpg"
    Image.new("RGB", (160, 90), "red").save(path)
    data = path.read_bytes()
    key = thumbnail_key(VIDEO_ID, 3000)
    stub.add_response(
        "put_object",
        {},
        {
            "Bucket": "framesearch",
            "Key": key,
            "Body": data,
            "ContentType": "image/jpeg",
            "ContentLength": len(data),
            "ContentMD5": base64.b64encode(
                hashlib.md5(data, usedforsecurity=False).digest()
            ).decode(),
            "Metadata": {
                "video-id": str(VIDEO_ID),
                "timestamp-ms": "3000",
                "model-version": MODEL_VERSION,
            },
        },
    )
    assert store.upload_thumbnail(VIDEO_ID, VideoFrame(3000, path)) == key
    assert path.read_bytes() == data


def test_failed_thumbnail_upload_does_not_return_a_storage_key(storage, tmp_path):
    store, stub = storage
    path = tmp_path / "thumbnail.jpg"
    Image.new("RGB", (160, 90), "red").save(path)
    stub.add_client_error("put_object", "SlowDown", http_status_code=503)
    with pytest.raises(StorageError, match="upload failed"):
        store.upload_thumbnail(VIDEO_ID, VideoFrame(0, path))


@pytest.mark.parametrize("kind", ["missing", "corrupt", "oversize-pixels", "oversize-bytes"])
def test_invalid_thumbnails_are_not_uploaded(storage, tmp_path, kind):
    store, _ = storage
    path = tmp_path / "thumbnail.jpg"
    if kind == "corrupt":
        path.write_bytes(b"not an image")
    elif kind == "oversize-pixels":
        Image.new("RGB", (641, 10)).save(path)
    elif kind == "oversize-bytes":
        path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(StorageError if kind in ("missing", "corrupt") else ValueError):
        store.upload_thumbnail(VIDEO_ID, VideoFrame(0, path))
