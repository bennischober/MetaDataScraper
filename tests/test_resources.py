"""Resource politeness tests. Split from tests/test_shrinkray.py (2026-10-23)."""

import os
import unittest
from unittest import mock

from shrinkray import config as config_helper


class TestResources(unittest.TestCase):
    """Resource politeness: priority spawn kwargs, kill switch, RSS."""

    def test_spawn_kwargs_respects_kill_switch(self):
        """idle_priority=False disables the polite spawn kwargs."""
        from shrinkray import resources

        with mock.patch.object(resources, "_IDLE_PRIORITY", True):
            kwargs = resources.spawn_kwargs()
            if os.name == "nt":
                self.assertIn("creationflags", kwargs)
            else:
                self.assertEqual(kwargs, {})
        with mock.patch.object(resources, "_IDLE_PRIORITY", False):
            self.assertEqual(resources.spawn_kwargs(), {})

    def test_session_kwargs_posix_own_session(self):
        """Cancellable children spawn in their own session on POSIX (the
        process group IS the tree, so terminate_tree can killpg it); on
        Windows taskkill /T walks the tree, so nothing is needed."""
        from shrinkray import resources

        kwargs = resources.session_kwargs()
        if os.name == "nt":
            self.assertEqual(kwargs, {})
        else:
            self.assertEqual(kwargs, {"start_new_session": True})

    def test_rss_mib_sane(self):
        """The RSS is a plausible number (10 MiB .. 4 GiB) or None."""
        from shrinkray.resources import rss_mib

        rss = rss_mib()
        if rss is not None:
            self.assertGreaterEqual(rss, 10.0)
            self.assertLess(rss, 4096.0)

    def test_config_applies_resource_policy(self):
        """load_config honors resources.idle_priority (and resets cleanly)."""
        from shrinkray import resources

        old = config_helper._config
        old_priority = resources._IDLE_PRIORITY
        try:
            config_helper._config = None
            config = config_helper.Conf(
                {"resources": {"idle_priority": False}, "handbrake": {"max_threads": 6}}
            )
            self.assertFalse(config.idle_priority)
            self.assertEqual(config.handbrake_max_threads, 6)
            resources.apply(config)
            self.assertFalse(resources._IDLE_PRIORITY)
            config_on = config_helper.Conf({})
            self.assertTrue(config_on.idle_priority)
            self.assertEqual(config_on.handbrake_max_threads, 0)
        finally:
            config_helper._config = old
            resources._IDLE_PRIORITY = old_priority

    def test_conf_defaults(self):
        conf = config_helper.Conf({})
        self.assertEqual(conf.handbrake_max_threads, 0)
        self.assertTrue(conf.idle_priority)


if __name__ == "__main__":
    unittest.main()
