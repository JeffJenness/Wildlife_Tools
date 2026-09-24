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

"""TWI flow-routing tests (2026-09-23): D8, MFD and D-Infinity accumulation.

Checks conservation of area, the cone test of Tarboton (1997, Fig. 4: on an
outward cone the specific catchment area is r/2 for every cell of a ring;
D8 concentrates it on eight rays, dispersive methods spread it), a plane, and
NoData handling; then, if Spatial Analyst and the tutorial DEM are available,
compares each method's accumulation with Esri's Flow Accumulation. Run with
ArcGIS Pro's python."""
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TBX = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TBX)

import topographic_tools as tt  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  {}".format(name))
    else:
        FAIL += 1
        print("  FAIL  {}  {}".format(name, detail))


def square_geom(H, c=1.0):
    return {"h": np.full(H, c), "vA": np.full(H, c), "vB": np.full(H, c)}


def accumulate(z, valid, method, geom=None, want_terminals=False):
    geom = geom or square_geom(z.shape[0])
    filled, _order = tt._twi_priority_flood(np.where(valid, z, np.inf), valid)
    if tt._flow_key(method) == "D8":
        recv, order = tt._twi_receivers(filled, valid, geom)
        acc = tt._twi_accumulate(recv, order, valid)
        term = valid & (recv.reshape(z.shape) < 0)
    else:
        w, order = tt._twi_flow_weights(filled, valid, geom, method)
        acc = tt._twi_accumulate_w(w, order, valid)
        term = valid & (w.sum(axis=0) == 0)
    return (acc, term) if want_terminals else acc


# ---------------------------------------------------------------- the cone (Tarboton Fig. 4)
n = 161
yy, xx = np.mgrid[0:n, 0:n]
r = np.hypot(xx - n // 2, yy - n // 2)
cone = 100.0 - 0.5 * r
valid = np.ones(cone.shape, bool)
res = {}
for m in tt.FLOW_METHODS:
    t0 = time.time()
    res[m], term = accumulate(cone, valid, m, want_terminals=True)
    print("    {} on the cone: {:.1f} s".format(m, time.time() - t0))
    a = res[m]
    check("{}: area is conserved (sum over terminal cells of acc + 1 = number of cells)".format(m.split()[0]),
          abs((a[term] + 1.0).sum() - n * n) < 1e-6 * n * n, ((a[term] + 1.0).sum(), n * n))
for R in (20, 40):
    ring = np.abs(r - R) < 0.5
    true = R / 2.0
    for m in tt.FLOW_METHODS:
        sca = res[m][ring] + 1.0
        key = tt._flow_key(m)
        if key == "D8":
            check("D8 ring r={}: thread problem, a fifth of the ring below half the truth and the rays at ~2x".format(R),
                  sca.min() == 1 and sca.max() > 1.8 * true and (sca < true / 2).mean() > 0.15,
                  (sca.min(), np.median(sca), sca.max(), (sca < true / 2).mean()))
        else:
            check("{} ring r={}: every cell within a factor of 2 of the true SCA r/2 = {}".format(key, R, true),
                  sca.min() >= true / 2 and sca.max() <= 2 * true, (sca.min(), np.median(sca), sca.max()))
            check("{} ring r={}: ring mean within 15 percent of r/2".format(key, R),
                  abs(sca.mean() - true) < 0.15 * true, sca.mean())

# ---------------------------------------------------------------- a tilted plane
plane = np.tile(100.0 - np.arange(40, dtype=float), (30, 1))       # falls east
valid = np.ones(plane.shape, bool)
for m in tt.FLOW_METHODS:
    a = accumulate(plane, valid, m)
    if tt._flow_key(m) == "MFD":
        # MFD disperses sideways on a plane (Tarboton 1997, p. 9), so only the
        # column totals are exact: every column carries all the rows above it.
        check("MFD: on a plane each column's total accumulation is its distance from the west edge times the rows",
              np.allclose(a.sum(axis=0), np.arange(40) * 30.0, atol=1e-9), a.sum(axis=0)[:6])
    else:
        check("{}: on a plane every interior column accumulates its distance from the west edge".format(m.split()[0]),
              np.allclose(a[1:-1, 1:-1], np.arange(1, 39)[None, :], atol=1e-9), a[10, :6])

# ---------------------------------------------------------------- NoData hole
hole = cone.copy()
valid = np.ones(hole.shape, bool)
valid[70:75, 70:75] = False
for m in tt.FLOW_METHODS:
    a = accumulate(hole, valid, m)
    check("{}: NoData cells receive nothing and valid cells all finite".format(m.split()[0]),
          np.all(a[~valid] == 0) and np.all(np.isfinite(a[valid])))

# ---------------------------------------------------------------- D8 unchanged vs the weight router
# The multi-direction router must reproduce D8 when asked for D8 through the
# generic accumulate (it is not used that way in the tool, but the flat rule
# and terminals are shared and must agree).
rng = np.random.default_rng(3)
rough = rng.normal(size=(50, 70)).cumsum(0).cumsum(1) + rng.normal(scale=0.3, size=(50, 70))
valid = np.ones(rough.shape, bool)
filled, _order = tt._twi_priority_flood(rough, valid)
geom = square_geom(50)
recv, order = tt._twi_receivers(filled, valid, geom)
a_d8 = tt._twi_accumulate(recv, order, valid)
w, _o = tt._twi_flow_weights(filled, valid, geom, "DINF")
check("DINF weights: every routed cell's shares sum to 1, terminals to 0",
      np.allclose(w.sum(axis=0)[w.sum(axis=0) > 0], 1.0) and (w.sum(axis=0) == 0).sum() >= 1)
w, order = tt._twi_flow_weights(filled, valid, geom, "MFD")
check("MFD weights: every routed cell's shares sum to 1",
      np.allclose(w.sum(axis=0)[w.sum(axis=0) > 0], 1.0))
check("MFD: at least one interior cell sends water to three or more neighbors",
      ((w[:, 1:-1, 1:-1] > 0).sum(axis=0) >= 3).any())
a_mfd = tt._twi_accumulate_w(w, order, valid)
t_d8 = valid & (recv.reshape(rough.shape) < 0)
t_mfd = valid & (w.sum(axis=0) == 0)
check("rough terrain: D8 and MFD both conserve area (terminal totals = cell count)",
      abs((a_d8[t_d8] + 1).sum() - valid.sum()) < 1e-6 * valid.sum()
      and abs((a_mfd[t_mfd] + 1).sum() - valid.sum()) < 1e-6 * valid.sum(),
      ((a_d8[t_d8] + 1).sum(), (a_mfd[t_mfd] + 1).sum(), valid.sum()))

# ---------------------------------------------------------------- geographic geometry
geo = {"h": np.linspace(20.0, 30.0, 50), "vA": np.full(50, 30.0), "vB": np.full(50, 30.0)}
for m in ("MFD", "DINF"):
    w, _o = tt._twi_flow_weights(filled, valid, geo, m)
    check("{}: per-row (geographic) spans accepted, shares still sum to 1".format(m),
          np.allclose(w.sum(axis=0)[w.sum(axis=0) > 0], 1.0))

# ---------------------------------------------------------------- Esri comparison
# On the Grand Canyon DEM projected to UTM (bilinear, 30 m): a floating-point
# projected DEM, so steepest-descent ties are rare and Esri's planar cell
# geometry matches ours. (An integer DEM, or a geographic one, cannot agree
# cell for cell: ties are broken by undocumented rules, and Esri treats
# degree cells as square while this tool uses true ground distances.)
GDB = r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife\Pro_Test_Project\Pro Test Tools.gdb"
DEM = os.path.join(GDB, "Extract_AZ_D1")
try:
    import arcpy
    have_sa = arcpy.CheckExtension("Spatial") == "Available" and arcpy.Exists(DEM)
except Exception:
    have_sa = False
if have_sa:
    import tempfile
    from arcpy import sa
    arcpy.CheckOutExtension("Spatial")
    tmp = tempfile.mkdtemp(prefix="twi_route_")
    arcpy.env.workspace = tmp
    arcpy.env.overwriteOutput = True
    prj = os.path.join(tmp, "gc_utm.tif")
    arcpy.management.ProjectRaster(DEM, prj, arcpy.SpatialReference(26912), "BILINEAR", "30")
    filled_r = sa.Fill(prj)
    filled_r.save(os.path.join(tmp, "fill.tif"))
    zf = arcpy.RasterToNumPyArray(filled_r).astype(np.float64)
    zc = arcpy.RasterToNumPyArray(prj).astype(np.float64)
    nod = arcpy.Describe(prj).noDataValue
    valid = np.isfinite(zc) & (zc != nod) & (zf > -1e30)
    geom = square_geom(zf.shape[0], 30.0)
    filled, _o = tt._twi_priority_flood(np.where(valid, zf, np.inf), valid)
    check("Esri Fill: identical cell for cell", float(np.abs(filled - zf)[valid].max()) == 0.0)
    for key, tol_near, tol_cc in (("D8", 0.995, 0.99), ("DINF", 0.99, 0.99), ("MFD", 0.80, 0.97)):
        fdir = sa.FlowDirection(filled_r, flow_direction_type=key)
        facc = sa.FlowAccumulation(fdir, flow_direction_type=key)
        e = arcpy.RasterToNumPyArray(facc).astype(np.float64)
        ev = valid & np.isfinite(e) & (e > -1e30)
        t0 = time.time()
        ours = accumulate(zf, valid, key, geom)
        dt = time.time() - t0
        d = np.abs(ours[ev] - e[ev])
        exact = np.mean(d <= 0.5)
        near = np.mean(d <= np.maximum(2.0, 0.05 * e[ev]))
        cc = np.corrcoef(np.log1p(ours[ev]), np.log1p(e[ev]))[0, 1]
        check("Esri {}: exact on {:.2%}, within 2 cells or 5 percent on {:.2%}, log-correlation {:.4f} ({:.0f} s ours)"
              .format(key, exact, near, cc, dt), near >= tol_near and cc >= tol_cc, (exact, near, cc))
    arcpy.CheckInExtension("Spatial")
    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass
else:
    print("  (Esri comparison skipped: Spatial Analyst or the Grand Canyon DEM not available)")

print("\n{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
