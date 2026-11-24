"""
The GNSS map: the detailed map download (services/maps.py) with a
stand-in for the pmtiles tool, the countries, and the position track
(services/gnss.py).

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import hashlib
import io
import os
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

SANDBOX = tempfile.mkdtemp(prefix="chardsoft-test-")

for name, folder in (("CHAOS_DEFAULTS_DIR", "etc"), ("CHAOS_STATE_DIR", "var"), ("CHAOS_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHAOS_SKIP_STARTUP", "1")

from services import config, gnss, maps  # noqa: E402


class CountryTests(unittest.TestCase):

    def test_countries(self):
        found = maps.countries()
        self.assertGreater(len(found), 200)
        self.assertEqual(found["DE"]["name"], "Germany")
        west, south, east, north = found["DE"]["bbox"]
        self.assertTrue(5 < west < 7 and 47 < south < 48 and 14 < east < 16 and 54 < north < 56)

    def test_country_at(self):
        self.assertEqual(maps.country_at(52.52, 13.40), "DE")
        self.assertEqual(maps.country_at(48.86, 2.35), "FR")
        self.assertEqual(maps.country_at(-33.87, 151.21), "AU")
        self.assertIsNone(maps.country_at(40.0, -40.0))


class SettingsTests(unittest.TestCase):

    def test_source(self):
        self.assertEqual(maps.validate_settings({"source": "https://mirror.example/builds/"})[1],
                         {"source": "https://mirror.example/builds"})
        for bad in ("http://x", "ftp://x", "https://a b", "file:///etc"):
            self.assertFalse(maps.validate_settings({"source": bad})[0], bad)


class DownloadTests(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=SANDBOX))
        paths = {
            "MAP_DIR": self.dir, "REGION": self.dir / "region.pmtiles", "PART": self.dir / "region.part",
            "META": self.dir / "region.json", "LOG": self.dir / "download.log",
            "REGION_SHAPE": self.dir / "region.geojson", "TOOL": self.dir / "bin" / "pmtiles",
        }
        for key, value in paths.items():
            p = mock.patch.object(maps, key, value)
            p.start()
            self.addCleanup(p.stop)
        maps._job.update(process=None, started=None, country=None, error=None, stage=None, cancelled=False)
        self.log = mock.patch.object(maps, "log_event").start()
        self.addCleanup(mock.patch.stopall)

    def fake_tool(self, body):
        maps.TOOL.parent.mkdir(parents=True, exist_ok=True)
        maps.TOOL.write_text("#!/bin/sh\n" + body)
        os.chmod(maps.TOOL, 0o755)

    def wait(self):
        for _ in range(100):
            if maps.get_status()["state"] != "downloading":
                return
            time.sleep(0.05)
        self.fail("download did not end")

    def test_download(self):
        # $2: the source, $3: the output file.
        self.fake_tool('echo "fetching $2 $4 $5"\nprintf pmtiles-data > "$3"\n')
        with mock.patch.object(maps, "latest_build", return_value="20261124.pmtiles"), \
                mock.patch.object(maps, "free_bytes", return_value=50 * 10 ** 9):
            ok, message = maps.start("DE", 14)
            self.assertTrue(ok, message)
            self.assertFalse(maps.start("FR", 14)[0])
            self.wait()
        status = maps.get_status()
        self.assertEqual(status["state"], "ready", status)
        self.assertEqual(status["region"]["name"], "Germany")
        self.assertEqual(status["region"]["build"], "20261124.pmtiles")
        self.assertEqual(maps.REGION.read_text(), "pmtiles-data")
        self.assertIn("https://build.protomaps.com/20261124.pmtiles", maps.LOG.read_text())
        self.assertIn("--maxzoom=14", maps.LOG.read_text())
        self.assertTrue(maps.REGION_SHAPE.exists())
        self.assertTrue(self.log.call_args.kwargs.get("notify"))

        self.assertEqual(maps.delete(), (True, "Detailed map deleted."))
        self.assertEqual(maps.get_status()["state"], "none")

    def test_failure_keeps_the_old_map(self):
        maps.REGION.write_text("old")
        maps.META.write_text('{"name": "Old"}')
        self.fake_tool('echo "Error: 403 Forbidden"\nexit 1\n')
        with mock.patch.object(maps, "latest_build", return_value="x.pmtiles"), \
                mock.patch.object(maps, "free_bytes", return_value=50 * 10 ** 9):
            maps.start("DE", 12)
            for _ in range(100):
                if maps._job["stage"] is None:
                    break
                time.sleep(0.05)
        self.assertEqual(maps._job["error"], "Error: 403 Forbidden")
        self.assertEqual(maps.REGION.read_text(), "old")

    def test_cancel(self):
        self.fake_tool('printf part > "$3"\nsleep 30\n')
        with mock.patch.object(maps, "latest_build", return_value="x.pmtiles"), \
                mock.patch.object(maps, "free_bytes", return_value=50 * 10 ** 9):
            maps.start("DE", 15)
            for _ in range(100):
                if maps.running():
                    break
                time.sleep(0.05)
            self.assertEqual(maps.get_status()["state"], "downloading")
            self.assertTrue(maps.cancel()[0])
            self.wait()
        self.assertFalse(maps.PART.exists())
        self.assertEqual(maps.get_status()["state"], "none")

    def test_refusals(self):
        with mock.patch.object(maps, "free_bytes", return_value=50 * 10 ** 9):
            self.assertFalse(maps.start("XX", 14)[0])
            self.assertFalse(maps.start("DE", 13)[0])
        with mock.patch.object(maps, "free_bytes", return_value=10 ** 9):
            self.assertIn("free", maps.start("DE", 14)[1])

    def test_tool_checksum(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            data = b"#!/bin/sh\n"
            info = tarfile.TarInfo("pmtiles")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        archive = buffer.getvalue()
        good = {"x86_64": ("Linux_x86_64", hashlib.sha256(archive).hexdigest())}
        with mock.patch.object(maps.platform, "machine", return_value="x86_64"), \
                mock.patch.object(maps, "fetch", return_value=archive):
            with mock.patch.object(maps, "PMTILES_RELEASES", {"x86_64": ("Linux_x86_64", "0" * 64)}):
                self.assertIn("checksum", maps.ensure_tool()[1])
                self.assertFalse(maps.TOOL.exists())
            with mock.patch.object(maps, "PMTILES_RELEASES", good):
                self.assertEqual(maps.ensure_tool(), (True, "OK"))
        self.assertTrue(os.access(maps.TOOL, os.X_OK))


class TrackTests(unittest.TestCase):

    def setUp(self):
        gnss._track.update(points=[], saved=0.0, dirty=False)
        config.save_running(gnss.SETTINGS, {"enabled": True, "set_clock": True, "track_days": 1})

    def test_points_when_moved_or_later(self):
        now = 1_800_000_000
        gnss.add_to_track(52.5, 13.4, now, 1)
        gnss.add_to_track(52.50001, 13.40001, now + 10, 1)   # ~1 m: skipped
        gnss.add_to_track(52.501, 13.4, now + 20, 1)          # ~110 m
        gnss.add_to_track(52.501, 13.4, now + 400, 1)         # 5 min later
        self.assertEqual(len(gnss._track["points"]), 3)
        # A day later the old points go.
        gnss.add_to_track(52.6, 13.4, now + 86400 + 30, 1)
        self.assertEqual(len(gnss._track["points"]), 2)

    def test_settings_and_clear(self):
        self.assertTrue(gnss.validate_settings({"track_days": 7})[0])
        self.assertFalse(gnss.validate_settings({"track_days": 3})[0])
        self.assertFalse(gnss.validate_settings({"track_days": True})[0])
        gnss.add_to_track(52.5, 13.4, time.time(), 1)
        self.assertEqual(len(gnss.get_track()["points"]), 1)
        gnss.clear_track()
        self.assertEqual(gnss.get_track()["points"], [])

    def test_switching_off_deletes(self):
        gnss.add_to_track(52.5, 13.4, time.time(), 1)
        with mock.patch.object(gnss.transaction, "change", return_value={"success": True}):
            gnss.save_settings({"track_days": 0})
        self.assertEqual(gnss._track["points"], [])

    def test_distance(self):
        self.assertAlmostEqual(gnss.meters((52.5, 13.4), (52.501, 13.4)), 111.2, delta=0.5)


if __name__ == "__main__":
    unittest.main()
