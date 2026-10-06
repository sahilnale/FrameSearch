export type VideoStatus =
  "awaiting_upload" | "queued" | "processing" | "ready" | "failed";

export type Video = {
  id: string;
  filename: string;
  status: VideoStatus;
  duration_seconds: number | null;
  processing_error: string | null;
  created_at: string;
};

export type SearchResult = {
  video_id: string;
  frame_id: string;
  filename: string;
  timestamp_ms: number;
  thumbnail_url: string;
  similarity: number;
};

export type UploadTicket = {
  video_id: string;
  upload_url: string;
  object_key: string;
};
export type PlaybackTicket = { url: string; expires_in_seconds: number };
