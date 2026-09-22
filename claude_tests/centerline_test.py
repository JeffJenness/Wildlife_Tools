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

"""Tests for the Centerline and Width tool (centerline_width.py).

Run with ArcGIS Pro's python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" centerline_test.py
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

import centerline_width as cw  # noqa: E402

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


# ---------------------------------------------------------------------------
print("== pure graph work (no arcpy) ==")

# a path graph 0-1-2-3-4: trunk = the whole path
verts = np.array([[0.0, 0], [10, 0], [20, 0], [30, 0], [40, 0]])
edges = np.array([[0, 1], [1, 2], [2, 3], [3, 4]])
mask = np.ones(5, dtype=bool)
t = cw.trunk_path(verts, edges, mask)
check("trunk of a path graph = the whole path",
      set(t.tolist()) == {0, 1, 2, 3, 4}
      and (t[0], t[-1]) in ((0, 4), (4, 0)))

# a T: long bar 0..4 plus a short spur 2-5
verts = np.array([[0.0, 0], [10, 0], [20, 0], [30, 0], [40, 0],
                  [20, 5]])
edges = np.array([[0, 1], [1, 2], [2, 3], [3, 4], [2, 5]])
mask = np.ones(6, dtype=bool)
t = cw.trunk_path(verts, edges, mask)
check("trunk of a T ignores the short spur",
      5 not in set(t.tolist()) and len(t) == 5)
keep = cw.prune_spurs(verts, edges, min_len=8.0)
check("prune_spurs removes the 5-unit spur",
      keep.sum() == 4 and not keep[4])
keep = cw.prune_spurs(verts, edges, min_len=3.0)
check("prune_spurs keeps spurs above the threshold",
      keep.sum() == 5)
protect = np.zeros(5, dtype=bool)
protect[4] = True
keep = cw.prune_spurs(verts, edges, min_len=8.0, protect=protect)
check("protected edges are never pruned", keep.sum() == 5)

chains = cw.chains_of(verts, edges)
check("chains_of splits the T at the junction",
      len(chains) == 3
      and sorted(len(c) for c in chains) == [2, 3, 3])

# a pure cycle comes back closed
verts = np.array([[0.0, 0], [10, 0], [10, 10], [0, 10]])
edges = np.array([[0, 1], [1, 2], [2, 3], [3, 0]])
chains = cw.chains_of(verts, edges)
check("a pure cycle becomes one closed chain",
      len(chains) == 1 and chains[0][0] == chains[0][-1]
      and len(chains[0]) == 5)

# smoothing keeps endpoints and stays deterministic
xy = np.array([[0.0, 0], [10, 4], [20, -4], [30, 4], [40, 0]])
pe = np.array([[-100.0, -100, 100, -100], [100, -100, 100, 100],
               [100, 100, -100, 100], [-100, 100, -100, -100]])
bands = None
sm = cw.smooth_chain(xy, 15.0, pe, cw._pa.y_bands(pe))
check("smoothing keeps the endpoints fixed",
      np.allclose(sm[0], xy[0]) and np.allclose(sm[-1], xy[-1]))
check("smoothing reduces the wiggle",
      np.abs(sm[1:-1, 1]).max() < np.abs(xy[1:-1, 1]).max())

st, pts, cum = cw.station_chain(np.array([[0.0, 0], [100, 0]]), 25.0)
check("station_chain spacing", len(st) == 5
      and np.allclose(np.diff(st), 25.0))
piece = cw.slice_chain(np.array([[0.0, 0], [100, 0]]), cum
                       if len(cum) == 2 else np.array([0.0, 100.0]),
                       25.0, 75.0)
check("slice_chain endpoints exact",
      np.allclose(piece[0], [25, 0]) and np.allclose(piece[-1],
                                                     [75, 0]))

# ---------------------------------------------------------------------------
print("== engine fixtures ==")
import arcpy  # noqa: E402

TMP = tempfile.mkdtemp(prefix="cw_test_")
arcpy.env.overwriteOutput = True
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
GCS = arcpy.SpatialReference(4326)


def make_fc(name, geoms, sr=SR, attrs=None):
    fc = arcpy.management.CreateFeatureclass(
        GDB, name, "POLYGON", spatial_reference=sr).getOutput(0)
    cols = ["SHAPE@"]
    if attrs:
        arcpy.management.AddFields(fc, [["Name", "TEXT", "Name", 32,
                                         "", ""],
                                        ["Val", "LONG", "Val", "", "",
                                         ""]])
        cols += ["Name", "Val"]
    with arcpy.da.InsertCursor(fc, cols) as ins:
        for i, g in enumerate(geoms):
            ins.insertRow([g] + (list(attrs[i]) if attrs else []))
    return fc


def poly(rings, sr=SR):
    return arcpy.Polygon(
        arcpy.Array([arcpy.Array([arcpy.Point(*c) for c in r])
                     for r in rings]), sr)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x0, y1), (x1, y1), (x1, y0), (x0, y0)]


def read(fc, fields):
    out = []
    with arcpy.da.SearchCursor(fc, fields) as cur:
        for row in cur:
            out.append(row)
    return out


# ---------------------------------------------------------------------------
print("== straight rectangle: the canonical river ==")
RECT = make_fc("r1", [poly([rect(0, 0, 1000, 100)])],
               attrs=[("Big Wash", 7)])
m = Msgs()
out = cw.run_centerline(RECT, os.path.join(GDB, "cl1"),
                        mode="Main centerline only",
                        transfer_fields=["Name", "Val"],
                        out_points=os.path.join(GDB, "cl1_pts"),
                        station_spacing=50,
                        out_segments=os.path.join(GDB, "cl1_seg"),
                        linear_units="Meters", messages=m)
rows = read(out, ["SHAPE@", "Line_ID", "Src_FID", "IsTrunk",
                  "Length_m", "MinW_m", "MeanW_m", "MaxW_m", "Name",
                  "Val"])
check("one trunk line", len(rows) == 1 and rows[0][3] == 1)
shp = rows[0][0]
check("length ~ rectangle length",
      850 <= rows[0][4] <= 1050, str(rows[0][4]))
check("mean width ~ 100 m", 85 <= rows[0][6] <= 115, str(rows[0][6]))
check("min width not poisoned by boundary extension",
      rows[0][5] > 60, str(rows[0][5]))
check("attributes transferred to lines",
      rows[0][8] == "Big Wash" and rows[0][9] == 7)
# geometry runs along the midline and reaches the end caps
p0 = (shp.firstPoint.X, shp.firstPoint.Y)
p1 = (shp.lastPoint.X, shp.lastPoint.Y)
xs = sorted([p0[0], p1[0]])
check("extends to the end caps", xs[0] < 30 and xs[1] > 970,
      str(xs))
mid_ok = True
arr = shp.getPart(0)
for i in range(arr.count):
    pt = arr.getObject(i)
    if 100 < pt.X < 900 and abs(pt.Y - 50.0) > 15:
        mid_ok = False
check("midline stays near y=50", mid_ok)

prow = read(os.path.join(GDB, "cl1_pts"),
            ["Station_m", "Width_m", "Name", "Val", "Line_ID"])
st = sorted(p[0] for p in prow)
check("station points every ~50 m",
      len(prow) >= 15 and abs(np.median(np.diff(st)) - 50) < 5,
      "n={} med={}".format(len(prow),
                           np.median(np.diff(st)) if len(st) > 1
                           else "?"))
wmid = [p[1] for p in prow if 200 < p[0] < 800]
check("mid-river station widths ~ 100 m",
      wmid and all(85 <= w <= 115 for w in wmid))
check("attributes transferred to points",
      all(p[2] == "Big Wash" and p[3] == 7 for p in prow))

srow = read(os.path.join(GDB, "cl1_seg"),
            ["Seg_ID", "From_m", "To_m", "MinW_m", "MeanW_m",
             "MaxW_m", "Name"])
check("segments cover the line in order",
      len(srow) >= 15
      and all(a[2] > a[1] for a in srow)
      and all(a[3] <= a[4] <= a[5] for a in srow)
      and all(a[6] == "Big Wash" for a in srow))

# reserved-name transfer: OBJECTID / Shape_Length / Shape_Area carry
# the SOURCE polygon's values under a Src_ prefix (Jeff, 2026-08-24)
m = Msgs()
out = cw.run_centerline(RECT, os.path.join(GDB, "cl1r"),
                        mode="Main centerline only",
                        transfer_fields=["OBJECTID", "Shape_Length",
                                         "Shape_Area", "Name"],
                        linear_units="Meters", messages=m)
fset = {f.name for f in __import__("arcpy").ListFields(out)}
check("reserved fields renamed Src_*",
      {"Src_OBJECTID", "Src_Shape_Length", "Src_Shape_Area",
       "Name"} <= fset, str(sorted(fset)))
row = read(out, ["Src_OBJECTID", "Src_Shape_Length",
                 "Src_Shape_Area", "Length_m"])[0]
check("Src_* values are the SOURCE polygon's, not the line's",
      row[0] == 1 and abs(row[1] - 2200.0) < 1.0
      and abs(row[2] - 100000.0) < 10.0
      and abs(row[3] - row[1]) > 500,      # line length != perimeter
      str(row))

# a geometry field cannot transfer; reported, not crashed
m = Msgs()
out = cw.run_centerline(RECT, os.path.join(GDB, "cl1g"),
                        mode="Main centerline only",
                        transfer_fields=["Shape", "Val"],
                        linear_units="Meters", messages=m)
check("geometry field skipped with a message",
      any("cannot transfer" in t for t in m.info)
      and {f.name for f in
           __import__("arcpy").ListFields(out)} >= {"Val"})

# shapefile output: Src_ names truncated to 10 chars and unique
SHPDIR = os.path.join(TMP, "shp_out")
os.makedirs(SHPDIR, exist_ok=True)
m = Msgs()
out = cw.run_centerline(RECT, os.path.join(SHPDIR, "cl_shp"),
                        mode="Main centerline only",
                        transfer_fields=["Shape_Length",
                                         "Shape_Area"],
                        linear_units="Meters", messages=m)
names = [f.name for f in __import__("arcpy").ListFields(out)]
xf = [n for n in names
      if n.lower().startswith("src_") and n != "Src_FID"]
check("shapefile: transferred names <= 10 chars and unique",
      len(xf) == 2 and all(len(n) <= 10 for n in xf)
      and len(set(xf)) == 2, str(names))

# ---------------------------------------------------------------------------
print("== plus-shaped polygon: branches ==")
PLUS = make_fc("plus", [poly([[(400, 0), (600, 0), (600, 400),
                               (1000, 400), (1000, 600), (600, 600),
                               (600, 1000), (400, 1000), (400, 600),
                               (0, 600), (0, 400), (400, 400),
                               (400, 0)]])])
m = Msgs()
out = cw.run_centerline(PLUS, os.path.join(GDB, "cl2"),
                        mode="Full branched skeleton",
                        linear_units="Meters", messages=m)
rows = read(out, ["IsTrunk", "Length_m"])
check("skeleton mode yields multiple branches", len(rows) >= 3,
      str(len(rows)))
check("trunk flagged and tributaries flagged",
      any(r[0] == 1 for r in rows) and any(r[0] == 0 for r in rows))
m = Msgs()
out = cw.run_centerline(PLUS, os.path.join(GDB, "cl2t"),
                        mode="Main centerline only",
                        linear_units="Meters", messages=m)
rows = read(out, ["Length_m"])
check("trunk mode yields one line spanning opposite arms",
      len(rows) == 1 and rows[0][0] > 850, str(rows))

# default station spacing: derived from the TRUNK (length/100) and
# shared by the branches -- NOT 100 stations on every branch (Jeff's
# 395k-point blow-up, 2026-08-24)
m = Msgs()
out = cw.run_centerline(PLUS, os.path.join(GDB, "cl2p"),
                        mode="Full branched skeleton",
                        out_points=os.path.join(GDB, "cl2p_pts"),
                        linear_units="Meters", messages=m)
n_lines = len(read(out, ["Line_ID"]))
n_pts = len(read(os.path.join(GDB, "cl2p_pts"), ["Line_ID"]))
check("skeleton stations scale with trunk spacing, not 100/branch",
      100 <= n_pts < 100 * n_lines * 0.6,
      "{} lines, {} points".format(n_lines, n_pts))

# ---------------------------------------------------------------------------
print("== donut: a hole the size of the problem ==")
DONUT = make_fc("donut", [poly([rect(0, 0, 1000, 1000),
                                list(reversed(rect(300, 300, 700,
                                                   700)))])])
m = Msgs()
out = cw.run_centerline(DONUT, os.path.join(GDB, "cl3"),
                        mode="Full branched skeleton",
                        linear_units="Meters", messages=m)
rows = read(out, ["SHAPE@", "Length_m", "MeanW_m"])
# a square annulus's skeleton = the midline ring SPLIT at the four
# corner-spoke junctions (4 sides ~700 m) plus the corner spokes
check("donut: skeleton built without destroying the hole",
      len(rows) >= 4, str(len(rows)))
ring = max(rows, key=lambda r: r[1])
check("donut: longest piece ~ one ring side (700 m)",
      500 <= ring[1] <= 900, str(ring[1]))
total_len = sum(r[1] for r in rows)
check("donut: whole skeleton covers the ring channel",
      2400 <= total_len <= 4500, str(total_len))
check("donut: ring width ~ 300 m channel",
      200 <= ring[2] <= 320, str(ring[2]))

# island inside a river: skeleton routes around it
ISLE = make_fc("isle", [poly([rect(0, 0, 1000, 100),
                              list(reversed(rect(450, 40, 550,
                                                 60)))])])
m = Msgs()
out = cw.run_centerline(ISLE, os.path.join(GDB, "cl4"),
                        mode="Main centerline only",
                        linear_units="Meters", messages=m)
rows = read(out, ["SHAPE@", "Length_m"])
check("island: trunk still crosses the full river",
      len(rows) == 1 and rows[0][1] > 900, str(rows))

# an OFF-CENTER island: wide channel south (40 m), narrow north
# (10 m) -- the main centerline must take the WIDER channel (Jeff's
# Black Bear corridor finding, 2026-08-24)
ISLE2 = make_fc("isle2", [poly([rect(0, 0, 1000, 100),
                                list(reversed(rect(400, 40, 600,
                                                   90)))])])
m = Msgs()
out = cw.run_centerline(ISLE2, os.path.join(GDB, "cl4b"),
                        mode="Main centerline only",
                        linear_units="Meters", messages=m)
rows = read(out, ["SHAPE@", "MinW_m"])
ok_side = len(rows) == 1
if ok_side:
    arr = rows[0][0].getPart(0)
    for i in range(arr.count):
        pt = arr.getObject(i)
        if 420 < pt.X < 580 and pt.Y > 40:
            ok_side = False
check("off-center island: centerline takes the WIDER channel",
      ok_side)
# min width dips to ~15 m passing the island's corner funnel (honest
# geometry) but must stay clear of the 10 m narrow-channel value
check("off-center island: minimum width above the narrow channel's",
      len(rows) == 1 and rows[0][1] > 12, str(rows[0][1]
                                              if rows else "?"))

# ---------------------------------------------------------------------------
print("== batches, multipart, geographic ==")
TWO = make_fc("two", [poly([rect(0, 0, 500, 50)]),
                      poly([rect(0, 200, 800, 260)])])
out = cw.run_centerline(TWO, os.path.join(GDB, "cl5"),
                        mode="Main centerline only",
                        linear_units="Meters", messages=Msgs())
rows = read(out, ["Src_FID", "Length_m"])
check("two polygons -> two lines with distinct Src_FID",
      len(rows) == 2 and {r[0] for r in rows} == {1, 2})

MPART = make_fc("mp", [poly([rect(0, 0, 400, 40),
                             rect(0, 200, 400, 240)])])
out = cw.run_centerline(MPART, os.path.join(GDB, "cl6"),
                        mode="Main centerline only",
                        linear_units="Meters", messages=Msgs())
rows = read(out, ["Src_FID", "Part"])
check("multipart -> one line per part, same Src_FID",
      len(rows) == 2 and {r[0] for r in rows} == {1}
      and {r[1] for r in rows} == {1, 2})

d = 0.01
GRECT = make_fc("g1", [poly([rect(-111, 35, -111 + d, 35 + 0.001)],
                            GCS)], sr=GCS)
m = Msgs()
out = cw.run_centerline(GRECT, os.path.join(GDB, "cl7"),
                        mode="Main centerline only",
                        linear_units="Meters", messages=m)
rows = read(out, ["Length_m", "MeanW_m"])
exp_len = d * 111319.49 * math.cos(math.radians(35.0))
check("geographic: trunk length ~ true meters",
      len(rows) == 1 and abs(rows[0][0] - exp_len) / exp_len < 0.15,
      "{} vs {}".format(rows[0][0] if rows else "?", exp_len))
check("geographic: width ~ 111 m",
      len(rows) == 1 and 85 <= rows[0][1] <= 135, str(rows))

# ---------------------------------------------------------------------------
print()
print("{} passed, {} failed".format(PASS, FAIL))
try:
    arcpy.management.Delete(GDB)
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass
sys.exit(1 if FAIL else 0)
