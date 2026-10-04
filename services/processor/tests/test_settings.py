from pathlib import Path

import pytest

from framesearch_processor.settings import MODEL_VERSION, Settings


def test_default_model_matches_shared_contract():
    settings = Settings()
    assert f"{settings.model_name}:{settings.model_pretrained}" == MODEL_VERSION
    assert settings.image_batch_size == 4
    assert settings.torch_threads == 2


def test_read_environment(monkeypatch):
    monkeypatch.setenv("MODEL_CACHE_DIR", "/tmp/framesearch-model")
    monkeypatch.setenv("TORCH_NUM_THREADS", "3")
    monkeypatch.setenv("IMAGE_BATCH_SIZE", "2")
    settings = Settings.from_env()
    assert settings.model_cache_dir == Path("/tmp/framesearch-model")
    assert settings.torch_threads == 3
    assert settings.image_batch_size == 2


@pytest.mark.parametrize(
    "values",
    [
        {"model_name": "ViT-L-14"},
        {"model_pretrained": "openai"},
        {"torch_threads": 0},
        {"torch_threads": 9},
        {"image_batch_size": 0},
        {"image_batch_size": 9},
    ],
)
def test_reject_incompatible_or_unbounded_settings(values):
    with pytest.raises(ValueError):
        Settings(**values)


def test_reject_invalid_environment(monkeypatch):
    monkeypatch.setenv("TORCH_NUM_THREADS", "many")
    with pytest.raises(ValueError):
        Settings.from_env()
