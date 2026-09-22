# -*- coding: utf-8 -*-
# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Scholarship Fund notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Tests for the Longest Line in Polygon (Fetch) tool
(longest_line.py).

Run with ArcGIS Pro's python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" longest_line_test.py
"""
import math
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE),
                                "WildlifeTools", "Toolboxes"))

import longest_line as ll  # noqa: E402

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  {}".format(name))
    else:
        FAIL += 1
        print("  FAIL  {}  {}".format(name, detail))


class Msgs:
    def __init__(self):
        self.info = []
        self.warn = []

    def addMessage(self, t):
        self.info.append(str(t))

    def addWarningMessage(self, t):
        self.warn.append(str(t))


def ring(coords):
    return np.asarray(coords, dtype=np.float64)


def rect(x0, y0, x1, y1):
    return ring([(x0, y0), (x0, y1), (x1, y1), (x1, y0), (x0, y0)])


# ---------------------------------------------------------------------------
print("== longest interior line kernel (no arcpy) ==")

# rectangle: the diagonal
got = ll.longest_interior_line([rect(0, 0, 400, 300)])
check("rectangle: diagonal found",
      got is not None and abs(got[2] - 500.0) < 1e-6,
      str(got[2] if got else None))
ends = sorted([tuple(np.round(got[0], 6)), tuple(np.round(got[1],
                                                          6))])
check("rectangle: diagonal endpoints at corners",
      ends in ([(0.0, 0.0), (400.0, 300.0)],
               [(0.0, 300.0), (400.0, 0.0)]), str(ends))

# thin L-shape: the diagonal of the long arm, NOT corner to corner
L = ring([(0, 0), (0, 100), (20, 100), (20, 20), (300, 20),
          (300, 0), (0, 0)])
got = ll.longest_interior_line([L])
exp = math.hypot(300, 20)
check("L-shape: longest line stays inside the arms",
      got is not None and abs(got[2] - exp) < 0.5,
      "{} vs {}".format(got[2] if got else None, exp))

# central hole blocks the diagonal
DONUT = [rect(0, 0, 100, 100),
         ring(list(reversed(rect(40, 40, 60, 60))))]
got = ll.longest_interior_line(DONUT)
check("hole blocks the diagonal",
      got is not None and got[2] < 141.4 - 1.0
      and got[2] >= 100.0 - 1e-6,
      str(got[2] if got else None))

# the classic extension case (the manual's Figure): an M-roof room
# whose longest line passes THROUGH the two reflex valley vertices
# (100,10) and (200,10) and extends past BOTH to the slanted walls,
# giving y=10 from x=27 to x=273 -> exactly 246
MROOF = ring([(30, 0), (270, 0), (300, 100), (200, 10), (150, 40),
              (100, 10), (0, 100), (30, 0)])
got = ll.longest_interior_line([MROOF])
ok_ext = got is not None and abs(got[2] - 246.0) < 0.01
if ok_ext:
    xs = sorted([got[0][0], got[1][0]])
    ok_ext = abs(xs[0] - 27.0) < 0.01 and abs(xs[1] - 273.0) < 0.01
check("line extends past its two supporting reflex vertices",
      ok_ext, str((got[2], got[0], got[1]) if got else None))

# convexity sanity: hull-diameter equals the answer for convex shapes
HEX = ring([(math.cos(a) * 50 + 50, math.sin(a) * 50 + 50)
            for a in np.linspace(0, 2 * math.pi, 7)])
got = ll.longest_interior_line([HEX])
check("convex polygon: diameter through opposite vertices",
      got is not None and abs(got[2] - 100.0) < 1e-6,
      str(got[2] if got else None))

# ---------------------------------------------------------------------------
print("== radial fetch kernel ==")
edges = ll._pa.edges_of([rect(0, 0, 100, 60)])
d, capped = ll.radial_fetch(50, 30, [0, 90, 180, 270], edges, None)
check("radial distances to the four walls",
      np.allclose(d, [30, 50, 30, 50]), str(d))
check("no caps when uncapped", not capped.any())
d, capped = ll.radial_fetch(50, 30, [0, 90, 180, 270], edges, 40.0)
check("cap applies to the long rays",
      np.allclose(d, [30, 40, 30, 40])
      and list(capped) == [False, True, False, True], str(d))

bands = ll._pa.y_bands(edges)
x, y, moved = ll.nudge_inside(50, 30, edges, bands, 100)
check("inside station untouched", not moved and (x, y) == (50, 30))
x, y, moved = ll.nudge_inside(120, 30, edges, bands, 100)
check("outside station nudged just inside",
      moved and 99 < x <= 100.001 and ll._pa.points_inside(
          np.array([[x, y]]), edges, bands=bands)[0],
      "({}, {})".format(x, y))

# ---------------------------------------------------------------------------
print("== engine fixtures ==")
import arcpy  # noqa: E402

TMP = tempfile.mkdtemp(prefix="ll_test_")
arcpy.env.overwriteOutput = True
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
GCS = arcpy.SpatialReference(4326)


def make_fc(name, shape, geoms, sr=SR, attrs=None):
    fc = arcpy.management.CreateFeatureclass(
        GDB, name, shape, spatial_reference=sr).getOutput(0)
    cols = ["SHAPE@"]
    if attrs:
        arcpy.management.AddField(fc, "Name", "TEXT",
                                  field_length=32)
        cols.append("Name")
    with arcpy.da.InsertCursor(fc, cols) as ins:
        for i, g in enumerate(geoms):
            ins.insertRow([g] + ([attrs[i]] if attrs else []))
    return fc


def poly(rings_, sr=SR):
    return arcpy.Polygon(
        arcpy.Array([arcpy.Array([arcpy.Point(*c) for c in r])
                     for r in rings_]), sr)


def read(fc, fields):
    with arcpy.da.SearchCursor(fc, fields) as cur:
        return [row for row in cur]


# realistic UTM coordinates (near the central meridian, scale
# ~0.9996) so geodesic lengths sit within a meter of planar ones
X0, Y0 = 500000.0, 3800000.0


def sh(coords):
    return [(X0 + x, Y0 + y) for x, y in coords]


R1 = sh([(0, 0), (0, 300), (400, 300), (400, 0), (0, 0)])
LAKE = make_fc("lake", "POLYGON",
               [poly([R1]),
                poly([sh([(1000, 0), (1000, 100), (1400, 100),
                          (1400, 0), (1000, 0)]),
                      list(reversed(sh([(1150, 40), (1150, 60),
                                        (1250, 60), (1250, 40),
                                        (1150, 40)])))])],
               attrs=["Big", "Holey"])
m = Msgs()
out = ll.run_longest_line(LAKE, os.path.join(GDB, "fetch1"),
                          transfer_fields=["Name"], messages=m)
rows = read(out, ["Src_FID", "Length_m", "Azimuth", "X1", "Y1",
                  "X2", "Y2", "Name"])
check("one line per polygon", len(rows) == 2)
r1 = [r for r in rows if r[0] == 1][0]
check("rectangle lake: diagonal length (geodesic ~ planar here)",
      abs(r1[1] - 500.0) < 1.0, str(r1[1]))
check("azimuth consistent with endpoints",
      abs(((math.degrees(math.atan2(r1[5] - r1[3], r1[6] - r1[4]))
            % 360.0) - r1[2] + 180) % 360 - 180) < 1.0,
      str(r1[2]))
check("attributes transferred", all(r[7] in ("Big", "Holey")
                                    for r in rows))
r2 = [r for r in rows if r[0] == 2][0]
check("island shortens the second lake's fetch",
      r2[1] < math.hypot(400, 100) - 1.0, str(r2[1]))

# simplify tolerance: same answer within the tolerance, still runs
m = Msgs()
out = ll.run_longest_line(LAKE, os.path.join(GDB, "fetch1s"),
                          simplify_tolerance=2.0,
                          linear_units="Meters", messages=m)
rows = read(out, ["Src_FID", "Length_m"])
r1s = [r for r in rows if r[0] == 1][0]
check("simplify tolerance: diagonal within ~tolerance of exact",
      abs(r1s[1] - 500.0) < 4.0
      and any("Simplify tolerance" in t for t in m.info),
      str(r1s[1]))

# geographic rectangle ~ known geodesic diagonal
d = 0.01
GLAKE = make_fc("glake", "POLYGON",
                [poly([[(-111, 35), (-111, 35 + d), (-111 + 2 * d,
                                                     35 + d),
                        (-111 + 2 * d, 35), (-111, 35)]], GCS)],
                sr=GCS)
m = Msgs()
out = ll.run_longest_line(GLAKE, os.path.join(GDB, "fetch2"),
                          messages=m)
rows = read(out, ["Length_m"])
w = 2 * d * 111319.49 * math.cos(math.radians(35.005))
h = d * 110967.0
exp = math.hypot(w, h)
check("geographic lake: geodesic diagonal within 0.5%",
      len(rows) == 1 and abs(rows[0][0] - exp) / exp < 0.005,
      "{} vs {}".format(rows[0][0] if rows else None, exp))

# ---------------------------------------------------------------------------
print("== station fetch ==")
STN = make_fc("stn", "POINT",
              [arcpy.PointGeometry(arcpy.Point(X0 + 200, Y0 + 150),
                                   SR),
               arcpy.PointGeometry(arcpy.Point(X0 + 500, Y0 + 150),
                                   SR)])
m = Msgs()
out = ll.run_station_fetch(
    STN, LAKE, os.path.join(GDB, "rad1"),
    out_stations=os.path.join(GDB, "rad1_stn"),
    bearing_increment=90.0, linear_units="Meters", messages=m)
rows = read(out, ["Src_FID", "Kind", "Bearing", "Fetch_m",
                  "Capped"])
rays = [r for r in rows if r[1] == "Ray"]
check("4 rays + 2 exact lines per station",
      len(rays) == 8 and len(rows) == 12, str(len(rows)))
in1 = {r[2]: r[3] for r in rays if r[0] == 1}
check("center-station rays hit the right walls",
      abs(in1[0.0] - 150) < 0.5 and abs(in1[90.0] - 200) < 0.5
      and abs(in1[180.0] - 150) < 0.5 and abs(in1[270.0] - 200) < 0.5,
      str(in1))
srow = read(os.path.join(GDB, "rad1_stn"),
            ["Src_FID", "FetchMax", "MaxF_Brg", "MaxRay_m",
             "MaxChrd_m", "Shift_Dist"])
s1 = [r for r in srow if r[0] == 1][0]
check("station summary: max ray fetch on the long axis",
      abs(s1[1] - 200) < 0.5 and s1[2] in (90.0, 270.0)
      and s1[5] == 0.0, str(s1))
# EXACT longest ray / chord through the interior station at
# (200, 150) of the 400 x 300 rectangle: the chord through a far
# corner, e.g. (400, 300): direction (200, 150), |PV| = 250,
# extending back to (0, 0) -> chord 500 (the full diagonal);
# longest single ray = 250
check("EXACT longest ray from the station (corner-aimed)",
      abs(s1[3] - 250.0) < 0.5, str(s1[3]))
check("EXACT longest chord through the station (the diagonal)",
      abs(s1[4] - 500.0) < 0.5, str(s1[4]))
s2 = [r for r in srow if r[0] == 2][0]
check("dry-land station was nudged into the water",
      s2[5] > 0, str(s2))
# off-center exactness: station at (100, 50) in a 0..100 square...
# use a dedicated small square and hand-computed values
SQ = make_fc("sq", "POLYGON",
             [poly([sh([(2000, 0), (2000, 100), (2100, 100),
                        (2100, 0), (2000, 0)])])])
STN2 = make_fc("stn2", "POINT",
               [arcpy.PointGeometry(arcpy.Point(X0 + 2030, Y0 + 50),
                                    SR)])
m = Msgs()
out = ll.run_station_fetch(
    STN2, SQ, os.path.join(GDB, "rad1b"),
    out_stations=os.path.join(GDB, "rad1b_stn"),
    bearing_increment=90.0, linear_units="Meters", messages=m)
srow = read(os.path.join(GDB, "rad1b_stn"),
            ["MaxRay_m", "MaxChrd_m"])[0]
# P = (30, 50) in a 100 x 100 square: longest ray = to corner
# (100, 100) or (100, 0) = sqrt(70^2 + 50^2) = 86.023; longest chord
# through P = that line extended back to x=0 (at 3/7 of |PV|):
# 86.023 * 10/7 = 122.890
check("off-center EXACT ray matches hand computation",
      abs(srow[0] - 86.023) < 0.05, str(srow[0]))
check("off-center EXACT chord matches hand computation",
      abs(srow[1] - 122.890) < 0.05, str(srow[1]))

# max-fetch cap
m = Msgs()
out = ll.run_station_fetch(
    STN, LAKE, os.path.join(GDB, "rad2"),
    bearing_increment=90.0, max_fetch=100, linear_units="Meters",
    messages=m)
rows = read(out, ["Src_FID", "Kind", "Fetch_m", "Capped"])
mx = max(r[2] for r in rows if r[1] == "Ray")
mxc = max(r[2] for r in rows if r[1] == "MaxChord")
check("cap enforced at 100 m (rays; chords cap per side)",
      mx <= 100.0 + 1e-6 and mxc <= 200.0 + 1e-6
      and any(r[3] == 1 for r in rows), str((mx, mxc)))

# geographic stations: radial distances are true geodesics
GSTN = make_fc("gstn", "POINT",
               [arcpy.PointGeometry(
                   arcpy.Point(-111 + d, 35 + d / 2), GCS)], sr=GCS)
m = Msgs()
out = ll.run_station_fetch(
    GSTN, GLAKE, os.path.join(GDB, "rad3"),
    bearing_increment=90.0, linear_units="Meters", messages=m)
rows = read(out, ["Kind", "Bearing", "Fetch_m"])
by_b = {r[1]: r[2] for r in rows if r[0] == "Ray"}
check("geographic radials: east/west reach ~ half the lake width",
      abs(by_b[90.0] - w / 2) / (w / 2) < 0.01
      and abs(by_b[270.0] - w / 2) / (w / 2) < 0.01,
      str(by_b))
check("geographic radials: north/south reach ~ half the height",
      abs(by_b[0.0] - h / 2) / (h / 2) < 0.01
      and abs(by_b[180.0] - h / 2) / (h / 2) < 0.01, str(by_b))

# ---------------------------------------------------------------------------
print()
print("{} passed, {} failed".format(PASS, FAIL))
try:
    arcpy.management.Delete(GDB)
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass
sys.exit(1 if FAIL else 0)
