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
