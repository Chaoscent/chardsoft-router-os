"""
Modem readings: one mmcli reading serves many requests, and slow
readings do not pile up.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

SANDBOX = tempfile.mkdtemp(prefix="chardsoft-test-")

for name, folder in (("CHARDSOFT_DEFAULTS_DIR", "etc"), ("CHARDSOFT_STATE_DIR", "var"), ("CHARDSOFT_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHARDSOFT_SKIP_STARTUP", "1")

from services import modem, modem_watchdog as wd  # noqa: E402


class ReadingCacheTests(unittest.TestCase):

    def setUp(self):
        modem._modem_cache.update(time=0.0, data=None)
        self.reads = 0

    def slow_read(self):
        self.reads += 1
        time.sleep(0.3)
        return {"state": "ready", "n": self.reads}

    def test_parallel_requests_share_one_reading(self):
        results = []
        with mock.patch.object(modem, "read_modem_data", self.slow_read):
            threads = [threading.Thread(target=lambda: results.append(modem.get_modem_data())) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(self.reads, 1)
        self.assertEqual(len(results), 8)

    def test_old_reading_is_refreshed(self):
        with mock.patch.object(modem, "read_modem_data", self.slow_read):
            modem.get_modem_data()
            modem.get_modem_data(max_age=0)
        self.assertEqual(self.reads, 2)

    def test_callers_get_their_own_copy(self):
        with mock.patch.object(modem, "read_modem_data", self.slow_read):
            first = modem.get_modem_data()
            first["state"] = "changed"
            self.assertEqual(modem.get_modem_data()["state"], "ready")


def sample(rx, errors, tx):
    return {"rx_packets": rx, "rx_errors": errors, "tx_packets": tx}


class BandTests(unittest.TestCase):

    def test_nsa_without_5g_band_says_5g(self):
        self.assertEqual(modem.band_label("B3", "5G NSA"), "B3 + 5G")

    def test_known_5g_band_is_kept(self):
        self.assertEqual(modem.band_label("B3 + n78", "5G NSA"), "B3 + n78")

    def test_lte_and_unknown_stay(self):
        self.assertEqual(modem.band_label("B20", "LTE"), "B20")
        self.assertIsNone(modem.band_label(None, "5G NSA"))

    def test_pi_qmicli_output(self):
        output = ("Band Information:\n\tRadio Interface:   'lte'\n\tActive Band Class: 'eutran-3'\n"
                  "\tActive Channel:    '1300'\nBand Information (Extended):\n\tRadio Interface:   'lte'\n"
                  "\tActive Band Class: 'eutran-3'\n")
        self.assertEqual(modem.band_label(modem.format_bands(output), "5G NSA"), "B3 + 5G")


class WatchdogTests(unittest.TestCase):

    def setUp(self):
        wd.state.update(previous=None, stuck=0, resets=[], last_reset=None)
        self.counters = sample(100, 0, 100)
        self.resets = []

    def run_samples(self, steps, connection="activated"):
        """steps: (received, errors, sent) per sample; returns check() results."""
        results = []
        with mock.patch.object(wd.modem, "wwan_interface", return_value=("wwan0", "qmi_wwan")), \
             mock.patch.object(wd.modem, "get_connection_state", return_value=connection), \
             mock.patch.object(wd.modem, "ensure_link_mtu", return_value=False), \
             mock.patch.object(wd, "counters", side_effect=lambda i: dict(self.counters)), \
             mock.patch.object(wd, "reset_modem", side_effect=lambda reason: self.resets.append(reason) or True):
            results.append(wd.check())
            for rx, err, tx in steps:
                c = self.counters
                self.counters = sample(c["rx_packets"] + rx, c["rx_errors"] + err, c["tx_packets"] + tx)
                results.append(wd.check())
        return results

    def test_rejected_packets_lead_to_one_reset(self):
        results = self.run_samples([(0, 1500, 0)] * 3)
        self.assertEqual(results, ["ok", "stuck", "stuck", "reset"])
        self.assertEqual(len(self.resets), 1)
        self.assertIn("rejected", self.resets[0])
        self.assertIsNotNone(wd.state["last_reset"])

    def test_sending_without_answers_is_stuck_too(self):
        self.assertEqual(self.run_samples([(0, 0, 200)] * 3)[-1], "reset")

    def test_a_working_link_is_left_alone(self):
        results = self.run_samples([(0, 1500, 0), (0, 1500, 0), (5, 1500, 10), (0, 1500, 0)])
        self.assertEqual(results, ["ok", "stuck", "stuck", "ok", "stuck"])
        self.assertEqual(self.resets, [])

    def test_quiet_link_is_not_stuck(self):
        self.assertNotIn("reset", self.run_samples([(0, 0, 3)] * 6))

    def test_cooldown_and_hourly_limit(self):
        stuck = [(0, 1500, 0)] * 30
        with mock.patch.object(wd.time, "monotonic", side_effect=[i * 100.0 for i in range(1000)]):
            self.run_samples(stuck)
        # A reset every 300 s at most, 3 per hour.
        self.assertLessEqual(len(self.resets), wd.MAX_RESETS_PER_HOUR)
        self.assertGreaterEqual(len(self.resets), 2)

    def test_nothing_happens_without_mobile_data(self):
        self.assertEqual(set(self.run_samples([(0, 1500, 0)] * 4, connection="deactivated")), {"idle"})

    def test_counter_restart_is_not_stuck(self):
        self.assertFalse(wd.is_stuck(sample(500, 900, 500), sample(3, 2, 3)))


class LinkMtuTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.mtu_file = os.path.join(folder, "mtu")
        self.commands = []
        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == "/sys/class/net/wwan0/mtu":
                return real_open(self.mtu_file, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        self.patches = [
            mock.patch("builtins.open", side_effect=fake_open),
            mock.patch.object(modem, "privileged", side_effect=lambda cmd: cmd),
            mock.patch.object(modem, "run_command", side_effect=lambda cmd, **kw: self.commands.append(cmd) or (True, "")),
            mock.patch.object(modem, "has_nmcli", return_value=True),
            mock.patch.object(modem, "connection_exists", return_value=True),
            mock.patch.object(modem, "run", side_effect=lambda cmd, **kw: self.setting),
            mock.patch.dict(modem._connection_mtu, checked=False),
        ]
        self.setting = "auto"
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def set_mtu(self, value):
        with open(self.mtu_file, "w") as f:
            f.write(f"{value}\n")

    def test_qmi_link_below_1500_is_raised_and_remembered(self):
        self.set_mtu(1472)
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "qmi_wwan")):
            self.assertTrue(modem.ensure_link_mtu())
        self.assertIn(["ip", "link", "set", "wwan0", "mtu", "1500"], self.commands)
        self.assertIn(["nmcli", "connection", "modify", modem.CONNECTION, "gsm.mtu", "1500"], self.commands)

    def test_full_mtu_is_left_alone(self):
        self.set_mtu(1500)
        self.setting = "1500"
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "qmi_wwan")):
            self.assertFalse(modem.ensure_link_mtu())
        self.assertEqual(self.commands, [])

    def test_setting_is_saved_once_even_when_the_link_is_at_1500(self):
        self.set_mtu(1500)
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "qmi_wwan")):
            modem.ensure_link_mtu()
            modem.ensure_link_mtu()
        self.assertEqual(self.commands, [["nmcli", "connection", "modify", modem.CONNECTION, "gsm.mtu", "1500"]])

    def test_other_drivers_keep_the_carrier_mtu(self):
        self.set_mtu(1430)
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "cdc_mbim")):
            self.assertFalse(modem.ensure_link_mtu())
        self.assertEqual(self.commands, [])

    def test_new_connection_gets_the_mtu_for_qmi_only(self):
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "qmi_wwan")):
            props = modem.connection_properties(dict(modem.DEFAULT_SETTINGS))
        self.assertEqual(props[props.index("gsm.mtu") + 1], "1500")
        with mock.patch.object(modem, "wwan_interface", return_value=("wwan0", "cdc_mbim")):
            self.assertNotIn("gsm.mtu", modem.connection_properties(dict(modem.DEFAULT_SETTINGS)))


def mmcli_info(index, vendor, model, device_id, port, net, lock, state, driver="qmi"):
    """`mmcli -m` output as ModemManager 1.24 prints it (layout from the Pi)."""
    return f"""  -----------------------------
  General  |              path: /org/freedesktop/ModemManager1/Modem/{index}
           |         device id: {device_id}
  -----------------------------
  Hardware |      manufacturer: {vendor}
           |             model: {model}
           |      equipment id: 35000000000{index:0>4}
  -----------------------------
  System   |            device: /nonexistent/usb2/2-{index}
           |      primary port: {port}
           |             ports: {port} ({driver}), ttyACM0 (at), ttyACM1 (ignored),
           |                    ttyACM2 (at), {net} (net)
  -----------------------------
  Status   |              lock: {lock}
           |    unlock retries: sim-pin (3)
           |             state: {state}
           |       power state: on
  -----------------------------
  SIM      |  primary sim path: /org/freedesktop/ModemManager1/SIM/{index}
"""


L850 = mmcli_info("10", "Fibocom Wireless Inc.", "DW5820e L850-GL", "b" * 40, "cdc-wdm1", "wwan1", "sim-pin", "locked", "mbim")
RM520 = mmcli_info("9", "Quectel", "RM520N-GL", "a" * 40, "cdc-wdm0", "wwan0", "none", "connected")
LIST = "    /org/freedesktop/ModemManager1/Modem/10 [Fibocom] L850\n    /org/freedesktop/ModemManager1/Modem/9 [Quectel] RM520N-GL\n"


class MultiModemTests(unittest.TestCase):

    def setUp(self):
        modem.forget_modem_list()
        self.choice = ""
        self.active = "chardsoft-modem:cdc-wdm0\n"
        self.outputs = {"-L": LIST, "10": L850, "9": RM520}
        self.patches = [
            mock.patch.object(modem, "run", side_effect=self.fake_run),
            mock.patch.object(modem, "chosen_device", side_effect=lambda: self.choice),
            mock.patch.object(modem, "has_nmcli", return_value=True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        modem.forget_modem_list()

    def fake_run(self, cmd):
        if cmd[:2] == ["mmcli", "-L"]:
            return self.outputs["-L"]
        if cmd[:2] == ["mmcli", "-m"]:
            return self.outputs.get(cmd[2])
        if cmd[:1] == ["nmcli"] and "--active" in cmd:
            return self.active
        return None

    def test_names_and_ports_come_from_modemmanager(self):
        modems = modem.list_modems()
        self.assertEqual([m["index"] for m in modems], ["10", "9"])
        l850 = modems[0]
        self.assertEqual((l850["vendor"], l850["model"]), ("Fibocom Wireless Inc.", "DW5820e L850-GL"))
        self.assertEqual((l850["primary_port"], l850["data_port"]), ("cdc-wdm1", "wwan1"))
        self.assertEqual((l850["lock"], l850["state"]), ("sim-pin", "locked"))
        self.assertEqual(modems[1]["data_port"], "wwan0")

    def test_without_a_choice_the_modem_with_mobile_data_is_used(self):
        # The L850 is listed first, but mobile data runs on the RM520N.
        self.assertEqual(modem.get_modem_index(), "9")

    def test_without_a_choice_or_data_the_ready_one_is_used(self):
        self.active = ""
        self.assertEqual(modem.get_modem_index(), "9")

    def test_the_choice_wins(self):
        self.choice = "b" * 40
        self.assertEqual(modem.get_modem_index(), "10")
        with mock.patch("os.path.realpath", return_value="/drivers/cdc_mbim"):
            self.assertEqual(modem.wwan_interface(), ("wwan1", "cdc_mbim"))

    def test_a_chosen_modem_that_is_gone_falls_back(self):
        self.choice = "c" * 40
        self.assertEqual(modem.get_modem_index(), "9")

    def test_one_modem_is_simply_used(self):
        self.outputs["-L"] = "    /org/freedesktop/ModemManager1/Modem/10 [Fibocom] L850\n"
        self.choice = "a" * 40
        self.assertEqual(modem.get_modem_index(), "10")
        self.assertEqual(modem.modem_choices(), [])

    def test_choices_for_the_page(self):
        choices = modem.modem_choices()
        self.assertEqual([c["name"] for c in choices], ["Fibocom Wireless Inc. DW5820e L850-GL", "Quectel RM520N-GL"])
        self.assertEqual([c["selected"] for c in choices], [False, True])
        self.assertEqual(choices[0]["imei_end"], "0010")

    def test_connection_is_bound_only_with_several_modems(self):
        props = modem.connection_properties(dict(modem.DEFAULT_SETTINGS))
        self.assertEqual(props[props.index("gsm.device-id") + 1], "a" * 40)
        modem.forget_modem_list()
        self.outputs["-L"] = "    /org/freedesktop/ModemManager1/Modem/9 [Quectel] RM520N-GL\n"
        props = modem.connection_properties(dict(modem.DEFAULT_SETTINGS))
        self.assertEqual(props[props.index("gsm.device-id") + 1], "")


class SavedPinSimTests(unittest.TestCase):

    def test_saved_pin_is_not_sent_to_another_sim(self):
        settings = {**modem.DEFAULT_SETTINGS, "pin": "1234", "pin_sim": "89490200001234567890"}
        with mock.patch.object(modem, "get_modem_settings", return_value=settings), \
             mock.patch.object(modem, "sim_iccid", return_value="89490200009999999999"), \
             mock.patch.object(modem, "sim_command") as send:
            self.assertEqual(modem.auto_unlock(), "other_sim")
        send.assert_not_called()

    def test_old_pin_without_its_sim_waits_with_several_modems(self):
        settings = {**modem.DEFAULT_SETTINGS, "pin": "1234"}
        with mock.patch.object(modem, "get_modem_settings", return_value=settings), \
             mock.patch.object(modem, "list_modems", return_value=[{}, {}]), \
             mock.patch.object(modem, "sim_command") as send:
            self.assertEqual(modem.auto_unlock(), "other_sim")
        send.assert_not_called()

    def test_page_cannot_set_the_pin_sim(self):
        with mock.patch.object(modem, "get_modem_settings", return_value=dict(modem.DEFAULT_SETTINGS)):
            ok, result = modem.validate_modem_settings({"pin_sim": "89490200001234567890"})
        self.assertTrue(ok)
        self.assertEqual(result["pin_sim"], "")

    def test_backup_keeps_the_pin_with_its_sim(self):
        with mock.patch.object(modem, "get_modem_settings", return_value=dict(modem.DEFAULT_SETTINGS)):
            ok, result = modem.validate_stored_settings({"pin": "1234", "pin_sim": "89490200001234567890"})
        self.assertTrue(ok)
        self.assertEqual((result["pin"], result["pin_sim"]), ("1234", "89490200001234567890"))

    def test_pin_sim_is_not_shown_to_the_page(self):
        settings = {**modem.DEFAULT_SETTINGS, "pin": "1234", "pin_sim": "89490200001234567890"}
        with mock.patch.object(modem, "get_modem_settings", return_value=settings):
            self.assertNotIn("pin_sim", modem.public_settings())


if __name__ == "__main__":
    unittest.main()
