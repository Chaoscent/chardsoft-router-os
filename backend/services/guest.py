"""
Guest Wi-Fi isolation: guests reach the internet, nothing else.

The guest networks' interfaces (wlan0-guest, see services/wifi.py) are
LAN interfaces for DHCP, DNS and the route out (their names start with
"wlan"), so the router serves them like any LAN. These chains, hooked
first into INPUT and FORWARD for IPv4 and IPv6 (before ufw, whatever
its policies), take the rest away:

    CHARDSOFT-GUEST-IN    from a guest to the router: DHCP, DHCPv6, DNS,
                      ping and IPv6 neighbour/router discovery pass on
                      to the firewall; everything else (the dashboard,
                      SSH, apps) is rejected.
    CHARDSOFT-GUEST-FWD   from a guest to any private address (the LAN,
                      other guest networks, the network of an upstream
                      router, VPNs) is rejected; nothing can open a
                      connection into a guest network.

Guests on the same network do not see each other either (ap_isolate in
the access point).
"""

from shutil import which

from services.network import run_command, privileged

IN_CHAIN = "CHARDSOFT-GUEST-IN"
FWD_CHAIN = "CHARDSOFT-GUEST-FWD"

PRIVATE = {
    "iptables": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"],
    "ip6tables": ["fc00::/7", "fe80::/10"],
}


def _tools():

    return [t for t in (which("iptables"), which("ip6tables")) if t]


def rules(tool, interfaces):
    """[(chain, args)] for one tool (iptables or ip6tables)."""

    v6 = tool.endswith("ip6tables")
    icmp = "ipv6-icmp" if v6 else "icmp"

    found = []

    for name in interfaces:

        for proto, port in (("udp", "547" if v6 else "67"), ("udp", "53"), ("tcp", "53")):
            found.append((IN_CHAIN, ["-i", name, "-p", proto, "--dport", port, "-j", "RETURN"]))

        found.append((IN_CHAIN, ["-i", name, "-p", icmp, "-j", "RETURN"]))
        found.append((IN_CHAIN, ["-i", name, "-j", "REJECT"]))

        for network in PRIVATE["ip6tables" if v6 else "iptables"]:
            found.append((FWD_CHAIN, ["-i", name, "-d", network, "-j", "REJECT"]))

        found.append((FWD_CHAIN, ["-o", name, "-m", "conntrack", "--ctstate", "NEW", "-j", "REJECT"]))

    return found


def write(interfaces):
    """Rebuilds both chains for these guest interfaces; empty: no rules."""

    for tool in _tools():

        def ipt(*args):
            return run_command(privileged([tool, *args]))

        for chain, parent in ((IN_CHAIN, "INPUT"), (FWD_CHAIN, "FORWARD")):

            ipt("-N", chain)

            ok, out = ipt("-F", chain)

            if not ok:
                return False, f"The guest network's firewall could not be reset: {out}"

            hooked, _ = ipt("-C", parent, "-j", chain)

            if not hooked:

                ok, out = ipt("-I", parent, "1", "-j", chain)

                if not ok:
                    return False, f"The guest network's firewall could not be activated: {out}"

        for chain, args in rules(tool, interfaces):

            ok, out = ipt("-A", chain, *args)

            if not ok:
                return False, f"The guest network's firewall failed: {out}"

    return True, "OK"


def is_current(interfaces):
    """Whether the chains are hooked and hold the rules for these interfaces."""

    tools = _tools()

    if not tools:
        return True

    tool = tools[0]

    for chain, parent in ((IN_CHAIN, "INPUT"), (FWD_CHAIN, "FORWARD")):
        ok, _ = run_command(privileged([tool, "-C", parent, "-j", chain]))
        if not ok:
            return False

    for chain, args in rules(tool, interfaces):
        ok, _ = run_command(privileged([tool, "-C", chain, *args]))
        if not ok:
            return False

    return True


def wanted():
    """The guest interfaces of the networks that are on."""

    from services.wifi import guest_interfaces

    return sorted(guest_interfaces())


def sync():
    """Brings the chains in line with the Wi-Fi settings. (ok, message)."""

    interfaces = wanted()

    if not _tools():
        return (True, "OK") if not interfaces else (False, "iptables is not available for the guest network.")

    return write(interfaces)


def watch_once():
    """Puts the rules back when something (a ufw reload) dropped them."""

    interfaces = wanted()

    if interfaces and not is_current(interfaces):
        write(interfaces)


def remove():
    """Takes both chains out (uninstall)."""

    for tool in _tools():

        for chain, parent in ((IN_CHAIN, "INPUT"), (FWD_CHAIN, "FORWARD")):

            for _ in range(5):
                ok, _out = run_command(privileged([tool, "-D", parent, "-j", chain]))
                if not ok:
                    break

            run_command(privileged([tool, "-F", chain]))
            run_command(privileged([tool, "-X", chain]))


_watching = False


def watch():

    import threading
    import time

    global _watching

    if _watching:
        return

    _watching = True

    def loop():

        while True:

            try:
                watch_once()
            except Exception as e:
                print(f"[guest] {e}")

            time.sleep(30)

    threading.Thread(target=loop, daemon=True, name="guest-firewall").start()
