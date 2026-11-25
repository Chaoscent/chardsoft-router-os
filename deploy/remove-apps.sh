#!/usr/bin/env bash
#
# Removes the Apps Addon (Chardsoft Router Apps). The Apps page runs this
# ("Remove Apps Addon"); it also works by hand:
#
#   sudo deploy/remove-apps.sh                 apps and addon go, app data
#                                              and Docker stay
#   sudo deploy/remove-apps.sh --delete-data   app data goes too
#   sudo deploy/remove-apps.sh --purge         app data and Docker go (Docker
#                                              only if the addon installed it)
#
# Runs the installed addon's own installer with --remove. The sudoers rule
# allows exactly these three forms.

set -euo pipefail

ADDON_DIR=/opt/chardsoft-router-apps
INSTALLER=$ADDON_DIR/install.sh

usage() {
    echo "Usage: sudo $0 [--delete-data | --purge]" >&2
    exit 2
}

[[ $# -le 1 ]] || usage

case ${1:-} in
    ""|--delete-data|--purge) ;;
    *) usage ;;
esac

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

if [[ ! -f $INSTALLER ]]; then
    echo "The Apps Addon is not installed ($INSTALLER is missing)." >&2
    exit 1
fi

# Only ever run a root-owned installer nobody else can change.
if [[ $(stat -c %u "$ADDON_DIR") != 0 || $(stat -c %u "$INSTALLER") != 0 ]] \
        || [[ -n $(find "$ADDON_DIR" "$INSTALLER" -maxdepth 0 -perm /022) ]]; then
    echo "$INSTALLER must be root-owned and not writable by others." >&2
    exit 1
fi

# The installer deletes its own folder: run a copy.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp "$INSTALLER" "$tmp/install.sh"

echo "==> Removing the Apps Addon"
bash "$tmp/install.sh" --remove ${1:+"$1"}
