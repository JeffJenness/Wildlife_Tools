# -*- coding: utf-8 -*-
"""Regression tests for the Corridor Designer / Ecological Analysis review
of 2026-10-04. Run with Pro's Python:  propy corridor_review_test.py

1. Identify Termini Polygons imports its projection helper (the import line
   had been pasted inside the module docstring -> NameError whenever the
   Wildland Blocks and the raster differed in coordinate system).
2. A layer's DEFINITION QUERY survives a path-based read: Cumulative Surface
   and the Mahalanobis "all points, ignore the selection" read.
3. An output inside a FEATURE DATASET is recognized as a geodatabase output.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "WildlifeTools", "Toolboxes"))
import arcpy                     # noqa: E402
import mahalanobis as mh         # noqa: E402
import cumulative_surface as cs  # noqa: E402
import land_facet_termini as lt  # noqa: E402

n_pass = n_fail = 0


def check(name, ok, detail=""):
    global n_pass, n_fail
    if ok:
        n_pass += 1
    else:
        n_fail += 1
    print("{}  {}{}".format("PASS" if ok else "FAIL", name,
                            ("  -- " + str(detail)) if detail and not ok else ""))


class Msgs:
    def addMessage(self, s): pass
    def addWarningMessage(self, s): pass


arcpy.env.overwriteOutput = True
sr = arcpy.SpatialReference(26912)

# ---- 1. the termini module really has its projection helper
check("termini: clip_tools imported as _ct", hasattr(lt, "_ct")
      and hasattr(lt._ct, "project_geometry"))
check("termini: docstring no longer holds the stray import",
      "import clip_tools" not in (lt.__doc__ or ""))

# ---- 2. definition queries
fc = arcpy.management.CreateFeatureclass("memory", "crt_polys", "POLYGON",
                                         spatial_reference=sr)[0]
arcpy.management.AddField(fc, "Kind", "TEXT", field_length=10)
with arcpy.da.InsertCursor(fc, ["Kind", "SHAPE@"]) as c:
    for i, k in enumerate(["keep", "keep", "drop", "drop", "drop"]):
        x = 400000 + i * 1000
        c.insertRow((k, arcpy.Polygon(arcpy.Array(
            [arcpy.Point(x, 3800000), arcpy.Point(x, 3800500),
             arcpy.Point(x + 500, 3800500), arcpy.Point(x + 500, 3800000)]), sr)))
lyr = arcpy.management.MakeFeatureLayer(fc, "crt_lyr", "Kind = 'keep'")[0]
path = arcpy.Describe(lyr).catalogPath

check("definition_query: read from a layer",
      (mh.definition_query(lyr) or "").replace(" ", "") == "Kind='keep'",
      mh.definition_query(lyr))
check("definition_query: None for a bare path", mh.definition_query(path) is None)

geoms, n_total, n_sel, used = cs._polygon_geoms(
    lyr, path, "crt_lyr", True, sr, 10.0, Msgs(),
    pin=mh.pin_selection(lyr), dq=mh.definition_query(lyr))
check("Cumulative Surface: definition query honored (2 of 5)",
      len(geoms) == 2 and n_total == 2, (len(geoms), n_total))

arcpy.management.SelectLayerByAttribute(lyr, "NEW_SELECTION", "OBJECTID = 1")
geoms, n_total, n_sel, used = cs._polygon_geoms(
    lyr, path, "crt_lyr", True, sr, 10.0, Msgs(),
    pin=mh.pin_selection(lyr), dq=mh.definition_query(lyr))
check("Cumulative Surface: selection inside a definition query (1)",
      len(geoms) == 1 and used, (len(geoms), used))

geoms, *_ = cs._polygon_geoms(path, path, "crt_path", True, sr, 10.0, Msgs(),
                              pin=None, dq=None)
check("Cumulative Surface: bare path reads everything (5)", len(geoms) == 5,
      len(geoms))

# Mahalanobis: "use only the selected points" unchecked
pts = arcpy.management.CreateFeatureclass("memory", "crt_pts", "POINT",
                                          spatial_reference=sr)[0]
arcpy.management.AddField(pts, "Kind", "TEXT", field_length=10)
with arcpy.da.InsertCursor(pts, ["Kind", "SHAPE@XY"]) as c:
    for i, k in enumerate(["keep"] * 3 + ["drop"] * 4):
        c.insertRow((k, (400000 + i * 10, 3800000)))
plyr = arcpy.management.MakeFeatureLayer(pts, "crt_pts_lyr", "Kind = 'keep'")[0]
arcpy.management.SelectLayerByAttribute(plyr, "NEW_SELECTION", "OBJECTID = 1")
src, tmp = mh.without_selection(plyr, "crt_all_points")
xy = mh.read_point_xy(src, sr)
check("without_selection: selection ignored, definition query kept (3 of 7)",
      len(xy) == 3, len(xy))
check("without_selection: a scratch layer carries the query", tmp is not None)
if tmp:
    arcpy.management.Delete(tmp)
arcpy.management.SelectLayerByAttribute(plyr, "CLEAR_SELECTION")
plain = arcpy.management.MakeFeatureLayer(pts, "crt_pts_plain")[0]
arcpy.management.SelectLayerByAttribute(plain, "NEW_SELECTION", "OBJECTID = 1")
src, tmp = mh.without_selection(plain, "crt_all_points")
check("without_selection: no query -> the catalog path, all 7 points",
      tmp is None and len(mh.read_point_xy(src, sr)) == 7)
src, tmp = mh.without_selection(pts, "crt_all_points")
check("without_selection: a bare path passes through", tmp is None)

# ---- 3. feature datasets are inside a geodatabase
check("in_gdb_container: feature dataset",
      mh.in_gdb_container(r"D:\x\Data.gdb\FeatureDS"))
check("in_gdb_container: plain folder is not",
      not mh.in_gdb_container(r"D:\x\folder"))
import inspect                                              # noqa: E402
import patch_analysis                                       # noqa: E402
import bottleneck_analysis                                  # noqa: E402
check("Patch Analysis tests the output with in_gdb_container",
      "not _mh.in_gdb_container(out_ws)" in inspect.getsource(patch_analysis))
check("Bottleneck Analysis tests the output with in_gdb_container",
      "not _mh.in_gdb_container(ws)" in inspect.getsource(bottleneck_analysis))
check("Cumulative Surface grid builder imports arcpy",
      "import arcpy" in inspect.getsource(cs._build_grid))

print("\n{} passed, {} failed".format(n_pass, n_fail))
sys.exit(1 if n_fail else 0)
