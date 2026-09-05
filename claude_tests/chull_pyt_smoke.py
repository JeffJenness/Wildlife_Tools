# -*- coding: utf-8 -*-
"""Smoke test: Concave Hull through the .pyt.

Run with ArcGIS Pro's python.
"""
import math
import os
import sys
import tempfile

import numpy as np

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
      hasattr(arcpy.jenness, "ConcaveHull"))

TMP = tempfile.mkdtemp(prefix="chull_smoke_")
GDB = arcpy.management.CreateFileGDB(TMP, "s.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
X0, Y0 = 500000.0, 3800000.0
rng = np.random.default_rng(11)
fc = arcpy.management.CreateFeatureclass(
    GDB, "pts", "POINT", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as ins:
    n = 0
    while n < 300:
        a = rng.uniform(0.25 * math.pi, 1.75 * math.pi)
        r = rng.uniform(60.0, 100.0)
        ins.insertRow([arcpy.PointGeometry(
            arcpy.Point(X0 + r * math.cos(a), Y0 + r * math.sin(a)),
            SR)])
        n += 1

out = os.path.join(GDB, "hull")
arcpy.jenness.ConcaveHull(
    in_features=fc, method="Chi shape (characteristic shape)",
    out_features=out)
with arcpy.da.SearchCursor(out, ["Method", "NPoints",
                                 "Area_m2"]) as cur:
    row = next(cur)
check("GP run: one chi hull", row[1] == 300, str(row))
convex = math.pi * 100 * 100 * 0.75 * 1.2   # loose convex-ish bound
check("GP run: hull carves the C (area below convex hull)",
      row[2] < convex, str((row[2], convex)))

out2 = os.path.join(GDB, "hull_k")
arcpy.jenness.ConcaveHull(
    in_features=fc, method="k-nearest-neighbours hull",
    concavity=50, out_features=out2)
with arcpy.da.SearchCursor(out2, ["K_used"]) as cur:
    k = next(cur)[0]
check("GP run: kNN hull with K_used recorded", k >= 3, str(k))

try:
    arcpy.management.Delete(GDB)
except Exception:
    pass
print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
