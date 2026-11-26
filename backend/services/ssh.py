"""
SSH access (System page): whether the SSH server runs, whether it takes
passwords, and the keys that may sign in as the account the router runs
as (~/.ssh/authorized_keys of that account).

Changes never cut running SSH sessions: the server is reloaded, not
restarted, and turning it off stops new logins only. Password login can
only go off while a key is there, and the last key cannot be removed
while password login is off, so SSH never locks everyone out.

Password login is set in /etc/ssh/sshd_config.d/00-chardsoft.conf (first
value wins in sshd; 00- comes before the system's own files). The settings
in effect come from `sshd -T`.
"""

import base64
import hashlib
import os
import pwd
import re
import tempfile

from services.network import privileged, run_command

DROP_IN = "/etc/ssh/sshd_config.d/00-chardsoft.conf"

UNIT = "ssh"

KEY_TYPES = (
    "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521", "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
)

MAX_KEYS = 50


def account():
    """The account keys sign in as: the one the router runs as."""

    return pwd.getpwuid(os.getuid()).pw_name


def keys_file():

    return os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".ssh", "authorized_keys")


# -------------------------------------------------------------------
# Keys
# -------------------------------------------------------------------

def parse_key(line):
    """(type, blob, comment) of an authorized_keys line, or None."""

    words = line.strip().split()

    # The key type may follow options (from="...",...): find it.
    for i, word in enumerate(words):

        if word not in KEY_TYPES or i + 1 >= len(words):
            continue

        blob = words[i + 1]

        try:
            raw = base64.b64decode(blob, validate=True)
        except ValueError:
            return None

        # The blob starts with its own type name.
        if len(raw) < 4 or raw[4:4 + int.from_bytes(raw[:4], "big")] != word.encode():
            return None

        return word, blob, " ".join(words[i + 2:])

    return None


def fingerprint(blob):
    """SHA256:... as ssh-keygen -l prints it."""

    digest = hashlib.sha256(base64.b64decode(blob)).digest()

    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _read_lines():

    try:
        with open(keys_file()) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def _write_lines(lines):

    path = keys_file()
    folder = os.path.dirname(path)

    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)

    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".authorized_keys-")

    try:
        with os.fdopen(fd, "w") as f:
            f.write("".join(line + "\n" for line in lines))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def list_keys():

    keys = []

    for line in _read_lines():

        key = parse_key(line)

        if key:
            kind, blob, comment = key
            keys.append({"type": kind, "comment": comment, "fingerprint": fingerprint(blob)})

    return keys


def add_key(text):

    text = str(text or "").strip()

    if "\n" in text or "\r" in text:
        return False, "Paste one key (one line)."

    key = parse_key(text)

    if not key or text.split()[0] not in KEY_TYPES:
        return False, "That is not a public key. Paste the line from the .pub file, e.g. ssh-ed25519 AAAA... name"

    kind, blob, comment = key
    comment = re.sub(r"[^\w .@+:-]", "", comment)[:100]

    lines = _read_lines()
    known = [parse_key(line) for line in lines]

    if any(k and k[1] == blob for k in known):
        return False, "This key is already there."

    if sum(1 for k in known if k) >= MAX_KEYS:
        return False, f"At most {MAX_KEYS} keys."

    lines.append(" ".join(filter(None, (kind, blob, comment))))

    try:
        _write_lines(lines)
    except OSError as e:
        return False, f"The key could not be saved: {e}"

    return True, f"Key added ({fingerprint(blob)})."


def remove_key(print_):

    lines = _read_lines()
    keep = [line for line in lines if not ((k := parse_key(line)) and fingerprint(k[1]) == print_)]

    if len(keep) == len(lines):
        return False, "That key is not there."

    left = sum(1 for line in keep if parse_key(line))

    if left == 0 and get_status().get("password_login") is False:
        return False, "This is the last key and password login is off: SSH would let nobody in. Turn password login on first."

    try:
        _write_lines(keep)
    except OSError as e:
        return False, f"The key could not be removed: {e}"

    return True, "Key removed."


# -------------------------------------------------------------------
# Server
# -------------------------------------------------------------------

def effective_settings():
    """{setting: value} in effect (sshd -T), lowercase names; {} if unknown."""

    ok, output = run_command(privileged(["sshd", "-T"]), timeout=10)

    if not ok:
        return {}

    settings = {}

    for line in output.splitlines():
        name, _, value = line.partition(" ")
        settings.setdefault(name.lower(), value.strip())

    return settings


def get_status():

    installed = os.path.isfile("/usr/sbin/sshd")

    active, _ = run_command(["systemctl", "is-active", "--quiet", UNIT])
    enabled, _ = run_command(["systemctl", "is-enabled", "--quiet", UNIT])

    settings = effective_settings() if installed else {}

    password = settings.get("passwordauthentication")
    port = settings.get("port", "22")

    return {
        "installed": installed,
        "active": bool(active),
        "enabled": bool(enabled),
        "password_login": None if password is None else password == "yes",
        "root_login": settings.get("permitrootlogin"),
        "port": int(port) if port.isdigit() else 22,
        "account": account(),
        "keys": list_keys()
    }


def set_password_login(on):

    if not on and not list_keys():
        return False, "Add a key first: without password login, SSH only lets keys in."

    content = (
        "# Chardsoft Router OS (System page): password login for SSH.\n"
        f"PasswordAuthentication {'yes' if on else 'no'}\n"
        "KbdInteractiveAuthentication no\n"
    )

    fd, tmp = tempfile.mkstemp(prefix="chardsoft-sshd-")

    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        ok, output = run_command(privileged(["install", "-m", "644", tmp, DROP_IN]))
    finally:
        os.unlink(tmp)

    if not ok:
        return False, output or "The setting could not be written."

    # A config sshd refuses would keep it from starting: take it back.
    ok, output = run_command(privileged(["sshd", "-t"]), timeout=10)

    if not ok:
        run_command(privileged(["rm", "-f", DROP_IN]))
        return False, f"SSH did not accept the setting: {output}"

    active, _ = run_command(["systemctl", "is-active", "--quiet", UNIT])

    if active:
        ok, output = run_command(privileged(["systemctl", "reload", UNIT]))
        if not ok:
            return False, output or "SSH could not be reloaded."

    return True, f"Password login {'on' if on else 'off'}."


def set_enabled(on):
    """On: runs now and at boot. Off: no new logins (open sessions stay)."""

    ok, output = run_command(privileged(["systemctl", "enable" if on else "disable", "--now", UNIT]))

    if not ok:
        return False, output or "SSH could not be changed."

    return True, "SSH is on." if on else "SSH is off. Open SSH sessions stay until they end."

