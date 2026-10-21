import json
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path

from metadata_scrapper import config as config_helper
from metadata_scrapper import db, encode, helpers, quality
from metadata_scrapper import media as read_media


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
                self.assertEqual(version["version"], 3)
                paths = {r["path"] for r in migrated.execute("SELECT path FROM files")}
                # existing file => absolute; deleted file => dropped
                self.assertEqual(len(paths), 1)
                self.assertIn(str(existing), paths)
                migrated.close()
            finally:
                os.chdir(cwd)

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
        record1 = self._record("D:/Movies/a.mkv")
        record2 = self._record("D:/Movies/b.mkv")
        id1 = db.upsert_file(self.connection, record1)
        db.upsert_file(self.connection, record2)
        db.add_encode(self.connection, id1, "p", "x265", 1, 1, 0.0)

        # prune with the stored path strings (Path normalizes the separators)
        existing = {str(Path("D:/Movies/a.mkv"))}
        deleted = db.prune_missing(self.connection, existing)
        self.assertEqual(deleted, 1)
        self.assertIsNone(db.get_file_by_path(self.connection, Path("D:/Movies/b.mkv")))
        # encodes of surviving files are still there
        self.assertEqual(len(db.get_encodes_for_file(self.connection, id1)), 1)

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
        id_inside = db.upsert_file(self.connection, self._record("D:/Movies/a.mkv"))
        db.add_encode(self.connection, id_inside, "p", "x265", 1, 1, 0.0)
        id_outside = db.upsert_file(self.connection, self._record("E:/Other/b.mkv"))
        db.add_encode(self.connection, id_outside, "p", "x265", 2, 1, 50.0)

        # scan of D:/Movies finds only a.mkv, scope limits pruning to that root
        existing = {str(Path("D:/Movies/a.mkv"))}
        deleted = db.prune_missing(self.connection, existing, scope="D:/Movies")
        self.assertEqual(deleted, 0)  # b.mkv is outside the scope => kept
        self.assertIsNotNone(db.get_file_by_path(self.connection, Path("E:/Other/b.mkv")))
        self.assertEqual(len(db.get_encodes_for_file(self.connection, id_outside)), 1)

        # a.mkv is inside the scope and on disk => kept as well
        self.assertIsNotNone(db.get_file_by_path(self.connection, Path("D:/Movies/a.mkv")))

        # a file inside the scope that vanished is pruned
        id_gone = db.upsert_file(self.connection, self._record("D:/Movies/gone.mkv"))
        deleted = db.prune_missing(self.connection, existing, scope="D:/Movies")
        self.assertEqual(deleted, 1)
        self.assertIsNone(db.get_file_by_path(self.connection, Path("D:/Movies/gone.mkv")))
        _ = id_gone


class TestMain(unittest.TestCase):
    def test_get_category(self):
        from metadata_scrapper.__main__ import get_category

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
        from metadata_scrapper.__main__ import get_name

        self.assertEqual(get_name(Path("D:/Movies/Action/Movie.mkv")), "Movie")
        # multi-file folder => folder name
        self.assertEqual(get_name(Path("D:/Movies/Action/movie_t0.mkv")), "Action")

    def test_find_movies_exclude(self):
        import tempfile

        from metadata_scrapper.__main__ import find_movies

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

        from metadata_scrapper.__main__ import scan_movies

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


class TestReport(unittest.TestCase):
    def test_generate_report(self):
        import tempfile

        from metadata_scrapper.report import generate_report

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
            self.assertIn("MetaDataScraper Report", html)
            self.assertIn("a.mkv", html)
            self.assertIn("95.40", html)
            # the skipped-preset rendering
            self.assertIn("quality mode (VMAF&gt;95.0)", html)


class TestWatch(unittest.TestCase):
    def test_find_new_movies(self):
        import tempfile

        from metadata_scrapper.watch import find_new_movies

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

        from metadata_scrapper.watch import acquire_lock, release_lock

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

        from metadata_scrapper.watch import is_stable

        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "fresh.mkv"
            fresh.write_bytes(b"x")
            # just created => below the minimum age => not stable (fast path,
            # no sleep because the age check comes first)
            self.assertFalse(is_stable(fresh))

    def test_decide_too_short(self):
        import tempfile

        from metadata_scrapper.watch import decide

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
        from metadata_scrapper.watch import next_poll_interval

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
        from metadata_scrapper.watch import next_poll_interval

        # any new candidate (even skipped/unstable ones) resets to the base
        self.assertEqual(next_poll_interval(240.0, 60.0, True), 60.0)
        self.assertEqual(next_poll_interval(60.0, 60.0, True), 60.0)

    def test_non_default_base(self):
        from metadata_scrapper.watch import next_poll_interval

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


if __name__ == "__main__":
    unittest.main()
