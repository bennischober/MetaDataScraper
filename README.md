<div align="center">

# ShrinkRay

**Scan your movie library — then shrink it without losing quality.**

A Python CLI that catalogs video metadata and re-encodes your library with
Netflix-style, quality-measured encoding: smallest files that are still
perceptually transparent.

[![CI](https://github.com/bennischober/ShrinkRay/actions/workflows/ci.yml/badge.svg)](https://github.com/bennischober/ShrinkRay/actions/workflows/ci.yml)
[![CodeQL](https://github.com/bennischober/ShrinkRay/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/bennischober/ShrinkRay/actions/workflows/codeql-analysis.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://docs.astral.sh/uv/)
[![GitHub release](https://img.shields.io/github/v/release/bennischober/ShrinkRay)](https://github.com/bennischober/ShrinkRay/releases)

</div>

---

## What does it do?

**`scan`** walks your movie folders and catalogs every file: duration, codecs,
resolution, FPS, audio languages, HDR detection, black-bar detection — cached
in a sqlite database, so repeated scans are near-instant.

**`encode`** re-encodes your library with HandBrake to save disk space. The
default mode uses a fixed preset. The **quality mode** is where it gets
interesting:

> **Fix quality, not bitrate.** For every movie, short samples are probed at
> different CRF values, each probe is scored with [VMAF](https://github.com/Netflix/vmaf)
> (Netflix's Emmy-winning perceptual quality metric), and a binary search finds
> the *largest* CRF that stays visually transparent — then the movie is encoded
> once with exactly that setting.

Measured on real content (target VMAF 95, x265):

| Movie | Content | Result | Saved |
|---|---|---|---|
| Django Unchained (PAL DVD) | grainy live action | CRF 19, VMAF 95.25, letterbox bars removed | **64 %** |
| Bloodshot (alt. ending) | 1080p live action | CRF 20, VMAF 95.76 | **73 %** |
| Rick & Morty S01E01 | already-efficient HEVC | skipped — target unreachable | *(correctly)* |

Nothing is guessed: every decision is measured, recorded in the database, and
movies that cannot profit (already efficient sources) are skipped — not
wasted.

> [!NOTE]
> **HDR content is not yet supported by quality mode** — the VMAF model is
> SDR-trained and HDR sources should not be re-encoded with measured CRF
> until tone-mapping is implemented (see [TODO.md](TODO.md)).

## Quickstart

```bash
# scan a movie library
uvx shrinkray scan 'D:\Movies'

# shrink it (quality-measured, black bars removed)
uvx shrinkray encode 'D:\Movies' 'D:\Encoded' --quality --remove-bars
```

> [!TIP]
> Preview anything without writing files by adding `--dry-run`.

## Installation

<details>
<summary><strong>Option A — run directly from GitHub (no clone)</strong></summary>

```bash
uvx --from git+https://github.com/bennischober/ShrinkRay shrinkray scan 'D:\Movies'
```

</details>

<details>
<summary><strong>Option B — clone the repository</strong></summary>

```bash
git clone https://github.com/bennischober/ShrinkRay.git
cd ShrinkRay
uv sync
uv run shrinkray scan 'D:\Movies'
```

</details>

### Prerequisites

| Tool | Needed for | Install |
|---|---|---|
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Python & dependencies (manages everything else) | `winget install astral-sh.uv` |
| [FFmpeg](https://www.ffmpeg.org/download.html) | scanning, bar detection, VMAF scoring (GPL build on Windows) | `winget install Gyan.FFmpeg` |
| [HandBrake CLI](https://handbrake.fr/downloads.php) | re-encoding (optional) | `winget install HandBrake.HandBrake` |

## How it works

1. **Discovery** — the library is walked recursively for configured extensions
   (`config.json`), with the output directory always excluded.
2. **Metadata** — `ffprobe` extracts stream details; duration, aspect ratio and
   size are computed and cached (unchanged files skip probing entirely).
3. **Black-bar detection** — `cropdetect` samples several segments per movie;
   a threshold keeps noise pixels from being mistaken for bars.
4. **Quality search** *(quality mode)* — three 10-second samples are encoded at
   different CRF values and scored with VMAF against their source; a binary
   search converges on the largest CRF that still meets the quality floor.
5. **Encode & verify** — the full movie is encoded once with the winning CRF;
   the output duration is verified against the source to catch interrupted
   encodes.

## Commands

### `scan` — catalog your library

```bash
uv run shrinkray scan 'D:\Movies' --csv
```

| Option | Effect |
|---|---|
| `--csv` | additionally write the results as a CSV file |
| `--output / -o` | path of the output file |
| `--workers / -w N` | parallel workers (`0` = auto, `1` = sequential) |
| `--no-table` | skip the console table |
| `--rescan` | force a full re-probe, ignoring the cache |
| `--hash` | store a fast content hash (first/last MB) |

Scans are cached in `metadata.db` (sqlite): unchanged files (same mtime and
size) are not probed again — repeated scans are much faster.

### `encode` — shrink your library

```bash
# fixed preset (fast, predictable)
uv run shrinkray encode 'D:\Movies' 'D:\Encoded'

# quality-measured (smaller, transparent, slower)
uv run shrinkray encode 'D:\Movies' 'D:\Encoded' --quality

# with options
uv run shrinkray encode 'D:\Movies' 'D:\Encoded' \
    --quality --remove-bars --target-vmaf 95 --encoder x265
```

| Option | Effect |
|---|---|
| `--quality` | VMAF-driven CRF search per movie (see above) |
| `--target-vmaf N` | quality floor for quality mode (default: `95`) |
| `--remove-bars` | detect and crop stable baked-in black bars (irreversible, opt-in) |
| `--dry-run` | show what would be encoded, without encoding |
| `--overwrite` | re-encode even if the output already exists |
| `--workers / -w N` | concurrent encodes, each capped to a CPU share (default: sequential — one x265 encode already uses all cores) |

**Batch-safe:** every finished encode is recorded, so an interrupted run skips
its completed work on the next start. Movies whose quality target is provably
unreachable (e.g. already-efficient HEVC) or that would not shrink are skipped
and remembered — future runs do not re-probe them.

### `plan` — look before you leap

```bash
uv run shrinkray plan 'D:\Movies' --remove-bars
```

Runs the same probing pipeline as a quality encode (samples, CRF search, bar
detection) and prints the predicted outcome per movie — encode or skip, with
CRF, VMAF and an estimated size — plus the estimated total savings. Use it to
decide whether (and with which options) a real encode run is worth it. The
size estimate comes from the short probe samples, so treat it as a ballpark;
the probing predicts the decision, the encode measures it — the chosen CRF
can differ by ±1 between runs.

### `report` — the library as one page

```bash
uv run shrinkray report --output report.html
```

Generates a self-contained HTML page (dark theme, no external assets) from
the database: summary cards, a per-category breakdown and a per-movie table
with decision, sizes, CRF and VMAF. Read-only — regenerate any time.

### `app` — the resident application

```bash
uv run shrinkray app            # http://127.0.0.1:8420
```

Starts the resident application: a local server owning the job queue and the
watch loop, plus the **web dashboard** (open the printed URL). Terminal and
browser are just two consumers of the same engine and database:

- the dashboard shows live job status (running/queued with progress and
  elapsed time), the scanned library with decision chips, plan/encode results
  and job history
- jobs are enqueued from the UI (multi-directory: add several movie folders)
  or via the API (`POST /api/jobs`)
- the watch loop can be started/stopped from the UI or
  `POST /api/watch/start|stop`
- the configuration (`config.json`) is editable in the UI's settings dialog
- jobs survive restarts; jobs interrupted by a restart are marked as failed

The server binds to localhost only — there is no authentication, so do not
expose it to the network. Stop with Ctrl+C; the running job's state is
recovered on the next start.

### `watch` — one-shot folder watcher

```bash
uv run shrinkray watch 'D:\Downloads' 'D:\Encoded' --remove-bars
```

The standalone variant of the app's watch loop: polls the inbox folder for
new movie files, waits until they are stable (download/copy finished), applies
the same skip logic and encodes them — **sources stay untouched**. Use the
`app` command instead when you want this to run continuously alongside the
dashboard.

> [!NOTE]
> VMAF scores are only comparable within the same libvmaf version: the same
> encode scored 95.76 on ffmpeg 5 (libvmaf v2) and 94.40 on ffmpeg 9 (libvmaf v3).
> Decisions stay consistent because probing measures and decides with the local
> toolchain.

## Configuration

Optional [config.json](config.json) (defaults are used when missing):

```jsonc
{
    "file": {
        "extensions": [".mkv"],
        "output": { "seperator": ";" }
    },
    "ffmpeg": {
        "black_bars": { "MIN_WIDTH": 100, "MIN_HEIGHT": 100 }
    },
    "handbrake": {
        "preset": "Fast 1080p30",   // fixed-mode preset
        "encoder": "x265"
    },
    "codec": { "hdr": { "color_space": [], "color_transfer": [] } },
    "language": "en"
}
```

## Development

A [devcontainer](.devcontainer/devcontainer.json) is included (Ubuntu with uv,
ffmpeg and handbrake-cli) - open in VS Code and reopen in the container.

### Web UI (ui/)
The dashboard is a Svelte 5 + Vite + TypeScript app in `ui/`. Node.js is only
needed for *developing or rebuilding* the UI - using the tool does not require
it:
```bash
cd ui
npm install        # once
npm run dev        # dev server with hot reload (API proxied to :8420)
npm run build      # production build to ui/dist (served by the app)
npm run check      # svelte-check (type checking)
```

```bash
uv run pytest          # tests (with coverage report)
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy            # static type checking
```

Releases are automated: push a tag, the workflow checks, builds and publishes.

```bash
git tag v0.3.0
git push origin v0.3.0
```

## FAQ

<details>
<summary><code>python</code>/<code>uv</code> is not found in the command prompt</summary>

Your PATH is missing the install location — [see this answer](https://stackoverflow.com/a/13596981).
</details>

<details>
<summary><code>No such filter: 'cropdetect'</code></summary>

On Windows you need the **GPL build** of
[ffmpeg](https://github.com/BtbN/FFmpeg-Builds/releases) (the LGPL build lacks
the filter). On Linux/UNIX [this might help](http://ffmpeg.org/pipermail/ffmpeg-user/2015-October/028753.html).
</details>

<details>
<summary><code>'HandBrakeCLI' not found!</code></summary>

Install [HandBrake CLI](https://handbrake.fr/downloads.php) and make sure it is
on your `PATH` (the tool also checks the default install locations).
</details>

## Resources

- [FFmpeg documentation](https://ffmpeg.org/ffmpeg.html) ·
  [cropdetect filter](https://ffmpeg.org/ffmpeg-filters.html#cropdetect) ·
  [libvmaf](https://github.com/Netflix/vmaf)
- [HandBrake CLI options](https://handbrake.fr/docs/en/latest/cli/cli-options.html)
- [typer](https://typer.tiangolo.com/) ·
  [rich](https://rich.readthedocs.io/) ·
  [loguru](https://loguru.readthedocs.io/) ·
  [uv](https://docs.astral.sh/uv/)
