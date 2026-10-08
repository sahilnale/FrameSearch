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
  api: { retry: vi.fn(), complete: vi.fn() },
  errorMessage: (error: Error) => error.message,
}));

beforeEach(() => {
  vi.clearAllMocks();
  workspace.videos = [];
  workspace.error = null;
  workspace.loading = false;
});

it("shows an empty library as zero videos, without a loading indicator or error", () => {
  render(<LibraryPage />);
  expect(screen.getByRole("heading", { name: "All videos 0" })).toBeTruthy();
  expect(screen.getByText("A blank reel. Endless possibilities.")).toBeTruthy();
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
  expect(screen.queryByText("A blank reel. Endless possibilities.")).toBeNull();
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
