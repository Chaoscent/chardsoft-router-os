"""
GNSS: the modem's satellite receiver (GPS, GLONASS, Galileo, BeiDou),
read through ModemManager, and the router's clock from it.

Settings (the "gnss" area, part of backups):

    enabled     the receiver is on (ModemManager's gps-nmea and
                gps-raw location sources, plus 3gpp: the cell)
    set_clock   while the clock has not been synchronised over the
                internet (no RTC battery, no mobile data yet), a fresh
                fix sets it
    track_days  0 (off), 1, 7 or 30: the positions of that many days
                are kept (STATE_DIR/gnss_track.json) and drawn as a line
                on the map; a point every 25 m moved or 5 minutes

ModemManager hands over the receiver's latest NMEA sentences
(`mmcli --location-get`); they are parsed here, so the page can say
how many satellites are heard and used, not only the position. No
assistance data is downloaded (that would ask a third party): the
first fix can take a few minutes, faster with open sky.

The position never leaves the router; the page only links to a map.

A modem restart (watchdog, "Restart modem") turns the receiver off:
the watcher (every 5 s while on) switches it on again.
"""

import atexit
import datetime
import json
import math
import threading
import time

from services import transaction
from services.config import load_settings, load_persistent, save_persistent
from services.logs import log_event

SETTINGS = "gnss"

DEFAULT_SETTINGS = {"enabled": False, "set_clock": True, "track_days": 0}

TRACK = "gnss_track"
TRACK_DAYS = (0, 1, 7, 30)
TRACK_MIN_METERS = 25
TRACK_MIN_SECONDS = 300
TRACK_MAX_POINTS = 20000
TRACK_SAVE_SECONDS = 60

WATCH_SECONDS = 5

# A fix older than this is not "now" any more.
STALE_SECONDS = 30

# Before this the clock was never set (2025-01-01), see internet_time.
CLOCK_SET_AFTER = 1735689600

CLOCK_MIN_GAP = 600
CLOCK_TOLERANCE = 5

SOURCES = ("--location-enable-3gpp", "--location-enable-gps-nmea", "--location-enable-gps-raw")
OFF = ("--location-disable-3gpp", "--location-disable-gps-nmea", "--location-disable-gps-raw")

CONSTELLATIONS = {
    "GP": "GPS",
    "GL": "GLONASS",
    "GA": "Galileo",
    "GB": "BeiDou",
    "BD": "BeiDou",
    "GQ": "QZSS",
    "GI": "NavIC",
}

_lock = threading.Lock()

# The last reading, and when its fix time last moved on.
_last = {"reading": None, "fix_key": None, "fix_seen": 0.0, "clock_set": 0.0}

_watching = False


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def get_settings():

    settings = load_settings(SETTINGS, DEFAULT_SETTINGS)

    return {key: settings.get(key, value) for key, value in DEFAULT_SETTINGS.items()}


def validate_settings(data):

    if not isinstance(data, dict):
        return False, "Invalid settings."

    clean = {}

    for key, default in DEFAULT_SETTINGS.items():

        value = data.get(key, default)

        if key == "track_days":
            if isinstance(value, bool) or value not in TRACK_DAYS:
                return False, "Keep the track for 0, 1, 7 or 30 days."
        elif not isinstance(value, bool):
            return False, "Invalid value."

        clean[key] = value

    return True, clean


def save_settings(data):

    ok, clean = validate_settings({**get_settings(), **(data or {})})

    if not ok:
        return transaction.result(False, clean)

    outcome = transaction.change(SETTINGS, clean)

    # Track off: the positions are deleted, not just hidden.
    if outcome["success"] and not clean["track_days"]:
        clear_track()

    return outcome


# ------------------------------------------------------------------
# NMEA
# ------------------------------------------------------------------

def checksum_ok(sentence):

    if not sentence.startswith("$") or "*" not in sentence:
        return False

    body, _, given = sentence[1:].partition("*")

    value = 0

    for char in body:
        value ^= ord(char)

    return given[:2].upper() == f"{value:02X}"


def _float(text):

    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _int(text):

    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def coordinate(value, hemisphere):
    """NMEA ddmm.mmmm / dddmm.mmmm with N/S/E/W -> decimal degrees."""

    number = _float(value)

    if number is None or hemisphere not in ("N", "S", "E", "W"):
        return None

    degrees = int(number // 100)
    decimal = degrees + (number - degrees * 100) / 60

    return round(-decimal if hemisphere in ("S", "W") else decimal, 7)


def parse_nmea(sentences):
    """
    The receiver's state from NMEA sentences: fix, position, altitude,
    speed, dilution, satellites per constellation (in view, heard, used),
    and the UTC time of the fix.
    """

    state = {
        "fix": False, "fix_type": None, "quality": 0,
        "latitude": None, "longitude": None, "altitude": None,
        "speed_kmh": None, "course": None,
        "hdop": None, "pdop": None, "vdop": None,
        "used": None, "time": None, "date": None,
        "constellations": {}
    }

    used_ids = set()
    in_view = {}
    heard = {}

    for raw in sentences:

        sentence = raw.strip()

        if not checksum_ok(sentence):
            continue

        fields = sentence[1:].split("*")[0].split(",")
        talker, kind = fields[0][:2], fields[0][2:]

        if kind == "GGA" and len(fields) >= 10:

            quality = _int(fields[6]) or 0

            state["quality"] = max(state["quality"], quality)
            state["time"] = fields[1] or state["time"]

            if quality > 0:
                state["latitude"] = coordinate(fields[2], fields[3])
                state["longitude"] = coordinate(fields[4], fields[5])
                state["used"] = _int(fields[7])
                state["hdop"] = _float(fields[8]) if state["hdop"] is None else state["hdop"]
                state["altitude"] = _float(fields[9])

        elif kind == "RMC" and len(fields) >= 10:

            state["time"] = fields[1] or state["time"]
            state["date"] = fields[9] or state["date"]

            if fields[2] == "A":
                state["fix"] = True
                state["latitude"] = state["latitude"] if state["latitude"] is not None else coordinate(fields[3], fields[4])
                state["longitude"] = state["longitude"] if state["longitude"] is not None else coordinate(fields[5], fields[6])
                knots = _float(fields[7])
                state["speed_kmh"] = round(knots * 1.852, 1) if knots is not None else None
                state["course"] = _float(fields[8])

        elif kind == "GSA" and len(fields) >= 18:

            fix_type = _int(fields[2])

            if fix_type and fix_type > 1:
                state["fix_type"] = max(state["fix_type"] or 0, fix_type)

            used_ids.update((talker, f) for f in fields[3:15] if f)

            state["pdop"] = _float(fields[15]) if state["pdop"] is None else state["pdop"]
            state["hdop"] = _float(fields[16]) if state["hdop"] is None else state["hdop"]
            state["vdop"] = _float(fields[17].split("*")[0]) if state["vdop"] is None else state["vdop"]

        elif kind == "GSV" and len(fields) >= 4:

            name = CONSTELLATIONS.get(talker)

            if not name:
                continue

            total = _int(fields[3]) or 0

            # Several signal bands (L1, L5) each list the satellites again.
            in_view[name] = max(in_view.get(name, 0), total)

            satellites = heard.setdefault(name, set())

            for i in range(4, len(fields) - 3, 4):
                prn, snr = fields[i], _int(fields[i + 3])
                if prn and snr:
                    satellites.add(prn)

    if state["quality"] > 0:
        state["fix"] = True

    if state["fix"] and not state["fix_type"]:
        state["fix_type"] = 3 if state["altitude"] is not None else 2

    if state["used"] is None and used_ids:
        state["used"] = len(used_ids)

    state["constellations"] = {
        name: {"in_view": in_view[name], "heard": len(heard.get(name, ()))}
        for name in sorted(in_view, key=lambda n: -in_view[n])
    }

    return state


def fix_timestamp(state):
    """The fix's UTC time as a Unix timestamp, from RMC's date and time."""

    date, clock = state.get("date"), state.get("time")

    if not date or not clock or len(date) != 6 or len(clock) < 6:
        return None

    try:
        moment = datetime.datetime(
            2000 + int(date[4:6]), int(date[2:4]), int(date[0:2]),
            int(clock[0:2]), int(clock[2:4]), int(float(clock[4:])),
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        return None

    return moment.timestamp()


# ------------------------------------------------------------------
# ModemManager
# ------------------------------------------------------------------

def _modem():

    from services import modem

    return modem


def _value(value):

    return None if value in (None, "", "--") else value


def read_location(index):
    """(capabilities, enabled sources, cell, gps) from mmcli, or None."""

    modem = _modem()

    ok, status = modem.run_command(modem.privileged(["mmcli", "-m", index, "--location-status", "-J"]))

    if not ok:
        return None

    try:
        location = json.loads(status)["modem"]["location"]
    except (ValueError, KeyError, TypeError):
        return None

    capabilities = location.get("capabilities") or []
    enabled = location.get("enabled") or []

    cell, gps = {}, {}

    if enabled:

        ok, output = modem.run_command(modem.privileged(["mmcli", "-m", index, "--location-get", "-J"]))

        if ok:
            try:
                got = json.loads(output)["modem"]["location"]
            except (ValueError, KeyError, TypeError):
                got = {}

            cell = {k: _value(v) for k, v in (got.get("3gpp") or {}).items()}
            gps = got.get("gps") or {}

    return {
        "capabilities": capabilities if isinstance(capabilities, list) else [],
        "enabled": enabled if isinstance(enabled, list) else [],
        "cell": cell,
        "gps": gps
    }


def switch(index, on):

    modem = _modem()

    ok, out = modem.run_command(modem.privileged(["mmcli", "-m", index, *(SOURCES if on else OFF)]))

    if ok and on:
        # Every 5 s instead of ModemManager's default 30 s.
        modem.run_command(modem.privileged(["mmcli", "-m", index, "--location-set-gps-refresh-rate=5"]))

    return ok, out


def apply():
    """Switches the receiver on or off as the settings say."""

    index = _modem().get_modem_index()
    on = get_settings()["enabled"]

    if index is None:
        # Picked up by the watcher once the modem is there.
        return True, "Saved. No modem right now." if on else "GNSS off."

    location = read_location(index)

    if on and location is not None and "gps-nmea" not in location["capabilities"]:
        return False, "This modem has no GNSS receiver that ModemManager can use."

    ok, out = switch(index, on)

    if not ok:
        return False, f"ModemManager refused: {(out or '').strip().splitlines()[-1] if out else 'no answer'}"

    if on:
        start_watch()

    return True, "GNSS on. The first position can take a few minutes." if on else "GNSS off."


# ------------------------------------------------------------------
# The clock
# ------------------------------------------------------------------

def clock_synchronized():
    """Whether the system clock was set over the internet (NTP)."""

    output = _modem().run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]) or ""

    return output.strip() == "yes"


def maybe_set_clock(state, fresh):

    if not fresh or not state["fix"] or not get_settings()["set_clock"]:
        return

    stamp = fix_timestamp(state)

    if stamp is None or stamp < CLOCK_SET_AFTER:
        return

    now = time.time()

    if abs(now - stamp) <= CLOCK_TOLERANCE or now - _last["clock_set"] < CLOCK_MIN_GAP:
        return

    if clock_synchronized():
        return

    modem = _modem()

    ok, out = modem.run_command(modem.privileged(["date", "-u", "-s", f"@{int(stamp)}"]))

    _last["clock_set"] = time.time()

    if ok:
        log_event("system", f"Clock set from GNSS to {datetime.datetime.fromtimestamp(stamp):%Y-%m-%d %H:%M:%S} "
                            f"(it was {abs(now - stamp):.0f} s off).", notify=True)
    else:
        log_event("system", f"The clock could not be set from GNSS: {out}", "warning")


# ------------------------------------------------------------------
# The track
# ------------------------------------------------------------------

_track = {"points": None, "saved": 0.0, "dirty": False}


def meters(a, b):
    """Distance between two (latitude, longitude) points."""

    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))

    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2

    return 6371000 * 2 * math.asin(math.sqrt(h))


def _track_points():

    if _track["points"] is None:
        data = load_persistent(TRACK, [])
        _track["points"] = [p for p in data if isinstance(p, list) and len(p) == 3] if isinstance(data, list) else []

    return _track["points"]


def _save_track(force=False):

    if _track["points"] is not None and _track["dirty"] and (force or time.time() - _track["saved"] >= TRACK_SAVE_SECONDS):
        save_persistent(TRACK, _track["points"])
        _track.update(saved=time.time(), dirty=False)


def add_to_track(latitude, longitude, now, days):
    """Notes a position when it moved or a while passed; drops old points."""

    points = _track_points()

    keep_from = now - days * 86400

    if points and points[0][0] < keep_from:
        points[:] = [p for p in points if p[0] >= keep_from]
        _track["dirty"] = True

    last = points[-1] if points else None

    if last is None or meters((last[1], last[2]), (latitude, longitude)) >= TRACK_MIN_METERS \
            or now - last[0] >= TRACK_MIN_SECONDS:
        points.append([round(now), round(latitude, 6), round(longitude, 6)])
        del points[:-TRACK_MAX_POINTS]
        _track["dirty"] = True

    _save_track()


def get_track():

    days = get_settings()["track_days"]

    with _lock:
        points = list(_track_points()) if days else []

    keep_from = time.time() - days * 86400

    return {"days": days, "points": [p for p in points if p[0] >= keep_from]}


def clear_track():

    with _lock:
        _track_points().clear()
        _track["dirty"] = True
        _save_track(force=True)

    return True, "Track cleared."


def flush():

    with _lock:
        _save_track(force=True)


# ------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------

def describe(state, settings, location, has_modem, now):
    """The page's view: level, a sentence, and the numbers."""

    view = {
        "enabled": settings["enabled"],
        "set_clock": settings["set_clock"],
        "track_days": settings["track_days"],
        "level": "off",
        "message": "GNSS is off.",
        "position": None,
        "cell": (location or {}).get("cell") or None,
        "constellations": [],
        "in_view": 0,
        "heard": 0,
        "used": None,
        "fix_age": None,
        "clock_synchronized": None,
    }

    if not has_modem:
        return {**view, "level": "no_modem", "message": "No modem found."}

    if location is not None and location["capabilities"] and "gps-nmea" not in location["capabilities"]:
        return {**view, "level": "unsupported", "message": "This modem has no GNSS receiver that ModemManager can use."}

    if not settings["enabled"]:
        return view

    if location is None or "gps-nmea" not in location["enabled"]:
        return {**view, "level": "starting", "message": "Switching the receiver on..."}

    if state is None:
        return {**view, "level": "starting", "message": "Starting the receiver. The first sentences take a few seconds."}

    in_view = sum(c["in_view"] for c in state["constellations"].values())
    heard = sum(c["heard"] for c in state["constellations"].values())

    view.update(
        constellations=[{"name": name, **c} for name, c in state["constellations"].items()],
        in_view=in_view,
        heard=heard,
        used=state["used"],
    )

    age = now - _last["fix_seen"] if _last["fix_seen"] else None

    if state["fix"] and age is not None and age <= STALE_SECONDS:

        accuracy = round(state["hdop"] * 5) if state["hdop"] else None

        view["position"] = {
            "latitude": state["latitude"],
            "longitude": state["longitude"],
            "altitude": state["altitude"],
            "speed_kmh": state["speed_kmh"],
            "course": state["course"],
            "accuracy_m": max(accuracy, 2) if accuracy else None,
            "fix_type": "3D" if state["fix_type"] == 3 else "2D",
            "time": fix_timestamp(state),
        }

        used = f" from {state['used']} satellites" if state["used"] else ""
        near = f", accurate to about {view['position']['accuracy_m']} m" if accuracy else ""

        view.update(level="fix", fix_age=round(age),
                    message=f"Position found ({view['position']['fix_type']}){used}{near}.")

        return view

    if state["fix"] and age is not None:
        return {**view, "level": "searching", "fix_age": round(age),
                "message": f"Lost the position {round(age)} s ago. Searching again."}

    if not heard:
        return {**view, "level": "searching",
                "message": "No satellites heard yet. The modem needs an antenna on its GNSS port, "
                           "and a window or the open sky; indoors it often hears nothing."}

    return {**view, "level": "searching",
            "message": f"Searching: {heard} satellite{'s' if heard != 1 else ''} heard, no position yet. "
                       "The first fix can take a few minutes; open sky helps."}


def sample():
    """One reading: switches the receiver back on if needed, notes the fix, sets the clock."""

    settings = get_settings()
    index = _modem().get_modem_index()

    if index is None:
        _last["reading"] = None
        return None

    location = read_location(index)

    # A modem restart switched it off.
    if settings["enabled"] and location is not None and "gps-nmea" in location["capabilities"] \
            and "gps-nmea" not in location["enabled"]:
        switch(index, True)
        location = read_location(index)

    state = None

    if location is not None and settings["enabled"]:

        nmea = location["gps"].get("nmea") or []

        if isinstance(nmea, str):
            nmea = nmea.splitlines()

        if nmea:
            state = parse_nmea(nmea)

    fresh = False

    with _lock:

        if state and state["fix"]:

            key = (state["date"], state["time"])

            if key != _last["fix_key"]:
                _last["fix_key"] = key
                _last["fix_seen"] = time.time()
                fresh = True

                days = settings["track_days"]

                if days and state["latitude"] is not None and state["longitude"] is not None:
                    add_to_track(state["latitude"], state["longitude"], time.time(), days)

        _last["reading"] = (state, location, time.time())

    if state:
        maybe_set_clock(state, fresh)

    return state


def get_status(refresh=False):

    settings = get_settings()

    if refresh or not _watching or _last["reading"] is None:
        sample()

    reading = _last["reading"]
    has_modem = reading is not None

    state, location = (reading[0], reading[1]) if reading else (None, None)

    view = describe(state, settings, location, has_modem, time.time())

    if settings["enabled"] and settings["set_clock"]:
        view["clock_synchronized"] = clock_synchronized()

    return view


# ------------------------------------------------------------------
# The watcher
# ------------------------------------------------------------------

def start_watch():

    global _watching

    if _watching:
        return

    _watching = True

    # A clean stop writes the newest track points.
    atexit.register(flush)

    def loop():

        while True:

            try:
                if get_settings()["enabled"]:
                    sample()
            except Exception as e:
                print(f"[gnss] {e}")

            time.sleep(WATCH_SECONDS)

    threading.Thread(target=loop, daemon=True, name="gnss").start()


def watch():
    """At start: the receiver comes back on after a reboot."""

    if get_settings()["enabled"]:
        start_watch()
