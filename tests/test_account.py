"""
Account: signed-in devices (sign out one or all others), two-step login
(TOTP and recovery codes, also through the login API) and SSH keys.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

SANDBOX = tempfile.mkdtemp(prefix="chardsoft-test-")

for name, folder in (("CHARDSOFT_DEFAULTS_DIR", "etc"), ("CHARDSOFT_STATE_DIR", "var"), ("CHARDSOFT_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))
    os.makedirs(os.environ[name], exist_ok=True)

os.environ.setdefault("CHARDSOFT_SKIP_STARTUP", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "x" * 32)

from werkzeug.security import generate_password_hash  # noqa: E402

from services import config, ssh, totp  # noqa: E402

import app as appmod  # noqa: E402
import security  # noqa: E402

PASSWORD = "test-pass-1"

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"


def reset_users(**extra):
    config.delete_running("users")
    config.save_persistent("users", {"admin": {"password": generate_password_hash(PASSWORD), **extra}}, secret=True)


class Browser:

    def __init__(self, address="192.168.1.30", agent="Mozilla/5.0 (Windows NT 10.0) Firefox/140.0"):
        self.client = appmod.app.test_client()
        self.address = address
        self.agent = agent

    def env(self):
        return {"REMOTE_ADDR": self.address, "HTTP_USER_AGENT": self.agent}

    def get(self, path):
        return self.client.get(path, environ_base=self.env())

    def post(self, path, data=None):
        self.get("/login")
        with self.client.session_transaction() as s:
            token = s.get("csrf")
        return self.client.post(path, data=json.dumps(data or {}), content_type="application/json",
                                headers={"X-CSRF-Token": token}, environ_base=self.env())

    def login(self, **extra):
        return self.post("/api/login", {"username": "admin", "password": PASSWORD, **extra})


class TotpTests(unittest.TestCase):

    def test_rfc_6238_codes(self):
        self.assertEqual(totp.code_at(RFC_SECRET, 59 // 30), "287082")
        self.assertEqual(totp.code_at(RFC_SECRET, 1111111109 // 30), "081804")

    def test_window_and_reuse(self):
        now = 1_800_000_000
        step = now // 30
        code = totp.code_at(RFC_SECRET, step - 1)
        self.assertEqual(totp.matching_step(RFC_SECRET, code, now=now), step - 1)
        self.assertIsNone(totp.matching_step(RFC_SECRET, code, last_step=step - 1, now=now))
        self.assertIsNone(totp.matching_step(RFC_SECRET, totp.code_at(RFC_SECRET, step - 3), now=now))
        self.assertIsNone(totp.matching_step(RFC_SECRET, "abc", now=now))

    def test_recovery_codes_work_once(self):
        codes, hashes = totp.new_recovery_codes()
        self.assertEqual(len(codes), totp.RECOVERY_COUNT)
        left = totp.use_recovery(hashes, codes[0].upper())
        self.assertEqual(len(left), totp.RECOVERY_COUNT - 1)
        self.assertIsNone(totp.use_recovery(left, codes[0]))


class TwoStepLoginTests(unittest.TestCase):

    def setUp(self):
        security._attempts.clear()
        reset_users(totp={"secret": RFC_SECRET, "last_step": None, "recovery": [totp.hash_recovery("abcd-efgh")]})

    def tearDown(self):
        reset_users()

    def test_password_alone_asks_for_the_code(self):
        browser = Browser()
        data = browser.login().get_json()
        self.assertTrue(data["need_code"])
        self.assertFalse(data["success"])
        self.assertEqual(browser.get("/api/routing").status_code, 401)

    def test_code_signs_in_once(self):
        code = totp.code_at(RFC_SECRET, int(time.time() // 30))
        browser = Browser()
        self.assertTrue(browser.login(code=code).get_json()["success"])
        self.assertEqual(browser.get("/api/routing").status_code, 200)
        # The same code again: refused.
        self.assertEqual(Browser("192.168.1.31").login(code=code).status_code, 401)

    def test_recovery_code(self):
        self.assertTrue(Browser().login(code="ABCD-EFGH").get_json()["success"])
        self.assertEqual(Browser("192.168.1.32").login(code="abcd-efgh").status_code, 401)

    def test_wrong_codes_count_towards_the_lockout(self):
        browser = Browser("192.168.1.33")
        for _ in range(security.MAX_FAILURES):
            browser.login(code="000000")
        self.assertEqual(browser.login(code="000000").status_code, 429)


class SetupTests(unittest.TestCase):

    def setUp(self):
        security._attempts.clear()
        reset_users()
        self.browser = Browser("192.168.1.40")
        self.browser.login()

    def tearDown(self):
        reset_users()

    def test_turn_on_and_off(self):
        setup = self.browser.post("/api/account/2fa/setup", {"password": PASSWORD}).get_json()
        self.assertTrue(setup["uri"].startswith("otpauth://totp/"))
        self.assertIn("<svg", setup["qr"])

        self.assertEqual(self.browser.post("/api/account/2fa/enable", {"code": "000000"}).status_code, 400)

        code = totp.code_at(setup["secret"], int(time.time() // 30))
        on = self.browser.post("/api/account/2fa/enable", {"code": code}).get_json()
        self.assertEqual(len(on["recovery_codes"]), totp.RECOVERY_COUNT)
        self.assertTrue(self.browser.get("/api/account/2fa").get_json()["enabled"])

        self.assertEqual(self.browser.post("/api/account/2fa/disable", {"password": "wrong"}).status_code, 403)
        self.assertTrue(self.browser.post("/api/account/2fa/disable", {"password": PASSWORD}).get_json()["success"])
        self.assertFalse(self.browser.get("/api/account/2fa").get_json()["enabled"])

    def test_setup_needs_the_password(self):
        self.assertEqual(self.browser.post("/api/account/2fa/setup", {"password": "nope"}).status_code, 403)


class DeviceTests(unittest.TestCase):

    def setUp(self):
        security._attempts.clear()
        reset_users()
        # Devices signed in by other tests.
        config.save_system("sessions", {}, secret=True)

    def test_list_and_sign_out_one(self):
        laptop, phone = Browser("192.168.1.50"), Browser("192.168.1.51", "Mozilla/5.0 (iPhone) Safari/604.1")
        laptop.login()
        phone.login()

        devices = laptop.get("/api/account/devices").get_json()["devices"]
        mine = [d for d in devices if d["current"]]
        self.assertEqual(mine[0]["device"], "Firefox on Windows")
        other = next(d for d in devices if d["address"] == "192.168.1.51")
        self.assertEqual(other["device"], "Safari on iPhone")

        self.assertTrue(laptop.post("/api/account/devices/sign-out", {"id": other["id"]}).get_json()["success"])
        self.assertEqual(phone.get("/api/routing").status_code, 401)
        self.assertEqual(laptop.get("/api/routing").status_code, 200)

    def test_sign_out_everywhere_else(self):
        browsers = [Browser(f"192.168.1.{60 + i}") for i in range(3)]
        for b in browsers:
            b.login()
        self.assertIn("2 other devices", browsers[0].post("/api/account/devices/sign-out-others").get_json()["message"])
        self.assertEqual(browsers[0].get("/api/routing").status_code, 200)
        for b in browsers[1:]:
            self.assertEqual(b.get("/api/routing").status_code, 401)

    def test_logout_removes_the_device(self):
        a, b = Browser("192.168.1.70"), Browser("192.168.1.71")
        a.login()
        b.login()
        b.post("/logout")
        addresses = [d["address"] for d in a.get("/api/account/devices").get_json()["devices"]]
        self.assertNotIn("192.168.1.71", addresses)


KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGl0qeg3eXuNqjiAg3KLKYM0fsk7yJzu8ah/+V/f+zZt laptop"


class SshKeyTests(unittest.TestCase):

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), ".ssh", "authorized_keys")
        self.patch = mock.patch.object(ssh, "keys_file", lambda: self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_add_list_remove(self):
        ok, _ = ssh.add_key(KEY)
        self.assertTrue(ok)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        keys = ssh.list_keys()
        self.assertEqual(keys[0]["comment"], "laptop")
        self.assertTrue(keys[0]["fingerprint"].startswith("SHA256:"))
        self.assertFalse(ssh.add_key(KEY)[0])
        with mock.patch.object(ssh, "get_status", lambda: {"password_login": True}):
            self.assertTrue(ssh.remove_key(keys[0]["fingerprint"])[0])
        self.assertEqual(ssh.list_keys(), [])

    def test_bad_keys_are_refused(self):
        for bad in ("hello", "ssh-ed25519 notbase64!", KEY + "\n" + KEY,
                    "ssh-rsa AAAAC3NzaC1lZDI1NTE5AAAAIGl0qeg3eXuNqjiAg3KLKYM0fsk7yJzu8ah/+V/f+zZt",
                    'command="sh" ' + KEY):
            self.assertFalse(ssh.add_key(bad)[0], bad)

    def test_last_key_stays_without_password_login(self):
        ssh.add_key(KEY)
        print_ = ssh.list_keys()[0]["fingerprint"]
        with mock.patch.object(ssh, "get_status", lambda: {"password_login": False}):
            self.assertFalse(ssh.remove_key(print_)[0])

    def test_password_login_needs_a_key(self):
        self.assertFalse(ssh.set_password_login(False)[0])


if __name__ == "__main__":
    unittest.main()
