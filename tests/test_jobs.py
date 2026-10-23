"""Job queue and API path validation tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from shrinkray import db, jobs


class TestJobs(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.connection = db.connect(Path(self.tmp.name) / "test.db")
        self.queue = jobs.JobQueue(self.connection)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def test_cancel_terminates_running_subprocess(self):
        """The cancel event terminates an in-flight subprocess (regression:
        cancel was only checked between movies, so a long probe or encode
        ran to completion despite the user cancelling)."""
        from shrinkray.quality import _run

        cancel = threading.Event()
        # a process that runs until killed (long enough to cancel mid-flight)
        command = ["ping", "-n", "30", "127.0.0.1"] if os.name == "nt" else ["sleep", "30"]
        result: list = []

        def runner():
            result.append(_run(command, cancel=cancel))

        thread = threading.Thread(target=runner)
        thread.start()
        threading.Event().wait(0)  # yield
        import time as _time

        _time.sleep(0.5)  # let the process start
        cancel.set()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "_run did not return after cancel")
        code, err = result[0]
        self.assertEqual(code, -1)
        self.assertIn("cancelled", err)

    def test_check_cancel_raises(self):
        """_check_cancel raises Cancelled when the event is set."""
        from shrinkray.quality import Cancelled, _check_cancel

        _check_cancel(None)  # disabled check never raises
        event = threading.Event()
        _check_cancel(event)  # not set: fine
        event.set()
        with self.assertRaises(Cancelled):
            _check_cancel(event)

    def test_quality_final_encode_always_streams(self):
        """Quality mode's final encode goes through the streamed loop even
        with progress=None (watch mode) - that loop is the disk watchdog;
        a blocking subprocess.run would encode unwatched. A watchdog
        cancellation surfaces as Cancelled with the engine's reason and the
        partial output already removed."""
        import tempfile

        from shrinkray.quality import Cancelled, encode_with_quality

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "in.mkv"
            out = root / "enc" / "in.mkv"
            (root / "enc").mkdir()
            src.write_bytes(b"x" * 8)
            stub = root / "hb_stub.cmd" if os.name == "nt" else root / "hb_stub.sh"
            if os.name == "nt":
                stub.write_bytes(b"@echo off\r\necho Encoding: task 1 of 1, 42.31 %%\r\nexit 0\r\n")
            else:
                stub.write_bytes(b'#!/bin/sh\necho "Encoding: task 1 of 1, 42.31 %"\nexit 0\n')
                stub.chmod(0o755)

            def fake_probe(*args, **kwargs):
                return 20, 95.2, [], None

            with (
                mock.patch("shrinkray.quality._probe_movie", side_effect=fake_probe),
                mock.patch("shrinkray.quality.find_handbrake", return_value=str(stub)),
                mock.patch("shrinkray.encode.disk_free_bytes", return_value=1_000_000),
                self.assertRaises(Cancelled) as ctx,
            ):
                encode_with_quality(src, out, progress=None)
            self.assertIn("disk space", str(ctx.exception))
            # the streamed engine removed the partial output itself
            self.assertFalse(out.exists())

    def test_encode_single_file_payload(self):
        """A per-movie encode payload (a FILE path, as the UI's detail page
        sends) resolves to exactly one movie — regression: execute_encode
        scanned the file as a directory and reported done with 0 outcomes."""
        from pathlib import Path as _Path

        movie = _Path(self.tmp.name) / "Some Movie_t00.mkv"
        movie.write_bytes(b"x" * 16)  # content irrelevant; resolution is mocked
        job = self.queue.enqueue(
            jobs.Job(
                kind=jobs.JobKind.ENCODE,
                payload={
                    "paths": [str(movie)],
                    "output_dir": str(_Path(self.tmp.name) / "out"),
                    "quality": True,
                },
            )
        )
        outcomes: list = []

        def fake_encode_with_quality(movie, *args, **kwargs):
            outcomes.append({"movie": movie.name, "state": "encoded"})
            from types import SimpleNamespace

            return SimpleNamespace(
                input_size=1, output_size=1, chosen_crf=20, achieved_vmaf=95.0, error=None
            )

        from types import SimpleNamespace
        from unittest.mock import patch

        with (
            patch("shrinkray.quality.encode_with_quality", fake_encode_with_quality),
            patch(
                "shrinkray.service.record_quality_results",
                lambda *a, **k: SimpleNamespace(encoded=True, skipped=False),
            ),
        ):
            result = jobs.execute_encode(job, self.connection, threading.Event())
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["movie"], "Some Movie_t00.mkv")
        # the job's recorded outcomes carry the movie through (not empty)
        self.assertEqual(result["outcomes"][0]["movie"], "Some Movie_t00.mkv")

    def test_dedupe_job_execution(self):
        """A dedupe job analyzes the library and records its summary."""
        job = self.queue.enqueue(jobs.Job(kind=jobs.JobKind.DEDUPE, payload={}))
        self.assertIsNotNone(job.id)
        result = jobs.execute_dedupe(job, self.connection, threading.Event())
        self.assertEqual(result["groups"], 0)
        self.assertEqual(result["wasted_bytes"], 0)
        # two same-title same-duration files form one group
        self.connection.execute(
            "INSERT INTO files (path, mtime, size, name, category, duration_raw)"
            " VALUES ('D:/a/x.mkv', 1.0, 100, 'movie', 'Cat', 60.0)"
        )
        self.connection.execute(
            "INSERT INTO files (path, mtime, size, name, category, duration_raw)"
            " VALUES ('D:/b/x.mkv', 1.0, 100, 'movie', 'Cat', 60.0)"
        )
        self.connection.commit()
        result = jobs.execute_dedupe(job, self.connection, threading.Event())
        self.assertEqual(result["groups"], 1)
        self.assertEqual(result["wasted_bytes"], 100)

    def test_enqueue_and_persist(self):
        job = self.queue.enqueue(jobs.Job(kind=jobs.JobKind.SCAN, payload={"path": "D:/Movies"}))
        self.assertIsNotNone(job.id)
        self.assertEqual(job.state, jobs.JobState.QUEUED)
        # persisted => a fresh queue loads it as pending
        fresh = jobs.JobQueue(self.connection)
        self.assertEqual(len(fresh.all_jobs()), 1)
        loaded = fresh.take_next()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.kind, jobs.JobKind.SCAN)
        self.assertEqual(loaded.payload["path"], "D:/Movies")

    def test_take_next_marks_running_and_finish_stores_result(self):
        self.queue.enqueue(jobs.Job(kind=jobs.JobKind.PLAN, payload={}))
        taken = self.queue.take_next()
        self.assertEqual(taken.state, jobs.JobState.RUNNING)
        self.queue.finish(taken, {"plans": []})
        reloaded = self.queue.get(taken.id)
        self.assertEqual(reloaded.state, jobs.JobState.DONE)
        self.assertEqual(reloaded.result, {"plans": []})

    def test_fail_stores_error(self):
        self.queue.enqueue(jobs.Job(kind=jobs.JobKind.ENCODE, payload={}))
        taken = self.queue.take_next()
        self.queue.fail(taken, "boom")
        reloaded = self.queue.get(taken.id)
        self.assertEqual(reloaded.state, jobs.JobState.FAILED)
        self.assertEqual(reloaded.error, "boom")

    def test_terminal_states_clear_stale_progress(self):
        """fail()/mark_cancelled() drop the last progress dict (regression:
        terminated rows kept their live-state payload forever — invisible
        to the UI, dead weight in every snapshot)."""
        self.queue.enqueue(jobs.Job(kind=jobs.JobKind.ENCODE, payload={}))
        taken = self.queue.take_next()
        self.queue.report_progress(taken, {"stage": "encode", "percent": 40})
        self.queue.fail(taken, "boom")
        reloaded = self.queue.get(taken.id)
        self.assertIsNone(reloaded.result)

        self.queue.enqueue(jobs.Job(kind=jobs.JobKind.SCAN, payload={}))
        taken2 = self.queue.take_next()
        self.queue.report_progress(taken2, {"stage": "scan", "percent": 10})
        self.queue.mark_cancelled(taken2)
        reloaded2 = self.queue.get(taken2.id)
        self.assertIsNone(reloaded2.result)

    def test_cancelled_job_keeps_partial_result(self):
        """A graceful cancel carries the engine's partial result — the UI
        renders outcomes on cancelled encode rows (App.svelte reads
        outcomes from done OR cancelled rows)."""
        self.queue.enqueue(jobs.Job(kind=jobs.JobKind.ENCODE, payload={}))
        taken = self.queue.take_next()
        self.queue.report_progress(taken, {"percent": 50})
        self.queue.mark_cancelled(taken, {"outcomes": [{"movie": "a.mkv", "state": "encoded"}]})
        reloaded = self.queue.get(taken.id)
        self.assertEqual(reloaded.result["outcomes"][0]["movie"], "a.mkv")

    def test_history_pruned_to_retention_cap(self):
        """Terminal writes keep the newest JOB_RETENTION terminated rows and
        hard-delete older ones (the jobs table never had any retention —
        the library side did via prune_missing, this side grew forever).
        Queued/running rows are never touched."""
        from shrinkray.jobs import JOB_RETENTION

        total = JOB_RETENTION + 10
        for i in range(total):
            self.queue.enqueue(jobs.Job(kind=jobs.JobKind.SCAN, payload={"n": i}))
            taken = self.queue.take_next()
            self.queue.finish(taken, {"n": i})
        # one more, left queued: live rows never prune
        self.queue.enqueue(jobs.Job(kind=jobs.JobKind.SCAN, payload={"n": "keep"}))
        count = self.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        self.assertEqual(count, JOB_RETENTION + 1)
        # the OLDEST terminated rows went: survivors are the newest ids
        oldest = self.connection.execute(
            "SELECT MIN(id) FROM jobs WHERE state = 'done'"
        ).fetchone()[0]
        self.assertEqual(oldest, total - JOB_RETENTION + 1)

    def test_cancel_queued_job(self):
        job = self.queue.enqueue(jobs.Job(kind=jobs.JobKind.SCAN, payload={}))
        self.assertTrue(self.queue.cancel(job.id))
        reloaded = self.queue.get(job.id)
        self.assertEqual(reloaded.state, jobs.JobState.CANCELLED)
        # the queue no longer hands it out
        self.assertIsNone(self.queue.take_next())

    def test_cancel_running_job_signals_event(self):
        self.queue.enqueue(jobs.Job(kind=jobs.JobKind.SCAN, payload={}))
        taken = self.queue.take_next()
        event = threading.Event()
        self.queue.register_cancel_event(taken.id, event)
        self.assertTrue(self.queue.cancel(taken.id))
        self.assertTrue(event.is_set())
        self.queue.unregister_cancel_event(taken.id)

    def test_worker_executes_job(self):
        payload = {"path": str(Path(self.tmp.name) / "movies")}
        (Path(self.tmp.name) / "movies").mkdir()
        # a junk file: the scan handles it as an error, but the job succeeds
        (Path(self.tmp.name) / "movies" / "x.mkv").write_bytes(b"junk")
        job = self.queue.enqueue(jobs.Job(kind=jobs.JobKind.SCAN, payload=payload))
        worker = jobs.JobWorker(self.connection, self.queue)
        worker._execute(job)  # direct execution => deterministic, no thread wait
        reloaded = self.queue.get(job.id)
        self.assertEqual(reloaded.state, jobs.JobState.DONE)
        self.assertEqual(reloaded.result["movies"], 1)
        self.assertGreaterEqual(len(reloaded.result["errors"]), 1)

    def test_worker_captures_engine_crash_as_failure(self):
        # a nonexistent path: on windows it raises FileNotFoundError while
        # scanning; on linux it is just an empty relative directory =>
        # platform-dependent failure, but the worker must survive both and
        # record a terminal state either way
        job = self.queue.enqueue(
            jobs.Job(
                kind=jobs.JobKind.ENCODE, payload={"path": "Z:/nowhere", "output_dir": "Z:/out"}
            )
        )
        worker = jobs.JobWorker(self.connection, self.queue)
        worker._execute(job)
        reloaded = self.queue.get(job.id)
        self.assertIn(reloaded.state, (jobs.JobState.FAILED, jobs.JobState.DONE))
        if reloaded.state == jobs.JobState.FAILED:
            self.assertIn("FileNotFoundError", reloaded.error)

    def test_next_poll_interval_helper_exists(self):
        # guard the cross-module dependency used by watch mode
        self.assertTrue(callable(jobs.JobQueue.wait_for_work))


class TestApiPathValidation(unittest.TestCase):
    """Tests for the path allowlist of the resident app API."""

    def setUp(self):
        import tempfile

        from shrinkray import app as app_module

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        app_module.allow_roots([self.root])

    def tearDown(self):
        from shrinkray import app as app_module

        app_module._allowed_roots = []
        self.tmp.cleanup()

    def test_validate_path_accepts_inside_root(self):
        from shrinkray.app import validate_path

        path = validate_path(str(self.root / "movies"))
        self.assertEqual(path, self.root / "movies")

    def test_validate_path_accepts_root_itself(self):
        from shrinkray.app import validate_path

        self.assertEqual(validate_path(str(self.root)), self.root)

    def test_validate_path_rejects_outside_root(self):
        from fastapi import HTTPException

        from shrinkray.app import validate_path

        with self.assertRaises(HTTPException) as ctx:
            validate_path("C:/Windows/System32")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_validate_path_rejects_escape_via_dotdot(self):
        from fastapi import HTTPException

        from shrinkray.app import validate_path

        # .. inside the root resolves outside => must be rejected
        with self.assertRaises(HTTPException) as ctx:
            validate_path(str(self.root / ".." / "elsewhere"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_validate_path_rejects_empty(self):
        from fastapi import HTTPException

        from shrinkray.app import validate_path

        with self.assertRaises(HTTPException) as ctx:
            validate_path("   ")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_validate_path_rejects_relative(self):
        from fastapi import HTTPException

        from shrinkray.app import validate_path

        # relative paths resolve against the CWD, which is not an allowed root
        with self.assertRaises(HTTPException) as ctx:
            validate_path("movies")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_validate_path_accepts_root_itself_and_children(self):
        import tempfile

        from shrinkray.app import allow_roots, validate_path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            allow_roots([root])
            # the root itself and direct children are allowed
            self.assertEqual(validate_path(str(root)), root)
            self.assertEqual(validate_path(str(root / "sub")), root / "sub")
            # '..' escaping the root is rejected
            from fastapi import HTTPException

            with self.assertRaises(HTTPException) as ctx:
                validate_path(str(root / ".." / "elsewhere"))
            self.assertEqual(ctx.exception.status_code, 403)

    def test_ui_asset_containment(self):
        import tempfile

        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        with tempfile.TemporaryDirectory() as tmp:
            app_module.state = AppState(Path(tmp) / "test.db")
            try:
                client = TestClient(fast_app)
                # traversal attempts are rejected with 404 (not served)
                response = client.get("/assets/../metadata.db")
                self.assertEqual(response.status_code, 404)
                response = client.get("/assets/..%2F..%2Fpyproject.toml")
                self.assertEqual(response.status_code, 404)
                # a real asset inside dist is served (if the UI is built)
                dist = Path(__file__).parent.parent / "ui" / "dist" / "assets"
                if dist.is_dir() and any(dist.iterdir()):
                    asset_name = next(dist.iterdir()).name
                    ok = client.get(f"/assets/{asset_name}")
                    self.assertEqual(ok.status_code, 200)
                    # hashed assets are immutable: the browser never needs to
                    # revalidate them (the entry page is the freshness point)
                    self.assertEqual(
                        ok.headers.get("cache-control"),
                        "public, max-age=31536000, immutable",
                    )
            finally:
                app_module.state.shutdown()
                app_module.state = None

    def test_index_no_cache(self):
        """The entry page revalidates on every load (no-cache): a rebuilt UI
        must reach an open tab with a plain reload — a resident app's UI
        updates underneath the tab, and a hard-refresh ritual is a bug."""
        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        dist = Path(__file__).parent.parent / "ui" / "dist"
        if not (dist / "index.html").is_file():
            self.skipTest("UI not built")
        app_module.state = AppState(Path(tempfile.gettempdir()) / "shrinkray-test-cache.db")
        try:
            client = TestClient(fast_app)
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get("cache-control"), "no-cache")
        finally:
            app_module.state.shutdown()
            app_module.state = None


if __name__ == "__main__":
    unittest.main()
