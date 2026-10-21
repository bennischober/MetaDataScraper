# TODO

## Known limitations / planned work

### UI (next round)
- [ ] Design-rethink round: the user may want new prototypes for the dashboard
      (the Zone Record direction is locked, but a re-shape with new comps is
      possible at any time)
- [ ] Live job progress for encodes: the encode engine reports stage +
      per-movie fractions; a within-encode percent (frames encoded) needs a
      HandBrake progress parse or a size-based estimate
- [ ] Job detail view in the UI (click a history row -> full payload/result)
- [ ] Per-movie actions: encode/plan a single movie from its detail drawer

### HDR support (blocked on test material)
- [ ] Validate quality mode with real HDR content (HDR10 / Dolby Vision) —
      waiting for the external drive with HDR movies to come back
- [ ] The standard VMAF model (`vmaf_v0.6.1`) is trained on SDR content and
      produces misleading scores on HDR — options:
      - tone-map HDR sources to SDR before measurement (ffmpeg `tonemap`
        filter) and measure on the tone-mapped picture
      - or switch to a HDR-aware metric (e.g. CAMBI for banding as a
        complement) for HDR titles
- [ ] Ensure HandBrake keeps HDR metadata (`--all-audio` analog for master
      display flags / color tags) when re-encoding HDR content
- [ ] `scan` already detects HDR via `codec.hdr` config values; the encode
      path should refuse (or warn) for HDR content until this is done

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
