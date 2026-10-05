"""Single-threaded upload consumer with one pending message and explicit commits."""

from confluent_kafka import Consumer, KafkaError, KafkaException, Message

from .settings import KafkaSettings


class BrokerError(RuntimeError):
    """Broker access or offset confirmation failed; leave work pending for redelivery."""


class UploadConsumer:
    def __init__(self, settings: KafkaSettings) -> None:
        self._settings = settings
        self._started = False
        self._closed = False
        self._pending: Message | None = None
        self._client = Consumer(
            {
                "bootstrap.servers": ",".join(settings.brokers),
                "group.id": settings.group_id,
                "client.id": "framesearch-processor",
                "group.protocol": "classic",  # Compatible with Compose's Kafka 3.9.
                "enable.auto.commit": False,
                "enable.auto.offset.store": False,
                "auto.offset.reset": "earliest",
                "allow.auto.create.topics": False,
                "enable.partition.eof": False,
                "max.poll.interval.ms": 3_600_000,
                "socket.timeout.ms": 10_000,
                "queued.max.messages.kbytes": 1024,
                "fetch.max.bytes": 1_048_576,
            }
        )

    def start(self) -> None:
        if self._closed or self._started:
            raise BrokerError("Consumer must be started once before use")
        try:
            # Fetch all metadata without requesting an unknown topic by name.
            metadata = self._client.list_topics(timeout=10)
            topic = metadata.topics.get(self._settings.topic)
            if (
                topic is None
                or topic.error is not None
                or not topic.partitions
                or any(
                    partition.error is not None or partition.leader < 0
                    for partition in topic.partitions.values()
                )
            ):
                raise BrokerError("Upload topic is unavailable; run Kafka topic initialization")
            self._client.subscribe([self._settings.topic])
        except KafkaException as error:
            raise BrokerError("Kafka startup failed; check broker access") from error
        self._started = True

    def _require_started(self) -> None:
        if not self._started or self._closed:
            raise BrokerError("Consumer is not running")

    def poll(self) -> Message | None:
        self._require_started()
        if self._pending is not None:
            raise BrokerError("Previous upload event is still unacknowledged")
        try:
            message = self._client.poll(1)
        except KafkaException as error:
            raise BrokerError("Kafka receive failed; leave the event unacknowledged") from error
        if message is None:
            return None
        failure = message.error()
        if failure is not None:
            if failure.code() == KafkaError._PARTITION_EOF:
                return None
            raise BrokerError("Kafka receive failed; check broker logs") from KafkaException(
                failure
            )
        if (
            message.topic() != self._settings.topic
            or message.partition() < 0
            or message.offset() < 0
        ):
            raise BrokerError("Kafka returned an unexpected topic or invalid offset")
        self._pending = message
        return message

    def acknowledge(self, message: Message) -> None:
        """Caller must first confirm this event's durable terminal database state."""
        self._require_started()
        if self._pending is None or message is not self._pending:
            raise BrokerError("Only the pending upload event can be acknowledged")
        try:
            committed = self._client.commit(message=message, asynchronous=False)
        except KafkaException as error:
            raise BrokerError("Kafka offset commit failed; leave the event pending") from error
        if (
            not committed
            or len(committed) != 1
            or committed[0].error is not None
            or committed[0].topic != message.topic()
            or committed[0].partition != message.partition()
            or committed[0].offset != message.offset() + 1
        ):
            raise BrokerError("Kafka did not confirm the pending event's next offset")
        self._pending = None

    def close(self) -> None:
        """Leave the group without auto-committing pending work; safe to call twice."""
        if self._closed:
            return
        self._closed = True
        try:
            self._client.close()
        except KafkaException as error:
            raise BrokerError("Kafka consumer close failed") from error
