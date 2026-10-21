# TODO

## Known limitations / planned work

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

### HDR support (partially unblocked: real HDR content exists)
- [x] Refuse HDR content in quality mode until proper support exists
      (`hdr unsupported` skip reason, shipped 2026-10-21)
- [ ] Validate the refusal end-to-end with The Mandalorian (G:\Serien) —
      quality mode/plan on that file must skip, scan must keep detecting hdr
- [ ] Tone-map HDR sources to SDR before VMAF measurement (ffmpeg `tonemap`
      filter) and measure on the tone-mapped picture
- [ ] Ensure HandBrake keeps HDR metadata (master display flags / color
      tags) when re-encoding HDR content
- [ ] Then: allow HDR encodes with the tone-mapped measurement path

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
