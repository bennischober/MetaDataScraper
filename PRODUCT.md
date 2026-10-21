# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Lightweight framework (Preact/Svelte/Vite/Astro) served by the resident app; chosen by the user over plain HTML because the dashboard will grow interactive (live task status, actions). No build-step-heavy SPA framework. Backend: Python (FastAPI or stdlib HTTP) inside the `metadata-scrapper app` command.

## Users

The primary and only user is Benjamin — a self-hosting enthusiast managing a personal movie library (disc rips of ~30+ films and series seasons on local/external drives, G:\ etc.). He runs everything locally on his own Windows machine (16 cores, RTX 2070), is comfortable with terminals and uv, and does not want accounts or third-party services.

## Product Purpose

MetaDataScraper catalogs a personal movie library and shrinks it with measured, quality-preserving re-encoding. It answers three questions: what do I have (scan), what would optimizing achieve (plan), and how do I do it safely (encode with VMAF-verified quality targets, guardrails that skip hopeless movies, and full audit history in sqlite). Success = smaller library at perceptually transparent quality, with every decision recorded and reversible-by-rerun.

## Positioning

The storage-efficiency layer of a personal media stack: per-title, *measured* compression (VMAF-driven CRF search with skip guardrails) that no neighboring tool provides. Jellyfin/Plex serve; Radarr acquires; this tool shrinks — complementary by design, deliberately not a player, downloader, or multi-user platform.

## Operating Context

- Windows desktop, Python via uv, HandBrakeCLI + ffmpeg (gyan GPL build) as external binaries
- Movie library on external/network drives; long encodes run for hours
- The CLI (`scan`, `encode`, `plan`, `report`, `watch`) is and stays fully functional without any resident process
- A resident `app` command (autostart-able, tray-optional) owns long-running work (watch loop, encode queue); terminal and web UI are interchangeable consumers of the same engine and database
- Web UI runs on localhost only; no authentication, no cloud, no accounts

## Capabilities and Constraints

- Quality mode: VMAF-driven CRF bisect per movie (3×10s samples), guardrails: skip-if-larger, unreachable-target, minimum duration; truncation detection after every encode
- `--remove-bars`: stable baked-in black-bar cropping (irreversible, opt-in)
- sqlite database (schema v3, absolute paths) records scan cache, encode history, chosen CRF/VMAF, skips
- VMAF scores are only comparable within one libvmaf version (documented caveat)
- HDR content is NOT yet supported by quality mode (tone-mapping roadmap in TODO.md; blocked on test material)
- The 2070 GPU is irrelevant for VMAF (Turing unsupported by maintained CUDA builds); encoding stays software x265 for quality-per-bit

## Brand Commitments

Name: MetaDataScraper (historical, kept). Voice: precise, honest about limitations, no marketing fluff; documentation tone like the current README (professional, no emoji decoration per user preference).

## Evidence on Hand

- Measured results on real content: Django Unchained 5.55→1.98 GB (64.3%) at VMAF 95.25; Bloodshot 0.42→0.07 GB (82.7%); parallel run 3 movies/34.3 min
- Working HTML report (report.html, dark theme) and full test suite (49 tests) as implementation evidence
- Existing probe/plan/encode engine modules: src/metadata_scrapper/{quality,encode,watch,db}.py

## Product Principles

1. Measured, not guessed: every quality decision carries its VMAF number; nothing ships on hope.
2. The library is sacred: sources are never modified in place; guardrails refuse pointless or unsafe work; every action is auditable and reversible-by-rerun.
3. Lean personal tool: no accounts, no cloud, no daemon requirement; the CLI works alone; complexity must buy real value for the single user.
4. Honest boundaries: documented limitations (HDR, cross-version VMAF) instead of silent misbehavior.

## Accessibility & Inclusion

No specific requirements recorded yet; the web UI is a single-user localhost dashboard. Baseline: keyboard-operable controls, readable contrast (the report's dark theme already follows this).
