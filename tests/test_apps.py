"""
Apps page: the board-memory warning for apps that need more RAM.

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


if __name__ == "__main__":
    unittest.main()
