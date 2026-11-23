"""
Modem readings: one mmcli reading serves many requests, and slow
readings do not pile up.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

SANDBOX = tempfile.mkdtemp(prefix="chardsoft-test-")

for name, folder in (("CHAOS_DEFAULTS_DIR", "etc"), ("CHAOS_STATE_DIR", "var"), ("CHAOS_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHAOS_SKIP_STARTUP", "1")

from services import modem  # noqa: E402


class ReadingCacheTests(unittest.TestCase):

    def setUp(self):
        modem._modem_cache.update(time=0.0, data=None)
        self.reads = 0

    def slow_read(self):
        self.reads += 1
        time.sleep(0.3)
        return {"state": "ready", "n": self.reads}

    def test_parallel_requests_share_one_reading(self):
        results = []
        with mock.patch.object(modem, "read_modem_data", self.slow_read):
            threads = [threading.Thread(target=lambda: results.append(modem.get_modem_data())) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(self.reads, 1)
        self.assertEqual(len(results), 8)

    def test_old_reading_is_refreshed(self):
        with mock.patch.object(modem, "read_modem_data", self.slow_read):
            modem.get_modem_data()
            modem.get_modem_data(max_age=0)
        self.assertEqual(self.reads, 2)

    def test_callers_get_their_own_copy(self):
        with mock.patch.object(modem, "read_modem_data", self.slow_read):
            first = modem.get_modem_data()
            first["state"] = "changed"
            self.assertEqual(modem.get_modem_data()["state"], "ready")


if __name__ == "__main__":
    unittest.main()
