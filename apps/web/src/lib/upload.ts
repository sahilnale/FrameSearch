export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

export function validateVideo(file: File): string | null {
  if (!/\.mp4$/i.test(file.name) || (file.type && file.type !== "video/mp4"))
    return "Choose an MP4 video (.mp4).";
  if (file.size === 0)
    return "This file is empty. Choose a video with content.";
  if (file.size > MAX_UPLOAD_BYTES)
    return "This video is over 100 MB. Choose a smaller clip.";
  if (
    [...file.name].length > 255 ||
    /[\u0000-\u001f\u007f/\\]/u.test(file.name)
  )
    return "Rename this file using a simple filename of up to 255 characters.";
  return null;
}

export function putVideo(
  url: string,
  file: File,
  progress: (value: number) => void,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const finish = (error?: Error) => {
      signal.removeEventListener("abort", abort);
      if (error) reject(error);
      else resolve();
    };
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", "video/mp4");
    xhr.timeout = 180_000;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable)
        progress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () =>
      finish(
        xhr.status >= 200 && xhr.status < 300
          ? undefined
          : new Error(
              "The file transfer failed. Try again; an expired upload link requires a new upload.",
            ),
      );
    xhr.onerror = () =>
      finish(
        new Error(
          "Cannot upload to storage. Check your connection and the local storage service.",
        ),
      );
    xhr.ontimeout = () =>
      finish(new Error("The upload timed out. Please try again."));
    xhr.onabort = () =>
      finish(
        new DOMException("Upload paused. Try again to resume.", "AbortError"),
      );
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) {
      finish(new DOMException("Upload paused.", "AbortError"));
      return;
    }
    xhr.send(file);
  });
}

export function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(0)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatTime(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1000);
  return `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
}
