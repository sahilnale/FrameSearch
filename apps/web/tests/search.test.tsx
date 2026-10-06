import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { SearchExperience } from "@/components/search-workspace";

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("@/components/workspace", () => ({ useWorkspace: () => ({ videos: [{ id: "video-id", filename: "original.mp4", status: "ready" }], loading: false, error: null }) }));
vi.mock("@/lib/api", () => ({ api: { search: vi.fn() }, errorMessage: (error: Error) => error.message }));
beforeEach(() => vi.clearAllMocks());
const result = { video_id: "video-id", frame_id: "frame-id", filename: "original.mp4", timestamp_ms: 12000, thumbnail_url: "http://storage/signed-thumb", similarity: 0.314159 };

it("displays returned timestamps and raw cosine without claiming confidence", async () => {
  vi.mocked(api.search).mockResolvedValue({ query: "a dog", results: [result] });
  render(<SearchExperience/>);
  fireEvent.change(screen.getByLabelText("Describe a visual moment"), { target: { value: "a dog" } });
  fireEvent.submit(screen.getByRole("search"));
  await screen.findByRole("heading", { name: "original.mp4" });
  expect(screen.getByText("00:12")).toBeTruthy();
  expect(screen.getByText("0.314")).toBeTruthy();
  expect(screen.queryByText("31%")).toBeNull();
  expect(api.search).toHaveBeenCalledWith("a dog", null, expect.any(AbortSignal));
});

it("does not let an old response replace a newer query", async () => {
  let first!: (value: { query: string; results: typeof result[] }) => void;
  vi.mocked(api.search).mockImplementationOnce(() => new Promise(resolve => { first = resolve; })).mockResolvedValueOnce({ query: "train", results: [{ ...result, filename: "new-query.mp4" }] });
  render(<SearchExperience/>);
  const input = screen.getByLabelText("Describe a visual moment");
  fireEvent.change(input, { target: { value: "dog" } }); fireEvent.submit(screen.getByRole("search"));
  fireEvent.change(input, { target: { value: "train" } }); fireEvent.submit(screen.getByRole("search"));
  await screen.findByText("new-query.mp4");
  await act(async () => first({ query: "dog", results: [result] }));
  expect(screen.queryByRole("heading", { name: "original.mp4" })).toBeNull();
  expect(screen.getByText("For “train”")).toBeTruthy();
});

it("sends the actual selected video filter and offers an error retry", async () => {
  vi.mocked(api.search).mockRejectedValueOnce(new Error("processor unavailable")).mockResolvedValueOnce({ query: "water", results: [] });
  render(<SearchExperience initialVideo="video-id"/>);
  fireEvent.change(screen.getByLabelText("Describe a visual moment"), { target: { value: "water" } }); fireEvent.submit(screen.getByRole("search"));
  await screen.findByText("processor unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Try search again" }));
  await screen.findByText("No frames found yet.");
  expect(api.search).toHaveBeenLastCalledWith("water", "video-id", expect.any(AbortSignal));
});
