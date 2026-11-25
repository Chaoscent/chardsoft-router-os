"""
Rules of deploy/root-helper: the app's real configs pass, anything that
would run programs or touch other files as root is refused.

    cd backend && ../.venv/bin/python -m unittest discover -s ../tests
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

# The services must not touch the real /etc or /var/lib.
SANDBOX = tempfile.mkdtemp(prefix="chardsoft-test-")

for name, folder in (("CHARDSOFT_DEFAULTS_DIR", "etc"), ("CHARDSOFT_STATE_DIR", "var"), ("CHARDSOFT_RUNTIME_DIR", "tmp")):
    os.environ.setdefault(name, os.path.join(SANDBOX, folder))

os.environ.setdefault("CHARDSOFT_SKIP_STARTUP", "1")

loader = importlib.machinery.SourceFileLoader("root_helper", os.path.join(ROOT, "deploy", "root-helper"))
spec = importlib.util.spec_from_loader("root_helper", loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)

from services import apps, dhcp, dns, openvpn, setup_network, wireguard  # noqa: E402


def plan(*argv):
    return helper.plan(list(argv))


class GeneratedConfigs(unittest.TestCase):

    def test_dnsmasq(self):
        helper.check_dnsmasq(dhcp.render_dnsmasq_config(dhcp.DEFAULT_SETTINGS))
        helper.check_dnsmasq(dns.render_dns_config({**dns.DEFAULT_SETTINGS, "dnssec": True}))
        helper.check_dnsmasq(dns.render_dns_config({**dns.DEFAULT_SETTINGS, "upstream_mode": "preset"}))
        helper.check_dnsmasq(setup_network.render_dnsmasq("wlan0"))

    def test_dns_upstream(self):
        # Default: the router's own servers (install.sh's resolv-file),
        # never a third party unless chosen.
        default = dns.render_dns_config(dns.DEFAULT_SETTINGS).splitlines()
        self.assertNotIn("no-resolv", default)
        self.assertFalse([line for line in default if line.startswith("server=")])
        self.assertEqual(dns.describe_upstream(dns.DEFAULT_SETTINGS), "Modem / ISP")
        # A preset replaces them.
        preset = dns.render_dns_config({**dns.DEFAULT_SETTINGS, "upstream_mode": "preset"}).splitlines()
        self.assertIn("no-resolv", preset)
        self.assertIn("server=1.1.1.1", preset)

    def test_caddy(self):
        helper.check_caddy(apps.caddy_snippet("uptime-kuma", "uptime-kuma.chardsoft-router.local", 3001))

    def test_tayga(self):
        from services import clat
        helper.check_tayga(clat.render_config("64:ff9b::/96", "2a01:598:900e:7a75::c1a7", "2a01:598:900e:7a75::c1a6"))
        for bad in ("data-dir /root\n", "tun-device eth0\n", "prefix 64:ff9b::/96; rm -rf /\n", "dynamic-pool 10.0.0.0/8\n"):
            with self.assertRaises(helper.Refused, msg=bad):
                helper.check_tayga(bad)

    def test_clat_unit_and_config_path(self):
        plan("systemctl", "restart", "chardsoft-clat")
        self.assertEqual(helper.writable_kind("/etc/tayga/chardsoft-clat.conf"), "tayga")
        self.assertIsNone(helper.writable_kind("/etc/tayga/other.conf"))

    def test_nat_script(self):
        helper.check_nat_script(openvpn.NAT_SCRIPT_CONTENT)

    def test_openvpn_server(self):
        server = {**openvpn.DEFAULT_SERVER, "enabled": True, "endpoint": "vpn.example.org"}
        helper.check_openvpn(openvpn.render_server_config(server))

    def test_wireguard_server(self):
        data = {
            "server": {**wireguard.DEFAULT_SERVER, "address": "10.8.0.1/24", "listen_port": 51820, "private_key": "k" * 43 + "="},
            "peers": [{"name": "phone", "public_key": "p" * 43 + "=", "psk": "", "preshared_key": "", "address": "10.8.0.2/32", "allowed_ips": "10.8.0.2/32"}]
        }
        helper.check_wireguard(wireguard.render_server_config(data))


class DangerousContent(unittest.TestCase):

    CASES = [
        (helper.check_dnsmasq, "dhcp-script=/tmp/x"),
        (helper.check_dnsmasq, "conf-dir=/home/x"),
        (helper.check_dnsmasq, "enable-tftp\ntftp-root=/"),
        (helper.check_dnsmasq, "log-facility=/etc/cron.d/x"),
        (helper.check_dnsmasq, "conf-file=/tmp/evil"),
        (helper.check_hostapd, "wpa_psk_file=/etc/shadow"),
        (helper.check_hostapd, "ctrl_interface=/etc"),
        (helper.check_wireguard, "[Interface]\nPostUp = bash -c id"),
        (helper.check_wireguard, "[Interface]\nPostUp = iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE; id"),
        (helper.check_wireguard, "[Interface]\nPreUp = true"),
        (helper.check_openvpn, "up /tmp/x.sh"),
        (helper.check_openvpn, "plugin /usr/lib/x.so"),
        (helper.check_openvpn, "script-security 3"),
        (helper.check_openvpn, "ca /etc/shadow"),
        (helper.check_openvpn, "<ca>\nup /tmp/x"),
        (helper.check_caddy, "file_server\nroot * /"),
        (helper.check_caddy, "reverse_proxy 10.0.0.1:80"),
        (helper.check_nat_script, openvpn.NAT_SCRIPT_CONTENT + "id\n"),
    ]

    def test_refused(self):
        for check, text in self.CASES:
            with self.subTest(text=text), self.assertRaises(helper.Refused):
                check(text)

    def test_pki_runs_nothing_of_ours(self):
        for name in ("vars", "openssl-easyrsa.cnf"):
            with self.assertRaises(helper.Refused):
                helper.check_pki("", f"{helper.PKI_DIR}/{name}")


class Commands(unittest.TestCase):

    REFUSED = [
        ("bash", "-c", "id"),
        ("cp", "/etc/shadow", "/tmp/x"),
        ("ip", "netns", "exec", "x", "sh"),
        ("ip", "-batch", "/tmp/x"),
        ("/usr/sbin/iptables", "--modprobe=/tmp/x", "-L"),
        ("sysctl", "-p", "/tmp/x"),
        ("sysctl", "-w", "kernel.core_pattern=|/tmp/x"),
        ("systemctl", "link", "/tmp/x.service"),
        ("systemctl", "restart", "ssh"),
        ("journalctl", "--file=/etc/shadow"),
        ("easyrsa", "--batch", f"--pki-dir={helper.PKI_DIR}", "--vars=/tmp/v", "gen-crl"),
        ("openvpn", "--config", "/tmp/x"),
        ("wg", "set", "wg0", "private-key", "/etc/shadow"),
        ("nmcli", "connection", "import", "type", "openvpn", "file", "/tmp/x"),
        ("hostnamectl", "set-hostname", "a;b"),
        ("date", "-s", "tomorrow"),
        ("date", "-u", "-s", "@1795528404", "+%s"),
        ("date", "-f", "/etc/shadow"),
        ("cat", "/etc/shadow"),
        ("cat", "/etc/hostapd/chardsoft-../../shadow"),
        ("rm", "-rf", "/etc"),
        ("install", "-m", "644", "/tmp/x", "/etc/cron.d/x"),
        ("install", "-m", "4755", "/tmp/x", "/etc/hostapd/chardsoft-wlan0.conf"),
        ("find", helper.PKI_DIR, "-exec", "sh", ";"),
        ("/opt/x/deploy/modem-usb-mode.sh", "foo"),
    ]

    def test_refused(self):
        for argv in self.REFUSED:
            with self.subTest(argv=argv), self.assertRaises(helper.Refused):
                plan(*argv)

    def test_used_by_the_app(self):
        # Only checks the rules: programs missing here (WSL) are fine.
        for argv in (
            ("systemctl", "restart", "chardsoft-hostapd@wlan1.service"),
            ("systemctl", "is-active", "--quiet", "wg-quick@wg0"),
            ("journalctl", "--no-pager", "-o", "json", "-n", "500", "-u", "openvpn-server@*", "-p", "3"),
            ("/usr/sbin/iptables", "-t", "nat", "-C", "POSTROUTING", "-j", "CHARDSOFT-NAT"),
            ("sysctl", "-w", "net.ipv4.ip_forward=1"),
            ("ip", "addr", "add", "10.42.0.1/24", "dev", "wlan1"),
            ("easyrsa", "--batch", f"--pki-dir={helper.PKI_DIR}", "--use-algo=ec", "--curve=prime256v1", "build-client-full", "laptop", "nopass"),
            ("openvpn", "--genkey", "secret", "/etc/openvpn/server/chardsoft-tls-crypt.key"),
            ("find", helper.PKI_DIR, "-mindepth", "1", "-printf", "%y %P\\n"),
            ("hostnamectl", "set-hostname", "chardsoft-router"),
            ("date", "-u", "-s", "@1795528404"),
        ):
            with self.subTest(argv=argv):
                try:
                    plan(*argv)
                except helper.Refused as error:
                    if "is not installed" not in str(error):
                        raise


class Files(unittest.TestCase):

    def setUp(self):

        self.root = tempfile.mkdtemp(dir=SANDBOX)

        os.makedirs(f"{self.root}/etc/hostapd")
        os.makedirs(f"{self.root}/secret")

        with open(f"{self.root}/secret/shadow", "w") as f:
            f.write("root:hash\n")

        self.saved = helper.WRITABLE, helper.DIRECTORIES, helper.REMOVABLE

        helper.WRITABLE = ((f"{self.root}/etc/hostapd/chardsoft-*.conf", "hostapd"), (f"{self.root}/pki/**", "pki"))
        helper.DIRECTORIES = (f"{self.root}/pki", f"{self.root}/pki/**")
        helper.REMOVABLE = (f"{self.root}/pki",)

        self.source = f"{self.root}/source.conf"

        with open(self.source, "w") as f:
            f.write("interface=wlan0\nssid=Test\n")

    def tearDown(self):

        helper.WRITABLE, helper.DIRECTORIES, helper.REMOVABLE = self.saved

    def test_install_writes_mode_and_content(self):

        target = f"{self.root}/etc/hostapd/chardsoft-wlan0.conf"
        plan("install", "-m", "600", self.source, target)

        self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
        self.assertEqual(open(target).read(), open(self.source).read())

    def test_symlink_source_refused(self):

        os.symlink(f"{self.root}/secret/shadow", f"{self.root}/link.conf")

        with self.assertRaises(helper.Refused):
            plan("install", "-m", "600", f"{self.root}/link.conf", f"{self.root}/etc/hostapd/chardsoft-wlan1.conf")

    def test_symlink_target_replaced_not_followed(self):

        target = f"{self.root}/etc/hostapd/chardsoft-wlan2.conf"
        os.symlink(f"{self.root}/secret/shadow", target)

        plan("install", "-m", "600", self.source, target)

        self.assertFalse(os.path.islink(target))
        self.assertEqual(open(f"{self.root}/secret/shadow").read(), "root:hash\n")

    def test_symlinked_folder_refused(self):

        os.makedirs(f"{self.root}/pki")
        os.symlink(f"{self.root}/secret", f"{self.root}/pki/evil")

        with self.assertRaises(helper.Refused):
            plan("install", "-m", "600", self.source, f"{self.root}/pki/evil/x")

        with self.assertRaises(helper.Refused):
            plan("cat", f"{self.root}/pki/evil/shadow")

    def test_remove_does_not_follow_links(self):

        plan("install", "-D", "-m", "600", self.source, f"{self.root}/pki/issued/a.crt")
        os.symlink(f"{self.root}/secret", f"{self.root}/pki/evil")

        plan("rm", "-rf", f"{self.root}/pki")

        self.assertFalse(os.path.exists(f"{self.root}/pki"))
        self.assertTrue(os.path.exists(f"{self.root}/secret/shadow"))


if __name__ == "__main__":
    unittest.main()
