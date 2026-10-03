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

"""Hillshade gray levels (v1.99.42): N levels = values 0..N-1 in equal shares
of the 0-1 illumination range, and a raster attribute table for outputs of
32 or fewer levels. Covers all four hillshade modes.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" hillshade_levels_test.py
"""
import os
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
work = tempfile.mkdtemp(prefix="hs_levels_")
arcpy.env.scratchWorkspace = work
gdb = arcpy.management.CreateFileGDB(work, "out.gdb").getOutput(0)

# ---- synthetic DEM: a dome and a basin on a tilted plane, 30 m cells, UTM 12N
n = 240
y, x = np.mgrid[0:n, 0:n].astype(float)
dem = (1500 + 2.0 * x + 1.0 * y
       + 900 * np.exp(-((x - 80) ** 2 + (y - 90) ** 2) / 900.0)
       - 700 * np.exp(-((x - 170) ** 2 + (y - 150) ** 2) / 1200.0)
       + 25 * np.sin(x / 4.0) * np.cos(y / 5.0))
dem_path = os.path.join(work, "dem.tif")
ds = gdal.GetDriverByName("GTiff").Create(dem_path, n, n, 1, gdal.GDT_Float32)
ds.SetGeoTransform((400000.0, 30.0, 0, 4000000.0, 0, -30.0))
sr = osr.SpatialReference(); sr.ImportFromEPSG(26912)
ds.SetProjection(sr.ExportToWkt())
ds.GetRasterBand(1).WriteArray(dem.astype(np.float32))
ds.GetRasterBand(1).SetNoDataValue(-9999)
ds = None

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS", name, detail)
    else:
        failed += 1
        print("FAIL", name, detail)


def values_of(path):
    r = arcpy.RasterToNumPyArray(path, nodata_to_value=-9999)
    return r[r != -9999]


LYRX = {}


def run(label, fn, levels, **kw):
    out = os.path.join(gdb, "{}_{}".format(label, levels))
    LYRX[out] = fn(dem_path, out, elev_units="Meters", levels=levels, **kw)
    return out


def colorizer_of(out):
    import json
    with open(LYRX[out], encoding="utf-8") as f:
        return json.load(f)["layerDefinitions"][0]["colorizer"]


# reference: the continuous 0-1 illumination from a very fine run (32768 levels)
ref_out = run("ref", hs.run_enhanced_hillshade, 32768, azimuth=315, altitude=45)
ref = arcpy.RasterToNumPyArray(ref_out, nodata_to_value=-9999).astype(float)
ref_valid = ref != -9999
shade = (ref + 0.5) / 32768.0            # mid-class illumination, error < 2e-5

for N in (2, 3, 4, 8, 32, 33, 256):
    out = run("enh", hs.run_enhanced_hillshade, N, azimuth=315, altitude=45)
    arr = arcpy.RasterToNumPyArray(out, nodata_to_value=-9999)
    v = arr[arr != -9999]
    check("enhanced N=%d range" % N, v.min() >= 0 and v.max() <= N - 1,
          "min %d max %d" % (v.min(), v.max()))
    # exact rule: value = min(floor(N * shade), N - 1); allow the reference's
    # own quantization at class edges
    expect = np.minimum(np.floor(N * shade), N - 1)
    same = arr[ref_valid] == expect[ref_valid]
    edge = np.abs(N * shade[ref_valid] - np.rint(N * shade[ref_valid])) < N * 4e-5
    check("enhanced N=%d equals floor rule" % N, bool(np.all(same | edge)),
          "%d cells differ away from class edges" % int((~(same | edge)).sum()))
    has_rat = arcpy.Raster(out).hasRAT
    check("enhanced N=%d attribute table %s" % (N, "present" if N <= 32 else "absent"),
          has_rat == (N <= 32), "hasRAT=%s" % has_rat)
    col = colorizer_of(out)
    if N <= 32:
        classes = col["groups"][0]["classes"] if "groups" in col else []
        grays = [c["color"]["values"][:3] for c in classes]
        vals_in_lyrx = [int(c["values"][0]) for c in classes]
        check("enhanced N=%d layer file = gray unique values" % N,
              col["type"] == "CIMRasterUniqueValueColorizer" and col.get("fieldName") == "Value"
              and all(g[0] == g[1] == g[2] for g in grays)
              and vals_in_lyrx == sorted(np.unique(v).tolist())
              and grays[-1] == [255, 255, 255]
              and (grays[0] == [0, 0, 0] if vals_in_lyrx[0] == 0 else True)
              and grays == sorted(grays),
              "grays %s" % [g[0] for g in grays])
    else:
        check("enhanced N=%d layer file = stretch" % N,
              col["type"] == "CIMRasterStretchColorizer", col["type"])
    if N <= 32:
        rows = {int(r[0]): int(r[1]) for r in arcpy.da.SearchCursor(out, ["Value", "Count"])}
        u, c = np.unique(v, return_counts=True)
        check("enhanced N=%d table counts match the raster" % N,
              rows == dict(zip(u.tolist(), c.tolist())), str(rows))

# equal-width classes: with 3 levels the class edges sit at shade 1/3 and 2/3
out3 = os.path.join(gdb, "enh_3")
a3 = arcpy.RasterToNumPyArray(out3, nodata_to_value=-9999)
m = ref_valid & (a3 != -9999)
check("3 levels: class 0 holds shade < 1/3", bool(np.all(shade[m][a3[m] == 0] < 1 / 3 + 1e-4)))
check("3 levels: class 2 holds shade >= 2/3", bool(np.all(shade[m][a3[m] == 2] >= 2 / 3 - 1e-4)))

# the other three tools obey the same range and table rule
suns = [(280, 35, 50), (350, 45, 50)]
for label, fn, kw in (
        ("mdow", hs.run_mdow_hillshade, dict(azimuth=270, altitude=30)),
        ("multi", hs.run_multisun_hillshade, dict(suns=suns)),
        ("blurdem", hs.run_blurred_hillshade,
         dict(azimuth=315, altitude=45, blur_radius=3, radius_units="Cells",
              blur_target=hs.BLUR_TARGETS[0], kernel="Average")),
        ("blurhs", hs.run_blurred_hillshade,
         dict(azimuth=315, altitude=45, blur_radius=3, radius_units="Cells",
              blur_target=hs.BLUR_TARGETS[1], kernel="Average"))):
    for N in (4, 256):
        out = run(label, fn, N, **kw)
        v = values_of(out)
        check("%s N=%d range" % (label, N), v.min() >= 0 and v.max() <= N - 1,
              "min %d max %d" % (v.min(), v.max()))
        check("%s N=%d attribute table %s" % (label, N, "present" if N <= 32 else "absent"),
              arcpy.Raster(out).hasRAT == (N <= 32))
        want = "CIMRasterUniqueValueColorizer" if N <= 32 else "CIMRasterStretchColorizer"
        check("%s N=%d layer file type" % (label, N), colorizer_of(out)["type"] == want,
              colorizer_of(out)["type"])

# shadows and hypsometric shading stay inside the range too
out = run("enh_opts", hs.run_enhanced_hillshade, 5, azimuth=315, altitude=20,
          use_hypso=True, hypso_strength=60, model_shadows=True, shadow_dim=20)
v = values_of(out)
check("options N=5 range", v.min() >= 0 and v.max() <= 4, "min %d max %d" % (v.min(), v.max()))

# bad level counts are refused
for bad in (1, 0, 32769):
    try:
        run("bad", hs.run_enhanced_hillshade, bad, azimuth=315, altitude=45)
        check("levels=%d refused" % bad, False)
    except arcpy.ExecuteError:
        check("levels=%d refused" % bad, True)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
