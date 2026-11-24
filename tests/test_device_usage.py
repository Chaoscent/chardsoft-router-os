"""
Data per device (services/device_usage.py): slots, counting across
chain rebuilds, billing periods, and the chains. iptables is faked.

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

from services import device_usage as du  # noqa: E402

A, B = "AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"

LISTING = f"""Chain CHAOS-ACCT (1 references)
    pkts      bytes target     prot opt in     out     source               destination
      12      900 RETURN     all  --  br0    br0     0.0.0.0/0            0.0.0.0/0
     120    34567 RETURN     all  --  *      *       0.0.0.0/0            0.0.0.0/0            connmark match  0x10000/0xff0000 /* {A} */
       3      180 RETURN     all  --  *      *       0.0.0.0/0            0.0.0.0/0            connmark match  0x20000/0xff0000 /* {B} */"""


class CountTests(unittest.TestCase):

    def test_parse(self):
        self.assertEqual(du.parse_counters(LISTING), {A: 34567, B: 180})
        self.assertEqual(du.parse_counters(""), {})

    def test_slots_stay_and_fill_gaps(self):
        state = du.empty_state(datetime.date(2026, 11, 1))
        self.assertTrue(du.assign_slots(state, [A, B], {A: "Phone"}))
        self.assertEqual((state["devices"][A]["slot"], state["devices"][B]["slot"]), (1, 2))
        self.assertEqual(state["devices"][A]["name"], "Phone")
        self.assertFalse(du.assign_slots(state, [B, A], {}))
        del state["devices"][A]
        du.assign_slots(state, ["AA:BB:CC:DD:EE:03"], {})
        self.assertEqual(state["devices"]["AA:BB:CC:DD:EE:03"]["slot"], 1)

    def test_full_table_drops_the_quietest(self):
        state = du.empty_state(datetime.date(2026, 11, 1))
        macs = [f"AA:BB:CC:00:{i // 256:02X}:{i % 256:02X}" for i in range(du.MAX_SLOTS)]
        du.assign_slots(state, macs, {})
        for i, mac in enumerate(macs):
            state["devices"][mac]["bytes"] = i + 10
        state["devices"][macs[7]]["bytes"] = 0
        du.assign_slots(state, ["FF:FF:FF:FF:FF:01"], {})
        self.assertNotIn(macs[7], state["devices"])
        self.assertEqual(state["devices"]["FF:FF:FF:FF:FF:01"]["slot"], 8)

    def test_counting_survives_a_rebuild(self):
        state = du.empty_state(datetime.date(2026, 11, 1))
        du.assign_slots(state, [A], {})
        du.add_counters(state, {A: 1000})
        du.add_counters(state, {A: 1500})
        # Rebuilt: the counter starts again.
        du.add_counters(state, {A: 200})
        self.assertEqual(state["devices"][A]["bytes"], 1700)
        # Devices without a slot are ignored.
        du.add_counters(state, {B: 99})
        self.assertNotIn(B, state["devices"])

    def test_new_period(self):
        state = du.empty_state(datetime.date(2026, 10, 1))
        du.assign_slots(state, [A, B], {A: "Phone"})
        state["devices"][A]["bytes"] = 5
        state["devices"][B]["bytes"] = 9
        self.assertFalse(du.roll_period(state, datetime.date(2026, 10, 1)))
        self.assertTrue(du.roll_period(state, datetime.date(2026, 11, 1)))
        self.assertEqual(state["devices"][A]["bytes"], 0)
        self.assertEqual(state["devices"][A]["slot"], 1)
        self.assertEqual([d["mac"] for d in state["previous"]["devices"]], [B, A])


class ChainTests(unittest.TestCase):

    def test_build(self):
        calls = []

        def fake(command):
            calls.append(command)
            return ("-C" not in command), ""

        with mock.patch.object(du, "_tools", return_value=["iptables"]), \
                mock.patch.object(du, "run_command", fake), \
                mock.patch.object(du, "privileged", lambda c: c), \
                mock.patch.object(du, "lan_interfaces", return_value=["br0", "wlan1"]):
            du.build({A: 1, B: 2})

        text = [" ".join(c) for c in calls]

        self.assertIn(f"iptables -t mangle -A {du.MARK_CHAIN} -m mac --mac-source {A} -j CONNMARK --set-xmark 0x10000/0xff0000", text)
        self.assertIn(f"iptables -t filter -A {du.COUNT_CHAIN} -m connmark --mark 0x20000/0xff0000 -m comment --comment {B} -j RETURN", text)
        self.assertIn(f"iptables -t filter -A {du.COUNT_CHAIN} -i br0 -o wlan1 -j RETURN", text)
        self.assertIn(f"iptables -t mangle -I PREROUTING 1 -j {du.MARK_CHAIN}", text)
        self.assertIn(f"iptables -t filter -I FORWARD 1 -j {du.COUNT_CHAIN}", text)
        # LAN-to-LAN comes before the counting rules.
        self.assertLess(text.index(f"iptables -t filter -A {du.COUNT_CHAIN} -i wlan1 -o wlan1 -j RETURN"),
                        text.index(f"iptables -t filter -A {du.COUNT_CHAIN} -m connmark --mark 0x10000/0xff0000 -m comment --comment {A} -j RETURN"))

    def test_status_shares(self):
        state = du.empty_state(du.current_period())
        du.assign_slots(state, [A, B], {A: "Phone"})
        state["devices"][A]["bytes"] = 3_000
        state["devices"][B]["bytes"] = 1_000
        with mock.patch.object(du, "_state", state), \
                mock.patch("services.clients.get_client_list", return_value={"clients": []}):
            status = du.get_status(total=5_000, limit=1)
        self.assertEqual(status["devices"], [{"mac": A, "name": "Phone", "bytes": 3_000}])
        self.assertEqual((status["more"], status["counted"], status["other"]), (1, 4_000, 1_000))


if __name__ == "__main__":
    unittest.main()
