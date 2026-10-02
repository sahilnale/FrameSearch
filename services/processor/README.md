# FrameSearch processor

Developer 2's processor implementation, following master specification section 13.
This checkpoint contains the real CPU OpenCLIP embedding core. Video decoding,
Kafka consumption, storage, and database transitions are subsequent features.

## Install and test the embedding core

Use Python 3.12 and uv 0.6.3 (the version used to generate `uv.lock`):

```sh
cd services/processor
uv sync --frozen
uv run --frozen pytest tests/test_settings.py tests/test_embeddings.py
FRAMESEARCH_REAL_MODEL_TEST=1 uv run --frozen pytest -v tests/test_embeddings.py
uv run --frozen ruff check framesearch_processor/settings.py framesearch_processor/embeddings.py tests/test_settings.py tests/test_embeddings.py
```

Normal tests do not download weights. The explicit real-model test downloads the
genuine pretrained checkpoint on first run and tests actual text and image
inference. It is not a semantic accuracy measurement. Cache files persist under
`services/processor/.cache/openclip` by default; leave them in place for future
runs. Download time and hardware requirements will be recorded when measured.

OpenCLIP `3.3.0`, PyTorch `2.10.0`, and torchvision `0.25.0` are pinned.
Linux resolves torch/torchvision from the official CPU wheel index, avoiding CUDA
dependencies. macOS resolves the corresponding native PyPI wheels. Transitive
dependencies and artifact hashes are in `uv.lock`.

## Model contract

- `MODEL_NAME=ViT-B-32`
- `MODEL_PRETRAINED=laion2b_s34b_b79k`
- `model_version=ViT-B-32:laion2b_s34b_b79k`
- 512 finite, L2-normalized floats for both image and text embeddings.

Changing the model pair fails configuration rather than silently mixing vectors.
Both inference methods use the same model in evaluation mode with
`torch.inference_mode()`. CPU inference is mandatory; no GPU is needed.
OpenCLIP's tokenizer uses the checkpoint's 77-token context; long valid queries
are truncated by that tokenizer, even though the API allows up to 500 characters.

Additional processor-local settings (no changes to shared `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODEL_CACHE_DIR` | `.cache/openclip` | Persistent checkpoint cache |
| `TORCH_NUM_THREADS` | `2` | CPU threads, validated within 1–8 |
| `IMAGE_BATCH_SIZE` | `4` | Images per inference batch, validated within 1–8 |

Image encoding accepts 1–60 real image paths. Each file is closed after
preprocessing, and one shared lock serializes image/text inference. The lock is
released between image batches so text inference can run during indexing.

## Integration notes for Developer 1

The frozen model version is unchanged. No Go, schema, infrastructure, shared docs,
or environment files are modified by this work. The next checkpoint provides the
internal HTTP server on `0.0.0.0:8000`; indexing and its integrations come after it.
The intended deployment uses exactly one Python process and one worker replica.
