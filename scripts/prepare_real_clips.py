"""Download hash-pinned Commons footage, then prepare short credited MP4 excerpts."""

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path
from tempfile import NamedTemporaryFile

SOURCES = Path(__file__).with_name("real-footage-sources.json")
USER_AGENT = "FrameSearch evaluation/0.1 (https://github.com/sahilnale/FrameSearch)"


def verify_source(path: Path, clip: dict) -> None:
    if path.stat().st_size != clip["size_bytes"]:
        raise ValueError(f"Unexpected source size: {clip['id']}")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha1").hexdigest()
    if digest != clip["source_sha1"]:
        raise ValueError(f"Source checksum changed: {clip['id']}")


def download_sources(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(SOURCES.read_text())
    for clip in dataset["clips"]:
        destination = cache / f"{clip['id']}.webm"
        if destination.exists():
            verify_source(destination, clip)
            print(f"Verified cached source: {clip['id']}", flush=True)
            continue
        temporary = None
        request = urllib.request.Request(clip["download_url"], headers={"User-Agent": USER_AGENT})
        try:
            with (
                urllib.request.urlopen(request, timeout=30) as response,
                NamedTemporaryFile(
                    dir=cache, prefix=clip["id"], suffix=".partial", delete=False
                ) as out,
            ):
                temporary = Path(out.name)
                total = 0
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > clip["size_bytes"]:
                        raise ValueError(f"Download exceeds pinned size: {clip['id']}")
                    out.write(chunk)
            verify_source(temporary, clip)
            temporary.rename(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(f"Downloaded and verified: {clip['id']}", flush=True)


def prepare_clips(cache: Path, output: Path) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is required; prepare inside the processor image if absent")
    dataset = json.loads(SOURCES.read_text())
    for clip in dataset["clips"]:
        verify_source(cache / f"{clip['id']}.webm", clip)
    output.mkdir(parents=True, exist_ok=False)
    changes = (
        "Excerpt of original seconds 0–18; audio removed; resized to 640-pixel width; "
        "converted to H.264 MP4 at 10 fps. Derived footage retains the source's license."
    )
    credits = [dataset["dataset"], changes, ""]
    for clip in dataset["clips"]:
        destination = output / clip["filename"]
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-i",
                str(cache / f"{clip['id']}.webm"),
                "-t",
                str(dataset["duration_seconds"]),
                "-an",
                "-vf",
                f"scale=640:-2,fps={dataset['fps']}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            check=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
        with destination.open("rb") as source:
            clip["prepared_sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
        clip["prepared_size_bytes"] = destination.stat().st_size
        credits.extend(
            [
                f"{clip['filename']}: {clip['title']} by {clip['author']}",
                clip["author_url"],
                clip["source_page"],
                f"License: {clip['license']} — {clip['license_url']}",
                changes,
                "",
            ]
        )
        print(f"Prepared real footage: {clip['id']}", flush=True)
    dataset["changes"] = changes
    (output / "sources.json").write_text(json.dumps(dataset, indent=2) + "\n")
    (output / "ATTRIBUTION.txt").write_text("\n".join(credits) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    download = subcommands.add_parser("download", help="fetch and verify pinned original sources")
    download.add_argument("--cache", type=Path, required=True)
    prepare = subcommands.add_parser(
        "prepare", help="create new MP4 excerpts without network access"
    )
    prepare.add_argument("--cache", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "download":
        download_sources(args.cache)
    else:
        prepare_clips(args.cache, args.output)


if __name__ == "__main__":
    main()
