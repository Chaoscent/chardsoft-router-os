#!/bin/bash
# Chardsoft Router OS: update from the System page.
#
#   update.sh start   starts the update as its own systemd job and returns
#                     (the app restarts during the update; the job goes on)
#   update.sh run     the update itself (what the job runs)
#   update.sh channel stable|development
#                     switches the release channel: release/<edition>
#                     (finished releases) or dev/<edition> (every change)
#
# Installed root-owned next to the root helper, which is the only way the
# app can start it (sudo root-helper update.sh start). It only works on
# the installer's root-owned copy (/etc/chardsoft-router-os/install.json:
# "managed"), never on someone's own clone.
#
# Stable never goes back: a release older than the installed version is
# skipped until a newer one comes (settings are never downgraded).
#
# Steps: back up the settings, fetch the branch, run the new installer
# (which updates packages, Python dependencies, services and the root
# helper), then check that the app answers. If it doesn't, the previous
# version is put back and installed again.
#
# Progress: /run/chardsoft-update/status.json (read by the app), log in
# /run/chardsoft-update/update.log.

set -uo pipefail

RECORD=/etc/chardsoft-router-os/install.json
STATE_DIR=/var/lib/chardsoft-router-os
STATUS_DIR=/run/chardsoft-update
STATUS=$STATUS_DIR/status.json
LOG=$STATUS_DIR/update.log
BACKUP_DIR=/var/backups/chardsoft-router-os
UNIT=chardsoft-update
APP_URL=http://127.0.0.1:5000/login
HEALTH_SECONDS=120

export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8

die() { echo "update.sh: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root."


# One value from the install record (root-owned, written by install.sh).
record() {
    python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$RECORD" "$1"
}


status() {
    # state, step, message: written atomically, readable by the app.
    python3 -I - "$STATUS" "$@" <<'PY'
import json, os, sys, time
path, state, step, message = sys.argv[1:5]
extra = dict(arg.split("=", 1) for arg in sys.argv[5:])
try:
    with open(path) as f:
        data = json.load(f)
except (OSError, ValueError):
    data = {}
data.update({"state": state, "step": step, "message": message, "updated": int(time.time()), **extra})
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}


check_managed() {

    [[ -f $RECORD ]] || die "No install record ($RECORD): install with install.sh first."

    [[ $(record managed) == "True" ]] || die "This router runs from its own clone; update it with git pull and install.sh."

    DIR=$(record dir)
    BRANCH=$(record branch)
    REPO=$(record repo)
    APP_USER=$(record user)

    [[ -d $DIR/.git && $(stat -c %u "$DIR") == 0 ]] || die "$DIR is not the installer's root-owned copy."
    [[ -z $(find "$DIR" ! -user root -print -quit) ]] || die "$DIR contains files not owned by root."
}


git_root() {
    git -c safe.directory="$DIR" -c core.hooksPath=/dev/null -C "$DIR" "$@"
}


# release/core -> stable, dev/core -> development (anything else: none).
channel_of() {
    case $1 in
        release/*) echo stable ;;
        dev/*)     echo development ;;
    esac
}


# Exit status 0 when version $1 is older than $2 (1.0.0-beta < 1.0.0 < 1.0.1).
version_older() {
    python3 -I - "$1" "$2" <<'PY'
import re, sys
def key(v):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$", v.strip())
    if not m:
        return None
    pre = m.group(4)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre is None, pre or "")
a, b = key(sys.argv[1]), key(sys.argv[2])
sys.exit(0 if a and b and a < b else 1)
PY
}


set_channel() {

    check_managed

    local channel=${1:-} branch

    case $channel in
        stable)      branch="release/${BRANCH#*/}" ;;
        development) branch="dev/${BRANCH#*/}" ;;
        *)           die "Usage: $0 channel stable|development" ;;
    esac

    [[ -n $(channel_of "$BRANCH") ]] || die "The branch $BRANCH has no release channel."

    if systemctl is-active --quiet "$UNIT"; then
        die "An update is running; change the channel afterwards."
    fi

    python3 -I - "$RECORD" "$branch" <<'PY'
import json, os, sys
path, branch = sys.argv[1:3]
with open(path) as f:
    data = json.load(f)
data["branch"] = branch
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY

    echo "Channel: $channel ($branch)"
}


start() {

    check_managed

    if systemctl is-active --quiet "$UNIT"; then
        die "An update is already running."
    fi

    install -d -o root -g root -m 755 "$STATUS_DIR"
    rm -f "$STATUS" "$LOG"

    status running starting "Starting the update." started="$(date +%s)"

    systemd-run --unit="$UNIT" --collect --quiet \
        --property=Type=exec \
        "$(readlink -f "$0")" run
}


app_answers() {

    local waited=0

    while (( waited < HEALTH_SECONDS )); do

        if systemctl is-active --quiet chardsoft-router-os \
            && curl -fsS -o /dev/null --max-time 5 "$APP_URL"; then
            return 0
        fi

        sleep 3
        waited=$(( waited + 3 ))
    done

    return 1
}


install_current() {
    # The installer of the version now checked out (not the old one), on
    # that code as it is: it must not fetch the branch again (a rollback
    # would get the new version back) nor switch the channel.
    CHARDSOFT_KEEP_CODE=1 CHARDSOFT_BRANCH="$BRANCH" \
        "$DIR/install.sh" --yes --user "$APP_USER" --dir "$DIR" >>"$LOG" 2>&1
}


run() {

    install -d -o root -g root -m 755 "$STATUS_DIR"
    exec >>"$LOG" 2>&1

    check_managed

    local previous target
    previous=$(git_root rev-parse HEAD)

    status running backup "Backing up the settings." from="$previous"

    install -d -o root -g root -m 700 "$BACKUP_DIR"
    tar -czf "$BACKUP_DIR/settings-$(date +%Y%m%d-%H%M%S)-${previous:0:7}.tar.gz" \
        -C "$(dirname "$STATE_DIR")" "$(basename "$STATE_DIR")" /etc/chardsoft-router-os 2>/dev/null
    chmod 600 "$BACKUP_DIR"/*.tar.gz

    # Keep the newest five.
    ls -1t "$BACKUP_DIR"/settings-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f

    status running download "Downloading the new version."

    git_root remote set-url origin "$REPO"

    if ! git_root fetch --quiet origin "$BRANCH"; then
        status failed download "Could not download the update (no internet, or GitHub not reachable). Nothing was changed."
        exit 1
    fi

    target=$(git_root rev-parse FETCH_HEAD)

    if [[ $target == "$previous" ]]; then
        status done done "Already up to date." to="$target"
        exit 0
    fi

    # Only ever install a version of the router (release branches are
    # empty until the first release).
    if ! git_root cat-file -e "$target:install.sh" 2>/dev/null \
            || ! git_root cat-file -e "$target:backend/app.py" 2>/dev/null; then
        if [[ $(channel_of "$BRANCH") == stable ]]; then
            status done done "There is no stable release yet. Nothing was changed."
        else
            status failed download "$BRANCH does not contain Chardsoft Router OS. Nothing was changed."
        fi
        exit 0
    fi

    local installed available
    installed=$(cat "$DIR/VERSION" 2>/dev/null || echo "")
    available=$(git_root show "$target:VERSION" 2>/dev/null || echo "")

    if [[ $(channel_of "$BRANCH") == stable ]] && version_older "$available" "$installed"; then
        status done done "Stable ($available) is older than the installed version ($installed). Nothing was changed; Stable updates come once it is newer."
        exit 0
    fi

    git_root reset --quiet --hard "$target"

    status running install "Installing (packages, dependencies, services). The dashboard restarts." to="$target"

    if install_current && app_answers; then
        status done done "Updated to ${target:0:7}."
        exit 0
    fi

    # Back to the version that worked.
    status running rollback "The new version did not start. Going back to ${previous:0:7}."

    git_root reset --quiet --hard "$previous"

    if install_current && app_answers; then
        status rolled-back done "The update failed, so the previous version (${previous:0:7}) is back. The log is in $LOG."
    else
        status failed rollback "The update and the way back both failed. Run the installer by hand (see $LOG)."
    fi

    exit 1
}


case ${1:-} in
    start)   start ;;
    run)     run ;;
    channel) set_channel "${2:-}" ;;
    *)       die "Usage: $0 start|run|channel stable|development" ;;
esac
