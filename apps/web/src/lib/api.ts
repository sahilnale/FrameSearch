import type { PlaybackTicket, SearchResult, UploadTicket, Video } from "./contracts";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const deadline = AbortSignal.timeout(30_000);
  const signal = options.signal ? AbortSignal.any([options.signal, deadline]) : deadline;
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      signal,
      cache: "no-store",
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError(deadline.aborted ? "The request took too long. Please try again." : "Cannot reach FrameSearch. Check that the local API is running.", "connection_failed", 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(body?.error?.message || `The request failed (${response.status}). Please try again.`, body?.error?.code || "request_failed", response.status);
  }
  if (body === null) throw new ApiError("The API returned an unreadable response.", "invalid_response", response.status);
  return body as T;
}

export const api = {
  list: (signal?: AbortSignal) => request<{ videos: Video[] }>("/api/v1/videos", { signal }),
  detail: (id: string, signal?: AbortSignal) => request<Video>(`/api/v1/videos/${id}`, { signal }),
  ready: (signal?: AbortSignal) => request<{ status: string }>("/health/ready", { signal }),
  upload: (file: File, signal?: AbortSignal) => request<UploadTicket>("/api/v1/videos/upload-url", { method: "POST", signal, body: JSON.stringify({ filename: file.name, content_type: "video/mp4", size_bytes: file.size }) }),
  complete: (id: string) => request<{ video_id: string; status: Video["status"] }>(`/api/v1/videos/${id}/complete`, { method: "POST" }),
  retry: (id: string) => request<{ video_id: string; status: "queued" }>(`/api/v1/videos/${id}/retry`, { method: "POST" }),
  search: (query: string, videoId: string | null, signal?: AbortSignal) => request<{ query: string; results: SearchResult[] }>("/api/v1/search", { method: "POST", signal, body: JSON.stringify({ query, limit: 12, video_id: videoId }) }),
  playback: (id: string, signal?: AbortSignal) => request<PlaybackTicket>(`/api/v1/videos/${id}/playback-url`, { signal }),
};

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}
