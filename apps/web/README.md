# FrameSearch web

Developer 2-owned Next.js App Router frontend. Shared HTTP contracts are in
`FrameSearch_Specs/SPEC.md`, section 13. All browser requests use the public Go
API; storage requests use the signed URLs returned by that API.

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
  a refresh action. Query chips are descriptions to try, not recorded results.
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

Developer 1's infrastructure, API and schema remain unchanged.
