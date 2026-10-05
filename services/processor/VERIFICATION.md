# Processor and AI verification

Branch: `codex/processor-core`. Service lifecycle checkpoint: `0927c2f`.
Full regression checkpoint: `02a95cc`; fixture generator checkpoint: `4f5738f`.
The public Go backend and frontend are excluded from this processor verification.

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

## Remaining scope

- Public Go upload/complete/retry/search/playback and shared Compose smoke are
  deferred at the user's request. Internal tests seed queued jobs rather than
  submitting through the unfinished public backend integration.
- Frontend work remains deferred until backend integration is ready.
- Broader relevance remains unmeasured. The real-footage check above records
  two first-frame misses; no model or ranking changes have been made to hide them.
- Crash recovery relies on stopping the sole processor and using Go's queued/
  stale reconciliation. No claim lease, transactional outbox, DLQ, or exactly-once
  delivery is claimed.
