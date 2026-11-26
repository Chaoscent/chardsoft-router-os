"""
Release channels: stable (release/*) and development (dev/*), and Stable
never offering an older version.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

SANDBOX = tempfile.mkdtemp(prefix="chardsoft-test-")

for name, folder in (("CHARDSOFT_DEFAULTS_DIR", "etc"), ("CHARDSOFT_STATE_DIR", "var"), ("CHARDSOFT_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHARDSOFT_SKIP_STARTUP", "1")

from services import update  # noqa: E402

LATEST = "b" * 40


def info(channel, version="1.0.0"):
    return {
        "version": version, "commit": "a" * 40, "branch": f"{'release' if channel == 'stable' else 'dev'}/core",
        "channel": channel, "repo": "https://github.com/owner/repo", "managed": True,
        "can_update": True, "reason": None, "status": None
    }


class ChannelTests(unittest.TestCase):

    def test_channel_of_branch(self):
        self.assertEqual(update.channel_of("release/core"), "stable")
        self.assertEqual(update.channel_of("dev/hardware-edition"), "development")
        self.assertIsNone(update.channel_of("main"))
        self.assertIsNone(update.channel_of(None))

    def test_versions(self):
        self.assertTrue(update.version_older("0.1.0-alpha", "1.0.0"))
        self.assertTrue(update.version_older("1.0.0-beta", "1.0.0"))
        self.assertTrue(update.version_older("1.0.0", "1.0.1"))
        self.assertFalse(update.version_older("1.0.0", "1.0.0"))
        self.assertFalse(update.version_older("1.1.0-dev", "1.0.0"))
        self.assertFalse(update.version_older("unknown", "1.0.0"))

    def check(self, channel, installed, remote):
        with mock.patch.multiple(update, get_update_info=lambda: info(channel, installed),
                                 _get_json=lambda url: {"sha": LATEST, "commit": {"message": "x"}},
                                 remote_version=lambda *a: remote):
            ok, result = update.check_for_update()
        self.assertTrue(ok)
        return result

    def test_stable_without_release(self):
        result = self.check("stable", "0.1.0-alpha", None)
        self.assertFalse(result["update_available"])
        self.assertIn("no stable release", result["note"])

    def test_stable_never_goes_back(self):
        result = self.check("stable", "1.1.0-dev", "1.0.0")
        self.assertFalse(result["update_available"])
        self.assertIn("older", result["note"])

    def test_newer_stable_is_offered(self):
        result = self.check("stable", "1.0.0", "1.0.1")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "1.0.1")

    def test_development_takes_every_commit(self):
        self.assertTrue(self.check("development", "1.0.0", "1.0.0")["update_available"])

    def test_set_channel_runs_the_updater(self):
        calls = []
        with mock.patch.multiple(update, get_update_info=lambda: info("development"),
                                 run_command=lambda cmd: calls.append(cmd) or (True, "")):
            self.assertTrue(update.set_channel("stable")[0])
            self.assertEqual(calls[-1][-2:], ["channel", "stable"])
            self.assertFalse(update.set_channel("nightly")[0])
            self.assertEqual(update.set_channel("development"), (True, "No change."))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
