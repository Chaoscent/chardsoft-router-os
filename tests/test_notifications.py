"""
The header's notifications (services/notifications.py) and the events
that feed them: a route that stops and works again, new devices.

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

from services import config, device_usage, logs, notifications as nf, routes  # noqa: E402


class NotificationTests(unittest.TestCase):

    def setUp(self):
        config.save_system(logs.EVENTS, [])
        config.save_system(nf.SEEN, {})

    def test_which_events(self):
        logs.log_event("auth", "admin logged in.")
        logs.log_event("auth", "Failed login for 'root'.", "warning")
        logs.log_event("modem", "Modem restarted automatically.", notify=True)
        logs.log_event("settings", "Revert failed.", "error")

        data = nf.get_notifications()

        self.assertEqual([i["message"] for i in data["items"]],
                         ["Revert failed.", "Modem restarted automatically.", "Failed login for 'root'."])
        self.assertEqual(data["unread"], 3)
        self.assertEqual(data["items"][1]["link"], "#/modem")
        self.assertEqual(data["items"][0]["link"], "#/logs")
        self.assertEqual(nf.unread_count(), 3)

    def test_mark_read(self):
        logs.log_event("data", "80% used.", "warning")
        newest = nf.get_notifications()["items"][0]["time"]

        self.assertTrue(nf.mark_read(newest))
        self.assertEqual(nf.unread_count(), 0)
        self.assertFalse(nf.get_notifications()["items"][0]["unread"])

        # Older marks never move "seen" back.
        nf.mark_read(newest - 100)
        self.assertEqual(nf.seen(), newest)
        self.assertFalse(nf.mark_read("soon"))

        logs.log_event("data", "Limit reached.", "warning")
        self.assertEqual(nf.unread_count(), 1)


class RouteEventTests(unittest.TestCase):

    def setUp(self):
        config.save_system(logs.EVENTS, [])
        routes._working.clear()
        routes._lost.clear()
        config.save_running(routes.SETTINGS, {"routes": [
            {"id": "r1", "destination": "10.20.0.0/16", "gateway": "192.168.1.5", "interface": "",
             "metric": 0, "enabled": True, "comment": ""}]})
        self.up = True
        self.have = {}

        def run(cmd):
            if "proto" in cmd and "show" in cmd and "-4" in cmd and self.have:
                return "10.20.0.0/16 via 192.168.1.5 dev eth0 proto 77 metric 0"
            return ""

        def command(cmd):
            if "replace" in cmd:
                if self.up:
                    self.have = {"x": 1}
                    return True, ""
                return False, "Error: Nexthop has invalid gateway."
            if "del" in cmd:
                self.have = {}
            return True, ""

        for p in (mock.patch.object(routes, "run", run), mock.patch.object(routes, "run_command", command),
                  mock.patch.object(routes, "privileged", lambda c: c)):
            p.start()
            self.addCleanup(p.stop)

    def messages(self):
        return [e["message"] for e in logs.get_events()]

    def test_waiting_at_first_is_quiet(self):
        self.up = False
        routes.sync()
        routes.sync()
        self.up = True
        routes.sync()
        self.assertEqual(self.messages(), [])

    def test_stop_and_back(self):
        routes.sync()
        self.up, self.have = False, {}
        routes.sync()
        routes.sync()
        self.up = True
        routes.sync()
        self.assertEqual(self.messages(), [
            "Your route to 10.20.0.0/16 works again.",
            "Your route to 10.20.0.0/16 stopped working: The gateway is not on any network the router is connected to.",
        ])
        self.assertTrue(logs.get_events()[0].get("notify"))


class NewDeviceTests(unittest.TestCase):

    def test_new_devices_after_the_first_look(self):
        config.save_system(logs.EVENTS, [])
        state = device_usage.empty_state(device_usage.current_period())
        seen = [{"AA:BB:CC:DD:EE:01"}]

        patches = [
            mock.patch.object(device_usage, "_state", state),
            mock.patch.object(device_usage, "_built", None),
            mock.patch.object(device_usage, "_tools", return_value=["iptables"]),
            mock.patch.object(device_usage, "build"),
            mock.patch.object(device_usage, "hooked", return_value=True),
            mock.patch.object(device_usage, "_save"),
            mock.patch.object(device_usage, "known_devices", lambda: (seen[0], {"AA:BB:CC:DD:EE:02": "Tablet"})),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        device_usage.sample()
        self.assertEqual(logs.get_events(), [])

        seen[0] = {"AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"}
        device_usage.sample()
        self.assertEqual([e["message"] for e in logs.get_events()], ["New device on your network: Tablet."])


if __name__ == "__main__":
    unittest.main()
