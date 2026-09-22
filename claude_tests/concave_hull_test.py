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

"""Tests for the Concave Hull tool (concave_hull.py).

Run with ArcGIS Pro's python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" concave_hull_test.py
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

import concave_hull as ch  # noqa: E402
import patch_analysis as _pa  # noqa: E402

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


def ring_area(ring):
    return abs(ch._ring_signed_area(np.asarray(ring)))


def all_inside(pts, ring, tol=1e-6):
    edges = np.hstack([ring, np.roll(ring, -1, axis=0)])
    return bool((_pa.points_inside(pts, edges)
                 | _pa.points_near_boundary(pts, edges, tol)).all())


def convex_area(pts):
    from scipy.spatial import ConvexHull
    return float(ConvexHull(pts).volume)


rng = np.random.default_rng(7)


def c_cloud(n=500):
    """A thick 'C': annulus 60-100 radius, opening toward the east."""
    pts = []
    while len(pts) < n:
        a = rng.uniform(0.25 * math.pi, 1.75 * math.pi)
        r = rng.uniform(60.0, 100.0)
        pts.append((r * math.cos(a), r * math.sin(a)))
    return np.asarray(pts)


CPTS = c_cloud()
CVX = convex_area(CPTS)

# ---------------------------------------------------------------------------
print("== chi shapes ==")
ring, area = ch.chi_shape(CPTS, 1e9)
check("threshold huge -> convex hull",
      ring is not None and abs(ring_area(ring) - CVX) < CVX * 1e-6,
      str((area, CVX)))
l_auto = ch.chi_auto_threshold(CPTS)
ring, area = ch.chi_shape(CPTS, l_auto)
check("auto threshold carves the C (area well under convex hull)",
      ring is not None and area < 0.75 * CVX and area > 0.2 * CVX,
      str((l_auto, area, CVX)))
check("auto chi contains every point", all_inside(CPTS, ring))
r_big, a_big = ch.chi_shape(CPTS, l_auto * 2.0)
check("nesting: bigger threshold -> bigger (or equal) area",
      a_big >= area - 1e-9, str((a_big, area)))

# ---------------------------------------------------------------------------
print("== alpha shapes ==")
a_opt = ch.alpha_spectrum_auto(CPTS, allow_parts=False)
rings, area = ch.alpha_shape(CPTS, a_opt)
ext = [r for r in rings if ch._ring_signed_area(r) > 0]
check("optimal alpha: single part covering the C",
      len(ext) == 1 and area < 0.8 * CVX,
      str((a_opt, len(ext), area, CVX)))
rings, area = ch.alpha_shape(CPTS, 1e9)
ext = [r for r in rings if ch._ring_signed_area(r) > 0]
check("alpha huge -> convex hull",
      len(ext) == 1 and abs(area - CVX) < CVX * 1e-6,
      str((area, CVX)))

# two separate clusters
TWO = np.vstack([rng.normal((0, 0), 10, (200, 2)),
                 rng.normal((500, 0), 10, (200, 2))])
a2 = ch.alpha_spectrum_auto(TWO, allow_parts=True)
rings, _ = ch.alpha_shape(TWO, a2)
ext = [r for r in rings if ch._ring_signed_area(r) > 0]
check("two clusters, parts allowed -> 2 exterior rings",
      len(ext) == 2, str((a2, len(ext))))
a1 = ch.alpha_spectrum_auto(TWO, allow_parts=False)
rings, _ = ch.alpha_shape(TWO, a1)
ext = [r for r in rings if ch._ring_signed_area(r) > 0]
check("two clusters, single part forced -> 1 exterior ring",
      len(ext) == 1 and a1 > a2, str((a1, len(ext))))

# a donut: alpha should find the hole
DON = []
while len(DON) < 600:
    a = rng.uniform(0, 2 * math.pi)
    r = rng.uniform(60.0, 100.0)
    DON.append((r * math.cos(a), r * math.sin(a)))
DON = np.asarray(DON)
a_d = ch.alpha_spectrum_auto(DON, allow_parts=True)
rings, _ = ch.alpha_shape(DON, a_d)
holes = [r for r in rings if ch._ring_signed_area(r) < 0]
check("donut cloud: alpha shape has an interior hole",
      len(holes) >= 1, str((a_d, len(rings), len(holes))))

# the automatic alpha must be gentler than concavity 100
a_auto = ch.alpha_spectrum_auto(CPTS, allow_parts=False)
a_100 = ch.alpha_from_concavity(CPTS, 100, allow_parts=False)
check("alpha auto sits above the concavity-100 extreme",
      a_auto > a_100 * 1.2, str((a_auto, a_100)))

# ---------------------------------------------------------------------------
print("== k-nearest-neighbours hull ==")
ring, k_used = ch.knn_hull(CPTS, 3)
check("kNN closes and contains every point",
      ring is not None and all_inside(CPTS, ring)
      and k_used >= 3, str(k_used))
check("kNN carves the C below the convex hull",
      ring_area(ring) < 0.85 * CVX,
      str((ring_area(ring), CVX)))
k0 = ch.knn_k_from_concavity(len(CPTS), 0)
k100 = ch.knn_k_from_concavity(len(CPTS), 100)
check("concavity mapping: 0 -> n-1, 100 -> 3",
      k0 == len(CPTS) - 1 and k100 == 3, str((k0, k100)))
ring, k_used = ch.knn_hull(CPTS, len(CPTS) - 1)
check("k = n-1 -> the convex hull",
      abs(ring_area(ring) - CVX) < CVX * 1e-6,
      str((ring_area(ring), CVX)))

# monotone contract: a smaller start can never settle on a LARGER k
# than a bigger start (the old x1.5 retry ladder could overshoot)
SPARSE = np.vstack([c_cloud(90), [[300.0, 0.0], [320.0, 15.0]]])
r3, k3 = ch.knn_hull(SPARSE, 3)
kk = int(round(math.sqrt(len(SPARSE))))
rs, ks = ch.knn_hull(SPARSE, kk)
check("kNN start 3 settles at k <= the sqrt(n) start's k",
      k3 <= ks and all_inside(SPARSE, r3)
      and all_inside(SPARSE, rs), str((k3, ks)))
check("kNN start 3 is at least as concave as the sqrt(n) start",
      ring_area(r3) <= ring_area(rs) + 1e-6,
      str((ring_area(r3), ring_area(rs))))

# ---------------------------------------------------------------------------
print("== driver: feature classes ==")
import arcpy  # noqa: E402

TMP = tempfile.mkdtemp(prefix="ch_test_")
arcpy.env.overwriteOutput = True
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
X0, Y0 = 500000.0, 3800000.0


class Msgs:
    def addMessage(self, t):
        pass

    def addWarningMessage(self, t):
        print("!", t)


fc = arcpy.management.CreateFeatureclass(
    GDB, "pts", "POINT", spatial_reference=SR).getOutput(0)
arcpy.management.AddField(fc, "Animal", "TEXT", field_length=16)
with arcpy.da.InsertCursor(fc, ["SHAPE@", "Animal"]) as ins:
    for x, y in CPTS:
        ins.insertRow([arcpy.PointGeometry(
            arcpy.Point(X0 + x, Y0 + y), SR), "Alpha"])
    for x, y in rng.normal((800, 0), 30, (100, 2)):
        ins.insertRow([arcpy.PointGeometry(
            arcpy.Point(X0 + x, Y0 + y), SR), "Beta"])

out = os.path.join(GDB, "hulls")
ch.run_concave_hull(fc, ch.METHOD_CHI, out, case_field="Animal",
                    messages=Msgs())
rows = {}
with arcpy.da.SearchCursor(out, ["Animal", "Method", "NPoints",
                                 "Param_m", "Parts", "Holes",
                                 "Area_m2"]) as cur:
    for row in cur:
        rows[row[0]] = row
check("driver: one hull per case-field group",
      set(rows) == {"Alpha", "Beta"}, str(set(rows)))
check("driver: point counts per group",
      rows["Alpha"][2] == len(CPTS) and rows["Beta"][2] == 100,
      str((rows["Alpha"][2], rows["Beta"][2])))
check("driver: chi carves the C group below convex hull",
      rows["Alpha"][6] < 0.75 * CVX, str(rows["Alpha"][6]))

out2 = os.path.join(GDB, "hulls_a")
ch.run_concave_hull(fc, ch.METHOD_ALPHA, out2, allow_parts=True,
                    messages=Msgs())
with arcpy.da.SearchCursor(out2, ["Parts", "Holes"]) as cur:
    parts, holes = next(cur)
check("driver: alpha over both clumps, parts allowed -> 2+ parts",
      parts >= 2, str((parts, holes)))

out3 = os.path.join(GDB, "hulls_k")
ch.run_concave_hull(fc, ch.METHOD_KNN, out3, case_field="Animal",
                    concavity=100, messages=Msgs())
k100 = {}
with arcpy.da.SearchCursor(out3, ["Animal", "K_used"]) as cur:
    for a, k in cur:
        k100[a] = k
check("driver: kNN K_used recorded for both groups",
      len(k100) == 2 and all(k >= 3 for k in k100.values()),
      str(k100))

out4 = os.path.join(GDB, "hulls_kb")
ch.run_concave_hull(fc, ch.METHOD_KNN, out4, case_field="Animal",
                    messages=Msgs())
kblank = {}
with arcpy.da.SearchCursor(out4, ["Animal", "K_used"]) as cur:
    for a, k in cur:
        kblank[a] = k
check("driver: kNN auto uses a gentler k than concavity 100",
      all(kblank[a] > k100[a] for a in k100), str((kblank, k100)))

print("== kNN concavity dial spans the workable range ==")
# Jeff's Oak Creek find (2026-09-05): with two separated clusters the
# kNN walk cannot close until k is large enough to see across the
# gap, so a fixed 3..n-1 dial collapses every mid setting onto that
# floor.  The dial must stretch over the k range that actually
# exists for the cloud.
check("kernel: floor-aware mapping reduces to classic at k_min=3",
      all(ch.knn_k_from_concavity(43, c) ==
          ch.knn_k_from_concavity(43, c, k_min=3)
          for c in (0, 25, 50, 75, 100)), "")
dial = {}
for cv in (0, 50, 100):
    oc = os.path.join(GDB, "hulls_dial_{}".format(cv))
    ch.run_concave_hull(fc, ch.METHOD_KNN, oc, concavity=cv,
                        messages=Msgs())
    with arcpy.da.SearchCursor(oc, ["NPoints", "K_used",
                                    "Area_m2"]) as cur:
        dial[cv] = next(cur)
n_all = dial[0][0]
check("dial: Concavity 0 is the convex hull (k = n-1)",
      dial[0][1] == n_all - 1, str(dial))
check("dial: K_used strictly decreasing across 0 / 50 / 100",
      dial[0][1] > dial[50][1] > dial[100][1], str(dial))
check("dial: area never grows as the dial tightens",
      dial[0][2] >= dial[50][2] >= dial[100][2], str(dial))

print("== driver: geographic points ==")
GCS = arcpy.SpatialReference(4326)
gfc = arcpy.management.CreateFeatureclass(
    GDB, "gpts", "POINT", spatial_reference=GCS).getOutput(0)
# ~2 km blob at 35 N
dlat = 1000.0 / 110967.0
dlon = 1000.0 / (111319.49 * math.cos(math.radians(35.0)))
with arcpy.da.InsertCursor(gfc, ["SHAPE@"]) as ins:
    for dx, dy in rng.uniform(-1, 1, (150, 2)):
        ins.insertRow([arcpy.PointGeometry(
            arcpy.Point(-111.0 + dx * dlon, 35.0 + dy * dlat),
            GCS)])
gout = os.path.join(GDB, "ghull")
ch.run_concave_hull(gfc, ch.METHOD_CHI, gout, concavity=0,
                    messages=Msgs())
with arcpy.da.SearchCursor(gout, ["Area_m2"]) as cur:
    garea = next(cur)[0]
# convex hull of a uniform 2 km x 2 km square sample: close to but
# under 4 sq km
check("geographic: geodesic area in a sane range (2.5-4.1 sq km)",
      2.5e6 < garea < 4.1e6, str(garea))

try:
    arcpy.management.Delete(GDB)
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass

print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
