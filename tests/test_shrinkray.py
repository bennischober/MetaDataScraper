import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from shrinkray import config as config_helper
from shrinkray import db, encode, helpers, jobs, quality
from shrinkray import media as read_media


class TestHelpers(unittest.TestCase):
    def test_convert_unit(self):
        self.assertEqual(helpers.convert_unit(1024, helpers.SIZE_UNIT.KB), 1)
        self.assertEqual(helpers.convert_unit(1048576, helpers.SIZE_UNIT.MB), 1)
        self.assertEqual(helpers.convert_unit(1073741824, helpers.SIZE_UNIT.GB), 1)
        self.assertEqual(helpers.convert_unit(123, helpers.SIZE_UNIT.BYTES), 123)

    def test_sanitize_number(self):
        self.assertEqual(helpers.sanitize_number(0), "0")
        self.assertEqual(helpers.sanitize_number(1.5), "1,5")
        self.assertEqual(helpers.sanitize_number(10), "10")
        self.assertEqual(helpers.sanitize_number("3.14"), "3,14")

    def test_timestr_to_int(self):
        self.assertEqual(helpers.timestr_to_int("01:02:03"), 3723)
        self.assertEqual(helpers.timestr_to_int("00:00:00"), 0)
        self.assertEqual(helpers.timestr_to_int(None), 0)
        self.assertEqual(helpers.timestr_to_int("garbage"), 0)

    def test_calc_time(self):
        self.assertEqual(helpers.calc_time(None, "01:00:00"), "01:00:00")
        self.assertEqual(helpers.calc_time("01:00:00", None), "01:00:00")
        self.assertEqual(helpers.calc_time("00:59:59", "00:00:01"), "01:00:00")
        self.assertEqual(helpers.calc_time("10:00:00", "05:30:15"), "15:30:15")

    def test_get_other_codecs(self):
        self.assertEqual(helpers.get_other_codecs([]), "")
        self.assertEqual(helpers.get_other_codecs([{"name": "aac"}]), "aac")
        self.assertEqual(
            helpers.get_other_codecs([{"name": "aac"}, {"name": "subrip"}]), "aac, subrip"
        )

    def test_generate_string(self):
        item = {
            "category": "Movies",
            "name": "Test",
            "duration": "01:00:00",
            "size": 1.5,
            "codec": "h264",
            "bitrate": 2.5,
            "aspect_ratio": "16x9",
            "dimensions": {"width": 1920, "height": 1080},
            "compression_rate": 1.5,
            "fps": 25,
            "crop": 0,
            "hdr": 0,
            "other_codecs": "aac, subrip",
            "audio_languages": "de, en",
        }
        row = helpers.generate_string(item, ";", True)
        self.assertEqual(
            row, "Movies;Test;01:00:00;1,5;h264;2,5;16x9;1920x1080;1,5;25;0;0;aac, subrip;de, en\n"
        )
        # without category => empty first column
        row = helpers.generate_string(item, ";", False)
        self.assertTrue(row.startswith(";Test;"))


class TestResources(unittest.TestCase):
    """Resource politeness: priority spawn kwargs, kill switch, RSS."""

    def test_spawn_kwargs_respects_kill_switch(self):
        """idle_priority=False disables the polite spawn kwargs."""
        from shrinkray import resources

        with mock.patch.object(resources, "_IDLE_PRIORITY", True):
            kwargs = resources.spawn_kwargs()
            if os.name == "nt":
                self.assertIn("creationflags", kwargs)
            else:
                self.assertEqual(kwargs, {})
        with mock.patch.object(resources, "_IDLE_PRIORITY", False):
            self.assertEqual(resources.spawn_kwargs(), {})

    def test_rss_mib_sane(self):
        """The RSS is a plausible number (10 MiB .. 4 GiB) or None."""
        from shrinkray.resources import rss_mib

        rss = rss_mib()
        if rss is not None:
            self.assertGreaterEqual(rss, 10.0)
            self.assertLess(rss, 4096.0)

    def test_config_applies_resource_policy(self):
        """load_config honors resources.idle_priority (and resets cleanly)."""
        from shrinkray import resources

        old = config_helper._config
        old_priority = resources._IDLE_PRIORITY
        try:
            config_helper._config = None
            config = config_helper.Conf(
                {"resources": {"idle_priority": False}, "handbrake": {"max_threads": 6}}
            )
            self.assertFalse(config.idle_priority)
            self.assertEqual(config.handbrake_max_threads, 6)
            resources.apply(config)
            self.assertFalse(resources._IDLE_PRIORITY)
            config_on = config_helper.Conf({})
            self.assertTrue(config_on.idle_priority)
            self.assertEqual(config_on.handbrake_max_threads, 0)
        finally:
            config_helper._config = old
            resources._IDLE_PRIORITY = old_priority

    def test_conf_defaults(self):
        conf = config_helper.Conf({})
        self.assertEqual(conf.handbrake_max_threads, 0)
        self.assertTrue(conf.idle_priority)


class TestIntegrations(unittest.TestCase):
    """Jellyfin tap-on-the-shoulder: opt-in, env token, never raises."""

    def setUp(self):
        from shrinkray import integrations

        self.integrations = integrations

    def _conf(self, data: dict):
        return config_helper.Conf(data)

    def test_unconfigured_is_noop(self):
        self.assertIsNone(self.integrations.jellyfin_settings(self._conf({})))
        # notify without configuration: False, never raises
        self.assertFalse(self.integrations.notify_jellyfin(self._conf({})))
        self.assertEqual(self.integrations.notify_after_encodes(self._conf({}), []), 0)

    def test_url_without_token_is_skipped(self):
        conf = self._conf({"integrations": {"jellyfin": {"url": "http://nas:8096"}}})
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(self.integrations.jellyfin_settings(conf))

    def test_token_from_env_var(self):
        conf = self._conf(
            {"integrations": {"jellyfin": {"url": "http://nas:8096/", "token_env": "MY_TOKEN_VAR"}}}
        )
        with mock.patch.dict(os.environ, {"MY_TOKEN_VAR": " secret "}, clear=True):
            settings = self.integrations.jellyfin_settings(conf)
        self.assertEqual(settings, {"url": "http://nas:8096", "token": "secret"})

    def test_notify_posts_to_refresh_endpoint(self):
        """A configured integration POSTs to {url}/Library/Refresh with the token."""
        conf = self._conf({"integrations": {"jellyfin": {"url": "http://nas:8096"}}})
        captured: dict = {}

        class FakeResponse:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            # urllib capitalizes header keys ("X-emby-token") => match loosely
            captured["token"] = next(
                (v for k, v in request.headers.items() if k.lower() == "x-emby-token"), None
            )
            return FakeResponse()

        with (
            mock.patch.dict(os.environ, {"SHRINKRAY_JELLYFIN_TOKEN": "tok"}, clear=True),
            mock.patch("urllib.request.urlopen", fake_urlopen),
        ):
            ok = self.integrations.notify_jellyfin(conf)
        self.assertTrue(ok)
        self.assertEqual(captured["url"], "http://nas:8096/Library/Refresh")
        self.assertEqual(captured["token"], "tok")
        self.assertEqual(captured["method"], "POST")

    def test_notify_never_raises(self):
        """Server down / bad response => False and a log, no exception."""
        conf = self._conf({"integrations": {"jellyfin": {"url": "http://127.0.0.1:9"}}})
        with mock.patch.dict(os.environ, {"SHRINKRAY_JELLYFIN_TOKEN": "tok"}, clear=True):
            self.assertFalse(self.integrations.notify_jellyfin(conf, timeout=0.2))

    def test_notify_after_encodes_counts(self):
        conf = self._conf({})
        self.assertEqual(self.integrations.notify_after_encodes(conf, [Path("a.mkv")]), 1)


class TestReadMedia(unittest.TestCase):
    def test_parse_frame_rate(self):
        self.assertEqual(read_media.parse_frame_rate("25/1"), 25.0)
        self.assertAlmostEqual(read_media.parse_frame_rate("30000/1001"), 29.97002997, places=6)
        self.assertEqual(read_media.parse_frame_rate("29.97"), 29.97)
        self.assertIsNone(read_media.parse_frame_rate("0/0"))
        self.assertIsNone(read_media.parse_frame_rate(None))
        self.assertIsNone(read_media.parse_frame_rate("garbage"))

    def test_get_raw_duration(self):
        self.assertEqual(
            read_media.get_raw_duration(
                {"tags": {"NUMBER_OF_FRAMES-eng": 100}, "avg_frame_rate": "25/1"}
            ),
            4.0,
        )
        self.assertEqual(
            read_media.get_raw_duration({"nb_frames": 100, "avg_frame_rate": "30000/1001"}),
            100 / (30000 / 1001),
        )
        self.assertIsNone(read_media.get_raw_duration({}))
        self.assertIsNone(
            read_media.get_raw_duration({"tags": {"NUMBER_OF_FRAMES": 10}, "avg_frame_rate": "0/0"})
        )

    def test_get_duration(self):
        self.assertEqual(
            read_media.get_duration({"tags": {"DURATION-eng": "01:02:03.400000000"}}), "01:02:03"
        )
        self.assertEqual(
            read_media.get_duration({"tags": {"DURATION-de": "01:02:03.400000000"}}), "01:02:03"
        )
        self.assertEqual(
            read_media.get_duration({"tags": {"DURATION": "01:02:03.400000000"}}), "01:02:03"
        )
        self.assertIsNone(read_media.get_duration({"tags": {}}))
        self.assertIsNone(read_media.get_duration({}))
        # configured language takes precedence
        self.assertEqual(
            read_media.get_duration(
                {"tags": {"DURATION-de": "02:00:00", "DURATION-eng": "01:00:00"}}, "de"
            ),
            "02:00:00",
        )
        self.assertEqual(
            read_media.get_duration(
                {"tags": {"DURATION-de": "02:00:00", "DURATION-eng": "01:00:00"}}, "en"
            ),
            "01:00:00",
        )

    def test_get_video_stream(self):
        streams = [{"codec_type": "audio"}, {"codec_type": "video", "codec_name": "h264"}]
        self.assertEqual(read_media.get_video_stream(streams)["codec_name"], "h264")
        self.assertIsNone(read_media.get_video_stream([]))
        self.assertIsNone(read_media.get_video_stream([{"codec_type": "audio"}]))

    def test_get_audio_languages(self):
        streams = [
            {"codec_type": "audio", "tags": {"language": "de"}},
            {"codec_type": "audio", "tags": {"language": "en"}},
            {"codec_type": "audio", "tags": {}},
            {"codec_type": "video", "tags": {"language": "de"}},
        ]
        self.assertEqual(read_media.get_audio_languages(streams), "de, en")
        self.assertEqual(read_media.get_audio_languages([]), "")

    def test_get_aspect_ratio(self):
        self.assertEqual(read_media.get_aspect_ratio(1920, 1080), "16x9")
        self.assertEqual(read_media.get_aspect_ratio(3840, 2160), "16x9")

    def test_is_hdr(self):
        config = config_helper.Conf(
            {"codec": {"hdr": {"color_space": ["bt2020nc"], "color_transfer": ["smpte2084"]}}}
        )
        hdr_stream = {"color_space": "bt2020nc", "color_transfer": "smpte2084"}
        sdr_stream = {"color_space": "bt709", "color_transfer": "bt709"}
        self.assertTrue(read_media.is_hdr(hdr_stream, config))
        self.assertFalse(read_media.is_hdr(sdr_stream, config))
        self.assertFalse(read_media.is_hdr({}, config))


class TestEncode(unittest.TestCase):
    def test_build_command(self):
        command = encode.build_command(
            "C:/HandBrake/HandBrakeCLI.exe",
            Path("in.mkv"),
            Path("out.mkv"),
            "Fast 1080p30",
            "x265",
        )
        self.assertEqual(command[0], "C:/HandBrake/HandBrakeCLI.exe")
        self.assertIn("--input", command)
        self.assertIn("in.mkv", command)
        self.assertIn("--output", command)
        self.assertIn("out.mkv", command)
        self.assertIn("Fast 1080p30", command)
        self.assertIn("x265", command)
        # audio passthrough without conflicting options
        self.assertIn("--aencoder", command)
        self.assertNotIn("--audio", command)
        self.assertIn("--optimize", command)

    def test_cap_threads(self):
        """The thread cap normalizes 0/None/negative to uncapped."""
        self.assertIsNone(encode.cap_threads(None))
        self.assertIsNone(encode.cap_threads(0))
        self.assertIsNone(encode.cap_threads(-3))
        self.assertEqual(encode.cap_threads(8), 8)

    def test_build_command_with_thread_cap(self):
        command = encode.build_command(
            "hb",
            Path("in.mkv"),
            Path("out.mkv"),
            "Fast 1080p30",
            "x265",
            encoder_threads=8,
        )
        self.assertIn("--encopts", command)
        self.assertIn("threads=8", command)
        # uncapped: no encopts at all
        plain = encode.build_command("hb", Path("in.mkv"), Path("out.mkv"), "Fast 1080p30", "x265")
        self.assertNotIn("--encopts", plain)

    def test_encode_movies_max_threads_tightens_share(self):
        """The configured cap wins when tighter than the parallel CPU share."""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "movie.mkv"
            source.write_bytes(b"x")
            with (
                tempfile.TemporaryDirectory() as out,
                mock.patch("shrinkray.encode.probe_duration_seconds", return_value=600.0),
                mock.patch("shrinkray.encode.find_handbrake", return_value="hb"),
                mock.patch("shrinkray.encode._encode_with_command") as enc,
            ):
                enc.side_effect = lambda i, o, cmd, progress=None: encode.EncodeResult(
                    input=i, output=o, success=True
                )
                # workers=2 on 16 cores would give a big share; the cap must win
                encode.encode_movies(
                    [source],
                    Path(out),
                    preset="Fast 1080p30",
                    encoder="x265",
                    workers=2,
                    max_threads=4,
                )
                command = enc.call_args[0][2]
        self.assertIn("--encopts", command)
        self.assertIn("threads=4", command)

    def test_saved_percent(self):
        result = encode.EncodeResult(
            input=Path("in.mkv"),
            output=Path("out.mkv"),
            success=True,
            input_size=1000,
            output_size=400,
        )
        self.assertEqual(result.saved_percent, 60.0)
        # larger output => negative
        result = encode.EncodeResult(
            input=Path("in.mkv"),
            output=Path("out.mkv"),
            success=True,
            input_size=1000,
            output_size=1500,
        )
        self.assertEqual(result.saved_percent, -50.0)
        # zero sizes => 0
        result = encode.EncodeResult(input=Path("in.mkv"), output=Path("out.mkv"), success=True)
        self.assertEqual(result.saved_percent, 0.0)

    def test_encode_file_missing_input(self):
        with self.assertRaises(FileNotFoundError):
            encode.encode_file(Path("does-not-exist.mkv"), Path("out.mkv"))

    def test_encode_movies_dry_run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "encoded"
            results = encode.encode_movies([Path("a.mkv"), Path("b.mkv")], out_dir, dry_run=True)
            self.assertEqual(len(results), 0)
            # dry run => no output directory created
            self.assertFalse(out_dir.exists())

    def test_encode_movies_skip_existing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            (out_dir / "a.mkv").write_bytes(b"x")
            results = encode.encode_movies([Path("a.mkv")], out_dir)
            # skipped => no result recorded
            self.assertEqual(len(results), 0)

    def test_streamed_encode_reaps_exit_code(self):
        """The streamed encode (progress path) must reap the process: at EOF
        Popen.returncode is still None, and reading it without wait() marked
        every successful streamed encode as failed with the whole HandBrake
        log as its error (job #20 was the first full streamed encode and
        exposed this)."""
        import tempfile

        from shrinkray import encode

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.mkv"
            out = Path(tmp) / "out.mkv"
            src.write_bytes(b"x" * 8)
            # cmd exits 0 and closes its pipes: EOF on stdout is reached,
            # so the streamed loop ends without cancellation
            command = ["cmd", "/c", "exit 0"] if os.name == "nt" else ["true"]
            result = encode._encode_with_command(src, out, command, progress=lambda p: None)
            # the fake encode wrote no output file: the command itself must
            # still have been reaped as SUCCESS (exit 0), reaching the
            # "no output file" check rather than "handbrake failed"
            self.assertIn(result.error in (None, "no output file was written"), (True,))
            self.assertTrue(
                result.success or result.error == "no output file was written",
                f"streamed run misread exit code: success={result.success} error={result.error!r}",
            )


class TestDb(unittest.TestCase):
    def setUp(self):
        import tempfile

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
        import tempfile

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
        import os
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
        import tempfile

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
        import tempfile

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
        import tempfile

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
        import tempfile

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

            # second call with the ETag: library null, rest fresh
            second = client.get("/api/snapshot", headers={"If-None-Match": body["library_etag"]})
            self.assertEqual(second.status_code, 200)
            body2 = second.json()
            self.assertIsNone(body2["library"])
            self.assertEqual(body2["library_etag"], body["library_etag"])
            self.assertIn("status", body2)
            self.assertIn("jobs", body2)

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
        import tempfile

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
        import tempfile

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
        import tempfile

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
        import tempfile

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
        import tempfile

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
        import tempfile

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
        import tempfile

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
        import tempfile

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

    def test_prune_missing_scoped(self):
        # a scan of one root must not wipe the history of files outside it
        # (real tmp paths: fictional drives are relative paths on linux/CI)
        import tempfile

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


class TestMain(unittest.TestCase):
    def test_get_category(self):
        from shrinkray.__main__ import get_category

        root = Path("D:/Movies")
        # normal: root/Category/MovieFolder/file.mkv
        self.assertEqual(
            get_category(Path("D:/Movies/Action/Movie 2020/movie.mkv"), root), "Action"
        )
        # movie directly in the root
        self.assertEqual(get_category(Path("D:/Movies/movie.mkv"), root), "Uncategorized")
        # deeper nesting => still the first folder level
        self.assertEqual(get_category(Path("D:/Movies/Action/Sub/movie.mkv"), root), "Action")

    def test_get_name(self):
        from shrinkray.__main__ import get_name

        self.assertEqual(get_name(Path("D:/Movies/Action/Movie.mkv")), "Movie")
        # multi-file folder => folder name
        self.assertEqual(get_name(Path("D:/Movies/Action/movie_t0.mkv")), "Action")

    def test_find_movies_exclude(self):
        import tempfile

        from shrinkray.__main__ import find_movies

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.mkv").write_bytes(b"x")
            (root / "out").mkdir()
            (root / "out" / "b.mkv").write_bytes(b"x")

            movies = find_movies(root, [".mkv"], exclude=root / "out")
            names = [m.name for m in movies]
            self.assertIn("a.mkv", names)
            self.assertNotIn("b.mkv", names)
            # without exclusion both are found
            movies = find_movies(root, [".mkv"])
            self.assertEqual(len(movies), 2)

    def test_scan_movies_parallel_deterministic(self):
        import tempfile

        from shrinkray.__main__ import scan_movies

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # create test files (no valid media => all error, but order is checked)
            (root / "Cat1").mkdir()
            (root / "Cat1" / "a1.mkv").write_bytes(b"x")
            (root / "Cat1" / "a2.mkv").write_bytes(b"x")
            (root / "Cat2").mkdir()
            (root / "Cat2" / "b1.mkv").write_bytes(b"x")

            config = config_helper.Conf()

            for workers in (1, 2, 8):
                _, _movies, errors = scan_movies(root, config, workers=workers)
                # all files error => error messages in deterministic file order
                self.assertEqual(len(errors), 3)
                self.assertIn("a1.mkv", errors[0])
                self.assertIn("a2.mkv", errors[1])
                self.assertIn("b1.mkv", errors[2])


class TestQuality(unittest.TestCase):
    def test_telemetry_saves_failure_bundle(self):
        """A failure bundle keeps stderr, command, context and artifacts."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "test.db"
            workdir = Path(tmp) / "work"
            workdir.mkdir()
            (workdir / "vmaf.json").write_text('{"pooled_metrics": {}}', encoding="utf-8")

            bundle = quality.save_failure(
                db_file=db_file,
                kind="vmaf-measure",
                command=["ffmpeg", "-i", "x.mkv"],
                stderr="",
                workdir=workdir,
                artifacts=["vmaf.json"],
                context={"hdr": True, "returncode": 1},
            )
            self.assertIsNotNone(bundle)
            # empty stderr is still recorded as an empty file (evidence of
            # the emptiness — yesterday's bug was exactly this blindness)
            self.assertTrue((bundle / "stderr.txt").exists())
            self.assertEqual(
                (bundle / "command.txt").read_text(encoding="utf-8"), "ffmpeg\n-i\nx.mkv\n"
            )
            self.assertTrue((bundle / "traceback.txt").exists())
            context = (bundle / "context.json").read_text(encoding="utf-8")
            self.assertIn('"hdr": true', context)
            # the partial artifact was copied (not moved)
            self.assertTrue((bundle / "artifact-vmaf.json").exists())
            self.assertTrue((workdir / "vmaf.json").exists())

    def test_telemetry_never_raises(self):
        """Telemetry must not break the error path it reports on."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # a file where the logs dir should be => mkdir fails => None
            blocker = Path(tmp) / "logs"
            blocker.write_text("not a directory", encoding="utf-8")
            bundle = quality.save_failure(
                db_file=Path(tmp) / "test.db",
                kind="encode",
            )
            self.assertIsNone(bundle)

    def test_sample_offsets(self):
        # 3 samples spread over 10%-90% of a 100s movie
        offsets = quality.sample_offsets(100.0, 3)
        self.assertEqual(offsets, [10.0, 50.0, 90.0])
        # single sample => middle-ish position
        self.assertEqual(quality.sample_offsets(100.0, 1), [10.0])
        # degenerate inputs
        self.assertEqual(quality.sample_offsets(0.0, 3), [])
        self.assertEqual(quality.sample_offsets(100.0, 0), [])

    def test_bisect_crf(self):
        # monotonic: low crf passes, high crf fails => largest passing crf wins
        passing = {14, 15, 16, 17, 18, 19, 20, 21}
        chosen = quality.bisect_crf(lambda c: c in passing, 14, 32)
        self.assertEqual(chosen, 21)
        # even the lowest crf fails => fall back to the lowest
        self.assertEqual(quality.bisect_crf(lambda c: False, 14, 32), 14)
        # everything passes => hi - 1... no, the largest measured pass
        self.assertEqual(quality.bisect_crf(lambda c: True, 14, 32), 31)

    def test_build_vmaf_command(self):
        for style in (quality.VMAF_STYLE_LEGACY, quality.VMAF_STYLE_MODERN):
            command = quality.build_vmaf_command(Path("enc.mkv"), Path("ref.mkv"), style=style)
            joined = " ".join(command)
            # distorted stream must be the first input (libvmaf main)
            self.assertEqual(command[command.index("-i") + 1], "enc.mkv")
            self.assertEqual(command[command.index("-i", command.index("-i") + 1) + 1], "ref.mkv")
            # model by bare filename => no windows drive colon in the filtergraph
            self.assertNotIn("\\", joined)
            if style == quality.VMAF_STYLE_MODERN:
                self.assertIn("model=path=vmaf_v0.6.1.json", joined)
            else:
                self.assertIn("model_path=vmaf_v0.6.1.json", joined)

    def test_detect_vmaf_style_without_ffmpeg(self):
        # a missing ffmpeg must raise a clear error (CI has no ffmpeg)
        with self.assertRaises(RuntimeError):
            quality.detect_vmaf_style(binary="definitely-not-installed-ffmpeg")

    def test_parse_vmaf_log(self):
        log_text = json.dumps({"pooled_metrics": {"vmaf": {"mean": 94.002412, "min": 46.9}}})
        self.assertAlmostEqual(quality.parse_vmaf_log(log_text), 94.002412, places=5)
        with self.assertRaises(ValueError):
            quality.parse_vmaf_log(json.dumps({"pooled_metrics": {}}))
        with self.assertRaises(ValueError):
            quality.parse_vmaf_log("{}")

    def test_build_encode_command(self):
        command = quality.build_encode_command(
            "hb.exe", Path("in.mkv"), Path("out.mkv"), 23, "x265", "medium"
        )
        self.assertIn("--quality", command)
        self.assertEqual(command[command.index("--quality") + 1], "23")
        self.assertIn("--encoder-preset", command)
        self.assertEqual(command[command.index("--encoder-preset") + 1], "medium")

    def test_min_encode_seconds_guard(self):
        self.assertEqual(encode.MIN_ENCODE_SECONDS, 90.0)
        # the guard lives in the encode path => importable constant

    def test_parse_crop_line(self):
        text = "[Parsed_cropdetect] crop=1920:800:0:140\n[Parsed_cropdetect] crop=1920:796:0:142\n"
        self.assertEqual(quality.parse_crop_line(text), (1920, 796, 0, 142))
        self.assertIsNone(quality.parse_crop_line("nothing here"))

    def test_intersect_crop_rects(self):
        frame = (1920, 1080)
        # identical rects => those bars
        rects = [(1920, 800, 0, 140), (1920, 800, 0, 140)]
        bars = quality.intersect_crop_rects(rects, frame)
        self.assertEqual(bars, (140, 140, 0, 0))
        # one sample with smaller/different bars => conservative intersection
        # (the picture area present in ALL samples is kept: y in [140, 940])
        smaller = [(1920, 800, 0, 140), (1920, 900, 0, 90)]
        self.assertEqual(quality.intersect_crop_rects(smaller, frame), (140, 140, 0, 0))
        # one sample showing side bars only => top/bottom bars vanish
        sides = [(1920, 1080, 0, 0), (1800, 1080, 60, 0)]
        self.assertEqual(quality.intersect_crop_rects(sides, frame), (0, 0, 60, 60))
        # noise-sized bars (below 2% of the dimension) => None
        tiny = [(1920, 1060, 0, 10), (1920, 1060, 0, 10)]
        self.assertIsNone(quality.intersect_crop_rects(tiny, frame))
        # empty input
        self.assertIsNone(quality.intersect_crop_rects([], frame))

    def test_build_encode_command_crop(self):
        command = quality.build_encode_command(
            "hb.exe", Path("in.mkv"), Path("out.mkv"), 20, "x265", "medium", crop=(140, 140, 0, 0)
        )
        self.assertEqual(command[command.index("--crop") + 1], "140:140:0:0")
        # no crop => explicit 0:0:0:0 (auto-crop would break VMAF geometry)
        plain = quality.build_encode_command(
            "hb.exe", Path("in.mkv"), Path("out.mkv"), 20, "x265", "medium"
        )
        self.assertEqual(plain[plain.index("--crop") + 1], "0:0:0:0")

    def test_build_encode_command_threads(self):
        """The quality pipeline passes the cap via --encopts threads=N."""
        command = quality.build_encode_command(
            "hb",
            Path("in.mkv"),
            Path("out.mkv"),
            20,
            "x265",
            "medium",
            encoder_threads=6,
        )
        self.assertIn("--encopts", command)
        self.assertIn("threads=6", command)
        plain = quality.build_encode_command(
            "hb", Path("in.mkv"), Path("out.mkv"), 20, "x265", "medium"
        )
        self.assertNotIn("--encopts", plain)


class TestReport(unittest.TestCase):
    def test_generate_report(self):
        import tempfile

        from shrinkray.report import generate_report

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            connection = db.connect(db_path)
            file_id = connection.execute(
                "INSERT INTO files (path, mtime, size, name, category, duration, codec, width, height)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("D:/Movies/a.mkv", 1.0, 1000, "a", "Movies", "01:00:00", "h264", 1920, 1080),
            ).lastrowid
            connection.execute(
                "INSERT INTO encodes (file_id, preset, encoder, input_size, output_size,"
                " saved_percent, chosen_crf, achieved_vmaf)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, "VMAF>95.0", "x265", 1000, 400, 60.0, 20, 95.4),
            )
            connection.commit()
            connection.close()

            out = Path(tmp) / "report.html"
            generate_report(db_path, out)
            html = out.read_text(encoding="utf-8")
            self.assertIn("ShrinkRay Report", html)
            self.assertIn("a.mkv", html)
            self.assertIn("95.40", html)
            # the skipped-preset rendering
            self.assertIn("quality mode (VMAF&gt;95.0)", html)


class TestWatch(unittest.TestCase):
    def test_find_new_movies(self):
        import tempfile

        from shrinkray.watch import find_new_movies

        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp)
            (inbox / "a.mkv").write_bytes(b"x")
            (inbox / "b.MKV").write_bytes(b"x")
            (inbox / "c.txt").write_bytes(b"x")
            (inbox / "sub").mkdir()
            (inbox / "sub" / "d.mkv").write_bytes(b"x")  # not recursive

            # nothing known => a and b (deduplicated), not c, not subdir
            new = find_new_movies(inbox, [".mkv"], set())
            names = {m.name for m in new}
            self.assertEqual(names, {"a.mkv", "b.MKV"})

            # everything known => empty
            known = {str(m.resolve()) for m in new}
            self.assertEqual(find_new_movies(inbox, [".mkv"], known), [])

    def test_acquire_and_release_lock(self):
        import tempfile

        from shrinkray.watch import acquire_lock, release_lock

        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "watch.lock"
            self.assertTrue(acquire_lock(lock))
            # second acquire fails (our own pid is alive)
            self.assertFalse(acquire_lock(lock))
            release_lock(lock)
            self.assertFalse(lock.exists())
            # stale lock (dead pid) => take over
            lock.write_text("999999999", encoding="utf-8")
            self.assertTrue(acquire_lock(lock))
            release_lock(lock)

    def test_is_stable_min_age(self):
        import tempfile

        from shrinkray.watch import is_stable

        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "fresh.mkv"
            fresh.write_bytes(b"x")
            # just created => below the minimum age => not stable (fast path,
            # no sleep because the age check comes first)
            self.assertFalse(is_stable(fresh))

    def test_decide_too_short(self):
        import tempfile

        from shrinkray.watch import decide

        with tempfile.TemporaryDirectory() as tmp:
            # a real, very short movie file (valid media, below the minimum)
            clip = Path(tmp) / "clip.mkv"
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg is None:
                self.skipTest("ffmpeg not available")
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=duration=1:size=320x240:rate=10",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    str(clip),
                ],
                capture_output=True,
                check=True,
            )
            self.assertEqual(decide(clip, 95.0, False, "x265"), "too short")

        with tempfile.TemporaryDirectory() as tmp:
            # junk file (invalid media) => must not crash the decision
            junk = Path(tmp) / "junk.mkv"
            junk.write_bytes(b"not a movie")
            self.assertEqual(decide(junk, 95.0, False, "x265"), "encode")


class TestAdaptivePoll(unittest.TestCase):
    def test_idle_growth_caps_at_max(self):
        from shrinkray.watch import next_poll_interval

        # idle polls double the interval up to 4x the base: 60 -> 120 -> 240 -> 240
        interval = 60.0
        interval = next_poll_interval(interval, 60.0, False)
        self.assertEqual(interval, 120.0)
        interval = next_poll_interval(interval, 60.0, False)
        self.assertEqual(interval, 240.0)
        interval = next_poll_interval(interval, 60.0, False)
        self.assertEqual(interval, 240.0)
        interval = next_poll_interval(interval, 60.0, False)
        self.assertEqual(interval, 240.0)

    def test_activity_resets_to_base(self):
        from shrinkray.watch import next_poll_interval

        # any new candidate (even skipped/unstable ones) resets to the base
        self.assertEqual(next_poll_interval(240.0, 60.0, True), 60.0)
        self.assertEqual(next_poll_interval(60.0, 60.0, True), 60.0)

    def test_non_default_base(self):
        from shrinkray.watch import next_poll_interval

        # growth is capped relative to the given base, not the default
        interval = 5.0
        interval = next_poll_interval(interval, 5.0, False)
        self.assertEqual(interval, 10.0)
        interval = next_poll_interval(interval, 5.0, False)
        self.assertEqual(interval, 20.0)
        interval = next_poll_interval(interval, 5.0, False)
        self.assertEqual(interval, 20.0)
        # reset also works with a non-default base
        self.assertEqual(next_poll_interval(20.0, 5.0, True), 5.0)


class TestReportCategory(unittest.TestCase):
    def test_generate_report_category_breakdown(self):
        import tempfile

        from shrinkray.report import generate_report

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            connection = db.connect(db_path)

            def add_file(name, category):
                return connection.execute(
                    "INSERT INTO files (path, mtime, size, name, category, duration, codec, width, height)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"D:/Movies/{name}",
                        1.0,
                        1000,
                        name,
                        category,
                        "01:00:00",
                        "h264",
                        1920,
                        1080,
                    ),
                ).lastrowid

            gb = 1024**3
            # category "Movies": one encode, one skip
            movies_encode = add_file("m1.mkv", "Movies")
            movies_skip = add_file("m2.mkv", "Movies")
            # category "Series": one skip, one never processed (no encode row)
            series_skip = add_file("s1.mkv", "Series")
            add_file("s2.mkv", "Series")

            connection.execute(
                "INSERT INTO encodes (file_id, preset, encoder, input_size, output_size,"
                " saved_percent, chosen_crf, achieved_vmaf)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (movies_encode, "VMAF>95.0", "x265", 3 * gb, int(1.5 * gb), 50.0, 20, 95.4),
            )
            connection.execute(
                "INSERT INTO encodes (file_id, preset, encoder, input_size, output_size,"
                " saved_percent, chosen_crf, achieved_vmaf)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (movies_skip, "skipped (too short)", "x265", gb, gb, 0.0, None, None),
            )
            connection.execute(
                "INSERT INTO encodes (file_id, preset, encoder, input_size, output_size,"
                " saved_percent, chosen_crf, achieved_vmaf)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (series_skip, "skipped (below minimum)", "x265", gb, gb, 0.0, None, None),
            )
            connection.commit()
            connection.close()

            out = Path(tmp) / "report.html"
            generate_report(db_path, out)
            html = out.read_text(encoding="utf-8")

            # the category breakdown sits between the summary cards and the movie table
            self.assertLess(html.index("Movies in database"), html.index("<th>Category</th>"))
            self.assertLess(html.index("<th>Category</th>"), html.index("<th>Movie</th>"))

            # "Movies": 2 movies, 1 encoded, 1 skipped
            self.assertIn(
                "<td>Movies</td><td class='num'>2</td><td class='num'>1</td><td class='num'>1</td>",
                html,
            )
            # "Series": 2 movies, 0 encoded, 1 skipped, and no encodes => '—' saved
            self.assertIn(
                "<td>Series</td><td class='num'>2</td><td class='num'>0</td><td class='num'>1</td>"
                "<td class='num'>1.00 GB</td><td class='num'>—</td>",
                html,
            )


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


class TestEncodeLock(unittest.TestCase):
    """The per-output-dir encode slot: collision-proof, stale-aware."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name).resolve() / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def test_acquire_and_release(self):
        from shrinkray.encode_lock import guard_encodes

        with guard_encodes(self.out, holder="test") as lock:
            self.assertTrue(lock.is_file())
            # pid-stamped
            self.assertEqual(int(lock.read_text()), os.getpid())
        self.assertFalse(lock.exists())

    def test_second_claimant_is_rejected(self):
        from shrinkray.encode_lock import EncodeBusyError, guard_encodes

        with guard_encodes(self.out), self.assertRaises(EncodeBusyError) as ctx:
            guard_encodes(self.out).__enter__()
        self.assertIn("pid", str(ctx.exception))

    def test_stale_lock_is_stolen(self):
        """A lock whose pid is dead is stolen (not honored forever)."""
        from shrinkray.encode_lock import _lock_path, acquire_encode_slot

        self.out.mkdir(parents=True, exist_ok=True)
        lock = _lock_path(self.out)
        lock.write_text("999999999")  # pid that does not exist
        acquired = acquire_encode_slot(self.out, holder="test")
        self.assertEqual(acquired, lock)
        # the stolen lock is re-stamped with OUR pid
        self.assertEqual(int(lock.read_text()), os.getpid())
        lock.unlink()

    def test_garbage_lock_content_is_stolen(self):
        from shrinkray.encode_lock import _lock_path, acquire_encode_slot

        self.out.mkdir(parents=True, exist_ok=True)
        lock = _lock_path(self.out)
        lock.write_text("not-a-pid")
        acquired = acquire_encode_slot(self.out)
        self.assertEqual(acquired, lock)
        lock.unlink()

    def test_encode_movies_busy_raises(self):
        """encode_movies propagates EncodeBusyError (no clobber, no swallow)."""
        import tempfile

        from shrinkray.encode_lock import EncodeBusyError, guard_encodes

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "movie.mkv"
            source.write_bytes(b"x")
            with (
                guard_encodes(Path(tmp) / "enc"),
                mock.patch("shrinkray.encode.probe_duration_seconds", return_value=600.0),
                mock.patch("shrinkray.encode.find_handbrake", return_value="hb"),
                self.assertRaises(EncodeBusyError),
            ):
                encode.encode_movies([source], Path(tmp) / "enc")


class TestDedupe(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.connection = db.connect(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def _add(self, path, name, size, duration, category="Cat", hash=None):
        self.connection.execute(
            "INSERT INTO files (path, mtime, size, name, category, duration_raw, codec, hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (path, 1.0, size, name, category, duration, "hevc", hash),
        )
        self.connection.commit()

    def test_normalize_name(self):
        from shrinkray.dedupe import normalize_name

        self.assertEqual(
            normalize_name("The.Mandalorian.S01E02.1080p.WEB-DL.DDP5.1"),
            normalize_name("The Mandalorian - S01E02"),
        )
        self.assertEqual(
            normalize_name("Movie.2020.1080p.BluRay.x265"),
            normalize_name("movie 2020"),
        )
        self.assertNotEqual(normalize_name("Movie A"), normalize_name("Movie B"))
        # noise-only names collapse to empty
        self.assertEqual(normalize_name("1080p x264"), "")

    def test_identical_by_hash(self):
        from shrinkray.dedupe import find_duplicates

        self._add("D:/a/movie.mkv", "movie", 1000, 100.0, hash="abc")
        self._add("D:/b/movie.mkv", "movie", 1000, 100.0, hash="abc")
        groups = find_duplicates(self.connection)
        self.assertEqual(len(groups), 1)
        # same name + same duration + same hash => both passes merge
        self.assertEqual(groups[0].reason, "identical + same title")
        self.assertEqual(groups[0].wasted_bytes, 1000)

    def test_keep_rationale_identical(self):
        """Byte-identical groups explain that quality cannot differ."""
        from shrinkray.dedupe import find_duplicates

        self._add("D:/a/movie.mkv", "movie", 1000, 100.0, hash="abc")
        self._add("D:/b/movie.mkv", "movie", 1000, 100.0, hash="abc")
        groups = find_duplicates(self.connection)
        self.assertIn("byte-identical", groups[0].keep_rationale)
        # shipped to the API payload
        self.assertIn("keep_rationale", groups[0].to_dict())

    def test_keep_rationale_bitrate(self):
        """Same title, different generations: the bitrate edge is the why."""
        from shrinkray.dedupe import find_duplicates

        # same duration, very different sizes => very different bitrates
        self._add("D:/a/movie.mkv", "movie", 9_000_000, 100.0)  # 0.72 MBit/s
        self._add("D:/b/movie.mkv", "movie", 1_000_000, 100.0)  # 0.08 MBit/s
        groups = find_duplicates(self.connection)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].files[0].path, "D:/a/movie.mkv")
        self.assertIn("bitrate", groups[0].keep_rationale)
        self.assertIn("0.72", groups[0].keep_rationale)

    def test_keep_rationale_resolution(self):
        """A resolution edge is explained as such."""
        from shrinkray.dedupe import find_duplicates

        self.connection.execute(
            "INSERT INTO files (path, mtime, size, name, category, duration_raw, codec,"
            " width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("D:/a/movie.mkv", 1.0, 4_000_000, "movie", "Cat", 100.0, "hevc", 1920, 1080),
        )
        self.connection.execute(
            "INSERT INTO files (path, mtime, size, name, category, duration_raw, codec,"
            " width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("D:/b/movie.mkv", 1.0, 3_000_000, "movie", "Cat", 100.0, "hevc", 1280, 720),
        )
        self.connection.commit()
        groups = find_duplicates(self.connection)
        self.assertEqual(len(groups), 1)
        # the 1080p file is bigger AND higher-res: resolution is the stated why
        self.assertIn("resolution", groups[0].keep_rationale)
        self.assertIn("1920x1080", groups[0].keep_rationale)

    def test_keep_rationale_honest_fallback(self):
        """No separating signal => the rationale says so (no invented reason)."""
        from shrinkray.dedupe import find_duplicates

        # same size + same duration, no hash, no resolution difference
        self._add("D:/a/movie.mkv", "movie", 1_000_000, 100.0)
        self._add("D:/b/movie.mkv", "movie", 1_000_000, 100.0)
        groups = find_duplicates(self.connection)
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            groups[0].keep_rationale, "no quality signal separates them; keeping the largest file"
        )

    def test_keep_rationale_does_not_change_the_pick(self):
        """The rationale documents the largest-file pick, never overrides it."""
        from shrinkray.dedupe import find_duplicates

        self._add("D:/a/movie.mkv", "movie", 5_000_000, 100.0)
        self._add("D:/b/movie.mkv", "movie", 1_000_000, 100.0)
        groups = find_duplicates(self.connection)
        self.assertEqual(groups[0].files[0].path, "D:/a/movie.mkv")  # largest first
        self.assertEqual(groups[0].wasted_bytes, 1_000_000)

    def test_duplicatefile_bitrate_property(self):
        from shrinkray.dedupe import DuplicateFile

        f = DuplicateFile(path="x", name="x", category="c", size=8_000_000, duration=10.0)
        self.assertAlmostEqual(f.bitrate_mbps, 6.4)
        self.assertIsNone(DuplicateFile(path="x", name="x", category="c", size=8).bitrate_mbps)

    def test_missing_rows_are_excluded(self):
        """A trash-ed (retired) duplicate leaves its group at once — the
        API endpoint excludes rows flagged missing (the delete endpoint
        retires them immediately)."""
        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        self._add("D:/a/movie.mkv", "movie", 1000, 100.0, hash="abc")
        self._add("D:/b/movie.mkv", "movie", 1000, 100.0, hash="abc")

        app_module.state = AppState(Path(self.tmp.name) / "test.db")
        # point the app state at this test's connection (same db file)
        app_module.state.connection.close()
        app_module.state.connection = self.connection
        app_module.state.queue = __import__("shrinkray.jobs", fromlist=["JobQueue"]).JobQueue(
            self.connection
        )
        try:
            client = TestClient(fast_app)
            groups = client.get("/api/duplicates").json()
            self.assertEqual(len(groups), 1)
            # retire one member the way the delete endpoint does
            self.connection.execute(
                "UPDATE files SET missing_scans = 1, missing_since = datetime('now')"
                " WHERE path = 'D:/b/movie.mkv'"
            )
            self.connection.commit()
            self.assertEqual(client.get("/api/duplicates").json(), [])
        finally:
            app_module.state.worker.stop()
            app_module.state = None

    def test_same_title_duration_match(self):
        from shrinkray.dedupe import find_duplicates

        self._add("D:/a/The Witcher S01E07.mkv", "The Witcher S01E07", 9800, 3600.0)
        self._add("D:/b/The.Witcher.S01E07.1080p.mkv", "The.Witcher.S01E07.1080p", 2100, 3601.0)
        groups = find_duplicates(self.connection)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].reason, "same title")
        # best candidate (largest) first
        self.assertEqual(groups[0].files[0].size, 9800)

    def test_same_name_different_episode_not_grouped(self):
        from shrinkray.dedupe import find_duplicates

        # same normalized name key but durations far apart => different content
        self._add("D:/a/Show S01E01.mkv", "Show S01E01", 5000, 2400.0)
        self._add("D:/b/Show.S01E01.720p.mkv", "Show.S01E01.720p", 3000, 2500.0)
        groups = find_duplicates(self.connection)
        self.assertEqual(len(groups), 0)

    def test_mixed_groups_merged(self):
        from shrinkray.dedupe import find_duplicates

        # two files identical by hash AND same title => one merged group
        self._add("D:/a/x.mkv", "Some Movie", 4000, 500.0, hash="h1")
        self._add("D:/b/x.mkv", "Some.Movie.1080p", 4000, 500.0, hash="h1")
        groups = find_duplicates(self.connection)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].reason, "identical + same title")
        self.assertEqual(len(groups[0].files), 2)

    def test_no_duplicates(self):
        from shrinkray.dedupe import find_duplicates

        self._add("D:/a/one.mkv", "One", 1000, 100.0, hash="h1")
        self._add("D:/a/two.mkv", "Two", 2000, 200.0, hash="h2")
        self.assertEqual(find_duplicates(self.connection), [])

    def test_api_duplicates_endpoint(self):
        from fastapi.testclient import TestClient

        import shrinkray.app as app_module
        from shrinkray.app import AppState
        from shrinkray.app import app as fast_app

        self._add("D:/a/dup.mkv", "Dup Movie", 1000, 100.0, hash="zz")
        self._add("D:/b/dup.mkv", "Dup.Movie", 1500, 100.0, hash="zz")

        app_module.state = AppState(Path(self.tmp.name) / "test.db")
        # reuse the same connection's data: point the app state at this db
        app_module.state.connection.close()
        app_module.state.connection = self.connection
        app_module.state.queue = __import__("shrinkray.jobs", fromlist=["JobQueue"]).JobQueue(
            self.connection
        )
        try:
            client = TestClient(fast_app)
            response = client.get("/api/duplicates")
            self.assertEqual(response.status_code, 200)
            groups = response.json()
            self.assertEqual(len(groups), 1)
            self.assertIn("identical", groups[0]["reason"])
            self.assertEqual(len(groups[0]["files"]), 2)
            self.assertEqual(groups[0]["wasted_bytes"], 1000)
        finally:
            app_module.state.worker.stop()
            app_module.state = None

    def test_similar_title_fuzzy(self):
        from shrinkray.dedupe import find_duplicates

        # a typo variant the exact pass misses
        self._add("D:/a/Django Unchained.mkv", "Django Unchained", 4000, 7200.0)
        self._add("D:/b/Django Unchainedd.mkv", "Django Unchainedd", 2100, 7200.0)
        groups = find_duplicates(self.connection)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].reason, "similar title")

    def test_fuzzy_requires_duration_match(self):
        from shrinkray.dedupe import find_duplicates

        # similar names, far-apart durations => different content
        self._add("D:/a/Show Episode.mkv", "Show Episode", 4000, 2400.0)
        self._add("D:/b/Show Episodex.mkv", "Show Episodex", 2100, 3900.0)
        self.assertEqual(find_duplicates(self.connection), [])

    def test_different_episodes_not_fuzzied(self):
        from shrinkray.dedupe import find_duplicates

        # distinct episodes of one show share most tokens but differ in duration
        self._add("D:/a/Show S01E01.mkv", "Show S01E01", 4000, 2400.0)
        self._add("D:/b/Show S01E02.mkv", "Show S01E02", 2100, 2500.0)
        self.assertEqual(find_duplicates(self.connection), [])


if __name__ == "__main__":
    unittest.main()
