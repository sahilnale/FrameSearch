"""Run processor broker checks in a disposable isolated Kafka 3.9 network."""

import argparse
import os
import subprocess
from pathlib import Path
from uuid import uuid4

PROCESSOR = Path(__file__).resolve().parents[1]
PRODUCTION_IMAGE = "framesearch-processor:kafka-checkpoint"
TEST_IMAGE = "framesearch-processor:kafka-tests"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="reuse the current test image")
    parser.add_argument(
        "--indexing", action="store_true", help="also run real MinIO/CLIP/PostgreSQL ingestion"
    )
    parser.add_argument(
        "--full", action="store_true", help="run every processor test; requires --indexing"
    )
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="run only the labeled search check; requires --indexing",
    )
    arguments = parser.parse_args()
    if (arguments.full or arguments.semantic) and not arguments.indexing:
        parser.error("--full/--semantic require --indexing to enable the live dependencies")
    if arguments.full and arguments.semantic:
        parser.error("choose either --full or --semantic")
    cache = PROCESSOR / ".cache/openclip"
    if arguments.indexing and not cache.is_dir():
        parser.error("cache real OpenCLIP weights before enabling --indexing")
    if not arguments.skip_build:
        subprocess.run(["docker", "build", "-t", PRODUCTION_IMAGE, str(PROCESSOR)], check=True)
        subprocess.run(
            [
                "docker",
                "build",
                "-t",
                TEST_IMAGE,
                "--build-arg",
                f"PROCESSOR_IMAGE={PRODUCTION_IMAGE}",
                "-f",
                str(PROCESSOR / "tests/processor-tests.Dockerfile"),
                str(PROCESSOR / "tests"),
            ],
            check=True,
        )
    namespace = f"framesearch-kafka-check-{uuid4().hex[:12]}"
    broker, tests = f"{namespace}-broker", f"{namespace}-tests"
    postgres, minio = f"{namespace}-postgres", f"{namespace}-minio"
    settings = {
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
    environment = os.environ.copy()
    if arguments.indexing:
        minio_image = "framesearch-minio-test:2025-10-15"
        if subprocess.run(
            ["docker", "image", "inspect", minio_image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    minio_image,
                    "-f",
                    str(PROCESSOR / "tests/minio.Dockerfile"),
                    str(PROCESSOR / "tests"),
                ],
                check=True,
            )
        environment.update(
            {
                "POSTGRES_USER": f"test_{uuid4().hex[:12]}",
                "POSTGRES_PASSWORD": uuid4().hex,
                "POSTGRES_DB": "framesearch_test",
                "MINIO_ROOT_USER": f"test-{uuid4().hex}",
                "MINIO_ROOT_PASSWORD": uuid4().hex,
                "S3_ENDPOINT_INTERNAL": "http://minio:9000",
                "S3_BUCKET": "framesearch",
            }
        )
        environment["DATABASE_URL"] = (
            f"postgresql://{environment['POSTGRES_USER']}:{environment['POSTGRES_PASSWORD']}"
            "@postgres:5432/framesearch_test?sslmode=disable"
        )
        environment["TEST_DATABASE_URL"] = environment["DATABASE_URL"]
        environment["S3_ACCESS_KEY"] = environment["MINIO_ROOT_USER"]
        environment["S3_SECRET_KEY"] = environment["MINIO_ROOT_PASSWORD"]
    try:
        subprocess.run(
            ["docker", "network", "create", "--internal", namespace],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        command = [
            "docker",
            "run",
            "--rm",
            "--detach",
            "--name",
            broker,
            "--network",
            namespace,
            "--network-alias",
            "kafka",
            "--tmpfs",
            "/var/lib/kafka/data:rw,size=256m,uid=1000,gid=1000",
        ]
        for name, value in settings.items():
            command.extend(["--env", f"{name}={value}"])
        subprocess.run(command + ["apache/kafka:3.9.0"], check=True, stdout=subprocess.DEVNULL)
        if arguments.indexing:
            command = [
                "docker",
                "run",
                "--rm",
                "--detach",
                "--name",
                postgres,
                "--network",
                namespace,
                "--network-alias",
                "postgres",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=512m",
                "--mount",
                f"type=bind,source={PROCESSOR.parents[1] / 'db/migrations/001_initial.sql'},"
                "target=/docker-entrypoint-initdb.d/001.sql,readonly",
            ]
            for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
                command.extend(["--env", name])
            subprocess.run(
                command + ["pgvector/pgvector:0.8.0-pg17"],
                check=True,
                env=environment,
                stdout=subprocess.DEVNULL,
            )
            command = [
                "docker",
                "run",
                "--rm",
                "--detach",
                "--name",
                minio,
                "--network",
                namespace,
                "--network-alias",
                "minio",
                "--tmpfs",
                "/data:rw,size=128m,mode=1777",
            ]
            for name in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
                command.extend(["--env", name])
            subprocess.run(
                command + [minio_image], check=True, env=environment, stdout=subprocess.DEVNULL
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
            "--mount",
            f"type=bind,source={PROCESSOR.parents[1] / 'scripts'},target=/scripts,readonly",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONPATH=/work",
            "--env",
            "FRAMESEARCH_KAFKA_TEST=1",
            "--env",
            "TEST_KAFKA_BROKERS=kafka:29092",
        ]
        waits = "/app/.venv/bin/python tests/wait_for_kafka.py && "
        selection = (
            "tests/test_kafka_settings.py tests/test_kafka.py tests/test_kafka_integration.py "
            "tests/test_events.py tests/test_jobs.py tests/test_worker.py "
            "tests/test_app.py tests/test_runtime.py"
        )
        if arguments.indexing:
            for name in (
                "DATABASE_URL",
                "TEST_DATABASE_URL",
                "S3_ENDPOINT_INTERNAL",
                "S3_BUCKET",
                "S3_ACCESS_KEY",
                "S3_SECRET_KEY",
            ):
                command.extend(["--env", name])
            for setting in (
                "FRAMESEARCH_DATABASE_TEST=1",
                "FRAMESEARCH_MINIO_TEST=1",
                "FRAMESEARCH_REAL_MODEL_TEST=1",
                "MODEL_CACHE_DIR=/cache/openclip",
                "HF_HOME=/tmp/huggingface",
                "HF_HUB_OFFLINE=1",
                "IMAGE_BATCH_SIZE=2",
            ):
                command.extend(["--env", setting])
            command.extend(["--mount", f"type=bind,source={cache},target=/cache/openclip,readonly"])
            waits += (
                "/app/.venv/bin/python tests/wait_for_postgres.py && "
                "/app/.venv/bin/python tests/wait_for_minio.py && "
            )
            selection += " tests/test_worker_integration.py tests/test_service_integration.py"
        if arguments.full:
            selection = ""
        if arguments.semantic:
            selection = "-s tests/test_visual_search.py"
        command.extend(
            [
                TEST_IMAGE,
                "sh",
                "-c",
                waits
                + "/app/.venv/bin/python -m pytest -q -p no:cacheprovider --tb=short "
                + selection,
            ]
        )
        subprocess.run(command, check=True, env=environment)
    except subprocess.CalledProcessError:
        subprocess.run(["docker", "logs", "--tail", "40", broker], check=False)
        raise
    finally:
        for name in (tests, minio, postgres, broker):
            subprocess.run(
                ["docker", "rm", "--force", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            ["docker", "network", "rm", namespace],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Removed this check's disposable Kafka containers and network", flush=True)


if __name__ == "__main__":
    main()
