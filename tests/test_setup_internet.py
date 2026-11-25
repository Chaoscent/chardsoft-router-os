"""
The setup wizard's Internet step: what it reports for each state of
the modem or the Ethernet uplink. System calls are faked.

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

import setup  # noqa: E402

READY = {"state": "ready", "vendor": "Quectel", "model": "RM520N-GL", "sim": "Ready", "carrier": "Telekom.de",
         "network": "5G NSA", "signal": "72", "lock": "sim-pin2", "connection": "activated", "imei": "868371056464940"}


def check(wan="wwan0", address="10.84.17.203", online=True, modem=None):
    with mock.patch.multiple(setup.network, get_wan_interface=lambda: wan, get_ip=lambda i: address or "Unknown"), \
         mock.patch.object(setup, "get_ping", return_value="23 ms" if online else "-- ms"), \
         mock.patch.object(setup.modem, "get_modem_data", return_value={**READY, **(modem or {})}):
        return setup.get_internet_check()


class InternetCheckTests(unittest.TestCase):

    def test_online_modem(self):
        result = check()
        self.assertEqual(result["verdict"], "online")
        self.assertEqual(result["modem"]["carrier"], "Telekom.de")

    def test_imei_is_not_shown(self):
        self.assertNotIn("imei", check()["modem"])

    def test_modem_states(self):
        cases = {
            "no_modem": {"state": "absent"},
            "no_modemmanager": {"state": "no_modemmanager"},
            "modem_starting": {"state": "not_ready"},
            "sim_pin": {"lock": "sim-pin"},
            "sim_blocked": {"lock": "sim-puk"},
            "data_off": {"connection": "deactivated"},
            "offline": {},
        }
        for verdict, modem in cases.items():
            self.assertEqual(check(online=False, address=None, modem=modem)["verdict"], verdict, modem)

    def test_ethernet_uplink(self):
        self.assertEqual(check(wan="eth0", address="192.168.178.20")["verdict"], "online")
        self.assertIsNone(check(wan="eth0")["modem"])
        self.assertEqual(check(wan="eth0", address=None, online=False)["verdict"], "no_cable")
        self.assertEqual(check(wan="eth0", address="192.168.178.20", online=False)["verdict"], "offline")


if __name__ == "__main__":
    unittest.main()
