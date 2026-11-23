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

"""Random Point Generator (random_points.py). The points are random, so the
checks are of what must hold for ANY run: the counts asked for, every point
where it may be, every distance kept, and the points spread evenly (each
random seed is fixed here, and the limits are 4.5 standard deviations wide,
so a pass or a failure is the same on every run). The spacing rules are
compared, candidate by candidate, with plain versions written out here that
test a candidate against every point already taken.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" random_points_test.py
"""
import math
import os
import shutil
import sys
import tempfile

import numpy as np
from osgeo import gdal, osr
from scipy.spatial.distance import pdist
from scipy.stats import chi2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import random_arrays as RA  # noqa: E402
import random_points as R  # noqa: E402
import repeating_shapes as RS  # noqa: E402

gdal.UseExceptions()
passed = failed = known = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


def known_fault(name, ok, detail=""):
    """A fault that has been found and reported and is not yet fixed: shown on every run, and not counted as a
    failure. When the engine is corrected this says so, and the line is to become an ordinary check()."""
    global known, passed
    if ok:
        passed += 1
        print("NOW PASSES (make it an ordinary check)  %s" % name)
    else:
        known += 1
        print("KNOWN FAULT, not yet fixed  %s  %s" % (name, detail))


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

    def warned(self, text):
        return any(text in s for s in self.warnings)


def near_share(k, n, p, z=4.5):
    """Is k of n what a share p would give, within z standard deviations?"""
    return abs(k - n * p) <= z * math.sqrt(n * p * (1.0 - p))


def even(counts):
    """Are these counts of equally likely boxes as even as chance allows (chi-square, 1 in 1,000)?"""
    c = np.asarray(counts, dtype=float)
    e = c.sum() / len(c)
    return float(((c - e) ** 2 / e).sum()) < chi2.ppf(0.999, len(c) - 1)


arcpy.env.overwriteOutput = True
work = tempfile.mkdtemp(prefix="random_points_test_")
gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
out_gdb = arcpy.management.CreateFileGDB(work, "out.gdb")[0]
UTM = 26912
SR = arcpy.SpatialReference(UTM)
WGS = arcpy.SpatialReference(4326)
X0, Y0 = 500000.0, 3900000.0
N_OUT = [0]


def out(tag="p"):
    N_OUT[0] += 1
    return os.path.join(out_gdb, "%s_%03d" % (tag, N_OUT[0]))


def read(fc):
    names = [f.name for f in arcpy.ListFields(fc)]
    extra = [n for n in ("Stratum", "Src_FID", "Category") if n in names]
    rows = list(arcpy.da.SearchCursor(fc, ["SHAPE@X", "SHAPE@Y", "Point_ID"] + extra))
    d = {"x": np.array([r[0] for r in rows], dtype=float), "y": np.array([r[1] for r in rows], dtype=float),
         "id": [r[2] for r in rows], "n": len(rows), "fields": extra}
    for k, nm in enumerate(extra):
        d[nm] = [r[3 + k] for r in rows]
    d["xy"] = np.column_stack([d["x"], d["y"]]) if rows else np.empty((0, 2))
    return d


def in_rect(d, x, y, w, h):
    return (d["x"] >= x) & (d["x"] <= x + w) & (d["y"] >= y) & (d["y"] <= y + h)


def box(x, y, w, h):
    return [[x, y], [x, y + h], [x + w, y + h], [x + w, y], [x, y]]


def hole(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]


def poly(*rings, wkid=UTM):
    return arcpy.AsShape({"rings": list(rings), "spatialReference": {"wkid": wkid}}, True)


def line(*pts, wkid=UTM):
    return arcpy.AsShape({"paths": [[list(p) for p in pts]], "spatialReference": {"wkid": wkid}}, True)


def make(name, kind, rows, wkid=UTM):
    """rows: (shape, zone text, population)"""
    fc = arcpy.management.CreateFeatureclass(gdb, name, kind, spatial_reference=arcpy.SpatialReference(wkid))[0]
    arcpy.management.AddField(fc, "Zone", "TEXT", field_length=10)
    arcpy.management.AddField(fc, "Pop", "LONG")
    with arcpy.da.InsertCursor(fc, ["SHAPE@", "Zone", "Pop"]) as c:
        for r in rows:
            c.insertRow(r)
    return fc


# =========================================================================================================
#  1. the small pieces
# =========================================================================================================
for label in R.STRATA_POLYGON + R.STRATA_POLYLINE + R.STRATA_RASTER + R.STRATA_EXTENT:
    if R.canon_strata(label) is None:
        check("choices: every stratification offered is understood", False, label)
        break
else:
    check("choices: every stratification offered is understood", True)
check("choices: what each stratification means",
      [R.canon_strata(s) for s in R.STRATA_RASTER] == [R.STRATA_NONE, R.STRATA_CATEGORY, R.STRATA_REGION, R.STRATA_IDFIELD]
      and R.canon_strata(R.STRATA_POLYGON[1]) == R.STRATA_INDIV and R.canon_strata(R.STRATA_POLYLINE[1]) == R.STRATA_INDIV
      and R.canon_strata("") == R.STRATA_NONE and R.canon_strata(None) == R.STRATA_NONE
      and R.canon_strata("something else") is None)
check("choices: what each way of sharing out the points means",
      [R.canon_alloc(s) for s in R.ALLOC_STRAT] == [R.ALLOC_EQUAL, R.ALLOC_PROP_AREA, R.ALLOC_EQUAL_FIELD,
                                                    R.ALLOC_PROP_FIELD, R.ALLOC_DENSITY]
      and [R.canon_alloc(s) for s in R.ALLOC_STRAT_LINE] == [R.ALLOC_EQUAL, R.ALLOC_PROP_AREA, R.ALLOC_EQUAL_FIELD,
                                                             R.ALLOC_PROP_FIELD, R.ALLOC_DENSITY]
      and [R.canon_alloc(s) for s in R.ALLOC_UNSTRAT + R.ALLOC_UNSTRAT_LINE] == [R.ALLOC_TOTAL, R.ALLOC_DENSITY] * 2
      and R.canon_alloc("something else") is None)
check("choices: what each way of measuring the spacing means",
      [R.canon_spacing(s) for s in R.SPACING_TYPES] == [R.SPACING_SPATIAL, R.SPACING_ALONG, R.SPACING_NETWORK]
      and R.canon_spacing(None) == R.SPACING_SPATIAL and R.canon_spacing("something else") is None)

# sharing a total out in proportion: quotas 3.5, 2.1 and 1.4 of 7 give 3, 2 and 1, and the one left over
# goes to the largest remainder
check("sharing out: 7 among weights 5, 3 and 2 is 4, 2 and 1", R._apportion(7, [5, 3, 2]).tolist() == [4, 2, 1],
      R._apportion(7, [5, 3, 2]).tolist())
check("sharing out: 600 among areas 1, 2 and 3 is exactly 100, 200 and 300",
      R._apportion(600, [1e6, 2e6, 3e6]).tolist() == [100, 200, 300])
rs = np.random.default_rng(5)
sums_ok = all(int(R._apportion(t, w).sum()) == t for t, w in ((int(rs.integers(1, 5000)), rs.uniform(0.1, 9, int(rs.integers(1, 40))))
                                                               for _ in range(300)))
check("sharing out: the shares always add up to the total, whatever the weights", sums_ok)
w = rs.uniform(0.1, 9, 25)
a = R._apportion(1000, w)
check("sharing out: no share is more than one away from its exact proportion", bool(np.all(np.abs(a - 1000 * w / w.sum()) < 1.0)))
check("sharing out: nothing for a weight of zero, a negative weight or no weight at all",
      R._apportion(10, [0, 5, -3, float("nan")]).tolist() == [0, 10, 0, 0] and R._apportion(10, [0, 0]).tolist() == [0, 0]
      and R._apportion(0, [1, 2]).tolist() == [0, 0])
check("units: the density units in square meters and meters",
      abs(R._DENS_M2["Acre"] - 43560 * 0.3048 ** 2) < 1e-9 and abs(R._DENS_M2["Square Mile"] - (5280 * 0.3048) ** 2) < 1e-6
      and R._DENS_M2["Hectare"] == 1e4 and R._DENS_M2["Square Kilometer"] == 1e6
      and R._DENS_LIN_M == {"Foot": 0.3048, "Mile": 5280 * 0.3048, "Meter": 1.0, "Kilometer": 1000.0}
      and sorted(R.DENSITY_UNITS) == sorted(R._DENS_M2) and sorted(R.DENSITY_UNITS_LINE) == sorted(R._DENS_LIN_M))

# ---- the spacing rule for points in the plane, against the plain version
rs = np.random.default_rng(1)
cand = rs.uniform(-1.0, 1.0, (6000, 2))
r = 0.05
grid = R._SpacingGrid(r)
taken = np.empty((0, 2))
agree = True
for x, y in cand:
    plain = (not len(taken)) or bool((((taken[:, 0] - x) ** 2 + (taken[:, 1] - y) ** 2) >= r * r).all())
    if grid.try_admit(float(x), float(y)) != plain:
        agree = False
        break
    if plain:
        taken = np.vstack([taken, [x, y]])
check("spacing in the plane: each of 6,000 candidates is taken or refused exactly as by testing it against "
      "every point already taken", agree, len(taken))
check("spacing in the plane: the points taken are all at least the spacing apart", pdist(taken).min() >= r, pdist(taken).min())

# ---- the spacing rule along a line
rs = np.random.default_rng(2)
along = R._AlongSpacing(40.0)
taken_s = {}
agree = True
for _ in range(4000):
    part, s = int(rs.integers(0, 3)), float(rs.uniform(0, 3000))
    plain = all(abs(s - t) >= 40.0 for t in taken_s.get(part, []))
    if along.try_admit(part, s) != plain:
        agree = False
        break
    if plain:
        taken_s.setdefault(part, []).append(s)
check("spacing along a line: as by testing each candidate against every point already on the same line", agree)
check("spacing along a line: points on different lines never block each other",
      R._AlongSpacing(40.0).try_admit(1, 100.0) and (lambda a: a.try_admit(1, 100.0) and a.try_admit(2, 100.0)
                                                    and not a.try_admit(1, 120.0))(R._AlongSpacing(40.0)))

# ---- the spacing rule through a network of lines
#  a Y of three lines that meet at (1000, 0); a T, where one line ends on the middle of another; two lines
#  that cross without sharing an end (not joined); and a square ring of four lines
A = lambda *p: np.array(p, dtype=float)                                        # noqa: E731
PARTS = {1: A((0, 0), (1000, 0)), 2: A((1000, 0), (2000, 1000)), 3: A((1000, 0), (2000, -1000)),
         4: A((0, 5000), (2000, 5000)), 5: A((1000, 5000), (1000, 6000)),
         6: A((0, 9000), (2000, 9000)), 7: A((1000, 8000), (1000, 10000)),
         10: A((0, 20000), (500, 20000)), 11: A((500, 20000), (500, 20500)), 12: A((500, 20500), (0, 20500)),
         13: A((0, 20500), (0, 20000))}
LEN = dict((u, float(np.hypot(*(a[1:] - a[:-1]).T).sum())) for u, a in PARTS.items())
J_AT = {1: 1000.0, 2: 0.0, 3: 0.0}                  # where the Y's meeting point is on each of its lines
T_AT = {4: 1000.0, 5: 0.0}


def network_distance(p, s, q, t):
    if p == q and p not in (10, 11, 12, 13):
        return abs(s - t)
    if p in J_AT and q in J_AT:
        return abs(s - J_AT[p]) + abs(t - J_AT[q])
    if p in T_AT and q in T_AT:
        return abs(s - T_AT[p]) + abs(t - T_AT[q])
    if p in (10, 11, 12, 13) and q in (10, 11, 12, 13):                        # the ring: the shorter way round
        d = abs(((p - 10) * 500.0 + s) - ((q - 10) * 500.0 + t))
        return min(d, 2000.0 - d)
    return float("inf")


net = R._NetworkSpacing(150.0)
m = Msgs()
net.build([[(u, PARTS[u]) for u in (1, 2, 3, 4, 5, 6, 7)], [(u, PARTS[u]) for u in (10, 11, 12, 13)]], m)
check("network: the meeting points are found: the Y, the T and the ring's four corners (6), and not the crossing",
      net.n_junctions == 6 and m.has("6 junctions connect the line networks."), (net.n_junctions, m.lines))
one = R._NetworkSpacing(150.0)
m1 = Msgs()
one.build([[(u, PARTS[u]) for u in (1, 2, 3)]], m1)
check("network: one meeting point in one network is reported in the singular",
      m1.lines == ["1 junction connects the line network."], m1.lines)
rs = np.random.default_rng(3)
uids = sorted(PARTS)
taken_n = []
agree, n_tested = True, 0
for _ in range(5000):
    u = uids[int(rs.integers(0, len(uids)))]
    s = float(rs.uniform(0, LEN[u]))
    plain = all(network_distance(u, s, q, t) >= 150.0 for q, t in taken_n)
    n_tested += 1
    if net.try_admit(u, s) != plain:
        agree = False
        break
    if plain:
        taken_n.append((u, s))
check("network: each of 5,000 candidates is taken or refused exactly as by measuring its distance through the "
      "lines to every point already taken", agree, (n_tested, len(taken_n)))
on6 = [(u, s) for u, s in taken_n if u == 6]
on7 = [(u, s) for u, s in taken_n if u == 7]
close = min(math.hypot(s - 1000.0, t - 1000.0) for _u, s in on6 for _v, t in on7) if on6 and on7 else None
check("network: two lines that only cross are not joined, so points on them can be nearer than the spacing",
      close is not None and close < 150.0, close)

# ---- places along a line
samp = R._line_sampler([(7, A((0, 0), (100, 0), (100, 50)))], 10.0)       # 150 long; 10 kept clear at each end
xy, part, s = R._line_at(samp, np.array([0.0, 90.0, 130.0, 65.0]))
check("along a line: with 10 kept clear at each end, 130 of a line of 150 can hold points", abs(samp["total"] - 130.0) < 1e-9,
      samp["total"])
check("along a line: a position is turned into the right place, round the corner, and its distance from the start",
      np.allclose(xy, [(10, 0), (100, 0), (100, 40), (75, 0)]) and np.allclose(s, [10, 100, 140, 75])
      and part.tolist() == [7, 7, 7, 7], (xy.tolist(), s.tolist()))
check("along a line: a line no longer than twice the clear distance can hold none",
      R._line_sampler([(7, A((0, 0), (20, 0)))], 10.0) is None and R._line_sampler([(7, A((0, 0), (20, 0)))], 9.0) is not None)
check("along a line: with nothing kept clear the whole line can hold points",
      abs(R._line_sampler([(1, A((0, 0), (3, 4))), (2, A((0, 0), (0, 5)))], None)["total"] - 10.0) < 1e-12)
check("rasters: a value to stand for 'no data' is one that is not a category",
      R._nodata_sentinel(np.uint8, {1, 2, 3}) == 0 and R._nodata_sentinel(np.uint8, {0, 1}) == 255
      and R._nodata_sentinel(np.uint8, {0, 255, 1}) == 2 and R._nodata_sentinel(np.int16, {-32768}) == 32767)

# =========================================================================================================
#  2. polygons
# =========================================================================================================
P1, P2, P3 = (X0, Y0, 1000.0, 1000.0), (X0 + 2000, Y0, 2000.0, 1000.0), (X0, Y0 + 2000, 3000.0, 1000.0)   # 1 : 2 : 3
POLYS = make("polys3", "POLYGON", [(poly(box(*P1)), "A", 30), (poly(box(*P2)), "B", 0), (poly(box(*P3)), "A", 12)])
OID = [r[0] for r in arcpy.da.SearchCursor(POLYS, ["OID@"])]

m = Msgs()
o = out()
ret = R.run_random_points(o, boundary=POLYS, n_points=6000, random_seed=7, messages=m)
d = read(o)
i1, i2, i3 = in_rect(d, *P1), in_rect(d, *P2), in_rect(d, *P3)
check("polygons, one total: the output path is returned and exactly 6,000 points are written", ret == o and d["n"] == 6000,
      (ret, d["n"]))
check("polygons, one total: every point is inside one of the polygons", bool((i1 | i2 | i3).all()), int((~(i1 | i2 | i3)).sum()))
check("polygons, one total: the polygons get points in proportion to their areas (1 : 2 : 3)",
      near_share(int(i1.sum()), 6000, 1 / 6) and near_share(int(i2.sum()), 6000, 2 / 6) and near_share(int(i3.sum()), 6000, 3 / 6),
      (int(i1.sum()), int(i2.sum()), int(i3.sum())))
gx = np.floor((d["x"][i3] - P3[0]) / 500.0).astype(int)
gy = np.floor((d["y"][i3] - P3[1]) / 500.0).astype(int)
check("polygons, one total: within a polygon the points are spread evenly (12 equal boxes)",
      even(np.bincount(np.clip(gx, 0, 5) * 2 + np.clip(gy, 0, 1), minlength=12)),
      np.bincount(np.clip(gx, 0, 5) * 2 + np.clip(gy, 0, 1), minlength=12).tolist())
check("polygons, one total: the points are numbered 1 to 6,000, and no other fields are added",
      d["id"] == list(range(1, 6001)) and d["fields"] == [], d["fields"])
check("polygons, one total: the output has the polygons' coordinate system",
      arcpy.Describe(o).spatialReference.factoryCode == UTM and arcpy.Describe(o).shapeType == "Point")
check("polygons, one total: the count is reported, and nothing is warned of",
      m.has("6,000 random points written.") and m.warnings == [], (m.lines[-1:], m.warnings))
summ = arcpy.metadata.Metadata(o).summary or ""
check("polygons, one total: the output's summary says how it was made",
      summ.startswith("Created by Random Point Generator: 6,000 of 6,000 requested points (")
      and summ.endswith("seed 7) over polygon features from polys3."), summ)

o2, o3 = out(), out()
R.run_random_points(o2, boundary=POLYS, n_points=6000, random_seed=7)
R.run_random_points(o3, boundary=POLYS, n_points=6000, random_seed=8)
check("random seed: the same seed gives the same points, another seed other points",
      np.array_equal(read(o2)["xy"], d["xy"]) and not np.array_equal(read(o3)["xy"], d["xy"]))
o4, o5 = out(), out()
R.run_random_points(o4, boundary=POLYS, n_points=50)
R.run_random_points(o5, boundary=POLYS, n_points=50)
check("random seed: with no seed, two runs differ", not np.array_equal(read(o4)["xy"], read(o5)["xy"]))

# a minimum spacing
SQUARE = make("square", "POLYGON", [(poly(box(*P1)), "A", 1)])
m = Msgs()
o = out()
R.run_random_points(o, boundary=SQUARE, n_points=300, min_spacing=40, random_seed=3, messages=m)
d = read(o)
check("spacing: 300 points at least 40 m apart in a square kilometer: all are placed, inside, and none nearer",
      d["n"] == 300 and bool(in_rect(d, *P1).all()) and pdist(d["xy"]).min() >= 40.0 and m.warnings == [],
      (d["n"], pdist(d["xy"]).min() if d["n"] > 1 else None, m.warnings))
o = out()
R.run_random_points(o, boundary=SQUARE, n_points=120, min_spacing=200, linear_units="Feet", random_seed=3)
d = read(o)
check("spacing: given in feet, it is kept in feet (200 ft = 60.96 m)", d["n"] == 120 and pdist(d["xy"]).min() >= 60.96,
      pdist(d["xy"]).min())
# more points than can fit: 0.547 x 1,000,000 / (pi x 20 x 20) = 435 is the most that random placing reaches
m = Msgs()
o = out()
R.run_random_points(o, boundary=SQUARE, n_points=2000, min_spacing=40, random_seed=3, max_attempts=30, messages=m)
d = read(o)
check("spacing, too many asked for: warned beforehand, with the limit for this area (about 435)",
      m.warned("2,000 points at a minimum spacing of 40 meters exceeds the theoretical random-packing limit (~435)"),
      m.warnings[:1])
check("spacing, too many asked for: as many as fit are placed, still none nearer than 40 m, and the shortfall is said",
      300 < d["n"] < 600 and pdist(d["xy"]).min() >= 40.0
      and m.warned("only %s of 2,000 points fit within 30 placement attempts per point" % format(d["n"], ","))
      and m.has("%s random points written (fewer than requested -- see the warnings above)." % format(d["n"], ",")),
      (d["n"], m.warnings[-1:], m.lines[-1:]))
check("spacing, too many asked for: the output's summary gives both numbers",
      ("%s of 2,000 requested points" % format(d["n"], ",")) in (arcpy.metadata.Metadata(o).summary or ""))

# a minimum distance from the boundary, with a hole
DONUT_G = poly(box(X0, Y0 + 5000, 1000, 1000), hole(X0 + 400, Y0 + 5400, 200, 200))
DONUT = make("donut", "POLYGON", [(DONUT_G, "A", 1)])
o = out()
R.run_random_points(o, boundary=DONUT, n_points=1500, boundary_gap=100, random_seed=4)
d = read(o)
edge = DONUT_G.boundary()
dist = np.array([edge.distanceTo(arcpy.PointGeometry(arcpy.Point(x, y), SR)) for x, y in d["xy"]])
inside = in_rect(d, X0, Y0 + 5000, 1000, 1000) & ~in_rect(d, X0 + 400, Y0 + 5400, 200, 200)
check("distance from the boundary: 1,500 points, all inside the polygon and out of its hole", d["n"] == 1500 and bool(inside.all()))
check("distance from the boundary: none nearer than 100 m to the outline or to the hole's edge",
      dist.min() >= 100.0 - 1e-6, dist.min())
check("distance from the boundary: the ground that is left is all used (some point within 5 m of the limit)",
      dist.min() < 105.0, dist.min())
m = Msgs()
o = out()
R.run_random_points(o, boundary=SQUARE, n_points=10, boundary_gap=600, random_seed=4, messages=m)
check("distance from the boundary: a distance that leaves nothing gives no points, a warning, and no error",
      read(o)["n"] == 0 and m.warned("the minimum distance from the boundary leaves no interior at all")
      and m.has("0 random points written (fewer than requested -- see the warnings above)."), (m.warnings, m.lines[-1:]))

# strata
m = Msgs()
o = out()
R.run_random_points(o, boundary=POLYS, strata_method=R.STRATA_POLYGON[1], alloc_method=R.ALLOC_STRAT[0], n_per_stratum=50,
                    random_seed=5, messages=m)
d = read(o)
i1, i2, i3 = in_rect(d, *P1), in_rect(d, *P2), in_rect(d, *P3)
check("each polygon a stratum, equal counts: exactly 50 in each", (int(i1.sum()), int(i2.sum()), int(i3.sum())) == (50, 50, 50)
      and d["n"] == 150 and m.has("3 strata defined."), (int(i1.sum()), int(i2.sum()), int(i3.sum())))
src = np.array(d["Src_FID"])
check("each polygon a stratum: every point names the polygon it is in, as its source and as its stratum",
      bool((src[i1] == OID[0]).all() and (src[i2] == OID[1]).all() and (src[i3] == OID[2]).all())
      and d["Stratum"] == [str(v) for v in d["Src_FID"]], d["fields"])
o = out()
R.run_random_points(o, boundary=POLYS, strata_method=R.STRATA_POLYGON[1], alloc_method=R.ALLOC_STRAT[1], n_points=600, random_seed=5)
d = read(o)
check("each polygon a stratum, in proportion to area: exactly 100, 200 and 300 of 600",
      (int(in_rect(d, *P1).sum()), int(in_rect(d, *P2).sum()), int(in_rect(d, *P3).sum())) == (100, 200, 300))
o = out()
R.run_random_points(o, boundary=POLYS, strata_method=R.STRATA_POLYGON[2], strata_field="Zone", alloc_method=R.ALLOC_STRAT[0],
                    n_per_stratum=80, random_seed=5)
d = read(o)
i1, i2, i3 = in_rect(d, *P1), in_rect(d, *P2), in_rect(d, *P3)
strat = np.array(d["Stratum"])
check("strata by a field, equal counts: 80 in zone A and 80 in zone B",
      int((strat == "A").sum()) == 80 and int((strat == "B").sum()) == 80 and bool((strat[i2] == "B").all())
      and bool((strat[i1 | i3] == "A").all()), (int((strat == "A").sum()), int((strat == "B").sum())))
check("strata by a field: zone A's 80 are shared between its two polygons by their areas (1 : 3 gives 20 and 60)",
      (int(i1.sum()), int(i3.sum())) == (20, 60), (int(i1.sum()), int(i3.sum())))
o = out()
R.run_random_points(o, boundary=POLYS, strata_method=R.STRATA_POLYGON[1], alloc_method=R.ALLOC_STRAT[2], population_field="Pop",
                    random_seed=5)
d = read(o)
check("counts from a field: each polygon gets the number in its field (30, 0 and 12)",
      (int(in_rect(d, *P1).sum()), int(in_rect(d, *P2).sum()), int(in_rect(d, *P3).sum())) == (30, 0, 12))
o = out()
R.run_random_points(o, boundary=POLYS, strata_method=R.STRATA_POLYGON[2], strata_field="Zone", alloc_method=R.ALLOC_STRAT[2],
                    population_field="Pop", random_seed=5)
d = read(o)
check("counts from a field, strata by a field: a zone gets the sum of its polygons' numbers (A: 42, B: 0)",
      d["n"] == 42 and set(d["Stratum"]) == {"A"}, (d["n"], set(d["Stratum"])))
o = out()
R.run_random_points(o, boundary=POLYS, strata_method=R.STRATA_POLYGON[1], alloc_method=R.ALLOC_STRAT[3], population_field="Pop",
                    n_points=70, random_seed=5)
d = read(o)
check("in proportion to a field: 70 shared as 30 : 0 : 12 is 50, 0 and 20",
      (int(in_rect(d, *P1).sum()), int(in_rect(d, *P2).sum()), int(in_rect(d, *P3).sum())) == (50, 0, 20))
NEG = make("polys_neg", "POLYGON", [(poly(box(*P1)), "A", 5), (poly(box(*P2)), "B", -1)])
raises("counts from a field: a negative number is refused", lambda: R.run_random_points(
    out(), boundary=NEG, strata_method=R.STRATA_POLYGON[1], alloc_method=R.ALLOC_STRAT[2], population_field="Pop"),
    "The population field 'Pop' contains negative values")

# density: a square kilometer is 100 hectares, 247.1 acres
m = Msgs()
o = out()
R.run_random_points(o, boundary=SQUARE, alloc_method=R.ALLOC_UNSTRAT[1], density=2, density_units="Hectare", random_seed=6, messages=m)
check("density: 2 for each hectare of a square kilometer is 200 points",
      read(o)["n"] == 200 and m.has("Density 2 points per hectare of area (geodesic) -> 200 points in all."), (read(o)["n"], m.lines))
o = out()
R.run_random_points(o, boundary=SQUARE, alloc_method=R.ALLOC_UNSTRAT[1], density=1, density_units="Acre", random_seed=6)
check("density: 1 for each acre of a square kilometer is 247 points", read(o)["n"] == 247, read(o)["n"])
o = out()
R.run_random_points(o, boundary=POLYS, strata_method=R.STRATA_POLYGON[1], alloc_method=R.ALLOC_STRAT[4], density=50,
                    density_units="Square Kilometer", random_seed=6)
d = read(o)
check("density, each polygon a stratum: 50 for each square kilometer gives 50, 100 and 150",
      (int(in_rect(d, *P1).sum()), int(in_rect(d, *P2).sum()), int(in_rect(d, *P3).sum())) == (50, 100, 150))

# an extent in place of a layer; a selection; a definition query; a circle
o = out()
R.run_random_points(o, extent=arcpy.Extent(X0, Y0, X0 + 400, Y0 + 200, spatial_reference=SR), n_points=500, random_seed=9)
d = read(o)
check("an extent as the area: 500 points, all within it, in its coordinate system",
      d["n"] == 500 and bool(in_rect(d, X0, Y0, 400, 200).all()) and arcpy.Describe(o).spatialReference.factoryCode == UTM
      and d["fields"] == [], d["n"])
lyr = arcpy.management.MakeFeatureLayer(POLYS, "rp_polys_lyr")[0]
arcpy.management.SelectLayerByAttribute(lyr, "NEW_SELECTION", "Zone = 'B'")
o = out()
R.run_random_points(o, boundary=lyr, n_points=200, random_seed=9)
d = read(o)
check("a selection on the area layer: only the selected polygon gets points", d["n"] == 200 and bool(in_rect(d, *P2).all()))
arcpy.management.Delete(lyr)
# A new layer and a new selection for each run, and the layer is handed over by its NAME, as the tool hands it
# over. (A layer OBJECT made in a script drops its selection as soon as str() is called on it, and the
# stratified paths call str() on the area before they read it. The tool itself never meets such an object:
# run through the toolbox, from the pane or from a script, it honors the selection in every mode.)
arcpy.management.MakeFeatureLayer(POLYS, "rp_polys_lyr2")
arcpy.management.SelectLayerByAttribute("rp_polys_lyr2", "NEW_SELECTION", "Zone = 'B'")
m = Msgs()
o = out()
R.run_random_points(o, boundary="rp_polys_lyr2", strata_method=R.STRATA_POLYGON[1], alloc_method=R.ALLOC_STRAT[0],
                    n_per_stratum=20, random_seed=9, messages=m)
d = read(o)
check("a selection, each polygon a stratum: one stratum, of the selected polygon",
      d["n"] == 20 and bool(in_rect(d, *P2).all()) and m.has("1 stratum defined."), (d["n"], m.lines[:2]))
check("a selection, each polygon a stratum: the run says how many of the layer's features are used (1 of 3)",
      m.has("The area layer has an active selection: 1 of 3 features are used."),
      "it says: %s" % [s for s in m.lines if "selection" in s])
arcpy.management.Delete("rp_polys_lyr2")
q = arcpy.management.MakeFeatureLayer(POLYS, "rp_polys_query", "Zone = 'A'")[0]
o = out()
R.run_random_points(o, boundary=q, n_points=400, random_seed=9)
d = read(o)
check("a definition query on the area layer: only the polygons the layer shows get points",
      d["n"] == 400 and bool((in_rect(d, *P1) | in_rect(d, *P3)).all()))
arcpy.management.Delete(q)
CX, CY, RAD = X0 + 6000.0, Y0 + 6000.0, 1000.0
CIRCLE = make("circle", "POLYGON", [(arcpy.AsShape({"curveRings": [[[CX + RAD, CY], {"a": [[CX + RAD, CY], [CX, CY], 0, 1]}]],
                                                    "spatialReference": {"wkid": UTM}}, True), "A", 1)])
o = out()
R.run_random_points(o, boundary=CIRCLE, n_points=4000, random_seed=10)
d = read(o)
rr = np.hypot(d["x"] - CX, d["y"] - CY)
check("a true circle as the area: every point is within the circle", d["n"] == 4000 and rr.max() <= RAD + 1e-6, rr.max())
check("a true circle: the points fill it evenly, right out to the edge (half lie within 707 m of the center, a "
      "tenth in the outer 51 m)", near_share(int((rr <= RAD / math.sqrt(2)).sum()), 4000, 0.5)
      and near_share(int((rr >= RAD * math.sqrt(0.9)).sum()), 4000, 0.1),
      (int((rr <= RAD / math.sqrt(2)).sum()), int((rr >= RAD * math.sqrt(0.9)).sum())))

# latitude and longitude: a tall box, 30 N to 60 N. By true area 56.6 percent of it lies south of 45 N.
GEO = make("geo_box", "POLYGON", [(poly(box(0.0, 30.0, 1.0, 30.0), wkid=4326), "A", 1)], wkid=4326)
m = Msgs()
o = out()
R.run_random_points(o, boundary=GEO, n_points=6000, random_seed=11, messages=m)
d = read(o)
south = (math.sin(math.radians(45)) - math.sin(math.radians(30))) / (math.sin(math.radians(60)) - math.sin(math.radians(30)))
check("degrees: 6,000 points inside the box, written in latitude and longitude",
      d["n"] == 6000 and bool(in_rect(d, 0.0, 30.0, 1.0, 30.0).all()) and arcpy.Describe(o).spatialReference.factoryCode == 4326)
check("degrees: the points are even by TRUE area (%.1f percent south of 45 N, not half)" % (100 * south),
      near_share(int((d["y"] < 45.0).sum()), 6000, south) and not near_share(int((d["y"] < 45.0).sum()), 6000, 0.5),
      (int((d["y"] < 45.0).sum()), 6000 * south))
check("degrees: the run says that it works in an equal-area projection", m.has("EQUAL-AREA working projection"), m.lines[:2])
GEO1 = make("geo_small", "POLYGON", [(poly(box(-112.0, 35.0, 1.0, 1.0), wkid=4326), "A", 1)], wkid=4326)
o = out()
R.run_random_points(o, boundary=GEO1, n_points=150, min_spacing=5, linear_units="Kilometers", random_seed=11)
d = read(o)
pts = [arcpy.PointGeometry(arcpy.Point(x, y), WGS) for x, y in d["xy"]]
nearest = min(pts[i].angleAndDistanceTo(pts[j], "GEODESIC")[1] for i in range(len(pts)) for j in range(i + 1, len(pts))
              if abs(d["y"][i] - d["y"][j]) < 0.06 and abs(d["x"][i] - d["x"][j]) < 0.08)
check("degrees, spacing of 5 km: all 150 placed, and no two nearer than 5 km over the ground (to 0.1 percent)",
      d["n"] == 150 and nearest >= 5000.0 * 0.999, (d["n"], nearest))
o = out()
R.run_random_points(o, boundary=GEO1, alloc_method=R.ALLOC_UNSTRAT[1], density=1, density_units="Square Kilometer", random_seed=11)
true_km2 = poly(box(-112.0, 35.0, 1.0, 1.0), wkid=4326).getArea("PRESERVE_SHAPE", "SQUAREKILOMETERS")
check("degrees, density: 1 for each square kilometer of the box's true area (about %d)" % round(true_km2),
      abs(read(o)["n"] - true_km2) < 0.005 * true_km2, (read(o)["n"], true_km2))

# the output in another coordinate system than the area's (the Output Coordinate System environment)
def with_output_cs(sr, **kw):
    arcpy.env.outputCoordinateSystem = sr
    try:
        o_ = out()
        R.run_random_points(o_, **kw)
        return o_, None
    except Exception as e:                                            # noqa: BLE001
        return None, e
    finally:
        arcpy.env.outputCoordinateSystem = None


def back_in_square(o_):
    back = [r[0].projectAs(SR) for r in arcpy.da.SearchCursor(o_, ["SHAPE@"])]
    return len(back), all(P1[0] - 2 <= g.firstPoint.X <= P1[0] + P1[2] + 2 and P1[1] - 2 <= g.firstPoint.Y <= P1[1] + P1[3] + 2
                          for g in back)


UTM11 = arcpy.SpatialReference(26911)
o, err = with_output_cs(UTM11, boundary=SQUARE, n_points=300, random_seed=12)
check("output coordinate system, another projection: the points are written in it, and are still inside the area",
      err is None and arcpy.Describe(o).spatialReference.factoryCode == 26911 and back_in_square(o) == (300, True),
      repr(err) if err else back_in_square(o))
o, err = with_output_cs(WGS, boundary=SQUARE, n_points=300, random_seed=12)
check("output coordinate system, latitude and longitude, for an area in a projection: the points are written "
      "in it, and are still inside the area",
      err is None and o is not None and arcpy.Describe(o).spatialReference.factoryCode == 4326
      and back_in_square(o) == (300, True), "the run stops with %r" % err if err else "")
# the same fault was in two tools that share the code: Random Sample Array Generator and Repeating Shapes
arcpy.env.outputCoordinateSystem = WGS
try:
    o_ra, o_rs, err = out("ra"), out("rs"), None
    try:
        RA.run_random_arrays(o_ra, boundary=SQUARE, n_arrays=6, array_shape=RA.SHAPES_AREA[0], n_rows=2, n_cols=2,
                             point_spacing=20, random_seed=12)
    except Exception as e:                                            # noqa: BLE001
        err = e
    check("output coordinate system, latitude and longitude: Random Sample Array Generator runs too, and its "
          "points are inside the area", err is None and arcpy.Exists(o_ra)
          and arcpy.Describe(o_ra).spatialReference.factoryCode == 4326 and back_in_square(o_ra)[1]
          and back_in_square(o_ra)[0] >= 6, repr(err) if err else (arcpy.Exists(o_ra) and back_in_square(o_ra)))
    err = None
    try:
        RS.run_repeating_shapes(RS.HEXAGONS, o_rs, boundary=SQUARE, area=1.0, area_units="Hectares")
    except Exception as e:                                            # noqa: BLE001
        err = e
    hexes = [r[0] for r in arcpy.da.SearchCursor(o_rs, ["SHAPE@"])] if err is None and arcpy.Exists(o_rs) else []
    check("output coordinate system, latitude and longitude: Repeating Shapes runs too, and its hexagons are "
          "one hectare each on the ground",
          err is None and 90 <= len(hexes) <= 170
          and all(abs(h.getArea("GEODESIC", "HECTARES") - 1.0) < 0.01 for h in hexes if h.pointCount == 7),
          repr(err) if err else (len(hexes), [round(h.getArea("GEODESIC", "HECTARES"), 4) for h in hexes[:3]]))
finally:
    arcpy.env.outputCoordinateSystem = None
# with no area layer and no extent given, the Extent environment is the area, in latitude and longitude as well
arcpy.env.outputCoordinateSystem = WGS
arcpy.env.extent = arcpy.Extent(X0, Y0, X0 + 1000, Y0 + 1000, spatial_reference=SR)
try:
    o, err = out(), None
    try:
        R.run_random_points(o, n_points=200, random_seed=12)
    except Exception as e:                                            # noqa: BLE001
        err = e
finally:
    arcpy.env.outputCoordinateSystem = None
    arcpy.env.extent = None
check("the Extent environment as the area, output in latitude and longitude: 200 points inside it",
      err is None and arcpy.Exists(o) and back_in_square(o) == (200, True), repr(err) if err else back_in_square(o))
o, err = with_output_cs(SR, boundary=GEO1, n_points=300, random_seed=12)
d = read(o) if o else {"n": 0}
check("output coordinate system, a projection, for an area in latitude and longitude: the points are written in it",
      err is None and d["n"] == 300 and arcpy.Describe(o).spatialReference.factoryCode == UTM
      and all(-112.001 <= g.firstPoint.X <= -110.999 and 34.999 <= g.firstPoint.Y <= 36.001
              for g in (r[0].projectAs(WGS) for r in arcpy.da.SearchCursor(o, ["SHAPE@"]))), repr(err) if err else d["n"])

# =========================================================================================================
#  3. polylines
# =========================================================================================================
L1 = A((X0, Y0 + 10000), (X0 + 1000, Y0 + 10000))                                     # 1,000 m
L2 = A((X0, Y0 + 11000), (X0 + 2000, Y0 + 11000), (X0 + 2000, Y0 + 12000))            # 3,000 m, with a corner
LINES = make("lines2", "POLYLINE", [(line(*L1.tolist()), "A", 10), (line(*L2.tolist()), "B", 4)])
LOID = [r[0] for r in arcpy.da.SearchCursor(LINES, ["OID@"])]


def on_chain(xy, a):
    """(distance from the line, distance along it) of each point, for a line of straight pieces."""
    best_d = np.full(len(xy), np.inf)
    best_s = np.zeros(len(xy))
    cum = 0.0
    for p, q in zip(a[:-1], a[1:]):
        v = q - p
        L = float(np.hypot(*v))
        t = np.clip(((xy - p) @ v) / (L * L), 0.0, 1.0)
        dd = np.hypot(*(xy - (p + t[:, None] * v)).T)
        better = dd < best_d
        best_d[better] = dd[better]
        best_s[better] = cum + t[better] * L
        cum += L
    return best_d, best_s


o = out()
m = Msgs()
R.run_random_points(o, boundary=LINES, n_points=4000, random_seed=13, messages=m)
d = read(o)
d1, s1 = on_chain(d["xy"], L1)
d2, s2 = on_chain(d["xy"], L2)
on1 = d1 < 1e-6
check("lines, one total: 4,000 points, every one on a line", d["n"] == 4000 and bool((np.minimum(d1, d2) < 1e-6).all()),
      float(np.minimum(d1, d2).max()))
check("lines, one total: the lines get points in proportion to their lengths (1 : 3)", near_share(int(on1.sum()), 4000, 0.25),
      int(on1.sum()))
check("lines, one total: along a line the points are spread evenly (10 equal stretches of the longer line)",
      even(np.bincount(np.clip((s2[~on1] / 300.0).astype(int), 0, 9), minlength=10)),
      np.bincount(np.clip((s2[~on1] / 300.0).astype(int), 0, 9), minlength=10).tolist())
check("lines, one total: no Stratum or source field, and the summary names the lines",
      d["fields"] == [] and (arcpy.metadata.Metadata(o).summary or "").endswith("over polyline features from lines2."), d["fields"])
o = out()
R.run_random_points(o, boundary=LINES, n_points=2000, boundary_gap=100, random_seed=13)
d = read(o)
d1, s1 = on_chain(d["xy"], L1)
d2, s2 = on_chain(d["xy"], L2)
on1 = d1 < 1e-6
check("lines, distance from the ends: no point within 100 m of either end of its line, measured along it",
      d["n"] == 2000 and s1[on1].min() >= 100 - 1e-6 and s1[on1].max() <= 900 + 1e-6 and s2[~on1].min() >= 100 - 1e-6
      and s2[~on1].max() <= 2900 + 1e-6, (s1[on1].min(), s1[on1].max(), s2[~on1].min(), s2[~on1].max()))
check("lines, distance from the ends: what is left is all used, and still shared by length (800 : 2,800)",
      s1[on1].min() < 110 and s2[~on1].max() > 2890 and near_share(int(on1.sum()), 2000, 800.0 / 3600.0), int(on1.sum()))
m = Msgs()
o = out()
R.run_random_points(o, boundary=LINES, strata_method=R.STRATA_POLYLINE[1], alloc_method=R.ALLOC_STRAT_LINE[0], n_per_stratum=10,
                    boundary_gap=600, random_seed=13, messages=m)
d = read(o)
check("lines, distance from the ends: a line shorter than twice the distance gets no points, and is warned of",
      d["n"] == 10 and set(d["Src_FID"]) == {LOID[1]}
      and m.warned("polyline OID %d: shorter than twice the boundary separation -- no points placed there." % LOID[0]),
      (d["n"], m.warnings))
# spacing along the line, and straight across
o = out()
m = Msgs()
R.run_random_points(o, boundary=LINES, strata_method=R.STRATA_POLYLINE[1], alloc_method=R.ALLOC_STRAT_LINE[0], n_per_stratum=18,
                    min_spacing=40, spacing_type=R.SPACING_TYPES[1], random_seed=14, messages=m)
d = read(o)
d1, s1 = on_chain(d["xy"], L1)
d2, s2 = on_chain(d["xy"], L2)
on1 = d1 < 1e-6
check("lines, each a stratum, spacing along the line: 18 on each, none nearer than 40 m along its line",
      int(on1.sum()) == 18 and int((~on1).sum()) == 18 and np.diff(np.sort(s1[on1])).min() >= 40.0
      and np.diff(np.sort(s2[~on1])).min() >= 40.0 and m.has("Minimum spacing is measured ALONG each polyline"),
      (int(on1.sum()), np.diff(np.sort(s1[on1])).min()))
check("lines, each a stratum: every point names its line", [v for v in d["Src_FID"]] == [LOID[0] if a else LOID[1] for a in on1]
      and d["Stratum"] == [str(v) for v in d["Src_FID"]])
o = out()
R.run_random_points(o, boundary=LINES, n_points=40, min_spacing=60, random_seed=14)
d = read(o)
check("lines, spacing straight across: 40 points, no two nearer than 60 m", d["n"] == 40 and pdist(d["xy"]).min() >= 60.0,
      pdist(d["xy"]).min())
m = Msgs()
o = out()
R.run_random_points(o, boundary=LINES, n_points=500, min_spacing=40, random_seed=14, max_attempts=40, messages=m)
d = read(o)
check("lines, too many asked for: warned beforehand and afterward, and the spacing still kept",
      m.warned("500 points at a minimum spacing of 40 meters exceeds the packing limit") and 40 < d["n"] < 100
      and pdist(d["xy"]).min() >= 40.0, (d["n"], m.warnings[:1]))
# a network: three lines meeting at one place
JX, JY = X0 + 1000.0, Y0 + 15000.0
Y_A, Y_B, Y_C = A((X0, JY), (JX, JY)), A((JX, JY), (JX + 1000, JY + 1000)), A((JX, JY), (JX + 1000, JY - 1000))
YNET = make("ynet", "POLYLINE", [(line(*Y_A.tolist()), "A", 1), (line(*Y_B.tolist()), "A", 1), (line(*Y_C.tolist()), "A", 1)])
m = Msgs()
o = out()
R.run_random_points(o, boundary=YNET, n_points=15, min_spacing=120, spacing_type=R.SPACING_TYPES[2], random_seed=15, messages=m)
d = read(o)
arms = [on_chain(d["xy"], a) for a in (Y_A, Y_B, Y_C)]
which = np.argmin(np.vstack([a[0] for a in arms]), axis=0)
to_j = np.array([abs(arms[w][1][k] - (1000.0 if w == 0 else 0.0)) for k, w in enumerate(which)])
worst = min((abs(to_j[i] - to_j[j]) if which[i] == which[j] else to_j[i] + to_j[j])
            for i in range(d["n"]) for j in range(i + 1, d["n"]))
check("lines, network spacing: 15 points on three lines that meet, no two nearer than 120 m by the way through the lines",
      d["n"] == 15 and worst >= 120.0, (d["n"], worst))
check("lines, network spacing: the meeting place is found, and the run says how the spacing is measured",
      any(s.startswith("1 junction") for s in m.lines)
      and m.has("Minimum spacing is measured through the CONNECTED LINE NETWORK"), [s for s in m.lines if "junction" in s])
# across the meeting place two points can be nearer than 120 m in a straight line; that is allowed
straight = pdist(d["xy"]).min()
check("lines, network spacing: the distance is the way through the lines, not the straight line (the nearest pair "
      "is at least 120 m apart by the lines)", worst >= 120.0 and straight <= worst + 1e-9, (straight, worst))
o, err = with_output_cs(WGS, boundary=LINES, n_points=50, random_seed=12)
d = read(o) if o else {"n": 0, "xy": np.empty((0, 2))}
back = np.array([[g.firstPoint.X, g.firstPoint.Y] for g in (r[0].projectAs(SR) for r in arcpy.da.SearchCursor(o, ["SHAPE@"]))]) \
    if o else np.empty((0, 2))
check("output coordinate system, latitude and longitude, for lines in a projection: 50 points, still on the lines "
      "(within 2 m)", err is None and d["n"] == 50
      and bool((np.minimum(on_chain(back, L1)[0], on_chain(back, L2)[0]) < 2.0).all()),
      "the run stops with %r" % err if err else d["n"])
arcpy.management.MakeFeatureLayer(LINES, "rp_lines_lyr")
arcpy.management.SelectLayerByAttribute("rp_lines_lyr", "NEW_SELECTION", "Zone = 'A'")
m = Msgs()
o = out()
R.run_random_points(o, boundary="rp_lines_lyr", n_points=200, random_seed=15, messages=m)
d = read(o)
check("a selection on the line layer: only the selected line gets points",
      d["n"] == 200 and bool((on_chain(d["xy"], L1)[0] < 1e-6).all()),
      "%d of 200 points are on the line that was not selected" % int((on_chain(d["xy"], L1)[0] >= 1e-6).sum()))
check("a selection on the line layer: the run says how many of the layer's lines are used (1 of 2)",
      m.has("The line layer has an active selection: 1 of 2 polylines are used."),
      "it says: %s" % [s for s in m.lines if "selection" in s])
arcpy.management.Delete("rp_lines_lyr")
o = out()
R.run_random_points(o, boundary=LINES, alloc_method=R.ALLOC_UNSTRAT_LINE[1], density=5, density_units="Kilometer", random_seed=15)
check("lines, density: 5 for each kilometer of 4 km of line is 20 points", read(o)["n"] == 20, read(o)["n"])
o = out()
R.run_random_points(o, boundary=LINES, strata_method=R.STRATA_POLYLINE[1], alloc_method=R.ALLOC_STRAT_LINE[1], n_points=400,
                    random_seed=15)
d = read(o)
check("lines, each a stratum, in proportion to length: exactly 100 and 300 of 400",
      (d["Src_FID"].count(LOID[0]), d["Src_FID"].count(LOID[1])) == (100, 300))

# =========================================================================================================
#  4. rasters
# =========================================================================================================
ROWS, COLS, CELL = 40, 50, 30.0
RX0, RYTOP = X0 + 20000.0, Y0 + 20000.0 + ROWS * CELL
VAL = np.zeros((ROWS, COLS), np.uint8)
VAL[0:20, 0:25] = 1                 # 500 cells
VAL[0:20, 25:50] = 2                # 500 cells
VAL[20:40, 10:30] = 3               # 400 cells
VAL[20:40, 30:50] = 1               # 400 cells: a second, separate region of category 1
NOD = VAL == 0                      # 200 cells without data, rows 20 to 39, columns 0 to 9


def write_int_raster(path, a, gt, epsg, nodata=0):
    ds = gdal.GetDriverByName("GTiff").Create(path, a.shape[1], a.shape[0], 1, gdal.GDT_Byte)
    ds.SetGeoTransform(gt)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(nodata)
    ds.GetRasterBand(1).WriteArray(a)
    ds = None


tif = os.path.join(work, "classes.tif")
write_int_raster(tif, VAL, (RX0, CELL, 0.0, RYTOP, 0.0, -CELL), UTM)
RAS = arcpy.management.CopyRaster(tif, os.path.join(gdb, "classes"))[0]
arcpy.management.BuildRasterAttributeTable(RAS, "Overwrite")
arcpy.management.AddField(RAS, "Zone", "TEXT", field_length=10)
arcpy.management.AddField(RAS, "Pop", "LONG")
with arcpy.da.UpdateCursor(RAS, ["Value", "Zone", "Pop"]) as c:
    for v, _z, _p in c:
        c.updateRow([v, "high" if v == 3 else "low", {1: 25, 2: 10, 3: 0}[v]])


def cell_of(d):
    col = np.floor((d["x"] - RX0) / CELL).astype(int)
    row = np.floor((RYTOP - d["y"]) / CELL).astype(int)
    return row, col


def values_at(d):
    row, col = cell_of(d)
    ok = (row >= 0) & (row < ROWS) & (col >= 0) & (col < COLS)
    v = np.zeros(d["n"], int)
    v[ok] = VAL[row[ok], col[ok]]
    return v


m = Msgs()
o = out("r")
R.run_random_points(o, boundary=RAS, n_points=5400, random_seed=20, messages=m)
d = read(o)
v = values_at(d)
check("raster, one total: 5,400 points, none in a cell without data, each naming its cell's category",
      d["n"] == 5400 and bool((v > 0).all()) and d["Category"] == v.tolist() and d["fields"] == ["Category"],
      (d["n"], int((v == 0).sum()), d["fields"]))
check("raster, one total: the categories get points in proportion to their cells (900 : 500 : 400)",
      near_share(int((v == 1).sum()), 5400, 0.5) and near_share(int((v == 2).sum()), 5400, 500 / 1800)
      and near_share(int((v == 3).sum()), 5400, 400 / 1800), np.bincount(v, minlength=4).tolist())
row, col = cell_of(d)
fx = (d["x"] - RX0) / CELL - col
fy = (RYTOP - d["y"]) / CELL - row
check("raster, one total: within their cells the points are anywhere, not at the centers (9 equal parts of a cell)",
      even(np.bincount(np.clip((fx * 3).astype(int), 0, 2) * 3 + np.clip((fy * 3).astype(int), 0, 2), minlength=9)),
      np.bincount(np.clip((fx * 3).astype(int), 0, 2) * 3 + np.clip((fy * 3).astype(int), 0, 2), minlength=9).tolist())
check("raster, one total: the run counts the categories", m.has("3 categories in the attribute table (Value field)."), m.lines[:1])
o = out("r")
R.run_random_points(o, boundary=RAS, strata_method=R.STRATA_RASTER[1], alloc_method=R.ALLOC_STRAT[0], n_per_stratum=100, random_seed=20)
d = read(o)
v = values_at(d)
summ = arcpy.metadata.Metadata(o).summary or ""
check("raster, each category a stratum: the output's summary uses the dialog's own words, and names the seed",
      "(stratify by raster category (value field); equal count in each stratum; seed 20) over the integer raster "
      "classes." in summ, summ)
check("raster, each category a stratum, equal counts: exactly 100 in each, named in Stratum and Category",
      np.bincount(v, minlength=4).tolist() == [0, 100, 100, 100] and d["Stratum"] == [str(x) for x in v]
      and d["Category"] == v.tolist(), np.bincount(v, minlength=4).tolist())
m = Msgs()
o = out("r")
R.run_random_points(o, boundary=RAS, strata_method=R.STRATA_RASTER[2], alloc_method=R.ALLOC_STRAT[0], n_per_stratum=60,
                    random_seed=20, messages=m)
d = read(o)
v = values_at(d)
row, col = cell_of(d)
first = (v == 1) & (row < 20)
second = (v == 1) & (row >= 20)
check("raster, each connected region a stratum: category 1's two separate regions get 60 each (4 regions, 240 points)",
      d["n"] == 240 and (int(first.sum()), int(second.sum()), int((v == 2).sum()), int((v == 3).sum())) == (60, 60, 60, 60)
      and m.has("4 contiguous regions found across 3 categories."),
      (d["n"], int(first.sum()), int(second.sum()), int((v == 2).sum()), int((v == 3).sum())))
strat = np.array(d["Stratum"])
check("raster, regions: each region has its own name, the category and a number",
      sorted(set(d["Stratum"])) == ["1 #1", "1 #2", "2 #1", "3 #1"] and len(set(strat[first])) == 1
      and len(set(strat[second])) == 1 and set(strat[first]) != set(strat[second]), sorted(set(d["Stratum"])))
o = out("r")
R.run_random_points(o, boundary=RAS, strata_method=R.STRATA_RASTER[3], strata_field="Zone", alloc_method=R.ALLOC_STRAT[1],
                    n_points=900, random_seed=20)
d = read(o)
v = values_at(d)
check("raster, strata by a field of its table, in proportion to area: 'low' (1,400 cells) 700 and 'high' (400) 200",
      (d["Stratum"].count("low"), d["Stratum"].count("high")) == (700, 200) and bool(((v == 3) == (np.array(d["Stratum"]) == "high")).all()),
      (d["Stratum"].count("low"), d["Stratum"].count("high")))
o = out("r")
R.run_random_points(o, boundary=RAS, strata_method=R.STRATA_RASTER[1], alloc_method=R.ALLOC_STRAT[2], population_field="Pop", random_seed=20)
v = values_at(read(o))
check("raster, counts from a field of its table: 25, 10 and 0", np.bincount(v, minlength=4).tolist() == [0, 25, 10, 0],
      np.bincount(v, minlength=4).tolist())
m = Msgs()
o = out("r")
R.run_random_points(o, boundary=RAS, alloc_method=R.ALLOC_UNSTRAT[1], density=10, density_units="Hectare", random_seed=20, messages=m)
check("raster, density: 10 for each hectare of the 162 hectares of cells with data is 1,620 points",
      read(o)["n"] == 1620 and m.has("Density 10 points per hectare of cell area (geodesic) -> 1,620 points in all."),
      (read(o)["n"], [s for s in m.lines if s.startswith("Density")]))
o = out("r")
R.run_random_points(o, boundary=RAS, n_points=250, min_spacing=50, random_seed=21)
d = read(o)
check("raster, spacing: 250 points, none in a cell without data, no two nearer than 50 m",
      d["n"] == 250 and bool((values_at(d) > 0).all()) and pdist(d["xy"]).min() >= 50.0, (d["n"], pdist(d["xy"]).min()))


def to_no_data(d):
    """Each point's distance to the nearest cell without data or to the raster's edge."""
    x, y = d["x"], d["y"]
    edge = np.minimum.reduce([x - RX0, RX0 + COLS * CELL - x, RYTOP - y, y - (RYTOP - ROWS * CELL)])
    bx0, bx1, by0, by1 = RX0, RX0 + 10 * CELL, RYTOP - 40 * CELL, RYTOP - 20 * CELL      # the block without data
    dx = np.maximum.reduce([bx0 - x, np.zeros_like(x), x - bx1])
    dy = np.maximum.reduce([by0 - y, np.zeros_like(y), y - by1])
    return np.minimum(edge, np.hypot(dx, dy))


o = out("r")
R.run_random_points(o, boundary=RAS, n_points=3000, boundary_gap=90, random_seed=22)
d = read(o)
dist = to_no_data(d)
check("raster, distance from the boundary: 3,000 points, none in a cell without data",
      d["n"] == 3000 and bool((values_at(d) > 0).all()), d["n"])
check("raster, distance from the boundary: no point nearer than the 90 m asked for to cells without data or to "
      "the raster's edge", dist.min() >= 90.0 - 1e-6,
      "the nearest is %.1f m away; %d of 3,000 points are nearer than 90 m" % (dist.min(), int((dist < 90.0).sum())))
check("raster, distance from the boundary: the cells that are far enough are all used (some point within 5 m of "
      "the limit)", dist.min() < 95.0, dist.min())
# By whole cells: a cell takes points when ALL of it is far enough. 90 m is 3 cells. From the raster's edge that
# leaves rows 3 to 36 and columns 3 to 46 (34 x 44 = 1,496 cells). From the block without data (rows 20 to 39,
# columns 0 to 9) it takes away, of those, the cells in rows 17 to 36 and columns 3 to 12 (20 x 10 = 200): a
# cell 2 rows up and 2 columns over is 2.83 cells away, corner to corner, which is not enough. 1,296 are left.
row, col = cell_of(d)
used = set(zip(row.tolist(), col.tolist()))
want_cells = set((r_, c_) for r_ in range(3, 37) for c_ in range(3, 47) if not (r_ >= 17 and c_ < 13))
check("raster, distance from the boundary: points fall only in the 1,296 cells that lie wholly 90 m in, and in "
      "nearly all of them", used <= want_cells and len(want_cells) == 1296 and len(used) > 0.85 * len(want_cells),
      (len(used), len(want_cells), sorted(used - want_cells)[:5]))
o = out("r")
R.run_random_points(o, boundary=RAS, n_points=3000, boundary_gap=30, random_seed=22)
dist = to_no_data(read(o))
check("raster, distance from the boundary: a distance of one cell (30 m) keeps points 30 m from the data's edge",
      dist.min() >= 30.0 - 1e-6, "the nearest is %.2f m away; %d of 3,000 points are nearer than 30 m" % (
          dist.min(), int((dist < 30.0).sum())))
o = out("r")
R.run_random_points(o, boundary=RAS, n_points=2000, boundary_gap=10, random_seed=22)
dist = to_no_data(read(o))
check("raster, distance from the boundary: a distance of less than a cell (10 m) is kept as well",
      dist.min() >= 10.0 - 1e-6, dist.min())

# a raster in latitude and longitude, 30 N to 60 N: cells shrink northward, and the points must follow true area
gtif = os.path.join(work, "geo_classes.tif")
write_int_raster(gtif, np.ones((60, 4), np.uint8), (0.0, 0.5, 0.0, 60.0, 0.0, -0.5), 4326)
arcpy.management.BuildRasterAttributeTable(gtif, "Overwrite")
m = Msgs()
o = out("r")
R.run_random_points(o, boundary=gtif, n_points=6000, random_seed=23, messages=m)
d = read(o)
check("raster in degrees: the points are even by TRUE area (%.1f percent south of 45 N, not half)" % (100 * south),
      d["n"] == 6000 and near_share(int((d["y"] < 45.0).sum()), 6000, south)
      and not near_share(int((d["y"] < 45.0).sum()), 6000, 0.5) and m.has("cells are picked with their TRUE areas"),
      (int((d["y"] < 45.0).sum()), 6000 * south))

# the Extent environment reads only a window of the raster
arcpy.env.extent = arcpy.Extent(RX0, RYTOP - 10 * CELL, RX0 + 20 * CELL, RYTOP, spatial_reference=SR)
try:
    m = Msgs()
    o = out("r")
    R.run_random_points(o, boundary=RAS, n_points=500, random_seed=24, messages=m)
finally:
    arcpy.env.extent = None
d = read(o)
check("raster, Extent environment: points only within the 10 by 20 cell window, and the run says so",
      d["n"] == 500 and bool(in_rect(d, RX0, RYTOP - 10 * CELL, 20 * CELL, 10 * CELL).all())
      and m.has("Extent environment: reading a 10 x 20 cell window of the raster."), (d["n"], m.lines[:3]))

FLOAT = os.path.join(work, "float.tif")
fds = gdal.GetDriverByName("GTiff").Create(FLOAT, 5, 5, 1, gdal.GDT_Float32)
fds.SetGeoTransform((RX0, CELL, 0.0, RYTOP, 0.0, -CELL))
fsr = osr.SpatialReference()
fsr.ImportFromEPSG(UTM)
fds.SetProjection(fsr.ExportToWkt())
fds.GetRasterBand(1).WriteArray(np.ones((5, 5), np.float32) * 1.5)
fds = None
raises("raster: one of decimals is refused", lambda: R.run_random_points(out("r"), boundary=FLOAT, n_points=5),
       "The raster must be an INTEGER raster")
NOVAT = os.path.join(work, "no_table.tif")
write_int_raster(NOVAT, VAL, (RX0, CELL, 0.0, RYTOP, 0.0, -CELL), UTM)
raises("raster: one without an attribute table is refused, with what to do", lambda: R.run_random_points(
    out("r"), boundary=NOVAT, n_points=5), "run Build Raster Attribute Table on it first")

# =========================================================================================================
#  5. outputs in a folder, and what is refused
# =========================================================================================================
folder = os.path.join(work, "shp")
os.makedirs(folder)
ret = R.run_random_points(os.path.join(folder, "pts"), boundary=POLYS, strata_method=R.STRATA_POLYGON[1],
                          alloc_method=R.ALLOC_STRAT[0], n_per_stratum=5, random_seed=30)
d = read(ret)
check("folder: the points are written as a shapefile, with their fields",
      ret == os.path.join(folder, "pts.shp") and d["n"] == 15 and d["fields"] == ["Stratum", "Src_FID"]
      and d["Stratum"] == [str(v) for v in d["Src_FID"]], (ret, d["fields"]))
PTS = arcpy.management.CreateFeatureclass(gdb, "pts_in", "POINT", spatial_reference=SR)[0]
raises("refused: points as the area", lambda: R.run_random_points(out(), boundary=PTS, n_points=5),
       "The area of interest must be polygons, polylines or an integer raster")
raises("refused: a stratification that is not offered", lambda: R.run_random_points(out(), boundary=POLYS, strata_method="By mood",
                                                                                     n_points=5),
       "Unknown stratification method: By mood")
raises("refused: strata with only an extent", lambda: R.run_random_points(
    out(), extent=arcpy.Extent(X0, Y0, X0 + 10, Y0 + 10, spatial_reference=SR), strata_method=R.STRATA_POLYGON[1], n_per_stratum=5),
    "an extent has no strata")
raises("refused: each polygon a stratum, for a raster", lambda: R.run_random_points(out(), boundary=RAS, strata_method=R.STRATA_POLYGON[1],
                                                                                    n_per_stratum=5),
       "for rasters use categories, contiguous regions or a strata ID field")
raises("refused: raster categories as strata, for polygons", lambda: R.run_random_points(
    out(), boundary=POLYS, strata_method=R.STRATA_RASTER[1], n_per_stratum=5), "needs an integer raster")
raises("refused: strata by a field, with no field", lambda: R.run_random_points(out(), boundary=POLYS, strata_method=R.STRATA_POLYGON[2],
                                                                               n_per_stratum=5),
       "needs the Strata ID field")
raises("refused: counts from a field, with no field", lambda: R.run_random_points(
    out(), boundary=POLYS, strata_method=R.STRATA_POLYGON[1], alloc_method=R.ALLOC_STRAT[2]), "needs the Population field")
raises("refused: counts from a field, without strata", lambda: R.run_random_points(
    out(), boundary=POLYS, alloc_method=R.ALLOC_STRAT[2], population_field="Pop"), "needs stratified sampling")
raises("refused: an area unit of density for lines", lambda: R.run_random_points(
    out(), boundary=LINES, alloc_method=R.ALLOC_UNSTRAT_LINE[1], density=1, density_units="Hectare"),
    "Unknown density unit for this input: Hectare")
raises("refused: a length unit of density for polygons", lambda: R.run_random_points(
    out(), boundary=POLYS, alloc_method=R.ALLOC_UNSTRAT[1], density=1, density_units="Kilometer"),
    "Unknown density unit for this input: Kilometer")
raises("refused: a distance unit that is not offered", lambda: R.run_random_points(out(), boundary=POLYS, n_points=5,
                                                                                   linear_units="Furlongs"),
       "Unknown distance unit: Furlongs")
raises("refused: a way of measuring spacing that is not offered", lambda: R.run_random_points(
    out(), boundary=POLYS, n_points=5, min_spacing=10, spacing_type="As the crow flies"), "Unknown spacing-measurement choice")
raises("refused: no number of points", lambda: R.run_random_points(out(), boundary=POLYS), "Give a total number of points greater than zero.")
raises("refused: no number for each stratum", lambda: R.run_random_points(out(), boundary=POLYS, strata_method=R.STRATA_POLYGON[1]),
       "Give a per-stratum number of points greater than zero.")
raises("refused: a density of zero", lambda: R.run_random_points(out(), boundary=POLYS, alloc_method=R.ALLOC_UNSTRAT[1], density=0),
       "Give a density greater than zero.")
raises("refused: a density so low that it comes to no points", lambda: R.run_random_points(
    out(), boundary=SQUARE, alloc_method=R.ALLOC_UNSTRAT[1], density=0.001, density_units="Hectare"),
    "The requested counts work out to zero points.")
raises("refused: nothing to say where", lambda: R.run_random_points(out(), n_points=5), "No coordinate system")
EMPTY = make("empty_polys", "POLYGON", [])
raises("refused: an area layer with no polygons", lambda: R.run_random_points(out(), boundary=EMPTY, strata_method=R.STRATA_POLYGON[1],
                                                                             n_per_stratum=5),
       "The area input contains no polygons")
m = Msgs()
o = out()
R.run_random_points(o, boundary=POLYS, n_points=50, min_spacing=10, spacing_type=R.SPACING_TYPES[1], random_seed=31, messages=m)
check("spacing along a line asked for polygons: warned, and the plain distance is used",
      m.warned("Along-the-line and network spacing apply to polyline areas of interest only")
      and pdist(read(o)["xy"]).min() >= 10.0, m.warnings)
check("the inputs are not changed", int(arcpy.management.GetCount(POLYS)[0]) == 3 and int(arcpy.management.GetCount(LINES)[0]) == 2
      and np.array_equal(arcpy.RasterToNumPyArray(RAS, nodata_to_value=0), VAL))

print("\n%d passed, %d failed%s" % (passed, failed, " (and %d known faults not yet fixed)" % known if known else ""))
arcpy.management.ClearWorkspaceCache()
if os.environ.get("KEEP_TEST_DATA"):
    print("kept:", work)
else:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
