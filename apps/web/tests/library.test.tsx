import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import LibraryPage from "@/app/library/page";
import type { Video } from "@/lib/contracts";
import { api } from "@/lib/api";

const workspace = vi.hoisted(() => ({
  videos: [] as Video[],
  loading: false,
  error: null as string | null,
  refresh: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@/components/workspace", () => ({ useWorkspace: () => workspace }));
vi.mock("@/components/upload-panel", () => ({ UploadPanel: () => null }));
vi.mock("@/lib/api", () => ({
  api: { retry: vi.fn(), complete: vi.fn(), playback: vi.fn() },
  errorMessage: (error: Error) => error.message,
}));

beforeEach(() => {
  vi.clearAllMocks();
  workspace.videos = [];
  workspace.error = null;
  workspace.loading = false;
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.mocked(api.playback).mockResolvedValue({
    url: "http://storage/signed-library-video",
    expires_in_seconds: 900,
  });
});

const uploaded: Video = {
  id: "uploaded-id",
  filename: "uploaded.mp4",
  status: "ready",
  duration_seconds: 18,
  processing_error: null,
  created_at: "2026-10-07T00:00:00Z",
};

it("shows an empty library as zero videos, without a loading indicator or error", () => {
  render(<LibraryPage />);
  expect(screen.getByRole("heading", { name: "All videos 0" })).toBeTruthy();
  expect(screen.getByText("No videos yet")).toBeTruthy();
  expect(screen.queryByText("Loading your library…")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("distinguishes an unreachable API from a verified empty library", async () => {
  workspace.error =
    "Cannot reach FrameSearch. Check that the local API is running.";
  render(<LibraryPage />);
  expect(screen.getByRole("alert").textContent).toContain(
    "Cannot reach FrameSearch",
  );
  expect(screen.queryByText("No videos yet")).toBeNull();
  expect(screen.queryByText("Loading your library…")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(workspace.refresh).toHaveBeenCalledTimes(1));
});

it("retries the existing failed video ID and refreshes its status", async () => {
  workspace.videos = [
    {
      id: "56fb63c9-8f08-4fd1-8e14-136d6960105f",
      filename: "failed.mp4",
      status: "failed",
      duration_seconds: null,
      processing_error: "The video could not be decoded.",
      created_at: "2026-10-07T00:00:00Z",
    },
  ];
  vi.mocked(api.retry).mockResolvedValue({
    video_id: workspace.videos[0].id,
    status: "queued",
  });
  render(<LibraryPage />);
  expect(screen.getByRole("alert").textContent).toContain(
    "could not be decoded",
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry indexing" }));
  await waitFor(() => expect(workspace.refresh).toHaveBeenCalledTimes(1));
  expect(api.retry).toHaveBeenCalledExactlyOnceWith(workspace.videos[0].id);
});

it("opens the original from its library row at zero and restores focus on close", async () => {
  workspace.videos = [uploaded];
  render(<LibraryPage />);
  const trigger = screen.getByRole("button", { name: "Play uploaded.mp4" });
  trigger.focus();
  fireEvent.click(trigger);
  const player = (await screen.findByLabelText(
    "uploaded.mp4 video player",
  )) as HTMLVideoElement;
  expect(api.playback).toHaveBeenCalledWith(
    "uploaded-id",
    expect.any(AbortSignal),
  );
  expect(player.getAttribute("src")).toBe(
    "http://storage/signed-library-video",
  );
  Object.defineProperty(player, "duration", { value: 18 });
  fireEvent.loadedMetadata(player);
  expect(player.currentTime).toBe(0);
  expect(player.play).toHaveBeenCalledOnce();
  expect(screen.queryByText("Matching frame")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close playback" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(
    screen
      .getByRole("link", { name: "Search uploaded.mp4" })
      .getAttribute("href"),
  ).toBe("/?video=uploaded-id");
});

it("keeps indexing videos unplayable until the shared API permits playback", () => {
  workspace.videos = [
    { ...uploaded, id: "queued", status: "queued" },
    { ...uploaded, id: "processing", status: "processing" },
  ];
  render(<LibraryPage />);
  expect(
    screen.queryByRole("button", { name: "Play uploaded.mp4" }),
  ).toBeNull();
  expect(
    screen.getAllByText(/Playback is available when indexing finishes/),
  ).toHaveLength(2);
  expect(api.playback).not.toHaveBeenCalled();
});

it("shows a playback error in the library player and can fetch a fresh link", async () => {
  workspace.videos = [uploaded];
  vi.mocked(api.playback).mockRejectedValueOnce(
    new Error("Storage unavailable"),
  );
  render(<LibraryPage />);
  fireEvent.click(screen.getByRole("button", { name: "Play uploaded.mp4" }));
  await screen.findByText("Storage unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Refresh playback" }));
  await screen.findByLabelText("uploaded.mp4 video player");
  expect(api.playback).toHaveBeenCalledTimes(2);
});
