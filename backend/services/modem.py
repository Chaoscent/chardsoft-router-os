import os
import subprocess
import re
import threading
import time

from services.modem_detector import detect_modem
from services.config import load_settings
from services.network import pick_ipv6
from services import transaction
from services.logs import log_event

SIGNAL_ENABLED = False


# mmcli can hang while the modem resets; nothing may wait for ever.
COMMAND_TIMEOUT = 15


def run(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=COMMAND_TIMEOUT)
    except Exception:
        return None


def privileged(cmd):

    from services.network import privileged as wrap
    return wrap(cmd)


# Connecting (nmcli up) can take a minute; more means the modem hangs.
CHANGE_TIMEOUT = 120


def run_command(cmd):

    from services.network import run_command as runner
    return runner(cmd, timeout=CHANGE_TIMEOUT)


def enable_signal_polling(index):
    """
    Signal values (RSRP, RSRQ, SINR) are only measured after this, and
    ModemManager only allows it as root. Retried until it worked.
    """

    global SIGNAL_ENABLED

    if SIGNAL_ENABLED:
        return

    ok, _ = run_command(privileged(["mmcli", "-m", index, "--signal-setup=5"]))

    SIGNAL_ENABLED = ok


def parse(pattern, text, default="Unknown"):
    if not text:
        return default

    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else default


# Values that are not known are None; the pages show them as "--".
UNKNOWN_FIELDS = (
    "carrier", "network", "signal", "imei", "sim",
    "rsrp", "rsrq", "sinr", "band",
    "lock", "pin_retries", "modem_state", "connection",
    "usb_mode", "ipv4", "ipv6", "rx_packets", "rx_errors", "data_dropped"
)


def get_modem_index():
    """
    The first modem ModemManager knows, e.g. "0", or None.
    """

    output = run(["mmcli", "-L"])

    match = re.search(r"/Modem/(\d+)", output or "")

    return match.group(1) if match else None


def signal_values(text):
    """
    rsrp / rsrq / sinr from `mmcli --signal-get`, which lists one
    section per technology ("LTE  | rsrp: -90.00 dBm", "5G | ...").
    5G values win over LTE (5G NSA reports both).
    """

    sections = {}
    current = None

    for line in (text or "").splitlines():

        head, sep, rest = line.partition("|")

        if not sep:
            continue

        if head.strip():
            current = head.strip().lower()

        match = re.match(r"\s*([\w/]+):\s*'?(-?\d+(?:\.\d+)?)", rest)

        if current and match:
            sections.setdefault(current, {})[match.group(1).lower()] = match.group(2)

    values = {}

    for tech in ("lte", "5g"):

        found = sections.get(tech, {})

        for key, names in (("rsrp", ("rsrp",)), ("rsrq", ("rsrq",)), ("sinr", ("s/n", "snr", "sinr"))):

            for name in names:
                if name in found and _plausible(key, found[name]):
                    values[key] = found[name]

    return values


# What a real measurement can be. Modems report -32768 (or -3276.8)
# for "no measurement", e.g. the 5G part of 5G NSA between bursts.
SIGNAL_RANGES = {
    "rsrp": (-160.0, -30.0),
    "rsrq": (-45.0, 20.0),
    "sinr": (-30.0, 50.0)
}


def _plausible(key, value):

    low, high = SIGNAL_RANGES[key]

    try:
        return low <= float(value) <= high
    except ValueError:
        return False


# ModemManager access technologies -> what people know them as.
ACCESS_TECH = {
    "5gnr": "5G",
    "lte": "LTE",
    "umts": "3G",
    "hsdpa": "3G",
    "hsupa": "3G",
    "hspa": "3G",
    "hspa-plus": "3G+",
    "edge": "2G",
    "gprs": "2G",
    "gsm": "2G"
}


# The active band, from the modem itself (QMI): ModemManager does not
# report it. Asked through ModemManager's proxy (-p), at most every
# BAND_SECONDS (the Modem page refreshes often).
BAND_SECONDS = 30

_band_cache = {"time": 0.0, "port": None, "value": None}


def qmi_port(info):
    """The QMI control port from `mmcli -m`, e.g. "cdc-wdm0", or None."""

    match = re.search(r"\b(cdc-wdm\d+) \(qmi\)", info or "")

    return match.group(1) if match else None


def format_bands(output):
    """
    "B3 + n78" from `qmicli --nas-get-rf-band-info`: LTE bands as B<n>,
    5G NR as n<n>, anything else as qmicli names it.
    """

    bands = []

    for name in re.findall(r"Active Band Class:\s*'([^']+)'", output or ""):

        lte = re.fullmatch(r"eutran-(\d+)", name)
        nr = re.fullmatch(r"(?:nr5g-band-|nr5g-|n)(\d+)", name)

        label = f"B{lte.group(1)}" if lte else f"n{nr.group(1)}" if nr else name

        if label not in bands:
            bands.append(label)

    return " + ".join(bands) or None


def band_label(bands, network):
    """
    In 5G NSA the modem only reports its LTE anchor (e.g. "B3"); the 5G
    carrier is there, but its band is not. "B3 + 5G" says so instead of
    looking like plain LTE.
    """

    if bands and network == "5G NSA" and not re.search(r"\bn\d+", bands):
        return f"{bands} + 5G"

    return bands


def get_active_band(info):

    port = qmi_port(info)

    if not port or not run(["which", "qmicli"]):
        return None

    now = time.monotonic()

    if _band_cache["port"] == port and now - _band_cache["time"] < BAND_SECONDS:
        return _band_cache["value"]

    ok, output = run_command(privileged([
        "qmicli", "-p", "-d", f"/dev/{port}", "--nas-get-rf-band-info"
    ]))

    _band_cache.update(time=now, port=port, value=format_bands(output) if ok else None)

    return _band_cache["value"]


def format_access_tech(value):
    """
    "lte, 5gnr" -> "5G NSA", "5gnr" -> "5G SA", "lte" -> "LTE".
    """

    if not value:
        return None

    techs = [t.strip().lower() for t in value.split(",") if t.strip()]

    if "5gnr" in techs:
        return "5G NSA" if "lte" in techs else "5G SA"

    for tech in techs:
        if tech in ACCESS_TECH:
            return ACCESS_TECH[tech]

    return value


# Locks that keep the SIM from working. Others, like sim-pin2 (the
# second PIN, only for fixed dialing numbers), do not.
BLOCKING_LOCKS = ("sim-pin", "sim-puk", "ph-sim-pin", "ph-net-pin")


def pin_enabled(locks):
    """
    True/False from mmcli's "enabled locks" ("sim, fixed-dialing",
    "none"), None without the line.
    """

    if locks is None:
        return None

    return "sim" in [part.strip() for part in locks.split(",")]


def sim_status(lock, state):
    """
    "Waiting for PIN", "Blocked (PUK)", "Ready" or None (unknown).
    """

    if lock in ("sim-pin", "ph-sim-pin", "ph-net-pin"):
        return "Waiting for PIN"

    if lock == "sim-puk":
        return "Blocked (PUK)"

    if state in ("failed",):
        return "Failed"

    if state in (
        "enabled", "searching", "registered",
        "connecting", "connected", "disconnecting"
    ):
        return "Ready"

    return None


def no_data(hardware, state, model=None):
    """
    Honest placeholder: which modem (if any) and why there is no data.
    state: "absent" (no modem), "not_ready" (modem found, no answer),
    "no_modemmanager" (modem found, ModemManager missing).
    """

    return {
        "state": state,
        "vendor": hardware["vendor"],
        "model": model or hardware["model"],
        "usb_id": hardware["usb_id"],
        **{field: None for field in UNKNOWN_FIELDS},
        # Also while the modem restarts after a USB mode switch.
        **data_link(hardware)
    }


# One reading serves every page for a moment: the header, the Modem
# page and the dashboard all ask every few seconds, and each reading
# runs several mmcli calls that take seconds while the modem is busy.
MODEM_CACHE_SECONDS = 2.0

_modem_cache = {"time": 0.0, "data": None}
_modem_lock = threading.Lock()


def get_modem_data(max_age=MODEM_CACHE_SECONDS):
    """
    The modem data, at most max_age seconds old. Callers that arrive
    while a reading runs wait for it instead of starting their own.
    """

    with _modem_lock:

        cached = _modem_cache["data"]

        if cached is not None and time.monotonic() - _modem_cache["time"] < max_age:
            return dict(cached)

        data = read_modem_data()

        _modem_cache.update(time=time.monotonic(), data=data)

        return dict(data)


def read_modem_data():
    """
    Live modem data from ModemManager. "state" is "ready", "not_ready"
    (a modem is there but does not answer yet) or "absent".
    """

    hardware = detect_modem()

    has_mmcli = run(["which", "mmcli"]) is not None

    index = get_modem_index() if has_mmcli else None

    if index is None:

        if hardware["usb_id"]:
            return no_data(hardware, "not_ready" if has_mmcli else "no_modemmanager")

        return no_data(hardware, "absent", model="No modem")

    enable_signal_polling(index)

    info = run(["mmcli", "-m", index])

    ok, signal = run_command(privileged(["mmcli", "-m", index, "--signal-get"]))

    if not ok:
        signal = None

    if info is None:
        return no_data(hardware, "not_ready")

    def field(pattern, text=info):
        value = parse(pattern, text, None)
        return value if value not in ("--", "") else None

    radio = signal_values(signal)

    return {
        "state": "ready",
        "vendor": hardware["vendor"] if hardware["usb_id"] else field(r"manufacturer:\s+(.+)"),
        "model": hardware["model"] if hardware["usb_id"] else field(r"model:\s+(.+)"),
        "usb_id": hardware["usb_id"],

        "carrier": field(r"operator name:\s+(.+)"),
        "network": format_access_tech(field(r"access tech:\s+(.+)")),
        "signal": field(r"signal quality:\s+'?(\d+)"),
        "imei": field(r"equipment id:\s+(.+)"),
        "sim": sim_status(field(r"\|\s+lock:\s+(\S+)"), field(r"\|\s+state:\s+'?([a-z-]+)")),

        "rsrp": radio.get("rsrp"),
        "rsrq": radio.get("rsrq"),
        "sinr": radio.get("sinr"),

        # ModemManager does not report it: asked over QMI.
        "band": band_label(get_active_band(info), format_access_tech(field(r"access tech:\s+(.+)"))),

        # "sim-pin" while the SIM waits for its PIN, "sim-puk" when it
        # is blocked; "none" or None when unlocked.
        "lock": field(r"\|\s+lock:\s+(\S+)"),
        "pin_retries": field(r"unlock retries:.*?\bsim-pin \((\d+)\)"),
        "puk_retries": field(r"unlock retries:.*?\bsim-puk \((\d+)\)"),
        # Whether the SIM asks for its PIN at every start ("sim" among
        # the enabled locks); None when ModemManager does not say.
        "pin_enabled": pin_enabled(field(r"enabled locks:\s+(.+)")),
        "modem_state": field(r"\|\s+state:\s+'?([a-z-]+)"),
        "connection": get_connection_state(),

        **data_link(hardware)
    }


# -------------------------------------------------------------------
# Mobile data (a NetworkManager connection) and the SIM PIN
# -------------------------------------------------------------------

MODEM_SETTINGS = "modem"

# NetworkManager connection for the modem; it brings the modem's
# data connection (wwan0) up at every start.
CONNECTION = "chardsoft-modem"

DEFAULT_SETTINGS = {
    # Off until switched on from the Modem page; an existing
    # connection of the user's own is left alone until then.
    "enabled": False,
    # Empty: NetworkManager picks the carrier's APN by itself.
    "apn": "",
    "username": "",
    "password": "",
    "roaming": False,
    # Only stored after it worked on the SIM (see the SIM PIN section).
    # The router sends it itself (auto_unlock), never NetworkManager.
    "pin": ""
}

APN_RE = re.compile(r"^[A-Za-z0-9._-]{0,100}$")
PIN_RE = re.compile(r"^\d{4,8}$")


def get_modem_settings():

    settings = load_settings(MODEM_SETTINGS, DEFAULT_SETTINGS)

    return {key: settings.get(key, DEFAULT_SETTINGS[key]) for key in DEFAULT_SETTINGS}


def public_settings():
    """
    The settings for the page: the PIN and password are never sent
    back, only whether they are set.
    """

    settings = get_modem_settings()

    return {
        **{k: v for k, v in settings.items() if k not in ("pin", "password")},
        "pin_saved": bool(settings["pin"]),
        # The saved PIN was wrong and forgotten (since the app started).
        "pin_rejected": auto_unlock_state["rejected"],
        "password_saved": bool(settings["password"])
    }


def validate_modem_settings(data):

    settings = get_modem_settings()

    data = dict(data or {})

    # The page leaves the password empty to keep the saved one.
    if data.get("password") in (None, "") and not data.get("clear_password"):
        data.pop("password", None)

    data.pop("clear_password", None)

    # The PIN is only set through unlock_sim (or forgotten).
    if data.get("forget_pin"):
        data["pin"] = ""
    else:
        data.pop("pin", None)

    data.pop("forget_pin", None)

    settings.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})

    if not isinstance(settings["enabled"], bool) or not isinstance(settings["roaming"], bool):
        return False, "Invalid value."

    apn = str(settings["apn"] or "").strip()

    if not APN_RE.fullmatch(apn):
        return False, "Invalid APN: letters, numbers, dots and dashes only."

    username = str(settings["username"] or "")
    password = str(settings["password"] or "")

    for value in (username, password):
        if len(value) > 100 or any(ord(c) < 0x20 for c in value):
            return False, "Invalid username or password."

    pin = str(settings["pin"] or "")

    if pin and not PIN_RE.fullmatch(pin):
        return False, "Invalid PIN."

    return True, {
        "enabled": settings["enabled"],
        "apn": apn,
        "username": username,
        "password": password,
        "roaming": settings["roaming"],
        "pin": pin
    }


def validate_stored_settings(data):
    """
    Saved settings as they are (a backup): the PIN and password are
    kept, not taken from the page's rules.
    """

    data = {**DEFAULT_SETTINGS, **{k: v for k, v in (data or {}).items() if k in DEFAULT_SETTINGS}}

    pin, password = str(data["pin"] or ""), str(data["password"] or "")

    ok, result = validate_modem_settings({
        k: v for k, v in data.items() if k not in ("pin", "password")
    })

    if not ok:
        return False, result

    if pin and not PIN_RE.fullmatch(pin):
        return False, "Invalid PIN."

    if len(password) > 100 or any(ord(c) < 0x20 for c in password):
        return False, "Invalid password."

    return True, {**result, "pin": pin, "password": password}


def save_modem_settings(data):

    ok, result = validate_modem_settings(data)

    if not ok:
        return transaction.result(False, result)

    outcome = transaction.change(MODEM_SETTINGS, result)
    outcome["settings"] = public_settings()

    return outcome


def has_nmcli():

    return run(["which", "nmcli"]) is not None


def connection_exists():

    output = run(["nmcli", "-t", "-f", "NAME", "connection", "show"]) or ""

    return CONNECTION in output.splitlines()


def get_connection_state():
    """
    "activated", "activating", "deactivated"... of the chardsoft-modem
    connection, or None without one.
    """

    if not has_nmcli():
        return None

    output = run(["nmcli", "-t", "-f", "NAME,STATE", "connection", "show", "--active"]) or ""

    for line in output.splitlines():

        name, _, state = line.rpartition(":")

        if name == CONNECTION:
            return state or "activated"

    return "deactivated" if connection_exists() else None


# qmi_wwan sizes its receive buffer by the interface MTU. Carriers often
# hand out 1472 or less while their network still delivers 1500-byte
# packets (QUIC, or TCP from a phone with an MTU of 1500 behind the
# router): one such packet makes the USB transfer fail (-EPROTO), and
# from then on every transfer from the modem fails until it is reset.
# Seen on the Pi with an RM520N on Telekom: the link died a minute after
# a phone joined the Wi-Fi, while iperf on the router itself (packets
# capped by its own MTU) ran for minutes. A 1500 MTU fixes it.
LINK_MTU = 1500


def needs_full_mtu(driver=None):
    """Whether the modem's driver needs LINK_MTU (qmi_wwan)."""

    if driver is None:
        _, driver = wwan_interface()

    return driver == "qmi_wwan"


def ensure_link_mtu():
    """
    Raises the qmi_wwan interface to LINK_MTU when it came up with less,
    and sets the connection so it comes up with it from then on (the
    setting applies at the next connect; nothing is interrupted).
    Returns True when it raised the MTU.
    """

    interface, driver = wwan_interface()

    if not interface or not needs_full_mtu(driver):
        return False

    try:
        with open(f"/sys/class/net/{interface}/mtu") as f:
            mtu = int(f.read().strip())
    except (OSError, ValueError):
        return False

    if mtu >= LINK_MTU:
        return False

    ok, _ = run_command(privileged(["ip", "link", "set", interface, "mtu", str(LINK_MTU)]))

    if has_nmcli() and connection_exists():
        run_command(privileged(["nmcli", "connection", "modify", CONNECTION, "gsm.mtu", str(LINK_MTU)]))

    return ok


def connection_properties(settings, apn_mode="auto", apn=None):
    """
    nmcli properties of the chardsoft-modem connection. apn_mode: "auto"
    (the carrier's APN from NetworkManager's database), "given" (apn),
    or "legacy" (NetworkManager without gsm.auto-config).
    """

    props = [
        "connection.autoconnect", "yes",
        # NetworkManager's default: a few attempts, then a pause of
        # minutes before the next ones. 0 (for ever) retries without any
        # pause, hundreds of times a minute, which carriers may punish.
        "connection.autoconnect-retries", "-1",
        "gsm.home-only", "no" if settings["roaming"] else "yes",
        "gsm.username", settings["username"],
        "gsm.password", settings["password"],
        "gsm.password-flags", "0",
        # Never the PIN: NetworkManager would send it at every attempt
        # (one apply tries up to three APNs), and three wrong PINs block
        # the SIM. The router unlocks the SIM itself (auto_unlock).
        "gsm.pin", "",
        "gsm.pin-flags", "4",
        "ipv4.method", "auto",
        "ipv6.method", "auto"
    ]

    if needs_full_mtu():
        props += ["gsm.mtu", str(LINK_MTU)]

    if apn_mode == "auto":
        props += ["gsm.apn", "", "gsm.auto-config", "yes"]
    elif apn_mode == "given":
        props += ["gsm.apn", apn or "", "gsm.auto-config", "no"]
    else:
        props += ["gsm.apn", apn or ""]

    return props


def network_apn():
    """
    The APN the network gave the modem when it attached (its initial
    bearer), e.g. "internet.telekom"; None when it reports none.
    """

    index = get_modem_index()

    if index is None:
        return None

    info = run(["mmcli", "-m", index]) or ""

    match = re.search(r"initial bearer path:\s+\S*/Bearer/(\d+)", info)

    if not match:
        return None

    bearer = run(["mmcli", "-b", match.group(1)]) or ""

    apn = parse(r"\|\s+apn:\s+(\S+)", bearer, None)

    # IMS (calls over LTE) and SOS (emergency calls) carry no internet;
    # with VoLTE, the modem often attaches with IMS.
    if not apn or apn == "--" or apn.lower() in ("ims", "sos") or not APN_RE.fullmatch(apn):
        return None

    return apn


def short_error(message):
    """
    NetworkManager's error without its "Hint: use journalctl ..." tail.
    """

    message = str(message or "").strip()

    message = re.split(r"\s*Hint: use ", message)[0].strip()

    return message.removeprefix("Error: ")[:200]


def apply_modem_settings():
    """
    Creates, updates or removes the chardsoft-modem connection. A missing
    signal is not an error: the connection comes up when it can.
    """

    settings = get_modem_settings()

    if not has_nmcli():

        if not settings["enabled"]:
            return True, "Mobile data off."

        return False, "NetworkManager (nmcli) is not available."

    if not settings["enabled"]:

        if connection_exists():

            ok, result = run_command(privileged(["nmcli", "connection", "delete", CONNECTION]))

            if not ok:
                return False, f"The modem connection could not be removed: {result}"

        return True, "Mobile data off."

    def write(apn_mode, apn=None):

        props = connection_properties(settings, apn_mode, apn)

        if connection_exists():
            return run_command(privileged([
                "nmcli", "connection", "modify", CONNECTION, *props
            ]))

        return run_command(privileged([
            "nmcli", "connection", "add", "type", "gsm", "ifname", "*",
            "con-name", CONNECTION, *props
        ]))

    def up():
        return run_command(privileged(["nmcli", "--wait", "25", "connection", "up", CONNECTION]))

    # The APNs to try: the one entered; otherwise the carrier's from
    # NetworkManager's database (can be outdated), then the one the
    # network gave the modem, then none: the network's default for the
    # SIM, which works with plans whose APN isn't in the database.
    if settings["apn"]:
        attempts = [("given", settings["apn"])]
    else:
        attempts = [("auto", None)]
        given = network_apn()
        if given:
            attempts.append(("given", given))
        attempts.append(("given", ""))

    message = ""

    for apn_mode, apn in attempts:

        ok, result = write(apn_mode, apn)

        # Older NetworkManager without gsm.auto-config.
        if not ok and "auto-config" in str(result):
            ok, result = write("legacy", apn)

        if not ok:
            return False, f"The modem connection could not be saved: {short_error(result)}"

        connected, message = up()

        if connected:
            used = apn if apn_mode == "given" and apn else None
            return True, "Mobile data connected" + (f" (APN {used})." if used else ".")

        # A SIM or registration problem fails every APN alike.
        if any(word in str(message) for word in ("SIM", "PIN", "not registered")):
            break

    return True, (
        f"Mobile data on, not connected yet: {short_error(message)}. "
        f"It connects by itself when it can; if it never does, enter your carrier's APN."
    )


def get_sim_path(index):

    info = run(["mmcli", "-m", index]) or ""

    match = re.search(r"primary sim path:\s+(\S+)", info) or re.search(r"/SIM/(\d+)", info)

    if not match:
        return None

    value = match.group(1)

    return value.rsplit("/", 1)[-1] if "/" in value else value


# -------------------------------------------------------------------
# SIM PIN and PUK
#
# Every wrong PIN costs one of three attempts, then the SIM wants its
# PUK; every wrong PUK costs one of ten, then the SIM is unusable for
# good. So:
#   - each code is sent once per request, never retried,
#   - the router sends a saved PIN by itself at most once, and never
#     with the last attempt; a PIN the SIM rejects is forgotten,
#   - the page has to confirm a request that uses the last PIN attempt
#     or one of the last PUK attempts.
# -------------------------------------------------------------------

PUK_RE = re.compile(r"^\d{8}$")

# From this many attempts left on, a request has to be confirmed.
PIN_CONFIRM_AT = 1
PUK_CONFIRM_AT = 3

# One SIM command at a time (the page and auto_unlock).
_sim_lock = threading.Lock()

auto_unlock_state = {
    # Wall-clock time the saved PIN was rejected and forgotten.
    "rejected": None,
    # The saved PIN is held back (too few attempts left); logged once.
    "held": False
}


def retries_of(value):

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sim_command(args):
    """
    (ok, output) of `mmcli -i <SIM> <args>`, or (False, message) without
    a modem or SIM.
    """

    index = get_modem_index()

    if index is None:
        return False, "No modem found."

    sim = get_sim_path(index)

    if sim is None:
        return False, "No SIM found."

    return run_command(privileged(["mmcli", "-i", sim, *args]))


def code_was_wrong(output, before, after):
    """
    Whether a failed PIN/PUK command used up an attempt. Only a known,
    unchanged count of attempts says it did not (e.g. the modem was
    busy); anything else counts as wrong, to be safe.
    """

    if "IncorrectPassword" in (output or "") or "ncorrect" in (output or ""):
        return True

    return before is None or after is None or after < before


def needs_confirmation(retries, threshold):

    return retries is not None and retries <= threshold


def remember_pin(pin):
    """Saves (or with "" forgets) the PIN, already in effect on the SIM."""

    settings = get_modem_settings()

    if settings["pin"] == pin:
        return True

    settings["pin"] = pin

    outcome = transaction.change(MODEM_SETTINGS, settings, apply=False)

    return outcome["success"]


def pin_failure(message, data_after, field="pin_retries"):

    retries = retries_of(data_after.get(field))

    return {
        "success": False,
        "message": message + (f" {retries} attempts left." if retries is not None else ""),
        "retries": retries
    }


def unlock_sim(pin, remember=True, confirm=False):
    """
    Sends the PIN once. Returns {"success", "message", "retries"}, and
    "confirm": True instead when it would use the last attempt.
    """

    pin = str(pin or "").strip()

    if not PIN_RE.fullmatch(pin):
        return {"success": False, "message": "The PIN has 4 to 8 digits."}

    with _sim_lock:

        data = get_modem_data(max_age=0)

        if data.get("lock") == "sim-puk":
            return {"success": False, "message": "The SIM is blocked: it needs its PUK."}

        if data.get("lock") != "sim-pin":
            return {"success": False, "message": "The SIM does not ask for a PIN."}

        before = retries_of(data.get("pin_retries"))

        if needs_confirmation(before, PIN_CONFIRM_AT) and not confirm:
            return {
                "success": False, "confirm": True, "retries": before,
                "message": "This is the last attempt: a wrong PIN blocks the SIM."
            }

        ok, output = sim_command([f"--pin={pin}"])

        after_data = get_modem_data(max_age=0)

        if not ok:

            if not code_was_wrong(output, before, retries_of(after_data.get("pin_retries"))):
                return {"success": False, "message": "The modem did not take the PIN now. Try again in a moment."}

            return pin_failure("Wrong PIN.", after_data)

        auto_unlock_state.update(rejected=None, held=False)

        if remember:

            remember_pin(pin)

            if get_modem_settings()["enabled"]:
                apply_modem_settings()

    return {"success": True, "message": "SIM unlocked."}


def auto_unlock():
    """
    Unlocks the SIM with the saved PIN when it asks for one (after a
    boot, a modem restart or a SIM swap). Returns "no_pin", "not_locked",
    "held", "unlocked", "rejected" or "failed" (try again later).
    """

    pin = get_modem_settings()["pin"]

    if not pin:
        return "no_pin"

    if not _sim_lock.acquire(blocking=False):
        return "failed"

    try:

        data = get_modem_data(max_age=0)

        if data.get("lock") != "sim-pin":
            auto_unlock_state["held"] = False
            return "not_locked"

        before = retries_of(data.get("pin_retries"))

        # The last attempt is left to a person.
        if before is not None and before <= PIN_CONFIRM_AT:

            if not auto_unlock_state["held"]:
                auto_unlock_state["held"] = True
                log_event(
                    "modem",
                    "The SIM asks for its PIN with one attempt left: the saved PIN is not used. "
                    "Enter it on the Modem page.",
                    level="warning"
                )

            return "held"

        ok, output = sim_command([f"--pin={pin}"])

        if ok:
            log_event("modem", "SIM unlocked with the saved PIN.")
            return "unlocked"

        after = retries_of(get_modem_data(max_age=0).get("pin_retries"))

        if not code_was_wrong(output, before, after):
            return "failed"

        auto_unlock_state["rejected"] = time.time()

        # Never sent again; a pending Modem change can keep it from
        # being saved, the in-memory "rejected" holds it back meanwhile.
        forgotten = remember_pin("")

        log_event(
            "modem",
            "The SIM rejected the saved PIN" + (f" ({after} attempts left)" if after is not None else "")
            + ("; it was forgotten." if forgotten else ".")
            + " Enter the PIN on the Modem page.",
            level="warning"
        )

        return "rejected"

    finally:
        _sim_lock.release()


AUTO_UNLOCK_SECONDS = 10

_auto_unlock_started = False


def watch_sim():
    """Starts the auto-unlock thread once."""

    global _auto_unlock_started

    if _auto_unlock_started:
        return

    _auto_unlock_started = True

    def loop():

        while True:

            try:
                if auto_unlock_state["rejected"] is None:
                    auto_unlock()
            except Exception as e:
                print(f"[modem] SIM unlock: {e}")

            time.sleep(AUTO_UNLOCK_SECONDS)

    threading.Thread(target=loop, daemon=True, name="sim-unlock").start()


def unlocked_sim_data():
    """(data, error): the modem data while the SIM is unlocked."""

    data = get_modem_data(max_age=0)

    if data.get("state") != "ready":
        return None, "No modem found."

    if data.get("lock") in BLOCKING_LOCKS:
        return None, "Unlock the SIM first."

    return data, None


def set_pin_lock(enabled, pin, remember=True, confirm=False):
    """
    Turns the SIM PIN on or off (the SIM wants its current PIN for
    both). Turning it off forgets the saved PIN.
    """

    pin = str(pin or "").strip()

    if not isinstance(enabled, bool):
        return {"success": False, "message": "Invalid value."}

    if not PIN_RE.fullmatch(pin):
        return {"success": False, "message": "The PIN has 4 to 8 digits."}

    with _sim_lock:

        data, error = unlocked_sim_data()

        if error:
            return {"success": False, "message": error}

        if data.get("pin_enabled") is enabled:
            return {"success": False, "message": "The PIN is on already." if enabled else "The PIN is off already."}

        before = retries_of(data.get("pin_retries"))

        if needs_confirmation(before, PIN_CONFIRM_AT) and not confirm:
            return {
                "success": False, "confirm": True, "retries": before,
                "message": "This is the last attempt: a wrong PIN blocks the SIM."
            }

        ok, output = sim_command([f"--pin={pin}", "--enable-pin" if enabled else "--disable-pin"])

        after_data = get_modem_data(max_age=0)

        if not ok:

            if not code_was_wrong(output, before, retries_of(after_data.get("pin_retries"))):
                return {"success": False, "message": f"The SIM did not take it: {output}"}

            return pin_failure("Wrong PIN.", after_data)

        auto_unlock_state.update(rejected=None, held=False)

        remember_pin(pin if enabled and remember else "")

    if enabled:
        return {"success": True, "message": "PIN on: the SIM asks for it at every start."}

    return {"success": True, "message": "PIN off."}


def change_pin(pin, new_pin, remember=True, confirm=False):
    """Changes the SIM PIN; a saved PIN becomes the new one."""

    pin, new_pin = str(pin or "").strip(), str(new_pin or "").strip()

    if not PIN_RE.fullmatch(pin) or not PIN_RE.fullmatch(new_pin):
        return {"success": False, "message": "The PIN has 4 to 8 digits."}

    with _sim_lock:

        data, error = unlocked_sim_data()

        if error:
            return {"success": False, "message": error}

        if data.get("pin_enabled") is False:
            return {"success": False, "message": "The PIN is off: turn it on first."}

        before = retries_of(data.get("pin_retries"))

        if needs_confirmation(before, PIN_CONFIRM_AT) and not confirm:
            return {
                "success": False, "confirm": True, "retries": before,
                "message": "This is the last attempt: a wrong PIN blocks the SIM."
            }

        ok, output = sim_command([f"--pin={pin}", f"--change-pin={new_pin}"])

        after_data = get_modem_data(max_age=0)

        if not ok:

            if not code_was_wrong(output, before, retries_of(after_data.get("pin_retries"))):
                return {"success": False, "message": f"The SIM did not take it: {output}"}

            return pin_failure("Wrong current PIN.", after_data)

        auto_unlock_state.update(rejected=None, held=False)

        if remember or get_modem_settings()["pin"]:
            remember_pin(new_pin)

    return {"success": True, "message": "PIN changed."}


def unblock_sim(puk, new_pin, remember=True, confirm=False):
    """
    Unblocks a SIM with its PUK and sets a new PIN. Wrong PUKs are
    counted down from ten; at zero the SIM is unusable for good.
    """

    puk, new_pin = str(puk or "").strip(), str(new_pin or "").strip()

    if not PUK_RE.fullmatch(puk):
        return {"success": False, "message": "The PUK has 8 digits."}

    if not PIN_RE.fullmatch(new_pin):
        return {"success": False, "message": "The new PIN has 4 to 8 digits."}

    with _sim_lock:

        data = get_modem_data(max_age=0)

        if data.get("lock") != "sim-puk":
            return {"success": False, "message": "The SIM is not blocked."}

        before = retries_of(data.get("puk_retries"))

        if needs_confirmation(before, PUK_CONFIRM_AT) and not confirm:
            return {
                "success": False, "confirm": True, "retries": before,
                "message": f"Only {before} PUK attempts left: after the last wrong one the SIM is unusable for good."
            }

        ok, output = sim_command([f"--puk={puk}", f"--pin={new_pin}"])

        after_data = get_modem_data(max_age=0)

        if not ok:

            if not code_was_wrong(output, before, retries_of(after_data.get("puk_retries"))):
                return {"success": False, "message": "The modem did not take the PUK now. Try again in a moment."}

            return pin_failure("Wrong PUK.", after_data, field="puk_retries")

        auto_unlock_state.update(rejected=None, held=False)

        remember_pin(new_pin if remember else "")

        if remember and get_modem_settings()["enabled"]:
            apply_modem_settings()

    return {"success": True, "message": "SIM unblocked with the new PIN."}


# -------------------------------------------------------------------
# The data link: USB mode, addresses, received data
# -------------------------------------------------------------------

USB_MODE_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "deploy", "modem-usb-mode.sh"
)

USB_MODES = {"qmi_wwan": "qmi", "cdc_mbim": "mbim"}

# A switch running in the background: {"running", "target", "message"}.
usb_switch = {"running": False, "target": None, "message": None}


def wwan_interface():
    """
    The modem's network interface (wwan0) and its driver, or (None, None).
    """

    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return None, None

    for name in names:

        if not name.startswith("wwan"):
            continue

        try:
            driver = os.path.basename(os.path.realpath(f"/sys/class/net/{name}/device/driver"))
        except OSError:
            driver = None

        return name, driver

    return None, None


def read_counter(interface, name):

    try:
        with open(f"/sys/class/net/{interface}/statistics/{name}") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def interface_addresses(interface):
    """
    (IPv4, IPv6) global addresses of an interface, None when it has none.
    """

    ipv4 = None
    lines = (run(["ip", "-o", "addr", "show", "dev", interface, "scope", "global"]) or "").splitlines()

    for line in lines:

        parts = line.split()

        if "inet" in parts:
            ipv4 = parts[parts.index("inet") + 1].split("/")[0]
            break

    return ipv4, pick_ipv6(lines)


def data_link(hardware):
    """
    USB mode, addresses and whether received data is being dropped:
    connected, but the driver counts the replies as errors (Quectel in
    MBIM mode) while next to nothing arrives.
    """

    interface, driver = wwan_interface()

    if interface is None:
        return {
            "usb_mode": None, "ipv4": None, "ipv6": None,
            "rx_packets": None, "rx_errors": None, "data_dropped": False,
            "quectel": str(hardware.get("usb_id") or "").startswith("2c7c:"),
            "usb_switch": dict(usb_switch)
        }

    ipv4, ipv6 = interface_addresses(interface)

    rx_packets = read_counter(interface, "rx_packets")
    rx_errors = read_counter(interface, "rx_errors")

    dropped = (
        rx_errors is not None and rx_packets is not None
        and rx_errors >= 3 and rx_errors > rx_packets
    )

    return {
        "usb_mode": USB_MODES.get(driver, driver),
        "ipv4": ipv4,
        "ipv6": ipv6,
        "rx_packets": rx_packets,
        "rx_errors": rx_errors,
        "data_dropped": dropped,
        "quectel": str(hardware.get("usb_id") or "").startswith("2c7c:"),
        "usb_switch": dict(usb_switch)
    }


def switch_usb_mode(target):
    """
    Starts switching a Quectel modem's USB mode in the background (the
    modem restarts, about a minute). Returns {"success", "message"}.
    """

    if target not in ("qmi", "mbim"):
        return {"success": False, "message": "Unknown mode."}

    if usb_switch["running"]:
        return {"success": False, "message": "The modem is already switching."}

    if not os.path.isfile(USB_MODE_SCRIPT):
        return {"success": False, "message": "The switch script is missing (reinstall Chardsoft Router OS)."}

    def work():

        ok, output = run_command(privileged([USB_MODE_SCRIPT, target]))

        lines = [l for l in str(output or "").strip().splitlines() if l.strip()]

        usb_switch.update({
            "running": False,
            "message": (lines[-1] if lines else ("Done." if ok else "The switch failed.")),
            "success": ok
        })

        # The connection comes back by itself; apply once for the new port.
        if ok and get_modem_settings()["enabled"]:
            apply_modem_settings()

    usb_switch.update({"running": True, "target": target, "message": None, "success": None})

    threading.Thread(target=work, name="modem-usb-mode", daemon=True).start()

    return {"success": True, "message": f"Switching the modem to {target.upper()} mode. It restarts; this takes about a minute."}
