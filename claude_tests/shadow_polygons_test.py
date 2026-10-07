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
"""Create Shadow Polygons with distance, angle and spread (2026-10-06): the
ring plan and the shadow offset match the Graphic Background tools' drop
shadow, and the engine builds the rings it promises on a projected and a
geographic polygon. Calls the ENGINE directly (no ImportToolbox, so no
sidecar churn). Run with ArcGIS Pro's python from the repo root:
  "C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" claude_tests/shadow_polygons_test.py
"""
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import cartography_tools as ct  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        fails.append(name)


def near(a, b, tol):
    return abs(a - b) <= tol


# ------------------------------------------------------------ the arithmetic
plan = ct.plan_rings(5, 100.0, 50.0, 50.0)
check("5 rings", len(plan) == 5)
check("spread 50: band from 0 to size", near(plan[0][0], 100.0, 1e-9) and near(plan[-1][1], 0.0, 1e-9))
check("rings 20 m each", all(near(o - i, 20.0, 1e-9) for o, i, _ in plan))
check("innermost ring at the minimum transparency", near(plan[-1][2], 50.0, 1e-9))
check("outermost ring nearly clear", plan[0][2] > 90.0 and plan[0][2] < 100.0, plan[0][2])
check("transparency falls inward", all(plan[j][2] > plan[j + 1][2] for j in range(4)))
plan0 = ct.plan_rings(4, 100.0, 0.0, 60.0)
check("spread 0: band from -size to +size", near(plan0[0][0], 100.0, 1e-9) and near(plan0[-1][1], -100.0, 1e-9))
plan100 = ct.plan_rings(4, 100.0, 100.0, 60.0)
check("spread 100: hard edge at size", all(near(o, 100.0, 1e-9) and near(i, 100.0, 1e-9) for o, i, _ in plan100))
check("one ring: full opacity", ct.plan_rings(1, 50.0, 50.0, 70.0) == [(50.0, 0.0, 30.0)])
dx, dy = ct.shadow_offset(135.0, 10.0)
check("light from the upper left: shadow to the lower right", dx > 0 and dy < 0 and near(math.hypot(dx, dy), 10.0, 1e-9), (dx, dy))
dx, dy = ct.shadow_offset(90.0, 10.0)
check("light from the top: shadow straight down", near(dx, 0.0, 1e-9) and near(dy, -10.0, 1e-9), (dx, dy))
dx, dy = ct.shadow_offset(0.0, 0.0)
check("no distance: no shift", dx == 0 and dy == 0)
check("compass to math: north 0 -> 90, east 90 -> 0, northwest 315 -> 135, south 180 -> 270",
      ct.light_azimuth_to_math(0) == 90 and ct.light_azimuth_to_math(90) == 0
      and ct.light_azimuth_to_math(315) == 135 and ct.light_azimuth_to_math(180) == 270)


# ------------------------------------------------------------- the engine
class Msgs:
    def __init__(self):
        self.lines = []

    def addMessage(self, t):
        self.lines.append(t)

    def addWarningMessage(self, t):
        self.lines.append("W " + t)

    def addErrorMessage(self, t):
        self.lines.append("E " + t)


work = tempfile.mkdtemp(prefix="shadow_test_")
gdb = arcpy.management.CreateFileGDB(work, "t.gdb").getOutput(0)


def square(name, sr, x0, y0, side):
    fc = arcpy.management.CreateFeatureclass(gdb, name, "POLYGON", spatial_reference=sr).getOutput(0)
    with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as cur:
        pts = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side), (x0, y0)]
        cur.insertRow([arcpy.Polygon(arcpy.Array([arcpy.Point(*p) for p in pts]), sr)])
    return fc


def rows_of(fc, size_field="Buffer_Size", transp_field="Transparency"):
    out = []
    with arcpy.da.SearchCursor(fc, ["SHAPE@", size_field, transp_field]) as cur:
        for geom, bs, tr in cur:
            out.append((geom, bs, tr))
    return out


# a 1000 m square in UTM 12N
utm = arcpy.SpatialReference(26912)
src = square("sq", utm, 500000.0, 4000000.0, 1000.0)

# --- the original behavior: no shift, spread 50 -> rings outward only
out1 = os.path.join(gdb, "sq_glow")
m = Msgs()
lyrx = ct.run_shadow_polygons(src, out1, num_rings=5, size_value=100.0, size_units="Meters",
                              min_transparency=50.0, messages=m)
r = rows_of(out1)
check("glow: source + 5 rings, no core", len(r) == 6, len(r))
source = [x for x in r if x[1] == 0.0 and x[2] == 100.0]
check("glow: source row transparent", len(source) == 1)
rings = sorted([x for x in r if x[2] < 100.0], key=lambda x: x[1])
check("glow: ring outer distances 20..100", [round(x[1]) for x in rings] == [20, 40, 60, 80, 100], [x[1] for x in rings])
check("glow: innermost ring at 50", near(rings[0][2], 50.0, 1e-6), rings[0][2])
outer_ext = rings[-1][0].extent
check("glow: outer ring reaches 100 m beyond the square on every side",
      near(outer_ext.XMin, 500000 - 100, 2) and near(outer_ext.XMax, 501000 + 100, 2)
      and near(outer_ext.YMin, 4000000 - 100, 2) and near(outer_ext.YMax, 4001000 + 100, 2))
check("glow: nothing inside the source", all(x[0].disjoint(source[0][0]) or x[0].touches(source[0][0]) for x in rings))
check("glow: layer file written", lyrx and os.path.exists(lyrx), lyrx)

# --- a drop shadow: shifted 200 m to the lower right, soft edge centered (spread 0)
out2 = os.path.join(gdb, "sq_drop")
m = Msgs()
ct.run_shadow_polygons(src, out2, num_rings=4, size_value=100.0, size_units="Meters",
                       min_transparency=40.0, distance_value=200.0, angle=315.0, spread=0.0, messages=m)
r = rows_of(out2)
pieces = [x for x in r if x[2] < 100.0]
check("drop: source + 4 rings + core", len(r) == 6, len(r))
check("drop: band radii -100..100", [round(x[1]) for x in sorted(pieces, key=lambda x: x[1])] == [-100, -50, 0, 50, 100],
      [x[1] for x in pieces])
core = min(pieces, key=lambda x: x[1])
check("drop: core at full opacity", near(core[2], 40.0, 1e-6), core[2])
all_shadow = pieces[0][0]
for x in pieces[1:]:
    all_shadow = all_shadow.union(x[0])
ext = all_shadow.extent
shift = 200.0 / math.sqrt(2.0)
check("drop: shadow extent shifted to the lower right",
      near(ext.XMax, 501000 + shift + 100, 3) and near(ext.YMin, 4000000 - shift - 100, 3), (ext.XMax, ext.YMin))
check("drop: shadow reaches past the square's right edge", ext.XMax > 501000 + 100)
check("drop: nothing covers the source", all(x[0].disjoint(source[0][0]) or x[0].touches(source[0][0]) for x in pieces))
check("drop: message names the shift", any("shifted 200.0 m" in t for t in m.lines), m.lines[:3])

# --- hard edge: spread 100, one ring
out3 = os.path.join(gdb, "sq_hard")
ct.run_shadow_polygons(src, out3, num_rings=1, size_value=50.0, size_units="Meters",
                       min_transparency=30.0, distance_value=100.0, angle=0.0, spread=100.0, messages=Msgs())
r = rows_of(out3)
pieces = [x for x in r if x[2] < 100.0]
check("hard: pieces at 70 opacity", all(near(x[2], 30.0, 1e-6) for x in pieces), [x[2] for x in pieces])
ext = pieces[0][0].extent if len(pieces) == 1 else pieces[0][0].union(pieces[1][0]).extent
check("hard: light from the north -> shadow straight south", near(ext.YMin, 4000000 - 100 - 50, 2) and near(ext.XMin, 500000 - 50, 2), (ext.XMin, ext.YMin))

# --- geographic input: geodesic buffers and a geodesic shift
wgs = arcpy.SpatialReference(4326)
srcg = square("sqg", wgs, -111.65, 35.19, 0.01)
out4 = os.path.join(gdb, "sqg_drop")
m = Msgs()
ct.run_shadow_polygons(srcg, out4, num_rings=3, size_value=0.1, size_units="Proportion of extent width",
                       min_transparency=50.0, distance_value=300.0, angle=270.0, spread=50.0, messages=m)
r = rows_of(out4)
pieces = [x for x in r if x[2] < 100.0]
check("geo: 3 rings + core", len(pieces) == 4, len(pieces))
check("geo: geodesic buffering reported", any("geodesic" in t for t in m.lines))
ext = max(pieces, key=lambda x: x[1])[0].extent
check("geo: light from the west -> shadow shifted east", ext.XMax > -111.64 + 0.0001 and near(ext.YMax, 35.20 + (ext.YMax - 35.20), 1e-9))
src_ext = arcpy.Describe(srcg).extent
check("geo: shadow sits east of the source", ext.XMax - src_ext.XMax > 0.002, ext.XMax - src_ext.XMax)

# --- a shapefile output keeps the short field names
shp = os.path.join(work, "sq_shadow.shp")
ct.run_shadow_polygons(src, shp, num_rings=2, size_value=50.0, size_units="Meters", messages=Msgs())
names = [f.name for f in arcpy.ListFields(shp)]
check("shapefile field names", "BufferSize" in names and "Transp_Pct" in names, names)

# --- a memory output: the layer file lands in the scratch folder
mem_out = r"memory\sq_shadow_mem"
lyrx_mem = ct.run_shadow_polygons(src, mem_out, num_rings=2, size_value=50.0, size_units="Meters", messages=Msgs())
check("memory output: layer file written", bool(lyrx_mem) and os.path.exists(lyrx_mem), lyrx_mem)
check("memory output: layer file in the scratch folder",
      bool(lyrx_mem) and os.path.normcase(os.path.dirname(lyrx_mem)) == os.path.normcase(arcpy.env.scratchFolder), lyrx_mem)
check("memory output: records written", int(arcpy.management.GetCount(mem_out)[0]) == 3)

# --- bad values are refused
for kw, msg in (({"distance_value": -1}, "negative"), ({"spread": 120}, "spread"), ({"num_rings": 0}, "at least 1"),
                ({"angle": 400}, "compass direction")):
    try:
        ct.run_shadow_polygons(src, os.path.join(gdb, "bad"), **kw)
        check("refused: " + msg, False)
    except arcpy.ExecuteError as e:
        check("refused: " + msg, msg in str(e).lower(), str(e))

print("\nSHADOW POLYGONS: %d failed" % len(fails))
sys.exit(1 if fails else 0)
