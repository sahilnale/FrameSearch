import math
import os
from pathlib import Path

import open_clip
import pytest
import torch
from PIL import Image

from framesearch_processor.embeddings import OpenClipEmbedder, normalized_vectors
from framesearch_processor.settings import MODEL_NAME, MODEL_PRETRAINED, MODEL_VERSION, Settings


def assert_unit_vector(vector):
    assert len(vector) == 512
    assert all(math.isfinite(value) for value in vector)
    assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1, abs=1e-6)


def test_frozen_checkpoint_exists_in_pinned_openclip():
    assert (MODEL_NAME, MODEL_PRETRAINED) in open_clip.list_pretrained()


def test_normalize_multiple_vectors():
    features = torch.arange(1, 1025, dtype=torch.float32).reshape(2, 512)
    original = features.clone()
    vectors = normalized_vectors(features, expected_rows=2)
    assert len(vectors) == 2
    for vector in vectors:
        assert_unit_vector(vector)
    assert torch.equal(features, original)


@pytest.mark.parametrize("shape", [(512,), (1, 511), (1, 513), (2, 512), (1, 512, 1)])
def test_reject_dimension_or_batch_count_mismatch(shape):
    with pytest.raises(ValueError, match="shape"):
        normalized_vectors(torch.ones(shape), expected_rows=1)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_reject_nonfinite_vectors(bad):
    features = torch.ones(1, 512)
    features[0, 5] = bad
    with pytest.raises(ValueError, match="nonfinite"):
        normalized_vectors(features, expected_rows=1)


def test_reject_zero_vector_in_a_batch():
    features = torch.ones(2, 512)
    features[1] = 0
    with pytest.raises(ValueError, match="zero-length"):
        normalized_vectors(features, expected_rows=2)


def test_reject_empty_batch():
    with pytest.raises(ValueError, match="at least one"):
        normalized_vectors(torch.empty(0, 512), expected_rows=0)


def test_normalization_avoids_float32_overflow():
    vectors = normalized_vectors(torch.full((1, 512), 1e38), expected_rows=1)
    assert_unit_vector(vectors[0])


@pytest.mark.real_model
@pytest.mark.skipif(
    os.getenv("FRAMESEARCH_REAL_MODEL_TEST") != "1",
    reason="set FRAMESEARCH_REAL_MODEL_TEST=1 to download and run the real checkpoint",
)
def test_real_cpu_text_and_batched_image_embeddings(tmp_path):
    settings = Settings.from_env()
    settings = Settings(
        model_cache_dir=settings.model_cache_dir,
        torch_threads=settings.torch_threads,
        image_batch_size=2,
    )
    model = OpenClipEmbedder(settings)
    assert model.model_version == MODEL_VERSION
    assert next(model._model.parameters()).device.type == "cpu"
    assert not model._model.training
    text = model.embed_text("a red square on a white background")
    assert_unit_vector(text)
    assert model.embed_text("  a red square on a white background  ") == pytest.approx(text)
    with pytest.raises(ValueError, match="text"):
        model.embed_text(" " * 5)
    with pytest.raises(ValueError, match="text"):
        model.embed_text("x" * 501)
    paths = []
    for color in ["red", "blue", "green", "white", "black"]:
        path: Path = tmp_path / f"{color}.png"
        Image.new("RGB", (256, 256), color).save(path)
        paths.append(path)
    vectors = model.embed_images(paths)
    assert len(vectors) == 5  # Crosses three actual inference batches.
    for vector in vectors:
        assert_unit_vector(vector)
    assert vectors[0] != vectors[1]
    assert model.embed_images(paths[:1])[0] == pytest.approx(vectors[0], abs=1e-5)
    with pytest.raises(ValueError, match="image count"):
        model.embed_images([])
    with pytest.raises(ValueError, match="image count"):
        model.embed_images(paths[:1] * 61)
    with pytest.raises(FileNotFoundError):
        model.embed_images([tmp_path / "missing.png"])
