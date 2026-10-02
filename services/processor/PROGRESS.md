# Developer 2 processor checkpoints

Base contract/backend commit: `1028604`. Branch: `codex/processor-core`.
Remote main was reorganized into feature commits; the processor work was carried
onto this fresh branch without modifying or discarding the earlier branch.
Each feature gets a separate commit after its focused checks.

| Feature | Status | Verification |
| --- | --- | --- |
| Pinned CPU OpenCLIP model, text/image embeddings | Implemented, verified, pushed | Core commit `500fc9d`; 22 unit tests and real CPU inference pass |
| Internal text HTTP endpoint and readiness | Implemented, verified, pushed | Commit `de4cb2a`; 38 unit tests and real HTTP test pass |
| Processor container packaging | Implemented, verified | Linux ARM64 build and live real HTTP check pass |
| FFprobe validation and timestamped FFmpeg sampling | Planned | Not run |
| Kafka, MinIO, pgvector indexing and recovery | Planned | Not run |
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

## Next checkpoint

FFprobe metadata validation and accurate timestamped FFmpeg sampling, with tests
for invalid media, duration/file limits, sampling at 3-second intervals and the
60-frame cap. Kafka, storage and transactional database indexing follow as their
own feature commits. Full end-to-end functionality is not yet implemented.
