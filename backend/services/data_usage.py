"""
Mobile data used this billing period, with an optional monthly limit.

The modem's interface counters (wwan*) are read every 30 seconds and
added up; download and upload both count, as with the carrier. The
total is written to STATE_DIR/data_usage_counter.json every few
minutes, so a reboot loses at most that much. A counter that went down
(the modem restarted, the driver reloaded) starts again from zero, and
the new value counts in full.

Settings (the "data_usage" area, part of backups):

    limit_gb        the plan's data per period; 0 = no limit
    billing_day     the day of the month the plan starts over (1-28)
    warn_percent    a warning in the Logs at this share of the limit
    block_at_limit  at the limit, devices on the LAN lose the internet
                    (the router itself and the dashboard keep working)
                    until the next period or "Resume" on the dashboard

The block is the CHAOS-QUOTA chain in filter/FORWARD: it rejects
everything forwarded out of the modem (wwan+). The CLAT's IPv4 leaves
through the modem as IPv6, so it is covered too.

The router's own count is close to the carrier's, never the same: the
carrier rounds per session and may count differently. "Set used
amount" corrects it to what the carrier's app shows.
"""

import atexit
import datetime
import threading
import time
from shutil import which

from services.config import load_settings, load_persistent, save_persistent
from services.logs import log_event
from services.network import run_command, privileged

SETTINGS = "data_usage"
COUNTER = "data_usage_counter"

CHAIN = "CHAOS-QUOTA"

DEFAULT_SETTINGS = {
    "limit_gb": 0,
    "billing_day": 1,
    "warn_percent": 80,
    "block_at_limit": False
}

SAMPLE_SECONDS = 30
SAVE_SECONDS = 300

MAX_LIMIT_GB = 100000
HISTORY_PERIODS = 6

GB = 1000 ** 3

_lock = threading.Lock()

# The counter in memory; None until loaded.
_state = None
_saved_at = 0.0

# Whether the block is in place, as last applied (None: not known yet).
_blocked = None


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def get_settings():

    settings = load_settings(SETTINGS, DEFAULT_SETTINGS)

    return {key: settings.get(key, value) for key, value in DEFAULT_SETTINGS.items()}


def validate_settings(data):
    """(True, clean settings) or (False, message)."""

    data = data if isinstance(data, dict) else {}

    try:
        limit = float(data.get("limit_gb", 0) or 0)
    except (TypeError, ValueError):
        return False, "The data limit must be a number of GB."

    if not 0 <= limit <= MAX_LIMIT_GB:
        return False, f"The data limit must be between 0 and {MAX_LIMIT_GB} GB."

    day = data.get("billing_day", 1)

    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 28:
        return False, "The plan starts over on a day from 1 to 28."

    warn = data.get("warn_percent", 80)

    if isinstance(warn, bool) or not isinstance(warn, int) or not 50 <= warn <= 99:
        return False, "Warn at 50 to 99 percent."

    block = data.get("block_at_limit", False)

    if not isinstance(block, bool):
        return False, "Invalid value."

    # Whole GB stay whole ("20", not "20.0").
    limit = int(limit) if limit == int(limit) else round(limit, 2)

    return True, {"limit_gb": limit, "billing_day": day, "warn_percent": warn, "block_at_limit": block}


# ------------------------------------------------------------------
# Billing periods
# ------------------------------------------------------------------

def period_start(today, billing_day):
    """The first day of the billing period that holds `today`."""

    if today.day >= billing_day:
        return today.replace(day=billing_day)

    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)

    return datetime.date(year, month, billing_day)


def next_period_start(start):

    year, month = (start.year, start.month + 1) if start.month < 12 else (start.year + 1, 1)

    return datetime.date(year, month, start.day)


# ------------------------------------------------------------------
# Counting
# ------------------------------------------------------------------

def read_counters():
    """{interface: (rx_bytes, tx_bytes)} of the modem's interfaces."""

    counters = {}

    try:

        with open("/proc/net/dev") as f:

            for line in f.readlines()[2:]:

                name, _, data = line.partition(":")
                name = name.strip()
                parts = data.split()

                if name.startswith("wwan") and len(parts) >= 9:
                    counters[name] = (int(parts[0]), int(parts[8]))

    except OSError:
        pass

    return counters


def empty_state(start):

    return {
        "period": start.isoformat(),
        "rx": 0,
        "tx": 0,
        # Added by "Set used amount" (can be negative).
        "offset": 0,
        "last": {},
        "warned": False,
        "limit_logged": False,
        "resumed": False,
        "today": {"date": None, "bytes": 0},
        "history": []
    }


def _load():

    global _state

    if _state is not None:
        return _state

    data = load_persistent(COUNTER, None)

    today = datetime.date.today()
    fresh = empty_state(period_start(today, get_settings()["billing_day"]))

    if isinstance(data, dict):
        fresh.update({key: data[key] for key in fresh if key in data})
        # After a reboot the interface counters start at zero again.
        fresh["last"] = {}

    _state = fresh

    return _state


def _save(force=False):

    global _saved_at

    if force or time.time() - _saved_at >= SAVE_SECONDS:
        save_persistent(COUNTER, _state)
        _saved_at = time.time()


def used_bytes(state):

    return max(0, state["rx"] + state["tx"] + state.get("offset", 0))


def _roll_period(state, today, settings):
    """Starts a new period when the billing day passed. True if it did."""

    start = period_start(today, settings["billing_day"])

    if state["period"] == start.isoformat():
        return False

    history = [{"period": state["period"], "bytes": used_bytes(state)}] + list(state.get("history", []))

    state.update(empty_state(start), history=history[:HISTORY_PERIODS], last=state["last"])

    log_event("data", f"New billing period from {start.isoformat()}: the mobile data count starts at zero.")

    return True


def add_sample(state, counters, today):
    """Adds what the interfaces moved since the last sample."""

    added = 0

    for name, (rx, tx) in counters.items():

        before = state["last"].get(name)

        if before is None:
            # First reading since the start: only the baseline.
            state["last"][name] = [rx, tx]
            continue

        # A counter that went down started again from zero.
        drx = rx - before[0] if rx >= before[0] else rx
        dtx = tx - before[1] if tx >= before[1] else tx

        state["rx"] += drx
        state["tx"] += dtx
        added += drx + dtx

        state["last"][name] = [rx, tx]

    for name in list(state["last"]):
        if name not in counters:
            state["last"].pop(name)

    if state["today"].get("date") != today.isoformat():
        state["today"] = {"date": today.isoformat(), "bytes": 0}

    state["today"]["bytes"] += added

    return added


def _check_limit(state, settings):
    """Logs the warning and the limit once per period."""

    limit = settings["limit_gb"] * GB

    if not limit:
        return

    used = used_bytes(state)

    # A higher limit (or a corrected count): the messages fire again
    # when it is reached, and "Resume" no longer applies.
    if used < limit:
        state.update(limit_logged=False, resumed=False)

    if used < limit * settings["warn_percent"] / 100:
        state["warned"] = False

    if used >= limit and not state["limit_logged"]:

        state["limit_logged"] = True
        state["warned"] = True

        if settings["block_at_limit"]:
            log_event("data", f"Data limit of {settings['limit_gb']} GB reached: devices on your network are offline "
                              f"until the next period, or until you resume on the dashboard.", level="warning")
        else:
            log_event("data", f"Data limit of {settings['limit_gb']} GB reached.", level="warning")

        _save(force=True)

    elif used >= limit * settings["warn_percent"] / 100 and not state["warned"]:

        state["warned"] = True

        log_event("data", f"{settings['warn_percent']}% of the {settings['limit_gb']} GB data limit used.",
                  level="warning")

        _save(force=True)


def should_block(state, settings):

    return bool(
        settings["block_at_limit"]
        and settings["limit_gb"]
        and used_bytes(state) >= settings["limit_gb"] * GB
        and not state.get("resumed")
    )


# ------------------------------------------------------------------
# The block
# ------------------------------------------------------------------

def _tools():

    return [tool for tool in (which("iptables"), which("ip6tables")) if tool]


def apply_block(block):
    """Puts the block in place or takes it out. Cheap when nothing changed."""

    global _blocked

    for tool in _tools():

        def filt(*args):
            return run_command(privileged([tool, *args]))

        if block:

            filt("-N", CHAIN)

            has_rule, _ = filt("-C", CHAIN, "-o", "wwan+", "-j", "REJECT")

            if not has_rule:
                filt("-F", CHAIN)
                filt("-A", CHAIN, "-o", "wwan+", "-j", "REJECT")

            hooked, _ = filt("-C", "FORWARD", "-j", CHAIN)

            if not hooked:
                filt("-I", "FORWARD", "1", "-j", CHAIN)

        elif _blocked is not False:

            # Gone after a reboot, so failures here are fine.
            for _ in range(5):
                ok, _out = filt("-D", "FORWARD", "-j", CHAIN)
                if not ok:
                    break

            filt("-F", CHAIN)
            filt("-X", CHAIN)

    _blocked = block


def remove():
    """Takes the block out (uninstall)."""

    global _blocked

    _blocked = True
    apply_block(False)


# ------------------------------------------------------------------
# What the pages call
# ------------------------------------------------------------------

def sample():
    """One reading: counts, rolls the period, warns, blocks."""

    with _lock:

        state = _load()
        settings = get_settings()
        today = datetime.date.today()

        rolled = _roll_period(state, today, settings)

        add_sample(state, read_counters(), today)

        _check_limit(state, settings)

        block = should_block(state, settings)

        # Checked every time (-C): a firewall reload drops the hook.
        if block or _blocked is not False:
            apply_block(block)

        _save(force=rolled)


def get_status():

    with _lock:

        state = _load()
        settings = get_settings()

        today = datetime.date.today()
        start = period_start(today, settings["billing_day"])
        end = next_period_start(start)

        used = used_bytes(state) if state["period"] == start.isoformat() else 0

        limit = settings["limit_gb"] * GB or None

        days = (end - start).days
        elapsed = (today - start).days + 1

        # Only a guess after a few days.
        projected = round(used / elapsed * days) if elapsed >= 3 and used else None

        if limit and used >= limit:
            level = "blocked" if should_block(state, settings) else "limit"
        elif limit and used >= limit * settings["warn_percent"] / 100:
            level = "warning"
        else:
            level = "ok"

        today_bytes = state["today"]["bytes"] if state["today"].get("date") == today.isoformat() else 0

        return {
            "used": used,
            "today": today_bytes,
            "limit": limit,
            "percent": round(used / limit * 100, 1) if limit else None,
            "projected": projected,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "days_left": (end - today).days,
            "level": level,
            "resumed": bool(state.get("resumed")) and bool(limit) and used >= limit,
            "counting": bool(read_counters()),
            "settings": settings,
            "history": state.get("history", [])
        }


def reset():
    """Starts the count of this period again at zero."""

    with _lock:

        state = _load()

        state.update(rx=0, tx=0, offset=0, warned=False, limit_logged=False, resumed=False)

        _save(force=True)

    log_event("data", "Mobile data count set to zero.")

    sample()


def set_used(gigabytes):
    """Corrects the count to what the carrier shows. (ok, message)."""

    try:
        value = float(gigabytes)
    except (TypeError, ValueError):
        return False, "Enter the used data in GB."

    if not 0 <= value <= MAX_LIMIT_GB:
        return False, f"Enter a value between 0 and {MAX_LIMIT_GB} GB."

    with _lock:

        state = _load()

        state["offset"] = round(value * GB) - (state["rx"] + state["tx"])

        # The warnings fire again for the corrected amount.
        state.update(warned=False, limit_logged=False)

        _save(force=True)

    log_event("data", f"Mobile data count set to {value:g} GB.")

    sample()

    return True, "Saved."


def resume():
    """Lifts the block until the next period."""

    with _lock:

        state = _load()
        state["resumed"] = True

        _save(force=True)

    log_event("data", "Internet resumed over the data limit until the next billing period.", level="warning")

    sample()


def flush():
    """Writes the count now (on shutdown)."""

    with _lock:
        if _state is not None:
            _save(force=True)


_watching = False


def watch():

    global _watching

    if _watching:
        return

    _watching = True

    # A clean stop (restart, update, shutdown) writes the latest count.
    atexit.register(flush)

    def loop():

        while True:

            try:
                sample()
            except Exception as e:
                print(f"[data] {e}")

            time.sleep(SAMPLE_SECONDS)

    threading.Thread(target=loop, daemon=True, name="data-usage").start()
