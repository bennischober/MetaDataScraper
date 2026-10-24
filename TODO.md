# TODO

## Open work (what is really left)

Work is tracked on GitHub; this file mirrors it. Last reconciled 2026-10-24 -
landed items move to the Completed record with PR pointers, open items carry
their issue numbers, so the tracker and this file stop drifting apart.

### In flight / ready to claim (issues #97-#102, #105)

- [ ] **Module layout refactor (phases 2-6 of the #63 record)** - issue #97:
      `tools/` (one subprocess runner, one ffprobe caller), split
      `quality.py`/`encode.py` into `engine/` as pure moves, `server/`
      (routers + security.py), merge the duplicate implementations, then turn on
      the `import-linter` layers contract as the seventh gate command.
- [ ] **HDR size-estimate correction** - issue #98: HDR 4K estimates carry a
      systematic bias (probe samples hit easy content; measured 19.1% real vs
      78% predicted on The Mandalorian). Correct the math (now on the AV1 curve)
      and document the uncertainty range in plan/README.
- [ ] **HDR/DV test material** - issue #99: short HDR10 clip in `testdata/`
      with regression tests (HDR detection + 10-bit encoder path); DV sample if
      one can be produced license-safely.
- [ ] **Retry verbatim + history path filter** - issue #100: retry must replay
      the job's stored payload (it re-binds the form today); the per-movie
      history filter must match all payload paths (multi-folder batches show
      "no jobs" per movie).
- [ ] **Dashboard a11y pass** - issue #101: focus traps in overlays,
      aria-pressed on toggles, 24px+ hit targets, larger interactive type
      (critique found 10px), and the fmtSaved sign that reads growth as a gain.
      (`aria-sort` already landed in LibraryPage.)
- [ ] **UI test suite** - issue #102: CI's `ui` job runs check + build only;
      add Vitest (+ Testing Library) coverage for api.ts / progress store /
      one page pattern, wired into CI.
- [x] **TODO.md cleanup** - issue #105 (this rewrite).

### Needs a product decision (kept open deliberately)

- [ ] **Default VMAF target: 95.0 or 96?** - issue #103. The code constant
      (`DEFAULT_TARGET_VMAF`, quality.py) and every CLI default say 95.0;
      PRODUCT.md and the root AGENTS.md product rules say 96. Until decided:
      quote the constant, never the prose, and do not "fix" the number in a doc
      PR. Changing the constant invalidates cached plan fingerprints (the
      target participates in them) - a deliberate one-time behaviour change.
- [ ] **Dolby Vision dual-layer: warn-only or preserve?** - issue #104. The DV
      enhancement layer may be lost on re-encode. Needs a DV test file (#99)
      and a spike through the current pipeline before the call.

### On the real NAS (blocked until hardware access)

- [ ] Swap-in-place smoke on real hardware: systemd unit + a container run
      against a real share. WSL rehearsal passed 2026-10-22; #93 (#35) landed
      the swap, the retained ledger (`/api/retained`) and RetainedPage.svelte.

### Blocked

- [ ] Phase-2 folder rename MetaDataScrapper -> ShrinkRay (needs all
      sessions closed).

## Documented limitations (no planned work)
- VMAF scores are not comparable across libvmaf versions (README) - the
  tool measures and decides with the local toolchain; only cross-machine
  score comparisons are affected.
- `plan` size estimates come from short probe samples - a ballpark, not a
  contract (grain-heavy content underestimates; HDR overestimates savings,
  see issue #98 for the correction).

## Ideas (not scheduled)
- Event-based watching (ReadDirectoryChangesW / inotify) instead of the
  adaptive poll - more responsive, but the poll is a good trade-off today.
- Delta library sync via `GET /api/snapshot?since=<iso>` (row-level deltas +
  tombstones; sketch ~80 lines + merge tests). Trigger condition: library
  payload exceeds ~1 MB (~5k movies) or long batch runs feel laggy. Until
  then the fingerprint design is correct.
- Terminal surface: a rich `Live` layout inside `shrinkray watch` for
  at-a-glance monitoring over SSH (~50-100 lines, no new deps) - scope,
  trade-offs and the full-TUI trigger condition in docs/tui.md.
- PEP 723 inline script deps for scripts/ helpers (uv-runnable without
  the project venv; pattern taken from the orthos TUI example).
- Dashboard design-rethink round: the Zone Record direction is locked, but a
  re-shape with new comps is possible at any time (user's call).

## Decisions (2026-10-22)
- [x] **Releases are changelog-written.** Every tag gets real release
      notes assembled from the cycle's history (CHANGELOG.md is the
      durable record; the GitHub release carries the same text). No
      bare tags.
- [x] **No PyInstaller/packaging - ever.** The distribution IS the uv-based
      install (install.ps1 on Windows, clone+uv sync or uvx from git on
      Linux). A plain source checkout stays auditable, patchable in place
      and dependency-managed by uv; a frozen binary would add a build
      pipeline, AV false-positive risk and a second artifact to keep fresh
      for zero benefit on a self-hosted single-user tool.
- [x] **Secrets never in config.json** (it is a tracked file): integration
      tokens live in environment variables, config.json only names the env
      var. Honors the no-secrets-in-commits rule by construction.

## Completed record (commit-hash pointers)
- **2026-10-24 wave:** swap-in-place with retained originals #35 (PR #93);
  bearer-token dashboard auth #36 (PR #92); container image #34 (PR #94);
  AV1-only output #21 (PR #90); black-bar spike writeup #51 (PR #95);
  module-layout decision record #63 (PR #91); config schema validation #61
  (PR #89); atomic config write #60 (PR #88); config location #58 (PR #87).
- Reliability wave A1-A4 + disk watchdog (2026-10-23): `535b64f`, `b9fbfaa`,
  `9f2c4df`, `32c0b15`, `41fe9aa`
- Recursive-trap batch (watch starvation, disk-full retry burn, overlap
  guard, app stop_event): `dcf3f23`; stall watchdog + silence-proof cancel
  + tree-safe termination: `af329c4`
- Verification & health: `--verify` full-file VMAF (schema v8) `bf69da1`;
  `shrinkray doctor` `97bc2b9`
- CI/testing hygiene: lockfile check + concurrency `2a6bf60`; report
  output-dir fix + Verified column `22fa535`; compare/report/doctor CLI
  tests `a828111`
- Dedupe removal #37 and Jellyfin removal #43 (2026-10-24 PRs #76/#71).
- SSE live progress, plan-cache reuse (34539b4), job detail expansion and
  per-movie links: landed earlier in the UI round.
- Older records: git history and CHANGELOG.md
