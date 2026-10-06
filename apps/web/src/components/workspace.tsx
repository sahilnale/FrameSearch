"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { api, errorMessage } from "@/lib/api";
import type { Video } from "@/lib/contracts";

type LibraryState = {
  videos: Video[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  online: boolean | null;
};
const WorkspaceContext = createContext<LibraryState | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const refresh = useCallback(async () => {
    try {
      const data = await api.list();
      setVideos(data.videos);
      setError(null);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function poll() {
      try {
        const data = await api.list(controller.signal);
        if (active) {
          setVideos(data.videos);
          setError(null);
        }
      } catch (error) {
        if (active) setError(errorMessage(error));
      } finally {
        if (active) setLoading(false);
      }
    }
    async function health() {
      try {
        await api.ready(controller.signal);
        if (active) setOnline(true);
      } catch {
        if (active) setOnline(false);
      }
    }
    void poll();
    void health();
    const polling = setInterval(() => {
      if (!document.hidden) void poll();
    }, 5_000);
    const readiness = setInterval(() => {
      if (!document.hidden) void health();
    }, 15_000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(polling);
      clearInterval(readiness);
    };
  }, []);

  return (
    <WorkspaceContext.Provider
      value={{ videos, loading, error, refresh, online }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const state = useContext(WorkspaceContext);
  if (!state) throw new Error("WorkspaceProvider is required");
  return state;
}
