import hashlib
import time
from functools import wraps

from flask import session, redirect, url_for, request, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

from services.config import (
    load_defaults,
    load_running,
    load_persistent
)

from services import transaction
from services import sessions
from services import totp


# users.json in /var/lib/chardsoft-router-os, created by /setup. There is
# no default login: without an account the router is in setup mode.
USERS = "users"

# security.json: default timeouts in /etc/chardsoft-router-os,
# the user's custom timeouts in /var/lib/chardsoft-router-os.
SECURITY = "security"

BUILTIN_SECURITY = {
    "idle_timeout": 300,        # 5 minutes
    "absolute_timeout": 3600    # 1 hour
}


def load_users():
    users = load_running(USERS, None) or load_persistent(USERS, None)

    return users if isinstance(users, dict) else {}


def save_users(users):
    return transaction.change(USERS, users)["success"]


def load_security_config():
    """
    Only the user's custom timeouts; empty when none are set.
    """

    data = load_running(SECURITY, {})

    return data if isinstance(data, dict) else {}


def save_security_config(config):
    return transaction.change(SECURITY, config)["success"]


def _timeout(key):
    default = int(load_defaults(SECURITY, BUILTIN_SECURITY)[key])

    try:
        value = int(load_security_config().get(key))
    except (TypeError, ValueError):
        return default

    return value if value > 0 else default


def get_idle_timeout():
    return _timeout("idle_timeout")


def get_absolute_timeout():
    return _timeout("absolute_timeout")


def set_session_timeouts(idle_timeout=None, absolute_timeout=None):
    config = load_security_config()

    if idle_timeout is None:
        config.pop("idle_timeout", None)
    else:
        config["idle_timeout"] = int(idle_timeout)

    if absolute_timeout is None:
        config.pop("absolute_timeout", None)
    else:
        config["absolute_timeout"] = int(absolute_timeout)

    save_security_config(config)


# Checked when the user doesn't exist, so a wrong name takes as long
# as a wrong password and the timing doesn't reveal valid names.
_DUMMY_HASH = generate_password_hash("chardsoft-no-such-user")


def verify_login(username, password):
    users = load_users()

    if username not in users:
        check_password_hash(_DUMMY_HASH, password)
        return False

    return check_password_hash(
        users[username]["password"],
        password
    )


def password_stamp(username, users=None):
    """
    A short fingerprint of the user's password hash. Sessions carry it;
    a new password changes it and so ends every session signed in with
    the old one, also on other devices (cookies can't be revoked
    otherwise).
    """

    users = load_users() if users is None else users
    entry = users.get(username)

    if not entry:
        return None

    return hashlib.sha256(entry["password"].encode()).hexdigest()[:16]


def login_user(username, address=None, agent=None):
    now = time.time()

    session.clear()

    session["user"] = username
    session["stamp"] = password_stamp(username)
    session["created_at"] = now
    session["last_activity"] = now

    # This device in the list of signed-in devices.
    session["sid"] = sessions.add(
        username, address, agent if agent is not None else request.headers.get("User-Agent"),
        get_idle_timeout(), get_absolute_timeout()
    )


def logout_user():
    sessions.remove(session.get("sid"))
    session.clear()


def signed_in_devices():
    return sessions.list_for(
        session.get("user"), session.get("sid"), get_idle_timeout(), get_absolute_timeout()
    )


def sign_out_device(sid):
    """Signs out one of the user's other devices."""

    user = session.get("user")

    if not sid or sid == session.get("sid"):
        return False, "This is the device you are using: use Sign out."

    if not sessions.check(sid, user):
        return False, "That device is already signed out."

    sessions.remove(sid)

    return True, "Signed out."


def sign_out_others():
    """Every device of this user but this one; returns how many."""

    return sessions.remove_user(session.get("user"), keep=session.get("sid"))


def is_logged_in():
    if "user" not in session:
        return False

    now = time.time()

    created_at = session.get("created_at")
    last_activity = session.get("last_activity")

    if created_at is None or last_activity is None:
        session.clear()
        return False

    # Password changed (or account gone) since this session signed in.
    stamp = password_stamp(session["user"])

    if not stamp or session.get("stamp") != stamp:
        session.clear()
        return False

    # Signed out from another device (or from before devices were listed).
    if not sessions.check(session.get("sid"), session["user"]):
        session.clear()
        return False

    absolute_timeout = get_absolute_timeout()
    idle_timeout = get_idle_timeout()

    if now - created_at >= absolute_timeout:
        session.clear()
        return False

    if now - last_activity >= idle_timeout:
        session.clear()
        return False

    return True


def update_session_activity():
    if "user" in session:
        session["last_activity"] = time.time()
        sessions.touch(session.get("sid"))


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):

        if not is_logged_in():

            if request.path.startswith("/api/") or \
               request.path.startswith("/fragment/"):

                return jsonify({"error": "unauthorized"}), 401

            return redirect(url_for("login_page"))

        update_session_activity()

        return func(*args, **kwargs)

    return wrapper


def change_password(current_password, new_password):
    username = session.get("user")

    if not username:
        return False, "Not authenticated"

    users = load_users()

    if username not in users:
        return False, "User not found"

    if not check_password_hash(
        users[username]["password"],
        current_password
    ):
        return False, "Current password is incorrect"

    users[username]["password"] = generate_password_hash(
        new_password
    )

    if not save_users(users):
        return False, "Password could not be saved"

    # The new password signs out every device, this one too.
    sessions.remove_user(username)
    session.clear()

    return True, "Password changed successfully"

# -------------------------------------------------------------------
# Two-step login (services/totp.py)
# -------------------------------------------------------------------

def second_factor(username):
    """The user's two-step login record, or None when it is off."""

    entry = load_users().get(username) or {}
    record = entry.get("totp")

    return record if isinstance(record, dict) and record.get("secret") else None


def two_factor_status(username):

    record = second_factor(username)

    return {
        "enabled": record is not None,
        "recovery_left": len(record.get("recovery") or []) if record else 0
    }


def verify_second_factor(username, code):
    """
    True for a current code from the app (not used before) or an unused
    recovery code (then spent). Only call after the password was right.
    """

    users = load_users()
    entry = users.get(username) or {}
    record = entry.get("totp")

    if not isinstance(record, dict) or not record.get("secret"):
        return False

    step = totp.matching_step(record["secret"], code, record.get("last_step"))

    if step is not None:
        record["last_step"] = step
        return save_users(users)

    left = totp.use_recovery(record.get("recovery") or [], code)

    if left is not None:
        record["recovery"] = left
        return save_users(users)

    return False


def enable_two_factor(username, secret, code):
    """(True, recovery codes) once a code from the app proves the setup."""

    step = totp.matching_step(secret, code)

    if step is None:
        return False, "Wrong code. Check the time on your phone and try the newest code."

    users = load_users()

    if username not in users:
        return False, "User not found."

    codes, hashes = totp.new_recovery_codes()

    users[username]["totp"] = {"secret": secret, "last_step": step, "recovery": hashes, "since": int(time.time())}

    if not save_users(users):
        return False, "Two-step login could not be saved."

    return True, codes


def new_recovery_codes(username):

    users = load_users()
    record = (users.get(username) or {}).get("totp")

    if not isinstance(record, dict):
        return False, "Two-step login is off."

    codes, hashes = totp.new_recovery_codes()
    record["recovery"] = hashes

    if not save_users(users):
        return False, "The codes could not be saved."

    return True, codes


def disable_two_factor(username):

    users = load_users()

    if username not in users or "totp" not in users[username]:
        return True, "Two-step login is off."

    del users[username]["totp"]

    if not save_users(users):
        return False, "Two-step login could not be turned off."

    return True, "Two-step login is off."
