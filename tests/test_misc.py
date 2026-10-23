"""Helper and config-hygiene tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shrinkray import config as config_helper
from shrinkray import helpers


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


class TestRemovedIntegrationsKey(unittest.TestCase):
    """The Jellyfin integration was removed (issue #43): a config.json that
    still carries its 'integrations' key must load, but the removal must be
    visible - exactly one warning naming the key, never silence."""

    def test_leftover_key_warns_once_and_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                '{"integrations": {"jellyfin": {"url": "http://nas:8096"}}}',
                encoding="utf-8",
            )
            with (
                mock.patch.object(config_helper, "_config", None),
                mock.patch("shrinkray.config.log_warning") as warn,
            ):
                conf = config_helper.load_config(tmp)
            # the config still loads; every other default is intact
            self.assertEqual(conf.extensions, config_helper.DEFAULT_EXTENSIONS)
            # exactly one warning, naming the removed key
            self.assertEqual(warn.call_count, 1)
            self.assertIn("integrations", warn.call_args.args[0])

    def test_clean_config_does_not_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(config_helper, "_config", None),
                mock.patch("shrinkray.config.log_warning") as warn,
            ):
                config_helper.load_config(tmp)
            # the 'config file not found' warning is the only expected one
            self.assertFalse(any("integrations" in str(call.args) for call in warn.call_args_list))


if __name__ == "__main__":
    unittest.main()
