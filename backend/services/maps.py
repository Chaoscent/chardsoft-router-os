"""
The detailed map for the GNSS map (Modem page): OpenStreetMap data of
one country, kept on the router as a single PMTiles file and served to
the page, so the map works without any third-party request.

Getting it is the one deliberate exception, started by the user:

    1. the `pmtiles` tool (go-pmtiles, pinned version and SHA-256) is
       fetched from GitHub once, into STATE_DIR/maps/bin;
    2. `pmtiles extract` reads only the country's part of Protomaps'
       daily OpenStreetMap build (HTTP range requests) into
       STATE_DIR/maps/region.part, renamed to region.pmtiles when done.

Both go over the router's own internet, so over mobile data they count
towards the data limit. The source is a setting ("source"), so another
build or mirror can be used: Protomaps asks not to hotlink its builds
heavily; one download per router is the intended use.

Without a detailed map the page draws the built-in world map
(frontend/static/vendor/map/countries.geojson, Natural Earth).
"""

import datetime
import hashlib
import io
import json
import os
import platform
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import urllib.request

from services.config import STATE_DIR, load_settings
from services import transaction
from services.logs import log_event

SETTINGS = "maps"

DEFAULT_SOURCE = "https://build.protomaps.com"
BUILDS_LIST = "https://build-metadata.protomaps.dev/builds.json"

DEFAULT_SETTINGS = {"source": DEFAULT_SOURCE}

MAP_DIR = STATE_DIR / "maps"
REGION = MAP_DIR / "region.pmtiles"
PART = MAP_DIR / "region.part"
META = MAP_DIR / "region.json"
LOG = MAP_DIR / "download.log"
REGION_SHAPE = MAP_DIR / "region.geojson"
TOOL = MAP_DIR / "bin" / "pmtiles"

COUNTRIES = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "static", "vendor", "map", "countries.geojson")

PMTILES_VERSION = "1.31.2"

# sha256 of the release archives, checked before anything is unpacked.
PMTILES_RELEASES = {
    "aarch64": ("Linux_arm64", "f8bd47e7ea866863489cad588fbaf2f31f42e5821f7a03f009b3769f05801cb1"),
    "x86_64": ("Linux_x86_64", "3ed7dbf4ec2e6dfe5e25b6f70d1ffc932729f93c86db353bf514dd71010a312f"),
}

# Zoom levels: what the map shows at its closest.
DETAILS = {
    12: "Towns and main roads",
    14: "Streets",
    15: "Everything (buildings, paths)",
}

# Never fill the card completely.
MIN_FREE_BYTES = 1024 ** 3

TIMEOUT = 30

_lock = threading.Lock()
_job = {"process": None, "started": None, "country": None, "error": None, "stage": None}


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def get_settings():

    settings = load_settings(SETTINGS, DEFAULT_SETTINGS)

    return {"source": settings.get("source") or DEFAULT_SOURCE}


def validate_settings(data):

    if not isinstance(data, dict):
        return False, "Invalid settings."

    source = str(data.get("source") or DEFAULT_SOURCE).strip().rstrip("/")

    if not source.startswith("https://") or len(source) > 200 or any(c.isspace() for c in source):
        return False, "The map source must be an https:// address."

    return True, {"source": source}


def save_settings(data):

    ok, clean = validate_settings(data)

    if not ok:
        return transaction.result(False, clean)

    return transaction.change(SETTINGS, clean)


# ------------------------------------------------------------------
# Countries
# ------------------------------------------------------------------

_countries = None


def countries():
    """{code: {"name", "bbox", "feature"}} from the built-in world map."""

    global _countries

    if _countries is not None:
        return _countries

    try:
        with open(COUNTRIES) as f:
            data = json.load(f)
    except (OSError, ValueError):
        _countries = {}
        return _countries

    found = {}

    for feature in data.get("features", []):

        code = feature["properties"].get("code")
        name = feature["properties"].get("name")

        if not code or not name or code in found:
            continue

        xs, ys = [], []

        for polygon in feature["geometry"]["coordinates"]:
            for x, y in polygon[0]:
                xs.append(x)
                ys.append(y)

        if xs:
            found[code] = {"name": name, "bbox": [min(xs), min(ys), max(xs), max(ys)], "feature": feature}

    _countries = found

    return _countries


def country_at(latitude, longitude):
    """The country whose outline holds the point (ray casting), or None."""

    def inside(ring):
        hit = False
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > latitude) != (y2 > latitude) and \
                    longitude < (x2 - x1) * (latitude - y1) / (y2 - y1) + x1:
                hit = not hit
        return hit

    for code, info in countries().items():

        west, south, east, north = info["bbox"]

        if not (south <= latitude <= north and west <= longitude <= east):
            continue

        for polygon in info["feature"]["geometry"]["coordinates"]:
            if inside(polygon[0]) and not any(inside(hole) for hole in polygon[1:]):
                return code

    return None


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------

def read_meta():

    try:
        with open(META) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None

    return meta if REGION.exists() else None


def running():

    process = _job["process"]

    return process is not None and process.poll() is None


def log_tail(lines=6):

    try:
        with open(LOG, errors="replace") as f:
            text = f.read()[-4000:]
    except OSError:
        return ""

    # Progress bars redraw with \r: the last state of each line.
    rows = [row.split("\r")[-1].strip() for row in text.split("\n")]

    return "\n".join([r for r in rows if r][-lines:])


def free_bytes():

    try:
        MAP_DIR.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(MAP_DIR).free
    except OSError:
        return None


def get_status():

    meta = read_meta()

    status = {
        "state": "none",
        "region": meta,
        "size": REGION.stat().st_size if meta else None,
        "free": free_bytes(),
        "details": {str(k): v for k, v in DETAILS.items()},
        "source": get_settings()["source"],
        "error": _job["error"],
    }

    if running():

        try:
            done = PART.stat().st_size
        except OSError:
            done = 0

        status.update(
            state="downloading",
            stage=_job["stage"],
            country=_job["country"],
            downloaded=done,
            seconds=round(time.time() - _job["started"]),
            log=log_tail(),
        )

    elif _job["stage"] == "tool":
        status.update(state="downloading", stage="tool", country=_job["country"], downloaded=0,
                      seconds=round(time.time() - _job["started"]), log="")

    elif meta:
        status["state"] = "ready"

    elif _job["error"]:
        status.update(state="failed", log=log_tail())

    return status


# ------------------------------------------------------------------
# Downloading
# ------------------------------------------------------------------

def fetch(url, limit=None):

    request = urllib.request.Request(url, headers={"User-Agent": "chardsoft-router-os"})

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read(limit) if limit else response.read()


def ensure_tool():
    """The pmtiles tool, fetched and checked once. (ok, message)."""

    if TOOL.exists() and os.access(TOOL, os.X_OK):
        return True, "OK"

    release = PMTILES_RELEASES.get(platform.machine())

    if not release:
        return False, f"No pmtiles tool for this processor ({platform.machine()})."

    name, digest = release
    url = (f"https://github.com/protomaps/go-pmtiles/releases/download/v{PMTILES_VERSION}/"
           f"go-pmtiles_{PMTILES_VERSION}_{name}.tar.gz")

    try:
        archive = fetch(url)
    except OSError as e:
        return False, f"The pmtiles tool could not be downloaded: {e}"

    if hashlib.sha256(archive).hexdigest() != digest:
        return False, "The pmtiles tool did not match its checksum; it was not used."

    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            member = tar.getmember("pmtiles")
            if not member.isfile():
                return False, "Unexpected pmtiles archive."
            content = tar.extractfile(member).read()
    except (tarfile.TarError, KeyError) as e:
        return False, f"Unexpected pmtiles archive: {e}"

    TOOL.parent.mkdir(parents=True, exist_ok=True)

    tmp = TOOL.with_suffix(".tmp")

    with open(tmp, "wb") as f:
        f.write(content)

    os.chmod(tmp, 0o755)
    os.replace(tmp, TOOL)

    return True, "OK"


def latest_build(source):
    """The newest daily build's file name, e.g. "20261124.pmtiles"."""

    if source != DEFAULT_SOURCE:
        # A mirror: the newest name from its own builds.json, or "latest".
        try:
            builds = json.loads(fetch(f"{source}/builds.json"))
            return builds[-1]["key"]
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return "latest.pmtiles"

    builds = json.loads(fetch(BUILDS_LIST))

    return builds[-1]["key"]


def start(code, maxzoom):
    """Starts downloading one country. (ok, message)."""

    with _lock:

        if running() or _job["stage"] == "tool":
            return False, "A map download is already running."

        info = countries().get(str(code or ""))

        if not info:
            return False, "Choose a country."

        try:
            maxzoom = int(maxzoom)
        except (TypeError, ValueError):
            maxzoom = None

        if maxzoom not in DETAILS:
            return False, "Choose the level of detail."

        free = free_bytes()

        if free is not None and free < MIN_FREE_BYTES * 2:
            return False, f"Only {free / 1e9:.1f} GB free on the router: a detailed map needs a few GB."

        _job.update(process=None, started=time.time(), country=info["name"], error=None, stage="tool")

    threading.Thread(target=_run, args=(code, info, maxzoom), daemon=True, name="map-download").start()

    return True, f"Downloading the map of {info['name']}."


def _fail(message):

    _job.update(error=message, stage=None)
    log_event("system", f"Map download failed: {message}", "warning")


def _run(code, info, maxzoom):

    try:

        ok, message = ensure_tool()

        if not ok:
            return _fail(message)

        source = get_settings()["source"]

        try:
            build = latest_build(source)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            return _fail(f"The list of map builds could not be read: {e}")

        MAP_DIR.mkdir(parents=True, exist_ok=True)

        with open(REGION_SHAPE, "w") as f:
            json.dump(info["feature"], f)

        for stale in (PART, LOG):
            try:
                stale.unlink()
            except OSError:
                pass

        command = [str(TOOL), "extract", f"{source}/{build}", str(PART),
                   f"--region={REGION_SHAPE}", f"--maxzoom={maxzoom}", "--download-threads=4"]

        with open(LOG, "w") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       cwd=MAP_DIR, start_new_session=True)

        _job.update(process=process, stage="extract")

        code_ = process.wait()

        if _job.get("cancelled"):
            _job.update(cancelled=False, stage=None, error=None)
            try:
                PART.unlink()
            except OSError:
                pass
            return

        if code_ != 0 or not PART.exists() or PART.stat().st_size == 0:
            return _fail(log_tail(2) or f"pmtiles stopped with code {code_}.")

        os.replace(PART, REGION)

        meta = {
            "code": code,
            "name": info["name"],
            "bbox": info["bbox"],
            "maxzoom": maxzoom,
            "build": build,
            "source": source,
            "downloaded": datetime.datetime.now().isoformat(timespec="minutes"),
        }

        with open(META, "w") as f:
            json.dump(meta, f)

        _job.update(stage=None, error=None)

        size = REGION.stat().st_size

        log_event("system", f"Map of {info['name']} downloaded ({size / 1e6:.0f} MB).", notify=True)

    except Exception as e:  # noqa: BLE001 - the page must hear about anything
        _fail(str(e))


def cancel():

    process = _job["process"]

    if not running():
        return False, "No map download is running."

    _job["cancelled"] = True

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.terminate()

    return True, "Map download stopped."


def delete():

    if running():
        return False, "Stop the download first."

    removed = False

    for path in (REGION, META, REGION_SHAPE, PART, LOG):
        try:
            path.unlink()
            removed = True
        except OSError:
            pass

    _job.update(error=None, stage=None)

    return (True, "Detailed map deleted.") if removed else (False, "There is no detailed map.")
