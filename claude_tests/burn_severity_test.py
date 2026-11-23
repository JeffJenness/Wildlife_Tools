# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award notice, marking of modified versions, use of names,
# and an additional permission to link with ArcGIS Pro).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""MTBS, RAVG and BAER Burn Severity (burn_severity.py): the fire catalog,
the classified rasters of the image services, and the request for data
bundles. Nothing here touches the Burn Severity Portal, the U.S. Geological
Survey's or the Forest Service's servers, or the internet: a stand-in
service on this computer answers in their forms, and a guard stops any
attempt to reach another computer. The stand-in's rasters are made here,
with a class for every cell that depends only on where the cell is, and the
fires' perimeters run through the middles of cells, so which cells belong to
a fire, and the true area of each class, are worked out here beforehand.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" burn_severity_test.py
"""
import datetime
import json
import math
import os
import re
import shutil
import sys
import tempfile
import urllib.parse

import numpy as np
from osgeo import gdal, osr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
sys.path.insert(0, HERE)
import arcpy  # noqa: E402
import burn_severity as B  # noqa: E402
from stand_in_service import StandIn, no_internet, pointed  # noqa: E402

gdal.UseExceptions()
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
    except BaseException as e:                                        # noqa: BLE001
        return e


def refused(name, fn, has):
    e = error_of(fn)
    check(name, isinstance(e, B.FireError) and has in str(e), "%s: %s" % (type(e).__name__, e))


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


class Clock:
    def __init__(self):
        self.now, self.slept = 7000.0, []

    def time(self):
        self.now += 0.001
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(float(seconds), 3))
        self.now += float(seconds)


EMAIL = "your_address@your_location.com"
R = 6378137.0
CELL = 30.0


def unmerc(x, y):
    return math.degrees(x / R), math.degrees(2.0 * math.atan(math.exp(y / R)) - math.pi / 2.0)


def class_at(col, row):
    """The class of the stand-in's cell (col, row): 1 to 4, in stripes four cells wide. It depends only on
    where the cell is, never on what was asked for."""
    return 1 + (col // 4) % 4


# a fire whose perimeter is a rectangle through the middles of cells: 31 columns by 21 rows of cells touch it
CX, CY = int(math.radians(-111.0) * R // CELL), int(math.log(math.tan(math.pi / 4 + math.radians(33.5) / 2)) * R // CELL)
COLS = range(CX + 10, CX + 41)
ROWS = range(CY + 5, CY + 26)


def ring_of(c0, c1, r0, r1):
    """A rectangle from the middle of cell column c0 to the middle of column c1 (and rows likewise), as
    longitude and latitude."""
    corners = [((c0 + 0.5) * CELL, (r0 + 0.5) * CELL), ((c0 + 0.5) * CELL, (r1 + 0.5) * CELL),
               ((c1 + 0.5) * CELL, (r1 + 0.5) * CELL), ((c1 + 0.5) * CELL, (r0 + 0.5) * CELL)]
    pts = [list(unmerc(x, y)) for x, y in corners]
    return pts + [pts[0]]


E2 = 0.00669437999014             # WGS 84: the square of the ellipsoid's first eccentricity


def true_areas(cols, rows, known=(1, 2, 3, 4, 5, 6), sphere=False):
    """{class: square meters on the ground} for a block of cells. On a sphere a Web Mercator cell at
    latitude phi would cover its map area times the square of the cosine of phi. On the WGS 84 ellipsoid,
    which is the ground, it covers that times (1 - e2) / (1 - e2 sin2 phi) squared: east to west a degree
    is N cos phi and north to south it is M, where N and M are the ellipsoid's two radii of curvature."""
    out = {}
    for r in rows:
        lat = 2.0 * math.atan(math.exp((r + 0.5) * CELL / R)) - math.pi / 2.0
        on_ground = CELL * CELL * math.cos(lat) ** 2
        if not sphere:
            on_ground *= (1.0 - E2) / (1.0 - E2 * math.sin(lat) ** 2) ** 2
        for c in cols:
            v = class_at(c, r)
            if v in known:
                out[v] = out.get(v, 0.0) + on_ground
    return out


def fire(n, name="WOODBURY", event="AZ3525811164320190721", year=2019, cols=(CX + 10, CX + 40), rows=(CY + 5, CY + 25), **over):
    p = {"id": n, "event_id": event, "incid_name": name, "incid_type": "Wildfire", "map_id": 10016000 + n, "map_prog": "MTBS",
         "asmt_type": "Extended", "burnbndac": 1000.5, "burnbndlat": "33.5", "burnbndlon": "-111.0",
         "ig_date": "%d-06-08T00:00:00Z" % year, "postfire_date": "%d-07-07" % (year + 1), "dnbr_offst": -12, "dnbr_stddv": 9,
         "incgreen_t": -9999, "low_t": 70, "mod_t": 520, "high_t": 280, "comment": None, "irwinid": "ABC-123",
         "nonstandard": False}
    p.update(over)
    return {"type": "Feature", "id": "fire_polygons.%d" % n, "properties": p,
            "geometry": {"type": "Polygon", "coordinates": [ring_of(cols[0], cols[1], rows[0], rows[1])]}}


N_TIF = [0]


def tiff(x0, y0, x1, y1, empty=False, only_cols=None):
    """A GeoTIFF of the stand-in's classes for a rectangle of Web Mercator, as bytes."""
    w, h = int(round((x1 - x0) / CELL)), int(round((y1 - y0) / CELL))
    N_TIF[0] += 1
    path = "/vsimem/stand_in_%d.tif" % N_TIF[0]
    ds = gdal.GetDriverByName("GTiff").Create(path, w, h, 1, gdal.GDT_Byte)
    ds.SetGeoTransform((x0, CELL, 0.0, y1, 0.0, -CELL))
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(3857)
    ds.SetProjection(sr.ExportToWkt())
    c0, r_top = int(round(x0 / CELL)), int(round(y1 / CELL)) - 1
    a = np.zeros((h, w), np.uint8)
    if not empty:
        for j in range(w):
            if only_cols is None or (c0 + j) in only_cols:
                a[:, j] = class_at(c0 + j, 0)
    ds.GetRasterBand(1).WriteArray(a)
    ds = None
    f = gdal.VSIFOpenL(path, "rb")
    gdal.VSIFSeekL(f, 0, 2)
    size = gdal.VSIFTellL(f)
    gdal.VSIFSeekL(f, 0, 0)
    data = bytes(gdal.VSIFReadL(1, size, f))
    gdal.VSIFCloseL(f)
    gdal.Unlink(path)
    return data


class FakePortal:
    """The catalog (a WFS), the U.S. Geological Survey's map server (a WCS), the Forest Service's image services
    and the portal's bundle queue."""

    def __init__(self, web):
        self.web = web
        self.fires = []
        self.wcs = "ok"                  # ok | none | error | page
        self.image = "ok"                # ok | empty | error | refusal | partial
        self.queue = {"success": True, "message": "Your request has been successfully submitted."}
        self.queued = []
        web.answer("/geoserver/wfs", handler=self.wfs)
        web.answer("/geoserver/wcs", handler=self.coverage)
        web.answer("/images/", handler=self.export)
        web.answer("/queue", handler=self.enqueue, method="POST")

    def match(self, q):
        cql = q.one("cql_filter") or ""
        out = list(self.fires)
        m = re.search(r"event_id IN \(([^)]*)\)", cql)
        if m:
            ids = set(x.strip().strip("'") for x in m.group(1).split(","))
            out = [f for f in out if f["properties"]["event_id"] in ids]
        return out

    def wfs(self, q):
        hits = self.match(q)
        if q.one("resultType") == "hits":
            return 200, {"Content-Type": "text/xml"}, '<wfs:FeatureCollection numberMatched="%d" numberReturned="0"/>' % len(hits)
        start, count = int(q.one("startIndex") or 0), int(q.one("count") or 500)
        return {"type": "FeatureCollection", "features": hits[start:start + count]}

    def coverage(self, q):
        if self.wcs == "none":
            return 404, {"Content-Type": "application/xml"}, '<ows:ExceptionReport><ows:Exception exceptionCode="NoSuchCoverage"/></ows:ExceptionReport>'
        if self.wcs == "error":
            return 500, {}, "Internal error"
        if self.wcs == "page":
            return 200, {"Content-Type": "image/tiff"}, "<html><body>Service is restarting</body></html>"
        xs = re.search(r"X\(([-\d.]+),([-\d.]+)\)", q.query_text.replace("%28", "(").replace("%29", ")").replace("%2C", ","))
        ys = re.search(r"Y\(([-\d.]+),([-\d.]+)\)", q.query_text.replace("%28", "(").replace("%29", ")").replace("%2C", ","))
        return 200, {"Content-Type": "image/tiff"}, tiff(float(xs.group(1)), float(ys.group(1)), float(xs.group(2)), float(ys.group(2)))

    def export(self, q):
        if self.image == "error":
            return 500, {}, "Internal error"
        if self.image == "refusal":      # the image services answer some failures with 200 and a message
            return 200, {"Content-Type": "image/tiff"}, json.dumps({"error": {"code": 400, "message": "Invalid or missing input parameters."}})
        x0, y0, x1, y1 = [float(v) for v in q.one("bbox").split(",")]
        return 200, {"Content-Type": "image/tiff"}, tiff(x0, y0, x1, y1, empty=self.image == "empty",
                                                         only_cols=range(CX + 10, CX + 14) if self.image == "partial" else None)

    def enqueue(self, q):
        self.queued.append(q)
        return self.queue


work = tempfile.mkdtemp(prefix="burn_test_")
arcpy.env.overwriteOutput = True
gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
web = StandIn()
clock = Clock()


def fresh():
    web.forget()
    del clock.slept[:]
    B._last[0] = 0.0
    return FakePortal(web)


point = pointed(B, GEOSERVER=web.url + "geoserver/", IMAGERY=web.url + "images/", QUEUE=web.url + "queue", time=clock)
point.__enter__()

# =========================================================================================================
#  1. small pieces
# =========================================================================================================
check("the catalog's filter: the program alone", B.catalog_filter("MTBS") == "map_prog='MTBS'")
check("the catalog's filter: names (either of two), years, a rectangle and fire IDs, with an apostrophe made safe",
      B.catalog_filter("BAER", name="wood; O'Neil", year_from=2019, year_to=2020, box=(-112.0, 33.0, -111.0, 34.5),
                       fire_ids=["AZ1", "AZ2"])
      == "map_prog='BAER' AND event_id IN ('AZ1', 'AZ2') AND (incid_name ILIKE '%wood%' OR incid_name ILIKE '%O''Neil%') "
         "AND ig_date >= '2019-01-01' AND ig_date <= '2020-12-31' AND BBOX(geom,-112.000000,33.000000,-111.000000,34.500000,'EPSG:4326')",
      B.catalog_filter("BAER", name="wood; O'Neil", year_from=2019, year_to=2020, box=(-112.0, 33.0, -111.0, 34.5), fire_ids=["AZ1", "AZ2"]))
check("fire IDs: a fire, or one assessment of a fire (ID:map ID); a fire named both ways has all its assessments",
      B.split_fire_ids("az1; AZ2:100; AZ2:200\nAZ3; AZ3:5") == (["AZ1", "AZ2", "AZ3"], {"AZ2": {100, 200}}), B.split_fire_ids("az1; AZ2:100; AZ2:200\nAZ3; AZ3:5"))
refused("fire IDs: a map ID that is not a number", lambda: B.split_fire_ids("AZ1:abc"), 'The fire ID entry "AZ1:abc" could not be read.')
check("a long list of fire IDs is asked for 100 at a time",
      [len(b["fire_ids"]) for b in B._id_batches({"fire_ids": ["F%d" % i for i in range(250)]})] == [100, 100, 50]
      and B._id_batches({"name": "x"}) == [{"name": "x", "fire_ids": None}])
f0 = fire(1)
check("a fire's year, rectangle and region", B.year_of(f0) == 2019 and B.region_of(f0) == "CONUS"
      and B.region_of(fire(2, burnbndlat="64.0", burnbndlon="-150.0")) == "AK" and B.region_of(fire(3, burnbndlat="20.0", burnbndlon="-156.0")) == "HI"
      and B.region_of(fire(4, burnbndlat="18.2", burnbndlon="-66.5")) == "PR" and abs(B.box_of(f0)[0] - unmerc((CX + 10.5) * CELL, 0)[0]) < 1e-9)
lon0, lat0 = unmerc((CX + 10.5) * CELL, (CY + 5.5) * CELL)
check("longitude and latitude to Web Mercator and back", abs(B.merc(lon0, lat0)[0] - (CX + 10.5) * CELL) < 1e-6
      and abs(B.merc(lon0, lat0)[1] - (CY + 5.5) * CELL) < 1e-6)
# the fire is 30 cells by 20 cells of 30 m on the map; on the ground each is smaller by the cosine of the latitude
lat_mid = math.radians(unmerc(0, (CY + 15.5) * CELL)[1])
# (the engine measures a perimeter on a sphere, "good to a fraction of a percent": it is used only to tell whether
# a raster covers its fire)
check("a perimeter's area: its map area times the square of the cosine of its latitude (to 0.5 percent)",
      abs(B.area_of(f0) - 30 * 20 * 900.0 * math.cos(lat_mid) ** 2) < 0.005 * B.area_of(f0), (B.area_of(f0), 30 * 20 * 900.0 * math.cos(lat_mid) ** 2))
check("a request's rectangle is grown to whole cells, with one cell to spare", B._snap((100.0, 200.0, 310.0, 400.0)) == (60.0, 150.0, 360.0, 450.0))
check("what is a TIFF and what is not", B._is_tiff(tiff(0, 0, 60, 60)) and not B._is_tiff(b"<html>") and not B._is_tiff(b'{"error":1}'))
check("what a service said in place of a raster, in a line",
      B._refusal(b'{"error": {"code": 400, "message": "Invalid size"}}') == "Invalid size (code 400)"
      and B._refusal(b"<html><body><h1>Bad  Gateway</h1></body></html>") == "Bad Gateway" and B._refusal(b"") == "nothing")
check("names made safe for a dataset", (B.safe_name("MTBS_O'Neil / East_2019_severity"), B.safe_name("2019 fire"), B.safe_name("x" * 80, 20))
      == ("MTBS_O_Neil_East_2019_severity", "fire_2019_fire", "x" * 20))
check("whole paths joined, as many as fit; never a cut path",
      B.join_paths(["C:\\a\\one", "C:\\a\\two", "C:\\a\\three"], 22) == "C:\\a\\one; C:\\a\\two" and B.join_paths(["x" * 30], 20) == "")
check("an email address is told from what is not one", B.valid_email(EMAIL) and not B.valid_email("maria at home") and not B.valid_email(""))
check("each program's class raster products",
      list(B.PROGRAMS["MTBS"]["products"]) == [B.PRODUCT_MTBS] and list(B.PROGRAMS["RAVG"]["products"]) == [B.PRODUCT_BA7, B.PRODUCT_CC5, B.PRODUCT_CBI4]
      and list(B.PROGRAMS["BAER"]["products"]) == [B.PRODUCT_SBS])

# =========================================================================================================
#  2. talking to the services
# =========================================================================================================
portal = fresh()
web.answer("/geoserver/ping", body="pong")
with pointed(B, PAUSE=0.5):
    for _ in range(3):
        B._read(web.url + "geoserver/ping")
a = web.asked("/geoserver/ping")
check("requests say who is asking, and are half a second apart", len(a) == 3 and all(x.headers.get("user-agent") == B.USER_AGENT for x in a)
      and len(clock.slept) == 2 and all(0.45 <= s <= 0.5 for s in clock.slept) and "@" not in B.USER_AGENT, clock.slept)
with pointed(B, PAUSE=0.0):
    web.forget()
    del clock.slept[:]
    web.answer("/geoserver/busy", status=503, body="busy", times=2)
    web.answer("/geoserver/busy", body="ok")
    check("busy twice, then an answer: asked three times, after waits of 2 and 5 seconds",
          B._read(web.url + "geoserver/busy")[0] == b"ok" and len(web.asked("/geoserver/busy")) == 3 and clock.slept == [2.0, 5.0], clock.slept)
    for status, again in ((429, 3), (408, 3), (400, 1), (404, 1), (403, 1)):
        web.forget()
        web.answer("/geoserver/no", status=status, body="the reason given")
        e = error_of(lambda: B._read(web.url + "geoserver/no"))
        check("status %d: %s, and the service's own words are kept" % (status, "asked again" if again > 1 else "reported at once"),
              isinstance(e, B.FireError) and e.status == status and "the reason given" in e.detail and len(web.asked("/geoserver/no")) == again,
              (repr(e), len(web.asked("/geoserver/no"))))
    web.forget()
    web.fail("/geoserver/cut", "cut", body=b"x" * 5000)
    e = error_of(lambda: B._read(web.url + "geoserver/cut"))
    check("an answer cut off part way is never taken as whole: it is asked for again, then reported as cut off",
          isinstance(e, B.FireError) and "stopped answering part way" in str(e) and len(web.asked("/geoserver/cut")) == 3, repr(e))
    web.forget()
    web.fail("/geoserver/gone", "refuse")
    e = error_of(lambda: B._read(web.url + "geoserver/gone"))
    check("a connection that is dropped: reported in words, not as a crash", isinstance(e, B.FireError)
          and ("could not be reached" in str(e) or "stopped answering" in str(e)), repr(e))

# =========================================================================================================
#  3. the catalog
# =========================================================================================================
B.PAUSE = 0.0
portal = fresh()
portal.fires = [fire(i, name="FIRE %d" % i, event="AZ%017d" % i) for i in range(1200)]
check("counting: the catalog is asked for the number alone", B.count("MTBS", name="fire") == 1200
      and web.asked("/geoserver/wfs")[0].one("resultType") == "hits" and web.asked("/geoserver/wfs")[0].one("typeNames") == B.CATALOG)
web.forget()
portal = FakePortal(web)
portal.fires = [fire(i, name="FIRE %d" % i, event="AZ%017d" % i) for i in range(1200)]
found = B.search("MTBS", name="fire", year_from=2019)
reqs = web.asked("/geoserver/wfs")
check("searching: 1,200 fires come in three requests of 500, in order, none lost or repeated",
      len(found) == 1200 and [(r.one("count"), r.one("startIndex")) for r in reqs] == [("500", "0"), ("500", "500"), ("500", "1000")]
      and [f["properties"]["id"] for f in found] == list(range(1200)), [(r.one("count"), r.one("startIndex")) for r in reqs])
check("searching: longitude and latitude, oldest first, with the filter",
      reqs[0].one("srsName") == "EPSG:4326" and reqs[0].one("sortBy") == "ig_date,id" and reqs[0].one("outputFormat") == "application/json"
      and reqs[0].one("cql_filter") == "map_prog='MTBS' AND (incid_name ILIKE '%fire%') AND ig_date >= '2019-01-01'", reqs[0].query)
web.forget()
portal = FakePortal(web)
portal.fires = [fire(i, name="FIRE %d" % i, event="AZ%017d" % i, year=2021 - (i % 3)) for i in range(250)]
found = B.search("MTBS", fire_ids=["AZ%017d" % i for i in range(250)])
check("searching by 250 fire IDs: three requests, and the fires are still oldest first",
      len(found) == 250 and len(web.asked("/geoserver/wfs")) == 3 and [B.year_of(f) for f in found] == sorted(B.year_of(f) for f in found),
      (len(found), len(web.asked("/geoserver/wfs"))))
check("searching: at most the number asked for", len(B.search("MTBS", limit=7, name="fire")) == 7)
e = error_of(lambda: B.search("MTBS", cancelled=lambda: True, name="x"))
check("searching: cancelled before the first request", isinstance(e, B.Cancelled), repr(e))
web.forget()
web.answer("/geoserver/wfs", body="<html>maintenance</html>")
refused("a catalog that answers with a web page", lambda: B.search("MTBS", name="x"), "The fire catalog did not answer as expected.")
refused("and a count that is not a count", lambda: B.count("MTBS", name="x"), "The fire catalog did not answer as expected.")

# =========================================================================================================
#  4. a class raster for one fire
# =========================================================================================================
portal = fresh()
raw = os.path.join(work, "raw.tif")
ok = B.fetch_classes(f0, ("wcs", None), raw)
q = web.asked("/geoserver/wcs")[0]
text = urllib.parse.unquote(q.query_text)
check("MTBS, first source: the Survey's map server is asked for the fire's region and year, in whole cells around the fire",
      ok is True and "coverageId=mtbs__mtbs_CONUS_2019" in text
      and "subset=X(%.1f,%.1f)" % ((CX + 9) * CELL, (CX + 42) * CELL) in text and "subset=Y(%.1f,%.1f)" % ((CY + 4) * CELL, (CY + 27) * CELL) in text, text)
cut = os.path.join(work, "cut.tif")
areas = B.cut_to_fire(raw, f0, cut, B.MTBS_CLASSES)
want = true_areas(COLS, ROWS)
check("cut to the fire: the classes found are those of the cells the perimeter touches", sorted(areas) == sorted(want), (sorted(areas), sorted(want)))
check("cut to the fire: each class's TRUE area on the ground, row by row (to a millionth)",
      all(abs(areas[v] - want[v]) < 1e-6 * want[v] for v in want), dict((v, (areas[v], want[v])) for v in want))
# A second measure, which shares nothing with the engine's arithmetic: the block of 31 by 21 cells as a polygon,
# measured on the ellipsoid by ArcGIS. (Until 2026-11-23 the engine's areas were on a sphere, cell size times the
# cosine of the latitude, squared, which is 0.26 percent more than the ground at 33.5 N. This check found it.)
edges = [unmerc((CX + 10) * CELL, (CY + 5) * CELL), unmerc((CX + 10) * CELL, (CY + 26) * CELL),
         unmerc((CX + 41) * CELL, (CY + 26) * CELL), unmerc((CX + 41) * CELL, (CY + 5) * CELL)]
on_ellipsoid = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in edges + edges[:1]]),
                             arcpy.SpatialReference(4326)).getArea("GEODESIC", "SQUAREMETERS")
check("cut to the fire: the areas called TRUE are the ground's own (within 0.01 percent of ArcGIS's measure on the ellipsoid)",
      abs(sum(areas.values()) - on_ellipsoid) < 1e-4 * on_ellipsoid,
      "they differ by %.3f percent (%.1f against %.1f sq m)" % (
          100.0 * (sum(areas.values()) / on_ellipsoid - 1.0), sum(areas.values()), on_ellipsoid))
on_sphere = sum(true_areas(COLS, ROWS, sphere=True).values())
check("for the record: on a sphere the same cells would measure 0.26 percent more, 26 times what the check above allows",
      0.0025 < on_sphere / on_ellipsoid - 1.0 < 0.0027, on_sphere / on_ellipsoid - 1.0)
check("cut to the fire: that is about 70 percent of the area counted in map cells, at this latitude",
      0.69 < sum(areas.values()) / (31 * 21 * 900.0) < 0.70, sum(areas.values()) / (31 * 21 * 900.0))
ds = gdal.Open(cut)
arr = ds.GetRasterBand(1).ReadAsArray()
gt = ds.GetGeoTransform()
nodata, has_colors = ds.GetRasterBand(1).GetNoDataValue(), ds.GetRasterBand(1).GetColorTable() is not None
ds = None
c0, r_top = int(round(gt[0] / CELL)), int(round(gt[3] / CELL)) - 1
inside = np.array([[(c0 + j) in COLS and (r_top - i) in ROWS for j in range(arr.shape[1])] for i in range(arr.shape[0])])
expect = np.array([[class_at(c0 + j, r_top - i) for j in range(arr.shape[1])] for i in range(arr.shape[0])])
check("cut to the fire: every cell the perimeter touches keeps its class, every other cell is 0, which is 'no data'",
      np.array_equal(arr[inside], expect[inside]) and not arr[~inside].any() and nodata == 0 and int(inside.sum()) == 31 * 21,
      (int((arr[inside] != expect[inside]).sum()), int(arr[~inside].astype(bool).sum())))
check("cut to the fire: no color table is written (it would give ArcGIS Pro 250 black boxes without names)", not has_colors)
areas5 = B.cut_to_fire(raw, f0, os.path.join(work, "cut5.tif"), [c for c in B.MTBS_CLASSES if c["value"] in (1, 2)])
check("cut to the fire: values that are not classes of the product are left out", sorted(areas5) == [1, 2], sorted(areas5))
portal.wcs = "none"
check("MTBS, first source has no raster for the year: that is 'none here', not a failure", B.fetch_classes(f0, ("wcs", None), raw) is False)
ok = B.fetch_classes(f0, ("wcs", None), raw, attempt=1)
q = web.asked("/images/")[0]
rule = json.loads(q.one("mosaicRule"))
check("MTBS, second source: the Forest Service's image service of the same region, for that year, cell for cell",
      ok is True and q.path == "/images/USFS_EDW_MTBS_CONUS/ImageServer/exportImage" and rule["where"] == "year = 2019"
      and q.one("bbox") == "%.1f,%.1f,%.1f,%.1f" % ((CX + 9) * CELL, (CY + 4) * CELL, (CX + 42) * CELL, (CY + 27) * CELL)
      and q.one("size") == "33,23" and (q.one("bboxSR"), q.one("imageSR"), q.one("format"), q.one("interpolation"), q.one("noData"))
      == ("3857", "3857", "tiff", "RSP_NearestNeighbor", "0"), q.query)
check("there is no third source", B.fetch_classes(f0, ("wcs", None), raw, attempt=2) is None
      and B.fetch_classes(f0, ("image", "X"), raw, attempt=1) is None)
ok = B.fetch_classes(fire(9, map_prog="RAVG"), ("image", "USFS_EDW_RAVG_BasalAreaPercentChange"), raw)
q = web.asked("/images/")[-1]
check("RAVG: the image service's mosaic whose years hold the fire's year",
      ok is True and q.path == "/images/USFS_EDW_RAVG_BasalAreaPercentChange/ImageServer/exportImage"
      and json.loads(q.one("mosaicRule"))["where"] == "beginyear <= 2019 AND endyear >= 2019", q.one("mosaicRule"))
portal.image = "refusal"
refused("an image service that answers 'OK' with a message in place of a raster: a failure, with what it said",
        lambda: B.fetch_classes(f0, ("image", "USFS_EDW_BAER_SoilBurnSeverityClassification"), raw),
        "sent no raster for a request of 33 by 23 cells. It answered: Invalid or missing input parameters. (code 400)")
portal.wcs = "page"
refused("the map server answering with a web page: a failure, with what it said", lambda: B.fetch_classes(f0, ("wcs", None), raw),
        "The U.S. Geological Survey's map server sent no raster. It answered: Service is restarting")
portal.image = "empty"
B.fetch_classes(f0, ("image", "X"), raw)
check("a raster with no class cells for the fire is 'nothing here'", B.cut_to_fire(raw, f0, os.path.join(work, "none.tif"), B.MTBS_CLASSES) is None)

# =========================================================================================================
#  5. whole runs
# =========================================================================================================
N_RUN = [0]


def run(**kw):
    m = Msgs()
    N_RUN[0] += 1
    o = kw.pop("out", None) or os.path.join(gdb, "fires_%02d" % N_RUN[0])
    try:
        r = B.run(o, messages=m, **kw)
    except BaseException as e:                                        # noqa: BLE001
        r = e
    return r, o, m


FLD = [f[0] for f in B.FIELDS]


def table(fc):
    return [dict(zip(["shape"] + FLD, row)) for row in arcpy.da.SearchCursor(fc, ["SHAPE@"] + FLD)]


ras_gdb = arcpy.management.CreateFileGDB(work, "rasters.gdb")[0]
portal = fresh()
other = fire(2, name="O'NEIL EAST", event="AZ3300011100020200601", year=2020, cols=(CX + 100, CX + 120), rows=(CY + 5, CY + 15),
             nonstandard="True", comment="Mapped from a late image", incgreen_t=-150, burnbndac=500.25)
portal.fires = [f0, other]
r, o, m = run(program="mtbs", fire_name="wood; neil", year_from=2019, year_to=2020, products=[B.PRODUCT_MTBS],
              raster_workspace=ras_gdb, bundle_email=EMAIL, bundle_images=True)
ras1, ras2 = os.path.join(ras_gdb, "MTBS_WOODBURY_2019_severity"), os.path.join(ras_gdb, "MTBS_O_NEIL_EAST_2020_severity")
check("run: it returns what was found and written, the rasters, and the portal's answer about the bundles",
      isinstance(r, dict) and (r["found"], r["written"], r["rasters"]) == (2, 2, [ras1, ras2]) and "successfully" in r["bundle_message"], r)
rows_ = dict((x["fire_name"], x) for x in table(o))
w = rows_.get("WOODBURY", {})
check("perimeters: what the catalog says of the fire",
      [w.get(k) for k in ("program", "fire_id", "fire_year", "ignition_date", "fire_type", "assessment", "acres", "map_id", "irwin_id")]
      == ["MTBS", "AZ3525811164320190721", 2019, datetime.datetime(2019, 6, 8), "Wildfire", "Extended", 1000.5, 10016001, "ABC-123"], w)
check("perimeters: the thresholds, with the catalog's two crossed columns put right, and its 'no value' (-9999) left empty",
      [w.get(k) for k in ("dnbr_offset", "dnbr_stddev", "greenness_t", "low_t", "moderate_t", "high_t")] == [-12, 9, None, 70, 280, 520]
      and rows_["O'NEIL EAST"]["greenness_t"] == -150, [w.get(k) for k in ("greenness_t", "low_t", "moderate_t", "high_t")])
check("perimeters: where to find the fire on the portal, and the raster fetched for it",
      w.get("portal_url") == B.PORTAL + "viewer/?product=MTBS" and w.get("raster") == ras1 and rows_["O'NEIL EAST"]["comment"] == "Mapped from a late image"
      and w.get("comment") is None, (w.get("portal_url"), w.get("raster")))
check("perimeters: the outline, in WGS 84, with the fire's true area (to 0.5 percent)",
      arcpy.Describe(o).spatialReference.factoryCode == 4326
      and abs(w["shape"].getArea("GEODESIC", "SQUAREMETERS") - B.area_of(f0)) < 0.005 * B.area_of(f0),
      w and (w["shape"].getArea("GEODESIC", "SQUAREMETERS"), B.area_of(f0)))
check("run: the messages say what was found and written", m.has('2 MTBS fires match (name contains "wood; neil"; 2019 to 2020).')
      and m.has("Wrote 2 perimeters to %s: 1,501 acres in all, as MTBS reports them." % o)
      and m.has("Fetched 2 classified rasters into " + ras_gdb), m.lines)
t = dict((int(x[0]), x[1:]) for x in arcpy.da.SearchCursor(ras1, ["Value", "Count", "Class_Name", "Hectares", "Acres", "Percent"]))
names = dict((c["value"], c["name"]) for c in B.MTBS_CLASSES)
total = sum(want.values())
check("raster: its table names each class and gives its TRUE area, not its cells times 900",
      sorted(t) == sorted(want) and all(t[v][1] == names[v] and abs(t[v][2] - want[v] / 10000.0) < 1e-6 * want[v] / 10000.0
                                        and abs(t[v][3] - want[v] / 4046.8564224) < 1e-6 * want[v] and abs(t[v][4] - 100.0 * want[v] / total) < 1e-6
                                        for v in want), t)
d = arcpy.Describe(ras1)
check("raster: in Web Mercator, on the services' own 30-unit grid", d.spatialReference.factoryCode == 3857 and d.meanCellWidth == 30.0, d.meanCellWidth)
bundle = portal.queued[0] if portal.queued else None
form = bundle.form() if bundle else {}
body = json.loads(form.get("products", ["{}"])[0])
check("bundles: one request, as the portal's own viewer sends it, with the address to mail the link to",
      len(portal.queued) == 1 and form.get("email") == [EMAIL] and form.get("request_origin") == ["'viewer'"]
      and bundle.headers.get("referer") == B.PORTAL + "viewer/" and body["download_type"] == "mapping_products"
      and body["projection"] == "UTM", (form.get("email"), bundle and bundle.headers))
check("bundles: an ordinary fire by its map ID, a non-standard one by its file; every product of the program, and the two images",
      body.get("mapping_ids") == [10016001] and body.get("mapping_bundles") == [["mtbs/2020/AZ3300011100020200601.zip", 10016002]]
      and body.get("mapping_products") == B.PROGRAMS["MTBS"]["bundle_products"] + B.BUNDLE_IMAGES, body)
order = [("queue" if a.path == "/queue" else "raster") for a in web.requests if a.path == "/queue" or "wcs" in a.path or "/images/" in a.path]
check("bundles: asked for BEFORE the rasters are fetched, so the portal works meanwhile", order[:1] == ["queue"] and "raster" in order[1:], order)
check("bundles: the run says they were asked for, and afterward that they were NOT downloaded and where the link goes",
      m.has("The request for the data bundles of 2 fires was sent to the Burn Severity Portal") and m.warned("THE DATA BUNDLES WERE NOT DOWNLOADED.")
      and m.warned(EMAIL), (m.lines, m.warnings))
md = arcpy.metadata.Metadata(o)
check("metadata: what was asked for and done; the address the link was mailed to is NOT written into the output",
      'Choices: name contains "wood; neil"; 2019 to 2020. 2 perimeters written; 2 classified rasters fetched into' in (md.summary or "")
      and "data bundles requested by email" in (md.summary or "")
      and EMAIL not in ((md.summary or "") + (md.description or "") + (md.credits or "") + (md.accessConstraints or "")), md.summary)

# a study area chooses the fires by their perimeters, not by the rectangle around it
portal = fresh()
portal.fires = [f0, other]
sr = arcpy.SpatialReference(4326)
area = arcpy.management.CreateFeatureclass(gdb, "study_area", "POLYGON", spatial_reference=sr)[0]
ax, ay = unmerc((CX + 30) * CELL, (CY + 40) * CELL)        # a triangle whose south corner reaches into WOODBURY only;
bx, by = unmerc((CX + 130) * CELL, (CY + 40) * CELL)       # the rectangle around it also covers O'NEIL EAST
cx, cy = unmerc((CX + 30) * CELL, (CY + 10) * CELL)
with arcpy.da.InsertCursor(area, ["SHAPE@"]) as c:
    c.insertRow((arcpy.Polygon(arcpy.Array([arcpy.Point(ax, ay), arcpy.Point(bx, by), arcpy.Point(cx, cy)]), sr),))
r, o, m = run(study_area=area)
q = web.asked("/geoserver/wfs")[0]
check("study area: the catalog is asked for the rectangle around it", "BBOX(geom," in q.one("cql_filter") and isinstance(r, dict), q.one("cql_filter"))
check("study area: only the fire whose perimeter touches the study area itself is kept, and it is written whole",
      isinstance(r, dict) and r["written"] == 1 and [x["fire_name"] for x in table(o)] == ["WOODBURY"]
      and m.has("1 MTBS fire matches (perimeters that touch the study area).")
      and m.has("The study area only selects the fires. Each fire that touches it is written whole"), (repr(r), m.lines))

# one assessment of a fire, by its map ID
portal = fresh()
portal.fires = [fire(1), fire(2, asmt_type="Initial")]
r, o, m = run(fire_ids="AZ3525811164320190721:10016002; AZ3525811164320190721:999")
check("fire ID with a map ID: that assessment alone; the other is said to be left out, and an entry with no assessment is warned of",
      isinstance(r, dict) and r["written"] == 1 and table(o)[0]["assessment"] == "Initial"
      and m.has("1 other assessment of these fires was left out") and m.warned("The MTBS catalog has no assessment for this entry: AZ3525811164320190721:999."),
      (repr(r), m.lines, m.warnings))

# sources that have nothing, fail, or hold only part of a fire
portal = fresh()
portal.fires = [f0]
portal.wcs = "none"
r, o, m = run(year_from=2019, products=[B.PRODUCT_MTBS], raster_workspace=ras_gdb)
check("the first source has nothing: the second is asked, and the raster comes from it",
      isinstance(r, dict) and len(r["rasters"]) == 1 and len(web.asked("/geoserver/wcs")) == 1 and len(web.asked("/images/")) == 1 and m.warnings == [],
      (repr(r), m.warnings))
portal = fresh()
portal.fires = [f0]
portal.wcs, portal.image = "none", "empty"
r, o, m = run(year_from=2019, products=[B.PRODUCT_MTBS], raster_workspace=ras_gdb)
check("neither source has a raster for the fire: the perimeter is still written, with an empty raster field, and the run says "
      "the fire's bundle is the other place to look", isinstance(r, dict) and r["rasters"] == [] and r["written"] == 1 and table(o)[0]["raster"] in (None, "")
      and m.warned("The image services hold no classified raster for 1 of the fires: WOODBURY 2019 (severity).")
      and not m.warned("COULD NOT BE FETCHED"), (repr(r), m.warnings))
portal = fresh()
portal.fires = [fire(i, name="FIRE %d" % i, event="AZ%017d" % i) for i in range(8)]
portal.wcs, portal.image = "error", "error"
r, o, m = run(year_from=2019, products=[B.PRODUCT_MTBS], raster_workspace=ras_gdb)
check("both sources FAIL: reported as failures, apart from 'no raster'; after five in a row the tool stops asking, and says "
      "how many it did not ask for", isinstance(r, dict) and r["rasters"] == [] and r["written"] == 8
      and m.warned("5 rasters COULD NOT BE FETCHED, because the image service failed or refused the request")
      and m.warned("After 5 requests in a row had failed the tool stopped asking, so 3 more rasters were not asked for.")
      and not m.warned("hold no classified raster"), (repr(r), m.warnings))
portal = fresh()
portal.fires = [f0]
portal.wcs, portal.image = "none", "partial"
r, o, m = run(year_from=2019, products=[B.PRODUCT_MTBS], raster_workspace=ras_gdb)
part = sum(true_areas(range(CX + 10, CX + 14), ROWS).values())
check("a raster that covers a small part of its fire (4 of 31 columns): written, with a warning that gives the hectares "
      "mapped and the share",
      isinstance(r, dict) and len(r["rasters"]) == 1
      and m.warned("1 of the rasters covers only a small part of its fire: WOODBURY 2019 (severity): %d of %d hectares, %d percent" % (
          round(part / 1e4), round(B.area_of(f0) / 1e4), round(100.0 * part / B.area_of(f0)))), (repr(r), m.warnings))
folder = os.path.join(work, "as_files")
os.makedirs(folder)
portal = fresh()
portal.fires = [f0]
r, o, m = run(year_from=2019, products=[B.PRODUCT_MTBS], raster_workspace=folder)
check("rasters into a folder: a GeoTIFF with its class table", isinstance(r, dict) and r["rasters"] == [os.path.join(folder, "MTBS_WOODBURY_2019_severity.tif")]
      and os.path.isfile(r["rasters"][0]) and [x[0] for x in arcpy.da.SearchCursor(r["rasters"][0], ["Class_Name"])][:1] == ["Unburned to Low"], repr(r))

# the bundles when the portal does not take the request
portal = fresh()
portal.fires = [f0]
portal.queue = {"success": False, "message": "The queue is full."}
r, o, m = run(year_from=2019, bundle_email=EMAIL)
check("the portal refuses the bundle request: a warning with its reason, and the rest of the run goes on",
      isinstance(r, dict) and r["written"] == 1 and r["bundle_message"] == ""
      and m.warned("THE REQUEST FOR THE DATA BUNDLES WAS NOT MADE: The portal did not accept the bundle request: The queue is full.")
      and not m.warned("THE DATA BUNDLES WERE NOT DOWNLOADED"), (repr(r), m.warnings))
portal = fresh()
web.forget()
web.answer("/queue", status=503, body="busy", method="POST")
portal = FakePortal(web)
portal.fires = [f0]
r, o, m = run(year_from=2019, bundle_email=EMAIL)
check("the portal is busy: the bundle request is sent ONCE (a second would ask for the bundles twice), and the run goes on",
      isinstance(r, dict) and len([a for a in web.requests if a.path == "/queue"]) == 1 and m.warned("THE REQUEST FOR THE DATA BUNDLES WAS NOT MADE"),
      (len([a for a in web.requests if a.path == "/queue"]), m.warnings))
refused("more fires than the portal takes in one bundle request (500)",
        lambda: B.request_bundles(EMAIL, [fire(i) for i in range(501)], ["x"]), "The portal takes at most 500 fires in one bundle request, and 501 were found.")
portal = fresh()
r, o, m = run(year_from=2019, bundle_email=EMAIL)
check("no fire matches: an empty output, a warning, and no bundle request",
      isinstance(r, dict) and r["written"] == 0 and arcpy.Exists(o) and m.warned("No MTBS fire matches, so the output is empty.")
      and m.has("No bundle was requested, because no fire matched.") and portal.queued == [], (repr(r), m.lines))
this_year = datetime.date.today().year
portal = fresh()
portal.fires = [f0]
r, o, m = run(year_from=2019, year_to=9999)
check("a last year in the future is taken as 'through the latest': warned of, and asked through this year",
      isinstance(r, dict) and m.warned("Fires were asked for through 9999") and ("ig_date <= '%d-12-31'" % this_year) in web.asked("/geoserver/wfs")[0].one("cql_filter"),
      m.warnings)

# ---- what is refused before anything is asked
portal = fresh()
for name, kw, words in (
        ("a program that is not one of the three", dict(program="NIFC", year_from=2019), "The program must be MTBS, RAVG or BAER."),
        ("nothing to search by", dict(), "Give at least a fire name, a year, a study area or a fire ID."),
        ("a product of another program", dict(year_from=2019, products=[B.PRODUCT_SBS], raster_workspace=ras_gdb),
         'MTBS has no product named "Soil burn severity (4 classes)". Its products: Thematic burn severity (6 classes).'),
        ("rasters asked for with nowhere to put them", dict(year_from=2019, products=[B.PRODUCT_MTBS]), "Give a folder or a file geodatabase for the classified rasters."),
        ("a place for the rasters that is not there", dict(year_from=2019, products=[B.PRODUCT_MTBS], raster_workspace=os.path.join(work, "nowhere")), "does not exist."),
        ("an email address that is not one", dict(year_from=2019, bundle_email="maria at home"), "That does not look like an email address: maria at home"),
        ("a first year before the programs began", dict(year_from=1970), "The first year must be between 1984 and this year."),
        ("years the wrong way round", dict(year_from=2020, year_to=2019), "The first year is after the last year.")):
    r, o, m = run(**kw)
    check("refused: %s" % name, isinstance(r, B.FireError) and words in str(r) and not arcpy.Exists(o), repr(r))
r, o, m = run(year_from=2019, out=os.path.join(work, "fires.shp"))
check("refused: an output outside a geodatabase, with an example named for the program",
      isinstance(r, B.FireError) and "MTBS_fires" in str(r) and "GBIF" not in str(r), repr(r))
check("refused: none of those asked any service anything", web.requests == [], web.requests)

check("at the end: every request found an answer prepared", web.unanswered == [], web.unanswered)
check("at the end: nothing tried to reach another computer", guard.attempts == [], guard.attempts)
point.__exit__(None, None, None)
B.PAUSE = 0.5
check("at the end: the engine's own addresses are put back", B.GEOSERVER == "https://edcintl.cr.usgs.gov/geoserver/"
      and B.QUEUE == "https://burnseverity.cr.usgs.gov/downloads/addQueue.php")
web.close()
guard.stop()

print("\n%d passed, %d failed%s" % (passed, failed, " (and %d known faults not yet fixed)" % known if known else ""))
arcpy.management.ClearWorkspaceCache()
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
