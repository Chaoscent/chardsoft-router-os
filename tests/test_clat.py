"""
IPv4 over an IPv6-only WAN (services/clat.py): when it runs, the NAT64
prefix from DNS64, the tayga config, the commands, and the LAN route.
System commands are faked.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import ipaddress
import os
import struct
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

from services import clat, config, firewall, routing, wan_routing  # noqa: E402

V6_ONLY = """4: wwan0    inet6 2a01:598:900e:7a75:58e3:4d81:f4b9:5774/64 scope global dynamic noprefixroute"""
PLACEHOLDER = """4: wwan0    inet 192.0.0.2/27 brd 192.0.0.31 scope global wwan0
4: wwan0    inet6 2a01:598:900e:7a75:58e3:4d81:f4b9:5774/64 scope global"""
DUAL = """4: wwan0    inet 10.84.17.203/32 scope global wwan0
4: wwan0    inet6 2a01:598:900e:7a75:58e3:4d81:f4b9:5774/64 scope global"""


def dns_answer(tid, addresses):
    """A DNS response to `ipv4only.arpa AAAA`, with name compression."""
    question = b"\x08ipv4only\x04arpa\x00" + struct.pack("!HH", 28, 1)
    answers = b"".join(b"\xc0\x0c" + struct.pack("!HHIH", 28, 1, 60, 16) + ipaddress.IPv6Address(a).packed
                       for a in addresses)
    return struct.pack("!HHHHHH", tid, 0x8180, 1, len(addresses), 0, 0) + question + answers


def set_routing(**values):
    config.save_running("routing", {**routing.DEFAULT_SETTINGS, **values})


class PrefixTests(unittest.TestCase):

    def test_dns64_prefix_is_found(self):
        data = dns_answer(77, ["64:ff9b::c000:aa", "64:ff9b::c000:ab"])
        self.assertEqual(clat.prefix_from_addresses(clat.parse_aaaa(data, 77)), "64:ff9b::/96")

    def test_carrier_specific_prefix(self):
        data = dns_answer(5, ["2a01:598:7ff:1:0:64:c000:aa"])
        self.assertEqual(clat.prefix_from_addresses(clat.parse_aaaa(data, 5)), "2a01:598:7ff:1:0:64::/96")

    def test_wrong_id_or_no_dns64(self):
        self.assertEqual(clat.parse_aaaa(dns_answer(1, ["64:ff9b::c000:aa"]), 2), [])
        self.assertIsNone(clat.prefix_from_addresses(clat.parse_aaaa(dns_answer(3, []), 3)))

    def test_falls_back_to_the_well_known_prefix(self):
        with mock.patch.object(clat, "wan_dns_servers", return_value=[]):
            self.assertEqual(clat.discover_prefix("wwan0"), ("64:ff9b::/96", "default"))


class WhenTests(unittest.TestCase):

    def wanted(self, addresses_output, **settings):
        set_routing(**settings)
        with mock.patch.object(clat, "run", return_value=addresses_output), \
             mock.patch.object(clat, "is_installed", return_value=True):
            return clat.is_wanted("wwan0")

    def test_ipv6_only_wan_needs_it(self):
        self.assertIs(self.wanted(V6_ONLY), True)

    def test_modem_placeholder_is_not_ipv4(self):
        self.assertIs(self.wanted(PLACEHOLDER), True)

    def test_real_ipv4_does_not(self):
        self.assertIs(self.wanted(DUAL), False)

    def test_wan_down_keeps_what_runs(self):
        self.assertIsNone(self.wanted(""))

    def test_switched_off(self):
        self.assertIs(self.wanted(V6_ONLY, clat=False), False)
        self.assertIs(self.wanted(V6_ONLY, forwarding=False), False)

    def test_not_installed(self):
        set_routing()
        with mock.patch.object(clat, "which", return_value=None):
            self.assertIs(clat.is_wanted("wwan0", (V6_ONLY, False)), False)


class ConfigTests(unittest.TestCase):

    def test_addresses_are_in_the_wan_64(self):
        host, tayga = clat.addresses_for("2a01:598:900e:7a75:58e3:4d81:f4b9:5774")
        self.assertEqual(host, "2a01:598:900e:7a75::c1a7")
        self.assertEqual(tayga, "2a01:598:900e:7a75::c1a6")

    def test_config(self):
        text = clat.render_config("64:ff9b::/96", "2a01:db8::c1a7", "2a01:db8::c1a6")
        self.assertIn("tun-device clat\n", text)
        self.assertIn("map 192.0.0.4 2a01:db8::c1a7\n", text)
        self.assertIn("prefix 64:ff9b::/96\n", text)


class FakeSystem:

    def __init__(self):
        self.commands = []
        self.config = None
        self.tun = None
        self.addresses = V6_ONLY

    def run_command(self, cmd):
        cmd = [c for c in cmd if c not in ("sudo", "-n")]
        self.commands.append(cmd)
        if cmd[:2] == ["systemctl", "restart"]:
            self.tun = "17"
        if cmd[:2] == ["systemctl", "stop"]:
            self.tun = None
        if cmd[:1] == ["install"]:
            with open(cmd[-2]) as f:
                self.config = f.read()
        return True, ""

    def run(self, cmd):
        if cmd[:2] == ["systemctl", "is-active"]:
            return "active" if self.tun else None
        if cmd[:3] == ["ip", "-o", "addr"]:
            return self.addresses
        return ""

    def patch(self):
        return mock.patch.multiple(
            clat, run=self.run, run_command=self.run_command, privileged=lambda c: c,
            is_installed=lambda: True, get_wan_interface=lambda: "wwan0",
            installed_config=lambda: self.config, tun_index=lambda: self.tun,
            discover_prefix=lambda wan: ("64:ff9b::/96", "DNS64 of test"))


class ApplyTests(unittest.TestCase):

    def setUp(self):
        set_routing()
        config.save_running(clat.STATE, {})
        clat._applied["key"] = None

    def test_sets_everything_up_once(self):
        fake = FakeSystem()
        with fake.patch(), mock.patch("services.ipv6.is_forwarding_enabled", return_value=True):
            self.assertEqual(clat.sync(), (True, "CLAT on."))
            self.assertTrue(clat.is_active())
            first = list(fake.commands)
            fake.commands.clear()
            clat.sync()
        self.assertIn(["systemctl", "restart", "chaos-clat"], first)
        self.assertIn(["ip", "addr", "replace", "192.0.0.4/32", "dev", "clat"], first)
        self.assertIn(["ip", "-6", "route", "replace", "2a01:598:900e:7a75::c1a7/128", "dev", "clat"], first)
        self.assertIn(["iptables", "-t", "nat", "-A", "CHAOS-CLAT", "-o", "clat", "-j", "MASQUERADE"], first)
        self.assertIn(["ip", "route", "replace", "default", "dev", "clat", "metric", "2000"], first)
        # The modem's link needs no proxy NDP.
        self.assertFalse(any("proxy" in c for c in first))
        # Nothing changed: no commands at all.
        self.assertEqual(fake.commands, [])

    def test_new_wan_prefix_rewrites_and_restarts(self):
        fake = FakeSystem()
        with fake.patch(), mock.patch("services.ipv6.is_forwarding_enabled", return_value=True):
            clat.sync()
            fake.commands.clear()
            fake.addresses = "4: wwan0    inet6 2a01:598:9007:686b::5/64 scope global"
            clat.sync()
        self.assertIn(["systemctl", "restart", "chaos-clat"], fake.commands)
        self.assertIn("map 192.0.0.4 2a01:598:9007:686b::c1a7", fake.config)

    def test_real_ipv4_stops_it(self):
        fake = FakeSystem()
        with fake.patch(), mock.patch("services.ipv6.is_forwarding_enabled", return_value=True):
            clat.sync()
            fake.addresses = DUAL
            self.assertEqual(clat.sync(), (True, "CLAT off."))
        self.assertIn(["systemctl", "stop", "chaos-clat"], fake.commands)
        self.assertFalse(clat.is_active())

    def test_lan_ipv4_route_goes_through_clat(self):
        with mock.patch.object(clat, "is_active", return_value=True):
            self.assertEqual(wan_routing.wan_route("-4", "wwan0"), ["default", "dev", "clat", "metric", "100"])


class FirewallTests(unittest.TestCase):

    def test_essential_rules_allow_the_clat(self):
        with mock.patch.multiple(firewall, get_wan_interface=lambda: "wwan0", get_lan_interfaces=lambda: ["wlan1"]):
            args = [r["args"] for r in firewall.get_essential_rules()]
        self.assertTrue(any(a[:7] == ["route", "allow", "in", "on", "wlan1", "out", "on"] and a[7] == "clat" for a in args))
        self.assertTrue(any(a[:8] == ["route", "allow", "in", "on", "clat", "out", "on", "wwan0"] for a in args))


if __name__ == "__main__":
    unittest.main()
