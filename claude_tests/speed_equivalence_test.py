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

"""Equivalence harness for the 2026-09-04 speed round: every
vectorized rewrite must give EXACTLY the same answers as the scalar
code it replaced.

1. _clip_polygon_rect (vectorized S-H) vs _clip_polygon_rect_ref
   (the original scalar loop): bit-identical signed areas over
   thousands of randomized rings x cells -- this is the sampler's
   most safety-critical geometry.
2. ArealMeasure.points_in (batch even-odd) vs point_in (scalar):
   identical booleans, including hole parity.
3. The kappa polygon grid index vs the linear scan: identical
   extracted classes (arcpy section).
Plus timing checks proving the speedups are real."""
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))),
    "WildlifeTools", "Toolboxes"))
import grts_core as G

rng = np.random.default_rng(99)
ok = 0


def check(cond, label):
    global ok
    assert cond, label
    ok += 1
    print("  ok:", label)


def rand_ring(n, scale=100.0, star=False, close=False):
    if star:
        a = np.sort(rng.uniform(0, 2 * math.pi, n))
        rad = rng.uniform(0.2, 1.0, n) * scale
        r = np.column_stack([rad * np.cos(a), rad * np.sin(a)])
        r += rng.uniform(-scale, scale, 2)
    else:
        r = rng.uniform(-scale, scale, size=(n, 2))
    if close:
        r = np.vstack([r, r[:1]])
    return r


# ---- 1. clip equivalence ----------------------------------------------
cases = 0
t_ref = t_vec = 0.0
for rep in range(1500):
    kind = rep % 5
    if kind == 0:
        rings = [rand_ring(rng.integers(3, 12))]
    elif kind == 1:
        rings = [rand_ring(rng.integers(20, 200), star=True,
                           close=True)]
    elif kind == 2:                    # outer + hole (both stars)
        outer = rand_ring(60, star=True, close=True)
        rings = [outer, outer[::-1] * 0.4]
    elif kind == 3:                    # sliver triangle
        rings = [np.array([[0, 0], [100, 0],
                           [100, rng.uniform(1e-9, 1e-4)]])]
    else:                              # axis-aligned square w/
        s = rng.uniform(1, 50)         # collinear points
        rings = [np.array([[0, 0], [s / 2, 0], [s, 0], [s, s],
                           [0, s], [0, 0]])]
    for _ in range(4):
        cx = rng.uniform(-120, 120)
        cy = rng.uniform(-120, 120)
        w = rng.uniform(1e-3, 150)
        h = rng.uniform(1e-3, 150)
        t0 = time.time()
        a_ref = G._clip_polygon_rect_ref(rings, cx, cy, cx + w,
                                         cy + h)
        t_ref += time.time() - t0
        t0 = time.time()
        a_vec = G._clip_polygon_rect(rings, cx, cy, cx + w, cy + h)
        t_vec += time.time() - t0
        assert a_vec == a_ref, (rep, kind, a_vec, a_ref)
        cases += 1
# whole-cell and empty-cell sanity on each fixture family
sq = [np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)]
assert G._clip_polygon_rect(sq, -5, -5, 15, 15) == \
    G._clip_polygon_rect_ref(sq, -5, -5, 15, 15) == 100.0
assert G._clip_polygon_rect(sq, 20, 20, 30, 30) == 0.0
check(True, "clip: {} randomized cases bit-identical to the "
            "scalar reference".format(cases))

big = [rand_ring(10000, star=True, close=True)]
t0 = time.time()
for _ in range(200):
    G._clip_polygon_rect_ref(big, -20, -20, 60, 60)
ref_s = time.time() - t0
t0 = time.time()
for _ in range(200):
    G._clip_polygon_rect(big, -20, -20, 60, 60)
vec_s = time.time() - t0
check(vec_s < ref_s / 5,
      "clip: 10k-vertex ring {:.1f}x faster ({:.2f}s -> {:.2f}s "
      "per 200 clips)".format(ref_s / max(vec_s, 1e-9), ref_s,
                              vec_s))

# ---- 2. points_in equivalence -----------------------------------------
for rep in range(30):
    outer = rand_ring(rng.integers(10, 300), star=True, close=True)
    rings = [outer]
    if rep % 2:
        rings.append(outer[::-1] * rng.uniform(0.2, 0.6))
    m = G.ArealMeasure(rings)
    pts = rng.uniform(-130, 130, size=(3000, 2))
    batch = m.points_in(pts)
    scalar = np.array([m.point_in(x, y) for x, y in pts])
    assert (batch == scalar).all(), rep
check(True, "points_in: 90,000 point tests identical to the "
            "scalar loop (with and without holes)")

# sliver frame: batch rejection must collect its test points fast
# (the scalar dart loop measured ~9 ms per point_in call chain and
# minutes overall on frames like this)
sliver = G.ArealMeasure([np.array([[0, 0], [100000, 0],
                                   [100000, 5], [0, 5], [0, 0]],
                                  float)])
lo = np.array([0.0, -5000.0])
hi = np.array([100000.0, 5000.0])     # area = 1/2000 of the box
r2 = np.random.default_rng(1)
t0 = time.time()
got = 0
while got < 4000:
    cand = r2.uniform(lo, hi, size=(200000, 2))
    got += int(sliver.points_in(cand).sum())
dt = time.time() - t0
check(dt < 10, "sliver frame: 4000 test points collected by batch "
               "rejection in {:.1f}s (scalar loop: minutes)"
               .format(dt))

# ---- 3. kappa polygon index equivalence (arcpy) -----------------------
import tempfile  # noqa: E402
import shutil  # noqa: E402
import arcpy  # noqa: E402
import accuracy_assessment as AA  # noqa: E402

tmp = tempfile.mkdtemp(prefix="speedeq_")
gdb = arcpy.management.CreateFileGDB(tmp, "t.gdb").getOutput(0)
sr = arcpy.SpatialReference(26912)
fc = arcpy.management.CreateFeatureclass(
    gdb, "cells", "POLYGON", spatial_reference=sr).getOutput(0)
arcpy.management.AddField(fc, "Cls", "TEXT", field_length=16)
classes = ["A", "B", "C", "D"]
with arcpy.da.InsertCursor(fc, ["SHAPE@", "Cls"]) as ins:
    for gy in range(25):
        for gx in range(25):
            x0 = 500000 + gx * 400.0
            y0 = 3600000 + gy * 400.0
            ins.insertRow([arcpy.Polygon(arcpy.Array(
                [arcpy.Point(x0, y0), arcpy.Point(x0 + 400, y0),
                 arcpy.Point(x0 + 400, y0 + 400),
                 arcpy.Point(x0, y0 + 400),
                 arcpy.Point(x0, y0)]), sr),
                classes[(gx + gy) % 4]])
probe = np.column_stack([
    rng.uniform(499800, 510200, 800),
    rng.uniform(3599800, 3610200, 800)])     # includes outside pts

vals_idx = AA._extract_from_polygons(probe, fc, "Cls", None, None)
t0 = time.time()
vals_idx_r = AA._extract_from_polygons(probe, fc, "Cls", 150.0,
                                       None)
t_idx = time.time() - t0
AA._POLY_INDEX_MIN = 10 ** 9                 # disable the index
vals_lin = AA._extract_from_polygons(probe, fc, "Cls", None, None)
t0 = time.time()
vals_lin_r = AA._extract_from_polygons(probe, fc, "Cls", 150.0,
                                       None)
t_lin = time.time() - t0
check(vals_idx == vals_lin,
      "kappa index: 800 point extractions identical to the linear "
      "scan (625 polygons)")
check(vals_idx_r == vals_lin_r,
      "kappa index: majority-circle extractions identical "
      "(tie-breaking preserved); {:.1f}x faster ({:.2f}s -> "
      "{:.2f}s)".format(t_lin / max(t_idx, 1e-9), t_lin, t_idx))

try:
    arcpy.management.Delete(gdb)
except Exception:                                        # noqa: BLE001
    pass
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK ({} checks)".format(ok))
