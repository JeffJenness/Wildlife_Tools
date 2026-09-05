# -*- coding: utf-8 -*-
"""Smoke test: the three Classification Accuracy tools through the
.pyt. Run with ArcGIS Pro's python.
"""
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
for t in ("ClassificationAccuracy", "CompareAccuracyAnalyses",
          "AccuracySampleSize"):
    check("toolbox lists " + t, hasattr(arcpy.jenness, t))

TMP = tempfile.mkdtemp(prefix="acc_smoke_")
GDB = arcpy.management.CreateFileGDB(TMP, "s.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
X0, Y0 = 500000.0, 3800000.0
M = np.array([[60, 22, 4], [2, 30, 3], [1, 4, 10]])
fc = arcpy.management.CreateFeatureclass(
    GDB, "pts", "POINT", spatial_reference=SR).getOutput(0)
arcpy.management.AddField(fc, "PredCls", "LONG")
arcpy.management.AddField(fc, "ObsCls", "LONG")
with arcpy.da.InsertCursor(fc, ["SHAPE@", "PredCls",
                                "ObsCls"]) as ins:
    n = 0
    for i in range(3):
        for j in range(3):
            for _ in range(int(M[i, j])):
                ins.insertRow([arcpy.PointGeometry(
                    arcpy.Point(X0 + (n % 20) * 30.0,
                                Y0 + (n // 20) * 30.0), SR),
                    i + 1, j + 1])
                n += 1

ctab = os.path.join(GDB, "cls_stats")
res = arcpy.jenness.ClassificationAccuracy(
    in_features=fc, predicted_field="PredCls",
    observed_field="ObsCls", out_class_table=ctab)
msgs = res.getMessages()
check("GP run: kappa 0.5493 in the messages", "0.5493" in msgs)
check("GP run: R snippet in the messages",
      "psych::cohen.kappa" in msgs)
n_rows = int(arcpy.management.GetCount(ctab).getOutput(0))
check("GP run: class table has 3 rows", n_rows == 3, str(n_rows))

res = arcpy.jenness.CompareAccuracyAnalyses(
    analyses="'Model A' 0.8 0.001;'Model B' 0.4 0.001")
check("GP compare: pairwise Z printed",
      "Z = 8.944" in res.getMessages())

res = arcpy.jenness.AccuracySampleSize(
    method="Worst case (no class proportions known)",
    n_classes=4, confidence=95, precision=10)
check("GP sample size: n = 156",
      "n = 156" in res.getMessages()
      and str(res.getOutput(1)) == "156")

try:
    arcpy.management.Delete(GDB)
except Exception:
    pass
print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
