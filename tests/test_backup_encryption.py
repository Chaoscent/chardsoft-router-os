"""
Encrypted backups: round trip, wrong password, tampering.

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

for name, folder in (("CHARDSOFT_DEFAULTS_DIR", "etc"), ("CHARDSOFT_STATE_DIR", "var"), ("CHARDSOFT_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHARDSOFT_SKIP_STARTUP", "1")

from services import backup  # noqa: E402


def plain_backup():

    content = {"config": {"dashboard": {"ping_enabled": True}}, "system": {}}

    return {
        "format": backup.FORMAT, "version": backup.VERSION, "created": 1760000000,
        "hostname": "router", **content, "checksum": backup.checksum(content)
    }


class Encryption(unittest.TestCase):

    def test_round_trip(self):

        sealed = backup.encrypt_backup(plain_backup(), "correct horse")

        self.assertNotIn("ping_enabled", json.dumps(sealed))

        ok, opened = backup.parse_backup(json.dumps(sealed), "correct horse")

        self.assertTrue(ok, opened)
        self.assertEqual(opened["config"], plain_backup()["config"])

    def test_needs_password(self):

        sealed = backup.encrypt_backup(plain_backup(), "correct horse")

        ok, message = backup.parse_backup(json.dumps(sealed))

        self.assertFalse(ok)
        self.assertIn("encrypted", message)

    def test_wrong_password(self):

        sealed = backup.encrypt_backup(plain_backup(), "correct horse")

        ok, message = backup.parse_backup(json.dumps(sealed), "wrong horse")

        self.assertFalse(ok)
        self.assertIn("Wrong password", message)

    def test_header_tampering_detected(self):

        sealed = backup.encrypt_backup(plain_backup(), "correct horse")
        sealed["hostname"] = "someone-else"

        ok, _ = backup.parse_backup(json.dumps(sealed), "correct horse")

        self.assertFalse(ok)

    def test_expensive_kdf_refused(self):

        sealed = backup.encrypt_backup(plain_backup(), "correct horse")
        sealed["encryption"]["kdf"]["n"] = 2 ** 24

        ok, message = backup.parse_backup(json.dumps(sealed), "correct horse")

        self.assertFalse(ok)
        self.assertIn("not allowed", message)

    def test_short_password_refused(self):

        with self.assertRaises(ValueError):
            backup.encrypt_backup(plain_backup(), "short")

    def test_plain_backup_still_works(self):

        ok, opened = backup.parse_backup(json.dumps(plain_backup()))

        self.assertTrue(ok, opened)


if __name__ == "__main__":
    unittest.main()
