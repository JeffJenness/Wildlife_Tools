# -*- coding: utf-8 -*-
"""Tests for the Random Sample Array Generator (random_arrays.py).

Run with ArcGIS Pro's python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" random_arrays_test.py
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

import random_arrays as ra  # noqa: E402

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


class Msgs:
    def __init__(self):
        self.info = []
        self.warn = []

    def addMessage(self, t):
        self.info.append(str(t))

    def addWarningMessage(self, t):
        self.warn.append(str(t))


# ---------------------------------------------------------------------------
print("== array offset geometry (no arcpy) ==")

offs, rowcol = ra._offsets_grid(3, 4, 50.0)
check("grid count", len(offs) == 12 and len(rowcol) == 12)
check("grid rows/cols 1-based", rowcol[:, 0].min() == 1
      and rowcol[:, 0].max() == 3 and rowcol[:, 1].max() == 4)
check("grid centered on focal",
      abs(offs[:, 0].mean()) < 1e-9 and abs(offs[:, 1].mean()) < 1e-9)
d01 = math.hypot(offs[1, 0] - offs[0, 0], offs[1, 1] - offs[0, 1])
check("grid spacing exact", abs(d01 - 50.0) < 1e-9)
first = offs[0]
check("grid row 1 is north, col 1 west",
      first[0] < 0 and first[1] > 0)

t = ra._offsets_tri(25, 10.0)
check("tri count", len(t) == 25)
check("tri point 1 is the focal point",
      abs(t[0, 0]) < 1e-12 and abs(t[0, 1]) < 1e-12)
dd = np.hypot(t[:, 0], t[:, 1])
check("tri ordered by distance", bool((np.diff(np.round(dd, 9))
                                       >= 0).all()))
pd = np.hypot(t[:, None, 0] - t[None, :, 0],
              t[:, None, 1] - t[None, :, 1])
np.fill_diagonal(pd, np.inf)
check("tri min pairwise distance = spacing",
      abs(pd.min() - 10.0) < 1e-6)

offs, brgs = ra._offsets_circle(12, 100.0, False, 0.0)
check("circle count (no center)", len(offs) == 12)
r_all = np.hypot(offs[:, 0], offs[:, 1])
check("circle radius exact", bool(np.allclose(r_all, 100.0)))
check("circle 30-degree steps",
      bool(np.allclose(np.diff(brgs), 30.0)))
check("circle starts due north",
      abs(offs[0, 0]) < 1e-9 and abs(offs[0, 1] - 100.0) < 1e-9)

offs, brgs = ra._offsets_circle(12, 100.0, True, 0.0)
check("circle with center: center counts toward n",
      len(offs) == 12 and abs(offs[0, 0]) < 1e-12
      and abs(offs[0, 1]) < 1e-12 and math.isnan(brgs[0]))
check("circle with center: 11 ring points at 360/11 steps",
      bool(np.allclose(np.diff(brgs[1:]), 360.0 / 11)))

rot = ra._rotate_cw(np.array([[0.0, 1.0]]), 90.0)
check("clockwise rotation: north -> east",
      abs(rot[0, 0] - 1.0) < 1e-9 and abs(rot[0, 1]) < 1e-9)
rot = ra._rotate_to_dir(np.array([[1.0, 0.0]]), 3.0, 4.0)
check("align rotation: x-axis onto the line direction",
      abs(rot[0, 0] - 0.6) < 1e-9 and abs(rot[0, 1] - 0.8) < 1e-9)

# ---------------------------------------------------------------------------
print("== engine fixtures ==")
import arcpy  # noqa: E402

TMP = tempfile.mkdtemp(prefix="ra_test_")
arcpy.env.overwriteOutput = True
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)          # UTM 12N, meters


def make_fc(name, shape, geoms):
    fc = arcpy.management.CreateFeatureclass(
        GDB, name, shape, spatial_reference=SR).getOutput(0)
    with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as ins:
        for g in geoms:
            ins.insertRow([g])
    return fc


def poly(coords):
    return arcpy.Polygon(arcpy.Array([arcpy.Point(*c)
                                      for c in coords]), SR)


def line(coords):
    return arcpy.Polyline(arcpy.Array([arcpy.Point(*c)
                                       for c in coords]), SR)


SQUARE = make_fc("square", "POLYGON",
                 [poly([(0, 0), (0, 1000), (1000, 1000), (1000, 0),
                        (0, 0)])])
STRAIGHT = make_fc("straight", "POLYLINE",
                   [line([(0, 0), (4000, 0)])])
DIAG = make_fc("diag", "POLYLINE",
               [line([(0, 0), (3000, 3000)])])
YNET = make_fc("ynet", "POLYLINE",
               [line([(-2000, 0), (0, 0)]),
                line([(0, 0), (2000, 500)]),
                line([(0, 0), (2000, -500)])])


def read_pts(fc, fields=("SHAPE@XY", "Array_ID", "Point_ID")):
    out = []
    with arcpy.da.SearchCursor(fc, list(fields)) as cur:
        for row in cur:
            out.append(row)
    return out


def by_array(rows):
    d = {}
    for row in rows:
        d.setdefault(row[1], []).append(row)
    return d


# ---------------------------------------------------------------------------
print("== polygons: grid arrays ==")
m = Msgs()
out = ra.run_random_arrays(
    os.path.join(GDB, "g1"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=4, array_shape="Grid", n_rows=5, n_cols=5,
    point_spacing=50, orientation_mode="Fixed", orientation_angle=0,
    must_fit=True, linear_units="Meters", random_seed=7,
    spacing_type="Absolute spatial distance", messages=m)
rows = read_pts(out, ("SHAPE@XY", "Array_ID", "Point_ID", "Row",
                      "Col"))
arrays = by_array(rows)
check("4 arrays x 25 points", len(arrays) == 4
      and all(len(v) == 25 for v in arrays.values()),
      str({k: len(v) for k, v in arrays.items()}))
xy = np.array([r[0] for r in rows])
check("must-fit: every point inside the square",
      bool((xy >= 0).all() and (xy <= 1000).all()))
ok_sp = True
for aid, pts in arrays.items():
    p = np.array([r[0] for r in pts])
    pd = np.hypot(p[:, None, 0] - p[None, :, 0],
                  p[:, None, 1] - p[None, :, 1])
    np.fill_diagonal(pd, np.inf)
    if abs(pd.min() - 50.0) > 1e-3:      # gdb 0.0001 m snap grid
        ok_sp = False
check("within-array spacing = 50 m", ok_sp)
rc = {(r[3], r[4]) for r in rows if r[1] == 1}
check("Row/Col fields cover the 5x5 design",
      rc == {(i, j) for i in range(1, 6) for j in range(1, 6)})

# same seed reproduces
out2 = ra.run_random_arrays(
    os.path.join(GDB, "g1b"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=4, array_shape="Grid", n_rows=5, n_cols=5,
    point_spacing=50, orientation_mode="Fixed", orientation_angle=0,
    must_fit=True, linear_units="Meters", random_seed=7,
    spacing_type="Absolute spatial distance", messages=Msgs())
xy2 = np.array([r[0] for r in read_pts(out2)])
check("same seed -> identical coordinates",
      bool(np.allclose(np.sort(xy, axis=0), np.sort(xy2, axis=0))))

# footprint spacing between arrays
m = Msgs()
out = ra.run_random_arrays(
    os.path.join(GDB, "g2"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=3, array_shape="Grid", n_rows=3, n_cols=3,
    point_spacing=40, orientation_mode="Random", must_fit=True,
    min_spacing=150, linear_units="Meters", random_seed=11,
    spacing_type="Absolute spatial distance", messages=m)
arrays = by_array(read_pts(out))
ids = sorted(arrays)
ok_fp = len(ids) >= 2
for i in range(len(ids)):
    for j in range(i + 1, len(ids)):
        a = np.array([r[0] for r in arrays[ids[i]]])
        b = np.array([r[0] for r in arrays[ids[j]]])
        dmin = np.hypot(a[:, None, 0] - b[None, :, 0],
                        a[:, None, 1] - b[None, :, 1]).min()
        if dmin < 150 - 1e-6:
            ok_fp = False
check("footprint spacing: nearest points of any two arrays >= 150 m",
      ok_fp)

# boundary gap (footprint)
out = ra.run_random_arrays(
    os.path.join(GDB, "g3"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=3, array_shape="Grid", n_rows=3, n_cols=3,
    point_spacing=40, orientation_mode="Fixed", orientation_angle=0,
    must_fit=True, boundary_gap=100, linear_units="Meters",
    random_seed=3, spacing_type="Absolute spatial distance",
    messages=Msgs())
xy = np.array([r[0] for r in read_pts(out)])
check("boundary gap: every array point >= 100 m inside",
      bool((xy >= 100 - 1e-6).all() and (xy <= 900 + 1e-6).all()))

# extend mode: full arrays that may leave the polygon
m = Msgs()
out = ra.run_random_arrays(
    os.path.join(GDB, "g4"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=3, array_shape="Grid", n_rows=3, n_cols=3,
    point_spacing=800, orientation_mode="Fixed", orientation_angle=0,
    must_fit=False, linear_units="Meters", random_seed=5,
    spacing_type="Absolute spatial distance", messages=m)
arrays = by_array(read_pts(out))
xy = np.array([r[0] for a in arrays.values() for r in a])
check("extend mode: every array complete (9 points)",
      len(arrays) == 3 and all(len(v) == 9 for v in arrays.values()))
check("extend mode: some points lie outside the polygon",
      bool(((xy < 0) | (xy > 1000)).any()))
# gap ignored with a warning in extend mode
m = Msgs()
ra.run_random_arrays(
    os.path.join(GDB, "g5"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=1, array_shape="Grid", n_rows=2, n_cols=2,
    point_spacing=50, orientation_mode="Fixed", must_fit=False,
    boundary_gap=100, linear_units="Meters", random_seed=5,
    spacing_type="Absolute spatial distance", messages=m)
check("extend mode ignores the boundary gap with a warning",
      any("no meaning" in w for w in m.warn))

# an array wider than the polygon can hold: detected up front
import time as _time
m = Msgs()
_t0 = _time.time()
out = ra.run_random_arrays(
    os.path.join(GDB, "g6"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=5, array_shape="Grid", n_rows=10, n_cols=10,
    point_spacing=200, orientation_mode="Fixed",
    orientation_angle=0, must_fit=True, linear_units="Meters",
    random_seed=61, spacing_type="Absolute spatial distance",
    messages=m)
_dt = _time.time() - _t0
check("impossible grid (1800 m span vs 1414 m diagonal): warned up "
      "front, zero arrays, no budget burned",
      len(read_pts(out)) == 0
      and any("possibly hold" in w for w in m.warn) and _dt < 20,
      "dt={:.1f}s warns={}".format(_dt, m.warn))

# ---------------------------------------------------------------------------
print("== polygons: triangular and circular arrays ==")
out = ra.run_random_arrays(
    os.path.join(GDB, "t1"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=2, array_shape="Triangular", array_points=20,
    point_spacing=30, orientation_mode="Fixed", orientation_angle=0,
    must_fit=True, linear_units="Meters", random_seed=9,
    spacing_type="Absolute spatial distance", messages=Msgs())
arrays = by_array(read_pts(out))
ok_tri = len(arrays) == 2
for pts in arrays.values():
    p = np.array([r[0] for r in pts])
    pd = np.hypot(p[:, None, 0] - p[None, :, 0],
                  p[:, None, 1] - p[None, :, 1])
    np.fill_diagonal(pd, np.inf)
    if len(p) != 20 or abs(pd.min() - 30.0) > 1e-3:
        ok_tri = False
check("triangular arrays: 20 points at 30 m lattice spacing", ok_tri)

out = ra.run_random_arrays(
    os.path.join(GDB, "c1"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=2, array_shape="Circular", array_points=12, radius=100,
    include_center=True, randomize_start=False, must_fit=True,
    linear_units="Meters", random_seed=13,
    spacing_type="Absolute spatial distance", messages=Msgs())
rows = read_pts(out, ("SHAPE@XY", "Array_ID", "Point_ID", "Bearing"))
arrays = by_array(rows)
ok_circ = len(arrays) == 2
for pts in arrays.values():
    pts = sorted(pts, key=lambda r: r[2])
    if len(pts) != 12:
        ok_circ = False
        continue
    cx, cy = pts[0][0]
    if pts[0][3] is not None:               # center has no bearing
        ok_circ = False
    ring = np.array([r[0] for r in pts[1:]])
    rr = np.hypot(ring[:, 0] - cx, ring[:, 1] - cy)
    if not np.allclose(rr, 100.0, atol=1e-3):
        ok_circ = False
    if abs(pts[1][3]) > 1e-9:               # first ring point north
        ok_circ = False
    steps = np.diff([r[3] for r in pts[1:]])
    if not np.allclose(steps, 360.0 / 11, atol=1e-9):
        ok_circ = False
check("circular arrays: center + 11 ring points at radius, "
      "360/11-degree steps from north", ok_circ)

# randomized start angle stays within one step
out = ra.run_random_arrays(
    os.path.join(GDB, "c2"), boundary=SQUARE,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=3, array_shape="Circular", array_points=12, radius=80,
    include_center=False, randomize_start=True, must_fit=True,
    linear_units="Meters", random_seed=17,
    spacing_type="Absolute spatial distance", messages=Msgs())
rows = read_pts(out, ("SHAPE@XY", "Array_ID", "Point_ID", "Bearing"))
firsts = [min(v, key=lambda r: r[2])[3] for v in by_array(rows).values()]
check("randomized start: first bearing within [0, 30) degrees",
      all(0.0 <= b < 30.0 for b in firsts), str(firsts))

# ---------------------------------------------------------------------------
print("== polylines: 2-D arrays at line points ==")
out = ra.run_random_arrays(
    os.path.join(GDB, "l1"), boundary=STRAIGHT,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=3, array_shape="Circular", array_points=8, radius=60,
    include_center=True, randomize_start=False, must_fit=True,
    linear_units="Meters", random_seed=19,
    spacing_type="Absolute spatial distance", messages=Msgs())
rows = read_pts(out, ("SHAPE@XY", "Array_ID", "Point_ID"))
centers = [min(v, key=lambda r: r[2])[0]
           for v in by_array(rows).values()]
check("2-D array centers sit on the polyline",
      all(abs(c[1]) < 1e-3 and 0 <= c[0] <= 4000 for c in centers))

out = ra.run_random_arrays(
    os.path.join(GDB, "l2"), boundary=DIAG,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=2, array_shape="Grid", n_rows=1, n_cols=3,
    point_spacing=100, orientation_mode="Align", must_fit=True,
    linear_units="Meters", random_seed=23,
    spacing_type="Absolute spatial distance", messages=Msgs())
arrays = by_array(read_pts(out))
ok_align = len(arrays) == 2
for pts in arrays.values():
    p = np.array([r[0] for r in pts])
    v = p - p.mean(axis=0)
    d = v / np.maximum(np.hypot(v[:, 0], v[:, 1])[:, None], 1e-12)
    for k in range(len(d)):
        if np.hypot(*v[k]) > 1e-3 \
                and abs(abs(d[k, 0] * math.sqrt(0.5)
                            + d[k, 1] * math.sqrt(0.5)) - 1.0) > 1e-3:
            ok_align = False
check("align-to-line: 1x3 grid rows run along the 45-degree line",
      ok_align)

# ---------------------------------------------------------------------------
print("== polylines: 1-D along-the-line arrays ==")
out = ra.run_random_arrays(
    os.path.join(GDB, "l3"), boundary=STRAIGHT,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=3, array_shape="Along the polyline", array_points=5,
    point_spacing=100, must_fit=True, linear_units="Meters",
    random_seed=29, spacing_type="Absolute spatial distance",
    messages=Msgs())
rows = read_pts(out)
arrays = by_array(rows)
ok_1d = len(arrays) == 3
for pts in arrays.values():
    pts = sorted(pts, key=lambda r: r[2])
    p = np.array([r[0] for r in pts])
    if len(p) != 5 or not np.allclose(p[:, 1], 0.0, atol=1e-3):
        ok_1d = False
        continue
    gaps = np.abs(np.diff(np.sort(p[:, 0])))
    if not np.allclose(gaps, 100.0, atol=1e-3):
        ok_1d = False
check("1-D odd array: 5 on-line points every 100 m", ok_1d)

out = ra.run_random_arrays(
    os.path.join(GDB, "l4"), boundary=STRAIGHT,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=2, array_shape="Along the polyline", array_points=4,
    point_spacing=100, must_fit=True, linear_units="Meters",
    random_seed=31, spacing_type="Absolute spatial distance",
    messages=Msgs())
arrays = by_array(read_pts(out))
ok_1d = len(arrays) == 2
for pts in arrays.values():
    p = np.sort(np.array([r[0][0] for r in pts]))
    if len(p) != 4 or not np.allclose(np.diff(p), 100.0, atol=1e-3):
        ok_1d = False
check("1-D even array: 4 points straddling the focal point", ok_1d)

# an array longer than the line: detected up front, nothing placed
m = Msgs()
out = ra.run_random_arrays(
    os.path.join(GDB, "l5"), boundary=STRAIGHT,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=2, array_shape="Along the polyline", array_points=50,
    point_spacing=100, must_fit=True, linear_units="Meters",
    random_seed=37, spacing_type="Absolute spatial distance",
    max_attempts=50, messages=m)
check("1-D array longer than the line: impossible, warned up front",
      len(read_pts(out)) == 0 and any("no arrays can fit" in w
                                      for w in m.warn), str(m.warn))

# Y network: crossing the confluence picks one branch and warns
m = Msgs()
out = ra.run_random_arrays(
    os.path.join(GDB, "l6"), boundary=YNET,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=8, array_shape="Along the polyline", array_points=21,
    point_spacing=100, must_fit=True, linear_units="Meters",
    random_seed=41, spacing_type="Absolute spatial distance",
    messages=m)
rows = read_pts(out)
segs = [((-2000, 0), (0, 0)), ((0, 0), (2000, 500)),
        ((0, 0), (2000, -500))]


def dist_to_seg(p, a, b):
    ax, ay = a
    bx, by = b
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    t = max(0.0, min(1.0, ((p[0] - ax) * vx + (p[1] - ay) * vy) / L2))
    return math.hypot(p[0] - (ax + t * vx), p[1] - (ay + t * vy))


ok_on = all(min(dist_to_seg(r[0], *s) for s in segs) < 1e-3
            for r in rows)
check("Y network: every 1-D point lies on the network", ok_on)
check("Y network: arrays complete (21 points each)",
      all(len(v) == 21 for v in by_array(rows).values())
      and len(by_array(rows)) >= 1)
check("Y network: ambiguous-junction warning raised",
      any("ambiguous" in w for w in m.warn), str(m.warn))

# ---------------------------------------------------------------------------
print("== raster areas of interest ==")
arr = np.ones((60, 60), dtype=np.int32)
arr[:, 30:] = 2
arr[25:35, 25:35] = 9                        # will become NoData
ras = arcpy.NumPyArrayToRaster(arr, arcpy.Point(0, 0), 10, 10,
                               value_to_nodata=9)
ras_path = os.path.join(GDB, "cats")
arcpy.management.DefineProjection(ras, SR)
ras.save(ras_path)
arcpy.management.BuildRasterAttributeTable(ras_path, "Overwrite")

m = Msgs()
out = ra.run_random_arrays(
    os.path.join(GDB, "r1"), boundary=ras_path,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=4, array_shape="Grid", n_rows=3, n_cols=3,
    point_spacing=30, orientation_mode="Fixed", orientation_angle=0,
    must_fit=True, linear_units="Meters", random_seed=43,
    spacing_type="Absolute spatial distance", messages=m)
rows = read_pts(out, ("SHAPE@XY", "Array_ID", "Point_ID",
                      "Category"))
arrays = by_array(rows)
in_hole = [r for r in rows
           if 250 < r[0][0] < 350 and 250 < r[0][1] < 350]
check("raster must-fit: 4 complete arrays, no point on NoData",
      len(arrays) == 4 and all(len(v) == 9 for v in arrays.values())
      and not in_hole
      and all(r[3] in (1, 2) for r in rows))

m = Msgs()
out = ra.run_random_arrays(
    os.path.join(GDB, "r2"), boundary=ras_path,
    strata_method="Stratify by raster category",
    alloc_method="Equal count", n_per_stratum=2, array_shape="Grid",
    n_rows=2, n_cols=2, point_spacing=30, orientation_mode="Fixed",
    must_fit=True, linear_units="Meters", random_seed=47,
    spacing_type="Absolute spatial distance", messages=m)
rows = read_pts(out, ("SHAPE@XY", "Array_ID", "Point_ID", "Stratum"))
strat_of = {}
for r in rows:
    strat_of.setdefault(r[3], set()).add(r[1])
check("raster categories: 2 arrays per stratum",
      set(strat_of) == {"1", "2"}
      and all(len(v) == 2 for v in strat_of.values()),
      str({k: len(v) for k, v in strat_of.items()}))

m = Msgs()
out = ra.run_random_arrays(
    os.path.join(GDB, "r3"), boundary=ras_path,
    strata_method="No stratification", alloc_method="Fixed total",
    n_arrays=3, array_shape="Grid", n_rows=3, n_cols=3,
    point_spacing=120, orientation_mode="Fixed", orientation_angle=0,
    must_fit=False, linear_units="Meters", random_seed=53,
    spacing_type="Absolute spatial distance", messages=m)
rows = read_pts(out, ("SHAPE@XY", "Array_ID", "Point_ID",
                      "Category"))
arrays = by_array(rows)
check("raster extend mode: full arrays, off-data points carry a "
      "null Category",
      len(arrays) == 3 and all(len(v) == 9 for v in arrays.values()))

# ---------------------------------------------------------------------------
print("== stratified polygons ==")
TWO = make_fc("two", "POLYGON",
              [poly([(0, 0), (0, 1000), (1000, 1000), (1000, 0),
                     (0, 0)]),
               poly([(2000, 0), (2000, 1000), (3000, 1000),
                     (3000, 0), (2000, 0)])])
out = ra.run_random_arrays(
    os.path.join(GDB, "s1"), boundary=TWO,
    strata_method="Stratify by individual polygon",
    alloc_method="Equal count", n_per_stratum=2,
    array_shape="Circular", array_points=6, radius=50,
    include_center=False, randomize_start=False, must_fit=True,
    linear_units="Meters", random_seed=59,
    spacing_type="Absolute spatial distance", messages=Msgs())
rows = read_pts(out, ("SHAPE@XY", "Array_ID", "Point_ID", "Stratum",
                      "Src_FID"))
strat_of = {}
for r in rows:
    strat_of.setdefault(r[3], set()).add(r[1])
check("individual-polygon strata: 2 arrays each",
      len(strat_of) == 2
      and all(len(v) == 2 for v in strat_of.values()))
check("Src_FID carried", all(r[4] in (1, 2) for r in rows))

# ---------------------------------------------------------------------------
print()
print("{} passed, {} failed".format(PASS, FAIL))
try:
    arcpy.management.Delete(GDB)
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass
sys.exit(1 if FAIL else 0)
