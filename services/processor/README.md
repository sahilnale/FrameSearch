# FrameSearch processor

Developer 2's processor implementation, following master specification section 13.
This checkpoint contains the real CPU OpenCLIP embedding core, internal text
HTTP service, and FFprobe upload validation. Frame extraction, Kafka consumption,
storage, and database transitions are subsequent features. The validation module
is not yet connected to an ingestion worker.

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

## Container packaging

From the repository root:

```sh
docker build -t framesearch-processor services/processor
docker run --rm --name framesearch-processor \
  -p 127.0.0.1:8000:8000 \
  -v framesearch-model-cache:/cache \
  framesearch-processor
```

The image installs Python 3.12, pinned uv 0.6.3, locked CPU dependencies, and
FFmpeg/FFprobe. It runs one HTTP process as UID 10001, with a writable model cache
at `/cache/openclip`. The uv installer uses PyPI, avoiding another required image
registry. The model is downloaded at runtime rather than bundled in the image.
The actual downloaded checkpoint cache currently occupies approximately 577 MiB
on the host development machine.

Linux ARM64 image build and live HTTP verification passed using the genuine
cached checkpoint. One Docker memory snapshot after text inference was 1.462 GiB
for this processor. This is not a peak-memory or complete-stack measurement.

Developer 1 can use `services/processor` as the Compose build context, keep port
8000 internal, mount a persistent volume at `/cache`, and probe `/health/ready`.
Allow time for the initial model download. The standalone loopback port above is
only for local verification. This milestone's readiness checks the embedding
model; Kafka consumption and indexing are not implemented yet.

Verify a running local service over an actual HTTP socket:

```sh
cd services/processor
PYTHONPATH=. uv run --frozen python tests/check_service.py --url http://localhost:8000
```

This checks the model version, readiness, liveness, and genuine finite normalized
512-dimensional text response. It does not claim video indexing or semantic
retrieval is complete.

## Validate downloaded video files

`framesearch_processor.media.probe_video(path, expected_size_bytes)` inspects the
actual local file with FFprobe. It enforces the shared 100 MiB / 180-second limits,
checks the downloaded size against the upload declaration, and returns the video
stream index, dimensions, duration, and start timestamp. It checks the MP4
container/brand rather than trusting the filename. Audio-only files and embedded
cover art do not count as playable video.

Invalid media raises `InvalidVideo` with an actionable reason. Missing tools,
probe timeouts, and malformed tool output raise `MediaToolError`, so the later
worker can distinguish media rejection from infrastructure failure. FFprobe has
a 15-second timeout and can access only local files. Metadata validation alone
does not establish that every frame decodes; the next extraction feature must
check actual decoding before indexing can succeed.

The real-media tests generate MP4, MOV, Matroska, audio-only, and duration-boundary
fixtures with FFmpeg, then run real FFprobe. They skip on hosts without those
binaries. From the repository root, run the full suite in a disposable processor
container after building the image above:

```sh
docker run --rm --user 0 --workdir /work \
  --mount "type=bind,source=$PWD/services/processor,target=/work,readonly" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e UV_PROJECT_ENVIRONMENT=/app/.venv -e UV_CACHE_DIR=/tmp/uv-cache \
  framesearch-processor sh -c \
  'uv sync --frozen --project /work --python /app/.venv/bin/python --quiet && /app/.venv/bin/python -m pytest -q -p no:cacheprovider'
```

This installs locked test dependencies in the disposable container and mounts
source read-only; it does not change the production image's non-root runtime.
Latest container run: 80 passed, two opt-in real-model tests skipped. Both real
model checks were verified at the earlier embedding/HTTP checkpoints.

## Integration notes for Developer 1

The frozen model version is unchanged. No Go, schema, infrastructure, shared docs,
or environment files are modified by this work. The internal HTTP server can
listen on `0.0.0.0:8000` within Compose; indexing and its integrations come after it.
The intended deployment uses exactly one Python process and one worker replica.
