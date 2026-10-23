"""Quality/CRF-search tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import json
import tempfile
import unittest
from pathlib import Path

from shrinkray import encode, quality


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


if __name__ == "__main__":
    unittest.main()
