"""The resident application: HTTP API over the engine and job queue.

The app owns long-running work (the job queue and the watch loop); the CLI
stays fully functional without it. Consumers (web UI, curl, scripts) talk
to the local HTTP API — same engine, same database, same guardrails.

Run with `shrinkray app`.
"""

import json
import os
import os.path
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from shrinkray import db
from shrinkray.dedupe import find_duplicates
from shrinkray.jobs import Job, JobKind, JobQueue, JobWorker
from shrinkray.logger import log, log_error
from shrinkray.report import generate_report
from shrinkray.watch import watch

app = FastAPI(title="ShrinkRay", version="0.3.0")


@app.middleware("http")
async def log_requests(request, call_next):
    """Logs every request with duration and status (the app's access log).

    Args:
        request: The incoming request.
        call_next: The next middleware/handler.

    Returns:
        The response.
    """
    import time as _time

    start = _time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as e:
        duration = (_time.perf_counter() - start) * 1000
        log_error(
            f"{request.method} {request.url.path} crashed after {duration:.0f}ms: "
            f"{type(e).__name__}: {e}"
        )
        raise
    duration = (_time.perf_counter() - start) * 1000
    if request.url.path.startswith("/api"):
        log(f"{request.method} {request.url.path} -> {response.status_code} ({duration:.0f}ms)")
    return response


# paths the API may operate on. Requests can only address folders inside
# these roots (plus the roots themselves) => the API cannot be tricked into
# scanning, writing or exposing arbitrary filesystem locations. Set via
# allow_roots() at startup, derived from the database location by default.
_allowed_roots: list[Path] = []


def allow_roots(roots: list[Path]):
    """Sets the folder roots the API may operate on.

    Args:
        roots (list[Path]): The allowed root folders (absolute paths).
    """
    global _allowed_roots
    _allowed_roots = [root.resolve() for root in roots]


def validate_path(path_str: str, what: str = "path") -> Path:
    """Validates a user-provided path against the allowed roots.

    A path is allowed when it is inside one of the allowed roots (or equal
    to one). Relative paths and paths that escape a root via '..' are
    rejected.

    Args:
        path_str (str): The user-provided path.
        what (str): Name of the parameter (for the error message).

    Returns:
        Path: The resolved absolute path.

    Raises:
        HTTPException: If the path is not inside an allowed root.
    """
    if not path_str or not path_str.strip():
        raise HTTPException(status_code=400, detail=f"{what} is empty")
    try:
        # realpath normalizes away '..' segments and symlinks
        normalized = os.path.realpath(path_str)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"invalid {what}: {e}") from e

    for root in _allowed_roots:
        # CodeQL-recognized containment: the realpath must be the root
        # itself or a child of it (root with trailing separator)
        # strip the trailing separator: a drive root ('D:\\') already ends
        # in one, and appending os.sep would require a double separator
        root_str = os.path.realpath(str(root)).rstrip(os.sep)
        if normalized == root_str or normalized.startswith(root_str + os.sep):
            return Path(normalized)
    raise HTTPException(
        status_code=403,
        detail=f"{what} is outside the allowed roots: {_allowed_roots}",
    )


class RevealRequest(BaseModel):
    """Request body for the reveal endpoint.

    Attributes:
        path (str): The file whose containing folder should be revealed.
    """

    path: str


class JobRequest(BaseModel):
    """Request body for creating a job.

    Attributes:
        kind (JobKind): What to do (scan/plan/encode).
        path (str): The movie folder the job operates on.
        paths (list[str] | None): Additional movie folders (multi-directory
            support; merged with path).
        output_dir (str | None): Output directory (encode only).
        quality (bool): Use VMAF-driven quality mode (encode/plan).
        target_vmaf (float): The VMAF quality floor.
        remove_bars (bool): Crop stable baked-in black bars.
        overwrite (bool): Re-encode already encoded movies.
        workers (int | None): Concurrent encodes (fixed mode).
        rescan (bool): Ignore the scan cache.
        hash (bool): Compute content hashes while scanning.
    """

    kind: JobKind
    path: str = ""
    paths: list[str] | None = None
    output_dir: str | None = None
    quality: bool = False
    target_vmaf: float = Field(95.0, ge=1.0, le=100.0)
    remove_bars: bool = False
    overwrite: bool = False
    workers: int | None = Field(None, ge=1, le=64)
    rescan: bool = False
    hash: bool = False


class AppState:
    """The shared state of the resident app (queue, worker, watcher).

    The sqlite connection is shared across threads (FastAPI request threads,
    the worker, the watcher). Access is serialized by a re-entrant lock:
    every read/write of the database outside the worker's own job execution
    goes through with_lock(). WAL mode keeps writers and readers from
    blocking each other across processes.
    """

    def __init__(self, db_file: Path):
        """Initializes the app state.

        Jobs left 'running' by a previous app instance are marked failed
        (interrupted): the worker is single-threaded, so a running state in
        the database without a live worker is stale by definition.

        Args:
            db_file (Path): The sqlite database file.
        """
        self.db_file = db_file
        self.db_lock = threading.RLock()
        self.connection = db.connect(db_file)
        self.queue = JobQueue(self.connection)
        self.worker = JobWorker(self.connection, self.queue)
        with self.db_lock:
            self._recover_stale_jobs()
        self.watch_thread: threading.Thread | None = None
        self.watch_stop = threading.Event()

    def with_lock(self, fn):
        """Runs fn(connection) under the database lock.

        Args:
            fn (callable): Function receiving the connection.

        Returns:
            The function's return value.
        """
        with self.db_lock:
            return fn(self.connection)

    def _recover_stale_jobs(self):
        """Marks jobs left running by a previous app instance as failed."""
        self.connection.execute(
            "UPDATE jobs SET state = ?, error = ?, finished_at = datetime('now') WHERE state = ?",
            (
                "failed",
                "interrupted by app restart",
                "running",
            ),
        )
        self.connection.commit()

    def shutdown(self):
        """Stops the worker and closes the database."""
        self.worker.stop()
        self.watch_stop.set()
        if self.watch_thread is not None:
            self.watch_thread.join(timeout=10)
        self.connection.close()


state: AppState | None = None


def get_state() -> AppState:
    """Returns the app state (initialized at startup).

    Returns:
        AppState: The app state.

    Raises:
        HTTPException: If the state is not initialized.
    """
    if state is None:
        raise HTTPException(status_code=503, detail="app not initialized")
    return state


def db_access():
    """FastAPI dependency providing the app's database connection.

    Locking happens in the handlers (s.with_lock) so that acquire, work and
    release run on the same worker thread — a generator-yielded lock here
    would be released by a different threadpool thread than it was acquired
    on (the 'cannot release un-acquired lock' crash).

    Yields:
        sqlite3.Connection: The app's connection (unlocked).
    """
    s = get_state()
    yield s.connection


@app.get("/")
def index():
    """Serves the built web UI.

    Returns:
        FileResponse: The UI's index.html (or a hint when not built).
    """
    # the built UI lives at the repo root (dev: ui/dist; the app resolves it
    # relative to the repository root so a checkout layout works everywhere)
    index_path = _ui_dist() / "index.html"
    if not index_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="UI not built. Run: npm install && npm run build in ui/",
        )
    return FileResponse(index_path)


def _ui_dist() -> Path:
    """Resolves the built UI directory (repo-root ui/dist).

    Returns:
        Path: The dist directory (may not exist if the UI is not built).
    """
    # dev/checkout layout: repo root is three levels above this module
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "ui" / "dist"


@app.get("/assets/{asset_path:path}")
def ui_asset(asset_path: str):
    """Serves the built UI's static assets.

    The asset path is contained to the build output: traversal attempts
    ('..', absolute paths, drive letters) resolve outside dist/ and are
    rejected.

    Args:
        asset_path (str): The asset file path within the build output.

    Returns:
        FileResponse: The asset file.
    """
    dist = os.path.realpath(str(_ui_dist() / "assets"))
    # realpath normalizes away '..' segments => the result must stay a child
    # of the assets directory, otherwise the path escapes the build output
    normalized = os.path.realpath(os.path.join(dist, asset_path))
    if not (normalized == dist or normalized.startswith(dist + os.sep)):
        raise HTTPException(status_code=404, detail="asset not found")
    if not os.path.isfile(normalized):
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(normalized)


@app.get("/api/duplicates")
def duplicates():
    """Returns duplicate groups across the whole scanned library.

    Grouping: byte-identical files (content hash) and same-title files
    (normalized name + duration tolerance). Read-only — resolving a group
    is a manual, explicit user action. The rows are snapshotted under the
    lock; the matching itself runs outside it (the fuzzy pass is O(n²) and
    must not block the worker).

    Returns:
        list[dict]: The duplicate groups, best candidate first.
    """
    import sqlite3

    s = get_state()

    def snapshot(c: sqlite3.Connection) -> list[sqlite3.Row]:
        return c.execute(
            """
            SELECT path, name, category, size, duration_raw, codec, hash, width, height
            FROM files ORDER BY size DESC
            """
        ).fetchall()

    rows = s.with_lock(snapshot)

    # match outside the lock, on a private connection to the same file
    private = sqlite3.connect(s.db_file, check_same_thread=False)
    private.row_factory = sqlite3.Row
    try:
        private.executescript(
            """
            CREATE TEMP TABLE files (
                path TEXT PRIMARY KEY, name TEXT, category TEXT, size INTEGER,
                duration_raw REAL, codec TEXT, hash TEXT, width INTEGER, height INTEGER
            )
            """
        )
        private.executemany(
            "INSERT INTO temp.files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [tuple(r) for r in rows],
        )
        groups = find_duplicates(private)
    finally:
        private.close()
    return [g.to_dict() for g in groups]


def get_config():
    """Returns the current configuration (as stored in config.json).

    Returns:
        dict: The configuration data.
    """
    s = get_state()
    config_path = _config_path(s)
    if not config_path.is_file():
        # return the effective defaults so the UI can edit them
        from shrinkray.config import Conf

        conf = Conf()
        return _conf_to_dict(conf)
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def _config_path(s: AppState) -> Path:
    """Resolves the config.json path for the app's database location.

    Args:
        s (AppState): The app state.

    Returns:
        Path: The config.json path.
    """
    return s.db_file.parent / "config.json"


def _conf_to_dict(conf) -> dict:
    """Converts a Conf object into the config.json dict shape.

    Args:
        conf (Conf): The configuration object.

    Returns:
        dict: The config.json-shaped dict.
    """
    return {
        "codec": {
            "hdr": {
                "color_space": conf.hdr_color_spaces,
                "color_transfer": conf.hdr_color_transfers,
            }
        },
        "file": {
            "extensions": conf.extensions,
            "output": {"seperator": conf.separator},
        },
        "ffmpeg": {
            "black_bars": {
                "MIN_WIDTH": conf.black_bar_min_width,
                "MIN_HEIGHT": conf.black_bar_min_height,
            }
        },
        "handbrake": {
            "preset": conf.handbrake_preset,
            "encoder": conf.handbrake_encoder,
        },
        "language": conf.language,
    }


@app.get("/api/config")
def read_config():
    """Returns the current configuration (as stored in config.json).

    Returns:
        dict: The configuration data.
    """
    return get_config()


@app.put("/api/config")
def update_config(data: dict):
    """Updates the configuration (writes config.json).

    The config is validated by loading it through Conf after writing;
    invalid configs are rejected with 400 and the file restored.

    Args:
        data (dict): The configuration in config.json shape.

    Returns:
        dict: The saved configuration.
    """
    s = get_state()
    config_path = _config_path(s)

    backup = None
    if config_path.is_file():
        backup = config_path.read_text(encoding="utf-8")

    try:
        config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # validate: reset the cached config and load it fresh
        import shrinkray.config as config_module

        config_module._config = None
        config_module.load_config(s.db_file.parent)
    except Exception as e:
        # restore the backup on failure
        if backup is not None:
            config_path.write_text(backup, encoding="utf-8")
        raise HTTPException(status_code=400, detail=f"invalid config: {e}") from e
    return data


@app.get("/api/status")
def status(conn=Depends(db_access)):
    """Returns the app status: queue depth, running job, watcher state.

    Args:
        conn: The database connection (db_access dependency).

    Returns:
        dict: The status summary.
    """
    s = get_state()
    running = s.with_lock(
        lambda c: [j for j in s.queue.all_jobs(limit=10) if j.state.value in ("running", "queued")]
    )
    return {
        "queue": [j.to_dict() for j in running],
        "watcher": "running" if s.watch_thread and s.watch_thread.is_alive() else "stopped",
        "database": str(s.db_file),
    }


@app.get("/api/jobs")
def list_jobs(limit: int = 50, conn=Depends(db_access)):
    """Returns the recent job history, newest first.

    Args:
        limit (int): Maximum number of jobs.
        conn: The database connection (db_access dependency).

    Returns:
        list[dict]: The jobs.
    """
    s = get_state()
    return s.with_lock(lambda c: [j.to_dict() for j in s.queue.all_jobs(limit=limit)])


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, conn=Depends(db_access)):
    """Returns one job by id.

    Args:
        job_id (int): The job id.
        conn: The database connection (db_access dependency).

    Returns:
        dict: The job.
    """
    s = get_state()
    job = s.with_lock(lambda c: s.queue.get(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_dict()


@app.post("/api/jobs")
def create_job(request: JobRequest):
    """Enqueues a new job.

    Args:
        request (JobRequest): The job parameters.

    Returns:
        dict: The created job.
    """
    s = get_state()
    # dedupe needs no paths (it analyzes the whole library)
    if request.kind != JobKind.DEDUPE:
        if not request.path:
            raise HTTPException(status_code=400, detail="path is required for this job kind")
        path = validate_path(request.path, "path")
        if not path.exists():
            raise HTTPException(status_code=400, detail=f"path does not exist: {path}")

    # multi-directory: merge the primary path with any additional paths
    all_paths = []
    if request.kind != JobKind.DEDUPE:
        all_paths = [path]
    for extra in request.paths or []:
        extra_path = validate_path(extra, "paths")
        if not extra_path.exists():
            raise HTTPException(status_code=400, detail=f"path does not exist: {extra_path}")
        all_paths.append(extra_path)
    # deduplicate, keep order
    seen: set[Path] = set()
    unique_paths: list[Path] = []
    for p in all_paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    payload: dict = {"paths": [str(p) for p in unique_paths]}
    if request.kind == JobKind.DEDUPE:
        payload = {}
    elif request.kind == JobKind.ENCODE:
        if not request.output_dir:
            raise HTTPException(status_code=400, detail="encode requires output_dir")
        payload["output_dir"] = str(validate_path(request.output_dir, "output_dir"))
        payload["quality"] = request.quality
        payload["target_vmaf"] = request.target_vmaf
        payload["remove_bars"] = request.remove_bars
        payload["overwrite"] = request.overwrite
        payload["workers"] = request.workers
    elif request.kind == JobKind.PLAN:
        payload["target_vmaf"] = request.target_vmaf
        payload["remove_bars"] = request.remove_bars
    else:  # scan
        payload["rescan"] = request.rescan
        payload["hash"] = request.hash
        payload["workers"] = request.workers

    job = s.queue.enqueue(Job(kind=request.kind, payload=payload))
    return job.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    """Cancels a queued or running job.

    Args:
        job_id (int): The job id.

    Returns:
        dict: The acknowledgement.
    """
    s = get_state()
    if not s.queue.cancel(job_id):
        raise HTTPException(status_code=404, detail="job not found or already finished")
    return {"cancelled": job_id}


@app.get("/api/library")
def library(
    conn=Depends(db_access),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
):
    """Returns the scanned library from the database.

    Supports ETag-based conditional requests: the ETag is a fingerprint
    over the library contents (file count, max rowid, latest encode id,
    max mtime), so an unchanged library answers 304 without a payload -
    the UI's 3-second poll becomes nearly free.

    Args:
        conn: The database connection (db_access dependency).
        if_none_match (str | None): The client's If-None-Match header value.

    Returns:
        Response: JSON list, or 304 Not Modified when unchanged.
    """
    import hashlib

    s = get_state()
    fingerprint = s.with_lock(
        lambda c: c.execute(
            """
            SELECT COUNT(*), COALESCE(MAX(f.rowid), 0),
                   COALESCE((SELECT MAX(id) FROM encodes), 0),
                   COALESCE((SELECT MAX(mtime) FROM files), 0),
                   COALESCE((SELECT COUNT(*) FROM files WHERE missing_scans > 0), 0)
            FROM files f
            """
        ).fetchone()
    )
    etag = '"lib-' + hashlib.md5(str(tuple(fingerprint)).encode()).hexdigest()[:16] + '"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    rows = s.with_lock(
        lambda c: c.execute(
            """
            SELECT f.path, f.name, f.category, f.duration, f.codec, f.width, f.height,
                   f.size, f.hdr, f.missing_scans, f.missing_since,
                   e.preset, e.chosen_crf, e.achieved_vmaf, e.input_size, e.output_size,
                   e.saved_percent, e.encoder_preset, e.encode_seconds,
                   e.probe_count, e.probe_seconds, e.tool_versions
            FROM files f
            LEFT JOIN encodes e ON e.id = (
                SELECT id FROM encodes WHERE file_id = f.id ORDER BY id DESC LIMIT 1
            )
            ORDER BY f.category, f.name
            """
        ).fetchall()
    )
    body = [
        {
            "path": r["path"],
            "name": r["name"],
            "category": r["category"],
            "duration": r["duration"],
            "codec": r["codec"],
            "resolution": [r["width"], r["height"]],
            "size": r["size"],
            "hdr": bool(r["hdr"]),
            "missing": bool(r["missing_scans"]),
            "missing_since": r["missing_since"],
            "encode": {
                "preset": r["preset"],
                "crf": r["chosen_crf"],
                "vmaf": r["achieved_vmaf"],
                "input_size": r["input_size"],
                "output_size": r["output_size"],
                "saved_percent": r["saved_percent"],
                "encoder_preset": r["encoder_preset"],
                "encode_seconds": r["encode_seconds"],
                "probe_count": r["probe_count"],
                "probe_seconds": r["probe_seconds"],
                "tool_versions": r["tool_versions"],
            }
            if r["preset"]
            else None,
        }
        for r in rows
    ]
    return JSONResponse(body, headers={"ETag": etag})


@app.post("/api/reveal")
def reveal(request: RevealRequest):
    """Reveals a file's containing folder in the system file manager.

    The path must be a file inside the allowed roots (the same allowlist
    as every other endpoint); the reveal itself is the OS file manager
    opening a window - a local, user-visible action.

    Args:
        request (RevealRequest): The file whose folder to reveal.

    Returns:
        dict: The acknowledgement.
    """
    path = validate_path(request.path, "path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")
    folder = str(path.parent)
    try:
        if os.name == "nt":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder], check=False, timeout=10)
        else:
            subprocess.run(["xdg-open", folder], check=False, timeout=10)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not open folder: {e}") from e
    return {"revealed": folder}


@app.get("/report")
def report_page():
    """Serves the generated HTML report (regenerating it first).

    The report path is contained to the database folder (an app-controlled
    location, not user-supplied per request).

    Returns:
        FileResponse: The report file.
    """
    s = get_state()
    report_dir = os.path.realpath(str(s.db_file.parent))
    report_path = os.path.join(report_dir, "report.html")
    try:
        generate_report(s.db_file, Path(report_path))
    except Exception as e:
        log_error(f"report generation failed: {e}")
        raise HTTPException(status_code=500, detail="report generation failed") from e
    # CodeQL-recognized containment: realpath must stay inside report_dir
    normalized = os.path.realpath(report_path)
    if not (normalized.startswith(report_dir + os.sep)):
        raise HTTPException(status_code=500, detail="report path invalid")
    return FileResponse(normalized, media_type="text/html")


@app.post("/api/watch/{action}")
def watch_control(
    action: str,
    inbox: str | None = None,
    output_dir: str | None = None,
    body: dict | None = None,
):
    """Starts or stops the watch loop inside the app.

    Accepts inbox/output_dir either as query parameters or as a JSON body
    (the UI sends a body).

    Args:
        action (str): 'start' or 'stop'.
        inbox (str | None): The inbox folder (start only).
        output_dir (str | None): The output folder (start only).
        body (dict | None): JSON body alternative to the query parameters.

    Returns:
        dict: The watcher state.
    """
    s = get_state()

    if action == "stop":
        s.watch_stop.set()
        return {"watcher": "stopping"}

    if action == "start":
        body = body or {}
        inbox = inbox or body.get("inbox")
        output_dir = output_dir or body.get("output_dir")
        if s.watch_thread and s.watch_thread.is_alive():
            return {"watcher": "already running"}
        if not inbox or not output_dir:
            raise HTTPException(status_code=400, detail="start requires inbox and output_dir")
        inbox_path = validate_path(inbox, "inbox")
        output_path = validate_path(output_dir, "output_dir")

        def run_watch():
            try:
                watch(
                    inbox_path,
                    output_path,
                    once=False,
                    poll_interval=60.0,
                )
            except Exception as e:  # the thread must never die silently
                log_error(f"watch loop crashed: {e}")

        s.watch_stop.clear()
        s.watch_thread = threading.Thread(target=run_watch, name="watcher", daemon=True)
        s.watch_thread.start()
        return {"watcher": "running"}

    raise HTTPException(status_code=400, detail="action must be 'start' or 'stop'")


def _default_roots() -> list[Path]:
    """Computes the default allowed roots: all available drive roots.

    A localhost-only single-user tool can safely operate on whole drives;
    the strict default (database folder only) made every real library path
    a 403. Explicit --allow roots still narrow this down.

    Returns:
        list[Path]: The drive roots that exist (e.g. C:/, D:/, G:/).
    """
    import string

    roots = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:/")
        if drive.exists():
            roots.append(drive)
    return roots or [Path.cwd()]


def run_app(
    db_file: Path,
    host: str = "127.0.0.1",
    port: int = 8420,
    allowed_roots: list[Path] | None = None,
):
    """Boots the resident app (blocking; the server owns the process).

    Args:
        db_file (Path): The sqlite database file.
        host (str): The bind address (localhost by default).
        port (int): The bind port.
        allowed_roots (list[Path] | None): Folders the API may operate on.
            Defaults to all available drive roots (localhost-only app).
    """
    global state
    allow_roots(allowed_roots or _default_roots())
    state = AppState(db_file)
    state.worker.start()
    log(f"ShrinkRay app listening on http://{host}:{port}")

    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        state.shutdown()
        log("app stopped")
