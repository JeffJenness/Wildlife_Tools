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

"""GBIF Species Occurrences (gbif_occurrences.py): all three methods, a
direct fetch, a requested download and an existing download. Nothing here
touches GBIF, any account or the internet. A stand-in service on this
computer (stand_in_service.py) answers in GBIF's form with made-up records;
a guard stops any attempt to reach another computer; the stored sign-in is
replaced by a made-up one ("maria"), so Windows Credential Manager is never
read; and the engine's waits run against a clock of the test's own.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" gbif_occurrences_test.py
"""
import base64
import io
import json
import math
import os
import random
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
sys.path.insert(0, HERE)
import arcpy  # noqa: E402
import gbif_occurrences as G  # noqa: E402
from stand_in_service import StandIn, no_internet, pointed  # noqa: E402

guard = no_internet()                    # from here on, nothing may reach another computer
passed = failed = known = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


def known_fault(name, ok, detail=""):
    """A fault that has been found and reported and is not yet fixed: shown on every run, and not counted as a
    failure. When the engine is corrected this says so, and the line is to become an ordinary check()."""
    global known, passed
    if ok:
        passed += 1
        print("NOW PASSES (make it an ordinary check)  %s" % name)
    else:
        known += 1
        print("KNOWN FAULT, not yet fixed  %s  %s" % (name, detail))


def error_of(fn):
    try:
        fn()
        return None
    except Exception as e:                                            # noqa: BLE001
        return e


def refused(name, fn, has, status=None):
    e = error_of(fn)
    check(name, isinstance(e, G.GbifError) and has in str(e) and (status is None or e.status == status),
          "%s: %s" % (type(e).__name__, e))


class Msgs:
    def __init__(self):
        self.lines, self.warnings = [], []

    def addMessage(self, s):
        self.lines.append(s)

    def addWarningMessage(self, s):
        self.warnings.append(s)

    def has(self, text):
        return any(text in s for s in self.lines)

    def warned(self, text):
        return any(text in s for s in self.warnings)

    def everything(self):
        return "\n".join(self.lines + self.warnings)


class Clock:
    """Stands in for the engine's ``time``: sleeping takes no time and is written down."""

    def __init__(self):
        self.now = 5000.0
        self.slept = []

    def time(self):
        self.now += 0.001
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(float(seconds), 3))
        self.now += float(seconds)


USER, PASSWORD = "maria", "not-a-real-password-7Q"
BASIC = "Basic " + base64.b64encode(("%s:%s" % (USER, PASSWORD)).encode()).decode()
KEY = "0012345-260101123456789"
DOI = "10.15468/dl.abc123"
DS1, DS2 = "50c9509d-22c7-4a22-a47d-8c48425ef4a7", "8a863029-f435-446a-821e-275f4f641165"
TITLES = {DS1: "iNaturalist Research-grade Observations", DS2: "Museum of Northern Arizona & Friends"}


def record(i, lat=35.2, lon=-111.6, ds=DS1, **over):
    """One record as GBIF's search service gives it (numbers are numbers there)."""
    o = {"key": 4011000000 + i, "datasetKey": ds, "scientificName": "Antilocapra americana (Ord, 1815)",
         "species": "Antilocapra americana", "taxonRank": "SPECIES", "kingdom": "Animalia", "phylum": "Chordata",
         "class": "Mammalia", "order": "Artiodactyla", "family": "Antilocapridae", "genus": "Antilocapra",
         "taxonKey": 2440902, "speciesKey": 2440902, "eventDate": "2023-06-14T09:12:00", "year": 2023, "month": 6,
         "day": 14, "decimalLatitude": lat, "decimalLongitude": lon, "coordinateUncertaintyInMeters": 25.0,
         "elevation": 2100.5, "individualCount": 2, "basisOfRecord": "HUMAN_OBSERVATION", "occurrenceStatus": "PRESENT",
         "countryCode": "US", "stateProvince": "Arizona", "county": "Coconino", "locality": "north of Flagstaff",
         "recordedBy": "Maria Lopez|J. Smith", "identifiedBy": "Maria Lopez", "license": "CC_BY_NC_4_0",
         "datasetName": "the record's own name for its source", "iucnRedListCategory": "LC",
         "issues": ["COORDINATE_ROUNDED", "CONTINENT_DERIVED_FROM_COORDINATES"],
         "media": [{"type": "Sound", "identifier": "https://example.org/a.mp3"},
                   {"type": "StillImage", "identifier": "https://example.org/photo_%d.jpg" % i}],
         "extensions": {"something": [1, 2]}, "isSequenced": False, "lastInterpreted": "2024-01-01T00:00:00"}
    if lat is None:
        del o["decimalLatitude"], o["decimalLongitude"]
    o.update(over)
    return o


class FakeGbif:
    """What the stand-in answers."""

    def __init__(self, web):
        self.web = web
        self.records = []
        self.says_count = None
        self.statuses = ["SUCCEEDED"]    # what the download's status is, poll after poll (the last one stays)
        self.polls = 0
        self.zip_bytes = b""
        self.account_ok = True
        self.requested = []
        self.cancelled = []
        self.format = "DWCA"
        web.answer("/v1/occurrence/search/predicate", handler=self.search, method="POST")
        web.answer("/v2/taxon/", handler=self.taxon)
        web.answer("/v2/species/match", handler=self.match)
        web.answer("/v1/dataset/", handler=self.dataset)
        web.answer("/v1/occurrence/download/request/", handler=self.cancel, method="DELETE")
        web.answer(lambda q: q.path == "/v1/occurrence/download/request", handler=self.request, method="POST")
        web.answer(lambda q: q.path.endswith("/citation"), handler=self.cite)
        web.answer(lambda q: q.path.endswith("/datasets"), handler=self.datasets)
        web.answer("/files/", handler=lambda q: (200, {"Content-Type": "application/zip"}, self.zip_bytes))
        web.answer("/v1/occurrence/download/", handler=self.status)

    def search(self, q):
        b = q.json()
        limit, offset = int(b["limit"]), int(b["offset"])
        total = len(self.records) if self.says_count is None else self.says_count
        return {"offset": offset, "limit": limit, "endOfRecords": offset + limit >= len(self.records), "count": total,
                "results": self.records[offset:offset + limit]}

    def taxon(self, q):
        key = q.path.rsplit("/", 1)[1]
        if key == "2440902":
            return {"key": 2440902, "scientificName": "Antilocapra americana (Ord, 1815)", "taxonRank": "SPECIES"}
        if key == "359":
            return {"key": 359, "scientificName": "Mammalia", "taxonRank": "CLASS"}
        return 404, {}, "Entity not found"

    def match(self, q):
        name = (q.one("scientificName") or "").lower()
        if name == "antilocapra americana":
            return {"usage": {"key": 2440902, "name": "Antilocapra americana (Ord, 1815)",
                              "canonicalName": "Antilocapra americana", "rank": "SPECIES"},
                    "diagnostics": {"matchType": "EXACT"}}
        if name == "antilocapra americanna":
            return {"usage": {"key": 2440902, "canonicalName": "Antilocapra americana", "rank": "SPECIES"},
                    "diagnostics": {"matchType": "FUZZY"}}
        if name == "antilocapra nonsensica":
            return {"usage": {"key": 2440901, "canonicalName": "Antilocapra", "rank": "GENUS"},
                    "diagnostics": {"matchType": "HIGHERRANK"}}
        return {"diagnostics": {"matchType": "NONE"}}

    def dataset(self, q):
        key = q.path.rsplit("/", 1)[1]
        return {"key": key, "title": TITLES[key]} if key in TITLES else (404, {}, "Entity not found")

    def request(self, q):
        if q.headers.get("authorization") != BASIC or not self.account_ok:
            return 401, {}, ""
        self.requested.append(q.json())
        return 201, {"Content-Type": "application/json"}, KEY

    def cancel(self, q):
        self.cancelled.append(q.path.rsplit("/", 1)[1])
        return 204, {}, ""

    def status(self, q):
        key = q.path.rsplit("/", 1)[1]
        if key != KEY:
            return 404, {}, "Download not found"
        st = self.statuses[min(self.polls, len(self.statuses) - 1)]
        self.polls += 1
        return {"key": KEY, "doi": DOI, "status": st, "totalRecords": 4, "numberDatasets": 2, "size": len(self.zip_bytes),
                "downloadLink": self.web.url + "files/" + KEY + ".zip", "request": {"format": self.format}}

    def cite(self, q):
        return 200, {"Content-Type": "text/plain"}, "GBIF.org (23 November 2026) GBIF Occurrence Download https://doi.org/%s\n" % DOI

    def datasets(self, q):
        return {"offset": 0, "limit": 100, "endOfRecords": True,
                "results": [{"datasetKey": k, "datasetTitle": t} for k, t in TITLES.items()]}


work = tempfile.mkdtemp(prefix="gbif_test_")
arcpy.env.overwriteOutput = True
gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
web = StandIn()
clock = Clock()
account = [(USER, PASSWORD)]             # what the made-up credential store holds; [None] is "no sign-in stored"
env_before = dict((k, os.environ.pop(k, None)) for k in ("GBIF_USER", "GBIF_PWD"))


def fresh():
    web.forget()
    del clock.slept[:]
    account[0] = (USER, PASSWORD)
    return FakeGbif(web)


point = pointed(G, V1=web.url + "v1/", V2=web.url + "v2/", time=clock,
                _read_windows_credential=lambda target: account[0] if target == G.CREDENTIAL_TARGET else None)
point.__enter__()

# =========================================================================================================
#  1. small pieces
# =========================================================================================================
check("numbers in words", (G.n0(1234567), G.span(45), G.span(600), G.span(7200), G.size_words(900), G.size_words(2048),
                           G.size_words(5 * 1024 ** 2), G.size_words(3 * 1024 ** 3))
      == ("1,234,567", "45 seconds", "10 minutes", "2.0 hours", "900 bytes", "2 KB", "5.0 MB", "3.00 GB"))
check("coordinates are written to six decimals of a degree, without trailing zeros",
      [G._num(v) for v in (-111.6, 35, 35.1234567, 0.0000001, 180.0, -0.5)] == ["-111.6", "35", "35.123457", "0", "180", "-0.5"],
      [G._num(v) for v in (-111.6, 35, 35.1234567, 0.0000001, 180.0, -0.5)])
check("a list typed into a box: semicolons and new lines, quotes and blanks dropped",
      G.split_list(' "Antilocapra americana" ; 2440902\n\n ;Puma ') == ["Antilocapra americana", "2440902", "Puma"])
check("several people in one field are separated the same way whichever route the record came by",
      G.people_list("Maria Lopez|J. Smith") == "Maria Lopez; J. Smith" and G.people_list("A ; B;C") == "A; B; C"
      and G.people_list("Just One") == "Just One")
check("a license is given as its web address, whichever form GBIF sent",
      G.normalize("license", "CC_BY_4_0") == "http://creativecommons.org/licenses/by/4.0/legalcode"
      and G.normalize("license", "http://creativecommons.org/publicdomain/zero/1.0/legalcode")
      == "http://creativecommons.org/publicdomain/zero/1.0/legalcode" and G.normalize("locality", "CC_BY_4_0") == "CC_BY_4_0")
check("licenses in words", (G.license_short("http://creativecommons.org/publicdomain/zero/1.0/legalcode"),
                           G.license_short("http://creativecommons.org/licenses/by/4.0/legalcode"),
                           G.license_short("http://creativecommons.org/licenses/by-nc/4.0/legalcode"), G.license_short(None))
      == ("CC0 (no restriction)", "CC BY (credit required)", "CC BY-NC (credit required, non-commercial use only)", "(not stated)"))
check("text that is a number, and text that is not",
      [G._to_float(t) for t in ("35.5", " -111.25 ", "1e3", ".5", "abc", "", None, "nan", "inf", "12,5")]
      == [35.5, -111.25, 1000.0, 0.5, None, None, None, None, None, None])
wr = G.WriteResult()
check("values are fitted to their fields: whole numbers, decimals, and text cut to length (and counted)",
      (G._convert("I", 0, "2023", wr), G._convert("I", 0, "2.0", wr), G._convert("I", 0, "many", wr),
       G._convert("I", 0, "99999999999", wr), G._convert("D", 0, "25.5", wr), G._convert("D", 0, "about 25", wr),
       G._convert("T", 5, "abcdefgh", wr), G._convert("T", 5, "abc", wr), G._convert("T", 5, "", wr), wr.truncated)
      == (2023, 2, None, None, 25.5, None, "abcde", "abc", None, 1))
check("kinds of record: the default three; none or all of them is no filter",
      G.kinds_to_codes(G.RECORD_KIND_DEFAULT) == ["HUMAN_OBSERVATION", "OBSERVATION", "MACHINE_OBSERVATION", "OCCURRENCE",
                                                  "PRESERVED_SPECIMEN", "MATERIAL_SAMPLE", "MATERIAL_CITATION"]
      and G.kinds_to_codes([]) == [] and G.kinds_to_codes(G.RECORD_KIND_NAMES) == [] and G.kinds_to_codes(["Fossils"]) == ["FOSSIL_SPECIMEN"])
check("errors in words", G.explain(401, "").startswith("GBIF did not accept the user name and password")
      and G.explain(429, "").startswith("GBIF is limiting requests") and G.explain(420, "").startswith("GBIF is limiting requests")
      and G.explain(503, "<html>x</html>") == "GBIF is busy or unavailable right now. Try again in a few minutes."
      and G.explain(400, "Unknown predicate key FOO") == "GBIF returned an error (400). Unknown predicate key FOO"
      and G.explain(413, "") == "GBIF refused the request as too large.")
check("a download's status in words", (G.status_words("SUSPENDED"), G.status_words("KILLED"), G.status_words("FILE_ERASED"),
                                       G.status_words(None), G.status_words("NEW_ONE"))
      == ("waiting its turn", "stopped by GBIF", "erased by GBIF", "unknown", "new_one"))
check("a download's key has GBIF's form", bool(G.KEY_RE.match(KEY)) and not G.KEY_RE.match("12345-26") and not G.KEY_RE.match(KEY + "x"))
check("the output must be a feature class directly in a file geodatabase, with a plain name",
      G.check_output(os.path.join(gdb, "points")) is None and "inside a file geodatabase" in G.check_output(r"C:\data\points.shp")
      and "not inside a feature dataset" in G.check_output(os.path.join(gdb, "fds", "points"))
      and "must start with a letter" in G.check_output(os.path.join(gdb, "2 points")) and G.check_output("") is not None)
check("the fields: 48, each named once, each with a description",
      len(G.FIELDS) == 48 == len(set(G.FIELD_NAMES)) and all(f[1] and f[6] and f[2] in "TID" for f in G.FIELDS))

# ---- rings, hulls and the point-in-polygon test
check("a ring is closed, and a repeated corner dropped",
      G.close_ring([(0, 0), (4, 0), (4, 0), (4, 3)]) == [(0, 0), (4, 0), (4, 3), (0, 0)])
check("signed area: counterclockwise is positive", G.signed_area([(0, 0), (4, 0), (4, 3)]) == 6.0
      and G.signed_area([(0, 0), (4, 3), (4, 0)]) == -6.0)
check("convex hull of a square with points inside and on its sides: the four corners, counterclockwise",
      G.convex_hull([(0, 0), (2, 0), (4, 0), (4, 4), (0, 4), (2, 2), (1, 3), (4, 2), (0, 0)]) == [(0, 0), (4, 0), (4, 4), (0, 4)],
      G.convex_hull([(0, 0), (2, 0), (4, 0), (4, 4), (0, 4), (2, 2), (1, 3), (4, 2), (0, 0)]))
circle = [(-111.0 + 0.5 * math.cos(2 * math.pi * k / 400), 35.0 + 0.5 * math.sin(2 * math.pi * k / 400)) for k in range(400)]
poly, how = G.enclosing_polygon(circle, 40)


def in_convex(poly_, p, tol=1e-9):
    n = len(poly_)
    return all((poly_[(k + 1) % n][0] - poly_[k][0]) * (p[1] - poly_[k][1])
               - (poly_[(k + 1) % n][1] - poly_[k][1]) * (p[0] - poly_[k][0]) >= -tol for k in range(n))


check("a hull of 400 corners cut down to at most 40: a convex polygon that still holds every corner of the hull",
      how == "edges" and 3 <= len(poly) <= 40 and all(in_convex(poly, p) for p in circle), (how, len(poly)))
check("and is only a little larger than the hull (under 3 percent)",
      0 < abs(G.signed_area(poly)) / abs(G.signed_area(circle)) - 1.0 < 0.03, abs(G.signed_area(poly)) / abs(G.signed_area(circle)))
check("a hull that is small enough is used as it is", G.enclosing_polygon(circle[:30], 40) == (circle[:30], "hull"))

HOLED = [[[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)], [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]],
         [[(20.0, 0.0), (25.0, 0.0), (22.5, 7.0)]]]


def plain_inside(polys, x, y):
    """Even-odd, every edge of every ring, each polygon by itself."""
    for rings in polys:
        inside = False
        for ring in rings:
            j = len(ring) - 1
            for i in range(len(ring)):
                (ax, ay), (bx, by) = ring[i], ring[j]
                j = i
                if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
                    inside = not inside
        if inside:
            return True
    return False


rnd = random.Random(4)
pts = [(rnd.uniform(-2, 27), rnd.uniform(-2, 12)) for _ in range(20000)]
index = G.PolygonIndex(HOLED)
check("inside or outside: the fast test agrees with the plain one for 20,000 random points, hole and all",
      all(index.contains(x, y) == plain_inside(HOLED, x, y) for x, y in pts))
check("inside or outside: known places", (index.contains(1, 1), index.contains(5, 5), index.contains(22.5, 3),
                                           index.contains(15, 5), index.contains(22.5, 9)) == (True, False, True, False, False))
f = G.GbifFilter()
check("with no study area every place is kept", f.contains(0, 0) is True)

# ---- the filter as GBIF's predicate
f = G.GbifFilter()
check("filter: with nothing chosen, only records that have coordinates",
      f.predicate() == {"type": "equals", "key": "HAS_COORDINATE", "value": "true"}, f.predicate())
f.include_no_coordinates = True
check("filter: records without coordinates wanted too: no condition at all", f.predicate() is None)
f = G.GbifFilter()
f.taxon_keys = ["2440902"]
p1 = f.predicate()
f.taxon_keys = ["2440902", "359"]
p2 = f.predicate()
check("filter: one species is 'equals', several are 'in'",
      p1["predicates"][0] == {"type": "equals", "key": "TAXON_KEY", "value": "2440902"}
      and p2["predicates"][0] == {"type": "in", "key": "TAXON_KEY", "values": ["2440902", "359"]}, (p1, p2))
f = G.GbifFilter()
f.polygons = [[[(0, 0), (0, 10), (10, 10), (10, 0)], [(4, 4), (6, 4), (6, 6), (4, 6)]]]       # outer clockwise, hole counterclockwise
check("filter: the study area as WKT: closed rings, the outer one counterclockwise and the hole clockwise, as GBIF wants",
      f.to_wkt() == "POLYGON((0 0,10 0,10 10,0 10,0 0),(4 4,4 6,6 6,6 4,4 4))", f.to_wkt())
check("filter: a study area is a 'within' condition, and then no separate condition on coordinates",
      f.predicate() == {"type": "within", "geometry": "POLYGON((0 0,10 0,10 10,0 10,0 0),(4 4,4 6,6 6,6 4,4 4))"}, f.predicate())
f.include_no_coordinates = True
check("filter: study area, and records without coordinates too: inside the area OR without coordinates",
      f.predicate() == {"type": "or", "predicates": [{"type": "within", "geometry": f.to_wkt()},
                                                     {"type": "equals", "key": "HAS_COORDINATE", "value": "false"}]})
f.polygons.append([[(20, 0), (25, 0), (22.5, 7)]])
check("filter: two polygons are one MULTIPOLYGON", f.to_wkt().startswith("MULTIPOLYGON(((0 0,") and f.to_wkt().endswith(",((20 0,25 0,22.5 7,20 0)))"),
      f.to_wkt())
check("filter: the outline's corners are counted as GBIF will count them", f.polygon_point_count() == 5 + 5 + 4 and f.polygon_fits_download())
f = G.GbifFilter()
f.country, f.state_province, f.year_from, f.year_to, f.months = "US", "Arizona", 2015, 2024, [6, 7, 7]
f.basis_of_record, f.licenses = ["HUMAN_OBSERVATION"], ["CC0_1_0", "CC_BY_4_0"]
f.exclude_geospatial_issues = f.exclude_centroids = f.present_only = f.images_only = True
f.max_uncertainty_m, f.excluded_dataset_keys = 100, [DS2]
got = dict((json.dumps(c, sort_keys=True), 1) for c in f.predicate()["predicates"])
want = [{"type": "equals", "key": "HAS_COORDINATE", "value": "true"}, {"type": "equals", "key": "COUNTRY", "value": "US"},
        {"type": "equals", "key": "STATE_PROVINCE", "value": "Arizona"},
        {"type": "greaterThanOrEquals", "key": "YEAR", "value": "2015"}, {"type": "lessThanOrEquals", "key": "YEAR", "value": "2024"},
        {"type": "in", "key": "MONTH", "values": ["6", "7"]}, {"type": "in", "key": "BASIS_OF_RECORD", "values": ["HUMAN_OBSERVATION"]},
        {"type": "in", "key": "LICENSE", "values": ["CC0_1_0", "CC_BY_4_0"]},
        {"type": "equals", "key": "HAS_GEOSPATIAL_ISSUE", "value": "false"},
        {"type": "not", "predicate": {"type": "in", "key": "DATASET_KEY", "values": [DS2]}},
        {"type": "isNull", "parameter": "DISTANCE_FROM_CENTROID_IN_METERS"},
        {"type": "equals", "key": "OCCURRENCE_STATUS", "value": "PRESENT"},
        {"type": "lessThanOrEquals", "key": "COORDINATE_UNCERTAINTY_IN_METERS", "value": "100"},
        {"type": "equals", "key": "MEDIA_TYPE", "value": "StillImage"}]
check("filter: every other choice, each as GBIF's condition, all joined by 'and'",
      f.predicate()["type"] == "and" and len(got) == len(want) and all(json.dumps(c, sort_keys=True) in got for c in want),
      [c for c in want if json.dumps(c, sort_keys=True) not in got])

# =========================================================================================================
#  2. talking to GBIF
# =========================================================================================================
gbif = fresh()
web.answer("/v1/ping", json={"ok": True})
G._json(web.url + "v1/ping")
a = web.asked("/v1/ping")[0]
check("requests say who is asking; a plain request carries no sign-in",
      a.headers.get("user-agent") == G.USER_AGENT and a.headers.get("accept") == "application/json"
      and "authorization" not in a.headers and "@" not in G.USER_AGENT, a.headers)
# GBIF's API reference: "we highly recommend you set the HTTP User-Agent to a URL or email address. We can then
# contact you if there is a problem."
check("requests give GBIF a web address to reach the tool's author at, and name the add-in",
      a.headers.get("user-agent") == "WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro; https://www.jennessent.com)",
      a.headers.get("user-agent"))
client_cs = open(os.path.join(HERE, "..", "WildlifeTools", "OnlineData", "Gbif", "GbifClient.cs"), encoding="utf-8").read()
check("the add-in's window names itself to GBIF in the same words as the geoprocessing tool",
      '"%s"' % G.USER_AGENT in client_cs and "GbifClient(string userAgent = UserAgent)" in client_cs)
web.forget()
web.answer("/v1/busy", status=503, body="<html>busy</html>", times=2)
web.answer("/v1/busy", json={"ok": 1})
check("busy twice, then an answer: asked three times, after waits of 2 and 5 seconds",
      G._json(web.url + "v1/busy") == {"ok": 1} and len(web.asked("/v1/busy")) == 3 and clock.slept == [2.0, 5.0], clock.slept)
web.forget()
web.answer("/v1/down", status=503, body="busy")
refused("busy every time: given up, in words a user can act on", lambda: G._json(web.url + "v1/down"),
        "GBIF is busy or unavailable right now. Try again in a few minutes.", 503)
for status, words in ((429, "GBIF is limiting requests"), (400, "GBIF returned an error (400). the reason"),
                      (404, "GBIF returned an error (404)."), (401, "GBIF did not accept the user name and password.")):
    web.forget()
    web.answer("/v1/no", status=status, body="the reason")
    refused("status %d: reported at once with GBIF's own reason" % status, lambda: G._json(web.url + "v1/no"), words, status)
    check("status %d: asked once only" % status, len(web.asked("/v1/no")) == 1, len(web.asked("/v1/no")))
web.forget()
web.answer("/v1/page", body="<html>Maintenance</html>")
refused("a web page in place of data: reported for what it is", lambda: G._json(web.url + "v1/page"),
        "GBIF returned something that is not the expected data.")
web.forget()
web.fail("/v1/gone", "refuse")
e = error_of(lambda: G._json(web.url + "v1/gone"))
check("a connection that is dropped: a failure to reach GBIF or to get its answer, not a crash",
      isinstance(e, G.GbifError) and ("could not be reached" in str(e) or "did not answer in time" in str(e)), repr(e))
web.forget()
web.answer("/v1/post", status=503, body="busy")
e = error_of(lambda: G._json(web.url + "v1/post", data=b"{}"))
check("a request that changes something at GBIF (it has a body) is sent ONCE, even when GBIF is busy",
      isinstance(e, G.GbifError) and len(web.asked("/v1/post")) == 1, len(web.asked("/v1/post")))
e = error_of(lambda: G._json(web.url + "v1/post", data=b"{}", repeatable=True))
check("unless it only reads (a search): then it is repeated like any other", len(web.asked("/v1/post")) > 2, len(web.asked("/v1/post")))
check("numbers can be kept as the text GBIF sent, so no digit is lost",
      (lambda: (web.forget(), web.answer("/v1/n", body='{"key": 4011223344556677889, "lat": 35.123456789012345}'),
                G._json(web.url + "v1/n", raw_numbers=True))[2])() == {"key": "4011223344556677889", "lat": "35.123456789012345"})

# the sign-in
check("the stored sign-in is read from the place the window keeps it", G.stored_account() == (USER, PASSWORD)
      and G.stored_user_name() == USER and G.CREDENTIAL_TARGET == "JennessWildlifeTools.GBIF")
account[0] = None
check("no sign-in stored: none, and no error", G.stored_account() is None and G.stored_user_name() is None)
os.environ["GBIF_USER"], os.environ["GBIF_PWD"] = "robot", "robot-pwd"
check("no sign-in stored: the environment variables GBIF_USER and GBIF_PWD are used instead", G.stored_account() == ("robot", "robot-pwd"))
del os.environ["GBIF_USER"], os.environ["GBIF_PWD"]
account[0] = (USER, PASSWORD)

# names
gbif = fresh()
said = []
keys = G.resolve_taxa("Antilocapra americana; 359; Pronghorn (2440902)\nantilocapra americanna", said.append)
check("species: by name, by key, by name with its key; a repeat is taken once", keys == ["2440902", "359"], keys)
check("species: each is reported; a name GBIF had to read as another is said to be so",
      said[0] == "Species or group: Antilocapra americana (species; GBIF taxon key 2440902)."
      and said[1] == "Species or group: Mammalia (class; GBIF taxon key 359)."
      and said[3].endswith('The name given was "antilocapra americanna"; GBIF read it as this one.'), said)
refused("species: a name GBIF can place only to a higher rank is refused, and the rank it found is named",
        lambda: G.resolve_taxa("Antilocapra nonsensica", said.append),
        'GBIF does not know the name "Antilocapra nonsensica" (the closest it has is the genus Antilocapra).')
refused("species: a name GBIF does not know", lambda: G.resolve_taxa("Unicornus", said.append), 'GBIF does not know the name "Unicornus".')
refused("species: a key that is not in GBIF's backbone", lambda: G.resolve_taxa("999", said.append),
        "999 is not a taxon key in the GBIF backbone taxonomy.")
check("species: a key is looked up in the backbone taxonomy",
      any(a.path == "/v2/taxon/%s/359" % G.BACKBONE for a in web.asked("/v2/taxon/")), [a.path for a in web.asked("/v2/taxon/")])

# =========================================================================================================
#  3. the direct fetch, page by page
# =========================================================================================================
def fetch(n, says=None, total=None, cancel_at=None):
    gbif = fresh()
    gbif.records = [record(i) for i in range(n)]
    gbif.says_count = says
    f = G.GbifFilter()
    f.taxon_keys = ["2440902"]
    said, steps = [], []
    got = G.fetch_search(f, n if total is None else total, said.append, lambda p, t: steps.append(t),
                         lambda: cancel_at is not None and len(web.asked("/v1/occurrence/search/predicate")) >= cancel_at)
    return got, said, steps, f


f = G.GbifFilter()
f.taxon_keys = ["2440902"]
gbif = fresh()
gbif.records = [record(i) for i in range(7)]
check("the count is asked for with the filter and for no records",
      G.count_records(f) == 7 and web.asked("/v1/occurrence/search/predicate")[0].json()
      == {"predicate": f.predicate(), "limit": 0, "offset": 0}, web.asked("/v1/occurrence/search/predicate")[0].json())
got, said, steps, f = fetch(650)
pages = [a.json() for a in web.asked("/v1/occurrence/search/predicate")]
check("650 records: three requests, of 300, 300 and the 50 that are left",
      len(got) == 650 and [(p["limit"], p["offset"]) for p in pages] == [(300, 0), (300, 300), (50, 600)],
      [(p["limit"], p["offset"]) for p in pages])
check("pages: each carries the filter", all(p["predicate"] == f.predicate() for p in pages))
check("pages: the records are in order and none is repeated", [o["key"] for o in got] == [str(4011000000 + i) for i in range(650)])
check("pages: only what the output needs is kept of each record",
      "extensions" not in got[0] and "lastInterpreted" not in got[0] and "media" in got[0] and "issues" in got[0])
check("pages: a short pause between requests, and the total at the end",
      clock.slept.count(0.15) >= 2 and said[-1].startswith("Fetched 650 records in "), (clock.slept, said[-1:]))
got, said, steps, f = fetch(100000, total=100000)
last = web.asked("/v1/occurrence/search/predicate")[-1].json()
check("100,000 records, the most GBIF's search will give: the last page ends exactly there",
      len(got) == 100000 and last["offset"] + last["limit"] == 100000, (len(got), last["offset"], last["limit"]))
got, said, steps, f = fetch(40, total=500)
check("GBIF gives fewer than it counted (40 of 500): the fetch ends with what there is", len(got) == 40, len(got))
e = error_of(lambda: fetch(2000, cancel_at=2))
check("cancelled after two pages: the fetch stops there", isinstance(e, G.Cancelled)
      and len(web.asked("/v1/occurrence/search/predicate")) == 2, repr(e))


def fetch_with_trouble(status, times, n=650):
    gbif = fresh()
    web.forget()
    web.answer("/v1/occurrence/search/predicate", status=status, body="trouble", times=times, method="POST")
    gbif = FakeGbif(web)
    gbif.records = [record(i) for i in range(n)]
    f = G.GbifFilter()
    said = []
    try:
        return G.fetch_search(f, n, said.append, lambda p, t: None, lambda: False), said
    except Exception as e:                                            # noqa: BLE001
        return e, said


got, said = fetch_with_trouble(429, 2)
check("asked to slow down twice: 30 seconds' wait each time, said in the messages, and then everything is fetched",
      not isinstance(got, Exception) and len(got) == 650
      and said[:2] == ["GBIF asked this computer to slow down; waiting 30 seconds (1 of 5).",
                       "GBIF asked this computer to slow down; waiting 30 seconds (2 of 5)."]
      and 59.0 <= sum(s for s in clock.slept if s >= 0.5) <= 61.0, (repr(got)[:80], said[:2], sum(clock.slept)))
got, said = fetch_with_trouble(429, 6)
check("asked to slow down six times in a row: given up after five waits",
      isinstance(got, G.GbifError) and got.status == 429 and len(said) == 5, (repr(got), said))
got, said = fetch_with_trouble(400, 1)
check("a request GBIF refuses outright (400): reported at once, with no waiting",
      isinstance(got, G.GbifError) and got.status == 400 and said == [], (repr(got), said))

# one search record into one row
row = next(G.records_from_search([json.loads(json.dumps(record(5)), parse_float=str, parse_int=str)]))
lon, lat, has, v = row
check("search record: the place", (lon, lat, has) == (-111.6, 35.2, True), (lon, lat, has))
check("search record: what, when and where",
      (v["gbifID"], v["scientificName"], v["species"], v["taxonRank"], v["class_"], v["order_"], v["taxonKey"], v["eventDate"],
       v["year"], v["month"], v["day"], v["countryCode"], v["stateProvince"], v["county"])
      == ("4011000005", "Antilocapra americana (Ord, 1815)", "Antilocapra americana", "SPECIES", "Mammalia", "Artiodactyla",
          "2440902", "2023-06-14T09:12:00", "2023", "6", "14", "US", "Arizona", "Coconino"), v)
check("search record: GBIF's flags are joined; the first photograph (not the sound) is given",
      (v["issues"], v["mediaType"], v["photoURL"]) == ("COORDINATE_ROUNDED;CONTINENT_DERIVED_FROM_COORDINATES", "StillImage",
                                                       "https://example.org/photo_5.jpg"), (v["issues"], v["mediaType"], v["photoURL"]))
bare = next(G.records_from_search([{"key": "9", "media": [{"type": "Sound", "identifier": "x"}], "issues": [],
                                    "recordedBy": ["a list", "is not a value"], "isSequenced": True}]))
check("search record: no coordinates is said to be so; a record with only a sound has no photograph; a list is not a value",
      bare[2] is False and bare[3]["mediaType"] is None and bare[3]["photoURL"] is None and bare[3]["recordedBy"] is None
      and bare[3]["issues"] == "", bare)

# =========================================================================================================
#  4. a download's file
# =========================================================================================================
COLS = [f_[5] for f_ in G.FIELDS if f_[5]] + ["extraColumn"]


def dwca_row(i, lat="35.2", lon="-111.6", ds=DS1, **over):
    r = dict((c, "") for c in COLS)
    r.update({"gbifID": str(4011000000 + i), "scientificName": "Antilocapra americana (Ord, 1815)",
              "species": "Antilocapra americana", "taxonRank": "SPECIES", "kingdom": "Animalia", "class": "Mammalia",
              "order": "Artiodactyla", "genus": "Antilocapra", "taxonKey": "2440902", "eventDate": "2023-06-14",
              "year": "2023", "month": "6", "day": "14", "decimalLatitude": lat, "decimalLongitude": lon,
              "coordinateUncertaintyInMeters": "25.0", "basisOfRecord": "HUMAN_OBSERVATION", "countryCode": "US",
              "recordedBy": "Maria Lopez|J. Smith", "datasetKey": ds, "datasetName": "the record's own name",
              "license": "CC_BY_4_0", "mediaType": "StillImage", "sex": "FEMALE", "lifeStage": "ADULT",
              "issue": "COORDINATE_ROUNDED"})
    r.update(over)
    return "\t".join(r[c] for c in COLS)


def make_zip(path, rows, simple=False, metadata=True, multimedia=True, titles=True, bom=False):
    table = ("\ufeff" if bom else "") + "\t".join(COLS) + "\n" + "\n".join(rows) + "\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if simple:
            z.writestr(KEY + ".csv", table)
            return path
        z.writestr("occurrence.txt", table)
        z.writestr("verbatim.txt", "gbifID\n1\n")
        if metadata:
            z.writestr("metadata.xml", "<eml><dataset><title>GBIF Occurrence Download %s</title></dataset></eml>" % KEY)
        if multimedia:
            z.writestr("multimedia.txt", "gbifID\ttype\tformat\tidentifier\n"
                       "4011000001\tSound\taudio/mp3\thttps://example.org/1.mp3\n"
                       "4011000001\tStillImage\timage/jpeg\thttps://example.org/1a.jpg\n"
                       "4011000001\tStillImage\timage/jpeg\thttps://example.org/1b.jpg\n"
                       "4011000002\t\timage/jpeg\thttps://example.org/2.jpg\n"
                       "4011000003\tStillImage\timage/jpeg\thttps://example.org/" + "x" * 600 + ".jpg\n"
                       "short line\n")
        if titles:
            z.writestr("dataset/%s.xml" % DS1, "<eml><dataset><title>%s</title></dataset></eml>" % TITLES[DS1])
            z.writestr("dataset/%s.xml" % DS2, "<eml><dataset><title xml:lang=\"en\">Museum of Northern Arizona &amp; Friends</title></dataset></eml>")
    return path


FOUR = [dwca_row(1), dwca_row(2, ds=DS2, license="CC0_1_0"), dwca_row(3, lat="", lon=""), dwca_row(4, lat="0.0", lon="0.0")]
zpath = make_zip(os.path.join(work, "dwca.zip"), FOUR + ["4011000009\ttoo\tshort", ""], bom=True)
t = G.DownloadFile(zpath)
check("download file: the records' table is found, and a mark at the start of the file does not spoil the first column",
      t.table_name == "occurrence.txt" and t.columns[0] == COLS[0] and t.has_coordinates, (t.table_name, t.columns[:2]))
recs = list(t.records())
check("download file: every row is read; a short row is filled out and an empty line passed over", len(recs) == 5, len(recs))
check("download file: a row's place and values", recs[0][:3] == (-111.6, 35.2, True) and recs[0][3]["gbifID"] == "4011000001"
      and recs[0][3]["class_"] == "Mammalia" and recs[0][3]["sex"] == "FEMALE" and recs[0][3]["issues"] == "COORDINATE_ROUNDED", recs[0])
check("download file: a row with no coordinates is said to have none", recs[2][2] is False and recs[3][:3] == (0.0, 0.0, True))
check("download file: each record's first photograph, from the table of media (a sound is passed over, an address too "
      "long for its field left out)", [r[3]["photoURL"] for r in recs[:4]] == ["https://example.org/1a.jpg", "https://example.org/2.jpg", None, None],
      [r[3]["photoURL"] for r in recs[:4]])
check("download file: which download it is, and its sources' names (with '&' read properly)",
      t.download_key() == KEY and t.dataset_titles() == TITLES, (t.download_key(), t.dataset_titles()))
t.close()
t = G.DownloadFile(make_zip(os.path.join(work, "simple.zip"), FOUR, simple=True))
recs = list(t.records())
check("a download in GBIF's Simple form: read the same way; it is known by its file's name and carries no photographs",
      t.table_name == KEY + ".csv" and len(recs) == 4 and t.download_key() == KEY and t.images_absent
      and recs[0][3]["photoURL"] is None and t.dataset_titles() == {}, (t.table_name, len(recs)))
t.close()
open(os.path.join(work, "broken.zip"), "wb").write(b"PK\x03\x04 this is not a whole zip file")
refused("a file that is not a whole zip", lambda: G.DownloadFile(os.path.join(work, "broken.zip")), "cannot be read as a zip file")
with zipfile.ZipFile(os.path.join(work, "other.zip"), "w") as z:
    z.writestr("readme.txt", "nothing here")
refused("a zip with no table of records", lambda: G.DownloadFile(os.path.join(work, "other.zip")),
        "The GBIF download does not contain a table this tool can read.")

# =========================================================================================================
#  5. writing the points
# =========================================================================================================
N_RUN = [0]


def out_fc():
    N_RUN[0] += 1
    return os.path.join(gdb, "gbif_%02d" % N_RUN[0])


def table(fc):
    return [dict(zip(["shape"] + G.FIELD_NAMES, r)) for r in arcpy.da.SearchCursor(fc, ["SHAPE@"] + G.FIELD_NAMES)]


t = G.DownloadFile(zpath)
o = out_fc()
r = G.write_points(o, t.records(), dataset_titles=t.dataset_titles())
t.close()
rows_ = table(o)
check("writing: points in WGS 84 with the engine's fields in order",
      arcpy.Describe(o).spatialReference.factoryCode == 4326 and [f_.name for f_ in arcpy.ListFields(o)][2:] == G.FIELD_NAMES)
check("writing: the two records without coordinates (one of them the short row), and the one at 0, 0, are left "
      "out and counted apart",
      (r.written, r.skipped_no_coordinates, r.skipped_zero_zero) == (2, 2, 1) and [x["gbifID"] for x in rows_] == ["4011000001", "4011000002"],
      (r.written, r.skipped_no_coordinates, r.skipped_zero_zero))
x = rows_[0]
check("writing: a row's point and values, with numbers as numbers",
      abs(x["shape"].firstPoint.X - -111.6) < 1e-9 and abs(x["shape"].firstPoint.Y - 35.2) < 1e-9
      and (x["year"], x["month"], x["day"], x["decimalLatitude"], x["coordUncert_m"], x["individualCount"]) == (2023, 6, 14, 35.2, 25.0, None), x)
check("writing: several people are separated by semicolons; the license is its web address; the record's page is given",
      (x["recordedBy"], x["license"], x["recordURL"]) == ("Maria Lopez; J. Smith", "http://creativecommons.org/licenses/by/4.0/legalcode",
                                                         "https://www.gbif.org/occurrence/4011000001"), (x["recordedBy"], x["license"], x["recordURL"]))
check("writing: each row gets GBIF's own title of its source, not the name the record carried",
      (rows_[0]["datasetName"], rows_[1]["datasetName"]) == (TITLES[DS1], TITLES[DS2]) and r.dataset_names == TITLES
      and r.by_dataset == {DS1: 1, DS2: 1}, (rows_[0]["datasetName"], r.by_dataset))
check("writing: the licenses are counted", r.by_license == {"http://creativecommons.org/licenses/by/4.0/legalcode": 1,
                                                            "http://creativecommons.org/publicdomain/zero/1.0/legalcode": 1}, r.by_license)
t = G.DownloadFile(zpath)
o = out_fc()
r = G.write_points(o, t.records(), keep_unlocated=True)
t.close()
rows_ = table(o)
check("writing, records without coordinates kept: they are rows with NO point, never a point at 0, 0",
      (r.written, r.written_without_location) == (5, 3) and [x["shape"] is None for x in rows_] == [False, False, True, True, True]
      and rows_[2]["gbifID"] == "4011000003" and rows_[3]["gbifID"] == "4011000004", (r.written, r.written_without_location, [x["shape"] is None for x in rows_]))
t = G.DownloadFile(zpath)
o = out_fc()
r = G.write_points(o, t.records(), keep=lambda lon, lat: False)
t.close()
check("writing, cut to a study area: records outside it are left out and counted", (r.written, r.skipped_outside_area) == (0, 2),
      (r.written, r.skipped_outside_area))
many = [(-111.0, 35.0, True, {"gbifID": str(i), "license": "CC0_1_0"}) for i in range(1200)]
half = out_fc()
e = error_of(lambda: G.write_points(half, iter(many), cancelled=lambda: True))
check("cancelled while writing: the writing stops", isinstance(e, G.Cancelled), repr(e))
left = int(arcpy.management.GetCount(half)[0]) if arcpy.Exists(half) else None
check("cancelled while writing: no half-written feature class is left under the output's name", left is None,
      "an empty or part-written feature class (%s rows) is left" % left)

# =========================================================================================================
#  6. whole runs
# =========================================================================================================
SR84 = arcpy.SpatialReference(4326)
BOX = arcpy.Extent(-112.0, 35.0, -111.0, 36.0, spatial_reference=SR84)


def run(gbif_setup=None, **kw):
    m = Msgs()
    o = kw.pop("out", None) or out_fc()
    try:
        r = G.run(o, messages=m, **kw)
    except BaseException as e:                                        # noqa: BLE001  (Cancelled too)
        r = e
    return r, o, m


# ---- fetch directly
gbif = fresh()
gbif.records = [record(i, ds=DS1 if i % 3 else DS2) for i in range(650)] + [record(900, lat=None), record(901, lat=0.0, lon=0.0)]
r, o, m = run(taxa="Antilocapra americana", extent=BOX, year_from=2015, year_to=2024, max_uncertainty_m=100)
check("fetch: it returns the number written, and no download", r == {"written": 650, "download_key": None, "doi": None, "download_file": None}, r)
first = web.asked("/v1/occurrence/search/predicate")[0].json()
conds = dict((c.get("key") or c.get("type"), c) for c in first["predicate"]["predicates"])
check("fetch: the count carries the choices: species, rectangle, years, the three default kinds, presence, no flagged or centered places",
      first["limit"] == 0 and conds["TAXON_KEY"]["value"] == "2440902"
      and conds["within"]["geometry"] == "POLYGON((-112 35,-111 35,-111 36,-112 36,-112 35))"
      and conds["BASIS_OF_RECORD"]["values"] == G.kinds_to_codes(G.RECORD_KIND_DEFAULT) and "OCCURRENCE_STATUS" in conds
      and "HAS_GEOSPATIAL_ISSUE" in conds and "isNull" in conds and conds["COORDINATE_UNCERTAINTY_IN_METERS"]["value"] == "100",
      first["predicate"])
check("fetch: the messages give the count, what was written and what was left out",
      m.has("GBIF has 652 records that match.") and m.has("Wrote 650 points to " + o)
      and m.has("Left out 1 without usable coordinates.") and m.has("Left out 1 at latitude 0, longitude 0"), m.lines)
check("fetch: the sources are looked up by name and listed with their counts",
      m.has("The records come from 2 sources (name, GBIF dataset key, records):")
      and m.has("    %s   [%s]   433" % (TITLES[DS1], DS1)) and m.has("    %s   [%s]   217" % (TITLES[DS2], DS2)),
      [s for s in m.lines if "[" in s])
check("fetch: it says that no file is kept and that a direct fetch has no DOI",
      m.has("A direct fetch creates no zip file to keep") and m.has("GBIF issues no DOI for that"), m.lines)
md = arcpy.metadata.Metadata(o)
check("fetch: the output's metadata says how it was fetched, with the choices and the licenses",
      "Fetched directly from GBIF's occurrence search service (no DOI is issued for a direct fetch). Choices: species or "
      "groups: Antilocapra americana; study area: a polygon layer or rectangle; years: 2015 to 2024" in (md.summary or "")
      and "650 records written" in (md.summary or "") and "CC BY-NC (credit required, non-commercial use only): 650" in (md.summary or ""),
      md.summary)
check("fetch: no sign-in is sent with any request of a direct fetch", all("authorization" not in a.headers for a in web.requests))
gbif = fresh()
gbif.says_count = 100001
r, o, m = run(taxa="2440902")
check("fetch: more than 100,000 records is refused, with the method to use instead, and nothing is fetched",
      isinstance(r, G.GbifError) and 'There are 100,001 records, more than the 100,000 that can be fetched directly. Choose the '
      'method "Request a GBIF download" instead.' in str(r) and len(web.asked("/v1/occurrence/search/predicate")) == 1
      and not arcpy.Exists(o), repr(r))
gbif = fresh()
r, o, m = run(taxa="2440902")
check("fetch: nothing found: an empty feature class and a warning", isinstance(r, dict) and r["written"] == 0 and arcpy.Exists(o)
      and m.has("GBIF has no records that match.") and m.warned("No records matched"), (r, m.warnings))
gbif = fresh()
gbif.records = [record(1), record(2, lat=None)]
r, o, m = run(taxa="2440902", include_no_coordinates=True)
check("fetch, records without coordinates wanted: they are written as rows without a point, and the run says where to look",
      isinstance(r, dict) and r["written"] == 2 and m.has("Wrote 2 records to") and m.has("1 of them have no coordinates and "
                                                                                           "are rows without a point"), m.lines)

# ---- request a download
gbif = fresh()
gbif.records = [record(i) for i in range(4)]
gbif.statuses = ["PREPARING", "RUNNING", "RUNNING", "SUCCEEDED"]
gbif.zip_bytes = open(zpath, "rb").read()
r, o, m = run(method=G.METHOD_DOWNLOAD, taxa="Antilocapra americana", extent=BOX)
kept = os.path.join(work, G.DOWNLOADS_FOLDER, os.path.basename(o) + ".zip")
check("download: it returns what was written, the download's key, its DOI and where GBIF's file is kept",
      r == {"written": 2, "download_key": KEY, "doi": DOI, "download_file": kept}, r)
check("download: GBIF's own file is kept, whole, in Data_Downloads beside the geodatabase, named for the output",
      os.path.isfile(kept) and open(kept, "rb").read() == gbif.zip_bytes and not os.path.exists(kept + ".part"), kept)
check("download: asked of GBIF once, as the stored account, in the full (Darwin Core) form, with no e-mail notice",
      len(gbif.requested) == 1 and gbif.requested[0]["creator"] == USER and gbif.requested[0]["format"] == "DWCA"
      and gbif.requested[0]["sendNotification"] is False and gbif.requested[0]["predicate"]["type"] == "and", gbif.requested)
check("download: the run waits while GBIF prepares it, asking every 15 seconds", gbif.polls >= 4 and 44.0 <= sum(clock.slept) <= 76.0,
      (gbif.polls, sum(clock.slept)))
check("download: the messages name the account, the key, the citation and the file",
      m.has('Requesting the download on the GBIF account "maria".') and m.has("GBIF accepted the request: download " + KEY)
      and m.has("Cite this download as: GBIF.org (23 November 2026) GBIF Occurrence Download https://doi.org/" + DOI)
      and m.has("GBIF's original zip file is kept at " + kept), m.lines)
check("download: the password is in no message, no web address and no request's text",
      PASSWORD not in m.everything() and all(PASSWORD not in a.target and PASSWORD.encode() not in a.body for a in web.requests))
with_auth = [a for a in web.requests if "authorization" in a.headers]
check("download: the sign-in is sent with the request for the download and with nothing else",
      [(a.method, a.path) for a in with_auth] == [("POST", "/v1/occurrence/download/request")] and with_auth[0].headers["authorization"] == BASIC,
      [(a.method, a.path) for a in with_auth])
md = arcpy.metadata.Metadata(o)
check("download: the metadata carries the key, the DOI and the citation",
      ("GBIF download %s, DOI %s" % (KEY, DOI)) in (md.summary or "") and DOI in (md.credits or ""), (md.summary, md.credits))
rows_ = table(o)
check("download: the points are written with the sources' names and the photographs' addresses",
      [x["gbifID"] for x in rows_] == ["4011000001", "4011000002"] and rows_[1]["datasetName"] == TITLES[DS2]
      and rows_[0]["photoURL"] == "https://example.org/1a.jpg", [x["gbifID"] for x in rows_])

gbif = fresh()
gbif.records = [record(1)]
account[0] = None
r, o, m = run(method=G.METHOD_DOWNLOAD, taxa="2440902")
check("download, no sign-in stored: refused with where to sign in, and GBIF is asked for nothing",
      isinstance(r, G.GbifError) and "no sign-in is stored on this computer" in str(r) and gbif.requested == []
      and web.asked("/v1/occurrence/search/predicate") == [], repr(r))
gbif = fresh()
gbif.records = [record(1)]
gbif.account_ok = False
r, o, m = run(method=G.METHOD_DOWNLOAD, taxa="2440902")
check("download, GBIF does not accept the stored sign-in: said plainly, naming the account and never the password",
      isinstance(r, G.GbifError) and r.status == 401 and 'GBIF did not accept the stored sign-in for the account "maria"' in str(r)
      and PASSWORD not in str(r) and PASSWORD not in m.everything(), repr(r))
gbif = fresh()
r, o, m = run(method=G.METHOD_DOWNLOAD, taxa="2440902")
check("download, nothing matches: no download is asked of GBIF, and the feature class is empty",
      isinstance(r, dict) and r["written"] == 0 and gbif.requested == [] and m.warned("no download was requested from GBIF"), (r, m.warnings))
gbif = fresh()
gbif.records = [record(1)]
gbif.statuses = ["RUNNING"]
r, o, m = run(method=G.METHOD_DOWNLOAD, taxa="2440902", max_wait_minutes=2)
check("download, GBIF not finished within the longest wait: the run stops and says how to load it later",
      isinstance(r, G.GbifError) and ("GBIF had not finished download %s after" % KEY) in str(r) and "The request is still running "
      "at GBIF and stays on the account" in str(r) and G.METHOD_LOAD in str(r) and gbif.cancelled == [], repr(r))
for bad in ("FAILED", "KILLED", "CANCELLED"):
    gbif = fresh()
    gbif.records = [record(1)]
    gbif.statuses = ["RUNNING", bad]
    r, o, m = run(method=G.METHOD_DOWNLOAD, taxa="2440902")
    check("download, GBIF reports it %s: said in words" % bad.lower(),
          isinstance(r, G.GbifError) and ("GBIF did not finish download %s (status: %s)." % (KEY, G.status_words(bad))) in str(r), repr(r))

# cancelling during the wait: the request is cancelled at GBIF too
gbif = fresh()
gbif.records = [record(1)]
gbif.statuses = ["RUNNING"]


def cancel_now(seconds, cancelled):
    raise G.Cancelled()


with pointed(G, _sleep=cancel_now):
    r, o, m = run(method=G.METHOD_DOWNLOAD, taxa="2440902")
dels = [a for a in web.requests if a.method == "DELETE"]
check("cancelled while GBIF prepares the download: the request is cancelled at GBIF as well, with the sign-in, and the run says so",
      isinstance(r, G.Cancelled) and ("The request was cancelled at GBIF as well (download %s)." % KEY) in str(r)
      and gbif.cancelled == [KEY] and len(dels) == 1 and dels[0].headers.get("authorization") == BASIC, (repr(r), gbif.cancelled))
gbif = fresh()
web.forget()
web.answer("/v1/occurrence/download/request/", status=500, body="no", method="DELETE")
gbif = FakeGbif(web)
gbif.records = [record(1)]
gbif.statuses = ["RUNNING"]
with pointed(G, _sleep=cancel_now):
    r, o, m = run(method=G.METHOD_DOWNLOAD, taxa="2440902")
check("cancelled, and GBIF cannot be told: the run says the download is still being prepared there, and where to cancel it",
      isinstance(r, G.Cancelled) and "the request could not be cancelled at GBIF" in str(r) and KEY in str(r), repr(r))

# a study area too detailed for GBIF's download service
gbif = fresh()
gbif.records = [record(1)]
gbif.statuses = ["SUCCEEDED"]
star = arcpy.management.CreateFeatureclass(gdb, "star", "POLYGON", spatial_reference=SR84)[0]
n_pts = 12000
ring = [arcpy.Point(-111.5 + (0.4 if k % 2 else 0.2) * math.cos(2 * math.pi * k / n_pts),
                    35.5 + (0.4 if k % 2 else 0.2) * math.sin(2 * math.pi * k / n_pts)) for k in range(n_pts)]
with arcpy.da.InsertCursor(star, ["SHAPE@"]) as c:
    c.insertRow((arcpy.Polygon(arcpy.Array(ring + [ring[0]]), SR84),))
gbif.zip_bytes = open(make_zip(os.path.join(work, "star.zip"), [
    dwca_row(1, lat="35.5", lon="-111.5"),                       # at the middle of the star: inside it
    dwca_row(2, lat="35.5", lon="-111.15"),                      # between two points of the star: in its hull only
    dwca_row(3, lat="35.5", lon="-111.21")]), "rb").read()       # the same
r, o, m = run(method=G.METHOD_DOWNLOAD, study_area=star)
wkt = gbif.requested[0]["predicate"]["geometry"] if gbif.requested and "geometry" in gbif.requested[0]["predicate"] else \
    [c for c in (gbif.requested[0]["predicate"].get("predicates") or []) if c.get("type") == "within"][0]["geometry"] if gbif.requested else ""
check("a study area of 12,000 corners: GBIF is asked for a simpler shape of no more than 10,000, and the run explains",
      isinstance(r, dict) and 3 < wkt.count(",") + 1 <= 10000 and m.has("The study area polygon is very detailed: its outline has 12,001 vertices"),
      (repr(r)[:100], wkt.count(",") + 1, m.lines[:3]))
check("a study area of 12,000 corners: the records are then cut to the true study area",
      isinstance(r, dict) and r["written"] == 1 and m.has("Left out 2 outside the true study area.")
      and m.has("including the 2 outside the true study area that were left out of the feature class"), (repr(r)[:100], m.lines))

# ---- load an existing download
gbif = fresh()
gbif.zip_bytes = open(zpath, "rb").read()
r, o, m = run(method=G.METHOD_LOAD, existing_download=KEY)
check("existing download, by its key: fetched from GBIF, kept and loaded, with no sign-in",
      isinstance(r, dict) and r["written"] == 2 and r["download_key"] == KEY and r["doi"] == DOI and os.path.isfile(r["download_file"])
      and m.has("Loading GBIF download %s (DOI %s)." % (KEY, DOI)) and all("authorization" not in a.headers for a in web.requests),
      (repr(r), m.lines[:3]))
check("existing download: the run says that the other choices are not applied again",
      m.has("The other choices (species, dates, kinds of record and so on) were applied by GBIF when the download was made"))
gbif = fresh()
r, o, m = run(method=G.METHOD_LOAD, existing_download=' "%s" ' % zpath, extent=arcpy.Extent(-112, 35, -111, 36, spatial_reference=SR84))
check("existing download, by its file: loaded from the file where it is, and cut to the rectangle given",
      isinstance(r, dict) and r["download_file"] == zpath and r["download_key"] == KEY and r["written"] == 2
      and m.has("The records of the download are cut to the study area given here.") and m.has("GBIF's original zip file is at " + zpath)
      and web.asked("/files/") == [], (repr(r), m.lines))
gbif = fresh()
web.forget()                             # GBIF cannot be reached at all: nothing is prepared, so every request gets 404
nokey = make_zip(os.path.join(work, "nokey.zip"), FOUR, metadata=False)
r, o, m = run(method=G.METHOD_LOAD, existing_download=nokey)
check("existing download, a file that does not say which download it is, with GBIF out of reach: still loaded, with a warning",
      isinstance(r, dict) and r["written"] == 2 and r["download_key"] is None and r["doi"] is None
      and m.warned("The zip file does not say which GBIF download it is"), (repr(r), m.warnings))
web.forget()
gbif = fresh()
for st, words in (("FILE_ERASED", "GBIF has erased the file of download %s" % KEY), ("FAILED", "Download %s cannot be loaded (status: failed)." % KEY)):
    gbif = fresh()
    gbif.statuses = [st]
    r, o, m = run(method=G.METHOD_LOAD, existing_download=KEY)
    check("existing download that GBIF reports as %s: said in words" % G.status_words(st), isinstance(r, G.GbifError) and words in str(r), repr(r))
gbif = fresh()
gbif.format = "SPECIES_LIST"
r, o, m = run(method=G.METHOD_LOAD, existing_download=KEY)
check("existing download that is a list of species: refused, since it has no places", isinstance(r, G.GbifError)
      and "A species-list download has no coordinates" in str(r), repr(r))
gbif = fresh()
r, o, m = run(method=G.METHOD_LOAD, existing_download="0099999-260101123456789")
check("existing download, a key GBIF does not have", isinstance(r, G.GbifError) and "GBIF has no download with the key 0099999-260101123456789." in str(r), repr(r))
r, o, m = run(method=G.METHOD_LOAD, existing_download=os.path.join(work, "no_such.zip"))
check("existing download, neither a key nor a file", isinstance(r, G.GbifError) and "is neither a GBIF download key" in str(r), repr(r))

# a download that is cut off on its way here
gbif = fresh()
web.forget()
big = open(zpath, "rb").read()
web.fail("/files/", "cut", body=big)
gbif = FakeGbif(web)
gbif.zip_bytes = big
r, o, m = run(method=G.METHOD_LOAD, existing_download=KEY)
kept = os.path.join(work, G.DOWNLOADS_FOLDER, os.path.basename(o) + ".zip")
check("a download cut off on its way: the run fails, and does not load half a file as if it were whole",
      isinstance(r, Exception) and not arcpy.Exists(o), repr(r))
check("a download cut off on its way: the message says that the file was cut off, how much came, and what to do",
      isinstance(r, G.GbifError) and "The file of download %s was cut off on its way from GBIF, twice (" % KEY in str(r)
      and "so it was not kept" in str(r) and 'run the tool again with the method "%s" and that key' % G.METHOD_LOAD in str(r), "it says: %s" % r)
check("a download cut off on its way: it is tried a second time, and not a third", len(web.asked("/files/")) == 2, len(web.asked("/files/")))
folder_ = os.path.dirname(kept)
check("a download cut off on its way: no broken file, and no working file, is left in Data_Downloads",
      not os.path.exists(kept) and not [n for n in (os.listdir(folder_) if os.path.isdir(folder_) else []) if n.endswith(".part")],
      "left: %s" % (os.listdir(folder_) if os.path.isdir(folder_) else None))
gbif = fresh()
web.forget()
web.fail("/files/", "cut", body=big, times=1)
gbif = FakeGbif(web)
gbif.zip_bytes = big
r, o, m = run(method=G.METHOD_LOAD, existing_download=KEY)
kept = os.path.join(work, G.DOWNLOADS_FOLDER, os.path.basename(o) + ".zip")
check("a download cut off once and whole the second time: the run goes on, and the file kept is the whole one",
      isinstance(r, dict) and arcpy.Exists(o) and len(web.asked("/files/")) == 2 and os.path.isfile(kept) and open(kept, "rb").read() == big,
      repr(r))
gbif = fresh()
web.forget()
web.fail("/files/", "cut stream", body=big)
gbif = FakeGbif(web)
gbif.zip_bytes = big
r, o, m = run(method=G.METHOD_LOAD, existing_download=KEY)
kept = os.path.join(work, G.DOWNLOADS_FOLDER, os.path.basename(o) + ".zip")
check("a download sent in pieces with no length promised, and cut off: refused in the same words, and nothing kept",
      isinstance(r, G.GbifError) and "was cut off on its way from GBIF, twice" in str(r) and not arcpy.Exists(o) and not os.path.exists(kept),
      repr(r))

# ---- what is refused before GBIF is asked anything
gbif = fresh()
for name, kw, words in (
        ("a method that is not one of the three", dict(method="Guess", taxa="2440902"), "The method must be one of: Fetch directly; Request a GBIF download; Load an existing GBIF download."),
        ("loading, with no download named", dict(method=G.METHOD_LOAD), "Give the existing download: its GBIF key"),
        ("a download named, with another method", dict(taxa="2440902", existing_download=KEY), "An existing download is named, so choose the method"),
        ("a study area and a rectangle together", dict(study_area=star, extent=BOX), "Give a study area layer or a rectangle, not both."),
        ("nothing chosen at all", dict(), "Choose at least one species or group, a study area, or a country."),
        ("years the wrong way round", dict(taxa="2440902", year_from=2024, year_to=2020), "The first year is later than the last year."),
        ("an uncertainty of zero", dict(taxa="2440902", max_uncertainty_m=0), "The coordinate uncertainty must be a number of meters greater than zero")):
    r, o, m = run(**kw)
    check("refused: %s" % name, isinstance(r, G.GbifError) and words in str(r) and not arcpy.Exists(o), repr(r))
check("refused: none of those asked GBIF for records or for a download",
      [a for a in web.requests if "search/predicate" in a.path or "download" in a.path] == [])
r, o, m = run(taxa="2440902", out=os.path.join(work, "points.shp"))
check("refused: an output outside a geodatabase, with an example of the right form",
      isinstance(r, G.GbifError) and "GBIF_occurrences (the field names are too long for a shapefile)" in str(r), repr(r))

check("at the end: nothing tried to reach another computer", guard.attempts == [], guard.attempts)
point.__exit__(None, None, None)
check("at the end: the engine's own addresses are put back", G.V1 == "https://api.gbif.org/v1/" and G.V2 == "https://api.gbif.org/v2/")
for k, v in env_before.items():
    if v is not None:
        os.environ[k] = v
web.close()
guard.stop()

print("\n%d passed, %d failed%s" % (passed, failed, " (and %d known faults not yet fixed)" % known if known else ""))
arcpy.management.ClearWorkspaceCache()
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
