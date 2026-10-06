"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, Film, RotateCcw, X } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { SearchResult } from "@/lib/contracts";
import { formatTime } from "@/lib/upload";

export function PlaybackOverlay({
  result,
  onClose,
}: {
  result: SearchResult;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const request = useRef<AbortController | null>(null);
  const target = useRef(result.timestamp_ms / 1000);
  const loaded = useRef(false);
  const expires = useRef(0);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [time, setTime] = useState<number | null>(null);

  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    try {
      const signed = await api.playback(result.video_id, controller.signal);
      if (!controller.signal.aborted) {
        expires.current = Date.now() + signed.expires_in_seconds * 1000;
        setUrl(signed.url);
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setError(errorMessage(error));
        setLoading(false);
      }
    }
  }, [result.video_id]);

  function refresh() {
    loaded.current = false;
    setLoading(true);
    setError(null);
    setUrl(null);
    void load();
  }

  useEffect(() => {
    const focused = document.activeElement as HTMLElement | null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.showModal();
    // The loader updates state only after the external HTTP request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    return () => {
      request.current?.abort();
      document.body.style.overflow = oldOverflow;
      focused?.focus();
    };
  }, [load]);

  useEffect(() => {
    if (!url) return;
    const timer = setTimeout(() => {
      if (!loaded.current) {
        setLoading(false);
        setError(
          "This video is taking too long to load. Refresh playback or check the storage service.",
        );
      }
    }, 20_000);
    return () => clearTimeout(timer);
  }, [url]);

  function seek() {
    const player = video.current;
    if (!player) return;
    if (
      !Number.isFinite(player.duration) ||
      target.current < 0 ||
      target.current > player.duration
    ) {
      setLoading(false);
      setError("This matching timestamp is outside the playable video.");
      return;
    }
    try {
      player.currentTime = target.current;
      loaded.current = true;
      setTime(player.currentTime);
      setLoading(false);
      void player.play().catch(() => {
        /* Native play controls remain available if autoplay is blocked. */
      });
    } catch {
      setLoading(false);
      setError("Could not seek to this frame. Refresh playback and try again.");
    }
  }

  return (
    <dialog
      ref={dialog}
      className="playback-dialog"
      aria-labelledby="playback-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="playback-panel">
        <header className="player-header">
          <div>
            <div className="eyebrow">
              <span /> A MOMENT, FOUND
            </div>
            <h2 id="playback-title">{result.filename}</h2>
          </div>
          <button
            autoFocus
            className="icon-button"
            aria-label="Close playback"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>
        <div className="player-stage">
          {url && (
            <video
              ref={video}
              src={url}
              controls
              preload="metadata"
              crossOrigin="anonymous"
              playsInline
              aria-label={`${result.filename} video player`}
              onLoadedMetadata={seek}
              onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
              onError={() => {
                setLoading(false);
                setError(
                  "Playback could not load. The signed link may have expired, or storage is unavailable. Refresh playback to get a new link.",
                );
              }}
              onPlay={() => {
                if (Date.now() >= expires.current) {
                  target.current = video.current?.currentTime ?? target.current;
                  video.current?.pause();
                  void refresh();
                }
              }}
            />
          )}{" "}
          {loading && (
            <div className="player-loading" role="status">
              <span className="spinner" />
              <span>
                {url
                  ? `Jumping to ${formatTime(result.timestamp_ms)}…`
                  : "Preparing your moment…"}
              </span>
            </div>
          )}
          {error && (
            <div className="player-error" role="alert">
              <Film size={28} strokeWidth={1} />
              <p>{error}</p>
              <button
                className="button button-secondary"
                onClick={() => {
                  target.current =
                    video.current?.currentTime || result.timestamp_ms / 1000;
                  void refresh();
                }}
              >
                <RotateCcw size={14} />
                Refresh playback
              </button>
            </div>
          )}
        </div>
        <footer className="player-footer">
          <div>
            <span className="player-time">
              {time === null ? "--:--" : formatTime(time * 1000)}
            </span>
            <span className="player-found">
              MATCHING FRAME <strong>{formatTime(result.timestamp_ms)}</strong>
            </span>
          </div>
          <button
            className="text-button"
            disabled={!url || loading || !!error}
            onClick={() => {
              target.current = result.timestamp_ms / 1000;
              seek();
            }}
          >
            Back to matching frame <ArrowUpRight size={14} />
          </button>
        </footer>
      </div>
    </dialog>
  );
}
