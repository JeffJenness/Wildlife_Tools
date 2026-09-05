# -*- coding: utf-8 -*-
"""Smoke test: Maximum Inscribed Geometry through the .pyt.

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
      hasattr(arcpy.jenness, "MaximumInscribedGeometry"))

TMP = tempfile.mkdtemp(prefix="ig_smoke_")
GDB = arcpy.management.CreateFileGDB(TMP, "s.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
X0, Y0 = 500000.0, 3800000.0
fc = arcpy.management.CreateFeatureclass(
    GDB, "poly", "POLYGON", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as ins:
    ins.insertRow([arcpy.Polygon(arcpy.Array(
        [arcpy.Point(X0 + x, Y0 + y) for x, y in
         [(0, 0), (200, 0), (200, 100), (0, 100), (0, 0)]]), SR)])

out = os.path.join(GDB, "shapes")
arcpy.jenness.MaximumInscribedGeometry(
    in_features=fc, geometry_type="All four", out_features=out)
kinds = {}
with arcpy.da.SearchCursor(out, ["Shape_Kind", "Radius_m",
                                 "Length_m", "Width_m"]) as cur:
    for row in cur:
        kinds[row[0]] = row
check("GP run: 4 shapes written", len(kinds) == 4, str(set(kinds)))
check("GP run: circle r = 50",
      abs(kinds["Circle"][1] - 50.0) < 0.5,
      str(kinds.get("Circle")))
check("GP run: rectangle 200 x 100",
      abs(kinds["Rectangle"][2] - 200.0) < 2.0
      and abs(kinds["Rectangle"][3] - 100.0) < 2.0,
      str(kinds.get("Rectangle")))

try:
    arcpy.management.Delete(GDB)
except Exception:
    pass
print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
