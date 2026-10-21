# TODO

## Known limitations / planned work

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

### UI (next round)
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
- [ ] FULL-MOVIE VMAF verification of that encode (running) — does the
      probed 94.78 hold across the episode?
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
