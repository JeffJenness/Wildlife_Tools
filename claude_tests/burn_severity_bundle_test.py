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

"""Load Burn Severity Bundle (burn_severity_bundle.py). Nothing here touches
the Burn Severity Portal: the bundles are made up, with rasters of 20 by 16
cells whose values are set by this file, so every count, area and mean
below is worked out here and not taken from the tool. The one place where
the engine asks the portal's fire catalog (a fire in the portal's older
form) is answered by a stand-in.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" burn_severity_bundle_test.py
"""
import datetime
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile

import numpy as np
from osgeo import gdal, osr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import burn_severity as B  # noqa: E402
import burn_severity_bundle as BB  # noqa: E402

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
    except BB.BundleError as e:
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


def no_catalog(*_a, **_k):
    raise AssertionError("the test asked the fire catalog when it should not have")


REAL_SEARCH = B.search
B.search = no_catalog                    # nothing below may reach the portal

arcpy.env.overwriteOutput = True
work = tempfile.mkdtemp(prefix="bundle_test_")
ROWS, COLS, CELL = 16, 20, 30.0
X0, YTOP = 400000.0, 3900000.0 + ROWS * CELL
INSIDE = np.zeros((ROWS, COLS), bool)
INSIDE[3:13, 4:16] = True                # the burned area: 10 rows by 12 columns = 120 cells
PERIM = [(X0 + 4 * CELL, YTOP - 13 * CELL), (X0 + 4 * CELL, YTOP - 3 * CELL), (X0 + 16 * CELL, YTOP - 3 * CELL),
         (X0 + 16 * CELL, YTOP - 13 * CELL)]                                    # its outline, on cell edges
HA, ACRE = CELL * CELL / 10000.0, CELL * CELL / (43560.0 * 0.3048 ** 2)        # one cell: 0.09 ha, 0.2224 acres
RAMP = (np.arange(ROWS)[:, None] * 100 + np.arange(COLS)[None, :])             # every cell its own value


def classes_inside(counts, outside):
    """A class raster: ``outside`` everywhere but the burned area, which is filled in reading order with
    ``counts`` = [(value, how many cells), ...] adding up to 120."""
    a = np.full((ROWS, COLS), outside, np.uint8)
    a[INSIDE] = np.concatenate([np.full(n, v, np.uint8) for v, n in counts])
    return a


def write_raster(path, a, epsg, nodata=None, colors=False, driver="GTiff"):
    kind = {"uint8": gdal.GDT_Byte, "int16": gdal.GDT_Int16, "float32": gdal.GDT_Float32}[a.dtype.name]
    bands = 1 if a.ndim == 2 else a.shape[0]
    ds = gdal.GetDriverByName(driver).Create(path, COLS, ROWS, bands, kind)
    ds.SetGeoTransform((X0, CELL, 0.0, YTOP, 0.0, -CELL))
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(epsg)
    ds.SetProjection(sr.ExportToWkt())
    for b in range(bands):
        band = ds.GetRasterBand(b + 1)
        if colors:                               # a GeoTIFF takes its color table only before its cells
            ct = gdal.ColorTable()
            for v in range(256):
                ct.SetColorEntry(v, (v, 255 - v, 0, 255))
            band.SetColorTable(ct)
        if nodata is not None:
            band.SetNoDataValue(nodata)
        band.WriteArray(a if a.ndim == 2 else a[b])
    ds = None


def write_perimeter(path, epsg, attrs):
    folder, name = os.path.split(path)
    fc = arcpy.management.CreateFeatureclass(folder, name, "POLYGON", spatial_reference=arcpy.SpatialReference(epsg))[0]
    spec = [("Event_ID", "TEXT"), ("Incid_Name", "TEXT"), ("Incid_Type", "TEXT"), ("Map_ID", "LONG"),
            ("Map_Prog", "TEXT"), ("Asmnt_Type", "TEXT"), ("BurnBndAc", "DOUBLE"), ("Ig_Date", "DATE"),
            ("Pre_ID", "TEXT"), ("Post_ID", "TEXT"), ("dNBR_offst", "LONG"), ("dNBR_stdDv", "LONG"),
            ("IncGreen_T", "LONG"), ("Low_T", "LONG"), ("Mod_T", "LONG"), ("High_T", "LONG"), ("Comment", "TEXT")]
    for nm, kind in spec:
        arcpy.management.AddField(fc, nm, kind, field_length=60 if kind == "TEXT" else None)
    shape = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in PERIM + PERIM[:1]]), arcpy.SpatialReference(epsg))
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + [s[0] for s in spec]) as c:
        # a shapefile has no empty values: text is "" and a number 0 where the bundle says nothing
        c.insertRow([shape] + [attrs.get(nm, "" if kind == "TEXT" else None if kind == "DATE" else 0)
                               for nm, kind in spec])


# ---------------------------------------------------------------------------------------------------------
#  the made-up bundle: one fire of each program
# ---------------------------------------------------------------------------------------------------------
bundle = os.path.join(work, "bundle_src")
MT, RV, BA = "AZ3525811164320190721", "CA3712011932120200904", "NM3364610811320210605"
mt_dir = os.path.join(bundle, "mtbs", MT.lower())
rv_dir = os.path.join(bundle, "ravg", RV.lower())
ba_dir = os.path.join(bundle, "baer", BA.lower())
for d in (mt_dir, rv_dir, ba_dir):
    os.makedirs(d)
mt = os.path.join(mt_dir, "mtbs_%s_10016328_20190705_20200707_" % MT.lower())
rv = os.path.join(rv_dir, "ravg_%s_2001_20200820_20200925_" % RV.lower())
ba = os.path.join(ba_dir, "baer_%s_3001_20210601_20210612_" % BA.lower())

# MTBS: dNBR with three cells of the burned area missing; RdNBR that declares -9999 and is filled with
# -32768 outside the fire; six classes with no "no data" value declared and 0 outside the fire; two images.
DNBR = RAMP.astype(np.int16).copy()
for rc in ((3, 4), (5, 9), (12, 15)):
    DNBR[rc] = -32768
RDNBR = (2000 - RAMP).astype(np.int16)
RDNBR[~INSIDE] = -32768
RDNBR[4, 5] = RDNBR[6, 6] = -32768
DNBR6_COUNTS = [(1, 40), (2, 30), (3, 20), (4, 15), (5, 5), (6, 10)]            # 6 is the mask class
DNBR6 = classes_inside(DNBR6_COUNTS, 0)
IMAGE = np.stack([RAMP.astype(np.int16) + k for k in (1, 2, 3)])
write_raster(mt + "dnbr.tif", DNBR, 26912, nodata=-32768)
write_raster(mt + "rdnbr.tif", RDNBR, 26912, nodata=-9999)
write_raster(mt + "dnbr6.tif", DNBR6, 26912, colors=True)
write_raster(os.path.join(mt_dir, "mtbs_%s_10016328_20190705_l8_refl.tif" % MT.lower()), IMAGE, 26912)
write_raster(os.path.join(mt_dir, "mtbs_%s_10016328_20200707_l8_refl.tif" % MT.lower()), IMAGE, 26912)
write_perimeter(mt + "burn_bndy.shp", 26912, {
    "Event_ID": MT, "Incid_Name": "WOODBURY", "Incid_Type": "Wildfire", "Map_ID": 10016328, "Map_Prog": "MTBS",
    "Asmnt_Type": "Extended", "BurnBndAc": 26.7, "Ig_Date": datetime.datetime(2019, 6, 8),
    "Pre_ID": "804003620190705", "Post_ID": "804003620200707", "dNBR_offst": -12, "dNBR_stdDv": 9,
    "IncGreen_T": -9999, "Low_T": 70, "Mod_T": 280, "High_T": 520, "Comment": ""})

# RAVG, in another UTM zone: basal area loss as decimals with four cells missing; seven classes with 0 declared
# as "no data"; four classes whose file name has two words for the product, with 255 declared.
BASAL = (RAMP / 16.0).astype(np.float32)
for rc in ((3, 5), (7, 7), (9, 14), (12, 4)):
    BASAL[rc] = -9999.0
BA7_COUNTS = [(1, 20), (2, 10), (3, 10), (4, 20), (5, 20), (6, 10), (7, 20), (9, 10)]      # 9 is the mask class
CBI4_COUNTS = [(1, 60), (2, 30), (3, 20), (4, 10)]
write_raster(rv + "ba.tif", BASAL, 26911, nodata=-9999.0)
write_raster(rv + "ba7.tif", classes_inside(BA7_COUNTS, 0), 26911, nodata=0, colors=True)
write_raster(rv + "rdnbr_cbi4.tif", classes_inside(CBI4_COUNTS, 255), 26911, nodata=255, colors=True)
write_perimeter(rv + "burn_bndy.shp", 26911, {
    "Event_ID": RV, "Incid_Name": "CREEK", "Incid_Type": "Wildfire", "Map_ID": 2001, "Map_Prog": "RAVG",
    "Asmnt_Type": "Initial", "BurnBndAc": 26.7, "Ig_Date": datetime.datetime(2020, 9, 4)})

# BAER: soil burn severity that declares 0 as "no data", holds none, and is filled with 255 outside the fire;
# and the 256-value BARC, which is not a class raster.
SBS_COUNTS = [(1, 50), (2, 40), (3, 20), (4, 5), (5, 5)]                         # 5 is the mask class
BARC256 = (RAMP % 200).astype(np.uint8)
write_raster(ba + "sbs.tif", classes_inside(SBS_COUNTS, 255), 32613, nodata=0, colors=True)
write_raster(ba + "barc256.tif", BARC256, 32613)
write_perimeter(ba + "burn_bndy.shp", 32613, {
    "Event_ID": BA, "Incid_Name": "JOHNSON 2", "Incid_Type": "Wildfire", "Map_ID": 3001, "Map_Prog": "BAER",
    "Asmnt_Type": "Emergency", "BurnBndAc": 26.7, "Ig_Date": datetime.datetime(2021, 6, 5)})

# ---- 1. reading the names of a bundle's files
fires = BB.find_fires(bundle)
check("names: three fires, each known by its program, its ID and its map number",
      sorted(fires) == [("BAER", BA, "3001"), ("MTBS", MT, "10016328"), ("RAVG", RV, "2001")], sorted(fires))
f = fires.get(("MTBS", MT, "10016328"), {"rasters": {}})
check("names: the MTBS fire's rasters, with the two images told apart by their dates",
      sorted(f["rasters"]) == ["dnbr", "dnbr6", "post_20200707_l8_refl", "pre_20190705_l8_refl", "rdnbr"],
      sorted(f["rasters"]))
check("names: its perimeter is the shapefile", str(f.get("perimeter", "")).endswith("burn_bndy.shp"), f.get("perimeter"))
check("names: a product named with two words keeps both",
      sorted(fires.get(("RAVG", RV, "2001"), {"rasters": {}})["rasters"]) == ["ba", "ba7", "rdnbr_cbi4"],
      fires.get(("RAVG", RV, "2001")))
loose = os.path.join(work, "loose")
os.makedirs(loose)
for nm in ("mtbs_az3525811164320190721_10016328_20190705_20200707_dnbr.tif",
           "MTBS_AZ3525811164320190721_10016328_20190705_20200707_DNBR6.TIF",
           "mtbs_az3525811164320190721_10016328_20190705_20200707_rep_pts.shp",
           "mtbs_az3525811164320190721_10016328_20190705_20200707_burn_bndy.shp",
           "mtbs_az3525811164320190721_10016328_20190705_20200707_metadata.xml",
           "readme.txt", "dnbr.tif", "mtbs_notanevent_1_dnbr.tif",
           "mtbs_az3525811164320190721_10016329_20190705_20200707_dnbr.tif"):
    open(os.path.join(loose, nm), "w").close()
lf = BB.find_fires(loose)
k1, k2 = ("MTBS", MT, "10016328"), ("MTBS", MT, "10016329")
check("names: capital letters in a file name make no difference; a second map of the same fire is a second entry",
      sorted(lf) == [k1, k2] and sorted(lf[k1]["rasters"]) == ["dnbr", "dnbr6"] and sorted(lf[k2]["rasters"]) == ["dnbr"],
      dict((k, sorted(v["rasters"])) for k, v in lf.items()))
check("names: of two shapefiles, the perimeter is the one named for the burn boundary",
      os.path.basename(lf[k1]["perimeter"]).endswith("burn_bndy.shp"), lf[k1]["perimeter"])
check("names: the dates before and after the fire are read from the name",
      (lf[k1]["pre"], lf[k1]["post"]) == ("20190705", "20200707"), (lf[k1]["pre"], lf[k1]["post"]))

# ---- 2. what kind of raster a product is
u8 = np.array([[1, 2], [3, 0]], np.uint8)
check("kinds: the known products",
      [BB.classify_product(p, u8, None, True, np)[0] for p in ("dnbr", "rdnbr", "dnbr6", "ba", "cc", "cbi", "ba7",
                                                                "cc5", "cbi4", "sbs", "barc4", "barc256")]
      == ["continuous", "continuous", "classified", "continuous", "continuous", "continuous", "classified",
          "classified", "classified", "classified", "classified", "continuous"])
check("kinds: each class raster gets its own program's classes",
      BB.classify_product("dnbr6", u8, None, True, np)[1] is B.MTBS_CLASSES
      and BB.classify_product("ba7", u8, None, True, np)[1] is B.BA7_CLASSES
      and BB.classify_product("cc5", u8, None, True, np)[1] is B.CC5_CLASSES
      and BB.classify_product("rdnbr_cbi4", u8, None, True, np)[1] is B.CBI4_CLASSES
      and BB.classify_product("sbs", u8, None, True, np)[1] is B.SBS_CLASSES)
check("kinds: an unknown product of a few whole values is taken as classes without names",
      BB.classify_product("mystery", u8, None, True, np)[:2] == ("classified", None))
check("kinds: an unknown product of many values, or of decimals, is taken as continuous",
      BB.classify_product("mystery", np.arange(100, dtype=np.uint8), None, True, np)[0] == "continuous"
      and BB.classify_product("mystery", u8.astype(np.float32), None, False, np)[0] == "continuous")
check("classes: the mask class of each program is the one left out of the percentages",
      [[c["value"] for c in cl if c["mask"]] for cl in (B.MTBS_CLASSES, B.BA7_CLASSES, B.CC5_CLASSES, B.CBI4_CLASSES,
                                                        B.SBS_CLASSES)] == [[6], [9], [9], [9], [5]])

# ---- 3. unpacking
zip_path = os.path.join(work, "Jenness_bundle_2026.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for base, _d, files in os.walk(bundle):
        for fn in files:
            full = os.path.join(base, fn)
            z.write(full, os.path.relpath(full, bundle))
home = os.path.join(work, "home")
os.makedirs(home)
folder, unpacked = BB.unpack(zip_path, home)
check("unpack: a zip goes into a folder of its own name under Burn_Severity_Bundles",
      unpacked is True and folder == os.path.join(home, "Burn_Severity_Bundles", "Jenness_bundle_2026")
      and os.path.isfile(os.path.join(folder, "mtbs", MT.lower(), os.path.basename(mt + "dnbr.tif"))), folder)
check("unpack: every file of the zip is there, whole",
      sorted(os.path.relpath(os.path.join(b, fn), folder) for b, _d, fs in os.walk(folder) for fn in fs)
      == sorted(os.path.relpath(os.path.join(b, fn), bundle) for b, _d, fs in os.walk(bundle) for fn in fs)
      and open(os.path.join(folder, "mtbs", MT.lower(), os.path.basename(mt + "dnbr.tif")), "rb").read()
      == open(mt + "dnbr.tif", "rb").read())
stamp = os.path.getmtime(os.path.join(folder, "mtbs", MT.lower(), os.path.basename(mt + "dnbr.tif")))
BB.unpack(zip_path, home)
check("unpack: a second time, files already there are not written again",
      os.path.getmtime(os.path.join(folder, "mtbs", MT.lower(), os.path.basename(mt + "dnbr.tif"))) == stamp)
check("unpack: a folder is used where it is", BB.unpack(bundle, home) == (bundle, False))
check("unpack: quotation marks around a pasted path are ignored", BB.unpack('"%s"' % bundle, home) == (bundle, False))
raises("unpack: a path that is not there", lambda: BB.unpack(os.path.join(work, "nothing.zip"), home),
       "There is no file or folder at")
not_zip = os.path.join(work, "not_a_zip.zip")
open(not_zip, "w").write("this is text")
raises("unpack: a file that is not a zip", lambda: BB.unpack(not_zip, home), "is not a zip file")
evil = os.path.join(work, "evil.zip")
with zipfile.ZipFile(evil, "w") as z:
    z.writestr("ok.txt", "fine")
    z.writestr("../../escaped_by_the_bundle_test.txt", "should never be written")
raises("unpack: a zip with an entry that points outside its own folder is refused", lambda: BB.unpack(evil, home),
       "holds an entry outside its own folder")
check("unpack: and nothing was written outside",
      not os.path.exists(os.path.join(home, "escaped_by_the_bundle_test.txt"))
      and not os.path.exists(os.path.join(work, "escaped_by_the_bundle_test.txt"))
      and not os.path.exists(os.path.join(home, "Burn_Severity_Bundles", "escaped_by_the_bundle_test.txt")))

# ---- 4. a whole run, from the zip, into a geodatabase
gdb = arcpy.management.CreateFileGDB(home, "fires.gdb")[0]
perims, summary = os.path.join(gdb, "bundle_perimeters"), os.path.join(gdb, "bundle_summary")
m = Msgs()
r = BB.run(zip_path, gdb, out_perimeters=perims, out_summary=summary, messages=m)
check("run: three fires, ten rasters, the perimeters and the summary",
      r["fires"] == 3 and len(r["rasters"]) == 10 and r["perimeters"] == perims and r["summary"] == summary,
      (r["fires"], len(r["rasters"]), r["perimeters"], r["summary"]))
check("run: it says where the zip was unpacked and what it holds",
      m.has("Unpacked the zip file into " + folder) and m.has("The bundle holds 3 fires."), m.lines[:2])
check("run: each fire is listed with its rasters",
      m.has("MTBS WOODBURY 2019 (%s): dnbr, dnbr6, post_20200707_l8_refl, pre_20190705_l8_refl, rdnbr." % MT)
      and m.has("RAVG CREEK 2020 (%s): ba, ba7, rdnbr_cbi4." % RV)
      and m.has("BAER JOHNSON 2 2021 (%s): barc256, sbs." % BA), m.lines[2:6])
check("run: nothing warned about", m.warnings == [], m.warnings)
names = sorted(os.path.basename(p) for p in r["rasters"])
check("run: the rasters are named for the program, the fire, the year and the product",
      names == sorted(["MTBS_WOODBURY_2019_dnbr", "MTBS_WOODBURY_2019_dnbr6", "MTBS_WOODBURY_2019_rdnbr",
                       "MTBS_WOODBURY_2019_pre_20190705_l8_refl", "MTBS_WOODBURY_2019_post_20200707_l8_refl",
                       "RAVG_CREEK_2020_ba", "RAVG_CREEK_2020_ba7", "RAVG_CREEK_2020_rdnbr_cbi4",
                       "BAER_JOHNSON_2_2021_barc256", "BAER_JOHNSON_2_2021_sbs"]), names)


def out(name):
    return os.path.join(gdb, name)


def cells_of(name):
    return arcpy.RasterToNumPyArray(out(name))


def table_of(name):
    return dict((int(r[0]), r[1:]) for r in arcpy.da.SearchCursor(
        out(name), ["Value", "Count", "Class_Name", "Hectares", "Acres", "Percent"]))


def summary_rows(product, fire_name):
    flds = [f[0] for f in BB.SUMMARY_FIELDS]
    return [dict(zip(flds, row)) for row in arcpy.da.SearchCursor(summary, flds)
            if row[flds.index("product")] == product and row[flds.index("fire_name")] == fire_name]


def close(a, b, rel=1e-9):
    return a is not None and b is not None and abs(a - b) <= rel * max(1.0, abs(b))


# the rasters themselves: same cells, same place, same projection
d = arcpy.Describe(out("MTBS_WOODBURY_2019_dnbr"))
check("rasters: copied with their own cells (30 m), extent and projection",
      (d.width, d.height, d.meanCellWidth, d.meanCellHeight) == (COLS, ROWS, CELL, CELL)
      and close(d.extent.XMin, X0) and close(d.extent.YMax, YTOP) and d.spatialReference.factoryCode == 26912,
      (d.width, d.height, d.meanCellWidth, d.extent.XMin, d.extent.YMax, d.spatialReference.factoryCode))
check("rasters: each fire keeps its own projection",
      arcpy.Describe(out("RAVG_CREEK_2020_ba")).spatialReference.factoryCode == 26911
      and arcpy.Describe(out("BAER_JOHNSON_2_2021_sbs")).spatialReference.factoryCode == 32613)
check("rasters: the values are the bundle's values", np.array_equal(cells_of("MTBS_WOODBURY_2019_dnbr"), DNBR)
      and np.array_equal(cells_of("MTBS_WOODBURY_2019_dnbr6"), DNBR6)
      and np.array_equal(cells_of("BAER_JOHNSON_2_2021_barc256"), BARC256)
      and np.allclose(cells_of("RAVG_CREEK_2020_ba"), BASAL))
check("rasters: an image of several bands is copied with all its bands",
      arcpy.Describe(out("MTBS_WOODBURY_2019_pre_20190705_l8_refl")).bandCount == 3
      and np.array_equal(cells_of("MTBS_WOODBURY_2019_pre_20190705_l8_refl"), IMAGE))


def empty_cells(name):
    """Which cells ArcGIS takes as 'no data' (a geodatabase raster keeps them as a mask, not as a value)."""
    return (arcpy.RasterToNumPyArray(out(name), nodata_to_value=77)
            != arcpy.RasterToNumPyArray(out(name), nodata_to_value=78))


check("rasters: the cells without data are the ones the bundle's cells really mark, whatever the file declared",
      np.array_equal(empty_cells("MTBS_WOODBURY_2019_dnbr"), DNBR == -32768)
      and np.array_equal(empty_cells("MTBS_WOODBURY_2019_rdnbr"), RDNBR == -32768)       # the file said -9999
      and np.array_equal(empty_cells("MTBS_WOODBURY_2019_dnbr6"), ~INSIDE)               # the file said nothing: 0
      and np.array_equal(empty_cells("BAER_JOHNSON_2_2021_sbs"), ~INSIDE)                # the file said 0: 255
      and np.array_equal(empty_cells("RAVG_CREEK_2020_ba7"), ~INSIDE)
      and np.array_equal(empty_cells("RAVG_CREEK_2020_rdnbr_cbi4"), ~INSIDE)
      and np.array_equal(empty_cells("RAVG_CREEK_2020_ba"), BASAL == -9999.0)
      and not empty_cells("BAER_JOHNSON_2_2021_barc256").any(),
      [int(empty_cells(n).sum()) for n in ("MTBS_WOODBURY_2019_dnbr", "MTBS_WOODBURY_2019_rdnbr",
                                           "MTBS_WOODBURY_2019_dnbr6", "BAER_JOHNSON_2_2021_sbs",
                                           "RAVG_CREEK_2020_ba7", "RAVG_CREEK_2020_rdnbr_cbi4")])
ras = arcpy.Raster(out("MTBS_WOODBURY_2019_dnbr"))
good = DNBR[DNBR != -32768].astype(float)
# (ArcGIS gives the standard deviation of a sample, n - 1; either form is taken here)
check("rasters: statistics come with the raster, and are those of its real values",
      ras.minimum == good.min() and ras.maximum == good.max() and close(ras.mean, good.mean(), 1e-6)
      and (close(ras.standardDeviation, good.std(ddof=1), 1e-6) or close(ras.standardDeviation, good.std(), 1e-6)),
      (ras.minimum, ras.maximum, ras.mean, ras.standardDeviation))
ras = arcpy.Raster(out("MTBS_WOODBURY_2019_rdnbr"))
good = RDNBR[RDNBR != -32768].astype(float)
check("rasters: the statistics leave out the fill value that the file did not declare",
      ras.minimum == good.min() and ras.maximum == good.max() and close(ras.mean, good.mean(), 1e-6),
      (ras.minimum, ras.maximum, ras.mean, good.min(), good.max(), good.mean()))
md = arcpy.metadata.Metadata(out("MTBS_WOODBURY_2019_dnbr6"))
check("rasters: each says in its metadata what it is and where it came from",
      (md.summary or "").startswith("MTBS thematic burn severity, 6 classes of the WOODBURY fire of 2019 (fire ID %s), "
                                    "loaded on %s" % (MT, datetime.date.today().strftime("%Y-%m-%d")))
      and "attribute table names each class" in (md.description or ""), md.summary)

# the class tables
t = table_of("MTBS_WOODBURY_2019_dnbr6")
want = dict((c["value"], c["name"]) for c in B.MTBS_CLASSES)
check("class table: a row for each class in the raster, with the program's name for it, and none for 0",
      sorted(t) == [1, 2, 3, 4, 5, 6] and all(t[v][1] == want[v] for v in t), dict((v, t[v][1]) for v in t))
check("class table: the count, hectares and acres of each class (a cell is 0.09 ha)",
      all(t[v][0] == n and close(t[v][2], n * HA) and close(t[v][3], n * ACRE) for v, n in DNBR6_COUNTS),
      dict((v, t[v][:1] + t[v][2:4]) for v in t))
check("class table: the percentages are of the 110 cells assessed, and the mask class has none",
      all(close(t[v][4], 100.0 * n / 110.0) for v, n in DNBR6_COUNTS if v != 6) and t[6][4] is None,
      dict((v, t[v][4]) for v in t))
check("class table: the percentages add up to 100", close(sum(t[v][4] for v in t if t[v][4] is not None), 100.0, 1e-9))
t = table_of("RAVG_CREEK_2020_ba7")
check("class table (RAVG, 7 classes): counts, names and percentages of the 110 cells not masked",
      sorted(t) == [1, 2, 3, 4, 5, 6, 7, 9] and all(t[v][0] == n for v, n in BA7_COUNTS)
      and all(close(t[v][4], 100.0 * n / 110.0) for v, n in BA7_COUNTS if v != 9) and t[9][4] is None
      and t[9][1] == "Masked" and t[7][1] == "90% to 100%", t)
t = table_of("RAVG_CREEK_2020_rdnbr_cbi4")
check("class table (RAVG, 4 classes, product named with two words): counts and percentages of all 120 cells",
      sorted(t) == [1, 2, 3, 4] and all(t[v][0] == n and close(t[v][4], 100.0 * n / 120.0) for v, n in CBI4_COUNTS)
      and t[4][1] == "High (CBI 2.25 to 3.0)", t)
t = table_of("BAER_JOHNSON_2_2021_sbs")
check("class table (BAER): the 255 outside the fire is not a class; percentages are of the 115 cells not masked",
      sorted(t) == [1, 2, 3, 4, 5] and all(t[v][0] == n for v, n in SBS_COUNTS)
      and all(close(t[v][4], 100.0 * n / 115.0) for v, n in SBS_COUNTS if v != 5) and t[5][4] is None, t)
check("class table: a raster of continuous values has none",
      "Class_Name" not in [f.name for f in arcpy.ListFields(out("MTBS_WOODBURY_2019_dnbr"))])

# the summary table
rows = summary_rows("dnbr6", "WOODBURY")
by = dict((x["class_value"], x) for x in rows)
check("summary: a row for each class of a class raster",
      sorted(by) == [1, 2, 3, 4, 5, 6] and all(by[v]["class_name"] == want[v] and by[v]["cells"] == n
                                                and close(by[v]["hectares"], n * HA) and close(by[v]["acres"], n * ACRE)
                                                for v, n in DNBR6_COUNTS), rows[:2])
check("summary: the same percentages as the raster's own table",
      all(close(by[v]["percent"], 100.0 * n / 110.0) for v, n in DNBR6_COUNTS if v != 6) and by[6]["percent"] is None,
      dict((v, by[v]["percent"]) for v in by))
check("summary: each row says which fire, program, year and raster it is about",
      all((x["program"], x["fire_id"], x["fire_year"], x["kind"], x["raster"]) ==
          ("MTBS", MT, 2019, "classified", out("MTBS_WOODBURY_2019_dnbr6")) and x["mean"] is None for x in rows), rows[:1])
rows = summary_rows("dnbr", "WOODBURY")
inside = DNBR[INSIDE & (DNBR != -32768)].astype(float)
check("summary: one row for a continuous raster, of the burned area only, without the missing cells (117 cells)",
      len(rows) == 1 and rows[0]["cells"] == 117 == inside.size and rows[0]["kind"] == "continuous"
      and rows[0]["class_value"] is None and rows[0]["percent"] is None, rows)
check("summary: its mean, standard deviation, least, median and greatest value",
      len(rows) == 1 and close(rows[0]["mean"], inside.mean()) and close(rows[0]["std_dev"], inside.std())
      and rows[0]["minimum"] == inside.min() and rows[0]["median"] == float(np.median(inside))
      and rows[0]["maximum"] == inside.max() and close(rows[0]["hectares"], 117 * HA)
      and close(rows[0]["acres"], 117 * ACRE), rows)
rows = summary_rows("rdnbr", "WOODBURY")
inside = RDNBR[INSIDE & (RDNBR != -32768)].astype(float)
check("summary: a fill value the file did not declare is left out (118 cells)",
      len(rows) == 1 and rows[0]["cells"] == 118 and close(rows[0]["mean"], inside.mean())
      and rows[0]["minimum"] == inside.min(), rows)
rows = summary_rows("ba", "CREEK")
inside = BASAL[INSIDE & (BASAL != -9999.0)].astype(float)
check("summary: a raster of decimals (116 cells)",
      len(rows) == 1 and rows[0]["cells"] == 116 and close(rows[0]["mean"], inside.mean(), 1e-6)
      and close(rows[0]["maximum"], inside.max(), 1e-6) and close(rows[0]["median"], float(np.median(inside)), 1e-6),
      rows)
rows = summary_rows("barc256", "JOHNSON 2")
inside = BARC256[INSIDE].astype(float)
check("summary: BARC of 256 values is summarized as continuous, over the burned area (120 cells)",
      len(rows) == 1 and rows[0]["kind"] == "continuous" and rows[0]["cells"] == 120
      and close(rows[0]["mean"], inside.mean()), rows)
check("summary: the images of several bands have no rows", summary_rows("pre_20190705_l8_refl", "WOODBURY") == [])
n_rows = int(arcpy.management.GetCount(summary)[0])
check("summary: 6 + 1 + 1 rows for MTBS, 1 + 8 + 4 for RAVG, 1 + 5 for BAER", n_rows == 27 == len(r["rows"]), n_rows)

# the perimeters
flds = ["program", "fire_name", "fire_id", "fire_year", "ignition_date", "fire_type", "assessment", "acres",
        "dnbr_offset", "dnbr_stddev", "greenness_t", "low_t", "moderate_t", "high_t", "pre_scene", "post_scene",
        "comment", "map_id"]
got = dict((row[1], dict(zip(flds, row[:-1]) , shape=row[-1])) for row in arcpy.da.SearchCursor(perims, flds + ["SHAPE@"]))
w = got.get("WOODBURY", {})
check("perimeters: one row for each fire", sorted(got) == ["CREEK", "JOHNSON 2", "WOODBURY"], sorted(got))
check("perimeters: what the bundle says of the fire",
      [w.get(k) for k in flds[:8]] == ["MTBS", "WOODBURY", MT, 2019, "2019-06-08", "Wildfire", "Extended", 26.7],
      [w.get(k) for k in flds[:8]])
check("perimeters: the thresholds the analyst chose; one that was not used (-9999) is left empty",
      [w.get(k) for k in ("dnbr_offset", "dnbr_stddev", "greenness_t", "low_t", "moderate_t", "high_t")]
      == [-12, 9, None, 70, 280, 520], [w.get(k) for k in flds[8:14]])
check("perimeters: the two images and the map number",
      [w.get(k) for k in ("pre_scene", "post_scene", "map_id")] == ["804003620190705", "804003620200707", 10016328],
      [w.get(k) for k in flds[14:]])
check("perimeters: a comment the bundle left blank is empty here (not the one space a shapefile gives back)",
      w.get("comment") is None, repr(w.get("comment")))
check("perimeters: in latitude and longitude (WGS 84)", arcpy.Describe(perims).spatialReference.factoryCode == 4326)
areas = dict((k, v["shape"].getArea("GEODESIC", "SQUAREMETERS")) for k, v in got.items())
check("perimeters: each outline is still its 120 cells (108,000 sq m, within 0.2 percent)",
      all(abs(a - 120 * CELL * CELL) < 0.002 * 120 * CELL * CELL for a in areas.values()), areas)
utm = w["shape"].projectAs(arcpy.SpatialReference(26912)) if w else None
check("perimeters: and lies where the bundle put it (within 2 m)",
      utm is not None and abs(utm.extent.XMin - PERIM[0][0]) < 2.0 and abs(utm.extent.YMax - PERIM[1][1]) < 2.0,
      utm and (utm.extent.XMin - PERIM[0][0], utm.extent.YMax - PERIM[1][1]))
md = arcpy.metadata.Metadata(perims)
check("perimeters: the metadata says what they are", "the perimeters of 3 fires" in (md.summary or ""), md.summary)
src_ds = gdal.Open(mt + "dnbr6.tif")                 # held in a name: GDAL frees a dataset nothing refers to
check("the bundle's own files are not changed", np.array_equal(src_ds.ReadAsArray(), DNBR6)
      and src_ds.GetRasterBand(1).GetColorTable() is not None)
src_ds = None

# ---- 5. the same bundle from its folder, into a folder
out_folder = os.path.join(work, "as_files")
os.makedirs(out_folder)
m = Msgs()
r2 = BB.run(bundle, out_folder, messages=m)
check("folder run: nothing is unpacked, and the rasters are GeoTIFFs",
      not m.has("Unpacked") and len(r2["rasters"]) == 10 and all(p.lower().endswith(".tif") and os.path.isfile(p)
                                                                 for p in r2["rasters"])
      and not r2["perimeters"] and not r2["summary"], (r2["perimeters"], r2["summary"], r2["rasters"][:1]))
t_ds = gdal.Open(os.path.join(out_folder, "MTBS_WOODBURY_2019_rdnbr.tif"))
check("folder run: a GeoTIFF declares the 'no data' value its cells really use (-32768, where the bundle said -9999)",
      t_ds.GetRasterBand(1).GetNoDataValue() == -32768.0 and np.array_equal(t_ds.ReadAsArray(), RDNBR),
      t_ds.GetRasterBand(1).GetNoDataValue())
t_ds = None
tif = os.path.join(out_folder, "MTBS_WOODBURY_2019_dnbr6.tif")
t2 = dict((int(x[0]), x[1:]) for x in arcpy.da.SearchCursor(tif, ["Value", "Count", "Class_Name", "Percent"]))
check("folder run: the class table is there too; a file's table cannot be empty, so the mask's percent is -999",
      sorted(t2) == [1, 2, 3, 4, 5, 6] and t2[1][1] == "Unburned to Low" and close(t2[1][2], 100.0 * 40 / 110.0)
      and t2[6][2] == -999.0, t2)
check("folder run: the summary rows are returned even when no table is asked for", len(r2["rows"]) == 27, len(r2["rows"]))
check("folder run: the bundle's folder is left as it was",
      sorted(os.listdir(bundle)) == ["baer", "mtbs", "ravg"] and not os.path.isdir(os.path.join(out_folder, BB.FOLDER)))

# a second run into the same place replaces what is there
r3 = BB.run(bundle, gdb, out_perimeters=perims, out_summary=summary, messages=Msgs())
check("second run: the outputs are replaced, not added to",
      int(arcpy.management.GetCount(summary)[0]) == 27 and int(arcpy.management.GetCount(perims)[0]) == 3
      and sorted(os.path.basename(p) for p in r3["rasters"]) == names)

# ---- 6. what is refused
raises("refused: an output place that is not there", lambda: BB.run(bundle, os.path.join(work, "no_such_folder")),
       "does not exist")
raises("refused: the memory workspace", lambda: BB.run(bundle, "memory"), "cannot be written to the memory workspace")
raises("refused: no output place", lambda: BB.run(bundle, ""), "Give a folder or a file geodatabase for the output")
raises("refused: perimeters outside a geodatabase",
       lambda: BB.run(bundle, gdb, out_perimeters=os.path.join(out_folder, "p.shp")),
       "Put the output perimeters in a file geodatabase.")
raises("refused: a summary table outside a geodatabase",
       lambda: BB.run(bundle, gdb, out_summary=os.path.join(out_folder, "s.dbf")),
       "Put the output summary table in a file geodatabase.")
empty = os.path.join(work, "empty_bundle")
os.makedirs(os.path.join(empty, "notes"))
open(os.path.join(empty, "notes", "readme.txt"), "w").write("nothing")
raises("refused: a folder with no bundle files, naming what it does hold", lambda: BB.run(empty, gdb),
       "The folder does hold files in another form: notes (1 .txt)")

# ---- 7. a bundle with a part this tool cannot read, and a class raster of a kind it does not know
mixed = os.path.join(work, "mixed")
shutil.copytree(mt_dir, os.path.join(mixed, "mtbs", MT.lower()))
os.makedirs(os.path.join(mixed, "extras", "maps"))
for nm in ("map1.pdf", "map2.pdf", "notes.docx"):
    open(os.path.join(mixed, "extras", "maps", nm), "w").write("x")
write_raster(os.path.join(mixed, "mtbs", MT.lower(), "mtbs_%s_10016328_20190705_20200707_zones.tif" % MT.lower()),
             classes_inside([(1, 60), (2, 60)], 0), 26912)
gdb2 = arcpy.management.CreateFileGDB(work, "mixed.gdb")[0]
m = Msgs()
r4 = BB.run(mixed, gdb2, out_summary=os.path.join(gdb2, "s"), messages=m)
check("unread: the run says how many fires it reads, and warns of the folder it could not use",
      m.has("The bundle holds 1 fire that this tool reads.")
      and m.warned("1 folder of the bundle was NOT LOADED") and m.warned("maps (1 .docx, 2 .pdf)".replace("maps", os.path.join("extras", "maps"))),
      (m.lines[:1], m.warnings))
check("unknown class raster: loaded, summarized by value without names, and warned of",
      m.warned("1 classified raster is of a kind this tool does not know")
      and sorted((x[6], x[7], x[8]) for x in r4["rows"] if x[4] == "zones")
      == [(1, "(no name known for value 1)", 60), (2, "(no name known for value 2)", 60)],
      (m.warnings, [x[6:9] for x in r4["rows"] if x[4] == "zones"]))

# ---- 7b. "no data" in two more forms
edge = os.path.join(work, "edge")
EV = "UT3812311190320220701"
edge_dir = os.path.join(edge, "baer", EV.lower())
os.makedirs(edge_dir)
ed = os.path.join(edge_dir, "baer_%s_4001_20220620_20220712_" % EV.lower())
BOTH = RAMP.astype(np.int16).copy()                 # declares -9999 and holds it; holds -32768 as well
BOTH[~INSIDE] = -32768
BOTH[3, 4] = BOTH[4, 4] = -9999
BOTH[5, 5] = -32768
write_raster(ed + "dnbr.tif", BOTH, 26912, nodata=-9999)
SBS2_COUNTS = [(1, 50), (2, 40), (3, 20), (4, 5), (5, 5)]
write_raster(ed + "sbs.tif", classes_inside(SBS2_COUNTS, 255), 26912, colors=True)       # 255 outside, nothing declared
write_perimeter(ed + "burn_bndy.shp", 26912, {"Event_ID": EV, "Incid_Name": "EDGE", "Map_Prog": "BAER",
                                              "Ig_Date": datetime.datetime(2022, 7, 1)})
gdb_e = arcpy.management.CreateFileGDB(work, "edge.gdb")[0]
m = Msgs()
r7 = BB.run(edge, gdb_e, messages=m)
rows = [x for x in r7["rows"] if x[4] == "dnbr"]
inside = BOTH[INSIDE & (BOTH != -32768) & (BOTH != -9999)].astype(float)
check("no data, both forms: a raster that holds the value it declares AND an undeclared -32768 is summarized "
      "without either (117 cells)",
      len(rows) == 1 and rows[0][8] == 117 == inside.size and close(rows[0][12], inside.mean())
      and rows[0][14] == inside.min(), rows and (rows[0][8], rows[0][12], rows[0][14], inside.mean(), inside.min()))
rows = [x for x in r7["rows"] if x[4] == "sbs"]
check("no data, 255 undeclared: a soil burn severity raster filled with 255 outside the fire, with no value "
      "declared, has no class 255",
      sorted((x[6], x[8]) for x in rows) == SBS2_COUNTS
      and all(close(x[11], 100.0 * x[8] / 115.0) for x in rows if x[6] != 5), [(x[6], x[7], x[8], x[11]) for x in rows])

e_dnbr = arcpy.RasterToNumPyArray(os.path.join(gdb_e, "BAER_EDGE_2022_dnbr"), nodata_to_value=77) \
    != arcpy.RasterToNumPyArray(os.path.join(gdb_e, "BAER_EDGE_2022_dnbr"), nodata_to_value=78)
e_sbs = arcpy.RasterToNumPyArray(os.path.join(gdb_e, "BAER_EDGE_2022_sbs"), nodata_to_value=77) \
    != arcpy.RasterToNumPyArray(os.path.join(gdb_e, "BAER_EDGE_2022_sbs"), nodata_to_value=78)
check("no data, both forms: in the raster that is written, the cells of BOTH values are without data",
      np.array_equal(e_dnbr, (BOTH == -32768) | (BOTH == -9999)), int(e_dnbr.sum()))
check("no data, 255 undeclared: in the raster that is written, the 255 cells are without data, and its table has "
      "the five classes only",
      np.array_equal(e_sbs, ~INSIDE) and sorted(int(x[0]) for x in arcpy.da.SearchCursor(
          os.path.join(gdb_e, "BAER_EDGE_2022_sbs"), ["Value"])) == [1, 2, 3, 4, 5], int(e_sbs.sum()))
check("no data: the bundle's own files are left as they were",
      np.array_equal(gdal.Open(ed + "dnbr.tif").ReadAsArray(), BOTH))

# the rule itself, case by case (see fill_values)
u8 = lambda *v: np.array(v, np.uint8)                                          # noqa: E731
i16 = lambda *v: np.array(v, np.int16)                                         # noqa: E731
SBS = B.SBS_CLASSES
check("no-data rule, classes: nothing declared, 0 outside the fire", BB.fill_values("classified", SBS, u8(0, 1, 2), None, np) == [0])
check("no-data rule, classes: nothing declared, 255 outside the fire", BB.fill_values("classified", SBS, u8(255, 1, 2), None, np) == [255])
check("no-data rule, classes: a value declared and not used, 255 outside the fire",
      BB.fill_values("classified", SBS, u8(255, 1, 2), 0, np) == [255])
check("no-data rule, classes: the value declared is used, and nothing else",
      BB.fill_values("classified", SBS, u8(255, 1, 2), 255, np) == [255] and BB.fill_values("classified", SBS, u8(0, 1, 2), 0, np) == [0])
check("no-data rule, classes: 0 and 255 both mark empty cells: the declared one comes first",
      BB.fill_values("classified", SBS, u8(0, 255, 1), 255, np) == [255, 0]
      and BB.fill_values("classified", SBS, u8(0, 255, 1), None, np) == [0, 255])
check("no-data rule, classes: no empty cells at all", BB.fill_values("classified", SBS, u8(1, 2, 3), None, np) == []
      and BB.fill_values("classified", SBS, u8(1, 2, 3), 0, np) == [])
check("no-data rule, classes of a kind not known: only 0, and only when nothing is declared (255 may be a class)",
      BB.fill_values("classified", None, u8(0, 255, 7), None, np) == [0]
      and BB.fill_values("classified", None, u8(0, 255, 7), 9, np) == []
      and BB.fill_values("classified", None, u8(0, 255, 7), 255, np) == [255])
check("no-data rule, continuous: -32768 that is not declared", BB.fill_values("continuous", None, i16(-32768, 5, 9), -9999, np) == [-32768]
      and BB.fill_values("continuous", None, i16(-32768, 5, 9), None, np) == [-32768])
check("no-data rule, continuous: the declared value and -32768 both: the declared one comes first",
      BB.fill_values("continuous", None, i16(-32768, -9999, 9), -9999, np) == [-9999, -32768])
check("no-data rule, continuous: nothing to do when the file is right, and 0 and 255 are values like any other",
      BB.fill_values("continuous", None, i16(-32768, 5, 9), -32768, np) == [-32768]
      and BB.fill_values("continuous", None, i16(0, 255, 9), None, np) == []
      and BB.fill_values("continuous", None, np.array([0.5, -32768.0], np.float32), None, np) == [])

# ---- 8. the portal's older form: Imagine rasters in a folder named for the fire, some in a tar, no perimeter
OLD = "AZ3364611101320110529"
old_bundle = os.path.join(work, "old_bundle")
old_dir = os.path.join(old_bundle, "baer", OLD.lower())
os.makedirs(old_dir)
BARC4 = np.full((ROWS, COLS), 1, np.uint8)                                 # the land outside is in the lowest class
BARC4[INSIDE] = np.concatenate([np.full(n, v, np.uint8) for v, n in [(1, 30), (2, 40), (3, 30), (4, 20)]])
write_raster(os.path.join(old_dir, "wallow_barc4_utm.img"), BARC4, 32612, driver="HFA")
write_raster(os.path.join(old_dir, "wallow_dnbr_utm.img"), RAMP.astype(np.int16), 32612, driver="HFA")
write_raster(os.path.join(old_dir, "wallow_20110605_post_utm.img"), IMAGE, 32612, driver="HFA")
tar_src = os.path.join(work, "wallow_barc256_utm.img")
write_raster(tar_src, BARC256, 32612, driver="HFA")
with tarfile.open(os.path.join(old_dir, "wallow_more.tar"), "w") as tf:
    tf.add(tar_src, arcname="deep/inside/wallow_barc256_utm.img")
    info = tarfile.TarInfo("../outside_by_the_bundle_test.txt")
    info.size = 0
    tf.addfile(info)

found = {}
added = BB.find_older(old_bundle, found)
key = ("BAER", OLD, "")
check("older form: the fire is known by the folder's name",
      added == [key] and found[key].get("older") is True and found[key]["perimeter"] is None, added)
check("older form: every raster is taken, named for its product; an image keeps its date; the tar is opened",
      sorted(found.get(key, {"rasters": {}})["rasters"]) == ["barc256", "barc4", "dnbr", "post_20110605"],
      sorted(found.get(key, {"rasters": {}})["rasters"]))
check("older form: a tar's files are unpacked into one folder beside it, each by its own name: an entry that "
      "points outside (../) lands inside that folder like the rest, and nothing is written anywhere else",
      sorted(os.listdir(os.path.join(old_dir, "wallow_more_unpacked")))
      == ["outside_by_the_bundle_test.txt", "wallow_barc256_utm.img"]
      and not os.path.exists(os.path.join(old_dir, "outside_by_the_bundle_test.txt"))
      and not os.path.exists(os.path.join(old_bundle, "baer", "outside_by_the_bundle_test.txt")),
      (os.listdir(old_dir), os.listdir(os.path.join(old_dir, "wallow_more_unpacked"))))
check("older form: its folders are not reported as unread", BB.find_unread(old_bundle, found) == [],
      BB.find_unread(old_bundle, found))

# the catalog's answer: the same outline, in latitude and longitude
sr_utm = arcpy.SpatialReference(32612)
outline = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in PERIM + PERIM[:1]]), sr_utm).projectAs(
    arcpy.SpatialReference(4326))
ring = [[p.X, p.Y] for p in outline.getPart(0)]
asked = []


def catalog(program, cancelled=None, with_geometry=True, limit=None, **criteria):
    asked.append((program, criteria))
    return [{"type": "Feature",
             "properties": {"event_id": OLD, "incid_name": "WALLOW", "incid_type": "Wildfire", "map_prog": "BAER",
                            "asmt_type": "Emergency", "ig_date": "2011-05-29T00:00:00Z", "burnbndac": 26.7,
                            "high_t": 111, "mod_t": 222, "low_t": 50},
             "geometry": {"type": "Polygon", "coordinates": [ring]}}]


B.search = catalog
gdb3 = arcpy.management.CreateFileGDB(work, "old.gdb")[0]
p3, s3 = os.path.join(gdb3, "perims"), os.path.join(gdb3, "summary")
m = Msgs()
r5 = BB.run(old_bundle, gdb3, out_perimeters=p3, out_summary=s3, messages=m)
check("older form: the catalog is asked once, for this fire only",
      asked == [("BAER", {"fire_ids": [OLD]})], asked)
check("older form: the run explains the older form, and names the fire from the catalog",
      m.has("1 of the fire came in the portal's older form") and m.has("BAER WALLOW 2011 (%s, older form): "
                                                                      "barc256, barc4, dnbr, post_20110605." % OLD),
      m.lines[:4])
check("older form: four rasters are loaded",
      sorted(os.path.basename(p) for p in r5["rasters"]) == ["BAER_WALLOW_2011_barc256", "BAER_WALLOW_2011_barc4",
                                                             "BAER_WALLOW_2011_dnbr", "BAER_WALLOW_2011_post_20110605"],
      r5["rasters"])
rows = [x for x in r5["rows"] if x[4] == "barc4"]
check("older form: the classes are counted inside the perimeter only (30, 40, 30 and 20 cells of 120)",
      sorted((x[6], x[8]) for x in rows) == [(1, 30), (2, 40), (3, 30), (4, 20)]
      and all(close(x[11], 100.0 * x[8] / 120.0) for x in rows), [(x[6], x[8], x[11]) for x in rows])
rows = [x for x in r5["rows"] if x[4] == "dnbr"]
inside = RAMP[INSIDE].astype(float)
check("older form: a continuous raster is summarized inside the perimeter (120 cells)",
      len(rows) == 1 and rows[0][8] == 120 and close(rows[0][12], inside.mean()), rows)
got = [row for row in arcpy.da.SearchCursor(p3, ["program", "fire_name", "fire_id", "fire_year", "ignition_date",
                                                 "assessment", "moderate_t", "high_t", "low_t", "SHAPE@"])]
check("older form: the perimeter and what is known of the fire come from the catalog",
      len(got) == 1 and got[0][:6] == ("BAER", "WALLOW", OLD, 2011, "2011-05-29", "Emergency")
      and abs(got[0][-1].getArea("GEODESIC", "SQUAREMETERS") - 108000.0) < 216.0, got and got[0][:9])
md = arcpy.metadata.Metadata(p3)
check("older form: the perimeters' metadata says where that one came from",
      "1 of the perimeters is of fires whose bundle came in the portal's older form" in (md.description or ""),
      (md.description or "")[:300])


# the catalog cannot be reached: the rasters are still loaded, and the run says what is missing
def no_service(program, **_k):
    raise BB.BundleError("The fire catalog did not answer.")


B.search = no_service
m = Msgs()
r6 = BB.run(old_bundle, gdb3, out_perimeters=os.path.join(gdb3, "perims2"), out_summary=os.path.join(gdb3, "summary2"),
            messages=m)
check("older form, no catalog: warned, with the reason",
      m.warned("The fire catalog could not be read (The fire catalog did not answer), so the fires in the older form "
               "have no perimeter and no name here"), m.warnings)
check("older form, no catalog: the rasters are loaded under the fire's ID, and no perimeters are written",
      len(r6["rasters"]) == 4 and r6["perimeters"] == "" and not arcpy.Exists(os.path.join(gdb3, "perims2"))
      and all(os.path.basename(p).startswith("BAER_%s_2011_" % OLD) for p in r6["rasters"])
      and m.warned("The bundle holds no perimeter shapefile, so no perimeters were written."),
      (r6["rasters"], r6["perimeters"], m.warnings))
rows = [x for x in r6["rows"] if x[4] == "dnbr"]
check("older form, no catalog: a continuous raster is then summarized over its whole rectangle (320 cells)",
      len(rows) == 1 and rows[0][8] == ROWS * COLS and close(rows[0][12], RAMP.mean()), rows)
B.search = no_catalog

print("\n%d passed, %d failed%s" % (passed, failed, " (and %d known faults not yet fixed)" % known if known else ""))
B.search = REAL_SEARCH
arcpy.management.ClearWorkspaceCache()
if os.environ.get("KEEP_TEST_DATA"):
    print("kept:", work)
else:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
