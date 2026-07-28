"""Per-user store of TPI / Slope-Position / Landform classification systems.

Systems are JSON files (one per system) in a STABLE per-user location that
SURVIVES add-in reinstalls -- NOT the add-in folder:

    %LOCALAPPDATA%\\JennessEnterprises\\WildlifeTools\\classification_systems\\

The C# "Classification System Builder" (writer) and the Python geoprocessing
"apply" tools (reader) both use this module's layout + the schema documented in
``docs/classification_system_schema.md``. Default systems bundled with the
add-in (``Toolboxes/classification_seeds/*.json``) are copied into the store on
first use and NEVER overwrite a file the user already has (so user edits and
user-created systems persist). Sharing a system = copying its .json file.
"""

import os
import re
import glob
import json
import shutil

SCHEMA_VERSION = 1
FAMILIES = ("slope_position", "landform", "general")
_OPS = ("<", "<=", ">", ">=", "=", "!=")
# TPI representations a criterion can threshold. 'sd' = neighborhood-scale
# (dev); 'standardized' = whole-raster z-score (general grids / demstd); 'pctl'
# = percentile. Warning KIND: raw / sd (dev+demstd/standardized) / pctl.
_UNITS = ("raw", "standardized", "sd", "pctl")
SLOPE_UNITS = ("Degrees", "Percent")
_APP_SUBPATH = os.path.join("JennessEnterprises", "WildlifeTools",
                            "classification_systems")


def store_dir():
    """The per-user classification-systems folder (created on demand by
    ``ensure_store``). Falls back to the home directory if LOCALAPPDATA is
    somehow unset."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, _APP_SUBPATH)


def _seeds_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "classification_seeds")


def ensure_store(seed=True):
    """Create the store folder if needed and (optionally) copy in any bundled
    default systems that are not already present. Returns the folder path."""
    d = store_dir()
    os.makedirs(d, exist_ok=True)
    if seed:
        seed_defaults(d)
    return d


def seed_defaults(dest=None):
    """Copy each bundled default system into the store IF a file of that name
    is not already there. Never overwrites -- user edits win."""
    dest = dest or store_dir()
    os.makedirs(dest, exist_ok=True)
    src = _seeds_dir()
    if not os.path.isdir(src):
        return
    for f in glob.glob(os.path.join(src, "*.json")):
        target = os.path.join(dest, os.path.basename(f))
        if not os.path.exists(target):
            try:
                shutil.copy2(f, target)
            except Exception:
                pass


def _slug(name):
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_")
    return s or "system"


def list_systems(family=None):
    """All valid systems in the store (optionally filtered by family), sorted
    by name. Invalid/unreadable files are skipped silently."""
    d = ensure_store()
    out = []
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                s = json.load(fh)
            validate(s)
        except Exception:
            continue
        if family and s.get("family") != family:
            continue
        s["_path"] = f
        out.append(s)
    out.sort(key=lambda s: str(s.get("name", "")).lower())
    return out


def system_names(family=None):
    return [s["name"] for s in list_systems(family)]


def load_system(name, family=None):
    """Load a system by its display ``name`` (case-insensitive)."""
    lname = str(name).strip().lower()
    for s in list_systems(family):
        if str(s.get("name", "")).strip().lower() == lname:
            return s
    raise KeyError("No classification system named {!r} in the store."
                   .format(name))


def save_system(system, overwrite=True):
    """Validate and write a system to the store. Returns the file path."""
    validate(system)
    d = ensure_store(seed=False)
    path = os.path.join(d, _slug(system["name"]) + ".json")
    if os.path.exists(path) and not overwrite:
        raise IOError("A system file already exists: {}".format(path))
    tmp = dict(system)
    tmp.pop("_path", None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(tmp, fh, indent=2)
    return path


def validate(system):
    """Raise ValueError if ``system`` does not conform to the schema. Both the
    reader and writer call this so a bad file never reaches the classifier."""
    if not isinstance(system, dict):
        raise ValueError("A classification system must be a JSON object.")
    if system.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Unsupported schemaVersion {!r} (this tool reads {})."
                         .format(system.get("schemaVersion"), SCHEMA_VERSION))
    if not system.get("name"):
        raise ValueError("Classification system is missing a name.")
    fam = system.get("family")
    if fam not in FAMILIES:
        raise ValueError("Unknown family {!r}; expected one of {}."
                         .format(fam, ", ".join(FAMILIES)))
    su = system.get("slopeUnits")
    if su is not None and su not in SLOPE_UNITS:
        raise ValueError("Unknown slopeUnits {!r}; expected one of {}."
                         .format(su, ", ".join(SLOPE_UNITS)))
    inputs = system.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("System {!r} declares no inputs.".format(system["name"]))
    roles = {}
    for i in inputs:
        role = i.get("role")
        if not role:
            raise ValueError("An input is missing its role.")
        roles[role] = i.get("kind", "external")
    classes = system.get("classes")
    if not isinstance(classes, list) or not classes:
        raise ValueError("System {!r} has no classes.".format(system["name"]))
    seen_vals = set()
    for c in classes:
        v = c.get("value")
        if not isinstance(v, int):
            raise ValueError("Class {!r} needs an integer value."
                             .format(c.get("name")))
        if v == 0:
            raise ValueError("Class value 0 is reserved for unclassified/"
                             "NoData cells; use values 1 and up (class {!r})."
                             .format(c.get("name")))
        if v in seen_vals:
            raise ValueError("Duplicate class value {} in {!r}."
                             .format(v, system["name"]))
        seen_vals.add(v)
        if not c.get("name"):
            raise ValueError("A class (value {}) is missing its name.".format(v))
        col = c.get("color")
        if col is not None:
            if (not isinstance(col, (list, tuple)) or len(col) != 3
                    or not all(isinstance(x, int) and 0 <= x <= 255 for x in col)):
                raise ValueError("Class {!r} color must be [r,g,b] 0-255."
                                 .format(c.get("name")))
        crits = c.get("criteria")
        if not isinstance(crits, list) or not crits:
            raise ValueError("Class {!r} has no criteria.".format(c.get("name")))
        for cr in crits:
            if cr.get("input") not in roles:
                raise ValueError("Class {!r} references undeclared input {!r}."
                                 .format(c.get("name"), cr.get("input")))
            u = cr.get("units", "raw")
            if u not in _UNITS:
                raise ValueError("Unknown units {!r} in class {!r}."
                                 .format(u, c.get("name")))
            conds = cr.get("conditions")
            if not isinstance(conds, list) or not (1 <= len(conds) <= 2):
                raise ValueError("A criterion in class {!r} needs 1 or 2 "
                                 "conditions.".format(c.get("name")))
            for i, cond in enumerate(conds):
                if cond.get("op") not in _OPS:
                    raise ValueError("Bad operator {!r} in class {!r}."
                                     .format(cond.get("op"), c.get("name")))
                if not isinstance(cond.get("value"), (int, float)):
                    raise ValueError("A condition in class {!r} needs a numeric "
                                     "value.".format(c.get("name")))
                # anything but and/or would silently act as OR in the classifier
                if i == 1 and cond.get("join", "and") not in ("and", "or"):
                    raise ValueError("Condition join must be 'and' or 'or' in "
                                     "class {!r} (got {!r})."
                                     .format(c.get("name"), cond.get("join")))
    return True
