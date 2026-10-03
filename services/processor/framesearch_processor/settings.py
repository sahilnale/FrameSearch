"""Processor configuration. The model pair is fixed by the shared Go contract."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MODEL_NAME = "ViT-B-32"
MODEL_PRETRAINED = "laion2b_s34b_b79k"
MODEL_VERSION = f"{MODEL_NAME}:{MODEL_PRETRAINED}"
EMBEDDING_DIMENSIONS = 512
MAX_TEXT_CHARACTERS = 500
MAX_FRAMES = 60


@dataclass(frozen=True)
class Settings:
    model_name: str = MODEL_NAME
    model_pretrained: str = MODEL_PRETRAINED
    model_cache_dir: Path = Path(".cache/openclip")
    torch_threads: int = 2
    image_batch_size: int = 4

    def __post_init__(self) -> None:
        if (self.model_name, self.model_pretrained) != (MODEL_NAME, MODEL_PRETRAINED):
            raise ValueError(f"MODEL_NAME and MODEL_PRETRAINED must match {MODEL_VERSION}")
        if not 1 <= self.torch_threads <= 8:
            raise ValueError("TORCH_NUM_THREADS must be between 1 and 8")
        if not 1 <= self.image_batch_size <= 8:
            raise ValueError("IMAGE_BATCH_SIZE must be between 1 and 8")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            model_name=os.getenv("MODEL_NAME", MODEL_NAME),
            model_pretrained=os.getenv("MODEL_PRETRAINED", MODEL_PRETRAINED),
            model_cache_dir=Path(os.getenv("MODEL_CACHE_DIR", ".cache/openclip")),
            torch_threads=int(os.getenv("TORCH_NUM_THREADS", "2")),
            image_batch_size=int(os.getenv("IMAGE_BATCH_SIZE", "4")),
        )


@dataclass(frozen=True)
class StorageSettings:
    endpoint: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    bucket: str

    def __post_init__(self) -> None:
        try:
            url = urlsplit(self.endpoint)
            valid_endpoint = (
                url.scheme in ("http", "https")
                and bool(url.hostname)
                and not any(character.isspace() for character in self.endpoint)
                and url.username is None
                and url.password is None
                and url.path in ("", "/")
                and not url.query
                and not url.fragment
                and (url.port is None or 1 <= url.port <= 65535)
            )
        except (ValueError, TypeError):
            valid_endpoint = False
        if not valid_endpoint:
            raise ValueError("S3_ENDPOINT_INTERNAL must be an absolute http(s) storage endpoint")
        for name, value in (("S3_ACCESS_KEY", self.access_key), ("S3_SECRET_KEY", self.secret_key)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if (
            not isinstance(self.bucket, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket)
            or any(sequence in self.bucket for sequence in ("..", ".-", "-."))
        ):
            raise ValueError("S3_BUCKET must be a valid 3–63 character bucket name")

    @classmethod
    def from_env(cls) -> "StorageSettings":
        return cls(
            endpoint=os.getenv("S3_ENDPOINT_INTERNAL", "http://localhost:9000"),
            access_key=os.getenv("S3_ACCESS_KEY", ""),
            secret_key=os.getenv("S3_SECRET_KEY", ""),
            bucket=os.getenv("S3_BUCKET", "framesearch"),
        )
