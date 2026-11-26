"""
Packet capture (Tools page): tcpdump on one interface (or all) into a
.pcap file that Wireshark opens. One capture at a time.

- Filters: one device, a port, a protocol, and/or a capture filter of
  the admin's own (tcpdump/Wireshark capture filter syntax). The admin's
  own connection to the dashboard can be left out.
- Limits: tcpdump stops by itself after the time (-G/-W, also if the app
  restarts), and the app stops it at the size limit. The file lives in
  RAM (/tmp is tmpfs), so the size is capped.
- tcpdump runs through the root helper and writes the file as the app's
  user (-Z), into the app's own runtime folder. The file is deleted when
  a new capture starts, by the admin, or an hour after the capture ended.
"""

import ipaddress
import os
import pwd
import re
import subprocess
import threading
import time

from services.config import RUNTIME_DIR
from services.network import privileged
from services.net_tools import interfaces

CAPTURE_DIR = os.path.join(str(RUNTIME_DIR), "capture")
CAPTURE_FILE = os.path.join(CAPTURE_DIR, "capture.pcap")

DURATIONS = (30, 60, 300, 900, 1800, 3600)
SIZES_MB = (10, 25, 50, 100)
PROTOCOLS = ("tcp", "udp", "icmp", "icmp6", "arp")

KEEP_SECONDS = 3600

FILTER_RE = re.compile(r"[A-Za-z0-9 .:/()_!&|=<>\[\]-]{1,400}")

_lock = threading.Lock()
_state = {}


class CaptureError(Exception):
    pass


def available():

    return any(os.path.isfile(os.path.join(d, "tcpdump")) for d in ("/usr/sbin", "/usr/bin"))


def build_filter(options, viewer=None):
    """The capture filter (BPF) from the form; "" for everything."""

    parts = []

    host = str(options.get("host") or "").strip()

    if host:
        try:
            parts.append(f"host {ipaddress.ip_address(host)}")
        except ValueError:
            raise CaptureError("The device is an IP address, e.g. 192.168.1.20.")

    port = str(options.get("port") or "").strip()

    if port:
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise CaptureError("The port is a number from 1 to 65535.")
        parts.append(f"port {int(port)}")

    protocol = str(options.get("protocol") or "")

    if protocol:
        if protocol not in PROTOCOLS:
            raise CaptureError("Unknown protocol.")
        parts.append(protocol)

    custom = " ".join(str(options.get("filter") or "").split())

    if custom:
        if not FILTER_RE.fullmatch(custom) or custom.startswith("-"):
            raise CaptureError("The capture filter has characters it cannot have.")
        parts.append(f"({custom})")

    # The browser's own connection to the dashboard (and SSH) stays out.
    if options.get("exclude_self") and viewer:
        try:
            parts.append(f"not (host {ipaddress.ip_address(viewer)} and (port 80 or port 443 or port 22))")
        except ValueError:
            pass

    return " and ".join(parts)


def _size():

    try:
        return os.path.getsize(CAPTURE_FILE)
    except OSError:
        return 0


def _expire():
    """Deletes a finished capture after KEEP_SECONDS."""

    ended = _state.get("ended")

    if ended and time.time() - ended > KEEP_SECONDS:
        delete()


def status():

    with _lock:

        _expire()

        running = bool(_state.get("process") and _state["process"].poll() is None)

        return {
            "available": available(),
            "interfaces": ["any", *interfaces()],
            "durations": DURATIONS,
            "sizes_mb": SIZES_MB,
            "protocols": PROTOCOLS,
            "running": running,
            "interface": _state.get("interface"),
            "filter": _state.get("filter"),
            "started": _state.get("started"),
            "ended": _state.get("ended"),
            "seconds": _state.get("seconds"),
            "limit_mb": _state.get("limit_mb"),
            "size": _size() if _state else 0,
            "packets": _state.get("packets"),
            "reason": _state.get("reason"),
            "error": _state.get("error"),
            "file": bool(_state) and not running and _size() > 24,
            "expires": _state["ended"] + KEEP_SECONDS if _state.get("ended") else None
        }


def _watch(process, limit, deadline):
    """Stops tcpdump at the size or time limit; notes how it ended."""

    reason = "time"

    while process.poll() is None:

        # tcpdump's own -G only ends at the next packet after the time.
        if time.time() >= deadline:
            _stop(process)
            break

        if _size() >= limit:
            reason = "size"
            _stop(process)
            break

        if _state.get("stop_requested"):
            reason = "stopped"
            _stop(process)
            break

        time.sleep(0.5)

    _, err = process.communicate()

    packets = re.search(r"(\d+) packets? captured", err or "")

    with _lock:

        if _state.get("process") is not process:
            return

        _state.update(
            ended=time.time(),
            reason=reason,
            packets=int(packets.group(1)) if packets else None,
            error=None if packets or _size() > 24 else (err or "tcpdump stopped.").strip()[-300:]
        )


def _stop(process):

    process.terminate()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def start(options, viewer=None):

    if not available():
        raise CaptureError("tcpdump is not installed (run install.sh once to add it).")

    interface = str(options.get("interface") or "any")

    if interface != "any" and interface not in interfaces():
        raise CaptureError(f"The interface {interface} does not exist.")

    try:
        seconds = int(options.get("seconds") or 300)
        limit_mb = int(options.get("limit_mb") or 50)
    except (TypeError, ValueError):
        raise CaptureError("Unknown time or size.")

    if seconds not in DURATIONS or limit_mb not in SIZES_MB:
        raise CaptureError("Unknown time or size.")

    expression = build_filter(options, viewer)

    with _lock:

        if _state.get("process") and _state["process"].poll() is None:
            raise CaptureError("A capture is running. Stop it first.")

        _remove_file()
        os.makedirs(CAPTURE_DIR, mode=0o700, exist_ok=True)

        user = pwd.getpwuid(os.getuid()).pw_name

        cmd = ["tcpdump", "-i", interface, "-n", "-s", "0", "-U", "-Z", user,
               "-G", str(seconds), "-W", "1", "-w", CAPTURE_FILE]

        if expression:
            cmd.append(expression)

        process = subprocess.Popen(
            privileged(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )

        _state.clear()
        _state.update(
            process=process, interface=interface, filter=expression, started=time.time(),
            seconds=seconds, limit_mb=limit_mb
        )

    threading.Thread(
        target=_watch, args=(process, limit_mb * 1024 * 1024, time.time() + seconds), daemon=True
    ).start()

    # A wrong filter or interface ends tcpdump at once: report it here.
    for _ in range(20):
        time.sleep(0.1)
        if _state.get("ended"):
            break

    if _state.get("ended") and _state.get("error"):
        raise CaptureError(_state["error"])

    return status()


def stop():

    with _lock:

        process = _state.get("process")
        running = bool(process and process.poll() is None)

        if running:
            _state["stop_requested"] = True

    if running:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        # The watcher notes how it ended.
        for _ in range(20):
            if _state.get("ended"):
                break
            time.sleep(0.1)

    return status()


def _remove_file():

    try:
        os.remove(CAPTURE_FILE)
    except FileNotFoundError:
        pass


def delete():
    """Deletes the file (caller holds no lock, or is _expire under it)."""

    if _state.get("process") and _state["process"].poll() is None:
        raise CaptureError("Stop the capture first.")

    _remove_file()
    _state.clear()


def download_name():

    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(_state.get("started") or time.time()))

    return f"capture-{_state.get('interface') or 'any'}-{stamp}.pcap"
