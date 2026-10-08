"""Bounded readiness wait for the disposable broker in run_kafka_tests.py."""

import os
import time

from confluent_kafka import KafkaException
from confluent_kafka.admin import AdminClient

client = AdminClient({"bootstrap.servers": os.environ["TEST_KAFKA_BROKERS"], "log_level": 0})
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    try:
        if client.list_topics(timeout=2).brokers:
            print("Disposable Kafka broker is ready", flush=True)
            break
    except KafkaException:
        pass
    time.sleep(1)
else:
    raise RuntimeError("Disposable Kafka startup timed out after 60 seconds")
