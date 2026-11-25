"""
The Routes page (services/routes.py): validation of static routes,
parsing `ip route`, and applying with `proto 77` only. `ip` is faked.

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

from services import areas, backup, config, routes  # noqa: E402


def route(**values):
    return {"id": "r1", "destination": "10.20.0.0/16", "gateway": "192.168.1.5", "interface": "",
            "metric": 0, "comment": "", "enabled": True, **values}


class ValidateTests(unittest.TestCase):

    def test_good_routes_are_cleaned(self):
        ok, clean = routes.validate_settings({"routes": [
            route(destination="10.20.3.4/16", metric="50", comment=" Office "),
            route(id="r2", destination="2001:db8:5::/48", gateway="fe80::1", interface="eth0"),
            route(id="r3", destination="10.99.0.1", gateway="", interface="wg0")]})
        self.assertTrue(ok, clean)
        self.assertEqual(clean["routes"][0]["destination"], "10.20.0.0/16")
        self.assertEqual(clean["routes"][0]["metric"], 50)
        self.assertEqual(clean["routes"][0]["comment"], "Office")
        self.assertEqual(clean["routes"][2]["destination"], "10.99.0.1/32")

    def test_bad_routes(self):
        cases = [
            route(destination="0.0.0.0/0"),
            route(destination="::/0", gateway="fe80::1"),
            route(destination="banana"),
            route(destination="127.0.0.0/8"),
            route(destination="224.0.0.0/4"),
            route(gateway="2001:db8::1"),
            route(gateway="10.20.0.1"),
            route(gateway="", interface=""),
            route(interface="eth0; reboot"),
            route(metric=10000),
            route(metric=True),
            route(enabled="yes"),
            route(comment="x" * 65),
        ]
        for case in cases:
            self.assertFalse(routes.validate_settings({"routes": [case]})[0], case)

    def test_duplicates_and_limits(self):
        self.assertFalse(routes.validate_settings({"routes": [route(), route(id="r2")]})[0])
        self.assertTrue(routes.validate_settings({"routes": [route(), route(id="r2", metric=5)]})[0])
        self.assertFalse(routes.validate_settings({"routes": [route(id=f"r{i}", metric=i) for i in range(65)]})[0])
        self.assertFalse(routes.validate_settings({"routes": "x"})[0])

    def test_area_and_backup(self):
        self.assertTrue(areas.AREAS["static_routes"].confirm)
        self.assertIn("static_routes", backup.get_validators())


class ParseTests(unittest.TestCase):

    def test_lines(self):
        r = routes.parse_route("default via 192.168.0.1 dev eth0 proto dhcp src 192.168.0.243 metric 100")
        self.assertEqual((r["destination"], r["gateway"], r["interface"], r["origin"], r["metric"], r["table"]),
                         ("default", "192.168.0.1", "eth0", "dhcp", 100, "main"))
        r = routes.parse_route("unreachable default table 4242 proto boot metric 4000")
        self.assertEqual((r["type"], r["table"], r["metric"]), ("unreachable", "4242", 4000))
        r = routes.parse_route("10.43.0.0/24 dev wlan1 proto kernel scope link src 10.43.0.1 linkdown")
        self.assertTrue(r["down"])
        self.assertEqual(routes.describe(r)["origin_label"], "Connected")
        self.assertIsNone(routes.parse_route(""))

    def test_explain(self):
        self.assertIn("not on any network", routes.explain("Error: Nexthop has invalid gateway."))
        self.assertIn("does not exist", routes.explain('Cannot find device "wg0"'))


class FakeIp:

    def __init__(self):
        # (family, destination, metric) -> line as the kernel prints it
        self.table = {}
        self.commands = []
        self.unreachable = set()

    def run(self, cmd):
        family = cmd[1]
        if cmd[2:6] == ["route", "show", "proto", routes.PROTO]:
            return "\n".join(line for (f, _d, _m), line in self.table.items() if f == family)
        return ""

    def run_command(self, cmd):
        cmd = [c for c in cmd if c not in ("sudo", "-n")]
        self.commands.append(cmd)
        family, action, dest = cmd[1], cmd[3], cmd[4]
        metric = int(cmd[cmd.index("metric") + 1])
        if action == "replace":
            if dest in self.unreachable:
                return False, "Error: Nexthop has invalid gateway."
            shown = dest[:-3] if dest.endswith("/32") else dest
            args = cmd[5:cmd.index("metric")]
            if "dev" not in args:
                args += ["dev", "eth0"]
            self.table[(family, dest, metric)] = " ".join([shown, *args, "metric", str(metric)])
        elif action == "del":
            self.table.pop((family, dest, metric), None)
        return True, ""

    def patch(self):
        return mock.patch.multiple(routes, run=self.run, run_command=self.run_command, privileged=lambda c: c)


def save(*items):
    ok, clean = routes.validate_settings({"routes": list(items)})
    assert ok, clean
    config.save_running(routes.SETTINGS, clean)


class SyncTests(unittest.TestCase):

    def test_adds_once_and_never_touches_other_routes(self):
        ip = FakeIp()
        save(route(), route(id="r2", destination="10.99.0.5", gateway="192.168.1.6"),
             route(id="r3", destination="2001:db8:5::/48", gateway="fe80::1", interface="eth0"))
        with ip.patch():
            self.assertEqual(routes.sync(), (True, "Routes applied."))
            ip.commands.clear()
            routes.sync()
        self.assertEqual(ip.commands, [])
        self.assertEqual(len(ip.table), 3)

    def test_changed_and_removed(self):
        ip = FakeIp()
        save(route(), route(id="r2", destination="10.30.0.0/16"))
        with ip.patch():
            routes.sync()
            save(route(gateway="192.168.1.9"))
            routes.sync()
        self.assertEqual(list(ip.table.values()), ["10.20.0.0/16 via 192.168.1.9 dev eth0 metric 0"])

    def test_off_and_waiting(self):
        ip = FakeIp()
        ip.unreachable.add("10.30.0.0/16")
        save(route(enabled=False), route(id="r2", destination="10.30.0.0/16"))
        with ip.patch():
            ok, message = routes.sync()
            status = routes.get_status()
        self.assertTrue(ok)
        self.assertIn("1 route waiting", message)
        states = {r["destination"]: (r["state"], r["detail"]) for r in status["routes"]}
        self.assertEqual(states["10.20.0.0/16"][0], "off")
        self.assertEqual(states["10.30.0.0/16"][0], "waiting")
        self.assertIn("not on any network", states["10.30.0.0/16"][1])
        self.assertEqual(ip.table, {})


if __name__ == "__main__":
    unittest.main()
