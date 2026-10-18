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

"""GroundGeometry checks (no DEM needed): (1) geographic mode is bit-identical
to the legacy per-row table; (2) on Web Mercator the lattice interpolation
matches exact per-cell spans at arbitrary columns to < 1e-6 relative;
(3) a polar stereographic grid, where the scale varies along rows, is also
matched per cell; (4) the planar fallback engages for an unprojectable CS;
(5) the half (surface-area) table matches the legacy geographic construction."""
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes"))
import arcpy
import numpy as np
import hillshade as hs

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    ok = ok and bool(cond)


# (1) geographic: identical to the legacy table
sr_geo = arcpy.SpatialReference(4326)
gt = (-112.5, 0.01, 0.0, 36.0, 0.0, -0.01)
W, H = 300, 200
gg = hs.GroundGeometry(gt, W, H, sr_geo, sr_geo.exportToString())
legacy = hs._geographic_row_distances(gt, H, 6378137.0, 6356752.314245179)
check("geographic mode", gg.mode == "geographic")
check("geographic rows identical to legacy table",
      all(np.array_equal(gg.row[k], legacy[k]) for k in legacy))
rows2d = gg.rows(10, 20)
check("geographic 2-D rows are broadcasts of the per-row table",
      all(np.array_equal(rows2d[k][3], np.full(W, legacy[k][13])) for k in legacy))

# (2) Web Mercator: lattice vs exact at arbitrary cells
sr_wm = arcpy.SpatialReference(3857)
wkt = sr_wm.exportToString()
R = 6378137.0
cell = 500.0
W, H = 2000, 1500
y0 = R * math.log(math.tan(math.pi / 4 + math.radians(45.0) / 2))
gt = (-W / 2 * cell, cell, 0.0, y0 + H / 2 * cell, 0.0, -cell)
t0 = time.time()
gg = hs.GroundGeometry(gt, W, H, sr_wm, wkt)
dt = time.time() - t0
check("web mercator mode", gg.mode == "geodesic", "lattice %s built in %.2f s" % (gg.info["lattice"], dt))
pg = gg._to_ecef
rng = np.random.RandomState(1)
cols = rng.uniform(0, W - 1, 400)
rws = rng.uniform(0, H - 1, 400)
exact = hs._distances_at_cells(gt, cols, rws, pg)
worst = 0.0
for k in exact:
    # interpolate rows() at integer rows nearest -- compare at integer cells instead
    pass
ic = rng.randint(0, W, 400)
ir = rng.randint(0, H, 400)
exact = hs._distances_at_cells(gt, ic.astype(float), ir.astype(float), pg)
for k in exact:
    got = np.array([gg.rows(r, r + 1)[k][0, c] for r, c in zip(ir, ic)])
    rel = np.max(np.abs(got - exact[k]) / exact[k])
    worst = max(worst, rel)
check("web mercator lattice matches exact per-cell spans", worst < 1e-6, "worst rel err %.2e" % worst)
mid = H // 2
# ellipsoidal Web Mercator: k = R / (N(lat) cos lat), N = a / sqrt(1 - e2 sin2)
e2 = 1 - (6356752.314245179 / R) ** 2
N45 = R / math.sqrt(1 - e2 * math.sin(math.radians(45.0)) ** 2)
k45 = R / (N45 * math.cos(math.radians(45.0)))
check("web mercator scale factor at 45N", abs(gg.info["scale_x"] - k45) < 2e-4,
      "%.4f (expected %.4f)" % (gg.info["scale_x"], k45))
print("   note:", gg.info["note"][:160], "...")

# (3) polar stereographic (EPSG 3031, Antarctica): scale varies along rows
sr_ps = arcpy.SpatialReference(3031)
wkt = sr_ps.exportToString()
cell = 2000.0
W, H = 1200, 900
gt = (-W / 2 * cell + 600e3, cell, 0.0, H / 2 * cell + 300e3, 0.0, -cell)
gg = hs.GroundGeometry(gt, W, H, sr_ps, wkt)
check("polar stereographic mode", gg.mode == "geodesic", "lattice %s" % (gg.info["lattice"],))
pg = gg._to_ecef
ic = rng.randint(0, W, 300)
ir = rng.randint(0, H, 300)
exact = hs._distances_at_cells(gt, ic.astype(float), ir.astype(float), pg)
worst = 0.0
for k in exact:
    got = np.array([gg.rows(r, r + 1)[k][0, c] for r, c in zip(ir, ic)])
    worst = max(worst, np.max(np.abs(got - exact[k]) / exact[k]))
check("polar stereographic lattice matches exact per-cell spans", worst < 1e-5, "worst rel err %.2e" % worst)
# the single-column approximation would have erred by the along-row variation:
full = gg.rows(0, H)["h"]
rowvar = float(np.max(np.abs(full - gg.row["h"][:, None]) / gg.row["h"][:, None])) * 100
print("   along-row variation the old per-row method would have ignored: %.2f%%" % rowvar)
print("   scale range %.4f - %.4f" % (gg.info["scale_min"], gg.info["scale_max"]))

# half table: geographic equals the legacy surface-area construction
sr_geo = arcpy.SpatialReference(4326)
gt = (-112.5, 0.01, 0.0, 36.0, 0.0, -0.01)
W, H = 50, 40
gg = hs.GroundGeometry(gt, W, H, sr_geo)
a, b = 6378137.0, 6356752.314245179
leg = hs._half_distances_at_cells(gt, np.zeros(H), np.arange(H, dtype=float),
                                  lambda x, y: hs._ecef(x, y, a, b))
check("geographic half table equals legacy construction",
      all(np.array_equal(gg.half_row[k], leg[k]) for k in leg))
hr = gg.half_rows(5, 9)
check("half_rows broadcast", np.array_equal(hr["pix_m2"][2], np.full(W, leg["pix_m2"][7])))

# (4) planar fallback: a projected SR whose WKT cannot be parsed
class FakeSR(object):
    type = "Projected"
    metersPerUnit = 1.0
    name = "Bogus"
    def exportToString(self):
        return "PROJCS[garbage"
gg = hs.GroundGeometry((0, 10, 0, 0, 0, -10), 30, 20, FakeSR(), "PROJCS[garbage")
check("planar fallback", gg.mode == "planar" and bool(gg.info["warning"]),
      gg.info["warning"][:90])
check("planar rows constant", float(gg.rows(0, 20)["h"][5, 7]) == 10.0)

# (5) UTM: scale within 0.1%
sr_utm = arcpy.SpatialReference(26912)
gt = (400000.0, 30.0, 0.0, 3900000.0, 0.0, -30.0)
gg = hs.GroundGeometry(gt, 3000, 3000, sr_utm, sr_utm.exportToString())
check("UTM scale factors within 0.1% of 1",
      abs(gg.info["scale_min"] - 1) < 1e-3 and abs(gg.info["scale_max"] - 1) < 1e-3,
      "%.5f - %.5f" % (gg.info["scale_min"], gg.info["scale_max"]))

print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
