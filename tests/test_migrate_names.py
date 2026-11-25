"""
install.sh's migrate_names: an install from before the rename (chaos ->
chardsoft) moves to the new names once. Runs the shell function in a test
folder (CHARDSOFT_MIGRATE_ROOT) with systemctl, iptables and nmcli faked.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import os
import subprocess
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def migrate_function():
    """The function's source, cut out of install.sh."""

    with open(os.path.join(ROOT, "install.sh")) as f:
        text = f.read()

    start = text.index("migrate_names() {")
    end = text.index("\n}\n", start) + 3

    return text[start:end]


HARNESS = textwrap.dedent(r'''
    set -euo pipefail

    LOG=$CHARDSOFT_MIGRATE_ROOT/calls.log
    STATE_DIR=/var/lib/chardsoft-router-os
    INSTALL_DIR=/opt/chaos-router-os

    step() { :; }
    info() { :; }
    warn() { echo "WARN $*" >> "$LOG"; }

    systemctl() {
        echo "systemctl $*" >> "$LOG"
        if [[ $1 == list-units ]]; then
            echo "chaos-hostapd@wlan0.service loaded active running Access point"
            echo "chaos-hostapd@wlan1.service loaded active running Access point"
        fi
        # A unit that is not there fails, as the real one does.
        [[ $1 == disable && $3 == chaos-clat ]] && return 5
        return 0
    }

    # filter: two chains hooked into FORWARD (one rule with a quoted
    # comment) and a jump from one old chain to the other; nat: one.
    iptables() {
        echo "iptables $*" >> "$LOG"
        if [[ $3 == -S ]]; then
            case $2 in
                filter) printf '%s\n' '-P FORWARD DROP' '-N CHAOS-ACCT' '-N CHAOS-PAUSE' \
                            '-A FORWARD -j CHAOS-ACCT' \
                            '-A FORWARD -m comment --comment "paused devices" -j CHAOS-PAUSE' \
                            '-A FORWARD -i eth0 -j ACCEPT' \
                            '-A CHAOS-PAUSE -j CHAOS-ACCT' ;;
                nat) printf '%s\n' '-N CHAOS-NAT' '-A POSTROUTING -j CHAOS-NAT' ;;
                mangle) ;;
            esac
        fi
        return 0
    }

    # No IPv6 tables on this system: skipped.
    ip6tables() { [[ $3 == -S ]] && return 1; return 0; }

    nmcli() {
        echo "nmcli $*" >> "$LOG"
        if [[ $1 == -t ]]; then
            printf '%s\n' 'u1:chaos-modem' 'u2:chaos-lan' 'u3:chaos-lan-eth0' 'u4:Wired connection 1'
        fi
        return 0
    }
''')


class MigrateNamesTests(unittest.TestCase):

    def setUp(self):

        self.root = tempfile.mkdtemp(prefix="chardsoft-migrate-")

        for path, text in {
            "etc/chaos-router-os/install.json": '{"dir": "/opt/chaos-router-os"}',
            "var/lib/chaos-router-os/wifi.json": '{"networks": {}}',
            "tmp/chaos-router-os/boot.json": "{}",
            "opt/chaos-router-os/backend/app.py": "",
            "opt/chaos-router-os/.venv/bin/python": "",
            "etc/systemd/system/chaos-router-os.service": "",
            "etc/systemd/system/chaos-hostapd@.service": "",
            "etc/systemd/system/dnsmasq.service.d/chaos-router-os.conf": "",
            "etc/hostapd/chaos-wlan0.conf": "",
            "etc/hostapd/hostapd.conf": "not ours",
            "etc/dnsmasq.d/chaos-router-dhcp.conf": "",
            "etc/dnsmasq.d/other.conf": "not ours",
            "etc/caddy/chaos-apps/kuma.caddy": "",
            "etc/caddy/Caddyfile.before-chaos": "the user's own",
            "etc/openvpn/server/chaos.conf": "",
            "etc/openvpn/server/chaos-pki/ca.crt": "CA",
            "etc/openvpn/server/chaos-crl.pem": "",
            "etc/openvpn/chaos-nat.sh": "",
            "etc/sudoers.d/chaos-router-os": "",
            "usr/local/libexec/chaos-router-os/root-helper": "",
        }.items():
            full = os.path.join(self.root, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(text)

    def run_migration(self):

        script = HARNESS + migrate_function() + '\nmigrate_names\necho "INSTALL_DIR=$INSTALL_DIR" >> "$LOG"\n'

        result = subprocess.run(
            ["bash", "-c", script],
            env={**os.environ, "CHARDSOFT_MIGRATE_ROOT": self.root},
            capture_output=True, text=True, timeout=30
        )

        self.assertEqual(result.returncode, 0, result.stderr)

        with open(os.path.join(self.root, "calls.log")) as f:
            return f.read().splitlines()

    def exists(self, path):

        return os.path.exists(os.path.join(self.root, path))

    def read(self, path):

        with open(os.path.join(self.root, path)) as f:
            return f.read()

    def test_moves_settings_and_code(self):

        log = self.run_migration()

        self.assertEqual(self.read("etc/chardsoft-router-os/install.json"), '{"dir": "/opt/chaos-router-os"}')
        self.assertEqual(self.read("var/lib/chardsoft-router-os/wifi.json"), '{"networks": {}}')
        self.assertFalse(self.exists("etc/chaos-router-os"))
        self.assertFalse(self.exists("var/lib/chaos-router-os"))
        # Running settings dropped: rebuilt as after a boot.
        self.assertFalse(self.exists("tmp/chaos-router-os"))
        # The code moved, its Python environment is built anew.
        self.assertTrue(self.exists("opt/chardsoft-router-os/backend/app.py"))
        self.assertFalse(self.exists("opt/chardsoft-router-os/.venv"))
        self.assertIn("INSTALL_DIR=/opt/chardsoft-router-os", log)

    def test_services_and_files(self):

        log = self.run_migration()

        for unit in ("chaos-hostapd@wlan0.service", "chaos-hostapd@wlan1.service",
                     "chaos-router-os", "chaos-router-mdns", "chaos-clat", "openvpn-server@chaos"):
            self.assertIn(f"systemctl disable --now {unit}", log)

        for gone in ("etc/systemd/system/chaos-router-os.service", "etc/systemd/system/chaos-hostapd@.service",
                     "etc/systemd/system/dnsmasq.service.d/chaos-router-os.conf",
                     "etc/hostapd/chaos-wlan0.conf", "etc/dnsmasq.d/chaos-router-dhcp.conf",
                     "etc/openvpn/server/chaos.conf", "etc/openvpn/chaos-nat.sh",
                     "etc/sudoers.d/chaos-router-os", "usr/local/libexec/chaos-router-os"):
            self.assertFalse(self.exists(gone), gone)

        # Other programs' files stay.
        self.assertEqual(self.read("etc/hostapd/hostapd.conf"), "not ours")
        self.assertEqual(self.read("etc/dnsmasq.d/other.conf"), "not ours")

        # Kept under the new names: app routes, the user's Caddyfile, the VPN keys.
        self.assertTrue(self.exists("etc/caddy/chardsoft-apps/kuma.caddy"))
        self.assertEqual(self.read("etc/caddy/Caddyfile.before-chardsoft"), "the user's own")
        self.assertEqual(self.read("etc/openvpn/server/chardsoft-pki/ca.crt"), "CA")
        self.assertTrue(self.exists("etc/openvpn/server/chardsoft-crl.pem"))

    def test_firewall_chains(self):

        log = self.run_migration()

        # The jumps from FORWARD / POSTROUTING out (the quoted comment
        # survives as one argument), other rules stay.
        self.assertIn("iptables -t filter -D FORWARD -j CHAOS-ACCT", log)
        self.assertIn("iptables -t filter -D FORWARD -m comment --comment paused devices -j CHAOS-PAUSE", log)
        self.assertIn("iptables -t nat -D POSTROUTING -j CHAOS-NAT", log)
        self.assertNotIn("iptables -t filter -D FORWARD -i eth0 -j ACCEPT", log)
        # A jump inside an old chain goes with its chain.
        self.assertFalse([line for line in log if "-D CHAOS-" in line])

        for chain in ("CHAOS-ACCT", "CHAOS-PAUSE"):
            self.assertIn(f"iptables -t filter -F {chain}", log)
            self.assertIn(f"iptables -t filter -X {chain}", log)

        self.assertIn("iptables -t nat -X CHAOS-NAT", log)

    def test_network_manager_names(self):

        log = self.run_migration()

        self.assertIn("nmcli connection modify u1 connection.id chardsoft-modem", log)
        self.assertIn("nmcli connection modify u2 connection.id chardsoft-lan", log)
        self.assertIn("nmcli connection modify u3 connection.id chardsoft-lan-eth0", log)
        self.assertFalse([line for line in log if "modify u4" in line])

    def test_runs_once(self):

        self.run_migration()
        os.remove(os.path.join(self.root, "calls.log"))

        # Nothing old left: the second run does nothing at all.
        self.assertEqual(self.run_migration(), ["INSTALL_DIR=/opt/chardsoft-router-os"])

    def test_new_install_untouched(self):

        empty = tempfile.mkdtemp(prefix="chardsoft-migrate-new-")
        self.root = empty

        self.assertEqual(self.run_migration(), ["INSTALL_DIR=/opt/chardsoft-router-os"])


if __name__ == "__main__":
    unittest.main()
