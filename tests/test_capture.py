"""
Packet capture: the filter from the form, the checks before tcpdump
starts, and the one form the root helper allows.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import importlib.machinery
import importlib.util
import os
import pwd
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

from services import capture  # noqa: E402

loader = importlib.machinery.SourceFileLoader("root_helper_capture", os.path.join(ROOT, "deploy", "root-helper"))
spec = importlib.util.spec_from_loader("root_helper_capture", loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class FilterTests(unittest.TestCase):

    def test_everything(self):
        self.assertEqual(capture.build_filter({}), "")

    def test_parts_are_joined(self):
        self.assertEqual(
            capture.build_filter({"host": "192.168.1.20", "port": "53", "protocol": "udp",
                                  "filter": "not  net 10.0.0.0/8", "exclude_self": True}, viewer="192.168.1.5"),
            "host 192.168.1.20 and port 53 and udp and (not net 10.0.0.0/8) and "
            "not (host 192.168.1.5 and (port 80 or port 443 or port 22))"
        )

    def test_ipv6_device(self):
        self.assertEqual(capture.build_filter({"host": "fd00::1"}), "host fd00::1")

    def test_bad_input_is_refused(self):
        for bad in ({"host": "example.com"}, {"port": "70000"}, {"protocol": "sctp"},
                    {"filter": "-r /etc/shadow"}, {"filter": "host x; reboot"}, {"filter": "`id`"}):
            with self.assertRaises(capture.CaptureError, msg=bad):
                capture.build_filter(bad)


class StartTests(unittest.TestCase):

    def test_checks_before_tcpdump(self):
        with mock.patch.object(capture, "available", lambda: True), \
             mock.patch.object(capture, "interfaces", lambda: ["eth0"]), \
             mock.patch.object(capture.subprocess, "Popen") as popen:
            for bad in ({"interface": "eth9"}, {"seconds": 7}, {"limit_mb": 5000}):
                with self.assertRaises(capture.CaptureError, msg=bad):
                    capture.start(bad)
            popen.assert_not_called()


class HelperTests(unittest.TestCase):

    def form(self, *expression, user=None, seconds="300", path=None):
        user = user or pwd.getpwuid(os.getuid()).pw_name
        return ["-i", "eth0", "-n", "-s", "0", "-U", "-Z", user, "-G", seconds, "-W", "1",
                "-w", path or helper.CAPTURE_FILE, *expression]

    def allowed(self, args):
        try:
            helper.cmd_tcpdump(args)
        except helper.Refused as error:
            if "is not installed" not in str(error):
                raise

    def test_the_app_form(self):
        with mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid())}):
            self.allowed(self.form())
            self.allowed(self.form("host 192.168.1.20 and port 53"))

    def test_everything_else_is_refused(self):
        with mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid())}):
            for args in (self.form("-r /etc/shadow"), self.form("a", "b"), self.form(user="root"),
                         self.form(seconds="99999"), self.form(path="/etc/x.pcap"),
                         ["-i", "eth0", "-z", "sh"], self.form("host x; reboot")):
                with self.subTest(args=args), self.assertRaises(helper.Refused):
                    helper.cmd_tcpdump(args)


if __name__ == "__main__":
    unittest.main()
