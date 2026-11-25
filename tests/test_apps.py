"""
Apps page: the board-memory warning for apps that need more RAM, and
the admin's own apps.

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

from services import apps  # noqa: E402


def meminfo(kb):
    path = os.path.join(SANDBOX, f"meminfo-{kb}")
    with open(path, "w") as f:
        f.write(f"MemTotal:       {kb} kB\nMemFree:          1000 kB\n")
    return path


class BoardMemoryTests(unittest.TestCase):

    def test_reported_memory_rounds_up_to_the_board_size(self):
        # What Raspberry Pi boards report next to what they are sold as.
        for kb, board in ((925_000, 1024), (1_890_000, 2048), (3_880_000, 4096),
                          (8_000_000, 8192), (16_200_000, 16384), (480_000, 512)):
            self.assertEqual(apps.board_ram_mb(meminfo(kb)), board, kb)

    def test_unreadable_meminfo_gives_none(self):
        self.assertIsNone(apps.board_ram_mb(os.path.join(SANDBOX, "missing")))
        path = os.path.join(SANDBOX, "broken")
        with open(path, "w") as f:
            f.write("nothing useful\n")
        self.assertIsNone(apps.board_ram_mb(path))


class CatalogTests(unittest.TestCase):

    CATALOG = [
        {"id": "jellyfin", "name": "Jellyfin", "min_ram_mb": 2048},
        {"id": "vaultwarden", "name": "Vaultwarden"},
        {"id": "odd", "name": "Odd", "min_ram_mb": "lots"},
    ]

    def get_apps(self, board):
        answers = {"version": {"addon": "1", "running": True}, "catalog": self.CATALOG, "status": []}
        with mock.patch.multiple(apps, addon_installed=lambda: True,
                                 cli_json=lambda cmd: (True, answers[cmd]),
                                 board_ram_mb=lambda: board):
            return apps.get_apps()

    def test_small_board_flags_heavy_apps(self):
        data = self.get_apps(1024)
        flags = {a["id"]: a["low_ram"] for a in data["apps"]}
        self.assertEqual(flags, {"jellyfin": True, "vaultwarden": False, "odd": False})
        self.assertEqual(data["board_ram_mb"], 1024)

    def test_big_enough_board_flags_nothing(self):
        self.assertFalse(any(a["low_ram"] for a in self.get_apps(2048)["apps"]))

    def test_unknown_board_flags_nothing(self):
        self.assertFalse(any(a["low_ram"] for a in self.get_apps(None)["apps"]))


class CustomAppTests(unittest.TestCase):

    def test_id_from_name(self):
        self.assertEqual(apps.custom_id("Home Assistant"), "home-assistant")
        self.assertEqual(apps.custom_id("  --Ünïcode App!! "), "n-code-app")
        self.assertEqual(apps.custom_id("x" * 40 + "-y"), "x" * 31)
        self.assertEqual(apps.custom_id("!!!"), "")

    def test_form_definition(self):
        d = apps.custom_definition({
            "name": "My App", "mode": "form", "image": " nginx ", "port": "80",
            "volumes": ["/config", " "], "env": [{"key": "A", "value": "1"}, {"key": " ", "value": "x"}, "junk"]
        })
        self.assertEqual(d, {"id": "my-app", "name": "My App", "mode": "form", "image": "nginx",
                             "port": "80", "volumes": ["/config"], "env": {"A": "1"}})

    def test_compose_definition(self):
        d = apps.custom_definition({"name": "Stack", "mode": "compose", "compose": "services: {}", "service": " web ", "port": 8080})
        self.assertEqual(d["service"], "web")
        self.assertEqual(d["compose"], "services: {}")
        self.assertNotIn("image", d)

    def test_listed_after_the_catalog(self):
        status = [
            {"id": "vaultwarden", "host": "vaultwarden.chardsoft-router.local", "port": 9102, "state": "running"},
            {"id": "mqtt", "custom": True, "name": "MQTT", "port": None, "state": "running",
             "images": ["eclipse-mosquitto:2"], "accepted": ["port 1883/tcp is opened ..."]},
            {"id": "kuma", "custom": True, "name": "Kuma", "port": 9200, "state": "stopped",
             "host": "kuma.chardsoft-router.local", "images": ["louislam/uptime-kuma:1"]},
        ]
        answers = {"version": {"addon": "1", "running": True}, "catalog": [{"id": "vaultwarden", "name": "Vaultwarden"}], "status": status}
        with mock.patch.multiple(apps, addon_installed=lambda: True,
                                 cli_json=lambda cmd: (True, answers[cmd]), board_ram_mb=lambda: 4096):
            listed = apps.get_apps()["apps"]
        self.assertEqual([a["id"] for a in listed], ["vaultwarden", "kuma", "mqtt"])
        kuma, mqtt = listed[1], listed[2]
        self.assertTrue(kuma["custom"] and kuma["web"] and kuma["installed"])
        self.assertFalse(mqtt["web"])
        self.assertEqual(mqtt["risks"], ["port 1883/tcp is opened ..."])
        self.assertEqual(kuma["description"], "louislam/uptime-kuma:1")

    def test_actions_reach_own_apps_but_not_install(self):
        status = [{"id": "kuma", "custom": True, "name": "Kuma"}]
        answers = {"catalog": [], "status": status}
        started = []
        with mock.patch.multiple(apps, addon_installed=lambda: True,
                                 cli_json=lambda cmd: (True, answers[cmd]),
                                 start_job=lambda *a, **k: started.append(a) or (True, "ok")):
            self.assertTrue(apps.run_action("stop", "kuma")[0])
            self.assertFalse(apps.run_action("install", "kuma")[0])
            self.assertFalse(apps.run_action("stop", "unknown")[0])
        self.assertEqual(started[0][3], [apps.CLI, "stop", "kuma"])

    def test_nameless_app_is_refused_before_the_app_manager(self):
        with mock.patch.multiple(apps, addon_installed=lambda: True,
                                 cli_json=lambda *a: self.fail("must not run")):
            self.assertFalse(apps.check_custom({"name": "!!", "mode": "form"})[0])
            self.assertFalse(apps.add_custom({"name": "", "mode": "form"})[0])



class AddonRemoveTests(unittest.TestCase):

    def remove(self, **options):
        started = {}

        def start_job(action, app_id, name, cmd, after=None, cleanup=None):
            started.update(action=action, cmd=cmd, cleanup=cleanup)
            return True, "started"

        with mock.patch.multiple(apps, addon_installed=lambda: True, start_job=start_job):
            ok, _ = apps.remove_addon(**options)

        self.assertTrue(ok)
        return started

    def test_options_become_one_flag(self):
        self.assertEqual(self.remove()["cmd"][1:], [])
        self.assertEqual(self.remove(delete_data=True)["cmd"][1:], ["--delete-data"])
        self.assertEqual(self.remove(delete_data=True, purge=True)["cmd"][1:], ["--purge"])

    def test_runs_the_remover_and_cleans_up_names(self):
        started = self.remove()
        self.assertEqual(started["action"], "addon_remove")
        self.assertEqual(os.path.basename(started["cmd"][0]), "remove-apps.sh")
        self.assertIs(started["cleanup"], apps.sync_integration_later)

    def test_nothing_to_remove(self):
        with mock.patch.object(apps, "addon_installed", lambda: False):
            ok, message = apps.remove_addon()
        self.assertFalse(ok)
        self.assertIn("not installed", message)


if __name__ == "__main__":
    unittest.main()
