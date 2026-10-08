# Processor and AI verification

Branch: `codex/processor-core`. Service lifecycle checkpoint: `0927c2f`.
Full regression checkpoint: `02a95cc`; fixture generator checkpoint: `4f5738f`.
The original regression excludes the Go backend. A separate actual public API
queue smoke is recorded below. Frontend/browser happy-path verification is
recorded separately in `../../apps/web/VERIFICATION.md`.

## Executed complete suite

```sh
python services/processor/tests/run_kafka_tests.py --indexing --full --skip-build
```

**506 passed, zero skipped, 47.94 seconds** at the full regression checkpoint.
This includes unit checks and every
opt-in real integration; it does not mean 506 end-to-end jobs. Production/dev
images were built from the standard owned Dockerfiles before the run.

Environment: Docker Desktop Linux ARM64, Python 3.12, CPU OpenCLIP 3.3.0/PyTorch
2.10.0, genuine cached `ViT-B-32:laion2b_s34b_b79k`, actual FFmpeg/FFprobe, Kafka
3.9.0, PostgreSQL 17/pgvector 0.8.0, and private MinIO built from its pinned source.
Checkpoint downloads were disabled. Tests used disposable containers/internal
networking, the unchanged shared migration, and read-only source/weight mounts.
Packaged Uvicorn checks import `/app`, run as UID 10001, and use actual HTTP sockets.

| Area | Verified behavior |
| --- | --- |
| Model | Real CPU image/text inference; fixed checkpoint, 512 finite L2-normalized values; one model shared by HTTP/indexing |
| Media validation | Actual MP4 authority, declared size checks, 100 MiB/180-second limits, corrupt/disguised/audio-only rejection |
| Sampling | Three-second grid, cap of 60, genuine source timestamps including VFR/nonzero offsets, aspect ratio and temporary-file cleanup |
| Storage | Private real source/JPEG transfers, deterministic thumbnail keys/metadata, signed reads, retry without duplicate objects |
| Database | Atomic video-first claims, idempotent frame upserts, complete expected-frame validation, ready/completed and failed/failed transactions, rollback and stale-claim protection |
| Job policy | Bounded retries/backoff, immediate corrupt-media failure, safe public error reasons, duplicate terminal jobs, shutdown/uncertain DB outcomes left pending |
| Kafka | Frozen keyed envelopes, actual consumption, synchronous confirmed next-offset commits, unacknowledged replay, restart at committed offset, no automatic topic creation |
| Ingestion | Real Kafka event → source download → decoding → CLIP → MinIO JPEGs → pgvector → terminal state → offset commit |
| Failure ordering | Malformed/busy/missing jobs stop the worker without committing or processing later queued events; offset-commit failures cannot advance consumption |
| HTTP/lifecycle | Real text endpoint and readiness, responsive HTTP during blocking work, initialization/failure readiness, one process/worker/model, storage/consumer cleanup and SIGTERM shutdown |
| Retrieval mechanics | Actual HTTP text vector queried against ready-only same-model pgvector rows; genuine finite cosine scores and expected frame timestamps |

All test-owned rows, objects/private buckets, topics/groups, processes,
containers, and network were cleaned up. No Developer 1 files or unrelated
project services were modified.

## Labeled visual-search evaluation

```sh
python services/processor/tests/run_kafka_tests.py --indexing --semantic --skip-build
```

**One live evaluation test passed in 10.66 seconds, zero skips.** Three original
generated clips were indexed through the actual packaged service: Kafka event,
private MinIO download, FFmpeg, real CPU CLIP, thumbnail transfers, pgvector,
ready/completed state, then offset commit. Every clip has three sampled frames
at 0, 3000, and 6000 ms. The HTTP text endpoint produced the real query vectors;
exact SQL ranked the nine same-model frames from these three ready videos.
This checks internal processor/retrieval behavior, not the public Go search API.

The five labels were committed before running inference. None were changed
after seeing the results. Raw observations are saved in
`evaluations/generated-shapes-v1.json`.

| Query | Expected / first result | First cosine score |
| --- | --- | --- |
| a red circle on a white background | red-circle | 0.3403 |
| a round red shape | red-circle | 0.3786 |
| a blue square on a white background | blue-square | 0.3238 |
| a green triangle on a white background | green-triangle | 0.3723 |
| a square blue shape | blue-square | 0.3558 |

Top-1 video accuracy: **5/5 (100%)**. Recall@5: **5/5 (100%)**, defined as the
expected clip appearing among the top five frame results. Each query has one
expected clip. Scores are raw cosine similarities, not confidence probabilities.
These simple synthetic shapes establish a basic sanity check, with no statistical
claim about accuracy on real footage, complex scenes, or matching moments within
a changing video. The evaluation records misses without treating a perfect score
as a test requirement. The full runner now also includes this additional test.

The source generator and labels are under `scripts/`; local generated MP4s and
PNG previews are under `services/processor/.cache/demo-clips`. All live evaluation
rows, objects/bucket, topic/group, process, containers, and network were removed.
Host checks after adding the evaluation: **375 passed, 132 deliberately disabled
live checks skipped** (6.60 seconds). Ruff and formatting pass for processor,
tests, and the fixture script.

## Real-footage relevance evaluation

```sh
python services/processor/tests/run_kafka_tests.py --indexing --semantic --real-footage --skip-build
```

**One live evaluation test passed in 7.88 seconds, zero skips.** Downloaded
Wikimedia Commons recordings: [Puppy playing](https://commons.wikimedia.org/wiki/File:Puppy_playing.webm)
by Subhashish Panigrahi (CC BY-SA 3.0),
[Small waterfall](https://commons.wikimedia.org/wiki/File:Small_waterfall.webm)
by Editor (CC BY 3.0), and
[Cross Country train](https://commons.wikimedia.org/wiki/File:Cross_Country_Train_220005_at_Burton-on-Trent_station_-_HD_video_clip.webm)
by Elliott Brown (CC BY-SA 2.0). Source hashes, attribution, licenses, and bounded
downloads are recorded in `scripts/real-footage-sources.json`. Original files
total about 43 MB; prepared excerpts total about 2.65 MB. Derived media retain
their source licenses and have an adjacent `ATTRIBUTION.txt`.

Each excerpt contains original seconds 0–18, resized/re-encoded as real H.264
MP4 with audio removed. The actual processor sampled six frames per clip at
0/3000/6000/9000/12000/15000 ms: **18 frames total**. Includes ordinary camera
movement, clutter, blur, and a train arriving after an initially empty platform.
All sampled frames were visually inspected. Nine query labels and relevant
timestamps were committed at `8997c00` before CLIP inference. Labels, queries,
and the frozen checkpoint were not changed after seeing results.

The same actual packaged Kafka → MinIO → FFmpeg → CLIP → pgvector → terminal DB
state → committed-offset path ran successfully, with real HTTP text vectors and
ready-only/same-model exact ranking. Prepared file SHA-256 hashes were checked
before upload. Full raw observations are in `evaluations/commons-real-v1.json`.

| Query | First clip / timestamp | First frame relevant? |
| --- | --- | --- |
| a puppy playing indoors | puppy-indoors / 0 s | Yes |
| a dog on a tiled floor | puppy-indoors / 12 s | Yes |
| a small brown dog in a room with green walls | puppy-indoors / 0 s | Yes |
| a waterfall flowing over rocks | park-waterfall / 12 s | Yes |
| a garden with trees and cascading water | park-waterfall / 6 s | Yes |
| a small stream running between rocks and plants | park-waterfall / 0 s | Yes |
| a passenger train next to a station platform | passenger-train / 9 s | Yes |
| a train with a yellow front | passenger-train / 6 s | **No**; expected 9 or 12 s |
| an empty railway platform with no train | passenger-train / 9 s | **No**; expected 0, 3, or 6 s |

- Top-1 video accuracy: **9/9 (100%)**.
- Top-1 relevant-frame accuracy: **7/9 (77.8%)**.
- A relevant labeled frame appeared in the top five for **9/9 queries**.
- Mean frame Recall@5: **85.2%** (fraction of each query's labeled relevant
  frames in its top five, averaged across queries).
- Mean frame Precision@5: **82.2%** (fraction of each query's top five that are
  labeled relevant, averaged across queries).

The two first-frame misses are recorded limitations in this sample, not corrected
by rewriting queries or labels. The evaluation's pytest pass confirms the real
pipeline and metric recording; it does not assert perfect semantic accuracy.
Three visually distinct clips and nine queries are a small sanity check, not a
representative benchmark. Scores remain raw cosine similarities, not probabilities.

Generated-shape compatibility through the extended evaluator: **one live check
passed, zero skips, 7.40 seconds**; all five queries still match their expected
clip/frame first. Host regression: **375 passed, 132 opt-in live checks skipped**
(4.38 seconds). Ruff, formatting, offline frozen lock check, and whitespace checks
pass. All test-owned rows, private buckets/objects, topic/group, processes,
containers, and networks were removed. No Developer 1 files changed.

## Actual Go API and queue smoke

```sh
python services/processor/tests/run_kafka_tests.py --indexing --api --skip-build
```

**One live API integration test passed in 9.16 seconds, zero skips.** The Go API
was built from its unchanged canonical `services/api/Dockerfile` at main commit
`66faaf4`. The owned test image copies that actual executable into the processor
test runtime. Both real service processes run as UID 10001 with real HTTP sockets,
Kafka 3.9, private MinIO, PostgreSQL 17/pgvector 0.8.0, FFmpeg, and cached CPU CLIP.
No queued jobs or frames are seeded: the public API creates and publishes them.

1. Start Go while the processor is stopped. API readiness correctly returns 503.
2. Request upload URLs, PUT all three hash-verified real clips to private MinIO,
   then call `complete` twice per clip. Exactly three real keyed Kafka events,
   three durable queued jobs, zero claim attempts, and unset processor offsets
   are inspected. Repeated completion creates no extra job/event. Playback is
   rejected with 409 before videos are ready.
3. Start the actual packaged processor. All three queued jobs finish with one
   claim each, videos become ready, jobs become completed, and committed offset
   reaches 3. All 18 real frame vectors have 512 finite normalized values and
   timestamps 0/3000/6000/9000/12000/15000 ms. Go readiness becomes 200.
4. Public Go search calls the real text endpoint and returns genuine results;
   the puppy query ranks the puppy video first. Per-video filtering returns six
   frames with correct filenames/timestamps. Signed JPEG requests return actual
   images, signed MP4 GET returns the exact uploaded bytes, and byte-range GET
   returns 206 with correct content/range. Expiry is 900 seconds; unsigned reads
   return 403. The processor logs one real model load and cleans temporary files.

Raw smoke summary is in `evaluations/api-queue-smoke.json`. The first run reached
queue creation but its offset inspection raced initial Kafka coordinator loading.
A bounded read-only wait for the three coordinator startup errors fixed the test;
no application code or Kafka acknowledgment behavior changed. Host regression:
**375 passed, 133 explicitly opt-in checks skipped** (4.02 seconds). Ruff,
formatting, offline lock validation, and whitespace checks pass. The actual API
case ran separately with every needed dependency enabled and no skipped checks.
All test-owned rows, private bucket/objects, topic/group, service processes,
containers, and network were removed. No unrelated project services started.

This proves the actual Go API → Kafka → processor → real vectors → public search
and signed reads in an isolated network. Public signing addresses point at MinIO
inside that test network, where the client runs. Shared Compose default localhost
addresses/CORS, a browser player, and deployment are separate checks. This smoke
does not newly measure semantic accuracy; the nine-query results above remain.

Without `--skip-build`, the helper builds all required canonical/service test
images. Prepared real footage and cached weights are prerequisites. To include
the new public API case in a future complete suite, use `--indexing --full --api`;
without `--api` that opt-in case is deliberately skipped.

## Remaining scope

- Full shared Compose startup with persistent volumes/model cache remains
  unverified. Separate actual public API and browser happy paths pass.
- Actual public corrupt-upload → failed → retry → ready and stale-crash recovery
  remain additional integration checks; their underlying policies/DB behavior
  have focused coverage, but this happy-path smoke does not claim those flows.
- Frontend upload/search/playback now works in a genuine live demo, including
  CORS and native timestamp seeking. The web report records 12 UI tests, built
  production container, three browser uploads, 18 frames and committed offset 3.
- Broader relevance remains unmeasured. The real-footage check above records
  two first-frame misses; no model or ranking changes have been made to hide them.
- Crash recovery relies on stopping the sole processor and using Go's queued/
  stale reconciliation. No claim lease, transactional outbox, DLQ, or exactly-once
  delivery is claimed.
