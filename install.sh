#!/usr/bin/env bash
#
# Chardsoft Router OS installer for Raspberry Pi OS Lite (64-bit).
#
#   curl -fsSL https://raw.githubusercontent.com/chaoscent/chardsoft-router-os/dev/core/install.sh | sudo bash
#
# or, from a clone:
#
#   sudo ./install.sh
#
# Installs the packages, the app with its Python environment, the
# default settings and the systemd service. On a router that is not set
# up yet, it finishes by showing the setup Wi-Fi's name, password and QR
# code. Safe to run again: an existing install is updated, settings stay.
#
# Options (also as environment variables):
#   --country XX   Wi-Fi country code, e.g. US      (CHARDSOFT_COUNTRY;
#                  asked when not given, no default)
#   --dir PATH     where to clone the app            (CHARDSOFT_DIR, default /opt/chardsoft-router-os)
#   --user NAME    the account the app runs as (default: the one that
#                  called sudo; the updater passes it)
#   --yes          no questions, use the defaults (needs --country
#                  unless the system already has one)

set -euo pipefail

REPO=${CHARDSOFT_REPO:-https://github.com/chaoscent/chardsoft-router-os}
BRANCH=${CHARDSOFT_BRANCH:-dev/core}
INSTALL_DIR=${CHARDSOFT_DIR:-/opt/chardsoft-router-os}
COUNTRY=${CHARDSOFT_COUNTRY:-}
ASSUME_YES=0
APP_USER_ARG=""

# 1: the code in INSTALL_DIR is ours, root-owned (the default /opt
# clone). 0: started from someone's own clone, which stays theirs.
MANAGED=1

# What and where was installed, for the updater (deploy/update.sh).
INSTALL_RECORD=/etc/chardsoft-router-os/install.json

STATE_DIR=/var/lib/chardsoft-router-os
SUDOERS_FILE=/etc/sudoers.d/chardsoft-router-os

# Packages this installer installed (not the ones that were already
# there): uninstall.sh removes exactly these.
PACKAGES_RECORD=/etc/chardsoft-router-os/installed-packages

# dnsmasq serves the LAN only: it must not become the router's own DNS
# server (see keep_name_resolution).
DNSMASQ_DROPIN=/etc/systemd/system/dnsmasq.service.d/chardsoft-router-os.conf

# Where dnsmasq finds upstream servers for the devices while the DNS
# page names none (see keep_name_resolution).
DNSMASQ_UPSTREAM=/etc/dnsmasq.d/chardsoft-router-upstream.conf

# DNS servers that worked before the install (restored if needed).
SAVED_NAMESERVERS=""

PACKAGES=(
    git python3-venv python3-pip
    network-manager
    dnsmasq hostapd iw rfkill
    modemmanager mobile-broadband-provider-info libqmi-utils
    tayga
    ufw iptables
    wireguard-tools openvpn easy-rsa openresolv
    qrencode traceroute iperf3
    caddy
    avahi-daemon avahi-utils
)

# Commands the app runs with `sudo -n` (see README, System Requirements).
# Root-owned home of the root helper (see setup_sudo).
HELPER_DIR=/usr/local/libexec/chardsoft-router-os


# -------------------------------------------------------------------
# Output
# -------------------------------------------------------------------

step() { printf '\n\033[1;34m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m  x %s\033[0m\n' "$*" >&2; exit 1; }

# Questions work even with `curl | sudo bash` (stdin is the script).
ask() {

    local prompt=$1 default=$2 answer=""

    if [[ $ASSUME_YES -eq 1 || ! -r /dev/tty ]]; then
        echo "$default"
        return
    fi

    read -r -p "    $prompt [$default]: " answer < /dev/tty || true

    echo "${answer:-$default}"
}


# -------------------------------------------------------------------
# Steps
# -------------------------------------------------------------------

parse_args() {

    while [[ $# -gt 0 ]]; do
        case $1 in
            --country) COUNTRY=${2:-}; shift 2 ;;
            --dir)     INSTALL_DIR=${2:-}; shift 2 ;;
            --user)    APP_USER_ARG=${2:-}; shift 2 ;;
            --yes|-y)  ASSUME_YES=1; shift ;;
            *)         die "Unknown option: $1" ;;
        esac
    done
}


check_system() {

    step "Checking the system"

    [[ $EUID -eq 0 ]] || die "Run with sudo."

    # --user: the updater, which runs as root without sudo.
    APP_USER=${APP_USER_ARG:-${SUDO_USER:-}}

    if [[ -z $APP_USER || $APP_USER == "root" ]] || ! id "$APP_USER" >/dev/null 2>&1; then
        die "Run with sudo from the user account the router should run as (not as root)."
    fi

    APP_GROUP=$(id -gn "$APP_USER")

    info "App user: $APP_USER"

    # shellcheck disable=SC1091
    . /etc/os-release

    if [[ ${ID:-} != "debian" && ${ID:-} != "raspbian" && ${ID_LIKE:-} != *debian* ]]; then
        warn "This is ${PRETTY_NAME:-an unknown system}. Chardsoft Router OS is made for Raspberry Pi OS Lite (64-bit)."
    else
        info "System: ${PRETTY_NAME:-Debian}"
    fi

    if [[ $(uname -m) != "aarch64" ]]; then
        warn "Not a 64-bit ARM system ($(uname -m)). Raspberry Pi OS Lite (64-bit) is recommended."
    fi

    if grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
        info "Board: $(tr -d '\0' < /proc/device-tree/model)"
    else
        warn "This does not look like a Raspberry Pi. Only the Pi 5 is tested."
    fi
}


# Installs from before 2026-11-25 used "chaos" names (folders, services,
# config files, firewall chains, NetworkManager connections). Moves such
# an install to the chardsoft names once; does nothing on new installs or
# ones already moved. The settings move as they are. The running settings
# are dropped, so the app rebuilds them from the saved ones and applies
# everything under the new names when it starts, as after a boot: the
# access points, DHCP and IPv4 for devices are off until then (about a
# minute). SSH over the LAN cable is not touched.
migrate_names() {

    # A test folder in front of every path (tests/test_migrate_names.sh).
    local root=${CHARDSOFT_MIGRATE_ROOT:-}

    local old_etc=$root/etc/chaos-router-os
    local old_state=$root/var/lib/chaos-router-os
    local old_code=$root/opt/chaos-router-os
    local new_code=$root/opt/chardsoft-router-os
    local systemd=$root/etc/systemd/system

    # The updater of an old install passes its folder (--dir): the new
    # one is meant, also when the move happened already.
    local moving_code=0

    if [[ $INSTALL_DIR == "${old_code#"$root"}" ]]; then
        INSTALL_DIR=${new_code#"$root"}
        moving_code=1
    elif [[ $INSTALL_DIR == "${new_code#"$root"}" ]]; then
        moving_code=1
    fi

    if [[ ! -e $old_etc && ! -e $old_state && ! -e $systemd/chaos-router-os.service && ! -e $old_code ]]; then
        return 0
    fi

    step "Moving the install to its new names (chaos -> chardsoft)"

    # The old services, then their unit files.
    local unit
    while read -r unit; do
        [[ -n $unit ]] && { systemctl disable --now "$unit" 2>/dev/null || true; }
    done < <(systemctl list-units --all --plain --no-legend 'chaos-*' 2>/dev/null | awk '{print $1}')

    for unit in chaos-router-os chaos-router-mdns chaos-clat; do
        systemctl disable --now "$unit" 2>/dev/null || true
    done

    if [[ -f $root/etc/openvpn/server/chaos.conf ]]; then
        systemctl disable --now openvpn-server@chaos 2>/dev/null || true
    fi

    rm -f "$systemd/chaos-router-os.service" "$systemd/chaos-router-mdns.service" \
          "$systemd/chaos-hostapd@.service" "$systemd/chaos-clat.service" \
          "$systemd/dnsmasq.service.d/chaos-router-os.conf"
    systemctl daemon-reload || true

    # Defaults and saved settings move; the running ones are rebuilt.
    if [[ -d $old_etc && ! -e $root/etc/chardsoft-router-os ]]; then
        mv "$old_etc" $root/etc/chardsoft-router-os
    fi

    if [[ -d $old_state && ! -e $root$STATE_DIR ]]; then
        mv "$old_state" "$root$STATE_DIR"
    fi

    rm -rf $root/tmp/chaos-router-os

    # The code, when it is in the old default folder (an own clone or a
    # --dir folder elsewhere stays). Its Python environment has the folder
    # written into it: setup_python builds it anew.
    if (( moving_code )) && [[ -d $old_code && ! -e $new_code ]]; then
        mv "$old_code" "$new_code"
        rm -rf "$new_code/.venv"
        info "Code: ${new_code#"$root"} (the Python environment is set up again)"
    fi

    # Config files the app writes: written anew under the new names.
    rm -f $root/etc/hostapd/chaos-*.conf $root/etc/dnsmasq.d/chaos-router-*.conf $root/etc/tayga/chaos-clat.conf

    if [[ -d $root/etc/caddy/chaos-apps && ! -e $root/etc/caddy/chardsoft-apps ]]; then
        mv $root/etc/caddy/chaos-apps $root/etc/caddy/chardsoft-apps
    fi

    if [[ -f $root/etc/caddy/Caddyfile.before-chaos && ! -e $root/etc/caddy/Caddyfile.before-chardsoft ]]; then
        mv $root/etc/caddy/Caddyfile.before-chaos $root/etc/caddy/Caddyfile.before-chardsoft
    fi

    # OpenVPN server: the same keys under the new names (clients keep
    # working: they check the certificate "server", not a name); its
    # config is written anew by the app.
    local name
    if [[ -f $root/etc/openvpn/server/chaos.conf ]]; then
        for name in pki tls-crypt.key crl.pem; do
            if [[ -e $root/etc/openvpn/server/chaos-$name && ! -e $root/etc/openvpn/server/chardsoft-$name ]]; then
                mv "$root/etc/openvpn/server/chaos-$name" "$root/etc/openvpn/server/chardsoft-$name"
            fi
        done
        rm -f $root/etc/openvpn/server/chaos.conf
    fi

    rm -f $root/etc/openvpn/chaos-nat.sh

    # Root helper and its sudo rule: installed anew by setup_sudo.
    rm -f $root/etc/sudoers.d/chaos-router-os
    rm -rf $root/usr/local/libexec/chaos-router-os

    # Firewall: the old chains out (the jumps to them first). The app
    # builds the new ones when it starts.
    local cmd table rule chain
    for cmd in iptables ip6tables; do
        command -v "$cmd" >/dev/null || continue
        for table in filter nat mangle; do
            "$cmd" -t "$table" -S >/dev/null 2>&1 || continue
            while read -r rule; do
                eval "$cmd -t $table ${rule/#-A /-D }" 2>/dev/null || true
            done < <("$cmd" -t "$table" -S | grep -E -- '-j CHAOS-' | grep -vE '^-A CHAOS-')
            while read -r chain; do
                "$cmd" -t "$table" -F "$chain" 2>/dev/null || true
            done < <("$cmd" -t "$table" -S | awk '/^-N CHAOS-/ {print $2}')
            while read -r chain; do
                "$cmd" -t "$table" -X "$chain" 2>/dev/null || true
            done < <("$cmd" -t "$table" -S | awk '/^-N CHAOS-/ {print $2}')
        done
    done

    # NetworkManager: the same connections under new names (no reconnect).
    local uuid
    if command -v nmcli >/dev/null; then
        while IFS=: read -r uuid name; do
            case $name in
                chaos-lan|chaos-lan-*|chaos-modem)
                    nmcli connection modify "$uuid" connection.id "chardsoft-${name#chaos-}" 2>/dev/null || true
                    ;;
            esac
        done < <(nmcli -t -f UUID,NAME connection show 2>/dev/null)
    fi

    if [[ -e $root/opt/chaos-router-apps || -e $root/usr/local/bin/chaos-apps ]]; then
        warn "The Apps Addon was installed under the old names: install it again from the System page."
    fi

    info "Done. Access points, DHCP and IPv4 for devices come back when the dashboard starts."
}


install_packages() {

    step "Installing packages (this can take a few minutes)"

    export DEBIAN_FRONTEND=noninteractive

    local pkg new=()

    SAVED_NAMESERVERS=$(awk '$1 == "nameserver" && $2 != "127.0.0.1" {print $2}' /etc/resolv.conf 2>/dev/null)

    for pkg in "${PACKAGES[@]}"; do
        dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" || new+=("$pkg")
    done

    # iperf3 runs only for a test from the Tools page, never as a
    # daemon listening all the time (its package asks).
    echo "iperf3 iperf3/start_daemon boolean false" | debconf-set-selections 2>/dev/null || true

    apt-get update -q
    apt-get install -y -q "${PACKAGES[@]}"

    # Add to the record (an update may install packages added later).
    mkdir -p "$(dirname "$PACKAGES_RECORD")"
    touch "$PACKAGES_RECORD"

    if (( ${#new[@]} )); then
        printf '%s\n' "${new[@]}" >> "$PACKAGES_RECORD"
        sort -u -o "$PACKAGES_RECORD" "$PACKAGES_RECORD"
        info "Newly installed: ${new[*]}"
    fi

    find_modem
}


# A modem that was plugged in before ModemManager was installed is not
# found until its ports are tagged for ModemManager (udev rules that
# came with the package) and ModemManager looks again. Without this it
# only shows up after a reboot.
find_modem() {

    command -v mmcli >/dev/null || return 0

    # No modem plugged in (USB vendors of the usual 4G/5G modules).
    cat /sys/bus/usb/devices/*/idVendor 2>/dev/null \
        | grep -qxiE '2c7c|1199|1e0e|05c6|2cb7|12d1|19d2|1bc7|413c' || return 0

    if mmcli -L 2>/dev/null | grep -q "/Modem/"; then
        return 0
    fi

    info "Looking for the modem (ModemManager)..."

    # Only the modem's own ports: Ethernet and Wi-Fi stay untouched.
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --action=add --subsystem-match=usbmisc --subsystem-match=tty 2>/dev/null || true
    udevadm trigger --action=add --subsystem-match=net --sysname-match='wwan*' 2>/dev/null || true
    udevadm settle --timeout=15 2>/dev/null || true

    systemctl restart ModemManager 2>/dev/null || true

    local _
    for _ in $(seq 1 30); do
        if mmcli -L 2>/dev/null | grep -q "/Modem/"; then
            info "Modem found."
            return 0
        fi
        sleep 1
    done

    warn "ModemManager does not see the modem yet. If the Modem page stays empty, reboot once."
}


resolves() {
    getent hosts github.com >/dev/null 2>&1 || getent hosts deb.debian.org >/dev/null 2>&1
}


keep_name_resolution() {

    step "Keeping the router's own name resolution"

    # Installing dnsmasq makes it the system's DNS server (it registers
    # 127.0.0.1 with openresolv after starting). On a router it serves
    # the LAN: the router asks its upstream DNS itself, so turning DNS
    # off on the dashboard (or dnsmasq without upstream servers right
    # after the install, as on a fresh Debian) never breaks its own
    # lookups. dnsmasq still answers on 127.0.0.1 (the dashboard's DNS
    # checks use that); only the registration is switched off.
    mkdir -p "$(dirname "$DNSMASQ_DROPIN")"

    printf '%s\n' \
        "# Chardsoft Router OS: dnsmasq serves the LAN; it does not become the" \
        "# router's own DNS server (no start-resolvconf)." \
        "[Service]" \
        "ExecStartPost=" > "$DNSMASQ_DROPIN"

    # Debian starts dnsmasq with -r /run/dnsmasq/resolv.conf, a file only
    # the resolvconf package writes. With openresolv (Raspberry Pi OS,
    # NetworkManager) it never exists, so dnsmasq had no upstream
    # servers and refused every lookup from the devices until servers
    # were saved on the DNS page. It also reads the router's own
    # servers (modem / ISP) now; servers saved on the DNS page replace
    # them (no-resolv). Its own addresses in there are skipped.
    printf '%s\n' \
        "# Chardsoft Router OS: the router's own DNS servers (modem / ISP)" \
        "# for the devices, unless the DNS page names others." \
        "resolv-file=/etc/resolv.conf" > "$DNSMASQ_UPSTREAM"

    systemctl daemon-reload

    if command -v resolvconf >/dev/null; then
        resolvconf -d lo.dnsmasq 2>/dev/null || true
    fi

    systemctl restart dnsmasq 2>/dev/null || true
    sleep 2

    if resolves; then
        info "OK"
        return
    fi

    # The network was set up before openresolv took over resolv.conf:
    # give it the servers that worked before (until the next reboot,
    # when the network registers them itself).
    if [[ -n $SAVED_NAMESERVERS ]] && command -v resolvconf >/dev/null; then

        local servers
        mapfile -t servers <<< "$SAVED_NAMESERVERS"

        printf 'nameserver %s\n' "${servers[@]}" | resolvconf -a chardsoft-installer
        sleep 1

        if resolves; then
            info "Restored the DNS servers from before the install: ${servers[*]}"
            return
        fi
    fi

    die "Name resolution does not work after installing the packages (see /etc/resolv.conf)."
}


# git as root in our own root-owned clone: no hooks, and no complaint
# about the folder's owner.
git_root() {
    git -c safe.directory="$INSTALL_DIR" -c core.hooksPath=/dev/null "$@"
}


get_code() {

    step "Getting Chardsoft Router OS"

    local here=""

    # Started as a file from a clone (not piped from curl): install that one.
    if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
        here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    fi

    if [[ -n $here && -f $here/backend/app.py && $here != "$INSTALL_DIR" ]]; then
        INSTALL_DIR=$here
        MANAGED=0
        info "Using this copy: $INSTALL_DIR"
        warn "Its code stays writable by its owner, and the System page cannot update it."
        warn "For a router in use, install with the curl command instead (root-owned copy in /opt)."
        return
    fi

    # The code must belong to root: the installer and updater run parts of
    # it as root, and the app (which runs as $APP_USER) must not be able to
    # change them. A copy the app user owns (older installers) is not
    # trusted, not even for git: it is moved aside and cloned fresh.
    if [[ -d $INSTALL_DIR/.git ]] && [[ -n $(find "$INSTALL_DIR" ! -user root -print -quit) ]]; then
        local old
        old="$INSTALL_DIR.user-owned-$(date +%Y%m%d-%H%M%S)"
        warn "$INSTALL_DIR belongs to $APP_USER; moving it to $old and getting a fresh copy."
        mv "$INSTALL_DIR" "$old"
    fi

    if [[ -d $INSTALL_DIR/.git ]]; then
        info "Updating $INSTALL_DIR ($BRANCH)"
        git_root -C "$INSTALL_DIR" remote set-url origin "$REPO"
        git_root -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
        git_root -C "$INSTALL_DIR" reset --quiet --hard FETCH_HEAD
        return
    fi

    [[ -e $INSTALL_DIR ]] && die "$INSTALL_DIR exists but is not a Chardsoft Router OS clone."

    git_root clone --quiet --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
    chmod -R go-w "$INSTALL_DIR"
}


setup_python() {

    step "Setting up the Python environment"

    info "Python $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

    # Root-owned like the code (see get_code); an own clone keeps its owner.
    local as=(env)
    (( MANAGED )) || as=(sudo -H -u "$APP_USER")

    if [[ ! -x $INSTALL_DIR/.venv/bin/python ]]; then
        "${as[@]}" python3 -m venv "$INSTALL_DIR/.venv"
    fi

    "${as[@]}" "$INSTALL_DIR/.venv/bin/pip" install -q --root-user-action=ignore --upgrade pip
    "${as[@]}" "$INSTALL_DIR/.venv/bin/pip" install -q --root-user-action=ignore -r "$INSTALL_DIR/requirements.txt"
}


setup_defaults() {

    step "Writing the default settings (/etc/chardsoft-router-os)"

    mkdir -p "$STATE_DIR"
    chown "$APP_USER:$APP_GROUP" "$STATE_DIR"

    # Existing defaults are kept (local edits survive updates).
    "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/backend/install_defaults.py"

    # Read by the updater and the System page.
    "$INSTALL_DIR/.venv/bin/python" - "$INSTALL_RECORD" "$REPO" "$BRANCH" "$INSTALL_DIR" "$MANAGED" "$APP_USER" <<'PY'
import json, sys
path, repo, branch, folder, managed, user = sys.argv[1:]
with open(path, "w") as f:
    json.dump({"repo": repo, "branch": branch, "dir": folder, "managed": managed == "1", "user": user}, f, indent=2)
PY
    chmod 644 "$INSTALL_RECORD"

    # Everything below belongs to the app user. Repairs installs where an
    # older installer left root-owned files there (e.g. system/).
    chown -R "$APP_USER:$APP_GROUP" "$STATE_DIR"

    if [[ -d /tmp/chardsoft-router-os ]]; then
        chown -R "$APP_USER:$APP_GROUP" /tmp/chardsoft-router-os
    fi

    # No longer read since the setup page exists (no default login).
    rm -f /etc/chardsoft-router-os/users.json
}


# Country codes with names (tzdata, on every Raspberry Pi OS).
COUNTRY_LIST=/usr/share/zoneinfo/iso3166.tab


is_country() {

    [[ $1 =~ ^[A-Z]{2}$ ]] || return 1

    # Without the list, any two letters are accepted.
    [[ -r $COUNTRY_LIST ]] || return 0

    grep -q "^$1"$'\t' "$COUNTRY_LIST"
}


# Every country with its code, sorted by name, in columns.
show_countries() {

    [[ -r $COUNTRY_LIST ]] || return 0

    local width
    width=$(stty size 2>/dev/null < /dev/tty | awk '{print $2}')
    [[ $width =~ ^[0-9]+$ ]] || width=100

    # Columns of 30 characters, filled top to bottom.
    awk -F'\t' '/^[A-Z]/ {print $1 "  " substr($2, 1, 26)}' "$COUNTRY_LIST" \
        | LC_ALL=C.UTF-8 sort -k2 \
        | awk -v cols="$(( (width - 4) / 30 > 0 ? (width - 4) / 30 : 1 ))" '
            { line[NR] = $0 }
            END {
                rows = int((NR + cols - 1) / cols)
                for (r = 1; r <= rows; r++) {
                    out = "    "
                    for (c = 0; c < cols; c++) {
                        i = r + c * rows
                        if (i <= NR) out = out sprintf("%-30s", line[i])
                    }
                    sub(/ +$/, "", out)
                    print out
                }
            }'
}


setup_country() {

    step "Wi-Fi country"

    if [[ -z $COUNTRY ]]; then

        # No default: the country decides which channels and transmit
        # power are legal, so it must be the user's own choice. Only a
        # country already set on this system is used without asking.
        local current=""

        if command -v raspi-config >/dev/null; then
            current=$(raspi-config nonint get_wifi_country 2>/dev/null || true)
        fi

        if [[ $ASSUME_YES -eq 1 || ! -r /dev/tty ]]; then

            is_country "${current^^}" \
                || die "No Wi-Fi country set. Run again with --country XX (e.g. --country US)."

            COUNTRY=$current

        else

            info "Wi-Fi stays off until a country is set: it decides the allowed channels."
            info "Country codes:"
            echo
            show_countries
            echo

            while true; do

                read -r -p "    Your country code (two letters, e.g. US): " COUNTRY < /dev/tty || true
                COUNTRY=${COUNTRY^^}
                COUNTRY=${COUNTRY//[[:space:]]/}

                is_country "$COUNTRY" && break

                warn "\"$COUNTRY\" is not a country code from the list above."
            done
        fi
    fi

    COUNTRY=${COUNTRY^^}

    is_country "$COUNTRY" || die "Invalid country code: $COUNTRY"

    # Sets the regulatory domain and unblocks Wi-Fi on Raspberry Pi OS.
    if command -v raspi-config >/dev/null; then
        raspi-config nonint do_wifi_country "$COUNTRY" || warn "raspi-config could not set the country."
    else
        iw reg set "$COUNTRY" || true
        rfkill unblock wlan || true
    fi

    # The default for the setup Wi-Fi and the WiFi page.
    "$INSTALL_DIR/.venv/bin/python" - "$COUNTRY" <<'PY'
import json, sys
path = "/etc/chardsoft-router-os/wifi.json"
with open(path) as f:
    data = json.load(f)
data["country"] = sys.argv[1]
with open(path, "w") as f:
    json.dump(data, f, indent=2)
PY

    info "Country: $COUNTRY"
}


setup_sudo() {

    step "Checking permissions"

    # The root helper and the scripts it may run, root-owned outside the
    # app's folder (which the app can write): the app gets root only
    # through checked commands (deploy/root-helper).
    install -d -o root -g root -m 755 "$HELPER_DIR"

    local file
    for file in root-helper update.sh install-apps.sh modem-usb-mode.sh; do
        install -o root -g root -m 755 "$INSTALL_DIR/deploy/$file" "$HELPER_DIR/$file"
    done

    local tmp
    tmp=$(mktemp)

    {
        echo "# Chardsoft Router OS: the app runs system commands only through"
        echo "# the root helper, which checks each one (sudo -n)."
        echo "$APP_USER ALL=(root) NOPASSWD: $HELPER_DIR/root-helper"
    } > "$tmp"

    if visudo -cf "$tmp" >/dev/null; then
        install -m 440 "$tmp" "$SUDOERS_FILE"
        info "Added $SUDOERS_FILE for $APP_USER (root helper only)."
    else
        warn "Could not create a valid sudoers file; the app cannot change system settings."
    fi

    rm -f "$tmp"

    # Raspberry Pi OS gives its first user sudo for everything without a
    # password; the app then runs with that too.
    if sudo -u "$APP_USER" sudo -n -l 2>/dev/null | grep -q "NOPASSWD: ALL"; then
        warn "$APP_USER also has passwordless sudo for everything (Raspberry Pi OS default)."
        warn "The app would then not be limited to the root helper."
    fi
}


# Quectel modems in MBIM mode can drop every received packet on Linux
# (cdc_mbim): mobile data "connects" but nothing comes back. QMI is
# what Quectel recommends for Linux. CHARDSOFT_MODEM_MODE=keep leaves it.
modem_mode() {

    [[ ${CHARDSOFT_MODEM_MODE:-auto} == keep ]] && return 0

    local mode
    mode=$("$INSTALL_DIR/deploy/modem-usb-mode.sh" status 2>/dev/null || echo none)

    [[ $mode == mbim ]] || return 0

    grep -qxi 2c7c /sys/bus/usb/devices/*/idVendor 2>/dev/null || return 0

    step "Modem: switching to QMI mode"

    info "Quectel modems lose their received data in MBIM mode on Linux."

    if "$INSTALL_DIR/deploy/modem-usb-mode.sh" qmi | sed 's/^/    /'; then
        info "Switch back with: sudo $INSTALL_DIR/deploy/modem-usb-mode.sh mbim"
    else
        warn "The modem could not be switched; the Modem page offers it again."
    fi
}


check_ports() {

    step "Checking ports"

    # dnsmasq serves DNS (53) and DHCP (67); anything else there clashes.
    local other
    other=$(ss -H -lunp 2>/dev/null | grep -E ':(53|67) ' | grep -v dnsmasq || true)

    if [[ -n $other ]]; then
        warn "Another program uses port 53 or 67 (dnsmasq will clash with it):"
        echo "$other" | sed 's/^/      /' >&2
    fi

    # Caddy serves the dashboard on 80 and 443 (e.g. not Apache or nginx).
    local web
    web=$(ss -H -ltnp 2>/dev/null | grep -E ':(80|443) ' | grep -v caddy || true)

    if [[ -n $web ]]; then
        warn "Another program uses port 80 or 443 (Caddy needs them for the dashboard):"
        echo "$web" | sed 's/^/      /' >&2
    fi

    if [[ -z $other && -z $web ]]; then
        info "OK"
    fi
}


is_set_up() {

    [[ -s $STATE_DIR/users.json ]]
}


# Whether an SSH session reaches this Pi over its Wi-Fi. (sudo drops
# $SSH_CLIENT, so look at the open connections instead.)
ssh_over_wifi() {

    local address interface

    for address in $(
        ss -Htn state established '( sport = :22 )' 2>/dev/null \
            | awk '{print $3}' \
            | sed -E 's/:[0-9]+$//; s/^\[//; s/\]$//; s/^::ffff://'
    ); do

        interface=$(ip -o addr show | awk -v a="$address" '$4 ~ "^"a"/" {print $2; exit}')

        if [[ $interface == wl* ]]; then
            return 0
        fi

    done

    return 1
}


show_setup_wifi() {

    sudo -H -u "$APP_USER" \
        "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/backend/setup_wifi.py" || true
}


# Creates the setup Wi-Fi's name and password now, so they can be shown
# before the service starts (it uses the same ones).
prepare_setup_wifi() {

    (
        cd "$INSTALL_DIR/backend"
        sudo -H -u "$APP_USER" "$INSTALL_DIR/.venv/bin/python" -c \
            'from services.setup_network import get_credentials; get_credentials()' >/dev/null
    )
}


start_service() {

    step "Starting the service"

    SUDO_USER=$APP_USER "$INSTALL_DIR/deploy/install-service.sh"
}


has_wifi() {

    local dev

    for dev in /sys/class/net/*; do
        [[ -d $dev/wireless ]] && return 0
    done

    return 1
}


wait_for_setup_wifi() {

    local _

    for _ in $(seq 1 30); do
        # One access point unit per radio: chardsoft-hostapd@wlan0, ...
        systemctl list-units --state=active --plain --no-legend 'chardsoft-hostapd@*' 2>/dev/null \
            | grep -q . && return 0
        sleep 1
    done

    return 1
}


# The router's LAN address for messages: br0, Ethernet, then Wi-Fi; not
# the internet side (default route) unless nothing else has an address.
# Not `hostname -I`, whose first address may be Docker's (Apps Addon).
lan_address() {

    local wan candidates
    wan=$(ip route show default 2>/dev/null | awk '{print $5; exit}')

    candidates=$(
        ip -4 -o addr show scope global 2>/dev/null \
            | awk '{print $2, $4}' \
            | grep -Ev '^(docker|br-|veth|wg|ovpn|tun|tap|virbr|wwan|clat|lo)' \
            | awk '{ split($2, a, "/"); p = ($1 == "br0") ? 0 : ($1 ~ /^(eth|en)/) ? 1 : 2; print p, $1, a[1] }' \
            | sort -k1,1n -k2,2
    )

    { grep -v " $wan " <<< "$candidates" || true; echo "$candidates"; } \
        | awk 'NF == 3 { print $3; exit }'
}


finish() {

    local address
    address=$(lan_address)

    if is_set_up; then

        # The new code, service file and root helper only run after this.
        start_service

        step "Done"
        info "Chardsoft Router OS is updated and running."
        info "Dashboard: http://$address/"
        return
    fi

    # No Wi-Fi card (e.g. a VM): set up from a computer on the LAN.
    if ! has_wifi; then

        start_service

        step "Set up your router"
        info "This router has no Wi-Fi, so there is no setup Wi-Fi."
        info "Open the setup page from a computer on the LAN:"
        info "  http://chardsoft-router.local/setup  or  http://$address/setup"
        return
    fi

    prepare_setup_wifi

    if ssh_over_wifi; then

        # Starting the service turns wlan0 into the setup Wi-Fi, which
        # ends this SSH session: show everything first.
        step "Set up your router"
        warn "You are connected over Wi-Fi. This session ends when the setup Wi-Fi starts."
        show_setup_wifi
        info "Scan the QR code with your phone. Starting in 15 seconds..."
        sleep 15

        start_service
        return
    fi

    start_service

    step "Set up your router"

    # The QR code only when the setup Wi-Fi really runs.
    if ! wait_for_setup_wifi; then
        warn "The setup Wi-Fi did not start (journalctl -u chardsoft-router-os shows why)."
        info "Set up the router from a computer on the LAN instead:"
        info "  http://chardsoft-router.local/setup  or  http://$address/setup"
        return
    fi

    info "The setup Wi-Fi is on. Scan the QR code with your phone;"
    info "the setup page opens by itself."

    show_setup_wifi

    info "Show this again any time before setup with:"
    info "  $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/backend/setup_wifi.py"
}


main() {

    parse_args "$@"

    check_system
    migrate_names
    install_packages
    keep_name_resolution
    get_code
    setup_python
    setup_defaults
    setup_country
    setup_sudo
    modem_mode
    check_ports
    finish
}


# Everything above only defines functions: a partly downloaded script
# (curl | sudo bash) never runs anything.
main "$@"
