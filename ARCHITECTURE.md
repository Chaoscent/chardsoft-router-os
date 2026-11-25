# Chardsoft Router OS — Architecture

> [!abstract] Purpose
> The architecture of Chardsoft Router OS: what it is made of, where things live, and the rules the code follows.
>
> When development moves to another chat or another person, start with this document, then README.md (usage) and PLAN.md (what is done and what is next).
>
> Details evolve; the decisions marked as **invariants** at the end should not change casually. Where something is planned but not built yet, this document says so.

---

# Project Overview

**Project:** Chardsoft Router OS

**Target:** Raspberry Pi 5 with Raspberry Pi OS Lite (64-bit), a cellular modem (tested: Quectel RM520N-GL) and optional extra Wi-Fi adapters.

Chardsoft Router OS is not an operating system of its own: it installs **on top of Raspberry Pi OS** and turns the Pi into a router managed from a web dashboard.

- Mobile data through the modem (or Ethernet as the WAN)
- Ethernet LAN, LAN bridge
- Wi-Fi access points, one network per radio
- DHCP and DNS
- Firewall, routing and NAT
- VPN: WireGuard and OpenVPN servers, WireGuard and OpenVPN client profiles
- Web dashboard over HTTPS, with a setup wizard
- Safe Apply: changes are verified and reverted when they cut you off
- Backup, restore, factory reset, uninstaller
- Optional apps in Docker (Apps Addon)

One command turns a fresh Raspberry Pi OS into a router:

```bash
curl -fsSL https://chardsoft.com/router-os/core | sudo bash
```

---

# Layers

```text
┌─────────────────────────────────────────────┐
│ Browser: dashboard (HTML, CSS, JavaScript)  │
├─────────────────────────────────────────────┤
│ Caddy: HTTPS, app names                     │
├─────────────────────────────────────────────┤
│ Chardsoft Router OS app (Flask, gunicorn)       │
├─────────────────────────────────────────────┤
│ Settings: defaults, persistent, running     │
├─────────────────────────────────────────────┤
│ System services: NetworkManager, hostapd,   │
│ dnsmasq, ufw, WireGuard, OpenVPN,           │
│ ModemManager, Avahi                         │
├─────────────────────────────────────────────┤
│ Raspberry Pi OS (Debian)                    │
├─────────────────────────────────────────────┤
│ Raspberry Pi 5, modem, Wi-Fi adapters       │
└─────────────────────────────────────────────┘
```

The app never re-implements a network service. It writes the services' own configuration (hostapd, dnsmasq, NetworkManager connections, ufw rules, WireGuard and OpenVPN configs), starts them through systemd and checks that they stay up.

Optional apps are a separate layer that Core never depends on:

```text
Chardsoft Router OS Core
        │ optional
        ▼
   Apps Addon ── Docker ──┬── App
                          ├── App
                          └── App
```

---

# Filesystem Layout

| Location | Purpose | Written by |
|---|---|---|
| `/opt/chardsoft-router-os/` | The app: a git clone with its Python environment (`.venv`) | Installer (git) |
| `/etc/chardsoft-router-os/` | Default settings (JSON) and the record of installed packages | Installer only, never the app |
| `/var/lib/chardsoft-router-os/` | Persistent settings: the last applied and verified state | App |
| `/tmp/chardsoft-router-os/` | Running settings, pending changes; gone after a reboot | App |

**Renamed from "chaos" (2026-11-25).** Installs from before used `chaos` names (`/opt|/etc|/var/lib/chaos-router-os`, the services `chaos-router-os`, `chaos-router-mdns`, `chaos-hostapd@`, `chaos-clat`, `openvpn-server@chaos`, `/etc/hostapd/chaos-*`, `/etc/dnsmasq.d/chaos-router-*`, `/etc/caddy/chaos-apps`, the `CHAOS-*` firewall chains, the NetworkManager connections `chaos-lan*` and `chaos-modem`). `install.sh` moves such an install once (`migrate_names`, before anything else): the old services are stopped and their units removed, defaults and saved settings and the code move (the Python environment is built again), the running settings are dropped so the app applies everything under the new names as after a boot, old config files are removed (the app writes them anew), the OpenVPN keys and the Caddy app routes keep their contents under the new names, the old chains are unhooked and deleted, the NetworkManager connections are renamed in place (no reconnect). Tested in `tests/test_migrate_names.py`. The updater's rollback cannot undo it.

## App: `/opt/chardsoft-router-os/`

The installer clones the repository here (or uses the clone it is run from) and keeps it updated with git. It holds code only:

```text
backend/       Flask app, services/ (one module per area), installer helpers
frontend/      templates/ (pages), static/ (CSS, JavaScript, vendor files)
deploy/        systemd units, Caddyfile, service installer
install.sh     Core installer
uninstall.sh   Uninstaller
VERSION        e.g. 0.1.0-alpha
```

Code is **not** in `/etc`: by the Filesystem Hierarchy Standard, `/etc` is configuration only, and code there would mix with settings that must survive updates. `/opt` is where software installed outside the package manager belongs. Packaged as a `.deb` some day, it would move to `/usr/lib/chardsoft-router-os`. The installer option `--dir PATH` chooses another folder.

The sidebar shows the version and, in a git clone, the commit: `v0.1.0-alpha (8464d20)`.

## Defaults: `/etc/chardsoft-router-os/`

Written by `backend/install_defaults.py` during install, never by the app. Existing files are kept on updates, so local edits survive. Examples: the firewall (on, nothing comes in from the internet, the LAN keeps working), NAT, the Wi-Fi country chosen during install. `installed-packages` lists the packages the installer added, so the uninstaller removes exactly those.

## Persistent: `/var/lib/chardsoft-router-os/`

One JSON file per settings area (`wifi.json`, `dhcp.json`, `network.json`, ...), plus `users.json` (the admin account) and `system/` (records of what is actually on the system: firewall rules, the OpenVPN CA backup, the event log, the session key). Secrets are mode 600.

This is the **last known-good** configuration. It survives reboots and updates, and the router boots with it. Deleting an area's file restores that area's defaults.

## Running: `/tmp/chardsoft-router-os/`

The configuration the system runs right now, one JSON file per area, plus `pending.json` (changes waiting for confirmation) and small records (e.g. which addresses the access points set). Rebuilt from `/var/lib` at every boot.

## Service configs

The system services keep reading their own files; the app generates them from the JSON settings:

| Service | File(s) |
|---|---|
| hostapd (one per radio) | `/etc/hostapd/chardsoft-<radio>.conf`, unit `chardsoft-hostapd@<radio>` |
| dnsmasq | `/etc/dnsmasq.d/chardsoft-router-*.conf` |
| NetworkManager | connections `chardsoft-lan` (bridge), `chardsoft-lan-<port>`, `chardsoft-modem` |
| WireGuard / OpenVPN | `/etc/wireguard/`, `/etc/openvpn/` |
| Caddy | `/etc/caddy/Caddyfile`, `/etc/caddy/chardsoft-apps/*.caddy` |
| Firewall | ufw rules, recorded in `system/firewall_rules.json` |

---

# Safe Apply: How Settings Change

Every settings area (network, firewall, routing, Wi-Fi, DHCP, DNS, mobile data, VPN servers and profiles, blocked devices) is registered in `backend/services/areas.py` with an apply, a verify and a baseline function, and changes only through `backend/services/transaction.py`.

```text
Page sends a change
        ↓
Backend validation (never trusts the page's own checks)
        ↓
Running config  (/tmp)
        ↓
Apply to the system (write configs, restart services)
        ↓
Verify (services still up after a moment, addresses set)
        ↓
   ┌────┴─────────────────────────────┐
   │ failed                           │ verified
   ▼                                  ▼
Persistent config is restored    Can it cut the user off?
and applied again                (network, firewall, Wi-Fi,
                                  DHCP, DNS, VPN servers,
                                  blocking a device)
                                   │ no            │ yes
                                   ▼               ▼
                              Persistent      Pending: banner on every
                              (/var/lib)      page, "Keep" or "Revert"
                                                   │
                                   ┌───────────────┴──────────────┐
                                   ▼                              ▼
                             Keep: persistent        No answer in 120 s, Revert,
                                                     or a reboot: back to the
                                                     persistent config
```

- **Dependents:** an area that others read (e.g. the network: which interface is the WAN, which ports are bridged) re-applies them after a change.
- **Several areas can be pending at once** and are kept or reverted together. Turning on a Wi-Fi network also makes DHCP serve that radio; both wait for one confirmation.
- **Before the first change** to an area, what the system runs is recorded as its persistent config (the baseline), so there is always something to go back to.
- **Validation happens twice:** the page checks input for immediate feedback; the backend validates everything again and is the only authority.

## Brick Recovery

A bad network change must never permanently lock the user out:

```text
Change cuts off the dashboard
        ↓
Nobody confirms → reverted after 120 seconds
        or
Power cycle
        ↓
Boot: the kernel's boot ID differs from the last start
        ↓
Running config is deleted and rebuilt from /var/lib
        ↓
Every configured area is applied in order
        ↓
Router reachable on the last known-good config
```

An app restart (not a reboot) keeps running changes but reverts unconfirmed ones (`recover_pending`). Temporary configuration never becomes authoritative by existing.

## Settings Format Changes

`/var/lib/chardsoft-router-os/schema.json` holds the format version of the settings files (`backend/services/schema.py`); an install without it is version 1, unless it has no settings at all (a fresh install, stamped current). Format 2 adds the dashboard's "Internet" and "Mobile data" widgets to a saved layout. On every start, before the boot sync, files from an older version go through the update steps in `MIGRATIONS`, in both the persistent and the running layer, and the new version is saved. Each step works on any subset of files and is safe to run twice. Settings from a newer version (after a downgrade) are left alone.

Backups record the format as `schema`. A restore runs the same steps on an older backup before validating it, and refuses one from a newer Router OS.

Small changes can still be read on the fly instead: `wifi.json` and `dhcp.json` used to describe a single network or scope and are read as the settings of that one interface; the converted form is saved with the next change.

---

# Network

## Interfaces

| Interface | Role |
|---|---|
| `wwan0` | The modem's data interface; WAN by default. Managed by ModemManager and NetworkManager, read-only on the Network page. |
| `eth0` | LAN by default; can be switched to WAN (Ethernet uplink instead of the modem). DHCP client or static. |
| `wlan0`, `wlan1`, ... | Wi-Fi radios: built-in and USB adapters. Each can run an access point. |
| `br0` | Optional LAN bridge. |

## Wi-Fi

- **One access point per radio**, each its own systemd unit `chardsoft-hostapd@<radio>` with its own name, band, channel and subnet (10.42.0.1/24, 10.43.0.1/24, ...).
- **Saving one radio leaves the others running:** an access point only restarts when its config changed.
- **One band per radio:** a radio sends on one channel at a time, so 2.4 and 5 GHz at once need two radios.
- **Bands and channels:** the page offers only what `iw` reports for the radio (e.g. no 6 GHz on the Pi's built-in Wi-Fi).
- **Country:** one for all radios, set during install (no default) and changeable on the Wi-Fi page.
- **DHCP follows:** turning on an access point makes DHCP serve its radio in its subnet.

## DHCP and DNS (dnsmasq)

- **DHCP:** one scope per interface, each with its own range, gateway, DNS and lease time. The local domain and the static leases are shared. Bridge ports are served through `br0`. A scope on eth0 pauses while eth0 is the WAN.
- **DNS:** answered only on the LAN interfaces, never the WAN.
- **Local names:** `chardsoft-router.local` and `<app>.chardsoft-router.local` for every device on the LAN.
- **The router's own name lookups** do not go through dnsmasq: the installer keeps the system's resolver, so switching DNS off on the dashboard never breaks the router's own lookups.
- **Upstream servers:** by default ("Modem / ISP", also before the DNS page is first saved) dnsmasq asks the router's own servers: the installer adds `/etc/dnsmasq.d/chardsoft-router-upstream.conf` with `resolv-file=/etc/resolv.conf`, because Debian's `-r /run/dnsmasq/resolv.conf` is only written by the resolvconf package and never exists with openresolv (Raspberry Pi OS). A preset or custom servers replace them (`no-resolv`). The internet check asks dnsmasq on 127.0.0.1 as well as the system resolver, so a DNS server that only fails for the devices shows.

## LAN Bridge

Off by default. When on, the chosen ports (Ethernet and Wi-Fi, all by default) share one network on `br0`; the others keep their own. Wired ports are NetworkManager bridge ports; access points join with hostapd's `bridge=`.

## Modem and Mobile Data

- **ModemManager (`mmcli`)** reads the modem: carrier, network type, signal, SIM state.
- **Mobile data** is the NetworkManager connection `chardsoft-modem`: APN (automatic from the carrier database `mobile-broadband-provider-info`, falling back to the APN the network gave the modem, or entered by hand), roaming, optional username and password. It reconnects by itself.
- **SIM PIN:** sent once per click, never retried automatically, so it can't run the SIM into the PUK lock. It is saved for unlocking at startup only after it has worked.
- **USB data mode:** Quectel modems (e.g. RM520N-GL) in MBIM mode lose every received packet on Linux (`cdc_mbim` counts them as RX errors): the connection is up, but nothing comes back. The installer switches them to QMI (`deploy/modem-usb-mode.sh`, `AT+QCFG="usbnet",0`). The Modem page shows the data mode and the addresses, and offers the switch when received data is being dropped.
- **APN order when the field is empty:** NetworkManager's carrier database (can be outdated), then the APN the network gave the modem (never `IMS`, which is for calls), then the empty APN (the network's default for the SIM). NetworkManager retries a failing connection a few times, then pauses.
- **IPv6-only plans (CLAT)** (`services/clat.py`): many carriers hand out IPv6 only (Telekom's `internet.v6.telekom`, also what the network picks with an empty APN). While the WAN has IPv6 but no real IPv4 (the modem's placeholder `192.0.0.x` does not count), the router runs the device half of 464XLAT for the LAN: LAN IPv4 is NAT-ed (`CHARDSOFT-CLAT`) to `192.0.0.4` on the `clat` interface, tayga (`chardsoft-clat.service`, config `/etc/tayga/chardsoft-clat.conf`) translates it to `<WAN /64>::c1a7` and the carrier's NAT64 prefix, found with DNS64 (RFC 7050, `ipv4only.arpa`) or else `64:ff9b::/96`. The LAN routing table sends IPv4 into `clat`; the router's own IPv4 uses it too (metric 2000, any real IPv4 route wins). The watcher follows the modem's /64, which changes with every connection. On an Ethernet WAN the two addresses are answered with proxy NDP. Switch: "IPv4 over IPv6 (CLAT)" on the Firewall page (Automatic / Off).
- **LAN routing** (`services/wan_routing.py`): LAN traffic has a routing table of its own (4242, rules at preference 9000/9001), so it always leaves through the WAN even when eth0 or a Wi-Fi radio also has a DHCP default route with a better metric (a home network during testing). Specific routes of the main table still apply (`suppress_prefixlength 0`); the router's own traffic is not affected. With the WAN down the table answers "unreachable" at once. A 10 s watcher follows the WAN's gateway; while a full-tunnel VPN profile is engaged the rules step aside for the tunnel's routes.
- **Static routes** (`services/routes.py`, Routes page, area `static_routes`, confirmed like the firewall): added to the main table with `proto 77`, so the router lists and removes exactly its own (`ip route show proto 77`) and never touches other routes. No default routes (the WAN is chosen on the Network page). Specific routes of the main table also apply to LAN devices through rule 9000 of the LAN routing. A route whose gateway or interface is missing does not fail the change: it shows as "Waiting" with the kernel's reason in plain words, and the 10 s LAN routing watcher adds it once possible, also after the kernel dropped it with its interface. IPv6 metric 0 is kept as 1024, like the kernel does. The page also shows every routing table except `local` (link-local and multicast IPv6 routes hidden) and the rules, with notes on the router's own.
- **Internet time** (`services/internet_time.py`, Clients page, area `internet_time`, not confirmed because it never touches the dashboard): pauses (until a time, until 07:00, until resumed) and weekly offline times per MAC. Paused devices are rejected in the `CHARDSOFT-PAUSE` chain, first in filter/FORWARD for IPv4 and IPv6, so they keep DHCP, DNS and the dashboard; the CLAT and VPN client tunnels are forwarded and covered too. An offline time that ends before it starts belongs to the day it starts and runs past midnight. A 15 s watcher rebuilds the chain whenever the set of paused devices changes (or the hook went missing) and logs each device going offline and online. "Resume" during an offline time sets `allow_until` to its end. Before the clock is set (earlier than 2025) offline times and timed pauses are skipped; "until resumed" still applies.
- **Data per device** (`services/device_usage.py`): each MAC seen on the LAN gets a slot 1-255, stored with the counts. `CHARDSOFT-ACCT-MARK` (mangle/PREROUTING) tags each device's connections with its slot in connmark bits 16-23 (`--set-xmark slot<<16/0x00ff0000`); `CHARDSOFT-ACCT` (first in filter/FORWARD) counts every packet of a tagged connection, both ways, for IPv4 and IPv6, skipping LAN-to-LAN pairs. The CLAT's translated packets are new connections without a tag, so IPv4 over an IPv6-only plan counts once. Every 30 s the counters are read and added per billing period (the data limit's billing day; a lower reading means the chains were rebuilt) to `device_usage_counter.json`. New devices rebuild the chains; a full table drops the device that used the least. The dashboard shows the top three and "the router itself and overhead" (mobile total minus all devices).
- **Notifications** (`services/notifications.py`, bell in the header): a view of the event log, not a store of its own: every warning and error, plus info events logged with `log_event(..., notify=True)` (watchdog modem restart, new billing period, new device on the LAN after the first look, a static route that works again after it stopped). Read state is one timestamp (`system/notifications.json`); opening the list marks everything up to its newest entry as read. The unread count rides on `/api/header` (every second), cached on the event log's mtime and the read timestamp.
- **USB Wi-Fi hotplug** (`services/wifi_hotplug.py`): a watcher (5 s) compares the radios (`/sys/class/net/*/wireless`) with the last look. A radio that appears with an enabled saved network gets the Wi-Fi and DHCP settings applied (`apply_wifi_settings`, `apply_dhcp_settings`; under the transaction lock and never while a Safe Apply change is pending, then retried), is checked to still run after 4 s, and is announced (notification). A new radio without a network is announced once per radio MAC (`wifi_adapters_seen`) with its driver, bands (`iw phy`) and whether it has access point mode. A radio that goes away gets `stop_access_point`, DHCP and the guest rules re-applied. The first look also starts a saved network whose adapter came after the start-up apply. Names follow the plug order, so `wifi.RADIOS` (`wifi_radios.json`) keeps the radio MAC (`phy80211/macaddress`) each network last ran on: `apply_wifi_settings` and `verify_wifi_settings` leave out a network whose name now belongs to another adapter, and saving a network on the Wi-Fi page (`save_wifi_settings`) forgets the old adapter so it runs on the new one. Each look first syncs the firewall's essential rules (`firewall.sync_essentials`: DHCP, DNS, dashboard and SSH per LAN interface) when the set of LAN interfaces changed, USB Ethernet included, before a network starts; they are built from the interfaces that exist at apply time, so a later adapter would otherwise get its network but no addresses.
- **Guest Wi-Fi** (`services/wifi.py` "guest" per network, `services/guest.py`): a second BSS in the radio's hostapd config (`bss=wlan0-guest`, name cut to 15 characters, its own locally administered BSSID, `ap_isolate=1`), only on radios whose `iw phy` interface combinations allow two access points (unknown: allowed). The interface is created by hostapd; the router then gives it its own subnet (suggested free 10.x.0.1/24), DHCP follows it like a radio, and its name starting with "wlan" makes it a LAN interface for DNS, the essential firewall rules and LAN routing. It is never listed as a radio. Isolation does not depend on ufw's policies: `CHARDSOFT-GUEST-IN` (first in INPUT) passes only DHCP/DHCPv6, DNS and ICMP to the firewall and rejects the rest; `CHARDSOFT-GUEST-FWD` (first in FORWARD) rejects guests to private, CGNAT, link-local and ULA addresses and any new connection into a guest network. Rebuilt with every Wi-Fi apply and checked every 30 s (a ufw reload drops the hooks).
- **GNSS** (`services/gnss.py`, area `gnss`, Modem page, dashboard widget "gnss"): ModemManager's `gps-nmea` and `3gpp` location sources (`mmcli --location-enable-*`, refresh rate 5 s), read as JSON (`--location-status -J`, `--location-get -J`). When ModemManager refuses `gps-nmea` (the RM520N over QMI: "couldn't setup required NMEA traces"), the receiver is started as `gps-unmanaged` (up to 4 tries 3 s apart: just after a stop the modem answers "Serial command timed out") and a reader thread takes the sentences straight from the modem's NMEA port (the port mmcli lists as "(gps)", e.g. `/dev/ttyUSB1`; the service has `SupplementaryGroups=dialout`); the last 3 s of sentences make one reading. The NMEA sentences are parsed by the router (checksums; GGA, RMC, GSA, GSV per constellation), so the page can tell "no satellites heard: antenna?" from "searching" from a fix with its accuracy (about HDOP x 5 m). A fix counts as current while its NMEA time keeps moving (30 s). No assistance data is downloaded (no third-party requests). A watcher (5 s while on) switches the receiver back on after a modem restart. The clock: with `set_clock`, a fresh fix from 2025 or later sets the system clock (`date -u -s @<seconds>`, the only form the root helper allows) when it is more than 5 s off and `timedatectl` says NTP has not synchronised it; at most every 10 minutes, logged as a notification.
- **GNSS map** (`frontend/static/js/map.js`, `services/maps.py`, area `maps`): MapLibre GL JS 5, pmtiles and the Protomaps style are vendored (`frontend/static/vendor/map`, with fonts, icons and the Natural Earth countries), so the page draws a world map with no third-party request, loaded only when the card scrolls into view. A detailed map is one country from Protomaps' daily OpenStreetMap build: on request the router fetches the pinned `pmtiles` tool (SHA-256 checked) into `STATE_DIR/maps/bin` and runs `pmtiles extract <source>/<build> region.part --region=<country outline> --maxzoom=12|14|15` (range requests, only that country), renamed to `region.pmtiles` when done; a failed or stopped download keeps the previous map. The page reads it from `/maps/region.pmtiles` (login, range requests). The source is a setting for mirrors. The track (`gnss_track.json`, off by default, 1/7/30 days, a point per 25 m or 5 min, at most 20,000) is deleted when switched off.
- **Tools page** (`services/net_tools.py`, only reads): ping, traceroute (`traceroute -n -q 1`, package from the installer), name lookup through the router's own resolver (`getent`, so the DNS page's blocklist shows), a TCP port check and `ip route get`, which names the user's static route when one covers the address. Targets must be an IP address or a host name (never an option), commands run without a shell and with timeouts, and one test runs at a time. `#/tools?tool=route&target=...` opens a test directly (the Routes page's "Test"). **Speed (iperf3):** `iperf3 -c <target> -p <port> -t 10 -J` (`-R` for download), the speed from the receiving side; over the modem (`ip route get` says wwan/clat) the summary says how much mobile data it used. **Speed test from a device:** `iperf3 -s -1 -p 5201 -J` waits 5 minutes for one test; while it waits, `firewall.sync_rules("speedtest", ...)` allows 5201 on the LAN interfaces only (comment "Chardsoft Router OS: Speed test", never imported as a user rule), closed when it ends, is stopped, or at the next start-up after a crash. The server's JSON has only its own side (zeros under `sum_received` when it sent), so its `sum_sent` counts then. The installer preseeds iperf3's debconf question so it never runs as a daemon.
- **Modem watchdog** (`services/modem_watchdog.py`): a modem can stay "connected" while every received packet is rejected by the USB driver, or while nothing comes back at all. After about 45 s of that the modem is reset (`mmcli -r`), at most every 5 minutes and 3 times an hour; each reset is logged.
- **Mobile data counter and data limit** (`services/data_usage.py`): the modem's interface counters (`wwan*`, download and upload) are added up every 30 s per billing period (Modem page: data per period, the day it starts over, warn at, "Only warn" or "Stop internet for devices"). The count lives in `/var/lib/chardsoft-router-os/data_usage_counter.json`, written every 5 minutes and on a clean stop; a counter that went down (modem restart) counts again from zero. The warning and the limit are logged once per period. "Stop internet for devices" is the `CHARDSOFT-QUOTA` chain in filter/FORWARD, rejecting everything forwarded out of `wwan+` (the CLAT's traffic too); the router and the dashboard stay reachable. "Resume anyway" lifts it until the next period. The count can be corrected to the carrier's ("Correct count" stores an offset).
- **Dashboard edit mode** (`static/js/dashboard.js`, saved as the "dashboard" area's `layout` through `/api/dashboard/layout`, format unchanged): "Edit dashboard" works on a copy of the layout (hidden built-in widgets included) shown on the page itself. A widget's handle drags it (pointer events with capture, so mouse and touch alike; the widget lifts after 5 px; its place stays open and moves through the grid, a copy follows the pointer, the others slide over with a FLIP animation; near the edges `main` scrolls), its right edge sets the width, snapped to the widths of its type, measured from where the drag started (only above 1100 px, where the 12 columns show). Arrow keys on the handle move it, on the edge change the width; a live region says the new place or width. The content is `inert` while editing. Hidden widgets and "Traffic graph" sit in a tray; "List view" edits the same copy as rows (Apply or Cancel); Done saves, Cancel drops the copy, Escape only ends a drag or the list view.
- **Internet check** (`services/internet_check.py`, dashboard widget "Internet"): every 30 s, or by hand, it checks the uplink in steps (modem and SIM or the Ethernet WAN's address, forwarding and NAT, the data limit, a ping over IPv4 and IPv6, a DNS lookup, the CLAT on IPv6-only plans, the LAN routing table). The first failing step is the headline with its fix: switch on mobile data, restart the modem (`mmcli -r`), repair (re-applies routing, CLAT and LAN routing), resume over the data limit, or a link to the page where it is fixed.
- **IPv6 for the LAN** (`services/ipv6.py`, switched in the routing settings): mobile networks rarely delegate a prefix, so the router picks a unique local /48 once (`ula_prefix`) and gives each LAN that DHCP serves a /64 of it (subnet ID from the interface name, so it stays put). dnsmasq announces it (`enable-ra`, `ra-stateless`), NAT66 (`CHARDSOFT-NAT6`) shares the WAN's address. Forwarding on would make the kernel drop router advertisements on a WAN where it handles them itself, so such a WAN is set to `accept_ra=2` first. The addresses are set with `ip` and re-added by a 15 s watcher. While a full-tunnel VPN profile is engaged, the kill switch chain rejects LAN IPv6 to the WAN (the tunnel carries IPv4 only), so nothing bypasses it.

## Firewall, Routing, NAT

- **ufw** with default policies: incoming and routed traffic denied, outgoing allowed.
- **Essential rules:** SSH, the dashboard, DHCP, DHCPv6 and DNS are allowed on the LAN interfaces only.
- **Routing:** IP forwarding and masquerading to the WAN.
- **On by default:** shipped in `/etc/chardsoft-router-os` and applied from the first boot.

---

# Web Access

```text
Browser
   ↓
http(s)://chardsoft-router.local
   ↓
Caddy (ports 80, 443)
   ↓
Chardsoft Router OS app (127.0.0.1:5000, gunicorn)
```

- **HTTPS** uses Caddy's own local certificate authority (`tls internal`). A certificate is issued on the first visit, but only for the router's own LAN addresses and names: Caddy asks the app first (`/caddy/tls-allowed`).
- **Certificate warning:** browsers warn once per device because they don't know the local CA. The System page offers the CA certificate (`/router-ca.crt`) to install. A warning-free certificate would need the user's own domain and a public CA. That is not built; it would be an opt-in feature.
- **HTTP → HTTPS:** after setup, the app redirects HTTP to HTTPS. Before setup it stays plain HTTP, because the setup Wi-Fi's captive portal and phones' connectivity checks need it.
- **`.local` names:**
  - LAN devices get them from the router's DNS (dnsmasq).
  - Devices that use another DNS server get them over mDNS: the service `chardsoft-router-mdns` announces them with Avahi.

---

# Setup and First Start

```text
Fresh Raspberry Pi OS Lite
        ↓
curl installer
        ↓
App starts, no admin account → setup mode
        ↓
Setup Wi-Fi "Chardsoft-Setup-XXXX" (WPA2, 2.4 GHz, 10.42.0.1/24)
on the first radio that can do 2.4 GHz (preferably the built-in wlan0),
with a captive portal; the installer shows its name, password and QR code
        ↓
Phone joins → setup page opens by itself
        ↓
Admin account, optionally the first Wi-Fi network, a look at the
internet connection (modem or Ethernet; only reads, never blocks)
        ↓
Setup Wi-Fi replaced by the real one (or turned off)
        ↓
Dashboard: https://chardsoft-router.local
```

There is no default login: without an account the router is in setup mode, and the setup page only works until an account exists.

---

# Installer

## Commands

```bash
curl -fsSL https://chardsoft.com/router-os/core | sudo bash
```

The URL is a redirect on chardsoft.com to the installer in the repository (today: branch `dev/core`).

## Philosophy

The installer is a **bootstrapper**, not the app. It gets the code with git and never lists app files, so new files in the repository arrive without changing the installer.

## Steps

1. **Check the system:**
   - It must run with sudo, from the user account the app should run as.
   - It checks for Raspberry Pi OS, 64-bit and a Raspberry Pi; anything else is warned about, not refused.
2. **Packages:**
   - It installs these and records which ones were new:
     - git, python3-venv, NetworkManager
     - dnsmasq, hostapd, iw, rfkill
     - ModemManager, mobile-broadband-provider-info
     - ufw, iptables
     - wireguard-tools, openvpn, easy-rsa, openresolv
     - qrencode, Caddy, Avahi
   - A modem plugged in before ModemManager came is looked for again, so no reboot is needed.
3. **Name resolution:** dnsmasq must not become the router's own resolver; the installer checks that name lookups still work.
4. **Code:**
   - It clones or updates the repository in `/opt/chardsoft-router-os`, owned by the app user.
   - It creates the Python environment.
5. **Defaults:** it writes `/etc/chardsoft-router-os` and leaves existing files alone.
6. **Wi-Fi country:**
   - It lists every country with its code; the user types theirs, with no default.
   - `--country XX` skips the question.
7. **sudoers:**
   - The app runs as a normal user and uses `sudo -n` for a fixed list of commands only (systemctl, nmcli, mmcli, ip, ufw, ...).
   - Paths are resolved with `type -P`.
8. **Ports:** it warns when something else uses 53, 67, 80 or 443.
9. **Modem mode:** a Quectel modem in MBIM mode is switched to QMI (`CHARDSOFT_MODEM_MODE=keep` skips it).
10. **Services:**
   - `deploy/install-service.sh` installs `chardsoft-router-os` (the app), `chardsoft-router-mdns` and the `chardsoft-hostapd@` template.
   - It installs the Caddyfile and keeps the previous one as `Caddyfile.before-chardsoft`.
11. **Finish:** on a router that is not set up yet, it shows the setup Wi-Fi with its QR code.

## Running It Again

Safe to run any number of times: it updates the code, repairs the infrastructure and restarts the services, and **keeps all settings**. This is also how updates work today.

## Uninstaller

```bash
sudo /opt/chardsoft-router-os/uninstall.sh
```

**What it removes:**
- The Apps Addon with all apps (and Docker, if the addon installed it).
- Every system change Chardsoft Router OS made: Wi-Fi, DHCP, DNS, VPNs, mobile data, firewall rules, NAT, the LAN bridge, Caddy routes and the `.local` names.
- The packages the installer installed, and all of its files.

**What it keeps:** eth0's settings (so SSH stays), the hostname, the Wi-Fi country and the system journal.

**How it runs:** it continues as its own process, so a dropped SSH connection doesn't stop it halfway. `--dry-run` shows what it would do.

---

# Services

```text
systemd
   ├── chardsoft-router-os          The app (gunicorn on 127.0.0.1:5000, as the app user)
   ├── chardsoft-router-mdns        .local names over mDNS
   ├── chardsoft-hostapd@<radio>    One access point per radio
   ├── caddy                    HTTPS, ports 80 and 443
   ├── dnsmasq                  DHCP and DNS for the LAN
   ├── NetworkManager           Interfaces, bridge, mobile data
   ├── ModemManager             The modem
   ├── ufw                      Firewall
   ├── wg-quick@, openvpn@      VPN servers and client profiles
   └── docker                   Only with the Apps Addon
```

## Boot

```text
Raspberry Pi boot
        ↓
NetworkManager, ModemManager, dnsmasq, Caddy, ...
        ↓
chardsoft-router-os starts
        ↓
New boot ID → running config rebuilt from /var/lib
        ↓
Areas applied in order: network, mobile data, firewall, routing,
blocked devices, DHCP, DNS, Wi-Fi, VPN
        ↓
Not set up yet → setup Wi-Fi with captive portal
        ↓
App names (DNS, mDNS, Caddy routes) synced
```

---

# Editions and Branches

Chardsoft Router OS has two editions:

- **Core:** the main edition for any Raspberry Pi 5 with a cellular modem. Networking first.
- **Hardware Edition:** Core plus support for physical switches and OLED displays, made for the author's own Pi 5 router design, **Chardsoft Router**. Not installable from Core.

```bash
curl -fsSL https://chardsoft.com/router-os/core | sudo bash
curl -fsSL https://chardsoft.com/router-os/hardware-edition | sudo bash
```

The Hardware Edition has its own version and states which Core version it is based on:

```text
Chardsoft Router OS Core
    └── Core v2

Chardsoft Router OS Hardware Edition
    └── Hardware Edition v1
        └── based on Core v2
```

**Branches** in the repository `chaoscent/chardsoft-router-os`:

| Branch | Content |
|---|---|
| `dev/core` | Core development; what the installer installs until v1 |
| `dev/hardware-edition` | Hardware Edition development |
| `dev/apps` | The Apps Addon |
| `release/core`, `release/hardware-edition`, `release/apps` | Empty until the v1 release; from then on, only the newest full release |

At v1, `VERSION` becomes `1.0.0`, the release branches are filled and the installers, the redirect on chardsoft.com and GitHub's default branch switch to `release/*` (see PLAN.md).

**Current version:** `0.1.0-alpha`.

---

# Apps Addon

## Optional

Neither Core nor the Hardware Edition ships with the Apps Addon. The Apps page in the sidebar offers to install it with one click. Core never depends on it; the Apps Addon depends on Core.

## What It Is

The addon lives on the branch `dev/apps` and installs into `/opt/chardsoft-router-apps` (owned by root):

- **Docker Engine and Docker Compose** from Docker's own repository.
- **Router-friendly Docker settings:**
  - `ip-forward-no-drop`: Docker doesn't block forwarding.
  - Its own address pool, 172.31.0.0/16.
- **The App Manager** `chardsoft-apps` (`/usr/local/bin/chardsoft-apps`). It is the only thing the app may run for apps (one sudoers rule), and only for apps in the catalog.

## Catalog

The catalog is **git-based**: no API, just one folder per app in the addon's repository with a Docker Compose definition and metadata (name, description, version, port, download size). The App Manager reads it, and the dashboard shows it.

Today's official catalog has fixed versions of Uptime Kuma, Vaultwarden, Jellyfin and Nextcloud. The Chardsoft Router OS project doesn't keep updating these versions.

Planned: community-maintained app definitions and additional catalogs. Chardsoft Router OS maintains the platform and the integration; app maintainers maintain their apps.

## How an App Fits In

Example: the user installs Nextcloud.

```text
Apps page → Install
        ↓
chardsoft-apps install nextcloud (Docker Compose project chardsoft-nextcloud)
        ↓
Port published on 127.0.0.1 only (never on the LAN or WAN)
        ↓
Chardsoft Router OS adds:
  - dnsmasq:  nextcloud.chardsoft-router.local → the router
  - Avahi:    the same name over mDNS
  - Caddy:    nextcloud.chardsoft-router.local → 127.0.0.1:<port>, HTTPS
        ↓
https://nextcloud.chardsoft-router.local
```

- **No extra ports:** apps open no ports beyond 80 and 443. Docker's published ports would bypass ufw, so they only go to 127.0.0.1, and Caddy is the only way in.
- **HTTPS:** like the dashboard, with Caddy's internal TLS. HTTP redirects to HTTPS.

## Lifecycle and Versions

- **Lifecycle:** install, open, start, stop, restart, update, logs, remove (keeping or deleting the data). Every action runs as a job with a live log on the Apps page.
- **Versions:**
  - The addon has its own version (shown on the Apps page), independent of Core.
  - Each app has its own version from the catalog.
  - Updating an app never requires updating Core.

## Isolation

A crashing app must not affect:
- routing, DHCP, DNS, Wi-Fi, VPN or the modem;
- the dashboard.

Apps run in containers with their own networks, separate from the router's services.

Planned: building and maintaining own app containers.

---

# Factory Reset

A factory reset returns the router to a freshly installed state without reinstalling Raspberry Pi OS. It is different from an update, which keeps the settings.

**Safety:** it asks for the admin password (System page) and says what it removes.

**Steps:**
1. Every area is set back to its defaults and applied:
   - Wi-Fi, DHCP, DNS, mobile data and the VPNs off.
   - Own firewall rules removed, LAN bridge removed.
   - **eth0 keeps its connection**, so the router stays reachable.
2. Generated secrets are deleted: OpenVPN certificates, WireGuard and hostapd configs, VPN client profiles.
3. `/var/lib/chardsoft-router-os` and `/tmp/chardsoft-router-os` are emptied. No stale configuration survives.
4. The router reboots into setup mode. The defaults from `/etc` (firewall, NAT) apply, and the setup Wi-Fi starts.

**The app itself stays installed**, and so do the **Apps Addon, Docker and the apps**. Their names and routes are set up again at the next start.

## Reset Levels

| Level | Resets | Keeps | Status |
|---|---|---|---|
| Router reset | All Chardsoft Router OS settings, secrets, admin account | The app, Apps Addon, apps and their data | Built (the factory reset) |
| Full reset | Additionally the apps and their data | The app | Planned |
| Uninstall | Everything Chardsoft Router OS set up, including Apps and Docker | eth0's settings, hostname, Wi-Fi country | Built (`uninstall.sh`) |
| Reinstall | Raspberry Pi OS itself | Nothing | Outside Chardsoft Router OS |

Planned: offering a backup download before a reset. Today, download one on the System page first.

**Recovery:** the installer can always repair or reinstall the app and the infrastructure, with or without the old settings.

---

# Security

- **Access:** HTTPS for the dashboard; admin login with password hashes; the session cookie is secure over HTTPS.
- **Session key and CSRF:** the session key is random per router. A CSRF token is required for every request that changes something.
- **Validation and least privilege:**
  - Every input is validated in the backend.
  - System commands run without a shell, as argument lists, so settings can't inject commands.
  - The app runs as a normal user, with `sudo -n` for a fixed list of commands only.
- **Reach:**
  - The app only listens on 127.0.0.1; Caddy is the only way in.
  - The firewall allows the dashboard, SSH, DHCP and DNS only on LAN interfaces.
  - `X-Forwarded-For` is trusted only from Caddy (localhost).
- **Apps:** the App Manager is root-owned, may only run catalog apps, and apps publish ports on 127.0.0.1 only.
- **Web shell:** off by default, needs its own password per session, LAN only, and not offered on phones.
- **Secrets on disk:** Wi-Fi passwords, VPN keys and the SIM PIN are stored with mode 600. The dashboard never sends the PIN or the mobile data password back.

---

# Licensing

- **Software and docs** (Core, Apps Addon): open source, [Apache License 2.0](LICENSE). The [NOTICE](NOTICE) file (Copyright Chaoscent) must stay with every copy; changed files must say they were changed.
- **Contributions** come under the same license (Apache 2.0, section 5), so they can also be used in commercial products, e.g. on Chardsoft Router hardware.
- **Names and logos** ("Chardsoft", "Chardsoft Router OS", "Chardsoft Router"): not licensed. **Chardsoft Router hardware** (PCB, case): not public. The Hardware Edition may stay closed: Apache 2.0 allows closed products built on the code.
- **Third-party code** keeps its license (xterm.js: MIT). Devices sold with Chardsoft Router OS preinstalled must also meet the obligations of the GPL software in the OS image (Linux, dnsmasq, ...): its source code or a written offer for it, and its license texts.

---

# Development Workflow

Development happens on a PC (WSL), hardware testing on the Raspberry Pi.

```text
Edit (WSL)
 ↓
Test locally (app on port 5000; simulated radios and modem for UI tests)
 ↓
git commit
 ↓
git push (dev/core)
 ↓
On the Pi: run the curl installer again (updates, keeps settings)
 ↓
Hardware test
```

Git is the source of truth for the code. The online demo (demo.chardsoft.com/router-os) is built from the frontend by the separate `chaos-router-os-demo` project, with a mock of the API. Changes to the API's format need the mock updated before the next demo build.

---

# Hardware Tests

Each of these must pass on a real Pi before a release:

- **Fresh install:** fresh Pi, installer, setup Wi-Fi with QR code and captive portal, setup wizard, dashboard on `https://chardsoft-router.local`.
- **Wi-Fi:** an access point per radio, clients connect, get DHCP and reach the router; saving one radio keeps the other's clients connected.
- **DHCP and DNS:** addresses on every served interface, local names, app names.
- **Mobile data:** modem found right after install, SIM PIN, APN automatic and by hand, reconnect after a reboot.
- **Caddy:** HTTPS on the router's names and addresses, HTTP redirect after setup, the CA download.
- **Safe Apply:** a change that cuts off the dashboard is reverted after 120 s and after a power cycle.
- **Update:** the installer run again keeps every setting.
- **Factory reset:** settings gone, router reachable over eth0, setup mode, apps still installed.
- **Apps:** addon install, app install, the app's name over HTTPS, a crashing app does not affect the router.
- **Uninstall:** nothing left behind; SSH over eth0 still works.

---

# Architecture Invariants

> [!important] These are architectural rules

- Core is installed through the permanent public bootstrap URL.
- Git is the source of truth for the code; the installer never lists app files.
- The code (`/opt`) is separate from the defaults (`/etc`), the persistent settings (`/var/lib`) and the running settings (`/tmp`).
- The app never writes `/etc/chardsoft-router-os`.
- Persistent settings are the last known-good state; settings become persistent only after validation, applying and verification.
- Running and pending settings are never authoritative at boot: a reboot always starts from `/var/lib`.
- Every change that can cut the user off waits for confirmation and reverts by itself.
- The backend validates everything; system commands never go through a shell.
- The app runs as a normal user with a fixed sudo command list.
- The installer is safe to run again and keeps all settings; updates never erase configuration.
- dnsmasq serves the LAN, never the router's own name lookups.
- `https://chardsoft-router.local` is the management address.
- The setup Wi-Fi runs on a 2.4 GHz-capable radio until an admin account exists. There is no default login.
- Core never depends on the Apps Addon; apps publish ports on 127.0.0.1 only, and Caddy is their only way in.
- A factory reset resets configuration, not the app and not the apps.

---

# Overview

```text
                         CHAOS ROUTER OS
                                │
                 ┌──────────────┴──────────────┐
                 │                             │
                CORE                      APPS ADDON (optional)
                 │                             │
     ┌───────────┼─────────────┐               ▼
     │           │             │             Docker
  Network       Web         Settings           │
     │           │             │               ├── App
     │           ├── Caddy     ├── /etc        ├── App
     │           └── App       ├── /var/lib    └── App
     │               (/opt)    └── /tmp
     │
     ├── NetworkManager (eth0, br0, chardsoft-modem)
     ├── ModemManager (wwan0)
     ├── hostapd (one per radio)
     ├── dnsmasq (DHCP, DNS)
     ├── ufw
     └── WireGuard, OpenVPN
```

> [!success] Core Design Rule
> **Chardsoft Router OS Core is a stable router platform on top of Raspberry Pi OS. Git supplies the code; settings live apart from it. A change becomes permanent only once it is verified, and a reboot always returns to the last known-good state. Optional apps live outside the router's lifecycle.**
