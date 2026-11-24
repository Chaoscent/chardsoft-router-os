"""
The Routes page: the routing tables and rules in plain words, and the
user's own static routes.

Static routes (the "static_routes" area, Safe Apply with confirmation:
a wrong route can cut the browser off) are added to the main table with
`proto 77`, so the router finds exactly its own routes again with
`ip route show proto 77` and never touches anyone else's. Specific
routes of the main table also apply to LAN devices' traffic (rule 9000
of services/wan_routing.py); a default route is not allowed here, the
WAN is chosen on the Network page.

A route whose gateway or interface is not there yet (cable unplugged,
VPN down) is not an error: it shows as "waiting", and the 10 s watcher
(wan_routing.watch) adds it once it can, and again after the kernel
removed it with its interface.
"""

import ipaddress
import re
import threading

from services.config import load_settings
from services.logs import log_event

from services.network import run, run_command, privileged

SETTINGS = "static_routes"

PROTO = "77"

MAX_ROUTES = 64

INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,15}$")
ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")

FAMILIES = ("-4", "-6")

# Shown in the tables (iproute2's names and numbers).
ORIGINS = {
    "kernel": "Connected",
    "dhcp": "DHCP",
    "ra": "Router advertisement",
    "static": "Network settings",
    "boot": "System",
    PROTO: "Your route",
}

TABLES = {
    "main": "Router",
    "4242": "Your devices",
}

_lock = threading.Lock()

# Why a route could not be added: {(destination, metric): message}.
_waiting = {}

# Routes that were in the kernel at the last sync (to tell when one
# stops), and the ones that stopped (to tell when one works again).
_working = set()
_lost = set()


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def get_settings():

    settings = load_settings(SETTINGS, {"routes": []})

    routes = settings.get("routes")

    return {"routes": routes if isinstance(routes, list) else []}


def _clean_route(route, index):

    if not isinstance(route, dict):
        raise ValueError("Invalid route.")

    # Named by what the user typed: the page sends every route at once.
    where = str(route.get("destination") or "").strip()[:45] or f"Route {index + 1}"

    try:
        network = ipaddress.ip_network(str(route.get("destination") or "").strip(), strict=False)
    except ValueError:
        raise ValueError(f"{where}: the destination must be a network like 10.20.0.0/16 or one address.")

    if network.prefixlen == 0:
        raise ValueError(f"{where}: a default route is not allowed here. Choose the internet connection on the Network page.")

    if network.is_loopback or network.is_multicast:
        raise ValueError(f"{where}: loopback and multicast networks cannot be routed.")

    gateway = str(route.get("gateway") or "").strip()

    if gateway:

        try:
            address = ipaddress.ip_address(gateway)
        except ValueError:
            raise ValueError(f"{where}: the gateway must be an IP address.")

        if address.version != network.version:
            raise ValueError(f"{where}: the gateway must be an IPv{network.version} address, like the destination.")

        if address in network and network.prefixlen < network.max_prefixlen:
            raise ValueError(f"{where}: the gateway cannot be inside the destination network.")

        gateway = str(address)

    interface = str(route.get("interface") or "").strip()

    if interface and not INTERFACE_RE.fullmatch(interface):
        raise ValueError(f"{where}: invalid interface name.")

    if not gateway and not interface:
        raise ValueError(f"{where}: enter a gateway, an interface, or both.")

    metric = route.get("metric", 0)

    if metric in ("", None):
        metric = 0

    try:
        metric = int(metric)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: the metric must be a number.")

    if isinstance(route.get("metric"), bool) or not 0 <= metric <= 9999:
        raise ValueError(f"{where}: the metric must be between 0 and 9999.")

    enabled = route.get("enabled", True)

    if not isinstance(enabled, bool):
        raise ValueError(f"{where}: invalid value.")

    comment = str(route.get("comment") or "").strip()

    if len(comment) > 64 or any(ord(c) < 0x20 for c in comment):
        raise ValueError(f"{where}: the note has at most 64 characters.")

    route_id = route.get("id")

    if not isinstance(route_id, str) or not ID_RE.fullmatch(route_id):
        route_id = f"r{index + 1}"

    return {
        "id": route_id,
        "destination": str(network),
        "gateway": gateway,
        "interface": interface,
        "metric": metric,
        "enabled": enabled,
        "comment": comment,
    }


def validate_settings(data):
    """(True, clean settings) or (False, message)."""

    if not isinstance(data, dict) or not isinstance(data.get("routes"), list):
        return False, "Invalid settings."

    if len(data["routes"]) > MAX_ROUTES:
        return False, f"At most {MAX_ROUTES} routes."

    clean = []
    seen = set()
    ids = set()

    try:

        for index, route in enumerate(data["routes"]):

            route = _clean_route(route, index)

            key = key_of(route)

            if key in seen:
                return False, f"{route['destination']} with metric {route['metric']} is there twice."

            seen.add(key)

            while route["id"] in ids:
                route["id"] = f"r{len(ids) + 1}-{index}"

            ids.add(route["id"])

            clean.append(route)

    except ValueError as e:
        return False, str(e)

    return True, {"routes": clean}


# ------------------------------------------------------------------
# Applying
# ------------------------------------------------------------------

def family_of(destination):

    return "-6" if ":" in destination else "-4"


def kernel_metric(route):
    """The metric as the kernel keeps it: IPv6 stores 0 as 1024."""

    if family_of(route["destination"]) == "-6" and not route["metric"]:
        return 1024

    return route["metric"]


def key_of(route):

    return route["destination"], kernel_metric(route)


def route_args(route):
    """ip route arguments without the verb."""

    args = [route["destination"]]

    if route["gateway"]:
        args += ["via", route["gateway"]]

    if route["interface"]:
        args += ["dev", route["interface"]]

    return args + ["metric", str(kernel_metric(route)), "proto", PROTO]


def parse_route(line):
    """
    One line of `ip route show` as a dict: type, destination, gateway,
    interface, metric, origin, source, table. None for nothing useful.
    """

    parts = line.split()

    if not parts:
        return None

    kind = "unicast"

    if parts[0] in ("unreachable", "blackhole", "prohibit", "throw", "local", "broadcast", "multicast", "anycast", "nat"):
        kind = parts.pop(0)

    if not parts:
        return None

    destination = parts.pop(0)

    def value(key):
        return parts[parts.index(key) + 1] if key in parts[:-1] else None

    metric = value("metric")

    return {
        "type": kind,
        "destination": destination,
        "gateway": value("via"),
        "interface": value("dev"),
        "metric": int(metric) if metric and metric.isdigit() else 0,
        "origin": value("proto") or "boot",
        "source": value("src"),
        "table": value("table") or "main",
        "down": "linkdown" in parts,
    }


def _normal(destination):

    try:
        return str(ipaddress.ip_network(destination, strict=False))
    except ValueError:
        return destination


def installed(family):
    """The router's own routes now in the kernel: {(destination, metric): parsed}."""

    found = {}

    for line in (run(["ip", family, "route", "show", "proto", PROTO]) or "").splitlines():

        route = parse_route(line)

        if route:
            found[(_normal(route["destination"]), route["metric"])] = route

    return found


def _matches(have, route):

    return (have.get("gateway") or "") == route["gateway"] and \
        (not route["interface"] or have.get("interface") == route["interface"])


def sync():
    """
    Brings the kernel in line with the settings. Returns (ok, message);
    routes that cannot be added yet are "waiting", not errors.
    """

    with _lock:

        wanted = [r for r in get_settings()["routes"] if r.get("enabled", True)]

        waiting = {}

        for family in FAMILIES:

            mine = [r for r in wanted if family_of(r["destination"]) == family]
            keys = {key_of(r) for r in mine}

            have = installed(family)

            for key in have:
                if key not in keys:
                    run_command(privileged(["ip", family, "route", "del", key[0], "metric", str(key[1]), "proto", PROTO]))

            for route in mine:

                key = key_of(route)

                if key in have and _matches(have[key], route):
                    continue

                ok, out = run_command(privileged(["ip", family, "route", "replace", *route_args(route)]))

                if not ok:
                    waiting[key] = explain(out)

        _waiting.clear()
        _waiting.update(waiting)

        working = {key_of(r) for r in wanted} - set(waiting)

        for key in sorted(_working & set(waiting)):
            log_event("routes", f"Your route to {key[0]} stopped working: {waiting[key]}", "warning")
            _lost.add(key)

        for key in sorted(_lost & working):
            log_event("routes", f"Your route to {key[0]} works again.", notify=True)

        # Deleted or switched-off routes are not "lost" any more.
        _lost.intersection_update(set(waiting))

        _working.clear()
        _working.update(working)

    if waiting:
        return True, f"Saved. {len(waiting)} route{'s' if len(waiting) > 1 else ''} waiting for {'their' if len(waiting) > 1 else 'its'} gateway or interface."

    return True, "Routes applied."


def explain(output):
    """The kernel's complaint in plain words."""

    text = (output or "").strip()

    if "Nexthop has invalid gateway" in text or "Network is unreachable" in text:
        return "The gateway is not on any network the router is connected to."

    if "Cannot find device" in text:
        return "The interface does not exist (unplugged, or the VPN is down)."

    if "No such device" in text or "Device for nexthop is not up" in text:
        return "The interface is down."

    return text.splitlines()[-1] if text else "The route could not be added."


def remove():
    """Takes the router's own routes out (uninstall)."""

    for family in FAMILIES:
        run_command(privileged(["ip", family, "route", "flush", "proto", PROTO]))


# ------------------------------------------------------------------
# What the page shows
# ------------------------------------------------------------------

def _shown(route, family):
    """Routes worth showing: no local, broadcast or multicast entries."""

    if route["table"] == "local" or route["type"] in ("local", "broadcast", "multicast", "anycast"):
        return False

    if family == "-6" and route["destination"].startswith(("fe80:", "ff00:", "ff02:", "multicast")):
        return False

    return True


def describe(route):

    destination = route["destination"]

    if destination == "default":
        target = "Everything else (internet)"
    else:
        target = destination

    origin = ORIGINS.get(route["origin"], route["origin"])

    return {
        **route,
        "target": target,
        "origin_label": origin,
        "table_label": TABLES.get(route["table"], f"Table {route['table']}"),
        "mine": route["origin"] == PROTO,
    }


RULE_NOTES = {
    "0": "Addresses of the router itself",
    "9000": "Specific routes of the router's table also apply to devices",
    "9001": "Devices on this network use the \"Your devices\" table",
    "32766": "Everything else uses the router's table",
    "32767": "Fallback table",
}


def get_rules(family):

    rules = []

    for line in (run(["ip", family, "rule", "show"]) or "").splitlines():

        pref, _, text = line.partition(":")
        text = " ".join(text.replace("[detached]", "").split())

        if not pref.strip().isdigit():
            continue

        note = RULE_NOTES.get(pref.strip())

        if not note and "fwmark" in text:
            note = "VPN tunnel traffic"
        elif not note and "lookup" in text:
            note = "VPN or policy routing"

        rules.append({"pref": int(pref), "rule": text, "note": note or ""})

    return rules


def get_status():

    tables = {}

    for family in FAMILIES:

        rows = []

        for line in (run(["ip", family, "route", "show", "table", "all"]) or "").splitlines():

            route = parse_route(line)

            if route and _shown(route, family):
                rows.append(describe(route))

        # The router's table first, then the LAN's, then the rest.
        order = {"main": 0, "4242": 1}
        rows.sort(key=lambda r: (order.get(r["table"], 2), r["table"], r["destination"] != "default", r["metric"]))

        tables["ipv4" if family == "-4" else "ipv6"] = rows

    active = {}

    for family in FAMILIES:
        active.update(installed(family))

    routes = []

    for route in get_settings()["routes"]:

        key = key_of(route)

        if not route.get("enabled", True):
            state, detail = "off", ""
        elif key in active:
            state, detail = "active", ""
        else:
            state, detail = "waiting", _waiting.get(key, "Not added yet.")

        routes.append({**route, "state": state, "detail": detail})

    return {
        "routes": routes,
        "tables": tables,
        "rules": {"ipv4": get_rules("-4"), "ipv6": get_rules("-6")},
    }
