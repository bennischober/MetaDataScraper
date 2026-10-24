# Changelog

All notable changes to ShrinkRay are documented here. Releases are
tagged in git; the GitHub release for each tag carries the same notes.

## [Unreleased]

### Added

- **A minimum-resolution gate**: `eligibility.min_source_height` (default
  1080, matching the floor PRODUCT.md states) skips a source whose height is
  below it with the reason `resolution too low`, before any sample extraction
  or probe encode (issue #18). A lower-resolution source has too few pixels
  to save meaningfully, so the probe cost buys nothing, and the gate decides
  from cached metadata instead of a new probe. The gate is off when set to 0,
  and an unreadable height is probed rather than excluded: an unknown
  resolution must never silently drop a file the operator expected to be
  considered.

### Changed

- **`plan` extracts each probe window once instead of three times**: the
  sample windows are extracted up front and the same list is handed to the CRF
  search and to the reported size factor (issue #30). `find_optimal_crf_and_skip`
  used to extract its own copy for the search while `plan_quality` extracted a
  second one for the size estimate, and the size estimate used only the first
  sample of that second list, so every window was decoded and written more than
  once per plan. With the duration-based sample counts that multiplier applies
  to a larger base cost. A fresh `encode` extracted twice for the same reason.

- **The typing, lint and coverage bar is enforced, not aspirational**: every
  module under `src/shrinkray/` is now fully annotated and mypy runs with
  `disallow_untyped_defs`, `disallow_incomplete_defs`, `warn_return_any`,
  `no_implicit_reexport` and `strict_equality` (issue #40). `py.typed` ships
  in the wheel, ruff runs the `S` (bandit) rules with each exception carrying
  its reason, and coverage has a floor so a regression fails instead of sliding.
  The per-module mypy rollout list is gone: one config applies to all of it.

### Fixed

- **Probe samples were re-encoded with lossy libx264**: sample extraction
  now stream-copies the source (`-c:v copy`, keyframe-aligned, timestamps
  normalised with `-avoid_negative_ts make_zero`) instead of running a CRF 12
  x264 pass (issue #16). The samples are both the VMAF reference and the
  probe encode's input, so the re-encode biased every measurement: it smoothed
  grain and fine detail, which made the probe source easier than the real one
  and pushed the chosen CRF too high. The copy also keeps the source's bit
  depth, colour tags and HDR side data, which the x264 pass dropped. If a
  container cannot hold the source codec the extraction falls back to a
  lossless `ffv1` re-extract and logs that it did, rather than silently
  encoding lossily.

- **A batch encode's live progress was silently dropped**: `encode_movies`
  rebound its `progress` parameter with its own rich progress bar, so a resident
  app job never streamed a percent for a fixed-preset batch. The caller's
  callback now receives every event alongside the bar.

### Removed

- **Duplicate detection and the only destructive route**: `dedupe.py`, the
  `/api/duplicates` read route, `/api/duplicates/delete`, the Duplicates page,
  the `dedupe` job kind, and the `send2trash`/`rapidfuzz` dependencies are
  gone (issue #37). Deduplication is library management, which PRODUCT.md puts
  outside this tool's scope, and the delete route was the one code path in
  ShrinkRay that could remove a file from the library. A `metadata.db` that
  still holds `dedupe` rows keeps them as history: they read back as a generic
  `legacy` job, and a `queued` one is retired as failed at startup with the
  reason recorded instead of being executed.

- **Jellyfin integration**: the opt-in library-refresh notification after
  encode batches is gone (issue #43). ShrinkRay changes a file's bytes;
  telling another application about it is that application's job — and the
  integration was the only outbound network call in the codebase and an
  unauthenticated path to the process environment (any dashboard visitor
  could point `integrations.jellyfin.url` at their own host and exfiltrate
  any environment variable's value as a request header). A `config.json`
  that still carries the `integrations` key loads normally and logs one
  warning naming the removed key.

## [0.3.0] - 2026-10-22

The MetaDataScrapper era ends with this release: the tool is renamed to
**ShrinkRay** and its purpose has grown from reading video metadata to a
complete, quality-measured library shrinking pipeline.

### Added

- **Quality-measured re-encoding (the core feature)**: per-title
  VMAF-driven CRF search — short samples are probed at different CRF
  values, each scored with VMAF against its source, and a binary search
  finds the largest CRF that still meets the quality floor (default 95,
  roughly "transparent"); the movie is then encoded once with exactly
  that setting. Fixed-preset mode remains available.
- **HDR10 support**: tone-mapped (hable) VMAF measurement on both
  inputs, automatic 10-bit encoder switch (`x265_10bit`), calibrated
  HDR target and CRF range. Verified end-to-end on real content
  (The Mandalorian S01E02: 4.26 GB → 3.45 GB at full-movie VMAF 95.72).
- **Duplicate detection**: byte-identical (content hash), same-title
  and fuzzy-title grouping with duration gating; group summaries with
  wasted bytes; keep-rationale explains honestly why the best file is
  kept (byte-identical / highest resolution / highest bitrate / "no
  quality signal separates them"). DELETE moves files to the OS recycle
  bin (recoverable) and retires the row immediately; restored files
  recover on the next scan.
- **Resident app + web dashboard**: job queue (scan, plan, encode,
  dedupe) with live progress and per-movie fractions, adaptive polling
  snapshot API (one round-trip, ETag-cached library), provenance chain
  per title, config editing from the UI.
- **Generation tracking (schema v7)**: every encode records input/output
  bitrate, source/output paths and its generation number; re-encoding an
  encode chains automatically. `shrinkray compare` renders a title's
  provenance chain (the generation-drift ledger).
- **Resource politeness**: encodes, probes and VMAF measurements run
  below normal priority (config: `resources.idle_priority`); per-encode
  thread cap (`handbrake.max_threads`, CLI `--threads`); the resident
  app logs its own memory every 5 minutes and exposes `rss_mib` in the
  status API (measured: ~16-64 MiB resident).
- **Concurrent encode guard**: a pid-stamped lock file per output
  directory makes encoder collisions (job worker vs watch loop vs CLI)
  impossible instead of unlikely; stale locks of dead processes are
  stolen.
- **Opt-in Jellyfin integration**: one fire-and-forget
  `POST /Library/Refresh` after encode batches (token via environment
  variable, never in config.json).
- **Soft-missing reconciliation (schema v6)**: deleted files are flagged
  and hard-deleted only after 3 consecutive missing scans; reappearing
  files recover automatically; mass-flagging emits an accident warning.
- **Failure evidence bundles**: failed VMAF measurements and encodes
  bundle command, stderr (empty recorded explicitly), traceback and
  artifacts into `logs/` for diagnosis.
- **Windows installer**: `install.ps1` one-liner (uv, GPL ffmpeg with
  libvmaf, HandBrakeCLI, repo, dashboard build; idempotent).
- **Linux/NAS deployment**: verified end-to-end on Ubuntu (WSL
  rehearsal): full gate, VMAF measurement, headless recoverable trash,
  graceful HandBrake absence, POSIX niceness and `/proc` RSS; systemd
  unit in `deploy/`, README Linux/NAS section.

### Changed

- Rename MetaDataScrapper → ShrinkRay (package `src/shrinkray/`, CLI
  `shrinkray`, GitHub repo). All tool configs, ignores and docs swept.
- Scans are deterministic on every filesystem (sorted output; os.walk
  order is hash-ordered on ext4).
- Encode results are recorded benchmark-grade (schema v5): source
  snapshot, CRF, VMAF, probe statistics, tool versions.
- Duplicates API and UI exclude retired/missing rows.

### Fixed

- Same-thread db locking crash ("cannot release un-acquired lock").
- Live progress deadlock: subprocess pipes are drained concurrently
  while streaming stdout.
- Encode truncation: outputs are duration-verified, not trusted by exit
  code alone (HandBrake can exit 0 on failure).
- Path allowlist hardened (realpath + containment; traversal rejected).
- `os.startfile` guarded so mypy's linux view passes CI.
- Test suite uses real temp directories (fictional `D:/...` paths broke
  scope containment on Linux); scan order determinism; rename leftovers
  in tool configs (coverage target, first-party lists, gitignore model
  path).

### Security

- Supply-chain hardening: uv malware-check gate, 7-day new-package
  cooldown with security-fix exemption, CodeQL-recognized path
  sanitization.
- No secrets in tracked files: integration tokens live in environment
  variables by construction.

[0.3.0]: https://github.com/bennischober/ShrinkRay/releases/tag/v0.3.0
