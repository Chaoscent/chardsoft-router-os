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
  and Wi-Fi; the service starts at boot; `/opt/chaos-router-os` belongs to
  root; `/etc/sudoers.d/chaos-router-os` only allows the root helper.
- [ ] **Update an existing install** by running the installer again: an
  app-user-owned `/opt/chaos-router-os` is moved aside
  (`.user-owned-<date>`) and cloned fresh.
- [ ] **Updates from the System page:** check, update, and a forced failure
  (rollback to the previous commit). Settings backups land in
  `/var/backups/chardsoft-router-os`.
- [ ] **Setup Wi-Fi** with real phones (iOS, Android, Windows), including the
  old open bug: the wizard's Wi-Fi gave phones no address (check the Logs
  entry "DHCP for the new Wi-Fi failed").
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
  nss-mdns); multi-label names (`app.chaos-router.local`) are the open question.
- [ ] **Uninstall** in a VM (snapshot before), then `dpkg -l`, `/etc`,
  `iptables-save`, `nmcli connection`, `ufw status`; then a fresh install.
- [ ] **RAM** on a 1 GB board: `free -h`, `ps -eo rss,comm --sort=-rss | head`.

---

## 2. Decisions

- [ ] **Dedicated service user.** The app runs as the user who installed it.
  On Raspberry Pi OS that user has passwordless sudo for everything, so the
  root helper only limits the app on systems without that (the installer
  warns). A system user of its own would make the helper count everywhere,
  but changes the web shell: it would no longer be the admin's own shell.
- [ ] **Names on the router.** The default Wi-Fi name "Chaos Router", the
  setup Wi-Fi "ChaosRouter-Setup-XXXX" and `chaos-router.local` (and
  `<app>.chaos-router.local`) carry the hardware's name. Keep them, or use
  Chardsoft names for installs on any Pi? Changing the `.local` name changes
  every app address and certificate on routers in use.

---

## 3. Still to build

- [ ] **IPv6** (router advertisements, DHCPv6, forwarding; CLAT/464XLAT for
  IPv6-only mobile networks). IPv6 forwarding is deliberately left off:
  turning it on makes the kernel ignore router advertisements, which can cut
  off the modem's IPv6. Needs the modem on the Pi to test.
- [ ] **Full-tunnel VPN and the WAN side:** answers to connections from the
  internet side (WireGuard/OpenVPN servers on the router, remote access over
  the modem) also go into the tunnel and break. Needs policy routing for
  traffic that came in on the WAN.
- [ ] **Media and files for apps** (Jellyfin's media folder is root-only in
  `/var/lib/chaos-router-apps`): a shared folder (Samba/SFTP) or a USB drive.
- [ ] **RAM-heavy apps on 1 GB boards:** warn or hide them in the catalog.
- [ ] **Schema version** in the JSON config files, with migrations between
  versions (the updater runs the installer, which keeps existing settings).
- [ ] **Optional setup step:** check the WAN or modem.
- [ ] **Hardware Edition** (OLED, buttons, RGB, GPS) and the Apps Addon (v2).

---

## 4. At the v1 release

- [ ] Set `VERSION` to `1.0.0`, fill `release/core` from `dev/core`, and
  switch `BRANCH` in `install.sh`, the install URL in README and PLAN, the
  VPS redirect (`/router-os/core`) and GitHub's default branch from `dev/core`
  to `release/core`. Same for `release/hardware-edition` and `release/apps`
  (`BRANCH` in `deploy/install-apps.sh`). Installed routers follow the branch
  in `/etc/chaos-router-os/install.json`; switch it there (or reinstall) to
  move them to `release/core`.
- [ ] Push `~/projects/apps` to the `dev/apps` branch (the one-click install
  clones it from there).

---

## Done since the last plan

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
