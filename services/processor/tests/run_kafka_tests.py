"""Run processor broker checks in a disposable isolated Kafka 3.9 network."""

import argparse
import subprocess
from pathlib import Path
from uuid import uuid4

PROCESSOR = Path(__file__).resolve().parents[1]
PRODUCTION_IMAGE = "framesearch-processor:kafka-checkpoint"
TEST_IMAGE = "framesearch-processor:kafka-tests"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="reuse the current test image")
    arguments = parser.parse_args()
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
        subprocess.run(
            [
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
                "FRAMESEARCH_KAFKA_TEST=1",
                "--env",
                "TEST_KAFKA_BROKERS=kafka:29092",
                TEST_IMAGE,
                "sh",
                "-c",
                "/app/.venv/bin/python tests/wait_for_kafka.py && "
                "/app/.venv/bin/python -m pytest -q -p no:cacheprovider --tb=short "
                "tests/test_kafka_settings.py tests/test_kafka.py tests/test_kafka_integration.py "
                "tests/test_events.py tests/test_jobs.py",
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        subprocess.run(["docker", "logs", "--tail", "40", broker], check=False)
        raise
    finally:
        for name in (tests, broker):
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
