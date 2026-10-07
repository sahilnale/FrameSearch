"""One CPU model shared by HTTP text inference and batched image inference."""

import logging
import threading
from collections.abc import Sequence
from pathlib import Path

import open_clip
import torch
from PIL import Image

from .settings import EMBEDDING_DIMENSIONS, MAX_FRAMES, MAX_TEXT_CHARACTERS, MODEL_VERSION, Settings

logger = logging.getLogger(__name__)


def normalized_vectors(features: torch.Tensor, expected_rows: int) -> list[list[float]]:
    """Reject invalid encoder output before any embedding reaches HTTP or the DB."""
    if expected_rows < 1:
        raise ValueError("encoder output must contain at least one vector")
    if features.ndim != 2 or tuple(features.shape) != (expected_rows, EMBEDDING_DIMENSIONS):
        raise ValueError(
            f"encoder output must have shape ({expected_rows}, {EMBEDDING_DIMENSIONS})"
        )
    values = features.detach().to(device="cpu", dtype=torch.float64)
    if not torch.isfinite(values).all().item():
        raise ValueError("encoder output contains nonfinite values")
    norms = torch.linalg.vector_norm(values, dim=1, keepdim=True)
    if not torch.isfinite(norms).all().item() or (norms <= 0).any().item():
        raise ValueError("encoder output contains an invalid or zero-length vector")
    return (values / norms).to(dtype=torch.float32).tolist()


class OpenClipEmbedder:
    model_version = MODEL_VERSION

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
        torch.set_num_threads(settings.torch_threads)
        if (settings.model_name, settings.model_pretrained) not in open_clip.list_pretrained():
            raise ValueError("the frozen model/checkpoint is unavailable in installed OpenCLIP")
        logger.info("Loading %s on CPU; cache=%s", MODEL_VERSION, settings.model_cache_dir)
        self._model, _, self._preprocess = open_clip.create_model_and_transforms(
            settings.model_name,
            pretrained=settings.model_pretrained,
            device="cpu",
            precision="fp32",
            cache_dir=str(settings.model_cache_dir),
        )
        self._model.eval()
        self._tokenizer = open_clip.get_tokenizer(settings.model_name)
        # Readiness requires successful real inference and contract validation.
        self.embed_text("a video frame")
        logger.info("OpenCLIP is ready: %s", MODEL_VERSION)

    def embed_text(self, text: str) -> list[float]:
        text = text.strip()
        if not 1 <= len(text) <= MAX_TEXT_CHARACTERS:
            raise ValueError(f"text must contain 1 to {MAX_TEXT_CHARACTERS} characters")
        with self._lock, torch.inference_mode():
            tokens = self._tokenizer([text]).to("cpu")
            return normalized_vectors(self._model.encode_text(tokens), expected_rows=1)[0]

    def embed_images(self, paths: Sequence[Path]) -> list[list[float]]:
        """Encode at most 60 real images, releasing the model lock between small batches."""
        if not 1 <= len(paths) <= MAX_FRAMES:
            raise ValueError(f"image count must be between 1 and {MAX_FRAMES}")
        output: list[list[float]] = []
        for start in range(0, len(paths), self._settings.image_batch_size):
            batch_paths = paths[start : start + self._settings.image_batch_size]
            images = []
            for path in batch_paths:
                with Image.open(path) as image:
                    images.append(self._preprocess(image.convert("RGB")))
            batch = torch.stack(images).to("cpu")
            with self._lock, torch.inference_mode():
                output.extend(
                    normalized_vectors(
                        self._model.encode_image(batch), expected_rows=len(batch_paths)
                    )
                )
        return output
