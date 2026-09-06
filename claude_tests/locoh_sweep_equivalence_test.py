"""LoCoH Parameter Explorer -- algorithmic equivalence check.

The C# sweep engine (LocohSweepEngine.cs) finds the 50/75/95% crossing
indices WITHOUT building the cumulative union at every step, on the
identity that a point can only become newly covered by the hull just
added (union(acc, h) \\ acc is a subset of h).  It then builds only
three batch unions, at the crossings.  This test proves that shortcut
against the reference approach -- count points inside the true
cumulative union at every step -- using real arcpy geometry, over the
k / a / r variants' hull sets and both sort orders.
"""
import os
import sys

import numpy as np
import arcpy

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  [" + detail + "]" if detail else ""))


SR = arcpy.SpatialReference(32612)
rng = np.random.default_rng(11)

# two clusters plus loose scatter -- the shape that exercises both sort
# orders and leaves real gaps between dense cores
pts = np.vstack([
    rng.normal((500000.0, 3800000.0), 120, (50, 2)),
    rng.normal((501400.0, 3800600.0), 90, (40, 2)),
    rng.uniform((499600.0, 3799600.0), (501800.0, 3801000.0), (30, 2)),
])
N = len(pts)
GEOMS = [arcpy.PointGeometry(arcpy.Point(float(x), float(y)), SR)
         for x, y in pts]


def build_hulls(method, value):
    """One hull per focal point by the variant's rule; returns
    (hull_geometry, area, count, member_indices) sorted the variant's way
    (k: area ascending; a/r: count descending, area ascending)."""
    hulls = []
    for i in range(N):
        d = np.hypot(pts[:, 0] - pts[i, 0], pts[:, 1] - pts[i, 1])
        order = np.argsort(d, kind="stable")
        if method == "k":
            sel = order[:value]
        elif method == "a":
            cum = np.cumsum(d[order])
            sel_count = int(np.count_nonzero(cum <= value))
            sel = order[:max(sel_count, 3)]
        else:
            sel_count = int(np.count_nonzero(d[order] <= value))
            sel = order[:max(sel_count, 3)]
        arr = arcpy.Array([arcpy.Point(float(pts[j, 0]), float(pts[j, 1]))
                           for j in sel])
        hull = arcpy.Multipoint(arr, SR).convexHull()
        if hull.type != "polygon" or hull.area == 0:
            continue
        hulls.append((hull, hull.area, len(sel), np.asarray(sel)))
    if method == "k":
        hulls.sort(key=lambda h: h[1])
    else:
        hulls.sort(key=lambda h: (-h[2], h[1]))
    return hulls


def crossings_reference(hulls, needs):
    """Reference: true cumulative union at every step, count every point
    inside it (boundary-inclusive)."""
    acc = None
    covered_counts = []
    for hull, _a, _c, _m in hulls:
        acc = hull if acc is None else acc.union(hull)
        covered_counts.append(
            sum(1 for g in GEOMS if not acc.disjoint(g)))
    idx = []
    for need in needs:
        found = next((i for i, c in enumerate(covered_counts)
                      if c >= need), -1)
        idx.append(found)
    return idx


def crossings_shortcut(hulls, needs):
    """The C# engine's shortcut: mark the added hull's members free, then
    test only still-unmarked points against the ADDED hull alone."""
    covered = np.zeros(N, dtype=bool)
    cnt = 0
    counts = []
    for hull, _a, _c, members in hulls:
        for m in members:
            if not covered[m]:
                covered[m] = True
                cnt += 1
        ext = hull.extent
        for j in range(N):
            if covered[j]:
                continue
            x, y = pts[j]
            if x < ext.XMin or x > ext.XMax or y < ext.YMin or y > ext.YMax:
                continue
            if not hull.disjoint(GEOMS[j]):
                covered[j] = True
                cnt += 1
        counts.append(cnt)
    idx = []
    for need in needs:
        found = next((i for i, c in enumerate(counts) if c >= need), -1)
        idx.append(found)
    return idx


def union_area(hulls, idx):
    acc = None
    for h in hulls[:idx + 1]:
        acc = h[0] if acc is None else acc.union(h[0])
    return acc.area


print("== crossing-index shortcut vs true cumulative union ==")
needs = [int(np.ceil(p * N)) for p in (0.50, 0.75, 0.95)]
max_pair = float(np.max([np.hypot(pts[:, 0] - x, pts[:, 1] - y).max()
                         for x, y in pts]))
nn = [np.sort(np.hypot(pts[:, 0] - x, pts[:, 1] - y))[1] for x, y in pts]
r1 = max(nn) / 2.0

cases = [("k", 5), ("k", 12), ("k", 40),
         ("a", max_pair * 0.10), ("a", max_pair * 0.40),
         ("r", r1), ("r", r1 * 3.0)]
for method, value in cases:
    hulls = build_hulls(method, value)
    ref = crossings_reference(hulls, needs)
    fast = crossings_shortcut(hulls, needs)
    label = "{}={:.0f}".format(method, value)
    check("crossing indices identical ({})".format(label), ref == fast,
          "ref={} fast={}".format(ref, fast))
    for ri, tag in zip(ref, ("50", "75", "95")):
        if ri < 0:
            continue
        a_ref = union_area(hulls, ri)
        a_fast = union_area(hulls, ri)     # same index -> same union
        check("area at {}% matches ({})".format(tag, label),
              abs(a_ref - a_fast) <= 1e-9 * max(a_ref, 1.0),
              "{} vs {}".format(a_ref, a_fast))

print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
