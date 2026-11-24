"""
"Why no internet?": the dashboard's step-by-step check of the uplink.

Each step says in plain words what it found. The first one that fails
is the headline, with a button that fixes it where the router can (a
modem restart, switching mobile data on, repairing the routes), or a
link to the page where it is fixed. Only reads, except for fix().

    connection   the WAN has an interface and an address
    modem        modem found, SIM unlocked, mobile data on (modem WAN)
    sharing      forwarding and NAT are on (Firewall page)
    data_limit   the data limit has not stopped the LAN
    reach        a ping over IPv4 or IPv6 gets an answer
    dns          the router can look up names
    clat         IPv4 for devices on an IPv6-only plan
    lan_routing  devices' traffic leaves through the WAN
"""

import datetime
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from services import network, modem, routing, clat, wan_routing, data_usage, modem_watchdog

PING_TARGETS = {"-4": "1.1.1.1", "-6": "2606:4700:4700::1111"}

DNS_NAME = "one.one.one.one"

TIMEOUT = 3

_fix_lock = threading.Lock()


def ping(family, target):

    try:
        result = subprocess.run(
            ["ping", family, "-c", "1", "-W", "2", target],
            capture_output=True, text=True, timeout=TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    return result.returncode == 0


def resolves(name=DNS_NAME):

    try:
        result = subprocess.run(["getent", "ahosts", name], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return False

    return result.returncode == 0 and bool(result.stdout.strip())


def step(id, label, ok, detail, action=None):
    """ok: True, False, or None when the step does not apply."""

    return {"id": id, "label": label, "ok": ok, "detail": detail, "action": action}


def link(label, href):

    return {"kind": "link", "label": label, "href": href}


def fix_button(fix, label):

    return {"kind": "fix", "fix": fix, "label": label}


def modem_step(data):

    label = "Modem"

    if data["state"] == "absent":
        return step("modem", label, False, "No modem found. Plug it in, or use Ethernet as the internet connection.",
                    link("Network page", "#/network"))

    if data["state"] == "no_modemmanager":
        return step("modem", label, False, "A modem is plugged in, but ModemManager is missing. Run the installer again.")

    if data["state"] == "not_ready":
        return step("modem", label, False, "The modem is starting. This takes up to a minute.")

    if data.get("lock") == "sim-puk":
        return step("modem", label, False, "The SIM is blocked. Unblock it with its PUK in a phone, then put it back.")

    if data.get("lock") in ("sim-pin", "ph-sim-pin", "ph-net-pin"):
        return step("modem", label, False, "The SIM waits for its PIN.", link("Enter PIN", "#/modem"))

    if not modem.get_modem_settings().get("enabled") and data.get("connection") != "activated":
        return step("modem", label, False, "Mobile data is switched off.", fix_button("mobile_on", "Switch on mobile data"))

    if data.get("connection") == "activating":
        return step("modem", label, False, "Mobile data is connecting.")

    if data.get("connection") != "activated":
        return step("modem", label, False, "Mobile data is on, but not connected. A restart of the modem often helps.",
                    fix_button("restart_modem", "Restart modem"))

    if data.get("data_dropped"):
        return step("modem", label, False, "The modem is connected but drops everything it receives.",
                    fix_button("restart_modem", "Restart modem"))

    carrier = data.get("carrier") or "the carrier"
    signal = f", signal {data['signal']}%" if data.get("signal") is not None else ""

    return step("modem", label, True, f"Connected to {carrier}{signal}.")


def run_check():

    wan = network.get_wan_interface()
    is_modem = wan == network.MODEM_INTERFACE

    # The slow parts at once: two pings, a lookup, the modem.
    with ThreadPoolExecutor(max_workers=4) as pool:
        v4 = pool.submit(ping, "-4", PING_TARGETS["-4"])
        v6 = pool.submit(ping, "-6", PING_TARGETS["-6"])
        dns = pool.submit(resolves)
        modem_data = pool.submit(modem.get_modem_data) if is_modem else None
        v4, v6, dns = v4.result(), v6.result(), dns.result()
        modem_data = modem_data.result() if modem_data else None

    steps = []

    # 1. The uplink itself.
    address = network.get_ip(wan) if wan else None
    has_address = bool(address and address != "Unknown")

    if is_modem:
        steps.append(modem_step(modem_data))
    elif not wan:
        steps.append(step("connection", "Internet connection", False, "No internet connection is set up.",
                          link("Network page", "#/network")))
    else:
        steps.append(step("connection", "Internet connection", has_address or v4 or v6,
                          f"{wan} has the address {address}." if has_address
                          else f"{wan} has no address. Check the cable to your other router or modem."))

    # 2. Sharing with the LAN.
    settings = routing.get_routing_settings()

    if not settings["forwarding"] or not settings["nat"]:
        steps.append(step("sharing", "Sharing with your devices", False,
                          "Forwarding or NAT is switched off, so devices cannot use the router's internet.",
                          link("Firewall page", "#/firewall")))
    else:
        steps.append(step("sharing", "Sharing with your devices", True, "Forwarding and NAT are on."))

    # 3. The data limit.
    usage = data_usage.get_status()

    if usage["level"] == "blocked":
        end = datetime.date.fromisoformat(usage["period_end"])
        steps.append(step("data_limit", "Data limit", False,
                          f"The data limit is used up, so devices are offline until {end.day} {end:%b}.",
                          fix_button("resume", "Resume anyway")))
    elif usage["limit"]:
        steps.append(step("data_limit", "Data limit", True, f"{usage['percent']:g}% of the limit used."))

    # 4. Out there.
    if v4 or v6:
        both = "IPv4 and IPv6" if v4 and v6 else "IPv4" if v4 else "IPv6"
        steps.append(step("reach", "Reach the internet", True, f"Answers over {both}."))
    else:
        steps.append(step("reach", "Reach the internet", False, "No answer from the internet."))

    steps.append(step("dns", "Names (DNS)", dns,
                      "Names are found." if dns else "Names cannot be looked up, so websites do not open.",
                      None if dns or not (v4 or v6) else link("DNS page", "#/dns")))

    # 5. IPv4 for devices on an IPv6-only plan.
    wanted = clat.is_wanted(wan) if wan else None

    if wanted is True:
        active = clat.is_active()
        steps.append(step("clat", "IPv4 for devices", active,
                          "Your plan is IPv6 only; the router translates IPv4 for your devices." if active
                          else "Your plan is IPv6 only, and the IPv4 translation (CLAT) is not running.",
                          None if active else fix_button("repair", "Repair")))

    # 6. Devices' route out.
    if wan and wan_routing.is_wanted():
        ok = wan_routing.verify()
        steps.append(step("lan_routing", "Route for your devices", ok,
                          f"Devices' traffic leaves through {wan}." if ok
                          else "The route for your devices is out of date.",
                          None if ok else fix_button("repair", "Repair")))

    # The headline: the first problem, else online.
    problem = next((s for s in steps if s["ok"] is False), None)

    if problem:
        verdict, message, action = problem["id"], problem["detail"], problem["action"]
    else:
        verdict, message, action = "online", "Everything works.", None

    # The watchdog restarted the modem in the last hour.
    last = modem_watchdog.state.get("last_reset")
    restarted = last if last and time.time() - last < 3600 else None

    return {
        "verdict": verdict,
        "online": problem is None,
        "message": message,
        "action": action,
        "steps": steps,
        "wan": wan,
        "auto_restart": restarted,
        "checked": time.time()
    }


def fix(kind):
    """Runs a fix button's action. (ok, message)."""

    if not _fix_lock.acquire(blocking=False):
        return False, "A fix is already running."

    try:

        if kind == "restart_modem":
            ok = modem_watchdog.reset_modem("restarted from the dashboard", automatic=False)
            return (True, "The modem restarts. It reconnects within a minute.") if ok \
                else (False, "The modem could not be restarted.")

        if kind == "mobile_on":
            settings = modem.public_settings()
            outcome = modem.save_modem_settings({
                "enabled": True,
                "roaming": settings["roaming"],
                "apn": settings["apn"],
                "username": settings["username"]
            })
            return outcome["success"], outcome["message"]

        if kind == "resume":
            data_usage.resume()
            return True, "Devices are online again until the next billing period."

        if kind == "repair":
            ok, message = routing.apply_routing()
            try:
                clat.sync()
                wan_routing.sync()
            except Exception as e:
                return False, f"Repair failed: {e}"
            return ok, "Repaired." if ok else message

        return False, "Unknown fix."

    finally:
        _fix_lock.release()
