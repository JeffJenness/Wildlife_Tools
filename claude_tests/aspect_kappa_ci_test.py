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

"""Aspect tools: von Mises kappa small-sample correction (Fisher 1993 eq. 4.41)
and the arcsin confidence interval (eqs. 4.42-4.43, Table 4.3 caution).

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" aspect_kappa_ci_test.py

Part 1 is pure numpy. Part 2 runs Aspect Focal Statistics (kappa, 3x3
rectangle and a Weight kernel) and Aspect Zonal Statistics as Table on a
small synthetic aspect raster and checks cells/zones against the scalar
reference.
"""
import math
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import numpy as np
import aspect_tools as at

ok = 0
bad = 0


def check(name, got, want, tol=1e-6):
    global ok, bad
    if (got is None and want is None) or (
            got is not None and want is not None and abs(got - want) <= tol):
        ok += 1
    else:
        bad += 1
        print("FAIL", name, "got", got, "want", want)


def kml(r):
    r = min(r, 0.999999)
    if r < 0.53:
        return 2 * r + r ** 3 + 5 * r ** 5 / 6
    if r < 0.85:
        return -0.4 + 1.39 * r + 0.43 / (1 - r)
    return 1 / (r ** 3 - 4 * r ** 2 + 3 * r)


def corr(k, n):
    if n > 15 or n < 1 or k <= 0:
        return k
    return max(k - 2 / (n * k), 0) if k < 2 else (n - 1) ** 3 * k / (n ** 3 + n)


def ref_stats(az, w=None):
    """(rbar, n_eff, kappa, half or None) for compass degrees ``az``."""
    az = np.asarray(az, float)
    w = np.ones_like(az) if w is None else np.asarray(w, float)
    s = (w * np.sin(np.radians(az))).sum()
    c = (w * np.cos(np.radians(az))).sum()
    rbar = min(math.hypot(s, c) / w.sum(), 1.0)
    neff = w.sum() ** 2 / (w * w).sum()
    k = corr(kml(rbar), neff)
    half = None
    if neff > 1 and rbar > 0 and k > 0:
        x = 1.96 / math.sqrt(neff * rbar * k)
        if x < 1:
            half = math.degrees(math.asin(x))
    return rbar, neff, k, half


# ---------------------------------------------------------------- part 1
for r in (0.05, 0.2, 0.4, 0.53, 0.7298, 0.85, 0.9528, 0.999):
    for n in (1, 2, 5, 7.1, 9, 15, 15.5, 16, 1000):
        got = float(at._kappa_small_sample(
            at._kappa_from_rbar(np.asarray([r])), np.asarray([n]))[0])
        check("kappa r=%s n=%s" % (r, n), got, corr(kml(r), n), 1e-9)

# Fisher (1993) Example 4.21: n = 15, R-bar = 0.7298, mean 168.5; he prints
# kappa 2.21 -> 1.80, s.e. 0.2252, interval (142.3, 194.7).
z = {"n": 15, "nodata": 0, "flat": 0,
     "sin": 15 * 0.7298 * math.sin(math.radians(168.5)),
     "cos": 15 * 0.7298 * math.cos(math.radians(168.5)),
     "hist": np.zeros(3600)}
z["hist"][1685] = 15
row = at._zone_stats_row(z, True)
check("fisher kappa", row["VM_KAPPA"], 1.80, 0.02)
check("fisher lo", row["CI95_LO"], 142.3, 0.2)
check("fisher hi", row["CI95_HI"], 194.7, 0.2)

# About Aspect's five cells
az5 = [340, 355, 5, 20, 30]
z = {"n": 5, "nodata": 0, "flat": 0,
     "sin": sum(math.sin(math.radians(a)) for a in az5),
     "cos": sum(math.cos(math.radians(a)) for a in az5),
     "hist": np.zeros(3600)}
row = at._zone_stats_row(z, True)
check("five kappa", row["VM_KAPPA"], 5.35, 0.01)
check("five half", row["CI95_HALF"], 22.8, 0.05)
check("five lo", row["CI95_LO"], 343.2, 0.05)
check("five hi", row["CI95_HI"], 28.9, 0.05)

n = 1000000
z = {"n": n, "nodata": 0, "flat": 0, "sin": 0.0, "cos": n * 0.1,
     "hist": np.ones(3600)}
row = at._zone_stats_row(z, True)
check("weak kappa unchanged", row["VM_KAPPA"], kml(0.1), 1e-9)
check("weak caution", float(row["_CI_CAUTION"]), 1.0)
z = {"n": 4, "nodata": 0, "flat": 0, "sin": 0.0, "cos": 4 * 0.3,
     "hist": np.ones(3600)}
check("dispersed: no interval", at._zone_stats_row(z, True)["CI95_HALF"], None)
for k, n, want in ((2.0, 2, True), (1.99, 9, False), (1.5, 10, True),
                   (1.49, 14, False), (1.0, 15, True), (0.99, 24, False),
                   (0.4, 25, True), (0.39, 10 ** 9, False)):
    check("table 4.3 k=%s n=%s" % (k, n),
          float(at._fisher_interval_suitable(k, n)), float(want))
print("part 1: passed", ok, "failed", bad)

# ---------------------------------------------------------------- part 2
import arcpy
from osgeo import gdal, osr

arcpy.env.overwriteOutput = True
tmp = tempfile.mkdtemp(prefix="aspkappa_")
try:
    rng = np.random.default_rng(7)
    H, W = 12, 14
    asp = (rng.normal(40.0, 35.0, (H, W)) % 360.0).astype(np.float32)
    asp[3, 4] = -1.0                       # a flat cell
    asp[8, 9] = -9999.0                    # NoData
    src = os.path.join(tmp, "aspect.tif")
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(26912)
    ds = gdal.GetDriverByName("GTiff").Create(src, W, H, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((400000.0, 30.0, 0, 3900000.0, 0, -30.0))
    ds.SetProjection(sr.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(-9999.0)
    ds.GetRasterBand(1).WriteArray(asp)
    ds = None

    def read(path):
        d = gdal.Open(path)
        b = d.GetRasterBand(1)
        a = b.ReadAsArray().astype(np.float64)
        nd = b.GetNoDataValue()
        d = None
        return a, nd

    valid = (asp >= 0)

    # --- focal kappa, 3x3 rectangle (n <= 9 everywhere: always corrected)
    out = os.path.join(tmp, "kap3.tif")
    at.run_aspect_focal(src, out, "KAPPA", neighborhood="Rectangle",
                        width=3, height=3, nbr_units="Cells")
    got, nd = read(out)
    for r, c in ((0, 0), (5, 5), (3, 4), (8, 9), (11, 13), (4, 4)):
        rs, cs = slice(max(r - 1, 0), r + 2), slice(max(c - 1, 0), c + 2)
        vals = asp[rs, cs][valid[rs, cs]]
        want = ref_stats(vals)[2]
        check("focal 3x3 (%d,%d)" % (r, c), float(got[r, c]), want, 2e-3 * max(want, 1))

    # --- focal kappa, 7x7 rectangle (interior n = 49 > 15: uncorrected)
    out = os.path.join(tmp, "kap7.tif")
    at.run_aspect_focal(src, out, "KAPPA", neighborhood="Rectangle",
                        width=7, height=7, nbr_units="Cells")
    got, nd = read(out)
    for r, c in ((6, 6), (0, 0), (5, 3)):
        rs, cs = slice(max(r - 3, 0), r + 4), slice(max(c - 3, 0), c + 4)
        vals = asp[rs, cs][valid[rs, cs]]
        want = ref_stats(vals)[2]
        check("focal 7x7 (%d,%d)" % (r, c), float(got[r, c]), want, 2e-3 * max(want, 1))

    # --- focal kappa, Weight kernel: Kish effective n
    kfile = os.path.join(tmp, "w.txt")
    kern = np.array([[0.5, 1, 0.5], [1, 2, 1], [0.5, 1, 0.5]])
    with open(kfile, "w") as fh:
        fh.write("3 3\n" + "\n".join(" ".join(str(v) for v in row) for row in kern) + "\n")
    out = os.path.join(tmp, "kapw.tif")
    at.run_aspect_focal(src, out, "KAPPA", neighborhood="Weight", kernel_file=kfile)
    got, nd = read(out)
    for r, c in ((5, 5), (6, 8), (2, 2)):
        vals, ws = [], []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if 0 <= rr < H and 0 <= cc < W and valid[rr, cc]:
                    vals.append(asp[rr, cc])
                    ws.append(kern[dr + 1, dc + 1])
        want = ref_stats(vals, ws)[2]
        check("focal weight (%d,%d)" % (r, c), float(got[r, c]), want, 2e-3 * max(want, 1))

    # --- zonal: a small zone (corrected) and a large one
    zone = np.ones((H, W), np.int32)
    zone[0:2, 0:4] = 2                      # 8 cells
    zsrc = os.path.join(tmp, "zones.tif")
    ds = gdal.GetDriverByName("GTiff").Create(zsrc, W, H, 1, gdal.GDT_Int32)
    ds.SetGeoTransform((400000.0, 30.0, 0, 3900000.0, 0, -30.0))
    ds.SetProjection(sr.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(zone)
    ds = None

    class Msg:
        def __init__(self):
            self.lines = []

        def addMessage(self, t):
            self.lines.append(("M", t))

        def addWarningMessage(self, t):
            self.lines.append(("W", t))

        def addErrorMessage(self, t):
            self.lines.append(("E", t))

    m = Msg()
    gdb = arcpy.management.CreateFileGDB(tmp, "t.gdb").getOutput(0)
    tbl = os.path.join(gdb, "zstats")
    at.run_aspect_zonal(zsrc, "Value", src, tbl, ignore_nodata=True,
                        statistics_type="ALL", messages=m)
    flds = [f.name for f in arcpy.ListFields(tbl)]
    zf = [f for f in flds if f.upper() in ("VALUE", "ZONE", "ZONE_VALUE")]
    zname = zf[0] if zf else flds[1]
    with arcpy.da.SearchCursor(tbl, [zname, "COUNT", "VM_KAPPA", "CI95_HALF"]) as cur:
        for zv, cnt, kap, half in cur:
            sel = (zone == int(zv)) & valid
            rbar, neff, k, h = ref_stats(asp[sel])
            check("zonal count %s" % zv, float(cnt), float(sel.sum()))
            # the tool bins to 0.1 degree only for median/range; sums are exact
            check("zonal kappa %s" % zv, kap, k, 1e-4 * max(k, 1))
            check("zonal half %s" % zv, half, h, 1e-3)
    txt = " ".join(t for _k, t in m.lines)
    check("zonal small-sample message", float("eq. 4.41" in txt), 1.0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("passed", ok, "failed", bad)
sys.exit(1 if bad else 0)
