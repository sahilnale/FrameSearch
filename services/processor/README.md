# FrameSearch processor

Developer 2's processor implementation, following master specification section 13.
This checkpoint contains the real CPU OpenCLIP embedding core, internal text
HTTP service, FFprobe upload validation, timestamped FFmpeg frame extraction,
and private source/thumbnail transfers through MinIO's S3 API. PostgreSQL
connections, atomic job claims, and retry-safe frame writes are verified against
the shared schema, including transactional success and failure finalization.
These stages are connected by a real video indexing operation with bounded
single-job retries and durable terminal outcomes. The service launches the serial
Kafka ingestion loop and shares one loaded model with its text HTTP endpoint.
The complete live processor suite passes. Labeled synthetic and real-footage
search evaluations are recorded; the real sample exposes two first-frame misses.
The public Go API upload/queue/search/signed-read smoke now passes in an isolated
live stack. The frontend now supports uploads, search, and timestamp playback;
its genuine browser happy path also passes. Full shared Compose startup and public
recovery integrations remain pending. See `VERIFICATION.md` and
`../../apps/web/VERIFICATION.md` for exact results and remaining scope.

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
| `MODEL_CACHE_DIR` | `$XDG_CACHE_HOME/openclip`, otherwise `.cache/openclip` | Explicit override for the persistent checkpoint cache |
| `TORCH_NUM_THREADS` | `2` | CPU threads, validated within 1–8 |
| `IMAGE_BATCH_SIZE` | `4` | Images per inference batch, validated within 1–8 |

Image encoding accepts 1–60 real image paths. Each file is closed after
preprocessing, and one shared lock serializes image/text inference. The lock is
released between image batches so text inference can run during indexing.

## Run the processor service

Start local PostgreSQL, Kafka with its initialized upload topic, and private MinIO
with its initialized bucket. Export their shared `DATABASE_URL`, `KAFKA_BROKERS`,
`KAFKA_TOPIC`, `S3_ENDPOINT_INTERNAL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and `S3_BUCKET`
settings before running the host service. See the root `.env.example` for local
values; host endpoints use localhost, whereas Compose supplies internal names.

```sh
cd services/processor
uv run --frozen uvicorn framesearch_processor.app:app --host 127.0.0.1 --port 8000 --workers 1
```

Use exactly one Uvicorn worker so that the model loads once per service process.
Startup downloads and validates the genuine model in a background thread.
`GET /health/live` returns 200 while weights load; `GET /health/ready` returns 503
until real model warmup, schema/bucket checks, and Kafka subscription succeed.
One dedicated thread initializes the model and then processes one indexing job at
a time. One HTTP inference runs at a time, off the event loop, using the same model
and lock as image inference. Startup failures or an unresolved ingestion event keep
readiness at 503 and log the cause. Text inference remains available if its model
is healthy while ingestion is stopped. Fix the cause and recover pending work
before restarting the processor.

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
# Use --env-file .env instead when that file configures your existing local stack.
# Select processor explicitly for processor-only development.
docker compose --env-file .env.example -f infra/docker-compose.yml up -d --build processor
```

The image installs Python 3.12, pinned uv 0.6.3, locked CPU dependencies, and
FFmpeg/FFprobe. It removes uv's installer cache before exporting the dependency
layer. It runs one HTTP process as UID 10001, with a writable model cache at
`/cache/openclip`. The uv installer uses PyPI, avoiding another required image
registry. The model is downloaded at runtime rather than bundled in the image.
The actual downloaded checkpoint cache currently occupies approximately 577 MiB
on the host development machine.

Linux ARM64 image build and live HTTP verification passed using the genuine
cached checkpoint. One Docker memory snapshot after text inference was 1.462 GiB
for this processor. This is not a peak-memory or complete-stack measurement.

Shared Compose uses `services/processor` as the build context, keeps port 8000
internal, persists `/model-cache`, and probes `/health/ready`. Selecting processor
starts its database/migration, broker/topic, and storage/bucket dependencies.
Allow time for the initial model download. Keep exactly one processor replica and
one Uvicorn worker. The command is an integration instruction; the disposable
processor stack below and separate public API/browser happy paths are verified.
Full shared Compose startup remains pending. The frontend is not required for
these processor checks.

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
shared migration. Database failures raise `DatabaseError`; the check is not yet
wired into HTTP readiness. Atomic claims and frame persistence are described
below, including transactional ready/completed and failed states.

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

## Atomic job claiming

`Database.claim_job(job_id, video_id)` accepts two UUIDs from the validated event.
It locks the video first, matching Go's lock order, then locks only the job that
belongs to that video. A guarded update claims a queued job and increments its
`attempt_count`; the video moves to `processing` in the same transaction. Old
error fields are cleared. The result reaches the caller only after commit.

| Result outcome | Meaning |
| --- | --- |
| `claimed` | Contains a `ClaimedJob` with job/video IDs, original object key, declared byte size, and attempt count |
| `busy` | The job and video are already processing; this is not a terminal result |
| `terminal` | The job is completed or failed; no state is changed |
| `missing` | The video/job is absent or the job belongs to another video; no state is changed |

Inconsistent job/video states raise `JobStateError`. Terminal old failed jobs
remain terminal after Go creates a new retry job; delayed old events leave that
new job untouched. Duplicates do not increment attempts or modify timestamps.
Database errors or a rejected/suppressed video update roll back the entire claim.

This is a row-locked claim, not a lease. Stale recovery still requires stopping
the sole processor before Go resets an existing processing job to queued. No
Kafka offsets are handled by this component, and `busy`/`missing` outcomes must
not be treated as durable terminal transitions by the later worker.

With the disposable test database and environment described above:

```sh
FRAMESEARCH_DATABASE_TEST=1 uv run --frozen pytest -q tests/test_database.py tests/test_job_claims.py
```

The new claim suite has 34 checks: 32 against actual PostgreSQL and two UUID input
guards. Together with connection/configuration regression checks, **56 passed,
no skips** (0.84 seconds) on Linux ARM64. Tests verify concurrent claims, video
before job lock order, duplicate and delayed events, all job/video status pairs,
reclaimed jobs, and rollback after actual database-trigger failures. Test rows
and uniquely named triggers/functions are cleaned up; use only a disposable
database. The unchanged shared migration is used directly.

The packaged production image also passed a real claim/duplicate check as UID
10001 without mounting source code. The host suite passed **159 tests with 72
skips** (6.52 seconds); live database/media/model checks require their opt-in
environments. Full Kafka-to-indexing integration remains pending.

## Idempotent frame persistence

`Database.upsert_frames(claim, records)` writes 1–60 `FrameRecord` values in one
transaction. The caller must upload each thumbnail successfully before passing
its returned key and real embedding to the database:

```python
from framesearch_processor.database import FrameRecord

# `claim` is the ClaimedJob returned by a successful claim_job call.
# Keep media extraction, model inference, and storage requests outside DB locks.
records = []
for frame, vector in zip(clip.frames, vectors, strict=True):
    key = store.upload_thumbnail(claim.video_id, frame, model.model_version)
    records.append(FrameRecord(frame.timestamp_ms, key, vector, model.model_version))
frame_ids = database.upsert_frames(claim, records)
```

The method validates all inputs before connecting: timestamps, deterministic
thumbnail keys, the frozen model version, and 512 finite L2-normalized coordinates.
Duplicate timestamps in one batch are rejected. It locks video before job and
requires both to be processing with the receipt's current attempt count. A receipt
from before recovery or from an older manual retry job cannot write frames.
Recovery still requires stopping the sole processor first; this is not a lease.

Conflicts on `(video_id, timestamp_ms, model_version)` update the embedding and
thumbnail key while preserving the frame UUID and creation time. Returned UUIDs
match input order and become available after commit. A later frame failure rolls
back earlier inserts and updates in that batch. Successful writes leave job/video
status unchanged, so processing frames remain excluded by Go's ready-only search.
This method does not check S3 object existence or prune older rows. Success
finalization below verifies the complete expected frame set before marking ready.

```sh
FRAMESEARCH_DATABASE_TEST=1 uv run --frozen pytest -q tests/test_frame_persistence.py
# For the real combined pipeline, also configure a disposable local MinIO,
# cache the real checkpoint, and enable FRAMESEARCH_MINIO_TEST=1,
# FRAMESEARCH_REAL_MODEL_TEST=1 and HF_HUB_OFFLINE=1.
```

The new suite contains 30 input checks and 14 live checks. Together with previous
database/configuration/claim tests, **100 passed, no skips** (5.82 seconds), using
actual PostgreSQL 16/pgvector 0.8.7, MinIO, FFmpeg, and cached CPU OpenCLIP weights.
It verifies concurrent retries, stable IDs, 60-frame batches, stale claims, model
isolation, and whole-batch rollback under database-trigger failures. The combined
pipeline persisted genuine vectors for timestamps `0, 3000, 6000` and executed a
real text-to-vector cosine query. This does not measure semantic search accuracy
or exercise Go/Kafka ingestion. Packaged-image verification passed as UID 10001
without mounting source. Host regression: **189 passed, 86 skipped** (3.97 seconds).

## Successful job completion

After successful uploads and frame persistence, call
`Database.complete_job(claim, clip.metadata.duration_seconds, expected_timestamps)`.
The expected timestamps must come from the complete extraction result, containing
1–60 unique integer timestamps. Duration must be finite, positive, and at most
180 seconds. The caller must finish all thumbnail PUTs before invoking completion;
this database method cannot verify remote storage durability.

In one video-first locked transaction it validates the current processing claim,
checks that every expected active-model frame has its deterministic key and a
normalized vector, removes obsolete active-model rows from previous attempts,
and changes video to `ready` and job to `completed`. It stores actual duration,
clears both errors, and updates both timestamps. Other model rows remain isolated.
Any missing/invalid frame, stale claim, or failed status update prevents readiness
and rolls back the transaction, including frame cleanup. Only a successful return
confirms the commit; a later duplicate event sees the terminal state via claim_job.

Added 33 checks: 17 input guards and 16 live PostgreSQL checks. The focused
database suite, including the previous real MinIO/CLIP persistence pipeline,
passed **133 tests, no skips** (8.24 seconds). Success boundaries include one frame
and 60 frames/180 seconds. Trigger tests reject or suppress either status update
and verify both states, duration, and pruning roll back. Packaged-image completion
passed as UID 10001 without a source mount. Host suite: **206 passed, 102 skipped**
(5.63 seconds). Failure recording and Kafka acknowledgment remain separate work.

## Terminal job failure

`Database.fail_job(claim, reason)` stores the same trimmed actionable reason in
the video's `processing_error` and job's `last_error`, and moves both rows to
`failed` in one video-first locked transaction. Reasons contain 1–1000 Unicode
characters after trimming; the later worker must use bounded messages rather
than dumping exception traces into these public fields.

Only the current processing claim can fail. Queued/completed videos, stale
attempt receipts, and older failed jobs cannot overwrite a newer retry. Failed
or suppressed updates roll back both rows. Partial frame rows remain hidden by
ready-only search and can be upserted by a later manual retry. A successful
return confirms durable terminal failure; this method does not commit Kafka
offsets or schedule retries.

Added 18 checks: five input guards and 13 actual PostgreSQL checks. The focused
live suite passed **151 tests, no skips** (6.17 seconds), including a failed job
followed by a new retry that reuses frame IDs and completes while preserving the
old job's failure history. Packaged success/failure checks passed as UID 10001.
Host regression: **211 passed, 115 skipped** (3.49 seconds).

## One connected indexing attempt

`VideoIndexer(database, storage, embedder).index(claim)` consumes an already
claimed job. It downloads the original database key, validates/decodes the MP4,
encodes actual JPEGs with the supplied shared OpenCLIP model, uploads every
thumbnail, upserts the batch, and commits successful completion. Blocking media,
model, and storage work happens before the short database transactions. Temporary
sources/JPEGs are cleaned even when a downstream operation fails.

The caller owns claim handling, bounded retries, and durable failure recording.
An interrupted attempt raises its real error and leaves processing state intact;
retry uses the same claim receipt and deterministic thumbnail/frame keys. No
Kafka offsets or automatic retry policy are handled here. Reuse the HTTP service's
single model rather than constructing a second model for indexing.

Three real integration checks passed: successful ready indexing and actual
text-vector retrieval, interrupted second-thumbnail upload followed by successful
retry, and corrupt MP4 rejection followed by caller-recorded failure. With previous
database tests, **154 passed, no skips** (10.09 seconds), using actual MinIO,
FFmpeg, cached CPU OpenCLIP, and PostgreSQL. Host: **211 passed, 118 skipped**
(3.42 seconds). The production image builds and imports the packaged indexer as
UID 10001. Public API/Kafka integration and semantic evaluation remain pending.

## Shared Compose model cache

Developer 1's Compose sets `XDG_CACHE_HOME=/model-cache` and mounts its persistent
volume there. The processor now derives the default OpenCLIP cache from that
setting; `MODEL_CACHE_DIR` still overrides it explicitly. The image prepares
`/model-cache/openclip` with UID 10001 ownership so a new named volume can be used
without running the service as root. Standalone Docker retains `/cache/openclip`,
and a host with neither variable retains `.cache/openclip`.

All **13 settings checks passed**, including four new path/override checks. The
production image passed actual non-root named-volume writes, persistence in a
second container, and genuine offline CLIP inference through Compose's cache path.
Test volumes were removed and the real checkpoint retained. Docker ran out of
disk space during the first image build and left a damaged layer; the corrected
build removes **743.3 MiB** of installer cache before export, and the damaged
task-owned build records were cleaned up. No shared Compose file was changed.

## Kafka event envelope validation

`parse_media_uploaded(value, key)` accepts the frozen UTF-8 JSON envelope and
Kafka video key, returning typed event/video/job UUIDs and a UTC creation time.
It validates all six required fields, `event_type=media.uploaded`, integer schema
version 1, RFC3339 UTC timestamps (including Go's fractional seconds), and a
partition key matching the video UUID. Payloads are capped at 16 KiB. Duplicate
JSON fields, extra/missing envelope fields, unknown versions/types, tombstones,
invalid IDs/times/encoding, and mismatched keys raise `EventValidationError`.

Parsing does not touch the database or acknowledge a message. A malformed event
does not identify a trustworthy job to mark failed, so the future consumer must
keep it unacknowledged and report the problem. This checkpoint adds no broker
dependency or consumer loop. All **41 focused checks passed** (0.02 seconds).

## Single-job retry policy

`JobProcessor(database, indexer, stop_event=...).process(event)` claims a queued
event's job once and runs up to three indexing attempts with interruptible
one- and two-second backoffs. Retries reuse the claim and stable frame/object
keys; `attempt_count` tracks database claims, not individual local attempts.
The indexer uses the same already-loaded model as HTTP inference.

Successful indexing returns `completed` only after its ready/completed commit.
Invalid MP4s fail immediately with a bounded actionable reason. Exhausted storage,
media-tool, or inference errors return `failed` only after both failed states
commit. Existing completed/failed jobs return `terminal` without reindexing.
Only these returned outcomes may be acknowledged by the Kafka caller.

Busy/missing jobs, stale claims, database errors without a confirmed terminal
commit, and interrupted retry backoff raise instead. They must remain
unacknowledged. An interrupted job remains processing; stop the sole processor
before using Go's reconciliation command to recover it. A lost commit response
can leave an already-terminal job pending; redelivery checks its durable state.
This policy does not consume Kafka or create a second model/worker.

```sh
uv run --frozen pytest -q tests/test_jobs.py
# With disposable PostgreSQL/MinIO and cached real weights configured:
FRAMESEARCH_DATABASE_TEST=1 FRAMESEARCH_MINIO_TEST=1 \
  FRAMESEARCH_REAL_MODEL_TEST=1 HF_HUB_OFFLINE=1 \
  uv run --frozen pytest -q tests/test_job_processing.py
```

The 24 policy checks pass, and three actual pipeline checks verify queued-to-ready
completion, duplicate handling, a transient thumbnail upload followed by successful
retry, and immediate corrupt-upload failure. The focused live regression suite
passed **235 tests, no skips** (13.68 seconds) using PostgreSQL 17/pgvector 0.8.0,
matching shared Compose, plus actual MinIO/FFmpeg/cached CPU CLIP. Packaged import
and database checks pass as UID 10001. Host regression: **280 passed, 121 skipped**
(4.03 seconds); opt-in integration/tooling checks account for the skips.

## Kafka client configuration

The processor pins `confluent-kafka==2.15.1`, including native librdkafka, in the
existing lockfile. CPU macOS ARM64 and Linux ARM64 wheels are available; no broker
or model is bundled into that dependency. `KafkaSettings.from_env()` reads:

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAFKA_BROKERS` | `localhost:9092` | Comma-separated host:port endpoints; Compose supplies `kafka:29092` |
| `KAFKA_TOPIC` | `media.uploaded` | Shared upload-event topic |
| `KAFKA_CONSUMER_GROUP` | `framesearch-processor` | Processor-local stable group; retain it across restarts |

Configuration rejects empty/invalid endpoints and topic/group identifiers before
constructing a native client. All **34 Kafka configuration checks pass**, with
**62 focused configuration checks passing** (0.03 seconds). The actual native
2.15.1 consumer constructs and closes successfully on macOS ARM64. This checkpoint
does not subscribe, consume, acknowledge, or change the HTTP lifecycle.

## Manual Kafka offsets

`UploadConsumer(KafkaSettings.from_env())` checks existing topic/partition metadata
before subscribing. It uses the classic consumer protocol for Kafka 3.9, earliest
offsets for a new group, a one-hour maximum interval between polls for CPU indexing,
and a bounded prefetch queue. Automatic topic creation, offset storage, and offset
commits are disabled. Use the adapter from one worker thread.

`poll()` waits at most one second and returns one pending message. Another poll
is rejected until `acknowledge(message)` confirms a synchronous commit of that
exact message's next offset, including the returned partition/error/offset.
The caller must first confirm durable completed/failed state through `JobProcessor`.
Commit errors leave the event pending. `close()` leaves the group without committing
pending work. The serial loop below connects it to jobs during HTTP startup.

From the repo root, run a disposable real-broker check with no published ports:

```sh
python services/processor/tests/run_kafka_tests.py
# Reuse the images after a successful current build:
python services/processor/tests/run_kafka_tests.py --skip-build
```

The helper builds the production/dev images, starts Kafka 3.9.0 in an internal
test network, runs checks, then removes its containers/network. Each real test
creates and removes its own unique topic/group. **134 focused checks passed,
no skips** (5.77 seconds), including three actual broker tests proving uncommitted
redelivery, restart at the next committed offset, and no missing-topic creation.
There are 32 new adapter unit checks for failure/ordering/close guards. Host suite:
**346 passed, 124 skipped** (4.42 seconds). Actual production-image native client
construction and genuine offline CLIP inference pass as UID 10001 without source
mounts. No public API ingestion or semantic evaluation is claimed by these tests.

## Serial Kafka ingestion loop

`IngestionWorker(consumer, jobs, stop_event).run()` starts the consumer and handles
one event/job at a time on its calling worker thread. It parses the frozen envelope,
calls `JobProcessor.process`, and acknowledges only a confirmed completed/failed/
already-terminal outcome. Its readiness event is cleared on every exit. The service
must give the job processor and worker the same shutdown event and shared model.

Malformed events, busy/missing jobs, database uncertainty, and offset commit failures
stop consumption, close the consumer, and leave the unresolved offset pending.
The loop never moves past that event to acknowledge later work. Investigate malformed
records; for interrupted processing, stop the sole processor before using Go's
reconciliation command. No DLQ or claim lease is introduced.

From the repo root with genuine weights already cached:

```sh
python services/processor/tests/run_kafka_tests.py --indexing
# Reuse the current production/dev images:
python services/processor/tests/run_kafka_tests.py --indexing --skip-build
```

This adds disposable PostgreSQL 17/pgvector 0.8.0 and MinIO to the isolated Kafka
network, applies the unchanged shared migration, mounts weights/source read-only,
and cleans its test resources. **156 focused checks passed, no skips** (12.95 seconds),
including 17 loop checks and five actual Kafka-to-database pipeline checks. Real
events produce three genuine normalized vectors/JPEG objects at `0, 3000, 6000`,
duplicates cause one DB claim, corrupt MP4s commit failed state, and malformed/busy/
missing events leave offsets and later queued work untouched. Host suite:
**363 passed, 129 skipped** (4.46 seconds). This is not public Go API ingestion or
semantic evaluation. Service startup now launches this loop as described below.

## Shared model and worker lifecycle

The production app starts one `ProcessorRuntime` thread. It loads/warmups real
OpenCLIP once, validates the existing database schema and private storage bucket,
and runs the Kafka consumer with a `VideoIndexer` referencing that same model.
Model loading, media processing, storage/DB calls, and broker polling stay off the
HTTP event loop. The existing model lock bounds concurrent image/text inference.

`/health/ready` returns 503 during model loading, worker initialization, shutdown,
or ingestion failure, with a bounded public reason. `/health/live` remains available.
An unresolved event stops consumption and leaves its offset pending. Shutdown
signals the shared stop event, interrupts retry backoff, and waits for the current
initialization/indexing operation and cleanup before releasing model references.
SIGTERM shutdown was verified on the packaged Uvicorn process. If the process is
forcibly killed, recover its stale processing job after it has stopped.

The updated `--indexing` helper runs **186 focused checks, no skips** (19.42 seconds).
Twelve lifecycle/builder checks verify model identity, initialization/error paths,
HTTP responsiveness, resource cleanup, and waiting for in-flight shutdown. Two
actual packaged Uvicorn socket checks verify genuine text embeddings alongside
Kafka indexing/duplicate handling, three durable real frame vectors/thumbnails,
actual text-to-pgvector SQL, one logged model load, and SIGTERM cleanup. A malformed
Kafka event produces `worker_failed` readiness, no committed offset, and untouched
later queued work. Host suite: **375 passed, 131 skipped** (4.43 seconds).

For valid queued work left by the DB-to-Kafka publish gap, Go provides
`make reconcile`. For stale processing jobs, stop the sole processor before Go's
stale-recovery pass. For processor-only recovery, use these explicit
equivalents of the root `reconcile-stale` target (use your existing env file):

```sh
docker compose --env-file .env.example -f infra/docker-compose.yml stop processor
docker compose --env-file .env.example -f infra/docker-compose.yml run --rm --no-deps api \
  --reconcile --include-stale --processor-stopped
docker compose --env-file .env.example -f infra/docker-compose.yml start processor
```

The API image must already be built; shared `RECONCILE_STALE_AFTER` defaults to
15 minutes. This recovery republishes valid queued/stale jobs; malformed Kafka
records require investigation and explicit resolution. There is no claim lease,
DLQ, or atomic database/Kafka transaction. Public API and browser happy-path
checkpoints are recorded below and in the owned web verification report.
Synthetic relevance results are recorded below.

## Generated data and labeled search check

`scripts/create_demo_clips.py` produces three original 6.2-second MP4 clips:
red circle, blue square, and green triangle. The five human-readable expected
matches in `scripts/visual-search-labels.json` were committed before inference.
See `scripts/README.md` for host/Docker generation commands and use the resulting
MP4s for the later upload demo.

From the repository root, with the current processor/test images and genuine
weights cached:

```sh
python services/processor/tests/run_kafka_tests.py --indexing --semantic --skip-build
```

This executes only the labeled check and prints the measured JSON results. It
generates fresh clips, seeds test-owned queued jobs, and runs real packaged HTTP,
Kafka, private MinIO, FFmpeg, CLIP, and pgvector. The public Go API is excluded.
All nine frames are ready before genuine HTTP text vectors rank them by exact
cosine distance. Test resources are removed afterward.

**One live evaluation test passed, no skips** (10.66 seconds). All five queries
ranked the expected clip first: top-1 video accuracy **5/5**, Recall@5 **5/5**
(expected clip among the top five frame results). Observations and raw cosine
scores are in `evaluations/generated-shapes-v1.json`. This tiny synthetic set
does not measure relevance on real footage or changing scenes. The evaluator
records semantic misses honestly rather than requiring a perfect score to pass.

For actual Commons footage, first download/prepare the credited excerpts using
`scripts/prepare_real_clips.py` (commands in `scripts/README.md`), then run:

```sh
python services/processor/tests/run_kafka_tests.py --indexing --semantic --real-footage --skip-build
```

This reuses local hash-verified MP4s without downloading inside tests. The nine
labels in `scripts/real-footage-labels.json` were committed before inference and
include relevant sampled timestamps. **One live check passed, no skips** (7.88
seconds): all nine queries found the correct video first, while **7/9** found the
correct moment first. A labeled relevant frame appeared in the top five for
all nine queries. Yellow-front-train and empty-platform queries missed their
first-frame targets. Mean frame Recall@5 is **85.2%**, Precision@5 **82.2%**.
Definitions, the misses, and scope are in `VERIFICATION.md`; raw scores/source
hashes are in `evaluations/commons-real-v1.json`. This is a small real-footage
check, not a representative accuracy benchmark. Add `--real-footage` to `--full`
to use this dataset instead of generated shapes in a future complete run.

## Integration notes for Developer 1

The actual Go API queue smoke can run independently of the frontend:

```sh
python services/processor/tests/run_kafka_tests.py --indexing --api
# After successfully building the current API/processor test images:
python services/processor/tests/run_kafka_tests.py --indexing --api --skip-build
```

Requires cached real CLIP and prepared Commons excerpts from
`scripts/prepare_real_clips.py`. It builds the unchanged canonical Go API image,
copies its actual executable into the test runtime, and uses disposable real
Kafka/MinIO/PostgreSQL with the unchanged migration. It creates all videos/jobs
through public HTTP upload/complete calls, checks three queued jobs/events while
the processor is stopped, starts the real service, and verifies all three jobs,
18 real frames, and committed offset 3. Duplicate completions add no jobs/events.
Public search/filtering, signed JPEG/MP4, byte-range reads, and 403 unsigned reads
pass. **One live check passed, zero skips** (9.16 seconds); summary is in
`evaluations/api-queue-smoke.json`. Test resources are removed afterward. This
does not verify a browser or shared Compose's default public addresses. Public
failed-upload retry and stale-crash recovery remain separate integration checks.

The complete processor/AI suite can run without the Go service or frontend:

```sh
python services/processor/tests/run_kafka_tests.py --indexing --full
# With the current successfully built images:
python services/processor/tests/run_kafka_tests.py --indexing --full --skip-build
```

All live dependency flags are enabled, including real media and model checks.
The complete suite at `02a95cc` passed **506 tests with zero skips** (47.94 seconds).
The separately executed labeled check adds one more test to future full runs. See
`VERIFICATION.md` for the tested behavior and remaining integration boundaries.
Add `--api` to `--indexing --full` to include the new opt-in public API smoke and
build its combined test image; otherwise that case is deliberately skipped.

The frozen model version and all shared contracts are unchanged. No Go, schema,
infrastructure, shared docs, or environment files are modified by this work.
The internal HTTP server listens on `0.0.0.0:8000` within Compose and starts the
single indexing worker automatically. Shared MinIO must retain its persistent
data, private bucket, credentials/CORS, and Go's browser-accessible signing
endpoint. The processor uses only the internal storage endpoint. Remaining
integration includes full shared Compose startup and public retry/recovery. The
frontend's real browser happy path passes separately; see
`../../apps/web/VERIFICATION.md`.
