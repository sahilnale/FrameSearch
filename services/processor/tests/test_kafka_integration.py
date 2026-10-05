"""Real broker offsets/redelivery; this adapter checkpoint does not index videos."""

import json
import os
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

import pytest
from confluent_kafka import OFFSET_INVALID, KafkaError, KafkaException, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

from framesearch_processor.events import parse_media_uploaded
from framesearch_processor.kafka import BrokerError, UploadConsumer
from framesearch_processor.settings import KafkaSettings

pytestmark = [
    pytest.mark.kafka,
    pytest.mark.skipif(
        os.getenv("FRAMESEARCH_KAFKA_TEST") != "1", reason="Enable explicit disposable Kafka checks"
    ),
]


@pytest.fixture
def kafka_settings():
    settings = KafkaSettings(
        tuple(os.getenv("TEST_KAFKA_BROKERS", "localhost:9092").split(",")),
        f"framesearch-test-{uuid4().hex}",
        f"framesearch-test-{uuid4().hex}",
    )
    admin = AdminClient({"bootstrap.servers": ",".join(settings.brokers)})
    admin.create_topics([NewTopic(settings.topic, 1, 1)], request_timeout=10)[
        settings.topic
    ].result(15)
    try:
        yield settings
    finally:
        admin.delete_topics([settings.topic], request_timeout=10)[settings.topic].result(15)
        try:
            admin.delete_consumer_groups([settings.group_id], request_timeout=10)[
                settings.group_id
            ].result(15)
        except KafkaException as error:
            if error.args[0].code() != KafkaError.GROUP_ID_NOT_FOUND:
                raise


def publish(settings, count=1):
    producer = Producer(
        {"bootstrap.servers": ",".join(settings.brokers), "message.timeout.ms": 10_000}
    )
    failures = []
    values = []
    for _ in range(count):
        video_id = uuid4()
        payload = json.dumps(
            {
                "event_id": str(uuid4()),
                "event_type": "media.uploaded",
                "schema_version": 1,
                "video_id": str(video_id),
                "job_id": str(uuid4()),
                "created_at": datetime.now(UTC).isoformat(),
            }
        ).encode()
        values.append(payload)
        producer.produce(
            settings.topic,
            key=str(video_id).encode(),
            value=payload,
            on_delivery=lambda error, message: failures.append(error) if error else None,
        )
    assert producer.flush(15) == 0 and failures == []
    return values


def receive(consumer):
    deadline = monotonic() + 20
    while monotonic() < deadline:
        message = consumer.poll()
        if message is not None:
            return message
    pytest.fail("Kafka upload event was not delivered within 20 seconds")


def committed_offset(consumer, settings):
    partitions = consumer._client.committed([TopicPartition(settings.topic, 0)], timeout=10)
    assert len(partitions) == 1 and partitions[0].error is None
    return partitions[0].offset


def test_real_pending_record_is_uncommitted_and_replayed_after_close(kafka_settings):
    settings = kafka_settings
    payload = publish(settings)[0]
    consumer = UploadConsumer(settings)
    try:
        consumer.start()
        message = receive(consumer)
        assert message.value() == payload and message.offset() == 0
        parse_media_uploaded(message.value(), message.key())
        assert committed_offset(consumer, settings) == OFFSET_INVALID
    finally:
        consumer.close()
    restarted = UploadConsumer(settings)
    try:
        restarted.start()
        message = receive(restarted)
        assert message.value() == payload and message.offset() == 0
        assert committed_offset(restarted, settings) == OFFSET_INVALID
        restarted.acknowledge(message)
        assert committed_offset(restarted, settings) == 1
    finally:
        restarted.close()


def test_real_confirmed_commit_resumes_at_next_record(kafka_settings):
    settings = kafka_settings
    values = publish(settings, 2)
    consumer = UploadConsumer(settings)
    try:
        consumer.start()
        first = receive(consumer)
        assert first.offset() == 0 and first.value() == values[0]
        consumer.acknowledge(first)
        assert committed_offset(consumer, settings) == 1
    finally:
        consumer.close()
    restarted = UploadConsumer(settings)
    try:
        restarted.start()
        second = receive(restarted)
        assert second.offset() == 1 and second.value() == values[1]
        with pytest.raises(BrokerError, match="unacknowledged"):
            restarted.poll()
        restarted.acknowledge(second)
        assert committed_offset(restarted, settings) == 2
    finally:
        restarted.close()


def test_real_missing_topic_is_not_automatically_created(kafka_settings):
    settings = KafkaSettings(
        kafka_settings.brokers, f"framesearch-absent-{uuid4().hex}", kafka_settings.group_id
    )
    consumer = UploadConsumer(settings)
    try:
        with pytest.raises(BrokerError, match="topic"):
            consumer.start()
        assert settings.topic not in consumer._client.list_topics(timeout=10).topics
    finally:
        consumer.close()
