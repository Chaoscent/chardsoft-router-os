"""
Version and updates (System page).

The installed version is VERSION plus the commit of the installer's
clone, read from .git directly: the code belongs to root, and git as the
app user refuses to work in a folder it doesn't own.

"Check for updates" asks GitHub for the newest commit of the installed
branch. "Update now" starts deploy/update.sh through the root helper; it
runs as its own systemd job and reports in /run/chardsoft-update.

Release channels: stable (release/<edition>, finished releases) and
development (dev/<edition>, every change). The branch is kept in the
install record; deploy/update.sh changes it. Stable never goes back to an
older version: the update waits until Stable is newer.
"""

import json
import os
import re
import urllib.error
import urllib.request

from services.config import DEFAULTS_DIR

from services.network import privileged, run_command


APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INSTALL_RECORD = DEFAULTS_DIR / "install.json"

STATUS_FILE = "/run/chardsoft-update/status.json"

UPDATE_SCRIPT = "/usr/local/libexec/chardsoft-router-os/update.sh"

GITHUB_RE = re.compile(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(\.git)?/?$")

TIMEOUT = 10

CHANNELS = ("stable", "development")

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$")


def channel_of(branch):
    """release/core -> "stable", dev/core -> "development", else None."""

    branch = str(branch or "")

    if branch.startswith("release/"):
        return "stable"

    if branch.startswith("dev/"):
        return "development"

    return None


def version_key(version):
    """Sortable: 1.0.0-beta < 1.0.0 < 1.0.1. None when not a version."""

    match = VERSION_RE.match(str(version or "").strip())

    if not match:
        return None

    major, minor, patch, pre = match.groups()

    return (int(major), int(minor), int(patch), pre is None, pre or "")


def version_older(a, b):
    """True when version a is older than b (unknown versions: False)."""

    ka, kb = version_key(a), version_key(b)

    return bool(ka and kb and ka < kb)


def read_version():

    try:
        with open(os.path.join(APP_ROOT, "VERSION")) as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "unknown"


def read_commit(root=APP_ROOT):
    """The checked-out commit (full hash) from .git, or None."""

    git = os.path.join(root, ".git")

    try:
        with open(os.path.join(git, "HEAD")) as f:
            head = f.read().strip()
    except OSError:
        return None

    if not head.startswith("ref: "):
        return head if re.fullmatch(r"[0-9a-f]{40}", head) else None

    ref = head[5:]

    try:
        with open(os.path.join(git, ref)) as f:
            return f.read().strip()
    except OSError:
        pass

    # After git gc, refs live in packed-refs.
    try:
        with open(os.path.join(git, "packed-refs")) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        pass

    return None


def install_record():

    try:
        with open(INSTALL_RECORD) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_update_info():
    """What the System page shows before any check."""

    record = install_record()
    commit = read_commit(record.get("dir") or APP_ROOT)

    managed = record.get("managed") is True

    return {
        "version": read_version(),
        "commit": commit,
        "branch": record.get("branch"),
        "channel": channel_of(record.get("branch")),
        "repo": record.get("repo"),
        "managed": managed,
        "can_update": managed and os.path.isfile(UPDATE_SCRIPT),
        "reason": None if managed else (
            "Installed from an own clone: update it with git pull and install.sh."
            if record else "No install record: install with install.sh to get updates here."
        ),
        "status": get_update_status()
    }


def _get_json(url):

    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "chardsoft-router-os"
    })

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read(512 * 1024))


def _get_text(url):

    request = urllib.request.Request(url, headers={"User-Agent": "chardsoft-router-os"})

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read(4096).decode("utf-8", "replace")


def remote_version(owner, name, commit):
    """VERSION of a commit on GitHub, or None (no such file: empty branch)."""

    try:
        return _get_text(f"https://raw.githubusercontent.com/{owner}/{name}/{commit}/VERSION").strip() or None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def check_for_update():
    """(True, info) with the newest commit of the branch, or (False, message)."""

    info = get_update_info()

    if not info["branch"] or not info["repo"]:
        return False, info["reason"] or "Unknown branch."

    match = GITHUB_RE.search(info["repo"])

    if not match:
        return False, "Updates are only checked for GitHub repositories."

    owner, name = match.group(1), match.group(2)

    url = f"https://api.github.com/repos/{owner}/{name}/commits/{urllib.request.quote(info['branch'], safe='')}"

    try:
        data = _get_json(url)
    except urllib.error.HTTPError as error:
        if error.code == 403:
            return False, "GitHub refused the check for now (rate limit). Try again in an hour."
        return False, f"GitHub answered {error.code}."
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False, "GitHub could not be reached. Is the router online?"

    latest = data.get("sha") if isinstance(data, dict) else None

    if not isinstance(latest, str) or not re.fullmatch(r"[0-9a-f]{40}", latest):
        return False, "GitHub sent an unexpected answer."

    commit = data.get("commit") or {}

    latest_version = remote_version(owner, name, latest)

    available = latest != info["commit"]
    note = None

    # Stable: nothing before the first release, never an older version.
    if info["channel"] == "stable" and available:
        if not latest_version:
            available, note = False, "There is no stable release yet."
        elif version_older(latest_version, info["version"]):
            available = False
            note = (f"Stable ({latest_version}) is older than the installed version "
                    f"({info['version']}). Stable updates come once it is newer.")

    return True, {
        **info,
        "latest": latest,
        "latest_version": latest_version,
        "note": note,
        "latest_message": str((commit.get("message") or "").splitlines()[0] if commit.get("message") else "")[:200],
        "latest_date": str((commit.get("committer") or {}).get("date") or ""),
        "update_available": available
    }


def start_update():

    info = get_update_info()

    if not info["can_update"]:
        return False, info["reason"] or "The updater is not installed (run install.sh once)."

    if (info["status"] or {}).get("state") == "running":
        return False, "An update is already running."

    ok, result = run_command(privileged([UPDATE_SCRIPT, "start"]))

    if not ok:
        return False, result or "The update could not be started."

    return True, "Update started. The dashboard restarts during the update."


def set_channel(channel):

    if channel not in CHANNELS:
        return False, "Unknown channel."

    info = get_update_info()

    if not info["can_update"]:
        return False, info["reason"] or "The updater is not installed (run install.sh once)."

    if not info["channel"]:
        return False, f"The branch {info['branch']} has no release channel."

    if info["channel"] == channel:
        return True, "No change."

    if (info["status"] or {}).get("state") == "running":
        return False, "An update is running; change the channel afterwards."

    ok, result = run_command(privileged([UPDATE_SCRIPT, "channel", channel]))

    if not ok:
        return False, result or "The channel could not be changed."

    return True, f"Channel: {channel}."


def get_update_status():

    try:
        with open(STATUS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
