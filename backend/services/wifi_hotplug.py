"""
USB Wi-Fi adapters plugged in or pulled out while the router runs. A
watcher looks at the radios every 5 s:

- plugged in, with a network saved for it on the Wi-Fi page: the
  network starts and DHCP serves it, no click needed;
- plugged in and new: a notification names it (driver, bands, whether
  it can send a network) and points to the Wi-Fi page, which lists it;
- pulled out: its access point is stopped, its address and DHCP are
  cleaned up; the network starts again when the adapter is back.

Interface names follow the plug order (wlan1, wlan2, ...), so each
network remembers the adapter it last ran on (wifi.RADIOS, the radio's
MAC). When another adapter shows up under that name its network stays
off until it is saved for it on the Wi-Fi page: a 6 GHz network never
lands on an adapter that cannot send it.

The firewall's essential rules (DHCP, DNS, the dashboard on each LAN
interface) follow the LAN interfaces too, USB Ethernet adapters
included: they are built from the interfaces that exist when the
firewall is applied, so an adapter plugged in later would otherwise
get its network but no addresses.

Nothing runs while a Safe Apply change waits for its confirmation; the
adapter is picked up right after.
"""

import os
import threading
import time

from services import transaction, wifi
from services.config import load_persistent, save_persistent
from services.logs import log_event


WATCH_SECONDS = 5

# hostapd may give up a moment after a "successful" start.
START_SETTLE = 4

# Radio MACs of adapters already announced (no second notification).
SEEN = "wifi_adapters_seen"
SEEN_MAX = 50

_watching = False

# {interface: radio MAC} at the last look; None before the first.
_last = None

# The LAN interfaces the essential firewall rules were synced for.
_lans = None


def present():

    return {name: wifi.radio_mac(name) for name in wifi.get_wireless_interfaces()}


def _read(path):

    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def describe(interface):
    """{"label", "driver", "bands", "access_points"} for a notification."""

    device = os.path.realpath(f"/sys/class/net/{interface}/device")
    driver = os.path.basename(os.path.realpath(f"{device}/driver")) if os.path.exists(f"{device}/driver") else ""

    # A USB adapter: the network device is one of its interfaces, the
    # names are on the USB device above it.
    maker = _read(os.path.join(device, "..", "manufacturer"))
    product = _read(os.path.join(device, "..", "product"))

    names = " ".join(p for p in (maker, product) if p and p.lower() not in ("wireless_device", "wireless device"))

    support = wifi.get_band_support(interface) or {}
    bands = [band for band, channels in support.items() if channels]

    label = names or "Wi-Fi adapter"

    if driver:
        label += f" ({driver})"

    return {
        "label": label,
        "driver": driver,
        "bands": bands,
        "access_points": wifi.max_access_points(interface),
    }


def _band_text(bands):

    return f"{', '.join(bands)} GHz" if bands else "unknown bands"


def _configured(interface):

    return wifi.get_wifi_settings()["networks"].get(interface)


def _start(interface):
    """
    Applies the saved Wi-Fi and DHCP settings (the new radio's network
    included). None while a change waits for its confirmation.
    """

    with transaction._lock:

        if transaction.load_pending():
            return None

        ok, message = wifi.apply_wifi_settings()

        if ok:
            from services import dhcp
            dhcp.apply_dhcp_settings()

    if not ok:
        return False, message

    time.sleep(START_SETTLE)

    if not wifi.is_hostapd_running(interface):
        return False, wifi.get_hostapd_error(interface) or "The access point stopped right after starting."

    return True, message


def _announce(interface, mac):

    seen = load_persistent(SEEN, [])
    seen = seen if isinstance(seen, list) else []

    info = describe(interface)

    if mac and mac in seen:
        log_event("wifi", f"Wi-Fi adapter plugged in: {interface}, {info['label']}.")
        return

    if info["access_points"] == 0:
        advice = "It cannot send a Wi-Fi network (no access point mode), so the router cannot use it."
        level = "warning"
    else:
        advice = "Set up its network on the Wi-Fi page."
        level = "info"

    log_event(
        "wifi",
        f"New Wi-Fi adapter: {interface}, {info['label']}, {_band_text(info['bands'])}. {advice}",
        level, notify=True, interface=interface
    )

    if mac:
        save_persistent(SEEN, (seen + [mac])[-SEEN_MAX:])


def added(interface, mac):
    """
    A radio appeared. False when it has to be tried again later (a
    change waits for its confirmation).
    """

    net = _configured(interface)

    if not net:
        _announce(interface, mac)
        return True

    ssid = net.get("ssid") or interface

    if not net.get("enabled"):
        log_event("wifi", f"Wi-Fi adapter plugged in: {interface}. Its network \"{ssid}\" is switched off.")
        return True

    if wifi.other_adapter(interface):
        log_event(
            "wifi",
            f"A different Wi-Fi adapter is plugged in as {interface} ({describe(interface)['label']}). "
            f"\"{ssid}\" was set up for the one before, so it stays off: check its settings on the "
            "Wi-Fi page and save them for this adapter.",
            "warning", interface=interface
        )
        return True

    if wifi.is_hostapd_running(interface):
        return True

    outcome = _start(interface)

    if outcome is None:
        return False

    ok, message = outcome

    if ok:
        log_event("wifi", f"Wi-Fi adapter plugged in: \"{ssid}\" is on ({interface}).", notify=True)
    else:
        log_event("wifi", f"Wi-Fi adapter plugged in, but \"{ssid}\" did not start on {interface}: {message}",
                  "warning", interface=interface)

    return True


def removed(interface):
    """A radio went away: its access point, address and DHCP are cleaned up."""

    net = _configured(interface)

    running = interface in wifi.running_instances() or interface in wifi.applied_addresses()

    if running:

        with transaction._lock:

            wifi.stop_access_point(interface)

            from services import dhcp, guest

            dhcp.apply_dhcp_settings()
            guest.sync()

    if net and net.get("enabled"):
        log_event("wifi", f"Wi-Fi adapter {interface} unplugged: \"{net.get('ssid') or interface}\" is off "
                          "until it is plugged in again.", notify=True)
    else:
        log_event("wifi", f"Wi-Fi adapter {interface} unplugged.")


def follow_firewall():
    """
    The essential rules for the LAN interfaces that exist now; synced
    when they change (and at the first look, for adapters that came
    after the firewall was applied at boot).
    """

    global _lans

    from services import firewall

    lans = sorted(firewall.get_lan_interfaces())

    if lans == _lans:
        return

    with transaction._lock:

        # The pending change (or its revert) applies the firewall
        # itself; looked at again afterwards.
        if transaction.load_pending():
            return

        ok, message = firewall.sync_essentials()

    if not ok:
        log_event("system", f"The firewall rules for {', '.join(lans)} could not be updated: {message}",
                  "warning", notify=True)

    # Also after a failure: one warning, not one every few seconds.
    _lans = lans


def check():
    """One look. At the first one, a saved network whose adapter came
    late (after the start-up apply) is started too."""

    global _last

    # Before a network starts: its DHCP must get through.
    follow_firewall()

    now = present()

    if _last is None:
        previous = {name: mac for name, mac in now.items() if wifi.is_hostapd_running(name) or not _wanted(name)}
    else:
        previous = _last

    for name in sorted(set(previous) - set(now)):
        removed(name)

    retry = {}

    for name in sorted(set(now) - set(previous)):
        if not added(name, now[name]):
            retry[name] = True

    # A different adapter under a known name (unplugged and another
    # plugged in between two looks) counts as out and in.
    for name in sorted(set(now) & set(previous)):
        if now[name] and previous[name] and now[name] != previous[name]:
            removed(name)
            if not added(name, now[name]):
                retry[name] = True

    # Not looked at yet: tried again next time.
    _last = {name: mac for name, mac in now.items() if name not in retry}


def _wanted(interface):

    net = _configured(interface)

    return bool(net and net.get("enabled"))


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
                print(f"[wifi-hotplug] {e}")

            time.sleep(WATCH_SECONDS)

    threading.Thread(target=loop, daemon=True, name="wifi-hotplug").start()
