"""
Two-step login: time-based one-time codes (TOTP, RFC 6238) as authenticator
apps make them (Aegis, Google Authenticator, 1Password, ...): 6 digits,
a new one every 30 seconds, from a secret shared once by QR code.

Standard library only. One step of clock difference either way is
accepted, and a code that was used once is not accepted again.
Recovery codes (for a lost phone) are kept as hashes and work once each.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

DIGITS = 6
PERIOD = 30
WINDOW = 1

RECOVERY_COUNT = 8

ISSUER = "Chardsoft Router"


def new_secret():
    """A random secret, as the Base32 text apps take (160 bits)."""

    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):

    secret = secret.upper().replace(" ", "")

    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code_at(secret, step):

    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF

    return str(number % 10 ** DIGITS).zfill(DIGITS)


def matching_step(secret, code, last_step=None, now=None):
    """
    The time step the code belongs to, or None (wrong, too old, or used
    before: steps up to last_step are spent).
    """

    code = "".join(str(code or "").split())

    if len(code) != DIGITS or not code.isdigit():
        return None

    current = int((time.time() if now is None else now) // PERIOD)

    for step in range(current - WINDOW, current + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step

    return None


def uri(secret, account):
    """otpauth:// link, the content of the QR code."""

    label = urllib.parse.quote(f"{ISSUER}:{account}")
    query = urllib.parse.urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": PERIOD})

    return f"otpauth://totp/{label}?{query}"


# -------------------------------------------------------------------
# Recovery codes
# -------------------------------------------------------------------

def _normal(code):

    return "".join(c for c in str(code or "").lower() if c.isalnum())


def hash_recovery(code):

    return hashlib.sha256(_normal(code).encode()).hexdigest()


def new_recovery_codes():
    """(codes to show once, their hashes to keep). Like "k3f9-x2m7"."""

    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"

    codes = [
        "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(RECOVERY_COUNT)
    ]

    return codes, [hash_recovery(code) for code in codes]


def use_recovery(hashes, code):
    """The hashes without the one this code matches, or None."""

    wanted = hash_recovery(code)

    if len(_normal(code)) != 8:
        return None

    for i, stored in enumerate(hashes or []):
        if hmac.compare_digest(stored, wanted):
            return hashes[:i] + hashes[i + 1:]

    return None
