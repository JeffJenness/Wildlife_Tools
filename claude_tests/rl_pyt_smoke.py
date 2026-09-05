# -*- coding: utf-8 -*-
"""Smoke test: Radiating Lines and Points through the .pyt.

Run with ArcGIS Pro's python.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PYT = os.path.join(os.path.dirname(HERE), "WildlifeTools",
                   "Toolboxes", "JennessEnterprisesTools.pyt")

import arcpy  # noqa: E402

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


arcpy.env.overwriteOutput = True
arcpy.ImportToolbox(PYT)
check("toolbox lists the tool",
      hasattr(arcpy.jenness, "RadiatingLines"))

TMP = tempfile.mkdtemp(prefix="rl_smoke_")
GDB = arcpy.management.CreateFileGDB(TMP, "s.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
X0, Y0 = 500000.0, 3800000.0
pts = arcpy.management.CreateFeatureclass(
    GDB, "pts", "POINT", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(pts, ["SHAPE@"]) as ins:
    ins.insertRow([arcpy.PointGeometry(arcpy.Point(X0, Y0), SR)])

out_l = os.path.join(GDB, "rays")
out_p = os.path.join(GDB, "ray_pts")
arcpy.jenness.RadiatingLines(
    points=pts, n_lines=8, line_length=100.0, linear_units="Meters",
    out_lines=out_l, out_points=out_p)
n_l = int(arcpy.management.GetCount(out_l).getOutput(0))
n_p = int(arcpy.management.GetCount(out_p).getOutput(0))
check("GP run: 8 lines", n_l == 8, str(n_l))
check("GP run: 1 origin + 8 end points", n_p == 9, str(n_p))

wall = arcpy.management.CreateFeatureclass(
    GDB, "wall", "POLYLINE", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(wall, ["SHAPE@"]) as ins:
    ins.insertRow([arcpy.Polyline(arcpy.Array(
        [arcpy.Point(X0 + 40, Y0 - 200),
         arcpy.Point(X0 + 40, Y0 + 200)]), SR)])
wall_w = arcpy.management.CreateFeatureclass(
    GDB, "wall_w", "POLYLINE", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(wall_w, ["SHAPE@"]) as ins:
    ins.insertRow([arcpy.Polyline(arcpy.Array(
        [arcpy.Point(X0 - 30, Y0 - 200),
         arcpy.Point(X0 - 30, Y0 + 200)]), SR)])
out_b = os.path.join(GDB, "rays_b")
arcpy.jenness.RadiatingLines(
    points=pts, bearing_interval=90.0, line_length=100.0,
    linear_units="Meters", block_lines=[wall, wall_w],
    out_lines=out_b)
blocked = {}
with arcpy.da.SearchCursor(out_b, ["Bearing", "Length_m",
                                   "Blocked"]) as cur:
    for brg, ln, bl in cur:
        blocked[round(brg)] = (ln, bl)
check("GP run: east ray blocked at 40 m (multivalue barriers)",
      abs(blocked[90][0] - 40.0) < 0.5 and blocked[90][1] == 1,
      str(blocked.get(90)))
check("GP run: west ray blocked at 30 m (second barrier FC)",
      abs(blocked[270][0] - 30.0) < 0.5 and blocked[270][1] == 1,
      str(blocked.get(270)))
check("GP run: north ray unblocked",
      abs(blocked[0][0] - 100.0) < 0.5 and blocked[0][1] == 0,
      str(blocked.get(0)))

try:
    arcpy.management.Delete(GDB)
except Exception:
    pass
print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
