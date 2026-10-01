# FrameSearch — One-Day Codex Implementation Specification

## Mission

Build **FrameSearch**, a working, locally runnable media search application in which a user uploads a short video and searches its **visual content** in natural language. Results must show real matching frames, their timestamps, and a video player that seeks to that moment.

**Time budget:** one workday, **two developers using Codex agents**. Favor a small, complete, tested system over a large collection of partial services.

**Nonnegotiable:** real video decoding with FFmpeg, real image/text embeddings from the same pretrained CLIP model, real vector search with pgvector, and an end-to-end demo. Never fabricate search results or performance numbers.

**Scope clarification:** This version searches *what is visible in video frames* (e.g., "a car on a rainy street at night"). It does **not** claim to understand dialogue, plot events, betrayal, or narrative causality. Subtitle/narrative search is a future extension, not part of the one-day acceptance criteria.

## 1. Product MVP

A single-user local web app must support:

1. Upload an MP4 video (max **100 MB**, max **3 minutes**, reject or fail cleanly if invalid).
2. Show upload and indexing status: `awaiting_upload`, `queued`, `processing`, `ready`, `failed`.
3. Automatically extract timestamped still frames at **one frame every 3 seconds**, capped at 60 frames/video.
4. Generate genuine **OpenCLIP ViT-B-32** image embeddings (512 dimensions with a pinned, compatible pretrained checkpoint) for each extracted frame.
5. Search indexed frames with natural-language text using the **same OpenCLIP checkpoint** and pgvector cosine similarity.
6. Display top matching thumbnails, video filename, timestamp, and raw cosine similarity (not a calibrated confidence percentage).
7. Click a search result to play the source video at its timestamp.
8. Show useful error and empty states, and allow a failed video to be retried.

Do **not** build accounts, payments, collaboration, playlists, scene narration, subtitle parsing, text keyword retrieval, LLM agents, Kubernetes, Grafana, Prometheus dashboards, Redis, rate limiting, or multi-tenant support in the MVP. A simple logs/status page is enough.

## 2. Architecture and Technology Choices

Use **one monorepo**, with these services:

- **Web:** Next.js App Router, TypeScript, Tailwind CSS; straightforward dark UI.
- **Public API:** Go with Chi (or Gin), pgx, AWS SDK v2, Kafka client.
- **Python processor:** one Python service responsible for both (a) consuming Kafka indexing jobs and (b) serving an **internal** `POST /embed/text` endpoint. Load the OpenCLIP model once per Python process and share it between processing and text inference; keep the HTTP server responsive while the worker performs blocking CPU processing (e.g. a dedicated worker thread/task with bounded concurrency). It also uses FFmpeg/FFprobe, PostgreSQL, and MinIO.
- **PostgreSQL + pgvector:** metadata and vectors.
- **MinIO:** private video files and generated thumbnails.
- **Kafka (single broker in KRaft mode):** durable upload-event delivery and decoupled indexing. No Redis, no second job queue.
- **Docker Compose:** local orchestration. CPU-only operation is mandatory; GPU is optional, never required.

**Flows**

- Upload: Browser → Go API (`upload-url`) → browser uploads to MinIO using a presigned URL → browser calls `complete` → Go records queued job and sends Kafka `media.uploaded` event → Python consumes and indexes → PostgreSQL marks ready.
- Search: Browser → Go API → Python `/embed/text` → pgvector similarity query in Go → signed thumbnail/playback URLs → Browser.

### Important implementation constraints

- Configure MinIO's presigned URLs so they resolve **in the host browser**, not only inside Docker (e.g. use a browser-accessible MinIO endpoint for signing). Configure bucket CORS for local frontend origin.
- Python/OpenCLIP may take time to download the first model checkpoint; document model download and persist the cache in a volume.
- Use an exact pinned model/checkpoint identifier shared by indexing and querying, and store `model_version` with every frame. Never silently mix model versions.
- Keep the storage bucket private. Serve video and thumbnail objects through short-lived presigned GET URLs. Document that local demo has **no authentication** and must not be deployed publicly.
- Allow CPU inference with small batches; no GPU-only dependencies.

## 3. Repository Layout

```text
framesearch/
  apps/web/                 # Next.js web UI
  services/api/             # Go public API
  services/processor/       # Python Kafka worker + internal embed API
  infra/docker-compose.yml
  db/migrations/
  scripts/                  # demo clip generator, smoke test, optional bench
  tests/                    # integration tests where appropriate
  docs/architecture.md
  docs/progress.md
  .env.example
  Makefile
  README.md
```

Simple layout beats artificially granular packages. Use reproducible Go and Python dependency versions and a JS lockfile.

## 4. Database and State Model

Create migrations with pgvector extension and at minimum:

**videos**
- `id UUID PRIMARY KEY`
- `filename TEXT NOT NULL`
- `object_key TEXT NOT NULL UNIQUE`
- `content_type TEXT NOT NULL`
- `size_bytes BIGINT NOT NULL`
- `duration_seconds DOUBLE PRECISION NULL`
- `status TEXT NOT NULL CHECK (status IN ('awaiting_upload','queued','processing','ready','failed'))`
- `processing_error TEXT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

**video_frames**
- `id UUID PRIMARY KEY`
- `video_id UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE`
- `timestamp_ms INTEGER NOT NULL`
- `thumbnail_key TEXT NOT NULL`
- `embedding VECTOR(512) NOT NULL`
- `model_version TEXT NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `UNIQUE(video_id, timestamp_ms, model_version)`

**processing_jobs**
- `id UUID PRIMARY KEY`
- `video_id UUID NOT NULL REFERENCES videos(id)`
- `status TEXT NOT NULL` (queued / processing / completed / failed)
- `attempt_count INTEGER NOT NULL DEFAULT 0`
- `last_error TEXT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

Create HNSW index for cosine distance if supported by local pgvector version; also provide an exact-distance query path or explain why indexing is not beneficial with tiny datasets. Search only frames from `ready` videos, matching the active `model_version`.

### Idempotency, retries, and truthfulness

- The `complete` endpoint must be idempotent: repeated calls must not create duplicate active jobs.
- Kafka consumer commits offsets **only after a durable successful or terminal-failure state transition**. If processing fails transiently, retry with a small bounded exponential backoff; once exhausted, record `failed` and commit. Expose manual retry via API.
- On retry, upsert frame records by `(video_id, timestamp_ms, model_version)`; never return partially indexed videos as ready.
- If the process crashes mid-index, stale `processing` jobs can get stuck. Implement a **minimal startup reconciliation pass** that requeues stale processing jobs (or a deterministic documented recovery command). Ensure retries are safe for duplicate events.
- **Known limitation permitted for day one:** database update and Kafka publish are not atomic. Explicitly document the DB-to-Kafka publish gap and offer a `make reconcile` command that republishes `queued` jobs. Do not claim exactly-once delivery or production-grade reliability. A transactional outbox is a future improvement.
- Avoid two workers processing the same job concurrently: use atomic database claim (`UPDATE ... WHERE status = 'queued' RETURNING ...`) and a single worker replica by default. A time-bounded claim/lease is a stretch improvement, not a core requirement.

## 5. Public API (Go)

Implement these endpoints with consistent JSON errors and validation:

```text
POST /api/v1/videos/upload-url
POST /api/v1/videos/{id}/complete
GET  /api/v1/videos
GET  /api/v1/videos/{id}
GET  /api/v1/videos/{id}/playback-url
POST /api/v1/videos/{id}/retry
POST /api/v1/search
GET  /health/live
GET  /health/ready
```

`POST /api/v1/videos/upload-url` request:

```json
{"filename":"clip.mp4","content_type":"video/mp4","size_bytes":1234567}
```

Response:

```json
{"video_id":"<uuid>","upload_url":"<presigned PUT URL>","object_key":"videos/<uuid>/original.mp4"}
```

`complete` checks object exists, compares object size to declared size, checks expected upload content type, and enqueues processing. FFprobe in worker is the authority on the actual video format/duration; do not trust the file extension alone. A bad video becomes `failed` with an actionable error.

Search request:

```json
{"query":"a person walking through a city at night","limit":12,"video_id":null}
```

Search response:

```json
{
  "query":"a person walking through a city at night",
  "results":[
    {"video_id":"<uuid>","frame_id":"<uuid>","filename":"clip.mp4","timestamp_ms":12000,"thumbnail_url":"<signed URL>","similarity":0.32}
  ]
}
```

Numbers and identifiers above are **format examples only**, not expected test results.

- Validate `limit` within 1–30 and cap query length.
- Use cosine distance (`<=>`), return similarity as `1 - cosine_distance`, and sort highest to lowest.
- Use parameterized SQL, request deadlines, and proper HTTP errors.
- Seek via native HTML video `currentTime` when metadata is loaded; handle CORS and byte-range playback correctly.

## 6. Python Processor

The Python service must:

1. Load one chosen OpenCLIP `ViT-B-32` model/checkpoint at startup; publish readiness only when it loads.
2. Serve internal `POST /embed/text` returning a normalized 512-dimensional embedding and `model_version`. Validate query length.
3. Consume JSON Kafka events from topic `media.uploaded` keyed by `video_id`.
4. Atomically claim a queued processing job in PostgreSQL.
5. Download the uploaded video from MinIO; use FFprobe to reject invalid, oversize-duration, or non-video content.
6. Use FFmpeg to sample up to 60 frames at 3-second intervals, producing timestamped thumbnails.
7. Run real batched image encoding using the identical checkpoint as text encoding; L2-normalize all vectors.
8. Upload thumbnails to MinIO and upsert `(video_id, timestamp_ms, model_version, embedding)` in PostgreSQL.
9. Mark `ready` only after every expected frame succeeds; otherwise record `failed` with reason.
10. Release temp files on every path, expose meaningful logs, and recover safely from duplicated messages.

Keep model initialization and inference out of Go. Support CPU by default. Use conservative resource settings to prevent memory exhaustion.

## 7. Web UI

Use a clean dark-themed layout. Only three views are needed:

- **Library/upload:** upload video, show list and processing status, retry failed item.
- **Search (primary):** search box, actual thumbnail results, timestamps, raw similarity scores, and video-name labels.
- **Playback overlay:** click a result and immediately seek to matching timestamp.

Use polling at a modest interval for processing status. Implement loading, zero-result, and error states. No mock cards or fabricated counters. Avoid spending time on elaborate animations or generic dashboard charts.

## 8. Tests and Acceptance Criteria

### Tests that must run

- Go validation tests for upload requests and search limits.
- Go tests for legal status transitions and repeat `complete` behavior.
- Python tests for timestamps/sampling, vector dimensionality, L2 normalization, and idempotent frame upserts.
- **Real integration smoke test** using a tiny test video (generate one with FFmpeg if no clip supplied): upload via public API, complete, observe Kafka consumption, wait for indexed/ready status, issue a text query, verify returned frames belong to that video, verify timestamps and signed playback URL. A successful response alone does **not** prove semantic relevance.
- Confirm a second `complete` call doesn't produce extra active jobs.
- Confirm retry from a failed processing state is possible.

### Semantic accuracy check (small but real)

Create at least **5 human-readable queries** and a tiny set of **2–3 visually distinct, legally usable test clips**, with hand-labeled expected matching clips. Report Recall@5 (or accuracy at the video level) without claiming meaningful statistical generalization from this tiny dataset.

### Completion means all of the following actually work

- `docker compose up --build` starts all required containers.
- User can upload an actual MP4 and see statuses change.
- MinIO contains the video and indexed thumbnails.
- Kafka message is consumed by Python processor.
- FFmpeg extracts frames; OpenCLIP produces genuine vectors.
- pgvector returns timestamped natural-language matches.
- Clicking a match seeks video playback to the right moment.
- A smoke test validates the full path.
- README includes exact commands, limitations, and first-run model download note.

## 9. One-Day Execution Plan / Agent Ownership

**Developer A (Go + infrastructure)** owns:
- `services/api/**`, `db/migrations/**`, `infra/**`, root `Makefile`, `.env.example`
- HTTP contracts, object storage upload/serving, PostgreSQL search, Kafka publishing, API tests.

**Developer B (Python + web)** owns:
- `services/processor/**`, `apps/web/**`, `scripts/demo*`, search evaluation fixture and tests.
- Kafka consumption, FFmpeg, embeddings, internal text embedding API, frontend experience.

Agree **first** on: environment variable names, API request/response schemas, Kafka envelope, database schema, model version, and browser-facing MinIO URL. Put these contracts in `docs/architecture.md` before concurrent coding. Neither agent edits the other agent's directories without coordinating. Developer A integrates both branches and runs the end-to-end smoke test; Developer B helps resolve interface issues.

Execution order:

1. Get Compose services + DB migration healthy.
2. Make a small clip upload and MinIO storage work.
3. Process a Kafka event into one extracted, embedded frame.
4. Complete batch indexing and pgvector retrieval.
5. Make the browser search/playback loop work.
6. Add idempotency/retry tests, smoke test, docs, and polish.

**Stop feature expansion as soon as the critical path works; invest remaining time in fixing bugs and demonstrating the system.**

## 10. Run Commands

Provide a root `Makefile` with:

```bash
make up           # Docker Compose up --build
make down         # stop containers
make logs         # tail all service logs
make test         # unit tests
make smoke        # end-to-end ingestion + search
make reconcile    # republish DB queued/stale jobs; safely idempotent
```

Use `infra/docker-compose.yml` consistently for `make` targets. Provide `.env.example`, Dockerfiles, dependency lockfiles, and complete startup instructions. Document available RAM/storage requirements if discovered empirically.

## 11. Stretch Goals — only after passing acceptance criteria

Ordered by value:

1. **Subtitle search** for videos with subtitle tracks: parse timed captions, add PostgreSQL full-text retrieval, and fuse with CLIP results. Label it clearly as dialogue search, not plot understanding.
2. Scene-boundary-aware sampling or neighboring-frame grouping.
3. Small performance comparison: exact pgvector vs HNSW and p50/p95 search latency, with actual measured hardware/dataset details.
4. Prometheus metrics and a modest status page.
5. Transactional outbox and lease-based recovery.

Do not implement Redis, Kubernetes, a separate ML microservice, or Grafana in the one-day version unless the core application is working and time remains.

## 12. Agent Operating Rules

- **Implement actual files and working code**, not a design-only answer.
- Inspect the repository first. If empty, initialize it.
- Keep a task checklist in `docs/progress.md`: planned, implemented, verified, blocked.
- Compile/build and run focused tests after each vertical slice.
- Use pinning/lockfiles and keep imports/build contexts correct.
- Never silently replace Kafka, FFmpeg, OpenCLIP, pgvector, or MinIO with mocks.
- Do not claim a test, metric, or feature passed unless you ran it successfully.
- Make reasonable decisions autonomously. If blocked, clearly document the blocker and continue with independent work.
- Final report: working features, test commands/results, incomplete features, known limitations, exact commands to start and demo.

**Primary success condition:** A user uploads a real video and can find a visually matching moment from a natural-language query, then play it at the returned timestamp.


## 13. Frozen Integration Contracts (authoritative for both agents)

Before coding, both developers read this section. Changes require agreement and a single coordinated change to this master spec. If an earlier section is less precise, this section wins.

### HTTP contract
- Public API base: `http://localhost:8080`; frontend: `http://localhost:3000`; MinIO browser endpoint: `http://localhost:9000`; processor internal HTTP: `http://processor:8000`.
- `POST /api/v1/videos/upload-url` JSON `{ "filename": string, "content_type": "video/mp4", "size_bytes": integer }` → `201` with `{ "video_id": UUID, "upload_url": string, "object_key": string }`.
- `POST /api/v1/videos/{id}/complete` empty body → `200` with `{ "video_id": UUID, "status": "queued" | "processing" | "ready" }`. Repeated requests return current state without creating another active job. Server validates object existence and expected size.
- `GET /api/v1/videos` → `200` `{ "videos": Video[] }`; `GET /api/v1/videos/{id}` → `200` `Video`; `Video = { id, filename, status, duration_seconds: number | null, processing_error: string | null, created_at }`.
- `POST /api/v1/videos/{id}/retry` → `200` `{ "video_id": UUID, "status": "queued" }`; valid only when failed, else return a consistent 409 JSON error.
- `GET /api/v1/videos/{id}/playback-url` → `200` `{ "url": string, "expires_in_seconds": 900 }`, valid only when ready.
- `POST /api/v1/search` request `{ "query": string, "limit"?: integer, "video_id"?: UUID | null }` → `200` `{ "query": string, "results": SearchResult[] }` where `SearchResult = { video_id: UUID, frame_id: UUID, filename: string, timestamp_ms: integer, thumbnail_url: string, similarity: number }`.
- All API failures: `{ "error": { "code": string, "message": string } }` and appropriate HTTP code.
- Processor internal `POST /embed/text` request `{ "text": string }` → `{ "embedding": number[512], "model_version": string }`. `GET /health/ready` only succeeds after model loads.

### Event contract
Kafka topic `media.uploaded`, partition key `video_id`, UTF-8 JSON:
```json
{"event_id":"uuid","event_type":"media.uploaded","schema_version":1,"video_id":"uuid","job_id":"uuid","created_at":"2026-01-01T00:00:00Z"}
```
Only this topic is mandatory. No `media.processed` or DLQ requirement for day one. Go writes job/video state before publishing. The Python processor is idempotent for duplicate messages. If DB commit succeeds but publish fails, the manual `make reconcile` path re-emits queued work (known limitation, not exactly-once semantics).

### Database and model contract
- Database schema from section 4 is canonical. Add a uniqueness safeguard for only one active processing job per video, using a partial index or equivalent atomic transaction design.
- Processor is the only writer of frame rows and processing transitions from `queued` to `processing` to `ready` or `failed`. Go creates `queued` jobs and performs manual retry transitions.
- `model_version` must be a single fixed string, chosen once and used by both services. Suggested: `ViT-B-32:laion2b_s34b_b79k` using the corresponding OpenCLIP pretrained checkpoint; verify that identifier against installed OpenCLIP before pinning. This checkpoint creates 512-dimensional vectors. If unavailable, choose one verified checkpoint and update the master contract before implementation.
- Coordinate environment variables using `.env.example`. Required variable names: `DATABASE_URL`, `KAFKA_BROKERS`, `KAFKA_TOPIC`, `S3_ENDPOINT_INTERNAL`, `S3_ENDPOINT_PUBLIC`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `PROCESSOR_URL`, `NEXT_PUBLIC_API_URL`, `MODEL_NAME`, `MODEL_PRETRAINED`.
- Database is initialized by A's migrations; B must not write new migrations directly. Changes are proposed to A and integrated deliberately.

### Development rules
- Both agents start from the same repository commit, in separate branches/worktrees. Each follows this master spec plus their own role file.
- Developer A owns shared `infra/`, `db/`, `.env.example`, root `Makefile`, `docs/architecture.md`, `docs/progress.md`, and the integration/merge. Developer B owns `services/processor/`, `apps/web/`, and `scripts/` media fixture utilities. Both can create tests under their owned service directory. B opens an integration request rather than changing A-owned files.
- Developer A creates a minimal initial working repository and publishes the shared contract commit before either agent starts independent implementation. No agent rewrites another agent's implementation.
- If work is still incomplete at the end of the day, report what actually runs rather than fabricate completeness. Local use only: no auth, no public production deployment.
