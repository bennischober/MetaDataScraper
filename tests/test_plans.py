"""Plans table (schema v9): migration, upsert, fingerprint validation."""

import tempfile
import unittest
from pathlib import Path

from shrinkray import db


class TestPlans(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_file = Path(self.tmp.name) / "test.db"
        self.connection = db.connect(self.db_file)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def _file(self, name: str = "movie.mkv") -> tuple[int, Path]:
        movie = Path(self.tmp.name) / name
        movie.write_bytes(b"x" * 16)
        file_id = db.upsert_file(
            self.connection,
            db.MovieRecord(
                path=movie,
                mtime=movie.stat().st_mtime,
                size=movie.stat().st_size,
                name=name,
                category="Cat",
            ),
        )
        self.connection.commit()
        return file_id, movie

    def _save(self, file_id: int, **overrides) -> int:
        args: dict = {
            "file_id": file_id,
            "chosen_crf": 21,
            "achieved_vmaf": 95.47,
            "crop": None,
            "probes": [],
            "skipped_reason": None,
            "input_mtime": Path(self.tmp.name).joinpath("movie.mkv").stat().st_mtime,
            "input_size": 16,
            "encoder": "x265",
            "encoder_preset": "medium",
            "target_vmaf": 95.0,
            "crf_low": 14,
            "crf_high": 32,
            "sample_count": 7,
            "sample_seconds": 5.0,
            "remove_bars": False,
        }
        args.update(overrides)
        return db.save_plan(self.connection, **args)

    def _lookup(self, movie: Path, **overrides):
        args: dict = {
            "encoder": "x265",
            "encoder_preset": "medium",
            "target_vmaf": 95.0,
            "crf_range": (14, 32),
            "sample_count": 7,
            "sample_seconds": 5.0,
            "remove_bars": False,
        }
        args.update(overrides)
        return db.get_valid_plan(self.connection, movie, **args)

    def test_migrations_reach_v9_and_plans_table_exists(self):
        version = self.connection.execute("SELECT version FROM schema_version").fetchone()[0]
        self.assertEqual(version, db.SCHEMA_VERSION)
        tables = {
            r[0]
            for r in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertIn("plans", tables)
        # provenance columns exist on encodes
        cols = {r[1] for r in self.connection.execute("PRAGMA table_info(encodes)")}
        self.assertIn("crf_source", cols)
        self.assertIn("plan_created_at", cols)

    def test_save_plan_upserts_one_row_per_file(self):
        file_id, _movie = self._file()
        self._save(file_id)
        self._save(file_id, chosen_crf=20)  # upsert: replaces, no duplicate
        rows = self.connection.execute("SELECT * FROM plans").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chosen_crf"], 20)

    def test_valid_plan_roundtrip(self):
        file_id, movie = self._file()
        self._save(file_id)
        plan = self._lookup(movie)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["chosen_crf"], 21)
        self.assertEqual(plan["achieved_vmaf"], 95.47)

    def test_mismatched_fingerprint_is_invalid(self):
        file_id, movie = self._file()
        self._save(file_id)
        # any changed decision input invalidates
        self.assertIsNone(self._lookup(movie, encoder_preset="slow"))
        self.assertIsNone(self._lookup(movie, target_vmaf=93.0))
        self.assertIsNone(self._lookup(movie, crf_range=(16, 30)))
        self.assertIsNone(self._lookup(movie, sample_count=5))
        self.assertIsNone(self._lookup(movie, sample_seconds=4.0))
        self.assertIsNone(self._lookup(movie, remove_bars=True))
        # changed file (mtime+size) invalidates
        movie.write_bytes(b"x" * 32)
        self.assertIsNone(self._lookup(movie))
        # vanished file invalidates
        movie.unlink()
        self.assertIsNone(self._lookup(movie))

    def test_no_plan_row_is_none(self):
        _, movie = self._file()
        self.assertIsNone(self._lookup(movie))
