# FrameSearch processor

Developer 2's processor implementation, following master specification section 13.
This checkpoint contains the real CPU OpenCLIP embedding core and internal text
HTTP service. Video decoding, Kafka consumption, storage, and database transitions
are subsequent features.

## Install and test the embedding core

Use Python 3.12 and uv 0.6.3 (the version used to generate `uv.lock`):

```sh
cd services/processor
uv sync --frozen
uv run --frozen pytest
FRAMESEARCH_REAL_MODEL_TEST=1 uv run --frozen pytest -v -m real_model
uv run --frozen ruff check framesearch_processor tests
uv run --frozen ruff format --check framesearch_processor tests
```

Normal tests do not download weights. The explicit real-model test downloads the
genuine pretrained checkpoint on first run and tests actual text and image
inference. It is not a semantic accuracy measurement. Cache files persist under
`services/processor/.cache/openclip` by default; leave them in place for future
runs. Download time and hardware requirements will be recorded when measured.

The initial real CPU text/image test passed after a first checkpoint download in
389.91 seconds on this Apple Silicon development machine; that includes the
download and test work, not inference latency. The subsequent real HTTP test uses
the same cached weights. Network speed and machine resources affect first startup.

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

## Run the internal text service

```sh
cd services/processor
uv run --frozen uvicorn framesearch_processor.app:app --host 127.0.0.1 --port 8000 --workers 1
```

Use exactly one Uvicorn worker so that the model loads once per service process.
Startup downloads and validates the genuine model in a background thread.
`GET /health/live` returns 200 while weights load; `GET /health/ready` returns 503
until a real warmup text inference succeeds. Model loading failure keeps readiness
at 503 and logs the cause. Restart the process after fixing a download/cache error.
One HTTP inference runs at a time, off the event loop, using the same model and
lock as image inference.

```sh
curl --fail http://localhost:8000/health/ready
curl --fail http://localhost:8000/embed/text \
  -H 'Content-Type: application/json' \
  --data '{"text":"a car on a rainy street at night"}'
```

The embedding response is `{ "embedding": number[512], "model_version": string }`.
The `text` field must be a string containing 1–500 Unicode characters after
trimming whitespace. Invalid JSON/requests return 422, unavailable model 503,
and inference failures 500, all with `{ "error": { "code", "message" } }`.
This endpoint is for Go-to-processor calls. It has no authentication; keep it
inside the Compose network and use loopback binding for standalone local tests.

Once weights are cached, verification can explicitly avoid another download:

```sh
FRAMESEARCH_REAL_MODEL_TEST=1 HF_HUB_OFFLINE=1 uv run --frozen pytest -v -m real_model
```

The HTTP/lifecycle unit tests use doubles only in test files to verify failures
and blocking behavior. The real-model tests exercise actual OpenCLIP text/image
inference and the HTTP endpoint. Application code has no fake embedding mode.

## Integration notes for Developer 1

The frozen model version is unchanged. No Go, schema, infrastructure, shared docs,
or environment files are modified by this work. The internal HTTP server can
listen on `0.0.0.0:8000` within Compose; indexing and its integrations come after it.
The intended deployment uses exactly one Python process and one worker replica.
