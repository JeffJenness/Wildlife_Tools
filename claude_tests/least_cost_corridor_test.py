# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award notice, marking of modified versions, use of names,
# and an additional permission to link with ArcGIS Pro).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Least-Cost Corridor (least_cost_corridor.py). Every corridor surface the
engine writes is compared, cell by cell, with one worked out here by a
second, plain method (Dijkstra's algorithm written out with a priority
queue, sharing no code with the engine). The cost rasters are 24 by 36
cells of made-up costs, and the termini are rectangles on cell edges, so
which cells are sources is never in doubt.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" least_cost_corridor_test.py
"""
import heapq
import json
import math
import os
import shutil
import sys
import tempfile

import numpy as np
from osgeo import gdal, osr
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import clip_tools as CT  # noqa: E402
import land_facet_linkage as L  # noqa: E402
import least_cost_corridor as C  # noqa: E402
import mahalanobis as MH  # noqa: E402

gdal.UseExceptions()
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


known = 0


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


arcpy.env.overwriteOutput = True
work = tempfile.mkdtemp(prefix="corridor_test_")
H, W, CELL = 24, 36, 100.0
X0, YTOP = 400000.0, 3900000.0 + H * CELL
UTM = 26912
DIAG = math.hypot(CELL, CELL)


# ---------------------------------------------------------------------------------------------------------
#  the second method
# ---------------------------------------------------------------------------------------------------------
def planar(r, c, r2, c2):
    return CELL if (r == r2 or c == c2) else DIAG


def accumulated(cost, valid, sources, dist=planar):
    """Least accumulated cost from the nearest source cell to every cell, moving to any of the eight
    neighbors; a move costs the mean of the two cells' costs times the distance between their centers."""
    d = np.full(cost.shape, np.inf)
    heap = []
    for r, c in zip(*np.nonzero(sources & valid)):
        d[r, c] = 0.0
        heapq.heappush(heap, (0.0, int(r), int(c)))
    while heap:
        dd, r, c = heapq.heappop(heap)
        if dd > d[r, c]:
            continue
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                r2, c2 = r + dr, c + dc
                if (dr or dc) and 0 <= r2 < cost.shape[0] and 0 <= c2 < cost.shape[1] and valid[r2, c2]:
                    nd = dd + 0.5 * (cost[r, c] + cost[r2, c2]) * dist(r, c, r2, c2)
                    if nd < d[r2, c2]:
                        d[r2, c2] = nd
                        heapq.heappush(heap, (nd, r2, c2))
    return d


def corridor_of(cost, valid, src_a, src_b, dist=planar):
    """(corridor surface with NaN where there is none, which cells have a value)"""
    da, db = accumulated(cost, valid, src_a, dist), accumulated(cost, valid, src_b, dist)
    both = np.isfinite(da) & np.isfinite(db)
    out = np.full(cost.shape, np.nan)
    if both.any():
        out[both] = da[both] + db[both]
        corridor_of.largest_sum = float(out[both].max())       # for slice_of, below
        out[both] -= out[both].min()
    return out, both


def slice_of(want, both, p):
    """The cells of the p percent slice of the corridor surface just worked out by corridor_of: those at or under
    that percentile of its values. Cells whose values are equal but for rounding go in together: every cell
    along one best route from block to block has the same sum, worked out by a different chain of additions,
    and "equal" is taken as within a billionth of the largest sum."""
    return both & (want <= np.percentile(want[both], p) + 1e-9 * corridor_of.largest_sum)


# ---------------------------------------------------------------------------------------------------------
#  the made-up data
# ---------------------------------------------------------------------------------------------------------
def write_cost(path, a, nodata_cells=None, epsg=UTM, gt=None):
    ds = gdal.GetDriverByName("GTiff").Create(path, a.shape[1], a.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform(gt or (X0, CELL, 0.0, YTOP, 0.0, -CELL))
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(epsg)
    ds.SetProjection(sr.ExportToWkt())
    out = a.astype(np.float32)
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999.0)
    if nodata_cells is not None:
        out[nodata_cells] = -9999.0
    band.WriteArray(out)
    ds = None
    return path


def cells(r0, r1, c0, c1):
    m = np.zeros((H, W), bool)
    m[r0:r1, c0:c1] = True
    return m


def rect(r0, r1, c0, c1, sr, gt=None, shrink=0.0):
    x0, dx, _a, y0, _b, dy = gt or (X0, CELL, 0.0, YTOP, 0.0, -CELL)
    xa, xb = x0 + c0 * dx + shrink, x0 + c1 * dx - shrink
    ya, yb = y0 + r1 * dy + shrink, y0 + r0 * dy - shrink
    return arcpy.Polygon(arcpy.Array([arcpy.Point(xa, ya), arcpy.Point(xa, yb), arcpy.Point(xb, yb),
                                      arcpy.Point(xb, ya), arcpy.Point(xa, ya)]), sr)


def termini(gdb, name, rows, wkid=UTM):
    """rows: (category label, category id, block, polygon)"""
    fc = arcpy.management.CreateFeatureclass(gdb, name, "POLYGON", spatial_reference=arcpy.SpatialReference(wkid))[0]
    arcpy.management.AddField(fc, "Category", "TEXT", field_length=100)
    arcpy.management.AddField(fc, "CategoryID", "LONG")
    arcpy.management.AddField(fc, "Block", "LONG")
    with arcpy.da.InsertCursor(fc, ["Category", "CategoryID", "Block", "SHAPE@"]) as c:
        for r in rows:
            c.insertRow(r)
    return fc


def read_raster(path):
    a = arcpy.RasterToNumPyArray(path, nodata_to_value=-1.0e30).astype(np.float64)
    a[a < -1.0e29] = np.nan
    return a


def same_surface(got, want, both):
    """The engine's raster against the second method: values where there should be values, none elsewhere."""
    if got.shape != want.shape or not np.array_equal(np.isfinite(got), both):
        return False
    return bool(np.allclose(got[both], want[both], rtol=2e-6, atol=0.02))


def worst(got, want, both):
    if got.shape != want.shape:
        return "shapes %s and %s" % (got.shape, want.shape)
    if not np.array_equal(np.isfinite(got), both):
        return "%d cells differ in having a value" % int((np.isfinite(got) != both).sum())
    return "largest difference %.6f of values up to %.1f" % (float(np.abs(got[both] - want[both]).max()),
                                                             float(want[both].max()))


def polygon_rows(fc):
    f = ["Category", "CategoryID", "Block_A", "Block_B", "Percentile", "CorridorID", "Area_SqM", "Cells", "SHAPE@"]
    return [dict(zip(f, r)) for r in arcpy.da.SearchCursor(fc, f)]


rng = np.random.default_rng(11)
COST = rng.uniform(1.0, 5.0, (H, W)).astype(np.float32).astype(np.float64)
COST[0:18, 18] = 50.0                              # a ridge that is costly to cross, open at its south end
NODATA = cells(3, 8, 8, 13)                        # a block without data...
NODATA[5, 10] = False                              # ...around one lone cell that nothing can reach
VALID = ~NODATA
SR = arcpy.SpatialReference(UTM)
B1 = cells(10, 13, 1, 4) | cells(15, 17, 1, 3)     # Wildland Block 1 has two termini
B2 = cells(9, 13, 31, 34)
B3 = cells(1, 4, 24, 27)
gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
cost_tif = write_cost(os.path.join(work, "cost.tif"), COST, NODATA)
T12 = termini(gdb, "termini_12", [("Black bear", 7, 1, rect(10, 13, 1, 4, SR)), ("Black bear", 7, 1, rect(15, 17, 1, 3, SR)),
                                  ("Black bear", 7, 2, rect(9, 13, 31, 34, SR))])
TID = "44444444-dddd-4ddd-8ddd-444444444444"
L.write(T12, {"schema": L.SCHEMA, "record": L.TERMINI, "analysis_id": TID})

# ---- 1. small pieces
check("pairs: read from text in any of three forms",
      C._parse_block_pairs(["1-2", "3,4", "'5:6'"]) == {frozenset((1, 2)), frozenset((3, 4)), frozenset((5, 6))})
check("pairs: the order within a pair does not matter, and text that is no pair is ignored",
      C._parse_block_pairs(["2-1", "x-y", "7"]) == {frozenset((1, 2))} and C._parse_block_pairs([]) is None
      and C._parse_block_pairs(["nothing"]) is None)
check("percentiles: the default list is 0.1 and then 1 to 10",
      C.DEFAULT_PERCENTILES == [0.1, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])

# the engine's own graph and solver against the second method, with no files involved
from scipy.sparse.csgraph import dijkstra  # noqa: E402
graph = C._build_cost_graph(COST, VALID, (X0, CELL, 0.0, YTOP, 0.0, -CELL), False)
for nm, src in (("Block 1", B1), ("Block 2", B2), ("one corner cell", cells(0, 1, 0, 1))):
    eng = dijkstra(graph, directed=False, indices=np.flatnonzero((src & VALID).ravel()), min_only=True).reshape(H, W)
    mine = accumulated(COST, VALID, src)
    check("accumulated cost from %s: the same by both methods, in every cell" % nm,
          np.array_equal(np.isfinite(eng), np.isfinite(mine))
          and np.allclose(eng[np.isfinite(mine)], mine[np.isfinite(mine)], rtol=1e-12, atol=1e-9),
          int((np.isfinite(eng) != np.isfinite(mine)).sum()))
check("accumulated cost: the lone cell inside the block without data is reached from nowhere",
      not np.isfinite(accumulated(COST, VALID, B1)[5, 10]))
# a surface that costs 1 everywhere: the answer is plain distance, known without any method
flat = np.ones((H, W))
g1 = C._build_cost_graph(flat, np.ones((H, W), bool), (X0, CELL, 0.0, YTOP, 0.0, -CELL), False)
d = dijkstra(g1, directed=False, indices=[0], min_only=True).reshape(H, W)
rr, cc = np.indices((H, W))
octile = CELL * (np.maximum(rr, cc) - np.minimum(rr, cc)) + DIAG * np.minimum(rr, cc)
check("accumulated cost over a surface of cost 1: straight and diagonal steps of the true lengths",
      np.allclose(d, octile, rtol=1e-12), float(np.abs(d - octile).max()))
d = dijkstra(g1, directed=False, indices=np.flatnonzero(cells(0, H, 0, 1).ravel()), min_only=True).reshape(H, W)
check("accumulated cost from a whole edge: each cell's distance is to the NEAREST source cell (100 m per column)",
      np.allclose(d, CELL * cc, rtol=1e-12))
check("accumulated cost: a cost of 3 everywhere gives three times the distances",
      np.allclose(dijkstra(C._build_cost_graph(3.0 * flat, np.ones((H, W), bool), (X0, CELL, 0.0, YTOP, 0.0, -CELL),
                                               False), directed=False, indices=[0], min_only=True).reshape(H, W),
                  3.0 * octile, rtol=1e-12))

# ---- 2. one corridor between two blocks
out_gdb = arcpy.management.CreateFileGDB(work, "out.gdb")[0]
out_fc = os.path.join(out_gdb, "corridors")
PCTS = [1, 5, 10, 50]
m = Msgs()
n = C.run_least_cost_corridor(T12, cost_raster=cost_tif, out_workspace=out_gdb, out_fc=out_fc, percentiles=PCTS,
                              messages=m)
want, both = corridor_of(COST, VALID, B1, B2)
ras = os.path.join(out_gdb, "Cor_Black_bear_B1_B2")
check("one corridor: one raster is written, named for the category and the two blocks",
      n == 1 and arcpy.Exists(ras), (n, arcpy.Exists(ras)))
got = read_raster(ras) if arcpy.Exists(ras) else np.zeros((1, 1))
check("one corridor: the surface is the same as the second method's, in every cell", same_surface(got, want, both),
      worst(got, want, both))
check("one corridor: cells without data, and the lone cell nothing can reach, have no value",
      not np.isfinite(got[NODATA]).any() and not np.isfinite(got[5, 10]) and int(np.isfinite(got).sum()) == int(both.sum()))
check("one corridor: the surface is zero at its lowest and nowhere negative",
      abs(np.nanmin(got)) < 1e-6 and float(np.nanmin(got)) >= 0.0, np.nanmin(got))
zero_lab, _n = ndimage.label(np.isfinite(got) & (got <= 0.02), structure=np.ones((3, 3), int))
joined = set(zero_lab[B1 & (zero_lab > 0)]) & set(zero_lab[B2 & (zero_lab > 0)])
check("one corridor: the zero cells form one unbroken path from Block 1 to Block 2", len(joined) == 1, joined)
path_cells = zero_lab == (list(joined)[0] if joined else -1)
check("one corridor: that path goes round the costly ridge through its open south end",
      not path_cells[0:18, 18].any() and path_cells[18:24, 18].any(), np.nonzero(path_cells[:, 18])[0])
d = arcpy.Describe(ras)
check("one corridor: the raster has the cost raster's cells, place and coordinate system",
      (d.width, d.height, d.meanCellWidth) == (W, H, CELL) and abs(d.extent.XMin - X0) < 1e-6
      and abs(d.extent.YMax - YTOP) < 1e-6 and d.spatialReference.factoryCode == UTM,
      (d.width, d.height, d.meanCellWidth, d.extent.XMin, d.extent.YMax))

rows = polygon_rows(out_fc)
check("polygons: every row names the category, its id and the two blocks",
      rows and all((r["Category"], r["CategoryID"], r["Block_A"], r["Block_B"]) == ("Black bear", 7, 1, 2) for r in rows),
      rows[:1])
check("polygons: the slices asked for, and no others", sorted(set(r["Percentile"] for r in rows)) == [1.0, 5.0, 10.0, 50.0],
      sorted(set(r["Percentile"] for r in rows)))
check("polygons: the widest slice is written first and the narrowest last",
      [r["Percentile"] for r in rows] == sorted((r["Percentile"] for r in rows), reverse=True))
check("polygons: numbered 1, 2, 3 ... through the whole output",
      [r["CorridorID"] for r in rows] == list(range(1, len(rows) + 1)), [r["CorridorID"] for r in rows])
raw_1 = int((both & (want <= np.percentile(want[both], 1))).sum())
check("polygons: the 1 percent slice is the whole least-cost path (31 cells), not the %d cells a bare comparison "
      "with the percentile picks out of it" % raw_1,
      sum(r["Cells"] for r in rows if r["Percentile"] == 1.0) == int(path_cells.sum()) > raw_1,
      (sum(r["Cells"] for r in rows if r["Percentile"] == 1.0), int(path_cells.sum()), raw_1))
for p in PCTS:
    mask = slice_of(want, both, p)
    pieces = ndimage.label(mask, structure=np.ones((3, 3), int))[1]
    mine = [r for r in rows if r["Percentile"] == float(p)]
    check("polygons, %g percent: the cells are those at or under that percentile of the surface, with cells of "
          "equal value kept together (%d cells)" % (p, mask.sum()),
          sum(r["Cells"] for r in mine) == int(mask.sum()), (sum(r["Cells"] for r in mine), int(mask.sum())))
    check("polygons, %g percent: one row for each separate piece (%d)" % (p, pieces), len(mine) == pieces,
          (len(mine), pieces))
    check("polygons, %g percent: each shape covers its cells exactly, and Area_SqM is that area" % p,
          all(abs(r["SHAPE@"].area - r["Cells"] * CELL * CELL) < 1.0
              and abs(r["Area_SqM"] - r["Cells"] * CELL * CELL) < 0.002 * r["Cells"] * CELL * CELL for r in mine),
          [(r["SHAPE@"].area, r["Cells"], r["Area_SqM"]) for r in mine][:2])
    share = 100.0 * sum(r["Cells"] for r in mine) / both.sum()
    check("polygons, %g percent: the slice holds at least that share of the surface's cells (cells of equal value "
          "at the threshold all come in, so it can hold more)" % p, share >= p, share)


def union(geoms):
    u = geoms[0]
    for g in geoms[1:]:
        u = u.union(g)
    return u


u = dict((p, union([r["SHAPE@"] for r in rows if r["Percentile"] == float(p)])) for p in PCTS)
check("polygons: each narrower slice lies inside the next wider one",
      all(u[a].difference(u[b]).area < 1.0 for a, b in ((1, 5), (5, 10), (10, 50))),
      [u[a].difference(u[b]).area for a, b in ((1, 5), (5, 10), (10, 50))])
on_path = [u[1].contains(arcpy.PointGeometry(arcpy.Point(X0 + (c + 0.5) * CELL, YTOP - (r + 0.5) * CELL), SR))
           for r, c in zip(*np.nonzero(path_cells))]
check("polygons: the wider slices (5 percent and up) contain the whole least-cost path",
      all(u[5].contains(arcpy.PointGeometry(arcpy.Point(X0 + (c + 0.5) * CELL, YTOP - (r + 0.5) * CELL), SR))
          for r, c in zip(*np.nonzero(path_cells))))
# The cells of the least-cost path all have the value zero but for rounding in the 13th decimal place. This
# slice (1 percent of 839 cells) holds fewer cells than the path has (31): before the correction of 2026-11-23
# the threshold fell among those rounding differences and the slice took 16 of the path's cells, a "corridor"
# that stopped partway between the blocks.
check("polygons: a slice that holds any of the least-cost path holds all of it (the 1 percent slice here)",
      all(on_path), "%d of the path's %d cells are in the slice" % (sum(on_path), len(on_path)))
check("messages: the corridor and the totals are reported",
      m.has("Black bear (Wildland Block 1 <-> 2): corridor written (%d percentile polygon pieces)." % len(rows))
      and m.has("1 corridor raster and %d polygon pieces written." % len(rows)), m.lines[-2:])
check("messages: nothing warned about", m.warnings == [], m.warnings)

rec = L.read(ras, L.CORRIDOR) or {}
check("record on the raster: what it is, of which category and blocks, at which slices",
      rec.get("tool") == "Least-Cost Corridor" and rec.get("category") == {"category_id": 7, "label": "Black bear"}
      and (rec.get("block_a"), rec.get("block_b")) == (1, 2) and rec.get("percentiles") == [1.0, 5.0, 10.0, 50.0]
      and rec.get("geodesic") is False, rec)
check("record on the raster: it carries the analysis id of the termini it came from, and names its sources",
      rec.get("analysis_id") == TID and rec.get("source_analysis_id") == TID
      and rec.get("source_termini") == T12 and rec.get("source_cost_raster") == cost_tif, rec)
frec = L.read(out_fc, L.CORRIDOR) or {}
check("record on the polygons: the same analysis, with each corridor and its number of pieces listed",
      frec.get("analysis_id") == TID and len(frec.get("corridors") or []) == 1
      and frec["corridors"][0].get("n_pieces") == len(rows) and frec["corridors"][0].get("label") == "Black bear"
      and (frec["corridors"][0].get("block_a"), frec["corridors"][0].get("block_b")) == (1, 2), frec)
summ = arcpy.metadata.Metadata(out_fc).summary or ""
check("metadata of the polygons: the summary counts the rasters and lists the slices",
      "1 corridor raster across 4 percentile thresholds each (1%, 5%, 10%, 50%)." in summ, summ)
lyrx = C.write_layer_file(out_fc, Msgs())
doc = json.load(open(lyrx, encoding="utf-8")) if lyrx and os.path.isfile(lyrx) else {}
layer = (doc.get("layerDefinitions") or [{}])[0]
classes = (((layer.get("renderer") or {}).get("groups") or [{}])[0].get("classes") or [])
check("layer file: one class for each slice that is present, drawn widest first",
      [c.get("label") for c in classes] == ["1%", "5%", "10%", "50%"]
      and layer.get("featureSortInfos") == [{"type": "CIMFeatureSortInfo", "fieldName": "Percentile",
                                             "sortDirection": "Descending"}], [c.get("label") for c in classes])
check("the cost raster and the termini are not changed",
      np.allclose(gdal.Open(cost_tif).ReadAsArray()[VALID], COST[VALID]) and int(arcpy.management.GetCount(T12)[0]) == 3)

# the default slices
out_fc2 = os.path.join(out_gdb, "corridors_default")
C.run_least_cost_corridor(T12, cost_raster=cost_tif, out_workspace=out_gdb, out_prefix="Def", out_fc=out_fc2)
check("default slices: 0.1 percent and 1 to 10 percent",
      sorted(set(r["Percentile"] for r in polygon_rows(out_fc2))) == C.DEFAULT_PERCENTILES
      and arcpy.Exists(os.path.join(out_gdb, "Def_Black_bear_B1_B2")), sorted(set(r["Percentile"] for r in polygon_rows(out_fc2))))

# ---- 3. three blocks: every pair, or only the pairs asked for
T123 = termini(gdb, "termini_123", [("Black bear", 7, 1, rect(10, 13, 1, 4, SR)), ("Black bear", 7, 1, rect(15, 17, 1, 3, SR)),
                                    ("Black bear", 7, 2, rect(9, 13, 31, 34, SR)), ("Black bear", 7, 3, rect(1, 4, 24, 27, SR))])
gdb3 = arcpy.management.CreateFileGDB(work, "three.gdb")[0]
m = Msgs()
n = C.run_least_cost_corridor(T123, cost_raster=cost_tif, out_workspace=gdb3, out_fc=os.path.join(gdb3, "corridors"),
                              percentiles=[5], messages=m)
check("three blocks: a corridor for every pair", n == 3, n)
for (a, sa), (b, sb) in (((1, B1), (2, B2)), ((1, B1), (3, B3)), ((2, B2), (3, B3))):
    want, both = corridor_of(COST, VALID, sa, sb)
    got = read_raster(os.path.join(gdb3, "Cor_Black_bear_B%d_B%d" % (a, b)))
    check("three blocks: the surface between Blocks %d and %d is the second method's" % (a, b),
          same_surface(got, want, both), worst(got, want, both))
rows = polygon_rows(os.path.join(gdb3, "corridors"))
check("three blocks: the polygons of all three corridors are in the one feature class",
      sorted(set((r["Block_A"], r["Block_B"]) for r in rows)) == [(1, 2), (1, 3), (2, 3)]
      and [r["CorridorID"] for r in rows] == list(range(1, len(rows) + 1)), sorted(set((r["Block_A"], r["Block_B"]) for r in rows)))
gdb3b = arcpy.management.CreateFileGDB(work, "three_b.gdb")[0]
n = C.run_least_cost_corridor(T123, cost_raster=cost_tif, out_workspace=gdb3b, out_fc=os.path.join(gdb3b, "corridors"),
                              percentiles=[5], block_pairs=["3-1"])
check("three blocks: asked for the pair 3-1 only, only that corridor is built",
      n == 1 and arcpy.Exists(os.path.join(gdb3b, "Cor_Black_bear_B1_B3"))
      and not arcpy.Exists(os.path.join(gdb3b, "Cor_Black_bear_B1_B2")), n)

# a selection on the termini layer
lyr = arcpy.management.MakeFeatureLayer(T123, "lcc_termini_lyr")[0]
arcpy.management.SelectLayerByAttribute(lyr, "NEW_SELECTION", "Block IN (2, 3)")
gdb3c = arcpy.management.CreateFileGDB(work, "three_c.gdb")[0]
m = Msgs()
n = C.run_least_cost_corridor(lyr, cost_raster=cost_tif, out_workspace=gdb3c, out_fc=os.path.join(gdb3c, "corridors"),
                              percentiles=[5], messages=m)
check("selection on the termini: only the selected blocks are joined",
      n == 1 and arcpy.Exists(os.path.join(gdb3c, "Cor_Black_bear_B2_B3"))
      and m.has("The termini layer has an active selection: 2 of 4 features are used."), (n, m.lines[:2]))
check("selection on the termini: the record names the dataset, not the layer",
      (L.read(os.path.join(gdb3c, "Cor_Black_bear_B2_B3")) or {}).get("source_termini") == T123)
arcpy.management.Delete(lyr)

# ---- 4. surfaces that are cut in two, costs below zero, termini on cells without data
WALL = NODATA | cells(0, H, 18, 19)                # no data all the way down column 18
wall_tif = write_cost(os.path.join(work, "cost_wall.tif"), COST, WALL)
gdb4 = arcpy.management.CreateFileGDB(work, "wall.gdb")[0]
m = Msgs()
n = C.run_least_cost_corridor(T123, cost_raster=wall_tif, out_workspace=gdb4, out_fc=os.path.join(gdb4, "corridors"),
                              percentiles=[5], messages=m)
want, both = corridor_of(COST, ~WALL, B2, B3)
got = read_raster(os.path.join(gdb4, "Cor_Black_bear_B2_B3"))
check("cut in two: the pair on the same side is joined, and the two pairs across the gap are warned of",
      n == 1 and m.warned("no path exists between Wildland Blocks 1 and 2")
      and m.warned("no path exists between Wildland Blocks 1 and 3") and len(m.warnings) == 2, (n, m.warnings))
check("cut in two: the corridor that is built has values only on its own side of the gap",
      same_surface(got, want, both) and not np.isfinite(got[:, :18]).any(), worst(got, want, both))
raises("cut in two: with no pair that can be joined, the run stops and says why",
       lambda: C.run_least_cost_corridor(T12, cost_raster=wall_tif, out_workspace=gdb4,
                                         out_fc=os.path.join(gdb4, "none"), percentiles=[5]),
       "No corridors could be built.")
check("cut in two: and leaves no polygons behind", not arcpy.Exists(os.path.join(gdb4, "none")))

NEG = COST.copy()
NEG[20:24, 10:14] = -1.0
neg_tif = write_cost(os.path.join(work, "cost_neg.tif"), NEG, NODATA)
gdb5 = arcpy.management.CreateFileGDB(work, "neg.gdb")[0]
m = Msgs()
C.run_least_cost_corridor(T12, cost_raster=neg_tif, out_workspace=gdb5, out_fc=os.path.join(gdb5, "corridors"),
                          percentiles=[5], messages=m)
want, both = corridor_of(NEG, VALID & (NEG >= 0), B1, B2)
got = read_raster(os.path.join(gdb5, "Cor_Black_bear_B1_B2"))
check("costs below zero: warned of, with their number, and treated as ground that cannot be crossed",
      m.warned("Black bear: 16 cells of the cost raster are negative and were treated as impassable")
      and same_surface(got, want, both) and not np.isfinite(got[20:24, 10:14]).any(), (m.warnings, worst(got, want, both)))

T_ND = termini(gdb, "termini_nodata", [("Black bear", 7, 1, rect(10, 13, 1, 4, SR)), ("Black bear", 7, 2, rect(9, 13, 31, 34, SR)),
                                       ("Black bear", 7, 4, rect(3, 5, 8, 10, SR))])
gdb6 = arcpy.management.CreateFileGDB(work, "nd.gdb")[0]
m = Msgs()
n = C.run_least_cost_corridor(T_ND, cost_raster=cost_tif, out_workspace=gdb6, out_fc=os.path.join(gdb6, "corridors"),
                              percentiles=[5], messages=m)
check("termini on cells without data: that block is passed over with a warning, and the others are joined",
      n == 1 and m.warned("Wildland Block 4's termini fall entirely on NoData or unusable cells")
      and arcpy.Exists(os.path.join(gdb6, "Cor_Black_bear_B1_B2")), (n, m.warnings))
T_ONE = termini(gdb, "termini_one_block", [("Black bear", 7, 1, rect(10, 13, 1, 4, SR))])
m = Msgs()
raises("one block only: nothing to join", lambda: C.run_least_cost_corridor(
    T_ONE, cost_raster=cost_tif, out_workspace=gdb6, out_fc=os.path.join(gdb6, "one"), percentiles=[5], messages=m),
    "No corridors could be built.")
check("one block only: the reason is given first",
      m.warned("Black bear: termini exist in fewer than 2 Wildland Blocks; no corridor can be built."), m.warnings)

# ---- 5. termini in another coordinate system than the cost raster
WGS = arcpy.SpatialReference(4326)
# (each rectangle is drawn 20 m inside its cells' edges, so that a meter's shift between the two datums cannot
# move a cell's center across an edge)
T_WGS = termini(gdb, "termini_wgs", [
    ("Black bear", 7, 1, CT.project_geometry(rect(10, 13, 1, 4, SR, shrink=20.0), WGS)),
    ("Black bear", 7, 1, CT.project_geometry(rect(15, 17, 1, 3, SR, shrink=20.0), WGS)),
    ("Black bear", 7, 2, CT.project_geometry(rect(9, 13, 31, 34, SR, shrink=20.0), WGS))], wkid=4326)
gdb7 = arcpy.management.CreateFileGDB(work, "wgs.gdb")[0]
C.run_least_cost_corridor(T_WGS, cost_raster=cost_tif, out_workspace=gdb7, out_fc=os.path.join(gdb7, "corridors"),
                          percentiles=[5])
want, both = corridor_of(COST, VALID, B1, B2)
got = read_raster(os.path.join(gdb7, "Cor_Black_bear_B1_B2"))
check("termini in latitude and longitude, raster in UTM: the same corridor as with termini in UTM",
      same_surface(got, want, both), worst(got, want, both))
check("termini in latitude and longitude: the polygons are in the raster's coordinate system",
      arcpy.Describe(os.path.join(gdb7, "corridors")).spatialReference.factoryCode == UTM)

# ---- 6. a cost raster in latitude and longitude: true distances on the ground
GT_GEO = (-112.0, 0.01, 0.0, 35.24, 0.0, -0.01)
geo_tif = write_cost(os.path.join(work, "cost_geo.tif"), COST, NODATA, epsg=4326, gt=GT_GEO)
T_GEO = termini(gdb, "termini_geo", [("Black bear", 7, 1, rect(10, 13, 1, 4, WGS, gt=GT_GEO)),
                                     ("Black bear", 7, 1, rect(15, 17, 1, 3, WGS, gt=GT_GEO)),
                                     ("Black bear", 7, 2, rect(9, 13, 31, 34, WGS, gt=GT_GEO))], wkid=4326)
R_EARTH = 6371008.8                                # the mean radius of the earth, in meters


def on_the_ground(r, c, r2, c2):
    la, lo = math.radians(35.24 - (r + 0.5) * 0.01), math.radians(-112.0 + (c + 0.5) * 0.01)
    lb, lob = math.radians(35.24 - (r2 + 0.5) * 0.01), math.radians(-112.0 + (c2 + 0.5) * 0.01)
    t = math.sin((la - lb) / 2) ** 2 + math.cos(la) * math.cos(lb) * math.sin((lo - lob) / 2) ** 2
    return 2.0 * R_EARTH * math.asin(math.sqrt(t))


gdb8 = arcpy.management.CreateFileGDB(work, "geo.gdb")[0]
m = Msgs()
C.run_least_cost_corridor(T_GEO, cost_raster=geo_tif, out_workspace=gdb8, out_fc=os.path.join(gdb8, "corridors"),
                          percentiles=[5], messages=m)
want, both = corridor_of(COST, VALID, B1, B2, on_the_ground)
got = read_raster(os.path.join(gdb8, "Cor_Black_bear_B1_B2"))
big = both & (want > 0.05 * np.nanmax(want))
ratio = got[big] / want[big]
check("degrees: the run says that it measures true distances",
      m.has("Black bear: the cost raster's coordinate system is geographic -- using true geodesic distances."), m.lines[:3])
check("degrees: the surface has values in the same cells as the second method's",
      np.array_equal(np.isfinite(got), both), int((np.isfinite(got) != both).sum()))
check("degrees: its values are the second method's, measured over the ground (within 0.2 percent, the same "
      "everywhere)", abs(ratio.mean() - 1.0) < 0.002 and ratio.std() < 1e-4, (ratio.mean(), ratio.std()))
gg = C._build_cost_graph(np.ones((H, W)), np.ones((H, W), bool), GT_GEO, True)
ns, ew = gg[0, W], gg[0, 1]                        # one step south, one step east, from the top left cell
check("degrees: a step of 0.01 degree is about 1,112 m northward and less eastward, by the cosine of the latitude",
      abs(ns - 1112.0) < 2.5 and abs(ew / ns - math.cos(math.radians(35.235))) < 1e-4, (ns, ew, ew / ns))
check("degrees: the record says the distances were geodesic",
      (L.read(os.path.join(gdb8, "Cor_Black_bear_B1_B2")) or {}).get("geodesic") is True)
grow = polygon_rows(os.path.join(gdb8, "corridors"))
cell_m2 = on_the_ground(12, 5, 13, 5) * on_the_ground(12, 5, 12, 6)
check("degrees: Area_SqM is the true area on the ground (within 1 percent of cells times a cell's size there)",
      grow and all(abs(r["Area_SqM"] - r["Cells"] * cell_m2) < 0.01 * r["Cells"] * cell_m2 for r in grow),
      [(r["Area_SqM"], r["Cells"] * cell_m2) for r in grow][:2])

# ---- 7. a workspace of cost rasters, one for each category
ws = os.path.join(work, "costs")
os.makedirs(ws)
COST_B = COST[::-1, :].copy()                      # a different surface for the second category
COST_M = (COST * 2.0 + 1.0)                        # and a third, standing for a Mahalanobis distance raster
inv1 = write_cost(os.path.join(ws, "inverted_facet1.tif"), COST, NODATA)
inv2 = write_cost(os.path.join(ws, "inverted_facet2.tif"), COST_B, NODATA)
mah2 = write_cost(os.path.join(ws, "mahal_facet2.tif"), COST_M, NODATA)
pval = write_cost(os.path.join(ws, "mahal_p_facet3.tif"), COST, NODATA)
write_cost(os.path.join(ws, "no_record.tif"), COST, NODATA)
L.write(inv1, {"schema": L.SCHEMA, "record": L.INVERTED, "facet": {"facet_id": 1}})
L.write(inv2, {"schema": L.SCHEMA, "record": L.INVERTED, "facet": {"facet_id": 2}})
L.write(mah2, {"schema": L.SCHEMA, "record": L.MAHALANOBIS, "out_type": MH.OUT_D2, "facet": {"facet_id": 2}})
L.write(pval, {"schema": L.SCHEMA, "record": L.MAHALANOBIS, "out_type": MH.OUT_PVALUE, "facet": {"facet_id": 3}})
m = Msgs()
found = C.discover_cost_rasters(ws, m)
check("workspace: a raster for each category that has one; a raster with no record is not taken",
      sorted(found) == [1, 2] and os.path.basename(found[1]["path"]) == "inverted_facet1.tif", sorted(found))
check("workspace: where a category has both, the Mahalanobis distance raster is used, with a warning",
      os.path.basename(found.get(2, {"path": ""})["path"]) == "mahal_facet2.tif"
      and m.warned("Facet 2: both a Mahalanobis distance raster and an inverted raster were found"), m.warnings)
check("workspace: a Mahalanobis p-value raster is not a cost surface, and is passed over with a warning",
      3 not in found and m.warned("Facet 3: 'mahal_p_facet3.tif' is a Mahalanobis p-value raster"), m.warnings)
check("workspace: the run says what it found",
      m.has("Found 2 cost rasters in the workspace (1 Mahalanobis distance, 1 inverted)."), m.lines)
ws_h = os.path.join(work, "costs_hsm")
os.makedirs(ws_h)
hsm = write_cost(os.path.join(ws_h, "inverted_hsm.tif"), COST, NODATA)
L.write(hsm, {"schema": L.SCHEMA, "record": L.INVERTED, "band": 1})
found_h = C.discover_cost_rasters(ws_h, Msgs())
check("workspace: an inverted suitability raster, which has no facet, is category 1 and is named for its file",
      sorted(found_h) == [1] and found_h[1]["facet"] == {"facet_id": 1, "category_label": "inverted_hsm"}, found_h)

T_CAT = termini(gdb, "termini_cats", [
    ("Facet one", 1, 1, rect(10, 13, 1, 4, SR)), ("Facet one", 1, 2, rect(9, 13, 31, 34, SR)),
    ("Facet two", 2, 1, rect(10, 13, 1, 4, SR)), ("Facet two", 2, 2, rect(9, 13, 31, 34, SR)),
    ("Facet nine", 9, 1, rect(10, 13, 1, 4, SR)), ("Facet nine", 9, 2, rect(9, 13, 31, 34, SR))])
gdb9 = arcpy.management.CreateFileGDB(work, "cats.gdb")[0]
m = Msgs()
n = C.run_least_cost_corridor(T_CAT, cost_workspace=ws, out_workspace=gdb9, out_fc=os.path.join(gdb9, "corridors"),
                              percentiles=[5], messages=m)
check("workspace run: a corridor for each category that has a cost raster; the one without is warned of",
      n == 2 and m.warned("Facet nine: no matching cost raster found in the workspace (facet id 9); skipped."),
      (n, m.warnings))
PLAIN1 = cells(10, 13, 1, 4)
for label, name, surface in (("Facet one", "Cor_Facet_one_B1_B2", COST), ("Facet two", "Cor_Facet_two_B1_B2", COST_M)):
    want, both = corridor_of(surface.astype(np.float32).astype(np.float64), VALID, PLAIN1, B2)
    got = read_raster(os.path.join(gdb9, name))
    check("workspace run: %s is built over its own cost raster" % label, same_surface(got, want, both),
          worst(got, want, both))
rows = polygon_rows(os.path.join(gdb9, "corridors"))
check("workspace run: both categories' polygons are in the one feature class",
      sorted(set((r["Category"], r["CategoryID"]) for r in rows)) == [("Facet one", 1), ("Facet two", 2)],
      sorted(set((r["Category"], r["CategoryID"]) for r in rows)))
gdb10 = arcpy.management.CreateFileGDB(work, "cats_one.gdb")[0]
m = Msgs()
n = C.run_least_cost_corridor(T_CAT, cost_workspace=ws, out_workspace=gdb10, out_fc=os.path.join(gdb10, "corridors"),
                              percentiles=[5], facet_values=["2", "5"], messages=m)
check("categories to process: only the one asked for is built; one that is not in the termini is warned of",
      n == 1 and arcpy.Exists(os.path.join(gdb10, "Cor_Facet_two_B1_B2"))
      and m.warned("Requested facet value 5 does not occur in the termini feature class and was skipped."),
      (n, m.warnings))
n = C.run_least_cost_corridor(T_CAT, cost_raster=cost_tif, out_workspace=gdb10, out_prefix="One",
                              out_fc=os.path.join(gdb10, "corridors_single"), percentiles=[5], facet_values=[1])
check("categories to process: with one category chosen, a single cost raster can be given",
      n == 1 and arcpy.Exists(os.path.join(gdb10, "One_Facet_one_B1_B2")), n)

# ---- 8. outputs in a folder
folder = os.path.join(work, "as_files")
os.makedirs(folder)
n = C.run_least_cost_corridor(T12, cost_raster=cost_tif, out_workspace=folder, out_fc=os.path.join(folder, "corridors.shp"),
                              percentiles=[5, 10], messages=Msgs())
want, both = corridor_of(COST, VALID, B1, B2)
tif = os.path.join(folder, "Cor_Black_bear_B1_B2.tif")
check("folder: the raster is a GeoTIFF and the polygons a shapefile",
      n == 1 and os.path.isfile(tif) and os.path.isfile(os.path.join(folder, "corridors.shp")), os.listdir(folder))
got = read_raster(tif) if os.path.isfile(tif) else np.zeros((1, 1))
check("folder: the same surface there", same_surface(got, want, both), worst(got, want, both))
srows = polygon_rows(os.path.join(folder, "corridors.shp"))
check("folder: the shapefile has the slices, the category and the counts of cells",
      sorted(set(r["Percentile"] for r in srows)) == [5.0, 10.0] and all(r["Category"] == "Black bear" for r in srows)
      and sum(r["Cells"] for r in srows if r["Percentile"] == 5.0) == int(slice_of(want, both, 5).sum()),
      srows[:1])

# ---- 9. what is refused
ok = dict(cost_raster=cost_tif, out_workspace=out_gdb, out_fc=os.path.join(out_gdb, "refused"))
raises("refused: a slice of 0 percent", lambda: C.run_least_cost_corridor(T12, percentiles=[0, 5], **ok),
       "Percentile thresholds must be greater than 0 and at most 100.")
raises("refused: a slice over 100 percent", lambda: C.run_least_cost_corridor(T12, percentiles=[5, 101], **ok),
       "Percentile thresholds must be greater than 0 and at most 100.")
raises("refused: no place for the rasters", lambda: C.run_least_cost_corridor(
    T12, cost_raster=cost_tif, out_fc=os.path.join(out_gdb, "refused")), "An output workspace is required")
raises("refused: no polygons to write to", lambda: C.run_least_cost_corridor(
    T12, cost_raster=cost_tif, out_workspace=out_gdb), "An output feature class is required")
raises("refused: no cost raster and no workspace", lambda: C.run_least_cost_corridor(
    T12, out_workspace=out_gdb, out_fc=os.path.join(out_gdb, "refused")),
    "Supply either a single cost raster or a cost-raster workspace.")
BLOCKS = arcpy.management.CreateFeatureclass(gdb, "wildland_blocks", "POLYGON", spatial_reference=SR)[0]
with arcpy.da.InsertCursor(BLOCKS, ["SHAPE@"]) as c:
    c.insertRow((rect(10, 13, 1, 4, SR),))
raises("refused: a layer that is not termini, with advice", lambda: C.run_least_cost_corridor(BLOCKS, **ok),
       "this does not look like an Identify Termini Polygons output")
raises("refused: one cost raster for termini of several categories", lambda: C.run_least_cost_corridor(T_CAT, **ok),
       "A single cost raster was supplied, but 3 categories are present")
raises("refused: categories asked for that the termini do not have",
       lambda: C.run_least_cost_corridor(T_CAT, facet_values=[42], **ok),
       "None of the requested facet values occur in the termini feature class.")
empty_ws = os.path.join(work, "no_costs")
os.makedirs(empty_ws)
raises("refused: a workspace with no cost rasters", lambda: C.run_least_cost_corridor(
    T_CAT, cost_workspace=empty_ws, out_workspace=out_gdb, out_fc=os.path.join(out_gdb, "refused")),
    "No cost rasters carrying a Land Facet linkage record were found")
check("refused: none of those left an output", not arcpy.Exists(os.path.join(out_gdb, "refused")))

print("\n%d passed, %d failed%s" % (passed, failed, " (and %d known faults not yet fixed)" % known if known else ""))
arcpy.management.ClearWorkspaceCache()
if os.environ.get("KEEP_TEST_DATA"):
    print("kept:", work)
else:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
