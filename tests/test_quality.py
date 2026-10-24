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
        scores = {c: 99.0 - (c - 14) * 0.5 for c in range(14, 32)}
        calls: list[int] = []

        def score(c: int) -> float:
            calls.append(c)
            return scores[c]

        chosen = quality.bisect_crf(score, 14, 32, target_vmaf=95.0)
        self.assertEqual(chosen, 22)  # 99 - (c-14)*0.5 >= 95 up to c=22
        # every CRF measured at most once
        self.assertEqual(len(calls), len(set(calls)))
        # even the lowest crf fails => fall back to the lowest
        self.assertEqual(quality.bisect_crf(lambda c: 80.0, 14, 32, target_vmaf=95.0), 14)
        # everything passes => the largest measured pass (hi is exclusive)
        self.assertEqual(quality.bisect_crf(lambda c: 99.0, 14, 32, target_vmaf=95.0), 31)

    def _interpolated_search(self, curve, lo: int, hi: int, target: float) -> tuple[int, int]:
        """Runs bisect_crf over a synthetic curve; returns (chosen, probes)."""
        calls: list[int] = []
        cache: dict[int, float] = {}

        def score(c: int) -> float:
            calls.append(c)
            if c not in cache:
                cache[c] = curve(c)
            return cache[c]

        chosen = quality.bisect_crf(score, lo, hi, target_vmaf=target)
        assert len(calls) == len(set(calls))  # each CRF measured once
        return chosen, len(calls)

    def _plain_bisect(self, curve, lo: int, hi: int, target: float) -> tuple[int, int]:
        calls: list[int] = []

        def check(c: int) -> bool:
            calls.append(c)
            return curve(c) >= target

        while hi - lo > 1:
            mid = (lo + hi) // 2
            if check(mid):
                lo = mid
            else:
                hi = mid
        assert len(calls) == len(set(calls))
        return lo, len(calls)

    def test_interpolation_matches_bisect_on_curve_family(self):
        """Issue #23's acceptance property: on stated monotonic curve
        families (linear, concave, convex, step), the interpolated search
        returns the same CRF as the plain bisect, and never needs more
        probes than that bisect's worst case."""
        lo, hi, target = 14, 32, 95.0
        curves = {
            # linear fall from VMAF 99 at CRF 14 to 80 at CRF 32
            "linear": lambda c: 99.0 - (99.0 - 80.0) * (c - lo) / (hi - lo - 1),
            # concave: steep drop first, then flattens
            "concave": lambda c: 99.0 - 19.0 * ((c - lo) / (hi - lo - 1)) ** 0.5,
            # convex: flat first, then steep — the regula-falsi trap
            "convex": lambda c: 99.0 - 19.0 * ((c - lo) / (hi - lo - 1)) ** 2.0,
            # step: perfect until a cliff, then worthless
            "step": lambda c: 97.0 if c <= 21 else 60.0,
        }
        for name, curve in curves.items():
            with self.subTest(curve=name):
                chosen_interp, probes_interp = self._interpolated_search(curve, lo, hi, target)
                chosen_plain, probes_plain = self._plain_bisect(curve, lo, hi, target)
                self.assertEqual(chosen_interp, chosen_plain, f"CRF mismatch on {name}")
                self.assertLessEqual(
                    probes_interp,
                    probes_plain + 1,
                    f"{name}: interpolation used more probes ({probes_interp}) "
                    f"than bisect ({probes_plain})",
                )

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


class TestSubprocessTimeouts(unittest.TestCase):
    """The short-lived ffprobe/ffmpeg calls turn a TimeoutExpired into their
    documented failure path instead of hanging the thread forever (issue
    #62: a stale NFS/SMB mount blocks the kernel read with no timeout)."""

    def test_detect_vmaf_style_timeout_raises_documented_error(self):
        import subprocess

        # the style is detected once and cached module-wide; other tests in
        # the suite may have populated the cache, so reset it around this
        # test (and restore it — the real value stays valid)
        saved = quality._vmaf_style
        quality._vmaf_style = None
        try:
            with (
                patch(
                    "shrinkray.quality.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60),
                ),
                self.assertRaises(RuntimeError) as ctx,
            ):
                quality.detect_vmaf_style()
            self.assertIn("60s", str(ctx.exception))
            # a failed detection must not be cached: the next call probes again
            self.assertIsNone(quality._vmaf_style)
        finally:
            quality._vmaf_style = saved

    def test_detect_crop_rect_timeout_returns_none(self):
        import subprocess

        movie = Path(tempfile.mkdtemp()) / "no-such-file.mkv"
        try:
            with patch(
                "shrinkray.quality.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=60),
            ) as mocked:
                self.assertIsNone(quality.detect_crop_rect(movie, None, count=3))
            # exactly the ffprobe frame-size call was made (not the
            # cropdetect _run); the duration probe inside uses -show_format
            self.assertIn("-show_format", mocked.call_args.args[0])
        finally:
            movie.parent.rmdir()

    def test_min_source_size_gate_skips_before_probing(self):
        """A source below the size floor is skipped before any probe runs.

        Probe cost is roughly constant per file while the payoff scales with
        size, so the smallest files have the worst cost/benefit ratio. The gate
        must decide from one stat and reach neither extraction nor HandBrake.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "movie.mkv"
            src.write_bytes(b"x" * 8)  # a few bytes: far below 5 GB
            with (
                patch.object(quality, "probe_duration_seconds", return_value=600.0),
                patch.object(quality, "source_height", return_value=2160),
                patch.object(quality, "extract_samples") as fake_extract,
                patch.object(quality, "find_handbrake") as fake_hb,
            ):
                result = quality.plan_quality(src, min_source_size_gb=5.0)
        self.assertFalse(result.success)
        self.assertEqual(result.skipped_reason, "below min size")
        self.assertIn("5.00 GB", result.error or "")
        fake_extract.assert_not_called()
        fake_hb.assert_not_called()

    def test_min_source_size_gate_is_off_by_default(self):
        """A small file is a normal candidate when no floor is configured.

        The gate must be opt-in: a file that would previously be probed must
        still be probed when the operator has not asked for a size floor.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "movie.mkv"
            src.write_bytes(b"x" * 8)
            with (
                patch.object(quality, "probe_duration_seconds", return_value=600.0),
                patch.object(quality, "source_height", return_value=2160),
                patch.object(quality, "find_handbrake", return_value="hb"),
                patch.object(quality, "extract_samples", return_value=[(Path("s0.mkv"), 10.0)]),
                patch.object(quality, "estimate_size_factor", return_value=0.5),
                patch.object(quality, "probe_crf", return_value=95.0),
            ):
                result = quality.plan_quality(src)
        self.assertTrue(result.success)
        self.assertIsNone(result.skipped_reason)

    def test_min_source_height_gate_skips_before_probing(self):
        """A source below the height floor is skipped before any probe runs.

        The gate exists to avoid spending the size probe and the CRF search on
        a source whose pixel count bounds the saving; it must decide from the
        height alone and reach neither extraction nor HandBrake.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "movie.mkv"
            src.write_bytes(b"x" * 8)
            with (
                patch.object(quality, "probe_duration_seconds", return_value=600.0),
                patch.object(quality, "source_height", return_value=720),
                patch.object(quality, "extract_samples") as fake_extract,
                patch.object(quality, "find_handbrake") as fake_hb,
            ):
                result = quality.plan_quality(src, min_source_height=1080)
        self.assertFalse(result.success)
        self.assertEqual(result.skipped_reason, "resolution too low")
        self.assertIn("720p", result.error or "")
        self.assertIn("1080p", result.error or "")
        fake_extract.assert_not_called()
        fake_hb.assert_not_called()

    def test_min_source_height_gate_is_off_by_default(self):
        """A 720p source is a normal candidate when no floor is configured.

        The gate must be opt-in: a source that would previously be probed must
        still be probed when the operator has not asked for a resolution floor.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "movie.mkv"
            src.write_bytes(b"x" * 8)
            with (
                patch.object(quality, "probe_duration_seconds", return_value=600.0),
                patch.object(quality, "source_height", return_value=720),
                patch.object(quality, "find_handbrake", return_value="hb"),
                patch.object(quality, "extract_samples", return_value=[(Path("s0.mkv"), 10.0)]),
                patch.object(quality, "estimate_size_factor", return_value=0.5),
                patch.object(quality, "probe_crf", return_value=95.0),
            ):
                result = quality.plan_quality(src)
        self.assertTrue(result.success)
        self.assertIsNone(result.skipped_reason)

    def test_min_source_height_gate_unknown_height_is_not_skipped(self):
        """An unreadable height must not silently exclude a file.

        A gate that treats "unknown" as "too low" would drop titles the
        operator expected to be considered; the honest answer is to probe.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "movie.mkv"
            src.write_bytes(b"x" * 8)
            with (
                patch.object(quality, "probe_duration_seconds", return_value=600.0),
                patch.object(quality, "source_height", return_value=None),
                patch.object(quality, "find_handbrake", return_value="hb"),
                patch.object(quality, "extract_samples", return_value=[(Path("s0.mkv"), 10.0)]),
                patch.object(quality, "estimate_size_factor", return_value=0.5),
                patch.object(quality, "probe_crf", return_value=95.0),
            ):
                result = quality.plan_quality(src, min_source_height=1080)
        self.assertTrue(result.success)
        self.assertIsNone(result.skipped_reason)

    def test_extract_samples_stream_copies_the_source(self):
        """Samples are stream-copied, never re-encoded.

        The samples are the VMAF reference and the probe encode's input, so a
        lossy extraction biases every measurement: the probe source becomes
        easier than the real one and the chosen CRF lands too high. A copy also
        keeps the source's bit depth and HDR side data, which an x264 pass drops.
        """
        commands: list[list[str]] = []

        def fake_run(command, cancel=None):
            commands.append(command)
            Path(command[-1]).write_bytes(b"x" * 8)
            return 0, ""

        with (
            patch(
                "shrinkray.quality.subprocess.run",
                return_value=type(
                    "P", (), {"returncode": 0, "stdout": b'{"format": {"duration": 100.0}}'}
                )(),
            ),
            patch("shrinkray.quality._run", side_effect=fake_run),
            tempfile.TemporaryDirectory() as tmp,
        ):
            quality.extract_samples(Path("movie.mkv"), Path(tmp), 2, 10.0)
        self.assertEqual(len(commands), 2)
        for command in commands:
            self.assertIn("-c:v", command)
            self.assertEqual(command[command.index("-c:v") + 1], "copy")
            for lossy in ("libx264", "libx265", "-crf", "-preset"):
                self.assertNotIn(lossy, command)

    def test_extract_samples_falls_back_when_copy_fails(self):
        """A container that cannot hold the source codec re-extracts losslessly.

        The fallback must stay pristine (ffv1), never a lossy re-encode, and
        must say so in the log because it costs a decode and encode.
        """
        commands: list[list[str]] = []

        def fake_run(command, cancel=None):
            commands.append(command)
            if command[command.index("-c:v") + 1] == "copy":
                return 1, "codec not supported in container"
            Path(command[-1]).write_bytes(b"x" * 8)
            return 0, ""

        with (
            patch(
                "shrinkray.quality.subprocess.run",
                return_value=type(
                    "P", (), {"returncode": 0, "stdout": b'{"format": {"duration": 100.0}}'}
                )(),
            ),
            patch("shrinkray.quality._run", side_effect=fake_run),
            patch("shrinkray.quality.log_warning") as warned,
            tempfile.TemporaryDirectory() as tmp,
        ):
            samples = quality.extract_samples(Path("movie.mkv"), Path(tmp), 1, 10.0)
        self.assertEqual(len(samples), 1)
        self.assertEqual(commands[1][commands[1].index("-c:v") + 1], "ffv1")
        self.assertTrue(warned.called)

    def test_extract_samples_timeout_raises_documented_error(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            movie = Path(tmp) / "movie.mkv"
            movie.write_bytes(b"x")
            with (
                patch(
                    "shrinkray.quality.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=60),
                ),
                self.assertRaises(RuntimeError) as ctx,
            ):
                quality.extract_samples(movie, Path(tmp) / "work", count=3, seconds=10.0)
        self.assertIn("timed out after 60s", str(ctx.exception))
        self.assertIn("movie.mkv", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
