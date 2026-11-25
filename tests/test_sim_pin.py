"""
SIM PIN and PUK: codes are sent once, the saved PIN never costs more
than one attempt and never the last one, and NetworkManager never gets
the PIN.

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

from services import modem  # noqa: E402

WRONG = "error: couldn't send PIN: 'GDBus.Error:org.freedesktop.ModemManager1.Error.MobileEquipment.IncorrectPassword'"


class FakeSim:
    """A SIM behind mmcli: lock, attempts left, and the commands sent."""

    def __init__(self, lock="sim-pin", pin_retries=3, puk_retries=10, pin_enabled=True,
                 pin="1234", puk="12345678", saved_pin="", busy=False):
        self.lock = lock
        self.pin_retries = pin_retries
        self.puk_retries = puk_retries
        self.pin_enabled = pin_enabled
        self.pin = pin
        self.puk = puk
        self.busy = busy
        self.sent = []
        self.settings = {**modem.DEFAULT_SETTINGS, "pin": saved_pin}
        self.logs = []

    def data(self, max_age=None):
        return {
            "state": "ready", "lock": self.lock,
            "pin_retries": None if self.pin_retries is None else str(self.pin_retries),
            "puk_retries": None if self.puk_retries is None else str(self.puk_retries),
            "pin_enabled": self.pin_enabled,
        }

    def command(self, args):
        self.sent.append(list(args))
        if self.busy:
            return False, "error: modem busy"
        codes = dict(a[2:].split("=", 1) for a in args if "=" in a)
        if "puk" in codes:
            if codes["puk"] != self.puk:
                self.puk_retries -= 1
                return False, WRONG
            self.pin, self.lock, self.pin_retries = codes["pin"], "none", 3
            return True, "successfully sent PUK code to the SIM"
        if codes.get("pin") != self.pin:
            if self.pin_retries is not None:
                self.pin_retries -= 1
                if self.pin_retries == 0:
                    self.lock = "sim-puk"
            return False, WRONG
        self.pin_retries = 3
        if "--enable-pin" in args:
            self.pin_enabled = True
        elif "--disable-pin" in args:
            self.pin_enabled = False
        elif "change-pin" in codes:
            self.pin = codes["change-pin"]
        else:
            self.lock = "none"
        return True, "ok"

    def remember(self, pin):
        self.settings["pin"] = pin
        return True

    def __enter__(self):
        modem.auto_unlock_state.update(rejected=None, held=False)
        self.patches = [
            mock.patch.object(modem, "get_modem_data", self.data),
            mock.patch.object(modem, "sim_command", self.command),
            mock.patch.object(modem, "remember_pin", self.remember),
            mock.patch.object(modem, "get_modem_settings", lambda: dict(self.settings)),
            mock.patch.object(modem, "apply_modem_settings", lambda: (True, "")),
            mock.patch.object(modem, "log_event", lambda *a, **k: self.logs.append((a, k))),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


class AutoUnlockTests(unittest.TestCase):

    def test_saved_pin_unlocks(self):
        with FakeSim(saved_pin="1234") as sim:
            self.assertEqual(modem.auto_unlock(), "unlocked")
            self.assertEqual(sim.lock, "none")

    def test_wrong_saved_pin_costs_one_attempt_and_is_forgotten(self):
        with FakeSim(saved_pin="9999") as sim:
            self.assertEqual(modem.auto_unlock(), "rejected")
            self.assertEqual(sim.pin_retries, 2)
            self.assertEqual(sim.settings["pin"], "")
            self.assertIsNotNone(modem.auto_unlock_state["rejected"])
            # Nothing more is sent: the PIN is gone.
            self.assertEqual(modem.auto_unlock(), "no_pin")
            self.assertEqual(len(sim.sent), 1)

    def test_last_attempt_is_never_used(self):
        with FakeSim(saved_pin="1234", pin_retries=1) as sim:
            self.assertEqual(modem.auto_unlock(), "held")
            self.assertEqual(modem.auto_unlock(), "held")
            self.assertEqual(sim.sent, [])
            self.assertEqual(len(sim.logs), 1)

    def test_busy_modem_keeps_the_pin(self):
        with FakeSim(saved_pin="1234", busy=True) as sim:
            self.assertEqual(modem.auto_unlock(), "failed")
            self.assertEqual(sim.settings["pin"], "1234")

    def test_unknown_attempts_count_as_wrong(self):
        with FakeSim(saved_pin="1234", pin_retries=None, busy=True) as sim:
            self.assertEqual(modem.auto_unlock(), "rejected")
            self.assertEqual(sim.settings["pin"], "")

    def test_unlocked_sim_is_left_alone(self):
        with FakeSim(saved_pin="1234", lock="none") as sim:
            self.assertEqual(modem.auto_unlock(), "not_locked")
            self.assertEqual(sim.sent, [])


class ManualTests(unittest.TestCase):

    def test_unlock_and_remember(self):
        with FakeSim() as sim:
            self.assertTrue(modem.unlock_sim("1234")["success"])
            self.assertEqual(sim.settings["pin"], "1234")

    def test_wrong_pin_says_attempts_left(self):
        with FakeSim() as sim:
            result = modem.unlock_sim("0000")
            self.assertFalse(result["success"])
            self.assertIn("2 attempts left", result["message"])
            self.assertEqual(sim.settings["pin"], "")

    def test_last_attempt_needs_confirmation(self):
        with FakeSim(pin_retries=1) as sim:
            self.assertTrue(modem.unlock_sim("1234")["confirm"])
            self.assertEqual(sim.sent, [])
            self.assertTrue(modem.unlock_sim("1234", confirm=True)["success"])

    def test_pin_off_forgets_saved_pin(self):
        with FakeSim(lock="none", saved_pin="1234") as sim:
            self.assertTrue(modem.set_pin_lock(False, "1234")["success"])
            self.assertFalse(sim.pin_enabled)
            self.assertEqual(sim.settings["pin"], "")

    def test_pin_on_saves_it(self):
        with FakeSim(lock="none", pin_enabled=False) as sim:
            self.assertTrue(modem.set_pin_lock(True, "1234")["success"])
            self.assertTrue(sim.pin_enabled)
            self.assertEqual(sim.settings["pin"], "1234")

    def test_pin_on_twice_sends_nothing(self):
        with FakeSim(lock="none") as sim:
            self.assertFalse(modem.set_pin_lock(True, "1234")["success"])
            self.assertEqual(sim.sent, [])

    def test_locked_sim_cannot_change_lock(self):
        with FakeSim(lock="sim-pin") as sim:
            self.assertFalse(modem.set_pin_lock(False, "1234")["success"])
            self.assertEqual(sim.sent, [])

    def test_change_pin_updates_saved_pin(self):
        with FakeSim(lock="none", saved_pin="1234") as sim:
            self.assertTrue(modem.change_pin("1234", "5678", remember=False)["success"])
            self.assertEqual(sim.pin, "5678")
            self.assertEqual(sim.settings["pin"], "5678")

    def test_wrong_current_pin_on_change(self):
        with FakeSim(lock="none") as sim:
            result = modem.change_pin("0000", "5678")
            self.assertIn("Wrong current PIN", result["message"])
            self.assertEqual(sim.pin, "1234")


class PukTests(unittest.TestCase):

    def test_unblock_sets_new_pin(self):
        with FakeSim(lock="sim-puk", pin_retries=0) as sim:
            self.assertTrue(modem.unblock_sim("12345678", "4321")["success"])
            self.assertEqual(sim.pin, "4321")
            self.assertEqual(sim.settings["pin"], "4321")

    def test_wrong_puk_says_attempts_left(self):
        with FakeSim(lock="sim-puk", pin_retries=0) as sim:
            result = modem.unblock_sim("11111111", "4321")
            self.assertIn("9 attempts left", result["message"])
            self.assertEqual(sim.settings["pin"], "")

    def test_last_puk_attempts_need_confirmation(self):
        with FakeSim(lock="sim-puk", pin_retries=0, puk_retries=3) as sim:
            self.assertTrue(modem.unblock_sim("12345678", "4321")["confirm"])
            self.assertEqual(sim.sent, [])

    def test_puk_only_for_blocked_sim(self):
        with FakeSim(lock="sim-pin") as sim:
            self.assertFalse(modem.unblock_sim("12345678", "4321")["success"])
            self.assertEqual(sim.sent, [])

    def test_puk_format(self):
        with FakeSim(lock="sim-puk") as sim:
            self.assertFalse(modem.unblock_sim("1234", "4321")["success"])
            self.assertEqual(sim.sent, [])


class ParsingTests(unittest.TestCase):

    def test_enabled_locks(self):
        self.assertFalse(modem.pin_enabled("fixed-dialing"))
        self.assertTrue(modem.pin_enabled("sim, fixed-dialing"))
        self.assertFalse(modem.pin_enabled("none"))
        self.assertIsNone(modem.pin_enabled(None))

    def test_network_manager_never_gets_the_pin(self):
        props = modem.connection_properties({**modem.DEFAULT_SETTINGS, "pin": "1234"})
        self.assertNotIn("1234", props)
        self.assertEqual(props[props.index("gsm.pin") + 1], "")


if __name__ == "__main__":
    unittest.main()
