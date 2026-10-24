# Security Policy

## Supported versions

Only the latest release (and `main`) receives security fixes. ShrinkRay is a
self-hosted tool; if you run an older release, update before reporting a
problem that a newer version may already have fixed.

## Reporting a vulnerability

Please report privately through
[GitHub Security Advisories](https://github.com/bennischober/ShrinkRay/security/advisories/new)
("Report a vulnerability"). Do not open a public issue for anything you
believe is exploitable.

What to include: the affected version or commit, how to reproduce it, and —
for anything involving a crafted media file — the smallest file that triggers
it. A report that cannot be reproduced will be closed as unclear after a
reasonable effort, so the reproduction matters more than the write-up.

## Response expectations

This is a best-effort, single-maintainer project: expect an initial response
within about a week. There is no bounty and no formal SLA. Fixes land through
the normal pull-request workflow; a fix release follows as soon as the fix is
verified. You will be credited in the release notes unless you ask not to be.

## Current security posture (read before exposing the dashboard)

The resident server binds to `127.0.0.1` and has **no authentication**. That
is a deliberate development-stage trade-off, tracked as issue #36. Do not
expose it to an untrusted network. Until authentication lands, anyone who can
reach the port can use **every** API route, including these:

- `POST /api/duplicates/delete` — moves library files to the OS trash without
  authentication. (The dedupe subsystem's removal is tracked in issue #37.)
- `PUT /api/config` — rewrites `config.json` without authentication.

Binding the server to a LAN interface is your decision; the above is what it
currently means. This section must stay in sync with the README's dashboard
section — if one changes, change both.

## Scope notes

- ShrinkRay shells out to `ffprobe`, `ffmpeg` and `HandBrakeCLI`; all
  subprocess calls use argument lists (never shell strings) and timeouts.
- Media file paths from API requests are validated against an allowlist of
  configured roots before any file operation (`validate_path` in
  `src/shrinkray/app.py`).
- Source media files are never deleted; replaced originals are renamed aside
  and kept.
