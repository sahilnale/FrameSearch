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
- Next: direct signed upload, processing/retry, search results, timestamp playback.

Foundation checks: ESLint (zero warnings), TypeScript, and production build passed.
The browser preview was inspected at its default compact size and 1440px desktop.
The font is bundled locally with its SIL Open Font License in `src/fonts/OFL.txt`.

Developer 1's infrastructure, API and schema remain unchanged.
