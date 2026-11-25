"""
Routing: IP forwarding and NAT from the LAN to the internet.

Forwarding is the kernel switch (net.ipv4.ip_forward) that lets the
router pass traffic between interfaces at all. NAT (masquerading)
rewrites LAN addresses to the router's WAN address on the way out,
so LAN devices and VPN clients reach the internet.

The WAN is eth0 while it is in WAN mode, otherwise the modem (set on
the Network page; see network.get_wan_interface).

The NAT rule lives in a dedicated chain in the nat table, hooked into
POSTROUTING. ufw only manages the filter table, so enabling, disabling
or reloading the firewall never removes it.

IPv6 for the LAN (a prefix of its own, router advertisements, NAT66)
is optional and lives in services/ipv6.py; its switch and prefix are
stored here, so forwarding and NAT stay one setting for both.
"""

from services.config import load_settings

from services import transaction

from services.network import (
    run_command,
    privileged,
    get_wan_interface,
    get_eth0_role
)

from services.vpn_common import (
    which,
    get_lan_networks
)

from services.firewall import is_forwarding_enabled

from services import ipv6

from services import wan_routing

from services import clat

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

ROUTING = "routing"

DEFAULT_SETTINGS = {
    "forwarding": True,
    "nat": True,
    # Off until switched on; the prefix is chosen the first time.
    "ipv6": False,
    "ula_prefix": "",
    # IPv4 for the LAN over an IPv6-only WAN (services/clat.py); only
    # runs while the WAN has no IPv4 of its own.
    "clat": True
}

CHAIN = "CHARDSOFT-NAT"

FORWARD_SYSCTL = "net.ipv4.ip_forward"


# -------------------------------------------------------------------
# Settings
# -------------------------------------------------------------------

def get_routing_settings():

    settings = load_settings(ROUTING, DEFAULT_SETTINGS)

    return {key: settings[key] for key in DEFAULT_SETTINGS}


def get_routing_baseline():
    """
    What runs before the first change: forwarding as the system has
    it, and no NAT of ours.
    """

    return {
        "forwarding": is_forwarding_enabled(),
        "nat": False,
        "ipv6": False,
        "ula_prefix": "",
        "clat": False
    }


def validate_routing_settings(data):
    """
    Returns (True, settings) or (False, message).
    """

    settings = get_routing_settings()
    settings.update(data or {})

    for key in ("forwarding", "nat", "ipv6", "clat"):

        if not isinstance(settings.get(key), bool):
            return False, f"Invalid value for {key}."

    prefix = settings.get("ula_prefix") or ""

    if not isinstance(prefix, str):
        return False, "Invalid IPv6 prefix."

    if prefix:

        network = ipv6.parse_ula_prefix(prefix)

        if not network:
            return False, "The IPv6 prefix must be a /48 in fd00::/8, like fd12:3456:789a::/48."

        prefix = str(network)

    # Chosen once, kept when IPv6 is switched off and on again.
    elif settings["ipv6"]:
        prefix = ipv6.new_ula_prefix()

    return True, {
        "forwarding": settings["forwarding"],
        "nat": settings["nat"],
        "ipv6": settings["ipv6"],
        "ula_prefix": prefix,
        "clat": settings["clat"]
    }


def save_routing_settings(data):

    ok, result = validate_routing_settings(data)

    if not ok:
        return transaction.result(False, result)

    outcome = transaction.change(ROUTING, result)
    outcome["status"] = get_routing_status()

    return outcome


# -------------------------------------------------------------------
# Status
# -------------------------------------------------------------------

def nat_rule(wan):

    return ["-o", wan, "-j", "MASQUERADE"]


def iptables(*args):

    tool = which("iptables")

    if not tool:
        return False, "iptables is not available."

    return run_command(privileged([tool, "-t", "nat", *args]))


def is_nat_active(wan):

    hooked, _ = iptables("-C", "POSTROUTING", "-j", CHAIN)

    if not hooked:
        return False

    active, _ = iptables("-C", CHAIN, *nat_rule(wan))

    return active


def get_routing_status():

    settings = get_routing_settings()
    wan = get_wan_interface()

    return {
        "settings": settings,
        "wan": wan,
        "eth0_role": get_eth0_role(),
        "forwarding": is_forwarding_enabled(),
        "nat": is_nat_active(wan),
        "lan_networks": get_lan_networks(),
        "ipv6": ipv6.get_status(),
        "clat": clat.get_status(),
        "installed": bool(which("iptables") and which("sysctl"))
    }


# -------------------------------------------------------------------
# Apply / verify
# -------------------------------------------------------------------

def set_forwarding(enabled):

    tool = which("sysctl")

    if not tool:
        return False, "sysctl is not available."

    return run_command(privileged([
        tool,
        "-w",
        f"{FORWARD_SYSCTL}={1 if enabled else 0}"
    ]))


def apply_nat(enabled, wan):
    """
    Rebuilds the NAT chain: one masquerade rule for the WAN, or none.
    """

    if not which("iptables"):

        if not enabled:
            return True, "NAT off."

        return False, "iptables is not available."

    # Creating fails harmlessly when the chain exists.
    iptables("-N", CHAIN)

    ok, result = iptables("-F", CHAIN)

    if not ok:
        return False, f"Could not reset NAT: {result}"

    if enabled:

        ok, result = iptables("-A", CHAIN, *nat_rule(wan))

        if not ok:
            return False, f"Could not enable NAT on {wan}: {result}"

    # The hook stays while NAT is off; the chain is just empty.
    hooked, _ = iptables("-C", "POSTROUTING", "-j", CHAIN)

    if not hooked:

        ok, result = iptables("-A", "POSTROUTING", "-j", CHAIN)

        if not ok:
            return False, f"Could not activate NAT: {result}"

    return True, f"NAT on {wan}." if enabled else "NAT off."


# TCP MSS clamping (mangle table): mobile networks often have a smaller
# MTU than the LAN, and without it some sites stall for LAN devices
# (path MTU discovery blocked somewhere on the way).
MSS_CHAIN = "CHARDSOFT-MSS"


def apply_mss_clamping():

    for tool in filter(None, (which("iptables"), which("ip6tables"))):

        def mangle(*args):
            return run_command(privileged([tool, "-t", "mangle", *args]))

        mangle("-N", MSS_CHAIN)

        ok, result = mangle("-F", MSS_CHAIN)

        if not ok:
            return False, f"Could not reset MSS clamping: {result}"

        ok, result = mangle(
            "-A", MSS_CHAIN, "-p", "tcp", "--tcp-flags", "SYN,RST", "SYN",
            "-j", "TCPMSS", "--clamp-mss-to-pmtu"
        )

        if not ok:
            return False, f"Could not set up MSS clamping: {result}"

        hooked, _ = mangle("-C", "FORWARD", "-j", MSS_CHAIN)

        if not hooked:

            ok, result = mangle("-A", "FORWARD", "-j", MSS_CHAIN)

            if not ok:
                return False, f"Could not activate MSS clamping: {result}"

    return True, "OK"


# Traffic that came in on the WAN never goes back out of it. With IPv6
# forwarding on, packets for unused addresses in the modem's /64 would
# otherwise be sent back to the carrier, which routes them to us again,
# until their hop limit runs out: every stray packet sent ~60 times, on
# mobile data. ufw's "deny routed" catches it too, but not with the
# firewall off.
LOOP_CHAIN = "CHARDSOFT-LOOP"


def apply_loop_guard(wan):

    outs = sorted({wan, "wwan+"} - {None, ""})

    for tool in filter(None, (which("iptables"), which("ip6tables"))):

        def filt(*args):
            return run_command(privileged([tool, *args]))

        filt("-N", LOOP_CHAIN)

        ok, result = filt("-F", LOOP_CHAIN)

        if not ok:
            return False, f"Could not reset the loop guard: {result}"

        for out in outs:
            ok, result = filt("-A", LOOP_CHAIN, "-i", out, "-o", out, "-j", "DROP")
            if not ok:
                return False, f"Could not set up the loop guard: {result}"

        hooked, _ = filt("-C", "FORWARD", "-j", LOOP_CHAIN)

        if not hooked:

            ok, result = filt("-I", "FORWARD", "1", "-j", LOOP_CHAIN)

            if not ok:
                return False, f"Could not activate the loop guard: {result}"

    return True, "OK"


def apply_routing():

    settings = get_routing_settings()
    wan = get_wan_interface()

    ok, result = set_forwarding(settings["forwarding"])

    if not ok:
        return False, f"Could not change IP forwarding: {result}"

    ok, result = apply_nat(settings["nat"], wan)

    if not ok:
        return False, result

    ok, mss = apply_mss_clamping()

    if not ok:
        return False, mss

    ok, loop = apply_loop_guard(wan)

    if not ok:
        return False, loop

    ok, v6 = ipv6.apply(settings["forwarding"], settings["nat"])

    if not ok:
        return False, v6

    # IPv4 over an IPv6-only WAN. Not fatal: the rest of the routing
    # works without it, and its state is shown on the Firewall page.
    clat.sync()

    # LAN traffic leaves through the WAN, whatever other default
    # routes the router has (see services/wan_routing.py).
    ok, lan = wan_routing.sync()

    if not ok:
        return False, f"Could not route the LAN to the WAN: {lan}"

    forwarding = "on" if settings["forwarding"] else "off"

    return True, f"Forwarding {forwarding}. {result} {v6}"


def verify_routing():

    settings = get_routing_settings()

    if is_forwarding_enabled() != settings["forwarding"]:
        return False, "IP forwarding did not reach the requested state."

    if settings["nat"] and not is_nat_active(get_wan_interface()):
        return False, "The NAT rule is not active."

    return ipv6.verify(settings["forwarding"], settings["nat"])
