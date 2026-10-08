# FrameSearch processor

Developer 2's processor implementation, following master specification section 13.
This checkpoint contains the real CPU OpenCLIP embedding core, internal text
HTTP service, FFprobe upload validation, timestamped FFmpeg frame extraction,
and private source/thumbnail transfers through MinIO's S3 API. Kafka consumption
and database transitions are subsequent features. The PostgreSQL connection
layer is verified against the shared schema. These components are not yet
connected to an ingestion worker.

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
does not establish that every frame decodes. The sampler below checks actual
decoding before indexing can succeed.

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
The validation checkpoint passed 80 tests with two opt-in model tests skipped.
The sampling checkpoint passed **121 tests with no skips**, using actual FFmpeg
and all three opt-in model checks with the cached genuine checkpoint. To run the
same full check after caching weights, add these options before the image name
in the command above:

```sh
  --mount "type=bind,source=$PWD/services/processor/.cache/openclip,target=/cache/openclip,readonly" \
  -e MODEL_CACHE_DIR=/cache/openclip -e HF_HOME=/tmp/huggingface \
  -e HF_HUB_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 \
  -e FRAMESEARCH_REAL_MODEL_TEST=1 -e IMAGE_BATCH_SIZE=2
```

## Extract timestamped thumbnails

`framesearch_processor.sampling.extracted_frames` validates and decodes a source
MP4 in one FFmpeg pass. It selects the first available source frame at or after
each three-second grid point, capped at 60. A gap in variable-frame-rate footage
can leave fewer samples; it does not synthesize duplicate frames. JPEGs have a
maximum edge of 640 pixels and preserve display aspect ratio, including portrait
and non-square-pixel footage.

Frame timestamps come from integer presentation timestamps and the exact rational
time base. They are floored to milliseconds. For example, a frame at 3.003 seconds
is stored as `3003`. The source timeline is preserved, including nonzero starting
timestamps, so the returned times correspond to source playback. JPEG filenames
are deterministic (`frame-003003.jpg`), and the storage adapter uses stable object
keys on retries.

Use the context manager while embedding and uploading the frames:

```python
from pathlib import Path
from framesearch_processor.embeddings import OpenClipEmbedder
from framesearch_processor.sampling import extracted_frames
from framesearch_processor.settings import Settings

model = OpenClipEmbedder(Settings.from_env())  # Once per service process.
with extracted_frames(Path("clip.mp4")) as clip:
    vectors = model.embed_images([frame.path for frame in clip.frames])
    print([(frame.timestamp_ms, len(vector))
           for frame, vector in zip(clip.frames, vectors, strict=True)])
```

Temporary JPEGs and decoder diagnostics are removed on success, decoder failure,
and errors raised by the frame consumer. The caller's source file is preserved.
The decoder uses two threads, filtering/encoding use one each, and extraction has
a 600-second timeout. Actual packet decoding failures are rejected even when
FFprobe can read the MP4 header. Every output JPEG is opened and decoded before
the context yields its frames.

Checks cover sampling boundaries, the 180-second/60-frame limit, fractional-rate
drift against independent FFprobe frame timestamps, sparse/VFR footage, shifted
timelines, audio preceding video, thumbnail sizing/aspect ratio, deterministic
retry outputs, cleanup, corrupt packets, and real frame-to-OpenCLIP inference.
An additional native-browser check with byte-range playback confirmed the
timestamp behavior for generated zero-offset and shifted color clips. Complete
application playback and cross-browser testing remain part of later integration.

## Download sources and upload thumbnails through MinIO

`framesearch_processor.storage.ObjectStorage` uses pinned Boto3 `1.43.109` and
the existing shared S3 environment variables. It uses Signature V4, path-style
requests, and region `us-east-1`, matching the Go storage client.

| Variable | Default | Purpose |
| --- | --- | --- |
| `S3_ENDPOINT_INTERNAL` | `http://localhost:9000` | Internal MinIO/S3 endpoint |
| `S3_ACCESS_KEY` | Required | Access key; hidden from settings repr |
| `S3_SECRET_KEY` | Required | Secret key; hidden from settings repr |
| `S3_BUCKET` | `framesearch` | Existing private bucket |

The application adapter does not create buckets or change access policies.
`check_bucket()` verifies access to the configured bucket. Use the source
`object_key` and `size_bytes` from the database rather than rebuilding a path.
`downloaded_video()` checks the object's content type and declared size, streams
it in 64 KiB chunks with the 100 MiB bound, and removes the local temporary copy
when its context exits. The original remote upload stays in storage.

Once the database worker supplies `video_id` (a UUID), `object_key`, and
`size_bytes`, its media/storage stage can use these components together:

```python
from framesearch_processor.sampling import extracted_frames
from framesearch_processor.settings import StorageSettings
from framesearch_processor.storage import ObjectStorage

# Reuse the service's already-loaded OpenClipEmbedder as `model`.
store = ObjectStorage(StorageSettings.from_env())
try:
    store.check_bucket()
    with store.downloaded_video(object_key, size_bytes) as source:
        with extracted_frames(source, size_bytes) as clip:
            vectors = model.embed_images([frame.path for frame in clip.frames])
            records = [
                {
                    "timestamp_ms": frame.timestamp_ms,
                    "embedding": vector,
                    "model_version": model.model_version,
                    "thumbnail_key": store.upload_thumbnail(
                        video_id, frame, model.model_version
                    ),
                }
                for frame, vector in zip(clip.frames, vectors, strict=True)
            ]
    # Transactional persistence of these records is the next feature.
finally:
    store.close()
```

Thumbnail keys are stable across retries:
`thumbnails/{video_uuid}/{model_version}/{timestamp_ms:06d}.jpg`. Uploads validate
JPEG content and the 640-pixel edge bound, set `image/jpeg`, include video/time/
model metadata, and send Content-MD5 for integrity checking. The key is returned
only after a successful PUT. Retrying overwrites the same keys rather than
creating duplicates. SDK requests have bounded timeouts and at most three total
attempts; job-level retry handling remains part of the future worker.

Missing uploads and mismatched upload metadata raise actionable `InvalidVideo`
errors. Missing buckets, bad credentials, interrupted transfers, and failed
thumbnail PUTs raise `StorageError`. A later worker must persist the job/video
failure or retry state; it must only mark a video ready after all thumbnails and
frame rows are durable. This module does not change database states yet.

Run the real storage checks from the processor directory:

```sh
uv run --frozen python tests/run_minio_tests.py
# Include genuine inference after caching weights with the real-model test above:
uv run --frozen python tests/run_minio_tests.py --real-model
```

The harness builds the production processor image, a test image with locked dev
dependencies, and a non-root MinIO server from the official pinned source tag
`RELEASE.2025-10-15T17-29-55Z`. The public MinIO image could not be pulled during
this checkpoint, so `tests/minio.Dockerfile` follows upstream's source build
route. These Dockerfiles are test infrastructure, not the shared deployment.

Runtime checks use an internal disposable Docker network with no published
ports, random test credentials, and new private buckets per test. Source and
cached model mounts are read-only; both containers run as UID 10001. The harness
removes its containers and network afterwards, retaining images and the host
model cache. The full storage checkpoint passed **175 tests, no skips, in
18.86 seconds**, including real FFmpeg, actual MinIO transfers, signed thumbnail
reads, anonymous-access rejection, repeatable retry keys, and all four genuine
CPU model checks. This does not measure semantic retrieval accuracy or establish
complete application integration.

Moving to AWS S3 later requires provisioning a private bucket, changing endpoint
and credentials, and copying existing objects while preserving keys. The current
Go/Python clients both sign for `us-east-1`; use that AWS region initially or
coordinate configurable region support in both services. AWS S3 has not been
tested by this checkpoint.

## PostgreSQL connection checkpoint

The processor uses pinned `psycopg[binary]==3.3.6`. `DatabaseSettings.from_env()`
requires the shared `DATABASE_URL` with an explicit host and database name.
Credentials are hidden from settings repr and URL validation errors. The URL's
TLS settings are passed to libpq, including the local `sslmode=disable` example.

`framesearch_processor.database.Database.connection()` opens one connection per
operation, commits its transaction on success, rolls back on exceptions, and
always closes it. Connections are not shared between threads. It enforces a
five-second connection timeout, a 15-second statement timeout, and a five-second
lock timeout. The application name is `framesearch-processor`.

`Database.check_schema()` checks connectivity, the three canonical `public`
tables, and the installed `vector` extension. It does not run or modify the
shared migration. Database failures raise `DatabaseError`; this checkpoint does
not yet wire that check into HTTP readiness or implement job/frame writes.

```python
from framesearch_processor.database import Database
from framesearch_processor.settings import DatabaseSettings

database = Database(DatabaseSettings.from_env())
database.check_schema()
```

Local verification: **157 passed, 40 skipped** (7.13 seconds). Focused Linux
ARM64 checks: **22 passed, no skips** (0.24 seconds), including fifteen new
configuration checks and seven real PostgreSQL checks. The actual production
image also passed its schema check as UID 10001. Tests used the existing shared
migration in a disposable PostgreSQL 16 / pgvector 0.8.7 instance, on an internal
Docker network with no published ports. Test resources were cleaned up.

To run the seven live checks with a disposable local PostgreSQL server with
pgvector is available, apply `db/migrations/001_initial.sql` to that test database,
then run from `services/processor`:

```sh
# Point this variable only at a disposable local test database.
export TEST_DATABASE_URL='postgres://test_user:test_password@localhost:5432/framesearch_test?sslmode=disable'
FRAMESEARCH_DATABASE_TEST=1 uv run --frozen pytest -q tests/test_database.py
```

The test role needs CREATE DATABASE privileges for the missing-migration check.
Tests create and remove their own uniquely named table and empty database, and
check connection closure, successful commit, rollback, timeout handling, schema
availability, actual vector SQL, and invalid credentials. They do not alter
application rows. These checks are separate from full indexing or Go integration.

Database work is split into individual checkpoints: connection/configuration,
atomic job claiming, idempotent frame upserts, and ready/failed transactions.

## Integration notes for Developer 1

The frozen model version is unchanged. No Go, schema, infrastructure, shared docs,
or environment files are modified by this work. The internal HTTP server can
listen on `0.0.0.0:8000` within Compose; indexing and its integrations come after it.
The intended deployment uses exactly one Python process and one worker replica.
MinIO deployment still needs a persistent data volume, private bucket, shared
credentials, browser CORS, and the public endpoint used by Go for signed URLs.
The processor uses only the internal endpoint. The test-only pinned source
Dockerfile is available as a reference if the shared MinIO image is unavailable.
