# Developer 2 processor checkpoints

Base contract/backend commit: `1028604`. Branch: `codex/processor-core`.
Remote main was reorganized into feature commits; the processor work was carried
onto this fresh branch without modifying or discarding the earlier branch.
Each feature gets a separate commit after its focused checks.

| Feature | Status | Verification |
| --- | --- | --- |
| Pinned CPU OpenCLIP model, text/image embeddings | Implemented, unit verified | 22 unit tests pass; real checkpoint test running |
| Internal text HTTP endpoint and readiness | In progress | Tests pending |
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
  under the network sandbox. Retried with approved network access; actual
  checkpoint download/inference verification is still in progress.
- Read the latest migration and database/upload guide at `1028604`. The 512-vector
  model contract, frame uniqueness, video-first lock order, and active-job index
  match the planned processor. No schema changes proposed or applied.
