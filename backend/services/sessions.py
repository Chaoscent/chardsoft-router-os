"""
Signed-in devices: every login gets an id, kept here with when and from
where it signed in. The session cookie carries the id; a session whose id
is gone is signed out. That is how "Sign out" on another device and
"Sign out everywhere else" work (a cookie alone cannot be taken back).

Kept in /var/lib/chardsoft-router-os/system/sessions.json, so a restart
of the app or the router signs nobody out. Ended and timed-out sessions
are dropped whenever the list is read or changed.
"""

import re
import secrets
import threading
import time

from services.config import load_system, save_system

RECORD = "sessions"

# last_seen is written at most this often (not on every request).
TOUCH_EVERY = 60

MAX_SESSIONS = 50

_lock = threading.Lock()


def _load():

    data = load_system(RECORD, {})

    return data if isinstance(data, dict) else {}


def _save(data):

    save_system(RECORD, data, secret=True)


def _alive(entry, now, idle, lifetime):

    if not isinstance(entry, dict):
        return False

    # last_seen lags up to TOUCH_EVERY behind the cookie's own activity.
    return (now - entry.get("created", 0) < lifetime
            and now - entry.get("last_seen", 0) < idle + TOUCH_EVERY)


def _pruned(data, idle, lifetime):

    now = time.time()

    return {sid: entry for sid, entry in data.items() if _alive(entry, now, idle, lifetime)}


def describe_agent(agent):
    """ "Firefox on Windows" from a User-Agent header (best effort)."""

    agent = str(agent or "")

    browser = next((name for pattern, name in (
        (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"SamsungBrowser", "Samsung Internet"),
        (r"Firefox/|FxiOS", "Firefox"), (r"Chrome/|CriOS", "Chrome"), (r"Safari/", "Safari"),
        (r"curl/", "curl"),
    ) if re.search(pattern, agent)), "Browser")

    system = next((name for pattern, name in (
        (r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android", "Android"),
        (r"Windows", "Windows"), (r"Mac OS X|Macintosh", "macOS"), (r"CrOS", "ChromeOS"),
        (r"Linux", "Linux"),
    ) if re.search(pattern, agent)), "")

    return f"{browser} on {system}" if system else browser


def add(user, address, agent, idle, lifetime):
    """A new signed-in device; its id goes into the session cookie."""

    sid = secrets.token_urlsafe(18)
    now = time.time()

    with _lock:

        data = _pruned(_load(), idle, lifetime)

        data[sid] = {
            "user": user,
            "created": now,
            "last_seen": now,
            "address": str(address or "")[:64],
            "device": describe_agent(agent)
        }

        # Never grows without end (oldest go first).
        if len(data) > MAX_SESSIONS:
            for old in sorted(data, key=lambda s: data[s]["last_seen"])[:len(data) - MAX_SESSIONS]:
                del data[old]

        _save(data)

    return sid


def check(sid, user):

    entry = _load().get(sid) if sid else None

    return isinstance(entry, dict) and entry.get("user") == user


def touch(sid, address=None):

    now = time.time()

    with _lock:

        data = _load()
        entry = data.get(sid)

        if not isinstance(entry, dict) or now - entry.get("last_seen", 0) < TOUCH_EVERY:
            return

        entry["last_seen"] = now

        if address:
            entry["address"] = str(address)[:64]

        _save(data)


def remove(sid):

    with _lock:

        data = _load()

        if data.pop(sid, None) is not None:
            _save(data)


def remove_user(user, keep=None):
    """Signs out every device of the user but `keep`; returns how many."""

    with _lock:

        data = _load()

        gone = [sid for sid, entry in data.items()
                if isinstance(entry, dict) and entry.get("user") == user and sid != keep]

        for sid in gone:
            del data[sid]

        if gone:
            _save(data)

    return len(gone)


def list_for(user, current, idle, lifetime):
    """The user's signed-in devices, newest activity first."""

    with _lock:

        data = _load()
        alive = _pruned(data, idle, lifetime)

        if len(alive) != len(data):
            _save(alive)

    devices = [
        {
            "id": sid,
            "device": entry.get("device", "Browser"),
            "address": entry.get("address", ""),
            "created": entry.get("created"),
            "last_seen": entry.get("last_seen"),
            "current": sid == current
        }
        for sid, entry in alive.items()
        if entry.get("user") == user
    ]

    return sorted(devices, key=lambda d: (not d["current"], -(d["last_seen"] or 0)))
