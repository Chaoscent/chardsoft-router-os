"""
IPv6 for the LAN: prefix, per-LAN subnets, addresses, NAT66, router
advertisements, the VPN leak guard. System commands are faked.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import ipaddress
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

from services import config, dhcp, firewall, ipv6, routing, vpn_profiles  # noqa: E402

PREFIX = "fd12:3456:789a::/48"


class FakeSystem:
    """Records privileged commands; `ip -6 addr show` answers from a table."""

    def __init__(self, addresses=None):
        self.commands = []
        self.addresses = addresses or {}

    def run(self, cmd):
        if cmd[:4] == ["ip", "-6", "-o", "addr"]:
            dev = cmd[cmd.index("dev") + 1]
            return "\n".join(
                f"5: {dev}    inet6 {a} scope global \\       valid_lft forever"
                for a in self.addresses.get(dev, [])
            )
        return ""

    def run_command(self, cmd):
        cmd = [c for c in cmd if c not in ("sudo", "-n")]
        self.commands.append(cmd)
        # -C checks: "exists" only for rules this fake has seen added.
        if "-C" in cmd:
            rule = cmd[cmd.index("-C") + 1:]
            added = [c[c.index("-A") + 1:] for c in self.commands if "-A" in c]
            return (rule in added), ""
        return True, ""

    def patch(self):
        return mock.patch.multiple(ipv6, run=self.run, run_command=self.run_command,
                                   privileged=lambda c: c, which=lambda n: n)


def set_routing(**values):
    config.save_running("routing", {**routing.DEFAULT_SETTINGS, **values})


class PrefixTests(unittest.TestCase):

    def test_new_prefix_is_a_random_ula_48(self):
        prefixes = {ipv6.new_ula_prefix() for _ in range(20)}
        self.assertEqual(len(prefixes), 20)
        for prefix in prefixes:
            self.assertIsNotNone(ipv6.parse_ula_prefix(prefix))

    def test_parse_rejects_non_ula_and_wrong_length(self):
        for bad in ("", "garbage", "2001:db8::/48", "fd12:3456:789a::/64", "fc00::/48", None):
            self.assertIsNone(ipv6.parse_ula_prefix(bad), bad)

    def test_lan_networks_are_stable_and_inside_the_prefix(self):
        two = ipv6.lan_networks(["br0", "wlan1"], PREFIX)
        three = ipv6.lan_networks(["br0", "wlan1", "wlan2"], PREFIX)
        self.assertEqual(two["br0"], three["br0"])
        self.assertEqual(two["wlan1"], three["wlan1"])
        self.assertEqual(len(set(three.values())), 3)
        base = ipaddress.IPv6Network(PREFIX)
        for net in three.values():
            self.assertEqual(net.prefixlen, 64)
            self.assertTrue(net.subnet_of(base))

    def test_colliding_subnet_ids_get_the_next_free_one(self):
        with mock.patch.object(ipv6.zlib, "crc32", return_value=7):
            nets = ipv6.lan_networks(["a", "b", "c"], PREFIX)
        self.assertEqual(len(set(nets.values())), 3)

    def test_router_address_is_one(self):
        net = ipaddress.IPv6Network("fd12:3456:789a:1::/64")
        self.assertEqual(ipv6.router_address(net), "fd12:3456:789a:1::1/64")


class RoutingSettingsTests(unittest.TestCase):

    def setUp(self):
        set_routing()

    def test_switching_on_chooses_a_prefix(self):
        ok, result = routing.validate_routing_settings({"ipv6": True})
        self.assertTrue(ok)
        self.assertIsNotNone(ipv6.parse_ula_prefix(result["ula_prefix"]))

    def test_existing_prefix_is_kept(self):
        set_routing(ula_prefix=PREFIX)
        ok, result = routing.validate_routing_settings({"ipv6": True})
        self.assertEqual(result["ula_prefix"], PREFIX)
        ok, result = routing.validate_routing_settings({"ipv6": False})
        self.assertEqual(result["ula_prefix"], PREFIX)

    def test_off_without_prefix_stays_empty(self):
        ok, result = routing.validate_routing_settings({"ipv6": False})
        self.assertTrue(ok)
        self.assertEqual(result["ula_prefix"], "")

    def test_invalid_values_are_refused(self):
        self.assertFalse(routing.validate_routing_settings({"ipv6": "yes"})[0])
        self.assertFalse(routing.validate_routing_settings({"ula_prefix": "2001:db8::/48"})[0])
        self.assertFalse(routing.validate_routing_settings({"ula_prefix": 5})[0])


class AddressTests(unittest.TestCase):

    def setUp(self):
        set_routing(ipv6=True, ula_prefix=PREFIX)
        config.save_running(ipv6.APPLIED, {})
        self.address = ipv6.router_address(ipv6.lan_networks(["br0"], PREFIX)["br0"])

    def sync(self, fake, served=("br0",)):
        with fake.patch(), mock.patch.object(ipv6, "served_interfaces", return_value=list(served)):
            return ipv6.sync_lan_addresses()

    def test_missing_address_is_added(self):
        fake = FakeSystem()
        self.assertTrue(self.sync(fake)[0])
        self.assertIn(["ip", "-6", "addr", "replace", self.address, "dev", "br0", "nodad"], fake.commands)

    def test_present_address_needs_no_command(self):
        fake = FakeSystem({"br0": [self.address]})
        config.save_running(ipv6.APPLIED, {"br0": self.address})
        self.sync(fake)
        self.assertEqual(fake.commands, [])

    def test_stale_address_is_removed(self):
        config.save_running(ipv6.APPLIED, {"wlan9": "fd12:3456:789a:9::1/64"})
        fake = FakeSystem({"br0": [self.address]})
        self.sync(fake)
        self.assertIn(["ip", "-6", "addr", "del", "fd12:3456:789a:9::1/64", "dev", "wlan9"], fake.commands)
        self.assertEqual(config.load_running(ipv6.APPLIED), {"br0": self.address})

    def test_switching_off_removes_everything(self):
        config.save_running(ipv6.APPLIED, {"br0": self.address})
        set_routing(ipv6=False, ula_prefix=PREFIX)
        fake = FakeSystem({"br0": [self.address]})
        self.sync(fake)
        self.assertIn(["ip", "-6", "addr", "del", self.address, "dev", "br0"], fake.commands)
        self.assertEqual(config.load_running(ipv6.APPLIED), {})


class ForwardingAndNatTests(unittest.TestCase):

    def test_wan_keeps_router_advertisements(self):
        fake = FakeSystem()
        with fake.patch(), mock.patch.object(ipv6, "_read_proc", return_value="1"):
            ipv6.set_forwarding(True, "wwan0")
        self.assertEqual(fake.commands[0], ["sysctl", "-w", "net.ipv6.conf.wwan0.accept_ra=2"])
        self.assertEqual(fake.commands[-1], ["sysctl", "-w", "net.ipv6.conf.all.forwarding=1"])

    def test_networkmanager_wan_is_left_alone(self):
        fake = FakeSystem()
        with fake.patch(), mock.patch.object(ipv6, "_read_proc", return_value="0"):
            ipv6.set_forwarding(True, "eth0")
        self.assertEqual(fake.commands, [["sysctl", "-w", "net.ipv6.conf.all.forwarding=1"]])

    def test_nat66_masquerades_the_prefix_on_the_wan(self):
        fake = FakeSystem()
        with fake.patch():
            self.assertTrue(ipv6.apply_nat(True, "wwan0", PREFIX)[0])
            self.assertTrue(ipv6.is_nat_active("wwan0", PREFIX))
        self.assertIn(["ip6tables", "-t", "nat", "-A", ipv6.NAT_CHAIN,
                       "-s", PREFIX, "-o", "wwan0", "-j", "MASQUERADE"], fake.commands)

    def test_nat66_off_leaves_the_chain_empty(self):
        fake = FakeSystem()
        with fake.patch():
            ipv6.apply_nat(False, "wwan0", PREFIX)
        self.assertFalse(any("MASQUERADE" in c for c in fake.commands))


class RouterAdvertisementTests(unittest.TestCase):

    SETTINGS = {
        "interfaces": {"br0": {**dhcp.DEFAULT_SCOPE, "enabled": True, "range_start": "192.168.1.100",
                               "range_end": "192.168.1.200"}},
        "domain": "", "reservations": []
    }

    def render(self):
        with mock.patch.multiple(dhcp, is_wan_interface=lambda n: False, effective_interface=lambda n: n,
                                 get_interface_network=lambda n: ipaddress.IPv4Interface("192.168.1.1/24")):
            return dhcp.render_dnsmasq_config(self.SETTINGS)

    def test_on_adds_router_advertisements(self):
        set_routing(ipv6=True, ula_prefix=PREFIX)
        text = self.render()
        self.assertIn("enable-ra\n", text)
        self.assertIn("dhcp-range=set:chardsoft-br0,::,constructor:br0,ra-stateless,64,12h", text)

    def test_off_has_none(self):
        set_routing(ipv6=False, ula_prefix=PREFIX)
        text = self.render()
        self.assertNotIn("enable-ra", text)
        self.assertNotIn("constructor", text)


class GuardTests(unittest.TestCase):

    def test_dhcpv6_is_an_essential_rule(self):
        with mock.patch.multiple(firewall, get_wan_interface=lambda: "wwan0",
                                 get_lan_interfaces=lambda: ["br0"]):
            args = [r["args"] for r in firewall.get_essential_rules()]
        self.assertTrue(any("547" in a and "udp" in a for a in args))

    def kill_switch_rules(self, profile):
        commands = []

        def run_command(cmd):
            commands.append(cmd)
            return ("-C" not in cmd), ""

        with mock.patch.multiple(vpn_profiles, run_command=run_command, privileged=lambda c: c,
                                 engaged_profile=lambda p=None: profile,
                                 get_wan_interface=lambda: "wwan0"), \
             mock.patch.object(vpn_profiles.shutil, "which", side_effect=lambda n: n):
            vpn_profiles.apply_kill_switch([])

        return [c for c in commands if "REJECT" in c]

    def test_full_tunnel_blocks_lan_ipv6_around_the_vpn(self):
        rejects = self.kill_switch_rules({"name": "x", "full_tunnel": True, "kill_switch": False})
        self.assertTrue(rejects)
        self.assertTrue(all(c[0] == "ip6tables" for c in rejects))

    def test_kill_switch_blocks_both(self):
        rejects = self.kill_switch_rules({"name": "x", "full_tunnel": True, "kill_switch": True})
        self.assertEqual({c[0] for c in rejects}, {"iptables", "ip6tables"})

    def test_split_tunnel_blocks_nothing(self):
        self.assertEqual(self.kill_switch_rules({"name": "x", "full_tunnel": False}), [])


class WanAddressTests(unittest.TestCase):
    """The Modem page and the Firewall page show the same, stable address."""

    LINES = [
        "4: wwan0    inet 192.0.0.2/29 scope global wwan0",
        "4: wwan0    inet6 2a01:598:9005:ee01:5546:63fd:6fb5:4ea1/64 scope global temporary dynamic",
        "4: wwan0    inet6 2a01:598:9005:ee01:1111:2222:3333:4444/64 scope global deprecated dynamic",
        "4: wwan0    inet6 2a01:598:9005:ee01:b52c:272e:5b7a:d8bb/64 scope global dynamic mngtmpaddr",
    ]

    def test_stable_address_wins(self):
        from services.network import pick_ipv6
        self.assertEqual(pick_ipv6(self.LINES), "2a01:598:9005:ee01:b52c:272e:5b7a:d8bb")

    def test_only_temporary_falls_back(self):
        from services.network import pick_ipv6
        self.assertEqual(pick_ipv6(self.LINES[:2]), "2a01:598:9005:ee01:5546:63fd:6fb5:4ea1")
        self.assertIsNone(pick_ipv6(self.LINES[:1]))

    def test_both_pages_agree(self):
        from services import modem
        with mock.patch.object(modem, "run", return_value="\n".join(self.LINES)):
            v4, v6 = modem.interface_addresses("wwan0")
        with mock.patch.object(ipv6, "run", return_value="\n".join(self.LINES[1:])), \
             mock.patch.object(ipv6, "get_wan_interface", return_value="wwan0"), \
             mock.patch.object(ipv6, "is_forwarding_enabled", return_value=False), \
             mock.patch.object(ipv6, "which", return_value=None):
            status = ipv6.get_status()
        self.assertEqual(v4, "192.0.0.2")
        self.assertEqual(status["wan_address"], v6)


class SignalTests(unittest.TestCase):

    def test_placeholder_values_fall_back_to_lte(self):
        from services import modem
        text = ("  LTE |  rsrp: -95.00 dBm\n      |  rsrq: -11.00 dB\n      |  s/n: 7.00 dB\n"
                "  5G  |  rsrp: -32768.00 dBm\n      |  rsrq: -32768.00 dB\n      |  s/n: -3276.80 dB\n")
        self.assertEqual(modem.signal_values(text), {"rsrp": "-95.00", "rsrq": "-11.00", "sinr": "7.00"})

    def test_real_5g_values_win(self):
        from services import modem
        text = ("  LTE |  rsrp: -95.00 dBm\n  5G  |  rsrp: -88.00 dBm\n")
        self.assertEqual(modem.signal_values(text)["rsrp"], "-88.00")

    def test_only_placeholders_give_nothing(self):
        from services import modem
        self.assertEqual(modem.signal_values("  5G  |  rsrp: -32768.00 dBm\n"), {})


if __name__ == "__main__":
    unittest.main()
