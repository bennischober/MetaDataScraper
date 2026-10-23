"""Helper and integration-token tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import os
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


class TestIntegrations(unittest.TestCase):
    """Jellyfin tap-on-the-shoulder: opt-in, env token, never raises."""

    def setUp(self):
        from shrinkray import integrations

        self.integrations = integrations

    def _conf(self, data: dict):
        return config_helper.Conf(data)

    def test_unconfigured_is_noop(self):
        self.assertIsNone(self.integrations.jellyfin_settings(self._conf({})))
        # notify without configuration: False, never raises
        self.assertFalse(self.integrations.notify_jellyfin(self._conf({})))
        self.assertEqual(self.integrations.notify_after_encodes(self._conf({}), []), 0)

    def test_url_without_token_is_skipped(self):
        conf = self._conf({"integrations": {"jellyfin": {"url": "http://nas:8096"}}})
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(self.integrations.jellyfin_settings(conf))

    def test_token_from_env_var(self):
        conf = self._conf(
            {"integrations": {"jellyfin": {"url": "http://nas:8096/", "token_env": "MY_TOKEN_VAR"}}}
        )
        with mock.patch.dict(os.environ, {"MY_TOKEN_VAR": " secret "}, clear=True):
            settings = self.integrations.jellyfin_settings(conf)
        self.assertEqual(settings, {"url": "http://nas:8096", "token": "secret"})

    def test_notify_posts_to_refresh_endpoint(self):
        """A configured integration POSTs to {url}/Library/Refresh with the token."""
        conf = self._conf({"integrations": {"jellyfin": {"url": "http://nas:8096"}}})
        captured: dict = {}

        class FakeResponse:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            # urllib capitalizes header keys ("X-emby-token") => match loosely
            captured["token"] = next(
                (v for k, v in request.headers.items() if k.lower() == "x-emby-token"), None
            )
            return FakeResponse()

        with (
            mock.patch.dict(os.environ, {"SHRINKRAY_JELLYFIN_TOKEN": "tok"}, clear=True),
            mock.patch("urllib.request.urlopen", fake_urlopen),
        ):
            ok = self.integrations.notify_jellyfin(conf)
        self.assertTrue(ok)
        self.assertEqual(captured["url"], "http://nas:8096/Library/Refresh")
        self.assertEqual(captured["token"], "tok")
        self.assertEqual(captured["method"], "POST")

    def test_notify_never_raises(self):
        """Server down / bad response => False and a log, no exception."""
        conf = self._conf({"integrations": {"jellyfin": {"url": "http://127.0.0.1:9"}}})
        with mock.patch.dict(os.environ, {"SHRINKRAY_JELLYFIN_TOKEN": "tok"}, clear=True):
            self.assertFalse(self.integrations.notify_jellyfin(conf, timeout=0.2))

    def test_notify_after_encodes_counts(self):
        conf = self._conf({})
        self.assertEqual(self.integrations.notify_after_encodes(conf, [Path("a.mkv")]), 1)


if __name__ == "__main__":
    unittest.main()
