"""
GNSS (services/gnss.py): NMEA parsing, the page's plain words, the
receiver coming back after a modem restart, and the clock. mmcli is
faked with its JSON output.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import datetime
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

SANDBOX = tempfile.mkdtemp(prefix="chardsoft-test-")

for name, folder in (("CHAOS_DEFAULTS_DIR", "etc"), ("CHAOS_STATE_DIR", "var"), ("CHAOS_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHAOS_SKIP_STARTUP", "1")

from services import config, dashboard_layout, gnss  # noqa: E402


def nmea(body):
    value = 0
    for char in body:
        value ^= ord(char)
    return f"${body}*{value:02X}"


FIX = [
    nmea("GPGSV,2,1,07,02,45,120,38,05,30,200,35,12,60,080,41,15,10,300,00"),
    nmea("GPGSV,2,2,07,18,25,040,33,24,70,150,44,29,05,350,"),
    nmea("GAGSV,1,1,03,04,40,100,36,09,20,220,31,11,15,010,"),
    nmea("GNGSA,A,3,02,05,12,18,24,04,09,,,,,,1.6,0.9,1.3"),
    nmea("GNGGA,121530.00,5231.0410,N,01323.5160,E,1,07,0.9,48.5,M,39.0,M,,"),
    nmea("GNRMC,121530.00,A,5231.0410,N,01323.5160,E,0.2,85.0,251126,,,A"),
]

SEARCHING = [
    nmea("GPGSV,1,1,04,02,45,120,22,05,30,200,,12,60,080,19,15,10,300,"),
    nmea("GNGSA,A,1,,,,,,,,,,,,,,,"),
    nmea("GNGGA,121530.00,,,,,0,00,,,M,,M,,"),
    nmea("GNRMC,121530.00,V,,,,,,,251126,,,N"),
]

NOTHING = [nmea("GNGGA,,,,,,0,00,,,M,,M,,"), nmea("GNRMC,,V,,,,,,,,,,N")]


class NmeaTests(unittest.TestCase):

    def test_checksum(self):
        self.assertTrue(gnss.checksum_ok(FIX[0]))
        self.assertFalse(gnss.checksum_ok(FIX[0][:-2] + "00"))
        self.assertFalse(gnss.checksum_ok("GPGGA,1,2"))

    def test_fix(self):
        state = gnss.parse_nmea(FIX)
        self.assertTrue(state["fix"])
        self.assertEqual(state["fix_type"], 3)
        self.assertAlmostEqual(state["latitude"], 52.517350, places=5)
        self.assertAlmostEqual(state["longitude"], 13.391933, places=5)
        self.assertEqual((state["used"], state["altitude"], state["hdop"]), (7, 48.5, 0.9))
        self.assertEqual(state["speed_kmh"], 0.4)
        self.assertEqual(state["constellations"], {
            "GPS": {"in_view": 7, "heard": 5}, "Galileo": {"in_view": 3, "heard": 2}})
        self.assertEqual(gnss.fix_timestamp(state),
                         datetime.datetime(2026, 11, 25, 12, 15, 30, tzinfo=datetime.timezone.utc).timestamp())

    def test_southern_and_western(self):
        self.assertAlmostEqual(gnss.coordinate("3352.1280", "S"), -33.8688, places=4)
        self.assertAlmostEqual(gnss.coordinate("15112.5600", "W"), -151.2093, places=4)
        self.assertIsNone(gnss.coordinate("", "N"))

    def test_searching(self):
        state = gnss.parse_nmea(SEARCHING)
        self.assertFalse(state["fix"])
        self.assertEqual(state["constellations"]["GPS"], {"in_view": 4, "heard": 2})
        self.assertIsNone(state["latitude"])

    def test_broken_sentences_are_skipped(self):
        state = gnss.parse_nmea(["garbage", "$GNGGA,1,2*00", FIX[4][:-3] + "*FF"])
        self.assertFalse(state["fix"])


class FakeModem:
    """mmcli with JSON output, the receiver on or off."""

    def __init__(self, sentences, capable=True, nmea_refused=False, port=None):
        self.sentences = sentences
        self.capable = capable
        # The RM520N over QMI: gps-nmea refused, gps-unmanaged works.
        self.nmea_refused = nmea_refused
        self.port = port
        # Unmanaged tries that time out (just after a stop).
        self.busy = 0
        self.enabled = []
        self.calls = []

    def run_command(self, command):
        self.calls.append(command)
        if "--location-status" in command:
            caps = ["3gpp-lac-ci", "gps-raw", "gps-nmea", "gps-unmanaged"] if self.capable else ["3gpp-lac-ci"]
            return True, json.dumps({"modem": {"location": {"capabilities": caps, "enabled": self.enabled}}})
        if command[-1:] == ["-J"] and len(command) == 4:
            ports = ["cdc-wdm0 (qmi)", "ttyUSB0 (ignored)", f"{self.port} (gps)", "ttyUSB2 (at)"]
            return True, json.dumps({"modem": {"generic": {"ports": ports}}})
        if "--location-enable-gps-nmea" in command and self.nmea_refused:
            return False, "error: couldn't setup location gathering: QMI protocol error (48): 'InvalidArgument'"
        if "--location-enable-gps-unmanaged" in command:
            if self.busy:
                self.busy -= 1
                return False, "error: Serial command timed out"
            self.enabled = ["3gpp-lac-ci", "gps-unmanaged"]
        if "--location-disable-gps-unmanaged" in command:
            self.enabled = []
        if "--location-get" in command:
            gps = {"nmea": self.sentences if "gps-nmea" in self.enabled else [], "latitude": "--"}
            return True, json.dumps({"modem": {"location": {
                "3gpp": {"mcc": "262", "mnc": "01", "tac": "00B4D5", "cid": "0181A06B", "lac": "--"}, "gps": gps}}})
        if "--location-enable-gps-nmea" in command:
            self.enabled = ["3gpp-lac-ci", "gps-nmea", "gps-raw"]
        if "--location-disable-gps-nmea" in command:
            self.enabled = []
        return True, ""

    def privileged(self, command):
        return command

    def get_modem_index(self):
        return "0"

    def run(self, command):
        return "no\n"


class ReceiverTests(unittest.TestCase):

    def setUp(self):
        config.save_running(gnss.SETTINGS, {"enabled": True, "set_clock": True})
        gnss._last.update(reading=None, fix_key=None, fix_seen=0.0, clock_set=0.0)
        self.modem = FakeModem(FIX)
        for p in (mock.patch.object(gnss, "_modem", lambda: self.modem),
                  mock.patch.object(gnss, "start_watch"),
                  mock.patch.object(gnss, "log_event")):
            p.start()
            self.addCleanup(p.stop)

    def test_apply_switches_on_and_off(self):
        self.assertTrue(gnss.apply()[0])
        self.assertIn("gps-nmea", self.modem.enabled)
        self.assertTrue(any("--location-set-gps-refresh-rate=5" in c for c in self.modem.calls))
        config.save_running(gnss.SETTINGS, {"enabled": False, "set_clock": True})
        self.assertEqual(gnss.apply(), (True, "GNSS off."))
        self.assertEqual(self.modem.enabled, [])

    def test_no_receiver(self):
        self.modem.capable = False
        ok, message = gnss.apply()
        self.assertFalse(ok)
        self.assertEqual(gnss.get_status()["level"], "unsupported")

    def test_back_on_after_a_modem_restart(self):
        gnss.apply()
        self.modem.enabled = []
        with mock.patch.object(gnss, "maybe_set_clock"):
            gnss.sample()
        self.assertIn("gps-nmea", self.modem.enabled)

    def test_status_with_a_fix(self):
        gnss.apply()
        with mock.patch.object(gnss, "maybe_set_clock"):
            status = gnss.get_status(refresh=True)
        self.assertEqual(status["level"], "fix")
        self.assertIn("from 7 satellites", status["message"])
        self.assertEqual(status["position"]["accuracy_m"], 4)
        self.assertEqual(status["cell"]["mcc"], "262")
        self.assertIsNone(status["cell"]["lac"])
        self.assertEqual((status["heard"], status["in_view"]), (7, 10))

    def test_status_while_searching(self):
        gnss.apply()
        self.modem.sentences = SEARCHING
        status = gnss.get_status(refresh=True)
        self.assertEqual(status["level"], "searching")
        self.assertIn("2 satellites heard", status["message"])
        self.modem.sentences = NOTHING
        self.assertIn("antenna", gnss.get_status(refresh=True)["message"])

    def test_off(self):
        config.save_running(gnss.SETTINGS, {"enabled": False, "set_clock": True})
        self.assertEqual(gnss.get_status(refresh=True)["level"], "off")


class UnmanagedTests(unittest.TestCase):
    """No NMEA through ModemManager: the sentences come from the modem's port."""

    def setUp(self):
        config.save_running(gnss.SETTINGS, {"enabled": True, "set_clock": True})
        gnss._last.update(reading=None, fix_key=None, fix_seen=0.0, clock_set=0.0)
        self.master, slave = os.openpty()
        self.slave = os.ttyname(slave)
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, slave)
        self.modem = FakeModem(FIX, nmea_refused=True, port=os.path.basename(self.slave))
        for p in (mock.patch.object(gnss, "_modem", lambda: self.modem),
                  mock.patch.object(gnss, "start_watch"),
                  mock.patch.object(gnss, "maybe_set_clock"),
                  mock.patch.object(gnss, "gps_port", lambda index: self.slave),
                  mock.patch.object(gnss, "log_event")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(gnss.stop_port)

    def test_falls_back_and_reads_the_port(self):
        self.assertTrue(gnss.apply()[0])
        self.assertEqual(self.modem.enabled, ["3gpp-lac-ci", "gps-unmanaged"])
        gnss.sample()
        time.sleep(0.3)
        os.write(self.master, ("\r\n".join(FIX) + "\r\n").encode())
        for _ in range(40):
            status = gnss.get_status(refresh=True)
            if status["level"] == "fix":
                break
            time.sleep(0.05)
        self.assertEqual(status["level"], "fix", status)
        self.assertAlmostEqual(status["position"]["latitude"], 52.51735, places=4)

    def test_tries_again_while_the_modem_is_busy(self):
        self.modem.busy = 2
        with mock.patch.object(gnss, "UNMANAGED_PAUSE", 0):
            self.assertTrue(gnss.apply()[0])
        self.assertEqual(self.modem.enabled, ["3gpp-lac-ci", "gps-unmanaged"])

    def test_old_sentences_do_not_count(self):
        gnss._port["lines"].clear()
        gnss._port["lines"].append((time.time() - 10, FIX[4]))
        self.assertEqual(gnss.port_sentences(), [])

    def test_off_stops_the_reader(self):
        gnss.apply()
        gnss.sample()
        self.assertTrue(gnss._port["thread"].is_alive())
        config.save_running(gnss.SETTINGS, {"enabled": False, "set_clock": True})
        gnss.apply()
        self.assertIsNone(gnss._port["thread"])
        self.assertEqual(self.modem.enabled, [])

    def test_port_from_mmcli(self):
        self.modem.port = "ttyUSB1"
        self.assertEqual(GPS_PORT(self.modem), "/dev/ttyUSB1")


def GPS_PORT(modem):
    with mock.patch.object(gnss, "_modem", lambda: modem):
        return ORIGINAL_GPS_PORT("0")


ORIGINAL_GPS_PORT = gnss.gps_port


class ClockTests(unittest.TestCase):

    def setUp(self):
        config.save_running(gnss.SETTINGS, {"enabled": True, "set_clock": True})
        gnss._last.update(clock_set=0.0)
        self.modem = FakeModem(FIX)
        for p in (mock.patch.object(gnss, "_modem", lambda: self.modem),
                  mock.patch.object(gnss, "log_event")):
            p.start()
            self.addCleanup(p.stop)
        self.state = gnss.parse_nmea(FIX)
        self.stamp = int(gnss.fix_timestamp(self.state))

    def dates(self):
        return [c for c in self.modem.calls if c[:1] == ["date"]]

    def test_sets_an_unsynchronised_clock_once(self):
        with mock.patch.object(gnss.time, "time", return_value=1000.0):
            gnss.maybe_set_clock(self.state, fresh=True)
            gnss.maybe_set_clock(self.state, fresh=True)
        self.assertEqual(self.dates(), [["date", "-u", "-s", f"@{self.stamp}"]])

    def test_leaves_it_alone(self):
        cases = [
            ("stale fix", dict(fresh=False)),
            ("internet time", dict(fresh=True, synced=True)),
            ("already right", dict(fresh=True, now=self.stamp + 2)),
            ("switched off", dict(fresh=True, setting=False)),
        ]
        for name, case in cases:
            self.modem.calls.clear()
            gnss._last["clock_set"] = 0.0
            config.save_running(gnss.SETTINGS, {"enabled": True, "set_clock": case.get("setting", True)})
            with mock.patch.object(gnss, "clock_synchronized", return_value=case.get("synced", False)), \
                    mock.patch.object(gnss.time, "time", return_value=case.get("now", 1000.0)):
                gnss.maybe_set_clock(self.state, fresh=case["fresh"])
            self.assertEqual(self.dates(), [], name)


class LayoutTests(unittest.TestCase):

    def test_widget(self):
        layout = dashboard_layout.default_layout() + [{"id": "gnss", "type": "gnss", "width": 6, "hidden": False}]
        self.assertEqual(dashboard_layout.validate_layout(layout)[-1]["type"], "gnss")

    def test_settings(self):
        self.assertTrue(gnss.validate_settings({"enabled": True})[0])
        self.assertFalse(gnss.validate_settings({"enabled": "yes"})[0])


if __name__ == "__main__":
    unittest.main()
