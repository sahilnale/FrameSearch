"""Opt-in checks against a real, disposable local MinIO server."""

import io
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from uuid import uuid4

import pytest
from PIL import Image
from test_sampling import make_video, requires_ffmpeg

from framesearch_processor.media import InvalidVideo
from framesearch_processor.sampling import extracted_frames
from framesearch_processor.settings import MODEL_VERSION, StorageSettings
from framesearch_processor.storage import ObjectStorage, StorageError

pytestmark = [
    pytest.mark.minio,
    pytest.mark.skipif(os.getenv("FRAMESEARCH_MINIO_TEST") != "1", reason="opt-in live MinIO test"),
]


@pytest.fixture
def minio():
    settings = StorageSettings.from_env()
    if urllib.parse.urlsplit(settings.endpoint).hostname not in {
        "minio",
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        pytest.fail("MinIO tests require a local test endpoint; they must not target AWS")
    # Each test owns a new private bucket; never clean up the configured application bucket.
    settings = replace(settings, bucket=f"framesearch-test-{uuid4().hex}")
    store = ObjectStorage(settings)
    try:
        store._client.create_bucket(Bucket=settings.bucket)
        store.check_bucket()
        yield store, settings
    finally:
        objects = store._client.list_objects_v2(Bucket=settings.bucket).get("Contents", [])
        if objects:
            store._client.delete_objects(
                Bucket=settings.bucket,
                Delete={"Objects": [{"Key": item["Key"]} for item in objects]},
            )
        store._client.delete_bucket(Bucket=settings.bucket)
        store.close()


@requires_ffmpeg
def test_live_download_extract_upload_and_retry_without_duplicate_objects(minio, tmp_path):
    store, settings = minio
    video_id = uuid4()
    source = make_video(tmp_path)
    source_bytes = source.read_bytes()
    source_key = f"videos/{video_id}/original.mp4"
    store._client.put_object(
        Bucket=settings.bucket, Key=source_key, Body=source_bytes, ContentType="video/mp4"
    )
    for attempt in range(2):
        with store.downloaded_video(
            source_key, len(source_bytes), temp_root=tmp_path
        ) as downloaded:
            assert downloaded.read_bytes() == source_bytes
            with extracted_frames(downloaded, len(source_bytes), temp_root=tmp_path) as clip:
                assert [frame.timestamp_ms for frame in clip.frames] == [0, 3000, 6000]
                keys = []
                for frame in clip.frames:
                    key = store.upload_thumbnail(video_id, frame)
                    keys.append(key)
                    signed = store._client.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": settings.bucket, "Key": key},
                        ExpiresIn=900,
                    )
                    with urllib.request.urlopen(signed, timeout=10) as response:
                        assert response.headers["Content-Type"] == "image/jpeg"
                        data = response.read()
                    assert data == frame.path.read_bytes()
                    with Image.open(io.BytesIO(data)) as image:
                        image.load()
                        assert image.size == (160, 90)
                    info = store._client.head_object(Bucket=settings.bucket, Key=key)
                    assert info["Metadata"] == {
                        "video-id": str(video_id),
                        "timestamp-ms": str(frame.timestamp_ms),
                        "model-version": MODEL_VERSION,
                    }
                if attempt == 0:
                    first_keys = keys
                else:
                    assert keys == first_keys
        assert not downloaded.exists()
        assert list(tmp_path.iterdir()) == [source]
    objects = store._client.list_objects_v2(
        Bucket=settings.bucket, Prefix=f"thumbnails/{video_id}/"
    )
    assert sorted(item["Key"] for item in objects["Contents"]) == sorted(first_keys)
    unsigned = (
        f"{settings.endpoint}/{settings.bucket}/{urllib.parse.quote(first_keys[0], safe='/')}"
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(unsigned, timeout=10)
    assert error.value.code == 403
    error.value.close()


def test_live_missing_video_and_invalid_metadata(minio, tmp_path):
    store, settings = minio
    with pytest.raises(InvalidVideo, match="missing from storage"):
        with store.downloaded_video("not-present.mp4", 4, temp_root=tmp_path):
            pytest.fail("missing source must fail")
    store._client.put_object(
        Bucket=settings.bucket, Key="wrong-type.mp4", Body=b"1234", ContentType="text/plain"
    )
    with pytest.raises(InvalidVideo, match="content type"):
        with store.downloaded_video("wrong-type.mp4", 4, temp_root=tmp_path):
            pytest.fail("wrong content type must fail")
    store._client.put_object(
        Bucket=settings.bucket, Key="wrong-size.mp4", Body=b"1234", ContentType="video/mp4"
    )
    with pytest.raises(InvalidVideo, match="upload declaration"):
        with store.downloaded_video("wrong-size.mp4", 5, temp_root=tmp_path):
            pytest.fail("wrong declared size must fail")
    assert list(tmp_path.iterdir()) == []


def test_live_missing_bucket_is_not_misclassified_as_missing_video(minio):
    _, settings = minio
    store = ObjectStorage(replace(settings, bucket=f"framesearch-missing-{uuid4().hex}"))
    try:
        with pytest.raises(StorageError):
            store.check_bucket()
        with pytest.raises(StorageError):
            with store.downloaded_video("video.mp4", 4):
                pytest.fail("missing infrastructure must fail")
    finally:
        store.close()


def test_live_bad_credentials_are_reported_as_storage_failure(minio):
    _, settings = minio
    store = ObjectStorage(
        replace(settings, access_key="invalid-test-access", secret_key="invalid-test-secret")
    )
    try:
        with pytest.raises(StorageError):
            store.check_bucket()
        with pytest.raises(StorageError):
            with store.downloaded_video("video.mp4", 4):
                pytest.fail("invalid credentials must fail")
    finally:
        store.close()


@requires_ffmpeg
@pytest.mark.real_model
@pytest.mark.skipif(os.getenv("FRAMESEARCH_REAL_MODEL_TEST") != "1", reason="opt-in real model")
def test_live_minio_media_pipeline_with_real_embeddings(minio, tmp_path):
    from framesearch_processor.embeddings import OpenClipEmbedder
    from framesearch_processor.settings import Settings

    store, settings = minio
    video_id = uuid4()
    source = make_video(tmp_path)
    data = source.read_bytes()
    source_key = f"videos/{video_id}/original.mp4"
    store._client.put_object(
        Bucket=settings.bucket, Key=source_key, Body=data, ContentType="video/mp4"
    )
    model = OpenClipEmbedder(Settings.from_env())
    with store.downloaded_video(source_key, len(data)) as downloaded:
        with extracted_frames(downloaded) as clip:
            vectors = model.embed_images([frame.path for frame in clip.frames])
            assert model.model_version == MODEL_VERSION
            assert len(vectors) == len(clip.frames) == 3
            for frame, vector in zip(clip.frames, vectors, strict=True):
                assert len(vector) == 512
                assert all(math.isfinite(value) for value in vector)
                assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(
                    1, abs=1e-6
                )
                key = store.upload_thumbnail(video_id, frame, model.model_version)
                info = store._client.head_object(Bucket=settings.bucket, Key=key)
                assert info["ContentLength"] == frame.path.stat().st_size
                assert info["ContentType"] == "image/jpeg"
