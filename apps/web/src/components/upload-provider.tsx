"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { UploadTicket } from "@/lib/contracts";
import { putVideo, validateVideo } from "@/lib/upload";
import { useWorkspace } from "./workspace";

type Phase = "idle" | "preparing" | "uploading" | "completing" | "done" | "error";
type UploadState = { file: File | null; phase: Phase; progress: number; error: string | null; choose: (file: File) => void; start: () => Promise<void>; clear: () => void; cancel: () => void; busy: boolean };
const UploadContext = createContext<UploadState | null>(null);

export function UploadProvider({ children }: { children: React.ReactNode }) {
  const { refresh } = useWorkspace();
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const ticket = useRef<UploadTicket | null>(null);
  const transferred = useRef(false);
  const lock = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const busy = phase === "preparing" || phase === "uploading" || phase === "completing";

  useEffect(() => {
    if (!busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  function clear() {
    if (lock.current) return;
    ticket.current = null; transferred.current = false;
    setFile(null); setPhase("idle"); setProgress(0); setError(null);
  }
  function choose(next: File) {
    if (lock.current) return;
    clear();
    const invalid = validateVideo(next);
    if (invalid) { setError(invalid); return; }
    setFile(next);
  }
  async function start() {
    if (!file || lock.current || phase === "done") return;
    lock.current = true;
    setError(null);
    const abort = new AbortController(); controller.current = abort;
    try {
      if (!ticket.current) { setPhase("preparing"); ticket.current = await api.upload(file, abort.signal); }
      if (!transferred.current) {
        setPhase("uploading"); setProgress(0);
        await putVideo(ticket.current.upload_url, file, setProgress, abort.signal);
        transferred.current = true;
      }
      setPhase("completing");
      await api.complete(ticket.current.video_id);
      setPhase("done");
    } catch (error) { setError(errorMessage(error)); setPhase("error"); }
    finally { lock.current = false; controller.current = null; await refresh(); }
  }
  return <UploadContext.Provider value={{ file, phase, progress, error, choose, start, clear, busy, cancel: () => controller.current?.abort() }}>{children}</UploadContext.Provider>;
}

export function useUpload() {
  const state = useContext(UploadContext);
  if (!state) throw new Error("UploadProvider is required");
  return state;
}
