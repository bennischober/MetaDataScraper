# Docker deployment (proposal - not built yet)

Status: **target install shape**. Recorded 2026-10-23, promoted from
"proposal" the same day when the product direction was set: ShrinkRay is meant
to run as a container on a server, pointed at a media folder, unattended. The
Dockerfile is still unbuilt — this file keeps the trade-offs and open
decisions in one place. Tracked as GitHub issue #34.

## The ask

Run ShrinkRay in a container on a NAS/mini-PC: bind-mount the media
library (`/media`), let the existing watcher (or cron) drive the
scan/encode cycle, dashboard reachable over the network.

## Verdict

A good fit that is not free. ShrinkRay is subprocess-driven (ffprobe,
ffmpeg, HandBrakeCLI), headless-capable, stateless except sqlite +
config, and already a polite guest (nice, thread caps, pid/encode
locks, WAL). All of that ports cleanly into a container. What does not
port for free: ownership/permissions and the "second artifact to keep
fresh" cost. Details below.

## Why the container fits

- **Subprocess model is container-native.** The binaries just get baked
  into the image; the Python code only orchestrates.
- **Deployment simplicity is real.** `docker run -v /media:/media` plus
  one env/config volume is a lower barrier than the systemd + uv path
  (deploy/shrinkray.service) for a friend's NAS.
- **Reproducibility.** The exact toolchain (ffmpeg + libvmaf version,
  HandBrake version) is pinned per image instead of varying per host -
  VMAF scores are only comparable within one toolchain (README), so a
  pinned image actually *improves* measurement consistency.
- **Nearly container-ready already.** Env-var-based secrets (config.json
  names the env var), headless web dashboard, watch loop with stability
  checks and adaptive backoff, single-writer encode locks.

## What genuinely changes (caveats)

1. **Ownership/permissions move into the container.** The process runs
   as some UID that must read/write the bind mount. On Synology/QNAP
   this is the classic PUID/PGID dance (linuxserver-style images solve
   it with s6 / entrypoint user-fixing). Needs a deliberate design.
2. **Trash/recycle semantics break.** No longer applicable: ShrinkRay does not
   delete source files at all, and the last `send2trash` user (the duplicates
   delete route) is gone with issue #37. Retained originals are ordinary files on the
   library volume, so they work in a container without a host trash mount. This
   also simplifies the swap-in-place design — the original is renamed aside, not
   trashed.
3. **Scheduling becomes deployment, not code.** Watcher (preferred:
   stability checks, adaptive backoff) or cron both work in-container;
   either way some logic moves into the friend's runtime. Needs docs.
4. **Second artifact to keep fresh.** The no-PyInstaller decision
   (2026-10-22) rejected frozen binaries partly to avoid maintaining a
   second distribution artifact. A container image is a different kind
   of artifact (environment, not binary) but the maintenance cost
   argument still applies: every toolchain update = image rebuild +
   registry push. Needs an explicit amendment to the Decisions list,
   not silent drift.
5. **Image weight.** ffmpeg (full build ~80 MB) + HandBrakeCLI + Python
   + uv lands around 1-2 GB. Fine on a NAS with big disks; still a
   real download per update.
6. **Persistence design.** sqlite db + config.json belong in a volume
   (`/config`), the library stays on the bind mount; never bake state
   into the image (db is user data, not source).
7. **One container per output directory.** The pid/encode locks work,
   but they are per-output-dir files on the shared mount - two
   containers on the same output dir are the same conflict the locks
   already handle (EncodeBusyError), not a corruption risk. Document
   "one watcher per library" anyway.

## Not problems

- Resource politeness: `nice` works in containers; thread caps are
  config; RSS self-monitoring reads /proc (works on Linux).
- os.startfile (open dashboard) is Windows-only and already guarded.
- Network: the dashboard is a plain web UI, ports are publishable.
- Tests: the suite runs without ffmpeg (by-design skips) and the CI
  mirror proved a bare-Linux toolchain works.

## Open decisions (needed before building)

1. **Trash story in containers:** no longer a decision. ShrinkRay never deletes a
   source; replaced originals are renamed aside and retained, so no host trash
   mount is needed. The remaining question is where retained originals live (a
   subdirectory of the library, or a configured path) and how the operator is
   told they can be removed.
2. **PUID/PGID:** fixed non-root user in the image (simple, needs
   `--user` discipline) vs entrypoint-driven user fixing
   (linuxserver-style, more magic)?
3. **Decisions-list amendment:** "Docker is an official deployment
   channel alongside systemd + uv install" - yes/no.
4. **Registry:** CI builds the image (build-only, no push) or publishes
   to ghcr.io on tags (needs a workflow + secrets)?

## Sketch (for the eventual build)

Multi-stage Dockerfile (uv base -> deps layer -> app layer), roughly:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.15-bookworm-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.15-slim-bookworm
# ffmpeg + HandBrakeCLI from debian repos; libvmaf included in ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg handbrake-cli && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/.venv /app/.venv
COPY src /app/src
ENV PATH="/app/.venv/bin:$PATH"
# entrypoint: PUID/PGID fixup, then exec the requested mode
COPY deploy/docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

Compose example shape:

```yaml
services:
  shrinkray:
    build: .
    ports: ["8080:8080"]
    environment:
      - PUID=1000
      - PGID=1000
      - SHRINKRAY_MODE=watch   # or serve / cron
    volumes:
      - /media:/media          # the library (read/write for encodes)
      - ./config:/config       # config.json + metadata.db
      - ./retained:/retained   # originals renamed aside, awaiting manual removal
```

Entrypoint modes: `serve` (resident app: dashboard + jobs), `watch`
(inbox watcher loop), `cron` (scan/plan/encode batches via supercronic
or crond). Default: `serve`.

## Proposed commit series (when approved)

1. `build(docker): image with pinned ffmpeg/handbrake toolchain` -
   Dockerfile + entrypoint + .dockerignore
2. `build(docker): compose example + container docs` - compose file,
   docs updates, PUID/PGID decision applied
3. `ci: docker build job (build-only)` - or registry push on tags,
   per decision 4

## Related

- deploy/shrinkray.service - the existing systemd path (works today)
- TODO.md - pointer kept under Linux/NAS deployment
- TODO.md Decisions - the no-PyInstaller decision this extends
- GitHub issue #35 (swap-in-place with a retained original) - decides where
  retained originals live, which sets whether the volume story needs a separate
  mount. Decide the two together.
