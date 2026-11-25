"""
Internet time: pause a device's internet, or give it offline times
(e.g. a bedtime from 22:00 to 07:00 on school days).

Unlike "Block device" (clients.py) this only stops the device's
traffic through the router: it keeps its address, can still reach the
router and its DNS, and is back the moment the pause ends. Paused
devices are rejected by MAC address in the CHARDSOFT-PAUSE chain, hooked
first into FORWARD for IPv4 and IPv6 (the CLAT's IPv4 and VPN client
tunnels are forwarded too, so they are covered).

Settings (the "internet_time" area, part of backups):

    devices: [{
        mac, name,
        paused_until    0: not paused, -1: until resumed, else a time
        allow_until     "Allow now" during an offline time, until then
        schedules: [{days: [0-6, Monday = 0], start: "22:00", end: "07:00"}]
    }]

An offline time that ends before it starts runs past midnight and
belongs to the day it starts (Friday 22:00-07:00 ends Saturday 07:00).
Times are the router's local time (System page). A clock that is not
set yet (no internet since boot, no RTC battery) skips the schedules
until it is, and says so.

The watcher (watch, every 15 s) follows the clock: it puts devices in
and out of the chain as their pauses and offline times start and end.
"""

import datetime
import threading
import time
from shutil import which

from services import transaction
from services.config import load_settings
from services.logs import log_event
from services.network import run_command, privileged

SETTINGS = "internet_time"

CHAIN = "CHARDSOFT-PAUSE"

MAX_DEVICES = 64
MAX_SCHEDULES = 8

# Before this the clock was never set (2025-01-01).
CLOCK_SET_AFTER = 1735689600

WATCH_SECONDS = 15

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_lock = threading.Lock()

# MACs in the chain, as last applied (None: not known yet).
_applied = None

_watching = False


def normalize_mac(mac):

    from services.clients import normalize_mac as normal

    return normal(mac)


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def get_settings():

    devices = load_settings(SETTINGS, {"devices": []}).get("devices")

    return {"devices": devices if isinstance(devices, list) else []}


def parse_time(text):
    """'22:00' -> minutes after midnight, or None."""

    try:
        hours, minutes = str(text).split(":")
        hours, minutes = int(hours), int(minutes)
    except ValueError:
        return None

    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None

    return hours * 60 + minutes


def _clean_schedule(rule):

    if not isinstance(rule, dict):
        raise ValueError("Invalid offline time.")

    days = rule.get("days")

    if not isinstance(days, list) or not days or \
            not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days):
        raise ValueError("Choose at least one day for each offline time.")

    start, end = parse_time(rule.get("start")), parse_time(rule.get("end"))

    if start is None or end is None:
        raise ValueError("Times look like 22:00.")

    if start == end:
        raise ValueError("An offline time must end at another time than it starts.")

    return {
        "days": sorted(set(days)),
        "start": f"{start // 60:02d}:{start % 60:02d}",
        "end": f"{end // 60:02d}:{end % 60:02d}"
    }


def _clean_device(device):

    if not isinstance(device, dict):
        raise ValueError("Invalid device.")

    mac = normalize_mac(device.get("mac"))

    if not mac:
        raise ValueError("Invalid MAC address.")

    paused, allow = device.get("paused_until", 0), device.get("allow_until", 0)

    for value in (paused, allow):
        if not isinstance(value, int) or isinstance(value, bool) or value < -1:
            raise ValueError("Invalid pause.")

    schedules = device.get("schedules", [])

    if not isinstance(schedules, list):
        raise ValueError("Invalid offline times.")

    if len(schedules) > MAX_SCHEDULES:
        raise ValueError(f"At most {MAX_SCHEDULES} offline times per device.")

    name = str(device.get("name") or "").strip()

    if len(name) > 64 or any(ord(c) < 0x20 for c in name):
        raise ValueError("Invalid device name.")

    return {
        "mac": mac,
        "name": name,
        "paused_until": paused,
        "allow_until": max(allow, 0),
        "schedules": [_clean_schedule(r) for r in schedules]
    }


def validate_settings(data):
    """(True, clean settings) or (False, message)."""

    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        return False, "Invalid settings."

    if len(data["devices"]) > MAX_DEVICES:
        return False, f"At most {MAX_DEVICES} devices."

    clean, seen = [], set()

    try:
        for device in data["devices"]:

            device = _clean_device(device)

            if device["mac"] in seen:
                return False, f"{device['mac']} is there twice."

            seen.add(device["mac"])
            clean.append(device)

    except ValueError as e:
        return False, str(e)

    return True, {"devices": clean}


# ------------------------------------------------------------------
# The clock
# ------------------------------------------------------------------

def clock_is_set(now=None):

    return (now if now is not None else time.time()) >= CLOCK_SET_AFTER


def _at(day, minutes):
    """The datetime of `minutes` after midnight on the date `day`."""

    return datetime.datetime.combine(day, datetime.time(minutes // 60, minutes % 60))


def window_end(rule, now):
    """When the offline time running at `now` ends, or None if it does not run."""

    start, end = parse_time(rule["start"]), parse_time(rule["end"])
    minute = now.hour * 60 + now.minute
    today = now.date()

    if start < end:

        if now.weekday() in rule["days"] and start <= minute < end:
            return _at(today, end)

        return None

    # Past midnight: tonight's part, or the rest of last night's.
    if now.weekday() in rule["days"] and minute >= start:
        return _at(today + datetime.timedelta(days=1), end)

    if (now.weekday() - 1) % 7 in rule["days"] and minute < end:
        return _at(today, end)

    return None


def next_start(rules, now):
    """The next time an offline time starts, within a week, or None."""

    found = None

    for offset in range(8):

        day = now.date() + datetime.timedelta(days=offset)

        for rule in rules:

            if day.weekday() not in rule["days"]:
                continue

            begin = _at(day, parse_time(rule["start"]))

            if begin > now and (found is None or begin < found):
                found = begin

    return found


def current_window(rules, now):
    """The latest end of the offline times running now, or None."""

    ends = [e for e in (window_end(r, now) for r in rules) if e]

    return max(ends) if ends else None


def describe_time(moment, now):
    """'21:30', 'tomorrow 07:00' or 'Sat 07:00'."""

    clock = f"{moment:%H:%M}"
    days = (moment.date() - now.date()).days

    if days == 0:
        return clock

    if days == 1:
        return f"tomorrow {clock}"

    return f"{DAY_NAMES[moment.weekday()]} {clock}"


def device_state(device, now_ts=None):
    """
    {paused, kind (pause/schedule/None), until (ts or None), label,
    next (ts or None), next_label, clock_set}.
    """

    now_ts = time.time() if now_ts is None else now_ts
    now = datetime.datetime.fromtimestamp(now_ts)
    clock = clock_is_set(now_ts)

    paused_until = device.get("paused_until", 0)
    schedules = device.get("schedules", [])

    state = {"paused": False, "kind": None, "until": None, "label": "Allowed",
             "next": None, "next_label": "", "clock_set": clock}

    if paused_until == -1:
        return {**state, "paused": True, "kind": "pause", "label": "Paused until you resume it"}

    if clock and paused_until > now_ts:
        until = datetime.datetime.fromtimestamp(paused_until)
        return {**state, "paused": True, "kind": "pause", "until": paused_until,
                "label": f"Paused until {describe_time(until, now)}"}

    if not schedules:
        return state

    if not clock:
        return {**state, "label": "Allowed (offline times wait until the router's clock is set)"}

    end = current_window(schedules, now)

    if end and device.get("allow_until", 0) < now_ts:
        return {**state, "paused": True, "kind": "schedule", "until": end.timestamp(),
                "label": f"Offline time until {describe_time(end, now)}"}

    if end:
        state["label"] = f"Allowed now, until {describe_time(datetime.datetime.fromtimestamp(device['allow_until']), now)}"

    upcoming = next_start(schedules, now)

    if upcoming:
        state["next"] = upcoming.timestamp()
        state["next_label"] = f"Next offline time: {describe_time(upcoming, now)}"

    return state


def paused_macs(settings=None, now_ts=None):

    settings = settings or get_settings()

    return sorted(d["mac"] for d in settings["devices"] if device_state(d, now_ts)["paused"])


# ------------------------------------------------------------------
# The chain
# ------------------------------------------------------------------

def _tools():

    return [t for t in (which("iptables"), which("ip6tables")) if t]


def _write_chain(macs):

    for tool in _tools():

        def filt(*args):
            return run_command(privileged([tool, *args]))

        filt("-N", CHAIN)

        ok, out = filt("-F", CHAIN)

        if not ok:
            return False, f"Could not reset the pause list: {out}"

        for mac in macs:

            ok, out = filt("-A", CHAIN, "-m", "mac", "--mac-source", mac, "-j", "REJECT")

            if not ok:
                return False, f"Could not pause {mac}: {out}"

        hooked, _ = filt("-C", "FORWARD", "-j", CHAIN)

        if not hooked:

            ok, out = filt("-I", "FORWARD", "1", "-j", CHAIN)

            if not ok:
                return False, f"Could not activate the pause list: {out}"

    return True, "OK"


def _hooked():

    tools = _tools()

    if not tools:
        return True

    ok, _ = run_command(privileged([tools[0], "-C", "FORWARD", "-j", CHAIN]))

    return ok


def apply():
    """Brings the chain in line with the settings and the clock."""

    global _applied

    with _lock:

        macs = paused_macs()

        if not _tools():
            _applied = macs
            return (True, "Saved.") if not macs else (False, "iptables is not available.")

        ok, message = _write_chain(macs)

        if not ok:
            _applied = None
            return False, message

        _applied = macs

    count = len(macs)

    return True, f"{count} device{'s' if count != 1 else ''} paused." if count else "No device paused."


def remove():
    """Takes the chain out (uninstall)."""

    for tool in _tools():

        for _ in range(5):
            ok, _out = run_command(privileged([tool, "-D", "FORWARD", "-j", CHAIN]))
            if not ok:
                break

        run_command(privileged([tool, "-F", CHAIN]))
        run_command(privileged([tool, "-X", CHAIN]))


# ------------------------------------------------------------------
# What the Clients page calls
# ------------------------------------------------------------------

def _entry(devices, mac, name):

    for device in devices:
        if device["mac"] == mac:
            if name:
                device["name"] = name
            return device

    device = {"mac": mac, "name": name, "paused_until": 0, "allow_until": 0, "schedules": []}
    devices.append(device)

    return device


def _save(devices):

    # Entries with nothing left to do are dropped.
    devices = [d for d in devices if d["paused_until"] or d["schedules"]]

    ok, clean = validate_settings({"devices": devices})

    if not ok:
        return transaction.result(False, clean)

    return transaction.change(SETTINGS, clean)


def _name(name):

    return str(name or "").strip()[:64]


def _told(outcome, mac, back=False):
    """The device's new state as the message, instead of the chain's count."""

    if not outcome["success"]:
        return outcome

    state = states().get(mac)

    if back and not state:
        message = "The internet is back."
    elif state and state["label"].startswith("Allowed now"):
        message = state["label"].replace("Allowed now, until", "Allowed until") + "."
    else:
        message = (state["label"] if state else "Saved") + "."

    return {**outcome, "message": message}


def pause_end(choice, now_ts=None):
    """
    When a pause chosen on the page ends: minutes as a number,
    "morning" (the next 07:00) or "resume" (-1). None for nonsense.
    """

    now_ts = time.time() if now_ts is None else now_ts

    if choice == "resume":
        return -1

    if choice == "morning":

        now = datetime.datetime.fromtimestamp(now_ts)
        morning = _at(now.date(), 7 * 60)

        if morning <= now:
            morning += datetime.timedelta(days=1)

        return int(morning.timestamp())

    if isinstance(choice, int) and not isinstance(choice, bool) and 1 <= choice <= 24 * 60:
        return int(now_ts) + choice * 60

    return None


def pause(mac, choice, name=""):

    mac = normalize_mac(mac)

    if not mac:
        return transaction.result(False, "Invalid MAC address.")

    until = pause_end(choice)

    if until is None:
        return transaction.result(False, "Choose how long to pause.")

    if until != -1 and not clock_is_set():
        return transaction.result(False, "The router's clock is not set yet. Pause until you resume instead.")

    devices = get_settings()["devices"]
    device = _entry(devices, mac, _name(name))
    device["paused_until"] = until

    return _told(_save(devices), mac)


def resume(mac):
    """Ends a pause, or allows the device during the offline time running now."""

    mac = normalize_mac(mac)
    devices = get_settings()["devices"]
    device = next((d for d in devices if d["mac"] == mac), None)

    if not device:
        return transaction.result(False, "This device is not paused.")

    state = device_state(device)

    if not state["paused"]:
        return transaction.result(False, "This device is not paused.")

    device["paused_until"] = 0

    # Still inside an offline time: allow until it ends.
    after = device_state(device)

    if after["paused"] and after["kind"] == "schedule":
        device["allow_until"] = int(after["until"])

    return _told(_save(devices), mac, back=True)


def set_schedules(mac, schedules, name=""):

    mac = normalize_mac(mac)

    if not mac:
        return transaction.result(False, "Invalid MAC address.")

    devices = get_settings()["devices"]
    device = _entry(devices, mac, _name(name))
    device["schedules"] = schedules if isinstance(schedules, list) else None
    device["allow_until"] = 0

    if device["schedules"] is None:
        return transaction.result(False, "Invalid offline times.")

    return _save(devices)


def states():
    """{mac: state plus the saved schedules} for every device with settings."""

    now_ts = time.time()

    return {
        d["mac"]: {**device_state(d, now_ts), "schedules": d["schedules"], "name": d["name"]}
        for d in get_settings()["devices"]
    }


# ------------------------------------------------------------------
# The watcher
# ------------------------------------------------------------------

def _names():

    return {d["mac"]: d["name"] or d["mac"] for d in get_settings()["devices"]}


def check():
    """One look at the clock: changes the chain when a pause started or ended."""

    before = _applied
    macs = paused_macs()

    if macs == before and _hooked():
        return

    ok, message = apply()

    if not ok:
        log_event("clients", f"Internet time: {message}", "error")
        return

    if before is None:
        return

    names = _names()

    for mac in sorted(set(macs) - set(before)):
        log_event("clients", f"{names.get(mac, mac)} is offline now (internet time).")

    for mac in sorted(set(before) - set(macs)):
        log_event("clients", f"{names.get(mac, mac)} is online again (internet time).")


def watch():

    global _watching

    if _watching:
        return

    _watching = True

    def loop():

        while True:

            try:
                check()
            except Exception as e:
                print(f"[internet time] {e}")

            time.sleep(WATCH_SECONDS)

    threading.Thread(target=loop, daemon=True, name="internet-time").start()
