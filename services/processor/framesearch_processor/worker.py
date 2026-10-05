"""Serial Kafka-to-job loop; the service lifecycle supplies its shared model."""

import logging
from threading import Event

from .events import parse_media_uploaded
from .jobs import JobProcessor, ProcessingStopped, UnfinishedJobError
from .kafka import UploadConsumer

logger = logging.getLogger(__name__)


class IngestionWorker:
    def __init__(self, consumer: UploadConsumer, jobs: JobProcessor, stop_event: Event) -> None:
        self._consumer = consumer
        self._jobs = jobs
        self._stop = stop_event
        self.ready = Event()

    def run(self) -> None:
        """Run on one dedicated thread; any unresolved event stops consumption."""
        failed = False
        try:
            if self._stop.is_set():
                return
            self._consumer.start()
            self.ready.set()
            while not self._stop.is_set():
                message = self._consumer.poll()
                if message is None:
                    continue
                if self._stop.is_set():
                    raise ProcessingStopped("Processor stopped before handling the upload event")
                event = parse_media_uploaded(message.value(), message.key())
                outcome = self._jobs.process(event)
                if outcome not in ("completed", "failed", "terminal"):
                    raise UnfinishedJobError("Job did not confirm a durable terminal outcome")
                self._consumer.acknowledge(message)
                logger.info(
                    "Upload event acknowledged: event=%s job=%s video=%s outcome=%s",
                    event.event_id,
                    event.job_id,
                    event.video_id,
                    outcome,
                )
        except ProcessingStopped:
            if not self._stop.is_set():
                failed = True
                raise
        except BaseException:
            failed = True
            raise
        finally:
            self.ready.clear()
            try:
                self._consumer.close()
            except Exception:
                if not failed:
                    raise
                # Preserve the unresolved event's original failure for the caller.
                logger.exception("Kafka close also failed after ingestion stopped")
