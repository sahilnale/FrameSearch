# Developer 2 processor checkpoints

Base contract/backend commit: `1028604`. Branch: `codex/processor-core`.
Remote main was reorganized into feature commits; the processor work was carried
onto this fresh branch without modifying or discarding the earlier branch.
Each feature gets a separate commit after its focused checks.

| Feature | Status | Verification |
| --- | --- | --- |
| Pinned CPU OpenCLIP model, text/image embeddings | Implemented, verified, pushed | Core commit `500fc9d`; 22 unit tests and real CPU inference pass |
| Internal text HTTP endpoint and readiness | Implemented, verified | 38 unit tests pass; real HTTP test passes |
| Processor container packaging | In progress | Docker daemon available; build pending |
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
