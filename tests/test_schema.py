"""
Settings format versions: stamping, update steps on both layers,
downgrades, backups from older and newer versions.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

SANDBOX = tempfile.mkdtemp(prefix="chardsoft-test-")

for name, folder in (("CHAOS_DEFAULTS_DIR", "etc"), ("CHAOS_STATE_DIR", "var"), ("CHAOS_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHAOS_SKIP_STARTUP", "1")

from services import backup, config, schema  # noqa: E402


def rename_ping(files):
    """Example step 1 -> 2: dashboard.ping_enabled becomes dashboard.ping."""

    dash = files.get("dashboard")

    if isinstance(dash, dict) and "ping_enabled" in dash:
        dash["ping"] = dash.pop("ping_enabled")


def drop_legacy(files):
    """Example step 2 -> 3: a settings file that is no longer used."""

    files.pop("legacy", None)


def format_three():
    return mock.patch.multiple(schema, VERSION=3, MIGRATIONS={1: rename_ping, 2: drop_legacy})


# The test modules share one sandbox; only touch these files.
OWN = ("schema", "dashboard", "legacy", "shell")


def clear():
    for folder in (config.STATE_DIR, config.RUNTIME_DIR):
        for name in OWN:
            (Path(folder) / f"{name}.json").unlink(missing_ok=True)


class StartupTests(unittest.TestCase):

    def setUp(self):
        clear()

    def tearDown(self):
        clear()

    def test_fresh_install_is_stamped_current(self):
        self.assertEqual(schema.migrate(), [])
        self.assertEqual(config.load_persistent("schema"), {"version": schema.VERSION})

    def test_old_install_is_updated_on_both_layers(self):
        config.save_persistent("dashboard", {"ping_enabled": True})
        config.save_running("dashboard", {"ping_enabled": False})
        config.save_persistent("legacy", {"x": 1})
        config.save_persistent("shell", {"enabled": True}, secret=True)

        with format_three():
            self.assertEqual(schema.migrate(), ["1->2", "2->3"])
            self.assertEqual(schema.stored_version(), 3)
            # Second start: nothing to do.
            self.assertEqual(schema.migrate(), [])

        self.assertEqual(config.load_persistent("dashboard"), {"ping": True})
        self.assertEqual(config.load_running("dashboard"), {"ping": False})
        self.assertFalse(config.has_persistent("legacy"))
        self.assertEqual(config.load_persistent("shell"), {"enabled": True})

    def test_secret_files_stay_private(self):
        config.save_persistent("dashboard", {"ping_enabled": True}, secret=True)

        with format_three():
            schema.migrate()

        mode = stat.S_IMODE(os.stat(config.state_path("dashboard")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_newer_settings_are_left_alone(self):
        config.save_persistent("schema", {"version": 9})
        config.save_persistent("dashboard", {"ping_enabled": True})

        self.assertEqual(schema.migrate(), [])
        self.assertEqual(config.load_persistent("dashboard"), {"ping_enabled": True})
        self.assertEqual(config.load_persistent("schema"), {"version": 9})

    def test_steps_can_run_twice(self):
        files = {"dashboard": {"ping_enabled": True}}

        with format_three():
            schema.migrate_files(files, 1)
            schema.migrate_files(files, 1)

        self.assertEqual(files, {"dashboard": {"ping": True}})

    def test_missing_step_is_an_error(self):
        with mock.patch.multiple(schema, VERSION=2, MIGRATIONS={}):
            with self.assertRaises(ValueError):
                schema.migrate_files({}, 1)

    def test_broken_schema_file_counts_as_version_one(self):
        for value in ({"version": "2"}, {"version": 0}, [], "x"):
            config.save_persistent("schema", value)
            self.assertEqual(schema.stored_version(), 1, value)


def make_backup(config_data, schema_version=None):

    content = {"config": config_data, "system": {}}
    raw = {"format": backup.FORMAT, "version": backup.VERSION, "created": 1760000000,
           "hostname": "router", **content, "checksum": backup.checksum(content)}

    if schema_version is not None:
        raw["schema"] = schema_version

    return json.dumps(raw)


class BackupTests(unittest.TestCase):

    def test_new_backups_carry_the_format(self):
        with mock.patch("services.system.get_hostname", return_value="router"):
            _, data = backup.create_backup()
        self.assertEqual(data["schema"], schema.VERSION)

    def test_old_backup_is_updated_before_restore(self):
        with format_three():
            ok, raw = backup.parse_backup(make_backup({"dashboard": {"ping_enabled": True}}))
        self.assertTrue(ok, raw)
        self.assertEqual(raw["config"]["dashboard"], {"ping": True})

    def test_backup_from_a_newer_router_is_refused(self):
        ok, message = backup.parse_backup(make_backup({}, schema_version=schema.VERSION + 1))
        self.assertFalse(ok)
        self.assertIn("newer", message)

    def test_current_backup_is_unchanged(self):
        data = {"dashboard": {"ping_enabled": True}}
        ok, raw = backup.parse_backup(make_backup(data, schema_version=schema.VERSION))
        self.assertTrue(ok, raw)
        self.assertEqual(raw["config"], data)


if __name__ == "__main__":
    unittest.main()
