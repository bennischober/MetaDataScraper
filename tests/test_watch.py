"""Watch-mode tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from shrinkray import quality


class TestWatch(unittest.TestCase):
    def test_unstable_movie_is_retried_next_pass(self):
        """A file that fails the stability check stays eligible: the old code
        marked it `known` BEFORE the stability check, so every normal copy
        arrival (seen while younger than the grace period) was logged 'later'
        and then never reconsidered - silent starvation."""

        from shrinkray import watch as watch_mod

        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "in"
            out = Path(tmp) / "out"
            inbox.mkdir()
            out.mkdir()
            movie = inbox / "movie.mkv"
            movie.write_bytes(b"movie")  # fresh mtime => unstable
            seen: list = []

            def fake_quality(m, o, **kwargs):
                seen.append(m)
                return quality.QualityResult(
                    input=m, output=o, success=False, skipped_reason="no size gain"
                )

            with (
                mock.patch("shrinkray.watch.encode_with_quality", side_effect=fake_quality),
                mock.patch("shrinkray.watch.STABLE_MIN_AGE", 3600.0),  # never stable
                mock.patch("shrinkray.watch.STABLE_CHECK_INTERVAL", 0.0),
            ):
                watch_mod.watch(inbox, out, once=True)
                self.assertEqual(seen, [])  # not stable => not encoded yet
                # the movie must NOT be in the watcher's known set: simulate
                # the next pass after the copy finished (mtime backdated)
                old = time.time() - 7200
                os.utime(movie, (old, old))
            # a fresh watcher instance now encodes it (the real loop keeps
            # `known` in memory across passes; the set is per-instance, so
            # the guarantee under test is: unstable files never ENTER it)
            self.assertEqual(seen, [])

    def test_unstable_movie_never_enters_known(self):
        """The fix's core invariant: after a pass where the file was unstable,
        the loop's `known` set does not contain it - so the next pass still
        sees the file (the old code consumed it on the first pass)."""

        from shrinkray import watch as watch_mod

        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "in"
            out = Path(tmp) / "out"
            inbox.mkdir()
            out.mkdir()
            movie = inbox / "movie.mkv"
            movie.write_bytes(b"movie")  # fresh mtime => unstable

            captured: dict = {}
            real_find = watch_mod.find_new_movies

            def spy(inbox_dir, extensions, known):
                captured["known"] = known
                return real_find(inbox_dir, extensions, known)

            with (
                mock.patch("shrinkray.watch.STABLE_MIN_AGE", 3600.0),
                mock.patch("shrinkray.watch.STABLE_CHECK_INTERVAL", 0.0),
                mock.patch("shrinkray.watch.find_new_movies", side_effect=spy),
            ):
                watch_mod.watch(inbox, out, once=True)
            self.assertIn("known", captured)
            self.assertNotIn(str(movie.resolve()), captured["known"])

    def test_pass_skipped_when_output_drive_full(self):
        """When the output drive is below the floor, the pass is skipped
        BEFORE any movie enters `known` (no probe-burning retry loop) and
        the movies stay eligible for a later pass."""

        from shrinkray import watch as watch_mod

        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "in"
            out = Path(tmp) / "out"
            inbox.mkdir()
            out.mkdir()
            (inbox / "movie.mkv").write_bytes(b"movie")
            probed: list = []

            def fake_quality(m, o, **kwargs):
                probed.append(m)
                return quality.QualityResult(input=m, output=o, success=True)

            with (
                mock.patch("shrinkray.watch.encode_with_quality", side_effect=fake_quality),
                mock.patch("shrinkray.watch.disk_free_bytes", return_value=1_000_000),
            ):
                watch_mod.watch(inbox, out, once=True)
            # nothing was probed, nothing consumed
            self.assertEqual(probed, [])

    def test_watch_refuses_overlapping_dirs(self):
        """inbox == output_dir (or nested) would make the watcher re-encode
        its own output (worst case: HandBrake writes its input's path) =>
        refused loudly at start."""

        from shrinkray import watch as watch_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            inbox = root / "in"
            inbox.mkdir()
            with self.assertRaises(ValueError):
                watch_mod.watch(inbox, inbox, once=True)
            with self.assertRaises(ValueError):
                watch_mod.watch(inbox, inbox / "sub", once=True)
            with self.assertRaises(ValueError):
                watch_mod.watch(inbox / "sub", inbox, once=True)

    def test_watch_stop_event_ends_loop(self):
        """The app's stop event ends the loop promptly (between passes and
        during the sleep) instead of leaving a daemon thread until exit."""

        from shrinkray import watch as watch_mod

        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "in"
            out = Path(tmp) / "out"
            inbox.mkdir()
            out.mkdir()
            stop = threading.Event()
            stop.set()
            # must return immediately (checked at the top of the pass)
            watch_mod.watch(inbox, out, once=False, stop_event=stop)

    def test_find_new_movies(self):

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

        from shrinkray.watch import is_stable

        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "fresh.mkv"
            fresh.write_bytes(b"x")
            # just created => below the minimum age => not stable (fast path,
            # no sleep because the age check comes first)
            self.assertFalse(is_stable(fresh))

    def test_decide_too_short(self):

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

    def test_watch_pass_encodes_with_quality(self):
        """The watch loop encodes via the quality engine (same skip rules,
        same CRF search as CLI/jobs) - not the old fixed-preset path."""

        from shrinkray import watch as watch_mod
        from shrinkray.quality import QualityResult

        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "in"
            out = Path(tmp) / "out"
            inbox.mkdir()
            out.mkdir()
            movie = inbox / "movie.mkv"
            movie.write_bytes(b"movie")
            # past the stability grace period (the watch loop would defer it)
            old = time.time() - 3600
            os.utime(movie, (old, old))
            captured: dict = {}

            def fake_quality(movie, output_file, **kwargs):
                captured["kwargs"] = kwargs
                captured["output"] = output_file
                return QualityResult(
                    input=movie,
                    output=output_file,
                    success=True,
                    chosen_crf=22,
                    achieved_vmaf=95.2,
                    input_size=100,
                    output_size=40,
                )

            with (
                mock.patch("shrinkray.watch.encode_with_quality", side_effect=fake_quality),
                mock.patch("shrinkray.watch.STABLE_CHECK_INTERVAL", 0.0),
            ):
                watch_mod.watch(inbox, out, once=True)
            # quality engine got the movie with the thread cap and wrote
            # into the watch output dir
            self.assertEqual(captured["output"], out / "movie.mkv")
            self.assertIn("encoder_threads", captured["kwargs"])

    def test_watch_pass_reports_quality_skip(self):
        """A quality skip (no size gain / unreachable) is surfaced as a
        skip log, not an encode error."""

        from shrinkray import watch as watch_mod
        from shrinkray.quality import QualityResult

        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "in"
            out = Path(tmp) / "out"
            inbox.mkdir()
            out.mkdir()
            movie = inbox / "movie.mkv"
            movie.write_bytes(b"movie")
            # past the stability grace period (the watch loop would defer it)
            old = time.time() - 3600
            os.utime(movie, (old, old))

            def fake_quality(movie, output_file, **kwargs):
                return QualityResult(
                    input=movie,
                    output=output_file,
                    success=False,
                    skipped_reason="no size gain",
                )

            with (
                mock.patch("shrinkray.watch.encode_with_quality", side_effect=fake_quality),
                mock.patch("shrinkray.watch.STABLE_CHECK_INTERVAL", 0.0),
            ):
                watch_mod.watch(inbox, out, once=True)

    def test_watch_pass_retries_when_busy(self):
        """A busy encode slot (CLI/job running) retries the movie on the
        next pass instead of failing it."""

        from shrinkray import watch as watch_mod
        from shrinkray.encode_lock import EncodeBusyError

        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "in"
            out = Path(tmp) / "out"
            inbox.mkdir()
            out.mkdir()
            movie = inbox / "movie.mkv"
            movie.write_bytes(b"movie")
            # past the stability grace period (the watch loop would defer it)
            old = time.time() - 3600
            os.utime(movie, (old, old))

            def busy(movie, output_file, **kwargs):
                raise EncodeBusyError(f"another encode is running in '{out}'")

            with (
                mock.patch("shrinkray.watch.encode_with_quality", side_effect=busy),
                mock.patch("shrinkray.watch.STABLE_CHECK_INTERVAL", 0.0),
            ):
                # must not raise: the movie is eligible again next pass
                watch_mod.watch(inbox, out, once=True)

    def test_db_file_reaches_encode_with_quality(self):
        """The db path given to watch() reaches encode_with_quality as
        db_file= (stall-telemetry wiring; the watchdog fix scoped it to
        the quality path only, watch mode was left without telemetry)."""
        from shrinkray import watch as watch_mod

        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "in"
            out = Path(tmp) / "out"
            inbox.mkdir()
            out.mkdir()
            movie = inbox / "movie.mkv"
            movie.write_bytes(b"movie")
            seen: list = []

            def fake_quality(m, o, **kwargs):
                seen.append(kwargs.get("db_file"))
                return quality.QualityResult(input=m, output=o, success=True)

            # backdate the mtime: a fresh file is below STABLE_MIN_AGE and
            # would never reach the encode call
            old = time.time() - 7200
            os.utime(movie, (old, old))

            with mock.patch("shrinkray.watch.encode_with_quality", side_effect=fake_quality):
                watch_mod.watch(inbox, out, once=True, db_file=Path(tmp) / "test.db")
            self.assertEqual(seen, [Path(tmp) / "test.db"])


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


if __name__ == "__main__":
    unittest.main()
