"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  ImageOff,
  Play,
  Scan,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { SearchResult } from "@/lib/contracts";
import { formatTime } from "@/lib/upload";
import { useWorkspace } from "./workspace";
import { PlaybackOverlay } from "./playback-overlay";
import { FootagePreview } from "./footage-preview";

export function SearchWorkspace() {
  const parameters = useSearchParams();
  const initialVideo = parameters.get("video") || "";
  return <SearchExperience key={initialVideo} initialVideo={initialVideo} />;
}

export function SearchExperience({
  initialVideo = "",
}: {
  initialVideo?: string;
}) {
  const {
    videos,
    loading: libraryLoading,
    error: libraryError,
    refresh: refreshLibrary,
  } = useWorkspace();
  const ready = videos.filter((video) => video.status === "ready");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState(initialVideo);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshed, setRefreshed] = useState(0);
  const [selected, setSelected] = useState<SearchResult | null>(null);
  const request = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => () => request.current?.abort(), []);

  async function search(text: string, videoId = scope) {
    const trimmed = text.trim();
    if (!trimmed) {
      input.current?.focus();
      return;
    }
    if ([...trimmed].length > 500) {
      setError("Use up to 500 characters to describe a visual moment.");
      return;
    }
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(null);
    setResults([]);
    setSubmitted(trimmed);
    setSelected(null);
    try {
      const response = await api.search(
        trimmed,
        videoId || null,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setResults(response.results);
        setRefreshed((value) => value + 1);
      }
    } catch (error) {
      if (!controller.signal.aborted) setError(errorMessage(error));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  return (
    <div className="page-content search-page">
      <div className={`search-studio ${submitted ? "has-results" : ""}`}>
        <section className="intro">
          <div>
            <div className="workspace-label">Visual search</div>
            <h1>
              Search your <span>videos.</span>
            </h1>
            <p>
              Describe what you’re looking for and jump to the matching frame.
            </p>
            {!submitted && (
              <div
                className="search-examples"
                aria-label="Example descriptions"
              >
                {[
                  { label: "Dogs playing", query: "A dog playing" },
                  { label: "City at night", query: "A busy city at night" },
                  { label: "Ocean waves", query: "Waves on a beach" },
                ].map((example) => (
                  <button
                    key={example.query}
                    type="button"
                    onClick={() => {
                      setQuery(example.query);
                      input.current?.focus();
                    }}
                  >
                    {example.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </section>
        <form
          className="search-form"
          role="search"
          onSubmit={(event) => {
            event.preventDefault();
            void search(query);
          }}
        >
          <label className="query-label" htmlFor="visual-query">
            What are you looking for?
          </label>
          <div className="search-field">
            <Search size={21} strokeWidth={1.5} />
            <input
              id="visual-query"
              ref={input}
              aria-label="Describe a visual moment"
              placeholder="Describe a scene, object, or activity…"
              value={query}
              maxLength={500}
              onChange={(event) => setQuery(event.target.value)}
            />
            <button
              type="submit"
              className="button"
              aria-label="Search"
              disabled={!query.trim()}
            >
              {loading ? <span className="spinner" /> : null}
              <span>Search</span>
              <ArrowRight size={16} />
            </button>
          </div>
          <div className="search-options">
            <label className="scope-select">
              <SlidersHorizontal size={13} />
              <select
                aria-label="Search videos"
                value={scope}
                onChange={(event) => {
                  const value = event.target.value;
                  setScope(value);
                  if (submitted) void search(submitted, value);
                }}
              >
                <option value="">All videos</option>
                {initialVideo &&
                  !ready.some((video) => video.id === initialVideo) && (
                    <option value={initialVideo}>Selected video</option>
                  )}
                {ready.map((video) => (
                  <option key={video.id} value={video.id}>
                    {video.filename}
                  </option>
                ))}
              </select>
            </label>
            <div className="search-hint">
              <span className="status-dot" />
              {libraryError
                ? "Library unavailable"
                : libraryLoading
                  ? "Loading your library"
                  : `${ready.length} ${ready.length === 1 ? "video" : "videos"} ready to search`}
            </div>
            <details className="search-help">
              <summary>Search tips</summary>
              <p>
                Describe visible objects, actions, or settings. Search matches
                visual content; speech and dialogue aren’t indexed.
              </p>
            </details>
          </div>
        </form>
      </div>
      {submitted ? (
        <section
          className="results-section"
          aria-label="Search results"
          aria-busy={loading}
        >
          <div className="section-heading">
            <div>
              <h2>
                {loading
                  ? "Searching…"
                  : error
                    ? "Search interrupted"
                    : `${results.length} matching ${results.length === 1 ? "frame" : "frames"}`}
              </h2>
              <p className="result-query">For “{submitted}”</p>
            </div>
            <span>Most similar first</span>
          </div>
          {error ? (
            <div className="notice error" role="alert">
              <p>{error}</p>
              <button
                className="button button-secondary"
                onClick={() => void search(submitted)}
              >
                Try search again <ArrowUpRight size={15} />
              </button>
            </div>
          ) : loading ? (
            <div className="results-grid" aria-label="Loading matching frames">
              {[0, 1, 2].map((number) => (
                <div key={number} className="result-skeleton">
                  <div />
                  <span />
                  <span />
                </div>
              ))}
            </div>
          ) : results.length === 0 ? (
            <div className="empty-panel">
              <Search size={30} strokeWidth={1} />
              <h2>No matching frames</h2>
              <p>
                {libraryError
                  ? "Your library could not be loaded. Check the connection in Library."
                  : ready.length === 0
                    ? "Check your library for upload and indexing status, then search again."
                    : "Try another description, or search across all your indexed videos."}
              </p>
              {ready.length === 0 && !libraryError && !libraryLoading && (
                <Link
                  href={videos.length ? "/library" : "/library?upload=1"}
                  className="button button-secondary"
                >
                  {videos.length ? "Open library" : "Add a video"}{" "}
                  <ArrowRight size={15} />
                </Link>
              )}
            </div>
          ) : (
            <>
              <div className="results-grid">
                {results.map((result, index) => (
                  <ResultCard
                    key={`${refreshed}-${result.frame_id}`}
                    result={result}
                    index={index}
                    onSelect={() => setSelected(result)}
                  />
                ))}
              </div>
              <div className="result-footer">
                <Scan size={13} />
                <span>Select a frame to play the video at that timestamp.</span>
                <button
                  className="text-button"
                  onClick={() => void search(submitted)}
                >
                  Refresh results
                </button>
              </div>
            </>
          )}
        </section>
      ) : libraryError ? (
        <div className="notice error search-notice" role="alert">
          <p>{libraryError}</p>
          <button
            className="button button-secondary"
            onClick={() => void refreshLibrary()}
          >
            <ArrowUpRight size={15} /> Reload library
          </button>
        </div>
      ) : libraryLoading ? (
        <div className="empty-panel search-start" role="status">
          <span className="spinner" /> Loading your library…
        </div>
      ) : ready.length ? (
        <section className="footage-section" aria-label="Your footage">
          <div className="section-heading">
            <div>
              <h2>Your footage</h2>
              <p className="result-query">
                Select a video to narrow your search.
              </p>
            </div>
            <Link href="/library" className="text-button">
              View library <ArrowUpRight size={15} />
            </Link>
          </div>
          <div className="footage-grid">
            {ready.slice(0, 3).map((video) => (
              <FootagePreview
                key={video.id}
                video={video}
                selected={scope === video.id}
                onSelect={() => {
                  setScope(scope === video.id ? "" : video.id);
                  input.current?.focus();
                }}
              />
            ))}
          </div>
        </section>
      ) : (
        <section className="empty-panel search-start">
          <Search size={30} strokeWidth={1.5} />
          <h2>
            {videos.length ? "No searchable videos yet" : "Start with a video"}
          </h2>
          <p>
            {videos.length
              ? "Check your library for upload and indexing status. Videos become searchable when indexing finishes."
              : "Upload a video and wait for indexing to finish before searching."}
          </p>
          <Link
            href={videos.length ? "/library" : "/library?upload=1"}
            className="button button-secondary"
          >
            {videos.length ? "Open library" : "Upload video"}{" "}
            <ArrowRight size={16} />
          </Link>
        </section>
      )}
      {selected && (
        <PlaybackOverlay
          key={selected.frame_id}
          videoId={selected.video_id}
          filename={selected.filename}
          timestampMs={selected.timestamp_ms}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

export function ResultCard({
  result,
  index,
  onSelect,
}: {
  result: SearchResult;
  index: number;
  onSelect?: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const content = (
    <>
      <div className="result-image">
        {failed ? (
          <div className="thumbnail-failed">
            <ImageOff size={25} />
            <span>Preview unavailable. Refresh results.</span>
          </div>
        ) : (
          // The API owns these short-lived signed URLs; fetch directly, without an image proxy/cache.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={result.thumbnail_url}
            alt={`Frame from ${result.filename} at ${formatTime(result.timestamp_ms)}`}
            loading="lazy"
            onError={() => setFailed(true)}
          />
        )}
        <span className="frame-rank">{String(index + 1).padStart(2, "0")}</span>
        <span className="timestamp">{formatTime(result.timestamp_ms)}</span>
        {onSelect && (
          <span className="frame-play">
            <Play size={19} />
          </span>
        )}
      </div>
      <div className="result-info">
        <h3 title={result.filename}>{result.filename}</h3>
        <div>
          <span className="frame-label">
            {onSelect ? "Play video" : "Matching frame"}
          </span>
          <span
            className="cosine-score"
            title="Raw cosine similarity; not a confidence percentage"
          >
            Similarity <strong>{result.similarity.toFixed(3)}</strong>
          </span>
        </div>
      </div>
    </>
  );
  return onSelect ? (
    <button
      className="result-card"
      onClick={onSelect}
      aria-label={`Play ${result.filename} at ${formatTime(result.timestamp_ms)}`}
    >
      {content}
    </button>
  ) : (
    <article className="result-card">{content}</article>
  );
}
