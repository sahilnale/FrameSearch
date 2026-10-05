# Generated visual-search data

`create_demo_clips.py` generates three original H.264 MP4s: a red circle, a blue
square, and a green triangle on white backgrounds. Each is 640×360, 10 fps, and
6.2 seconds, producing expected processor samples at 0, 3000, and 6000 ms.
The five query labels in `visual-search-labels.json` were written before model
evaluation. No text is drawn into the clips. These are basic synthetic checks,
not a benchmark of accuracy on films or everyday footage.

From the repository root, with Pillow and FFmpeg available:

```sh
uv run --project services/processor python scripts/create_demo_clips.py \
  --output services/processor/.cache/demo-clips
```

The generator refuses to overwrite an existing output directory. If the host has
no FFmpeg, use the built processor image (the output cache directory must exist):

```sh
mkdir -p services/processor/.cache
docker run --rm --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$PWD/scripts,target=/scripts,readonly" \
  --mount "type=bind,source=$PWD/services/processor/.cache,target=/output" \
  framesearch-processor:kafka-checkpoint \
  /app/.venv/bin/python /scripts/create_demo_clips.py --output /output/demo-clips
```

The output contains MP4s, their PNG previews, and a copy of the query labels.
Use these same MP4 files for a later public upload/search/playback smoke test.

Run their real processor-only relevance evaluation from the repo root after
building the processor test image and caching the genuine model:

```sh
python services/processor/tests/run_kafka_tests.py --indexing --semantic --skip-build
```

Recorded observations are in
`services/processor/evaluations/generated-shapes-v1.json`; the full scope is
explained in `services/processor/VERIFICATION.md`.

## Real footage from Wikimedia Commons

`real-footage-sources.json` pins three real recordings and their original SHA-1
checksums: a puppy indoors by Subhashish Panigrahi (CC BY-SA 3.0), a park waterfall
by Editor (CC BY 3.0), and a passenger train by Elliott Brown (CC BY-SA 2.0).
Source pages, author links, and license links are in that manifest. Downloaded
and derived media remain in the ignored cache, outside Git. Each derived clip
retains its source license; preparation writes `ATTRIBUTION.txt` alongside them.

From the repository root:

```sh
python3 scripts/prepare_real_clips.py download \
  --cache services/processor/.cache/real-footage-sources
docker run --rm --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$PWD/scripts,target=/scripts,readonly" \
  --mount "type=bind,source=$PWD/services/processor/.cache,target=/data" \
  framesearch-processor:kafka-checkpoint \
  /app/.venv/bin/python /scripts/prepare_real_clips.py prepare \
  --cache /data/real-footage-sources --output /data/real-footage
```

On a host with FFmpeg, the same `prepare` subcommand works directly with host
paths. Downloads stream within the pinned size limit and verify each original
checksum. Cached originals are verified before reuse. Preparation has no network
access, refuses to overwrite an output directory, and records derived SHA-256
hashes. It extracts original seconds 0–18, removes audio, resizes to 640 pixels
wide, and encodes real H.264 MP4 at 10 fps. All three excerpts validated and
sampled at 0/3000/6000/9000/12000/15000 ms with the packaged processor.

The nine labels in `real-footage-labels.json` were written after inspecting actual
sampled frames and before model inference. They include both an empty railway
platform and the train appearing later in that same clip. The relevant timestamps
let evaluation distinguish finding the right video from finding the right moment.
