"""
Data used per device this billing period (the dashboard's "Mobile data"
and the Clients page).

Each known device (by MAC) gets a slot 1-255. Its connections are
tagged with the slot in bits 16-23 of the connection mark, from the
first packet it sends (mangle/PREROUTING, chain CHAOS-ACCT-MARK); the
chain CHAOS-ACCT, first in filter/FORWARD, then counts every packet of
a tagged connection, both ways, IPv4 and IPv6. So downloads count too,
and so does IPv4 through the CLAT (the translated IPv6 packets are
connections of their own, without a tag, and are not counted twice).
Traffic between two LAN interfaces is not counted. Nothing else on the
router uses those mark bits (WireGuard's fwmark 51820 stays below).

The counters are read every 30 seconds and added up per period (same
billing day as the data limit), in STATE_DIR/device_usage_counter.json.
The mobile data total minus all devices is the router itself (updates,
DNS, the modem's own traffic) and protocol overhead.
"""

import atexit
import datetime
import re
import threading
import time
from shutil import which

from services.config import load_persistent, save_persistent
from services.network import run_command, privileged

COUNTER = "device_usage_counter"

MARK_CHAIN = "CHAOS-ACCT-MARK"
COUNT_CHAIN = "CHAOS-ACCT"

MASK = 0x00FF0000
SHIFT = 16
MAX_SLOTS = 255

SAMPLE_SECONDS = 30
SAVE_SECONDS = 300

ROW_RE = re.compile(r"^\s*\d+\s+(\d+)\s.*/\* ([0-9A-F:]{17}) \*/")

_lock = threading.Lock()

_state = None
_saved_at = 0.0

# The slots the chains were last built with ({mac: slot}), None: not yet.
_built = None

_watching = False


# ------------------------------------------------------------------
# State
# ------------------------------------------------------------------

def empty_state(start):

    return {"period": start.isoformat(), "devices": {}, "previous": None}


def current_period(today=None):

    from services import data_usage

    today = today or datetime.date.today()

    return data_usage.period_start(today, data_usage.get_settings()["billing_day"])


def _load():

    global _state

    if _state is not None:
        return _state

    data = load_persistent(COUNTER, None)
    state = empty_state(current_period())

    if isinstance(data, dict) and isinstance(data.get("devices"), dict):
        state.update(period=data.get("period", state["period"]), devices=data["devices"],
                     previous=data.get("previous"))

    # The kernel's counters start at zero after a reboot.
    for device in state["devices"].values():
        device["last"] = 0

    _state = state

    return _state


def _save(force=False):

    global _saved_at

    if _state is not None and (force or time.time() - _saved_at >= SAVE_SECONDS):
        save_persistent(COUNTER, _state)
        _saved_at = time.time()


def roll_period(state, start):
    """A new billing period starts every device at zero. True if it did."""

    if state["period"] == start.isoformat():
        return False

    top = sorted(state["devices"].items(), key=lambda item: -item[1].get("bytes", 0))

    state["previous"] = {
        "period": state["period"],
        "devices": [{"mac": mac, "name": d.get("name", ""), "bytes": d.get("bytes", 0)} for mac, d in top[:20]]
    }

    state["period"] = start.isoformat()

    for device in state["devices"].values():
        device["bytes"] = 0

    return True


def assign_slots(state, macs, names):
    """
    Gives new devices a free slot. Devices already known keep theirs.
    True if any device was added.
    """

    devices = state["devices"]
    used = {d["slot"] for d in devices.values()}
    added = False

    for mac in macs:

        if mac in devices:
            if names.get(mac):
                devices[mac]["name"] = names[mac]
            continue

        free = next((s for s in range(1, MAX_SLOTS + 1) if s not in used), None)

        if free is None:
            # Full: the device that used the least this period makes room.
            quiet = min(devices, key=lambda m: devices[m].get("bytes", 0))
            free = devices.pop(quiet)["slot"]

        devices[mac] = {"slot": free, "bytes": 0, "last": 0, "name": names.get(mac, "")}
        used.add(free)
        added = True

    return added


def add_counters(state, counters):
    """Adds what each device's counter moved since the last reading."""

    for mac, value in counters.items():

        device = state["devices"].get(mac)

        if not device:
            continue

        last = device.get("last", 0)

        # A lower value: the chain was rebuilt, it counts from zero.
        device["bytes"] = device.get("bytes", 0) + (value - last if value >= last else value)
        device["last"] = value


# ------------------------------------------------------------------
# The chains
# ------------------------------------------------------------------

def _tools():

    return [t for t in (which("iptables"), which("ip6tables")) if t]


def mark_of(slot):

    return f"0x{slot << SHIFT:x}/0x{MASK:x}"


def parse_counters(output):
    """{MAC: bytes} from `iptables -L CHAOS-ACCT -v -x -n`."""

    found = {}

    for line in (output or "").splitlines():

        match = ROW_RE.match(line)

        if match:
            found[match.group(2)] = found.get(match.group(2), 0) + int(match.group(1))

    return found


def read_counters():

    total = {}

    for tool in _tools():

        ok, output = run_command(privileged([tool, "-t", "filter", "-L", COUNT_CHAIN, "-v", "-x", "-n"]))

        if not ok:
            return None

        for mac, value in parse_counters(output).items():
            total[mac] = total.get(mac, 0) + value

    return total


def lan_interfaces():

    from services.network import get_lan_interface_names

    try:
        return list(get_lan_interface_names())
    except Exception:
        return []


def build(slots):
    """Writes both chains for {mac: slot}. The counters start at zero."""

    global _built

    lan = lan_interfaces()

    for tool in _tools():

        def ipt(*args):
            return run_command(privileged([tool, *args]))

        ipt("-t", "mangle", "-N", MARK_CHAIN)
        ipt("-t", "mangle", "-F", MARK_CHAIN)

        for mac, slot in sorted(slots.items(), key=lambda item: item[1]):
            ipt("-t", "mangle", "-A", MARK_CHAIN, "-m", "mac", "--mac-source", mac,
                "-j", "CONNMARK", "--set-xmark", mark_of(slot))

        ipt("-t", "filter", "-N", COUNT_CHAIN)
        ipt("-t", "filter", "-F", COUNT_CHAIN)

        # Between two LAN interfaces: not internet.
        for a in lan:
            for b in lan:
                ipt("-t", "filter", "-A", COUNT_CHAIN, "-i", a, "-o", b, "-j", "RETURN")

        for mac, slot in sorted(slots.items(), key=lambda item: item[1]):
            ipt("-t", "filter", "-A", COUNT_CHAIN, "-m", "connmark", "--mark", mark_of(slot),
                "-m", "comment", "--comment", mac, "-j", "RETURN")

        for table, parent, chain in (("mangle", "PREROUTING", MARK_CHAIN), ("filter", "FORWARD", COUNT_CHAIN)):

            hooked, _ = ipt("-t", table, "-C", parent, "-j", chain)

            if not hooked:
                ipt("-t", table, "-I", parent, "1", "-j", chain)

    _built = dict(slots)


def hooked():

    tools = _tools()

    if not tools:
        return True

    ok, _ = run_command(privileged([tools[0], "-t", "filter", "-C", "FORWARD", "-j", COUNT_CHAIN]))

    return ok


def remove():
    """Takes both chains out (uninstall)."""

    for tool in _tools():

        for table, parent, chain in (("mangle", "PREROUTING", MARK_CHAIN), ("filter", "FORWARD", COUNT_CHAIN)):

            for _ in range(5):
                ok, _out = run_command(privileged([tool, "-t", table, "-D", parent, "-j", chain]))
                if not ok:
                    break

            run_command(privileged([tool, "-t", table, "-F", chain]))
            run_command(privileged([tool, "-t", table, "-X", chain]))


# ------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------

def known_devices():
    """({MACs} on the LAN now, {mac: name})."""

    from services.clients import normalize_mac
    from services.network import get_clients

    macs, names = set(), {}

    for client in get_clients():

        mac = normalize_mac(client.get("mac"))

        if mac:
            macs.add(mac)
            if client.get("hostname") and client["hostname"] != "Unknown":
                names[mac] = client["hostname"]

    return macs, names


def sample():
    """One reading: counts, rolls the period, adds new devices."""

    if not _tools():
        return

    with _lock:

        state = _load()
        rolled = roll_period(state, current_period())

        # Read before anything is rebuilt: a rebuild zeroes the counters.
        if _built is not None:
            counters = read_counters()
            if counters is not None:
                add_counters(state, counters)

        macs, names = known_devices()
        added = assign_slots(state, sorted(macs), names)

        slots = {mac: d["slot"] for mac, d in state["devices"].items()}

        if added or _built != slots or not hooked():
            build(slots)
            for device in state["devices"].values():
                device["last"] = 0

        _save(force=rolled or added)


def flush():

    with _lock:
        _save(force=True)


def usage_by_mac():

    with _lock:

        state = _load()

        if state["period"] != current_period().isoformat():
            return {}

        return {mac: d.get("bytes", 0) for mac, d in state["devices"].items()}


def get_status(total=None, limit=8):
    """
    The devices that used the most this period, and what is left of the
    mobile data total for the router itself.
    """

    from services.clients import get_client_list

    try:
        names = {c["mac"]: c["hostname"] for c in get_client_list()["clients"] if c.get("hostname") not in (None, "", "Unknown")}
    except Exception:
        names = {}

    with _lock:

        state = _load()
        fresh = state["period"] == current_period().isoformat()
        devices = state["devices"] if fresh else {}

        rows = [
            {"mac": mac, "name": names.get(mac) or d.get("name") or mac, "bytes": d.get("bytes", 0)}
            for mac, d in devices.items() if d.get("bytes", 0) > 0
        ]

    rows.sort(key=lambda r: -r["bytes"])

    counted = sum(r["bytes"] for r in rows)

    return {
        "devices": rows[:limit],
        "more": max(0, len(rows) - limit),
        "counted": counted,
        "other": max(0, total - counted) if total is not None else None,
        "counting": _built is not None
    }


def watch():

    global _watching

    if _watching:
        return

    _watching = True

    atexit.register(flush)

    def loop():

        while True:

            try:
                sample()
            except Exception as e:
                print(f"[device usage] {e}")

            time.sleep(SAMPLE_SECONDS)

    threading.Thread(target=loop, daemon=True, name="device-usage").start()
