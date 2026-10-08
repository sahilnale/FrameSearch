"""One real indexing attempt; its caller owns claims, retries and failure recording."""

from pathlib import Path

from .database import ClaimedJob, Database, FrameRecord
from .embeddings import OpenClipEmbedder
from .sampling import extracted_frames
from .storage import ObjectStorage


class VideoIndexer:
    def __init__(
        self,
        database: Database,
        storage: ObjectStorage,
        embedder: OpenClipEmbedder,
        *,
        temp_root: Path | None = None,
    ) -> None:
        self._database = database
        self._storage = storage
        self._embedder = embedder
        self._temp_root = temp_root

    def index(self, claim: ClaimedJob) -> None:
        """Return only after the complete video/frame set has been committed ready."""
        with self._storage.downloaded_video(
            claim.object_key, claim.size_bytes, temp_root=self._temp_root
        ) as source:
            with extracted_frames(source, claim.size_bytes, temp_root=self._temp_root) as clip:
                vectors = self._embedder.embed_images([frame.path for frame in clip.frames])
                records = []
                for frame, vector in zip(clip.frames, vectors, strict=True):
                    key = self._storage.upload_thumbnail(
                        claim.video_id, frame, self._embedder.model_version
                    )
                    records.append(
                        FrameRecord(frame.timestamp_ms, key, vector, self._embedder.model_version)
                    )
                self._database.upsert_frames(claim, records)
                self._database.complete_job(
                    claim,
                    clip.metadata.duration_seconds,
                    [frame.timestamp_ms for frame in clip.frames],
                )
