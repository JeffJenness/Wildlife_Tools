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

"""Hillshade review fixes (v1.99.43): the shadow count leaves NoData cells
out, a blur under one cell warns, hypsometric shading writes nothing beside
the input DEM, an all-NoData DEM gives a plain error, and MDOW clamps each
sun at zero before blending.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" hillshade_review_test.py
"""
import os
import re
import sys
import tempfile

import numpy as np
from osgeo import gdal, osr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import hillshade as hs  # noqa: E402

gdal.UseExceptions()
arcpy.env.overwriteOutput = True
work = tempfile.mkdtemp(prefix="hs_review_")
arcpy.env.scratchWorkspace = work
gdb = arcpy.management.CreateFileGDB(work, "out.gdb").getOutput(0)

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS", name, detail)
    else:
        failed += 1
        print("FAIL", name, detail)


class Msgs(object):
    def __init__(self):
        self.info, self.warn = [], []

    def addMessage(self, t):
        self.info.append(t)

    def addWarningMessage(self, t):
        self.warn.append(t)

    def addErrorMessage(self, t):
        self.warn.append(t)


def make(name, arr):
    p = os.path.join(work, name)
    ds = gdal.GetDriverByName("GTiff").Create(p, arr.shape[1], arr.shape[0], 1,
                                              gdal.GDT_Float32)
    ds.SetGeoTransform((400000.0, 30.0, 0, 4000000.0, 0, -30.0))
    sr = osr.SpatialReference(); sr.ImportFromEPSG(26912)
    ds.SetProjection(sr.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(arr)
    ds.GetRasterBand(1).SetNoDataValue(-9999)
    ds = None
    return p


def shadow_count(msgs):
    for t in msgs.info:
        m = re.search(r"Cast shadows: ([\d,]+) cells \(([\d.]+)%\)", t)
        if m:
            return int(m.group(1).replace(",", "")), float(m.group(2))
    return None, None


n = 200
y, x = np.mgrid[0:n, 0:n].astype(float)
dem = (1500 + 2 * x + y + 900 * np.exp(-((x - 80) ** 2 + (y - 90) ** 2) / 900.0)
       - 700 * np.exp(-((x - 150) ** 2 + (y - 140) ** 2) / 1200.0)).astype(np.float32)

# ---- shadow count: NoData cells are not shadowed cells
half = dem.copy()
half[:, 120:] = -9999
m1, m2 = Msgs(), Msgs()
hs.run_enhanced_hillshade(make("nd.tif", half), os.path.join(gdb, "nd"),
                          azimuth=270, altitude=10, model_shadows=True, messages=m1)
hs.run_enhanced_hillshade(make("left.tif", dem[:, :120].copy()), os.path.join(gdb, "left"),
                          azimuth=270, altitude=10, model_shadows=True, messages=m2)
c1, p1 = shadow_count(m1)
c2, p2 = shadow_count(m2)
check("shadow count ignores NoData cells", c1 is not None and c1 == c2,
      "with NoData %s, same terrain without %s" % (c1, c2))
check("shadow percent is of the valid cells", p1 is not None and abs(p1 - p2) < 0.05,
      "%s%% vs %s%%" % (p1, p2))
a = arcpy.RasterToNumPyArray(os.path.join(gdb, "nd"), nodata_to_value=-9999)
b = arcpy.RasterToNumPyArray(os.path.join(gdb, "left"), nodata_to_value=-9999)
check("shadowed output unchanged by the NoData half", bool(np.array_equal(a[:, :119], b[:, :119])))

# ---- a blur under one cell warns and changes nothing; one cell and over does not warn
full = make("full.tif", dem)
hs.run_enhanced_hillshade(full, os.path.join(gdb, "plain"))
plain = arcpy.RasterToNumPyArray(os.path.join(gdb, "plain"), nodata_to_value=-9999)
for target in hs.BLUR_TARGETS:
    for r, want_warn in ((0.4, True), (0.9, True), (1.0, False), (3.0, False)):
        mm = Msgs()
        out = os.path.join(gdb, "blur")
        hs.run_blurred_hillshade(full, out, blur_radius=r, radius_units="Cells",
                                 blur_target=target, messages=mm)
        warned = any("smaller than one cell" in t for t in mm.warn)
        got = arcpy.RasterToNumPyArray(out, nodata_to_value=-9999)
        check("blur %.1f cells, %s: warning %s" % (r, target[:9], "given" if want_warn else "absent"),
              warned == want_warn)
        if want_warn:
            check("blur %.1f cells, %s: same as an ordinary hillshade" % (r, target[:9]),
                  bool(np.array_equal(got, plain)))
mm = Msgs()
hs.run_blurred_hillshade(full, os.path.join(gdb, "blur"), blur_radius=20,
                         radius_units="Meters", messages=mm)
check("20 m radius on 30 m cells warns", any("smaller than one cell" in t for t in mm.warn))

# ---- hypsometric shading leaves no file beside the input
aux = make("auxtest.tif", dem)
before = set(os.listdir(work))
hs.run_enhanced_hillshade(aux, os.path.join(gdb, "hyp"), use_hypso=True, hypso_strength=50)
new = sorted(f for f in set(os.listdir(work)) - before if f.startswith("auxtest"))
check("hypsometric shading writes nothing beside the DEM", not new, str(new))
hyp = arcpy.RasterToNumPyArray(os.path.join(gdb, "hyp"), nodata_to_value=-9999)
lo = np.unravel_index(np.argmin(np.where(plain != -9999, dem, np.inf)), dem.shape)
hi = np.unravel_index(np.argmax(np.where(plain != -9999, dem, -np.inf)), dem.shape)
check("hypsometric: lowest valid cell about halved", abs(hyp[lo] - plain[lo] * 0.5) <= 2,
      "%d vs %d" % (hyp[lo], plain[lo]))
check("hypsometric: highest valid cell unchanged", abs(int(hyp[hi]) - int(plain[hi])) <= 1,
      "%d vs %d" % (hyp[hi], plain[hi]))
hs.run_enhanced_hillshade(aux, os.path.join(gdb, "hyp0"), use_hypso=True, hypso_strength=0)
h0 = arcpy.RasterToNumPyArray(os.path.join(gdb, "hyp0"), nodata_to_value=-9999)
check("hypsometric strength 0 changes nothing", bool(np.array_equal(h0, plain)))

# ---- an all-NoData DEM with hypsometric shading: a plain error
try:
    hs.run_enhanced_hillshade(make("empty.tif", np.full((20, 20), -9999, np.float32)),
                              os.path.join(gdb, "empty"), use_hypso=True)
    check("all-NoData DEM refused", False, "no error raised")
except arcpy.ExecuteError as e:
    check("all-NoData DEM refused with a plain message", "no valid elevation" in str(e), str(e)[:70])
except Exception as e:                                            # noqa: BLE001
    check("all-NoData DEM refused with a plain message", False, type(e).__name__)

# ---- MDOW: each sun is clamped at 0 before the weighted blend (Mark 1992)
import math  # noqa: E402
steep = (1500 + 2500 * np.exp(-((x - 100) ** 2 + (y - 100) ** 2) / 400.0)).astype(np.float32)
hs.run_mdow_hillshade(make("steep.tif", steep), os.path.join(gdb, "mdow"),
                      azimuth=270, altitude=30, levels=256)
got = arcpy.RasterToNumPyArray(os.path.join(gdb, "mdow"), nodata_to_value=-9999).astype(float)
zz = steep.astype(float)
c = 30.0
NW, N_, NE = zz[:-2, :-2], zz[:-2, 1:-1], zz[:-2, 2:]
W_, E_ = zz[1:-1, :-2], zz[1:-1, 2:]
SW, S_, SE = zz[2:, :-2], zz[2:, 1:-1], zz[2:, 2:]
dzdx = ((NE + 2 * E_ + SE) - (NW + 2 * W_ + SW)) / (8 * c)
dzdy = ((SW + 2 * S_ + SE) - (NW + 2 * N_ + NE)) / (8 * c)
slope = np.arctan(np.hypot(dzdx, dzdy))
asp = np.arctan2(dzdy, -dzdx)
asp[asp < 0] += 2 * np.pi
zen = math.radians(60.0)
clamped = np.zeros_like(slope)
unclamped = np.zeros_like(slope)
for off in (-67.5, -22.5, 22.5, 67.5):
    dr = math.radians((360 - (270 + off) + 90) % 360)
    s = math.cos(zen) * np.cos(slope) + math.sin(zen) * np.sin(slope) * np.cos(dr - asp)
    w = np.sin(asp - dr) ** 2
    clamped += w * np.maximum(s, 0)
    unclamped += w * s
ref = np.minimum(np.floor(256 * clamped / 2), 255)
old = np.minimum(np.floor(np.maximum(256 * unclamped / 2, 0)), 255)
g = got[1:-1, 1:-1]
check("MDOW equals the per-sun-clamped blend", bool(np.all(np.abs(g - ref) <= 1)),
      "max diff %d" % int(np.abs(g - ref).max()))
check("MDOW test terrain separates the two rules", int((ref != old).sum()) > 1000,
      "%d cells differ between the rules" % int((ref != old).sum()))
check("MDOW never darker than the old blend-then-clamp rule", bool(np.all(g >= old - 1)))

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
