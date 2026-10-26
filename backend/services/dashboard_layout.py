"""
The dashboard's widgets: which ones show, in what order and how wide.

Stored as "layout" in dashboard.json (the "dashboard" area, so it is
part of backups). The page lays the widgets out on a 12 column grid;
narrow screens stack them, so the layout never needs positions.
"""

import re


# One per kind, except traffic graphs (one per interface you add).
WIDGET_TYPES = ("summary", "traffic", "interfaces", "system")

SINGLE_TYPES = ("summary", "interfaces", "system")

# Columns out of 12.
WIDTHS = (3, 4, 6, 8, 9, 12)

MAX_WIDGETS = 16

# "" is the WAN, whatever interface that is at the moment.
INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.@-]{0,15}$")

ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")


DEFAULT_LAYOUT = [
    {"id": "summary", "type": "summary", "width": 12, "hidden": False},
    {"id": "traffic-wan", "type": "traffic", "width": 9, "hidden": False, "interface": ""},
    {"id": "interfaces", "type": "interfaces", "width": 3, "hidden": False},
    {"id": "system", "type": "system", "width": 12, "hidden": False},
]


def default_layout():

    return [dict(widget) for widget in DEFAULT_LAYOUT]


def validate_layout(layout):
    """
    A clean copy of a layout, or ValueError with a message for people.
    """

    if not isinstance(layout, list) or not layout:
        raise ValueError("The dashboard needs at least one widget.")

    if len(layout) > MAX_WIDGETS:
        raise ValueError(f"The dashboard holds at most {MAX_WIDGETS} widgets.")

    clean = []
    ids = set()
    singles = set()

    for widget in layout:

        if not isinstance(widget, dict):
            raise ValueError("Invalid widget.")

        kind = widget.get("type")
        widget_id = widget.get("id")
        width = widget.get("width", 12)
        hidden = widget.get("hidden", False)

        if kind not in WIDGET_TYPES:
            raise ValueError("Unknown widget type.")

        if not isinstance(widget_id, str) or not ID_RE.match(widget_id) or widget_id in ids:
            raise ValueError("Invalid widget id.")

        if isinstance(width, bool) or width not in WIDTHS:
            raise ValueError("Invalid widget width.")

        if not isinstance(hidden, bool):
            raise ValueError("Invalid widget visibility.")

        if kind in SINGLE_TYPES:
            if kind in singles:
                raise ValueError("Each widget except traffic graphs can only be added once.")
            singles.add(kind)

        ids.add(widget_id)

        entry = {"id": widget_id, "type": kind, "width": width, "hidden": hidden}

        if kind == "traffic":

            interface = widget.get("interface", "")

            if not isinstance(interface, str) or not INTERFACE_RE.match(interface):
                raise ValueError("Invalid interface name.")

            entry["interface"] = interface

        clean.append(entry)

    if all(widget["hidden"] for widget in clean):
        raise ValueError("Show at least one widget.")

    return clean


def get_layout(config):
    """The saved layout from dashboard.json, else the default."""

    try:
        return validate_layout(config.get("layout"))
    except ValueError:
        return default_layout()
