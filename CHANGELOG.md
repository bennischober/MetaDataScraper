# Changelog

All notable changes to ShrinkRay are documented here. Releases are
tagged in git; the GitHub release for each tag carries the same notes.

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
