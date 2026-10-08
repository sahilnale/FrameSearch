"""Generate original labeled MP4 fixtures using Pillow and the real FFmpeg encoder."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

LABELS = Path(__file__).with_name("visual-search-labels.json")


def create_clips(output: Path) -> dict:
    """Create a new dataset directory; never overwrite an existing fixture set."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "FFmpeg is required; use the processor Docker image if absent on the host"
        )
    dataset = json.loads(LABELS.read_text())
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(LABELS, output / LABELS.name)
    for clip in dataset["clips"]:
        image_path = output / f"{clip['id']}.png"
        with Image.new("RGB", (640, 360), "white") as image:
            draw = ImageDraw.Draw(image)
            if clip["shape"] == "circle":
                draw.ellipse((230, 90, 410, 270), fill=clip["color"])
            elif clip["shape"] == "square":
                draw.rectangle((230, 90, 410, 270), fill=clip["color"])
            elif clip["shape"] == "triangle":
                draw.polygon(((320, 75), (215, 280), (425, 280)), fill=clip["color"])
            else:
                raise ValueError(f"Unsupported fixture shape: {clip['shape']}")
            image.save(image_path)
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-t",
                str(dataset["duration_seconds"]),
                "-vf",
                f"fps={dataset['fps']}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output / clip["filename"]),
            ],
            check=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    return dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory for the clips")
    args = parser.parse_args()
    dataset = create_clips(args.output)
    print(
        f"Created {len(dataset['clips'])} clips and {len(dataset['queries'])} labels: {args.output}"
    )


if __name__ == "__main__":
    main()
