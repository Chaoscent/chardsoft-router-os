"""
Mobile data used per billing period, the data limit and its block
(services/data_usage.py), and the dashboard's internet check
(services/internet_check.py). Counters and commands are faked.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import datetime
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

from services import config, dashboard_layout, data_usage as du, internet_check as ic, qr, schema  # noqa: E402

GB = du.GB
D = datetime.date


class PeriodTests(unittest.TestCase):

    def test_period_start(self):
        self.assertEqual(du.period_start(D(2026, 11, 24), 1), D(2026, 11, 1))
        self.assertEqual(du.period_start(D(2026, 11, 14), 15), D(2026, 10, 15))
        self.assertEqual(du.period_start(D(2026, 1, 3), 15), D(2025, 12, 15))
        self.assertEqual(du.period_start(D(2026, 11, 15), 15), D(2026, 11, 15))

    def test_next_period(self):
        self.assertEqual(du.next_period_start(D(2026, 12, 15)), D(2027, 1, 15))
        self.assertEqual(du.next_period_start(D(2026, 1, 28)), D(2026, 2, 28))


class ValidateTests(unittest.TestCase):

    def test_good(self):
        ok, clean = du.validate_settings({"limit_gb": "20", "billing_day": 15, "warn_percent": 90, "block_at_limit": True})
        self.assertTrue(ok)
        self.assertEqual(clean, {"limit_gb": 20, "billing_day": 15, "warn_percent": 90, "block_at_limit": True})
        self.assertEqual(du.validate_settings({"limit_gb": 2.5})[1]["limit_gb"], 2.5)

    def test_bad(self):
        for data in ({"limit_gb": -1}, {"limit_gb": "x"}, {"billing_day": 31}, {"billing_day": True},
                     {"warn_percent": 100}, {"block_at_limit": "yes"}):
            self.assertFalse(du.validate_settings(data)[0], data)


class CountTests(unittest.TestCase):

    def test_counts_both_directions_and_survives_counter_resets(self):
        state = du.empty_state(D(2026, 11, 1))
        today = D(2026, 11, 24)
        du.add_sample(state, {"wwan0": (1000, 500)}, today)      # baseline only
        self.assertEqual(du.used_bytes(state), 0)
        du.add_sample(state, {"wwan0": (3000, 1500)}, today)
        self.assertEqual(du.used_bytes(state), 3000)
        # Modem restarted: the counters begin again.
        du.add_sample(state, {"wwan0": (200, 100)}, today)
        self.assertEqual(du.used_bytes(state), 3300)
        self.assertEqual(state["today"]["bytes"], 3300)

    def test_interface_gone_and_back(self):
        state = du.empty_state(D(2026, 11, 1))
        today = D(2026, 11, 24)
        du.add_sample(state, {"wwan0": (0, 0)}, today)
        du.add_sample(state, {"wwan0": (100, 0)}, today)
        du.add_sample(state, {}, today)
        du.add_sample(state, {"wwan0": (50, 0)}, today)          # new baseline
        self.assertEqual(du.used_bytes(state), 100)


class FakeIptables:

    def __init__(self):
        self.chains = {}
        self.forward = []

    def run_command(self, cmd):
        args = [c for c in cmd if c not in ("sudo", "-n")][1:]
        op, chain = args[0], args[1]
        rule = args[2:]
        if op == "-N":
            self.chains.setdefault(chain, [])
        elif op == "-F":
            self.chains[chain] = []
        elif op == "-X":
            self.chains.pop(chain, None)
        elif op == "-C":
            rules = self.forward if chain == "FORWARD" else self.chains.get(chain, [])
            return (rule in rules), ""
        elif op == "-A":
            self.chains[chain].append(rule)
        elif op == "-I":
            self.forward.insert(0, rule[1:])
        elif op == "-D":
            if rule in self.forward:
                self.forward.remove(rule)
                return True, ""
            return False, ""
        return True, ""


class LimitTests(unittest.TestCase):

    def setUp(self):
        du._state = du.empty_state(du.period_start(D.today(), 1))
        du._blocked = None
        self.ipt = FakeIptables()
        self.counters = {"wwan0": (0, 0)}
        self.events = []
        self.patches = mock.patch.multiple(
            du, run_command=self.ipt.run_command, privileged=lambda c: c,
            _tools=lambda: ["iptables"], read_counters=lambda: dict(self.counters),
            save_persistent=lambda *a, **k: None,
            log_event=lambda *a, **k: self.events.append((a[1], k.get("level", "info"))))
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def settings(self, **values):
        config.save_running(du.SETTINGS, {**du.DEFAULT_SETTINGS, **values})

    def use(self, gigabytes):
        self.counters = {"wwan0": (int(gigabytes * GB), 0)}
        du.sample()

    def test_warns_once_then_blocks(self):
        self.settings(limit_gb=10, block_at_limit=True)
        du.sample()
        self.use(8.5)
        self.use(8.6)
        self.assertEqual(len(self.events), 1)
        self.assertIn("80%", self.events[0][0])
        self.assertEqual(self.ipt.forward, [])
        self.use(10.1)
        self.assertEqual(du.get_status()["level"], "blocked")
        self.assertEqual(self.ipt.forward, [["-j", du.CHAIN]])
        self.assertEqual(self.ipt.chains[du.CHAIN], [["-o", "wwan+", "-j", "REJECT"]])

    def test_resume_lifts_the_block(self):
        self.settings(limit_gb=1, block_at_limit=True)
        du.sample()
        self.use(2)
        du.resume()
        self.assertEqual(self.ipt.forward, [])
        self.assertNotIn(du.CHAIN, self.ipt.chains)
        status = du.get_status()
        self.assertEqual(status["level"], "limit")
        self.assertTrue(status["resumed"])

    def test_only_warn_never_blocks(self):
        self.settings(limit_gb=1, block_at_limit=False)
        du.sample()
        self.use(3)
        self.assertEqual(self.ipt.forward, [])
        self.assertEqual(du.get_status()["level"], "limit")

    def test_hook_comes_back_after_a_firewall_reload(self):
        self.settings(limit_gb=1, block_at_limit=True)
        du.sample()
        self.use(2)
        self.ipt.forward.clear()
        du.sample()
        self.assertEqual(self.ipt.forward, [["-j", du.CHAIN]])

    def test_correct_and_reset(self):
        self.settings(limit_gb=10)
        du.sample()
        self.use(1)
        self.assertTrue(du.set_used(4.25)[0])
        self.assertEqual(du.get_status()["used"], round(4.25 * GB))
        self.assertFalse(du.set_used("lots")[0])
        du.reset()
        self.assertEqual(du.get_status()["used"], 0)

    def test_new_period_starts_at_zero(self):
        self.settings(limit_gb=1, block_at_limit=True)
        du._state["period"] = "2000-01-01"
        du._state["rx"] = 5 * GB
        du.sample()
        self.assertEqual(du.get_status()["used"], 0)
        self.assertEqual(du._state["history"][0], {"period": "2000-01-01", "bytes": 5 * GB})
        self.assertEqual(self.ipt.forward, [])


class LayoutTests(unittest.TestCase):

    def test_new_widgets_are_valid(self):
        self.assertEqual([w["type"] for w in dashboard_layout.default_layout()][:3], ["summary", "status", "usage"])
        dashboard_layout.validate_layout(dashboard_layout.default_layout())

    def test_migration_adds_them_after_the_overview(self):
        files = {"dashboard": {"layout": [
            {"id": "system", "type": "system", "width": 12, "hidden": False},
            {"id": "summary", "type": "summary", "width": 12, "hidden": False}]}}
        schema.MIGRATIONS[1](files)
        schema.MIGRATIONS[1](files)  # safe twice
        self.assertEqual([w["id"] for w in files["dashboard"]["layout"]], ["system", "summary", "status", "usage"])
        dashboard_layout.validate_layout(files["dashboard"]["layout"])


class QrTests(unittest.TestCase):

    def test_payload(self):
        self.assertEqual(qr.wifi_payload('My;Net', 'p:w"1', "wpa2"), 'WIFI:T:WPA;S:My\;Net;P:p\\:w\\"1;;')
        self.assertEqual(qr.wifi_payload("Cafe", "", "open", hidden=True), "WIFI:T:nopass;S:Cafe;H:true;;")
        self.assertTrue(qr.wifi_payload("A", "b", "wpa3").startswith("WIFI:T:SAE;"))

    def test_svg(self):
        svg = qr.svg("WIFI:T:WPA;S:Net;P:secret123;;")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('fill="#fff"', svg)


MODEM_OK = {"state": "ready", "lock": None, "connection": "activated", "carrier": "Telekom.de", "signal": 70}


class CheckTests(unittest.TestCase):

    def check(self, modem_data=MODEM_OK, v4=True, v6=True, dns=True, enabled=True,
              clat_wanted=False, clat_active=False, routed=True, usage_level="ok", lan_dns=True):
        usage = {"level": usage_level, "limit": GB, "percent": 50, "period_end": "2026-12-01"}
        with mock.patch.multiple(ic, ping=lambda f, t: v4 if f == "-4" else v6, resolves=lambda: dns,
                                 devices_resolve=lambda: lan_dns), \
             mock.patch.object(ic.network, "get_wan_interface", return_value="wwan0"), \
             mock.patch.object(ic.network, "get_ip", return_value="Unknown"), \
             mock.patch.object(ic.modem, "get_modem_data", return_value=modem_data), \
             mock.patch.object(ic.modem, "get_modem_settings", return_value={"enabled": enabled}), \
             mock.patch.object(ic.routing, "get_routing_settings", return_value={"forwarding": True, "nat": True}), \
             mock.patch.object(ic.data_usage, "get_status", return_value=usage), \
             mock.patch.object(ic.clat, "is_wanted", return_value=clat_wanted), \
             mock.patch.object(ic.clat, "is_active", return_value=clat_active), \
             mock.patch.object(ic.wan_routing, "is_wanted", return_value=True), \
             mock.patch.object(ic.wan_routing, "verify", return_value=routed):
            return ic.run_check()

    def test_all_good(self):
        result = self.check()
        self.assertTrue(result["online"])
        self.assertEqual(result["verdict"], "online")

    def test_devices_dns_without_answers(self):
        # The router's own lookups work, dnsmasq has no upstream servers.
        result = self.check(lan_dns=False)
        self.assertFalse(result["online"])
        self.assertEqual(result["verdict"], "dns")
        self.assertIn("for your devices", result["message"])
        self.assertEqual(result["action"]["href"], "#/dns")

    def test_devices_resolve_reads_dns_query(self):
        # dns_query's own answer format (the rcode by name).
        answers = {
            "NOERROR": {"rcode": "NOERROR", "answers": [{"type": "A", "value": "1.1.1.1", "ttl": 60}]},
            "REFUSED": {"rcode": "REFUSED", "answers": []},
            "timeout": {"rcode": None, "answers": [], "error": "timed out"},
        }
        from services import dhcp, dns
        for name, expected in (("NOERROR", True), ("REFUSED", False), ("timeout", False)):
            with mock.patch.object(dns, "dns_query", return_value=answers[name]), \
                 mock.patch.object(dns, "get_dns_settings", return_value={"enabled": True}), \
                 mock.patch.object(dhcp, "has_dnsmasq", return_value=True):
                self.assertIs(ic.devices_resolve(), expected, name)
        with mock.patch.object(dns, "get_dns_settings", return_value={"enabled": False}):
            self.assertIsNone(ic.devices_resolve())

    def test_no_dns_server_for_devices(self):
        self.assertEqual(self.check(lan_dns=None)["verdict"], "online")

    def test_mobile_data_off_offers_switching_it_on(self):
        result = self.check({**MODEM_OK, "connection": "deactivated"}, v4=False, v6=False, dns=False, enabled=False)
        self.assertEqual(result["verdict"], "modem")
        self.assertEqual(result["action"], {"kind": "fix", "fix": "mobile_on", "label": "Switch on mobile data"})

    def test_pin_links_to_the_modem_page(self):
        result = self.check({**MODEM_OK, "lock": "sim-pin", "connection": None}, v4=False, v6=False)
        self.assertEqual(result["action"]["href"], "#/modem")

    def test_stuck_modem_offers_a_restart(self):
        result = self.check({**MODEM_OK, "data_dropped": True}, v4=False, v6=False)
        self.assertEqual(result["action"]["fix"], "restart_modem")

    def test_ipv6_only_without_clat(self):
        result = self.check(v4=False, clat_wanted=True, clat_active=False)
        self.assertEqual(result["verdict"], "clat")
        self.assertEqual(result["action"]["fix"], "repair")

    def test_blocked_by_the_data_limit(self):
        result = self.check(usage_level="blocked")
        self.assertEqual(result["verdict"], "data_limit")
        self.assertEqual(result["action"]["fix"], "resume")

    def test_dns(self):
        result = self.check(dns=False)
        self.assertEqual(result["verdict"], "dns")
        self.assertEqual(result["action"]["href"], "#/dns")

    def test_unknown_fix(self):
        self.assertFalse(ic.fix("format_disk")[0])


if __name__ == "__main__":
    unittest.main()
