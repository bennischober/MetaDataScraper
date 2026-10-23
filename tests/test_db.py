"""Database layer tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shrinkray import config as config_helper
from shrinkray import db


class TestDb(unittest.TestCase):
    def setUp(self):

        self.tmp = tempfile.TemporaryDirectory()
        self.connection = db.connect(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def _record(self, path: str) -> db.MovieRecord:
        return db.MovieRecord(path=Path(path), mtime=100.0, size=1000, name="test", category="Cat")

    def test_connect_creates_schema(self):
        tables = {
            row["name"]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertIn("files", tables)
        self.assertIn("encodes", tables)
        self.assertIn("schema_version", tables)
        version = self.connection.execute("SELECT version FROM schema_version").fetchone()
        self.assertEqual(version["version"], db.SCHEMA_VERSION)

    def test_migration_v1_to_v2(self):
        # simulate a v1 database: current schema without the quality columns

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v1.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE schema_version (version INTEGER NOT NULL);
                INSERT INTO schema_version (version) VALUES (1);
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL
                );
                CREATE TABLE encodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    preset TEXT NOT NULL,
                    encoder TEXT NOT NULL,
                    input_size INTEGER NOT NULL,
                    output_size INTEGER NOT NULL,
                    saved_percent REAL NOT NULL
                );
                """
            )
            connection.commit()
            connection.close()

            migrated = db.connect(db_path)
            version = migrated.execute("SELECT version FROM schema_version").fetchone()
            self.assertEqual(version["version"], db.SCHEMA_VERSION)
            # the quality columns exist and are usable
            columns = {
                row["name"] for row in migrated.execute("PRAGMA table_info(encodes)").fetchall()
            }
            self.assertIn("chosen_crf", columns)
            self.assertIn("achieved_vmaf", columns)
            migrated.close()

    def test_migration_v2_to_v3_absolute_paths(self):
        # v2 database with a relative path => migrated to absolute (or dropped)
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v2.db"
            # create a real file so the relative path can resolve to it
            existing = Path(tmp) / "movie.mkv"
            existing.write_bytes(b"x")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                connection = sqlite3.connect(db_path)
                connection.executescript(
                    """
                    CREATE TABLE schema_version (version INTEGER NOT NULL);
                    INSERT INTO schema_version (version) VALUES (2);
                    CREATE TABLE files (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        path TEXT NOT NULL UNIQUE,
                        mtime REAL NOT NULL,
                        size INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        category TEXT NOT NULL
                    );
                    CREATE TABLE encodes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                        preset TEXT NOT NULL,
                        encoder TEXT NOT NULL,
                        input_size INTEGER NOT NULL,
                        output_size INTEGER NOT NULL,
                        saved_percent REAL NOT NULL
                    );
                    INSERT INTO files (path, mtime, size, name, category) VALUES
                        ('movie.mkv', 1.0, 1, 'movie', 'Cat'),
                        ('deleted.mkv', 1.0, 1, 'gone', 'Cat');
                    """
                )
                connection.commit()
                connection.close()

                migrated = db.connect(db_path)
                version = migrated.execute("SELECT version FROM schema_version").fetchone()
                self.assertEqual(version["version"], db.SCHEMA_VERSION)
                paths = {r["path"] for r in migrated.execute("SELECT path FROM files")}
                # existing file => absolute; deleted file => dropped
                self.assertEqual(len(paths), 1)
                self.assertIn(str(existing), paths)
                migrated.close()
            finally:
                os.chdir(cwd)

    def test_migration_v5_to_v6_missing_columns(self):
        # v5 database: files without the soft-missing columns => added with
        # defaults, existing rows are considered present (not missing)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v5.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE schema_version (version INTEGER NOT NULL);
                INSERT INTO schema_version (version) VALUES (5);
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL
                );
                CREATE TABLE encodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    preset TEXT NOT NULL,
                    encoder TEXT NOT NULL,
                    input_size INTEGER NOT NULL,
                    output_size INTEGER NOT NULL,
                    saved_percent REAL NOT NULL
                );
                INSERT INTO files (path, mtime, size, name, category) VALUES
                    ('D:/Movies/a.mkv', 1.0, 1, 'movie', 'Cat');
                """
            )
            connection.commit()
            connection.close()

            migrated = db.connect(db_path)
            version = migrated.execute("SELECT version FROM schema_version").fetchone()
            self.assertEqual(version["version"], db.SCHEMA_VERSION)
            columns = {
                row["name"] for row in migrated.execute("PRAGMA table_info(files)").fetchall()
            }
            self.assertIn("missing_scans", columns)
            self.assertIn("missing_since", columns)
            row = migrated.execute("SELECT * FROM files").fetchone()
            self.assertEqual(row["missing_scans"], 0)
            self.assertIsNone(row["missing_since"])
            migrated.close()

    def test_migration_v7_generation_columns(self):
        # v6 database: encodes without the drift columns => added with
        # defaults, generation starts at 1

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v6.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE schema_version (version INTEGER NOT NULL);
                INSERT INTO schema_version (version) VALUES (6);
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL
                );
                CREATE TABLE encodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    preset TEXT NOT NULL,
                    encoder TEXT NOT NULL,
                    input_size INTEGER NOT NULL,
                    output_size INTEGER NOT NULL,
                    saved_percent REAL NOT NULL
                );
                INSERT INTO files (path, mtime, size, name, category) VALUES
                    ('D:/Movies/a.mkv', 1.0, 1, 'movie', 'Cat');
                """
            )
            connection.commit()
            connection.close()

            migrated = db.connect(db_path)
            version = migrated.execute("SELECT version FROM schema_version").fetchone()
            self.assertEqual(version["version"], db.SCHEMA_VERSION)
            columns = {
                row["name"] for row in migrated.execute("PRAGMA table_info(encodes)").fetchall()
            }
            self.assertIn("input_bitrate", columns)
            self.assertIn("output_bitrate", columns)
            self.assertIn("source_file", columns)
            self.assertIn("generation", columns)
            migrated.close()

    def test_derive_generation_chain(self):
        """Encoding an encode chains generation N+1 via source_file."""

        from shrinkray import service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            original = root / "movie.mkv"
            reencode = root / "movie_reencoded.mkv"

            file_id = db.upsert_file(self.connection, self._record(str(original)))
            db.add_encode(
                self.connection,
                file_id,
                "VMAF>95",
                "x265",
                1000,
                500,
                50.0,
                source_file=str(original),
                output_file=str(reencode),
                generation=1,
            )
            # the re-encode input is the output of generation 1
            self.assertEqual(service.derive_generation(self.connection, reencode), 2)
            # an unrelated file stays generation 1
            self.assertEqual(service.derive_generation(self.connection, root / "other.mkv"), 1)

    def test_add_encode_with_drift_fields(self):
        file_id = db.upsert_file(self.connection, self._record("a.mkv"))
        encode_id = db.add_encode(
            self.connection,
            file_id,
            "VMAF>95",
            "x265",
            1000,
            300,
            70.0,
            input_bitrate=18.4,
            output_bitrate=14.9,
            source_file="D:/Movies/a.mkv",
            output_file="D:/Out/a.mkv",
            generation=1,
        )
        row = self.connection.execute("SELECT * FROM encodes WHERE id = ?", (encode_id,)).fetchone()
        self.assertEqual(row["input_bitrate"], 18.4)
        self.assertEqual(row["output_bitrate"], 14.9)
        self.assertEqual(row["source_file"], "D:/Movies/a.mkv")
        self.assertEqual(row["output_file"], "D:/Out/a.mkv")
        self.assertEqual(row["generation"], 1)

    def test_add_encode_with_quality_fields(self):
        file_id = db.upsert_file(self.connection, self._record("a.mkv"))
        encode_id = db.add_encode(
            self.connection,
            file_id,
            "VMAF>95",
            "x265",
            1000,
            300,
            70.0,
            chosen_crf=22,
            achieved_vmaf=95.4,
        )
        row = self.connection.execute("SELECT * FROM encodes WHERE id = ?", (encode_id,)).fetchone()
        self.assertEqual(row["chosen_crf"], 22)
        self.assertAlmostEqual(row["achieved_vmaf"], 95.4)

    def test_library_api_exposes_benchmark_metrics(self):
        """The /api/library payload carries the schema-v5 encode metrics."""
        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        file_id = db.upsert_file(
            self.connection,
            db.MovieRecord(
                path=Path("D:/x/metrics.mkv"),
                mtime=100.0,
                size=1000,
                name="metrics",
                category="Cat",
            ),
        )
        db.add_encode(
            self.connection,
            file_id,
            "VMAF>95",
            "x265",
            1000,
            300,
            70.0,
            chosen_crf=22,
            achieved_vmaf=95.4,
            encoder_preset="slow",
            encode_seconds=1234.5,
            probe_count=3,
            probe_seconds=30.0,
            hdr=False,
            tool_versions="hb=1.9 libvmaf=2.3.1",
        )
        self.connection.commit()

        # AppState opens its own connection to the same database file the
        # test connection wrote to (committed rows are visible through WAL)
        app_module.state = AppState(Path(self.tmp.name) / "test.db")
        try:
            client = TestClient(fast_app)
            response = client.get("/api/library")
            self.assertEqual(response.status_code, 200)
            movie = next(m for m in response.json() if m["name"] == "metrics")
            encode = movie["encode"]
            self.assertEqual(encode["encoder_preset"], "slow")
            self.assertAlmostEqual(encode["encode_seconds"], 1234.5)
            self.assertEqual(encode["probe_count"], 3)
            self.assertAlmostEqual(encode["probe_seconds"], 30.0)
            self.assertEqual(encode["tool_versions"], "hb=1.9 libvmaf=2.3.1")
        finally:
            app_module.state.shutdown()
            app_module.state = None

    def test_library_etag_304(self):
        """An unchanged library answers If-None-Match with 304 (the poll
        becomes nearly free)."""
        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        app_module.state = AppState(Path(self.tmp.name) / "test.db")
        try:
            client = TestClient(fast_app)
            first = client.get("/api/library")
            self.assertEqual(first.status_code, 200)
            etag = first.headers["ETag"]
            # unchanged: 304 without a body
            cached = client.get("/api/library", headers={"If-None-Match": etag})
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(cached.content, b"")
            # a changed library (new encode row) invalidates the fingerprint
            file_id = db.upsert_file(
                self.connection,
                db.MovieRecord(
                    path=Path("D:/x/new.mkv"), mtime=99.0, size=5, name="new", category="Cat"
                ),
            )
            db.add_encode(self.connection, file_id, "VMAF>95", "x265", 5, 3, 40.0)
            self.connection.commit()
            fresh = client.get("/api/library", headers={"If-None-Match": etag})
            self.assertEqual(fresh.status_code, 200)
            self.assertNotEqual(fresh.headers["ETag"], etag)
        finally:
            app_module.state.shutdown()
            app_module.state = None

    def test_movie_detail_endpoint(self):
        """GET /api/movie returns the file facts plus the COMPLETE encode
        history (the library list carries only the latest encode), including
        the schema-v7 chain: a re-encoded file's page must show the parent
        row whose output produced it (the round-5 finding — the old query
        filtered on file_id alone and the parent row vanished)."""
        # real temp dir: the endpoint allowlist-checks the path, so the
        # movie file must live under an allowed root (a fictional D:/x
        # would be rejected before the database is ever consulted)

        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        with tempfile.TemporaryDirectory() as media_tmp:
            movie_path = str(Path(media_tmp) / "detail-movie.mkv")
            file_id = db.upsert_file(
                self.connection,
                db.MovieRecord(
                    path=Path(movie_path),
                    mtime=100.0,
                    size=1000,
                    name="detail-movie",
                    category="Cat",
                ),
            )
            db.add_encode(self.connection, file_id, "VMAF>95", "x265", 1000, 300, 70.0)
            db.add_encode(self.connection, file_id, "VMAF>90", "x265", 1000, 250, 75.0)
            self.connection.commit()

            app_module.state = AppState(Path(self.tmp.name) / "test.db")
            app_module.allow_roots([Path(media_tmp), Path(self.tmp.name)])
            try:
                client = TestClient(fast_app)
                response = client.get("/api/movie", params={"path": movie_path})
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["file"]["name"], "detail-movie")
                self.assertEqual(body["file"]["generation"], 1)  # no ancestry
                self.assertEqual(len(body["history"]), 2)  # both encodes, oldest first
                self.assertEqual(body["history"][0]["preset"], "VMAF>95")
                self.assertEqual(body["history"][1]["preset"], "VMAF>90")
                # schema v7 provenance fields ride along (None for plain rows)
                self.assertIn("input_bitrate", body["history"][0])
                self.assertIn("generation", body["history"][0])
                self.assertIn("source_file", body["history"][0])
                self.assertIn("id", body["history"][0])  # each-key, unique per row
                # unknown path inside the allowlist: 404
                missing = client.get(
                    "/api/movie", params={"path": str(Path(self.tmp.name) / "unknown.mkv")}
                )
                self.assertEqual(missing.status_code, 404)

                # ── chain: a gen-2 file must see its parent row ──
                output_path = str(Path(media_tmp) / "detail-movie_t01.mkv")
                db.add_encode(
                    self.connection,
                    file_id,
                    "VMAF>95",
                    "x265",
                    300,
                    150,
                    50.0,
                    source_file=movie_path,
                    output_file=output_path,
                    generation=1,
                )
                child_id = db.upsert_file(
                    self.connection,
                    db.MovieRecord(
                        path=Path(output_path),
                        mtime=200.0,
                        size=150,
                        name="detail-movie_t01",
                        category="Cat",
                    ),
                )
                db.add_encode(
                    self.connection,
                    child_id,
                    "VMAF>95",
                    "x265",
                    150,
                    80,
                    46.7,
                    source_file=output_path,
                    output_file=str(Path(media_tmp) / "detail-movie_t02.mkv"),
                    generation=2,
                )
                self.connection.commit()
                child = client.get("/api/movie", params={"path": output_path})
                self.assertEqual(child.status_code, 200)
                child_body = child.json()
                self.assertEqual(child_body["file"]["generation"], 2)
                # the parent's gen-1 row rides in via output_file linkage
                self.assertEqual(len(child_body["history"]), 2)
                self.assertEqual(child_body["history"][0]["file_path"], movie_path)
                self.assertEqual(child_body["history"][0]["generation"], 1)
                self.assertEqual(child_body["history"][1]["generation"], 2)
            finally:
                app_module.state.shutdown()
                app_module.state = None
                app_module._allowed_roots = []

    def test_config_endpoint_roundtrip(self):
        """GET /api/config returns the engine's full config shape (including
        the resource-politeness and integration sections) so the UI can edit
        them; POST persists what it is given."""
        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        app_module.state = AppState(Path(self.tmp.name) / "test.db")
        try:
            client = TestClient(fast_app)
            body = client.get("/api/config").json()
            # the sections the config page edits (defaults when no file exists)
            self.assertIn("handbrake", body)
            self.assertIn("max_threads", body["handbrake"])
            self.assertIn("resources", body)
            self.assertIn("idle_priority", body["resources"])
            self.assertIn("integrations", body)
            # PUT round-trips the new sections (the app's contract is PUT)
            body["resources"]["idle_priority"] = False
            body["handbrake"]["max_threads"] = 8
            body["integrations"] = {"jellyfin": {"url": "http://nas:8096", "token_env": "TOK"}}
            saved = client.put("/api/config", json=body)
            self.assertEqual(saved.status_code, 200)
            reread = client.get("/api/config").json()
            self.assertFalse(reread["resources"]["idle_priority"])
            self.assertEqual(reread["handbrake"]["max_threads"], 8)
            self.assertEqual(reread["integrations"]["jellyfin"]["url"], "http://nas:8096")
            # the token env NAME persists; no secret value ever does
            self.assertEqual(reread["integrations"]["jellyfin"]["token_env"], "TOK")
        finally:
            app_module.state.shutdown()
            app_module.state = None

    def test_snapshot_endpoint(self):
        """One request returns status + jobs + library (ETag-aware), and an
        unchanged library comes back as null while the rest stays fresh."""
        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        file_id = db.upsert_file(
            self.connection,
            db.MovieRecord(
                path=Path("D:/x/snap.mkv"), mtime=100.0, size=1000, name="snap", category="Cat"
            ),
        )
        db.add_encode(
            self.connection,
            file_id,
            "VMAF>95",
            "x265",
            1000,
            300,
            70.0,
            chosen_crf=22,
            achieved_vmaf=95.4,
        )
        self.connection.commit()

        app_module.state = AppState(Path(self.tmp.name) / "test.db")
        try:
            client = TestClient(fast_app)
            first = client.get("/api/snapshot")
            self.assertEqual(first.status_code, 200)
            body = first.json()
            # status shape (same contract as /api/status)
            self.assertIn("watcher", body["status"])
            self.assertIn("queue", body["status"])
            self.assertIn("database", body["status"])
            # self-monitoring: the app reports its own memory
            self.assertIn("rss_mib", body["status"])
            # the library is fully present on the first call
            self.assertIsInstance(body["library"], list)
            movie = next(m for m in body["library"] if m["name"] == "snap")
            # the encode sub-object carries the schema-v7 drift fields (NULL
            # for legacy rows, present as keys either way - non-breaking for
            # existing UI code that ignores them)
            self.assertIn("generation", movie["encode"])
            self.assertIn("input_bitrate", movie["encode"])
            self.assertIn("output_bitrate", movie["encode"])
            self.assertEqual(movie["encode"]["crf"], 22)
            # job history included
            self.assertIsInstance(body["jobs"], list)

            # second call with both ETags: library AND jobs null, status fresh
            second = client.get(
                "/api/snapshot",
                headers={"If-None-Match": f"{body['library_etag']}, {body['jobs_etag']}"},
            )
            self.assertEqual(second.status_code, 200)
            body2 = second.json()
            self.assertIsNone(body2["library"])
            self.assertIsNone(body2["jobs"])
            self.assertEqual(body2["library_etag"], body["library_etag"])
            self.assertEqual(body2["jobs_etag"], body["jobs_etag"])
            self.assertIn("status", body2)
            # only the status rides every tick
            self.assertIn("queue", body2["status"])

            # a state transition invalidates the jobs tag (a FINISHED job
            # row never mutates — (id, state) is the change signal).
            # Enqueue through the app's own queue object: direct SQL would
            # bypass its invariants.
            from shrinkray.jobs import Job, JobKind

            app_module.state.queue.enqueue(Job(kind=JobKind.SCAN, payload={"paths": ["D:/x"]}))
            fourth = client.get(
                "/api/snapshot",
                headers={"If-None-Match": f"{body['library_etag']}, {body['jobs_etag']}"},
            )
            body4 = fourth.json()
            self.assertIsNone(body4["library"])
            self.assertIsInstance(body4["jobs"], list)
            self.assertNotEqual(body4["jobs_etag"], body["jobs_etag"])

            # a RUNNING job's progress writes must invalidate the tag too:
            # report_progress updates the result column while state stays
            # 'running' — the fingerprint covers the running window's
            # result, or the UI's progress bars freeze mid-encode
            job = app_module.state.queue.take_next()
            self.assertIsNotNone(job)
            app_module.state.queue.report_progress(job, {"stage": "scan", "percent": 42})
            fifth = client.get(
                "/api/snapshot",
                headers={"If-None-Match": f"{body4['library_etag']}, {body4['jobs_etag']}"},
            )
            body5 = fifth.json()
            self.assertIsNone(body5["library"])
            self.assertIsInstance(body5["jobs"], list)
            self.assertNotEqual(body5["jobs_etag"], body4["jobs_etag"])

            # a change re-validates the library in the next snapshot
            db.add_encode(self.connection, file_id, "Fast 1080p30", "x265", 1, 1, 0.0)
            self.connection.commit()
            third = client.get("/api/snapshot", headers={"If-None-Match": body["library_etag"]})
            body3 = third.json()
            self.assertIsInstance(body3["library"], list)
            self.assertNotEqual(body3["library_etag"], body["library_etag"])
        finally:
            app_module.state.shutdown()
            app_module.state = None

    def test_reveal_rejects_outside_root(self):
        """The reveal endpoint uses the same path allowlist as everything
        else (a file manager window must never open outside the roots)."""
        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        app_module.state = AppState(Path(self.tmp.name) / "test.db")
        try:
            client = TestClient(fast_app)
            response = client.post("/api/reveal", json={"path": "C:/Windows/System32/cmd.exe"})
            self.assertEqual(response.status_code, 403)
        finally:
            app_module.state.shutdown()
            app_module.state = None

    def test_duplicates_delete_to_trash(self):
        """The delete endpoint moves the file to the OS trash: allowlist
        (403) and missing file (404) are enforced, success is recoverable
        (the file lands in the trash, not the void)."""

        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            victim = root / "duplicate.mkv"
            victim.write_bytes(b"duplicate data")

            app_module.state = AppState(root / "test.db")
            app_module.allow_roots([root])
            # the file was scanned before: a db row exists for it
            app_module.state.with_lock(
                lambda c: c.execute(
                    "INSERT INTO files (path, mtime, size, name, category)"
                    " VALUES (?, 1.0, 15, 'duplicate', 'Cat')",
                    (str(victim),),
                )
            )
            try:
                client = TestClient(fast_app)
                # outside the allowlist => rejected before any file work
                response = client.post(
                    "/api/duplicates/delete", json={"path": "C:/Windows/System32/cmd.exe"}
                )
                self.assertEqual(response.status_code, 403)
                self.assertTrue(victim.is_file())
                # inside the allowlist but not a file => 404
                response = client.post(
                    "/api/duplicates/delete", json={"path": str(root / "ghost.mkv")}
                )
                self.assertEqual(response.status_code, 404)
                # success: the file is gone from its folder
                response = client.post("/api/duplicates/delete", json={"path": str(victim)})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["to"], "trash")
                self.assertFalse(victim.is_file())
                # the row is retired at once: MISSING state without waiting
                # for the next scan (history retained, not hard-deleted)
                row = app_module.state.with_lock(
                    lambda c: c.execute(
                        "SELECT missing_scans, missing_since FROM files WHERE path = ?",
                        (str(victim),),
                    ).fetchone()
                )
                self.assertEqual(row["missing_scans"], 1)
                self.assertIsNotNone(row["missing_since"])
            finally:
                app_module._allowed_roots = []
                app_module.state.shutdown()
                app_module.state = None

    def test_add_skip_and_recognition(self):
        file_id = db.upsert_file(self.connection, self._record("b.mkv"))
        db.add_skip(self.connection, file_id, "no size gain")
        rows = db.get_encodes_for_file(self.connection, file_id)
        self.assertEqual(len(rows), 1)
        self.assertIn("skipped", rows[0]["preset"])
        self.assertIn("no size gain", rows[0]["preset"])
        self.assertEqual(rows[0]["input_size"], 0)

    def test_upsert_and_cache(self):

        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "test.mkv"
            file.write_bytes(b"x")
            stat = file.stat()
            record = db.MovieRecord(
                path=file, mtime=stat.st_mtime, size=stat.st_size, name="test", category="Cat"
            )
            db.upsert_file(self.connection, record)

            row = db.get_file_by_path(self.connection, file)
            self.assertIsNotNone(row)
            self.assertEqual(row["name"], "test")
            # unchanged => valid cache entry
            self.assertTrue(db.is_unchanged(row, file))
            # changed size => invalid
            file.write_bytes(b"more bytes than before")
            self.assertFalse(db.is_unchanged(row, file))

    def test_upsert_updates_existing(self):
        record = self._record("D:/Movies/test.mkv")
        file_id = db.upsert_file(self.connection, record)
        record.name = "updated"
        file_id2 = db.upsert_file(self.connection, record)
        self.assertEqual(file_id, file_id2)
        row = db.get_file_by_path(self.connection, Path("D:/Movies/test.mkv"))
        self.assertEqual(row["name"], "updated")

    def test_compute_hash(self):

        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "a.mkv"
            file.write_bytes(b"A" * (2 * 1024 * 1024))
            hash1 = db.compute_hash(file)
            self.assertEqual(hash1, db.compute_hash(file))
            # different content => different hash
            file.write_bytes(b"B" * (2 * 1024 * 1024))
            self.assertNotEqual(hash1, db.compute_hash(file))

    def test_encode_history(self):
        record = self._record("D:/Movies/test.mkv")
        file_id = db.upsert_file(self.connection, record)
        db.add_encode(self.connection, file_id, "Fast 1080p30", "x265", 1000, 400, 60.0)

        encodes = db.get_encodes_for_file(self.connection, file_id)
        self.assertEqual(len(encodes), 1)
        self.assertEqual(encodes[0]["preset"], "Fast 1080p30")
        self.assertEqual(encodes[0]["saved_percent"], 60.0)

    def test_prune_missing(self):
        # real paths: fictional ones like 'D:/Movies/a.mkv' are absolute on
        # windows but RELATIVE on linux (CI) => scope containment diverges

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            record1 = self._record(str(root / "a.mkv"))
            record2 = self._record(str(root / "b.mkv"))
            id1 = db.upsert_file(self.connection, record1)
            db.upsert_file(self.connection, record2)
            db.add_encode(self.connection, id1, "p", "x265", 1, 1, 0.0)

            # prune with the stored path strings (Path normalizes separators)
            existing = {str(root / "a.mkv")}
            # first scan: b.mkv is flagged, not deleted (grace period)
            result = db.prune_missing(self.connection, existing, scope=str(root))
            self.assertEqual(result.flagged, 1)
            self.assertEqual(result.pruned, 0)
            self.assertEqual(result.missing_now, 1)
            self.assertIsNotNone(db.get_file_by_path(self.connection, root / "b.mkv"))
            self.assertEqual(len(db.get_encodes_for_file(self.connection, id1)), 1)
            row = db.get_file_by_path(self.connection, root / "b.mkv")
            self.assertEqual(row["missing_scans"], 1)
            self.assertIsNotNone(row["missing_since"])

            # grace exhausted: the row (and its encodes) is hard-deleted
            for _ in range(db.MISSING_GRACE - 1):
                result = db.prune_missing(self.connection, existing, scope=str(root))
            self.assertEqual(result.pruned, 1)
            self.assertIsNone(db.get_file_by_path(self.connection, root / "b.mkv"))
            # a.mkv was never missing => its encode history survives
            self.assertEqual(len(db.get_encodes_for_file(self.connection, id1)), 1)

    def test_prune_missing_recovers_reappeared_files(self):

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            db.upsert_file(self.connection, self._record(str(root / "a.mkv")))
            # the file disappears => flagged
            result = db.prune_missing(self.connection, set(), scope=str(root))
            self.assertEqual(result.flagged, 1)
            # the file reappears (e.g. folder was moved back) => un-flagged
            result = db.prune_missing(self.connection, {str(root / "a.mkv")}, scope=str(root))
            self.assertEqual(result.recovered, 1)
            self.assertEqual(result.missing_now, 0)
            row = db.get_file_by_path(self.connection, root / "a.mkv")
            self.assertEqual(row["missing_scans"], 0)
            self.assertIsNone(row["missing_since"])

    def test_prune_missing_mass_flagging(self):
        # more than 20% of the scope flagged at once => the accident warning

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name in ("a", "b", "c", "d", "e", "f"):
                db.upsert_file(self.connection, self._record(str(root / f"{name}.mkv")))
            result = db.prune_missing(self.connection, set(), scope=str(root))
            self.assertEqual(result.flagged, 6)
            self.assertTrue(result.mass)

    def test_upsert_resets_missing_state(self):
        # a file flagged as missing is re-scanned (changed or rescan) =>
        # the upsert clears the missing state

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = str(root / "a.mkv")
            db.upsert_file(self.connection, self._record(path))
            db.prune_missing(self.connection, set(), scope=str(root))
            row = db.get_file_by_path(self.connection, root / "a.mkv")
            self.assertEqual(row["missing_scans"], 1)
            db.upsert_file(self.connection, self._record(path))
            row = db.get_file_by_path(self.connection, root / "a.mkv")
            self.assertEqual(row["missing_scans"], 0)
            self.assertIsNone(row["missing_since"])

    def test_has_output_for_latest_encode(self):

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            file_id = db.upsert_file(self.connection, self._record("c.mkv"))
            # no record at all => not finished
            self.assertFalse(
                db.has_output_for_latest_encode(self.connection, file_id, out / "c.mkv")
            )
            # skip marker (zero output size) => not finished
            db.add_skip(self.connection, file_id, "no size gain")
            self.assertFalse(
                db.has_output_for_latest_encode(self.connection, file_id, out / "c.mkv")
            )
            # real encode recorded but output missing => not finished (must re-encode)
            db.add_encode(self.connection, file_id, "VMAF>95", "x265", 100, 50, 50.0)
            self.assertFalse(
                db.has_output_for_latest_encode(self.connection, file_id, out / "c.mkv")
            )
            # output exists and is non-empty => finished
            (out / "c.mkv").write_bytes(b"encoded data")
            self.assertTrue(
                db.has_output_for_latest_encode(self.connection, file_id, out / "c.mkv")
            )
            # empty output file => not finished
            (out / "c.mkv").write_bytes(b"")
            self.assertFalse(
                db.has_output_for_latest_encode(self.connection, file_id, out / "c.mkv")
            )

    def test_finished_movies_excludes_truncated_output(self):
        """The quality-mode resume set excludes truncated outputs (debris):
        the db says encoded and the file is non-empty, but the duration does
        not verify => re-encode instead of skip-forever."""

        from shrinkray import service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / "movie.mkv"
            movie.write_bytes(b"x")
            out_dir = root / "enc"
            out_dir.mkdir()
            (out_dir / "movie.mkv").write_bytes(b"truncated debris")
            file_id = db.upsert_file(self.connection, self._record(str(movie)))
            db.add_encode(self.connection, file_id, "VMAF>95", "x265", 1000, 500, 50.0)
            with mock.patch("shrinkray.encode.durations_match", return_value=False):
                finished = service.finished_movies(self.connection, [movie], out_dir)
            self.assertEqual(finished, set())
            with mock.patch("shrinkray.encode.durations_match", return_value=True):
                finished = service.finished_movies(self.connection, [movie], out_dir)
            self.assertEqual(finished, {str(movie)})

    def test_verification_recorded_in_db(self):
        """record_quality_results persists verified_vmaf/subsample on the
        encode row (schema v8 columns)."""

        from shrinkray import service
        from shrinkray.quality import QualityResult

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / "movie.mkv"
            movie.write_bytes(b"x")
            result = QualityResult(
                input=movie,
                output=root / "enc" / "movie.mkv",
                success=True,
                target_vmaf=95.0,
                chosen_crf=22,
                achieved_vmaf=95.4,
                input_size=1000,
                output_size=400,
                verified_vmaf=94.9,
                verified_subsample=10,
            )
            outcome = service.record_quality_results(
                self.connection, movie, result, config_helper.Conf(), 95.0
            )
            self.assertTrue(outcome.encoded)
            file_id = self.connection.execute(
                "SELECT id FROM files WHERE path = ?", (str(movie),)
            ).fetchone()["id"]
            row = self.connection.execute(
                "SELECT verified_vmaf, verified_at, verified_subsample FROM encodes "
                "WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(row["verified_vmaf"], 94.9)
            self.assertEqual(row["verified_subsample"], 10)
            self.assertIsNotNone(row["verified_at"])

    def test_verification_without_data_is_noop(self):
        """A result without verification data must not touch the row (every
        non-verify encode goes through the same record path)."""

        from shrinkray import service
        from shrinkray.quality import QualityResult

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / "movie.mkv"
            movie.write_bytes(b"x")
            result = QualityResult(
                input=movie,
                output=root / "enc" / "movie.mkv",
                success=True,
                input_size=1000,
                output_size=400,
            )
            service.record_quality_results(
                self.connection, movie, result, config_helper.Conf(), 95.0
            )
            row = self.connection.execute("SELECT verified_vmaf FROM encodes").fetchone()
            self.assertIsNone(row["verified_vmaf"])

    def test_doctor_healthy_library(self):
        """A library whose rows all exist on disk and whose outputs are
        present produces no findings."""

        from shrinkray import service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / "movie.mkv"
            movie.write_bytes(b"x")
            output = root / "enc" / "movie.mkv"
            output.parent.mkdir()
            output.write_bytes(b"out")
            file_id = db.upsert_file(self.connection, self._record(str(movie)))
            db.add_encode(
                self.connection,
                file_id,
                "VMAF>95",
                "x265",
                1000,
                400,
                60.0,
                output_file=str(output),
            )
            findings = service.doctor(self.connection)
            self.assertEqual(findings, [])

    def test_doctor_reports_dead_paths(self):
        """A db row whose file is gone on disk is a finding (with the
        soft-missing counter as detail)."""

        from shrinkray import service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / "gone.mkv"  # never created
            db.upsert_file(self.connection, self._record(str(movie)))
            self.connection.execute(
                "UPDATE files SET missing_scans = 3 WHERE path = ?", (str(movie),)
            )
            self.connection.commit()
            findings = service.doctor(self.connection)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].issue, "dead path")
            self.assertIn("missing_scans=3", findings[0].detail or "")

    def test_doctor_reports_vanished_outputs(self):
        """An encode row whose output_file no longer exists is a finding."""

        from shrinkray import service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / "movie.mkv"
            movie.write_bytes(b"x")
            file_id = db.upsert_file(self.connection, self._record(str(movie)))
            db.add_encode(
                self.connection,
                file_id,
                "VMAF>95",
                "x265",
                1000,
                400,
                60.0,
                output_file=str(root / "enc" / "deleted.mkv"),
            )
            findings = service.doctor(self.connection)
            issues = [f.issue for f in findings]
            self.assertIn("encode output vanished", issues)

    def test_doctor_probe_all_detects_corruption(self):
        """--probe-all ffprobes every row: a file that exists but does not
        probe is reported as unprobeable; a probeable one is silent."""

        from shrinkray import service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            junk = root / "junk.mkv"
            junk.write_bytes(b"not a movie")
            db.upsert_file(self.connection, self._record(str(junk)))
            with mock.patch("shrinkray.service.probe_duration", return_value=None):
                findings = service.doctor(self.connection, probe_all=True)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].issue, "unprobeable (corrupt/truncated?)")

    def test_prune_missing_scoped(self):
        # a scan of one root must not wipe the history of files outside it
        # (real tmp paths: fictional drives are relative paths on linux/CI)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "library"
            other = Path(tmp).resolve() / "elsewhere"
            root.mkdir()
            other.mkdir()
            id_inside = db.upsert_file(self.connection, self._record(str(root / "a.mkv")))
            db.add_encode(self.connection, id_inside, "p", "x265", 1, 1, 0.0)
            id_outside = db.upsert_file(self.connection, self._record(str(other / "b.mkv")))
            db.add_encode(self.connection, id_outside, "p", "x265", 2, 1, 50.0)

            # scan of root finds only a.mkv, scope limits pruning to that root
            existing = {str(root / "a.mkv")}
            result = db.prune_missing(self.connection, existing, scope=str(root))
            self.assertEqual(result.pruned, 0)  # b.mkv is outside the scope => kept
            self.assertEqual(result.flagged, 0)  # ...and outside => not even flagged
            self.assertIsNotNone(db.get_file_by_path(self.connection, other / "b.mkv"))
            self.assertEqual(len(db.get_encodes_for_file(self.connection, id_outside)), 1)

            # a.mkv is inside the scope and on disk => kept as well
            self.assertIsNotNone(db.get_file_by_path(self.connection, root / "a.mkv"))

            # a file inside the scope that vanished is flagged first, then pruned
            db.upsert_file(self.connection, self._record(str(root / "gone.mkv")))
            result = db.prune_missing(self.connection, existing, scope=str(root))
            self.assertEqual(result.flagged, 1)
            for _ in range(db.MISSING_GRACE - 1):
                result = db.prune_missing(self.connection, existing, scope=str(root))
            self.assertEqual(result.pruned, 1)
            self.assertIsNone(db.get_file_by_path(self.connection, root / "gone.mkv"))


if __name__ == "__main__":
    unittest.main()
