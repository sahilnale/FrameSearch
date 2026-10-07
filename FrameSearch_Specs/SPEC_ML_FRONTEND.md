# SPEC_ML_FRONTEND.md — Developer B (ML Processor + UI)

## Codex role
You are Developer B. Implement the **real Python video ingestion/embedding processor and the Next.js frontend** for FrameSearch. Read `SPEC.md` first; its section 13 is the authoritative integration contract. Do not rewrite the Go service, database migrations, or infrastructure. Deliver real code, real ML inference, and an actual browser demo.

## Time and scope
One workday total for two developers. Implement media processing and search UI in the simplest credible way. FrameSearch searches **visual content**, not plot semantics or subtitles. Do not build fake narrative-search capabilities or a separate ML microservice.

## Exclusive ownership
- `services/processor/**`, `apps/web/**`, `scripts/**` for demo fixture generator, end-to-end smoke tooling, and evaluation fixtures. Add tests in these owned directories.
- Developer A owns `services/api/**`, `db/**`, `infra/**`, `.env.example`, root Makefile, root docs. Do not edit these directly; propose contract/schema changes to A.
- Work from the frozen shared contract commit on a separate branch/worktree.

## Required implementation

### 1. Python processor (single deployable service)
- Python 3.11+, FFmpeg/FFprobe, OpenCLIP/PyTorch CPU-safe inference, psycopg or async equivalent, Kafka consumer, boto3/MinIO SDK, small internal FastAPI HTTP endpoint.
- Load the exact shared OpenCLIP `MODEL_NAME` + `MODEL_PRETRAINED` pair once per process; cache checkpoint files on volume. Must return same `model_version` string used in stored frames. Image and text embeddings are L2-normalized 512-length finite floats.
- Run Kafka consumption and embedding HTTP without blocking each other; limit to **one active indexing job per processor**, bounded frame batches, startup health/readiness only after model is ready.
- Internal `POST /embed/text` validates input, embeds real text, returns shared response shape, and must not be exposed as a public Internet endpoint.

### 2. Kafka processing algorithm
1. Receive JSON `media.uploaded` event with agreed fields `event_id`, `event_type`, `schema_version`, `video_id`, `job_id`, `created_at`.
2. Atomically claim its queued job in PostgreSQL. If already ready or claimed, safely skip duplicates. A process crash may leave processing state; A provides manual reconciliation for stale jobs. Never falsely mark readiness.
3. Fetch the actual source from MinIO; FFprobe validates it is a video, checks <=3-minute duration and coherent file metadata. Reject corrupt videos with actionable errors.
4. FFmpeg extracts a frame every 3 seconds, at most 60; store millisecond timestamps accurately. Create deterministic thumbnail object keys; cleanup temporary files even when processing fails.
5. Encode the actual frames using OpenCLIP in small inference batches, CPU default, `torch.inference_mode()`. Normalize vectors and check dimensions.
6. Upload thumbnails to MinIO; upsert frame rows on `(video_id, timestamp_ms, model_version)`. Preserve model version, do not create duplicates on retries.
7. Once all expected frame insertions and thumbnails succeed, mark video `ready` and job `completed`. Otherwise mark failed with reason; never surface partial frames via public search.
8. Commit Kafka offset after durable success/terminal failure. On transient failure retry conservatively; once exhausted, persist failure and commit. Avoid hot loops.

### 3. Next.js UI
- App Router, TypeScript, Tailwind. Clean dark, cinema-inspired visual language; prioritize function over decorative UI.
- Library/upload: select MP4, enforce <=100 MB early, request presigned URL, PUT directly to MinIO, call complete, poll statuses. Show specific failures and retry action.
- Search: query field, submit to Go `POST /api/v1/search`; render **real** matching thumbnails, filenames, timestamps and raw cosine similarity scores. Handle loading, no results, network failure.
- Click a result: fetch playback URL from Go, open HTML video player, seek to `timestamp_ms / 1000` once metadata is available. Provide loading/error handling for seek and signed URL expiration.
- Call API using `NEXT_PUBLIC_API_URL`. Do not call DB, Kafka, or MinIO internal hostname from frontend client code. No invented demo cards or hardcoded search scores.

### 4. Scripts, fixtures, tests
- `scripts/create-demo-clips.sh` or Python equivalent: generate a small legally usable video via FFmpeg testsrc / drawn shapes or other unencumbered generated graphics. Prefer 2–3 visually distinct clips for a tiny sanity check.
- Smoke test: upload through Go API via presigned URL, complete, wait with bounded timeout for Kafka processor to mark ready, run search, verify real nonempty vector results and valid timestamps, retrieve signed thumbnail and playback URL. Do not equate returned results with semantic relevance.
- Basic processor tests: frame timestamp sampling and cap, normalization/dimensions, valid metadata errors, duplicate idempotency where feasible.
- Simple manually labeled visual-search checks for 5 queries across distinct clips; report tiny-sample Recall@5 honestly, optional if the full smoke test passes.
- Run unit tests and UI typecheck/build, and document exact results to A (A owns shared progress doc).

## Day-one implementation sequence
1. Wait for/inspect the agreed shared commit from A (schema, event/HTTP schemas, checkpoint, env names).
2. Implement Python CLIP model load + real `/embed/text` and frame encoding. Verify dimension normalization with a test.
3. Implement FFmpeg frame sampling and Kafka-to-database indexing loop, first processing one frame successfully.
4. Build upload and search pages wired to public Go API, then timestamp playback.
5. Produce demo clips and automated smoke/evaluation tooling; work with A to run the **real end-to-end flow**.
6. Spend remaining time on error handling, regression tests and UI polish. Do not attempt Kubernetes, Redis, subtitles, or advanced dashboards.

## Definition of done
Python consumes a real Kafka job, indexes actual MP4 frames and CLIP vectors into pgvector, returns actual text embeddings; Next.js uploads a real clip and displays search results whose thumbnails and timestamps seek into an actual playback video. Report the precise failures if any component cannot be validated.

## Codex execution instructions
Inspect repository state first, implement code rather than a plan, run tests after each slice, and do not introduce mocks into the final integration. Do not silently change shared contracts. At completion, report branch/commit, tests run, what works, blockers, and exact integration needs for A.
