# ui/ — ShrinkRay Console (Svelte 5 + Vite)

Score 12 (file count 3, subdirs 2, own config 1, module boundary 2, symbol density 2,
exports 2; code ratio and centrality below threshold) — a separate toolchain and design
system from the Python core, so it earns its own file. Repo-wide rules (gate, commit,
issue workflow) live in the root `AGENTS.md`.

## OVERVIEW
Dashboard for the resident app: polls `/api/snapshot`, streams job progress over SSE,
enqueues scan/plan/encode and shows the measured provenance per title. `npm run build`
writes `ui/dist`, which `src/shrinkray/app.py` serves (entry `no-cache`, hashed
`/assets/*` immutable). Node is needed only to develop or rebuild the UI.

## STRUCTURE
```
ui/
├── index.html  package.json  vite.config.ts   # entry, deps, dev proxy to :8420
├── src/App.svelte      # shell: rail, hash routing, poll loop, job params, ceremonies
├── src/lib/api.ts      # the ONLY network client + the API type mirror
├── src/lib/progress.svelte.ts  # the ONLY EventSource (live progress store)
├── src/lib/pages/      # Overview, Library, Jobs, Config, MovieDetail, PosterCard,
│                       # HintButton, HintMark
├── src/tokens.css      # design tokens, both themes (canonical); app.css reset
└── screens/ shots*.cjs .impeccable/   # gitignored local artifacts
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Add/change an endpoint call | `src/lib/api.ts` | types, `api.*`, formatters |
| New page or route | `src/App.svelte` | `PAGES`, rail nav, shortcut map |
| Shared state (movies, jobs, params) | `src/App.svelte` | pages are prop-only |
| Live progress | `src/lib/progress.svelte.ts` | `hello` seeds, `data` ticks |
| Colors, type, spacing | `src/tokens.css` | `[data-theme='dark'\|'light']`; rules in `DESIGN.md` |
| Bar-crop / encode-all ceremonies | `src/App.svelte` | one gate for every encode |

## CONVENTIONS
- **Svelte 5 runes only**: `$state`, `$derived`, `$props`, `$effect`. No stores, no
  SvelteKit, no router package — pages are a hash (`#/library`, `#/movie`).
- **Pages are dumb**: `src/lib/pages/*.svelte` take props and call `on*` handlers up to
  `App.svelte`, which owns all shared state. Pages never import another page; only the
  hint helpers (`HintButton`, `HintMark`) are shared inside that directory.
- **One network boundary**: every `fetch` lives in `api.ts` (the `api` object plus the
  ETag-cached `snapshot`/`libraryChanged`); `progress.svelte.ts` owns the only
  `EventSource`. One poll (`/api/snapshot`, 1s busy / 3s idle / 10s hidden).
- **Flow is not state**: SSE carries progress only; the snapshot poll is the state channel.
  Progress is never persisted and never polled.
- **Styling**: consume `var(--token)` from `tokens.css`; scoped `<style>` per component.
  Both themes render the same structure; `color-scheme` follows `[data-theme]`.
- **State as glyphs, not emoji or icon fonts**: `◐/◑ DARK`, `●/○ WATCHER`, `⚠ BARS ARMED`.
- **Persistence split**: `localStorage` (`ms.theme`, `ms.paths`, `ms.out`, `ms.vmaf`,
  `ms.workers`) survives sessions; `sessionStorage` (`ms.query`, `ms.category`,
  `ms.decision`, `ms.sortKey`, `ms.sortAsc`, `ms.jobsKind`, `ms.jobsState`, `sr.detail`)
  only the tab. `removeBars` is never persisted — arming dies with the tab.
- **Gate**: `npm run check` (svelte-check + `tsc`); CI runs `npm ci`, `npm run check`,
  `npm run test` (vitest: api contract helpers, the SSE progress store, the jobs page
  render pattern), `npm run build` in `ui/`.

## ANTI-PATTERNS
- `fetch`/`EventSource` in a component, or a locally re-declared API shape: extend
  `api.ts` instead — it mirrors the FastAPI JSON, so new server fields land there first.
- Branching a component on theme, hardcoding a hex, or adding shadows/glow/blur. Depth is
  tone, seams and hatching only.
- Red for anything but failure/irreversible work; amber for anything but progress.
  Informational feedback ("already queued") uses the quiet notice, never `role="alert"`.
- A new page missing from `PAGES`, the rail nav or the keyboard map.
- Refetching what the snapshot already delivered: the detail page filters `allJobs` in memory.

## NOTES
- `ui/README.md` is still the untouched Vite template text — not project docs.
- `DESIGN.md` is partly stale: it documents a grid/table toggle and the poster card, but
  `LibraryPage` is table-only and `PosterCard.svelte` has no importer.
- `App.svelte` hardcodes Windows defaults (`D:\Movies`, `D:\Encoded`) and VMAF 95; a fresh
  browser profile shows them verbatim. The rail legend still reads `1–5: pages` for four pages.
