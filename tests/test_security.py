"""
The web app's security checks through Flask's test client: sign-in,
CSRF, rate limiting, security headers, sessions ending on a password
change, the closed setup routes.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

SANDBOX = tempfile.mkdtemp(prefix="chardsoft-test-")

for name, folder in (("CHAOS_DEFAULTS_DIR", "etc"), ("CHAOS_STATE_DIR", "var"), ("CHAOS_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))
    os.makedirs(os.environ[name], exist_ok=True)

os.environ.setdefault("CHAOS_SKIP_STARTUP", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "x" * 32)

from werkzeug.security import generate_password_hash  # noqa: E402

from services import config  # noqa: E402

PASSWORD = "test-pass-1"

config.save_persistent("users", {"admin": {"password": generate_password_hash(PASSWORD)}}, secret=True)

import app as appmod  # noqa: E402
import security  # noqa: E402

app = appmod.app


class Browser:
    """A test client that sends the CSRF token like static/js/csrf.js."""

    def __init__(self, address="192.168.1.20"):
        self.client = app.test_client()
        self.address = address

    def get(self, path, **kw):
        return self.client.get(path, environ_base={"REMOTE_ADDR": self.address}, **kw)

    def token(self):
        self.get("/login")
        with self.client.session_transaction() as s:
            return s.get("csrf")

    def post(self, path, data=None, token=True, headers=None):
        h = {"X-CSRF-Token": self.token()} if token else {}
        h.update(headers or {})
        return self.client.post(path, data=json.dumps(data or {}), content_type="application/json",
                                headers=h, environ_base={"REMOTE_ADDR": self.address})

    def login(self, password=PASSWORD):
        return self.post("/api/login", {"username": "admin", "password": password})


class SignInTests(unittest.TestCase):

    def setUp(self):
        security._attempts.clear()

    def test_api_needs_a_session(self):
        for path in ("/api/dashboard", "/api/routing", "/api/dhcp", "/api/system/update"):
            self.assertEqual(Browser().get(path).status_code, 401, path)

    def test_pages_redirect_to_login(self):
        response = Browser().get("/fragment/dashboard")
        self.assertEqual(response.status_code, 401)

    def test_login_works_and_opens_the_api(self):
        browser = Browser()
        self.assertEqual(browser.login().status_code, 200)
        self.assertEqual(browser.get("/api/routing").status_code, 200)

    def test_wrong_password_and_unknown_user_fail_alike(self):
        browser = Browser()
        wrong = browser.login("nope-nope-nope")
        unknown = browser.post("/api/login", {"username": "root", "password": PASSWORD})
        self.assertEqual(wrong.status_code, unknown.status_code)
        self.assertEqual(wrong.get_json()["message"], unknown.get_json()["message"])

    def test_lockout_after_repeated_failures(self):
        browser = Browser("192.168.1.66")
        for _ in range(security.MAX_FAILURES):
            browser.login("wrong-password")
        response = browser.login()
        self.assertEqual(response.status_code, 429)
        # Other devices can still sign in.
        self.assertEqual(Browser("192.168.1.67").login().status_code, 200)

    def test_forwarded_for_is_ignored_from_the_lan(self):
        browser = Browser("192.168.1.70")
        for i in range(security.MAX_FAILURES):
            browser.post("/api/login", {"username": "admin", "password": "x"},
                         headers={"X-Forwarded-For": f"10.0.0.{i}"})
        self.assertEqual(browser.login().status_code, 429)


class CsrfTests(unittest.TestCase):

    def setUp(self):
        security._attempts.clear()
        self.browser = Browser()
        self.browser.login()

    def test_post_without_token_is_refused(self):
        response = self.browser.post("/api/routing/settings", {"nat": True}, token=False)
        self.assertEqual(response.status_code, 403)

    def test_post_with_a_wrong_token_is_refused(self):
        response = self.browser.post("/api/routing/settings", {}, token=False,
                                     headers={"X-CSRF-Token": "forged"})
        self.assertEqual(response.status_code, 403)

    def test_post_from_another_site_is_refused(self):
        response = self.browser.post("/api/routing/settings", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_login_form_is_protected_too(self):
        response = Browser().post("/api/login", {"username": "admin", "password": PASSWORD}, token=False)
        self.assertEqual(response.status_code, 403)


class HeaderTests(unittest.TestCase):

    def test_pages_cannot_be_framed(self):
        response = Browser().get("/login")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_api_answers_are_not_cached(self):
        self.assertEqual(Browser().get("/api/routing").headers["Cache-Control"], "no-store")

    def test_session_cookie_flags(self):
        security._attempts.clear()
        cookie = Browser().login().headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)


class SessionTests(unittest.TestCase):

    def setUp(self):
        security._attempts.clear()

    def tearDown(self):
        users = {"admin": {"password": generate_password_hash(PASSWORD)}}
        config.save_persistent("users", users, secret=True)
        config.save_running("users", users, secret=True)

    def test_password_change_ends_other_sessions(self):
        phone, laptop = Browser("192.168.1.30"), Browser("192.168.1.31")
        phone.login()
        laptop.login()
        response = laptop.post("/api/system/password", {"current_password": PASSWORD,
                                                        "new_password": "a-new-password-2"})
        self.assertTrue(response.get_json()["success"], response.get_json())
        self.assertEqual(phone.get("/api/routing").status_code, 401)
        self.assertEqual(laptop.get("/api/routing").status_code, 401)

    def test_logout_ends_the_session(self):
        browser = Browser()
        browser.login()
        browser.post("/logout")
        self.assertEqual(browser.get("/api/routing").status_code, 401)

    def test_setup_is_closed_once_set_up(self):
        browser = Browser()
        self.assertEqual(browser.get("/setup").status_code, 404)
        self.assertEqual(browser.get("/api/setup/internet").status_code, 404)
        response = browser.post("/api/setup/complete", {"username": "evil", "password": "x" * 12})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
