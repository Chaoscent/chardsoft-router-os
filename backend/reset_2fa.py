"""
Turns two-step login off for every account, for when the phone and the
recovery codes are both lost. Run on the router (SSH or a keyboard) as
the account the router runs as:

    sudo -u <user> /opt/chardsoft-router-os/.venv/bin/python /opt/chardsoft-router-os/backend/reset_2fa.py

The password stays as it is.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services import config  # noqa: E402


def main():

    changed = []

    for load, save in ((config.load_running, config.save_running),
                       (config.load_persistent, config.save_persistent)):

        users = load("users", None)

        if not isinstance(users, dict):
            continue

        names = [name for name, entry in users.items() if isinstance(entry, dict) and entry.pop("totp", None)]

        if names:
            save("users", users, secret=True)
            changed += names

    if changed:
        print("Two-step login is off for: " + ", ".join(sorted(set(changed))))
    else:
        print("Two-step login was not on.")


if __name__ == "__main__":
    main()
