# TODO

## Known limitations / planned work

### Reliability wave (DONE 2026-10-23 - the core loop's last honesty gaps)
- [x] A1 Interrupted-encode debris: a crashed/killed encode leaves a partial
      output file (non-empty, truncated). `encode_movies`' resume filter
      treats "output exists" as done => the movie is skipped forever and a
      corrupt file sits in the output dir. FIXED (`535b64f`): hold the encode
      slot BEFORE filtering (a concurrent encoder's in-flight output must not
      be mistaken for debris), then on existing output verify duration
      (`durations_match`): match => genuinely done, skip; mismatch =>
      debris from an interrupted run, remove and re-encode. ffprobe absent
      => stay conservative (skip, never delete on unverifiable). Also in
      `finished_movies` (quality-mode resume).
- [x] A2 Disk-space preflight (`b9fbfaa`): `check_disk_space()` gates
      `encode_movies` (sum of pending inputs as worst-case bound) and
      `encode_with_quality` (input size, before probing); refuse when
      free < required, warn below 1.5x, degrade gracefully on unreadable
      drives.
- [x] A3 Watch quality-mode parity (`9f2c4df`): the loop calls
      `encode_with_quality` (same skip rules via skipped_reason, HDR 10-bit
      for free); decide() keeps only cheap checks - no more full probe whose
      CRF was discarded. Watcher holds the encode slot around the call.
- [x] A4 CLI quality progress (`32c0b15`): rich Progress per quality run -
      "Probing..." until the first percent event, then a live bar;
      `_quality_progress_callback` maps the engine's percent events.
- [x] A2 follow-up, disk watchdog (`41fe9aa`): the preflight answers "will
      it fit?" at run start; the streamed encode loop now also watches
      DURING the encode (one statvfs per percent line). Free space below
      1 GiB => cancel, remove the partial output (our product from this
      run), report the reason - never fill a shared drive. Trade-off
      accepted: a near-the-end false cancel is possible. Both encode paths
      always stream now (watch mode previously encoded unwatched; fixed
      mode had a latent TypeError: the rich Progress object was passed as
      the callback and is not callable).

### Verification & health (after the reliability wave)
- [ ] Verify-after-encode hook: optional `--verify` flag running a short
      full-file VMAF check after each quality encode (sub-sampled
      `n_subsample` for very large files) - turns the one-time
      verification (scripts/verify_fullmovie.py) into a standing property
- [ ] `shrinkray doctor`: library health check - ffprobe every db row,
      report dead paths, truncated files, outputs without sources
      (mostly reuses existing primitives)

### Workflow (needs design + explicit user sign-off - closest we ever came to touching sources)
- [ ] Swap-in-place (`--replace`): completes the loop between "encode done"
      and "library shrunk" (today the swap is manual). Would verify =>
      trash source (recoverable) => move output into place. MUST have
      ceremony: explicit flag, per-file confirmation in the UI, dry-run
      mapping first. Nothing gets built before the user approves the design.

### CI/testing hygiene (small)
- [ ] Tests for `compare` and `report` commands (compare was live-smoked
      only)
- [ ] `uv lock --check` in CI (catch lockfile drift)
- [ ] CI concurrency group (two quick pushes currently run parallel full
      runs)

### Decisions (2026-10-22)
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

### Media-server integration (tier 3, in progress)
- [ ] Jellyfin library refresh after successful encodes (opt-in, off by
      default): POST {url}/Library/Refresh with the token from the env var
      named in config; fire-and-forget with a 5s timeout - a notification
      failure must never fail an encode. Plex deliberately not implemented
      until a real Plex server exists to test against.

### Linux/NAS deployment (verified 2026-10-22, WSL rehearsal)
- [x] Tier 2: full gate green on Ubuntu 20.04 (WSL), static ffmpeg +
      libvmaf measurement works, send2trash recoverable headless
      (~/.local/share/Trash), HandBrake-absent degradation OK, POSIX
      niceness + /proc RSS OK, scan->db->report end-to-end on Linux;
      README Linux/NAS section + deploy/shrinkray.service added
- [ ] On the real NAS: swap-permission check (Send2Trash needs write access
      to the trash of the library's filesystem - same-mount trash only),
      systemd unit smoke on real hardware

### Library reconciliation (shipped 2026-10-21)
- [x] Soft-missing instead of instant prune (schema v6): deleted files are
      flagged (missing_scans/missing_since) and hard-deleted with their encode
      history only after 3 consecutive scans that miss them; reappearing files
      recover automatically; upsert resets the flag; mass flagging (>20% of a
      scope in one pass) emits an accident warning (renamed folder, detached
      drive). Missing state is visible in /api/library, the dashboard library
      (MISSING badge) and the HTML report (pill + summary card)
- [ ] Manual "remove from library" action (UI button + CLI flag) that deletes
      a row and its history deliberately, bypassing the grace period
- [ ] Re-parenting: a moved file (renamed folder) currently re-flags under the
      new path while the old row ages out; a hash match could reconnect the
      encode history instead of starting fresh

### API/polling (optional - only if the system grows)
- [x] Resource politeness (shipped 2026-10-22): long children (encodes,
      probes, VMAF measurements) spawn below normal priority
      (Windows creation flag / POSIX niceness via resources.py), configurable
      via `resources.idle_priority` (default on); `handbrake.max_threads`
      caps per-encode threads (CLI `--threads`, config, resident app, watch
      loop); the resident app logs its own RSS every 5 min and exposes
      `rss_mib` in /api/status + /api/snapshot. Reason: the long-term target
      is a NAS/mini media server next to Plex/Jellyfin - the tool must be a
      polite guest there
- [ ] Delta library sync via `GET /api/snapshot?since=<iso>`: today's snapshot
      is fingerprint-skip (unchanged library = null, changed = the WHOLE
      library re-sent — no row-level deltas). The `since=` variant would send
      files WHERE scanned_at/encoded_at/missing_since > since plus tombstones
      for hard-prunes (or: only flag, never hard-prune), with a periodic full
      resync as the safety net. Sketch is ~80 lines + merge tests. Trigger
      condition: library payload exceeds ~1 MB (~5k movies) or long batch
      runs feel laggy. Until then the fingerprint design is correct — change
      events fire per encode completion, not per second, so the all-or-
      nothing cost is rare and bursty on localhost

### UI (next round)
- [x] Critique round 4 engine deferrals (shipped 2026-10-22): (a) duplicate
      keep-rationale - find_duplicates computes an honest one-sentence "why
      kept" per group (byte-identical / highest resolution / highest bitrate
      with 5% margin / "no quality signal separates them" fallback), shipped
      in the API payload as keep_rationale + per-file bitrate_mbps; the
      largest-file pick itself is unchanged. (b) Concurrent encode guard -
      encode_lock.py: pid-stamped lock file per output directory, acquired
      by all three encoder entry points (job worker, watch loop, CLI);
      second claimant gets EncodeBusyError (job skips with the reason, watch
      loop retries next pass), stale locks (dead pid) are stolen; scan+encode
      needs no guard (scans are read-only, WAL, output dirs excluded)
- [x] Critique round 2026-10-21 (impeccable dual-agent, 20/40): evidence wall
      shipped — the zone ramp plots every movie's measured VMAF as ticks;
      remove-bars no longer persists (per-run arming + confirmation strip
      naming the affected movie count); plan/encode result panels are
      always present and collapsible (no hidden showPlans state); ramp and
      strip captions say VMAF, not zones
- [ ] Design-rethink round: the user may want new prototypes for the dashboard
      (the Zone Record direction is locked, but a re-shape with new comps is
      possible at any time)
- [ ] Live job progress for encodes: the encode engine reports stage +
      per-movie fractions; a within-encode percent (frames encoded) needs a
      HandBrake progress parse or a size-based estimate (also: show
      done/total + current movie + ETA in the job sheet — critique P1)
- [ ] Job detail view in the UI (click a history row -> full payload/result)
- [ ] Per-movie actions: encode/plan a single movie from its detail drawer
- [ ] Retry from job payload verbatim (current retry re-binds the form;
      critique P1) + history filter matching all job paths (multi-folder
      batches show "no jobs" per movie today)
- [ ] A11y pass: focus traps in overlays, aria-sort/aria-pressed, 24px+ hit
      targets, larger interactive type (critique found 10px interactive
      default); fmtSaved growth sign reads as gain ("+5.0%" in a "Saved"
      column)

### HDR support (working on HDR10 — encode validated, findings recorded)
- [x] Refuse HDR content in quality mode until proper support exists
      (superseded by real support, shipped 2026-10-21)
- [x] Tone-map HDR sources to SDR before VMAF measurement (hable tonemap on
      both inputs inside the filtergraph)
- [x] 10-bit encoder switch (x265_10bit) with HDR metadata preservation
      (HandBrake keeps PQ/Main10 by default, verified)
- [x] Calibrated HDR target (94.5) and CRF range (10-32) — the Mandalorian
      plan converged at CRF 14, VMAF 94.78 (21 min plan on 4K)
- [x] Real encode run: The Mandalorian S01E02 at CRF 14 -> 4.26 GB -> 3.45 GB
      = **19.1% saved** (NOT the 78% the plan estimated!). The probe-based
      size estimate is strongly over-optimistic on HDR 4K content (same
      pattern as Django's SDR 77%-predicted/64%-real, but stronger).
      Duration verified equal (1856.576s both sides).
- [x] FULL-MOVIE VMAF verification (2026-10-22, scripts/verify_fullmovie.py):
      the probed 94.78 holds — **95.72 across the whole episode** (target
      94.5, tone-mapped measurement). The 10s probes slightly UNDER-estimate
      the full-movie quality here (easy-head samples). All four verification
      pairs pass their targets: rick 93.50@24, bloodshot 94.40@20, django
      95.36@20, mandalorian 95.72@14 — the CRF search is verified end-to-end,
      HDR included. Clean run, zero failure bundles (telemetry stayed idle)
- [ ] Size-estimate correction: HDR 4K estimates need a systematic bias
      factor or bitrate-based math (probe samples hit easy content);
      document the uncertainty range in plan/README
- [ ] Dolby Vision dual-layer handling (the DV enhancement layer may be
      lost on re-encode; warn or preserve — needs a DV test file)
- [ ] HDR test material in testdata/ for regression tests (short clip)

### Other known limitations
- [ ] VMAF scores are not comparable across libvmaf versions (documented in
      the README) — the tool measures and decides with the local toolchain,
      so only cross-machine score comparisons are affected
- [ ] The size estimate of `plan` comes from short probe samples — a ballpark,
      not a contract (grain-heavy content underestimates)
- [ ] Watch mode polls the folder (no filesystem events) — a poll interval of
      60s is a good trade-off, but event-based watching (ReadDirectoryChangesW
      / inotify) would be more responsive
- [ ] TMDB / metadata-API integration for real movie titles, posters in the
      report and duplicate detection (needs an API account — currently not
      wanted; the fast content hash already enables deduping without it)

## Ideas (not scheduled)
- [ ] Per-category breakdowns in the HTML report (drive structure)
- [ ] Sub-sampled full-movie verification as an optional QA step
      (`n_subsample` on modern libvmaf)
- [ ] GPU-accelerated VMAF when an Ampere-or-newer GPU is available
      (libvmaf_cuda is not part of stock ffmpeg builds; requires building
      ffmpeg/libvmaf from source with CUDA)
