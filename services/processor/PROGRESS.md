# Developer 2 processor checkpoints

Base contract/backend commit: `1028604`. Branch: `codex/processor-core`.
Synced main's infrastructure at `7f9308f` through merge `5637745`; schema unchanged.
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
| Transactional ready/completed states | Implemented, verified, pushed | Commit `a6b8913`; 133 focused checks pass, including missing frame rejection and rollback of both states/duration/pruning |
| Transactional failed states | Implemented, verified, pushed | Commit `74c4de1`; 151 focused checks pass, including failure rollback, retry completion, and protection of newer jobs |
| Connected real video indexing operation | Implemented, verified, pushed | Commit `27bd084`; 154 focused live checks pass, including actual ready indexing, interrupted upload recovery, and corrupt MP4 rejection |
| Frozen Kafka event envelope validation | Implemented, verified, pushed | Commit `94bd191`; 41 focused checks pass; no broker consumption or acknowledgment yet |
| Persistent model cache matching shared Compose | Implemented, verified, pushed | Commit `a096699`; 13 settings checks, non-root named-volume persistence, and real offline CLIP inference pass |
| Bounded single-job retries and durable outcomes | Implemented, verified, pushed | Commit `04f35db`; 24 policy checks and three actual queued-event pipeline checks; 235 focused live checks pass |
| Pinned Kafka client and shared configuration | Implemented, verified, pushed | Commit `8e029ad`; 34 Kafka configuration checks; 62 focused configuration checks pass; native consumer constructs/closes |
| Kafka consumer with manual offset commits | Implemented, verified, pushed | Commit `ed6e68e`; 32 adapter checks and three real Kafka 3.9 redelivery/offset/topic checks; 134 focused checks pass |
| Serial Kafka-to-job ingestion loop | Implemented, verified, pushed | Commit `f0f3e44`; 17 loop checks and five actual Kafka-to-CLIP/MinIO/PostgreSQL checks; 156 focused checks pass |
| Shared HTTP/worker lifecycle | Implemented, verified | 12 lifecycle checks and two actual packaged Uvicorn socket checks; 186 focused checks pass |
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

## Terminal job failure checkpoint

- Added `Database.fail_job` separately from success handling. Validates a bounded
  actionable reason and the current claim, then commits both failed statuses,
  error fields, and timestamps together. Preserves partial rows for retry; Go's
  ready-only search keeps them hidden. No automatic retries or Kafka commits yet.
- Added 18 checks: five reason input guards and 13 real PostgreSQL checks. Covers
  partial/no frames, trimmed reasons, Unicode character bounds, wrong/stale/queued/
  completed claims, both update failures/suppression, and an actual failed-to-new-
  retry-to-ready database flow with stable frame IDs and intact failure history.
- Live database/configuration suite: **151 passed, no skips** (6.17 seconds),
  including the real MinIO/FFmpeg/OpenCLIP persistence regression. Built production
  and test images; packaged upserts/completion/failure passed as UID 10001 with
  no application source mount. Host: **211 passed, 115 skipped** (3.49 seconds).
- Ruff/formatting/whitespace checks pass. Tests removed their disposable servers,
  network, private bucket, and owned rows/triggers/functions. No shared migration,
  Go, infrastructure, or unrelated service changed.

## Connected video indexing checkpoint

- Added `VideoIndexer.index` to connect download, actual MP4 validation/decoding,
  shared model inference, successful thumbnail PUTs, frame upserts, and completion.
  Caller supplies the claimed job and already-loaded model, and owns failure/
  retry policy. Blocking work stays outside database locks; context managers clean
  temporary files on success/error. No Kafka or HTTP lifecycle changes yet.
- Three actual integration checks pass: generated MP4 becomes ready with three
  genuine vectors/thumbnails and actual ready-only text-vector SQL results; an
  interrupted second thumbnail upload leaves processing/no rows/clean temp files,
  then retries to ready with only three objects; corrupt MP4 rejection is followed
  by explicit caller-recorded failed state. This is not a relevance evaluation.
- Focused live suite: **154 passed, no skips** (10.09 seconds). Host regression:
  **211 passed, 118 skipped** (3.42 seconds). Production/dev images build, packaged
  indexer import and DB checks pass as UID 10001, Ruff/formatting/whitespace pass,
  and disposable test resources are removed. No unrelated project was started.
- Fetched new main `7f9308f`, which adds Developer 1's backend infrastructure.
  Shared schema/Go contracts remain unchanged. Sync follows this feature commit;
  processor cache compatibility and Kafka wiring are separate checkpoints.

## Kafka envelope validation checkpoint

- Added a pure parser for the exact frozen JSON envelope and keyed video UUID.
  Returns UUIDs and UTC datetime; validates type/version/fields/timestamp/key,
  rejects duplicate fields and malformed/nonobject/oversize/tombstone payloads.
  Supports Go's RFC3339 nanosecond timestamps. No raw payloads in error messages.
- **41 focused tests passed** (0.02 seconds). Ruff and formatting pass. No new
  dependencies, database writes, broker operations, or acknowledgment behavior.
  Malformed events cannot safely identify a job; consumer handling comes next.
- Main's infrastructure was synced without conflicts at merge `5637745` and
  pushed. The model-cache compatibility work is separate. Its image built, but
  Docker shut down with a no-space-left-on-device error before live cache checks;
  container/Kafka verification awaits more host disk space. Existing checkpoint
  weights remain intact and unrelated projects have not been started.

## Shared Compose cache compatibility checkpoint

- Adopted `XDG_CACHE_HOME/openclip` when MODEL_CACHE_DIR is not explicitly set.
  Standalone Docker defaults to `/cache/openclip`; shared Compose defaults to
  its existing `/model-cache/openclip` volume. Host default remains unchanged.
  Image creates the Compose directory with UID 10001 ownership. No A-owned file
  changed; root credentials/environment defaults remain Developer 1's contracts.
- **13 settings tests passed**, including four new path/override guards. Actual
  production-image named-volume writes passed as UID 10001 and survived a second
  container. Genuine offline CLIP text inference passed with the existing weights
  at Compose's cache path, without MODEL_CACHE_DIR override. Test volume removed.
- Docker ran out of disk space and shut down during the first build. After user
  cleanup/restart, import checks exposed empty source files and a corrupt torch
  dependency layer in that failed image. Rebuilt the affected layer and cleared
  uv's installer cache before export (743.3 MiB), then passed real inference.
- Removed only the identified corrupt FrameSearch image/cache records and our
  completed disposable MinIO compiler cache. Built MinIO image, checkpoint weights,
  application volumes, and unrelated project caches were retained. Host free
  space after targeted cleanup: approximately 3.3 GiB. No Metro container started.
- Ruff, formatting, and whitespace checks pass. Job policy is a separate feature;
  Kafka broker/offset/lifecycle and public end-to-end tests remain pending.

## Single-job retry policy checkpoint

- Added `JobProcessor` separately from Kafka consumption. Claims once, retries
  the same receipt up to three attempts with interruptible one/two-second backoff,
  and returns only confirmed completed/failed/already-terminal outcomes. Invalid
  media fails immediately; infrastructure exhaustion stores a bounded public
  reason while private exception details stay in logs.
- Busy/missing jobs, stale claims, unsuccessful/ambiguous database commits, and
  interrupted retries cannot return an acknowledgment outcome. Failed active jobs
  remain subject to Go's existing manual retry/recovery; there is no lease or
  second queue. Local retries do not increment the DB claim attempt count.
- **24 policy checks passed** (1.95 seconds). Three real checks cover a queued
  upload completing, duplicate handling without new frames, transient thumbnail
  PUT interruption followed by completion, and actual corrupt-MP4 failure.
- Focused live regression: **235 passed, no skips** (13.68 seconds), against
  PostgreSQL 17/pgvector 0.8.0 as pinned in shared Compose, actual private MinIO,
  FFmpeg, and genuine cached CPU CLIP. Production/dev images build; packaged
  imports and database smoke pass as UID 10001 without application source mounts.
  Disposable containers/network and test-owned objects/rows/triggers were removed.
- Host regression: **280 passed, 121 skipped** (4.03 seconds). Ruff, formatting,
  offline lockfile validation, and whitespace checks pass. No Developer 1 files
  changed and no unrelated project service started. Kafka offsets and the shared
  HTTP/worker lifecycle remain separate checkpoints.

## Kafka client configuration checkpoint

- Added only the pinned native `confluent-kafka==2.15.1` dependency and
  `KafkaSettings`. Existing dependency versions remain unchanged; the lockfile
  adds one package. Shared broker/topic variables match Compose; the optional
  processor-local group defaults to `framesearch-processor`.
- Validates CSV host:port endpoints (including IPv6) and bounded topic/group
  identifiers. **34 focused Kafka checks** and **62 configuration regression
  checks pass** (0.03 seconds). Native client/librdkafka both report 2.15.1;
  actual consumer construction and close pass on macOS ARM64.
- Ruff, formatting, offline lockfile validation, and whitespace checks pass.
  No broker subscription, offset handling, HTTP lifecycle, or shared file changes
  are included. Kafka 3.9.0, matching Compose, is cached for subsequent real tests.

## Manual Kafka consumer checkpoint

- Added `UploadConsumer` with one pending record, bounded polling/prefetch,
  disabled automatic offset storage/commit/topic creation, and the classic group
  protocol for shared Kafka 3.9. Checks existing topic metadata before subscription.
  Rejects another poll until the pending record's synchronous next-offset commit
  is confirmed, including each returned partition error and exact coordinates.
- **32 adapter unit checks pass**, covering receive/start/commit failures,
  unexpected message coordinates, acknowledgment ordering, and idempotent close.
  Three actual broker checks prove replay after unacknowledged close, committed
  restart at the next event, and no automatic creation of an unknown topic.
- `python services/processor/tests/run_kafka_tests.py`: **134 passed, no skips**
  (5.77 seconds), using shared Kafka 3.9.0 with internal networking/no published
  ports. Test-owned topics/groups and disposable containers/network were removed.
  Standard production and dev images build. Packaged native consumer and genuine
  offline CLIP inference pass as UID 10001 without application source mounts.
- Full host suite: **346 passed, 124 skipped** (4.42 seconds). Ruff, formatting,
  offline lockfile validation, and whitespace checks pass. No shared files changed.
  Removed only 16 obsolete task-created processor image tags and their three
  identified old dependency-cache trees; host free space recovered to about 15 GiB
  before rebuilding. No application volumes or unrelated services were modified.
- Adapter does not yet call `JobProcessor` or run during HTTP service lifespan;
  those are subsequent independent checkpoints. No full public API smoke yet.

## Serial Kafka indexing checkpoint

- Added `IngestionWorker` separately from HTTP startup. Parses each keyed event,
  runs the single-job policy, and acknowledges only its durable terminal return.
  One job executes at a time; any unresolved event stops consumption without
  advancing to later events. Readiness clears and the consumer closes on all exits.
  Shutdown before/during poll or retry cannot claim/acknowledge new unfinished work.
- **17 loop unit checks pass**, covering durable ordering, idle/shutdown paths,
  invalid events, uncertain jobs/commits, unknown outcomes, startup/close failures,
  and preservation of the original failure when close also fails.
- Extended the owned Kafka test helper with `--indexing`: disposable PostgreSQL
  17/pgvector 0.8.0, private MinIO, unchanged migration, cached genuine CLIP,
  internal networking, no published ports, and read-only source/checkpoint mounts.
  Five real cases verify successful indexing/duplicate acknowledgment, corrupt
  MP4 terminal failure, and malformed/busy/missing events that stop without
  committing or processing later queued work. Sources/JPEG temp files are cleaned.
- `python services/processor/tests/run_kafka_tests.py --indexing --skip-build`:
  **156 passed, no skips** (12.95 seconds). Production/dev images built beforehand.
  The first run exposed a missing PYTHONPATH in the new schema-wait helper; fixed
  the test-container import path and reran successfully. Test-owned rows/objects/
  topics/groups, containers, and network were removed.
- Host suite: **363 passed, 129 skipped** (4.46 seconds). Ruff, formatting, offline
  lockfile validation, and whitespace checks pass. No shared files or unrelated
  services modified. The HTTP service does not yet launch this worker; service
  lifecycle and the public Go end-to-end path remain separate work.

## Shared HTTP/indexing service lifecycle checkpoint

- Production HTTP startup now creates one dedicated runtime thread: genuine
  model load/warmup, existing schema/private-bucket checks, and the serial Kafka
  loop. HTTP and indexing hold the same model instance. Blocking startup/indexing
  stays off the event loop. No embedding substitutes or separate model process.
- Readiness reports loading/starting/stopping/failure as 503 with bounded reasons.
  A worker failure keeps liveness and healthy text inference available while
  leaving its unresolved Kafka offset pending. Shutdown signals the shared event,
  waits for current initialization/indexing and consumer/storage cleanup, then
  releases model/worker references. No detached initialization thread is abandoned.
- **12 runtime/builder checks pass**: shared model/stop identity, background
  initialization, live/text HTTP during startup, schema/storage/broker failures,
  unexpected worker exit, cleanup, and shutdown waiting for in-flight work.
  Existing embedding-only factory tests explicitly disable ingestion; the
  production app always uses the real worker factory and backend environment.
- Two actual packaged Uvicorn process tests run with PYTHONPATH=/app (no test
  source override), real cached CPU CLIP, Kafka 3.9, PostgreSQL 17/pgvector 0.8.0,
  and private MinIO. One logged model load serves normalized HTTP text and three
  indexed frame vectors/JPEGs; duplicates cause one claim, text-to-pgvector SQL
  passes, and SIGTERM cleans up. An invalid envelope stops ingestion, changes
  readiness to worker_failed, leaves the offset unset, and preserves later work.
- `python services/processor/tests/run_kafka_tests.py --indexing --skip-build`:
  **186 passed, no skips** (19.42 seconds). Standard production/dev images built
  with current runtime source first. Updated HTTP verifier accepts worker-starting
  readiness; it passed against the actual packaged server. All disposable test
  processes, containers/network, rows/objects/topics/groups were cleaned up.
- Host suite: **375 passed, 131 skipped** (4.43 seconds). Ruff, formatting, offline
  lockfile validation, and whitespace checks pass. Documented current startup and
  stopped-processor stale recovery without requiring a frontend Dockerfile.
  No shared files or unrelated services modified. Full shared Compose/public Go
  ingestion/search/playback and semantic evaluation remain unverified.

## Remaining sequence

1. Real backend/processor end-to-end smoke test using the shared infrastructure.
2. Frontend upload, search, and playback, after the backend integration works.

Each feature remains a separate tested commit and is pushed at its checkpoint.
Full end-to-end functionality is not yet implemented.
