BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS videos (
 id UUID PRIMARY KEY,
 filename TEXT NOT NULL,
 object_key TEXT NOT NULL UNIQUE,
 content_type TEXT NOT NULL CHECK (content_type = 'video/mp4'),
 size_bytes BIGINT NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 104857600),
 duration_seconds DOUBLE PRECISION CHECK (duration_seconds > 0 AND duration_seconds <= 180),
 status TEXT NOT NULL CHECK (status IN ('awaiting_upload','queued','processing','ready','failed')),
 processing_error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS video_frames (
 id UUID PRIMARY KEY,
 video_id UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
 timestamp_ms INTEGER NOT NULL CHECK (timestamp_ms >= 0 AND timestamp_ms < 180000),
 thumbnail_key TEXT NOT NULL,
 embedding VECTOR(512) NOT NULL,
 model_version TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(video_id, timestamp_ms, model_version)
);
CREATE TABLE IF NOT EXISTS processing_jobs (
 id UUID PRIMARY KEY,
 video_id UUID NOT NULL REFERENCES videos(id),
 status TEXT NOT NULL CHECK (status IN ('queued','processing','completed','failed')),
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
 last_error TEXT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_video
 ON processing_jobs(video_id) WHERE status IN ('queued','processing');
CREATE INDEX IF NOT EXISTS frames_cosine_hnsw ON video_frames USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS frames_video ON video_frames(video_id);
CREATE INDEX IF NOT EXISTS jobs_status ON processing_jobs(status, updated_at);
COMMIT;
