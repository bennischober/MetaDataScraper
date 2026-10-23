"""CLI command tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import tempfile
import unittest
from pathlib import Path

from shrinkray import config as config_helper
from shrinkray import db


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


class TestCompareCli(unittest.TestCase):
    """The compare command: provenance chain table (live-smoked only until
    now - these tests cover the db queries and the CLI exit codes)."""

    def setUp(self):

        self.tmp = tempfile.TemporaryDirectory()
        self.db_file = Path(self.tmp.name) / "test.db"
        self.connection = db.connect(self.db_file)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def _chain(self, movie: Path):
        """Stores a two-generation encode chain for the movie (with codec
        snapshot data, as record_quality_results would write it)."""
        file_id = db.upsert_file(
            self.connection,
            db.MovieRecord(path=movie, mtime=100.0, size=1000, name="movie", category="Cat"),
        )
        gen1 = db.add_encode(
            self.connection,
            file_id,
            "VMAF>95",
            "x265",
            1000,
            400,
            60.0,
            chosen_crf=22,
            achieved_vmaf=95.4,
            source_codec="h264",
            source_file=str(movie),
            output_file=str(movie.parent / "enc" / movie.name),
            generation=1,
        )
        db.add_encode(
            self.connection,
            file_id,
            "VMAF>95",
            "x265",
            400,
            200,
            50.0,
            chosen_crf=24,
            achieved_vmaf=95.1,
            source_codec="hevc",
            source_file=str(movie.parent / "enc" / movie.name),
            output_file=str(movie.parent / "enc2" / movie.name),
            generation=2,
        )
        return file_id, gen1

    def test_compare_shows_chain(self):
        """A movie with a two-generation chain renders both rows."""

        from typer.testing import CliRunner

        from shrinkray.__main__ import app

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / "movie.mkv"
            movie.write_bytes(b"x")
            self._chain(movie)
            runner = CliRunner()
            result = runner.invoke(app, ["compare", str(movie), "--db", str(self.db_file)])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("movie.mkv", result.output)

            # both generations appear with their CRFs
            self.assertIn("22", result.output)
            self.assertIn("24", result.output)

    def test_compare_no_encodes_exit_zero_with_hint(self):
        """A movie without encodes prints the hint and exits 0 (not an
        error - it is an expected state for a freshly scanned library)."""

        from typer.testing import CliRunner

        from shrinkray.__main__ import app

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / "plain.mkv"
            movie.write_bytes(b"x")
            runner = CliRunner()
            result = runner.invoke(app, ["compare", str(movie), "--db", str(self.db_file)])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("No encodes recorded", result.output)

    def test_compare_missing_db_exits_error(self):
        """A missing database with an existing movie file: the command's
        own check fires with exit 1."""

        from typer.testing import CliRunner

        from shrinkray.__main__ import app

        with tempfile.TemporaryDirectory() as tmp:
            movie = Path(tmp) / "x.mkv"
            movie.write_bytes(b"x")
            runner = CliRunner()
            result = runner.invoke(app, ["compare", str(movie), "--db", str(Path(tmp) / "nope.db")])
            # exit 1 is the contract (scripts can detect it); the message
            # goes through the logger, which the CliRunner does not capture
            self.assertEqual(result.exit_code, 1)


class TestDoctorCli(unittest.TestCase):
    """The doctor command's CLI surface (service logic tested in TestDb)."""

    def setUp(self):

        self.tmp = tempfile.TemporaryDirectory()
        self.db_file = Path(self.tmp.name) / "test.db"
        self.connection = db.connect(self.db_file)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def test_doctor_healthy_exit_zero(self):
        """A healthy library prints the all-clear and exits 0."""

        from typer.testing import CliRunner

        from shrinkray.__main__ import app

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / "movie.mkv"
            movie.write_bytes(b"x")
            db.upsert_file(
                self.connection,
                db.MovieRecord(path=movie, mtime=100.0, size=1, name="movie", category="C"),
            )
            runner = CliRunner()
            result = runner.invoke(app, ["doctor", "--db", str(self.db_file)])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("healthy", result.output)

    def test_doctor_finding_exits_one(self):
        """A dead path produces the findings table and exit 1 (scripts/
        cron can detect unhealth)."""

        from typer.testing import CliRunner

        from shrinkray.__main__ import app

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            db.upsert_file(
                self.connection,
                db.MovieRecord(
                    path=root / "gone.mkv", mtime=100.0, size=1, name="gone", category="C"
                ),
            )
            runner = CliRunner()
            result = runner.invoke(app, ["doctor", "--db", str(self.db_file)])
            self.assertEqual(result.exit_code, 1, result.output)
            self.assertIn("dead path", result.output)
            self.assertIn("gone.mkv", result.output)


if __name__ == "__main__":
    unittest.main()
