import pytest

from framesearch_processor.settings import KafkaSettings


def test_kafka_environment_defaults(monkeypatch):
    for name in ("KAFKA_BROKERS", "KAFKA_TOPIC", "KAFKA_CONSUMER_GROUP"):
        monkeypatch.delenv(name, raising=False)
    assert KafkaSettings.from_env() == KafkaSettings()


def test_kafka_reads_shared_compose_and_optional_group(monkeypatch):
    monkeypatch.setenv("KAFKA_BROKERS", "kafka:29092, backup:9092")
    monkeypatch.setenv("KAFKA_TOPIC", "media.uploaded")
    monkeypatch.setenv("KAFKA_CONSUMER_GROUP", "framesearch-verification")
    assert KafkaSettings.from_env() == KafkaSettings(
        ("kafka:29092", "backup:9092"), "media.uploaded", "framesearch-verification"
    )


@pytest.mark.parametrize("broker", ["kafka:29092", "127.0.0.1:9092", "[::1]:9092"])
def test_local_dns_ipv4_and_ipv6_brokers(broker):
    assert KafkaSettings((broker,)).brokers == (broker,)


@pytest.mark.parametrize(
    "brokers",
    [
        (),
        ["kafka:9092"],
        (None,),
        ("",),
        ("kafka",),
        ("kafka:0",),
        ("kafka:65536",),
        ("kafka:abc",),
        ("kafka :9092",),
        ("http://kafka:9092",),
        ("secret@kafka:9092",),
        ("kafka:9092/path",),
        ("kafka:9092?secret=value",),
        ("kafka:9092#fragment",),
    ],
)
def test_invalid_brokers_do_not_reach_the_native_client(brokers):
    with pytest.raises(ValueError, match="KAFKA_BROKERS"):
        KafkaSettings(brokers)


@pytest.mark.parametrize("value", ["", "kafka:9092,", ",kafka:9092"])
def test_missing_csv_brokers_fail(monkeypatch, value):
    monkeypatch.setenv("KAFKA_BROKERS", value)
    with pytest.raises(ValueError, match="KAFKA_BROKERS"):
        KafkaSettings.from_env()


@pytest.mark.parametrize(
    "value", [None, "", ".", "..", "media uploaded", "media/uploaded", "x" * 250]
)
def test_invalid_topic(value):
    with pytest.raises(ValueError, match="KAFKA_TOPIC"):
        KafkaSettings(topic=value)


@pytest.mark.parametrize("value", [None, "", " ", "group/name", "x" * 256])
def test_invalid_group(value):
    with pytest.raises(ValueError, match="KAFKA_CONSUMER_GROUP"):
        KafkaSettings(group_id=value)
