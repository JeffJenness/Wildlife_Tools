# -*- coding: utf-8 -*-
# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Movebank Animal Tracks: animal tracking data published in the Movebank
Data Repository, as points and as one track line per animal.

The Movebank Data Repository (https://datarepository.movebank.org) holds
tracking datasets that their owners have published: each has a DOI, a
license and a citation, and its files can be had without an account. A
published dataset ("data package") holds one or more data files in
Movebank's CSV format (one row per event, most of them location fixes),
usually a reference-data file that describes the animals, tags and
deployments, and a README.

This is the engine behind the geoprocessing tool, and behind the last
step of the Movebank Animal Tracks window. Only the Python standard
library and arcpy are used.
"""

import array
import csv
import datetime
import html
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import gbif_occurrences as G

REPO = "https://datarepository.movebank.org/server/api/"
REPO_SITE = "https://datarepository.movebank.org"
USER_AGENT = "WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)"
DOWNLOADS_FOLDER = "Data_Downloads"

OUT_BOTH = "Points and track lines"
OUT_POINTS = "Points"
OUT_LINES = "Track lines"
OUTPUTS = [OUT_BOTH, OUT_POINTS, OUT_LINES]

# The columns every Movebank data file has, and the two that hold the
# location. Output field = the column's name with "-" and ":" as "_".
CORE = ["event-id", "visible", "timestamp", "location-long", "location-lat", "sensor-type",
        "individual-taxon-canonical-name", "tag-local-identifier", "individual-local-identifier",
        "study-name"]
REQUIRED = ["timestamp", "individual-local-identifier"]

CORE_DESCRIPTIONS = {
    "event_id": "Movebank's identifier of the event (one sensor measurement, here a location fix).",
    "visible": "false when the data owner or Movebank flagged the fix as an outlier; otherwise true.",
    "timestamp": "Date and time of the fix, in UTC.",
    "timestamp_text": "The date and time exactly as the data file gives it, in UTC, with its milliseconds.",
    "location_long": "Longitude of the fix in decimal degrees, WGS 84.",
    "location_lat": "Latitude of the fix in decimal degrees, WGS 84.",
    "sensor_type": "The kind of sensor that made the fix (gps, argos-doppler-shift, radio-transmitter, ...).",
    "individual_taxon_canonical_name": "Scientific name of the animal's species or other taxon.",
    "tag_local_identifier": "The data owner's identifier of the tag.",
    "individual_local_identifier": "The data owner's identifier of the animal.",
    "study_name": "Name of the Movebank study the data come from.",
}
LINE_FIELDS = [
    ("individual_local_identifier", "Animal", "TEXT", 255, "The data owner's identifier of the animal."),
    ("individual_taxon_canonical_name", "Taxon", "TEXT", 255, "Scientific name of the animal's species or other taxon."),
    ("tag_local_identifiers", "Tags", "TEXT", 255, "The tags the animal's fixes came from, separated by semicolons."),
    ("sensor_types", "Sensor types", "TEXT", 255, "The kinds of sensor the fixes came from, separated by semicolons."),
    ("study_name", "Study", "TEXT", 255, "Name of the Movebank study the data come from."),
    ("fix_count", "Number of fixes", "LONG", None, "Number of location fixes the track line passes through."),
    ("first_fix", "First fix (UTC)", "DATE", None, "Date and time of the first fix on the line, in UTC."),
    ("last_fix", "Last fix (UTC)", "DATE", None, "Date and time of the last fix on the line, in UTC."),
    ("duration_days", "Duration (days)", "DOUBLE", None, "Time from the first fix to the last, in days."),
    ("length_km", "Length (km)", "DOUBLE", None,
     "Length of the line in kilometers, measured as geodesic distance between successive fixes. "
     "It is the shortest path through the fixes, so the animal travelled at least this far."),
    ("animal_sex", "Sex", "TEXT", 60, "Sex of the animal, from the dataset's reference data."),
    ("animal_life_stage", "Life stage", "TEXT", 120, "Life stage of the animal when tagged, from the dataset's reference data."),
]


class MovebankError(Exception):
    """A failure the user can act on; shown without a traceback."""


class Cancelled(Exception):
    """The user cancelled the tool."""


# numbers, sizes, times and lists in words: the one copy, shared with the other Online Data engines
n0 = G.n0
size_words = G.size_words
span = G.span
split_list = G.split_list


# ---------------------------------------------------------------------------
#  the repository
# ---------------------------------------------------------------------------
def _open(url, accept="application/json", timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise MovebankError("The Movebank Data Repository has nothing at {}.".format(url))
        if e.code in (401, 403):
            raise MovebankError("The Movebank Data Repository refused the request ({}). The file may be "
                                "under embargo or restricted by its owner.".format(e.code))
        raise MovebankError("The Movebank Data Repository returned an error ({}).".format(e.code))
    except urllib.error.URLError as e:
        raise MovebankError("The Movebank Data Repository could not be reached ({}). Check the internet "
                            "connection.".format(getattr(e, "reason", e)))
    except (TimeoutError, OSError) as e:
        raise MovebankError("The Movebank Data Repository did not answer in time ({}). Try again.".format(e))


def _json(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            resp = _open(url)
            try:
                return json.loads(resp.read().decode("utf-8", "replace"))
            finally:
                resp.close()
        except MovebankError as e:
            last = e
            if "nothing at" in str(e) or "refused" in str(e):
                raise
            time.sleep(2 + 3 * attempt)
        except ValueError:
            last = MovebankError("The Movebank Data Repository returned something that is not the expected data.")
            time.sleep(2)
    raise last


def _first(md, key):
    v = md.get(key) or []
    return v[0].get("value") if v else None


def _all(md, key):
    return [v.get("value") for v in (md.get(key) or []) if v.get("value")]


def _plain(text):
    """Text with HTML tags and entities removed."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def describe_item(item):
    """A data package as a plain dict."""
    md = item.get("metadata", {})
    doi = (_first(md, "dc.identifier.doi") or "").replace("doi:", "").strip()
    cite = _plain(_first(md, "mdr.citation.CSE") or "")
    return {
        "uuid": item.get("uuid") or item.get("id"),
        "title": _first(md, "dc.title") or "",
        "doi": doi,
        "doi_url": "https://doi.org/" + doi if doi else "",
        "page": _first(md, "dc.identifier.uri") or "",
        "license": _first(md, "dc.rights") or "",
        "license_url": _first(md, "dc.rights.uri") or "",
        "citation": cite,
        "taxa": _all(md, "dwc.ScientificName"),
        "sensors": _all(md, "sensor.name"),
        "authors": _all(md, "dc.contributor.author"),
        "issued": _first(md, "dc.date.issued") or "",
        "animals": _first(md, "mdr.animal.count") or "",
        "locations": _first(md, "mdr.location.count") or "",
        "study_id": _first(md, "mdr.study.id") or "",
        "abstract": _first(md, "dc.description.abstract") or "",
        "paper": _first(md, "dc.relation.isreferencedby") or "",
        "_links": item.get("_links", {}),
    }


def search(text=None, taxon=None, sensor=None, page=0, size=25):
    """Published data packages that match. Returns (list of dicts, total)."""
    q = ["dsoType=ITEM", "f.entityType=Datapackage,equals", "size={}".format(int(size)),
         "page={}".format(int(page)), "sort=dc.date.accessioned,DESC"]
    if text and text.strip():
        q.append("query=" + urllib.parse.quote(text.strip()))
    if taxon and taxon.strip():
        q.append("f.scientificName=" + urllib.parse.quote(taxon.strip()) + ",contains")
    if sensor and sensor.strip():
        q.append("f.sensorName=" + urllib.parse.quote(sensor.strip()) + ",equals")
    r = _json(REPO + "discover/search/objects?" + "&".join(q))
    res = r["_embedded"]["searchResult"]
    objs = res.get("_embedded", {}).get("objects", [])
    return ([describe_item(o["_embedded"]["indexableObject"]) for o in objs],
            int(res.get("page", {}).get("totalElements", len(objs))))


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")


def find_package(dataset):
    """The data package named by a DOI (with or without doi: or
    https://doi.org/), a repository address or an item identifier."""
    s = (dataset or "").strip().strip('"')
    m = _UUID.search(s)
    if m and "doi" not in s.lower():
        return describe_item(_json(REPO + "core/items/" + m.group(0).lower()))
    h = re.search(r"handle/(\d+/[A-Za-z0-9_.\-]+)", s)
    if h:
        r = _json(REPO + "discover/search/objects?dsoType=ITEM&f.entityType=Datapackage,equals&size=5&query="
                  + urllib.parse.quote('"' + h.group(1) + '"'))
        for o in r["_embedded"]["searchResult"].get("_embedded", {}).get("objects", []):
            d = describe_item(o["_embedded"]["indexableObject"])
            if d["page"].endswith(h.group(1)):
                return d
        raise MovebankError("The Movebank Data Repository has no dataset at {}.".format(s))
    m = _DOI.search(s)
    if not m:
        raise MovebankError(
            "\"{}\" is not a DOI of a dataset in the Movebank Data Repository (such as "
            "10.5441/001/1.74pf3n2p), an address in the repository, or a folder or data file on "
            "disk.".format(s))
    doi = m.group(0).rstrip(".,;)")
    # a file's DOI is the dataset's DOI with /1, /2 ... added
    parts = doi.split("/")
    candidates = [doi] + (["/".join(parts[:-1])] if len(parts) > 3 and parts[-1].isdigit() else [])
    for d in candidates:
        r = _json(REPO + "discover/search/objects?dsoType=ITEM&f.entityType=Datapackage,equals&size=10&query="
                  + urllib.parse.quote('"doi:' + d + '"'))
        for o in r["_embedded"]["searchResult"].get("_embedded", {}).get("objects", []):
            pk = describe_item(o["_embedded"]["indexableObject"])
            if pk["doi"].lower() == d.lower():
                return pk
    raise MovebankError("The Movebank Data Repository has no dataset with the DOI {}. Only datasets "
                        "published in that repository can be loaded this way.".format(doi))


def list_files(package):
    """The files of a data package: dicts with name, size, url, kind
    ("data", "reference", "readme" or "other")."""
    files = []
    bundles = _json(package["_links"]["bundles"]["href"])
    for b in bundles.get("_embedded", {}).get("bundles", []):
        if b.get("name") != "ORIGINAL":
            continue
        url = b["_links"]["bitstreams"]["href"] + "?size=100"
        while url:
            r = _json(url)
            for bs in r.get("_embedded", {}).get("bitstreams", []):
                name = bs.get("name") or ""
                low = name.lower()
                kind = ("readme" if "readme" in low
                        else "reference" if low.endswith(".csv") and "reference-data" in low
                        else "data" if low.endswith(".csv") else "other")
                files.append({"name": name, "size": int(bs.get("sizeBytes") or 0),
                              "url": bs["_links"]["content"]["href"], "kind": kind})
            url = r.get("_links", {}).get("next", {}).get("href")
    return files


def _safe_name(name):
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "file"


def download_file(f, folder, step, cancelled, base, width):
    """Saves one file (through a .part file). A file already there with
    the same size is not fetched again. A transfer that is cut off part
    way is tried once more; a file that has not come whole is never kept
    (see ``gbif_occurrences.promised_length``). Returns its path."""
    path = os.path.join(folder, _safe_name(f["name"]))
    if os.path.isfile(path) and f["size"] and os.path.getsize(path) == f["size"]:
        return path, False
    tmp = path + ".part"

    def drop():
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass

    cut = None
    for _attempt in range(2):
        resp = _open(f["url"], accept="*/*", timeout=300)
        cut = None
        try:
            promised = G.promised_length(resp)
            total = int(promised or f["size"] or 0)
            done = 0
            with open(tmp, "wb") as dst:
                while True:
                    if cancelled():
                        raise Cancelled()
                    buf = resp.read(1 << 18)
                    if not buf:
                        break
                    dst.write(buf)
                    done += len(buf)
                    step(base + (width * done / total if total else 0),
                         "Fetching {}: {}{}".format(f["name"], size_words(done),
                                                    " of " + size_words(total) if total else ""))
            if promised is not None and done < promised:
                cut = "{} of {} came".format(size_words(done), size_words(promised))
        except (http.client.HTTPException, TimeoutError, ConnectionError) as e:
            cut = str(e) or type(e).__name__
        except BaseException:
            drop()
            raise
        finally:
            resp.close()
        if cut is None:
            os.replace(tmp, path)
            return path, True
        drop()
    raise MovebankError("{} was cut off on its way from the Movebank Data Repository, twice ({}), so it was not "
                        "kept. Run the tool again.".format(f["name"], cut))


def downloads_folder(anchor_fc, name):
    """Data_Downloads\\<name> in the project's home folder; beside the
    output geodatabase when the tool runs outside a project."""
    import arcpy
    import mahalanobis

    home = None
    try:
        home = arcpy.mp.ArcGISProject("CURRENT").homeFolder
    except Exception:                                            # noqa: BLE001
        home = None
    if not home or not os.path.isdir(home):
        ws = os.path.dirname(anchor_fc)
        if mahalanobis.is_memory_workspace(ws):
            home = arcpy.env.scratchFolder
        else:
            home = os.path.dirname(ws.rstrip("\\/"))
    folder = os.path.join(home, DOWNLOADS_FOLDER, _safe_name(name))
    os.makedirs(folder, exist_ok=True)
    return folder


# ---------------------------------------------------------------------------
#  Movebank's data files
# ---------------------------------------------------------------------------
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def field_name(column):
    """A data-file column as a field name: eobs:battery-voltage ->
    eobs_battery_voltage."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", column.strip()).strip("_")
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = "f_" + s
    return s[:64]


def parse_time(text):
    """'2017-11-14 09:03:00.000' (UTC) -> datetime, or None."""
    t = (text or "").strip()
    if len(t) < 19:
        return None
    try:
        micro = 0
        if len(t) > 20 and t[19] == ".":
            frac = re.match(r"\d+", t[20:])
            if frac:
                micro = int((frac.group(0) + "000000")[:6])
        return datetime.datetime(int(t[0:4]), int(t[5:7]), int(t[8:10]), int(t[11:13]), int(t[14:16]),
                                 int(t[17:19]), micro)
    except ValueError:
        return None


def _reader(path):
    f = open(path, "r", encoding="utf-8-sig", newline="")
    return f, csv.reader(f)


def is_data_file(path):
    """True when a CSV file has the columns of a Movebank data file."""
    try:
        f, rd = _reader(path)
        try:
            header = next(rd, [])
        finally:
            f.close()
    except (OSError, UnicodeDecodeError):
        return False
    cols = set(c.strip() for c in header)
    # A reference-data file is not a data file: it has an animal-id column. (A tag-id column does not tell
    # them apart: the study files this tool fetches from Movebank have one too.)
    return all(c in cols for c in REQUIRED) and "animal-id" not in cols


def read_reference(path):
    """animal-id -> {sex, life_stage} from a reference-data file."""
    out = {}
    try:
        f, rd = _reader(path)
    except OSError:
        return out
    try:
        header = [c.strip() for c in next(rd, [])]
        idx = {c: i for i, c in enumerate(header)}
        if "animal-id" not in idx:
            return out
        for row in rd:
            if len(row) <= idx["animal-id"]:
                continue
            a = row[idx["animal-id"]].strip()
            if not a:
                continue
            d = out.setdefault(a, {"sex": "", "life_stage": ""})
            for key, col in (("sex", "animal-sex"), ("life_stage", "animal-life-stage")):
                i = idx.get(col)
                if i is not None and i < len(row) and row[i].strip() and not d[key]:
                    d[key] = row[i].strip()
    finally:
        f.close()
    return out


class Scan(object):
    """What a first pass over the data files found."""

    def __init__(self):
        self.columns = []           # every column, in first-seen order
        self.types = {}             # extra column -> [all int, all float, max length, any value]
        self.rows = 0
        self.located = 0
        self.flagged = 0
        self.animals = {}           # animal -> number of located fixes
        self.first = None
        self.last = None


def scan_files(paths, step, cancelled, base, width):
    """First pass: the columns, the type each extra column can hold
    without losing anything, and what the files contain."""
    sc = Scan()
    total_bytes = sum(os.path.getsize(p) for p in paths) or 1
    done_bytes = 0
    for path in paths:
        f, rd = _reader(path)
        try:
            header = [c.strip() for c in next(rd, [])]
            missing = [c for c in REQUIRED if c not in header]
            if missing:
                raise MovebankError("{} is not a Movebank data file: it has no {} column.".format(
                    os.path.basename(path), " or ".join(missing)))
            for c in header:
                if c not in sc.columns:
                    sc.columns.append(c)
            idx = {c: i for i, c in enumerate(header)}
            extra = [(c, i) for c, i in idx.items() if c not in CORE]
            for c, _i in extra:
                sc.types.setdefault(c, [True, True, 0, False])
            ilat, ilon, ivis = idx.get("location-lat"), idx.get("location-long"), idx.get("visible")
            ianimal, itime = idx["individual-local-identifier"], idx["timestamp"]
            n = len(header)
            for row in rd:
                sc.rows += 1
                if sc.rows % 50000 == 0:
                    if cancelled():
                        raise Cancelled()
                    step(base + width * min(1.0, (done_bytes + f.buffer.tell()) / float(total_bytes)),
                         "Reading the data: {} rows".format(n0(sc.rows)))
                if len(row) < n:
                    row = row + [""] * (n - len(row))
                for c, i in extra:
                    v = row[i]
                    if v == "":
                        continue
                    t = sc.types[c]
                    t[3] = True
                    if t[0] and not _INT_RE.match(v):
                        t[0] = False
                    if t[1] and not _FLOAT_RE.match(v):
                        t[1] = False
                    if len(v) > t[2]:
                        t[2] = len(v)
                if ilat is None or ilon is None or row[ilat] == "" or row[ilon] == "":
                    continue
                sc.located += 1
                if ivis is not None and row[ivis].strip().lower() == "false":
                    sc.flagged += 1
                a = row[ianimal]
                sc.animals[a] = sc.animals.get(a, 0) + 1
                ts = row[itime]
                if ts:
                    if sc.first is None or ts < sc.first:
                        sc.first = ts
                    if sc.last is None or ts > sc.last:
                        sc.last = ts
        finally:
            done_bytes += os.path.getsize(path)
            f.close()
    return sc


def extra_fields(sc):
    """(column, field name, type, length) for the columns beyond the
    core ones. A column is a whole number or a decimal number only when
    every value in it is one AND the field can hold every digit, so
    nothing is lost: whole numbers of up to 9 digits are a LONG field, of
    up to 15 a DOUBLE (which holds them exactly), and longer ones (an
    identifier of 20 digits, say) stay TEXT. A column with no values at
    all is left out."""
    out, used = [], set(field_name(c) for c in CORE) | {"timestamp_text"}
    for c in sc.columns:
        if c in CORE:
            continue
        all_int, all_float, maxlen, any_value = sc.types.get(c, [False, False, 0, False])
        if not any_value:
            continue
        name = field_name(c)
        base, k = name, 2
        while name.lower() in (u.lower() for u in used):
            name = "{}_{}".format(base[:60], k)
            k += 1
        used.add(name)
        if all_int and maxlen <= 9:
            out.append((c, name, "LONG", None))
        elif all_float and not (all_int and maxlen > 15):
            out.append((c, name, "DOUBLE", None))
        else:
            out.append((c, name, "TEXT", max(8, min(4000, maxlen))))
    return out


def _split_dateline(points):
    """A track's points as one or more parts that never jump across the
    180th meridian: a segment that crosses it is cut there."""
    parts, cur = [], []
    prev = None
    for lon, lat in points:
        if prev is not None and abs(lon - prev[0]) > 180:
            # the short way round crosses the 180th meridian
            lon2 = lon + 360 if lon < prev[0] else lon - 360
            edge = 180.0 if lon2 > prev[0] else -180.0
            f = (edge - prev[0]) / (lon2 - prev[0]) if lon2 != prev[0] else 0.0
            ylat = prev[1] + f * (lat - prev[1])
            cur.append((edge, ylat))
            if len(cur) >= 2:
                parts.append(cur)
            cur = [(-edge, ylat)]
        cur.append((lon, lat))
        prev = (lon, lat)
    if len(cur) >= 2:
        parts.append(cur)
    return parts


# ---------------------------------------------------------------------------
#  the tool
# ---------------------------------------------------------------------------
def check_output(path):
    """None when the output path can be written, else the reason."""
    import mahalanobis

    p = str(path or "").strip().replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(p)
    if not name or not ws:
        return "Give the output as a feature class inside a file geodatabase."
    if not mahalanobis.is_gdb_workspace(ws):
        if mahalanobis.in_gdb_container(ws):
            return "Put the output directly in the geodatabase, not inside a feature dataset."
        return ("The output must be a feature class inside a file geodatabase (Movebank's field names "
                "are too long for a shapefile).")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return "The feature class name must start with a letter and hold only letters, digits and underscores."
    return None


def run(dataset, **kwargs):
    """Runs the tool (see ``_run``). Returns a dict: points, lines,
    doi, citation, folder."""
    import arcpy

    try:
        before = arcpy.env.autoCancelling
    except Exception:                                            # noqa: BLE001
        before = None
    made = []                          # the outputs this run has begun to write
    try:
        return _run(dataset, _made=made, **kwargs)
    except BaseException:
        # cancelled or failed while writing: half a set of points, or points without their lines, would
        # pass for the finished result, so what this run began is removed
        for path in made:
            G.discard_output(path)
        raise
    finally:
        if before is not None:
            try:
                arcpy.env.autoCancelling = before
            except Exception:                                    # noqa: BLE001
                pass


def _run(dataset, output=OUT_BOTH, out_points=None, out_lines=None, animals=None, date_from=None,
         date_to=None, include_flagged=False, messages=None, accept_terms=False, _made=None):
    import arcpy
    import mahalanobis

    def say(text):
        if messages is not None:
            messages.addMessage(text)

    def warn(text):
        if messages is not None:
            messages.addWarningMessage(text)

    def step(percent, text):
        try:
            arcpy.SetProgressorLabel(text)
            arcpy.SetProgressorPosition(int(max(0, min(100, percent))))
        except Exception:                                        # noqa: BLE001
            pass

    def cancelled():
        try:
            return bool(arcpy.env.isCancelled)
        except Exception:                                        # noqa: BLE001
            return False

    output = output or OUT_BOTH
    if output not in OUTPUTS:
        raise MovebankError("The output must be one of: {}.".format("; ".join(OUTPUTS)))
    want_points = output in (OUT_BOTH, OUT_POINTS)
    want_lines = output in (OUT_BOTH, OUT_LINES)
    for want, path, what in ((want_points, out_points, "points"), (want_lines, out_lines, "track lines")):
        if want:
            if not path:
                raise MovebankError("Give the output feature class for the {}.".format(what))
            why = check_output(path)
            if why:
                raise MovebankError(why)
    if want_points and want_lines and str(out_points).lower() == str(out_lines).lower():
        raise MovebankError("The points and the track lines need different output names.")
    out_points = str(out_points).strip().replace("/", "\\").rstrip("\\") if want_points else None
    out_lines = str(out_lines).strip().replace("/", "\\").rstrip("\\") if want_lines else None
    if date_from and date_to and date_from > date_to:
        raise MovebankError("The first date is later than the last date.")
    only = set(split_list(animals) if isinstance(animals, str) else (animals or []))

    try:
        arcpy.env.autoCancelling = False
    except Exception:                                            # noqa: BLE001
        pass
    arcpy.SetProgressor("step", "Finding the dataset...", 0, 100, 1)

    # ---- the files ---------------------------------------------------------
    src = (dataset or "").strip().strip('"')
    if not src:
        raise MovebankError("Name the dataset: the DOI of a dataset in the Movebank Data Repository, a "
                            "Movebank study (study 433126, which needs a Movebank sign-in), or a folder or "
                            "data file already on disk.")
    package = None
    reference = None
    readme_path = None                 # the README fetched or written in this run
    disk = None                        # what the README beside files already on disk says they are
    import movebank_account as _acct
    study_id = None if (os.path.isdir(src) or os.path.isfile(src)) else _acct.parse_study(src)
    if study_id:
        # ---- a study in Movebank itself, read with the user's own account ----
        session = _acct.Session()
        rec = _acct.study_record(session, study_id)
        title = rec.get("name") or "study {}".format(study_id)
        say("Movebank study: {}".format(title))
        say("Study ID {}; license: {}; read with the Movebank account {}. Study page: {}".format(
            study_id, _acct.license_name(rec), session.user, _acct.STUDY_PAGE.format(study_id)))
        if rec.get("i_have_download_access") != "true":
            raise MovebankError(
                "The Movebank account {} may not download this study{}. Its owner decides who may: ask through "
                "the study's page, {}".format(
                    session.user, ", though it may see it" if rec.get("i_can_see_data") == "true" else "",
                    _acct.STUDY_PAGE.format(study_id)))
        try:
            n_fixes = int(float(rec.get("number_of_deployed_locations") or 0))
        except ValueError:
            n_fixes = 0
        say("Movebank lists {} animals and {} location fixes for it{}.".format(
            rec.get("number_of_individuals") or "?", n0(n_fixes) if n_fixes else "?",
            ", from {} to {}".format((rec.get("timestamp_first_deployed_location") or "")[:10],
                                     (rec.get("timestamp_last_deployed_location") or "")[:10])
            if rec.get("timestamp_first_deployed_location") else ""))
        if n_fixes > _acct.LARGE_STUDY and not (date_from or date_to):
            warn("That is a large study: the file of its {} fixes will be about {}, and fetching it can take a "
                 "long time. The tool can be cancelled at any time. A range of dates makes the request "
                 "smaller.".format(n0(n_fixes), size_words(n_fixes * _acct.BYTES_PER_FIX)))
        step(1, "Reading the study's license terms...")
        terms_raw = _acct.license_terms(session, study_id)
        terms_text = _acct.plain(terms_raw) if terms_raw else None
        if terms_raw:
            if not accept_terms:
                raise MovebankError(
                    "THE STUDY'S LICENSE TERMS HAVE NOT BEEN ACCEPTED, so nothing was fetched. Movebank hands "
                    "over a study only after the account has accepted its terms. Read them, and if you accept "
                    "them, check \"I have read and accept the study's license terms\" and run the tool again. "
                    "The terms:\n\n{}".format(terms_text))
            if not _acct.accept_terms(session, study_id, terms_raw):
                raise MovebankError("Movebank did not confirm that the study's license terms were accepted. "
                                    "Accept them on the study's page ({}) by starting a download there, then "
                                    "run the tool again.".format(_acct.STUDY_PAGE.format(study_id)))
            say("The study's license terms were accepted in the name of the Movebank account {}, as you "
                "asked. Movebank remembers that, and will not ask again unless the owner changes the "
                "terms.".format(session.user))
        anchor = out_points or out_lines
        folder = downloads_folder(anchor, os.path.basename(anchor))
        say("Fetching the study into {}.".format(folder))
        got = _acct.fetch_study(session, rec, folder, date_from, date_to, step, cancelled, say, terms_text)
        data_paths = [got["data"]]
        reference = got["reference"]
        readme_path = got["readme"]
        cite_study = _acct.citation(rec)
        package = {"source": "study", "study_id": study_id, "title": title, "doi": "",
                   "license": _acct.license_name(rec),
                   "license_url": _acct.LICENSE_URLS.get(rec.get("license_type") or "", ""),
                   "issued": "", "animals": rec.get("number_of_individuals") or "",
                   "locations": str(n_fixes) if n_fixes else "", "citation": cite_study, "paper": "",
                   "page": _acct.STUDY_PAGE.format(study_id), "account": session.user,
                   # the investigator and the contact person are often the same: say it once
                   "owner": "; ".join(x for x in (
                       _acct.plain(rec.get("principal_investigator_name") or ""),
                       ("contact person " + _acct.plain(rec.get("contact_person_name") or ""))
                       if rec.get("contact_person_name") and _acct.plain(rec.get("contact_person_name") or "")
                       != _acct.plain(rec.get("principal_investigator_name") or "") else "") if x)}
        say("The study's data are kept in {} as Movebank sent them ({}), with the reference data about the "
            "animals and a README that holds the study's terms of use.".format(
                folder, size_words(os.path.getsize(got["data"]))))
    elif os.path.isdir(src) or os.path.isfile(src):
        folder = src if os.path.isdir(src) else os.path.dirname(src)
        if os.path.isdir(src):
            csvs = [os.path.join(src, n) for n in sorted(os.listdir(src)) if n.lower().endswith(".csv")]
            data_paths = [p for p in csvs if is_data_file(p)]
            refs = [p for p in csvs if "reference-data" in os.path.basename(p).lower()]
        else:
            data_paths = [src] if is_data_file(src) else []
            refs = [os.path.join(folder, n) for n in os.listdir(folder)
                    if n.lower().endswith(".csv") and "reference-data" in n.lower()]
        if not data_paths:
            raise MovebankError("{} holds no data file in Movebank's format (a CSV file with timestamp "
                                "and individual-local-identifier columns).".format(src))
        reference = refs[0] if refs else None
        say("Loading Movebank data already on disk: {}.".format("; ".join(os.path.basename(p) for p in data_paths)))
        # The data files do not say which dataset they are. The README beside them does, when it is one
        # this tool wrote for a study or one the Movebank Data Repository publishes with a dataset.
        disk = describe_readme(read_readme(folder))
        if disk and disk["kind"] == "study":
            say("The README beside {} says what {}: the Movebank study \"{}\" (study ID {}){}. A study is not "
                "a published dataset: its owner may have added to it or corrected it since.".format(
                    "it" if len(data_paths) == 1 else "them", "it is" if len(data_paths) == 1 else "they are",
                    disk["name"], disk["study_id"],
                    ", as this tool fetched it on {}".format(disk["fetched"]) if disk["fetched"] else ""))
        elif disk:
            say("The README beside {} says what {}: a dataset published in the Movebank Data Repository{}.".format(
                "it" if len(data_paths) == 1 else "them", "it is" if len(data_paths) == 1 else "they are",
                " (DOI {})".format(disk["doi"]) if disk["doi"] else ""))
        else:
            warn("No README beside the files says which dataset they are, so the output's metadata carries no "
                 "DOI or citation. Cite the dataset as its owner asks.")
    else:
        package = find_package(src)
        say("Dataset: {}".format(package["title"]))
        say("DOI {}; license: {}; published {}.".format(
            package["doi"] or "(none)", package["license"] or "(not stated)", package["issued"] or "(date not given)"))
        if package["animals"] or package["locations"]:
            say("The repository lists {} animals and {} locations for it.".format(
                package["animals"] or "?", n0(package["locations"]) if package["locations"].isdigit() else "?"))
        files = list_files(package)
        data = [f for f in files if f["kind"] == "data"]
        if not data:
            raise MovebankError("This dataset has no data file in Movebank's CSV format to load. Its files "
                                "are: {}.".format("; ".join(f["name"] for f in files) or "none"))
        wanted = data + [f for f in files if f["kind"] in ("reference", "readme")]
        anchor = out_points or out_lines
        folder = downloads_folder(anchor, os.path.basename(anchor))
        total = sum(f["size"] for f in wanted) or 1
        say("Fetching {} ({}) into {}.".format(
            "1 file" if len(wanted) == 1 else "{} files".format(len(wanted)), size_words(total), folder))
        done = 0
        data_paths = []
        for f in wanted:
            path, fetched = download_file(f, folder, step, cancelled, 2 + 28.0 * done / total, 28.0 * f["size"] / total)
            done += f["size"]
            if not fetched:
                say("{} is already in that folder at its full size, so it was not fetched again.".format(f["name"]))
            if f["kind"] == "data":
                data_paths.append(path)
            elif f["kind"] == "reference" and reference is None:
                reference = path
            elif f["kind"] == "readme" and readme_path is None:
                readme_path = path
        say("The dataset's original files are kept in {}: the data, {}the README, which describes the "
            "study and its terms of use.".format(folder, "the reference data about the animals and tags, "
                                                 if reference else ""))

    # ---- first pass ---------------------------------------------------------
    sc = scan_files(data_paths, step, cancelled, 32, 23)
    say("The data {} {} rows: {} location fixes of {} animals{}{}.".format(
        "file holds" if len(data_paths) == 1 else "files hold", n0(sc.rows), n0(sc.located), n0(len(sc.animals)),
        ", from {} to {} (UTC)".format(sc.first[:10], sc.last[:10]) if sc.first and sc.last else "",
        "; {} rows are measurements without a location and are not loaded".format(n0(sc.rows - sc.located))
        if sc.rows > sc.located else ""))
    if sc.located == 0:
        raise MovebankError("The dataset holds no location fixes, so there is nothing to map.")
    unknown = sorted(a for a in only if a not in sc.animals)
    if unknown:
        raise MovebankError("The dataset has no animal named {}. Its animals are: {}.".format(
            ", ".join(unknown), "; ".join(sorted(sc.animals))[:1500]))
    extras = extra_fields(sc)
    ref = read_reference(reference) if reference else {}

    # ---- the outputs ---------------------------------------------------------
    sr = mahalanobis.clean_sr(arcpy.SpatialReference(4326))
    core_fields = [(c, field_name(c)) for c in CORE]
    point_cursor = None
    point_names = []
    if want_points:
        if arcpy.Exists(out_points):
            arcpy.management.Delete(out_points)
        arcpy.management.CreateFeatureclass(os.path.dirname(out_points), os.path.basename(out_points), "POINT",
                                            has_m="DISABLED", has_z="DISABLED", spatial_reference=sr)
        if _made is not None:
            _made.append(out_points)
        spec = []
        for c, name in core_fields:
            if c == "timestamp":
                spec.append([name, "DATE", "Time of the fix (UTC)", None])
                spec.append(["timestamp_text", "TEXT", "Time of the fix (UTC), as text", 30])
            elif c in ("location-long", "location-lat"):
                spec.append([name, "DOUBLE", None, None])
            elif c == "visible":
                spec.append([name, "TEXT", "Visible (false = flagged as an outlier)", 5])
            else:
                spec.append([name, "TEXT", None, 40 if c == "event-id" else 255])
        for c, name, ftype, length in extras:
            spec.append([name, ftype, c, length])
        arcpy.management.AddFields(out_points, spec)
        point_names = [s[0] for s in spec]
        point_cursor = arcpy.da.InsertCursor(out_points, ["SHAPE@XY"] + point_names)

    tracks = {}                  # animal -> [times array, lons array, lats array, tags, sensors, taxon, study]
    written = skipped_flag = skipped_animal = skipped_date = bad = truncated = 0
    t_from = date_from if isinstance(date_from, datetime.datetime) else (
        datetime.datetime.combine(date_from, datetime.time.min) if date_from else None)
    t_to = _acct.end_of_day(date_to) if date_to else None
    epoch = datetime.datetime(1970, 1, 1)
    try:
        seen = 0
        for path in data_paths:
            f, rd = _reader(path)
            try:
                header = [c.strip() for c in next(rd, [])]
                idx = {c: i for i, c in enumerate(header)}
                n = len(header)
                ilat, ilon = idx.get("location-lat"), idx.get("location-long")
                if ilat is None or ilon is None:
                    continue
                ivis, ianimal, itime = idx.get("visible"), idx["individual-local-identifier"], idx["timestamp"]
                itag, isens = idx.get("tag-local-identifier"), idx.get("sensor-type")
                itax, istudy = idx.get("individual-taxon-canonical-name"), idx.get("study-name")
                core_idx = [idx.get(c) for c, _n in core_fields]
                extra_idx = [(idx.get(c), ftype, length) for c, _nm, ftype, length in extras]
                for row in rd:
                    seen += 1
                    if seen % 20000 == 0:
                        if cancelled():
                            raise Cancelled()
                        step(55 + 35.0 * seen / max(1, sc.rows), "Writing the fixes: {} of {} rows".format(
                            n0(seen), n0(sc.rows)))
                    if len(row) < n:
                        row = row + [""] * (n - len(row))
                    if row[ilat] == "" or row[ilon] == "":
                        continue
                    animal = row[ianimal]
                    if only and animal not in only:
                        skipped_animal += 1
                        continue
                    if not include_flagged and ivis is not None and row[ivis].strip().lower() == "false":
                        skipped_flag += 1
                        continue
                    try:
                        lon, lat = float(row[ilon]), float(row[ilat])
                    except ValueError:
                        bad += 1
                        continue
                    when = parse_time(row[itime])
                    if when is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
                        bad += 1
                        continue
                    if (t_from and when < t_from) or (t_to and when > t_to):
                        skipped_date += 1
                        continue
                    if point_cursor is not None:
                        vals = [(lon, lat)]
                        for (c, _nm), i in zip(core_fields, core_idx):
                            v = row[i] if i is not None else ""
                            if c == "timestamp":
                                vals.append(when)
                                vals.append(v or None)
                            elif c == "location-long":
                                vals.append(lon)
                            elif c == "location-lat":
                                vals.append(lat)
                            else:
                                vals.append(v or None)
                        for i, ftype, length in extra_idx:
                            v = row[i] if i is not None else ""
                            if v == "":
                                vals.append(None)
                            elif ftype == "LONG":
                                vals.append(int(v))
                            elif ftype == "DOUBLE":
                                vals.append(float(v))
                            else:
                                if len(v) > length:
                                    truncated += 1
                                    v = v[:length]
                                vals.append(v)
                        point_cursor.insertRow(vals)
                    if want_lines:
                        t = tracks.get(animal)
                        if t is None:
                            t = tracks[animal] = [array.array("d"), array.array("d"), array.array("d"),
                                                  [], [], "", ""]
                        t[0].append(round((when - epoch).total_seconds(), 3))
                        t[1].append(lon)
                        t[2].append(lat)
                        if itag is not None and row[itag] and row[itag] not in t[3]:
                            t[3].append(row[itag])
                        if isens is not None and row[isens] and row[isens] not in t[4]:
                            t[4].append(row[isens])
                        if itax is not None and row[itax] and not t[5]:
                            t[5] = row[itax]
                        if istudy is not None and row[istudy] and not t[6]:
                            t[6] = row[istudy]
                    written += 1
            finally:
                f.close()
    finally:
        if point_cursor is not None:
            del point_cursor

    # ---- the track lines ---------------------------------------------------------
    n_lines = single = 0
    if want_lines:
        step(91, "Building the track lines...")
        if arcpy.Exists(out_lines):
            arcpy.management.Delete(out_lines)
        arcpy.management.CreateFeatureclass(os.path.dirname(out_lines), os.path.basename(out_lines), "POLYLINE",
                                            has_m="DISABLED", has_z="DISABLED", spatial_reference=sr)
        if _made is not None:
            _made.append(out_lines)
        arcpy.management.AddFields(out_lines, [[nm, ft, alias, ln] for nm, alias, ft, ln, _d in LINE_FIELDS])
        wgs = arcpy.SpatialReference(4326)
        with arcpy.da.InsertCursor(out_lines, ["SHAPE@"] + [x[0] for x in LINE_FIELDS]) as cur:
            for k, animal in enumerate(sorted(tracks)):
                if cancelled():
                    raise Cancelled()
                times, lons, lats, tags, sensors, taxon, study = tracks[animal]
                order = sorted(range(len(times)), key=times.__getitem__)
                pts, last = [], None
                for i in order:
                    p = (lons[i], lats[i])
                    if p != last:                       # repeated fixes at one spot add nothing to a line
                        pts.append(p)
                        last = p
                if len(pts) < 2:
                    single += 1
                    continue
                parts = _split_dateline(pts)
                if not parts:
                    single += 1
                    continue
                geom = arcpy.Polyline(arcpy.Array([arcpy.Array([arcpy.Point(x, y) for x, y in part])
                                                   for part in parts]), wgs)
                try:
                    km = geom.getLength("GEODESIC", "KILOMETERS")
                except Exception:                                # noqa: BLE001
                    km = None
                t0, t1 = times[order[0]], times[order[-1]]
                info = ref.get(animal, {})
                cur.insertRow([geom, animal[:255], taxon[:255] or None, "; ".join(tags)[:255] or None,
                               "; ".join(sensors)[:255] or None, study[:255] or None, len(order),
                               epoch + datetime.timedelta(milliseconds=round(t0 * 1000)),
                               epoch + datetime.timedelta(milliseconds=round(t1 * 1000)),
                               (t1 - t0) / 86400.0, km, info.get("sex") or None,
                               (info.get("life_stage") or "")[:120] or None])
                n_lines += 1
                step(91 + 7.0 * (k + 1) / max(1, len(tracks)), "Building the track lines: {} of {}".format(
                    k + 1, len(tracks)))

    if _made is not None:
        del _made[:]                   # the outputs are whole: nothing that follows may remove them

    # ---- the report ---------------------------------------------------------
    left = []
    if skipped_flag:
        left.append("{} flagged as outliers by the data owner".format(n0(skipped_flag)))
    if skipped_animal:
        left.append("{} of animals that were not asked for".format(n0(skipped_animal)))
    if skipped_date:
        left.append("{} outside the dates asked for".format(n0(skipped_date)))
    if bad:
        left.append("{} whose coordinates or time could not be read".format(n0(bad)))
    if want_points:
        say("Wrote {} points to {}.".format(n0(written), out_points))
    if want_lines:
        say("Wrote {} track {} to {}, one for each animal, through its fixes in order of time.{}".format(
            n0(n_lines), "line" if n_lines == 1 else "lines", out_lines,
            " {} with fewer than two different locations {} no line.".format(
                "1 animal" if single == 1 else "{} animals".format(single), "has" if single == 1 else "have")
            if single else ""))
    if left:
        say("Left out: {}.".format("; ".join(left)))
    if include_flagged and sc.flagged:
        warn("{} fixes that the data owner flagged as outliers were included. Their \"visible\" field "
             "is false.".format(n0(sc.flagged)))
    if truncated:
        say("{} long text values were shortened to fit their fields.".format(n0(truncated)))
    if written == 0:
        warn("No fixes matched the animals and dates asked for, so the output is empty.")
    cite = package["citation"] if package else ((disk or {}).get("citation") or None)
    if package:
        say("License: {}{}.".format(package["license"] or "not stated",
                                    " ({})".format(package["license_url"]) if package["license_url"] else ""))
        if cite:
            say("Cite this dataset as: " + cite)
        if package.get("source") == "study":
            say("Movebank strongly encourages users to make a reasonable attempt to contact a study's owner about "
                "the intended use of its data, and to offer co-authorship or other credit as appropriate.{} The "
                "study's page: {}".format(
                    " This study's owner: {}.".format(package["owner"]) if package.get("owner") else "",
                    package["page"]))
        if not cite and package.get("source") == "study":
            warn("The study gives no citation. Its page says who owns it and how to reach them: {} Movebank "
                 "asks that the owner be contacted about the intended use, and credited.".format(package["page"]))
        if package["paper"]:
            say("The data belong to the publication {}, which should be cited as well.".format(
                package["paper"].replace("doi:", "https://doi.org/")))
        say("All use of Movebank data is subject to the dataset's license and to Movebank's general "
            "terms of use. Read the README in the download folder before using the data.")
    elif disk:
        if disk.get("license"):
            say("License, as the README gives it: {}.".format(disk["license"]))
        if cite:
            say("The README gives this citation for the data: " + cite)
        say("All use of Movebank data is subject to the dataset's license and to Movebank's general "
            "terms of use. Read the README in the folder before using the data.")

    # ---- metadata ---------------------------------------------------------
    summary = ("Created {} by the Movebank Animal Tracks tool from {}. {} location fixes of {} animals{}. "
               "{}{}{}".format(
                   datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   ("the Movebank study \"{}\" (study ID {}; license {}; {}), read with the Movebank account {}. "
                    "A study is not a published dataset: its owner can change it".format(
                        package["title"], package["study_id"], package["license"], package["page"],
                        package["account"])
                    if package and package.get("source") == "study" else
                    "the published dataset \"{}\" (DOI {}; license {}) in the Movebank Data Repository".format(
                       package["title"], package["doi"] or "none", package["license"] or "not stated")
                    if package else "Movebank data files on disk ({}){}".format(
                        "; ".join(os.path.basename(p) for p in data_paths),
                        "" if not disk
                        else ", which the README beside them names as the Movebank study \"{}\" (study ID {}{})".format(
                            disk["name"], disk["study_id"], "; fetched " + disk["fetched"] if disk["fetched"] else "")
                        if disk["kind"] == "study"
                        else ", which the README beside them names as a dataset published in the Movebank Data "
                             "Repository{}".format(" (DOI {})".format(disk["doi"]) if disk["doi"] else ""))),
                   n0(written), n0(len(tracks) if want_lines else len(set(only) or sc.animals)),
                   ", {} to {} (UTC)".format(sc.first[:10], sc.last[:10]) if sc.first and sc.last else "",
                   "Left out: {}. ".format("; ".join(left)) if left else "",
                   "Fixes flagged as outliers were included. " if include_flagged else "",
                   "Original files: {}.".format(folder)))
    credits = cite or ("Movebank study \"{}\" ({}).".format(package["title"], package["page"])
                       if package and package.get("source") == "study" else "Movebank (https://www.movebank.org).")
    # The README of THIS dataset: the one fetched or written in this run. The download folder is named
    # for the output feature class, so it can also hold the README of a dataset loaded there earlier, and
    # that one must not lend its description and terms of use to this output. Only for files already on
    # disk, which name no README, is the folder searched.
    if package:
        readme = read_text_file(readme_path) if readme_path else None
    else:
        readme = read_readme(folder) if folder else None
    terms = readme_terms(readme)
    limits = None
    if package or terms:
        limits = text_as_html(
            ("License: {}{}.\n\n".format(package["license"] or "not stated",
                                        " ({})".format(package["license_url"]) if package["license_url"] else "")
             if package else "")
            + ("Terms of use, from the dataset's README:\n\n" + terms + "\n\n" if terms else "")
            + "All use of data from Movebank is also subject to Movebank's general terms of use "
              "(https://www.movebank.org/cms/movebank-content/general-movebank-terms-of-use).")
    readme_html = ""
    if readme:
        # whose README it is: the one this tool writes for a study, the one the repository publishes with a
        # dataset, or (for files on disk whose README is neither) simply the one that was found
        kind = ("study" if package and package.get("source") == "study" else "published" if package
                else disk["kind"] if disk else "found")
        readme_html = ({"study": "<p><b>The README this tool wrote from the study's record at Movebank, with the "
                                 "study's terms of use:</b></p>",
                        "published": "<p><b>The dataset's README, as the Movebank Data Repository published "
                                     "it:</b></p>",
                        "found": "<p><b>The README found beside the files:</b></p>"}[kind]
                       + text_as_html(readme))
        say("The {} is recorded in the metadata of the output{}: the whole of it under "
            "Description, and its terms of use under Use Limitations. Right-click the layer and choose "
            "View Metadata to read it.".format(
                {"study": "README this tool wrote for the study, with its terms of use,",
                 "published": "dataset's README", "found": "README found beside the files"}[kind],
                "s" if want_points and want_lines else ""))
    if want_points:
        _stamp(out_points, "<p>Animal location fixes from Movebank. Each point is one fix: where a tagged "
               "animal was at one time, as its tag recorded it. The fields are the columns of Movebank's "
               "data file, described in the Movebank Attribute Dictionary. Coordinates are WGS 84 and "
               "times are UTC.</p>" + readme_html, summary, credits,
               [{"name": nm, "description": CORE_DESCRIPTIONS.get(nm, "")} for nm in point_names
                if CORE_DESCRIPTIONS.get(nm)]
               + [{"name": nm, "description": "The Movebank attribute \"{}\", as the data file gives it. See the "
                   "Movebank Attribute Dictionary for its definition.".format(c)} for c, nm, _t, _l in extras],
               messages, limits)
    if want_lines:
        _stamp(out_lines, "<p>Animal track lines from Movebank. Each line joins one animal's location fixes "
               "in order of time. A line shows the order of the fixes, not the path the animal took "
               "between them. Coordinates are WGS 84 and times are UTC.</p>" + readme_html, summary, credits,
               [{"name": nm, "description": d} for nm, _a, _t, _l, d in LINE_FIELDS], messages, limits)
    step(100, "Done: {} fixes{}.".format(n0(written), ", {} track lines".format(n0(n_lines)) if want_lines else ""))
    return {"points": out_points, "lines": out_lines, "written": written, "line_count": n_lines,
            "doi": package["doi"] if package else (disk or {}).get("doi", ""), "citation": cite or "",
            "folder": folder, "study_id": (package or {}).get("study_id", "") or (disk or {}).get("study_id", "")}


def describe_readme(text):
    """What a README beside Movebank files says the files are, or None when
    it does not say (or there is no README).

    Two READMEs are known. The one this tool writes beside a study it
    fetched gives {"kind": "study", "name", "study_id", "page", "fetched",
    "license", "citation"}. The one the Movebank Data Repository publishes
    with a dataset gives {"kind": "published", "citation", "doi"}."""
    if not text:
        return None
    m = re.match(r"\s*Movebank study: (.*)\nMovebank study ID: (\d+)\nStudy page: (\S+)", text)
    if m:
        when = re.search(r"^Fetched (\d{4}-\d{2}-\d{2})", text, flags=re.M)
        lic = re.search(r"^License: (.+)$", text, flags=re.M)
        cit = re.search(r"^Citation: (.*?)(?=^(?:Acknowledgements|Grants used|Taxa|Sensors|Study objective): "
                        r"|^-{5,}|\Z)", text, flags=re.M | re.S)
        return {"kind": "study", "name": m.group(1).strip(), "study_id": m.group(2), "page": m.group(3),
                "fetched": when.group(1) if when else "", "license": lic.group(1).strip() if lic else "",
                "citation": " ".join(cit.group(1).split()) if cit else "", "doi": ""}
    m = re.search(r"^Data package citation:[ \t]*\n(.+?)(?:\n[ \t]*\n|\Z)", text, flags=re.M | re.S)
    if m and "Movebank Data Repository" in text[:800]:
        citation = " ".join(m.group(1).split())
        doi = re.search(r"https?://doi\.org/(10\.5441/[^\s]+)", citation)
        return {"kind": "published", "citation": citation, "doi": doi.group(1).rstrip(".") if doi else "",
                "license": ""}
    return None


def read_readme(folder):
    """The text of the dataset's README in a folder of Movebank files, or
    None when there is none (a folder of files downloaded by hand may
    have none)."""
    try:
        names = sorted(n for n in os.listdir(folder)
                       if "readme" in n.lower() and os.path.isfile(os.path.join(folder, n)))
    except OSError:
        return None
    for name in names:
        text = read_text_file(os.path.join(folder, name))
        if text:
            return text
    return None


def read_text_file(path):
    """The text of a README file, or None."""
    try:
        with open(path, "rb") as f:
            text = f.read(400000).decode("utf-8", "replace")
    except OSError:
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n").strip() or None


def readme_terms(text):
    """The Terms of Use section of a Movebank README, or None."""
    m = re.search(r"^[ \t]*Terms of Use[ \t]*\n(.*?)(?=\n[ \t]*-{5,}|\Z)", text or "", flags=re.S | re.M | re.I)
    terms = m.group(1).strip() if m else ""
    return terms or None


def text_as_html(text):
    """Plain text as the paragraphs of a metadata element: blank lines
    separate paragraphs, and single line breaks are kept."""
    import html as _html

    paras = [p.strip("\n") for p in re.split(r"\n[ \t]*\n", text) if p.strip()]
    return "".join("<p>{}</p>".format(_html.escape(p, quote=False).replace("\n", "<br/>")) for p in paras)


def _stamp(fc, description, summary, credits, fields, messages, limits=None):
    """Never raises: metadata is a courtesy, not the result."""
    try:
        import arcpy
        import tool_metadata

        md = arcpy.metadata.Metadata(fc)
        md.title = os.path.basename(fc)
        md.tags = "Movebank, animal tracking, telemetry, animal movement, Wildlife and Forestry Tools"
        md.summary = summary
        md.description = description
        md.credits = credits
        if limits:
            md.accessConstraints = limits
        md.save()
        tool_metadata.set_entity_and_fields(fc, entity="Movebank animal tracking data.", fields=fields,
                                            messages=messages)
    except Exception as e:                                       # noqa: BLE001
        if messages is not None:
            try:
                messages.addWarningMessage("Could not write the output's metadata: {}".format(e))
            except Exception:                                    # noqa: BLE001
                pass
