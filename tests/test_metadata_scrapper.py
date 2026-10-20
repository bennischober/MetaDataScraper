import json
import unittest
from pathlib import Path

from metadata_scrapper import config as config_helper
from metadata_scrapper import encode, helpers, quality
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
        command = quality.build_vmaf_command(Path("enc.mkv"), Path("ref.mkv"))
        # distorted stream must be the first input (libvmaf main)
        self.assertEqual(command[command.index("-i") + 1], "enc.mkv")
        self.assertEqual(command[command.index("-i", command.index("-i") + 1) + 1], "ref.mkv")
        # model by bare filename => no windows drive colon in the filtergraph
        self.assertIn("model_path=vmaf_v0.6.1.json", " ".join(command))
        self.assertNotIn("\\", " ".join(command))

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


if __name__ == "__main__":
    unittest.main()
