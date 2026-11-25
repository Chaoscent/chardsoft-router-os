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


IPERF = os.path.join(ROOT, "tests", "data", "iperf3")


def iperf_sample(name):
    """Real iperf3 3.18 -J output (loopback on the Pi, 2 s tests)."""
    with open(os.path.join(IPERF, name)) as f:
        return f.read()


class IperfTests(unittest.TestCase):

    def test_client_upload_and_download(self):
        up = nt.parse_iperf(iperf_sample("cli1.json"))
        self.assertIsNone(up["error"])
        self.assertAlmostEqual(up["mbits"], 36884.9, places=1)
        self.assertEqual((up["remote"], up["reverse"], up["retransmits"]), ("127.0.0.1", False, 0))
        self.assertEqual(len(up["seconds"]), 2)
        down = nt.parse_iperf(iperf_sample("cli2.json"))
        self.assertTrue(down["reverse"])
        self.assertAlmostEqual(down["mbits"], 49787.2, places=1)

    def test_server_side_of_a_reverse_test(self):
        # The server only has its own side: zeros under sum_received.
        data = nt.parse_iperf(iperf_sample("srv2.json"))
        self.assertIsNone(data["error"])
        self.assertAlmostEqual(data["mbits"], 49791.3, places=1)
        self.assertGreater(data["bytes"], 0)
        r = nt.explain_server(iperf_sample("srv2.json"), False)
        self.assertIn("device's download", r["summary"])
        self.assertIn("device's upload", nt.explain_server(iperf_sample("srv1.json"), False)["summary"])

    def test_refused_and_busy(self):
        data = nt.parse_iperf(iperf_sample("cli3.json"))
        self.assertIn("Connection refused", data["error"])
        self.assertIn("no iperf3 server runs", nt.iperf_problem(data["error"], 5201))
        self.assertIn("busy", nt.iperf_problem("the server is busy running a test. try again later", 5201))
        self.assertEqual(nt.parse_iperf("")["error"], "iperf3 gave no result.")
        self.assertIn("nope", nt.parse_iperf("iperf3: error - nope")["error"])

    def test_run_builds_the_command(self):
        calls = []

        def fake(command, timeout):
            calls.append(command)
            if command[0] == "ip":
                return 0, "1.2.3.4 dev wwan0 src 10.0.0.2 uid 1000"
            return 0, iperf_sample("cli2.json")

        with mock.patch.object(nt, "which", return_value="/usr/bin/iperf3"), \
                mock.patch.object(nt, "execute", side_effect=fake):
            r = nt.run_tool({"tool": "iperf", "target": "speed.example", "port": "5202", "direction": "download", "family": "4"})
        command = calls[0]
        self.assertEqual(command[:3], ["iperf3", "-c", "speed.example"])
        self.assertIn("-R", command)
        self.assertIn("-4", command)
        self.assertEqual(command[command.index("-p") + 1], "5202")
        self.assertEqual(r["level"], "good")
        self.assertIn("Download from 127.0.0.1", r["summary"])
        # Over the modem: the data it used is said.
        self.assertTrue(r["mobile"])
        self.assertIn("of mobile data", r["summary"])

    def test_upload_without_mobile_note(self):
        def fake(command, timeout):
            return (0, "10.42.0.5 dev wlan0 src 10.42.0.1 uid 1000") if command[0] == "ip" else (0, iperf_sample("cli1.json"))

        with mock.patch.object(nt, "which", return_value="/usr/bin/iperf3"), \
                mock.patch.object(nt, "execute", side_effect=fake):
            r = nt.run_tool({"tool": "iperf", "target": "10.42.0.5", "port": "5201", "direction": "upload"})
        self.assertIn("Upload to", r["summary"])
        self.assertNotIn("mobile", r["summary"])

    def test_input(self):
        self.assertIn("error", nt.run_tool({"tool": "iperf", "target": "x", "port": "5201", "direction": "sideways"}))
        self.assertIn("error", nt.run_tool({"tool": "iperf", "target": "-s", "port": "5201"}))
        with mock.patch.object(nt, "which", return_value=None):
            self.assertIn("not installed", nt.run_tool({"tool": "iperf", "target": "x.example", "port": "5201"})["error"])


class FakeServer:
    """iperf3 -s -1: finishes with a sample, or waits until stopped."""

    def __init__(self, output=None):
        self.output = output
        self.code = None
        self.terminated = False
        import threading
        self.done = threading.Event()
        if output is not None:
            self.done.set()

    def poll(self):
        return 0 if self.done.is_set() else None

    def communicate(self, timeout=None):
        if not self.done.wait(timeout if timeout is not None else 5):
            raise nt.subprocess.TimeoutExpired("iperf3", timeout)
        return self.output or "", None

    def terminate(self):
        self.terminated = True
        self.done.set()

    kill = terminate

    def wait(self, timeout=None):
        self.done.wait(timeout)
        return 0


class IperfServerTests(unittest.TestCase):

    def setUp(self):
        self.rules = {}
        self.history = []

        def sync(owner, rules):
            self.rules[owner] = rules
            self.history.append((owner, len(rules)))
            return True, "OK"

        nt._server.update(process=None, until=None, stopped=False, last=None)
        for p in (
            mock.patch("services.firewall.sync_rules", side_effect=sync),
            mock.patch("services.firewall.get_lan_interfaces", return_value=["eth0", "wlan0", "wlan1"]),
            mock.patch.object(nt, "which", return_value="/usr/bin/iperf3"),
            mock.patch.object(nt, "lan_addresses", return_value=[{"interface": "wlan1", "address": "10.43.0.1"}]),
        ):
            p.start()
            self.addCleanup(p.stop)

    def wait_done(self):
        for _ in range(200):
            if nt._server["process"] is None:
                return
            nt.time.sleep(0.01)
        self.fail("the server did not finish")

    def test_one_test_then_the_port_closes(self):
        with mock.patch.object(nt.subprocess, "Popen", return_value=FakeServer(iperf_sample("srv1.json"))) as popen:
            nt.server_action({"action": "start"})
        self.assertEqual(popen.call_args[0][0], ["iperf3", "-s", "-1", "-p", "5201", "-J"])
        self.wait_done()
        # Opened on the three LAN interfaces, then closed.
        self.assertEqual(self.history, [(nt.SPEEDTEST_OWNER, 3), (nt.SPEEDTEST_OWNER, 0)])
        last = nt.server_status()["last"]
        self.assertEqual(last["level"], "good")
        self.assertIn("device's upload", last["summary"])

    def test_rules_only_on_the_lan(self):
        rules = nt.server_rules()
        self.assertEqual([r[3] for r in rules], ["eth0", "wlan0", "wlan1"])
        for r in rules:
            self.assertEqual(r[:3], ["allow", "in", "on"])
            self.assertIn("5201", r)
            self.assertEqual(r[-1], nt.SPEEDTEST_COMMENT)

    def test_stop_closes_without_a_result(self):
        server = FakeServer()
        with mock.patch.object(nt.subprocess, "Popen", return_value=server):
            self.assertTrue(nt.server_action({"action": "start"})["running"])
            self.assertTrue(self.rules[nt.SPEEDTEST_OWNER])
            # A second start keeps the one server.
            self.assertTrue(nt.server_action({"action": "start"})["running"])
            nt.server_action({"action": "stop"})
        self.wait_done()
        self.assertTrue(server.terminated)
        self.assertEqual(self.rules[nt.SPEEDTEST_OWNER], [])
        self.assertIsNone(nt.server_status()["last"])

    def test_nobody_came(self):
        with mock.patch.object(nt, "SERVER_WAIT", 0.05), \
                mock.patch.object(nt.subprocess, "Popen", return_value=FakeServer()):
            nt.server_action({"action": "start"})
            self.wait_done()
        self.assertEqual(self.rules[nt.SPEEDTEST_OWNER], [])
        self.assertIn("No device started a test", nt.server_status()["last"]["summary"])

    def test_port_that_cannot_open(self):
        with mock.patch("services.firewall.sync_rules", side_effect=lambda owner, rules: (False, "ufw broke") if rules else (True, "OK")):
            self.assertIn("could not be opened", nt.server_action({"action": "start"})["error"])
        self.assertIn("error", nt.server_action({"action": "jump"}))


if __name__ == "__main__":
    unittest.main()
