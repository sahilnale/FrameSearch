"use client";

import { useRef, useState } from "react";
import { ArrowUpRight, Check, FileVideo, Upload, X } from "lucide-react";
import { useUpload } from "./upload-provider";
import { formatBytes } from "@/lib/upload";

export function UploadPanel() {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const state = useUpload();
  const { file, phase, error, busy } = state;
  const label =
    phase === "preparing"
      ? "Preparing upload…"
      : phase === "uploading"
        ? `Uploading · ${state.progress}%`
        : phase === "completing"
          ? "Starting indexing…"
          : phase === "done"
            ? "Upload complete. Check indexing status below."
            : "Ready to upload";

  return (
    <section
      className={`upload-panel ${dragging ? "dragging" : ""}`}
      aria-label="Upload video"
      onDragOver={(event) => {
        event.preventDefault();
        if (!busy) setDragging(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node))
          setDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        if (event.dataTransfer.files.length === 1)
          state.choose(event.dataTransfer.files[0]);
      }}
    >
      <input
        ref={input}
        className="sr-only"
        type="file"
        accept=".mp4,video/mp4"
        aria-label="Choose MP4 video"
        disabled={busy}
        onChange={(event) => {
          const selected = event.target.files?.[0];
          if (selected) state.choose(selected);
          event.target.value = "";
        }}
      />
      {!file ? (
        <>
          <div className="upload-icon">
            <Upload size={25} strokeWidth={1.3} />
          </div>
          <div className="upload-copy">
            <h2>Upload a video</h2>
            <p>Drag an MP4 here, or choose one from your device.</p>
            <span>MP4 · Up to 100 MB · Maximum 3 minutes</span>
          </div>
          <button onClick={() => input.current?.click()} className="button">
            Choose video <ArrowUpRight size={17} />
          </button>
        </>
      ) : (
        <>
          <div className={`upload-icon ${phase === "done" ? "done" : ""}`}>
            {phase === "done" ? <Check size={25} /> : <FileVideo size={25} />}
          </div>
          <div className="upload-copy">
            <h2 title={file.name}>{file.name}</h2>
            <p aria-live="polite">
              {busy && <span className="spinner" />}
              {label}
            </p>
            <span>
              {formatBytes(file.size)} · MP4
              {phase === "done" ? " · Status appears below" : ""}
            </span>
            {phase === "uploading" && (
              <progress
                aria-label="Upload progress"
                max={100}
                value={state.progress}
              />
            )}
          </div>
          <div className="upload-actions">
            {phase === "done" ? (
              <button className="button button-secondary" onClick={state.clear}>
                Add another video
              </button>
            ) : busy ? (
              phase !== "completing" && (
                <button className="text-button" onClick={state.cancel}>
                  Pause
                </button>
              )
            ) : (
              <>
                <button onClick={() => void state.start()} className="button">
                  {phase === "error" ? "Try upload again" : "Upload video"}
                  <ArrowUpRight size={17} />
                </button>
                <button
                  className="icon-button"
                  aria-label="Clear selected file"
                  onClick={state.clear}
                >
                  <X size={18} />
                </button>
              </>
            )}
          </div>
        </>
      )}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
