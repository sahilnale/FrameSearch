"use client";

import { ArrowUpRight, Film, RefreshCw } from "lucide-react";
import { useWorkspace } from "@/components/workspace";
import { UploadPanel } from "@/components/upload-panel";
import { VideoRow } from "@/components/video-row";

export default function LibraryPage() {
  const { videos, loading, error, refresh } = useWorkspace();
  return (
    <div className="page-content">
      <section className="intro">
        <div>
          <h1>Video library</h1>
          <p>Upload videos and track when they’re ready to search.</p>
        </div>
      </section>
      <UploadPanel />
      <div className="section-heading">
        <h2>
          All videos{" "}
          {!loading && !error && (
            <span className="count-pill">{videos.length}</span>
          )}
        </h2>
        <button className="text-button" onClick={() => void refresh()}>
          <RefreshCw size={15} /> Refresh
        </button>
      </div>
      {error && (
        <div className="notice error" role="alert">
          <p>{error}</p>
          <button
            onClick={() => void refresh()}
            className="button button-secondary"
          >
            Try again <ArrowUpRight size={15} />
          </button>
        </div>
      )}
      {loading ? (
        <div className="empty-panel">
          <span className="spinner" />
          Loading your library…
        </div>
      ) : videos.length === 0 && !error ? (
        <div className="empty-panel">
          <Film size={32} strokeWidth={1} />
          <h2>No videos yet</h2>
          <p>Upload an MP4 above to start building your library.</p>
        </div>
      ) : videos.length > 0 ? (
        <div className="video-list">
          {videos.map((video) => (
            <VideoRow key={video.id} video={video} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
