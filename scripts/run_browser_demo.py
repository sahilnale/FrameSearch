"""Run a disposable genuine backend for the localhost:3000 browser demo.

No user environment file is read. Credentials are generated in memory. PostgreSQL,
Kafka, and MinIO data is temporary; stopping the script removes only its resources.
"""

import argparse
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Event
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PROCESSOR_IMAGE = "framesearch-processor:kafka-checkpoint"
API_IMAGE = "framesearch-api:queue-checkpoint"
MINIO_IMAGE = "framesearch-minio-test:2025-10-15"
WEB_IMAGE = "framesearch-web:ui-checkpoint"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--with-web", action="store_true", help="also run the production web image"
    )
    args = parser.parse_args()
    cache = ROOT / "services/processor/.cache/openclip"
    if not cache.is_dir():
        parser.error(
            "cache the real OpenCLIP checkpoint first; see the processor README"
        )
    for port in (8080, 9000, *([3000] if args.with_web else [])):
        with socket.socket() as check:
            try:
                check.bind(("127.0.0.1", port))
            except OSError:
                parser.error(
                    f"localhost:{port} is already in use; no existing service was stopped"
                )
    if not args.skip_build:
        for image, context, dockerfile in (
            (PROCESSOR_IMAGE, ROOT / "services/processor", None),
            (API_IMAGE, ROOT / "services/api", None),
            (
                MINIO_IMAGE,
                ROOT / "services/processor/tests",
                ROOT / "services/processor/tests/minio.Dockerfile",
            ),
            *([(WEB_IMAGE, ROOT / "apps/web", None)] if args.with_web else []),
        ):
            command = ["docker", "build", "-t", image]
            if dockerfile:
                command.extend(["-f", str(dockerfile)])
            subprocess.run([*command, str(context)], check=True)

    name = f"framesearch-web-demo-{uuid4().hex[:10]}"
    names = {
        service: f"{name}-{service}"
        for service in (
            "postgres",
            "kafka",
            "minio",
            "processor",
            "api",
            "web",
            "bootstrap",
        )
    }
    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_USER": f"demo_{uuid4().hex[:10]}",
            "POSTGRES_PASSWORD": uuid4().hex,
            "POSTGRES_DB": "framesearch_demo",
            "MINIO_ROOT_USER": f"demo-{uuid4().hex}",
            "MINIO_ROOT_PASSWORD": uuid4().hex,
            "S3_BUCKET": "framesearch-demo",
            "S3_ENDPOINT_INTERNAL": "http://minio:9000",
            "S3_ENDPOINT_PUBLIC": "http://localhost:9000",
            "KAFKA_BROKERS": "kafka:29092",
            "KAFKA_TOPIC": "media.uploaded",
            "PROCESSOR_URL": "http://processor:8000",
            "WEB_ORIGIN": "http://localhost:3000",
            "API_ADDR": ":8080",
            "MODEL_CACHE_DIR": "/cache/openclip",
            "HF_HUB_OFFLINE": "1",
            "IMAGE_BATCH_SIZE": "2",
            "TORCH_NUM_THREADS": "2",
            "MINIO_API_CORS_ALLOW_ORIGIN": "http://localhost:3000",
        }
    )
    environment["DATABASE_URL"] = (
        f"postgresql://{environment['POSTGRES_USER']}:{environment['POSTGRES_PASSWORD']}@postgres:5432/framesearch_demo?sslmode=disable"
    )
    environment["S3_ACCESS_KEY"] = environment["MINIO_ROOT_USER"]
    environment["S3_SECRET_KEY"] = environment["MINIO_ROOT_PASSWORD"]
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    def run(service, image, *, variables=(), options=(), command=(), detached=True):
        call = [
            "docker",
            "run",
            "--rm",
            "--name",
            names[service],
            "--network",
            name,
            "--network-alias",
            service,
        ]
        if detached:
            call.append("--detach")
        for variable in variables:
            call.extend(["--env", variable])
        subprocess.run(
            [*call, *options, image, *command],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL if detached else None,
        )

    common = (
        "DATABASE_URL",
        "S3_ENDPOINT_INTERNAL",
        "S3_ENDPOINT_PUBLIC",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_BUCKET",
        "KAFKA_BROKERS",
        "KAFKA_TOPIC",
    )
    try:
        subprocess.run(
            ["docker", "network", "create", name], check=True, stdout=subprocess.DEVNULL
        )
        run(
            "postgres",
            "pgvector/pgvector:0.8.0-pg17",
            variables=("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"),
            options=(
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=512m",
                "--mount",
                f"type=bind,source={ROOT / 'db/migrations/001_initial.sql'},target=/docker-entrypoint-initdb.d/001.sql,readonly",
            ),
        )
        kafka = {
            "CLUSTER_ID": "MkU3OEVBNTcwNTJENDM2Qk",
            "KAFKA_NODE_ID": "1",
            "KAFKA_PROCESS_ROLES": "broker,controller",
            "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@kafka:9093",
            "KAFKA_LISTENERS": "INTERNAL://:29092,CONTROLLER://:9093",
            "KAFKA_ADVERTISED_LISTENERS": "INTERNAL://kafka:29092",
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "INTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT",
            "KAFKA_INTER_BROKER_LISTENER_NAME": "INTERNAL",
            "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
            "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
            "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR": "1",
            "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR": "1",
            "KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS": "0",
            "KAFKA_AUTO_CREATE_TOPICS_ENABLE": "false",
            "KAFKA_LOG_DIRS": "/var/lib/kafka/data",
            "KAFKA_HEAP_OPTS": "-Xms256m -Xmx512m",
        }
        environment.update(kafka)
        run(
            "kafka",
            "apache/kafka:3.9.0",
            variables=tuple(kafka),
            options=("--tmpfs", "/var/lib/kafka/data:rw,size=256m,uid=1000,gid=1000"),
        )
        run(
            "minio",
            MINIO_IMAGE,
            variables=(
                "MINIO_ROOT_USER",
                "MINIO_ROOT_PASSWORD",
                "MINIO_API_CORS_ALLOW_ORIGIN",
            ),
            options=(
                "--publish",
                "127.0.0.1:9000:9000",
                "--tmpfs",
                "/data:rw,size=160m,mode=1777",
            ),
        )
        run(
            "bootstrap",
            PROCESSOR_IMAGE,
            variables=common,
            options=(
                "--mount",
                f"type=bind,source={ROOT / 'apps/web/tests/browser-bootstrap.py'},target=/bootstrap.py,readonly",
            ),
            command=("/app/.venv/bin/python", "/bootstrap.py"),
            detached=False,
        )
        run(
            "processor",
            PROCESSOR_IMAGE,
            variables=(
                *common,
                "MODEL_CACHE_DIR",
                "HF_HUB_OFFLINE",
                "IMAGE_BATCH_SIZE",
                "TORCH_NUM_THREADS",
            ),
            options=(
                "--mount",
                f"type=bind,source={cache},target=/cache/openclip,readonly",
            ),
        )
        run(
            "api",
            API_IMAGE,
            variables=(*common, "PROCESSOR_URL", "WEB_ORIGIN", "API_ADDR"),
            options=("--publish", "127.0.0.1:8080:8080"),
        )
        deadline = time.monotonic() + 120
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    "http://localhost:8080/health/ready", timeout=6
                ) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                pass
            stop.wait(1)
        else:
            raise RuntimeError(
                "Browser demo API did not become ready within 120 seconds"
            )
        if args.with_web:
            run("web", WEB_IMAGE, options=("--publish", "127.0.0.1:3000:3000"))
        print(f"Browser demo ready: http://localhost:3000 (backend {name})", flush=True)
        print(
            "Fresh empty library; upload your MP4s in the browser. Ctrl+C removes this disposable demo only.",
            flush=True,
        )
        stop.wait()
    finally:
        for service in (
            "web",
            "api",
            "processor",
            "bootstrap",
            "minio",
            "kafka",
            "postgres",
        ):
            subprocess.run(
                ["docker", "rm", "--force", names[service]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            ["docker", "network", "rm", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(
            "Removed only this browser demo's temporary containers/network", flush=True
        )


if __name__ == "__main__":
    main()
