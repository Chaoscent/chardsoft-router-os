"""
Core logic without hardware: safe apply (rollback, confirmation,
dependents), the dashboard layout, firewall rules, DHCP validation and
the dnsmasq config.

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

for name, folder in (("CHAOS_DEFAULTS_DIR", "etc"), ("CHAOS_STATE_DIR", "var"), ("CHAOS_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHAOS_SKIP_STARTUP", "1")

from services import areas, config, dashboard_layout, dhcp, firewall, transaction  # noqa: E402
from services.areas import Area  # noqa: E402


# -------------------------------------------------------------------
# Safe apply
# -------------------------------------------------------------------

class FakeArea:
    """An area whose apply records what was running and can be told to fail."""

    def __init__(self, name, confirm=False, baseline=None, dependents=()):
        self.name = name
        self.applied = []
        self.fail_on = None
        self.verify_fails_on = None
        self.raise_on = None
        self.area = Area(name, name.title(), apply=self.apply, verify=self.verify,
                         baseline=(lambda: baseline) if baseline is not None else None,
                         confirm=confirm, dependents=dependents)

    def running(self):
        return config.load_running(self.name, None)

    def apply(self):
        data = self.running()
        self.applied.append(data)
        if self.raise_on is not None and data == self.raise_on:
            raise RuntimeError("boom")
        if self.fail_on is not None and data == self.fail_on:
            return False, "apply failed"
        return True, "applied"

    def verify(self):
        if self.verify_fails_on is not None and self.running() == self.verify_fails_on:
            return False, "verify failed"
        return True, "OK"


class SafeApplyTests(unittest.TestCase):

    counter = 0

    def setUp(self):
        transaction._clear_pending()
        SafeApplyTests.counter += 1
        # A fresh area name per test: the sandbox keeps files between tests.
        self.fake = FakeArea(f"area{self.counter}", baseline={"v": 0})
        self.other = FakeArea(f"other{self.counter}", confirm=True, baseline={"v": 0})
        self.areas = {a.name: a.area for a in (self.fake, self.other)}
        patcher = mock.patch.object(transaction, "get_area", side_effect=lambda n: self.areas[n])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(transaction._clear_pending)

    def test_success_is_committed(self):
        outcome = transaction.change(self.fake.name, {"v": 1})
        self.assertTrue(outcome["success"])
        self.assertIsNone(outcome["pending"])
        self.assertEqual(config.load_persistent(self.fake.name), {"v": 1})

    def test_first_change_records_the_baseline(self):
        self.fake.fail_on = {"v": 1}
        transaction.change(self.fake.name, {"v": 1})
        self.assertEqual(config.load_persistent(self.fake.name), {"v": 0})
        self.assertEqual(self.fake.running(), {"v": 0})

    def test_failed_apply_rolls_back(self):
        transaction.change(self.fake.name, {"v": 1})
        self.fake.fail_on = {"v": 2}
        outcome = transaction.change(self.fake.name, {"v": 2})
        self.assertFalse(outcome["success"])
        self.assertIn("Previous settings restored", outcome["message"])
        self.assertEqual(self.fake.running(), {"v": 1})
        # The old settings were applied again.
        self.assertEqual(self.fake.applied[-1], {"v": 1})

    def test_failed_verify_rolls_back(self):
        transaction.change(self.fake.name, {"v": 1})
        self.fake.verify_fails_on = {"v": 2}
        outcome = transaction.change(self.fake.name, {"v": 2})
        self.assertFalse(outcome["success"])
        self.assertIn("verify failed", outcome["message"])
        self.assertEqual(self.fake.running(), {"v": 1})

    def test_exception_rolls_back(self):
        transaction.change(self.fake.name, {"v": 1})
        self.fake.raise_on = {"v": 2}
        outcome = transaction.change(self.fake.name, {"v": 2})
        self.assertFalse(outcome["success"])
        self.assertIn("boom", outcome["message"])
        self.assertEqual(self.fake.running(), {"v": 1})

    def test_confirmed_change_waits_then_commits(self):
        outcome = transaction.change(self.other.name, {"v": 1})
        self.assertTrue(outcome["success"])
        self.assertEqual(outcome["pending"]["areas"][0]["name"], self.other.name)
        self.assertEqual(config.load_persistent(self.other.name), {"v": 0})
        transaction.confirm_pending()
        self.assertEqual(config.load_persistent(self.other.name), {"v": 1})
        self.assertIsNone(transaction.get_pending())

    def test_revert_restores_the_last_good_settings(self):
        transaction.change(self.other.name, {"v": 1})
        transaction.revert_pending()
        self.assertEqual(self.other.running(), {"v": 0})
        self.assertEqual(self.other.applied[-1], {"v": 0})

    def test_expired_deadline_reverts(self):
        transaction.change(self.other.name, {"v": 1})
        pending = config.load_running(transaction.PENDING)
        pending["deadline"] = 0
        config.save_running(transaction.PENDING, pending)
        self.assertIsNone(transaction.get_pending())
        self.assertEqual(self.other.running(), {"v": 0})

    def test_restart_reverts_unconfirmed_changes(self):
        transaction.change(self.other.name, {"v": 1})
        transaction.recover_pending()
        self.assertEqual(self.other.running(), {"v": 0})

    def test_unconfirmed_area_refuses_a_direct_change(self):
        transaction.change(self.other.name, {"v": 1})
        outcome = transaction.change(self.other.name, {"v": 2}, confirm=False)
        self.assertFalse(outcome["success"])
        self.assertIn("unconfirmed", outcome["message"])

    def test_dependents_are_reapplied_only_when_configured(self):
        dependent = FakeArea(f"dep{self.counter}")
        self.areas[dependent.name] = dependent.area
        self.fake.area.dependents = (dependent.name,)
        transaction.change(self.fake.name, {"v": 1})
        self.assertEqual(dependent.applied, [])
        config.save_running(dependent.name, {"d": 1})
        transaction.change(self.fake.name, {"v": 2})
        self.assertEqual(dependent.applied, [{"d": 1}])

    def test_failing_dependent_fails_the_change(self):
        dependent = FakeArea(f"dep{self.counter}")
        self.areas[dependent.name] = dependent.area
        self.fake.area.dependents = (dependent.name,)
        transaction.change(self.fake.name, {"v": 1})
        config.save_running(dependent.name, {"d": 1})
        dependent.fail_on = {"d": 1}
        outcome = transaction.change(self.fake.name, {"v": 2})
        self.assertFalse(outcome["success"])
        self.assertTrue(outcome["message"].startswith(dependent.area.label))
        self.assertEqual(self.fake.running(), {"v": 1})


class AreaRegistryTests(unittest.TestCase):

    def test_dependents_exist_and_come_later(self):
        order = list(areas.AREAS)
        for name, area in areas.AREAS.items():
            for dependent in area.dependents:
                self.assertIn(dependent, areas.AREAS, f"{name} -> {dependent}")
                self.assertGreater(order.index(dependent), order.index(name), f"{name} -> {dependent}")

    def test_dhcp_rewrites_dns_and_routing_rewrites_dhcp(self):
        self.assertIn("dns", areas.AREAS["dhcp"].dependents)
        self.assertIn("dhcp", areas.AREAS["routing"].dependents)

    def test_areas_that_can_lock_you_out_need_confirmation(self):
        for name in ("network", "firewall", "dhcp", "wifi", "blocked_devices"):
            self.assertTrue(areas.AREAS[name].confirm, name)


# -------------------------------------------------------------------
# Dashboard layout
# -------------------------------------------------------------------

class LayoutTests(unittest.TestCase):

    def valid(self, **change):
        layout = dashboard_layout.default_layout()
        # The WAN graph: the only widget with an interface.
        next(w for w in layout if w["type"] == "traffic").update(change)
        return layout

    def test_default_is_valid(self):
        self.assertEqual(dashboard_layout.validate_layout(dashboard_layout.default_layout()),
                         dashboard_layout.default_layout())

    def test_rejects(self):
        cases = [
            [],
            "nope",
            self.valid(type="clock"),
            self.valid(width=5),
            self.valid(width=True),
            self.valid(hidden="no"),
            self.valid(id="Bad Id"),
            self.valid(id="traffic\n"),
            self.valid(interface="eth0\n"),
            self.valid(interface="x" * 16),
            self.valid(interface="eth0; reboot"),
            [{"id": "a", "type": "summary"}, {"id": "b", "type": "summary"}],
            [{"id": "a", "type": "traffic"}, {"id": "a", "type": "traffic"}],
            [{"id": f"t{i}", "type": "traffic"} for i in range(17)],
            [dict(w, hidden=True) for w in dashboard_layout.default_layout()],
        ]
        for layout in cases:
            with self.assertRaises(ValueError, msg=repr(layout)[:80]):
                dashboard_layout.validate_layout(layout)

    def test_unknown_keys_are_dropped(self):
        layout = self.valid(evil="<script>")
        self.assertNotIn("evil", dashboard_layout.validate_layout(layout)[1])

    def test_several_graphs_are_allowed(self):
        layout = [{"id": f"t{i}", "type": "traffic", "interface": "eth0"} for i in range(4)]
        self.assertEqual(len(dashboard_layout.validate_layout(layout)), 4)

    def test_broken_saved_layout_falls_back_to_default(self):
        self.assertEqual(dashboard_layout.get_layout({"layout": "garbage"}), dashboard_layout.default_layout())


# -------------------------------------------------------------------
# Firewall rules
# -------------------------------------------------------------------

class FirewallRuleTests(unittest.TestCase):

    def build(self, **data):
        with mock.patch.object(firewall, "get_interfaces", return_value=["eth0", "br0"]):
            return firewall.build_rule({"action": "allow", **data})

    def test_valid_rule(self):
        ok, args = self.build(port="8080", proto="tcp", interface="br0", comment="Web app")
        self.assertTrue(ok)
        self.assertEqual(args, ["allow", "in", "on", "br0", "from", "any", "to", "any",
                                "port", "8080", "proto", "tcp", "comment", "Web app"])

    def test_addresses_and_ranges(self):
        self.assertTrue(self.build(**{"from": "192.168.1.0/24"})[0])
        self.assertTrue(self.build(**{"from": "fd00::/8"})[0])
        self.assertTrue(self.build(port="1000:2000", proto="udp")[0])

    def test_rejects(self):
        cases = [
            {"action": "drop", "port": "80"},
            {"direction": "sideways", "port": "80"},
            {"action": "limit", "direction": "out", "port": "22"},
            {"interface": "wlan9", "port": "80"},
            {"proto": "icmp", "port": "80"},
            {"port": "0"},
            {"port": "65536"},
            {"port": "2000:1000", "proto": "tcp"},
            {"port": "80 443"},
            {"port": "1000:2000"},
            {"from": "not-an-ip", "port": "80"},
            {"to": "10.0.0.0/33", "port": "80"},
            {"comment": "a; rm -rf /", "port": "80"},
            {"comment": "x" * 65, "port": "80"},
            {},
        ]
        for case in cases:
            ok, message = self.build(**case)
            self.assertFalse(ok, case)
            self.assertIsInstance(message, str)

    def test_essential_rules_stay_on_the_lan(self):
        with mock.patch.multiple(firewall, get_wan_interface=lambda: "wwan0",
                                 get_lan_interfaces=lambda: ["br0", "wlan1"]):
            rules = firewall.get_essential_rules()
        for rule in rules:
            args = rule["args"]
            self.assertNotEqual(args[args.index("on") + 1], "wwan0", rule["description"])
            self.assertTrue(args[-1].startswith(firewall.ESSENTIAL_PREFIX))


# -------------------------------------------------------------------
# DHCP
# -------------------------------------------------------------------

NETWORK = ipaddress.IPv4Interface("192.168.1.1/24")


class DhcpTests(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch.multiple(
            dhcp, is_lan_capable=lambda n: n in ("eth0", "br0"), is_wan_interface=lambda n: False,
            effective_interface=lambda n: n, get_interface_network=lambda n: NETWORK)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = {"interfaces": {}, "domain": "", "reservations": []}

    def scope(self, **data):
        return dhcp.validate_scope({"interface": "eth0", "enabled": True, "range_start": "192.168.1.100",
                                    "range_end": "192.168.1.200", **data}, self.settings)

    def test_valid_scope(self):
        ok, scope = self.scope(dns=["1.1.1.1"])
        self.assertTrue(ok, scope)
        self.assertEqual(scope["dns"], ["1.1.1.1"])

    def test_scope_rejects(self):
        cases = [
            {"interface": "wwan0"},
            {"lease_time": "5y"},
            {"range_start": "192.168.2.10"},
            {"range_start": "192.168.1.200", "range_end": "192.168.1.100"},
            {"range_start": "192.168.1.1"},
            {"dns": ["1.1.1.1", "8.8.8.8", "9.9.9.9"]},
            {"dns": ["1.1.1.1\nscript=/x"]},
            {"gateway": "router"},
        ]
        for case in cases:
            ok, message = self.scope(**case)
            self.assertFalse(ok, case)

    def test_reservations(self):
        ok, r = dhcp.validate_reservation({"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.50", "hostname": "nas"}, NETWORK)
        self.assertTrue(ok)
        self.assertEqual(r["mac"], "AA:BB:CC:DD:EE:FF")
        for bad in (
            {"mac": "aa:bb:cc:dd:ee", "ip": "192.168.1.50"},
            {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.2.50"},
            {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.1"},
            {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.50", "hostname": "nas\ndhcp-script=/x"},
            {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.50", "hostname": "-nas"},
        ):
            self.assertFalse(dhcp.validate_reservation(bad, NETWORK)[0], bad)

    def test_config_has_no_injected_lines(self):
        ok, result = dhcp.validate_dhcp_settings({"domain": "lan\nconf-file=/etc/shadow"})
        self.assertFalse(ok)

    def test_rendered_config(self):
        settings = {
            "interfaces": {"eth0": {**dhcp.DEFAULT_SCOPE, "enabled": True, "range_start": "192.168.1.100",
                                    "range_end": "192.168.1.200", "dns": ["1.1.1.1"]}},
            "domain": "home.lan",
            "reservations": [{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.50", "hostname": "nas"}]
        }
        text = dhcp.render_dnsmasq_config(settings)
        self.assertIn("interface=eth0\n", text)
        self.assertIn("dhcp-range=set:chaos-eth0,192.168.1.100,192.168.1.200,255.255.255.0,12h", text)
        self.assertIn("dhcp-option=tag:chaos-eth0,option:router,192.168.1.1", text)
        self.assertIn("dhcp-option=tag:chaos-eth0,option:dns-server,1.1.1.1", text)
        self.assertIn("domain=home.lan", text)
        self.assertIn("dhcp-host=AA:BB:CC:DD:EE:FF,nas,192.168.1.50", text)


if __name__ == "__main__":
    unittest.main()
