"use client";

import Link from "next/link";
import { useState } from "react";
import {
  ArrowUpRight,
  Check,
  Clock3,
  FileVideo,
  Play,
  RotateCcw,
  Search,
  TriangleAlert,
} from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { Video, VideoStatus } from "@/lib/contracts";
import { formatTime } from "@/lib/upload";
import { useWorkspace } from "./workspace";

const labels: Record<VideoStatus, string> = {
  awaiting_upload: "Awaiting upload",
  queued: "Queued",
  processing: "Indexing",
  ready: "Ready to search",
  failed: "Indexing failed",
};
const hints: Record<VideoStatus, string> = {
  awaiting_upload: "File transfer is not complete.",
  queued: "Waiting for indexing. Playback is available when indexing finishes.",
  processing: "Preparing frames. Playback is available when indexing finishes.",
  ready: "Click to watch video.",
  failed: "This video could not be indexed.",
};

export function VideoRow({
  video,
  onPlay,
}: {
  video: Video;
  onPlay: () => void;
}) {
  const { refresh } = useWorkspace();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function retry() {
    setBusy(true);
    setError(null);
    try {
      await api.retry(video.id);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      await refresh();
      setBusy(false);
    }
  }
  async function complete() {
    setBusy(true);
    setError(null);
    try {
      await api.complete(video.id);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      await refresh();
      setBusy(false);
    }
  }
  return (
    <article className={`library-video ${video.status}`}>
      <div className="video-row">
        {video.status === "ready" ? (
          <button
            className="video-open"
            aria-label={`Play ${video.filename}`}
            onClick={onPlay}
          >
            <span className="video-symbol video-symbol-play" aria-hidden="true">
              <Play size={23} strokeWidth={1.5} />
              <span>Play</span>
            </span>
            <span className="video-info">
              <span className="video-filename" title={video.filename}>
                {video.filename}
              </span>
              <span className="video-description">
                {video.duration_seconds !== null
                  ? `${formatTime(video.duration_seconds * 1000)} · `
                  : ""}
                {hints[video.status]}
              </span>
            </span>
          </button>
        ) : (
          <>
            <div className="video-symbol">
              <FileVideo size={25} strokeWidth={1.1} />
              <span>MP4</span>
            </div>
            <div className="video-info">
              <h3 title={video.filename}>{video.filename}</h3>
              <p>
                {video.duration_seconds !== null
                  ? `${formatTime(video.duration_seconds * 1000)} · `
                  : ""}
                {hints[video.status]}
              </p>
            </div>
          </>
        )}
        <span className={`status-badge ${video.status}`}>
          {video.status === "processing" ? (
            <span className="spinner" />
          ) : video.status === "ready" ? (
            <Check size={11} />
          ) : video.status === "failed" ? (
            <TriangleAlert size={11} />
          ) : (
            <Clock3 size={11} />
          )}{" "}
          {labels[video.status]}
        </span>
        <div className="video-actions">
          {video.status === "ready" ? (
            <Link
              href={`/?video=${video.id}`}
              className="text-button"
              aria-label={`Search ${video.filename}`}
            >
              <Search size={15} />
              Search <ArrowUpRight size={13} />
            </Link>
          ) : video.status === "failed" ? (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void retry()}
            >
              {busy ? <span className="spinner" /> : <RotateCcw size={14} />}{" "}
              Retry indexing
            </button>
          ) : video.status === "awaiting_upload" ? (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void complete()}
            >
              Finish upload <ArrowUpRight size={13} />
            </button>
          ) : null}
        </div>
      </div>
      {(video.processing_error || error) && (
        <p className="video-error" role="alert">
          {error || video.processing_error}
        </p>
      )}
    </article>
  );
}
