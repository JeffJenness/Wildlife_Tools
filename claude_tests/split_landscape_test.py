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

"""Split Lines into Topography Segments: the two landscape attributes
(Average Landscape Slope, Average Landscape Compass Direction) on planar
DEMs of known slope and aspect, projected and geographic, through the engine
and through the .pyt. Run with ArcGIS Pro's python."""
import math
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TBX = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
PYT = os.path.join(TBX, "JennessEnterprisesTools.pyt")
sys.path.insert(0, TBX)

import arcpy  # noqa: E402
from osgeo import gdal, osr  # noqa: E402
import line_surface_tools as lst  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  {}".format(name))
    else:
        FAIL += 1
        print("  FAIL  {}  {}".format(name, detail))


def close(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


def write_plane(path, epsg, x0, y0, cell, ncols, nrows, kx, ky):
    """A GeoTIFF whose elevation is z = kx*(x-x0) + ky*(y0-y) in map units:
    kx per map unit eastward, ky per map unit southward (rows go south)."""
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, ncols, nrows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((x0, cell, 0.0, y0, 0.0, -cell))
    srs = osr.SpatialReference(); srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    cx = x0 + (np.arange(ncols) + 0.5) * cell
    cy = y0 - (np.arange(nrows) + 0.5) * cell
    z = kx * (cx[None, :] - x0) + ky * (y0 - cy[:, None])
    ds.GetRasterBand(1).WriteArray(z.astype(np.float32))
    ds.GetRasterBand(1).SetNoDataValue(-9999.0)
    ds.FlushCache(); ds = None
    return path


TMP = tempfile.mkdtemp(prefix="split_land_")
arcpy.env.overwriteOutput = True

# ---------------------------------------------------------------- projected: two planes
SR = arcpy.SpatialReference(26912)                     # NAD83 UTM 12N, meters
X0, Y0 = 400000.0, 3900000.0
dem_e = write_plane(os.path.join(TMP, "east_up.tif"), 26912, X0, Y0, 10.0, 60, 60, 0.10, 0.0)
dem_n = write_plane(os.path.join(TMP, "north_up.tif"), 26912, X0, Y0, 10.0, 60, 60, 0.0, -0.20)
# east_up: z rises 0.1 m per m eastward  -> slope atan(0.1)=5.7106 deg, downslope WEST (270)
# north_up: z = -0.2*(y0-y) = 0.2*(y-y0): rises northward -> atan(0.2)=11.3099 deg, downslope SOUTH (180)

line_pts = [(X0 + 100, Y0 - 300), (X0 + 400, Y0 - 300)]     # due east, 300 m
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
fc = arcpy.management.CreateFeatureclass(GDB, "line", "POLYLINE", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as ic:
    ic.insertRow([arcpy.Polyline(arcpy.Array([arcpy.Point(*p) for p in line_pts]), SR)])

for name, dem_path, exp_slope, exp_asp in (("east_up", dem_e, math.degrees(math.atan(0.1)), 270.0),
                                            ("north_up", dem_n, math.degrees(math.atan(0.2)), 180.0)):
    dem = lst.load_dem(dem_path)
    with arcpy.da.SearchCursor(fc, ["SHAPE@", "OID@"]) as sc:
        shp, oid = next(sc)
    recs = lst.build_segments(shp, "DEM", dem, 1.0, SR, oid, landscape=True)
    check("{}: segments built".format(name), len(recs) == 31, len(recs))   # 300 m geodesic walk at 10 m -> 31 pieces
    sl = [r["land_slope"] for r in recs]
    asp = [r["land_aspect"] for r in recs]
    check("{}: landscape slope = {:.4f} deg on every segment".format(name, exp_slope),
          all(close(v, exp_slope, 0.01) for v in sl), sl[:3])
    check("{}: landscape aspect = {} on every segment".format(name, exp_asp),
          all(close(v, exp_asp, 0.01) for v in asp), asp[:3])
    # reporting units
    v_pct = lst.seg_value(recs[0], "land_slope", slope_unit="Percent")
    check("{}: percent conversion".format(name),
          close(v_pct, math.tan(math.radians(exp_slope)) * 100.0, 0.05), v_pct)
    # the segment's own slope on east_up is the plane's slope (line runs east); on north_up it is 0
    own = lst.seg_value(recs[0], "abs_slope")
    check("{}: own segment slope consistent".format(name),
          close(own, exp_slope if name == "east_up" else 0.0, 0.05), own)

# ---------------------------------------------------------------- flat plane -> aspect undefined
dem_f = write_plane(os.path.join(TMP, "flat.tif"), 26912, X0, Y0, 10.0, 60, 60, 0.0, 0.0)
dem = lst.load_dem(dem_f)
with arcpy.da.SearchCursor(fc, ["SHAPE@", "OID@"]) as sc:
    shp, oid = next(sc)
recs = lst.build_segments(shp, "DEM", dem, 1.0, SR, oid, landscape=True)
check("flat: slope 0", all(close(r["land_slope"], 0.0, 1e-9) for r in recs))
check("flat: aspect undefined (None)", all(r["land_aspect"] is None for r in recs))

# ---------------------------------------------------------------- geographic DEM, projected line
LAT0, LON0 = 36.0, -112.0
cell_deg = 0.0002
K = 2000.0                                  # m of elevation per DEGREE of longitude eastward (~1.3 deg slope)
dem_g = write_plane(os.path.join(TMP, "geo_east_up.tif"), 4326, LON0, LAT0, cell_deg, 80, 80, K, 0.0)
# a short east-west line in UTM 12N placed inside that window
sr_geo = arcpy.SpatialReference(4326)
lat_c = LAT0 - 40 * cell_deg
p_a = arcpy.PointGeometry(arcpy.Point(LON0 + 20 * cell_deg, lat_c), sr_geo).projectAs(SR)
p_b = arcpy.PointGeometry(arcpy.Point(LON0 + 60 * cell_deg, lat_c), sr_geo).projectAs(SR)
fc_g = arcpy.management.CreateFeatureclass(GDB, "line_g", "POLYLINE", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(fc_g, ["SHAPE@"]) as ic:
    ic.insertRow([arcpy.Polyline(arcpy.Array([p_a.firstPoint, p_b.firstPoint]), SR)])
dem = lst.load_dem(dem_g)
with arcpy.da.SearchCursor(fc_g, ["SHAPE@", "OID@"]) as sc:
    shp, oid = next(sc)
recs = lst.build_segments(shp, "DEM", dem, 1.0, SR, oid, landscape=True)
m_per_deg_lon = 111320.0 * math.cos(math.radians(lat_c))
exp_g = math.degrees(math.atan(K / m_per_deg_lon))
sl = [r["land_slope"] for r in recs]
asp = [r["land_aspect"] for r in recs]
check("geographic DEM: slope {:.5f} deg from per-row cell width".format(exp_g),
      all(close(v, exp_g, exp_g * 0.02) for v in sl), (sl[:2], exp_g))
check("geographic DEM: aspect 270", all(close(v, 270.0, 0.5) for v in asp), asp[:2])

# ---------------------------------------------------------------- through the .pyt
arcpy.ImportToolbox(PYT)
out = os.path.join(GDB, "segs")
arcpy.jenness.SplitLinesIntoSegments(
    in_lines=fc, out_fc=out, elev_source="DEM raster", in_dem=dem_e, elev_units="Meters",
    surface_attributes=[["Average Landscape Slope", "Seg_Average_Landscape_Slope"],
                        ["Average Landscape Compass Direction", "Seg_Average_Landscape_Compass_Direction"],
                        ["Bearing", "Seg_Bearing"]],
    copy_fields=[], length_unit="Meters", slope_unit="Degrees")
names = [f.name for f in arcpy.ListFields(out)]
check("pyt: both fields created", "Seg_Average_Landscape_Slope" in names
      and "Seg_Average_Landscape_Compass_Direction" in names, names)
vals = [r for r in arcpy.da.SearchCursor(out, ["Seg_Average_Landscape_Slope",
                                               "Seg_Average_Landscape_Compass_Direction", "Seg_Bearing"])]
check("pyt: 31 segments", len(vals) == 31, len(vals))
check("pyt: slope values", all(close(v[0], math.degrees(math.atan(0.1)), 0.01) for v in vals), vals[:2])
check("pyt: aspect 270, bearing ~90 (true bearing of a UTM grid-east line)",
      all(close(v[1], 270.0, 0.01) and close(v[2], 90.0, 1.0) for v in vals), vals[:2])
# field metadata written
try:
    from arcpy import metadata as md
    xml = md.Metadata(out).xml
    check("pyt: field metadata mentions the landscape slope definition",
          "slope of the terrain itself" in xml and "Computed by the Split Lines" in xml)
except Exception as e:
    check("pyt: field metadata readable", False, e)
# shapefile short names
shp_out = os.path.join(TMP, "segs.shp")
arcpy.jenness.SplitLinesIntoSegments(
    in_lines=fc, out_fc=shp_out, elev_source="DEM raster", in_dem=dem_e, elev_units="Meters",
    surface_attributes=[["Average Landscape Slope", "SgLandSlp"],
                        ["Average Landscape Compass Direction", "SgLandAsp"]],
    copy_fields=[], length_unit="Meters", slope_unit="Percent")
names = [f.name for f in arcpy.ListFields(shp_out)]
check("pyt shapefile: short names", "SgLandSlp" in names and "SgLandAsp" in names, names)
v = next(iter(arcpy.da.SearchCursor(shp_out, ["SgLandSlp"])))[0]
check("pyt shapefile: percent slope 10", close(v, 10.0, 0.05), v)

# ---------------------------------------------------------------- traverse angle + vertices-only mode
dem = lst.load_dem(dem_e)
with arcpy.da.SearchCursor(fc, ["SHAPE@", "OID@"]) as sc:
    shp, oid = next(sc)
recs = lst.build_segments(shp, "DEM", dem, 1.0, SR, oid, landscape=True)
check("traverse: east line on east-rising plane = 0 (straight up the fall line)",
      all(close(r["traverse"], 0.0, 0.7) for r in recs), [r["traverse"] for r in recs][:3])
dem = lst.load_dem(dem_n)
recs = lst.build_segments(shp, "DEM", dem, 1.0, SR, oid, landscape=True)
check("traverse: east line on north-rising plane = 90 (along the contour)",
      all(close(r["traverse"], 90.0, 0.7) for r in recs), [r["traverse"] for r in recs][:3])
recs_v = lst.build_segments(shp, "DEM", dem, 1.0, SR, oid, landscape=True, vertex_only=True)
check("vertex-only: one segment for the two-vertex line", len(recs_v) == 1, len(recs_v))
check("vertex-only: plan length 300 m", close(recs_v[0]["plan_m"], 300.0, 0.5), recs_v[0]["plan_m"])
check("vertex-only: landscape slope averaged along the step",
      close(recs_v[0]["land_slope"], math.degrees(math.atan(0.2)), 0.01), recs_v[0]["land_slope"])
check("vertex-only: traverse 90", close(recs_v[0]["traverse"], 90.0, 0.7), recs_v[0]["traverse"])
out_v = os.path.join(GDB, "segs_v")
arcpy.jenness.SplitLinesIntoSegments(
    in_lines=fc, out_fc=out_v, elev_source="DEM raster", in_dem=dem_n, elev_units="Meters",
    segmentation="At the vertices only (one segment per vertex pair)",
    surface_attributes=[["Traverse Angle", "Seg_Traverse_Angle"],
                        ["Average Landscape Slope", "Seg_Average_Landscape_Slope"]],
    copy_fields=[], length_unit="Meters", slope_unit="Degrees")
vals = [r for r in arcpy.da.SearchCursor(out_v, ["Seg_Traverse_Angle", "Seg_Average_Landscape_Slope"])]
check("pyt vertex-only: one feature", len(vals) == 1, len(vals))
check("pyt vertex-only: traverse 90, slope 11.31", len(vals) == 1 and close(vals[0][0], 90.0, 0.7)
      and close(vals[0][1], math.degrees(math.atan(0.2)), 0.01), vals)

# ---------------------------------------------------------------- datum transformation + vertical CS rule
SR27 = arcpy.SpatialReference(26711)                   # NAD27 UTM 11
SR83_11 = arcpy.SpatialReference(26911)                # NAD83 UTM 11
XA, YA = 445800.0, 3705900.0                            # a real NAD27 UTM 11 location
tx = lst.datum_transformation(SR27, SR83_11)
check("datum: a transformation is chosen for NAD27 -> NAD83", tx is not None, tx)
g_tx = arcpy.PointGeometry(arcpy.Point(XA, YA), SR27).projectAs(SR83_11, tx).firstPoint
# the same point with the datum shift deliberately NOT applied: unproject on
# NAD27's ellipsoid and re-project the raw lat/long on NAD83's (what an
# unnamed projectAs does in a fresh process; the two differ by ~80 m here)
ll = arcpy.PointGeometry(arcpy.Point(XA, YA), SR27).projectAs(arcpy.SpatialReference(4267)).firstPoint
g_no = arcpy.PointGeometry(arcpy.Point(ll.X, ll.Y), arcpy.SpatialReference(4269)).projectAs(SR83_11).firstPoint
check("datum: the transformation moves the point tens of meters",
      math.hypot(g_tx.X - g_no.X, g_tx.Y - g_no.Y) > 20, (g_tx.X - g_no.X, g_tx.Y - g_no.Y))
# a NAD83 plane rising 0.1 m per m eastward, so a horizontal error shows up in Z
X0_83, Y0_83 = g_tx.X - 500.0, g_tx.Y + 500.0
dem_83 = write_plane(os.path.join(TMP, "nad83_plane.tif"), 26911, X0_83, Y0_83, 10.0, 120, 120, 0.10, 0.0)
fc27 = arcpy.management.CreateFeatureclass(GDB, "line27", "POLYLINE", spatial_reference=SR27).getOutput(0)
with arcpy.da.InsertCursor(fc27, ["SHAPE@"]) as ic:
    ic.insertRow([arcpy.Polyline(arcpy.Array([arcpy.Point(XA, YA), arcpy.Point(XA + 300.0, YA)]), SR27)])
dem = lst.load_dem(dem_83)
with arcpy.da.SearchCursor(fc27, ["SHAPE@", "OID@"]) as sc:
    shp, oid = next(sc)
recs = lst.build_segments(shp, "DEM", dem, 1.0, SR27, oid, vertex_only=True)
exp_z = 0.10 * (g_tx.X - X0_83)
bad_z = 0.10 * (g_no.X - X0_83)
check("datum: elevation sampled at the TRANSFORMED position", close(recs[0]["begin_z"], exp_z, 0.2), (recs[0]["begin_z"], exp_z))
check("datum: not at the untransformed position", abs(recs[0]["begin_z"] - bad_z) > 3.0, (recs[0]["begin_z"], bad_z))
check("datum: output vertex returns exactly to the input coordinates",
      close(recs[0]["begin_x"], XA, 1e-3) and close(recs[0]["begin_y"], YA, 1e-3), (recs[0]["begin_x"], recs[0]["begin_y"]))
# Topography along Lines shares the engine: build_parts must sample at the same place
parts, diag = lst.build_parts(shp, "DEM", dem, 1.0)
z_first = parts[0]["z_m"][0]
check("datum: build_parts (Topography along Lines) samples at the transformed position", close(z_first, exp_z, 0.2), (z_first, exp_z))
# vertical CS rule
sr_unknown = arcpy.SpatialReference()
sr_unknown.loadFromString(SR83_11.exportToString().split(";")[0] + ';VERTCS["Unknown VCS",VDATUM["Unknown"],PARAMETER["Vertical_Shift",0.0],PARAMETER["Direction",1.0],UNIT["Meter",1.0]]')
sr_navd = arcpy.SpatialReference(26911, 5703)
o1 = lst.output_spatial_reference(SR27, sr_unknown, "Meters")
o2 = lst.output_spatial_reference(SR27, sr_navd, "Meters")
o3 = lst.output_spatial_reference(SR27, SR83_11, "Meters")
check("VCS: an unknown DEM vertical CS is NOT attached", o1.VCS is None, o1.VCS.name if o1.VCS else None)
check("VCS: a known one (NAVD88) is attached", o2.VCS is not None and "NAVD" in o2.VCS.name, o2.VCS.name if o2.VCS else None)
check("VCS: no DEM vertical CS -> none attached (no generic stand-in)", o3.VCS is None)
check("VCS: horizontal CS stays the polylines'", o2.name == SR27.name and o1.name == SR27.name, (o1.name, o2.name))
# through the .pyt: the run warning and the output SR
out27 = os.path.join(GDB, "segs27")
arcpy.jenness.SplitLinesIntoSegments(
    in_lines=fc27, out_fc=out27, elev_source="DEM raster", in_dem=dem_83, elev_units="Meters",
    segmentation="At the vertices only (one segment per vertex pair)",
    surface_attributes=[["Begin Z", "Seg_Begin_Z"]], copy_fields=[], length_unit="Meters", slope_unit="Degrees")
msgs = arcpy.GetMessages()
check("pyt: datum warning issued", "different datums" in msgs and "Map Properties" in msgs, msgs[-600:])
check("pyt: output carries no vertical CS (DEM has none)", arcpy.Describe(out27).spatialReference.VCS is None)
z_out = next(iter(arcpy.da.SearchCursor(out27, ["Seg_Begin_Z"])))[0]
check("pyt: Begin Z at the transformed position", close(z_out, exp_z, 0.2), (z_out, exp_z))

print("\n{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
