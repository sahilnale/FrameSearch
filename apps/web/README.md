# FrameSearch web

Developer 2-owned Next.js App Router frontend. Shared HTTP contracts are in
`FrameSearch_Specs/SPEC.md`, section 13. All browser requests use the public Go
API; storage requests use the signed URLs returned by that API.

The current interface is a light workspace with cool gray and blue. Search uses
oversized type, a blue query panel and an asymmetric footage layout. Search and
Library share compact top navigation and guidance based on actual library status.
Copy and suggestions are independent of evaluation clips. Design decisions are in
[DESIGN.md](DESIGN.md).

The search start screen shows up to three ready videos using genuine midpoint
stills from signed playback URLs. Hover previews play muted on supported devices
when reduced motion is off, then pause and return to the still when you leave.
Select one to scope the query; select it again to search the whole library.
Preview failures do not prevent searching. The shared API/schema are unchanged.

In Library, click a ready video's filename or play tile to watch its original
from the beginning. Search remains a separate action. The existing playback API
only issues links for ready videos, so queued/indexing rows explain when playback
becomes available. The shared player retains native controls, signed-link recovery
and focus restoration; library playback shows duration instead of match controls.

```sh
cd apps/web
npm ci
npm run dev
```

Open http://localhost:3000. API defaults to http://localhost:8080. To change it,
copy `.env.example` to `.env.local` and set `NEXT_PUBLIC_API_URL` before building.
The API's `WEB_ORIGIN` and MinIO CORS must allow http://localhost:3000. This is a
local, single-user application; authentication is outside the shared MVP scope.

```sh
npm run lint
npm run typecheck
npm run build
```

## Checkpoints

- Visual foundation: responsive charcoal/green shell, navigation, empty states,
  shared TypeScript contracts, live library/status polling. No fabricated videos.
- Upload/library: signed direct-to-storage PUT with measured progress, early file
  checks, status polling, failed-index retry, and completion retry without a second
  upload. State survives navigation within the app. A browser reload loses the
  selected local file; the shared API has no upload-resume URL endpoint.
- Search: actual Go results, per-video filters, signed thumbnails, true timestamps
  and raw cosine scores. New queries abort older requests; expired thumbnails have
  a refresh action. Queries are user-entered and independent of evaluation clips.
- Playback: click a real result, request a signed original, wait for metadata,
  seek to the timestamp, use native controls. Escape/background/close dismiss the
  native modal and restore focus. Expired/unreadable URLs can be refreshed.

Foundation checks: ESLint (zero warnings), TypeScript, and production build passed.
The browser preview was inspected at its default compact size and 1440px desktop.
The font is bundled locally with its SIL Open Font License in `src/fonts/OFL.txt`.

Upload checkpoint: three focused UI tests, ESLint, and production build passed.
The tests isolate HTTP/transfer calls to exercise double submission and interrupted
completion. Real browser ingestion will be checked against the genuine stack.

Search checkpoint: six focused UI tests (upload plus search), lint, and production
build passed. The race test checks that a slow previous query cannot replace a
newer result. These unit fixtures are separate from the application's live data.

Playback checkpoint: nine focused UI tests, lint, and production build passed.
Metadata-gated seeking, fresh URL recovery, and invalid-timestamp errors are
covered. Native decoding/seek and cross-origin storage will be verified live.

Library regression checkpoint: **12 UI tests passed** (1.69s), with zero-warning
lint and TypeScript checks. Added explicit coverage for a verified empty library,
an unreachable API, and retrying an existing failed video without creating a new
video. The reported library error coincided with services offline; the live
empty-library check passed after starting the real API.

Developer 1's infrastructure, API and schema remain unchanged.

## Container

```sh
docker build -t framesearch-web:ui-checkpoint apps/web
```

Run that command from the repository root. The pinned Node LTS image builds with
`npm ci` and serves standalone output as the unprivileged `node` user. Pass
`--build-arg NEXT_PUBLIC_API_URL=http://localhost:8080` for a different public API
address. The browser URL is baked in at build time, as expected by the existing
shared Compose `web` service; changing only a running container's environment
does not change the client bundle.

Container verification: build passed; actual runtime serves `/` and `/library`
with HTTP 200 as UID 1000. The genuine backend/browser check also decoded an
18-second train clip and sought to 12 seconds (observed 12.200425s, readyState 4,
no media error). Escape returned focus to the selected result. Full shared
Compose startup remains a separate check.

## Live demo

The real browser check passed: three sample uploads became ready, 18 frames were
indexed, search thumbnails loaded, and playback sought to the selected timestamp.
Details and remaining scope are in [VERIFICATION.md](VERIFICATION.md).

From the repository root, after caching real model weights:

```sh
python3 scripts/run_browser_demo.py --with-web
# Only when the existing images match current source:
python3 scripts/run_browser_demo.py --with-web --skip-build
```

This builds unchanged canonical backend images and the owned web image, creates
fresh genuine dependencies with credentials held in memory, and starts a blank
library. Upload the samples in your browser. Open **http://localhost:3000**, not
127.0.0.1, to match the configured allowed origin. Ctrl+C removes only that demo's
temporary data/containers/network. The runner refuses occupied ports and never
starts other projects. Use shared Compose for persistent application data; its
complete startup remains to be executed separately.
