import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { PlaybackOverlay } from "@/components/playback-overlay";

vi.mock("@/lib/api", () => ({
  api: { playback: vi.fn() },
  errorMessage: (error: Error) => error.message,
}));
const result = {
  video_id: "video-id",
  frame_id: "frame-id",
  filename: "clip.mp4",
  timestamp_ms: 12000,
  thumbnail_url: "http://storage/thumb",
  similarity: 0.31,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(
    function (this: HTMLDialogElement) {
      this.open = true;
    },
  );
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.mocked(api.playback).mockResolvedValue({
    url: "http://storage/signed-video",
    expires_in_seconds: 900,
  });
});

it("waits for metadata before seeking to the returned frame timestamp", async () => {
  render(
    <PlaybackOverlay
      videoId={result.video_id}
      filename={result.filename}
      timestampMs={result.timestamp_ms}
      onClose={vi.fn()}
    />,
  );
  const video = (await screen.findByLabelText(
    "clip.mp4 video player",
  )) as HTMLVideoElement;
  expect(video.currentTime).toBe(0);
  Object.defineProperty(video, "duration", { configurable: true, value: 18 });
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(12);
  expect(video.play).toHaveBeenCalledTimes(1);
  expect(api.playback).toHaveBeenCalledWith(
    "video-id",
    expect.any(AbortSignal),
  );
});

it("fetches a new signed URL after playback fails instead of reusing an expired URL", async () => {
  vi.mocked(api.playback)
    .mockResolvedValueOnce({
      url: "http://storage/expired",
      expires_in_seconds: 900,
    })
    .mockResolvedValueOnce({
      url: "http://storage/fresh",
      expires_in_seconds: 900,
    });
  render(
    <PlaybackOverlay
      videoId={result.video_id}
      filename={result.filename}
      timestampMs={result.timestamp_ms}
      onClose={vi.fn()}
    />,
  );
  const video = await screen.findByLabelText("clip.mp4 video player");
  fireEvent.error(video);
  fireEvent.click(screen.getByRole("button", { name: "Refresh playback" }));
  await waitFor(() =>
    expect(
      screen.getByLabelText("clip.mp4 video player").getAttribute("src"),
    ).toBe("http://storage/fresh"),
  );
  expect(api.playback).toHaveBeenCalledTimes(2);
});

it("reports an out-of-range timestamp and closes on Escape", async () => {
  const close = vi.fn();
  render(
    <PlaybackOverlay
      videoId={result.video_id}
      filename={result.filename}
      timestampMs={result.timestamp_ms}
      onClose={close}
    />,
  );
  const video = await screen.findByLabelText("clip.mp4 video player");
  Object.defineProperty(video, "duration", { value: 5 });
  fireEvent.loadedMetadata(video);
  expect(screen.getByRole("alert").textContent).toContain(
    "outside the playable video",
  );
  fireEvent(
    screen.getByRole("dialog"),
    new Event("cancel", { cancelable: true }),
  );
  expect(close).toHaveBeenCalledTimes(1);
});

it("starts a library video at zero with duration instead of search-match controls", async () => {
  render(
    <PlaybackOverlay
      videoId="library-id"
      filename="library.mp4"
      onClose={vi.fn()}
    />,
  );
  const video = (await screen.findByLabelText(
    "library.mp4 video player",
  )) as HTMLVideoElement;
  Object.defineProperty(video, "duration", { value: 18 });
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(0);
  expect(video.play).toHaveBeenCalledOnce();
  expect(api.playback).toHaveBeenCalledWith(
    "library-id",
    expect.any(AbortSignal),
  );
  expect(screen.getByText("00:18")).toBeTruthy();
  expect(screen.queryByText("Matching frame")).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Back to matching frame" }),
  ).toBeNull();
});

it("preserves a rewound zero-second playhead when refreshing a search video", async () => {
  render(
    <PlaybackOverlay
      videoId={result.video_id}
      filename={result.filename}
      timestampMs={result.timestamp_ms}
      onClose={vi.fn()}
    />,
  );
  const first = (await screen.findByLabelText(
    "clip.mp4 video player",
  )) as HTMLVideoElement;
  Object.defineProperty(first, "duration", { value: 18 });
  fireEvent.loadedMetadata(first);
  first.currentTime = 0;
  fireEvent.error(first);
  fireEvent.click(screen.getByRole("button", { name: "Refresh playback" }));
  await waitFor(() => expect(api.playback).toHaveBeenCalledTimes(2));
  const refreshed = screen.getByLabelText(
    "clip.mp4 video player",
  ) as HTMLVideoElement;
  Object.defineProperty(refreshed, "duration", { value: 18 });
  fireEvent.loadedMetadata(refreshed);
  expect(refreshed.currentTime).toBe(0);
});
