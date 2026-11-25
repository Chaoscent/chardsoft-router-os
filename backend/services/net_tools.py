"""
The Tools page: ping, traceroute, name lookup, port check, "which
way" (`ip route get`) and speed (iperf3), run from the router, with the
result in plain words next to the raw output. The router can also be
the iperf3 server for one test from a device on the LAN.

Only reads, except the speed test server: it opens its port on the LAN
interfaces (never the WAN) while it waits, and closes it afterwards. Targets are checked (an IP address or a host name, never
anything that looks like an option), commands run without a shell and
with a timeout, and one test runs at a time.
"""

import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import time
from shutil import which

TOOLS = ("ping", "traceroute", "lookup", "port", "route", "iperf")

HOST_RE = re.compile(
    r"^(?=.{1,253}\.?$)([A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?\.)*"
    r"[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?\.?$"
)

INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,15}$")

PING_COUNT = 4
TRACE_HOPS = 20
PORT_TIMEOUT = 4

# iperf3: its usual port, a 10 s test; the router as the server waits
# 5 minutes for one test.
IPERF_PORT = 5201
IPERF_SECONDS = 10
SERVER_WAIT = 300

# The server's port rule (firewall.sync_rules owner; the comment keeps
# it out of the user's rules).
SPEEDTEST_OWNER = "speedtest"
SPEEDTEST_COMMENT = "Chardsoft Router OS: Speed test"

_lock = threading.Lock()


class ToolError(ValueError):
    """A problem worth showing as it is."""


# ------------------------------------------------------------------
# Input
# ------------------------------------------------------------------

def clean_target(text):

    target = str(text or "").strip()

    if not target:
        raise ToolError("Enter an address or a name, e.g. 1.1.1.1 or example.com.")

    # IPv6 with a zone, e.g. fe80::1%eth0.
    address, _, zone = target.partition("%")

    try:
        ipaddress.ip_address(address)
    except ValueError:
        pass
    else:
        if zone and not INTERFACE_RE.fullmatch(zone):
            raise ToolError("Invalid interface after the %.")
        return target

    if target.startswith("-") or not HOST_RE.fullmatch(target):
        raise ToolError("That is neither an IP address nor a host name.")

    return target.rstrip(".")


def clean_family(value):

    value = str(value or "auto")

    if value not in ("auto", "4", "6"):
        raise ToolError("Choose IPv4, IPv6 or automatic.")

    return value


def interfaces():
    """Network interfaces to send from, without loopback."""

    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return []

    return [n for n in names if n != "lo" and INTERFACE_RE.fullmatch(n)]


def clean_interface(value):

    value = str(value or "").strip()

    if not value:
        return ""

    if not INTERFACE_RE.fullmatch(value) or value not in interfaces():
        raise ToolError(f"The interface {value} does not exist.")

    return value


def clean_port(value):

    try:
        port = int(str(value).strip())
    except ValueError:
        raise ToolError("The port is a number from 1 to 65535.")

    if not 1 <= port <= 65535:
        raise ToolError("The port is a number from 1 to 65535.")

    return port


def family_flag(family):

    return [] if family == "auto" else [f"-{family}"]


def execute(command, timeout):
    """(exit code, output): stdout and stderr together, as in a terminal."""

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ToolError(f"{command[0]} is not installed. Run the installer again.")
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out + "\n(stopped: it took too long)"

    return result.returncode, (result.stdout + result.stderr).strip()


def name_problem(output):
    """The resolver's or the kernel's complaint in plain words, or None."""

    text = output or ""

    if "Name or service not known" in text or "unknown host" in text or "Temporary failure in name resolution" in text \
            or "No address associated" in text or "Cannot resolve" in text:
        return "The name could not be found. Check the spelling, or try the name lookup."

    if "Address family for hostname not supported" in text:
        return "The name has no address of the chosen IP version."

    if "Network is unreachable" in text or "connect: Invalid argument" in text:
        return "The router has no route to that address. With IPv6, the connection may have no IPv6."

    if "Cannot assign requested address" in text:
        return "The chosen interface has no address of that IP version."

    if "SO_BINDTODEVICE" in text or "Operation not permitted" in text:
        return "The router may not send from that interface."

    return None


def result(ok, summary, level=None, **extra):
    """level: good, warning or bad (the pill on the page)."""

    return {"ok": ok, "level": level or ("good" if ok else "bad"), "summary": summary, **extra}


# ------------------------------------------------------------------
# Ping
# ------------------------------------------------------------------

def parse_ping(output):

    address = re.search(r"^PING \S+ \(([^)]+)\)", output, re.M) or re.search(r"^PING (\S+)", output, re.M)
    sent = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", output)
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/", output)

    replies = [float(t) for t in re.findall(r"time[=<]([\d.]+) ?ms", output)]

    return {
        "address": address.group(1) if address else None,
        "sent": int(sent.group(1)) if sent else 0,
        "received": int(sent.group(2)) if sent else 0,
        "min": float(rtt.group(1)) if rtt else None,
        "avg": float(rtt.group(2)) if rtt else None,
        "max": float(rtt.group(3)) if rtt else None,
        "replies": replies
    }


def explain_ping(data, output):

    sent, received = data["sent"], data["received"]

    if not sent:
        return result(False, name_problem(output) or "The ping could not start.", **data)

    if not received:
        return result(False, "No answer. The device is off or unreachable, or it ignores pings (many servers and phones do).",
                      **data)

    speed = f"{data['avg']:.0f} ms on average" if data["avg"] is not None and data["avg"] >= 1 \
        else "under 1 ms on average"

    if received < sent:
        return result(True, f"{sent - received} of {sent} answers lost, {speed}. The connection is unstable.",
                      "warning", **data)

    if data["avg"] is not None and data["avg"] > 300:
        return result(True, f"All {sent} answered, but slowly: {speed}. Expect sluggish pages and calls.",
                      "warning", **data)

    return result(True, f"All {sent} answered, {speed}. The connection is fine.", **data)


def run_ping(target, family, interface):

    command = ["ping", *family_flag(family), "-c", str(PING_COUNT), "-i", "0.5", "-W", "2"]

    if interface:
        command += ["-I", interface]

    code, output = execute(command + [target], timeout=15)

    return {**explain_ping(parse_ping(output), output), "raw": output}


# ------------------------------------------------------------------
# Traceroute
# ------------------------------------------------------------------

def hop_note(address):

    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return ""

    if ip in ipaddress.ip_network("100.64.0.0/10"):
        return "Carrier network (shared addresses)"

    if ip.is_private:
        return "Private network (your other router or the carrier's)"

    return ""


def parse_traceroute(output):

    lines = output.splitlines()
    head = re.search(r"^traceroute to \S+ \(([^)]+)\)", output, re.M)

    hops = []

    for line in lines:

        match = re.match(r"^\s*(\d+)\s+(.*)$", line)

        if not match:
            continue

        rest = match.group(2)
        address = re.search(r"([0-9a-fA-F:.]+[0-9a-fA-F])\s+(?:\([^)]*\)\s+)?[\d.]+ ms", rest)
        time_ms = re.search(r"([\d.]+) ms", rest)

        address = address.group(1) if address else None

        hops.append({
            "hop": int(match.group(1)),
            "address": address,
            "ms": float(time_ms.group(1)) if time_ms else None,
            "note": hop_note(address) if address else "No answer",
            "flag": " ".join(re.findall(r"!\w*", rest))
        })

    return {"address": head.group(1) if head else None, "hops": hops}


def explain_traceroute(data, output):

    hops = data["hops"]

    if not hops:
        return result(False, name_problem(output) or "The traceroute could not start.", **data)

    last = hops[-1]

    if data["address"] and last["address"] == data["address"]:
        return result(True, f"Reached in {last['hop']} hop{'s' if last['hop'] != 1 else ''}, "
                            f"{last['ms']:.0f} ms there and back." if last["ms"] is not None
                      else f"Reached in {last['hop']} hops.", **data)

    if any(h["flag"] for h in hops):
        stop = next(h for h in hops if h["flag"])
        return result(False, f"Hop {stop['hop']} ({stop['address']}) says the destination is unreachable.", **data)

    answered = [h for h in hops if h["address"]]

    if not answered:
        return result(False, "No hop answered, not even the first. The internet connection seems down.", **data)

    return result(False,
                  f"The last answer came from hop {answered[-1]['hop']} ({answered[-1]['address']}). "
                  "Further routers stay silent: normal for many servers, so if the website works, all is well.",
                  "warning", **data)


def run_traceroute(target, family, interface):

    command = ["traceroute", *family_flag(family), "-n", "-q", "1", "-w", "2", "-m", str(TRACE_HOPS)]

    if interface:
        command += ["-i", interface]

    if not which("traceroute"):
        raise ToolError("traceroute is not installed. Run the installer again.")

    code, output = execute(command + [target], timeout=TRACE_HOPS * 3)

    return {**explain_traceroute(parse_traceroute(output), output), "raw": output}


# ------------------------------------------------------------------
# Name lookup
# ------------------------------------------------------------------

def blocked_by(name):
    """The blocklist entry of the DNS page that covers the name, or None."""

    try:
        from services.dns import get_dns_settings
        blocked = get_dns_settings().get("blocked_domains") or []
    except Exception:
        return None

    name = name.lower().rstrip(".")

    for domain in blocked:

        domain = str(domain).lower().strip(".")

        if domain and (name == domain or name.endswith("." + domain)):
            return domain

    return None


def run_lookup(target, family):

    try:
        ip = ipaddress.ip_address(target.partition("%")[0])
    except ValueError:
        ip = None

    if ip:

        started = time.monotonic()
        code, output = execute(["getent", "hosts", str(ip)], timeout=6)
        took = (time.monotonic() - started) * 1000

        names = output.split()[1:] if code == 0 else []

        if names:
            return result(True, f"{ip} is called {names[0]} ({took:.0f} ms).", names=names, addresses=[], raw=output)

        return result(True, f"{ip} has no name. That is common, nothing to worry about.", "warning",
                      names=[], addresses=[], raw=output)

    database = {"auto": "ahosts", "4": "ahostsv4", "6": "ahostsv6"}[family]

    started = time.monotonic()
    code, output = execute(["getent", database, target], timeout=8)
    took = (time.monotonic() - started) * 1000

    addresses = []

    for line in output.splitlines():
        parts = line.split()
        if parts and parts[0] not in addresses and len(parts) >= 2 and parts[1] == "STREAM":
            addresses.append(parts[0])

    v4 = [a for a in addresses if ":" not in a]
    v6 = [a for a in addresses if ":" in a]

    rule = blocked_by(target)

    if rule and (not addresses or all(a in ("0.0.0.0", "::") for a in addresses)):
        return result(False, f"Blocked by your DNS blocklist ({rule}, DNS page).",
                      addresses=addresses, names=[], raw=output or "(blocked)")

    if not addresses:
        return result(False, "The name was not found. Check the spelling; if every name fails, the DNS server "
                             "is unreachable (see the dashboard's internet check).",
                      addresses=[], names=[], raw=output or "(no answer)")

    parts = []

    if v4:
        parts.append(f"{len(v4)} IPv4")
    if v6:
        parts.append(f"{len(v6)} IPv6")

    return result(True, f"Found {' and '.join(parts)} address{'es' if len(addresses) > 1 else ''} in {took:.0f} ms.",
                  addresses=addresses, names=[], raw=output)


# ------------------------------------------------------------------
# Port check
# ------------------------------------------------------------------

def run_port(target, family, port):

    kinds = {"auto": socket.AF_UNSPEC, "4": socket.AF_INET, "6": socket.AF_INET6}[family]

    try:
        options = socket.getaddrinfo(target, port, kinds, socket.SOCK_STREAM)
    except socket.gaierror:
        return result(False, "The name could not be found.", raw="")

    last = ""

    for family_id, kind, proto, _, address in options[:4]:

        started = time.monotonic()

        try:

            with socket.socket(family_id, kind, proto) as sock:
                sock.settimeout(PORT_TIMEOUT)
                sock.connect(address)

            took = (time.monotonic() - started) * 1000

            return result(True, f"Port {port} is open on {address[0]}: the connection was accepted in {took:.0f} ms.",
                          address=address[0], raw=f"connected to {address[0]} port {port}")

        except ConnectionRefusedError:
            return result(False, f"Port {port} is closed on {address[0]}: the device answered, but nothing listens there.",
                          "warning", address=address[0], raw=f"{address[0]} port {port}: connection refused")

        except socket.timeout:
            last = f"{address[0]} port {port}: no answer within {PORT_TIMEOUT} s"

        except OSError as e:
            last = f"{address[0]} port {port}: {e.strerror or e}"

    return result(False, f"No answer on port {port}. A firewall drops it, or the device is off.", raw=last)


# ------------------------------------------------------------------
# Which way
# ------------------------------------------------------------------

def parse_route_get(output):

    line = output.splitlines()[0] if output else ""
    parts = line.split()

    def value(key):
        return parts[parts.index(key) + 1] if key in parts[:-1] else None

    return {
        "type": parts[0] if parts and parts[0] in ("unreachable", "blackhole", "prohibit", "local", "broadcast", "multicast") else "unicast",
        "gateway": value("via"),
        "interface": value("dev"),
        "source": value("src"),
        "table": value("table")
    }


def own_route(address):
    """The user's static route (Routes page) that covers the address."""

    try:
        from services import routes
        ip = ipaddress.ip_address(address)
        for route in routes.get_settings()["routes"]:
            network = ipaddress.ip_network(route["destination"], strict=False)
            if route.get("enabled", True) and ip.version == network.version and ip in network:
                return route
    except (ValueError, KeyError, TypeError):
        pass

    return None


def run_route(target, family):

    try:
        address = str(ipaddress.ip_address(target.partition("%")[0]))
    except ValueError:

        code, output = execute(["getent", {"auto": "ahosts", "4": "ahostsv4", "6": "ahostsv6"}[family], target], timeout=8)
        found = output.split()[0] if code == 0 and output else None

        if not found:
            return result(False, "The name could not be found.", raw=output)

        address = found

    code, output = execute(["ip", "route", "get", address], timeout=5)

    if code != 0:
        return result(False, name_problem(output) or f"No way to {address}: {output}", address=address, raw=output)

    data = {**parse_route_get(output), "address": address}

    if data["type"] == "local":
        return result(True, f"{address} is the router itself.", **data, raw=output)

    if data["type"] != "unicast":
        return result(False, f"{address} is {data['type']}: traffic to it is dropped.", **data, raw=output)

    way = f"through {data['interface']}"

    if data["gateway"]:
        way += f" to the gateway {data['gateway']}"
    else:
        way += ", directly (it is on that network)"

    mine = own_route(address)
    reason = f", by your route to {mine['destination']}" if mine else ""

    return result(True, f"Traffic from the router to {address} leaves {way}{reason}.", **data,
                  route=mine["destination"] if mine else None, raw=output)


# ------------------------------------------------------------------
# Speed (iperf3)
# ------------------------------------------------------------------

def clean_direction(value):

    value = str(value or "download")

    if value not in ("download", "upload"):
        raise ToolError("Choose download or upload.")

    return value


def parse_iperf(output):
    """
    iperf3 -J (client or server). The client's JSON has both sides
    (the speed is what the receiving side measured, sum_received); the
    server's only its own: zeros under sum_received when it sent (a
    reverse test), so then its sum_sent counts.
    """

    try:
        data = json.loads(output)
    except (ValueError, TypeError):
        return {"error": (output or "").strip() or "iperf3 gave no result."}

    if not isinstance(data, dict):
        return {"error": "iperf3 gave no result."}

    if data.get("error"):
        return {"error": str(data["error"])}

    end = data.get("end") or {}
    sent = end.get("sum_sent") or {}
    received = end.get("sum_received") or end.get("sum") or {}

    if not received.get("bits_per_second"):
        received = sent
    start = data.get("start") or {}
    connected = (start.get("connected") or [{}])[0]
    test = start.get("test_start") or {}

    if not received.get("bits_per_second"):
        return {"error": "The test ended without a result."}

    seconds = [
        round(i["sum"]["bits_per_second"] / 1e6, 1)
        for i in data.get("intervals") or []
        if isinstance(i, dict) and isinstance(i.get("sum"), dict) and "bits_per_second" in i["sum"]
    ]

    return {
        "error": None,
        "mbits": round(received["bits_per_second"] / 1e6, 1),
        "bytes": max(int(sent.get("bytes") or 0), int(received.get("bytes") or 0)),
        "retransmits": sent.get("retransmits"),
        "seconds": seconds,
        "local": connected.get("local_host"),
        "remote": connected.get("remote_host"),
        "reverse": bool(test.get("reverse")),
    }


def iperf_problem(text, port):

    text = text or ""

    if "busy" in text:
        return "The server is busy with another test. Try again in a minute, or use another server."

    if "Connection refused" in text:
        return f"Nothing answers on port {port} there: no iperf3 server runs (start one with iperf3 -s)."

    if "timed out" in text or "Connection timed out" in text:
        return f"The server did not answer. A firewall may block port {port}, or no iperf3 server runs there."

    if "No route to host" in text:
        return "The router has no way to that address."

    return name_problem(text) or f"iperf3: {text}"


def speed(mbits):

    return f"{mbits:.0f} Mbit/s" if mbits >= 10 else f"{mbits:.1f} Mbit/s"


def megabytes(count):

    return f"{count / 1e6:.0f} MB" if count >= 10e6 else f"{count / 1e6:.1f} MB"


def over_mobile(address):
    """Whether the router reaches the address over the modem (data that counts)."""

    if not address:
        return False

    code, output = execute(["ip", "route", "get", address], timeout=5)

    interface = parse_route_get(output)["interface"] if code == 0 else ""

    return bool(interface) and (interface.startswith("wwan") or interface == "clat")


def run_iperf(target, family, port, direction):

    if not which("iperf3"):
        raise ToolError("iperf3 is not installed. Run the installer again.")

    command = ["iperf3", "-c", target, "-p", str(port), "-t", str(IPERF_SECONDS), "-J",
               "--connect-timeout", "5000", *family_flag(family)]

    if direction == "download":
        command.append("-R")

    code, output = execute(command, timeout=IPERF_SECONDS + 20)

    data = parse_iperf(output)

    if data["error"]:
        return result(False, iperf_problem(data["error"], port), raw=output)

    way = f"Download from {data['remote']}" if direction == "download" else f"Upload to {data['remote']}"

    summary = f"{way}: {speed(data['mbits'])} over {IPERF_SECONDS} s."

    mobile = over_mobile(data["remote"])

    if mobile:
        summary += f" It used about {megabytes(data['bytes'])} of mobile data."

    return result(True, summary, **data, direction=direction, mobile=mobile, raw=output)


# The router as the server: one test from a device on the LAN, then it
# stops. The port is open on the LAN interfaces only while it waits.

_server_lock = threading.Lock()

_server = {"process": None, "until": None, "stopped": False, "last": None}


def server_rules():

    from services import firewall

    return [
        ["allow", "in", "on", lan, "from", "any", "to", "any", "port", str(IPERF_PORT), "comment", SPEEDTEST_COMMENT]
        for lan in firewall.get_lan_interfaces()
    ]


def close_server_port():
    """Also at startup: a port left open by a crash is closed."""

    from services import firewall

    return firewall.sync_rules(SPEEDTEST_OWNER, [])


def lan_addresses():
    """[{"interface", "address"}]: where devices reach the router."""

    from services import firewall

    found = []

    for lan in firewall.get_lan_interfaces():

        code, output = execute(["ip", "-4", "-o", "addr", "show", "dev", lan], timeout=5)

        for match in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+)/", output if code == 0 else ""):
            found.append({"interface": lan, "address": match.group(1)})

    return found


def server_status():

    process = _server["process"]

    return {
        "available": bool(which("iperf3")),
        "running": bool(process and process.poll() is None),
        "until": _server["until"],
        "port": IPERF_PORT,
        "addresses": lan_addresses(),
        "last": _server["last"],
    }


def explain_server(output, timed_out):

    if timed_out:
        return result(False, f"No device started a test within {SERVER_WAIT // 60} minutes.", level="warning")

    data = parse_iperf(output)

    if data["error"]:
        return result(False, f"The test did not finish: {data['error']}", raw=output)

    # Seen from the router: reverse means the router sent (the device's download).
    way = f"Router to {data['remote']} (the device's download)" if data["reverse"] \
        else f"{data['remote']} to the router (the device's upload)"

    return result(True, f"{way}: {speed(data['mbits'])}.", **data, raw=output)


def _wait_server(process):

    timed_out = False

    try:
        output, _ = process.communicate(timeout=SERVER_WAIT)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        timed_out = True

    with _server_lock:

        close_server_port()

        stopped = _server["stopped"]

        _server.update(process=None, until=None, stopped=False)

        if not stopped:
            _server["last"] = explain_server(output, timed_out)


def start_server():

    with _server_lock:

        if _server["process"] and _server["process"].poll() is None:
            return server_status()

        if not which("iperf3"):
            raise ToolError("iperf3 is not installed. Run the installer again.")

        from services import firewall

        ok, message = firewall.sync_rules(SPEEDTEST_OWNER, server_rules())

        if not ok:
            close_server_port()
            raise ToolError(f"The port could not be opened: {message}")

        try:
            process = subprocess.Popen(
                ["iperf3", "-s", "-1", "-p", str(IPERF_PORT), "-J"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
        except OSError as e:
            close_server_port()
            raise ToolError(f"iperf3 did not start: {e}")

        _server.update(process=process, until=time.time() + SERVER_WAIT, stopped=False, last=None)

        threading.Thread(target=_wait_server, args=(process,), daemon=True, name="iperf-server").start()

    return server_status()


def stop_server():

    with _server_lock:

        process = _server["process"]

        if process and process.poll() is None:
            _server["stopped"] = True
            process.terminate()

    # The waiting thread closes the port.
    if process:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    return server_status()


def server_action(data):

    action = (data or {}).get("action") if isinstance(data, dict) else None

    try:

        if action == "start":
            return start_server()

        if action == "stop":
            return stop_server()

    except ToolError as e:
        return {"error": str(e)}

    return {"error": "Unknown action."}


# ------------------------------------------------------------------
# Entry
# ------------------------------------------------------------------

def run_tool(data):
    """Runs one test. The page's result, or {"ok": False, "error": ...}."""

    if not isinstance(data, dict):
        return {"ok": False, "error": "Invalid request."}

    tool = data.get("tool")

    if tool not in TOOLS:
        return {"ok": False, "error": "Unknown tool."}

    try:

        target = clean_target(data.get("target"))
        family = clean_family(data.get("family"))
        interface = clean_interface(data.get("interface")) if tool in ("ping", "traceroute") else ""
        port = clean_port(data.get("port")) if tool in ("port", "iperf") else None
        direction = clean_direction(data.get("direction")) if tool == "iperf" else None

        if not _lock.acquire(blocking=False):
            return {"ok": False, "error": "Another test is still running. Wait for it to finish."}

        try:

            if tool == "ping":
                outcome = run_ping(target, family, interface)
            elif tool == "traceroute":
                outcome = run_traceroute(target, family, interface)
            elif tool == "lookup":
                outcome = run_lookup(target, family)
            elif tool == "port":
                outcome = run_port(target, family, port)
            elif tool == "iperf":
                outcome = run_iperf(target, family, port, direction)
            else:
                outcome = run_route(target, family)

        finally:
            _lock.release()

    except ToolError as e:
        return {"ok": False, "error": str(e)}

    return {**outcome, "tool": tool, "target": target}


def get_info():

    return {
        "interfaces": interfaces(),
        "traceroute": bool(which("traceroute")),
        "iperf": bool(which("iperf3")),
        "iperf_seconds": IPERF_SECONDS,
    }
