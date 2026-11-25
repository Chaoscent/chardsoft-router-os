# Chardsoft Router OS: Plan to v1

What is still missing before Core v1. The README covers what exists; this
file covers what's next.

Target platform: **Raspberry Pi OS Lite (64-bit)** on a Raspberry Pi 5.

---

## Where it stands

Core is feature-complete for v1. Almost everything has only been tested in
WSL with fake hardware (🧪 in the README), so the main work left is testing
on the Pi 5 and fixing what that finds. The installer, setup Wi-Fi, captive
portal and setup wizard have been tried on the Pi.

---

## 1. Hardware testing (Pi 5)

Most likely to need fixes: Wi-Fi, 6 GHz, the modem, the OpenVPN PKI, the
firewall, and the root helper's rules (a command the helper refuses shows
its reason, starting with `root-helper:`, in the page's message and the log).

- [ ] **Fresh install** on a fresh Raspberry Pi OS Lite (64-bit), over
  Ethernet and over Wi-Fi SSH: SSH and the dashboard stay reachable from eth0
  and Wi-Fi; the service starts at boot; `/opt/chardsoft-router-os` belongs to
  root; `/etc/sudoers.d/chardsoft-router-os` only allows the root helper.
- [ ] **Update an existing install** by running the installer again: an
  app-user-owned `/opt/chardsoft-router-os` is moved aside
  (`.user-owned-<date>`) and cloned fresh.
- [ ] **Updates from the System page:** check, update, and a forced failure
  (rollback to the previous commit). Settings backups land in
  `/var/backups/chardsoft-router-os`.
- [ ] **Setup Wi-Fi** with real phones (iOS, Android, Windows), including the
  old open bug: the wizard's Wi-Fi gave phones no address (check the Logs
  entry "DHCP for the new Wi-Fi failed"). The wizard's Internet step with
  the real modem: no SIM, SIM PIN, mobile data off, online; and with eth0
  as WAN.
- [ ] **Modem:** LAN devices get internet through the modem; `mmcli` parsing
  (signal per section, "lte, 5gnr" → "5G NSA", SIM state); the active band
  (`sudo qmicli -p -d /dev/cdc-wdm0 --nas-get-rf-band-info`, the Modem page
  shows e.g. "B3 + n78").
- [ ] **eth0 LAN/WAN switch:** default route, NAT and DHCP pause on eth0.
- [ ] **Wi-Fi:** several radios at once, 6 GHz on wlan1, channel widths
  (40/80/160 MHz; a radio that can't do one fails to start and is rolled
  back), 10.42.0.1/24 on wlan1 with DHCP and NAT.
- [ ] **LAN bridge** with eth0 + wlan1, DHCP on br0, revert and reboot. A port
  with a DHCP address from another router is now refused before applying.
- [ ] **LAN routing:** with eth0 or wlan0 also on a home network (its default
  route has the better metric), a phone on the router's Wi-Fi still goes out
  through the modem (`ip rule`, `ip route show table 4242`,
  `ip route get 1.1.1.1 from <phone> iif wlan1`).
- [ ] **CLAT** with the IPv6-only Telekom SIM: after the installer ran again
  (it adds tayga and `chardsoft-clat.service`), the Firewall page shows "Active,
  NAT64 64:ff9b::/96" (or the carrier's prefix); a phone on the router's
  Wi-Fi opens IPv4-only sites (`http://ipv4.google.com`, `ping 1.1.1.1` from
  a laptop); `ip route get 1.1.1.1 from <phone> iif wlan1` says `dev clat`;
  after a modem reconnect (new /64) it keeps working within 15 s.
- [ ] **Modem watchdog:** when the modem is stuck (rx errors rising, nothing
  received), it is reset within about a minute and the Logs say so.
- [ ] **Mobile data counter:** the dashboard's "Mobile data" roughly matches
  the carrier's app after a day (correct it on the Modem page if not); the
  count survives a reboot (`/var/lib/chardsoft-router-os/data_usage_counter.json`);
  with a small test limit and "Stop internet for devices", a phone on the
  Wi-Fi loses the internet while the dashboard stays reachable
  (`sudo iptables -S CHARDSOFT-QUOTA`), and "Resume anyway" brings it back.
- [ ] **Internet check** on the dashboard: shows "Online" when all is well;
  mobile data off, a SIM PIN, a pulled antenna or a stopped CLAT give the
  matching message and button ("Switch on mobile data", "Restart modem",
  "Repair").
- [ ] **Routes page:** a static route to a network behind another router on
  the LAN (e.g. `10.20.0.0/16 via 192.168.1.5`) shows "Active", is in
  `ip route show proto 77`, and the router reaches that network; it comes
  back after unplugging and replugging the cable; a gateway outside every
  network shows "Waiting" with the reason; the tables match `ip route show
  table all`.
- [ ] **Internet time** (Clients page): "Pause internet" for 30 minutes stops
  a phone's internet at once (`sudo iptables -S CHARDSOFT-PAUSE`) while it keeps
  its Wi-Fi and reaches the dashboard; it comes back by itself after the
  time and with "Resume internet". An offline time that starts in two
  minutes takes the phone offline within 15 seconds of its start; "Allow
  until it ends" lets it back in. The Logs say when a device goes offline
  and online. After a reboot without internet (clock not set) the page says
  that offline times wait for the clock.
- [ ] **GNSS** (Modem page): with an antenna on the RM520N's GNSS port and
  the Pi near a window, "Receiver: On" shows satellites heard within a
  minute and a position within a few minutes. The RM520N refuses NMEA over
  QMI, so `mmcli -m 0 --location-status` shows `gps-unmanaged` and the
  sentences come from `/dev/ttyUSB1` (tested 2026-11-24 without antenna:
  receiver on, the port sends GGA/RMC/VTG/GSA, no fix; check that GSV
  satellite lists appear once satellites are heard);
  "Open map" shows the right place. After "Restart modem" the receiver comes
  back on by itself. Clock: boot without mobile data and without network
  (`timedatectl` says not synchronised): with a position the Logs say
  "Clock set from GNSS" and `date` is right; with internet time GNSS never
  touches it. The dashboard's "Location" widget (Edit dashboard) matches.
- [ ] **GNSS map** (Modem page): the world map shows the router's dot; with
  "Track: Keep 1 day" a drive draws a line. "Download map" for the router's
  country at "Streets": the pmtiles tool comes from GitHub (checksum
  checked), the progress shows, and afterwards the map has streets and
  labels with no request leaving the router (browser dev tools: only the
  router's address). Check the size and time it took over mobile data and
  that "Stop download" and "Delete detailed map" work.
- [ ] **Guest Wi-Fi:** first check what the radios allow (`iw phy` "valid
  interface combinations": the page says when a radio sends only one
  network; the Pi's own Wi-Fi may be such a radio, a USB adapter usually is
  not). Then on a radio that can: a phone joins the guest network by its
  QR code and has internet; it cannot open the dashboard (also not by the
  LAN address), cannot ping a LAN device or another guest, and a LAN device
  cannot reach it (`sudo iptables -S CHARDSOFT-GUEST-IN`, `CHARDSOFT-GUEST-FWD`).
  DHCP serves `wlan0-guest`; the main network keeps working while guests
  are on; switching the guest network off removes `wlan0-guest`; the rules
  come back within 30 s after `sudo ufw reload`.
- [ ] **USB Wi-Fi hotplug:** with the 6 GHz network saved for the AXML
  (wlan1), plug it in while the router runs: within about 10 s the network
  is on (`systemctl status chardsoft-hostapd@wlan1`), DHCP serves 10.43.0.x and
  the bell says so; pull it out: the bell says it is off, `wlan1` is gone
  from `/etc/dnsmasq.d`; plug it back: on again. A never-seen adapter is
  announced once with its bands and shows on the Wi-Fi page. Note: the AXML
  runs on the 5G HAT's USB hub and on the Pi's own ports; the disconnects
  on 2026-11-24 came from a USB-C power injector in between, not the HAT.
  Check that it stays connected under load (a phone downloading on 6 GHz).
  Tested over SSH on 2026-11-25 (USB `authorized` 0/1 on the AXML): off
  and cleaned up in under 12 s, on again in under 25 s, both notified.
  Found then: the firewall had no DHCP/DNS rules for wlan1 (built at boot,
  before the adapter came), so a phone would get no address; now synced by
  the watcher. Check with a phone: it gets 10.43.0.x and opens websites;
  `sudo iptables -S ufw-user-input | grep wlan1` lists DHCP and DNS.
- [ ] **DNS for the devices:** found 2026-11-25: dnsmasq had no upstream
  servers (Debian's `/run/dnsmasq/resolv.conf` never exists with openresolv)
  and refused every lookup from the Wi-Fi. After the update:
  `/etc/dnsmasq.d/chardsoft-router-upstream.conf` exists, a phone on each
  network opens websites, the internet check says "Everything works", and
  choosing Cloudflare on the DNS page still switches to it.
- [ ] **Speed test (iperf3):** Tools, "Speed test from a device": start the
  server, run `iperf3 -c 10.43.0.1` and `iperf3 -c 10.43.0.1 -R` from a
  laptop or phone on the 6 GHz network (wlan1) (also the AXML under load); the card
  shows the speed, and `sudo iptables -S ufw-user-input | grep 5201` lists
  the LAN interfaces while it waits and nothing after. "Speed (iperf3)" tab
  to a LAN machine running `iperf3 -s`; a test over the modem says the
  mobile data it used. `systemctl is-enabled iperf3` is not "enabled".
- [ ] **Dashboard edit mode:** in a browser (mouse, and a phone or tablet
  by touch): drag widgets by the handle (the others slide, the page scrolls
  near the edges), drag the right edge (snaps to quarter ... full width),
  hide a widget and bring it back from the tray, add a graph, List view
  Apply and Cancel, then Done: the layout stays after a reload and in a
  second browser. Cancel drops everything; Escape mid-drag puts the widget
  back. Keyboard: Tab to a handle, arrow keys move it; on the edge, arrow
  keys change the width. Light and dark theme.
- [ ] **Notifications:** a wrong password at the login, a new phone joining
  the Wi-Fi, pulling the cable behind a static route ("stopped working",
  then "works again") and a watchdog modem restart each show under the
  bell with its count; opening the list clears the count.
- [ ] **Data per device:** stream a video on one phone for a few minutes:
  the dashboard's "Mobile data" lists that phone on top within a minute,
  close to what the phone's own data counter says (Wi-Fi usage); the
  Clients page shows it in the "Data" column. Check IPv4 through the CLAT
  counts too (`sudo iptables -L CHARDSOFT-ACCT -v -x -n` grows while browsing
  an IPv4-only site), and that the numbers survive a reboot.
- [ ] **Tools page:** after the reinstall `traceroute` is there; a
  traceroute to 1.1.1.1 over the modem shows the carrier's hops; "Send
  from" a VPN client interface pings through the tunnel; "Which way" for an
  address of a static route names that route (Routes page, "Test").
- [ ] **Wi-Fi QR code** (Wi-Fi page, "Show QR code"): an iPhone and an Android
  phone join by scanning it, also with WPA3 and a hidden network.
- [ ] **IPv6 for the LAN** (Firewall page, Routing & NAT): with the modem
  and with eth0 as WAN, a phone and a laptop on the LAN get an `fdXX:` address
  and reach `ipv6.google.com`; the WAN keeps its own IPv6 with forwarding on
  (`ip -6 addr show wwan0`, `ip -6 route`); the LAN address survives
  NetworkManager reconnecting the bridge and an access point restart (the
  watcher re-adds it within 15 s); with a full-tunnel VPN, LAN devices fall
  back to IPv4 (`curl -6` fails quickly, `curl -4` works).
- [ ] **VPN client** with a real provider: the LAN's public IP is the VPN's;
  kill switch (stop the tunnel: LAN devices lose the internet, the VPN page
  says so; disconnect: internet back).
- [ ] **Reboot and factory reset:** boot, factory reset into the setup Wi-Fi,
  app crash restart.
- [ ] **Web shell** under the service (gunicorn threads + WebSocket, `wss://`).
- [ ] **Caddy:** certificates by IP and by name, the captive portal on the
  setup Wi-Fi, `--remove` restoring the old Caddyfile.
- [ ] **Apps Addon** with real Docker: each catalog app, update, remove, a
  router rename, Vaultwarden over HTTPS.
- [ ] **mDNS names** per client (Windows, macOS/iOS, Android, Linux with
  nss-mdns); multi-label names (`app.chardsoft-router.local`) are the open question.
- [ ] **Uninstall** in a VM (snapshot before), then `dpkg -l`, `/etc`,
  `iptables-save`, `nmcli connection`, `ufw status`; then a fresh install.
- [ ] **RAM** on a 1 GB board: `free -h`, `ps -eo rss,comm --sort=-rss | head`.
  The Apps page should warn on Jellyfin and Nextcloud there.

---

## 2. Decisions

- [ ] **Dedicated service user.** The app runs as the user who installed it.
  On Raspberry Pi OS that user has passwordless sudo for everything, so the
  root helper only limits the app on systems without that (the installer
  warns). A system user of its own would make the helper count everywhere,
  but changes the web shell: it would no longer be the admin's own shell.
- [ ] **Rename chaos -> chardsoft on a router in use** (decided 2026-11-25:
  no "chaos" anywhere; the hardware is now "Chardsoft Router"). Test the
  migration on the Pi while someone is at it (`install.sh`'s
  `migrate_names`, run by the System page update): afterwards
  `/opt|/etc|/var/lib/chardsoft-router-os` exist and no `chaos-*` folder,
  service (`systemctl list-units 'chaos-*' --all`), firewall chain
  (`sudo iptables -S | grep CHAOS`) or NetworkManager connection is left;
  both access points, DHCP, DNS and IPv4 for devices (CLAT) work again
  within about a minute, mobile data stayed up, settings and the login are
  the same, `https://chardsoft-router.local` opens. Not covered by the
  updater's rollback (the old installer cannot undo it): if the new version
  does not start, run `sudo /opt/chardsoft-router-os/install.sh` by hand.
  Left: the Apps Addon (branch `dev/apps`: `/opt/chardsoft-router-apps`,
  `chardsoft-apps`) and the demo project's own name; the system hostname
  is the owner's (`sudo hostnamectl set-hostname ...`).

---

## 3. Still to build

- [ ] **IPv6 through the VPN** (LAN IPv6 into a full tunnel; today devices
  fall back to IPv4 while one is connected) and **prefix delegation** where a
  provider offers it (public addresses instead of NAT66).
- [ ] **Full-tunnel VPN and the WAN side:** answers to connections from the
  internet side (WireGuard/OpenVPN servers on the router, remote access over
  the modem) also go into the tunnel and break. Needs policy routing for
  traffic that came in on the WAN.
- [ ] **Media and files for apps** (Jellyfin's media folder is root-only in
  `/var/lib/chardsoft-router-apps`): a shared folder (Samba/SFTP) or a USB drive.
- [ ] **Hardware Edition** (OLED, buttons, RGB, GPS) and the Apps Addon (v2).

---

## 4. At the v1 release

- [ ] Set `VERSION` to `1.0.0`, fill `release/core` from `dev/core`, and
  switch `BRANCH` in `install.sh`, the install URL in README and PLAN, the
  VPS redirect (`/router-os/core`) and GitHub's default branch from `dev/core`
  to `release/core`. Same for `release/hardware-edition` and `release/apps`
  (`BRANCH` in `deploy/install-apps.sh`). Installed routers follow the branch
  in `/etc/chardsoft-router-os/install.json`; switch it there (or reinstall) to
  move them to `release/core`.
- [ ] Push `~/projects/apps` to the `dev/apps` branch (the one-click install
  clones it from there).

---

## Done since the last plan

- Dashboard: "Internet" widget that checks the uplink step by step (modem,
  sharing, data limit, ping over IPv4/IPv6, DNS, CLAT, LAN routing) and
  offers the fix for the first problem.
- Mobile data counter per billing period with an optional data limit:
  warning in the Logs, optional stop of the LAN's internet at the limit,
  "Resume anyway", correction to the carrier's count (Modem page).
- Wi-Fi page: "Show QR code" for the saved network (drawn on the router).
- Clients page: pause a device's internet (30 minutes to "until I resume
  it") and weekly offline times (e.g. school nights 21:00-07:00); the
  device keeps reaching the router.
- GNSS map: built-in world map (Natural Earth), optional detailed
  OpenStreetMap map of one country downloaded onto the router (Protomaps
  build, pmtiles extract), and an optional position track.
- GNSS from the modem (Modem page, optional dashboard widget): position,
  satellites per constellation, the mobile cell, and the clock from GNSS
  while there is no internet time.
- USB Wi-Fi hotplug: adapters plugged in or out while running; saved
  networks start and stop by themselves, new adapters are announced, each
  network remembers its adapter.
- Guest Wi-Fi: a second network on a radio (where the radio allows two),
  internet only, with a generated password and its own QR code.
- Notification bell in the header: warnings, errors and notable events
  (new device, modem restarted by the watchdog, new billing period, a
  static route that stops or works again) with links to their pages.
- Data per device this billing period (dashboard "Mobile data", Clients
  page), with the router's own share as the rest.
- Tools page: ping, traceroute, name lookup (with the DNS blocklist),
  port check and "which way" (`ip route get`), each with a plain verdict.
- Routes page: the routing tables (router, "Your devices", VPN tables) and
  rules in plain words, and static routes of your own through Safe Apply.
- The dashboard stays responsive while the modem is slow (polls wait for
  the last answer; one modem reading shared by all requests; timeouts).
- Modem watchdog: a stuck data link (every received packet rejected, or no
  answers at all) restarts the modem automatically.
- LAN routing: LAN traffic always leaves through the WAN, even when another
  interface has the better default route.
- CLAT (464XLAT): IPv4 for the LAN over IPv6-only mobile plans.
- "B3 + 5G" for 5G NSA, where the modem reports only the LTE band.
- Setup wizard: an Internet step that shows the modem or Ethernet uplink
  and what to do if it is not online (never blocks setup).
- Apps that need more memory than the board has (`min_ram_mb` in the
  catalog) show a warning and ask before installing.
- Settings format version (`schema.json`) with update steps on start and
  for older backups; backups from a newer Router OS are refused.
- Root helper (`deploy/root-helper`): the only program the app may run as
  root; checks commands, paths and config contents. Root-owned code and
  Python environment; scripts that run as root live root-owned in
  `/usr/local/libexec/chardsoft-router-os`.
- Encrypted backups (optional password, AES-256-GCM, scrypt).
- Updates from the System page (`deploy/update.sh`): settings backup, new
  installer, health check, automatic rollback.
- Web shell only over HTTPS behind Caddy (a warning without Caddy).
- VPN kill switch for full-tunnel profiles.
- MSS clamping; Avahi off the modem; DNS follows a new local domain at once;
  OpenVPN prefers ChaCha20 on CPUs without AES; app names for interfaces
  added later; real active client count; LAN bridge refuses ports with a
  DHCP address from another router.
- Security review of the web API: frame and sniffing headers, no caching
  of API answers, a password change ends every other session, unknown
  user names take as long as wrong passwords. Tests: `tests/` (89, run
  `cd backend && ../.venv/bin/python -m unittest discover -s ../tests`).
- IPv6 for the LAN (`services/ipv6.py`): a ULA prefix per router, one /64 per
  served LAN, router advertisements from dnsmasq, NAT66 to the WAN, the WAN
  kept on accept_ra=2, no IPv6 around a full-tunnel VPN.
- Dashboard widgets (several traffic graphs, editor); Wi-Fi channel width;
  active modem band over QMI; sentence case per the design kit.

---

## Hardware notes

**Pi 5 with 1 GB RAM:** fine for Core. Estimated use with everything enabled
is about 300–400 MB (OS ~100–150 MB, NetworkManager + ModemManager ~20–30 MB,
app ~40–80 MB, OpenVPN ~10 MB per instance, Caddy ~20–40 MB).

- `/tmp` may be RAM-backed: never write anything large there.
- The journal may be volatile: logs are lost on reboot.
- Apps Addon on 1 GB: Docker alone needs ~100 MB. Pi-hole, AdGuard and Uptime
  Kuma fit; Nextcloud, Jellyfin and Immich don't.

**Pi 4:** should work, with caveats: the modem runs over USB, OpenVPN uses
ChaCha20 (no AES instructions), and NetworkManager (Bookworm or newer) is
required.
