"""
IPv6 for the LAN.

Mobile networks hand a router one /64 at most (often a single address),
rarely a prefix to delegate, so the LAN gets addresses of its own: a
unique local prefix (RFC 4193, fdXX:XXXX:XXXX::/48) chosen once per
router, one /64 of it per LAN that DHCP serves. dnsmasq announces it
with router advertisements (SLAAC, and the router as DNS server), and
NAT66 shares the WAN's IPv6 address like NAT does for IPv4. That works
the same with any provider, the modem or Ethernet WAN.

The switch is part of the routing settings ("ipv6", "ula_prefix"); the
routing area applies forwarding, NAT66 and the LAN addresses, and the
DHCP config adds the router advertisements.

Forwarding on makes the kernel ignore router advertisements on
interfaces where it handles them itself (accept_ra=1), which would cut
the WAN's own IPv6 off. The WAN is set to accept_ra=2 (accept them
anyway) first. NetworkManager handles them in userspace (accept_ra=0)
and is left alone.

The LAN addresses are set with `ip`, like the Wi-Fi addresses, and
re-added by a small watcher when an interface was reset.
"""

import ipaddress
import secrets
import threading
import time
import zlib

from services.config import load_settings, load_running, save_running

from services.network import (
    run,
    run_command,
    privileged,
    get_wan_interface,
    pick_ipv6
)

from services.vpn_common import which

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

ROUTING = "routing"

NAT_CHAIN = "CHARDSOFT-NAT6"

FORWARD_SYSCTL = "net.ipv6.conf.all.forwarding"

PROC_CONF = "/proc/sys/net/ipv6/conf"

# Running state: the LAN addresses this module set, {"br0": "fd..::1/64"},
# so they can be removed when IPv6 or the interface goes away.
APPLIED = "ipv6_address"

ULA = ipaddress.IPv6Network("fd00::/8")

WATCH_SECONDS = 15


# -------------------------------------------------------------------
# Prefix
# -------------------------------------------------------------------

def new_ula_prefix():
    """A random /48 in fd00::/8, as RFC 4193 asks (40 random bits)."""

    value = (0xfd << 120) | (secrets.randbits(40) << 80)

    return str(ipaddress.IPv6Network((value, 48)))


def parse_ula_prefix(value):
    """The IPv6Network of a valid ULA /48, else None."""

    try:
        network = ipaddress.IPv6Network(str(value or "").strip())
    except ValueError:
        return None

    if network.prefixlen != 48 or not network.subnet_of(ULA):
        return None

    return network


def get_settings():

    settings = load_settings(ROUTING, {}) or {}

    return {
        "enabled": settings.get("ipv6") is True,
        "prefix": settings.get("ula_prefix") or ""
    }


def is_enabled(settings=None):

    settings = settings or get_settings()

    return settings["enabled"] and parse_ula_prefix(settings["prefix"]) is not None


def lan_networks(interfaces, prefix):
    """
    One /64 per interface: the subnet ID comes from the interface's
    name, so a LAN keeps its prefix when others are added or removed.
    """

    base = parse_ula_prefix(prefix)

    if not base:
        return {}

    networks = {}
    used = set()

    for name in sorted(set(interfaces)):

        subnet = zlib.crc32(name.encode()) & 0xFFFF

        # Two names with the same checksum: the next free ID.
        while subnet in used:
            subnet = (subnet + 1) & 0xFFFF

        used.add(subnet)

        networks[name] = ipaddress.IPv6Network(
            (int(base.network_address) | (subnet << 64), 64)
        )

    return networks


def router_address(network):
    """The router's own address in a LAN /64: ::1."""

    return f"{network.network_address + 1}/{network.prefixlen}"


# -------------------------------------------------------------------
# LAN addresses
# -------------------------------------------------------------------

def served_interfaces():
    """The interfaces dnsmasq serves (a bridge port through its bridge)."""

    # Imported here: the DHCP config reads this module.
    from services import dhcp

    return [scope["served"] for scope in dhcp.served_scopes()]


def wanted_addresses(settings=None):

    settings = settings or get_settings()

    if not is_enabled(settings):
        return {}

    networks = lan_networks(served_interfaces(), settings["prefix"])

    return {name: router_address(net) for name, net in networks.items()}


def global_addresses(interface):
    """The global IPv6 addresses (with prefix length) of an interface."""

    output = run(["ip", "-6", "-o", "addr", "show", "dev", interface, "scope", "global"])

    addresses = []

    for line in (output or "").splitlines():

        parts = line.split()

        if "inet6" in parts:
            addresses.append(parts[parts.index("inet6") + 1])

    return addresses


def _ip6(*args):

    return run_command(privileged(["ip", "-6", *args]))


def sync_lan_addresses(settings=None):
    """
    Adds the router's address to each served LAN and removes the ones
    no longer wanted. Cheap when nothing changed (the watcher calls it).
    """

    wanted = wanted_addresses(settings)
    applied = load_running(APPLIED, None) or {}
    errors = []

    for interface, address in applied.items():

        if wanted.get(interface) != address:
            # Fails harmlessly when the interface or address is gone.
            _ip6("addr", "del", address, "dev", interface)

    for interface, address in wanted.items():

        if address in global_addresses(interface):
            continue

        # NetworkManager may have switched IPv6 off on a LAN interface.
        sysctl = which("sysctl")

        if sysctl:
            run_command(privileged([
                sysctl, "-w", f"net.ipv6.conf.{interface}.disable_ipv6=0"
            ]))

        ok, result = _ip6("addr", "replace", address, "dev", interface, "nodad")

        if not ok:
            errors.append(f"{interface}: {result}")

    if wanted != applied:
        save_running(APPLIED, wanted)

    if errors:
        return False, "Could not set the LAN's IPv6 address: " + "; ".join(errors)

    return True, "OK"


# -------------------------------------------------------------------
# Forwarding
# -------------------------------------------------------------------

def _read_proc(path):

    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def is_forwarding_enabled():

    return _read_proc(f"{PROC_CONF}/all/forwarding") == "1"


def set_forwarding(enabled, wan):

    tool = which("sysctl")

    if not tool:
        return (False, "sysctl is not available.") if enabled else (True, "OK")

    # Keep the WAN's own IPv6 when the kernel handles its router
    # advertisements (1 would stop doing so with forwarding on).
    if enabled and wan and _read_proc(f"{PROC_CONF}/{wan}/accept_ra") == "1":

        ok, result = run_command(privileged([
            tool, "-w", f"net.ipv6.conf.{wan}.accept_ra=2"
        ]))

        if not ok:
            return False, f"Could not keep IPv6 on {wan}: {result}"

    return run_command(privileged([
        tool, "-w", f"{FORWARD_SYSCTL}={1 if enabled else 0}"
    ]))


# -------------------------------------------------------------------
# NAT66
# -------------------------------------------------------------------

def nat_rule(wan, prefix):

    return ["-s", prefix, "-o", wan, "-j", "MASQUERADE"]


def ip6tables(*args):

    tool = which("ip6tables")

    if not tool:
        return False, "ip6tables is not available."

    return run_command(privileged([tool, "-t", "nat", *args]))


def is_nat_active(wan, prefix):

    hooked, _ = ip6tables("-C", "POSTROUTING", "-j", NAT_CHAIN)

    if not hooked:
        return False

    active, _ = ip6tables("-C", NAT_CHAIN, *nat_rule(wan, prefix))

    return active


def apply_nat(enabled, wan, prefix):
    """Rebuilds the NAT66 chain: one masquerade rule for the WAN, or none."""

    if not which("ip6tables"):
        return (False, "ip6tables is not available.") if enabled else (True, "OK")

    # Creating fails harmlessly when the chain exists.
    ip6tables("-N", NAT_CHAIN)

    ok, result = ip6tables("-F", NAT_CHAIN)

    if not ok:
        return False, f"Could not reset NAT66: {result}"

    if enabled:

        ok, result = ip6tables("-A", NAT_CHAIN, *nat_rule(wan, prefix))

        if not ok:
            return False, f"Could not enable NAT66 on {wan}: {result}"

    hooked, _ = ip6tables("-C", "POSTROUTING", "-j", NAT_CHAIN)

    if not hooked:

        ok, result = ip6tables("-A", "POSTROUTING", "-j", NAT_CHAIN)

        if not ok:
            return False, f"Could not activate NAT66: {result}"

    return True, "OK"


# -------------------------------------------------------------------
# Apply / verify / status (called by the routing area)
# -------------------------------------------------------------------

def apply(forwarding, nat):

    settings = get_settings()
    on = is_enabled(settings)
    wan = get_wan_interface()

    # The CLAT (IPv4 over an IPv6-only WAN) needs IPv6 forwarding too.
    from services import clat

    clat_needs = clat.is_wanted(wan) is True or clat.is_active()

    ok, result = set_forwarding((on or clat_needs) and forwarding, wan)

    if not ok:
        return False, f"Could not change IPv6 forwarding: {result}"

    ok, result = apply_nat(on and nat, wan, settings["prefix"])

    if not ok:
        return False, result

    ok, result = sync_lan_addresses(settings)

    if not ok:
        return False, result

    if not on:
        return True, "IPv6 off."

    return True, f"IPv6 on ({settings['prefix']})."


def verify(forwarding, nat):

    settings = get_settings()

    if not is_enabled(settings):
        return True, "OK"

    if forwarding and not is_forwarding_enabled():
        return False, "IPv6 forwarding did not turn on."

    if nat and not is_nat_active(get_wan_interface(), settings["prefix"]):
        return False, "The NAT66 rule is not active."

    for interface, address in wanted_addresses(settings).items():
        if address not in global_addresses(interface):
            return False, f"{interface} has no IPv6 address."

    return True, "OK"


def get_status():

    settings = get_settings()
    on = is_enabled(settings)
    wan = get_wan_interface()

    lans = []

    if on:
        for name, net in lan_networks(served_interfaces(), settings["prefix"]).items():
            address = router_address(net)
            lans.append({
                "interface": name,
                "network": str(net),
                "address": address.split("/")[0],
                "active": address in global_addresses(name)
            })

    wan_lines = (run(["ip", "-6", "-o", "addr", "show", "dev", wan, "scope", "global"]) or "").splitlines() if wan else []

    return {
        "enabled": on,
        "prefix": settings["prefix"],
        "forwarding": is_forwarding_enabled(),
        "nat": on and is_nat_active(wan, settings["prefix"]),
        "lans": lans,
        # The same address the Modem page shows: a stable one, not a
        # privacy address that changes every few hours.
        "wan_address": pick_ipv6(wan_lines),
        "installed": bool(which("ip6tables"))
    }


# -------------------------------------------------------------------
# Watcher
# -------------------------------------------------------------------

_watching = False


def watch_lan_addresses():
    """
    Puts the LAN addresses back when an interface lost them (a Wi-Fi
    access point or the bridge restarted).
    """

    global _watching

    if _watching:
        return

    _watching = True

    def loop():

        while True:

            time.sleep(WATCH_SECONDS)

            try:
                if is_enabled():
                    sync_lan_addresses()
            except Exception:
                # Logged by the next apply; the watcher keeps going.
                pass

    threading.Thread(target=loop, daemon=True, name="ipv6-watch").start()
