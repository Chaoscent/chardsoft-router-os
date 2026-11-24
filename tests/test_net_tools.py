"""
The Tools page (services/net_tools.py): input checks, and reading the
output of ping, traceroute and `ip route get` into plain words.

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

from services import net_tools as nt  # noqa: E402

PING_OK = """PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=31.2 ms
64 bytes from 1.1.1.1: icmp_seq=2 ttl=57 time=28.9 ms
64 bytes from 1.1.1.1: icmp_seq=3 ttl=57 time=30.0 ms
64 bytes from 1.1.1.1: icmp_seq=4 ttl=57 time=29.4 ms

--- 1.1.1.1 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 1503ms
rtt min/avg/max/mdev = 28.900/29.875/31.200/0.871 ms"""

PING_LOSS = """PING example.com (93.184.215.14) 56(84) bytes of data.
64 bytes from 93.184.215.14: icmp_seq=1 ttl=55 time=112 ms
64 bytes from 93.184.215.14: icmp_seq=3 ttl=55 time=118 ms

--- example.com ping statistics ---
4 packets transmitted, 2 received, 50% packet loss, time 1510ms
rtt min/avg/max/mdev = 112.000/115.000/118.000/3.000 ms"""

PING_NONE = """PING 10.9.9.9 (10.9.9.9) 56(84) bytes of data.

--- 10.9.9.9 ping statistics ---
4 packets transmitted, 0 received, 100% packet loss, time 1530ms"""

TRACE_OK = """traceroute to 1.1.1.1 (1.1.1.1), 20 hops max, 60 byte packets
 1  192.168.0.1  0.612 ms
 2  100.72.0.1  18.304 ms
 3  *
 4  62.154.14.62  24.511 ms
 5  1.1.1.1  27.003 ms"""

TRACE_SILENT = """traceroute to 8.8.4.4 (8.8.4.4), 20 hops max, 60 byte packets
 1  192.168.0.1  0.612 ms
 2  *
 3  *"""

TRACE_UNREACHABLE = """traceroute to 10.20.0.9 (10.20.0.9), 20 hops max, 60 byte packets
 1  192.168.0.1  0.612 ms
 2  192.168.0.1  3001.2 ms !H"""


class InputTests(unittest.TestCase):

    def test_targets(self):
        for good in ("1.1.1.1", "2606:4700:4700::1111", "fe80::1%eth0", "example.com", "example.com.", "my-pi", "_dmarc.x.de"):
            nt.clean_target(good)
        for bad in ("", "-c 9", "--help", "a b", "x;reboot", "$(id)", "a..b", "fe80::1%eth0;x", "x" * 300):
            with self.assertRaises(nt.ToolError, msg=bad):
                nt.clean_target(bad)

    def test_ports_and_families(self):
        self.assertEqual(nt.clean_port("443"), 443)
        for bad in ("0", "65536", "x", ""):
            with self.assertRaises(nt.ToolError):
                nt.clean_port(bad)
        with self.assertRaises(nt.ToolError):
            nt.clean_family("5")

    def test_unknown_interface(self):
        with mock.patch.object(nt, "interfaces", return_value=["eth0"]):
            self.assertEqual(nt.clean_interface("eth0"), "eth0")
            with self.assertRaises(nt.ToolError):
                nt.clean_interface("wlan9")

    def test_run_tool_refuses_quietly(self):
        self.assertIn("error", nt.run_tool({"tool": "rm", "target": "x"}))
        self.assertIn("error", nt.run_tool({"tool": "ping", "target": "-f"}))
        self.assertIn("error", nt.run_tool(None))

    def test_one_test_at_a_time(self):
        nt._lock.acquire()
        try:
            self.assertIn("Another test", nt.run_tool({"tool": "ping", "target": "1.1.1.1"})["error"])
        finally:
            nt._lock.release()


class PingTests(unittest.TestCase):

    def test_all_answered(self):
        r = nt.explain_ping(nt.parse_ping(PING_OK), PING_OK)
        self.assertEqual((r["level"], r["received"], r["address"]), ("good", 4, "1.1.1.1"))
        self.assertIn("30 ms on average", r["summary"])

    def test_loss(self):
        r = nt.explain_ping(nt.parse_ping(PING_LOSS), PING_LOSS)
        self.assertEqual(r["level"], "warning")
        self.assertEqual(r["address"], "93.184.215.14")
        self.assertIn("2 of 4 answers lost", r["summary"])

    def test_no_answer_and_bad_name(self):
        self.assertIn("No answer", nt.explain_ping(nt.parse_ping(PING_NONE), PING_NONE)["summary"])
        out = "ping: nope.invalid: Name or service not known"
        self.assertIn("could not be found", nt.explain_ping(nt.parse_ping(out), out)["summary"])


class TracerouteTests(unittest.TestCase):

    def test_reached(self):
        r = nt.explain_traceroute(nt.parse_traceroute(TRACE_OK), TRACE_OK)
        self.assertEqual(r["level"], "good")
        self.assertIn("Reached in 5 hops", r["summary"])
        self.assertEqual(r["hops"][1]["note"], "Carrier network (shared addresses)")
        self.assertEqual(r["hops"][2]["address"], None)

    def test_silent(self):
        r = nt.explain_traceroute(nt.parse_traceroute(TRACE_SILENT), TRACE_SILENT)
        self.assertEqual(r["level"], "warning")
        self.assertIn("hop 1", r["summary"])

    def test_unreachable_flag(self):
        r = nt.explain_traceroute(nt.parse_traceroute(TRACE_UNREACHABLE), TRACE_UNREACHABLE)
        self.assertEqual(r["level"], "bad")
        self.assertIn("unreachable", r["summary"])


class RouteTests(unittest.TestCase):

    def test_parse(self):
        data = nt.parse_route_get("10.20.1.5 via 192.168.1.5 dev eth0 src 192.168.1.1 uid 1000 \n    cache")
        self.assertEqual((data["gateway"], data["interface"], data["source"]), ("192.168.1.5", "eth0", "192.168.1.1"))

    def test_names_the_users_route(self):
        output = "10.20.1.5 via 192.168.1.5 dev eth0 src 192.168.1.1 uid 1000"
        with mock.patch.object(nt, "execute", return_value=(0, output)), \
                mock.patch("services.routes.get_settings", return_value={"routes": [
                    {"destination": "10.20.0.0/16", "enabled": True}]}):
            r = nt.run_route("10.20.1.5", "auto")
        self.assertIn("by your route to 10.20.0.0/16", r["summary"])
        self.assertEqual(r["route"], "10.20.0.0/16")


if __name__ == "__main__":
    unittest.main()
