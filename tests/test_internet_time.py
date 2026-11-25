"""
Internet time (services/internet_time.py): pauses, offline times past
midnight, the clock, validation, and the CHAOS-PAUSE chain. iptables
is faked.

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

for name, folder in (("CHARDSOFT_DEFAULTS_DIR", "etc"), ("CHARDSOFT_STATE_DIR", "var"), ("CHARDSOFT_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHARDSOFT_SKIP_STARTUP", "1")

from services import areas, backup, config, internet_time as it  # noqa: E402

MAC = "AA:BB:CC:DD:EE:01"

# A Wednesday evening.
WED = datetime.datetime(2026, 11, 25, 21, 0)


def ts(moment):
    return moment.timestamp()


def device(**values):
    return {"mac": MAC, "name": "Tablet", "paused_until": 0, "allow_until": 0, "schedules": [], **values}


BEDTIME = {"days": [0, 1, 2, 3, 6], "start": "22:00", "end": "07:00"}


class ScheduleTests(unittest.TestCase):

    def test_window_past_midnight(self):
        # Wednesday 22:00 runs until Thursday 07:00.
        self.assertIsNone(it.window_end(BEDTIME, WED))
        self.assertEqual(it.window_end(BEDTIME, WED.replace(hour=23)), datetime.datetime(2026, 11, 26, 7, 0))
        self.assertEqual(it.window_end(BEDTIME, datetime.datetime(2026, 11, 26, 6, 59)),
                         datetime.datetime(2026, 11, 26, 7, 0))
        self.assertIsNone(it.window_end(BEDTIME, datetime.datetime(2026, 11, 26, 7, 0)))

    def test_the_night_belongs_to_the_day_it_starts(self):
        # No Friday in the days: Friday night is free, and so is Saturday
        # morning; Thursday's night still ends on Friday morning.
        self.assertIsNone(it.window_end(BEDTIME, datetime.datetime(2026, 11, 27, 23, 0)))
        self.assertIsNone(it.window_end(BEDTIME, datetime.datetime(2026, 11, 28, 3, 0)))
        self.assertIsNotNone(it.window_end(BEDTIME, datetime.datetime(2026, 11, 27, 3, 0)))
        # Sunday night ends Monday morning.
        self.assertIsNotNone(it.window_end(BEDTIME, datetime.datetime(2026, 11, 30, 3, 0)))

    def test_window_within_a_day(self):
        school = {"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "13:00"}
        self.assertIsNotNone(it.window_end(school, datetime.datetime(2026, 11, 25, 9, 0)))
        self.assertIsNone(it.window_end(school, datetime.datetime(2026, 11, 28, 9, 0)))
        self.assertIsNone(it.window_end(school, datetime.datetime(2026, 11, 25, 13, 0)))

    def test_next_start(self):
        self.assertEqual(it.next_start([BEDTIME], WED), datetime.datetime(2026, 11, 25, 22, 0))
        # Friday after 22:00: next is Sunday.
        self.assertEqual(it.next_start([BEDTIME], datetime.datetime(2026, 11, 27, 22, 30)),
                         datetime.datetime(2026, 11, 29, 22, 0))

    def test_describe_time(self):
        self.assertEqual(it.describe_time(datetime.datetime(2026, 11, 25, 22, 0), WED), "22:00")
        self.assertEqual(it.describe_time(datetime.datetime(2026, 11, 26, 7, 0), WED), "tomorrow 07:00")
        self.assertEqual(it.describe_time(datetime.datetime(2026, 11, 28, 7, 0), WED), "Sat 07:00")


class StateTests(unittest.TestCase):

    def test_states(self):
        now = ts(WED)
        self.assertFalse(it.device_state(device(), now)["paused"])

        state = it.device_state(device(paused_until=int(now) + 1800), now)
        self.assertEqual((state["paused"], state["kind"], state["label"]), (True, "pause", "Paused until 21:30"))

        self.assertFalse(it.device_state(device(paused_until=int(now) - 1), now)["paused"])
        self.assertTrue(it.device_state(device(paused_until=-1), now)["paused"])

        state = it.device_state(device(schedules=[BEDTIME]), now)
        self.assertFalse(state["paused"])
        self.assertEqual(state["next_label"], "Next offline time: 22:00")

        late = ts(WED.replace(hour=23))
        state = it.device_state(device(schedules=[BEDTIME]), late)
        self.assertEqual((state["paused"], state["kind"]), (True, "schedule"))
        self.assertEqual(state["label"], "Offline time until tomorrow 07:00")

        allowed = it.device_state(device(schedules=[BEDTIME], allow_until=int(state["until"])), late)
        self.assertFalse(allowed["paused"])
        self.assertIn("Allowed now", allowed["label"])

    def test_unset_clock_skips_schedules_but_not_an_open_pause(self):
        early = 1000.0
        self.assertFalse(it.device_state(device(schedules=[{"days": list(range(7)), "start": "00:00", "end": "23:59"}]), early)["paused"])
        self.assertTrue(it.device_state(device(paused_until=-1), early)["paused"])
        self.assertFalse(it.device_state(device(paused_until=5000), early)["paused"])

    def test_pause_end(self):
        now = ts(WED)
        self.assertEqual(it.pause_end(30, now), int(now) + 1800)
        self.assertEqual(it.pause_end("resume", now), -1)
        self.assertEqual(it.pause_end("morning", now), ts(datetime.datetime(2026, 11, 26, 7, 0)))
        self.assertEqual(it.pause_end("morning", ts(WED.replace(hour=5))), ts(WED.replace(hour=7, minute=0)))
        for bad in (0, -5, 99999, True, "forever", None, 1.5):
            self.assertIsNone(it.pause_end(bad, now), bad)


class ValidateTests(unittest.TestCase):

    def test_clean(self):
        ok, clean = it.validate_settings({"devices": [device(mac="aa-bb-cc-dd-ee-01",
                                                              schedules=[{"days": [3, 1, 1], "start": "7:5", "end": "22:00"}])]})
        self.assertTrue(ok, clean)
        self.assertEqual(clean["devices"][0]["mac"], MAC)
        self.assertEqual(clean["devices"][0]["schedules"][0], {"days": [1, 3], "start": "07:05", "end": "22:00"})

    def test_bad(self):
        for bad in (
            device(mac="nope"),
            device(paused_until="soon"),
            device(paused_until=-2),
            device(schedules=[{"days": [], "start": "22:00", "end": "07:00"}]),
            device(schedules=[{"days": [7], "start": "22:00", "end": "07:00"}]),
            device(schedules=[{"days": [True], "start": "22:00", "end": "07:00"}]),
            device(schedules=[{"days": [1], "start": "25:00", "end": "07:00"}]),
            device(schedules=[{"days": [1], "start": "22:00", "end": "22:00"}]),
            device(schedules=[BEDTIME] * 9),
            device(name="a\nb"),
        ):
            ok, message = it.validate_settings({"devices": [bad]})
            self.assertFalse(ok, bad)
        self.assertFalse(it.validate_settings({"devices": [device(), device()]})[0])
        self.assertFalse(it.validate_settings({"devices": "x"})[0])

    def test_area_and_backup(self):
        self.assertIn("internet_time", areas.AREAS)
        self.assertFalse(areas.AREAS["internet_time"].confirm)
        self.assertIn("internet_time", backup.get_validators())


class FakeIptables:

    def __init__(self):
        self.calls = []
        self.hooked = False

    def __call__(self, command):
        args = [a for a in command if a not in ("sudo", "-n")]
        self.calls.append(args)
        # -C (check) fails until the chain was hooked.
        if "-C" in args:
            return self.hooked, ""
        if args[1:3] == ["-I", "FORWARD"]:
            self.hooked = True
        return True, ""

    def macs(self):
        return [c[c.index("--mac-source") + 1] for c in self.calls if "--mac-source" in c]


class ChainTests(unittest.TestCase):

    def setUp(self):
        config.save_running(it.SETTINGS, {"devices": []})
        config.save_persistent(it.SETTINGS, {"devices": []})
        self.fake = FakeIptables()
        patches = [
            mock.patch.object(it, "_tools", return_value=["iptables"]),
            mock.patch.object(it, "run_command", self.fake),
            mock.patch.object(it, "privileged", lambda c: c),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_pause_and_resume(self):
        outcome = it.pause(MAC, 30, "Tablet")
        self.assertTrue(outcome["success"], outcome)
        self.assertIsNone(outcome["pending"])
        self.assertEqual(self.fake.macs(), [MAC])
        self.assertIn(["iptables", "-I", "FORWARD", "1", "-j", it.CHAIN], self.fake.calls)
        self.assertTrue(it.states()[MAC]["paused"])

        self.fake.calls.clear()
        outcome = it.resume(MAC)
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(self.fake.macs(), [])
        # Nothing left to remember: the entry is gone.
        self.assertEqual(it.get_settings()["devices"], [])
        self.assertFalse(it.resume(MAC)["success"])

    def test_resume_inside_an_offline_time_allows_until_it_ends(self):
        always = {"days": list(range(7)), "start": "00:00", "end": "23:59"}
        now = datetime.datetime.now()
        if now.hour == 23 and now.minute == 59:
            self.skipTest("the window just ended")
        self.assertTrue(it.set_schedules(MAC, [always])["success"])
        self.assertEqual(self.fake.macs(), [MAC])
        self.assertTrue(it.resume(MAC)["success"])
        saved = it.get_settings()["devices"][0]
        self.assertGreater(saved["allow_until"], 0)
        self.assertFalse(it.states()[MAC]["paused"])

    def test_bad_input(self):
        self.assertFalse(it.pause("x", 30)["success"])
        self.assertFalse(it.pause(MAC, "forever")["success"])
        self.assertFalse(it.set_schedules(MAC, "x")["success"])
        self.assertFalse(it.set_schedules(MAC, [{"days": [1], "start": "1:00", "end": "1:00"}])["success"])

    def test_watcher_follows_the_clock(self):
        config.save_running(it.SETTINGS, {"devices": [device(paused_until=-1)]})
        with mock.patch.object(it, "_applied", None), mock.patch.object(it, "log_event") as log:
            it.check()
            self.assertEqual(self.fake.macs(), [MAC])
            self.fake.calls.clear()
            it.check()
            # Nothing changed: only the hook is checked.
            self.assertEqual(self.fake.macs(), [])
            config.save_running(it.SETTINGS, {"devices": []})
            it.check()
            log.assert_called_once()
            self.assertIn("online again", log.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
