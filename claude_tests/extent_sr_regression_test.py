# -*- coding: utf-8 -*-
"""Repro + fix test for the extent-loses-its-CS crash (Jeff,
2026-09-02, Current Display Extent button): parse the exact
parameter text from his failed run, then run the very same design
end-to-end on it."""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife"
                   r"\Wildlife_Tools\WildlifeTools\Toolboxes")
import arcpy
import voronoi_tools as VT
import random_arrays as RA

WKT = ('PROJCRS["NAD_1983_UTM_Zone_12N",BASEGEOGCRS['
       '"GCS_North_American_1983",DATUM["D_North_American_1983",'
       'ELLIPSOID["GRS_1980",6378137.0,298.257222101]],'
       'PRIMEM["Greenwich",0.0],CS[ellipsoidal,2],'
       'AXIS["Latitude (lat)",north,ORDER[1]],'
       'AXIS["Longitude (lon)",east,ORDER[2]],'
       'ANGLEUNIT["Degree",0.0174532925199433]],'
       'CONVERSION["Transverse_Mercator",METHOD["Transverse_Mercator"],'
       'PARAMETER["False_Easting",500000.0],'
       'PARAMETER["False_Northing",0.0],'
       'PARAMETER["Central_Meridian",-111.0],'
       'PARAMETER["Scale_Factor",0.9996],'
       'PARAMETER["Latitude_Of_Origin",0.0]],CS[Cartesian,2],'
       'AXIS["Easting (X)",east,ORDER[1]],'
       'AXIS["Northing (Y)",north,ORDER[2]],'
       'LENGTHUNIT["Meter",1.0],ID["EPSG","26912"]]')
TEXT = ("602521.376877335 3614033.10173645 607957.39414937 "
        "3617540.06981705 " + WKT)

# 1. the Extent constructor accepts a spatial reference
sr = arcpy.SpatialReference(26912)
e = arcpy.Extent(0, 0, 1, 1, spatial_reference=sr)
assert e.spatialReference is not None and \
    "UTM_Zone_12N" in e.spatialReference.name
print("1. Extent(spatial_reference=...) OK")

# 2. parse_extent recovers coords AND the CS from the exact text
ext = VT.parse_extent(TEXT)
assert isinstance(ext, arcpy.Extent)
assert abs(ext.XMin - 602521.376877335) < 1e-6
assert abs(ext.YMax - 3617540.06981705) < 1e-6
assert ext.spatialReference is not None
assert "UTM_Zone_12N" in ext.spatialReference.name
print("2. parse_extent recovers the WKT CS:",
      ext.spatialReference.name)

# passthrough / null behavior
assert VT.parse_extent(None) is None
assert VT.parse_extent("DEFAULT") is None
assert VT.parse_extent(e) is e
print("3. None/DEFAULT/Extent passthrough OK")

# 4. the failed run end-to-end: extent-only, Jeff's exact design
tmp = tempfile.mkdtemp(prefix="ra_ext_")
arcpy.management.CreateFileGDB(tmp, "t.gdb")
out = os.path.join(tmp, "t.gdb", "Replicates")
RA.run_random_arrays(
    out_fc=out, boundary=None, extent=TEXT,
    strata_method="No stratification (the whole area is one stratum)",
    alloc_method="Fixed total number of arrays", n_arrays=10,
    array_shape="Grid (rows and columns of points)",
    n_rows=2, n_cols=4, point_spacing=50.0,
    orientation_mode="Random orientation for each array",
    must_fit=True, min_spacing=500.0, boundary_gap=500.0,
    linear_units="Meters", random_seed=7, messages=None)
n = int(arcpy.management.GetCount(out).getOutput(0))
d = arcpy.Describe(out)
assert n == 80, "expected 10 arrays x 8 points, got {}".format(n)
assert "UTM_Zone_12N" in d.spatialReference.name
print("4. end-to-end: {} points in 10 arrays, output CS {} -- "
      "crash fixed".format(n, d.spatialReference.name))

arcpy.management.Delete(os.path.join(tmp, "t.gdb"))
shutil.rmtree(tmp, ignore_errors=True)
