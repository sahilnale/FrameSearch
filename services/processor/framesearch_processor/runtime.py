"""One model and one indexing thread, with explicit startup/shutdown ownership."""

import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from threading import Event, Thread

from .database import Database
from .embeddings import OpenClipEmbedder
from .indexing import VideoIndexer
from .jobs import JobProcessor
from .kafka import UploadConsumer
from .settings import DatabaseSettings, KafkaSettings, Settings, StorageSettings
from .storage import ObjectStorage
from .worker import IngestionWorker

logger = logging.getLogger(__name__)
WorkerFactory = Callable[[OpenClipEmbedder, Event], AbstractContextManager[IngestionWorker]]


@contextmanager
def build_worker(embedder: OpenClipEmbedder, stop: Event) -> Iterator[IngestionWorker]:
    database = Database(DatabaseSettings.from_env())
    database.check_schema()
    with closing(ObjectStorage(StorageSettings.from_env())) as storage:
        storage.check_bucket()
        indexer = VideoIndexer(database, storage, embedder)
        jobs = JobProcessor(database, indexer, stop_event=stop)
        yield IngestionWorker(UploadConsumer(KafkaSettings.from_env()), jobs, stop)


class ProcessorRuntime:
    def __init__(
        self,
        settings: Settings,
        model_loader: Callable[[Settings], OpenClipEmbedder],
        worker_factory: WorkerFactory | None,
    ) -> None:
        self.embedder: OpenClipEmbedder | None = None
        self.worker: IngestionWorker | None = None
        self.model_failed = Event()
        self.worker_failed = Event()
        self.stop = Event()
        self.worker_enabled = worker_factory is not None
        self._settings = settings
        self._model_loader = model_loader
        self._worker_factory = worker_factory
        self._thread = Thread(target=self._run, name="framesearch-ingestion")

    def start(self) -> None:
        self._thread.start()

    def join(self) -> None:
        """After signaling stop, wait for in-flight initialization/indexing and cleanup."""
        self._thread.join()

    def _run(self) -> None:
        try:
            model = self._model_loader(self._settings)
        except Exception:
            self.model_failed.set()
            logger.exception("OpenCLIP startup failed")
            return
        if self.stop.is_set():
            return
        self.embedder = model
        if self._worker_factory is None:
            return  # Isolated embedding tests explicitly disable ingestion in their factory.
        try:
            with self._worker_factory(model, self.stop) as worker:
                self.worker = worker
                worker.run()
                if not self.stop.is_set():
                    raise RuntimeError("Ingestion worker exited without a shutdown request")
        except Exception:
            self.worker_failed.set()
            logger.exception(
                "Ingestion startup or worker failed; inspect pending work before restart"
            )
