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

"""GBIF Species Occurrences: fetch occurrence records from the Global
Biodiversity Information Facility and write them as points.

This is the engine behind the geoprocessing tool, and behind the last
step of the GBIF Species Occurrences window, which runs the tool so
that every run leaves a Geoprocessing History entry.

Three ways to get the records:
  * fetch them directly from GBIF's search service (up to 100,000);
  * ask GBIF to prepare a download (any number; gives a DOI; needs the
    user's GBIF account), wait for it and load it;
  * load a download that already exists, named by its GBIF key or by
    the path of its zip file.

The filter here must produce the same GBIF "predicate" as the window's
GbifFilter.cs, and the writer the same rows as GbifFeatureWriter.cs;
gbif_parity_test.py checks both.

Only the Python standard library and arcpy are used.
"""

import base64
import datetime
import io
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

V1 = "https://api.gbif.org/v1/"
V2 = "https://api.gbif.org/v2/"
BACKBONE = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"
USER_AGENT = "WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)"
CREDENTIAL_TARGET = "JennessWildlifeTools.GBIF"

MAX_PAGE = 300                  # records per search page
MAX_SEARCH = 100000             # the search service will not page past this
MAX_DOWNLOAD_POLYGON_POINTS = 10000

METHOD_FETCH = "Fetch directly"
METHOD_DOWNLOAD = "Request a GBIF download"
METHOD_LOAD = "Load an existing GBIF download"
METHODS = [METHOD_FETCH, METHOD_DOWNLOAD, METHOD_LOAD]

# kinds of record -> GBIF basis-of-record codes (the window's checkboxes)
RECORD_KINDS = [
    ("Observations", ["HUMAN_OBSERVATION", "OBSERVATION",
                      "MACHINE_OBSERVATION", "OCCURRENCE"]),
    ("Museum and herbarium specimens", ["PRESERVED_SPECIMEN"]),
    ("Material samples", ["MATERIAL_SAMPLE", "MATERIAL_CITATION"]),
    ("Fossils", ["FOSSIL_SPECIMEN"]),
    ("Zoos and gardens", ["LIVING_SPECIMEN"]),
]
RECORD_KIND_NAMES = [k for k, _ in RECORD_KINDS]
RECORD_KIND_DEFAULT = RECORD_KIND_NAMES[:3]

DOWNLOADS_FOLDER = "Data_Downloads"
KEY_RE = re.compile(r"^\d{7}-\d{15}$")

IMAGE_URL_LENGTH = 500
# multimedia.txt of a Darwin Core Archive is read into memory to find each
# record's first photograph; above this size (uncompressed) it is not read
MAX_MULTIMEDIA_BYTES = 200 * 1024 * 1024

# The output fields: (name, alias, kind, text length, search-record key,
# download-table column, description). Kinds: T text, I whole number,
# D decimal number. The same list, in the same order, as
# GbifFeatureWriter.Fields.
FIELDS = [
    ("gbifID", "GBIF record ID", "T", 20, "key", "gbifID",
     "GBIF's identifier of the occurrence record."),
    ("scientificName", "Scientific name", "T", 255, "scientificName", "scientificName",
     "The scientific name GBIF assigned to the record, with its authorship."),
    ("species", "Species", "T", 150, "species", "species",
     "The species, without authorship (empty when the record is identified only to a higher rank)."),
    ("taxonRank", "Rank of the identification", "T", 30, "taxonRank", "taxonRank",
     "The taxonomic rank the record is identified to (species, subspecies, genus, ...)."),
    ("kingdom", "Kingdom", "T", 60, "kingdom", "kingdom", "Kingdom in the GBIF backbone taxonomy."),
    ("phylum", "Phylum", "T", 60, "phylum", "phylum", "Phylum in the GBIF backbone taxonomy."),
    ("class_", "Class", "T", 60, "class", "class", "Class in the GBIF backbone taxonomy."),
    ("order_", "Order", "T", 60, "order", "order", "Order in the GBIF backbone taxonomy."),
    ("family", "Family", "T", 60, "family", "family", "Family in the GBIF backbone taxonomy."),
    ("genus", "Genus", "T", 60, "genus", "genus", "Genus in the GBIF backbone taxonomy."),
    ("taxonKey", "GBIF taxon key", "T", 20, "taxonKey", "taxonKey",
     "GBIF backbone key of the taxon the record is identified to."),
    ("speciesKey", "GBIF species key", "T", 20, "speciesKey", "speciesKey",
     "GBIF backbone key of the record's species."),
    ("eventDate", "Date of the record", "T", 60, "eventDate", "eventDate",
     "Date (or range of dates) of the occurrence, as text in ISO 8601 form."),
    ("year", "Year", "I", 0, "year", "year", "Year of the occurrence."),
    ("month", "Month", "I", 0, "month", "month", "Month of the occurrence (1-12)."),
    ("day", "Day", "I", 0, "day", "day", "Day of the month of the occurrence."),
    ("decimalLatitude", "Latitude (WGS 84)", "D", 0, "decimalLatitude", "decimalLatitude",
     "Latitude in decimal degrees, WGS 84, as GBIF interpreted it."),
    ("decimalLongitude", "Longitude (WGS 84)", "D", 0, "decimalLongitude", "decimalLongitude",
     "Longitude in decimal degrees, WGS 84, as GBIF interpreted it."),
    ("coordUncert_m", "Coordinate uncertainty (m)", "D", 0,
     "coordinateUncertaintyInMeters", "coordinateUncertaintyInMeters",
     "The stated uncertainty of the coordinates, in meters (empty when the publisher stated none)."),
    ("infoWithheld", "Information withheld", "T", 500, "informationWithheld", "informationWithheld",
     "The publisher's note on information that exists but is not shared, such as an obscured location."),
    ("dataGeneralized", "Data generalizations", "T", 500, "dataGeneralizations", "dataGeneralizations",
     "The publisher's note on how the shared data were made less specific than the original."),
    ("elevation_m", "Elevation (m)", "D", 0, "elevation", "elevation",
     "Elevation in meters, as GBIF interpreted it."),
    ("individualCount", "Individual count", "I", 0, "individualCount", "individualCount",
     "Number of individuals the record represents."),
    ("sex", "Sex", "T", 60, "sex", "sex", "Sex of the organism, where the publisher recorded it."),
    ("lifeStage", "Life stage", "T", 60, "lifeStage", "lifeStage",
     "Age class or life stage of the organism, where the publisher recorded it."),
    ("reproCondition", "Reproductive condition", "T", 120, "reproductiveCondition", "reproductiveCondition",
     "Reproductive condition of the organism, where the publisher recorded it."),
    ("behavior", "Behavior", "T", 255, "behavior", "behavior",
     "Behavior of the organism when recorded, where the publisher recorded it."),
    ("occurrenceStatus", "Present or absent", "T", 20, "occurrenceStatus", "occurrenceStatus",
     "PRESENT, or ABSENT for a record of a species looked for and not found."),
    ("basisOfRecord", "Basis of record", "T", 40, "basisOfRecord", "basisOfRecord",
     "The kind of record: an observation, a preserved specimen, a material sample, a fossil, a living specimen."),
    ("establishmentMeans", "Establishment means", "T", 60, "establishmentMeans", "establishmentMeans",
     "Whether the organism is native, introduced and so on, where the publisher recorded it."),
    ("countryCode", "Country code", "T", 4, "countryCode", "countryCode",
     "Two-letter ISO code of the country or area of the record."),
    ("stateProvince", "State or province", "T", 120, "stateProvince", "stateProvince",
     "State or province, in the publisher's spelling."),
    ("county", "County", "T", 120, "county", "county", "County or equivalent, in the publisher's spelling."),
    ("locality", "Locality", "T", 500, "locality", "locality",
     "The publisher's description of the place."),
    ("recordedBy", "Recorded by", "T", 255, "recordedBy", "recordedBy",
     "The people who recorded the occurrence, separated by semicolons."),
    ("identifiedBy", "Identified by", "T", 255, "identifiedBy", "identifiedBy",
     "The people who identified the organism, separated by semicolons."),
    ("institutionCode", "Institution code", "T", 100, "institutionCode", "institutionCode",
     "Code of the institution that holds the specimen or record."),
    ("collectionCode", "Collection code", "T", 100, "collectionCode", "collectionCode",
     "Code of the collection within the institution."),
    ("catalogNumber", "Catalog number", "T", 100, "catalogNumber", "catalogNumber",
     "The record's identifier within its collection (text, so leading zeros are kept)."),
    ("datasetKey", "GBIF dataset key", "T", 40, "datasetKey", "datasetKey",
     "GBIF's key of the dataset (the source) that published the record."),
    ("datasetName", "Source dataset", "T", 255, "datasetName", "datasetName",
     "GBIF's title of the dataset that published the record."),
    ("iucnRedList", "IUCN Red List category", "T", 10, "iucnRedListCategory", "iucnRedListCategory",
     "IUCN Red List category of the species, as GBIF reports it (LC, NT, VU, EN, CR, ...)."),
    ("license", "License of the record", "T", 80, "license", "license",
     "Web address of the legal text of the record's license (CC0, CC BY or CC BY-NC)."),
    ("rightsHolder", "Rights holder", "T", 255, "rightsHolder", "rightsHolder",
     "The person or organization that owns or manages the rights to the record."),
    ("issues", "GBIF issue flags", "T", 500, "issue", "issue",
     "The data-quality flags GBIF attached to the record, separated by semicolons."),
    ("mediaType", "Media attached", "T", 60, "mediaType", "mediaType",
     "StillImage when the record carries a photograph."),
    ("photoURL", "First photograph", "T", IMAGE_URL_LENGTH, "photoURL", None,
     "Web address of the record's first photograph (empty when the record has none, and for a download in "
     "GBIF's Simple format, which does not carry it). The photograph has its own license and credit, shown on "
     "the record's page on GBIF.org."),
    ("recordURL", "Record on GBIF.org", "T", 60, None, None,
     "Web address of the record's page on GBIF.org."),
]
FIELD_NAMES = [f[0] for f in FIELDS]

LICENSE_URLS = {
    "CC0_1_0": "http://creativecommons.org/publicdomain/zero/1.0/legalcode",
    "CC_BY_4_0": "http://creativecommons.org/licenses/by/4.0/legalcode",
    "CC_BY_NC_4_0": "http://creativecommons.org/licenses/by-nc/4.0/legalcode",
}


class GbifError(Exception):
    """A failure the user can act on; shown without a traceback."""

    def __init__(self, message, status=0):
        Exception.__init__(self, message)
        self.status = status


class Cancelled(Exception):
    """The user cancelled the tool."""


def n0(n):
    """12345 -> '12,345'."""
    return "{:,}".format(int(n))


def span(seconds):
    """A duration in plain words."""
    s = int(round(seconds))
    if s < 90:
        return "{} seconds".format(s)
    m = int(round(s / 60.0))
    if m < 90:
        return "{} minutes".format(m)
    return "{:.1f} hours".format(s / 3600.0)


def size_words(n):
    n = float(n)
    if n < 1024:
        return "{:.0f} bytes".format(n)
    if n < 1024 * 1024:
        return "{:.0f} KB".format(n / 1024)
    if n < 1024 ** 3:
        return "{:.1f} MB".format(n / 1024 ** 2)
    return "{:.2f} GB".format(n / 1024 ** 3)


# ---------------------------------------------------------------------------
#  the GBIF sign-in
# ---------------------------------------------------------------------------
def stored_account():
    """(user name, password) of the GBIF sign-in, or None.

    The sign-in is the one the GBIF Species Occurrences window keeps in
    Windows Credential Manager, under the user's own Windows login (entry
    "JennessWildlifeTools.GBIF"). For a computer or an account that has
    no such entry (a scheduled task, say) the environment variables
    GBIF_USER and GBIF_PWD are read instead. The password is never
    written to a message, a file or a parameter.
    """
    got = _read_windows_credential(CREDENTIAL_TARGET)
    if got and got[0]:
        return got
    user = os.environ.get("GBIF_USER")
    pwd = os.environ.get("GBIF_PWD")
    if user and pwd:
        return user, pwd
    return None


def stored_user_name():
    """The stored GBIF user name alone, or None."""
    try:
        got = stored_account()
    except Exception:                                            # noqa: BLE001
        return None
    return got[0] if got else None


def _read_windows_credential(target):
    """Reads a generic credential (CredReadW). None when there is none
    or when not on Windows."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(ptr)):      # 1 = generic
        return None
    try:
        cred = ptr.contents
        user = cred.UserName
        size = int(cred.CredentialBlobSize)
        pwd = ""
        if size > 0 and cred.CredentialBlob:
            pwd = ctypes.string_at(cred.CredentialBlob, size).decode("utf-16-le")
        return user, pwd
    finally:
        advapi.CredFree(ptr)


# ---------------------------------------------------------------------------
#  talking to GBIF
# ---------------------------------------------------------------------------
def explain(status, body):
    """GBIF's error text, shortened, with a plain lead-in."""
    detail = (body or "").strip()
    if detail.startswith("<"):
        detail = ""                                  # an HTML error page
    if len(detail) > 400:
        detail = detail[:400] + "..."
    if status == 401:
        lead = "GBIF did not accept the user name and password."
    elif status == 413:
        lead = "GBIF refused the request as too large."
    elif status in (420, 429):
        lead = ("GBIF is limiting requests from this computer or this account. Wait a minute and "
                "try again, or request a download instead.")
    elif status == 503:
        lead = "GBIF is busy or unavailable right now. Try again in a few minutes."
    else:
        lead = "GBIF returned an error ({}).".format(status)
    return lead if not detail else lead + " " + detail


def _open(url, data=None, auth=None, accept="application/json", method=None, timeout=100):
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if auth:
        token = base64.b64encode("{}:{}".format(auth[0], auth[1]).encode("utf-8")).decode("ascii")
        headers["Authorization"] = "Basic " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:                                        # noqa: BLE001
            body = ""
        raise GbifError(explain(e.code, body), e.code)
    except urllib.error.URLError as e:
        raise GbifError("GBIF could not be reached ({}). Check the internet connection.".format(
            getattr(e, "reason", e)))
    except (TimeoutError, OSError) as e:
        raise GbifError("GBIF did not answer in time ({}). Try again.".format(e))


def _text(url, data=None, auth=None, accept="application/json", method=None, tries=1):
    """The body of a response as text. ``tries`` > 1 repeats a request
    that failed for a reason that may pass (network, 5xx)."""
    last = None
    for attempt in range(max(1, tries)):
        try:
            resp = _open(url, data, auth, accept, method)
            try:
                return resp.read().decode("utf-8", "replace")
            finally:
                resp.close()
        except GbifError as e:
            last = e
            if e.status and e.status < 500:
                raise
            if attempt + 1 < tries:
                time.sleep(2 + 3 * attempt)
        except (TimeoutError, OSError, http.client.HTTPException) as e:   # the last: an answer cut short
            last = GbifError("GBIF did not answer in time ({}). Try again.".format(e))
            if attempt + 1 < tries:
                time.sleep(2 + 3 * attempt)
    raise last


def _json(url, data=None, auth=None, tries=3, raw_numbers=False, repeatable=False):
    """The JSON of a response. A request that failed for a reason that may
    pass, or whose answer could not be read, is repeated up to ``tries``
    times. A request with a body is sent ONCE unless it is ``repeatable``:
    a search is (it only reads), a download request is not (repeating it
    would ask GBIF for a second download)."""
    n = max(1, tries if (data is None or repeatable) else 1)
    for attempt in range(n):
        text = _text(url, data, auth, tries=n)
        try:
            if raw_numbers:
                # numbers stay as the text GBIF sent, as in the window's reader
                return json.loads(text or "null", parse_float=str, parse_int=str)
            return json.loads(text or "null")
        except ValueError:
            if attempt + 1 >= n:
                raise GbifError("GBIF returned something that is not the expected data.")
            time.sleep(2 + 3 * attempt)


def _items(root):
    if isinstance(root, list):
        return root
    if isinstance(root, dict) and isinstance(root.get("results"), list):
        return root["results"]
    return []


# ---------------------------------------------------------------------------
#  the filter
# ---------------------------------------------------------------------------
def _num(v):
    """Six decimals of a degree (about 0.1 m), without trailing zeros."""
    s = "{:.6f}".format(round(float(v), 6)).rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


def close_ring(ring):
    r = []
    for p in ring:
        if not r or r[-1][0] != p[0] or r[-1][1] != p[1]:
            r.append((p[0], p[1]))
    if r and (r[0][0] != r[-1][0] or r[0][1] != r[-1][1]):
        r.append(r[0])
    return r


def signed_area(ring):
    a = 0.0
    j = len(ring) - 1
    for i in range(len(ring)):
        a += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]
        j = i
    return a / 2.0


def convex_hull(points):
    """Andrew's monotone chain; counter-clockwise, not closed."""
    p = sorted(set(points))
    if len(p) < 3:
        return p

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    h = []
    for q in p:
        while len(h) >= 2 and cross(h[-2], h[-1], q) <= 0:
            h.pop()
        h.append(q)
    lower = len(h) + 1
    for q in reversed(p[:-1]):
        while len(h) >= lower and cross(h[-2], h[-1], q) <= 0:
            h.pop()
        h.append(q)
    h.pop()
    return h


def enclosing_polygon(hull, most):
    """A convex polygon of at most ``most`` vertices that CONTAINS the
    convex polygon ``hull`` (counter-clockwise, not closed). A hull can
    have as many vertices as the outline it was made from: a finely drawn
    circle is its own hull. The polygon is made of evenly spaced edges of
    the hull, each extended until it meets the next one, so it touches the
    hull along those edges and lies outside it everywhere else. When that
    cannot be done (edges that are parallel, or that turn through half a
    circle or more between one and the next), the rectangle around the
    hull is returned instead. Returns (polygon, how) with how one of
    "hull", "edges", "rectangle"."""
    n = len(hull)
    if n <= most:
        return hull, "hull"
    xs = [p[0] for p in hull]
    ys = [p[1] for p in hull]
    box = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    m = max(8, most // 2)
    picks = sorted(set(int(k * n / float(m)) for k in range(m)))
    edges = [(hull[i], hull[(i + 1) % n]) for i in picks]
    out = []
    for k in range(len(edges)):
        (ax, ay), (bx, by) = edges[k]
        (cx, cy), (dx, dy) = edges[(k + 1) % len(edges)]
        r = (bx - ax, by - ay)
        s = (dx - cx, dy - cy)
        den = r[0] * s[1] - r[1] * s[0]                  # > 0: the next edge turns left, by less than half a circle
        if den <= 1e-18 * span * span:
            return box, "rectangle"
        u = ((cx - ax) * s[1] - (cy - ay) * s[0]) / den
        x, y = ax + u * r[0], ay + u * r[1]
        if abs(x - xs[0]) > 100.0 * span or abs(y - ys[0]) > 100.0 * span:
            return box, "rectangle"                       # nearly parallel edges meet far away
        out.append((x, y))
    # proof: every vertex of the hull is inside or on the polygon
    tol = 1e-9 * span * span
    for k in range(len(out)):
        (ax, ay), (bx, by) = out[k], out[(k + 1) % len(out)]
        for (px, py) in hull[::max(1, n // 20000)]:
            if (bx - ax) * (py - ay) - (by - ay) * (px - ax) < -tol:
                return box, "rectangle"
    if min(p[1] for p in out) < -90.0 or max(p[1] for p in out) > 90.0 \
            or min(p[0] for p in out) < -180.0 or max(p[0] for p in out) > 180.0:
        return [(max(-180.0, x), max(-90.0, y)) if (x <= 0 or y <= 0) else (min(180.0, x), min(90.0, y))
                for x, y in box], "rectangle"
    return out, "edges"


class GbifFilter(object):
    """What is asked of GBIF. The twin of GbifFilter.cs."""

    def __init__(self):
        self.taxon_keys = []
        # polygons in WGS 84: each a list of rings (outer first), a ring a
        # list of (lon, lat)
        self.polygons = []
        self.country = None
        self.state_province = None
        self.include_no_coordinates = False
        self.year_from = None
        self.year_to = None
        self.months = []
        self.basis_of_record = []
        self.licenses = []
        self.exclude_geospatial_issues = False
        self.excluded_dataset_keys = []
        self.exclude_centroids = False
        self.present_only = False
        self.max_uncertainty_m = None
        self.images_only = False

    def polygon_point_count(self):
        return sum(len(close_ring(ring)) for poly in self.polygons for ring in poly)

    def polygon_fits_download(self):
        return self.polygon_point_count() <= MAX_DOWNLOAD_POLYGON_POINTS

    def to_wkt(self):
        parts = []
        for poly in self.polygons:
            rings = []
            for i, raw in enumerate(poly):
                ring = close_ring(raw)
                if len(ring) < 4:
                    continue
                ccw = signed_area(ring) > 0
                if ccw != (i == 0):                 # outer ccw, holes cw
                    ring.reverse()
                rings.append("(" + ",".join(_num(x) + " " + _num(y) for x, y in ring) + ")")
            if rings:
                parts.append("(" + ",".join(rings) + ")")
        if not parts:
            return None
        if len(parts) == 1:
            return "POLYGON" + parts[0]
        return "MULTIPOLYGON(" + ",".join(parts) + ")"

    def predicate(self):
        """The filter as GBIF's predicate (a dict), or None."""
        def eq(key, value, kind="equals"):
            return {"type": kind, "key": key, "value": value}

        def isin(key, values):
            return {"type": "in", "key": key, "values": list(values)}

        c = []
        if len(self.taxon_keys) == 1:
            c.append(eq("TAXON_KEY", self.taxon_keys[0]))
        elif len(self.taxon_keys) > 1:
            c.append(isin("TAXON_KEY", self.taxon_keys))
        wkt = self.to_wkt()
        if wkt is not None:
            within = {"type": "within", "geometry": wkt}
            if self.include_no_coordinates:
                c.append({"type": "or", "predicates": [within, eq("HAS_COORDINATE", "false")]})
            else:
                c.append(within)
        elif not self.include_no_coordinates:
            c.append(eq("HAS_COORDINATE", "true"))
        if self.country:
            c.append(eq("COUNTRY", self.country))
        if self.state_province:
            c.append(eq("STATE_PROVINCE", self.state_province))
        if self.year_from is not None:
            c.append(eq("YEAR", str(int(self.year_from)), "greaterThanOrEquals"))
        if self.year_to is not None:
            c.append(eq("YEAR", str(int(self.year_to)), "lessThanOrEquals"))
        months = sorted(set(int(m) for m in self.months if 1 <= int(m) <= 12))
        if 0 < len(months) < 12:
            c.append(isin("MONTH", [str(m) for m in months]))
        if self.basis_of_record:
            c.append(isin("BASIS_OF_RECORD", self.basis_of_record))
        if self.licenses:
            c.append(isin("LICENSE", self.licenses))
        if self.exclude_geospatial_issues:
            c.append(eq("HAS_GEOSPATIAL_ISSUE", "false"))
        if self.excluded_dataset_keys:
            c.append({"type": "not", "predicate": isin("DATASET_KEY", self.excluded_dataset_keys)})
        if self.exclude_centroids:
            c.append({"type": "isNull", "parameter": "DISTANCE_FROM_CENTROID_IN_METERS"})
        if self.present_only:
            c.append(eq("OCCURRENCE_STATUS", "PRESENT"))
        if self.max_uncertainty_m is not None:
            c.append(eq("COORDINATE_UNCERTAINTY_IN_METERS", _num(self.max_uncertainty_m),
                        "lessThanOrEquals"))
        if self.images_only:
            c.append(eq("MEDIA_TYPE", "StillImage"))
        if not c:
            return None
        if len(c) == 1:
            return c[0]
        return {"type": "and", "predicates": c}

    def with_convex_hull(self):
        """A copy whose study area is the convex hull of all polygons."""
        import copy
        f = copy.copy(self)
        pts = []
        for poly in self.polygons:
            if poly:
                pts.extend((p[0], p[1]) for p in poly[0])
        hull = convex_hull(pts)
        # a hull can itself have more vertices than the service accepts
        hull, f.hull_how = enclosing_polygon(hull, MAX_DOWNLOAD_POLYGON_POINTS - 10)
        f.hull_vertices = len(hull)
        f.polygons = [[hull]] if len(hull) >= 3 else []
        return f

    def contains(self, lon, lat):
        """Even-odd point-in-polygon test (holes respected). The edges are
        sorted into bands of latitude the first time it is asked, so each
        point is tested against the few edges at its own latitude."""
        if not self.polygons:
            return True
        key = (id(self.polygons), len(self.polygons))
        index = getattr(self, "_index", None)
        if index is None or index[0] != key:
            index = self._index = (key, PolygonIndex(self.polygons))
        return index[1].contains(lon, lat)


class PolygonIndex(object):
    """Even-odd point-in-polygon for many points against the same polygons
    (each a list of rings of (lon, lat), holes respected).

    Testing a point against every edge is too slow when both numbers are
    large: 200,000 records against an outline of 30,000 vertices is six
    billion steps. Here each polygon's edges are sorted once into bands of
    latitude, and a point is tested against the edges of its own band
    only: the ones that can cross its latitude. The answers are the same
    as the plain test's, edge for edge."""

    def __init__(self, polygons):
        self._polys = []
        for poly in polygons:
            edges, xs, ys = [], [], []
            for ring in poly:
                j = len(ring) - 1
                for i in range(len(ring)):
                    a, b = ring[i], ring[j]
                    j = i
                    xs.append(a[0])
                    ys.append(a[1])
                    if a[1] != b[1]:                # a level edge never crosses a latitude
                        edges.append((a[0], a[1], b[0], b[1]))
            if not edges:
                continue
            y0, y1 = min(ys), max(ys)
            n = max(1, min(4096, len(edges) // 4))
            h = (y1 - y0) / n
            bands = [[] for _ in range(n)]
            for e in edges:
                lo, hi = (e[1], e[3]) if e[1] < e[3] else (e[3], e[1])
                for k in range(self._band(lo, y0, h, n), self._band(hi, y0, h, n) + 1):
                    bands[k].append(e)
            self._polys.append((min(xs), y0, max(xs), y1, h, n, bands))

    @staticmethod
    def _band(y, y0, h, n):
        return min(n - 1, max(0, int((y - y0) / h))) if h > 0 else 0

    def contains(self, lon, lat):
        for x0, y0, x1, y1, h, n, bands in self._polys:
            if lon < x0 or lon > x1 or lat < y0 or lat > y1:
                continue                            # outside the rectangle around the polygon
            inside = False
            for ax, ay, bx, by in bands[self._band(lat, y0, h, n)]:
                if (ay > lat) != (by > lat) and lon < (bx - ax) * (lat - ay) / (by - ay) + ax:
                    inside = not inside
            if inside:
                return True
        return False


# ---------------------------------------------------------------------------
#  names
# ---------------------------------------------------------------------------
_KEY_TAIL = re.compile(r"[\(\[]\s*(\d+)\s*[\)\]]\s*$")


def split_list(text):
    """'a; b\\nc' -> ['a', 'b', 'c'] (quotes and blanks dropped)."""
    out = []
    for part in re.split(r"[;\r\n]+", text or ""):
        s = part.strip().strip("'\"").strip()
        if s:
            out.append(s)
    return out


def resolve_taxa(text, say):
    """The GBIF backbone keys for a list of names and keys.

    Each entry is a GBIF taxon key ("2440902"), a scientific name
    ("Antilocapra americana"), or a name followed by its key in
    brackets ("Antilocapra americana (2440902)": the key is used and
    the name is only a label). A name is matched against the GBIF
    backbone; a name GBIF cannot place stops the tool.
    """
    keys = []
    for entry in split_list(text):
        m = _KEY_TAIL.search(entry)
        key = None
        if m:
            key = m.group(1)
        elif entry.isdigit():
            key = entry
        if key is not None:
            try:
                t = _json(V2 + "taxon/" + BACKBONE + "/" + urllib.parse.quote(key))
            except GbifError as e:
                if e.status == 404:
                    raise GbifError("{} is not a taxon key in the GBIF backbone taxonomy.".format(key))
                raise
            say("Species or group: {} ({}; GBIF taxon key {}).".format(
                (t or {}).get("scientificName") or entry,
                ((t or {}).get("taxonRank") or "rank not given").lower(), key))
        else:
            r = _json(V2 + "species/match?scientificName=" + urllib.parse.quote(entry)) or {}
            usage = r.get("usage") or {}
            kind = (r.get("diagnostics") or {}).get("matchType") or "NONE"
            key = usage.get("key")
            found = usage.get("canonicalName") or usage.get("name")
            if not key or kind in ("NONE", "HIGHERRANK"):
                raise GbifError(
                    "GBIF does not know the name \"{}\"{}. Give the scientific name as GBIF spells it, "
                    "or the GBIF taxon key (the GBIF Species Occurrences window finds both, "
                    "from common names too).".format(
                        entry, "" if kind != "HIGHERRANK" else
                        " (the closest it has is the {} {})".format(
                            (usage.get("rank") or "taxon").lower(), found)))
            key = str(key)
            note = "" if kind == "EXACT" else " The name given was \"{}\"; GBIF read it as this one.".format(entry)
            say("Species or group: {} ({}; GBIF taxon key {}).{}".format(
                found, (usage.get("rank") or "rank not given").lower(), key, note))
        if key not in keys:
            keys.append(key)
    return keys


# ---------------------------------------------------------------------------
#  the study area
# ---------------------------------------------------------------------------
def _rings_of(geom):
    """The polygons of an arcpy Polygon as lists of rings of (x, y)."""
    polys = []
    for part in geom:
        rings, ring = [], []
        for pt in part:
            if pt is None:
                if len(ring) >= 3:
                    rings.append(ring)
                ring = []
            else:
                ring.append((pt.X, pt.Y))
        if len(ring) >= 3:
            rings.append(ring)
        if rings:
            # the outer ring is the largest; the rest are its holes
            rings.sort(key=lambda r: -abs(signed_area(r)))
            polys.append(rings)
    return polys


def _to_wgs84(geom):
    """Densifies where needed and projects a polygon to WGS 84."""
    import arcpy
    import clip_tools

    wgs84 = arcpy.SpatialReference(4326)
    sr = geom.spatialReference
    try:
        # straighten true curves, and add vertices along long straight
        # edges so they stay in place when the shape is unprojected
        ext = geom.extent
        step = max(ext.width, ext.height) / 200.0
        projected = sr is not None and getattr(sr, "type", "") == "Projected"
        if step > 0 and (getattr(geom, "hasCurves", False) or (projected and geom.pointCount < 400)):
            geom = geom.densify("DISTANCE", step, step)
    except Exception:                                            # noqa: BLE001
        pass
    if sr is not None and getattr(sr, "name", "") not in ("", "Unknown") and sr.factoryCode != 4326:
        geom = clip_tools.project_geometry(geom, wgs84)
    return geom


def study_area_polygons(layer=None, extent=None):
    """The study area as WGS 84 polygons (lists of rings), from a polygon
    layer (its selection and definition query are honored) or an extent.
    Polygons that overlap or adjoin are merged first."""
    import arcpy

    geoms = []
    if layer is not None:
        with arcpy.da.SearchCursor(layer, ["SHAPE@"]) as cur:
            for (g,) in cur:
                if g is not None and g.type == "polygon" and g.pointCount > 0:
                    geoms.append(g)
        if not geoms:
            raise GbifError("The study area has no polygons to use.")
    elif extent is not None:
        sr = getattr(extent, "spatialReference", None)
        if sr is None or getattr(sr, "name", "") in ("", "Unknown"):
            if not (-180 <= extent.XMin <= 180 and -180 <= extent.XMax <= 180
                    and -90 <= extent.YMin <= 90 and -90 <= extent.YMax <= 90):
                raise GbifError("The rectangle has no coordinate system and its numbers are not "
                                "degrees of longitude and latitude.")
            sr = arcpy.SpatialReference(4326)
        box = arcpy.Polygon(arcpy.Array([
            arcpy.Point(extent.XMin, extent.YMin), arcpy.Point(extent.XMin, extent.YMax),
            arcpy.Point(extent.XMax, extent.YMax), arcpy.Point(extent.XMax, extent.YMin),
            arcpy.Point(extent.XMin, extent.YMin)]), sr)
        geoms.append(box)
    else:
        return []

    projected = []
    for g in geoms:
        p = _to_wgs84(g)
        if p is not None and p.pointCount > 0:
            projected.append(p)
    if not projected:
        raise GbifError("The study area has no polygons to use.")
    merged = projected[0]
    if len(projected) > 1:
        merged = _union(projected)
    polys = _rings_of(merged)
    if not polys:
        raise GbifError("The study area has no polygons to use.")
    return polys


def _union(geoms):
    """One polygon from many (pairwise, so the work stays near n log n)."""
    work = list(geoms)
    while len(work) > 1:
        nxt = []
        for i in range(0, len(work) - 1, 2):
            nxt.append(work[i].union(work[i + 1]))
        if len(work) % 2:
            nxt.append(work[-1])
        work = nxt
    return work[0]


# ---------------------------------------------------------------------------
#  records
# ---------------------------------------------------------------------------
_INT_RE = re.compile(r"^\s*[+-]?\d+\s*$")
_FLOAT_RE = re.compile(r"^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$")


def _to_float(text):
    if text is None or not _FLOAT_RE.match(text):
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def people_list(text):
    if "|" not in text and ";" not in text:
        return text
    return "; ".join(s.strip() for s in re.split(r"[|;]", text) if s.strip())


def normalize(name, text):
    """Makes a value the same whichever route it came by."""
    if not text:
        return text
    if name == "license":
        return LICENSE_URLS.get(text.strip().upper(), text)
    if name in ("recordedBy", "identifiedBy"):
        return people_list(text)
    return text


def records_from_search(results):
    """(lon, lat, has coordinates, values by field name) per search record."""
    for o in results:
        vals = {}
        for (name, _a, _k, _l, skey, _c, _d) in FIELDS:
            if skey is None:
                continue
            if skey == "mediaType":
                media = o.get("media")
                has = False
                if isinstance(media, list):
                    for m in media:
                        if isinstance(m, dict) and m.get("identifier") is not None \
                                and m.get("type") in (None, "StillImage"):
                            has = True
                            break
                vals[name] = "StillImage" if has else None
                continue
            if skey == "photoURL":
                url = None
                media = o.get("media")
                if isinstance(media, list):
                    for m in media:
                        if isinstance(m, dict) and isinstance(m.get("identifier"), str) \
                                and m.get("type") in (None, "StillImage"):
                            url = m["identifier"]
                            break
                vals[name] = url if url and len(url) <= IMAGE_URL_LENGTH else None
                continue
            if skey == "issue":
                issues = o.get("issues")
                vals[name] = ";".join(i for i in issues if isinstance(i, str)) \
                    if isinstance(issues, list) else None
                continue
            v = o.get(skey)
            if v is True:
                v = "true"
            elif v is False:
                v = "false"
            elif not isinstance(v, str):
                v = None                         # arrays and objects are not field values
            vals[name] = v
        has = "decimalLongitude" in o and "decimalLatitude" in o
        lon = _to_float(o.get("decimalLongitude")) if isinstance(o.get("decimalLongitude"), str) else None
        lat = _to_float(o.get("decimalLatitude")) if isinstance(o.get("decimalLatitude"), str) else None
        yield (lon if lon is not None else 0.0, lat if lat is not None else 0.0, has, vals)


class DownloadFile(object):
    """The table inside a GBIF download zip, read without unpacking:
    "occurrence.txt" of a Darwin Core Archive, else the largest .csv
    (a Simple download). Tab-separated UTF-8, one header row, no quoting."""

    def __init__(self, zip_path):
        self.zip_path = zip_path
        try:
            self._zip = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError) as e:
            raise GbifError("{} cannot be read as a zip file ({}).".format(zip_path, e))
        entry = None
        for i in self._zip.infolist():
            if i.filename.lower() == "occurrence.txt":
                entry = i
                break
        if entry is None:
            csvs = [i for i in self._zip.infolist() if i.filename.lower().endswith(".csv")]
            if csvs:
                entry = max(csvs, key=lambda i: i.file_size)
        if entry is None:
            self._zip.close()
            raise GbifError("The GBIF download does not contain a table this tool can read.")
        self.table_name = entry.filename
        self._reader = io.TextIOWrapper(self._zip.open(entry), encoding="utf-8-sig", newline=None)
        header = self._reader.readline().rstrip("\n")
        self.columns = header.split("\t")
        self.lon_index = self.columns.index("decimalLongitude") if "decimalLongitude" in self.columns else -1
        self.lat_index = self.columns.index("decimalLatitude") if "decimalLatitude" in self.columns else -1
        self.has_coordinates = self.lon_index >= 0 and self.lat_index >= 0
        self.images_skipped = False      # multimedia.txt was too large to read
        self.images_absent = False       # no multimedia.txt: a Simple download

    def first_images(self):
        """GBIF record ID -> web address of the record's first still image,
        from "multimedia.txt" of a Darwin Core Archive. Empty for a Simple
        download, which has no such table, and (with images_skipped set)
        when the table is too large to hold in memory."""
        entry = None
        for i in self._zip.infolist():
            if i.filename.lower() == "multimedia.txt":
                entry = i
                break
        if entry is None:
            self.images_absent = True
            return {}
        if entry.file_size > MAX_MULTIMEDIA_BYTES:
            self.images_skipped = True
            return {}
        found = {}
        with io.TextIOWrapper(self._zip.open(entry), encoding="utf-8-sig", newline=None) as reader:
            head = reader.readline().rstrip("\n").split("\t")
            if "gbifID" not in head or "identifier" not in head:
                return {}
            k_id, k_url = head.index("gbifID"), head.index("identifier")
            k_type = head.index("type") if "type" in head else -1
            need = max(k_id, k_url, k_type)
            for line in reader:
                parts = line.rstrip("\n").split("\t")
                if len(parts) <= need:
                    continue
                rec, url = parts[k_id], parts[k_url]
                if not rec or not url or rec in found or len(url) > IMAGE_URL_LENGTH:
                    continue
                if k_type >= 0 and parts[k_type] not in ("", "StillImage"):
                    continue
                found[rec] = url
        return found

    def rows(self):
        n = len(self.columns)
        for line in self._reader:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < n:
                parts.extend([""] * (n - len(parts)))
            elif len(parts) > n:
                del parts[n:]
            yield parts

    def records(self):
        index = {c: i for i, c in enumerate(self.columns)}
        cols = [(name, index.get(csv)) for (name, _a, _k, _l, _s, csv, _d) in FIELDS if csv is not None]
        images = self.first_images()
        k_rec = index.get("gbifID")
        for r in self.rows():
            lon = lat = None
            if self.has_coordinates:
                lon = _to_float(r[self.lon_index])
                lat = _to_float(r[self.lat_index])
            has = lon is not None and lat is not None
            vals = {name: (r[i] if i is not None else None) for name, i in cols}
            vals["photoURL"] = images.get(r[k_rec]) if (images and k_rec is not None) else None
            yield (lon if lon is not None else 0.0, lat if lat is not None else 0.0, has, vals)

    def download_key(self):
        """The GBIF download key the zip belongs to, or None."""
        base = os.path.splitext(os.path.basename(self.table_name))[0]
        if KEY_RE.match(base):
            return base                                       # Simple download: <key>.csv
        try:
            meta = self._zip.read("metadata.xml").decode("utf-8", "replace")
        except KeyError:
            return None
        m = re.search(r"GBIF Occurrence Download\s+(\d{7}-\d{15})", meta)
        return m.group(1) if m else None

    def dataset_titles(self):
        """Dataset key -> title, from the dataset descriptions a Darwin
        Core Archive carries."""
        titles = {}
        for i in self._zip.infolist():
            m = re.match(r"dataset/([0-9a-fA-F-]{36})\.xml$", i.filename)
            if not m:
                continue
            try:
                xml = self._zip.read(i).decode("utf-8", "replace")
            except Exception:                                    # noqa: BLE001
                continue
            t = re.search(r"<title[^>]*>([^<]+)</title>", xml)
            if t:
                import html
                titles[m.group(1)] = html.unescape(t.group(1)).strip()
        return titles

    def close(self):
        try:
            self._reader.close()
        finally:
            self._zip.close()


class WriteResult(object):
    def __init__(self):
        self.written = 0
        self.skipped_no_coordinates = 0
        self.skipped_zero_zero = 0
        self.written_without_location = 0
        self.skipped_outside_area = 0
        self.truncated = 0
        self.by_dataset = {}
        self.dataset_names = {}
        self.by_license = {}


def _convert(kind, length, text, result):
    if not text:
        return None
    if kind == "I":
        if _INT_RE.match(text):
            v = int(text)
            if -2147483648 <= v <= 2147483647:
                return v
        d = _to_float(text)
        if d is not None and -2147483648 <= d <= 2147483647:
            return int(round(d))
        return None
    if kind == "D":
        return _to_float(text)
    if len(text) > length:
        result.truncated += 1
        return text[:length]
    return text


def write_points(out_fc, records, keep=None, keep_unlocated=False, dataset_titles=None,
                 progress=None, cancelled=None):
    """Creates the point feature class (WGS 84) and writes the records.
    The twin of GbifFeatureWriter.Write. An existing feature class of
    that name is replaced."""
    import arcpy
    import mahalanobis

    result = WriteResult()
    ws, name = os.path.split(out_fc)
    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)
    sr = mahalanobis.clean_sr(arcpy.SpatialReference(4326))
    arcpy.management.CreateFeatureclass(ws, name, "POINT", has_m="DISABLED", has_z="DISABLED",
                                        spatial_reference=sr)
    types = {"T": "TEXT", "I": "LONG", "D": "DOUBLE"}
    arcpy.management.AddFields(out_fc, [
        [f[0], types[f[2]], f[1], f[3] if f[2] == "T" else None] for f in FIELDS])

    titles = dataset_titles or {}
    kinds = [(f[0], f[2], f[3]) for f in FIELDS]
    with arcpy.da.InsertCursor(out_fc, ["SHAPE@XY"] + FIELD_NAMES) as cur:
        for lon, lat, has, vals in records:
            if cancelled is not None and result.written % 500 == 0 and cancelled():
                raise Cancelled()
            # keep_unlocated: a record with no usable coordinates becomes a
            # row with an EMPTY shape, never a point
            located = True
            if not has or lon != lon or lat != lat or lat < -90 or lat > 90 or lon < -180 or lon > 180:
                if not keep_unlocated:
                    result.skipped_no_coordinates += 1
                    continue
                located = False
            if located and lon == 0 and lat == 0:
                # "Null Island": coordinates missing at the source that
                # became 0, 0. Never mapped, whatever the filters were.
                if not keep_unlocated:
                    result.skipped_zero_zero += 1
                    continue
                located = False
            if located and keep is not None and not keep(lon, lat):
                result.skipped_outside_area += 1
                continue
            row = [(lon, lat) if located else None]
            rec_id = None
            ds_key = None
            for fname, kind, length in kinds:
                if fname == "recordURL":
                    text = None if rec_id is None else "https://www.gbif.org/occurrence/" + rec_id
                else:
                    text = normalize(fname, vals.get(fname))
                if fname == "gbifID":
                    rec_id = text or None
                if fname == "datasetKey":
                    ds_key = text or None
                    if text:
                        result.by_dataset[text] = result.by_dataset.get(text, 0) + 1
                elif fname == "datasetName" and ds_key is not None:
                    # every row gets its source's name: GBIF's own title for
                    # the dataset when known, else the name the record carries
                    if titles.get(ds_key):
                        text = titles[ds_key]
                    if text:
                        result.dataset_names[ds_key] = text
                elif fname == "license":
                    lic = text or "(not stated)"
                    result.by_license[lic] = result.by_license.get(lic, 0) + 1
                row.append(_convert(kind, length, text, result))
            cur.insertRow(row)
            result.written += 1
            if not located:
                result.written_without_location += 1
            if progress is not None and result.written % 1000 == 0:
                progress(result.written)
    if progress is not None:
        progress(result.written)
    return result


# ---------------------------------------------------------------------------
#  getting the records
# ---------------------------------------------------------------------------
def count_records(flt):
    body = json.dumps({"predicate": flt.predicate(), "limit": 0, "offset": 0}).encode("utf-8")
    r = _json(V1 + "occurrence/search/predicate", data=body, repeatable=True) or {}
    return int(r.get("count") or 0)


def fetch_search(flt, total, say, step, cancelled):
    """All matching records from the search service, a page at a time."""
    keep = set(f[4] for f in FIELDS if f[4]) | {"decimalLongitude", "decimalLatitude", "key",
                                               "issues", "media"}
    pred = flt.predicate()
    got = []
    waits = 0
    t0 = time.time()
    while len(got) < total:
        if cancelled():
            raise Cancelled()
        # GBIF refuses a page that would reach past its limit ("Max offset of 100001 exceeded"), so the last
        # page asks for no more than is left: of the records that match, and of what the service will give
        limit = min(MAX_PAGE, total - len(got), MAX_SEARCH - len(got))
        if limit <= 0:
            break
        body = json.dumps({"predicate": pred, "limit": limit, "offset": len(got)}).encode("utf-8")
        try:
            page = _json(V1 + "occurrence/search/predicate", data=body, raw_numbers=True, repeatable=True) or {}
        except GbifError as e:
            # GBIF asking for a pause, or failing for a while, does not throw away what has been fetched:
            # the request is made again after a wait, up to five times in a row
            passing = e.status in (420, 429) or not e.status or e.status >= 500
            if passing and waits < 5:
                waits += 1
                say(("GBIF asked this computer to slow down; waiting 30 seconds ({} of 5)." if e.status in (420, 429)
                     else "GBIF did not answer; waiting 30 seconds and asking again ({} of 5).").format(waits))
                _sleep(30, cancelled)
                continue
            raise
        waits = 0                                    # the allowance is for one stretch of trouble, not for the run
        results = _items(page)
        if not results:
            break
        for o in results:
            got.append({k: v for k, v in o.items() if k in keep})
        frac = min(1.0, len(got) / float(total))
        left = (time.time() - t0) * (1 - frac) / frac if frac > 0.02 else None
        step(frac * 85, "Fetched {} of {} records{}".format(
            n0(len(got)), n0(total), "" if left is None else "; about {} left".format(span(left))))
        if page.get("endOfRecords") is True:
            break
        time.sleep(0.15)                            # be polite to GBIF
    say("Fetched {} records in {}.".format(n0(len(got)), span(time.time() - t0)))
    return got


def source_titles(keys, cancelled):
    """GBIF's title for each dataset key (four requests at a time; a key
    whose title cannot be read is left out)."""
    todo = []
    for k in keys:
        if k and k not in todo:
            todo.append(k)
        if len(todo) >= 2000:
            break

    def one(key):
        if cancelled():
            return key, None
        try:
            d = _json(V1 + "dataset/" + urllib.parse.quote(key), tries=2) or {}
            return key, d.get("title")
        except Exception:                                        # noqa: BLE001
            return key, None

    titles = {}
    if todo:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for key, title in pool.map(one, todo):
                if title:
                    titles[key] = title
    if cancelled():
        raise Cancelled()
    return titles


def download_datasets(key):
    """Dataset key -> title for the datasets in a download."""
    titles = {}
    offset = 0
    while offset < 20000:
        r = _json(V1 + "occurrence/download/" + urllib.parse.quote(key)
                  + "/datasets?limit=100&offset={}".format(offset)) or {}
        items = _items(r)
        for e in items:
            k, t = e.get("datasetKey"), e.get("datasetTitle")
            if k and t:
                titles[k] = t
        if not items or r.get("endOfRecords") is not False:
            break
        offset += len(items)
    return titles


def download_status(key):
    try:
        return _json(V1 + "occurrence/download/" + urllib.parse.quote(key)) or {}
    except GbifError as e:
        if e.status == 404:
            raise GbifError("GBIF has no download with the key {}.".format(key))
        raise


def citation(key):
    try:
        return _text(V1 + "occurrence/download/" + urllib.parse.quote(key) + "/citation",
                     accept="text/plain, */*", tries=2).strip() or None
    except Exception:                                            # noqa: BLE001
        return None


STATUS_WORDS = {
    "PREPARING": "being prepared", "RUNNING": "running", "SUSPENDED": "waiting its turn",
    "SUCCEEDED": "ready", "FAILED": "failed", "CANCELLED": "cancelled", "KILLED": "stopped by GBIF",
    "FILE_ERASED": "erased by GBIF",
}
RUNNING = ("PREPARING", "RUNNING", "SUSPENDED")


def status_words(status):
    return STATUS_WORDS.get(status or "", (status or "unknown").lower())


def request_download(flt, account):
    pred = flt.predicate()
    if pred is None:
        raise GbifError("A download needs at least one filter (a species or group, a study area or a country).")
    # DWCA, not SIMPLE_CSV: GBIF's simple table leaves out sex, life stage,
    # county, the source's name, the Red List category and the
    # "information withheld" note, which a direct fetch does give.
    body = json.dumps({"creator": account[0], "sendNotification": False, "format": "DWCA",
                       "predicate": pred}).encode("utf-8")
    try:
        text = _text(V1 + "occurrence/download/request", data=body, auth=account)
    except GbifError as e:
        if e.status == 401:
            raise GbifError(
                "GBIF did not accept the stored sign-in for the account \"{}\". Open the GBIF "
                "Species Occurrences window, click Sign in on tab 4 and sign in again (the password "
                "may have changed at GBIF).".format(account[0]), 401)
        raise
    return text.strip().strip('"')


def cancel_download(key, account):
    """Cancels a download GBIF is still preparing (the same request the
    window's "Cancel the request" button sends)."""
    _text(V1 + "occurrence/download/request/" + urllib.parse.quote(key), auth=account, method="DELETE")


def _sleep(seconds, cancelled):
    end = time.time() + seconds
    while time.time() < end:
        if cancelled():
            raise Cancelled()
        time.sleep(min(1.0, max(0.0, end - time.time())))


def wait_for_download(key, max_wait_minutes, say, step, cancelled, first_delay=15):
    """Waits until GBIF finishes the download; returns its status record.
    Stops with a GbifError that names the key when GBIF reports a failure
    or when the wait limit passes."""
    t0 = time.time()
    limit = max(1.0, float(max_wait_minutes or 60)) * 60.0
    d = download_status(key)
    while d.get("status") in RUNNING:
        waited = time.time() - t0
        if waited > limit:
            raise GbifError(
                "GBIF had not finished download {} after {} (it is {}). The request is still running "
                "at GBIF and stays on the account. To load it when it is ready, run this tool again "
                "with the method \"{}\" and the existing download {}; or raise the longest wait.".format(
                    key, span(waited), status_words(d.get("status")), METHOD_LOAD, key))
        # GBIF gives no percentage; the bar creeps toward 60% and says how long it has been
        step(min(60, 5 + (waited / 60.0) * 4),
             "GBIF is preparing the download ({}); waiting {}. Small requests take a few minutes; "
             "large ones can take much longer.".format(status_words(d.get("status")), span(waited)))
        _sleep(first_delay, cancelled)
        d = download_status(key)
    if d.get("status") != "SUCCEEDED":
        raise GbifError("GBIF did not finish download {} (status: {}).".format(
            key, status_words(d.get("status"))))
    return d


def fetch_download_file(d, zip_path, step, cancelled):
    """Saves a finished download's zip file (through a .part file)."""
    url = d.get("downloadLink") or (V1 + "occurrence/download/request/"
                                    + urllib.parse.quote(d.get("key")) + ".zip")
    resp = _open(url, accept="*/*", timeout=300)
    tmp = zip_path + ".part"
    try:
        total = int(resp.headers.get("Content-Length") or d.get("size") or 0)
        done = 0
        with open(tmp, "wb") as dst:
            while True:
                if cancelled():
                    raise Cancelled()
                buf = resp.read(1 << 16)
                if not buf:
                    break
                dst.write(buf)
                done += len(buf)
                step(62 + (23.0 * done / total if total > 0 else 0),
                     "Fetching the file: {}{}".format(
                         size_words(done), " of " + size_words(total) if total > 0 else ""))
        if os.path.exists(zip_path):
            os.remove(zip_path)
        os.replace(tmp, zip_path)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    finally:
        resp.close()


def downloads_folder(out_fc):
    """Where GBIF's original zip files are kept: Data_Downloads in the
    project's home folder; beside the output geodatabase when the tool
    runs outside a project. Created if needed."""
    import arcpy
    import mahalanobis

    home = None
    try:
        home = arcpy.mp.ArcGISProject("CURRENT").homeFolder
    except Exception:                                            # noqa: BLE001
        home = None
    if not home or not os.path.isdir(home):
        ws = os.path.dirname(out_fc)
        if mahalanobis.is_memory_workspace(ws):
            home = arcpy.env.scratchFolder
        else:
            home = os.path.dirname(ws.rstrip("\\/"))
    folder = os.path.join(home, DOWNLOADS_FOLDER)
    os.makedirs(folder, exist_ok=True)
    return folder


# ---------------------------------------------------------------------------
#  the report and the metadata
# ---------------------------------------------------------------------------
def license_short(url):
    u = (url or "").lower()
    if "publicdomain/zero" in u:
        return "CC0 (no restriction)"
    if "by-nc" in u:
        return "CC BY-NC (credit required, non-commercial use only)"
    if "licenses/by/" in u:
        return "CC BY (credit required)"
    return url or "(not stated)"


def source_line(result, key, count):
    return "{}   [{}]   {}".format(result.dataset_names.get(key) or "(name not given)", key, n0(count))


def report_written(out_fc, r, say):
    say("Wrote {} {} to {}.{}{}{}{}{}".format(
        n0(r.written), "records" if r.written_without_location > 0 else "points", out_fc,
        " {} of them have no coordinates and are rows without a point: look for them in the "
        "attribute table, not on the map.".format(n0(r.written_without_location))
        if r.written_without_location > 0 else "",
        " Left out {} outside the true study area.".format(n0(r.skipped_outside_area))
        if r.skipped_outside_area > 0 else "",
        " Left out {} without usable coordinates.".format(n0(r.skipped_no_coordinates))
        if r.skipped_no_coordinates > 0 else "",
        " Left out {} at latitude 0, longitude 0 (missing coordinates recorded as zeros).".format(
            n0(r.skipped_zero_zero)) if r.skipped_zero_zero > 0 else "",
        " {} long text values were shortened to fit their fields.".format(n0(r.truncated))
        if r.truncated > 0 else ""))
    if r.by_license:
        say("Licenses of the records: {}.".format("; ".join(
            "{}: {}".format(license_short(k), n0(v))
            for k, v in sorted(r.by_license.items(), key=lambda kv: -kv[1]))))


def report_sources(r, say, most=25):
    n = len(r.by_dataset)
    say("The records come from {} {} (name, GBIF dataset key, records):".format(
        n0(n), "source" if n == 1 else "sources"))
    ranked = sorted(r.by_dataset.items(), key=lambda kv: -kv[1])
    for key, count in ranked[:most]:
        say("    " + source_line(r, key, count))
    if n > most:
        say("    and {} more with fewer records each (the datasetKey and datasetName fields of the "
            "output have them all).".format(n0(n - most)))


DESCRIPTION = (
    "Species occurrence records from the Global Biodiversity Information Facility (GBIF.org), "
    "written by the GBIF Species Occurrences tool of the Wildlife and Forestry Tools for ArcGIS "
    "Pro. Each row is one record that an organism was observed, collected or sampled at a place and "
    "time, published to GBIF by the dataset named in its datasetName field. Coordinates are WGS 84. "
    "A row without a point is a record that has no usable coordinates. GBIF data is not reviewed "
    "record by record: check the locality, the basis of record and the coordinate uncertainty of "
    "any record that matters, and of any point far outside a species' known range. Every record "
    "carries its own license (the license field), and GBIF asks that the data be cited.")


def html_link(url, label=None):
    """A link for a metadata element."""
    import html as _html

    return '<a href="{}">{}</a>'.format(_html.escape(url, quote=True), _html.escape(label or url, quote=False))


def html_text(text):
    import html as _html

    return _html.escape(text or "", quote=False)


def html_list(items):
    return "<ul>" + "".join("<li>{}</li>".format(i) for i in items) + "</ul>" if items else ""


def gbif_links(flt=None, r=None, key=None, doi=None, most=25):
    """The links of the output's metadata: the download and its DOI, the
    species or groups asked for, the sources of the records, and GBIF's
    pages on citing and using its data. Each item is a line of HTML."""
    items = []
    if doi:
        items.append("The download's DOI, which stands for exactly these records and every dataset that "
                     "contributed to them: " + html_link("https://doi.org/" + doi))
    if key:
        items.append("The download's page on GBIF.org (the request, its citation and its sources): "
                     + html_link("https://www.gbif.org/occurrence/download/" + key))
    labels = list(getattr(flt, "taxon_labels", None) or []) if flt is not None else []
    keys = list(flt.taxon_keys) if flt is not None else []
    for i, k in enumerate(keys):
        label = labels[i] if len(labels) == len(keys) else "GBIF taxon " + str(k)
        items.append("Species or group asked for: " + html_link("https://www.gbif.org/species/" + str(k), label))
    if r is not None and r.by_dataset:
        ranked = sorted(r.by_dataset.items(), key=lambda kv: -kv[1])
        for dk, count in ranked[:most]:
            items.append("Source of {} {}: {}".format(
                n0(count), "record" if count == 1 else "records",
                html_link("https://www.gbif.org/dataset/" + dk, r.dataset_names.get(dk) or dk)))
        if len(ranked) > most:
            items.append("and {} more sources with fewer records each: the datasetKey and datasetName fields "
                         "of the output have them all.".format(n0(len(ranked) - most)))
    items.append("Each record's own page on GBIF.org is in its recordURL field, and the web address of its "
                 "first photograph, when it has one, is in its photoURL field.")
    items.append("How GBIF asks that its data be cited: " + html_link("https://www.gbif.org/citation-guidelines"))
    items.append("GBIF's data user agreement: " + html_link("https://www.gbif.org/terms/data-user"))
    if not doi:
        items.append("Registering a derived dataset, which gives a DOI to records that were fetched directly: "
                     + html_link("https://www.gbif.org/derived-dataset/register"))
    items.append("GBIF, the Global Biodiversity Information Facility: " + html_link("https://www.gbif.org"))
    return items


def stamp_metadata(out_fc, summary, cite, messages=None, how=None, flt=None, r=None, key=None, doi=None):
    """Title, tags, summary, description, credits, use limitations and
    field descriptions. The description says where the records came from
    and links to the sources. Never raises: metadata is a courtesy, not
    the result."""
    try:
        import arcpy
        import tool_metadata

        md = arcpy.metadata.Metadata(out_fc)
        md.title = os.path.basename(out_fc)
        md.tags = "GBIF, species occurrence, biodiversity, Wildlife and Forestry Tools"
        md.summary = summary
        md.description = (
            "<p>" + html_text(DESCRIPTION) + "</p>"
            + ("<p><b>Where these records came from.</b> " + html_text(how) + "</p>" if how else "")
            + ("<p><b>How to cite them.</b> " + html_text(cite) + "</p>" if cite else "")
            + "<p><b>Links to the sources:</b></p>" + html_list(gbif_links(flt, r, key, doi)))
        md.credits = cite or "GBIF.org, the Global Biodiversity Information Facility (https://www.gbif.org)."
        if r is not None and r.by_license:
            md.accessConstraints = (
                "<p>Every record carries its own license, chosen by its publisher and held in its license "
                "field. In this feature class: "
                + html_text("; ".join("{}: {}".format(license_short(k), n0(v))
                                      for k, v in sorted(r.by_license.items(), key=lambda kv: -kv[1])))
                + ".</p><p>Use of GBIF data is subject to GBIF's data user agreement: "
                + html_link("https://www.gbif.org/terms/data-user") + "</p>")
        md.save()
        tool_metadata.set_entity_and_fields(
            out_fc, entity="GBIF occurrence records, one row per record.",
            fields=[{"name": f[0], "description": f[6]} for f in FIELDS], messages=messages)
    except Exception as e:                                       # noqa: BLE001
        if messages is not None:
            try:
                messages.addWarningMessage("Could not write the output's metadata: {}".format(e))
            except Exception:                                    # noqa: BLE001
                pass


def describe_filter(flt, taxa_text, area_words):
    """The choices in plain words, for the metadata summary."""
    parts = []
    if taxa_text:
        parts.append("species or groups: " + "; ".join(split_list(taxa_text)))
    if area_words:
        parts.append("study area: " + area_words)
    if flt.country:
        parts.append("country: " + flt.country)
    if flt.state_province:
        parts.append("state or province: " + flt.state_province)
    if flt.year_from is not None or flt.year_to is not None:
        parts.append("years: {} to {}".format(
            flt.year_from if flt.year_from is not None else "the earliest",
            flt.year_to if flt.year_to is not None else "the latest"))
    months = sorted(set(flt.months))
    if 0 < len(months) < 12:
        parts.append("months: " + ", ".join(str(m) for m in months))
    if flt.basis_of_record:
        parts.append("kinds of record: " + ", ".join(flt.basis_of_record))
    if flt.exclude_geospatial_issues:
        parts.append("records with a location problem flagged by GBIF left out")
    if flt.exclude_centroids:
        parts.append("records placed at the center of a country or region left out")
    if flt.present_only:
        parts.append("presence records only")
    if flt.licenses:
        parts.append("records licensed for non-commercial use only left out")
    if flt.max_uncertainty_m is not None:
        parts.append("coordinate uncertainty at most {} m (records stating none left out)".format(
            _num(flt.max_uncertainty_m)))
    if flt.images_only:
        parts.append("only records with a photograph")
    if flt.excluded_dataset_keys:
        parts.append("sources left out (GBIF dataset keys): " + ", ".join(flt.excluded_dataset_keys))
    if flt.include_no_coordinates:
        parts.append("records without coordinates included as rows without a point")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
#  the tool
# ---------------------------------------------------------------------------
def kinds_to_codes(kinds):
    """The basis-of-record codes for the chosen kinds; [] (no filter)
    when none or all are chosen."""
    chosen = [k for k in RECORD_KIND_NAMES if k in set(kinds or [])]
    if not chosen or len(chosen) == len(RECORD_KIND_NAMES):
        return []
    codes = []
    for name, c in RECORD_KINDS:
        if name in chosen:
            codes.extend(c)
    return codes


def check_output(out_fc):
    """None when the output path can be written, else the reason."""
    import mahalanobis

    p = str(out_fc or "").strip().replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(p)
    if not name or not ws:
        return "Give the output as a feature class inside a file geodatabase."
    if not mahalanobis.is_gdb_workspace(ws):
        if mahalanobis.in_gdb_container(ws):
            return "Put the output directly in the geodatabase, not inside a feature dataset."
        return ("The output must be a feature class inside a file geodatabase, such as "
                "C:\\Data\\Wildlife.gdb\\GBIF_occurrences (the field names are too long for a shapefile).")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return "The feature class name must start with a letter and hold only letters, digits and underscores."
    return None


def run(out_fc, **kwargs):
    """Runs the tool (see ``_run`` for the arguments). Returns a dict:
    written, download_key, doi, download_file (the last three None for a
    direct fetch). The tool watches for Cancel itself, so that a wait or
    a half-fetched file is left tidy; the session's own setting is put
    back afterwards."""
    import arcpy

    try:
        before = arcpy.env.autoCancelling
    except Exception:                                            # noqa: BLE001
        before = None
    try:
        return _run(out_fc, **kwargs)
    finally:
        if before is not None:
            try:
                arcpy.env.autoCancelling = before
            except Exception:                                    # noqa: BLE001
                pass


def _run(out_fc, method=METHOD_FETCH, taxa=None, study_area=None, extent=None, country=None,
         state_province=None, include_no_coordinates=False, year_from=None, year_to=None,
         months=None, record_kinds=None, exclude_location_problems=True, exclude_centroids=True,
         present_only=True, exclude_noncommercial=False, images_only=False, max_uncertainty_m=None,
         excluded_sources=None, existing_download=None, max_wait_minutes=60, messages=None,
         area_words=None):
    import arcpy

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

    method = method or METHOD_FETCH
    if method not in METHODS:
        raise GbifError("The method must be one of: {}.".format("; ".join(METHODS)))
    why = check_output(out_fc)
    if why:
        raise GbifError(why)
    out_fc = str(out_fc).strip().replace("/", "\\").rstrip("\\")
    existing = (existing_download or "").strip().strip('"').strip()
    if method == METHOD_LOAD and not existing:
        raise GbifError("Give the existing download: its GBIF key (such as 0012345-260101123456789) "
                        "or the path of its zip file.")
    if method != METHOD_LOAD and existing:
        raise GbifError("An existing download is named, so choose the method \"{}\", or clear the "
                        "existing download to ask GBIF for the records afresh.".format(METHOD_LOAD))
    if study_area is not None and extent is not None:
        raise GbifError("Give a study area layer or a rectangle, not both.")

    try:
        arcpy.env.autoCancelling = False
    except Exception:                                            # noqa: BLE001
        pass
    arcpy.SetProgressor("step", "Reading the choices...", 0, 100, 1)

    flt = GbifFilter()
    flt.include_no_coordinates = bool(include_no_coordinates)
    polygons = study_area_polygons(study_area, extent)
    flt.polygons = polygons
    if polygons:
        say("The study area is {} with {} vertices in its outline.".format(
            "1 polygon" if len(polygons) == 1 else "{} polygons".format(n0(len(polygons))),
            n0(flt.polygon_point_count())))

    out = {"written": 0, "download_key": None, "doi": None, "download_file": None}
    summary_head = "Created {} by the GBIF Species Occurrences tool. ".format(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

    if method == METHOD_LOAD:
        return _run_load(out_fc, existing, flt, max_wait_minutes, out, summary_head,
                         area_words, say, warn, step, cancelled, messages)

    flt.taxon_keys = resolve_taxa(taxa, say)
    flt.taxon_labels = split_list(taxa)          # for the links in the output's metadata
    flt.country = (country or "").strip().upper() or None
    flt.state_province = (state_province or "").strip() or None
    flt.year_from = int(year_from) if year_from not in (None, "") else None
    flt.year_to = int(year_to) if year_to not in (None, "") else None
    if flt.year_from is not None and flt.year_to is not None and flt.year_from > flt.year_to:
        raise GbifError("The first year is later than the last year.")
    flt.months = [int(m) for m in (months or [])]
    flt.basis_of_record = kinds_to_codes(record_kinds if record_kinds is not None else RECORD_KIND_DEFAULT)
    flt.exclude_geospatial_issues = bool(exclude_location_problems)
    flt.exclude_centroids = bool(exclude_centroids)
    flt.present_only = bool(present_only)
    flt.images_only = bool(images_only)
    if exclude_noncommercial:
        flt.licenses = ["CC0_1_0", "CC_BY_4_0"]
    if max_uncertainty_m not in (None, ""):
        if float(max_uncertainty_m) <= 0:
            raise GbifError("The coordinate uncertainty must be a number of meters greater than zero, or empty.")
        flt.max_uncertainty_m = float(max_uncertainty_m)
    flt.excluded_dataset_keys = split_list(excluded_sources) if isinstance(excluded_sources, str) \
        else [s for s in (excluded_sources or []) if s]
    if not flt.taxon_keys and not flt.polygons and not flt.country:
        raise GbifError("Choose at least one species or group, a study area, or a country. With none "
                        "of them, the request is every record GBIF holds.")
    choices = describe_filter(flt, taxa, area_words or ("a polygon layer or rectangle" if polygons else None))

    if method == METHOD_FETCH:
        step(0, "Counting...")
        total = count_records(flt)
        say("GBIF has no records that match." if total == 0 else
            "GBIF has 1 record that matches." if total == 1 else
            "GBIF has {} records that match.".format(n0(total)))
        if total > MAX_SEARCH:
            raise GbifError("There are {} records, more than the 100,000 that can be fetched directly. "
                            "Choose the method \"{}\" instead.".format(n0(total), METHOD_DOWNLOAD))
        results = fetch_search(flt, total, say, step, cancelled) if total > 0 else []
        step(87, "Looking up the names of the sources...")
        titles = source_titles([o.get("datasetKey") for o in results], cancelled)
        step(88, "Writing the points...")
        r = _write(out_fc, records_from_search(results), None, flt.include_no_coordinates, titles,
                   max(1, len(results)), 88, 10, step, cancelled)
        if total == 0:
            warn("No records matched, so the output feature class is empty. Widen the study area, "
                 "the dates or the kinds of record.")
        report_written(out_fc, r, say)
        if flt.excluded_dataset_keys:
            say("Sources left out at your request (GBIF dataset keys): {}.".format(
                "; ".join(flt.excluded_dataset_keys)))
        say("A direct fetch creates no zip file to keep: the only data created is the feature class "
            + out_fc + ".")
        say("These records were fetched directly, and GBIF issues no DOI for that. To cite them as a GBIF "
            "download, either request a GBIF download of the same choices (the method \"{}\") and cite its "
            "DOI, or register a \"derived dataset\" with GBIF from the list of sources below. Otherwise, "
            "check the 'License of the record' field and make sure you cite any data that requires citation "
            "using whatever method you normally use to cite data.".format(METHOD_DOWNLOAD))
        report_sources(r, say)
        stamp_metadata(out_fc, summary_head + "Fetched directly from GBIF's occurrence search service "
                       "(no DOI is issued for a direct fetch). Choices: {}. {} records written{}. "
                       "Licenses: {}.".format(
                           choices, n0(r.written),
                           ", {} of them without a point".format(n0(r.written_without_location))
                           if r.written_without_location else "",
                           "; ".join("{}: {}".format(license_short(k), n0(v))
                                     for k, v in sorted(r.by_license.items(), key=lambda kv: -kv[1]))
                           or "none"),
                       None, messages,
                       how="Fetched directly from GBIF's occurrence search service on {}, for these choices: "
                           "{}. GBIF issues no DOI for a direct fetch. To cite these records with a DOI, request "
                           "a GBIF download of the same choices, or register a derived dataset with GBIF from "
                           "the sources listed below.".format(datetime.date.today().strftime("%Y-%m-%d"), choices),
                       flt=flt, r=r)
        out["written"] = r.written
        step(100, "Done: {} {}.".format(n0(r.written), "records" if r.written_without_location else "points"))
        return out

    # ---- request a GBIF download -------------------------------------------
    account = stored_account()
    if not account:
        raise GbifError(
            "A GBIF download needs your GBIF account, and no sign-in is stored on this computer. Open "
            "the GBIF Species Occurrences window (Data Tools, Online Data), click Sign in on tab 4 "
            "and leave \"Remember\" checked. The tool then reads the sign-in from Windows Credential "
            "Manager; it is never a parameter.")
    step(1, "Counting...")
    total = count_records(flt)
    say("GBIF has no records that match." if total == 0 else
        "GBIF has 1 record that matches." if total == 1 else
        "GBIF has {} records that match.".format(n0(total)))
    if total == 0:
        r = _write(out_fc, iter(()), None, flt.include_no_coordinates, {}, 1, 88, 10, step, cancelled)
        warn("No records matched, so no download was requested from GBIF and the output feature class is "
             "empty. Widen the study area, the dates or the kinds of record.")
        stamp_metadata(out_fc, summary_head + "No records matched, so no GBIF download was requested. "
                       "Choices: {}. 0 records written.".format(choices), None, messages)
        out["written"] = r.written
        step(100, "Done: no records matched.")
        return out
    request = flt
    keep = None
    if not flt.polygon_fits_download():
        request = flt.with_convex_hull()
        keep = flt.contains
        say("The study area polygon is very detailed: its outline has {} vertices, and GBIF's download "
            "service accepts an outline of at most 10,000. The tool therefore simplifies the study area "
            "first ({}), downloads the records inside that simpler shape, and then clips them to the true "
            "study area when they are written.".format(
                n0(flt.polygon_point_count()),
                "to its convex hull" if getattr(request, "hull_how", "hull") == "hull" else
                "its convex hull has more than 10,000 vertices itself, so to a convex polygon of {} vertices "
                "that contains the hull".format(n0(request.hull_vertices))
                if request.hull_how == "edges" else
                "to the rectangle around it, because its convex hull has more than 10,000 vertices itself"))
    step(2, "Asking GBIF to prepare the download...")
    say("Requesting the download on the GBIF account \"{}\".".format(account[0]))
    key = request_download(request, account)
    out["download_key"] = key
    say("GBIF accepted the request: download {}. It is now on the GBIF account \"{}\". If this tool is "
        "stopped before the download is ready, load it later with the method \"{}\" and this key.".format(
            key, account[0], METHOD_LOAD))
    try:
        t0 = time.time()
        _sleep(15, cancelled)
        d = wait_for_download(key, max_wait_minutes, say, step, cancelled)
    except Cancelled:
        # whoever cancels means to cancel altogether: the request this run
        # made is cancelled at GBIF too, so nothing is left running there
        try:
            cancel_download(key, account)
            raise Cancelled("Cancelled. The request was cancelled at GBIF as well (download {}).".format(key))
        except GbifError as e:
            raise Cancelled("Cancelled here, but the request could not be cancelled at GBIF ({}), where "
                            "download {} is still being prepared. Cancel it on GBIF.org, or on tab 5 of the "
                            "GBIF Species Occurrences window.".format(e, key))
    say("GBIF finished it in {}: {} records from {} datasets.".format(
        span(time.time() - t0), n0(d.get("totalRecords") or 0), d.get("numberDatasets") or 0))
    return _load(out_fc, d, None, keep, flt.include_no_coordinates, out,
                 summary_head + "Loaded from a GBIF download requested by this run. Choices: {}. ".format(choices),
                 flt, say, warn, step, cancelled, messages)


def _write(out_fc, records, keep, keep_unlocated, titles, expect, base, width, step, cancelled):
    return write_points(
        out_fc, records, keep=keep, keep_unlocated=keep_unlocated, dataset_titles=titles,
        progress=lambda n: step(base + width * min(1.0, n / float(max(1, expect))),
                                "Writing the points: {}".format(n0(n))),
        cancelled=cancelled)


def _run_load(out_fc, existing, flt, max_wait_minutes, out, summary_head, area_words,
              say, warn, step, cancelled, messages):
    keep = flt.contains if flt.polygons else None
    if flt.polygons:
        say("The records of the download are cut to the study area given here.")
    say("The other choices (species, dates, kinds of record and so on) were applied by GBIF when the "
        "download was made; they are not applied again.")
    head = summary_head + "Loaded from an existing GBIF download{}. ".format(
        "; cut to the study area ({})".format(area_words or "a polygon layer or rectangle")
        if flt.polygons else "")
    if KEY_RE.match(existing):
        d = download_status(existing)
        say("Loading GBIF download {} (DOI {}).".format(existing, d.get("doi") or "not given"))
        if d.get("status") in RUNNING:
            say("GBIF is still preparing it; waiting.")
            try:
                d = wait_for_download(existing, max_wait_minutes, say, step, cancelled)
            except Cancelled:
                raise Cancelled("Cancelled. The download {} is still being prepared at GBIF.".format(existing))
        if d.get("status") == "FILE_ERASED":
            raise GbifError("GBIF has erased the file of download {} (it keeps unused downloads for a "
                            "limited time). Request the download again.".format(existing))
        if d.get("status") != "SUCCEEDED":
            raise GbifError("Download {} cannot be loaded (status: {}).".format(
                existing, status_words(d.get("status"))))
        if ((d.get("request") or {}).get("format") or "") == "SPECIES_LIST":
            raise GbifError("A species-list download has no coordinates, so it cannot be loaded as points.")
        return _load(out_fc, d, None, keep, flt.include_no_coordinates, out, head, None,
                     say, warn, step, cancelled, messages)
    if not os.path.isfile(existing):
        raise GbifError("\"{}\" is neither a GBIF download key (such as 0012345-260101123456789) nor "
                        "the path of an existing zip file.".format(existing))
    return _load(out_fc, None, existing, keep, flt.include_no_coordinates, out, head, None,
                 say, warn, step, cancelled, messages)


def _load(out_fc, d, zip_path, keep, keep_unlocated, out, summary_head, flt,
          say, warn, step, cancelled, messages):
    """Loads a finished download: ``d`` (its GBIF status record; the file
    is fetched and kept) or ``zip_path`` (a zip already on disk)."""
    name = os.path.basename(out_fc)
    fetched = False
    if zip_path is None:
        # GBIF's original file is KEPT, in Data_Downloads, named after the
        # output feature class: it also holds the citation of every source,
        # the rights statements and the records exactly as GBIF supplied them.
        folder = downloads_folder(out_fc)
        zip_path = os.path.join(folder, name + ".zip")
        step(62, "Fetching the file...")
        fetch_download_file(d, zip_path, step, cancelled)
        fetched = True
    table = DownloadFile(zip_path)
    try:
        key = (d or {}).get("key") or table.download_key()
        if d is None and key:
            try:
                d = download_status(key)
            except GbifError:
                d = None                        # offline, or GBIF no longer lists it
        if not table.has_coordinates and not keep_unlocated:
            raise GbifError("This GBIF download has no coordinates (a species list?), so it cannot be "
                            "loaded as points.")
        titles = {}
        try:
            titles = table.dataset_titles()
            if key and not titles:
                titles = download_datasets(key)
        except Cancelled:
            raise
        except Exception:                                        # noqa: BLE001
            pass                    # each row then keeps the source name its record carries
        step(86, "Writing the points...")
        expect = max(1, int((d or {}).get("totalRecords") or 0))
        r = _write(out_fc, table.records(), keep, keep_unlocated, titles, expect, 86, 12, step, cancelled)
    finally:
        table.close()
    total = int((d or {}).get("totalRecords") or 0)
    say("GBIF's original zip file is {} {} ({}). It holds the {}records as GBIF supplied them{}, the "
        "citation of every source and their rights statements.".format(
            "kept at" if fetched else "at", zip_path, size_words(os.path.getsize(zip_path)),
            n0(total) + " " if total else "",
            ", including the {} outside the true study area that were left out of the feature class".format(
                n0(r.skipped_outside_area)) if r.skipped_outside_area > 0 else ""))
    report_written(out_fc, r, say)
    if table.images_absent:
        say("This download is in GBIF's Simple format, which does not carry the web addresses of photographs, "
            "so the photoURL field is empty. The mediaType field still says which records have a photograph, "
            "and each record's page (the recordURL field) shows it.")
    elif table.images_skipped:
        say("The download's table of photographs is larger than {}, too large to read into memory, so the "
            "photoURL field is empty. Each record's page (the recordURL field) shows its photographs.".format(
                size_words(MAX_MULTIMEDIA_BYTES)))
    if flt is not None and flt.excluded_dataset_keys:
        say("Sources left out at your request (GBIF dataset keys): {}.".format(
            "; ".join(flt.excluded_dataset_keys)))
    doi = (d or {}).get("doi")
    cite = citation(key) if key else None
    if not cite and doi:
        cite = "GBIF.org GBIF Occurrence Download https://doi.org/" + doi
    if cite:
        say("Cite this download as: " + cite)
    elif not key:
        warn("The zip file does not say which GBIF download it is, so its DOI and citation could not "
             "be looked up.")
    report_sources(r, say)
    stamp_metadata(out_fc, summary_head + "GBIF download {}{}; original file: {}. {} records written{}{}. "
                   "Licenses: {}.".format(
                       key or "(key unknown)", ", DOI " + doi if doi else "", zip_path, n0(r.written),
                       ", {} of them without a point".format(n0(r.written_without_location))
                       if r.written_without_location else "",
                       "; {} outside the study area left out".format(n0(r.skipped_outside_area))
                       if r.skipped_outside_area else "",
                       "; ".join("{}: {}".format(license_short(k), n0(v))
                                 for k, v in sorted(r.by_license.items(), key=lambda kv: -kv[1])) or "none"),
                   cite, messages,
                   how="GBIF download {}{}, which GBIF prepared from its whole index. GBIF's original zip file, "
                       "with the records as GBIF supplied them, the citation of every source and their rights "
                       "statements, is {}.".format(
                           key or "(key unknown)", " (DOI {})".format(doi) if doi else "", zip_path),
                   flt=flt, r=r, key=key, doi=doi)
    out["written"] = r.written
    out["download_key"] = key
    out["doi"] = doi
    out["download_file"] = zip_path
    step(100, "Done: {} {}.".format(n0(r.written), "records" if r.written_without_location else "points"))
    return out
