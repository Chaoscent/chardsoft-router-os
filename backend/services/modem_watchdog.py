"""
Restarts the modem when its data link is stuck.

A Quectel modem can end up "connected" while nothing it receives gets
through: the USB driver counts every incoming packet as an error (seen
on the Pi after an APN change and reconnects), or the link sends and
nothing ever comes back. ModemManager and NetworkManager still report
"connected", so nothing else notices. A modem reset (mmcli -r) brings
it back within a minute.

Every SAMPLE_SECONDS the watchdog compares the wwan interface counters
with the last sample. The link counts as stuck in a sample when

    - receive errors went up by at least MIN_ERRORS and not a single
      packet arrived, or
    - at least MIN_SENT packets went out and not a single one arrived.

After STUCK_SAMPLES stuck samples in a row (about 45 s) the modem is
reset, at most once per COOLDOWN_SECONDS and MAX_RESETS_PER_HOUR times
an hour, so a SIM without service cannot make it reset for ever.
Every reset is written to the Logs page.

Each sample also makes sure a qmi_wwan link has a 1500 MTU
(modem.ensure_link_mtu): with less, full-size packets from the network
jam the link this way within a minute of real traffic.
"""

import threading
import time

from services import modem
from services.logs import log_event

SAMPLE_SECONDS = 15
STUCK_SAMPLES = 3
MIN_ERRORS = 20
MIN_SENT = 30
COOLDOWN_SECONDS = 300
MAX_RESETS_PER_HOUR = 3

state = {
    "previous": None,   # last counters {"rx_packets", "rx_errors", "tx_packets"}
    "stuck": 0,         # stuck samples in a row
    "resets": [],       # times of resets (monotonic)
    "last_reset": None  # wall-clock time of the last reset, for the Modem page
}

_started = False
_lock = threading.Lock()


def is_stuck(previous, current):
    """Whether the link looked stuck between two counter samples."""

    if not previous or not current:
        return False

    try:
        received = current["rx_packets"] - previous["rx_packets"]
        errors = current["rx_errors"] - previous["rx_errors"]
        sent = current["tx_packets"] - previous["tx_packets"]
    except (KeyError, TypeError):
        return False

    # Counters start again after the interface was recreated.
    if received < 0 or errors < 0 or sent < 0:
        return False

    if received > 0:
        return False

    return errors >= MIN_ERRORS or sent >= MIN_SENT


def may_reset(now):

    recent = [t for t in state["resets"] if now - t < 3600]
    state["resets"] = recent

    if recent and now - recent[-1] < COOLDOWN_SECONDS:
        return False

    return len(recent) < MAX_RESETS_PER_HOUR


def counters(interface):

    values = {name: modem.read_counter(interface, name) for name in ("rx_packets", "rx_errors", "tx_packets")}

    return None if None in values.values() else values


def reset_modem(reason, automatic=True):

    index = modem.get_modem_index()

    if index is None:
        return False

    ok, output = modem.run_command(modem.privileged(["mmcli", "-m", index, "-r"]))

    # The next reading must not show the stuck state from before.
    modem._modem_cache.update(time=0.0, data=None)

    if ok and not automatic:
        log_event("modem", f"Modem restarted: {reason}. It reconnects within a minute.")
    elif ok:
        log_event("modem", f"Modem restarted automatically: {reason}. It reconnects within a minute.", notify=True)
    else:
        log_event("modem", f"The modem's data link is stuck ({reason}), but restarting it failed: {output}", level="warning")

    return ok


def check():
    """One sample. Returns "reset", "stuck", "ok" or "idle"."""

    with _lock:

        interface, _ = modem.wwan_interface()

        # Only while mobile data is supposed to be up.
        if interface is None or modem.get_connection_state() != "activated":
            state.update(previous=None, stuck=0)
            return "idle"

        # A too small receive buffer is what makes the link stuck.
        modem.ensure_link_mtu()

        current = counters(interface)

        stuck = is_stuck(state["previous"], current)
        previous = state["previous"]
        state["previous"] = current
        state["stuck"] = state["stuck"] + 1 if stuck else 0

        if not stuck:
            return "ok"

        if state["stuck"] < STUCK_SAMPLES:
            return "stuck"

        now = time.monotonic()

        if not may_reset(now):
            return "stuck"

        errors = current["rx_errors"] - previous["rx_errors"]
        reason = (
            f"{errors} received packets were rejected and none got through"
            if errors >= MIN_ERRORS else
            "data went out but nothing came back"
        )

        state["resets"].append(now)
        state["last_reset"] = time.time()
        state.update(previous=None, stuck=0)

    reset_modem(reason)

    return "reset"


def watch():
    """Starts the watchdog thread once."""

    global _started

    if _started:
        return

    _started = True

    def loop():

        while True:

            time.sleep(SAMPLE_SECONDS)

            try:
                check()
            except Exception as e:
                print(f"[modem] Watchdog: {e}")

    threading.Thread(target=loop, daemon=True, name="modem-watchdog").start()
