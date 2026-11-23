"""
Format version of the settings files, and the steps that bring older
files up to date.

The version lives in its own file, /var/lib/chaos-router-os/schema.json,
not inside every settings file: some files are plain maps (users.json is
keyed by user name) where an extra key would be read as data.

Version 1 is the format of Router OS 0.1. An install without schema.json
is version 1, as is a backup without a "schema" field.

Adding a format change:

    1. Write a function that takes the settings of one layer as
       {name: data} and changes them in place, e.g.

           def _v1_to_v2(files):
               if "dhcp" in files:
                   files["dhcp"].setdefault("domain", "")

    2. Add it to MIGRATIONS under the version it starts from and raise
       VERSION by one.
    3. Add a test in tests/test_schema.py.

A step must work on any subset of files (a backup holds only some areas)
and on files the user may have edited by hand: check types, never assume
a key exists. A step must also be safe to run twice: if the router
loses power halfway, the next start runs it again on files it already
changed.

On start, the persistent layer (/var/lib) and the running layer (/tmp)
are both migrated, so a change waiting for confirmation is not lost.
Restored backups go through the same steps first.
"""

import json
import os
import sys
from pathlib import Path

from services import config

VERSION = 1

NAME = "schema"

# version a step starts from -> function(files)
MIGRATIONS = {}


def stored_version():

    data = config.load_persistent(NAME, None)

    if isinstance(data, dict) and isinstance(data.get("version"), int) and data["version"] >= 1:
        return data["version"]

    return 1


def save_version(version=None):

    config.save_persistent(NAME, {"version": VERSION if version is None else version})


def migrate_files(files, version):
    """
    Brings {name: data} from `version` up to VERSION in place.
    Returns the list of steps run, e.g. ["1->2"]. Raises ValueError
    for a version newer than this router knows.
    """

    if not isinstance(version, int) or version < 1:
        raise ValueError(f"Unknown settings format {version!r}.")

    if version > VERSION:
        raise ValueError(
            f"These settings are from a newer Router OS (format {version}); "
            f"this router reads format {VERSION} and older."
        )

    steps = []

    while version < VERSION:

        step = MIGRATIONS.get(version)

        if step is None:
            raise ValueError(f"No update step from settings format {version}.")

        step(files)
        steps.append(f"{version}->{version + 1}")
        version += 1

    return steps


def _load_layer(directory):
    """{name: (data, secret)} for every settings file in a layer."""

    files = {}

    for path in sorted(Path(directory).glob("*.json")):

        name = path.stem

        if name == NAME or ".tmp" in name:
            continue

        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue

        secret = (path.stat().st_mode & 0o077) == 0
        files[name] = (data, secret)

    return files


def _migrate_layer(directory, version):

    loaded = _load_layer(directory)
    files = {name: json.loads(json.dumps(data)) for name, (data, _) in loaded.items()}

    migrate_files(files, version)

    for name, data in files.items():

        if name in loaded and data == loaded[name][0]:
            continue

        secret = loaded[name][1] if name in loaded else False
        config.save_json(str(Path(directory) / f"{name}.json"), data, secret=secret)

    for name in loaded:
        if name not in files:
            os.remove(Path(directory) / f"{name}.json")


def migrate():
    """
    Runs on every start. Returns the steps run (empty when the files
    were already current).
    """

    version = stored_version()

    if version == VERSION:
        if not config.has_persistent(NAME) and os.access(config.STATE_DIR, os.W_OK):
            save_version()
        return []

    if version > VERSION:
        # An older Router OS after a downgrade: leave the files alone.
        print(
            f"[schema] Settings are format {version}, this version knows {VERSION}. "
            f"Not touching them.",
            file=sys.stderr
        )
        return []

    try:

        _migrate_layer(config.STATE_DIR, version)
        _migrate_layer(config.RUNTIME_DIR, version)
        steps = migrate_files({}, version)

    except (OSError, ValueError) as e:
        print(f"[schema] Could not update the settings: {e}", file=sys.stderr)
        return []

    save_version()

    print(f"[schema] Settings updated: {', '.join(steps)}")

    return steps
