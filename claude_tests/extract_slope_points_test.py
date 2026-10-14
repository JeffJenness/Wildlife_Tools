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

"""Extract Slope Values to Points (2026-10-14) and the write-to-the-input
option it shares with Extract Aspect Values to Points.

Synthetic 4 x 4 slope rasters (degrees, percent, rise:run, gradians of the
same ground) as GeoTIFFs, points in a scratch file geodatabase and a
shapefile. Checks: cell values as stored; bilinear interpolation on the
ANGLES (a point midway between 50- and 70-degree cells is 60 degrees =
173.2%, never the 197.0% mean of the percents); degree / percent / ratio /
gradian parity; NoData and out-of-range neighbors excluded; the in-place
mode (selection honored, other rows NULL, existing name uniquified);
shapefile -9999 and 10-character names; the unit-mismatch warning on Jeff's
real Extract_Slope_Percent raster when it is present; the aspect tool's
circular interpolation and in-place mode; and the two tools through
arcpy.ImportToolbox. Run with ArcGIS Pro's python."""
import math
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TBX = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TBX)

import arcpy  # noqa: E402
from osgeo import gdal, osr  # noqa: E402

import aspect_tools as at  # noqa: E402
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


def close(a, b, tol=1e-4):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


class Msgs(object):
    def __init__(self):
        self.lines = []
        self.warnings = []
        self.errors = []

    def addMessage(self, s):
        self.lines.append(str(s))

    def addWarningMessage(self, s):
        self.warnings.append(str(s))

    def addErrorMessage(self, s):
        self.errors.append(str(s))

    def any_warning(self, text):
        return any(text in w for w in self.warnings)


# ------------------------------------------------------------------ fixtures
EPSG = 32612
CELL = 10.0
X0, Y0 = 1000.0, 2000.0          # upper-left corner
NODATA = -9999.0

DEG = np.array([[50., 70., 50., 70.],
                [50., 70., 50., 70.],
                [20., 20., 20., 20.],
                [10., 10., NODATA, 10.]], np.float32)


def write_tif(path, arr):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((X0, CELL, 0.0, Y0, 0.0, -CELL))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(NODATA)          # before WriteArray (GDAL gotcha)
    band.WriteArray(arr)
    band.FlushCache()
    ds = None
    return path


def convert(deg, unit):
    out = deg.astype(np.float64).copy()
    ok = deg != NODATA
    if unit == "percent":
        out[ok] = np.tan(np.radians(deg[ok])) * 100.0
    elif unit == "ratio":
        out[ok] = np.tan(np.radians(deg[ok]))
    elif unit == "gradians":
        out[ok] = deg[ok] / 0.9
    return out.astype(np.float32)


# the six test points (name, x, y)
POINTS = [
    ("center4", 1010.0, 1990.0),   # midway between 50, 70, 50, 70
    ("oncell",  1005.0, 1995.0),   # exactly on the 50-degree cell center
    ("nodata",  1025.0, 1965.0),   # on the NoData cell center
    ("edge",    1030.0, 1970.0),   # 20, 20, NoData, 10 -> 16.667 degrees
    ("outside", 5000.0, 5000.0),   # beyond the raster
    ("row2",    1015.0, 1975.0),   # on the 20-degree cell center
]
EXPECT_NEAREST_DEG = {"center4": 70.0, "oncell": 50.0, "nodata": None,
                      "edge": 10.0, "outside": None, "row2": 20.0}
EXPECT_INTERP_DEG = {"center4": 60.0, "oncell": 50.0, "nodata": None,
                     "edge": 50.0 / 3.0, "outside": None, "row2": 20.0}


def make_points(gdb, name="pts", sr=None):
    fc = os.path.join(gdb, name)
    arcpy.management.CreateFeatureclass(
        gdb, name, "POINT",
        spatial_reference=sr or arcpy.SpatialReference(EPSG))
    arcpy.management.AddField(fc, "Name", "TEXT", field_length=16)
    with arcpy.da.InsertCursor(fc, ["Name", "SHAPE@XY"]) as cur:
        for nm, x, y in POINTS:
            cur.insertRow((nm, (x, y)))
    return fc


def values(fc, fld, key="Name"):
    out = {}
    with arcpy.da.SearchCursor(fc, [key, fld]) as cur:
        for k, v in cur:
            out[k] = v
    return out


def fields(fc):
    return [f.name for f in arcpy.ListFields(fc)]


def run_slope(points, tif, out, unit_label, interpolate, msgs=None,
              field_name="Slope", in_place=False):
    msgs = msgs or Msgs()
    fld = tt.run_extract_slope_points(points, tif, out, interpolate=interpolate,
                                      field_name=field_name,
                                      slope_units=unit_label, messages=msgs,
                                      in_place=in_place)
    return fld, msgs


def main():
    tmp = tempfile.mkdtemp(prefix="extract_slope_")
    print("scratch:", tmp)
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = tmp
    gdb = arcpy.management.CreateFileGDB(tmp, "t.gdb")[0]
    tifs = {u: write_tif(os.path.join(tmp, "slope_{}.tif".format(u)),
                         convert(DEG, u))
            for u in ("degrees", "percent", "ratio", "gradians")}
    labels = {"degrees": tt.SLOPE_UNITS_Z[0], "percent": tt.SLOPE_UNITS_Z[1],
              "ratio": tt.SLOPE_UNITS_Z[2], "gradians": tt.SLOPE_UNITS_Z[3]}
    pts = make_points(gdb)

    # 1. degrees, cell values ------------------------------------------------
    print("Degrees, values as stored")
    fld, m = run_slope(pts, tifs["degrees"], os.path.join(gdb, "deg_near"),
                       labels["degrees"], False)
    check("field named Slope", fld == "Slope", fld)
    got = values(os.path.join(gdb, "deg_near"), fld)
    for k, e in EXPECT_NEAREST_DEG.items():
        check("nearest {} = {}".format(k, e),
              (got[k] is None) if e is None else close(got[k], e), got[k])
    check("attributes carried (Name field)", "Name" in fields(os.path.join(gdb, "deg_near")))
    check("report counts 2 NoData/outside", any("2 on NoData" in s for s in m.lines), m.lines)
    check("no warnings on a clean raster", not m.warnings, m.warnings)

    # 2. degrees, interpolated ----------------------------------------------
    print("Degrees, bilinear on the angles")
    fld, m = run_slope(pts, tifs["degrees"], os.path.join(gdb, "deg_int"),
                       labels["degrees"], True)
    got_deg = values(os.path.join(gdb, "deg_int"), fld)
    for k, e in EXPECT_INTERP_DEG.items():
        check("interp {} = {}".format(k, None if e is None else round(e, 4)),
              (got_deg[k] is None) if e is None else close(got_deg[k], e), got_deg[k])

    # 3. percent: angle space, never the mean of the percents --------------
    print("Percent slope")
    fld, m = run_slope(pts, tifs["percent"], os.path.join(gdb, "pct_int"),
                       labels["percent"], True)
    got_pct = values(os.path.join(gdb, "pct_int"), fld)
    check("center4 = tan(60) x 100 = 173.205", close(got_pct["center4"], 173.2051, 1e-3),
          got_pct["center4"])
    check("center4 is NOT the mean of the percents (196.96)",
          not close(got_pct["center4"], 196.9616, 0.5), got_pct["center4"])
    for k in ("center4", "oncell", "edge", "row2"):
        check("percent parity {}: pct == tan(deg) x 100".format(k),
              close(got_pct[k], math.tan(math.radians(got_deg[k])) * 100.0, 1e-3),
              (got_pct[k], got_deg[k]))
    fld, m = run_slope(pts, tifs["percent"], os.path.join(gdb, "pct_near"),
                       labels["percent"], False)
    got = values(os.path.join(gdb, "pct_near"), fld)
    check("percent nearest center4 = 274.75 as stored", close(got["center4"], 274.7477, 1e-3),
          got["center4"])
    check("report names percent", any("percent" in s for s in m.lines), m.lines)

    # 4. ratio and gradians ------------------------------------------------
    print("Rise:run and gradians")
    fld, m = run_slope(pts, tifs["ratio"], os.path.join(gdb, "rat_int"),
                       labels["ratio"], True)
    got = values(os.path.join(gdb, "rat_int"), fld)
    check("ratio center4 = tan(60) = 1.7321", close(got["center4"], 1.7320508, 1e-5), got["center4"])
    fld, m = run_slope(pts, tifs["gradians"], os.path.join(gdb, "gon_int"),
                       labels["gradians"], True)
    got = values(os.path.join(gdb, "gon_int"), fld)
    check("gradians center4 = 66.667 gon", close(got["center4"], 200.0 / 3.0, 1e-3), got["center4"])
    check("gradians edge = 18.519 gon", close(got["edge"], 50.0 / 3.0 / 0.9, 1e-3), got["edge"])

    # 5. out-of-range cells -------------------------------------------------
    print("Out-of-range cells (a percent raster read as degrees)")
    fld, m = run_slope(pts, tifs["percent"], os.path.join(gdb, "pct_as_deg"),
                       labels["degrees"], True)
    got = values(os.path.join(gdb, "pct_as_deg"), fld)
    check("warning about the valid range", m.any_warning("outside the valid range for degrees"),
          m.warnings)
    # 119.2 and 274.7 are beyond 90 -> excluded; center4 has no usable neighbor
    check("center4 has no value (all four neighbors out of range)", got["center4"] is None, got["center4"])
    # edge: 36.4, 36.4, NoData, 17.6 (percent values under 90) -> mean of those as degrees
    exp = (36.397 + 36.397 + 17.633) / 3.0
    check("edge uses the in-range neighbors only", close(got["edge"], exp, 0.01), (got["edge"], exp))
    fld, m = run_slope(pts, tifs["percent"], os.path.join(gdb, "pct_as_deg_n"),
                       labels["degrees"], False)
    got = values(os.path.join(gdb, "pct_as_deg_n"), fld)
    check("nearest still writes the stored value", close(got["center4"], 274.7477, 1e-3), got["center4"])
    check("nearest warning says stored anyway", m.any_warning("receive the stored value anyway"), m.warnings)

    # 6. in place, with a selection -----------------------------------------
    print("In place")
    pts2 = make_points(gdb, "pts_inplace")
    lyr = arcpy.management.MakeFeatureLayer(pts2, "pts_lyr")[0]
    arcpy.management.SelectLayerByAttribute(lyr, "NEW_SELECTION", "Name IN ('center4', 'oncell')")
    fld, m = run_slope(lyr, tifs["degrees"], None, labels["degrees"], True, in_place=True)
    check("field added to the input", "Slope" in fields(pts2) and fld == "Slope", fields(pts2))
    got = values(pts2, "Slope")
    check("selected point center4 = 60", close(got["center4"], 60.0), got["center4"])
    check("selected point oncell = 50", close(got["oncell"], 50.0), got["oncell"])
    check("unselected rows NULL", all(got[k] is None for k in ("edge", "row2", "nodata", "outside")), got)
    check("report counts the rows left NULL", any("4 feature(s) outside" in s for s in m.lines), m.lines)
    check("in-place says Added the field", any("Added the field 'Slope'" in s for s in m.lines), m.lines)
    arcpy.management.SelectLayerByAttribute(lyr, "CLEAR_SELECTION")
    fld, m = run_slope(lyr, tifs["degrees"], None, labels["degrees"], False, in_place=True)
    check("existing name uniquified to Slope_2", fld == "Slope_2", fld)
    check("uniquify warning", m.any_warning("created as 'Slope_2'"), m.warnings)
    got = values(pts2, "Slope_2")
    check("all rows written without a selection", close(got["center4"], 70.0) and close(got["row2"], 20.0), got)
    check("write_to_input recognizes the label",
          at.write_to_input(at.WRITE_TO_INPUT) and not at.write_to_input(at.WRITE_TO_NEW))

    # 7. shapefile output ---------------------------------------------------
    print("Shapefile")
    shp = os.path.join(tmp, "out_pts.shp")
    fld, m = run_slope(pts, tifs["degrees"], shp, labels["degrees"], False,
                       field_name="SlopeValuesLong")
    check("shapefile field name cut to 10", fld == "SlopeValue", fld)
    got = values(shp, fld)
    check("shapefile NoData is -9999", close(got["outside"], -9999.0) and close(got["nodata"], -9999.0), got)
    check("report says -9999", any("-9999" in s for s in m.lines), m.lines)

    # 8. unit mismatch ------------------------------------------------------
    print("Unit mismatch")
    txt = tt.unit_mismatch_text("percent", tt.SLOPE_UNITS_Z[0])
    check("mismatch text names both units",
          "percent slope" in txt and tt.SLOPE_UNITS_Z[0] in txt and "read as degrees" in txt, txt)
    check("no history -> no mismatch", tt.slope_unit_mismatch(tifs["percent"], tt.SLOPE_UNITS_Z[0]) is None)
    real = r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife\Pro_Test_Project\Pro Test Tools.gdb\Extract_Slope_Percent"
    if arcpy.Exists(real):
        check("real percent raster detected", tt._detect_slope_units(real) == "percent",
              tt._detect_slope_units(real))
        check("percent raster + Degrees chosen -> mismatch",
              tt.slope_unit_mismatch(real, tt.SLOPE_UNITS_Z[0]) == ("percent", "degrees"))
        check("percent raster + Percent chosen -> none",
              tt.slope_unit_mismatch(real, tt.SLOPE_UNITS_Z[1]) is None)
        check("percent raster + Rise:run chosen -> mismatch",
              tt.slope_unit_mismatch(real, tt.SLOPE_UNITS_Z[2]) == ("percent", "ratio"))
        ext = arcpy.Describe(real).extent
        sr = arcpy.Describe(real).spatialReference
        fc = os.path.join(gdb, "real_pts")
        arcpy.management.CreateFeatureclass(gdb, "real_pts", "POINT", spatial_reference=sr)
        cx, cy = (ext.XMin + ext.XMax) / 2.0, (ext.YMin + ext.YMax) / 2.0
        with arcpy.da.InsertCursor(fc, ["SHAPE@XY"]) as cur:
            for dx, dy in ((0, 0), (ext.width * 0.1, ext.height * 0.1), (-ext.width * 0.1, -ext.height * 0.05)):
                cur.insertRow(((cx + dx, cy + dy),))
        fld, m = run_slope(fc, real, os.path.join(gdb, "real_out"), tt.SLOPE_UNITS_Z[0], True)
        check("run warns about the unit mismatch", m.any_warning("geoprocessing history records its values as percent slope"), m.warnings)
        fld, m = run_slope(fc, real, os.path.join(gdb, "real_out2"), tt.SLOPE_UNITS_Z[1], True)
        check("run with the right unit does not warn about units", not m.any_warning("geoprocessing history"), m.warnings)
    else:
        print("  (Jeff's Extract_Slope_Percent raster not found; history checks skipped)")

    # 8b. unit detection from history text: pane labels and Python keywords
    print("Unit detection from history text")
    H = tt._slope_units_from_history
    pane_pct = ('<Process ToolSource="c:\\...\\Spatial Analyst Tools.tbx\\Slope">Slope '
                'DEM_Filled "C:\\x\\Lab.gdb\\Flagstaff_Percent_Slope" "Percent rise" 1 '
                'Geodesic METER "GPU then CPU"</Process>')
    pane_deg = ('<Process ToolSource="...\\Slope">Slope DEM "C:\\x\\Lab.gdb\\Flagstaff_Percent_Slope" '
                '"Degree" 1 Geodesic METER</Process>')
    py_pct = '<Process ToolSource="...\\Slope">Slope dem out PERCENT_RISE 1 GEODESIC METER</Process>'
    py_deg = '<Process ToolSource="...\\SurfaceParameters">SurfaceParameters dem out SLOPE 3 METERS FIXED_NEIGHBORHOOD DEGREE</Process>'
    check("pane label Percent rise -> percent", H(pane_pct) == "percent", H(pane_pct))
    check("pane label Degree -> degrees (name with Percent does not fool it)", H(pane_deg) == "degrees", H(pane_deg))
    check("keyword PERCENT_RISE -> percent", H(py_pct) == "percent", H(py_pct))
    check("keyword DEGREE -> degrees", H(py_deg) == "degrees", H(py_deg))
    check("last slope process wins", H(py_deg + py_pct) == "percent")
    check("no slope process -> None", H("<Process ToolSource=\"...\\CopyRaster\">CopyRaster a b</Process>") is None)
    check("empty -> None", H("") is None and H(None) is None)
    flag = r"D:\ArcGIS_stuff\Teaching\FOR_333\2025\00_Homework\Kaufman_Nathan\05_Watersheds\Scratch_Watershed_Lab.gdb\Flagstaff_Percent_Slope"
    if arcpy.Exists(flag):
        check("Jeff's pane-made Flagstaff_Percent_Slope detected as percent",
              tt._detect_slope_units(flag) == "percent", tt._detect_slope_units(flag))
    else:
        print("  (Flagstaff_Percent_Slope not found; skipped)")

    # 9. the aspect tool still works, and in place ------------------------
    print("Aspect tool regression")
    asp = np.array([[359., 1.], [359., 1.]], np.float32)
    asp_tif = write_tif(os.path.join(tmp, "aspect.tif"), asp)
    afc = os.path.join(gdb, "asp_pts")
    arcpy.management.CreateFeatureclass(gdb, "asp_pts", "POINT", spatial_reference=arcpy.SpatialReference(EPSG))
    with arcpy.da.InsertCursor(afc, ["SHAPE@XY"]) as cur:
        cur.insertRow(((1010.0, 1990.0),))    # midway between the four cells
    m = Msgs()
    fld = at.run_extract_aspect_points(afc, asp_tif, os.path.join(gdb, "asp_out"), interpolate=True, messages=m)
    v = [r[0] for r in arcpy.da.SearchCursor(os.path.join(gdb, "asp_out"), [fld])][0]
    check("aspect 359|1 interpolates to ~0, not 180", v is not None and (v < 1.0 or v > 359.0), v)
    m = Msgs()
    fld = at.run_extract_aspect_points(afc, asp_tif, None, interpolate=False, messages=m, in_place=True)
    check("aspect in place adds Aspect to the input", "Aspect" in fields(afc) and fld == "Aspect", fields(afc))
    v = [r[0] for r in arcpy.da.SearchCursor(afc, ["Aspect"])][0]
    check("aspect in place nearest = 1 (the containing cell)", close(v, 1.0), v)
    check("aspect report counts", any("Sampled 1 point(s)" in s for s in m.lines), m.lines)

    # 10. through the toolbox --------------------------------------------
    print("Through arcpy.ImportToolbox")
    arcpy.ImportToolbox(os.path.join(TBX, "JennessEnterprisesTools.pyt"))
    pts3 = make_points(gdb, "pts_gp")
    res = arcpy.jenness.ExtractSlopeValuesToPoints(
        in_points=pts3, in_raster=tifs["percent"], write_to=at.WRITE_TO_INPUT,
        interpolate=True, field_name="SlopeGP", slope_units=labels["percent"])
    check("GP in place: field SlopeGP on the input", "SlopeGP" in fields(pts3), fields(pts3))
    got = values(pts3, "SlopeGP")
    check("GP in place: center4 = 173.205", close(got["center4"], 173.2051, 1e-3), got["center4"])
    outs = [res.getOutput(i) for i in range(res.outputCount)]
    check("GP in place: derived output names the input", any(str(o).endswith("pts_gp") for o in outs), outs)
    out_gp = os.path.join(gdb, "pts_gp_copy")
    arcpy.jenness.ExtractSlopeValuesToPoints(
        in_points=pts3, in_raster=tifs["degrees"], write_to=at.WRITE_TO_NEW,
        out_points=out_gp, interpolate=False, field_name="Slope",
        slope_units=labels["degrees"])
    check("GP new feature class made", arcpy.Exists(out_gp) and "Slope" in fields(out_gp))
    check("GP new feature class carries SlopeGP too", "SlopeGP" in fields(out_gp), fields(out_gp))
    arcpy.jenness.ExtractAspectValuesToPoints(
        in_points=afc, in_raster=asp_tif, write_to=at.WRITE_TO_NEW,
        out_points=os.path.join(gdb, "asp_gp"), interpolate=True, field_name="AspGP",
        input_convention=at.INPUT_CONVENTIONS[0])
    check("GP aspect new feature class", "AspGP" in fields(os.path.join(gdb, "asp_gp")))

    print("\n{} passed, {} failed".format(PASS, FAIL))
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
