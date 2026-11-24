"""
Wi-Fi QR codes: phones join a network by scanning them (camera app, or
Wi-Fi settings > QR icon). Drawn on the router as SVG (segno), so the
page loads nothing from elsewhere.
"""

import io

SECURITY = {
    "open": "nopass",
    "wpa2": "WPA",
    "wpa2-wpa3": "WPA",
    "wpa3": "SAE"
}


def _escape(value):

    return "".join("\\" + c if c in '\\;,:"' else c for c in str(value))


def wifi_payload(ssid, password="", security="wpa2", hidden=False):
    """The "WIFI:" text of the format phones understand."""

    kind = SECURITY.get(security, "WPA")

    text = f"WIFI:T:{kind};S:{_escape(ssid)};"

    if kind != "nopass":
        text += f"P:{_escape(password)};"

    if hidden:
        text += "H:true;"

    return text + ";"


def svg(text):
    """The QR code as an SVG document; it scales to any size."""

    import segno

    out = io.BytesIO()

    # Dark modules on white whatever the theme: scanners need the contrast.
    segno.make(text, error="m", micro=False).save(
        out, kind="svg", scale=1, border=4, dark="#000", light="#fff", omitsize=True, xmldecl=False,
        svgclass=None, lineclass=None
    )

    return out.getvalue().decode()
