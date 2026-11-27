"""
Whether a modem is plugged in before ModemManager knows it (or without
ModemManager): a WWAN network interface or a modem control port. Names
and details come from ModemManager, which knows the modems itself.
"""

import glob


def detect_modem():

    present = bool(glob.glob("/sys/class/net/wwan*") or glob.glob("/dev/cdc-wdm*"))

    return {
        "vendor": None,
        "model": None,
        "usb_id": None,
        "present": present
    }
