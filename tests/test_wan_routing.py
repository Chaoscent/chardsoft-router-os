"""
LAN traffic leaves through the WAN even when another interface has the
better default route (services/wan_routing.py). `ip` is faked; it
prints rules and routes the way the kernel does.

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

from services import config, wan_routing as wr  # noqa: E402


class FakeIp:

    def __init__(self, gateways=None):
        # Main-table default routes per family: {"-4": "default via ... dev wwan0 proto static metric 700"}
        self.gateways = gateways if gateways is not None else {
            "-4": ["default via 192.0.0.1 dev wwan0 proto static metric 700"],
            "-6": ["default via fe80::1 dev wwan0 proto static metric 700 pref medium"],
        }
        self.rules = {"-4": [], "-6": []}
        self.table = {"-4": [], "-6": []}
        self.commands = []

    def show(self, family, route):
        """How the kernel prints a route we added."""
        parts = route.split()
        if family == "-6":
            if parts[0] == "unreachable":
                parts[2:2] = ["dev", "lo"]
            parts += ["pref", "medium"]
        return " ".join(parts)

    def run(self, cmd):
        family = cmd[1]
        if cmd[2:4] == ["route", "show"] and "default" in cmd:
            dev = cmd[cmd.index("dev") + 1]
            return "\n".join(g for g in self.gateways.get(family, []) if f"dev {dev}" in g)
        if cmd[2:4] == ["rule", "show"]:
            return "\n".join(["0:\tfrom all lookup local"] + self.rules[family] + ["32766:\tfrom all lookup main"])
        if cmd[2:4] == ["route", "show"]:
            return "\n".join(self.show(family, r) for r in self.table[family])
        return ""

    def run_command(self, cmd):
        cmd = [c for c in cmd if c not in ("sudo", "-n")]
        self.commands.append(cmd)
        family, obj, action = cmd[1], cmd[2], cmd[3]
        args = cmd[4:]
        if obj == "rule":
            text = f"{args[1]}:\t{' '.join(args[2:])}"
            if action == "add":
                self.rules[family].append(text)
            elif action == "del":
                self.rules[family].remove(text)
        elif obj == "route":
            if action == "flush":
                self.table[family] = []
            else:
                route = " ".join(args[:-2])
                key = route.split(" metric ")[1] if " metric " in route else route
                self.table[family] = [r for r in self.table[family]
                                      if (r.split(" metric ")[1] if " metric " in r else r) != key]
                if action in ("add", "replace"):
                    self.table[family].append(route)
        return True, ""

    def patch(self, lans=("wlan1", "eth0"), wan="wwan0", full_tunnel=False, forwarding=True):
        config.save_running("routing", {"forwarding": forwarding})
        return mock.patch.multiple(
            wr, run=self.run, run_command=self.run_command, privileged=lambda c: c,
            get_wan_interface=lambda: wan, get_lan_interface_names=lambda: list(lans),
            full_tunnel_engaged=lambda: full_tunnel)


class LanRoutingTests(unittest.TestCase):

    def test_lan_traffic_gets_the_wan_table(self):
        ip = FakeIp()
        with ip.patch():
            self.assertEqual(wr.sync(), (True, "OK"))
            self.assertTrue(wr.verify())
        for family in ("-4", "-6"):
            rules = "\n".join(ip.rules[family])
            self.assertIn("9000:\tfrom all lookup main suppress_prefixlength 0", rules)
            self.assertIn("9001:\tfrom all iif wlan1 lookup 4242", rules)
            self.assertIn("9001:\tfrom all iif eth0 lookup 4242", rules)
        self.assertIn("default via 192.0.0.1 dev wwan0 metric 100", ip.table["-4"])
        self.assertIn("default via fe80::1 dev wwan0 metric 100", ip.table["-6"])
        self.assertIn("unreachable default metric 4000", ip.table["-4"])

    def test_second_sync_changes_nothing(self):
        ip = FakeIp()
        with ip.patch():
            wr.sync()
            ip.commands.clear()
            wr.sync()
        self.assertEqual(ip.commands, [])

    def test_new_gateway_replaces_the_route_without_a_flush(self):
        ip = FakeIp()
        with ip.patch():
            wr.sync()
            ip.gateways["-4"] = ["default via 10.20.30.1 dev wwan0 proto static metric 700"]
            ip.commands.clear()
            wr.sync()
        self.assertFalse(any("flush" in c for c in ip.commands))
        self.assertIn("default via 10.20.30.1 dev wwan0 metric 100", ip.table["-4"])
        self.assertNotIn("default via 192.0.0.1 dev wwan0 metric 100", ip.table["-4"])
        self.assertIn("unreachable default metric 4000", ip.table["-4"])

    def test_wan_down_leaves_unreachable(self):
        ip = FakeIp(gateways={})
        with ip.patch():
            wr.sync()
        self.assertEqual(ip.table["-4"], ["unreachable default metric 4000"])

    def test_full_tunnel_takes_over(self):
        ip = FakeIp()
        with ip.patch():
            wr.sync()
        with ip.patch(full_tunnel=True):
            wr.sync()
            self.assertTrue(wr.verify())
        self.assertEqual(ip.rules, {"-4": [], "-6": []})
        self.assertEqual(ip.table, {"-4": [], "-6": []})

    def test_forwarding_off_removes_it(self):
        ip = FakeIp()
        with ip.patch():
            wr.sync()
        with ip.patch(forwarding=False):
            wr.sync()
        self.assertEqual(ip.rules["-4"], [])

    def test_wan_is_never_a_lan(self):
        ip = FakeIp(gateways={"-4": ["default via 192.168.178.1 dev eth0 proto dhcp metric 100"]})
        with ip.patch(lans=("eth0", "wlan1"), wan="eth0"):
            wr.sync()
        self.assertNotIn("9001:\tfrom all iif eth0 lookup 4242", ip.rules["-4"])
        self.assertIn("default via 192.168.178.1 dev eth0 metric 100", ip.table["-4"])

    def test_route_normalization(self):
        self.assertEqual(wr.normalize_route("unreachable default dev lo proto kernel metric 4000 pref medium"),
                         "unreachable default metric 4000")
        self.assertEqual(wr.normalize_route("default via 192.0.0.1 dev wwan0 metric 100 linkdown"),
                         "default via 192.0.0.1 dev wwan0 metric 100")
        self.assertEqual(wr.normalize_route("default dev clat scope link metric 100"),
                         "default dev clat metric 100")


if __name__ == "__main__":
    unittest.main()
