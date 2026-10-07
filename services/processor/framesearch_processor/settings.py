"""Processor configuration. The model pair is fixed by the shared Go contract."""

import os
from dataclasses import dataclass
from pathlib import Path

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
