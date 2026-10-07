# SPEC_BACKEND.md — Developer A (Backend + Infrastructure)

## Codex role
You are Developer A. Implement FrameSearch's **Go API, PostgreSQL schema, Kafka publishing, MinIO integration, and local infrastructure**. Read `SPEC.md` first. The frozen contracts in its section 13 override ambiguity here. Do not implement Python media processing or the Next.js UI. Deliver real working code and tests, not scaffolding.

## Time and scope
One workday total for two developers. Prioritize the exact upload → queue → processor → search → playback path; integration comes before polish. The ML processor is owned by Developer B. Never replace it with a fake implementation in the final run.

## Exclusive ownership
- `services/api/**`, `db/migrations/**`, `infra/**`, `.env.example`, root `Makefile`, root `README.md`, `docs/architecture.md`, `docs/progress.md`.
- You own shared contract updates and final branch integration. Do not edit `services/processor/**`, `apps/web/**`, or B's `scripts/**` without explicit coordination.
- Choose the initial repo structure, create initial shared interfaces, then commit/push these before B works against them.

## Required implementation

### 1. Infrastructure first
- `infra/docker-compose.yml`: Next.js `web`, Go `api`, Python `processor`, PostgreSQL+pgvector, Kafka single-node KRaft, and MinIO. Ensure platform-compatible images, health checks, volumes, service names, dependency ordering, and correct build contexts.
- Expose host ports 3000 web, 8080 API, 9000 MinIO. Set up a private bucket and CORS for browser direct presigned PUTs from the Next.js origin. `S3_ENDPOINT_PUBLIC` must be browser-reachable; signing with only `minio:9000` will break browser upload.
- `.env.example`, Makefile: `up`, `down`, `logs`, `test`, `smoke`, `reconcile`. A may call a B-owned smoke script once available. Do not claim commands work until executed.

### 2. Schema/migrations
- pgvector extension; `videos`, `video_frames`, `processing_jobs` as `SPEC.md` section 4.
- `VECTOR(512)`, cosine operator and appropriate HNSW index if available. Index only frames of ready videos and matching model version in retrieval query.
- Explicit constraints for valid states; prevent duplicate active jobs via a partial unique index and atomic state transitions. Use transactional SQL where state and job row must move together.
- Migrations must run reliably and be documented. No automatic destructive migrations.

### 3. Go public API
Implement all HTTP endpoints in master section 13 and sections 5/8. Use Chi, pgx, AWS SDK v2, a Kafka client, structured logs. Validate file size <=100 MB, MP4 content type, names, query lengths, limit 1–30; never trust file extension as media proof.
- `upload-url`: create `awaiting_upload` video and generate presigned MinIO PUT URL with suitable expiry/content headers.
- `complete`: HEAD object, validate size and expected content type, atomically transition to `queued` and create one job; publish `media.uploaded` with exact envelope. Idempotent if repeated; document DB-to-publish atomicity gap.
- `retry`: only for `failed`, reset state safely and create/requeue work.
- `videos` listing/status: match shared JSON contract, include processing error.
- `playback-url`: signed short-lived GET URL; support native video playback and byte-range requests.
- `search`: call B's internal `/embed/text` with timeout; check 512-dimensional vector and pinned model_version; parameterized pgvector cosine nearest neighbor query, order by distance asc; return signed thumbnail URLs and `1 - distance` similarity. Do not calculate embeddings in Go.
- Health/liveness endpoints must convey real dependencies and readiness.

### 4. Reliability (small but honest)
- Kafka partition key `video_id`. Explicit producer error handling. Repeated complete must not spawn duplicate active jobs.
- `make reconcile` republishes queued jobs and stale processing jobs safely; use deterministic safeguards to avoid duplicate active processing. Single Python consumer instance by default.
- No exactly-once guarantees or fake 'transactional outbox'. Document limitations transparently; lease/fencing/outbox is stretch work only.

### 5. Tests
- Table-driven API validation tests; idempotent `complete` and legal status-transition tests; API schema shape checks.
- At least one real DB/infrastructure test of creation, enqueueing, and cosine vector retrieval, if local Docker is available.
- Run `go test ./...`, migrations, and actual Compose startup; record exact outcomes in progress documentation.

## Coordination checkpoints
1. Before independent work: freeze model checkpoint, env keys, SQL schema, event payload, API JSON and localhost ports in `SPEC.md`; communicate commit hash to B.
2. When B's processor becomes available: verify Go's Kafka payload is consumable and DB row names/types match.
3. When B's frontend is ready: fix CORS/signed-URL mismatches jointly, without silently changing contracts.
4. Merge B's branch, run `make smoke` on actual video, fix failures, note measured tests and limitations.

## Definition of done
A user can create a real upload and complete it; jobs are persisted and Kafka notified; an actual processor indexes; querying uses real OpenCLIP via B and real pgvector; response returns timestamped thumbnails; playback signed URL works. If this is not actually validated end-to-end, report it as partial.

## Codex execution instructions
Inspect repository state first, implement smallest working vertical slices, run tests continually, and maintain `docs/progress.md`. Never output just a plan, mocks, or invented benchmark figures. At completion, list commits, tests run/passed/failed, outstanding blockers, and startup steps.
