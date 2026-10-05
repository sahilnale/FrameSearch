"""Single-job policy; the Kafka caller acknowledges only a returned terminal outcome."""

import logging
from threading import Event
from typing import Literal

from .database import MAX_PROCESSING_ERROR_CHARACTERS, Database, DatabaseError, JobStateError
from .events import MediaUploaded
from .indexing import VideoIndexer
from .media import InvalidVideo, MediaToolError
from .storage import StorageError

logger = logging.getLogger(__name__)
MAX_INDEX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)


class ProcessingStopped(RuntimeError):
    """Shutdown interrupted processing; leave the offset and active job for recovery."""


class UnfinishedJobError(RuntimeError):
    """Busy/missing jobs are not durable terminal outcomes and cannot be acknowledged."""


def _exhausted_reason(error: Exception) -> str:
    if isinstance(error, StorageError):
        return "Storage unavailable after 3 attempts; check MinIO access and retry indexing"
    if isinstance(error, MediaToolError):
        return "Media tools failed after 3 attempts; check FFmpeg/FFprobe and retry indexing"
    return "Frame indexing failed after 3 attempts; check processor logs and retry indexing"


class JobProcessor:
    def __init__(
        self, database: Database, indexer: VideoIndexer, *, stop_event: Event | None = None
    ) -> None:
        self._database = database
        self._indexer = indexer
        self._stop = stop_event if stop_event is not None else Event()

    def process(self, event: MediaUploaded) -> Literal["completed", "failed", "terminal"]:
        if self._stop.is_set():
            raise ProcessingStopped("Processor is stopping")
        result = self._database.claim_job(event.job_id, event.video_id)
        if result.outcome == "terminal":
            return "terminal"
        if result.outcome != "claimed" or result.job is None:
            raise UnfinishedJobError(
                f"Job claim is {result.outcome}; reconcile the job before acknowledging this event"
            )
        for attempt in range(MAX_INDEX_ATTEMPTS):
            if self._stop.is_set():
                raise ProcessingStopped("Processor stopped before indexing; recover its active job")
            try:
                self._indexer.index(result.job)
                return "completed"
            except InvalidVideo as error:
                reason = str(error).strip()[:MAX_PROCESSING_ERROR_CHARACTERS]
                self._database.fail_job(
                    result.job, reason or "Invalid MP4; export and upload again"
                )
                return "failed"
            except JobStateError:
                # Stale receipts/inconsistent states must never fail a newer job.
                raise
            except Exception as error:
                logger.warning(
                    "Indexing attempt failed: job=%s attempt=%d error_type=%s",
                    event.job_id,
                    attempt + 1,
                    type(error).__name__,
                    exc_info=True,
                )
                if attempt == MAX_INDEX_ATTEMPTS - 1:
                    if isinstance(error, DatabaseError):
                        # A failed/ambiguous DB commit cannot establish a terminal outcome.
                        raise
                    self._database.fail_job(result.job, _exhausted_reason(error))
                    return "failed"
                if self._stop.wait(RETRY_DELAYS_SECONDS[attempt]):
                    raise ProcessingStopped(
                        "Processor stopped during retry; recover its active job"
                    ) from error
        raise RuntimeError("Indexing attempts unexpectedly exhausted")
