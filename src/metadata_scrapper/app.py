"""The resident application: HTTP API over the engine and job queue.

The app owns long-running work (the job queue and the watch loop); the CLI
stays fully functional without it. Consumers (web UI, curl, scripts) talk
to the local HTTP API — same engine, same database, same guardrails.

Run with `metadata-scrapper app`.
"""

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from metadata_scrapper import db
from metadata_scrapper.jobs import Job, JobKind, JobQueue, JobWorker
from metadata_scrapper.logger import log, log_error
from metadata_scrapper.report import generate_report
from metadata_scrapper.watch import watch

app = FastAPI(title="MetaDataScraper", version="0.3.0")


class JobRequest(BaseModel):
    """Request body for creating a job.

    Attributes:
        kind (JobKind): What to do (scan/plan/encode).
        path (str): The movie folder the job operates on.
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
    path: str
    output_dir: str | None = None
    quality: bool = False
    target_vmaf: float = Field(95.0, ge=1.0, le=100.0)
    remove_bars: bool = False
    overwrite: bool = False
    workers: int | None = Field(None, ge=1, le=64)
    rescan: bool = False
    hash: bool = False


class AppState:
    """The shared state of the resident app (queue, worker, watcher)."""

    def __init__(self, db_file: Path):
        """Initializes the app state.

        Args:
            db_file (Path): The sqlite database file.
        """
        self.db_file = db_file
        self.connection = db.connect(db_file)
        self.queue = JobQueue(self.connection)
        self.worker = JobWorker(self.connection, self.queue)
        self.watch_thread: threading.Thread | None = None
        self.watch_stop = threading.Event()

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


@app.get("/api/status")
def status():
    """Returns the app status: queue depth, running job, watcher state.

    Returns:
        dict: The status summary.
    """
    s = get_state()
    running = [j for j in s.queue.all_jobs(limit=10) if j.state.value in ("running", "queued")]
    return {
        "queue": [j.to_dict() for j in running],
        "watcher": "running" if s.watch_thread and s.watch_thread.is_alive() else "stopped",
        "database": str(s.db_file),
    }


@app.get("/api/jobs")
def list_jobs(limit: int = 50):
    """Returns the recent job history, newest first.

    Args:
        limit (int): Maximum number of jobs.

    Returns:
        list[dict]: The jobs.
    """
    s = get_state()
    return [j.to_dict() for j in s.queue.all_jobs(limit=limit)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    """Returns one job by id.

    Args:
        job_id (int): The job id.

    Returns:
        dict: The job.
    """
    s = get_state()
    job = s.queue.get(job_id)
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
    path = Path(request.path)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"path does not exist: {path}")

    payload: dict = {"path": str(path)}
    if request.kind == JobKind.ENCODE:
        if not request.output_dir:
            raise HTTPException(status_code=400, detail="encode requires output_dir")
        payload["output_dir"] = request.output_dir
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
def library():
    """Returns the scanned library from the database.

    Returns:
        list[dict]: One entry per movie with its latest encode decision.
    """
    s = get_state()
    rows = s.connection.execute(
        """
        SELECT f.path, f.name, f.category, f.duration, f.codec, f.width, f.height,
               f.size, f.hdr,
               e.preset, e.chosen_crf, e.achieved_vmaf, e.input_size, e.output_size,
               e.saved_percent
        FROM files f
        LEFT JOIN encodes e ON e.id = (
            SELECT id FROM encodes WHERE file_id = f.id ORDER BY id DESC LIMIT 1
        )
        ORDER BY f.category, f.name
        """
    ).fetchall()
    return [
        {
            "path": r["path"],
            "name": r["name"],
            "category": r["category"],
            "duration": r["duration"],
            "codec": r["codec"],
            "resolution": [r["width"], r["height"]],
            "size": r["size"],
            "hdr": bool(r["hdr"]),
            "encode": {
                "preset": r["preset"],
                "crf": r["chosen_crf"],
                "vmaf": r["achieved_vmaf"],
                "input_size": r["input_size"],
                "output_size": r["output_size"],
                "saved_percent": r["saved_percent"],
            }
            if r["preset"]
            else None,
        }
        for r in rows
    ]


@app.get("/report")
def report_page():
    """Serves the generated HTML report (regenerating it first).

    Returns:
        FileResponse: The report file.
    """
    s = get_state()
    report_path = s.db_file.parent / "report.html"
    try:
        generate_report(s.db_file, report_path)
    except Exception as e:
        log_error(f"report generation failed: {e}")
        raise HTTPException(status_code=500, detail="report generation failed") from e
    return FileResponse(report_path, media_type="text/html")


@app.post("/api/watch/{action}")
def watch_control(action: str, inbox: str | None = None, output_dir: str | None = None):
    """Starts or stops the watch loop inside the app.

    Args:
        action (str): 'start' or 'stop'.
        inbox (str | None): The inbox folder (start only).
        output_dir (str | None): The output folder (start only).

    Returns:
        dict: The watcher state.
    """
    s = get_state()

    if action == "stop":
        s.watch_stop.set()
        return {"watcher": "stopping"}

    if action == "start":
        if s.watch_thread and s.watch_thread.is_alive():
            return {"watcher": "already running"}
        if not inbox or not output_dir:
            raise HTTPException(status_code=400, detail="start requires inbox and output_dir")

        def run_watch():
            try:
                watch(
                    Path(inbox),
                    Path(output_dir),
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


def run_app(db_file: Path, host: str = "127.0.0.1", port: int = 8420):
    """Boots the resident app (blocking; the server owns the process).

    Args:
        db_file (Path): The sqlite database file.
        host (str): The bind address (localhost by default).
        port (int): The bind port.
    """
    global state
    state = AppState(db_file)
    state.worker.start()
    log(f"MetaDataScraper app listening on http://{host}:{port}")

    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        state.shutdown()
        log("app stopped")
