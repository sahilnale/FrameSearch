"""Bounded private object transfers through the shared S3/MinIO contract."""

import base64
import hashlib
import io
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image

from .media import MAX_DURATION_SECONDS, MAX_VIDEO_BYTES, InvalidVideo
from .sampling import THUMBNAIL_MAX_EDGE, VideoFrame
from .settings import MODEL_VERSION, StorageSettings

DOWNLOAD_CHUNK_BYTES = 64 * 1024
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024


class StorageError(RuntimeError):
    """Uncompleted storage operation; the future worker must retry/fail durably."""


def thumbnail_key(video_id: UUID, timestamp_ms: int, model_version: str = MODEL_VERSION) -> str:
    """Stable across retries, distinct across videos and the frozen model version."""
    if not isinstance(video_id, UUID):
        raise ValueError("thumbnail video_id must be a UUID")
    if type(timestamp_ms) is not int or not 0 <= timestamp_ms < MAX_DURATION_SECONDS * 1000:
        raise ValueError(
            "thumbnail timestamp must be an integer within the video's allowed timeline"
        )
    if model_version != MODEL_VERSION:
        raise ValueError("thumbnail model_version must match the frozen embedding model")
    return f"thumbnails/{video_id}/{model_version}/{timestamp_ms:06d}.jpg"


class ObjectStorage:
    def __init__(self, settings: StorageSettings) -> None:
        self._bucket = settings.bucket
        self._client = boto3.session.Session().client(
            "s3",
            endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            # Match the existing Go client's shared MinIO signing configuration.
            region_name="us-east-1",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=30,
                retries={"mode": "standard", "total_max_attempts": 3},
                # Use the explicit Content-MD5 integrity check on thumbnail PUTs.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def close(self) -> None:
        self._client.close()

    def check_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(
                "Storage bucket is unavailable; check connection and permissions"
            ) from exc

    def _download(self, object_key: str, expected_size_bytes: int, destination: Path) -> None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=object_key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise InvalidVideo(
                    "Uploaded video is missing from storage; upload it again"
                ) from exc
            raise StorageError(
                "Cannot fetch source video; check storage connection and permissions"
            ) from exc
        except BotoCoreError as exc:
            raise StorageError("Cannot connect to storage to fetch source video") from exc
        body = response.get("Body")
        if body is None:
            raise StorageError("Storage returned no source video body")
        try:
            with closing(body):
                if response.get("ContentLength") != expected_size_bytes:
                    raise InvalidVideo(
                        "Stored video size differs from the upload declaration; upload again"
                    )
                if response.get("ContentType") != "video/mp4":
                    raise InvalidVideo(
                        "Stored video content type is not video/mp4; upload an MP4 again"
                    )
                written = 0
                with destination.open("xb") as file:
                    for chunk in body.iter_chunks(chunk_size=DOWNLOAD_CHUNK_BYTES):
                        written += len(chunk)
                        if written > expected_size_bytes:
                            raise InvalidVideo(
                                "Source video exceeds its declared upload size; upload again"
                            )
                        file.write(chunk)
                if written != expected_size_bytes:
                    raise StorageError("Source video download was incomplete; retry processing")
        except (BotoCoreError, OSError) as exc:
            raise StorageError(
                "Source video download was interrupted or could not be written"
            ) from exc

    @contextmanager
    def downloaded_video(
        self,
        object_key: str,
        expected_size_bytes: int,
        *,
        temp_root: Path | None = None,
    ) -> Iterator[Path]:
        if not isinstance(object_key, str) or not 1 <= len(object_key.encode("utf-8")) <= 1024:
            raise ValueError("source object key must contain 1–1024 UTF-8 bytes")
        if type(expected_size_bytes) is not int or not 1 <= expected_size_bytes <= MAX_VIDEO_BYTES:
            raise ValueError("declared source size must contain 1–104857600 bytes")
        with TemporaryDirectory(prefix="framesearch-source-", dir=temp_root) as temporary:
            # The object key is never used as a local filesystem path.
            destination = Path(temporary) / "source.mp4"
            self._download(object_key, expected_size_bytes, destination)
            yield destination

    def upload_thumbnail(
        self,
        video_id: UUID,
        frame: VideoFrame,
        model_version: str = MODEL_VERSION,
    ) -> str:
        key = thumbnail_key(video_id, frame.timestamp_ms, model_version)
        try:
            with frame.path.open("rb") as file:
                data = file.read(MAX_THUMBNAIL_BYTES + 1)
            if not 1 <= len(data) <= MAX_THUMBNAIL_BYTES:
                raise ValueError("thumbnail must contain 1–2097152 bytes")
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG" or not all(
                    1 <= dimension <= THUMBNAIL_MAX_EDGE for dimension in image.size
                ):
                    raise ValueError("thumbnail must be JPEG with a maximum edge of 640 pixels")
                image.load()
        except OSError as exc:
            raise StorageError("Cannot read the generated thumbnail") from exc
        digest = hashlib.md5(data, usedforsecurity=False).digest()
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentLength=len(data),
                ContentType="image/jpeg",
                ContentMD5=base64.b64encode(digest).decode("ascii"),
                Metadata={
                    "video-id": str(video_id),
                    "timestamp-ms": str(frame.timestamp_ms),
                    "model-version": model_version,
                },
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError("Thumbnail upload failed; retry processing") from exc
        return key
