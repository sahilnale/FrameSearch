"""Build/run disposable local storage checks without modifying shared infrastructure."""

import argparse
import os
import subprocess
from pathlib import Path
from uuid import uuid4

PROCESSOR = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-model", action="store_true", help="use genuine locally cached weights offline"
    )
    arguments = parser.parse_args()
    cache = PROCESSOR / ".cache/openclip"
    if arguments.real_model and not cache.is_dir():
        parser.error("cache real OpenCLIP weights before enabling --real-model")
    subprocess.run(
        ["docker", "build", "-t", "framesearch-processor:storage-checkpoint", str(PROCESSOR)],
        check=True,
    )
    subprocess.run(
        [
            "docker",
            "build",
            "-t",
            "framesearch-minio-test:2025-10-15",
            "-f",
            str(PROCESSOR / "tests/minio.Dockerfile"),
            str(PROCESSOR / "tests"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "docker",
            "build",
            "-t",
            "framesearch-processor:storage-tests",
            "-f",
            str(PROCESSOR / "tests/processor-tests.Dockerfile"),
            str(PROCESSOR / "tests"),
        ],
        check=True,
    )
    namespace = f"framesearch-storage-check-{uuid4().hex[:12]}"
    server = f"{namespace}-minio"
    tests = f"{namespace}-tests"
    environment = os.environ.copy()
    environment.update(
        {
            "MINIO_ROOT_USER": f"test-{uuid4().hex}",
            "MINIO_ROOT_PASSWORD": uuid4().hex,
            "S3_ENDPOINT_INTERNAL": "http://minio:9000",
            "S3_BUCKET": "framesearch",
        }
    )
    environment["S3_ACCESS_KEY"] = environment["MINIO_ROOT_USER"]
    environment["S3_SECRET_KEY"] = environment["MINIO_ROOT_PASSWORD"]
    try:
        subprocess.run(
            ["docker", "network", "create", "--internal", namespace],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--detach",
                "--name",
                server,
                "--network",
                namespace,
                "--network-alias",
                "minio",
                "--env",
                "MINIO_ROOT_USER",
                "--env",
                "MINIO_ROOT_PASSWORD",
                "framesearch-minio-test:2025-10-15",
            ],
            check=True,
            env=environment,
            stdout=subprocess.DEVNULL,
        )
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            tests,
            "--network",
            namespace,
            "--workdir",
            "/work",
            "--mount",
            f"type=bind,source={PROCESSOR},target=/work,readonly",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "FRAMESEARCH_MINIO_TEST=1",
        ]
        for name in ("S3_ENDPOINT_INTERNAL", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
            command.extend(["--env", name])
        if arguments.real_model:
            command.extend(
                [
                    "--mount",
                    f"type=bind,source={cache},target=/cache/openclip,readonly",
                    "--env",
                    "MODEL_CACHE_DIR=/cache/openclip",
                    "--env",
                    "HF_HOME=/tmp/huggingface",
                    "--env",
                    "HF_HUB_OFFLINE=1",
                    "--env",
                    "HF_HUB_DISABLE_TELEMETRY=1",
                    "--env",
                    "FRAMESEARCH_REAL_MODEL_TEST=1",
                    "--env",
                    "IMAGE_BATCH_SIZE=2",
                ]
            )
        command.extend(
            [
                "framesearch-processor:storage-tests",
                "sh",
                "-c",
                "/app/.venv/bin/python tests/wait_for_minio.py && "
                "/app/.venv/bin/python -m pytest -q -p no:cacheprovider --tb=short",
            ]
        )
        subprocess.run(command, check=True, env=environment)
    finally:
        for container in (tests, server):
            subprocess.run(
                ["docker", "rm", "--force", container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            ["docker", "network", "rm", namespace],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    main()
