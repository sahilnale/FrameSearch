# FrameSearch

FrameSearch searches visible video frames with natural-language queries. Developer
1's Go backend, database schema, and local infrastructure are implemented.
Developer 2's Python processor, Next.js UI, media fixtures, and smoke tooling are
not in this branch yet. Real video indexing, CLIP inference, semantic search,
and browser playback are not yet validated end to end.

## Start the backend

Requires Docker Desktop with its engine running and Docker Compose. Published
ports bind to localhost; this app has no authentication and is for local use.

```sh
cp .env.example .env
make config
make backend
curl http://localhost:8080/health/live
```

If `docker` is missing from your shell PATH on macOS, use
`make DOCKER=/Applications/Docker.app/Contents/Resources/bin/docker backend`.
`.env` is ignored by Git. Default credentials are local demo placeholders. When
changing PostgreSQL credentials, keep user/password URL-safe because Compose
constructs its internal database connection string from those values.

Services: API `localhost:8080`, PostgreSQL `localhost:5432`, Kafka
`localhost:9092`, MinIO S3 `localhost:9000`, MinIO console `localhost:9001`.
Internal connections use Compose service names. Browser uploads and downloads
use `S3_ENDPOINT_PUBLIC`, which defaults to `http://localhost:9000`.

The bucket remains private. Upload, thumbnail, and playback URLs expire after
900 seconds. Browser direct uploads must send `Content-Type: video/mp4`.
MinIO permits CORS from `WEB_ORIGIN=http://localhost:3000` and supports byte-range
GET requests. Go verifies uploaded size/type before queueing; Python must still
validate actual format and the three-minute duration limit with FFprobe.

API readiness intentionally returns 503 while the processor is absent or its
model is loading. Liveness and the upload/queue endpoints can still operate in
backend-only mode. Search requires the actual processor; no substitute service
or embeddings are provided.

## Test

With Go 1.24+ installed:

```sh
make test
go -C services/api test -race ./...
```

For real database, MinIO, and Kafka checks using a Go test container:

```sh
make test-infra
```

This verifies migrations, concurrent enqueue/retry, pgvector cosine retrieval
with synthetic vectors, browser CORS, real signed object PUT/GET, private access,
byte ranges, repeated completion, and actual Kafka event publication. The object
payload in the infrastructure test is opaque fixture data; the test does not
claim video decoding, worker consumption, or semantic relevance. Test schemas,
buckets and objects are cleaned up. Test events remain in Kafka and contain job
IDs that will not exist in the application's public schema.

Without the TEST_DATABASE_URL and other TEST_* variables, the corresponding
integration tests explicitly skip in local `go test`. See
`services/api/TESTING.md` and `docs/progress.md` for exact scope and results.

## Start the complete application after Developer 2 integrates

Developer 2 must supply Dockerfiles with build contexts `services/processor` and
`apps/web`, plus `scripts/smoke.py` for the root smoke target.

```sh
make up
# In another terminal, after the processor is ready:
make smoke
```

The web service uses port 3000 and the public API base
`NEXT_PUBLIC_API_URL=http://localhost:8080`. The processor listens internally on
port 8000 and is not published to the host. It must implement `/health/ready` and
`/embed/text`, consume the frozen Kafka envelope, and write the shared schema.

Use `MODEL_NAME=ViT-B-32`, `MODEL_PRETRAINED=laion2b_s34b_b79k`, and stored
model_version `ViT-B-32:laion2b_s34b_b79k`. CPU operation is required. First startup
will download the real OpenCLIP checkpoint; its cache persists in the
`model-cache` volume. Full-stack RAM/disk requirements have not been measured.

MinIO public images and binary archives were unavailable during implementation.
The Compose build compiles genuine MinIO and mc from pinned upstream source
releases. First builds download Go modules and can take several minutes.

## Recovery and shutdown

```sh
make reconcile        # Republish queued jobs; worker may remain running.
make reconcile-stale  # Stop sole worker, reset stale jobs, republish, restart.
make migrate          # Rerun initial non-destructive migration.
make logs
make down             # Preserve named volumes.
```

Database commit and Kafka publication are not atomic. A publication failure
leaves the job queued and returns `queued_publish_failed`; use `make reconcile`.
Repeated complete does not republish. Delivery is at least once; the processor
must atomically claim jobs and handle duplicates. There is no transactional
outbox, lease, or exactly-once guarantee.

Stale recovery uses a 15-minute threshold by default. Stop every processor,
including any outside Compose, before reclaiming jobs. The recovery target
restarts the worker only if reconciliation succeeds. It does not delete frame
rows; frame upserts make reprocessing safe.

The initial migration is rerunnable; future migrations must remain safe for the
migration runner or introduce an explicit versioned runner. Automatic destructive
migrations, orphan-upload cleanup, authentication, and public deployment are out
of this MVP's scope.

Read `docs/database-and-upload-plan.md` for the schema and upload sequence and
`docs/architecture.md` for frozen integration contracts.
