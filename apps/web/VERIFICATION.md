# Frontend verification — 2026-10-07

## Footage workspace refinement

Feature checkpoints: typography `6a39b58`, preview component `ca84d65`, search
integration `b6db24e`, mobile link `c7f0b43`. All changes are under `apps/web`.

- **21 tests passed**, five files, 1.30s. New coverage checks signed-preview
  requests, paused metadata seeking, ticket/media failure fallbacks, bounded media
  loading, abort on unmount, the three-preview limit, source toggling and preserving
  the typed query. Lint, TypeScript and formatting checks passed. The final Docker
  production build passed, including Next.js TypeScript compilation.
- All three real preview videos decoded at **0.1s**, duration **18s**, readyState
  **4**, paused, no media error. Selecting the train card preserved the query,
  selected its actual UUID and focused the input. Selecting again returned to all
  videos.
- Real library-wide search returned **12** frames; the train filter returned
  **six**, all with loaded thumbnails. Native playback returned to the **00:12**
  match at **12.213259s**, readyState **4**, no media error. Escape closed the
  dialog and restored focus to the selected result.
- The search starting screen fits **320, 390 and 1440px** without horizontal
  overflow. A wrapping library action found on phones was corrected separately.

The search/playback observations used the same code before the final mobile
whitespace adjustment. Final starting-screen layout and paused previews were
checked against the final build. This remains an in-app Chromium check; the
existing Compose/recovery and broader browser scope below is unchanged.

Raw observations: `evaluations/footage-workspace-smoke.json`. Local screenshots
are ignored under `test-results/`.

## Light interface redesign

Current feature commit: `6999b5d`. Focused checkpoints and research are in
[DESIGN.md](DESIGN.md). All changes are under `apps/web`; the same genuine backend
and three ready sample videos were retained. No upload ingestion was repeated for
this visual change.

- `npm run test`: **15 passed**, four files, 1.68s. Three added cases prevent
  first-upload guidance while the library is loading/unavailable or already indexing.
- `npm run lint`, `npm run typecheck`: passed with zero lint warnings. Production
  Docker build passed for the final source, including Next.js TypeScript compilation.
- Real browser search returned 12 actual frames across the library and six with
  the train video selected. All six filtered thumbnails loaded. Results retain
  true timestamps and raw cosine values under the plain Similarity label.
- Selecting **00:12**, then returning to the matching frame, produced native
  playback at **12.221147s**, duration **18s**, readyState **4**, no media error.
  At 1440px the 1000px player is centered (x = 220px); Escape closed it and restored
  focus to the selected result.
- Search has no horizontal overflow at **320, 390 and 1440px**. The library also
  fit 320px with all three ready videos. Viewport overrides were reset afterward.
- Ten selected text/background pairs exceed 4.5:1. The lowest was the placeholder
  at **4.661:1**. This calculation is limited to the selected palette pairs.

Raw observations: `evaluations/redesign-smoke.json`. Ignored screenshots under
`test-results/` include the generic starting state, real search results, phone
layout and centered player. These checks used in-app Chromium; broader browser
and user testing remains separate. The shared Compose/recovery scope below is
unchanged.

## Original ingestion and frontend checkpoints

Branch: `codex/processor-core`. Feature checkpoints: foundation `e8ead3d`, upload
`5644497`, search `6014fbe`, playback `93c4063`, container `6c7cec1`, library
regressions `6dd3e01`. Developer 1 files were not modified.

## Executed checks

- `npm run test`: **12 passed**, four test files, 1.69 seconds. Cases cover early
  upload rejection, double submission, completion retry without a second upload,
  stale search responses, filtering, raw scores, metadata-gated seeking, fresh
  playback URLs, empty/offline libraries and failed-video retry.
- `npm run lint`, `npm run typecheck`: passed, zero lint warnings.
- `npm run build`: passed. Docker also ran `npm ci` and the production build
  successfully. Standalone runtime serves both pages with HTTP 200 as UID 1000;
  Node LTS is pinned by digest and fonts are bundled locally.
- Python demo helper: Ruff/format checks and CLI help passed; startup executed
  successfully with real dependencies. Shared Compose `config --quiet` passed.

Unit tests isolate HTTP/media calls. They are separate from the live check and
do not replace services, models, or results in the application.

## Actual browser flow

Used Codex browser tools against genuine Go, CPU OpenCLIP/FFmpeg, Kafka 3.9,
PostgreSQL 17/pgvector and private MinIO. The demo started with an empty database;
no videos, jobs, or frame rows were seeded. Temporary credentials were generated
in memory. Only localhost ports 8080/9000/3000 were published. No unrelated
project was started.

1. With API online, verified **All videos 0** and the blank-library screen. The
   user's earlier error coincided with no API listening on port 8080; no videos
   is handled separately from an unavailable service.
2. Uploaded the three hash-verified Commons MP4 excerpts through the browser file
   picker and frontend. Each PUT used Go's signed URL, then called the actual
   completion endpoint. Observed queued/indexing states and eventual readiness.
   The user also has copies and credits in Downloads.
3. Confirmed in the real database: **three ready 18-second videos, three completed
   jobs, one claim each, 18 frame rows**. Kafka committed offset: **3**.
4. Puppy and train searches returned real filtered frames. All six train
   thumbnails loaded. Results show filenames, timestamps and raw cosine values.
5. In the production frontend container, selected the train result at **00:12**.
   Native video decoded successfully: duration **18s**, currentTime **12.200425s**,
   readyState **4**, no media error, playing. “Back to matching frame” sought again
   to **12.138701s**. Escape closed the dialog and restored focus to that result.
6. At widths **390px** and **1440px**, document width equaled viewport width, with
   six loaded real thumbnails and no horizontal overflow.

Uploads were exercised in the development frontend; library/search/playback were
also verified in the production container against the same data. Raw observations
are in `evaluations/browser-smoke.json`. Local screenshots are ignored under
`test-results/`. The demo was left running with all three ready clips for the user;
its data is temporary and removed when the demo runner stops. Downloads copies
remain independent of that demo.

## Run and scope

From the repository root, with cached real model weights:

```sh
python3 scripts/run_browser_demo.py --with-web
# Reuse images only after building the current source:
python3 scripts/run_browser_demo.py --with-web --skip-build
```

Open **http://localhost:3000**, matching the API/MinIO allowed origin. The runner
refuses occupied ports and leaves other services alone. Ctrl+C removes only its
temporary containers/network. This demo uses ephemeral data; it is separate from
the persistent shared Compose environment.

Remaining: execute full shared Compose startup with persistent volumes/model
cache, and separately verify public corrupt-upload → retry → ready and stopped-
worker recovery. Failure and expired-link handling have unit tests; the live
browser happy path does not claim those recovery scenarios. Browser verification
used in-app Chromium, rather than a cross-browser matrix.

Semantic accuracy remains the separate tiny-sample processor evaluation (including
two first-frame misses); this UI check adds no benchmark.
