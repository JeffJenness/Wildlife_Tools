# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Adaptive Voronoi Polygons (adaptive_voronoi.py). The checks are the
properties a centroidal Voronoi tessellation must have, each worked out here
without the engine: the cells cover the boundary exactly once; every place
belongs to the cell of its nearest center; every center sits at the middle of
its own cell. One step of the method is also compared with a plain count of
which of 1,000,000 small squares is nearest to which starting point.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" adaptive_voronoi_test.py
"""
import math
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import adaptive_voronoi as V  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


def raises(name, fn, has):
    try:
        fn()
        check(name, False, "no error was raised")
    except arcpy.ExecuteError as e:
        check(name, has in str(e), str(e))
    except Exception as e:                                            # noqa: BLE001
        check(name, False, "wrong kind of error: %r" % e)


class Msgs:
    def __init__(self):
        self.lines, self.warnings = [], []

    def addMessage(self, s):
        self.lines.append(s)

    def addWarningMessage(self, s):
        self.warnings.append(s)

    def has(self, text):
        return any(text in s for s in self.lines)


arcpy.env.overwriteOutput = True
work = tempfile.mkdtemp(prefix="adaptive_voronoi_test_")
gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
UTM = 26912
X0, Y0, SIDE = 500000.0, 3900000.0, 10000.0          # a square 10 km on a side: 100,000,000 sq m


def box(x, y, w, h):
    return [[x, y], [x, y + h], [x + w, y + h], [x + w, y], [x, y]]            # clockwise: an outer ring


def hole(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]            # counterclockwise: a hole


def poly(*rings, wkid=UTM):
    return arcpy.AsShape({"rings": list(rings), "spatialReference": {"wkid": wkid}}, True)


def polygons(name, geoms, wkid=UTM):
    fc = arcpy.management.CreateFeatureclass(gdb, name, "POLYGON", spatial_reference=arcpy.SpatialReference(wkid))[0]
    with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as c:
        for g in geoms:
            c.insertRow((g,))
    return fc


def points(name, xy, wkid=UTM):
    fc = arcpy.management.CreateFeatureclass(gdb, name, "POINT", spatial_reference=arcpy.SpatialReference(wkid))[0]
    with arcpy.da.InsertCursor(fc, ["SHAPE@XY"]) as c:
        for p in xy:
            c.insertRow((p,))
    return fc, [r[0] for r in arcpy.da.SearchCursor(fc, ["OID@"])]


def read_cells(fc):
    """{Cell_ID: (shape, Area_Ha, Seed_FID or None)}"""
    seed = "Seed_FID" in [f.name for f in arcpy.ListFields(fc)]
    out = {}
    for r in arcpy.da.SearchCursor(fc, ["Cell_ID", "SHAPE@", "Area_Ha"] + (["Seed_FID"] if seed else [])):
        out[r[0]] = (r[1], r[2], r[3] if seed else None)
    return out


def read_points(fc):
    """{Cell_ID: (x, y, Seed_FID or None)}"""
    seed = "Seed_FID" in [f.name for f in arcpy.ListFields(fc)]
    out = {}
    for r in arcpy.da.SearchCursor(fc, ["Cell_ID", "SHAPE@XY"] + (["Seed_FID"] if seed else [])):
        out[r[0]] = (r[1][0], r[1][1], r[2] if seed else None)
    return out


def union_of(geoms):
    u = geoms[0]
    for g in geoms[1:]:
        u = u.union(g)
    return u


def cv(values):
    a = np.asarray(values, dtype=float)
    return float(a.std() / a.mean())


def tiling(cells, boundary_geom):
    """(sum of the cells' areas, area of their union, area lying outside the boundary)"""
    geoms = [c[0] for c in cells.values()]
    u = union_of(geoms)
    return sum(g.area for g in geoms), u.area, u.difference(boundary_geom).area


def nearest_center_agrees(cells, pts, xs, ys):
    """Of the places (xs, ys): how many lie in the cell of their NEAREST center. Places within a millimeter of
    being as near to a second center are passed over."""
    ids = sorted(pts)
    cx = np.array([pts[i][0] for i in ids])
    cy = np.array([pts[i][1] for i in ids])
    good = bad = 0
    sr = next(iter(cells.values()))[0].spatialReference
    for x, y in zip(xs, ys):
        d = np.hypot(cx - x, cy - y)
        order = np.argsort(d)
        if d[order[1]] - d[order[0]] < 0.001:
            continue
        p = arcpy.PointGeometry(arcpy.Point(float(x), float(y)), sr)
        if cells[ids[order[0]]][0].contains(p):
            good += 1
        else:
            bad += 1
    return good, bad


def grid_voronoi(seeds, n=1000):
    """Area and middle of each seed's share of the 10 km square, by counting which of n by n small squares is
    nearest to which seed. Nothing here comes from the engine."""
    seeds = np.asarray(seeds, dtype=float)
    step = SIDE / n
    xs = X0 + (np.arange(n) + 0.5) * step
    cnt = np.zeros(len(seeds))
    sx = np.zeros(len(seeds))
    sy = np.zeros(len(seeds))
    for j in range(n):
        y = Y0 + (j + 0.5) * step
        k = ((xs[:, None] - seeds[None, :, 0]) ** 2 + (y - seeds[None, :, 1]) ** 2).argmin(axis=1)
        c = np.bincount(k, minlength=len(seeds))
        cnt += c
        sx += np.bincount(k, weights=xs, minlength=len(seeds))
        sy += c * y
    with np.errstate(invalid="ignore", divide="ignore"):
        return cnt * step * step, sx / cnt, sy / cnt


# ---- 1. the arithmetic of areas and middles
a, cx, cy = V._ring_area_centroid([(0, 0), (4, 0), (4, 2), (0, 2)])
check("ring: a 4 by 2 rectangle has area 8 and its middle at (2, 1)", (abs(a), cx, cy) == (8.0, 2.0, 1.0), (a, cx, cy))
a2, cx2, cy2 = V._ring_area_centroid([(0, 0), (4, 0), (4, 2), (0, 2), (0, 0)])
check("ring: the same whether or not the ring is closed", (a2, cx2, cy2) == (a, cx, cy), (a2, cx2, cy2))
a3, cx3, cy3 = V._ring_area_centroid([(0, 0), (0, 2), (4, 2), (4, 0)])
check("ring: drawn the other way round, the area changes sign and the middle does not",
      (a3, cx3, cy3) == (-a, cx, cy), (a3, cx3, cy3))
a, cx, cy = V._ring_area_centroid([(0, 0), (6, 0), (0, 3)])
check("ring: a triangle's middle is the mean of its corners", abs(abs(a) - 9.0) < 1e-12 and abs(cx - 2.0) < 1e-12
      and abs(cy - 1.0) < 1e-12, (a, cx, cy))
# an L: a 2 by 2 square with its upper right 1 by 1 removed. Area 3; middle at (5/6, 5/6).
a, cx, cy = V._ring_area_centroid([(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)])
check("ring: an L shape", abs(abs(a) - 3.0) < 1e-12 and abs(cx - 5.0 / 6.0) < 1e-12 and abs(cy - 5.0 / 6.0) < 1e-12,
      (a, cx, cy))
check("ring: fewer than three corners is no area", V._ring_area_centroid([(0, 0), (1, 1)])[0] == 0.0)
far = [(X0 + x, Y0 + y) for x, y in ((0, 0), (4, 0), (4, 2), (0, 2))]
a, cx, cy = V._ring_area_centroid(far)
check("ring: no accuracy is lost at map coordinates in the millions",
      abs(abs(a) - 8.0) < 1e-6 and abs(cx - (X0 + 2)) < 1e-6 and abs(cy - (Y0 + 1)) < 1e-6, (a, cx - X0, cy - Y0))
# a 10 by 10 square with a 2 by 2 hole at its lower left corner region (1..3): area 96;
# middle x = (100 * 5 - 4 * 2) / 96
A, cx, cy = V._rings_area_centroid([np.array([(0, 0), (0, 10), (10, 10), (10, 0)]),
                                    np.array([(1, 1), (3, 1), (3, 3), (1, 3)])])
check("rings: a hole takes its area away and shifts the middle",
      abs(abs(A) - 96.0) < 1e-12 and abs(cx - 492.0 / 96.0) < 1e-12 and abs(cy - 492.0 / 96.0) < 1e-12, (A, cx, cy))
check("rings: nothing at all has no middle", V._rings_area_centroid([]) == (0.0, None, None))
holed = poly(box(X0, Y0, 10, 10), hole(X0 + 1, Y0 + 1, 2, 2), box(X0 + 20, Y0, 5, 5))
A, cx, cy = V._rings_area_centroid(V._geom_rings(holed))
tc = holed.trueCentroid
check("rings: a real polygon with a hole and a second part measures as ArcGIS measures it",
      abs(abs(A) - holed.area) < 1e-6 and abs(cx - tc.X) < 1e-6 and abs(cy - tc.Y) < 1e-6,
      (A, holed.area, cx - tc.X, cy - tc.Y))

# ---- 2. random starting points in a square, run until nothing moves
SQUARE_G = poly(box(X0, Y0, SIDE, SIDE))
SQUARE = polygons("square", [SQUARE_G])
m = Msgs()
out = os.path.join(gdb, "cvt25")
outp = os.path.join(gdb, "cvt25_pts")
ret = V.run_adaptive_voronoi(SQUARE, out, n_random=25, iterations=5000, out_points=outp, random_seed=42, messages=m)
cells, pts = read_cells(out), read_points(outp)
check("square: the output path is returned", ret == out, ret)
check("square: 25 cells and 25 centers, numbered 1 to 25 alike",
      sorted(cells) == list(range(1, 26)) and sorted(pts) == list(range(1, 26)), (sorted(cells), sorted(pts)))
check("square: the run stopped because nothing moved any more, and says so",
      any(s.startswith("Stopped after ") and "(converged (no centroid moved more than the tolerance)). 25 of 25 seed "
          "points survive." in s for s in m.lines), [s for s in m.lines if s.startswith("Stopped")])
check("square: it says which engine was used and how many points it placed",
      m.has("Adaptive Voronoi (Lloyd's algorithm), planar engine.")
      and m.has("25 random seed points placed inside the boundary (seed 42)"), m.lines[:3])
total, united, outside = tiling(cells, SQUARE_G)
check("square: the cells' areas add up to the square (100,000,000 sq m)", abs(total - SIDE * SIDE) < 100.0,
      total - SIDE * SIDE)
check("square: no two cells overlap (their union is as large as their sum)", abs(united - total) < 100.0, united - total)
check("square: no cell reaches outside the square", outside < 1.0, outside)
check("square: random starting points have no source, so there is no Seed_FID field",
      "Seed_FID" not in [f.name for f in arcpy.ListFields(out)]
      and "Seed_FID" not in [f.name for f in arcpy.ListFields(outp)])
inside_own = sum(1 for i in cells if cells[i][0].contains(arcpy.PointGeometry(arcpy.Point(pts[i][0], pts[i][1]),
                                                                              arcpy.SpatialReference(UTM))))
check("square: every center lies in the cell of the same number", inside_own == 25, inside_own)
rng = np.random.default_rng(2026)
good, bad = nearest_center_agrees(cells, pts, X0 + rng.uniform(0, SIDE, 600), Y0 + rng.uniform(0, SIDE, 600))
check("square: every one of 600 random places lies in the cell of its nearest center", bad == 0 and good > 590,
      (good, bad))
# the tolerance by default is 0.05 percent of the mean cell's width: sqrt(1e8 / 25) = 2,000 m, so 1 m
off = max(math.hypot(pts[i][0] - cells[i][0].trueCentroid.X, pts[i][1] - cells[i][0].trueCentroid.Y) for i in cells)
check("square: every center is within 3 m of the middle of its own cell (the tolerance is 1 m)", off < 3.0, off)
ha = [cells[i][1] for i in cells]
check("square: Area_Ha is each cell's area in hectares (within 0.2 percent of its area on the map)",
      all(abs(cells[i][1] - cells[i][0].area / 1e4) < 0.002 * cells[i][0].area / 1e4 for i in cells),
      [(cells[i][1], cells[i][0].area / 1e4) for i in list(cells)[:3]])
check("square: the areas reported in the messages are the areas of the cells",
      m.has("Cell areas: mean {:,.2f} ha, min {:,.2f}, max {:,.2f}; coefficient of variation {:.3f}".format(
          np.mean(ha), np.min(ha), np.max(ha), cv(ha))), [s for s in m.lines if s.startswith("Cell areas")])

# one step only, from the same starting points: the cells are far less even than when it has run its course
m1 = Msgs()
out1 = os.path.join(gdb, "cvt25_one")
V.run_adaptive_voronoi(SQUARE, out1, n_random=25, iterations=1, random_seed=42, messages=m1)
cv1, cv_end = cv([c[0].area for c in read_cells(out1).values()]), cv([c[0].area for c in cells.values()])
check("square: after one step the run says it stopped at the limit",
      m1.has("Stopped after 1 iteration (iteration limit). 25 of 25 seed points survive."),
      [s for s in m1.lines if s.startswith("Stopped")])
check("square: run to the end, the cells are much more nearly equal in area than after one step",
      cv_end < 0.5 * cv1, (cv_end, cv1))

# the same random seed gives the same answer; another seed, or none, gives another
out2, outp2 = os.path.join(gdb, "cvt25_again"), os.path.join(gdb, "cvt25_again_pts")
V.run_adaptive_voronoi(SQUARE, out2, n_random=25, iterations=5000, out_points=outp2, random_seed=42)
check("random seed: the same seed gives the same centers", read_points(outp2) == pts)
V.run_adaptive_voronoi(SQUARE, out2, n_random=25, iterations=3, out_points=outp2, random_seed=43)
p43 = read_points(outp2)
V.run_adaptive_voronoi(SQUARE, out2, n_random=25, iterations=3, out_points=outp2, random_seed=42)
p42 = read_points(outp2)
mn = Msgs()
V.run_adaptive_voronoi(SQUARE, out2, n_random=25, iterations=3, out_points=outp2, messages=mn)
check("random seed: another seed gives other centers", p43 != p42)
check("random seed: with no seed the centers differ again, and the messages name no seed",
      read_points(outp2) != p42 and mn.has("25 random seed points placed inside the boundary.")
      and not mn.has("(seed"), [s for s in mn.lines if "random seed points" in s])

# a looser tolerance stops sooner
mt = Msgs()
V.run_adaptive_voronoi(SQUARE, out2, n_random=25, iterations=5000, tolerance=50.0, random_seed=42, messages=mt)


def steps_of(msgs):
    line = [s for s in msgs.lines if s.startswith("Stopped after ")][0]
    return int(line.split()[2])


check("tolerance: 50 m stops in fewer steps than the 1 m of the default, with all 25 cells",
      steps_of(mt) < steps_of(m) and len(read_cells(out2)) == 25 and "converged" in
      [s for s in mt.lines if s.startswith("Stopped")][0], (steps_of(mt), steps_of(m)))

# ---- 3. starting points from a feature class; one step, checked against the count of small squares
SEEDS = [(501000.0, 3901500.0), (503500.0, 3900800.0), (507800.0, 3901200.0), (509000.0, 3904500.0),
         (506200.0, 3906000.0), (502200.0, 3907800.0), (504800.0, 3903600.0), (508300.0, 3908800.0)]
PTS, OIDS = points("seeds", SEEDS)
g_area, g_cx, g_cy = grid_voronoi(SEEDS)
m = Msgs()
out, outp = os.path.join(gdb, "from_pts"), os.path.join(gdb, "from_pts_centers")
V.run_adaptive_voronoi(SQUARE, out, in_points=PTS, iterations=1, out_points=outp, messages=m)
cells, pts = read_cells(out), read_points(outp)
check("points: one cell for each starting point, each naming the point it grew from",
      sorted(c[2] for c in cells.values()) == sorted(OIDS) and sorted(p[2] for p in pts.values()) == sorted(OIDS),
      ([c[2] for c in cells.values()], OIDS))
check("points: a cell and its center name the same starting point", all(cells[i][2] == pts[i][2] for i in cells))
by_seed = {p[2]: (p[0], p[1]) for p in pts.values()}
worst = max(math.hypot(by_seed[oid][0] - g_cx[k], by_seed[oid][1] - g_cy[k]) for k, oid in enumerate(OIDS))
check("points: after one step each center is at the middle of its starting point's share of the square "
      "(within 5 m of a count of 1,000,000 small squares)", worst < 5.0, worst)
line1 = [s for s in m.lines if s.startswith("  iteration    1:")]
check("points: the first step reports how uneven the starting points' shares were",
      len(line1) == 1 and line1[0].startswith("  iteration    1: 8 cells, area CV ")
      and abs(float(line1[0].rsplit(" ", 1)[1]) - cv(g_area)) < 0.003, (line1, cv(g_area)))
check("points: the same figure is given again at the end as that of the first iteration",
      any(s.endswith("(first iteration {}).".format(line1[0].rsplit(" ", 1)[1])) for s in m.lines) if line1 else False,
      [s for s in m.lines if s.startswith("Cell areas")])
total, united, outside = tiling(cells, SQUARE_G)
check("points: the cells cover the square once", abs(total - SIDE * SIDE) < 100.0 and abs(united - total) < 100.0
      and outside < 1.0, (total - SIDE * SIDE, united - total, outside))
desc = arcpy.metadata.Metadata(out).description or ""
check("points: the output's description says how it was made",
      "generated within polygon features from square, using source points from seeds over 1 iteration "
      "(iteration limit)." in desc and "Lloyd, S. 1982" in desc, desc[-260:])
check("points: the starting points themselves are not moved",
      [r[0] for r in arcpy.da.SearchCursor(PTS, ["SHAPE@XY"])] == SEEDS)

# a time limit that has already passed: no step is taken, and the cells are those of the starting points
m = Msgs()
V.run_adaptive_voronoi(SQUARE, out, in_points=PTS, iterations=50, time_limit_min=1e-9, out_points=outp, messages=m)
cells, pts = read_cells(out), read_points(outp)
check("time limit: reached before the first step, the run still writes the cells and says why it stopped",
      len(cells) == 8 and m.has("Stopped after 0 iterations (time limit). 8 of 8 seed points survive."),
      [s for s in m.lines if s.startswith("Stopped")])
by_seed = {p[2]: (p[0], p[1]) for p in pts.values()}
check("time limit: with no step taken the centers are the starting points",
      all(math.hypot(by_seed[oid][0] - SEEDS[k][0], by_seed[oid][1] - SEEDS[k][1]) < 0.001
          for k, oid in enumerate(OIDS)), by_seed)
areas_by_seed = {c[2]: c[0].area for c in cells.values()}
check("time limit: and the cells are the starting points' shares (within 0.2 percent of the count of small squares)",
      all(abs(areas_by_seed[oid] - g_area[k]) < 0.002 * g_area[k] for k, oid in enumerate(OIDS)),
      [(areas_by_seed[oid], g_area[k]) for k, oid in enumerate(OIDS)])

# a starting point just outside the boundary takes part; one far away, whose share misses the boundary, is dropped
PTS2, OIDS2 = points("seeds_out", SEEDS + [(510100.0, 3905000.0), (600000.0, 3905000.0)])
m = Msgs()
V.run_adaptive_voronoi(SQUARE, out, in_points=PTS2, iterations=20, out_points=outp, messages=m)
cells, pts = read_cells(out), read_points(outp)
check("outside points: the far one is dropped and the near one kept (9 of 10)",
      sorted(c[2] for c in cells.values()) == sorted(OIDS2[:9])
      and m.has("9 of 10 seed points survive."), ([c[2] for c in cells.values()], m.lines[-2:]))
near_pt = [p for p in pts.values() if p[2] == OIDS2[8]]
check("outside points: the near one's center has moved inside the square",
      len(near_pt) == 1 and X0 < near_pt[0][0] < X0 + SIDE and Y0 < near_pt[0][1] < Y0 + SIDE, near_pt)
total, united, outside = tiling(cells, SQUARE_G)
check("outside points: the nine cells still cover the square once",
      abs(total - SIDE * SIDE) < 100.0 and abs(united - total) < 100.0 and outside < 1.0,
      (total - SIDE * SIDE, united - total, outside))
check("outside points: cells and centers are numbered alike, 1 to 9, and name the same starting points",
      sorted(cells) == sorted(pts) == list(range(1, 10)) and all(cells[i][2] == pts[i][2] for i in cells))
# two starting points, both far away: the nearer one's share holds the whole boundary, the other's holds none
FARPTS, FAROIDS = points("seeds_far", [(600000.0, 3905000.0), (600500.0, 3905000.0)])
m = Msgs()
try:
    V.run_adaptive_voronoi(SQUARE, out, in_points=FARPTS, iterations=5, out_points=outp, messages=m)
    cells, pts = read_cells(out), read_points(outp)
    only = list(cells.values())
    check("two far points: one cell, the whole square, grown from the nearer point",
          len(only) == 1 and abs(only[0][0].area - SIDE * SIDE) < 100.0 and only[0][2] == FAROIDS[0]
          and m.has("1 of 2 seed points survive."), ([(c[0].area, c[2]) for c in only], m.lines[-2:]))
    check("two far points: its center ends at the middle of the square",
          len(pts) == 1 and math.hypot(pts[1][0] - (X0 + SIDE / 2), pts[1][1] - (Y0 + SIDE / 2)) < 1.0, pts)
except Exception as e:                                                # noqa: BLE001
    check("two far points: one cell, the whole square, grown from the nearer point", False, repr(e))

# the smallest numbers of random points
for n in (1, 2, 3):
    m = Msgs()
    try:
        V.run_adaptive_voronoi(SQUARE, out, n_random=n, iterations=200, out_points=outp, random_seed=11, messages=m)
        cells, pts = read_cells(out), read_points(outp)
        total, united, outside = tiling(cells, SQUARE_G)
        check("%d random point%s: %d cell%s that cover the square once" % (n, "" if n == 1 else "s", n,
                                                                           "" if n == 1 else "s"),
              len(cells) == n and abs(total - SIDE * SIDE) < 100.0 and abs(united - total) < 100.0 and outside < 1.0,
              (len(cells), total - SIDE * SIDE, united - total, outside))
        if n == 1:
            check("1 random point: its center ends at the middle of the square",
                  math.hypot(pts[1][0] - (X0 + SIDE / 2), pts[1][1] - (Y0 + SIDE / 2)) < 1.0, pts)
        if n == 2:
            check("2 random points: the square is cut into two equal halves (within 0.1 percent)",
                  abs(cells[1][0].area - cells[2][0].area) < 0.001 * SIDE * SIDE / 2,
                  (cells[1][0].area, cells[2][0].area))
    except Exception as e:                                            # noqa: BLE001
        check("%d random point%s: runs" % (n, "" if n == 1 else "s"), False, repr(e))

# ---- 4. other boundaries
# a square with a square hole: 100,000,000 less 16,000,000
DONUT_G = poly(box(X0, Y0, SIDE, SIDE), hole(X0 + 3000, Y0 + 3000, 4000, 4000))
HOLE_G = poly(box(X0 + 3000, Y0 + 3000, 4000, 4000))
m = Msgs()
V.run_adaptive_voronoi(polygons("donut", [DONUT_G]), out, n_random=30, iterations=40, out_points=outp, random_seed=5,
                       messages=m)
cells, pts = read_cells(out), read_points(outp)
total, united, outside = tiling(cells, DONUT_G)
in_hole = union_of([c[0] for c in cells.values()]).intersect(HOLE_G, 4).area
check("hole in the boundary: the cells cover the ring once (84,000,000 sq m) and none of the hole",
      abs(total - 84e6) < 100.0 and abs(united - total) < 100.0 and outside < 1.0 and in_hole < 1.0,
      (total - 84e6, united - total, outside, in_hole))
check("hole in the boundary: all 30 survive, cells and centers numbered alike",
      sorted(cells) == sorted(pts) == list(range(1, 31)) and m.has("30 of 30 seed points survive."), len(cells))

# an L, which is not convex: 100,000,000 less a 6 km by 6 km corner
L_G = poly([[X0, Y0], [X0, Y0 + SIDE], [X0 + 4000, Y0 + SIDE], [X0 + 4000, Y0 + 4000], [X0 + SIDE, Y0 + 4000],
            [X0 + SIDE, Y0], [X0, Y0]])
V.run_adaptive_voronoi(polygons("ell", [L_G]), out, n_random=20, iterations=40, random_seed=6)
cells = read_cells(out)
total, united, outside = tiling(cells, L_G)
check("L-shaped boundary: the cells cover it once (64,000,000 sq m)",
      abs(total - 64e6) < 100.0 and abs(united - total) < 100.0 and outside < 1.0,
      (total - 64e6, united - total, outside))

# two separate squares as two features of one boundary layer; then with only one of them selected
TWO = polygons("two_squares", [poly(box(X0, Y0, 4000, 4000)), poly(box(X0 + 6000, Y0, 4000, 4000))])
V.run_adaptive_voronoi(TWO, out, n_random=16, iterations=30, random_seed=7)
cells = read_cells(out)
total = sum(c[0].area for c in cells.values())
check("two boundary polygons: both are filled (32,000,000 sq m)", abs(total - 32e6) < 100.0, total - 32e6)
lyr = arcpy.management.MakeFeatureLayer(TWO, "av_two_lyr")[0]
arcpy.management.SelectLayerByLocation(lyr, "INTERSECT", arcpy.PointGeometry(arcpy.Point(X0 + 8000, Y0 + 2000),
                                                                             arcpy.SpatialReference(UTM)))
m = Msgs()
V.run_adaptive_voronoi(lyr, out, n_random=16, iterations=30, random_seed=7, messages=m)
cells = read_cells(out)
total = sum(c[0].area for c in cells.values())
xmin = min(c[0].extent.XMin for c in cells.values())
check("selection on the boundary: only the selected polygon is filled (16,000,000 sq m, all east of x = 506,000)",
      abs(total - 16e6) < 100.0 and xmin > X0 + 5999.0 and len(cells) == 16
      and m.has("The boundary layer has an active selection: 1 of 2 polygons are used."), (total, xmin, len(cells)))
arcpy.management.Delete(lyr)

# a boundary drawn as a true circle of radius 3 km (as a geodatabase buffer is)
CIRCLE_G = arcpy.AsShape({"curveRings": [[[X0 + 8000, Y0 + 5000], {"a": [[X0 + 8000, Y0 + 5000],
                                                                         [X0 + 5000, Y0 + 5000], 0, 1]}]],
                          "spatialReference": {"wkid": UTM}}, True)
circle_area = math.pi * 3000.0 ** 2
check("setup: the circle is a true curve of the right size", CIRCLE_G.hasCurves and abs(CIRCLE_G.area - circle_area) < 1.0,
      CIRCLE_G.area)
try:
    V.run_adaptive_voronoi(polygons("circle", [CIRCLE_G]), out, n_random=12, iterations=30, random_seed=8)
    cells = read_cells(out)
    total = sum(c[0].area for c in cells.values())
    check("circular boundary: 12 cells that fill the circle (within 0.1 percent of its area)",
          len(cells) == 12 and abs(total - circle_area) < 0.001 * circle_area, (len(cells), total / circle_area))
except Exception as e:                                                # noqa: BLE001
    check("circular boundary: 12 cells that fill the circle (within 0.1 percent of its area)", False, repr(e))

# ---- 5. latitude and longitude: one degree on a side at 35 N
GEO_G = poly(box(-112.0, 35.0, 1.0, 1.0), wkid=4326)
GEO = polygons("degrees", [GEO_G], wkid=4326)
m = Msgs()
out, outp = os.path.join(gdb, "geo"), os.path.join(gdb, "geo_pts")
V.run_adaptive_voronoi(GEO, out, n_random=20, iterations=60, out_points=outp, random_seed=9, messages=m)
cells, pts = read_cells(out), read_points(outp)
check("degrees: the sphere engine is used, and says so", m.has("Adaptive Voronoi (Lloyd's algorithm), exact-sphere engine."),
      m.lines[:1])
total, united, outside = tiling(cells, GEO_G)
check("degrees: 20 cells that cover the one-degree square once",
      len(cells) == 20 and abs(total - 1.0) < 1e-6 and abs(united - total) < 1e-6 and outside < 1e-9,
      (len(cells), total - 1.0, united - total, outside))
true_ha = GEO_G.getArea("PRESERVE_SHAPE", "HECTARES")
check("degrees: the areas in hectares add up to the true area of the square (within 0.5 percent)",
      abs(sum(c[1] for c in cells.values()) - true_ha) < 0.005 * true_ha, (sum(c[1] for c in cells.values()), true_ha))
sr84 = arcpy.SpatialReference(4326)
check("degrees: every center lies in the cell of the same number",
      all(cells[i][0].contains(arcpy.PointGeometry(arcpy.Point(pts[i][0], pts[i][1]), sr84)) for i in cells))
m1 = Msgs()
V.run_adaptive_voronoi(GEO, os.path.join(gdb, "geo_one"), n_random=20, iterations=1, random_seed=9, messages=m1)
cv1 = cv([c[1] for c in read_cells(os.path.join(gdb, "geo_one")).values()])
cv_end = cv([c[1] for c in cells.values()])
check("degrees: the cells' true areas become more nearly equal as it runs", cv_end < 0.6 * cv1, (cv_end, cv1))

# ---- 6. outputs in a folder, and what is refused
folder = os.path.join(work, "shp")
os.makedirs(folder)
m = Msgs()
ret = V.run_adaptive_voronoi(SQUARE, os.path.join(folder, "cells"), in_points=PTS, iterations=3,
                             out_points=os.path.join(folder, "centers"), messages=m)
check("folder: the cells and the centers are written as shapefiles",
      ret == os.path.join(folder, "cells.shp") and os.path.isfile(os.path.join(folder, "cells.shp"))
      and os.path.isfile(os.path.join(folder, "centers.shp")), (ret, os.listdir(folder)))
cells = read_cells(ret)
check("folder: the shapefile has every cell, its area and its starting point",
      len(cells) == 8 and sorted(c[2] for c in cells.values()) == sorted(OIDS)
      and all(c[1] > 0 for c in cells.values()), cells and list(cells.values())[0][1:])
raises("refused: neither starting points nor a number of random points",
       lambda: V.run_adaptive_voronoi(SQUARE, os.path.join(gdb, "x")), "Give either a point feature class or a number")
EMPTY = polygons("empty", [])
raises("refused: a boundary with no polygons", lambda: V.run_adaptive_voronoi(EMPTY, os.path.join(gdb, "x"), n_random=5),
       "The boundary input contains no polygons")
nosr = arcpy.management.CreateFeatureclass(folder, "no_cs.shp", "POLYGON")[0]
with arcpy.da.InsertCursor(nosr, ["SHAPE@"]) as c:
    c.insertRow((arcpy.Polygon(arcpy.Array([arcpy.Point(0, 0), arcpy.Point(0, 10), arcpy.Point(10, 10),
                                            arcpy.Point(10, 0)])),))
raises("refused: a boundary with no coordinate system", lambda: V.run_adaptive_voronoi(nosr, os.path.join(gdb, "x"),
                                                                                       n_random=5),
       "The boundary has no defined coordinate system")
check("refused: none of those left an output", not arcpy.Exists(os.path.join(gdb, "x")))

print("\n%d passed, %d failed" % (passed, failed))
arcpy.management.ClearWorkspaceCache()
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
