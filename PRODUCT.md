# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

A Svelte 5 + Vite dashboard served by the resident Python app (FastAPI), plus a
first-class CLI. The backend is an orchestrator: all heavy work is delegated to
external binaries (`ffprobe`, `ffmpeg`, `HandBrakeCLI`) via `subprocess`. No
build-step-heavy SPA framework; the dashboard is a control surface, not the product.

## Users

One operator with a large, already-collected video library on a home server or NAS
(tens to thousands of files, on local disks or a network share). They already have
automation that acquires and names their files; ShrinkRay deliberately knows nothing
about it. They are comfortable with terminals and containers, want unattended
operation, and will not accept a tool that can lose a file.

## Product Purpose

ShrinkRay answers two questions per file:

1. **Is this file bigger than its quality requires?** Asked only of H.264 sources.
   When the answer is yes, it re-encodes the file smaller at the same measured
   perceptual quality (VMAF-driven CRF search), and when the answer is no it leaves
   the file alone and records the verdict so the work is never repeated.
2. **Does this file carry baked-in black bars?** That is, does the frame's aspect
   ratio differ from the aspect ratio of its actual content? Asked only when the
   operator opts in (`--remove-bars`). Stable bars found are cropped away during the
   re-encode.

Before either question is asked, a file must be worth touching at all:

- **Minimum resolution: 1080p.** Below that, a re-encode saves too little to be
  worth the work.
- **Minimum codec: H.264.** Sources in an older codec are not re-encoded.

Among eligible files, the source codec decides what triggers a re-encode. An H.264
source is a candidate on size alone (and has its bars removed too when opted in). An
HEVC or AV1 source is already efficient, so size alone never triggers a re-encode; it
becomes a candidate only when it has black bars to remove, and a modern-codec file
without bars is left untouched.

There is exactly **one way to re-encode**: a VMAF-driven CRF search against a required
quality floor (VMAF 96 by default), then one full encode at the CRF it found. The
output is always **AV1, encoded on the CPU** — no other output codec and no hardware
encoder. Both 8-bit and 10-bit sources are supported. Only the video stream is
re-encoded: every audio and subtitle track is carried over 1:1 from the source,
untouched. A bar
removal goes through the same search. There is no fixed-preset or "just crop it"
encode — an encode whose quality is not measured would miss the point of the tool.

In practice it fuses two steps an operator would otherwise run by hand per file — an
`ab-av1`-style CRF search, then the encode at the CRF it found — into one, and applies
that to an entire library on its own. It is meant to be **fire and forget**: point it
at the library once, and it works through the backlog, picks up new and changed files
through its watchdog, and keeps a record of every decision so nothing is done twice.

The watchdog drives the whole loop. It watches the library folder; when a file
appears or changes, it scans that file (codec, resolution, black bars and the rest
of its properties), writes the result to ShrinkRay's own database, and decides again
from the fresh scan whether a re-encode is needed. A changed file is a new question:
its earlier verdict no longer applies.

Success = the library is smaller, every re-encoded file is provably no worse than its
source, every untouched file has a recorded reason, and nothing was ever lost.

**This is not a hobby project.** It is meant to be given to the community: installed
by strangers on their own servers, pointed at libraries they cannot re-acquire, and run
unattended for hours. That is a different obligation from a personal script — a defect
here can destroy someone else's data. The engineering standard this implies (secure at
every boundary, correct under interruption, honest about failure, tested where it
matters, readable by a stranger, no unsupported claim in a doc) is written down as
non-negotiable in [AGENTS.md](AGENTS.md).

## Positioning

The storage-efficiency layer of a self-hosted media stack: per-file, *measured*
compression that no neighbouring tool provides. Media servers play; acquisition tools
acquire and name; this tool shrinks — complementary by design, and deliberately
nothing else.

**It is not a library manager.** It does not name, rename, move, organize, match,
scrape, deduplicate, acquire, play, or serve media. Those are somebody else's job,
already done, upstream. ShrinkRay's only opinions about a file are its **size relative
to its quality** and whether its **frame matches its content**.

## Operating Context

- Headless Linux server or NAS, run as a container (the target install) or from a
  source checkout with `uv`; the CLI also runs on a Windows workstation for
  development and for one-off runs.
- The library arrives as one bind-mounted directory tree. ShrinkRay reads it, decides
  per file, and writes only inside it.
- HandBrakeCLI + ffmpeg (with `libvmaf`) are external binaries baked into the
  image; a pinned image is also what makes VMAF scores comparable across runs.
- Encodes run for hours. The service must be a polite guest: idle priority, thread
  caps, and a stall watchdog, so playback on the same box keeps its cores.
- The dashboard is reached over the LAN. The CLI is a first-class product: every
  capability is available without the resident process, and automation may drive it
  alone.
- Files appear and change continuously. A watchdog notices every stable new or
  changed file, re-scans it, and re-decides it without being asked.

## Capabilities and Constraints

- **One encode mode**: VMAF-driven CRF search per file against the required VMAF
  (96 by default; sampled probes, measured quality), then one full encode at the
  winning setting. Every re-encode, bar removal included, goes through it. The
  decision statistic and sampling density are one coupled decision, not two.
- **Output format**: AV1 only, CPU encoder only. 8-bit and 10-bit sources are both
  supported.
- **Video only**: audio and subtitle tracks are copied 1:1 from the source, never
  re-encoded, converted or dropped.
- **Skip guardrails**: already-efficient sources, unreachable targets, too-short
  files, and encodes that would not shrink are skipped and remembered.
- **Eligibility limits**: files below 1080p or in a codec older than H.264 are
  never re-encoded.
- **Source-codec gate**: H.264 sources are candidates on size alone. HEVC and AV1
  sources are candidates only when they have black bars to remove; without bars,
  they are not re-encoded.
- **Black-bar removal** (`--remove-bars`): stable baked-in bars are cropped;
  opt-in, and irreversible in the sense that the crop cannot be undone from the
  output alone.
- **Swap-in-place with a retained original**: a verified output takes the original's
  path; the original is *renamed aside*, never deleted. Replaced originals are listed
  in the UI for the operator to delete manually. Nothing in ShrinkRay ever deletes a
  source file.
- **Watchdog**: stable new and changed files are scanned into the database and
  re-decided automatically; interrupted work is recovered; finished work on an
  unchanged file is never redone.
- **Audit**: sqlite records every scan, verdict, encode, and replacement, so any
  decision can be explained and any run repeated.
- **Authentication: none today.** The dashboard has no auth and must not be exposed
  beyond a trusted network. This is a deliberate development-stage trade-off and a
  known gap, not a design position — see the roadmap item for auth.
- HDR10 is supported; Dolby Vision dual-layer is not yet.
- VMAF scores are only comparable within one libvmaf version.

## Brand Commitments

Name: ShrinkRay (historical, kept). Voice: precise, honest about limitations, no
marketing fluff; documentation tone like the current README (professional, no emoji
decoration per user preference).

## Evidence on Hand

- Measured results on real content (x265 output; AV1 output is not measured yet):
  Django Unchained 5.55→1.98 GB (64.3%) at VMAF 95.25; Bloodshot 0.42→0.07 GB
  (82.7%); a parallel run of 3 movies in 34.3 min.
- An already-efficient HEVC source is correctly *skipped* rather than re-encoded.
- Working dashboard (Svelte 5, dark/light), an HTML report, and a per-module test
  suite as implementation evidence.
- Engine modules: `src/shrinkray/{quality,encode,watch,jobs,db,app}.py`
  (agreed target layout: `tools/`, `engine/`, `server/` subpackages — issue #63).

## Product Principles

1. **Measured, not guessed.** Every quality decision carries its VMAF number;
   nothing ships on hope, and a size estimate is labelled as an estimate.
2. **The library is sacred.** No source file is ever deleted by ShrinkRay. A
   replaced original is renamed and retained until the operator removes it.
3. **Two questions, answered honestly.** "Is this file bigger than its quality
   requires?" and "Does it carry black bars its content does not need?" — including
   the honest answer "no, leave it alone", recorded so it is never asked twice.
4. **Nothing outside its lane.** No naming, moving, organizing, matching,
   deduplicating, acquiring, or serving. If a feature needs to know what a file is
   *called* or *which* file it is, it belongs to another tool.
5. **Unattended by default, inspectable always.** It runs without a babysitter
   and explains every decision it made, on the dashboard and in the CLI.
6. **Built to be trusted by strangers.** Secure at every boundary, correct under
   interruption, and honest about failure — the standard in AGENTS.md, not a
   preference. Community software that mishandles someone's library is worse than no
   software.
7. **Honest boundaries.** Documented limitations (HDR, cross-version VMAF,
   missing auth) instead of silent misbehaviour.

## Accessibility & Inclusion

No specific requirements recorded yet; the dashboard is a single-operator LAN
surface. Baseline: keyboard-operable controls, readable contrast, and the dark/light
theme pair already implemented.
