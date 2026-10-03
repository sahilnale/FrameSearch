# Developer 2 processor checkpoints

Base contract/backend commit: `1028604`. Branch: `codex/processor-core`.
Synced latest main `1b1185d` through a merge; the database schema is unchanged.
Fetched main again for the storage checkpoint; it remains at `1b1185d`.
Remote main was reorganized into feature commits; the processor work was carried
onto this fresh branch without modifying or discarding the earlier branch.
Each feature gets a separate commit after its focused checks.

| Feature | Status | Verification |
| --- | --- | --- |
| Pinned CPU OpenCLIP model, text/image embeddings | Implemented, verified, pushed | Core commit `500fc9d`; 22 unit tests and real CPU inference pass |
| Internal text HTTP endpoint and readiness | Implemented, verified, pushed | Commit `de4cb2a`; 38 unit tests and real HTTP test pass |
| Processor container packaging | Implemented, verified, pushed | Commit `aa6df84`; Linux ARM64 build and live real HTTP check pass |
| FFprobe upload validation | Implemented, verified, pushed | Commit `32f2652`; 35 focused unit tests and 7 real-media checks pass |
| Timestamped FFmpeg sampling | Implemented, verified, pushed | Commit `ba46b36`; 121 passed, including real decoding and all real-model checks |
| MinIO source download and thumbnail upload | Implemented, verified | Full suite: 175 passed, no skips, including actual MinIO and real CPU inference |
| PostgreSQL job claims, frame persistence and terminal states | Planned | Not run |
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

## Remaining sequence

1. Transactional PostgreSQL job claims, frame upserts, and terminal states.
2. Kafka consumption, bounded retries, offset handling, and service lifecycle.
3. Real backend/processor end-to-end smoke test using the shared infrastructure.
4. Frontend upload, search, and playback, after the backend integration works.

Each feature remains a separate tested commit and is pushed at its checkpoint.
Full end-to-end functionality is not yet implemented.
