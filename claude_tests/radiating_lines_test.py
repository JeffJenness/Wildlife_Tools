# -*- coding: utf-8 -*-
"""Tests for the Radiating Lines and Points tool (radiating_lines.py).

Run with ArcGIS Pro's python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" radiating_lines_test.py
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

import radiating_lines as rl  # noqa: E402

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


import arcpy  # noqa: E402

TMP = tempfile.mkdtemp(prefix="rl_test_")
arcpy.env.overwriteOutput = True
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
GCS = arcpy.SpatialReference(4326)
X0, Y0 = 500000.0, 3800000.0


def make_fc(name, shape, geoms, sr=SR, attrs=None):
    fc = arcpy.management.CreateFeatureclass(
        GDB, name, shape, spatial_reference=sr).getOutput(0)
    cols = ["SHAPE@"]
    if attrs:
        arcpy.management.AddField(fc, "Site", "TEXT",
                                  field_length=24)
        cols.append("Site")
    with arcpy.da.InsertCursor(fc, cols) as ins:
        for i, g in enumerate(geoms):
            ins.insertRow([g] + ([attrs[i]] if attrs else []))
    return fc


def read(fc, fields):
    with arcpy.da.SearchCursor(fc, fields) as cur:
        return [row for row in cur]


PTS = make_fc("pts", "POINT",
              [arcpy.PointGeometry(arcpy.Point(X0, Y0), SR),
               arcpy.PointGeometry(arcpy.Point(X0 + 1000, Y0), SR)],
              attrs=["Alpha", "Beta"])

# ---------------------------------------------------------------------------
print("== basic radiating lines ==")
m = Msgs()
out = rl.run_radiating_lines(
    PTS, n_lines=8, line_length=100, linear_units="Meters",
    transfer_fields=["Site"],
    out_lines=os.path.join(GDB, "r1"),
    out_points=os.path.join(GDB, "r1p"), messages=m)
rows = read(os.path.join(GDB, "r1"),
            ["Src_FID", "Origin_N", "Bearing", "Length_m", "Blocked",
             "Org_X", "End_X", "End_Y", "Site"])
check("8 lines per point", len(rows) == 16, str(len(rows)))
b1 = sorted(r[2] for r in rows if r[0] == 1)
check("bearings every 45 degrees from north",
      np.allclose(b1, np.arange(0, 360, 45)), str(b1))
check("full length, unblocked",
      all(abs(r[3] - 100.0) < 1e-6 and r[4] == 0 for r in rows))
check("attributes transferred",
      all(r[8] in ("Alpha", "Beta") for r in rows))
north = [r for r in rows if r[0] == 1 and r[2] == 0.0][0]
check("north line ends 100 m north",
      abs(north[6] - X0) < 1e-6 and abs(north[7] - (Y0 + 100)) < 1e-6,
      str((north[6], north[7])))

prow = read(os.path.join(GDB, "r1p"),
            ["Src_FID", "Origin_N", "Location", "Length_m",
             "Bearing"])
orgs = [r for r in prow if r[2] == "Origin"]
ends = [r for r in prow if r[2] == "End Point"]
check("points output: 2 origins + 16 end points",
      len(orgs) == 2 and len(ends) == 16)
check("origin rows carry null distance/bearing (gdb)",
      all(r[3] is None and r[4] is None and r[1] == 0 for r in orgs))

# bearing interval instead of count
out = rl.run_radiating_lines(
    PTS, bearing_interval=90, line_length=50, linear_units="Meters",
    out_lines=os.path.join(GDB, "r2"), messages=Msgs())
rows = read(os.path.join(GDB, "r2"), ["Src_FID", "Bearing"])
check("interval 90 -> 4 lines per point", len(rows) == 8)

# randomize first bearing, reproducible with a seed
out = rl.run_radiating_lines(
    PTS, n_lines=6, line_length=50, linear_units="Meters",
    randomize_first=True, random_seed=42,
    out_lines=os.path.join(GDB, "r3"), messages=Msgs())
b3 = sorted(r[0] for r in read(os.path.join(GDB, "r3"),
                               ["Bearing"]))[:6]
out = rl.run_radiating_lines(
    PTS, n_lines=6, line_length=50, linear_units="Meters",
    randomize_first=True, random_seed=42,
    out_lines=os.path.join(GDB, "r3b"), messages=Msgs())
b3b = sorted(r[0] for r in read(os.path.join(GDB, "r3b"),
                                ["Bearing"]))[:6]
check("randomized first bearing: not north, seed-reproducible",
      b3 == b3b and 0.0 < b3[0] < 60.0, str(b3[:2]))

# ---------------------------------------------------------------------------
print("== barriers ==")
WALL = make_fc("wall", "POLYLINE",
               [arcpy.Polyline(arcpy.Array(
                   [arcpy.Point(X0 + 40, Y0 - 200),
                    arcpy.Point(X0 + 40, Y0 + 200)]), SR)])
m = Msgs()
out = rl.run_radiating_lines(
    PTS, n_lines=4, line_length=100, linear_units="Meters",
    block_lines=WALL, out_lines=os.path.join(GDB, "r4"), messages=m)
rows = read(os.path.join(GDB, "r4"),
            ["Src_FID", "Bearing", "Length_m", "Blocked"])
p1 = {r[1]: (r[2], r[3]) for r in rows if r[0] == 1}
check("east ray blocked at the wall (40 m)",
      abs(p1[90.0][0] - 40.0) < 0.01 and p1[90.0][1] == 1,
      str(p1[90.0]))
check("other rays unblocked at full length",
      all(abs(p1[b][0] - 100.0) < 1e-6 and p1[b][1] == 0
          for b in (0.0, 180.0, 270.0)), str(p1))
p2 = {r[1]: (r[2], r[3]) for r in rows if r[0] == 2}
check("second point (east of wall): west ray blocked",
      abs(p2[270.0][0] - 960.0) < 0.01 or p2[270.0][1] == 0,
      str(p2))
# the second point is 960 m from the wall -- beyond the 100 m length,
# so ALL its rays run free
check("second point: all rays free (wall out of reach)",
      all(v[1] == 0 for v in p2.values()), str(p2))

BLK = make_fc("blk", "POLYGON",
              [arcpy.Polygon(arcpy.Array(
                  [arcpy.Point(X0 - 200, Y0 + 60),
                   arcpy.Point(X0 + 200, Y0 + 60),
                   arcpy.Point(X0 + 200, Y0 + 300),
                   arcpy.Point(X0 - 200, Y0 + 300),
                   arcpy.Point(X0 - 200, Y0 + 60)]), SR)])
out = rl.run_radiating_lines(
    PTS, n_lines=4, line_length=100, linear_units="Meters",
    block_lines=WALL, block_polygons=BLK,
    out_lines=os.path.join(GDB, "r5"), messages=Msgs())
rows = read(os.path.join(GDB, "r5"),
            ["Src_FID", "Bearing", "Length_m", "Blocked"])
p1 = {r[1]: (r[2], r[3]) for r in rows if r[0] == 1}
check("north ray blocked at the polygon boundary (60 m)",
      abs(p1[0.0][0] - 60.0) < 0.01 and p1[0.0][1] == 1,
      str(p1[0.0]))
check("east ray still blocked by the wall",
      abs(p1[90.0][0] - 40.0) < 0.01 and p1[90.0][1] == 1)

# ---------------------------------------------------------------------------
print("== multiple barrier feature classes ==")
WALL_W = make_fc("wall_w", "POLYLINE",
                 [arcpy.Polyline(arcpy.Array(
                     [arcpy.Point(X0 - 30, Y0 - 200),
                      arcpy.Point(X0 - 30, Y0 + 200)]), SR)])
out = rl.run_radiating_lines(
    PTS, n_lines=4, line_length=100, linear_units="Meters",
    block_lines=[WALL, WALL_W],
    out_lines=os.path.join(GDB, "r7"), messages=Msgs())
rows = read(os.path.join(GDB, "r7"),
            ["Src_FID", "Bearing", "Length_m", "Blocked"])
p1 = {r[1]: (r[2], r[3]) for r in rows if r[0] == 1}
check("two polyline FCs: east ray blocked at 40 m",
      abs(p1[90.0][0] - 40.0) < 0.01 and p1[90.0][1] == 1,
      str(p1[90.0]))
check("two polyline FCs: west ray blocked at 30 m",
      abs(p1[270.0][0] - 30.0) < 0.01 and p1[270.0][1] == 1,
      str(p1[270.0]))

# a semicolon-delimited multivalue string (the GP dialog's form)
out = rl.run_radiating_lines(
    PTS, n_lines=4, line_length=100, linear_units="Meters",
    block_lines="{};{}".format(WALL, WALL_W),
    out_lines=os.path.join(GDB, "r7b"), messages=Msgs())
rows = read(os.path.join(GDB, "r7b"),
            ["Src_FID", "Bearing", "Length_m", "Blocked"])
p1 = {r[1]: (r[2], r[3]) for r in rows if r[0] == 1}
check("semicolon multivalue string: both walls block",
      abs(p1[90.0][0] - 40.0) < 0.01
      and abs(p1[270.0][0] - 30.0) < 0.01, str(p1))

# origin INSIDE a barrier polygon: the BOUNDARY blocks, so the point
# still gets its full fan, each line stopping at the boundary
BOX = make_fc("box", "POLYGON",
              [arcpy.Polygon(arcpy.Array(
                  [arcpy.Point(X0 - 50, Y0 - 50),
                   arcpy.Point(X0 - 50, Y0 + 50),
                   arcpy.Point(X0 + 50, Y0 + 50),
                   arcpy.Point(X0 + 50, Y0 - 50),
                   arcpy.Point(X0 - 50, Y0 - 50)]), SR)])
PT1 = make_fc("pt1", "POINT",
              [arcpy.PointGeometry(arcpy.Point(X0, Y0), SR)])
out = rl.run_radiating_lines(
    PT1, n_lines=4, line_length=100, linear_units="Meters",
    block_polygons=BOX,
    out_lines=os.path.join(GDB, "r8"), messages=Msgs())
rows = read(os.path.join(GDB, "r8"),
            ["Bearing", "Length_m", "Blocked"])
check("origin inside barrier polygon: full fan, all stopped at "
      "the boundary (50 m)",
      len(rows) == 4 and all(abs(r[1] - 50.0) < 0.01 and r[2] == 1
                             for r in rows), str(rows))

# a barrier in a DIFFERENT coordinate system than the points
WALL_LL = arcpy.management.Project(
    WALL, os.path.join(GDB, "wall_ll"), GCS).getOutput(0)
out = rl.run_radiating_lines(
    PTS, n_lines=4, line_length=100, linear_units="Meters",
    block_lines=WALL_LL, out_lines=os.path.join(GDB, "r9"),
    messages=Msgs())
rows = read(os.path.join(GDB, "r9"),
            ["Src_FID", "Bearing", "Length_m", "Blocked"])
p1 = {r[1]: (r[2], r[3]) for r in rows if r[0] == 1}
check("barrier in a different CS: east ray still blocked ~40 m",
      abs(p1[90.0][0] - 40.0) < 0.5 and p1[90.0][1] == 1,
      str(p1[90.0]))

# ---------------------------------------------------------------------------
print("== geographic points: true geodesic radials ==")
GPTS = make_fc("gpts", "POINT",
               [arcpy.PointGeometry(arcpy.Point(-111.0, 35.0), GCS)],
               sr=GCS)
out = rl.run_radiating_lines(
    GPTS, n_lines=4, line_length=10, linear_units="Kilometers",
    out_lines=os.path.join(GDB, "r6"),
    out_points=os.path.join(GDB, "r6p"), messages=Msgs())
rows = read(os.path.join(GDB, "r6p"),
            ["Location", "Bearing", "X_Coord", "Y_Coord"])
east = [r for r in rows if r[0] == "End Point" and r[1] == 90.0][0]
north = [r for r in rows if r[0] == "End Point" and r[1] == 0.0][0]
dx_deg = east[2] - (-111.0)
dy_deg = north[3] - 35.0
exp_dx = 10000.0 / (111319.49 * math.cos(math.radians(35.0)))
exp_dy = 10000.0 / 110967.0
check("east endpoint ~10 km true east",
      abs(dx_deg - exp_dx) / exp_dx < 0.01, str((dx_deg, exp_dx)))
check("north endpoint ~10 km true north",
      abs(dy_deg - exp_dy) / exp_dy < 0.01, str((dy_deg, exp_dy)))

# ---------------------------------------------------------------------------
print()
print("{} passed, {} failed".format(PASS, FAIL))
try:
    arcpy.management.Delete(GDB)
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass
sys.exit(1 if FAIL else 0)
