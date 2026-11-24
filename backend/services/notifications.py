"""
The header's notifications: the router events worth a look, taken from
the event log (services/logs.py). That is every warning and error, and
info events logged with notify=True (a modem restarted by the
watchdog, a new billing period, a new device on the network, ...).

What was read is one point in time ("seen", system/notifications.json):
opening the list marks everything up to its newest entry as read. The
header asks every second, so the unread count is cached until the
event log or "seen" changes.
"""

import os
import threading

from services.config import load_system, save_system, system_path
from services.logs import EVENTS, get_events

SEEN = "notifications"

LIMIT = 30

# Where each source's notifications are dealt with.
LINKS = {
    "data": "#/modem?show=data-limit",
    "modem": "#/modem",
    "clients": "#/clients",
    "routes": "#/routes",
    "apps": "#/apps",
    "vpn": "#/vpn",
    "system": "#/system",
    "setup": "#/system",
    "backup": "#/system",
}

_lock = threading.Lock()
_cache = {"key": None, "count": 0}


def is_notification(event):

    return event.get("level") in ("warning", "error") or bool(event.get("notify"))


def seen():

    data = load_system(SEEN, {})

    try:
        return float(data.get("seen", 0))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _events_mtime():

    try:
        return os.stat(system_path(EVENTS)).st_mtime_ns
    except OSError:
        return 0


def get_notifications(limit=LIMIT):

    since = seen()

    items = [
        {
            "time": e.get("time", 0),
            "level": e.get("level", "info"),
            "source": e.get("source", ""),
            "message": e.get("message", ""),
            "link": LINKS.get(e.get("source"), "#/logs"),
            "unread": e.get("time", 0) > since
        }
        for e in get_events(limit=2000) if is_notification(e)
    ][:limit]

    return {"items": items, "unread": sum(i["unread"] for i in items)}


def unread_count():

    key = (_events_mtime(), seen())

    with _lock:

        if _cache["key"] != key:
            _cache["count"] = sum(
                1 for e in get_events(limit=2000)
                if is_notification(e) and e.get("time", 0) > key[1]
            )
            _cache["key"] = key

        return min(_cache["count"], 99)


def mark_read(until):
    """Everything up to `until` (an event time) is read."""

    try:
        until = float(until)
    except (TypeError, ValueError):
        return False

    if until > seen():
        save_system(SEEN, {"seen": until})

    return True
