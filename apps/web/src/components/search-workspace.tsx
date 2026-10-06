"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  ImageOff,
  Scan,
  Search,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { SearchResult } from "@/lib/contracts";
import { formatTime } from "@/lib/upload";
import { FrameArt } from "./frame-art";
import { useWorkspace } from "./workspace";
import { PlaybackOverlay } from "./playback-overlay";

const suggestions = [
  "A dog playing indoors",
  "Water flowing through a park",
  "A train at a station",
];

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
      <div className="eyebrow">
        <span /> A DIFFERENT WAY TO FIND
      </div>
      <section className="intro">
        <div>
          <h1>
            You remember the moment.
            <br />
            <span>We find the frame.</span>
          </h1>
          <p>
            Turn a thought into a timestamp. Search your footage in your own
            words.
          </p>
        </div>
        <div className="intro-mark">
          <Scan size={36} strokeWidth={1} />
          <span>
            VISUAL
            <br />
            INTELLIGENCE
          </span>
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
        <div className="search-field">
          <Search size={21} strokeWidth={1.5} />
          <input
            ref={input}
            aria-label="Describe a visual moment"
            placeholder="Describe a moment. A place. Something you saw…"
            value={query}
            maxLength={500}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit" className="button" disabled={!query.trim()}>
            {loading ? <span className="spinner" /> : null}
            <span>Find frames</span>
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
              <option value="">All indexed videos</option>
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
              Describe visible objects, places, colors, or activity. Results
              match visual frames; dialogue and story events are outside this
              search.
            </p>
          </details>
        </div>
      </form>
      {!submitted && (
        <div className="suggestions">
          <span>TRY A VISUAL DESCRIPTION</span>
          {suggestions.map((text) => (
            <button
              key={text}
              onClick={() => {
                setQuery(text);
                void search(text);
              }}
            >
              <Sparkles size={11} />
              {text}
              <ArrowUpRight size={11} />
            </button>
          ))}
        </div>
      )}
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
                  ? "Finding your moments…"
                  : error
                    ? "Search interrupted"
                    : `${results.length} ${results.length === 1 ? "moment" : "moments"} found`}{" "}
                {!loading && !error && (
                  <span className="count-pill">{results.length}</span>
                )}
              </h2>
              <p className="result-query">For “{submitted}”</p>
            </div>
            <span>RANKED BY VISUAL SIMILARITY</span>
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
              <h2>No frames found yet.</h2>
              <p>
                {ready.length === 0
                  ? "Upload a video and wait for indexing to finish, then search again."
                  : "Try another description, or search across all your indexed videos."}
              </p>
              {ready.length === 0 && (
                <Link
                  href="/library?upload=1"
                  className="button button-secondary"
                >
                  Add a video <ArrowRight size={15} />
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
                <span>
                  Real frames from your videos. Scores are raw cosine
                  similarity, not confidence percentages.
                </span>
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
      ) : (
        <section className="welcome-card search-welcome">
          <div className="welcome-copy">
            <span className="eyebrow muted">
              {ready.length
                ? "YOUR FOOTAGE HAS SOMETHING TO SHOW YOU"
                : "YOUR NEXT DISCOVERY STARTS HERE"}
            </span>
            <h2>
              {ready.length ? (
                <>
                  A few words.
                  <br />A whole new perspective.
                </>
              ) : (
                <>
                  A whole new way
                  <br />
                  to see your footage.
                </>
              )}
            </h2>
            <p>
              {ready.length
                ? "Describe a visual moment above. We’ll bring you the frames that look like it."
                : "Add your first video, describe a visual moment, and go straight to the matching frame."}
            </p>
            {ready.length ? (
              <button
                className="button button-secondary"
                onClick={() => input.current?.focus()}
              >
                Start exploring <ArrowRight size={17} />
              </button>
            ) : (
              <Link href="/library?upload=1" className="button">
                Upload your first video <ArrowRight size={17} />
              </Link>
            )}
            <span className="file-note">
              {ready.length
                ? "Objects. Places. Colors. Moments."
                : "MP4 · Up to 100 MB · 3 minutes"}
            </span>
          </div>
          <FrameArt />
        </section>
      )}
      {!submitted && (
        <div className="search-explainer">
          <div>
            <Scan size={16} />
            <span>Search what’s visible.</span>
          </div>
          <p>
            Describe the frame you’re looking for. We’ll point you to the
            moment.
          </p>
          <span>YOUR WORDS → YOUR FOOTAGE</span>
        </div>
      )}
      {selected && (
        <PlaybackOverlay
          key={selected.frame_id}
          result={selected}
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
            <ArrowUpRight size={21} />
          </span>
        )}
      </div>
      <div className="result-info">
        <h3 title={result.filename}>{result.filename}</h3>
        <div>
          <span className="frame-label">MATCHING FRAME</span>
          <span
            className="cosine-score"
            title="Raw cosine similarity; not a confidence percentage"
          >
            COSINE <strong>{result.similarity.toFixed(3)}</strong>
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
