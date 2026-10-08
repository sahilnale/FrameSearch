# Shared-stack verification — 2026-10-08

The Developer 1 integration checks below passed against the merged processor and
frontend from main `bc09d0d`, with the backend-owned smoke/test wiring in this
branch. No substitute embeddings or processor service were used.

## Environment and isolation

Docker Desktop on Apple Silicon, engine 29.8.2; genuine production Go API, Python
3.12 CPU OpenCLIP processor, Next.js frontend, PostgreSQL 17/pgvector 0.8.0,
Kafka 3.9 and source-built MinIO. Both production application images built.

The real shared `infra/docker-compose.yml` ran as project
`framesearch-integration`, with its own named volumes. An external temporary
Compose override changed only host ports to avoid the existing backend stack:
API 18080, frontend 13000, S3 19000, console 19001, PostgreSQL 15432 and Kafka
19092. Public S3, frontend API and CORS origins matched those ports. Application
internal service addresses, migrations, dependency order and worker remained the
shared configuration. Existing backend-only volumes were not removed.

## Commands and observed results

For an ordinary local stack on the default ports, reproduce the retained smoke
commands with:

```sh
make config
make up
# In a second terminal, after the model loads:
make smoke
# Use only a dedicated test stack with its sole worker:
make smoke-recovery
make test-infra
```

`make smoke` passed using a genuinely generated red 6.2-second H.264 MP4:

- Public upload reservation, private signed PUT and repeated complete succeeded.
- The real Kafka worker indexed the upload and the video became `ready`.
- Real model text inference and pgvector retrieval returned three frames of that
  video. Every returned thumbnail loaded as JPEG.
- Signed playback returned the exact original MP4 bytes; `Range: bytes=0-31`
  returned 206 and the expected bytes. Unsigned source/thumbnail GET returned 403.
- Upload URL and playback response expiration matched 900 seconds.

`make smoke-recovery` passed:

- Equal-sized corrupt MP4 bytes were uploaded and completed through the public
  API. The worker persisted `failed` and exposed a processing error; playback
  remained unavailable.
- The original unexpired signed PUT repaired the source with genuine valid bytes.
  Public retry succeeded, cleared the error, and produced searchable frames and
  correct playback. This tests recovery, not a new source-replacement API.
- A genuine 179.9-second blue MP4 was uploaded and the worker claimed its job.
  The test stopped that active worker with a zero-second grace period, verified
  the job remained `processing`, and aged only that job's timestamp by 16 minutes
  to exercise the default 15-minute threshold without waiting 15 minutes.
- The actual API reconciliation executable requeued that job. After worker
  restart, the same job completed with exactly two claims and 60 unique frames.
  Kafka reached committed offset 5 / log-end offset 5, with zero lag.

The complete Compose stack was then taken down **without deleting volumes** and
recreated with `up -d --no-build`. For this restart the processor was configured
with `HF_HUB_OFFLINE=1`:

- The real model loaded from the retained checkpoint cache without a download.
- All three ready videos, private original sources, thumbnails and playback bytes
  remained intact. Search and signed media checks passed again for every video.
- PostgreSQL retained 66 frames. All had 512 dimensions, unit norm within 0.00001,
  and model version `ViT-B-32:laion2b_s34b_b79k`.
- Kafka retained its committed offset with zero lag; the production frontend
  became healthy and served HTTP 200 after its startup completed.

Other verification:

- Root `make test` executed with container-backed Go/uv executable wrappers:
  Go passed; frozen processor suite **400 passed, 108 skipped**, 10.18 seconds.
  Skips were explicit gates for live services/model checks; the real public
  shared-stack checks above ran separately.
- `make test-infra` passed on the default backend stack and on the live shared
  stack with its custom CORS origin. Test events went to `media.uploaded.api-tests`;
  the live worker stayed ready, with application topic offset 5/5 unchanged.
- Smoke Python lint passed. Compose profiles/configuration validated.
- Frontend audit before this milestone: **28 tests passed**, typecheck and lint
  passed. See `apps/web/VERIFICATION.md` for Developer 2's real browser upload,
  result selection, timestamp playback and responsive checks.

## Limits and remaining review

These generated colors exercise integration and recovery, not semantic relevance.
Developer 2's hand-labeled Commons and shape evaluations are recorded separately
in `services/processor/VERIFICATION.md`; the tiny samples do not demonstrate
general accuracy. The restart check is recorded here; it is not a retained
automated browser test. Cross-browser coverage and performance benchmarking were
not added in this milestone.

Older backend-only Kafka volumes can still contain fixture events published to
the application topic before test-topic isolation. Those refer to removed test
schemas; this change prevents future pollution but does not purge old events or
advance an existing consumer's offsets. Preserve real queued work when resolving
an older local stack; the verified stack used fresh isolated volumes.

The implementation and verification are ready for user review before merging.
The later user-requested visual redesign is a separate frontend change.
