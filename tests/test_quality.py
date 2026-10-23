"""Quality/CRF-search tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shrinkray import db, encode, quality, service


class TestQuality(unittest.TestCase):
    def test_telemetry_saves_failure_bundle(self):
        """A failure bundle keeps stderr, command, context and artifacts."""

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
        # cropdetect reports the PICTURE rectangle, so the crop may only remove
        # bars present in ALL samples => keep the union of the picture areas
        # (here y in [90, 990): only the 90 px bar common to both is removed)
        smaller = [(1920, 800, 0, 140), (1920, 900, 0, 90)]
        self.assertEqual(quality.intersect_crop_rects(smaller, frame), (90, 90, 0, 0))
        # a sample with no bars at all vetoes the crop entirely
        sides = [(1920, 1080, 0, 0), (1800, 1080, 60, 0)]
        self.assertIsNone(quality.intersect_crop_rects(sides, frame))
        # noise-sized bars (below 2% of the dimension) => None
        tiny = [(1920, 1060, 0, 10), (1920, 1060, 0, 10)]
        self.assertIsNone(quality.intersect_crop_rects(tiny, frame))
        # empty input
        self.assertIsNone(quality.intersect_crop_rects([], frame))

    def test_intersect_crop_rects_bar_free_sample_vetoes(self):
        """A sample with no bars must prevent cropping, not be overruled."""
        frame = (1920, 1080)
        # one sample is the full frame => no bar is present in every sample
        self.assertIsNone(
            quality.intersect_crop_rects([(1920, 800, 0, 140), (1920, 1080, 0, 0)], frame)
        )
        # a sample with smaller bars still restricts the crop: the 140 px bar is
        # only a bar in one sample, so only the common 90 px may be removed
        self.assertEqual(
            quality.intersect_crop_rects([(1920, 800, 0, 140), (1920, 900, 0, 90)], frame),
            (90, 90, 0, 0),
        )

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


class TestPlanReuse(unittest.TestCase):
    """The encode-path "quicksave": a valid plan replaces probing; any
    fingerprint mismatch fails safe to the normal probe pipeline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_file = Path(self.tmp.name) / "test.db"
        self.connection = db.connect(self.db_file)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def _movie(self, name: str = "movie.mkv") -> Path:
        movie = Path(self.tmp.name) / name
        movie.write_bytes(b"x" * 8)
        service.ensure_file_row(self.connection, movie)
        self.connection.commit()
        return movie

    def _store_plan(self, movie: Path, **overrides) -> None:
        """Stores a plan for the movie via the service write path."""
        from shrinkray.quality import SampleProbe

        result = quality.QualityResult(
            input=movie,
            output=movie,
            success=overrides.pop("success", True),
            target_vmaf=overrides.pop("target_vmaf", 95.0),
            chosen_crf=overrides.pop("chosen_crf", 21),
            achieved_vmaf=overrides.pop("achieved_vmaf", 95.47),
            probes=overrides.pop("probes", [SampleProbe(crf=21, vmaf=95.47)]),
            input_size=movie.stat().st_size,
            output_size=movie.stat().st_size // 2,
            skipped_reason=overrides.pop("skipped_reason", None),
            encoder_preset="medium",
            remove_bars=False,
            crop=None,
            crf_range=overrides.pop("crf_range", (14, 32)),
            sample_count=3,
            sample_seconds=10.0,
            hdr=overrides.pop("hdr", False),
            encoder=overrides.pop("encoder", "x265"),
        )
        config = type("Conf", (), {"handbrake_encoder": "x265"})()
        record_plan = overrides.pop("record_plan", service.record_plan)
        record_plan(self.connection, movie, result, config)

    def _encode(self, movie: Path, **kwargs):
        """Runs encode_with_quality with every external seam mocked."""
        from shrinkray.encode import EncodeResult

        output = Path(self.tmp.name) / "out.mkv"

        def fake_encode_command(input_file, output_file, command, **kwargs):
            output_file.write_bytes(b"y" * (input_file.stat().st_size // 2))
            return EncodeResult(
                input=input_file,
                output=output_file,
                success=True,
                input_size=input_file.stat().st_size,
                output_size=output_file.stat().st_size,
            )

        with (
            patch.object(quality, "probe_duration_seconds", return_value=600.0),
            patch("shrinkray.encode.check_disk_space", return_value=(True, "")),
            patch.object(
                quality,
                "_source_snapshot",
                return_value={
                    "width": None,
                    "height": None,
                    "codec": None,
                    "duration": None,
                    "bitrate": None,
                },
            ),
            patch.object(quality, "find_handbrake", return_value="hb"),
            patch.object(
                quality, "_encode_with_command", side_effect=fake_encode_command
            ) as fake_encode,
        ):
            result = quality.encode_with_quality(movie, output, db_file=self.db_file, **kwargs)
        return result, fake_encode

    def test_valid_plan_skips_probing_and_encodes_planned_crf(self):
        movie = self._movie()
        self._store_plan(movie)
        with (
            patch.object(quality, "_probe_movie") as fake_probe,
            patch.object(quality, "_tool_versions", return_value="hb 1.0"),
        ):
            result, fake_encode = self._encode(movie)
        fake_probe.assert_not_called()  # zero probe encodes
        fake_encode.assert_called_once()
        self.assertTrue(result.success)
        self.assertEqual(result.chosen_crf, 21)
        self.assertEqual(result.achieved_vmaf, 95.47)
        self.assertEqual(result.crf_source, "plan")
        self.assertIsNotNone(result.plan_created_at)
        # the final command encodes at the plan's CRF
        command = fake_encode.call_args.args[2]
        self.assertEqual(command[command.index("--quality") + 1], "21")
        # the caller (job worker / CLI) persists the result; the encodes row
        # carries the provenance
        config = type("Conf", (), {"handbrake_encoder": "x265"})()
        service.record_quality_results(self.connection, movie, result, config)
        row = self.connection.execute(
            "SELECT crf_source, plan_created_at FROM encodes ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["crf_source"], "plan")
        self.assertEqual(row["plan_created_at"], result.plan_created_at)

    def test_fingerprint_mismatch_runs_probe_path(self):
        movie = self._movie()
        self._store_plan(movie, chosen_crf=21)
        with (
            patch.object(quality, "_probe_movie", return_value=(20, 95.1, [], None)) as fake_probe,
            patch.object(quality, "_tool_versions", return_value="hb 1.0"),
        ):
            result, _ = self._encode(movie, encoder_preset="slow")
        fake_probe.assert_called_once()
        self.assertEqual(result.chosen_crf, 20)
        self.assertEqual(result.crf_source, "probe")

    def test_changed_file_invalidates_plan(self):
        movie = self._movie()
        self._store_plan(movie)
        movie.write_bytes(b"x" * 64)  # size change => fingerprint mismatch
        with (
            patch.object(quality, "_probe_movie", return_value=(20, 95.1, [], None)) as fake_probe,
            patch.object(quality, "_tool_versions", return_value="hb 1.0"),
        ):
            result, _ = self._encode(movie)
        fake_probe.assert_called_once()
        self.assertEqual(result.crf_source, "probe")

    def test_plan_skip_decision_short_circuits_encode(self):
        movie = self._movie()
        self._store_plan(movie, success=False, skipped_reason="no size gain")
        with (
            patch.object(quality, "_probe_movie") as fake_probe,
            patch.object(quality, "_encode_with_command") as fake_encode,
        ):
            result, _ = self._encode(movie)
        fake_probe.assert_not_called()
        fake_encode.assert_not_called()  # nothing encoded at all
        self.assertFalse(result.success)
        self.assertEqual(result.skipped_reason, "no size gain")
        self.assertEqual(result.crf_source, "plan")

    def test_force_reprobe_bypasses_valid_plan(self):
        movie = self._movie()
        self._store_plan(movie)
        with (
            patch.object(quality, "_probe_movie", return_value=(20, 95.1, [], None)) as fake_probe,
            patch.object(quality, "_tool_versions", return_value="hb 1.0"),
        ):
            result, _ = self._encode(movie, force_reprobe=True)
        fake_probe.assert_called_once()
        self.assertEqual(result.chosen_crf, 20)
        self.assertEqual(result.crf_source, "probe")

    def test_no_plan_row_runs_probe_path(self):
        movie = self._movie()
        with (
            patch.object(quality, "_probe_movie", return_value=(20, 95.1, [], None)) as fake_probe,
            patch.object(quality, "_tool_versions", return_value="hb 1.0"),
        ):
            result, _ = self._encode(movie)
        fake_probe.assert_called_once()
        self.assertEqual(result.chosen_crf, 20)
        self.assertEqual(result.crf_source, "probe")

    def test_hdr_plan_reuses_after_engine_switches_to_10bit(self):
        """An HDR plan is stored under the EFFECTIVE encoder (x265_10bit)
        and a following encode of the same HDR file reuses it — regression:
        plan results never carried hdr/encoder, so HDR plans were stored as
        'x265' and the encode-side lookup (which matches on the overridden
        encoder) never found them; every HDR encode re-probed from scratch.
        See issues #26 and #24."""
        movie = self._movie()
        # plan_quality's write path: HDR override applied on the result
        # (effective encoder x265_10bit, HDR target/range), stored via
        # record_plan
        self._store_plan(
            movie,
            target_vmaf=quality.DEFAULT_HDR_TARGET_VMAF,
            chosen_crf=18,
            achieved_vmaf=94.8,
            crf_range=quality.DEFAULT_HDR_CRF_RANGE,
            encoder="x265_10bit",
            hdr=True,
        )
        row = self.connection.execute("SELECT encoder FROM plans").fetchone()
        self.assertEqual(row["encoder"], "x265_10bit")
        # the encode side: same HDR file => the engine switches to 10-bit
        # and MUST find the plan under it
        with (
            patch.object(quality, "is_hdr_file", return_value=True),
            patch.object(quality, "_probe_movie") as fake_probe,
            patch.object(quality, "_tool_versions", return_value="hb 1.0"),
        ):
            result, _ = self._encode(movie)
        fake_probe.assert_not_called()  # the plan replaces probing
        self.assertEqual(result.crf_source, "plan")
        self.assertEqual(result.chosen_crf, 18)
        self.assertEqual(result.encoder, "x265_10bit")


class TestProbeCache(unittest.TestCase):
    """The probe-encode cache (issue #24): per-sample measurements are
    content-addressed, so a re-run with the same inputs reuses them and a
    changed target VMAF reuses the probes it already measured."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name) / "work"
        self.workdir.mkdir()
        self.db_file = Path(self.tmp.name) / "test.db"
        self.connection = db.connect(self.db_file)
        self.movie = Path(self.tmp.name) / "movie.mkv"
        self.movie.write_bytes(b"x" * 8)
        self.sample = Path(self.tmp.name) / "sample.mkv"
        self.sample.write_bytes(b"y" * 8)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def _probe(self, **kwargs):
        """One probe_crf call with the encode/VMAF seams mocked."""
        encodes: list = []

        def fake_run(command, cancel=None):
            # the probe encode writes its output file (the --output argument)
            encodes.append(command)
            Path(command[command.index("--output") + 1]).write_bytes(b"z" * 4)
            return 0, ""

        defaults = {
            "samples": [(self.sample, 10.0)],
            "crf": 20,
            "workdir": self.workdir,
            "handbrake": "hb",
            "movie": self.movie,
            "sample_seconds": 10.0,
            "encoder": "x265",
            "encoder_preset": "medium",
            "db_file": self.db_file,
        }
        with (
            patch.object(quality, "_tool_versions", return_value="hb 1.0;libvmaf=v3"),
            patch.object(quality, "_run", side_effect=fake_run),
            patch.object(quality, "measure_vmaf", return_value=95.5),
        ):
            score = quality.probe_crf(**{**defaults, **kwargs})
        return score, encodes

    def test_cache_hit_skips_encode_and_measurement(self):
        score, encodes = self._probe()
        self.assertEqual(score, 95.5)
        self.assertEqual(len(encodes), 1)
        # identical inputs again: no encode, no VMAF measurement, same score
        score2, encodes2 = self._probe()
        self.assertEqual(score2, 95.5)
        self.assertEqual(len(encodes2), 0)

    def test_cache_hit_yields_the_uncached_result(self):
        """A cached run must produce the same per-CRF score as an uncached
        one (the acceptance criterion: same chosen CRF)."""
        with tempfile.TemporaryDirectory() as tmp2:
            uncached_db = Path(tmp2) / "other.db"
            db.connect(uncached_db).close()
            score_cached, _ = self._probe()
            score_uncached, encodes = self._probe(db_file=uncached_db)
            self.assertEqual(score_cached, score_uncached)
            self.assertEqual(len(encodes), 1)  # the uncached run encoded once

    def test_changed_target_reuses_measured_probes(self):
        """Changing ONLY the target VMAF keeps every probe-cache component
        identical, so the probes already measured are reused (they are the
        expensive part; the target is a decision threshold, not an input)."""
        score, encodes_first = self._probe(crf=20)
        self.assertEqual(len(encodes_first), 1)
        # a second CRF round at a different CRF: encodes (new key), then a
        # re-run of the SAME crf hits while the other stays cached
        _score2, encodes_new_crf = self._probe(crf=24)
        self.assertEqual(len(encodes_new_crf), 1)
        score3, encodes_again = self._probe(crf=20)
        self.assertEqual(len(encodes_again), 0)
        self.assertEqual(score3, score)

    def test_cache_invalidated_by_source_change(self):
        self._probe()
        # the source changed (mtime+size): the cached score is stale
        self.movie.write_bytes(b"x" * 64)

        stat = self.movie.stat()
        os.utime(self.movie, (stat.st_mtime + 5, stat.st_mtime + 5))
        _score, encodes = self._probe()
        self.assertEqual(len(encodes), 1)  # re-encoded, not reused

    def test_cache_key_changes_with_encoder_and_preset(self):
        self._probe()
        _score, encodes = self._probe(encoder="x265_10bit")
        self.assertEqual(len(encodes), 1)  # different encoder => new key
        _score2, encodes2 = self._probe(encoder_preset="slow")
        self.assertEqual(len(encodes2), 1)  # different preset => new key

    def test_force_reprobe_bypasses_cache(self):
        self._probe()
        _score, encodes = self._probe(use_probe_cache=False)
        self.assertEqual(len(encodes), 1)  # measured fresh despite the cache

    def test_no_db_file_probes_uncached(self):
        """Without a database (CLI one-shot), probing still works and
        re-encodes (nothing to cache into)."""
        score, encodes = self._probe(db_file=None)
        self.assertEqual(score, 95.5)
        self.assertEqual(len(encodes), 1)
        score2, encodes2 = self._probe(db_file=None)
        self.assertEqual(score2, 95.5)
        self.assertEqual(len(encodes2), 1)

    def test_size_factor_is_cached(self):
        factor, _ = self._size_factor()
        self.assertEqual(factor, 0.5)
        # second call: no encode
        factor2, encodes2 = self._size_factor()
        self.assertEqual(factor2, 0.5)
        self.assertEqual(len(encodes2), 0)

    def _size_factor(self, **kwargs):
        encodes: list = []

        def fake_run(command, cancel=None):
            encodes.append(command)
            Path(command[command.index("--output") + 1]).write_bytes(b"z" * 4)
            return 0, ""

        defaults = {
            "samples": [(self.sample, 10.0)],
            "workdir": self.workdir,
            "handbrake": "hb",
            "crf": 32,
            "movie": self.movie,
            "sample_seconds": 10.0,
            "encoder": "x265",
            "encoder_preset": "medium",
            "db_file": self.db_file,
        }
        with (
            patch.object(quality, "_tool_versions", return_value="hb 1.0;libvmaf=v3"),
            patch.object(quality, "_run", side_effect=fake_run),
        ):
            factor = quality.estimate_size_factor(**{**defaults, **kwargs})
        return factor, encodes

    def test_size_and_vmaf_caches_do_not_collide(self):
        """The same (sample, CRF) probed as size ratio and as VMAF score are
        different measurements: the kind is part of the key."""
        self._probe(crf=32)
        factor, encodes = self._size_factor()
        self.assertEqual(len(encodes), 1)  # not served from the vmaf entry
        self.assertEqual(factor, 0.5)


if __name__ == "__main__":
    unittest.main()
