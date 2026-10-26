"""
Throughput of every network interface, sampled once per second in the
background.

Every page reads the same samples, so the status bar and the dashboard
always agree, no matter how many browsers poll them. The "internet"
rates are the WAN's (eth0 in WAN mode, otherwise the modem); while
that is missing, the interface holding the default route is used.
The dashboard can also show any other interface (LAN, Wi-Fi, VPN).
"""

import threading
import time
from collections import deque

from services.network import get_wan_interface, get_default_interface

SAMPLE_SECONDS = 1

HISTORY_SECONDS = 60

# Not worth showing: loopback and the virtual links of containers.
HIDDEN_PREFIXES = ("lo", "veth", "docker", "br-")

_lock = threading.Lock()
_thread = None

# {interface: deque of {"t": unix time, "rx": Mbps, "tx": Mbps}}
histories = {}

# {interface: {"rx": Mbps, "tx": Mbps}}
_rates = {}

_latest = {"rx": 0.0, "tx": 0.0, "interface": None}


def _read_counters():
    """
    {interface: (rx_bytes, tx_bytes)} from /proc/net/dev.
    """

    counters = {}

    with open("/proc/net/dev") as f:

        for line in f.readlines()[2:]:

            name, _, data = line.partition(":")
            parts = data.split()

            if len(parts) >= 9:
                counters[name.strip()] = (int(parts[0]), int(parts[8]))

    return counters


def _pick_interface(names):

    for name in (get_wan_interface(), get_default_interface()):

        if name in names:
            return name

    return next((n for n in names if not n.startswith(HIDDEN_PREFIXES)), None)


def _sample_forever():

    # {interface: (rx_bytes, tx_bytes, time)}
    previous = {}

    while True:

        started = time.time()

        try:

            counters = {
                name: value for name, value in _read_counters().items()
                if not name.startswith(HIDDEN_PREFIXES)
            }

            wan = _pick_interface(counters)

            with _lock:

                for name, (rx, tx) in counters.items():

                    before = previous.get(name)

                    # The first sample or a counter reset only sets the baseline.
                    if before and rx >= before[0] and tx >= before[1]:

                        dt = max(started - before[2], 0.001)

                        down = round((rx - before[0]) * 8 / dt / 1_000_000, 4)
                        up = round((tx - before[1]) * 8 / dt / 1_000_000, 4)

                        _rates[name] = {"rx": down, "tx": up}

                        histories.setdefault(name, deque(maxlen=HISTORY_SECONDS)).append(
                            {"t": round(started, 3), "rx": down, "tx": up}
                        )

                        if name == wan:
                            _latest.update(rx=down, tx=up, interface=name)

                    previous[name] = (rx, tx, started)

                # Interfaces that went away (unplugged adapter, VPN down).
                for name in list(histories):
                    if name not in counters:
                        histories.pop(name, None)
                        _rates.pop(name, None)
                        previous.pop(name, None)

        except Exception:
            previous = {}

        time.sleep(max(0.05, SAMPLE_SECONDS - (time.time() - started)))


def _ensure_sampler():

    global _thread

    with _lock:

        if _thread and _thread.is_alive():
            return

        _thread = threading.Thread(target=_sample_forever, name="traffic-sampler", daemon=True)
        _thread.start()


def get_traffic():
    """
    The latest one-second rates in Mbps: {"rx", "tx", "interface"}.
    """

    _ensure_sampler()

    with _lock:
        return dict(_latest)


def get_history(interface=None):
    """
    Up to the last 60 one-second samples of an interface (default: the
    WAN): [{"t", "rx", "tx"}].
    """

    _ensure_sampler()

    with _lock:
        name = interface or _latest["interface"]
        return list(histories.get(name, ()))


def get_interfaces():
    """
    The current rates of every interface, WAN first:
    [{"interface", "rx", "tx", "wan"}].
    """

    _ensure_sampler()

    with _lock:

        wan = _latest["interface"]

        rows = [
            {"interface": name, **rates, "wan": name == wan}
            for name, rates in _rates.items()
        ]

    return sorted(rows, key=lambda r: (not r["wan"], r["interface"]))
