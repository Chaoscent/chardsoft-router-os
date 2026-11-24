"""
Guest Wi-Fi: the guest network's settings and hostapd section
(services/wifi.py) and its isolation chains (services/guest.py). Radios
and commands are faked.

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

for name, folder in (("CHAOS_DEFAULTS_DIR", "etc"), ("CHAOS_STATE_DIR", "var"), ("CHAOS_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHAOS_SKIP_STARTUP", "1")

from services import config, guest, wifi  # noqa: E402

IW_ONE = """Wiphy phy0
	valid interface combinations:
		 * #{ managed } <= 1, #{ P2P-device } <= 1, #{ P2P-client, P2P-GO } <= 1,
		   total <= 3, #channels <= 2
		 * #{ managed } <= 1, #{ AP } <= 1, #{ P2P-device } <= 1,
		   total <= 3, #channels <= 1
"""

IW_FOUR = """Wiphy phy1
	valid interface combinations:
		 * #{ managed } <= 2048, #{ AP, mesh point } <= 4, #{ P2P-client, P2P-GO } <= 1,
		   total <= 2048, #channels <= 1
"""


def network(**guest):
    return {
        "interface": "wlan1", "country": "DE", "enabled": True, "ssid": "Home", "password": "home-pass-1",
        "security": "wpa2", "band": "2.4", "channel": 6, "width": 20, "hidden": False,
        "isolate_clients": False, "address": "10.42.0.1", "prefix": 24,
        "guest": {"enabled": True, "ssid": "Home Guest", "password": "kite-2847-moss", "security": "wpa2",
                  "address": "10.43.0.1", "prefix": 24, **guest}
    }


class Radios(unittest.TestCase):

    def setUp(self):
        config.save_running(wifi.WIFI_SETTINGS, {"country": "DE", "networks": {}})
        self.max_aps = 4
        patches = [
            mock.patch.object(wifi, "get_wireless_interfaces", return_value=["wlan1"]),
            mock.patch.object(wifi, "get_band_support", return_value=None),
            mock.patch.object(wifi, "max_access_points", lambda i: self.max_aps),
            mock.patch.object(wifi, "bridges_interface", return_value=False),
            mock.patch.object(wifi, "run", return_value=""),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class GuestSettingsTests(Radios):

    def test_valid(self):
        ok, net = wifi.validate_network(network())
        self.assertTrue(ok, net)
        self.assertEqual(net["guest"]["address"], "10.43.0.1")
        self.assertTrue(net["guest"]["enabled"])

    def test_problems(self):
        cases = {
            "own": network(ssid="Home"),
            "empty": network(ssid=" "),
            "password": network(password=""),
            "short": network(password="short"),
            "overlap": network(address="10.42.0.50"),
            "security": network(security="wep"),
        }
        for name, data in cases.items():
            ok, message = wifi.validate_network(data)
            self.assertFalse(ok, name)

        ok, message = wifi.validate_network({**network(), "enabled": False})
        self.assertFalse(ok)
        self.assertIn("main network", message)

    def test_one_network_radios_say_so(self):
        self.max_aps = 1
        ok, message = wifi.validate_network(network())
        self.assertFalse(ok)
        self.assertIn("only send one network", message)
        # Off: no check, kept for later.
        ok, net = wifi.validate_network(network(enabled=False))
        self.assertTrue(ok, net)

    def test_open_guest_network(self):
        ok, net = wifi.validate_network(network(security="open", password=""))
        self.assertTrue(ok, net)
        self.assertEqual(net["guest"]["password"], "")

    def test_defaults_for_a_new_guest_network(self):
        net = wifi.get_network("wlan1")
        self.assertEqual(net["guest"]["ssid"], "Chaos Router Guest")
        self.assertFalse(net["guest"]["enabled"])
        self.assertNotEqual(net["guest"]["address"], net["address"])

    def test_stored_and_normalized(self):
        ok, net = wifi.validate_network(network())
        settings = wifi.with_network(wifi.get_wifi_settings(), net)
        config.save_running(wifi.WIFI_SETTINGS, settings)
        self.assertEqual(wifi.guest_interfaces(), {"wlan1-guest": ("wlan1", net["guest"])})
        # A guest interface never shows up as a radio of its own.
        self.assertTrue(wifi.validate_wifi_settings(settings)[0])

    def test_hostapd_section(self):
        ok, net = wifi.validate_network(network())
        with mock.patch.object(wifi, "guest_bssid", return_value="02:11:22:33:44:55"):
            text = wifi.render_hostapd_config(net)
        guest_part = text.split("bss=wlan1-guest", 1)[1]
        self.assertIn("bssid=02:11:22:33:44:55", guest_part)
        self.assertIn(f"ssid2={'Home Guest'.encode().hex()}", guest_part)
        self.assertIn("ap_isolate=1", guest_part)
        self.assertIn("wpa_passphrase=kite-2847-moss", guest_part)
        # Off: no second network.
        ok, net = wifi.validate_network(network(enabled=False))
        self.assertNotIn("bss=", wifi.render_hostapd_config(net))


class HelperTests(unittest.TestCase):

    def test_names(self):
        self.assertEqual(wifi.guest_interface("wlan0"), "wlan0-guest")
        self.assertEqual(wifi.guest_interface("wlx00c0cab12345"), "wlx00c0ca-guest")
        self.assertLessEqual(len(wifi.guest_interface("wlx00c0cab12345")), 15)
        self.assertTrue(wifi.is_guest_interface("wlan0-guest"))

    def test_max_access_points(self):
        for output, expected in ((IW_ONE, 1), (IW_FOUR, 4), ("Wiphy phy0\n", None)):
            with mock.patch("builtins.open", mock.mock_open(read_data="phy0")), \
                    mock.patch.object(wifi, "which", return_value="/usr/sbin/iw"), \
                    mock.patch.object(wifi, "run", return_value=output):
                self.assertEqual(wifi.max_access_points("wlan0"), expected)

    def test_bssid(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="dc:a6:32:01:02:03\n")):
            self.assertEqual(wifi.guest_bssid("wlan0"), "de:a6:32:01:02:03")


class FirewallTests(unittest.TestCase):

    def test_rules(self):
        v4 = guest.rules("iptables", ["wlan1-guest"])
        self.assertIn((guest.IN_CHAIN, ["-i", "wlan1-guest", "-p", "udp", "--dport", "67", "-j", "RETURN"]), v4)
        self.assertIn((guest.IN_CHAIN, ["-i", "wlan1-guest", "-j", "REJECT"]), v4)
        self.assertIn((guest.FWD_CHAIN, ["-i", "wlan1-guest", "-d", "192.168.0.0/16", "-j", "REJECT"]), v4)
        # The final reject comes after what passes.
        ins = [a for c, a in v4 if c == guest.IN_CHAIN]
        self.assertEqual(ins[-1], ["-i", "wlan1-guest", "-j", "REJECT"])
        v6 = guest.rules("/usr/sbin/ip6tables", ["wlan1-guest"])
        self.assertIn((guest.IN_CHAIN, ["-i", "wlan1-guest", "-p", "udp", "--dport", "547", "-j", "RETURN"]), v6)
        self.assertIn((guest.FWD_CHAIN, ["-i", "wlan1-guest", "-d", "fc00::/7", "-j", "REJECT"]), v6)

    def test_sync_without_guests_empties_the_chains(self):
        calls = []
        with mock.patch.object(guest, "_tools", return_value=["iptables"]), \
                mock.patch.object(guest, "run_command", lambda c: (calls.append(c) or ("-C" not in c), "")), \
                mock.patch.object(guest, "privileged", lambda c: c), \
                mock.patch.object(guest, "wanted", return_value=[]):
            self.assertEqual(guest.sync(), (True, "OK"))
        self.assertIn(["iptables", "-F", guest.IN_CHAIN], calls)
        self.assertFalse([c for c in calls if "-A" in c])


if __name__ == "__main__":
    unittest.main()
