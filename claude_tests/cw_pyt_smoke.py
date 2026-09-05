# -*- coding: utf-8 -*-
"""Smoke test: Centerline and Width through the .pyt itself.

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
      hasattr(arcpy.jenness, "CenterlineAndWidth"))

TMP = tempfile.mkdtemp(prefix="cw_smoke_")
GDB = arcpy.management.CreateFileGDB(TMP, "s.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
fc = arcpy.management.CreateFeatureclass(
    GDB, "riv", "POLYGON", spatial_reference=SR).getOutput(0)
arcpy.management.AddField(fc, "RName", "TEXT", field_length=32)
with arcpy.da.InsertCursor(fc, ["SHAPE@", "RName"]) as ins:
    ins.insertRow([arcpy.Polygon(arcpy.Array(
        [arcpy.Point(*c) for c in
         [(0, 0), (0, 80), (1200, 80), (1200, 0), (0, 0)]]), SR),
        "Test Wash"])

out = os.path.join(GDB, "cl")
arcpy.jenness.CenterlineAndWidth(
    in_features=fc,
    mode="Main centerline only",
    transfer_fields="RName",
    station_spacing=100,
    linear_units="Meters",
    out_lines=out,
    out_points=os.path.join(GDB, "cl_pts"))
n = int(arcpy.management.GetCount(out).getOutput(0))
np_ = int(arcpy.management.GetCount(
    os.path.join(GDB, "cl_pts")).getOutput(0))
check("GP run: one centerline + station points", n == 1 and np_ >= 10,
      "n={} pts={}".format(n, np_))
fields = {f.name for f in arcpy.ListFields(out)}
check("fields present incl. transferred",
      {"Line_ID", "Src_FID", "IsTrunk", "Length_m", "MinW_m",
       "MeanW_m", "MaxW_m", "RName"} <= fields, str(fields))
with arcpy.da.SearchCursor(out, ["MeanW_m", "RName"]) as cur:
    row = next(cur)
check("width ~ 80 m and name carried",
      68 <= row[0] <= 92 and row[1] == "Test Wash", str(row))

try:
    arcpy.management.Delete(GDB)
except Exception:
    pass
print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
