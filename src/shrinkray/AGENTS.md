# src/shrinkray — package internals

Why this file: 18 modules, ~11.4k LOC, one flat package where module choice is the
whole design decision (score 12: code ratio, own config, package boundary, 227 symbols,
>10 exports, imported by every entry point). `models/` holds one data file and earns no
file of its own.
Root AGENTS.md covers the product rules and the gate; this file is the module map.

## MODULE MAP

| Question | Module | Notes |
|---|---|---|
| CLI command, output tables | `__main__.py` (1067 L) | typer app; `scan/encode/plan/report/compare/doctor/watch/app`; no business logic |
| HTTP + dashboard | `app.py` (1311 L) | FastAPI, 16 routes incl. `/api/events` SSE, `/api/reveal`, static UI |
| Config schema and defaults | `config.py` (423 L) | pydantic `ConfigModel`, `Conf` flat facade, `resolve_config_path` |
| sqlite schema, migrations, queries | `db.py` (1082 L) | `SCHEMA_VERSION = 13`, `_migrate` |
| HandBrake invocation, stall watchdog, disk floor | `encode.py` (1029 L) | `_encode_with_command`, `find_handbrake`, `durations_match` |
| VMAF measure, crop detect, sampling, CRF search, plan cache | `quality.py` (2821 L) | `plan_quality`, `encode_with_quality`, `bisect_crf`, `SkipEncode` |
| One-encoder-per-output-dir lock | `encode_lock.py` (156 L) | `guard_encodes` context manager; 6 h stale window |
| Job queue, worker thread, live progress | `jobs.py` (989 L) | `JobQueue` (persisted), `ProgressBus` (in-memory, bounded) |
| Batch orchestration + result persistence | `service.py` (520 L) | `record_plan`, `save_encode`, `save_skip`, `doctor` |
| Inbox watchdog | `watch.py` (362 L) | `watch()`, `decide`, stability + lock |
| ffprobe parse | `media.py` (327 L) | `run_ffprobe`, `get_data` |
| Scan -> DB rows | `scanner.py` (404 L) | `scan_movies`, thread pool `min(8, cpu)` |
| HTML report | `report.py` (259 L) | self-contained, reads sqlite directly |
| Priority / nice / RSS | `tools/process.py` (moved from `resources.py`, #97 phase 2; transitional shim in `resources.py`) | `apply`, `spawn_kwargs` |
| Failure bundles | `telemetry.py` (99 L) | `save_failure` -> `logs/`, best-effort by design |
| Logging | `logger.py` (23 L) | loguru; `log`/`log_warning`/`log_error` |
| Grab bag (dissolving, #63) | `helpers.py` (257 L) | imported only by `__main__.py` and `scanner.py` |

`models/vmaf_v0.6.1.json` is shipped data (`quality.py:74` `VMAF_MODEL`), not source;
`py.typed` makes the package's annotations visible to consumers.

## IMPORT DIRECTION (target: #63 layers)

Today it is one flat package; the agreed target is `tools/` -> `engine/` -> `server/`.
Real edges that matter: `quality` -> `encode` + `media` + `resources` + `telemetry`;
`watch` -> `quality` + `encode_lock`; `jobs` -> `quality` + `encode` + `config`;
`app` -> `jobs` + `watch` + `report`; `service` -> `db` + `encode`.
`db`, `report`, `logger`, `helpers`, `resources` import no sibling.
`jobs.py` already does function-level imports inside `execute_*` — that is the
existing shape, not licence for a new upward edge.

## CONVENTIONS LOCAL TO THE PACKAGE

- `quality.py` is the engine: anything that measures or predicts lives here, never
  in `__main__.py` or `app.py`. `__main__.py` and `jobs.py` deliberately duplicate
  the per-movie loop (progress rendering differs); keep the decision in the engine.
- `Conf` exposes flat attributes (`config.handbrake_encoder_preset`); there is no
  `config.handbrake.encoder` access path outside the pydantic model.
- Every long child goes through `resources.spawn_kwargs()`; short ffprobes stay at
  normal priority.
- Encode slot: any new entry point that writes into an output dir must wrap its work
  in `encode_lock.guard_encodes(...)` — the CLI, `encode.py` and `watch.py` all do.
- Tests patch module-qualified names (`mock.patch("shrinkray.encode.find_handbrake")`);
  moving a function between modules breaks the tests' patch targets, not their logic.

## GOTCHAS

- `DEFAULT_TARGET_VMAF = 95.0` (`quality.py:120`) and the CLI `--target-vmaf`
  default is 95.0, while PRODUCT.md/README say "96 by default". Quote the constant,
  not the prose, and treat the mismatch as a doc defect to fix, not a code target.
- `CANDIDATE_CODECS = ("h264",)` and `MODERN_CODECS = ("hevc", "av1")`: HEVC/AV1
  are only candidates when bars must be removed.
- HDR switches the engine to `x265_10bit` and a lower VMAF floor (94.5); the probe
  preset differs from the final preset by design (#22) and the plan fingerprint keys on it.
- `quality.py` (2821 L) and `app.py` (1311 L) are the two files where a change
  deserves an issue first — a small edit near the CRF search or a route's containment
  check can silently change what every other path measures.
