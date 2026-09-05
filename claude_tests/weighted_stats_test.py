# -*- coding: utf-8 -*-
"""Tests for the Weighted Summary Statistics tool (weighted_stats.py).

Run with ArcGIS Pro's python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" weighted_stats_test.py
"""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE),
                                "WildlifeTools", "Toolboxes"))

import weighted_stats as ws  # noqa: E402

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


def close(a, b, tol=1e-10):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# ---------------------------------------------------------------------------
print("== pure math ==")

# 1) equal weights reduce EXACTLY to the ordinary sample statistics
v = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0])
s = ws.weighted_summary(v, np.full(v.size, 7.5))
check("equal-w mean == mean", close(s["w_mean"], v.mean()))
check("equal-w var == n-1 var", close(s["w_var"], v.var(ddof=1)))
check("equal-w sd == n-1 sd", close(s["w_sd"], v.std(ddof=1)))
check("equal-w median == median (even n)", close(s["w_median"], np.median(v)))

v2 = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0])
s2 = ws.weighted_summary(v2, np.ones(v2.size))
check("equal-w median == median (odd n)", close(s2["w_median"], np.median(v2)))

# 2) weighted values against an independent numpy computation
rng = np.random.default_rng(42)
v = rng.normal(50, 10, 500)
w = rng.uniform(0.1, 30, 500)
s = ws.weighted_summary(v, w)
W = w.sum()
mw = (w * v).sum() / W
var_w = (w * (v - mw) ** 2).sum() / (W - (w * w).sum() / W)
check("weighted mean vs numpy", close(s["w_mean"], mw))
check("weighted var vs numpy", close(s["w_var"], var_w))
check("weighted sd vs numpy", close(s["w_sd"], np.sqrt(var_w)))
check("unweighted family intact", close(s["mean"], v.mean())
      and close(s["var"], v.var(ddof=1)) and close(s["median"], np.median(v)))
check("w_total = sum of weights", close(s["w_total"], W))

# 3) weighted median: replication equivalence -- integer weights behave
#    exactly like repeating each value that many times
v = np.array([10.0, 20.0, 30.0, 40.0])
w = np.array([1.0, 1.0, 6.0, 2.0])
rep = np.repeat(v, w.astype(int))
s = ws.weighted_summary(v, w)
check("weighted median == replicated median", close(s["w_median"],
                                                    np.median(rep)))

# 4) dominant weight pulls the median to that value
s = ws.weighted_summary(np.array([1.0, 2.0, 100.0]),
                        np.array([1.0, 1.0, 50.0]))
check("dominant-weight median", close(s["w_median"], 100.0))

# 5) zero weights: excluded from the weighted family, kept in the record
#    family
v = np.array([5.0, 7.0, 1000.0])
w = np.array([2.0, 2.0, 0.0])
s = ws.weighted_summary(v, w)
check("zero-weight kept unweighted", close(s["mean"], v.mean()))
check("zero-weight dropped weighted", close(s["w_mean"], 6.0))

# 6) edge cases
s = ws.weighted_summary(np.array([]), np.array([]))
check("empty -> n=0, stats None", s["n"] == 0 and s["mean"] is None
      and s["w_mean"] is None)
s = ws.weighted_summary(np.array([4.2]), np.array([3.0]))
check("n=1 -> sd/var None, w_var None", s["sd"] is None and s["var"] is None
      and s["w_var"] is None and close(s["w_mean"], 4.2)
      and close(s["mean"], 4.2))
s = ws.weighted_summary(np.array([1.0, 2.0]), np.array([0.0, 0.0]))
check("all-zero weights -> weighted None", s["w_mean"] is None
      and close(s["mean"], 1.5))

# ---------------------------------------------------------------------------
print("== arcpy round trip ==")
import arcpy  # noqa: E402

tmp = tempfile.mkdtemp(prefix="wstats_")
gdb = os.path.join(tmp, "t.gdb")
arcpy.management.CreateFileGDB(tmp, "t.gdb")
sr = arcpy.SpatialReference(26912)   # NAD83 UTM 12N

fc = os.path.join(gdb, "polys")
arcpy.management.CreateFeatureclass(gdb, "polys", "POLYGON",
                                    spatial_reference=sr)
arcpy.management.AddField(fc, "HSI", "DOUBLE")
arcpy.management.AddField(fc, "CLASS", "TEXT", field_length=20)


def square(x0, y0, side):
    return arcpy.Polygon(arcpy.Array([arcpy.Point(x0, y0),
                                      arcpy.Point(x0, y0 + side),
                                      arcpy.Point(x0 + side, y0 + side),
                                      arcpy.Point(x0 + side, y0)]), sr)


# big low-value block + small high-value slivers: the classic case where
# the unweighted mean is misleadingly optimistic
rows = [
    (square(400000, 3800000, 5000), 20.0, "A"),   # 25 km2 of poor habitat
    (square(410000, 3800000, 500), 90.0, "A"),    # 0.25 km2 excellent
    (square(412000, 3800000, 500), 80.0, "A"),
    (square(414000, 3800000, 1000), 50.0, "B"),
    (square(416000, 3800000, 2000), 60.0, "B"),
    (square(419000, 3800000, 1000), None, "B"),   # null value
]
with arcpy.da.InsertCursor(fc, ["SHAPE@", "HSI", "CLASS"]) as cur:
    for r in rows:
        cur.insertRow(r)

# expected: recompute with geodesic areas read independently
areas = {}
with arcpy.da.SearchCursor(fc, ["OID@", "SHAPE@", "HSI", "CLASS"]) as cur:
    data = [(oid, shp.getArea("GEODESIC", "SQUAREMETERS"), hsi, cl)
            for oid, shp, hsi, cl in cur]

out1 = os.path.join(gdb, "stats_all")
ws.run_weighted_stats(fc, ["HSI"], out1, case_field=None, messages=None)

vals = np.array([d[2] for d in data if d[2] is not None])
wts = np.array([d[1] for d in data if d[2] is not None])
exp = ws.weighted_summary(vals, wts)

got = [r for r in arcpy.da.SearchCursor(
    out1, ["FIELD_NAME", "N", "N_NULL", "MEAN", "W_MEAN", "W_STD_DEV",
           "W_MEDIAN", "W_TOTAL", "MEDIAN", "STD_DEV"])]
check("one row, field name", len(got) == 1 and got[0][0] == "HSI")
r = got[0]
check("N / N_NULL", r[1] == 5 and r[2] == 1)
check("table MEAN", close(r[3], vals.mean(), 1e-9))
check("table W_MEAN vs independent calc", close(r[4], exp["w_mean"], 1e-9))
check("table W_STD_DEV", close(r[5], exp["w_sd"], 1e-9))
check("table W_MEDIAN", close(r[6], exp["w_median"], 1e-9))
check("table W_TOTAL", close(r[7], exp["w_total"], 1e-9))
check("weighted mean pulled toward the big poor block",
      r[4] < r[3] - 10, "W_MEAN {} vs MEAN {}".format(r[4], r[3]))

# case-field run
out2 = os.path.join(gdb, "stats_case")
ws.run_weighted_stats(fc, ["HSI"], out2, case_field="CLASS", messages=None)
got = {r[0]: r for r in arcpy.da.SearchCursor(
    out2, ["CASE_VAL", "N", "N_NULL", "W_MEAN"])}
check("case rows A and B", set(got) == {"A", "B"})
va = np.array([d[2] for d in data if d[3] == "A" and d[2] is not None])
wa = np.array([d[1] for d in data if d[3] == "A" and d[2] is not None])
expA = ws.weighted_summary(va, wa)
check("case A n/null", got["A"][1] == 3 and got["A"][2] == 0)
check("case B n/null", got["B"][1] == 2 and got["B"][2] == 1)
check("case A W_MEAN", close(got["A"][3], expA["w_mean"], 1e-9))

# selection honored (layer with 2 selected features)
lyr = arcpy.management.MakeFeatureLayer(fc, "wtest_lyr").getOutput(0)
arcpy.management.SelectLayerByAttribute(
    lyr, "NEW_SELECTION", "HSI IN (90, 80)")
out3 = os.path.join(gdb, "stats_sel")
ws.run_weighted_stats(lyr, ["HSI"], out3, case_field=None, messages=None)
got = [r for r in arcpy.da.SearchCursor(out3, ["N", "MEAN"])]
check("selection honored", got[0][0] == 2 and close(got[0][1], 85.0, 1e-9))

# polylines: length weighting
fcl = os.path.join(gdb, "lines")
arcpy.management.CreateFeatureclass(gdb, "lines", "POLYLINE",
                                    spatial_reference=sr)
arcpy.management.AddField(fcl, "VAL", "DOUBLE")
with arcpy.da.InsertCursor(fcl, ["SHAPE@", "VAL"]) as cur:
    cur.insertRow((arcpy.Polyline(arcpy.Array(
        [arcpy.Point(400000, 3800000), arcpy.Point(409000, 3800000)]), sr),
        10.0))
    cur.insertRow((arcpy.Polyline(arcpy.Array(
        [arcpy.Point(400000, 3801000), arcpy.Point(401000, 3801000)]), sr),
        90.0))
with arcpy.da.SearchCursor(fcl, ["SHAPE@", "VAL"]) as cur:
    ldata = [(shp.getLength("GEODESIC", "METERS"), v) for shp, v in cur]
out4 = os.path.join(gdb, "stats_lines")
ws.run_weighted_stats(fcl, ["VAL"], out4, case_field=None, messages=None)
lv = np.array([d[1] for d in ldata])
lw = np.array([d[0] for d in ldata])
lexp = ws.weighted_summary(lv, lw)
got = [r for r in arcpy.da.SearchCursor(out4, ["MEAN", "W_MEAN"])]
check("lines: MEAN 50", close(got[0][0], 50.0, 1e-9))
check("lines: W_MEAN length-weighted", close(got[0][1], lexp["w_mean"],
                                             1e-9))
check("lines: long poor line dominates", got[0][1] < 25,
      "W_MEAN {}".format(got[0][1]))

# folder output -> .dbf appended
out5 = os.path.join(tmp, "folder_stats")
res = ws.run_weighted_stats(fc, ["HSI"], out5, case_field=None,
                            messages=None)
check("folder output gains .dbf", res.lower().endswith(".dbf")
      and arcpy.Exists(res))

# points rejected
fcp = os.path.join(gdb, "pts")
arcpy.management.CreateFeatureclass(gdb, "pts", "POINT",
                                    spatial_reference=sr)
try:
    ws.run_weighted_stats(fcp, ["OBJECTID"], os.path.join(gdb, "x"),
                          case_field=None, messages=None)
    check("points rejected", False, "no error raised")
except ValueError as e:
    check("points rejected", "POLYLINE or POLYGON" in str(e))

# metadata stamped
md = arcpy.metadata.Metadata(out1)
check("metadata stamped", "Weighted Summary Statistics" in (md.title or "")
      and "ecologically correct" in (md.description or ""))

# .pyt registration: import the toolbox and check the tool exists
pyt = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes",
                   "JennessEnterprisesTools.pyt")
arcpy.ImportToolbox(pyt)
check("pyt tool registered", hasattr(arcpy.jenness,
                                     "WeightedSummaryStatistics"))

# full GP-level run through the .pyt
out6 = os.path.join(gdb, "stats_gp")
arcpy.jenness.WeightedSummaryStatistics(fc, "HSI", "CLASS", out6)
got = {r[0]: r[1] for r in arcpy.da.SearchCursor(out6,
                                                 ["CASE_VAL", "W_MEAN"])}
check("gp-level run matches engine",
      close(got["A"], expA["w_mean"], 1e-9))

print("\n{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
