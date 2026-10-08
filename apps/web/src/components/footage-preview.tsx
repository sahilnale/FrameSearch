"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Check, ImageOff, Play } from "lucide-react";
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
  const [previewing, setPreviewing] = useState(false);
  const media = useRef<HTMLVideoElement>(null);
  const posterTime = useRef(0);

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
      onMouseEnter={() => {
        if (
          ready &&
          !failed &&
          window.matchMedia(
            "(hover: hover) and (prefers-reduced-motion: no-preference)",
          ).matches
        ) {
          void media.current?.play().catch(() => {
            // A blocked preview leaves the still frame and search action available.
          });
        }
      }}
      onMouseLeave={() => {
        if (!ready || !media.current) return;
        media.current.pause();
        try {
          media.current.currentTime = posterTime.current;
        } catch {
          // A media failure already has its own fallback; leaving still stops playback.
        }
      }}
    >
      <span className="footage-image">
        {source && !failed && (
          <video
            ref={media}
            src={source}
            muted
            playsInline
            preload="metadata"
            aria-hidden="true"
            tabIndex={-1}
            onLoadedMetadata={(event) => {
              // Use actual footage from the middle of the clip as the still preview.
              const player = event.currentTarget;
              if (Number.isFinite(player.duration) && player.duration > 0) {
                posterTime.current = player.duration / 2;
                player.currentTime = posterTime.current;
              }
            }}
            onLoadedData={() => setReady(true)}
            onError={() => setFailed(true)}
            onPlay={() => setPreviewing(true)}
            onPause={() => setPreviewing(false)}
            onEnded={() => setPreviewing(false)}
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
        {previewing && (
          <span className="footage-preview-label" aria-hidden="true">
            <Play size={11} fill="currentColor" /> Previewing
          </span>
        )}
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
