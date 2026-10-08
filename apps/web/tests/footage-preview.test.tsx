import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FootagePreview } from "@/components/footage-preview";
import { api } from "@/lib/api";
import type { PlaybackTicket, Video } from "@/lib/contracts";

vi.mock("@/lib/api", () => ({ api: { playback: vi.fn() } }));

const video: Video = {
  id: "source-id",
  filename: "user-footage.mp4",
  status: "ready",
  duration_seconds: 18,
  processing_error: null,
  created_at: "2026-10-07T00:00:00Z",
};
const ticket = {
  url: "http://storage/video?signed=example",
  expires_in_seconds: 900,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.playback).mockResolvedValue(ticket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("uses a fresh playback ticket for a paused midpoint preview", async () => {
  const { container } = render(
    <FootagePreview video={video} selected={false} onSelect={vi.fn()} />,
  );
  await waitFor(() => expect(container.querySelector("video")).toBeTruthy());
  const media = container.querySelector("video")!;
  expect(api.playback).toHaveBeenCalledWith(video.id, expect.any(AbortSignal));
  expect(media.getAttribute("src")).toBe(ticket.url);
  expect(media.preload).toBe("metadata");
  expect(media.autoplay).toBe(false);
  Object.defineProperty(media, "duration", { value: 18 });
  fireEvent.loadedMetadata(media);
  expect(media.currentTime).toBe(9);
  fireEvent.loadedData(media);
  expect(container.querySelector(".spinner")).toBeNull();
  expect(screen.getByText("00:18")).toBeTruthy();
});

it("keeps source selection usable when the ticket request fails", async () => {
  vi.mocked(api.playback).mockRejectedValue(new Error("offline"));
  const select = vi.fn();
  render(<FootagePreview video={video} selected={false} onSelect={select} />);
  await screen.findByText("Preview unavailable");
  fireEvent.click(
    screen.getByRole("button", { name: "Search user-footage.mp4" }),
  );
  expect(select).toHaveBeenCalledOnce();
});

it("replaces failed media with a fallback and retains the selected state", async () => {
  const { container } = render(
    <FootagePreview video={video} selected onSelect={vi.fn()} />,
  );
  await waitFor(() => expect(container.querySelector("video")).toBeTruthy());
  fireEvent.error(container.querySelector("video")!);
  expect(screen.getByText("Preview unavailable")).toBeTruthy();
  expect(container.querySelector("video")).toBeNull();
  expect(screen.getByRole("button").getAttribute("aria-pressed")).toBe("true");
});

it("stops the loader if the media never produces a frame", async () => {
  vi.useFakeTimers();
  const { container } = render(
    <FootagePreview video={video} selected={false} onSelect={vi.fn()} />,
  );
  await act(async () => {});
  expect(container.querySelector("video")).toBeTruthy();
  await act(async () => vi.advanceTimersByTime(15_000));
  expect(screen.getByText("Preview unavailable")).toBeTruthy();
  expect(container.querySelector(".spinner")).toBeNull();
});

it("aborts an unfinished ticket request when the shelf is removed", async () => {
  let resolve!: (ticket: PlaybackTicket) => void;
  vi.mocked(api.playback).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const { unmount } = render(
    <FootagePreview video={video} selected={false} onSelect={vi.fn()} />,
  );
  const signal = vi.mocked(api.playback).mock.calls[0][1]!;
  expect(signal.aborted).toBe(false);
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(ticket));
});

it("previews muted footage on hover and stops at the poster frame when leaving", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
  const play = vi
    .spyOn(HTMLMediaElement.prototype, "play")
    .mockResolvedValue(undefined);
  const pause = vi
    .spyOn(HTMLMediaElement.prototype, "pause")
    .mockImplementation(() => {});
  const { container } = render(
    <FootagePreview video={video} selected={false} onSelect={vi.fn()} />,
  );
  await waitFor(() => expect(container.querySelector("video")).toBeTruthy());
  const player = container.querySelector("video")!;
  Object.defineProperty(player, "duration", { value: 18 });
  fireEvent.loadedMetadata(player);
  fireEvent.loadedData(player);
  const card = screen.getByRole("button", { name: "Search user-footage.mp4" });
  fireEvent.mouseEnter(card);
  expect(play).toHaveBeenCalledOnce();
  expect(player.muted).toBe(true);
  fireEvent.play(player);
  expect(screen.getByText("Previewing")).toBeTruthy();
  player.currentTime = 12;
  fireEvent.mouseLeave(card);
  expect(pause).toHaveBeenCalledOnce();
  expect(player.currentTime).toBe(9);
});

it("leaves previews still when motion or hover is unavailable", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false }));
  const play = vi
    .spyOn(HTMLMediaElement.prototype, "play")
    .mockResolvedValue(undefined);
  const { container } = render(
    <FootagePreview video={video} selected={false} onSelect={vi.fn()} />,
  );
  await waitFor(() => expect(container.querySelector("video")).toBeTruthy());
  fireEvent.loadedData(container.querySelector("video")!);
  fireEvent.mouseEnter(
    screen.getByRole("button", { name: "Search user-footage.mp4" }),
  );
  expect(play).not.toHaveBeenCalled();
});
