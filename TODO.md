# TODO

## Open work (what is really left)

### Workflow - needs design + explicit user sign-off
- [ ] Swap-in-place (`--replace`): completes the loop between "encode done"
      and "library shrunk" (today the swap is manual). Would verify =>
      trash source (recoverable) => move output into place. MUST have
      ceremony: explicit flag, per-file confirmation in the UI, dry-run
      mapping first. Nothing gets built before the user approves the design.

### Small, well-specified
- [ ] Jellyfin library refresh after successful encodes (opt-in, off by
      default): POST {url}/Library/Refresh with the token from the env var
      named in config; fire-and-forget with a 5s timeout - a notification
      failure must never fail an encode. Plex deliberately not implemented
      until a real Plex server exists to test against.
- [ ] HDR size-estimate correction: HDR 4K estimates need a systematic bias
      factor or bitrate-based math (probe samples hit easy content; measured
      19.1% real vs 78% predicted on The Mandalorian). Document the
      uncertainty range in plan/README.
- [ ] HDR test material in testdata/ for regression tests (short clip).
- [ ] Manual "remove from library" action (UI button + CLI flag) that deletes
      a row and its history deliberately, bypassing the grace period.
- [ ] Re-parenting: a moved file (renamed folder) re-flags under the new path
      while the old row ages out; a hash match could reconnect the encode
      history instead of starting fresh.

### UI (next round) - shared with the UI agent
- [ ] Live job progress for encodes: within-encode percent (frames encoded)
      needs a HandBrake progress parse or a size-based estimate (also: show
      done/total + current movie + ETA in the job sheet - critique P1)
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
- [ ] Swap-permission check (Send2Trash needs write access to the trash of
      the library's filesystem - same-mount trash only) and systemd unit
      smoke on real hardware. WSL rehearsal passed 2026-10-22.
- [ ] Docker image (proposal, friend request "mount /media + scheduled"):
      full trade-off map, open decisions (trash story, PUID/PGID, registry)
      and the proposed commit series live in docs/docker.md. Build only
      after explicit sign-off.

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
