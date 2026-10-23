"""Duplicate detection tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import tempfile
import unittest
from pathlib import Path

from shrinkray import db


class TestDedupe(unittest.TestCase):
    def setUp(self):

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
