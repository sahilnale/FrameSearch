# Developer 2 processor checkpoints

Base contract/backend commit: `1028604`. Branch: `codex/processor-core`.
Synced latest main `1b1185d` through a merge; the database schema is unchanged.
Processor checkpoints through atomic job claims were merged and pushed to main
at `822c29c`. This branch now starts from that main commit; the schema is unchanged.
Remote main was reorganized into feature commits; the processor work was carried
onto this fresh branch without modifying or discarding the earlier branch.
Each commit covers one small behavior with its focused checks. Database work is
split into connection/configuration, claims, frame upserts, and terminal states.

| Feature | Status | Verification |
| --- | --- | --- |
| Pinned CPU OpenCLIP model, text/image embeddings | Implemented, verified, pushed | Core commit `500fc9d`; 22 unit tests and real CPU inference pass |
| Internal text HTTP endpoint and readiness | Implemented, verified, pushed | Commit `de4cb2a`; 38 unit tests and real HTTP test pass |
| Processor container packaging | Implemented, verified, pushed | Commit `aa6df84`; Linux ARM64 build and live real HTTP check pass |
| FFprobe upload validation | Implemented, verified, pushed | Commit `32f2652`; 35 focused unit tests and 7 real-media checks pass |
| Timestamped FFmpeg sampling | Implemented, verified, pushed | Commit `ba46b36`; 121 passed, including real decoding and all real-model checks |
| MinIO source download and thumbnail upload | Implemented, verified, pushed | Commit `3407330`; 175 passed, no skips, including actual MinIO and real CPU inference |
| PostgreSQL connection/configuration and schema check | Implemented, verified, pushed | Commit `87cf40e`; 22 focused checks pass, including seven actual PostgreSQL checks and fifteen configuration checks |
| Atomic PostgreSQL job claiming | Implemented, verified, pushed | Commit `aa9a6a5`; 56 focused checks pass, including concurrent claims, lock order, duplicate handling, and rollback |
| Idempotent frame upserts | Implemented, verified, pushed | Commit `c6bc88b`; 100 focused checks pass, including real MinIO/FFmpeg/CLIP-to-pgvector persistence and retry checks |
| Transactional ready/completed states | Implemented, verified | 133 focused checks pass, including missing frame rejection and rollback of both states/duration/pruning |
| Transactional failed states | Planned | Not run |
| Kafka consumption, bounded retries and recovery | Planned | Not run |
| Real end-to-end smoke and semantic evaluation | Planned | Blocked on infrastructure and later features |

This file is Developer 2-owned. Developer 1 maintains the root progress document.
Only successfully executed checks will be marked verified here.

## Embedding core checks

- Locked installation with uv 0.6.3 / Python 3.12.7: passed.
- `python -m pytest -q tests/test_settings.py tests/test_embeddings.py`: 22 passed,
  one real-model test explicitly skipped by default (14.94 seconds).
- Ruff checks and formatting of core modules/tests: passed.
- `uv lock --check --offline`: passed.
- The explicit real-model test initially failed at Hugging Face DNS resolution
  under the network sandbox. Retried with approved network access: **passed**
  (389.91 seconds including first download). Actual text and image embeddings
  across three batches are finite, 512-dimensional and L2 normalized.
- Read the latest migration and database/upload guide at `1028604`. The 512-vector
  model contract, frame uniqueness, video-first lock order, and active-job index
  match the planned processor. No schema changes proposed or applied.

## Internal HTTP feature checks

- `python -m pytest -q`: 38 passed, two opt-in real-model tests skipped (2.52 seconds).
- Validated exact HTTP response, query trimming, Unicode character length,
  malformed JSON, model readiness/failure, and inference failure responses.
- Confirmed health requests finish while loading and while inference is blocked
  in a background thread; confirmed one model initialization per lifespan.
- Ruff checks: passed.
- `FRAMESEARCH_REAL_MODEL_TEST=1 HF_HUB_OFFLINE=1 python -m pytest -q -m real_model
  tests/test_real_http.py`: passed using genuine cached weights (3.94 seconds).
- No Kafka/MinIO/PostgreSQL services have been exercised at this milestone.

## Container packaging checks

- First build failed fetching the ghcr.io uv image with a registry timeout.
  Packaging now installs the same pinned uv 0.6.3 from PyPI instead.
- `docker build -t framesearch-processor:embedding-checkpoint services/processor`:
  passed on Docker Desktop Linux ARM64, Python 3.12.15, torch 2.10.0+cpu.
- Started the actual single-process server on loopback port 18000, with real
  cached weights mounted read-only and HF_HUB_OFFLINE=1. Model warmup succeeded.
- `PYTHONPATH=. python tests/check_service.py --url http://127.0.0.1:18000`:
  passed with network sandbox escalation; verifies liveness, readiness, fixed
  model version, and real normalized finite 512-dimensional text response.
- Container runs as UID/GID 10001; FFprobe 7.1.5 available.
- One `docker stats` snapshot after text inference: 1.462 GiB used. This does not
  measure peak indexing/startup RAM or the complete application's memory usage.
- Test container is stopped after verification; checkpoint cache stays on host.
- Dockerfile does not contain weights; Compose should persist `/cache` and keep
  port 8000 internal. No infrastructure files changed.

## Video validation checks

- Host focused tests: 35 passed, 7 skipped because FFmpeg/FFprobe are not installed
  on the host. Missing tooling is reported rather than treated as valid media.
- Full suite in the existing Linux ARM64 processor image, with current source
  mounted read-only and locked dev dependencies installed in the disposable
  container: **80 passed, 2 opt-in real-model tests skipped** (2.60 seconds).
- Seven real-media checks cover a valid MP4 with a non-MP4 filename, a corrupt
  file, Matroska/MOV disguised as MP4, audio-only MP4, exactly 180 seconds, and
  181 seconds. The fixtures are generated by actual FFmpeg and probed by FFprobe.
- Unit checks cover empty/missing/oversize files, declared-size mismatch,
  finite positive duration, playable video stream/cover art, broken metadata,
  missing tooling, timeout, and malformed probe output.
- Ruff checks and formatting: passed.
- No storage, database, or Kafka operations are performed by this feature.

## Frame extraction checks

- New sampling tests: 20 unit checks, 18 actual FFmpeg/media checks, and one
  opt-in real decode-to-OpenCLIP check. All passed in Linux ARM64 Docker.
- Full processor suite with current source mounted read-only and genuine cached
  weights mounted read-only, `HF_HUB_OFFLINE=1`,
  `FRAMESEARCH_REAL_MODEL_TEST=1`, `IMAGE_BATCH_SIZE=2`: **121 passed, no skips**
  (13.97 seconds). No new model download was needed.
- Validated 0.2/3/3.2/6.2-second boundaries and exactly 60 frames from a 180-second
  source, with timestamps `0, 3000, ..., 177000`.
- The 29.97 fps check compares sampling against all source frame timestamps from
  independent FFprobe output. At 36 seconds the selected timestamp is `36002`,
  preventing accumulated drift from sampling relative to the previous frame.
- Sparse/VFR input retains real timestamps and skips empty sampling intervals.
- A native in-app-browser check over a temporary localhost server with byte-range
  support found that resetting source start offsets produces incorrect seeks.
  The sampler preserves source PTS. Real regressions cover 0.25/2/10-second
  offsets and verify shifted frames' actual colors. Browser coverage is limited
  to these fixtures and this engine; the frontend is not implemented yet.
- Verified audio-first stream mapping, landscape/portrait/non-square-pixel
  aspect ratio, maximum 640-pixel JPEG edge, and repeatable filenames/content.
- Corrupt compressed packets with a readable MP4 header fail decoding. Temporary
  outputs are cleaned on success, tool failure, and downstream consumer errors;
  supplied source files remain intact.
- Actual extracted JPEGs produce finite normalized 512-dimensional vectors from
  the frozen OpenCLIP model, across bounded batches.
- Built `framesearch-processor:sampling-checkpoint` successfully. Verified actual
  extraction in the packaged image as production UID 10001, using a read-only
  source fixture: timestamps `0, 3000, 6000`, output cleanup, and source retention
  all passed. This check imports the packaged sampler without mounting source code.
- Ruff checks/formatting: passed. No Kafka, MinIO, or database operations added.

## MinIO storage checks

- Added pinned Boto3 `1.43.109` with locked dependencies. Uses the four shared
  S3 environment variables, `us-east-1` signing, and path-style requests, matching
  Go. Credentials are hidden from the settings representation.
- Focused host storage/configuration checks: **49 passed** (1.83 seconds).
  Botocore request stubs are limited to unit tests; application transfers use the
  real SDK. Covers bounded streaming, exact size/content-type checks, cleanup,
  media versus infrastructure errors, deterministic keys, valid JPEG bounds,
  Content-MD5, and failure before returning a thumbnail key.
- The attempted public MinIO image pull failed with an unavailable/access-denied
  registry response. Built the test-only server from the official pinned source
  tag `RELEASE.2025-10-15T17-29-55Z` using Go 1.24.8. This is a source build, not
  an official precompiled image. No shared infrastructure files were changed.
- `.venv/bin/python tests/run_minio_tests.py --real-model`: **175 passed, no
  skips** (18.86 seconds) on Linux ARM64 Docker. This includes five actual MinIO
  integration checks and all four genuine CPU OpenCLIP checks, using the existing
  cache offline.
- Actual MP4 upload/download, FFmpeg extraction at `0, 3000, 6000`, private JPEG
  upload, signed HTTP reads, metadata/content-type verification, and anonymous
  access rejection all passed. Processing the same clip twice leaves the same
  three thumbnail objects. Missing uploads, missing buckets, invalid metadata,
  and invalid credentials produce the expected error classes.
- Actual MinIO download-to-FFmpeg-to-OpenCLIP-to-thumbnail-upload check verifies
  all three vectors are finite, 512-dimensional, and L2 normalized. It does not
  measure search accuracy or exercise PostgreSQL/Kafka.
- Production image and test images built successfully. The processor and MinIO
  containers run as UID 10001; tests use an internal network with no published
  ports, disposable private buckets/credentials, and read-only source/cache
  mounts. Confirmed all harness containers and the network were removed.
- Ruff checks/formatting and `uv lock --check --offline`: passed.
- Storage is implemented as a reusable component. It is not wired into an
  ingestion worker or the HTTP readiness check. No Go, schema, infrastructure,
  shared environment files, or Developer 1-owned documentation were modified.
- AWS migration and actual AWS S3 transfers remain untested. Preserve object
  keys when copying data and coordinate bucket region with both signing clients.

## PostgreSQL connection checkpoint

- Fetched main again; it remains `1b1185d`. Read the master/Developer 2 specs and
  existing migration. No schema changes are needed or made for this checkpoint.
- Added pinned `psycopg[binary]==3.3.6` and its locked binary driver. Shared
  `DATABASE_URL` is required and validated without revealing credentials.
- Added short-lived connection contexts with commit, rollback, and close;
  connection/statement/lock timeouts are 5 seconds / 15 seconds / 5 seconds.
  The schema check requires the canonical tables and pgvector extension.
- Focused configuration/core/storage regression check: **73 passed** (3.48
  seconds), including 15 new PostgreSQL URL/credential-hiding checks.
- Full host suite: **157 passed, 40 skipped** (7.13 seconds). Skips include
  unavailable host media tools and opt-in real model/storage/database checks.
- Focused tests against actual PostgreSQL 16 / pgvector 0.8.7 on Linux ARM64:
  **22 passed, no skips** (0.24 seconds), including seven real database checks
  and fifteen configuration checks. Verified commit, rollback, closure on errors,
  separate connections, configured timeouts, query cancellation, missing schema,
  actual vector SQL, and invalid credentials.
- Built `framesearch-processor:database-connection` and its test image. The
  packaged production module passed the canonical schema check as UID 10001,
  without mounting application source. Tests applied the unchanged shared
  migration to an isolated disposable instance using the official pinned
  `pgvector/pgvector:0.8.7-pg16-bookworm` image. No ports were published.
- The first build failed after the host disk filled up; Docker then reported
  storage I/O errors. Cleared our disposable dependency cache; the user freed
  disk space and approved a Docker restart. Build and live checks passed after
  recovery. The real model checkpoint remains intact.
- Confirmed the disposable test server and network were removed. No shared
  migration or application rows were changed. Ruff checks, formatting, and
  offline lockfile validation pass.
- Connection logic is not wired into application readiness or a worker yet.
  Atomic claims, frame writes, and terminal transitions are separate commits.

## Atomic job claiming checkpoint

- Fetched main; still `1b1185d`. No migration, Go, infrastructure, or dependency
  changes are needed. Only Developer 2-owned processor files are modified.
- Added `Database.claim_job` using the shared video-first lock order and guarded
  queued-job update. Job/video become processing together; the attempt count is
  incremented once, old errors cleared, and source metadata returned after commit.
- Results distinguish claimed, busy, terminal, and missing/mismatched jobs.
  Inconsistent states raise `JobStateError`; nothing is partially committed.
  Late old failed-job events do not claim or modify a newer manual retry job.
- Added 34 focused claim checks: 32 real PostgreSQL checks and two UUID guards.
  Shared the existing connection-test fixture through `tests/conftest.py`.
- Real database/configuration suite: **56 passed, no skips** (0.84 seconds) on
  PostgreSQL 16 / pgvector 0.8.7, Linux ARM64, using the unchanged shared migration.
  Covers all 20 job/video status pairs, unknown/mismatched IDs, concurrent events,
  duplicate attempts/timestamps, late failed-job events, and requeued jobs.
- The lock-order check holds the video lock while a claimant waits and verifies
  the job can still be locked with NOWAIT. Trigger tests reject or suppress the
  subsequent video update and verify the job status/attempt increment roll back.
- Built `framesearch-processor:job-claims` and its dev test image using cached
  dependencies. Actual packaged claim/duplicate verification passed as UID 10001
  without mounting application source.
- Full host suite: **159 passed, 72 skipped** (6.52 seconds). Live media/model/
  storage/database checks retain their opt-in/tooling requirements.
- Tests used a disposable FrameSearch PostgreSQL container on an internal network
  with no published ports. The test container/network and test-owned rows,
  triggers, and functions were cleaned up. No unrelated service was started.
- Ruff checks/formatting, offline lockfile validation, and diff whitespace checks
  pass. Frame rows, final ready/failed transitions, Kafka, and worker lifecycle
  remain separate features.

## Idempotent frame persistence checkpoint

- Added bounded, validated `FrameRecord` batches using the existing shared
  `(video_id, timestamp_ms, model_version)` uniqueness constraint. No migration
  or dependency changes. Thumbnail keys must match the video/time/frozen model;
  vectors must contain 512 finite normalized coordinates.
- A video-first locked transaction verifies the processing claim's job and
  attempt count. Recovery/manual retry invalidate old receipts. Retry upserts
  preserve frame IDs and creation times; a later error rolls back the whole batch.
  Status remains processing, keeping partial results hidden from public search.
- Added 30 input checks and 14 live checks covering stable IDs, updated vectors,
  concurrent retries, 60 frames, invalid claims/states, old recovery/retry receipts,
  other model rows, and rollback after rejected/suppressed database inserts.
- Actual database/configuration suite: **100 passed, no skips** (5.82 seconds) on
  Linux ARM64, PostgreSQL 16/pgvector 0.8.7, using the unchanged shared migration.
  The combined real pipeline downloads a generated MP4 from private MinIO,
  extracts three JPEGs with FFmpeg, computes genuine CPU CLIP embeddings, uploads
  thumbnails, and upserts vectors twice without duplicate rows. Timestamps are
  `0, 3000, 6000`; actual text-to-pgvector cosine SQL also passes. This is not a
  semantic relevance evaluation or a full public Go/Kafka smoke test.
- Production and dev test images built successfully. Packaged persistence/retry
  verification passed as UID 10001 without mounting application source. Tests
  used internal networking, no published ports, disposable credentials/private
  buckets, and read-only source/checkpoint/migration mounts. All test containers,
  the network, and test-owned database rows/functions/triggers were removed.
- Full host suite: **189 passed, 86 skipped** (3.97 seconds). Ruff, formatting,
  offline lockfile validation, and whitespace checks pass. No unrelated services
  were started. Finalization still needs to verify the expected frame set; this
  write operation does not prune older rows or change terminal states.

## Successful job completion checkpoint

- Added `Database.complete_job` in its own feature. Requires the full extraction
  timeline, successful thumbnail uploads, valid duration, and the current claim.
  Checks expected active-model rows/keys/vector norms, removes obsolete rows from
  prior attempts, and commits ready/completed/duration/error clearing together.
  Other model rows remain untouched. Source/object existence remains the caller's
  responsibility; there is no storage/database distributed transaction.
- Added 33 checks: 17 input guards and 16 actual PostgreSQL checks. Missing rows,
  wrong keys/model/norm, outdated claims, and invalid states cannot mark ready.
  Trigger failures/suppression on either update roll back states, duration, and
  frame pruning. Tested one-frame and 60-frame/180-second success boundaries.
- Focused live database suite: **133 passed, no skips** (8.24 seconds), including
  previous claims/upserts and actual cached CLIP/MinIO/media persistence checks.
  Packaged production completion passed as UID 10001 without source mounts.
- Host regression: **206 passed, 102 skipped** (5.63 seconds). Ruff, formatting,
  and whitespace checks pass. Test resources and owned rows/triggers/functions
  were removed. The first test-image permission review timed out; the permitted
  retry succeeded. No shared schema/infrastructure or unrelated service changed.
- This feature is not wired into an ingestion worker yet. Failure recording and
  Kafka offsets remain subsequent checkpoints.

## Remaining sequence

1. Failed job and video transitions in the same transaction.
2. Kafka consumption, bounded retries, offset handling, and service lifecycle.
3. Real backend/processor end-to-end smoke test using the shared infrastructure.
4. Frontend upload, search, and playback, after the backend integration works.

Each feature remains a separate tested commit and is pushed at its checkpoint.
Full end-to-end functionality is not yet implemented.
