"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, Check, ImageOff } from "lucide-react";
import { api } from "@/lib/api";
import type { Video } from "@/lib/contracts";
import { formatTime } from "@/lib/upload";

export function FootagePreview({
  video,
  selected,
  onSelect,
}: {
  video: Video;
  selected: boolean;
  onSelect: () => void;
}) {
  const [source, setSource] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void api.playback(video.id, controller.signal).then(
      (ticket) => {
        if (!controller.signal.aborted) setSource(ticket.url);
      },
      () => {
        if (!controller.signal.aborted) setFailed(true);
      },
    );
    return () => controller.abort();
  }, [video.id]);

  useEffect(() => {
    if (!source || ready || failed) return;
    const deadline = window.setTimeout(() => setFailed(true), 15_000);
    return () => window.clearTimeout(deadline);
  }, [source, ready, failed]);

  return (
    <button
      className={`footage-card ${selected ? "selected" : ""}`}
      aria-label={`Search ${video.filename}`}
      aria-pressed={selected}
      onClick={onSelect}
    >
      <span className="footage-image">
        {source && !failed && (
          <video
            src={source}
            muted
            playsInline
            preload="metadata"
            aria-hidden="true"
            tabIndex={-1}
            onLoadedMetadata={(event) => {
              // Ask for a paused first frame; previews never autoplay.
              const media = event.currentTarget;
              if (Number.isFinite(media.duration) && media.duration > 0) {
                media.currentTime = Math.min(0.1, media.duration / 2);
              }
            }}
            onLoadedData={() => setReady(true)}
            onError={() => setFailed(true)}
          />
        )}
        {failed ? (
          <span className="footage-placeholder">
            <ImageOff size={24} strokeWidth={1.5} />
            Preview unavailable
          </span>
        ) : !ready ? (
          <span className="footage-placeholder" aria-hidden="true">
            <span className="spinner" />
          </span>
        ) : null}
        <span className="footage-corners" aria-hidden="true" />
        {video.duration_seconds !== null && (
          <span className="timestamp">
            {formatTime(video.duration_seconds * 1000)}
          </span>
        )}
        {selected && (
          <span className="footage-selected" aria-hidden="true">
            <Check size={13} /> Selected
          </span>
        )}
      </span>
      <span className="footage-info">
        <span>
          <span className="footage-filename" title={video.filename}>
            {video.filename}
          </span>
          <span className="footage-caption">
            {selected ? "Selected for search" : "Search this video"}
          </span>
        </span>
        <ArrowUpRight size={20} aria-hidden="true" />
      </span>
    </button>
  );
}
