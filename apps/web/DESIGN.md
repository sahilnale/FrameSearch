# FrameSearch interface direction

The product is a general tool for searching visual content in a user's videos.
The interface must work with arbitrary filenames and footage. Evaluation clips
belong in tests and demonstrations; they do not define the product's copy or
suggested queries.

The user chose a light workspace with white surfaces, cool gray and a blue accent.
These design decisions combine that preference with the patterns below.

## Research and resulting decisions

- [Frame.io search](https://help.frame.io/en/articles/9101079-enhanced-search-with-ai-search)
  places search scope and relevance ordering near results and opens semantic matches
  at their timestamps. FrameSearch keeps its actual video filter, thumbnail results
  and direct timestamp playback. Only the shared API's supported capabilities appear.
- [TwelveLabs search playground](https://beta.docs.twelvelabs.io/docs/resources/playground/search)
  separates query controls from visual results and supports selecting a frame to play
  a moment. FrameSearch gives its query field, previews and timestamps a clear order;
  a centered player preserves the search context.
- [Carbon empty states](https://www.carbondesignsystem.com/building-blocks/core/patterns/empty-states)
  calls for context-appropriate guidance and limited content. Each state has one useful
  next step: upload into an empty library, check existing indexing work, reload an
  unavailable library, or choose existing footage and enter a description when
  videos are ready.
- [WCAG text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
  requires at least 4.5:1 for ordinary text. The selected body, muted text, primary
  action, navigation, placeholder and status-chip pairs were calculated above that
  threshold. This is a palette check, not a complete accessibility audit.

## Visual and interaction rules

Use white panels on `#f4f5f8`, ink `#202635`, secondary text `#5a6578`, and blue
`#3156d3` for actions and selection. Green, amber and red distinguish processing
states with accompanying text. Footage retains its original colors. Navigation
contains Search and Library, with one upload entry point per view.

Use a compact top navigation instead of a permanent sidebar. Keep filenames and
result timestamps readable; supporting labels are generally 12–14px and the search
input is 16px. Mobile layouts rearrange controls without hiding the two destinations.
The player retains native controls, focus restoration and signed-link recovery.

Ready Library rows have one play target covering the file icon and filename,
with Search as a separate link. Watching a library video starts at zero and shows
its duration; search playback keeps its matching timestamp and return-to-frame
control. Pending rows explain that playback becomes available after indexing,
matching the existing ready-only signed playback contract.

Use direct labels: Search, Library, Upload a video, Play video, Similarity. Display
actual raw cosine values to three decimals, with the definition in their tooltip.
Remove promotional slogans, decorative frame art, duplicate counts and test-clip
query suggestions. Never fabricate previews or infer a video's contents from its name.

## Footage and visual character

The light palette stays, with a solid blue scan mark and underlined navigation.
The search screen now uses an editorial composition: oversized Manrope type on
the left, a blue query panel on the right, and a large footage preview beside two
smaller ones. One or two available previews use simpler layouts; phones stack
them. Results use a compact search header, large frames and filename rows instead
of enclosing every item in a white card. The layout uses actual library content.

When ready videos exist, the start screen shows up to three actual video
previews instead of an empty search placeholder. Each requests its signed original
through the existing playback API, uses metadata preload and seeks to the clip's
midpoint to decode a still. Hover plays it muted when the device supports hover and
reduced motion is off; leaving pauses and returns to the midpoint. The initial
screen remains paused. Browser preload is a hint; transferred bytes depend on the
browser and storage server. Requests abort when the shelf unmounts. Ticket/media
failures or a 15-second media timeout show an honest fallback
without disabling source selection. Selecting a source preserves the query and
focuses the input; selecting it again returns to all videos. No extra backend
endpoint, stored poster or filename-based imagery is introduced.

## Checkpoints

1. `38e67f2`: general copy and removal of fixture-specific suggestions/artwork.
2. `cd01879`: light palette, compact navigation, responsive layout, centered player.
3. `5554f89`: explicit playback actions and plain similarity labels.
4. `6999b5d`: separate loading, unavailable, indexing and first-upload guidance.
5. `6a39b58`: stronger typography, scan mark and navigation identity.
6. `ca84d65`: reusable signed-video preview and failure-handling tests.
7. `b6db24e`: footage shelf and query-preserving source selection.
8. `c7f0b43`: keep the library action on one line on phones.
9. `b6e4350`: shared whole-video/search player with zero-playhead recovery.
10. `431d015`: direct library playback and integration coverage.
11. `645320d`: editorial search composition, blue query panel and asymmetric footage.
12. `0651203`: midpoint stills and muted hover previews that respect reduced motion.

See [VERIFICATION.md](VERIFICATION.md) for the executed checks and their scope.
