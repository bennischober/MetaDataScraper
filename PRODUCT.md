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

ShrinkRay answers one question per file: **is this file bigger than its quality
requires?** When the answer is yes, it re-encodes the file smaller at the same
measured perceptual quality (VMAF-driven CRF search), and when the answer is no it
leaves the file alone and records the verdict so the work is never repeated.

Its second, narrower job is to remove stable baked-in black bars when the operator
asks for it.

It is the collection-scale form of a single-video quality search: the same measured
judgment that `ab-av1` or a careful HandBrake run gives one file, applied
unattended to an entire library, with a watchdog for new arrivals and a record of
every decision.

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
already done, upstream. ShrinkRay's only opinion about a file is its **size relative
to its quality**.

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
- New files appear continuously. A watchdog notices stable new arrivals and handles
  them without being asked.

## Capabilities and Constraints

- **Quality mode**: VMAF-driven CRF search per file (sampled probes, measured
  quality), then one full encode at the winning setting. The decision statistic and
  sampling density are one coupled decision, not two.
- **Skip guardrails**: already-efficient sources, unreachable targets, too-short
  files, and encodes that would not shrink are skipped and remembered.
- **Source-codec gate**: only sources where re-encoding can pay are candidates.
- **Black-bar removal** (`--remove-bars`): stable baked-in bars are cropped;
  opt-in, and irreversible in the sense that the crop cannot be undone from the
  output alone.
- **Swap-in-place with a retained original**: a verified output takes the original's
  path; the original is *renamed aside*, never deleted. Replaced originals are listed
  in the UI for the operator to delete manually. Nothing in ShrinkRay ever deletes a
  source file.
- **Watchdog**: stable new arrivals are picked up automatically; interrupted work is
  recovered; finished work is never redone.
- **Audit**: sqlite records every scan, verdict, encode, and replacement, so any
  decision can be explained and any run repeated.
- **Authentication: none today.** The dashboard has no auth and must not be exposed
  beyond a trusted network. This is a deliberate development-stage trade-off and a
  known gap, not a design position — see the roadmap item for auth.
- HDR10 is supported by quality mode; Dolby Vision dual-layer is not yet.
- VMAF scores are only comparable within one libvmaf version.

## Brand Commitments

Name: ShrinkRay (historical, kept). Voice: precise, honest about limitations, no
marketing fluff; documentation tone like the current README (professional, no emoji
decoration per user preference).

## Evidence on Hand

- Measured results on real content: Django Unchained 5.55→1.98 GB (64.3%) at VMAF
  95.25; Bloodshot 0.42→0.07 GB (82.7%); a parallel run of 3 movies in 34.3 min.
- An already-efficient HEVC source is correctly *skipped* rather than re-encoded.
- Working dashboard (Svelte 5, dark/light), an HTML report, and a per-module test
  suite as implementation evidence.
- Engine modules: `src/shrinkray/{quality,encode,watch,jobs,db,app}.py`.

## Product Principles

1. **Measured, not guessed.** Every quality decision carries its VMAF number;
   nothing ships on hope, and a size estimate is labelled as an estimate.
2. **The library is sacred.** No source file is ever deleted by ShrinkRay. A
   replaced original is renamed and retained until the operator removes it.
3. **One question, answered honestly.** "Is this file bigger than its quality
   requires?" — including the honest answer "no, leave it alone", recorded so it is
   never asked twice.
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
