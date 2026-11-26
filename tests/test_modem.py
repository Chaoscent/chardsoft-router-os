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

for name, folder in (("CHARDSOFT_DEFAULTS_DIR", "etc"), ("CHARDSOFT_STATE_DIR", "var"), ("CHARDSOFT_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHARDSOFT_SKIP_STARTUP", "1")

from services import modem, modem_watchdog as wd  # noqa: E402


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


def sample(rx, errors, tx):
    return {"rx_packets": rx, "rx_errors": errors, "tx_packets": tx}


class BandTests(unittest.TestCase):

    def test_nsa_without_5g_band_says_5g(self):
        self.assertEqual(modem.band_label("B3", "5G NSA"), "B3 + 5G")

    def test_known_5g_band_is_kept(self):
        self.assertEqual(modem.band_label("B3 + n78", "5G NSA"), "B3 + n78")

    def test_lte_and_unknown_stay(self):
        self.assertEqual(modem.band_label("B20", "LTE"), "B20")
        self.assertIsNone(modem.band_label(None, "5G NSA"))

    def test_pi_qmicli_output(self):
        output = ("Band Information:\n\tRadio Interface:   'lte'\n\tActive Band Class: 'eutran-3'\n"
                  "\tActive Channel:    '1300'\nBand Information (Extended):\n\tRadio Interface:   'lte'\n"
                  "\tActive Band Class: 'eutran-3'\n")
        self.assertEqual(modem.band_label(modem.format_bands(output), "5G NSA"), "B3 + 5G")


class WatchdogTests(unittest.TestCase):

    def setUp(self):
        wd.state.update(previous=None, stuck=0, resets=[], last_reset=None)
        self.counters = sample(100, 0, 100)
        self.resets = []

    def run_samples(self, steps, connection="activated"):
        """steps: (received, errors, sent) per sample; returns check() results."""
        results = []
        with mock.patch.object(wd.modem, "wwan_interface", return_value=("wwan0", "qmi_wwan")), \
             mock.patch.object(wd.modem, "get_connection_state", return_value=connection), \
             mock.patch.object(wd.modem, "ensure_link_mtu", return_value=False), \
             mock.patch.object(wd, "counters", side_effect=lambda i: dict(self.counters)), \
             mock.patch.object(wd, "reset_modem", side_effect=lambda reason: self.resets.append(reason) or True):
            results.append(wd.check())
            for rx, err, tx in steps:
                c = self.counters
                self.counters = sample(c["rx_packets"] + rx, c["rx_errors"] + err, c["tx_packets"] + tx)
                results.append(wd.check())
        return results

    def test_rejected_packets_lead_to_one_reset(self):
        results = self.run_samples([(0, 1500, 0)] * 3)
        self.assertEqual(results, ["ok", "stuck", "stuck", "reset"])
        self.assertEqual(len(self.resets), 1)
        self.assertIn("rejected", self.resets[0])
        self.assertIsNotNone(wd.state["last_reset"])

    def test_sending_without_answers_is_stuck_too(self):
        self.assertEqual(self.run_samples([(0, 0, 200)] * 3)[-1], "reset")

    def test_a_working_link_is_left_alone(self):
        results = self.run_samples([(0, 1500, 0), (0, 1500, 0), (5, 1500, 10), (0, 1500, 0)])
        self.assertEqual(results, ["ok", "stuck", "stuck", "ok", "stuck"])
        self.assertEqual(self.resets, [])

    def test_quiet_link_is_not_stuck(self):
        self.assertNotIn("reset", self.run_samples([(0, 0, 3)] * 6))

    def test_cooldown_and_hourly_limit(self):
        stuck = [(0, 1500, 0)] * 30
        with mock.patch.object(wd.time, "monotonic", side_effect=[i * 100.0 for i in range(1000)]):
            self.run_samples(stuck)
        # A reset every 300 s at most, 3 per hour.
        self.assertLessEqual(len(self.resets), wd.MAX_RESETS_PER_HOUR)
        self.assertGreaterEqual(len(self.resets), 2)

    def test_nothing_happens_without_mobile_data(self):
        self.assertEqual(set(self.run_samples([(0, 1500, 0)] * 4, connection="deactivated")), {"idle"})

    def test_counter_restart_is_not_stuck(self):
        self.assertFalse(wd.is_stuck(sample(500, 900, 500), sample(3, 2, 3)))


class LinkMtuTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.mtu_file = os.path.join(folder, "mtu")
        self.commands = []
        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == "/sys/class/net/wwan0/mtu":
                return real_open(self.mtu_file, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        self.patches = [
            mock.patch("builtins.open", side_effect=fake_open),
            mock.patch.object(modem, "privileged", side_effect=lambda cmd: cmd),
            mock.patch.object(modem, "run_command", side_effect=lambda cmd, **kw: self.commands.append(cmd) or (True, "")),
            mock.patch.object(modem, "has_nmcli", return_value=True),
            mock.patch.object(modem, "connection_exists", return_value=True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def set_mtu(self, value):
        with open(self.mtu_file, "w") as f:
            f.write(f"{value}\n")

    def test_qmi_link_below_1500_is_raised_and_remembered(self):
        self.set_mtu(1472)
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "qmi_wwan")):
            self.assertTrue(modem.ensure_link_mtu())
        self.assertIn(["ip", "link", "set", "wwan0", "mtu", "1500"], self.commands)
        self.assertIn(["nmcli", "connection", "modify", modem.CONNECTION, "gsm.mtu", "1500"], self.commands)

    def test_full_mtu_is_left_alone(self):
        self.set_mtu(1500)
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "qmi_wwan")):
            self.assertFalse(modem.ensure_link_mtu())
        self.assertEqual(self.commands, [])

    def test_other_drivers_keep_the_carrier_mtu(self):
        self.set_mtu(1430)
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "cdc_mbim")):
            self.assertFalse(modem.ensure_link_mtu())
        self.assertEqual(self.commands, [])

    def test_new_connection_gets_the_mtu_for_qmi_only(self):
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "qmi_wwan")):
            props = modem.connection_properties(dict(modem.DEFAULT_SETTINGS))
        self.assertEqual(props[props.index("gsm.mtu") + 1], "1500")
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "cdc_mbim")):
            self.assertNotIn("gsm.mtu", modem.connection_properties(dict(modem.DEFAULT_SETTINGS)))


if __name__ == "__main__":
    unittest.main()
