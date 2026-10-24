# TODO

## Open work (what is really left)

### Product direction (decided 2026-10-23 - see PRODUCT.md)

ShrinkRay is a server-resident, container-installed, unattended re-encoder for a
large existing collection. It changes a file's **bytes**, never its **identity**.
Library management (naming, moving, organizing, matching, deduplication,
acquisition) is out of scope. The work below follows from that decision; each item
is tracked as a GitHub issue and none of it is implemented yet.

- [ ] **Swap-in-place with a retained original** - GitHub issue #35. The
      target workflow: encode to a temp file, verify it, then replace the
      original *in place* with the original renamed aside - never deleted. The
      UI lists retained originals for manual deletion by the operator. Replaces
      the old `--replace` plan, which trashed the source; ShrinkRay must have no
      code path that deletes a source. Needs: the replace step, the retained-file
      ledger in the db, the UI list, and a `--keep-original` default.
- [x] **Remove the dedupe subsystem** - GitHub issue #37. `dedupe.py`,
      `/api/duplicates`, `/api/duplicates/delete` (the only destructive route),
      `DuplicatesPage.svelte`, `test_dedupe.py`, and the `send2trash` dependency
      (used only there). Deduplication is not something that happens in the
      collection this tool targets.
- [ ] **Authentication for the dashboard** - GitHub issue #36. Not needed for
      development; required before any LAN-exposed deployment. Until then the app
      binds localhost and the README says so.

### Workflow - needs design + explicit user sign-off
- [ ] Swap-in-place implementation detail: the *policy* is decided (retain, never
      delete); what is not designed is the atomic swap on a network share, the
      retention ledger, and the UI for manual cleanup. Nothing gets built before
      the design is reviewed.

### Small, well-specified
- [ ] HDR size-estimate correction: HDR 4K estimates need a systematic bias
      factor or bitrate-based math (probe samples hit easy content; measured
      19.1% real vs 78% predicted on The Mandalorian). Document the
      uncertainty range in plan/README.
- [ ] HDR test material in testdata/ for regression tests (short clip).
- [ ] ~~Manual "remove from library" action~~ - dropped 2026-10-23: out of scope
      (library management). Removing a *db row* is not a product feature.
- [ ] ~~Re-parenting~~ - dropped 2026-10-23: reconnecting a moved file's history
      assumes the tool tracks identity across renames, which is library
      management. A moved file is simply a new file.

### UI (next round) - shared with the UI agent
- [x] Live job progress: SSE stream (/api/events, ProgressBus pub-sub) -
      percent glides during encodes (chunk pump fix 8e86d82 made the
      piped percent lines visible at all), probe steps stream since
      75e4139
- [x] Plan-cache reuse ("quicksave", 34539b4): a plan run persists its
      decision (CRF or skip verdict + fingerprint) per file; an encode
      of an unchanged file with unchanged settings skips probing
      entirely and encodes straight at the planned CRF (crf_source
      provenance in the encodes row; --replan / force_reprobe escape
      hatch; watch mode deliberately never consults plans). Spec:
      docs/plan-reuse.md
- [ ] Retry from job payload verbatim (current retry re-binds the form;
      critique P1) + history filter matching all job paths (multi-folder
      batches show "no jobs" per movie today)
- [ ] Job detail view in the UI (click a history row -> full payload/result)
- [ ] Per-movie actions: encode/plan a single movie from its detail drawer
- [ ] A11y pass: focus traps in overlays, aria-sort/aria-pressed, 24px+ hit
      targets, larger interactive type (critique found 10px interactive
      default); fmtSaved growth sign reads as gain ("+5.0%" in a "Saved"
      column)
- [ ] Design-rethink round: the user may want new prototypes for the dashboard
      (the Zone Record direction is locked, but a re-shape with new comps is
      possible at any time)

### On the real NAS (blocked until hardware access)
- [ ] Swap-in-place smoke on real hardware: systemd unit + a container run
      against a real share. WSL rehearsal passed 2026-10-22. (The old
      Send2Trash trash-permission check is obsolete: ShrinkRay no longer
      deletes files, so no host trash access is needed — see issue #35.)
- [ ] Docker image (now the **target install shape**, not a proposal): mount
      the library, run the resident app, watchdog on. Open decisions (retained-
      original cleanup, PUID/PGID, registry) and the proposed commit series live
      in docs/docker.md. Tracked as GitHub issue #34.

### Blocked
- [ ] Dolby Vision dual-layer handling (the DV enhancement layer may be lost
      on re-encode; warn or preserve - needs a DV test file on the system).
- [ ] GitHub Release entry for v0.3.0 (manual - no gh CLI on this machine;
      the CHANGELOG text is the source for the release notes).
- [ ] Phase-2 folder rename MetaDataScrapper -> ShrinkRay (needs all
      sessions closed).

## Documented limitations (no planned work)
- VMAF scores are not comparable across libvmaf versions (README) - the
  tool measures and decides with the local toolchain; only cross-machine
  score comparisons are affected.
- `plan` size estimates come from short probe samples - a ballpark, not a
  contract (grain-heavy content underestimates; HDR overestimates savings,
  see the size-estimate correction above).

## Ideas (not scheduled)
- Event-based watching (ReadDirectoryChangesW / inotify) instead of the 60s
  poll - more responsive, but the poll is a good trade-off today.
- Delta library sync via `GET /api/snapshot?since=<iso>` (row-level deltas +
  tombstones; sketch ~80 lines + merge tests). Trigger condition: library
  payload exceeds ~1 MB (~5k movies) or long batch runs feel laggy. Until
  then the fingerprint design is correct.
- Terminal surface: a rich `Live` layout inside `shrinkray watch` for
  at-a-glance monitoring over SSH (~50-100 lines, no new deps) - scope,
  trade-offs and the full-TUI trigger condition in docs/tui.md.
- PEP 723 inline script deps for scripts/ helpers (uv-runnable without
  the project venv; pattern taken from the orthos TUI example).

## Decisions (2026-10-22)
- [x] **Mean over probe windows, paired with duration-based sampling**
      (2026-10-24, issues #15 + #14, one change): `achieved_vmaf` is the mean
      of the per-window scores, and the window count derives from the runtime
      (one 10 s window per 10 min, `--sample-count` as the fixed-budget
      override). The mean is robust against one VMAF-misbehaving window
      sinking an otherwise encodable movie; the concession is that a
      genuinely bad scene can hide behind several good ones — the ~4x denser
      coverage keeps that risk low. A floor guard (fail when any window
      falls below `target - tolerance`) is a possible follow-up: measure on
      real content first, do not add it speculatively. Plans decided under
      the old min statistic are not reused (the `plans.statistic` fingerprint
      column, schema v12).
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
- Older records: git history and CHANGELOG.md
