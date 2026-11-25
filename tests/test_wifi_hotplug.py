"""
USB Wi-Fi adapters plugged in and pulled out (services/wifi_hotplug.py)
and the adapter each network remembers (services/wifi.py).

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

from services import config, wifi, wifi_hotplug  # noqa: E402

BUILT_IN = "dc:a6:32:00:00:01"
AXML = "00:c0:ca:00:00:02"
OTHER = "00:c0:ca:00:00:03"


class FakeRadios:
    """The radios plugged in, the access points running, what was applied."""

    def __init__(self):
        self.radios = {"wlan0": BUILT_IN}
        self.running = {"wlan0"}
        self.applied = 0
        self.stopped = []
        self.start_fails = False

    def apply(self):
        self.applied += 1
        if self.start_fails:
            return False, "wlan1: hostapd failed"
        settings = wifi.get_wifi_settings()["networks"]
        for name in self.radios:
            net = settings.get(name)
            if net and net.get("enabled") and not wifi.other_adapter(name):
                self.running.add(name)
                wifi.remember_radio(name)
        return True, "Applied."

    def stop(self, interface):
        self.stopped.append(interface)
        self.running.discard(interface)
        return True, "Stopped."


NETWORKS = {
    "wlan0": {"enabled": True, "ssid": "Chaos Router"},
    "wlan1": {"enabled": True, "ssid": "Chaos Router 6G"},
}


class HotplugTests(unittest.TestCase):

    def setUp(self):
        self.fake = FakeRadios()
        config.save_persistent(wifi.RADIOS, {})
        config.save_persistent(wifi_hotplug.SEEN, [])
        wifi_hotplug._last = None
        wifi_hotplug._lans = None
        self.events = []
        # The LAN interfaces at each essentials sync; whether one fails.
        self.synced = []
        self.sync_fails = False

        settings = {"country": "DE", "networks": dict(NETWORKS)}

        patches = [
            mock.patch.object(wifi, "get_wireless_interfaces", lambda: sorted(self.fake.radios)),
            mock.patch.object(wifi, "radio_mac", lambda name: self.fake.radios.get(name)),
            mock.patch.object(wifi, "is_hostapd_running", lambda name: name in self.fake.running),
            mock.patch.object(wifi, "running_instances", lambda: sorted(self.fake.running)),
            mock.patch.object(wifi, "applied_addresses", lambda: {}),
            mock.patch.object(wifi, "apply_wifi_settings", self.fake.apply),
            mock.patch.object(wifi, "stop_access_point", self.fake.stop),
            mock.patch.object(wifi, "get_wifi_settings", lambda: settings),
            mock.patch.object(wifi, "get_band_support", lambda name: {"2.4": [1], "5": [36], "6": [37]}),
            mock.patch.object(wifi, "max_access_points", lambda name: 1),
            mock.patch.object(wifi_hotplug, "START_SETTLE", 0),
            mock.patch.object(wifi_hotplug, "log_event",
                              lambda source, message, level="info", notify=False, **kw:
                              self.events.append((level, notify, message))),
            mock.patch("services.dhcp.apply_dhcp_settings", lambda: (True, "OK")),
            mock.patch("services.guest.sync", lambda: (True, "OK")),
            # The firewall's LAN interfaces: eth0 plus the radios.
            mock.patch("services.firewall.get_lan_interfaces", lambda: ["eth0"] + sorted(self.fake.radios)),
            mock.patch("services.firewall.sync_essentials", self.sync_essentials),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        self.settings = settings
        wifi.remember_radio("wlan0")

    def sync_essentials(self):
        self.synced.append((["eth0"] + sorted(self.fake.radios), self.fake.applied))
        return (False, "ufw failed") if self.sync_fails else (True, "OK")

    def plug(self, name, mac):
        self.fake.radios[name] = mac
        wifi_hotplug.check()

    def unplug(self, name):
        self.fake.radios.pop(name)
        wifi_hotplug.check()

    def test_a_saved_network_starts_when_its_adapter_is_plugged_in(self):
        wifi_hotplug.check()
        self.assertEqual(self.fake.applied, 0)
        self.plug("wlan1", AXML)
        self.assertIn("wlan1", self.fake.running)
        self.assertEqual(wifi.remembered_radios()["wlan1"], AXML)
        self.assertTrue(any(n and '"Chaos Router 6G" is on' in m for _, n, m in self.events), self.events)

    def test_unplugged_stops_and_says_so(self):
        self.plug("wlan1", AXML)
        self.unplug("wlan1")
        self.assertEqual(self.fake.stopped, ["wlan1"])
        self.assertTrue(any("unplugged" in m and n for _, n, m in self.events))
        # Back in: on again.
        self.plug("wlan1", AXML)
        self.assertIn("wlan1", self.fake.running)

    def test_a_new_adapter_is_announced_once(self):
        wifi_hotplug.check()
        self.plug("wlan2", OTHER)
        new = [m for _, n, m in self.events if n and "New Wi-Fi adapter" in m]
        self.assertEqual(len(new), 1)
        self.assertIn("2.4, 5, 6 GHz", new[0])
        self.assertEqual(self.fake.applied, 0)
        self.unplug("wlan2")
        self.plug("wlan2", OTHER)
        self.assertEqual(len([m for _, n, m in self.events if n and "New Wi-Fi adapter" in m]), 1)

    def test_an_adapter_without_access_point_mode(self):
        wifi_hotplug.check()
        with mock.patch.object(wifi, "max_access_points", lambda name: 0):
            self.plug("wlan2", OTHER)
        self.assertTrue(any(level == "warning" and "cannot send" in m for level, _, m in self.events))

    def test_another_adapter_under_the_name_stays_off(self):
        self.plug("wlan1", AXML)
        self.unplug("wlan1")
        self.plug("wlan1", OTHER)
        self.assertNotIn("wlan1", self.fake.running)
        self.assertTrue(any(level == "warning" and "different Wi-Fi adapter" in m for level, _, m in self.events))
        # Saving on the Wi-Fi page makes it this adapter's network.
        wifi.forget_radio("wlan1")
        self.fake.apply()
        self.assertEqual(wifi.remembered_radios()["wlan1"], OTHER)

    def test_swapped_between_two_looks(self):
        self.plug("wlan1", AXML)
        self.fake.radios["wlan1"] = OTHER
        wifi_hotplug.check()
        self.assertEqual(self.fake.stopped, ["wlan1"])
        self.assertTrue(any("different Wi-Fi adapter" in m for _, _, m in self.events))

    def test_waits_for_a_pending_change(self):
        wifi_hotplug.check()
        with mock.patch("services.transaction.load_pending", lambda: {"areas": [{"name": "wifi"}]}):
            self.plug("wlan1", AXML)
        self.assertNotIn("wlan1", self.fake.running)
        wifi_hotplug.check()
        self.assertIn("wlan1", self.fake.running)

    def test_late_adapter_at_start(self):
        # Plugged in before the first look, after the start-up apply.
        self.fake.radios["wlan1"] = AXML
        wifi_hotplug.check()
        self.assertIn("wlan1", self.fake.running)

    def test_failed_start_is_a_warning(self):
        wifi_hotplug.check()
        self.fake.start_fails = True
        self.plug("wlan1", AXML)
        self.assertTrue(any(level == "warning" and "did not start" in m for level, _, m in self.events))

    def test_switched_off_network(self):
        self.settings["networks"]["wlan1"] = {"enabled": False, "ssid": "Chaos Router 6G"}
        wifi_hotplug.check()
        self.plug("wlan1", AXML)
        self.assertEqual(self.fake.applied, 0)


    # The firewall's essential rules (DHCP, DNS on each LAN interface).

    def test_firewall_follows_a_plugged_in_adapter_before_its_network_starts(self):
        wifi_hotplug.check()
        self.assertEqual(self.synced, [(["eth0", "wlan0"], 0)])
        self.plug("wlan1", AXML)
        # Synced for wlan1 before the apply that started it.
        self.assertEqual(self.synced[-1], (["eth0", "wlan0", "wlan1"], 0))
        self.assertEqual(self.fake.applied, 1)
        self.unplug("wlan1")
        self.assertEqual(self.synced[-1][0], ["eth0", "wlan0"])

    def test_firewall_not_synced_again_without_a_change(self):
        wifi_hotplug.check()
        wifi_hotplug.check()
        wifi_hotplug.check()
        self.assertEqual(len(self.synced), 1)

    def test_firewall_follows_an_adapter_without_a_saved_network(self):
        # Saved on the Wi-Fi page later: the rules are already there.
        wifi_hotplug.check()
        self.plug("wlan2", OTHER)
        self.assertEqual(self.synced[-1][0], ["eth0", "wlan0", "wlan2"])

    def test_firewall_waits_for_a_pending_change(self):
        with mock.patch("services.transaction.load_pending", lambda: {"areas": [{"name": "firewall"}]}):
            wifi_hotplug.check()
        self.assertEqual(self.synced, [])
        wifi_hotplug.check()
        self.assertEqual(len(self.synced), 1)

    def test_firewall_failure_warns_once(self):
        self.sync_fails = True
        wifi_hotplug.check()
        wifi_hotplug.check()
        warnings = [m for level, n, m in self.events if level == "warning" and "firewall rules" in m]
        self.assertEqual(len(warnings), 1)


class RadioMemoryTests(unittest.TestCase):

    def test_remember_and_other_adapter(self):
        config.save_persistent(wifi.RADIOS, {})
        radios = {"wlan1": AXML}
        with mock.patch.object(wifi, "radio_mac", lambda name: radios.get(name)):
            self.assertFalse(wifi.other_adapter("wlan1"))
            wifi.remember_radio("wlan1")
            self.assertFalse(wifi.other_adapter("wlan1"))
            radios["wlan1"] = OTHER
            self.assertTrue(wifi.other_adapter("wlan1"))
            wifi.forget_radio("wlan1")
            self.assertFalse(wifi.other_adapter("wlan1"))


if __name__ == "__main__":
    unittest.main()
