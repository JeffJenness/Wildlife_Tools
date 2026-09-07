import json
import os
import sys
import tempfile

import numpy as np
import arcpy

sys.path.insert(0, r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife"
                   r"\Wildlife_Tools\WildlifeTools\Toolboxes")
import kernel_density as kd

TMP = tempfile.mkdtemp(prefix="kd_symb_")
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
arcpy.env.overwriteOutput = True
SR = arcpy.SpatialReference(32612)


class Msgs:
    def addMessage(self, t):
        print("   msg:", t)

    def addWarningMessage(self, t):
        print("   WARN:", t)


# ---- a small continuous raster --------------------------------------
arr = np.random.default_rng(3).random((40, 40)).astype("float32")
ras = arcpy.NumPyArrayToRaster(arr, arcpy.Point(500000, 3800000), 30, 30)
ras_path = os.path.join(GDB, "kd_test")
ras.save(ras_path)
arcpy.management.DefineProjection(ras_path, SR)

lyrx = kd.kd_density_lyrx(ras_path, Msgs())
assert lyrx and os.path.exists(lyrx), "density lyrx not created"
doc = json.load(open(lyrx, encoding="utf-8"))
ld = doc["layerDefinitions"][0]
col = ld["colorizer"]
print("density lyrx:", os.path.basename(lyrx))
print("  transparency:", ld.get("transparency"))
print("  colorizer:", col.get("type"), "| stretch:", col.get("stretchType"))
print("  ramp segments:",
      len(col.get("colorRamp", {}).get("colorRamps", [])))
print("  has stretchStats for THIS raster:",
      "stretchStats" in col)
assert ld.get("transparency") == 30
assert len(col["colorRamp"]["colorRamps"]) == 255

# ---- a small contour-like FC with the Primary field ------------------
for shp, nm in [("POLYLINE", "ctr_lines"), ("POLYGON", "ctr_areas")]:
    fc = arcpy.management.CreateFeatureclass(
        GDB, nm, shp, spatial_reference=SR).getOutput(0)
    arcpy.management.AddField(fc, "Primary", "TEXT", field_length=5)
    with arcpy.da.InsertCursor(fc, ["SHAPE@", "Primary"]) as ins:
        pts = arcpy.Array([arcpy.Point(500000, 3800000),
                           arcpy.Point(500300, 3800300),
                           arcpy.Point(500600, 3800000),
                           arcpy.Point(500000, 3800000)])
        geom = (arcpy.Polygon(pts, SR) if shp == "POLYGON"
                else arcpy.Polyline(pts, SR))
        ins.insertRow([geom, "True"])
        ins.insertRow([geom, "False"])
    clyrx = kd.kd_contour_lyrx(fc, Msgs())
    assert clyrx and os.path.exists(clyrx), nm + " lyrx not created"
    cdoc = json.load(open(clyrx, encoding="utf-8"))
    cld = cdoc["layerDefinitions"][0]
    ren = cld["renderer"]
    classes = ren["groups"][0]["classes"]
    stypes = [c["symbol"]["symbol"]["type"] for c in classes]
    widths = [c["symbol"]["symbol"]["symbolLayers"][0]["width"]
              for c in classes]
    print(nm, "lyrx:", ren["type"], "on", ren["fields"],
          "| symbols:", stypes, "| widths:", widths)
    assert ren["type"] == "CIMUniqueValueRenderer"
    assert ren["fields"] == ["Primary"]
    want = "CIMPolygonSymbol" if shp == "POLYGON" else "CIMLineSymbol"
    assert all(s == want for s in stypes)
    assert widths == [1.5, 0.6]

print()
print("ALL SYMBOLOGY CHECKS PASSED")
