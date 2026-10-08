from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from confluent_kafka import Consumer, KafkaError, KafkaException, Message, TopicPartition

from framesearch_processor.kafka import BrokerError, UploadConsumer
from framesearch_processor.settings import KafkaSettings


def metadata(*, topic_error=None, partition_error=None, leader=1):
    partition = SimpleNamespace(error=partition_error, leader=leader)
    topic = SimpleNamespace(error=topic_error, partitions={0: partition})
    return SimpleNamespace(topics={"media.uploaded": topic})


@pytest.fixture
def broker(monkeypatch):
    client = Mock(spec=Consumer)
    client.list_topics.return_value = metadata()
    constructor = Mock(return_value=client)
    monkeypatch.setattr("framesearch_processor.kafka.Consumer", constructor)
    consumer = UploadConsumer(KafkaSettings())
    message = Mock(spec=Message)
    message.error.return_value = None
    message.topic.return_value = "media.uploaded"
    message.partition.return_value = 0
    message.offset.return_value = 7
    client.poll.return_value = message
    client.commit.return_value = [TopicPartition("media.uploaded", 0, 8)]
    return consumer, client, constructor, message


def test_native_config_never_automatically_stores_or_commits_offsets(broker):
    _, _, constructor, _ = broker
    configuration = constructor.call_args.args[0]
    assert configuration["enable.auto.commit"] is False
    assert configuration["enable.auto.offset.store"] is False
    assert configuration["allow.auto.create.topics"] is False
    assert configuration["group.protocol"] == "classic"
    assert configuration["auto.offset.reset"] == "earliest"
    assert configuration["max.poll.interval.ms"] == 3_600_000


def test_start_checks_existing_topic_before_subscribing(broker):
    consumer, client, _, _ = broker
    consumer.start()
    client.list_topics.assert_called_once_with(timeout=10)
    client.subscribe.assert_called_once_with(["media.uploaded"])
    client.commit.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["missing", "topic_error", "empty", "partition_error", "leader"]
)
def test_unavailable_topic_is_not_subscribed_or_created(broker, failure):
    consumer, client, _, _ = broker
    if failure == "missing":
        client.list_topics.return_value.topics.clear()
    elif failure == "empty":
        client.list_topics.return_value.topics["media.uploaded"].partitions.clear()
    elif failure == "topic_error":
        client.list_topics.return_value = metadata(
            topic_error=KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART)
        )
    elif failure == "partition_error":
        client.list_topics.return_value = metadata(
            partition_error=KafkaError(KafkaError.LEADER_NOT_AVAILABLE)
        )
    else:
        client.list_topics.return_value = metadata(leader=-1)
    with pytest.raises(BrokerError, match="topic"):
        consumer.start()
    client.subscribe.assert_not_called()


@pytest.mark.parametrize("operation", ["list_topics", "subscribe"])
def test_native_startup_failure_is_reported(broker, operation):
    consumer, client, _, _ = broker
    getattr(client, operation).side_effect = KafkaException(KafkaError(KafkaError._TRANSPORT))
    with pytest.raises(BrokerError, match="startup"):
        consumer.start()


def test_poll_waits_bounded_time_and_does_not_acknowledge(broker):
    consumer, client, _, message = broker
    consumer.start()
    assert consumer.poll() is message
    client.poll.assert_called_once_with(1)
    client.commit.assert_not_called()
    with pytest.raises(BrokerError, match="unacknowledged"):
        consumer.poll()
    assert client.poll.call_count == 1


def test_poll_timeout_does_not_create_pending_work(broker):
    consumer, client, _, message = broker
    consumer.start()
    client.poll.side_effect = [None, message]
    assert consumer.poll() is None
    assert consumer.poll() is message


def test_partition_eof_does_not_create_pending_work(broker):
    consumer, client, _, message = broker
    consumer.start()
    eof = Mock(spec=Message)
    eof.error.return_value = KafkaError(KafkaError._PARTITION_EOF)
    client.poll.side_effect = [eof, message]
    assert consumer.poll() is None
    assert consumer.poll() is message


def test_native_poll_failure_is_not_acknowledged(broker):
    consumer, client, _, _ = broker
    consumer.start()
    client.poll.side_effect = KafkaException(KafkaError(KafkaError._TRANSPORT))
    with pytest.raises(BrokerError, match="receive"):
        consumer.poll()
    client.commit.assert_not_called()


def test_message_error_is_not_an_upload_record(broker):
    consumer, client, _, message = broker
    consumer.start()
    message.error.return_value = KafkaError(KafkaError._TRANSPORT)
    with pytest.raises(BrokerError, match="receive"):
        consumer.poll()
    client.commit.assert_not_called()


@pytest.mark.parametrize("field,value", [("topic", "other"), ("partition", -1), ("offset", -1)])
def test_unexpected_message_coordinates_cannot_be_acknowledged(broker, field, value):
    consumer, client, _, message = broker
    consumer.start()
    getattr(message, field).return_value = value
    with pytest.raises(BrokerError, match="unexpected"):
        consumer.poll()
    client.commit.assert_not_called()


def test_successful_explicit_commit_allows_next_poll(broker):
    consumer, client, _, message = broker
    consumer.start()
    consumer.poll()
    consumer.acknowledge(message)
    client.commit.assert_called_once_with(message=message, asynchronous=False)
    assert consumer.poll() is message


def test_another_record_cannot_commit_over_pending_work(broker):
    consumer, client, _, _ = broker
    consumer.start()
    consumer.poll()
    with pytest.raises(BrokerError, match="pending"):
        consumer.acknowledge(Mock(spec=Message))
    client.commit.assert_not_called()


def test_nothing_can_be_acknowledged_without_pending_work(broker):
    consumer, client, _, message = broker
    consumer.start()
    with pytest.raises(BrokerError, match="pending"):
        consumer.acknowledge(message)
    client.commit.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["exception", "none", "empty", "error", "topic", "partition", "offset", "extra"]
)
def test_unconfirmed_commit_keeps_event_pending(broker, failure):
    consumer, client, _, message = broker
    consumer.start()
    consumer.poll()
    if failure == "exception":
        client.commit.side_effect = KafkaException(KafkaError(KafkaError._TRANSPORT))
    else:
        result = SimpleNamespace(topic="media.uploaded", partition=0, offset=8, error=None)
        if failure == "none":
            client.commit.return_value = None
        elif failure == "empty":
            client.commit.return_value = []
        elif failure == "extra":
            client.commit.return_value = [result, result]
        else:
            setattr(
                result,
                failure,
                {
                    "error": KafkaError(KafkaError._TRANSPORT),
                    "topic": "other",
                    "partition": 1,
                    "offset": 9,
                }[failure],
            )
            client.commit.return_value = [result]
    with pytest.raises(BrokerError, match="commit|confirm"):
        consumer.acknowledge(message)
    with pytest.raises(BrokerError, match="unacknowledged"):
        consumer.poll()


def test_close_is_idempotent_and_never_explicitly_commits(broker):
    consumer, client, _, _ = broker
    consumer.start()
    consumer.poll()
    consumer.close()
    consumer.close()
    client.close.assert_called_once()
    client.commit.assert_not_called()
    with pytest.raises(BrokerError, match="not running"):
        consumer.poll()


def test_close_failure_still_prevents_reuse(broker):
    consumer, client, _, _ = broker
    client.close.side_effect = KafkaException(KafkaError(KafkaError._TRANSPORT))
    with pytest.raises(BrokerError, match="close"):
        consumer.close()
    consumer.close()
    client.close.assert_called_once()
    with pytest.raises(BrokerError):
        consumer.start()


def test_closed_or_already_started_client_cannot_start_again(broker):
    consumer, _, _, _ = broker
    consumer.start()
    with pytest.raises(BrokerError, match="once"):
        consumer.start()
    consumer.close()
    with pytest.raises(BrokerError, match="once"):
        consumer.start()


def test_operations_require_start(broker):
    consumer, client, _, message = broker
    with pytest.raises(BrokerError, match="not running"):
        consumer.poll()
    with pytest.raises(BrokerError, match="not running"):
        consumer.acknowledge(message)
    client.poll.assert_not_called()
    client.commit.assert_not_called()
