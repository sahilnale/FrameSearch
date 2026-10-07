# Frozen backend integration contracts

Status: backend code and contracts exist. Compose, Makefile and processor/web integration described below are planned for subsequent review milestones; no live infrastructure or full video path has been verified.

The authoritative specification is `FrameSearch_Specs/SPEC.md`, section 13. Developer 1 owns Go, database, infrastructure, root docs and Makefile. Developer 2 exclusively owns `services/processor`, `apps/web` and `scripts`.

Public API `localhost:8080`, web `localhost:3000`, browser MinIO `localhost:9000`. Within Compose: `postgres:5432`, `kafka:29092`, `minio:9000`, `processor:8000`. All URLs returned to a browser are signed using S3_ENDPOINT_PUBLIC, never the internal service hostname. The bucket is private; MinIO permits CORS from WEB_ORIGIN. GET supports S3 byte ranges. Signed URLs expire after 900 seconds.

Model: MODEL_NAME=ViT-B-32, MODEL_PRETRAINED=laion2b_s34b_b79k, model_version=`ViT-B-32:laion2b_s34b_b79k`, 512 finite L2-normalized coordinates. The identifier exists in [OpenCLIP's official example](https://github.com/mlfoundations/open_clip). Runtime checkpoint loading is Developer 2's responsibility and remains unverified here. No substitute embeddings are used.

Environment names and defaults are in `.env.example`. Compose overrides internal addresses explicitly. Processor must read DATABASE_URL, KAFKA_BROKERS, KAFKA_TOPIC, S3_ENDPOINT_INTERNAL, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET, MODEL_NAME, MODEL_PRETRAINED. Frontend reads NEXT_PUBLIC_API_URL at build time. Processor listens on 0.0.0.0:8000 and exposes GET /health/ready plus POST /embed/text with {text} -> {embedding,model_version}.

## HTTP

All errors: {"error":{"code":"...","message":"..."}}. Upload POST /api/v1/videos/upload-url accepts filename, content_type=video/mp4, size_bytes in 1..104857600; returns 201 {video_id,upload_url,object_key}. PUT the file using Content-Type: video/mp4. POST /videos/{id}/complete has no required body; 200 {video_id,status}. List returns {videos:[{id,filename,status,duration_seconds,processing_error,created_at}]}; detail returns one Video. Retry accepts only failed videos; returns {video_id,status:"queued"}, otherwise 409. Playback requires ready and returns {url,expires_in_seconds:900}. Search accepts {query,limit?:1..30,video_id?:UUID|null}, default limit 12, trimmed query 1..500 Unicode characters. Returns {query,results:[{video_id,frame_id,filename,timestamp_ms,thumbnail_url,similarity}]}. Similarity is raw 1-cosine distance. Search excludes incomplete videos and other model versions.

## Jobs and recovery

The transaction locks the video row, inserts a queued processing_jobs row and updates videos.status together. A partial unique index allows at most one queued/processing job per video. Repeated complete on queued/processing/ready returns current state and does not republish. Failed complete returns 409; retry creates a new job only after failed state. Developer 2 must transition the failed job to failed in the same transaction as the failed video.

Kafka topic media.uploaded, key video_id, JSON {event_id:UUID,event_type:"media.uploaded",schema_version:1,video_id:UUID,job_id:UUID,created_at:RFC3339 UTC}. Go commits DB before synchronous Kafka publication with all acknowledgments. A publish failure returns 503 queued_publish_failed, but leaves the committed queued job. Repeated complete will not repair delivery; use make reconcile. Delivery is at least once, never exactly once. No transactional outbox.

Processor must atomically claim UPDATE processing_jobs SET status='processing',attempt_count=attempt_count+1,updated_at=now() WHERE id=$1 AND video_id=$2 AND status='queued' RETURNING id, and change the video in the same transaction. It alone writes frames and processing/ready/failed transitions. Upsert on (video_id,timestamp_ms,model_version); commit Kafka only after durable final state. Single worker replica, one job at a time. Job/video updates must take the video row lock first, matching Go lock order.

make reconcile republishes queued jobs while the processor may run. Crash recovery is explicit: make reconcile-stale stops the processor, invokes reconciliation with --include-stale --processor-stopped, then starts it again. Stale means older than RECONCILE_STALE_AFTER (15m default). This precaution prevents reclaiming a slow live CPU job without a lease. Stale reset changes the existing job back to queued without creating a second active job. Frame upserts make reprocessing safe. Do not run recovery with another external processor instance alive.

## Storage and migrations

`db/migrations/001_initial.sql` is transactional, non-destructive and rerunnable for the initial schema. Compose runs a dedicated psql migration service on every startup; PostgreSQL persists in a volume. Future schema changes must be new migrations, not silent alterations to 001. HNSW cosine index is provided; PostgreSQL may use exact distance on this tiny dataset. Parameterized query orders by embedding <=> vector and frame UUID to break ties.

No authentication; bind published ports to loopback. This is a local demo, not a public deployment. Processor and frontend are not implemented by Developer 1; full Compose build requires Developer 2's Dockerfiles. Model cache persists in a named volume; first load downloads genuine checkpoint weights and may take time.
