import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { MAX_UPLOAD_BYTES, putVideo, validateVideo } from "@/lib/upload";
import { UploadProvider } from "@/components/upload-provider";
import { UploadPanel } from "@/components/upload-panel";

vi.mock("@/components/workspace", () => ({ useWorkspace: () => ({ refresh: vi.fn().mockResolvedValue(undefined) }) }));
vi.mock("@/lib/api", () => ({ api: { upload: vi.fn(), complete: vi.fn() }, errorMessage: (error: Error) => error.message }));
vi.mock("@/lib/upload", async original => ({ ...await original<typeof import("@/lib/upload")>(), putVideo: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.upload).mockResolvedValue({ video_id: "real-ticket-id", upload_url: "http://storage/signed", object_key: "videos/id/original.mp4" });
  vi.mocked(api.complete).mockResolvedValue({ video_id: "real-ticket-id", status: "queued" });
  vi.mocked(putVideo).mockResolvedValue(undefined);
});

function choose() {
  fireEvent.change(screen.getByLabelText("Choose MP4 video"), { target: { files: [new File(["video"], "clip.mp4", { type: "video/mp4" })] } });
}

describe("upload boundaries", () => {
  it("rejects empty, wrong-format and oversized files before requesting a ticket", () => {
    expect(validateVideo(new File([], "empty.mp4"))).toMatch(/empty/);
    expect(validateVideo(new File(["data"], "clip.mov"))).toMatch(/MP4/);
    const large = new File(["a"], "clip.mp4");
    Object.defineProperty(large, "size", { value: MAX_UPLOAD_BYTES + 1 });
    expect(validateVideo(large)).toMatch(/100 MB/);
    expect(validateVideo(new File(["a"], "clip.mp4"))).toBeNull();
  });

  it("retries completion on the existing video without re-uploading or making a second ticket", async () => {
    vi.mocked(api.complete).mockRejectedValueOnce(new Error("API temporarily unavailable"));
    render(<UploadProvider><UploadPanel/></UploadProvider>);
    choose();
    fireEvent.click(screen.getByRole("button", { name: "Upload video" }));
    await screen.findByText("API temporarily unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Try upload again" }));
    await screen.findByText("Uploaded. Finding your frames.");
    expect(api.upload).toHaveBeenCalledTimes(1);
    expect(putVideo).toHaveBeenCalledTimes(1);
    expect(api.complete).toHaveBeenCalledTimes(2);
  });

  it("blocks double submission while a transfer is pending and reports real progress", async () => {
    let finish!: () => void;
    vi.mocked(putVideo).mockImplementation((_url, _file, progress) => { progress(37); return new Promise<void>(resolve => { finish = resolve; }); });
    render(<UploadProvider><UploadPanel/></UploadProvider>);
    choose();
    const button = screen.getByRole("button", { name: "Upload video" });
    fireEvent.click(button); fireEvent.click(button);
    await screen.findByText("Uploading · 37%");
    expect(api.upload).toHaveBeenCalledTimes(1);
    expect(api.complete).not.toHaveBeenCalled();
    await act(async () => finish());
    await waitFor(() => expect(api.complete).toHaveBeenCalledTimes(1));
  });
});
