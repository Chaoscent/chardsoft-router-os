"""
IPv4 over an IPv6-only WAN (CLAT, the device half of 464XLAT).

Mobile carriers increasingly hand out IPv6 only (Telekom's
internet.v6.telekom, also what the network picks with an empty APN).
Phones then translate their own IPv4 into IPv6 (a "CLAT"), and the
carrier's NAT64 (the "PLAT") translates it back. Devices behind the
router have no CLAT of their own, so the router runs one for the LAN:

    LAN device 10.43.0.157 --IPv4--> router
        NAT44 (CHARDSOFT-CLAT) to 192.0.0.4 out of the clat interface
        tayga: 192.0.0.4 <-> <WAN /64>::c1a7, destinations into the
        carrier's NAT64 prefix (e.g. 64:ff9b::1.1.1.1)
    --IPv6--> carrier NAT64 --IPv4--> internet

It runs by itself while the WAN has IPv6 but no real IPv4 (the modem's
placeholder 192.0.0.x does not count), forwarding is on and the routing
setting "clat" is on. The NAT64 prefix comes from the carrier's DNS
(RFC 7050: the AAAA records of ipv4only.arpa), else the well-known
64:ff9b::/96.

Pieces: the tayga package, the chardsoft-clat service (deploy/), its config
/etc/tayga/chardsoft-clat.conf (written here, checked by the root helper),
the NAT chain CHARDSOFT-CLAT, a default route over clat for the router's
own IPv4 (metric 2000: any real IPv4 route wins) and the LAN routing
table (services/wan_routing.py asks active_route()). A watcher keeps it
in step: the modem's /64 changes with every connection.
"""

import ipaddress
import os
import random
import socket
import struct
import tempfile
import threading
import time

from services.config import load_settings, load_running, save_running

from services.network import (
    run,
    run_command,
    privileged,
    get_wan_interface,
    pick_ipv6,
    MODEM_INTERFACE
)

from services.vpn_common import which

CONF = "/etc/tayga/chardsoft-clat.conf"
UNIT = "chardsoft-clat"
TUN = "clat"

# RFC 7335: 192.0.0.0/29 is for exactly this. Not .1/.2: modems put
# placeholder addresses there.
HOST_V4 = "192.0.0.4"
TAYGA_V4 = "192.0.0.6"

# Interface IDs in the WAN's /64 for the translated traffic and tayga.
HOST_ID = 0xc1a7
TAYGA_ID = 0xc1a6

WELL_KNOWN_PREFIX = "64:ff9b::/96"

# ipv4only.arpa resolves to these; a DNS64 server embeds them.
IPV4ONLY = (b"\xc0\x00\x00\xaa", b"\xc0\x00\x00\xab")

NAT_CHAIN = "CHARDSOFT-CLAT"
ROUTER_METRIC = "2000"

PREFIX_SECONDS = 3600
WATCH_SECONDS = 15

STATE = "clat"

_lock = threading.Lock()

# In memory: what was set up, so a quiet sync runs no commands.
_applied = {"key": None}


# -------------------------------------------------------------------
# When
# -------------------------------------------------------------------

def is_installed():

    return bool(which("tayga"))


def wan_addresses(wan):
    """(IPv6 address or None, whether the WAN has real IPv4)."""

    lines = (run(["ip", "-o", "addr", "show", "dev", wan, "scope", "global"]) or "").splitlines()

    native4 = False

    for line in lines:

        parts = line.split()

        if "inet" not in parts:
            continue

        try:
            address = ipaddress.IPv4Interface(parts[parts.index("inet") + 1]).ip
        except ValueError:
            continue

        if address not in ipaddress.IPv4Network("192.0.0.0/29"):
            native4 = True

    return pick_ipv6(lines), native4


def is_wanted(wan=None, addresses=None):
    """
    True or False; None when the WAN is down (keep what runs, the modem
    may only be reconnecting).
    """

    settings = load_settings("routing", {"forwarding": True, "clat": True})

    if not settings.get("forwarding", True) or not settings.get("clat", True) or not is_installed():
        return False

    wan = wan or get_wan_interface()

    if not wan:
        return False

    v6, native4 = addresses or wan_addresses(wan)

    if native4:
        return False

    if not v6:
        return None

    return True


# -------------------------------------------------------------------
# NAT64 prefix (RFC 7050)
# -------------------------------------------------------------------

def _dns_query(name, qtype=28):

    tid = random.randrange(1 << 16)
    header = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    question = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"

    return tid, header + question + struct.pack("!HH", qtype, 1)


def _skip_name(data, i):

    while True:

        length = data[i]

        if length == 0:
            return i + 1

        if length & 0xC0 == 0xC0:
            return i + 2

        i += 1 + length


def parse_aaaa(data, tid):
    """The IPv6 addresses in a DNS answer to our query."""

    if len(data) < 12:
        return []

    rid, flags, qd, an, _, _ = struct.unpack("!HHHHHH", data[:12])

    if rid != tid or not flags & 0x8000:
        return []

    i = 12

    for _ in range(qd):
        i = _skip_name(data, i) + 4

    found = []

    for _ in range(an):

        i = _skip_name(data, i)
        rtype, _, _, length = struct.unpack("!HHIH", data[i:i + 10])
        i += 10

        if rtype == 28 and length == 16:
            found.append(data[i:i + 16])

        i += length

    return found


def prefix_from_addresses(raw_addresses):
    """The /96 a DNS64 server embedded 192.0.0.170/171 in, or None."""

    for raw in raw_addresses:
        if raw[12:] in IPV4ONLY:
            return str(ipaddress.IPv6Network((raw[:12] + b"\0" * 4, 96)))

    return None


def wan_dns_servers(wan):

    servers = []

    for field in ("IP6.DNS", "IP4.DNS"):
        output = run(["nmcli", "-g", field, "device", "show", wan]) or ""
        servers += [s.strip() for s in output.replace("|", "\n").splitlines() if s.strip()]

    return servers


def discover_prefix(wan):
    """(prefix, how it was found): from the carrier's DNS64, else the default."""

    for server in wan_dns_servers(wan)[:4]:

        try:
            family = socket.AF_INET6 if ":" in server else socket.AF_INET
            tid, query = _dns_query("ipv4only.arpa")

            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.settimeout(2)
                s.sendto(query, (server, 53))
                data = s.recv(2048)

            prefix = prefix_from_addresses(parse_aaaa(data, tid))

            if prefix:
                return prefix, f"DNS64 of {server}"

        except (OSError, struct.error, IndexError, ValueError):
            continue

    return WELL_KNOWN_PREFIX, "default"


def get_prefix(wan):

    state = load_running(STATE, {}) or {}

    if state.get("prefix") and time.time() - state.get("prefix_time", 0) < PREFIX_SECONDS and state.get("wan") == wan:
        return state["prefix"], state.get("prefix_source", "")

    prefix, source = discover_prefix(wan)

    save_running(STATE, {**state, "prefix": prefix, "prefix_source": source,
                         "prefix_time": time.time(), "wan": wan})

    return prefix, source


# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

def addresses_for(wan_v6):
    """(our IPv6 for translated traffic, tayga's IPv6) in the WAN's /64."""

    net = ipaddress.IPv6Interface(f"{wan_v6}/64").network

    return str(net[HOST_ID]), str(net[TAYGA_ID])


def render_config(prefix, host_v6, tayga_v6):

    return (
        "# Generated by Chardsoft Router OS (services/clat.py). Do not edit by hand.\n"
        f"tun-device {TUN}\n"
        f"ipv4-addr {TAYGA_V4}\n"
        f"ipv6-addr {tayga_v6}\n"
        f"prefix {prefix}\n"
        f"map {HOST_V4} {host_v6}\n"
    )


def installed_config():

    try:
        with open(CONF) as f:
            return f.read()
    except OSError:
        return None


def install_config(text):

    fd, tmp = tempfile.mkstemp(prefix="chardsoft-clat-", suffix=".conf")

    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)

        return run_command(privileged(["install", "-D", "-m", "644", tmp, CONF]))

    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# -------------------------------------------------------------------
# Apply
# -------------------------------------------------------------------

def tun_index():

    try:
        with open(f"/sys/class/net/{TUN}/ifindex") as f:
            return f.read().strip()
    except OSError:
        return None


def wan_mtu(wan):

    try:
        with open(f"/sys/class/net/{wan}/mtu") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 1500


def _do(cmd):

    ok, out = run_command(privileged(cmd))

    return None if ok else f"{' '.join(cmd[:4])}: {out}"


def ipt(*args):

    return run_command(privileged(["iptables", "-t", "nat", *args]))


def set_nat(on):

    ipt("-N", NAT_CHAIN)
    ipt("-F", NAT_CHAIN)

    if on:
        ipt("-A", NAT_CHAIN, "-o", TUN, "-j", "MASQUERADE")

    hooked, _ = ipt("-C", "POSTROUTING", "-j", NAT_CHAIN)

    if not hooked:
        ipt("-A", "POSTROUTING", "-j", NAT_CHAIN)


def start(wan, wan_v6):
    """Sets everything up. Returns an error message or None."""

    prefix, source = get_prefix(wan)
    host_v6, tayga_v6 = addresses_for(wan_v6)
    text = render_config(prefix, host_v6, tayga_v6)

    changed = installed_config() != text

    if changed:
        ok, out = install_config(text)
        if not ok:
            return f"Could not write the CLAT config: {out}"

    active = (run(["systemctl", "is-active", UNIT]) or "").strip() == "active"

    if changed or not active or tun_index() is None:

        ok, out = run_command(privileged(["systemctl", "restart", UNIT]))

        if not ok:
            return f"The CLAT service did not start: {out}"

        for _ in range(20):
            if tun_index():
                break
            time.sleep(0.25)
        else:
            return "The CLAT interface did not appear."

    key = (text, tun_index(), wan)

    if _applied["key"] == key:
        return None

    errors = [e for e in (
        _do(["ip", "link", "set", TUN, "up", "mtu", str(max(1280, min(wan_mtu(wan) - 20, 1480)))]),
        _do(["ip", "addr", "replace", f"{HOST_V4}/32", "dev", TUN]),
        _do(["ip", "route", "replace", f"{TAYGA_V4}/32", "dev", TUN]),
        _do(["ip", "-6", "route", "replace", f"{host_v6}/128", "dev", TUN]),
        _do(["ip", "-6", "route", "replace", f"{tayga_v6}/128", "dev", TUN]),
        # The router's own IPv4; any real IPv4 default route wins.
        _do(["ip", "route", "replace", "default", "dev", TUN, "metric", ROUTER_METRIC]),
    ) if e]

    # On Ethernet the upstream router asks for our addresses (NDP);
    # the modem's link is point-to-point and routes the whole /64 to us.
    if wan != MODEM_INTERFACE and not wan.startswith("wwan"):
        errors += [e for e in (
            _do(["sysctl", "-w", f"net.ipv6.conf.{wan}.proxy_ndp=1"]),
            _do(["ip", "-6", "neigh", "replace", "proxy", host_v6, "dev", wan]),
            _do(["ip", "-6", "neigh", "replace", "proxy", tayga_v6, "dev", wan]),
        ) if e]

    # IPv6 forwarding carries the translated traffic to the WAN.
    from services import ipv6

    if not ipv6.is_forwarding_enabled():
        ok, out = ipv6.set_forwarding(True, wan)
        if not ok:
            errors.append(f"IPv6 forwarding: {out}")

    set_nat(True)

    if errors:
        return "; ".join(errors[:3])

    _applied["key"] = key

    state = load_running(STATE, {}) or {}
    save_running(STATE, {**state, "host_v6": host_v6, "since": state.get("since") or time.time()})

    return None


def stop():

    if tun_index() is None and _applied["key"] is None and not installed_config():
        return

    run_command(privileged(["systemctl", "stop", UNIT]))
    set_nat(False)

    _applied["key"] = None

    state = load_running(STATE, {}) or {}
    save_running(STATE, {k: v for k, v in state.items() if k not in ("host_v6", "since")})


def sync():
    """
    Starts or stops the CLAT as the WAN needs it. Returns (ok, message).
    """

    with _lock:

        wan = get_wan_interface()
        addresses = wan_addresses(wan) if wan else (None, False)
        wanted = is_wanted(wan, addresses)

        if wanted is None:
            return True, "WAN down; CLAT unchanged."

        if not wanted:
            stop()
            state = load_running(STATE, {}) or {}
            if state.get("error"):
                save_running(STATE, {**state, "error": None})
            return True, "CLAT off."

        error = start(wan, addresses[0])

        state = load_running(STATE, {}) or {}
        save_running(STATE, {**state, "error": error})

        return (False, error) if error else (True, "CLAT on.")


def is_active():

    return _applied["key"] is not None and tun_index() is not None


def active_route():
    """The LAN's IPv4 default route while the CLAT runs (wan_routing)."""

    return ["default", "dev", TUN] if is_active() else None


def get_status():

    state = load_running(STATE, {}) or {}

    return {
        "installed": is_installed(),
        "active": is_active(),
        "prefix": state.get("prefix") if is_active() else None,
        "prefix_source": state.get("prefix_source") if is_active() else None,
        "address": state.get("host_v6") if is_active() else None,
        "error": state.get("error")
    }


def remove():
    """Uninstall: service stopped, chain gone."""

    run_command(privileged(["systemctl", "stop", UNIT]))

    for args in (("-D", "POSTROUTING", "-j", NAT_CHAIN), ("-F", NAT_CHAIN), ("-X", NAT_CHAIN)):
        ipt(*args)
