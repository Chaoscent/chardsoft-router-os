"""
LAN traffic always leaves through the WAN.

The router's own default route is whatever has the lowest metric. When
eth0 or a Wi-Fi radio is also connected to another network (a home
router during testing, a hotel Wi-Fi), its DHCP default route wins over
the modem's, and LAN devices' traffic would be sent there, where the
firewall drops it ("Internet for <lan>" only allows the WAN) and NAT
does not apply. The router itself is online, the LAN is not.

So LAN traffic gets a routing table of its own:

    pref 9000  from all lookup main suppress_prefixlength 0
               Every specific route of the main table still applies
               (LAN to LAN, VPN servers, the router's own networks);
               only its default routes are skipped here.
    pref 9001  iif <lan> lookup 4242   (one per LAN interface)
               Traffic forwarded from a LAN uses table 4242:

    table 4242 default via <WAN gateway> dev <WAN>
               unreachable default metric 4000
               With the WAN down, LAN devices get "unreachable" at once
               instead of waiting for time-outs.

The router's own traffic is not affected. Same for IPv6.

While a full-tunnel VPN profile is engaged the rules are removed: the
tunnel's own routes (wg-quick's rules, OpenVPN's def1 routes) take the
LAN's traffic, as before. A watcher keeps the table in step with the
WAN (the modem's gateway changes with every connection).
"""

import threading
import time

from services.config import load_settings

from services.network import (
    run,
    run_command,
    privileged,
    get_wan_interface,
    get_lan_interface_names
)

TABLE = "4242"
SUPPRESS_PREF = "9000"
LAN_PREF = "9001"
WAN_METRIC = "100"
UNREACHABLE_METRIC = "4000"

WATCH_SECONDS = 10

FAMILIES = ("-4", "-6")

_lock = threading.Lock()


def full_tunnel_engaged():

    try:
        from services import vpn_profiles
        profile = vpn_profiles.engaged_profile()
    except Exception:
        return False

    return bool(profile and profile.get("full_tunnel"))


def is_wanted():
    """LAN routing is on while forwarding is on and no full tunnel runs."""

    settings = load_settings("routing", {"forwarding": True})

    return bool(settings.get("forwarding", True)) and not full_tunnel_engaged()


def wan_route(family, wan):
    """The WAN's default route as ip arguments, or None without one."""

    # IPv6-only WAN: IPv4 goes through the CLAT (services/clat.py).
    if family == "-4":
        from services import clat
        route = clat.active_route()
        if route:
            return [*route, "metric", WAN_METRIC]

    output = run(["ip", family, "route", "show", "default", "dev", wan]) or ""

    for line in output.splitlines():

        parts = line.split()

        if not parts or parts[0] != "default":
            continue

        via = ["via", parts[parts.index("via") + 1]] if "via" in parts else []

        # An explicit metric: ip -6 would add 1024 by itself and the
        # comparison with the table would never match.
        return ["default", *via, "dev", wan, "metric", WAN_METRIC]

    return None


def wanted_state(family, wan, lans):

    rules = [f"{SUPPRESS_PREF}: from all lookup main suppress_prefixlength 0"]
    rules += [f"{LAN_PREF}: from all iif {lan} lookup {TABLE}" for lan in lans]

    routes = [f"unreachable default metric {UNREACHABLE_METRIC}"]

    route = wan_route(family, wan) if wan else None

    if route:
        routes.insert(0, " ".join(route))

    return rules, routes


def current_rules(family):

    found = []

    for line in (run(["ip", family, "rule", "show"]) or "").splitlines():

        text = " ".join(line.replace("[detached]", "").split())

        if text.startswith((f"{SUPPRESS_PREF}:", f"{LAN_PREF}:")):
            found.append(text)

    return found


def current_routes(family):

    output = run(["ip", family, "route", "show", "table", TABLE]) or ""

    routes = []

    for line in output.splitlines():

        routes.append(normalize_route(line))

    return routes


def normalize_route(line):
    """
    A route as `ip route show` prints it, without the details ip adds by
    itself (proto, pref, flags, "dev lo" of IPv6 unreachable routes).
    """

    parts = line.split()

    for extra in ("proto", "pref", "expires", "scope"):
        if extra in parts:
            i = parts.index(extra)
            del parts[i:i + 2]

    parts = [p for p in parts if p not in ("linkdown", "onlink", "dead", "offload")]

    if parts[:1] == ["unreachable"] and "dev" in parts:
        i = parts.index("dev")
        del parts[i:i + 2]

    return " ".join(parts)


def _ip(family, *args):

    return run_command(privileged(["ip", family, *args]))


def _rule_args(rule):
    """"9001: from all iif wlan1 lookup 4242" -> ip rule arguments."""

    pref, _, selector = rule.partition(": ")

    return ["pref", pref, *selector.split()]


def sync():
    """
    Brings the rules and the table in line with the WAN and the LANs.
    Cheap when nothing changed. Returns (ok, message).
    """

    with _lock:

        wanted = is_wanted()
        wan = get_wan_interface() if wanted else None
        lans = [n for n in get_lan_interface_names() if n != wan] if wanted else []

        errors = []

        for family in FAMILIES:

            rules, routes = wanted_state(family, wan, lans) if wanted else ([], [])

            have = current_rules(family)

            for rule in have:
                if rule not in rules:
                    ok, out = _ip(family, "rule", "del", *_rule_args(rule))
                    if not ok:
                        errors.append(out)

            for rule in rules:
                if rule not in have:
                    ok, out = _ip(family, "rule", "add", *_rule_args(rule))
                    if not ok:
                        errors.append(out)

            have_routes = current_routes(family)

            if not routes:
                if have_routes:
                    _ip(family, "route", "flush", "table", TABLE)
                continue

            # Route by route, never a flush: LAN traffic keeps flowing.
            for route in have_routes:
                if route not in routes:
                    _ip(family, "route", "del", *route.split(), "table", TABLE)

            for route in routes:
                if route not in have_routes:
                    ok, out = _ip(family, "route", "replace", *route.split(), "table", TABLE)
                    if not ok:
                        errors.append(out)

        if errors:
            return False, "; ".join(errors[:3])

        return True, "OK"


def verify():
    """Whether the rules and routes are as wanted."""

    wanted = is_wanted()
    wan = get_wan_interface() if wanted else None
    lans = [n for n in get_lan_interface_names() if n != wan] if wanted else []

    for family in FAMILIES:

        rules, routes = wanted_state(family, wan, lans) if wanted else ([], [])

        if sorted(current_rules(family)) != sorted(rules):
            return False

        if sorted(current_routes(family)) != sorted(routes):
            return False

    return True


def remove():
    """Takes everything out (uninstall)."""

    for family in FAMILIES:

        for rule in current_rules(family):
            _ip(family, "rule", "del", *_rule_args(rule))

        _ip(family, "route", "flush", "table", TABLE)


_watching = False


def watch():

    global _watching

    if _watching:
        return

    _watching = True

    def loop():

        while True:

            try:
                from services import clat
                clat.sync()
            except Exception as e:
                print(f"[routing] CLAT: {e}")

            try:
                sync()
            except Exception as e:
                print(f"[routing] LAN routing: {e}")

            # Static routes the kernel dropped with their interface, or
            # that waited for their gateway (Routes page).
            try:
                from services import routes
                routes.sync()
            except Exception as e:
                print(f"[routing] static routes: {e}")

            time.sleep(WATCH_SECONDS)

    threading.Thread(target=loop, daemon=True, name="lan-routing").start()
