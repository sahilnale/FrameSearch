# Backend test commands

Requires Go 1.24 or newer. From `services/api`:

```sh
go test -count=1 ./...
go vet ./...
go test -race -count=1 ./...
go test -cover ./...
go build .
```

The unit/HTTP tests use test doubles only within `_test.go`. No fake processor,
Kafka broker, object store, or vector search is supplied to the application.
Synthetic vectors in tests verify contract validation and mathematical SQL
behavior; they do not measure OpenCLIP semantic relevance.

## Real database tests

Provision a disposable PostgreSQL instance with pgvector, then run:

```sh
TEST_DATABASE_URL='postgres://framesearch:framesearch@localhost:5432/framesearch?sslmode=disable' go test -run TestDB -v ./...
```

Without TEST_DATABASE_URL, these tests explicitly skip. The test role needs
CREATE SCHEMA and permission to create the vector extension if not installed.
Tests create and remove their own randomly named schemas, not application data.
They verify migration reruns, concurrent enqueueing, failed-video retry, the
partial unique index, ready/model/video search filters, cosine ordering and
similarities, and queued/stale reconciliation without extra jobs.

Full S3/Kafka/processor integration and real video smoke tests remain a separate
milestone. The DB tests use synthetic vectors and a test publisher; they do not
claim real ingestion or Kafka delivery.

## Real storage and Kafka publication

From the repository root, `make test-infra` runs the backend suite in Compose,
including all DB tests and TestInfrastructureUploadAndPublication. Alternatively
set TEST_DATABASE_URL, TEST_S3_ENDPOINT, TEST_KAFKA_BROKERS, and optional
TEST_S3_ACCESS_KEY/TEST_S3_SECRET_KEY (local demo defaults) for local go test.
The test verifies actual CORS, signed PUT, queued job creation, duplicate complete,
Kafka key/envelope, byte-range GET, and anonymous access rejection. The payload
is opaque fixture bytes; this does not prove video decoding or CLIP relevance.
