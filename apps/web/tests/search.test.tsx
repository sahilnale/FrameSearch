import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { SearchExperience } from "@/components/search-workspace";
import type { Video } from "@/lib/contracts";

const workspace = vi.hoisted(() => ({
  videos: [] as Video[],
  loading: false,
  error: null as string | null,
  refresh: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/workspace", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("@/lib/api", () => ({
  api: { search: vi.fn(), playback: vi.fn() },
  errorMessage: (error: Error) => error.message,
}));
beforeEach(() => {
  vi.clearAllMocks();
  workspace.videos = [
    {
      id: "video-id",
      filename: "original.mp4",
      status: "ready",
      duration_seconds: 18,
      processing_error: null,
      created_at: "2026-10-07T00:00:00Z",
    },
  ];
  workspace.loading = false;
  workspace.error = null;
  vi.mocked(api.playback).mockResolvedValue({
    url: "http://storage/signed-video",
    expires_in_seconds: 900,
  });
});
const result = {
  video_id: "video-id",
  frame_id: "frame-id",
  filename: "original.mp4",
  timestamp_ms: 12000,
  thumbnail_url: "http://storage/signed-thumb",
  similarity: 0.314159,
};

it("displays returned timestamps and raw cosine without claiming confidence", async () => {
  vi.mocked(api.search).mockResolvedValue({
    query: "a dog",
    results: [result],
  });
  render(<SearchExperience />);
  fireEvent.change(screen.getByLabelText("Describe a visual moment"), {
    target: { value: "a dog" },
  });
  fireEvent.submit(screen.getByRole("search"));
  await screen.findByRole("heading", { name: "original.mp4" });
  expect(screen.getByText("00:12")).toBeTruthy();
  expect(screen.getByText("0.314")).toBeTruthy();
  expect(screen.queryByText("31%")).toBeNull();
  expect(api.search).toHaveBeenCalledWith(
    "a dog",
    null,
    expect.any(AbortSignal),
  );
});

it("does not let an old response replace a newer query", async () => {
  let first!: (value: { query: string; results: (typeof result)[] }) => void;
  vi.mocked(api.search)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          first = resolve;
        }),
    )
    .mockResolvedValueOnce({
      query: "train",
      results: [{ ...result, filename: "new-query.mp4" }],
    });
  render(<SearchExperience />);
  const input = screen.getByLabelText("Describe a visual moment");
  fireEvent.change(input, { target: { value: "dog" } });
  fireEvent.submit(screen.getByRole("search"));
  fireEvent.change(input, { target: { value: "train" } });
  fireEvent.submit(screen.getByRole("search"));
  await screen.findByText("new-query.mp4");
  await act(async () => first({ query: "dog", results: [result] }));
  expect(screen.queryByRole("heading", { name: "original.mp4" })).toBeNull();
  expect(screen.getByText("For “train”")).toBeTruthy();
});

it("sends the actual selected video filter and offers an error retry", async () => {
  vi.mocked(api.search)
    .mockRejectedValueOnce(new Error("processor unavailable"))
    .mockResolvedValueOnce({ query: "water", results: [] });
  render(<SearchExperience initialVideo="video-id" />);
  fireEvent.change(screen.getByLabelText("Describe a visual moment"), {
    target: { value: "water" },
  });
  fireEvent.submit(screen.getByRole("search"));
  await screen.findByText("processor unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Try search again" }));
  await screen.findByText("No matching frames");
  expect(api.search).toHaveBeenLastCalledWith(
    "water",
    "video-id",
    expect.any(AbortSignal),
  );
});

it("offers a library reload when offline instead of telling users their library is empty", async () => {
  workspace.videos = [];
  workspace.error =
    "Cannot reach FrameSearch. Check that the local API is running.";
  render(<SearchExperience />);
  expect(screen.getByRole("alert").textContent).toContain(
    "Cannot reach FrameSearch",
  );
  expect(
    screen.queryByRole("heading", { name: "Start with a video" }),
  ).toBeNull();
  expect(screen.queryByRole("link", { name: "Upload video" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reload library" }));
  await waitFor(() => expect(workspace.refresh).toHaveBeenCalledTimes(1));
});

it("points to existing indexing work instead of requesting another upload", () => {
  workspace.videos[0].status = "processing";
  render(<SearchExperience />);
  expect(
    screen.getByRole("heading", { name: "No searchable videos yet" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Open library" }).getAttribute("href"),
  ).toBe("/library");
  expect(screen.queryByRole("link", { name: "Upload video" })).toBeNull();
});

it("waits for the library response before displaying first-upload guidance", () => {
  workspace.videos = [];
  workspace.loading = true;
  render(<SearchExperience />);
  expect(screen.getByRole("status").textContent).toContain(
    "Loading your library",
  );
  expect(screen.queryByRole("link", { name: "Upload video" })).toBeNull();
});

it("caps footage previews and uses the selected source without losing the query", async () => {
  const original = workspace.videos[0];
  workspace.videos = [
    original,
    { ...original, id: "second", filename: "second.mp4" },
    { ...original, id: "third", filename: "third.mp4" },
    { ...original, id: "fourth", filename: "fourth.mp4" },
    { ...original, id: "pending", filename: "pending.mp4", status: "queued" },
  ];
  vi.mocked(api.search).mockResolvedValue({ query: "water", results: [] });
  render(<SearchExperience />);
  await waitFor(() => expect(api.playback).toHaveBeenCalledTimes(3));
  expect(
    screen.queryByRole("button", { name: "Search fourth.mp4" }),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Search pending.mp4" }),
  ).toBeNull();
  const input = screen.getByLabelText("Describe a visual moment");
  fireEvent.change(input, { target: { value: "water" } });
  const source = screen.getByRole("button", { name: "Search second.mp4" });
  fireEvent.click(source);
  expect(source.getAttribute("aria-pressed")).toBe("true");
  expect(document.activeElement).toBe(input);
  expect((input as HTMLInputElement).value).toBe("water");
  expect(
    (screen.getByLabelText("Search videos") as HTMLSelectElement).value,
  ).toBe("second");
  fireEvent.click(source);
  expect(source.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(source);
  fireEvent.submit(screen.getByRole("search"));
  await screen.findByText("No matching frames");
  expect(api.search).toHaveBeenLastCalledWith(
    "water",
    "second",
    expect.any(AbortSignal),
  );
});
