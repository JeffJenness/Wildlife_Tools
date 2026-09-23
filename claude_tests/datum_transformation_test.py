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

"""Datum transformations across the toolbox (2026-09-23). A bare
Geometry.projectAs applies NO datum transformation in ArcGIS Pro, and neither
does a cursor's spatial_reference=, so NAD27 data met NAD83 rasters ~80-200 m
off. Every cross-datum projection now goes through clip_tools.project_geometry
/ transformation_name. This checks the helper, the reprojecting cursor in the
aspect tools, and the two line tools' engine. Run with ArcGIS Pro's python."""
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TBX = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TBX)

import arcpy  # noqa: E402
import clip_tools as _ct  # noqa: E402
import aspect_tools  # noqa: E402
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


SR27 = arcpy.SpatialReference(26711)      # NAD27 UTM 11N
SR83 = arcpy.SpatialReference(26911)      # NAD83 UTM 11N
XA, YA = 445800.0, 3705900.0              # southern California

# the untransformed target: same lat/long re-projected on NAD83's ellipsoid
ll = arcpy.PointGeometry(arcpy.Point(XA, YA), SR27).projectAs(arcpy.SpatialReference(4267)).firstPoint
no = arcpy.PointGeometry(arcpy.Point(ll.X, ll.Y), arcpy.SpatialReference(4269)).projectAs(SR83).firstPoint

# ---------------------------------------------------------------- the helper
name = _ct.transformation_name(SR27, SR83)
check("helper: names a transformation for NAD27 -> NAD83", bool(name), name)
check("helper: none for the same datum", _ct.transformation_name(SR27, arcpy.SpatialReference(26712)) is None)
g = _ct.project_geometry(arcpy.PointGeometry(arcpy.Point(XA, YA), SR27), SR83).firstPoint
check("helper: applies the datum shift (tens of meters from the untransformed point)",
      math.hypot(g.X - no.X, g.Y - no.Y) > 20, (g.X - no.X, g.Y - no.Y))
back = _ct.project_geometry(arcpy.PointGeometry(arcpy.Point(g.X, g.Y), SR83), SR27).firstPoint
check("helper: round trip returns exactly", abs(back.X - XA) < 1e-3 and abs(back.Y - YA) < 1e-3, (back.X - XA, back.Y - YA))
e = _ct.project_geometry(arcpy.Extent(XA, YA, XA + 100, YA + 100, spatial_reference=SR27), SR83)
check("helper: extents too", abs(e.XMin - g.X) < 0.5 and abs(e.YMin - g.Y) < 0.5, (e.XMin - g.X, e.YMin - g.Y))
with arcpy.EnvManager(geographicTransformations="NAD_1927_To_NAD_1983_NADCON"):
    _ct._PG_CACHE.clear()
    check("helper: honors the Geographic Transformations environment",
          _ct.transformation_name(SR27, SR83) == "NAD_1927_To_NAD_1983_NADCON", _ct.transformation_name(SR27, SR83))
_ct._PG_CACHE.clear()

# ---------------------------------------------------------------- the aspect tools' zone cursor
TMP = tempfile.mkdtemp(prefix="datum_")
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
zones = arcpy.management.CreateFeatureclass(GDB, "zones", "POLYGON", spatial_reference=SR27).getOutput(0)
arcpy.management.AddField(zones, "Zone", "LONG")
sq = arcpy.Polygon(arcpy.Array([arcpy.Point(XA, YA), arcpy.Point(XA + 200, YA), arcpy.Point(XA + 200, YA + 200),
                                arcpy.Point(XA, YA + 200), arcpy.Point(XA, YA)]), SR27)
with arcpy.da.InsertCursor(zones, ["SHAPE@", "Zone"]) as ic:
    ic.insertRow([sq, 1])
# a fake raster frame in NAD83 around the transformed square
gt = (g.X - 1000.0, 10.0, 0.0, g.Y + 1200.0, 0.0, -10.0)
W = H = 220
with aspect_tools._zone_cursor(zones, ["Zone", "SHAPE@"], SR83, gt, W, H, None, "polygons") as cur:
    rows = list(cur)
check("zone cursor: yields the zone", len(rows) == 1 and rows[0][0] == 1, rows)
if rows:
    c = rows[0][1]
    check("zone cursor: geometry is in the raster's SR", c.spatialReference.name == SR83.name, c.spatialReference.name)
    p = c.firstPoint
    check("zone cursor: geometry carries the datum shift",
          math.hypot(p.X - g.X, p.Y - g.Y) < 0.5 or math.hypot(p.X - no.X, p.Y - no.Y) > 20, (p.X, p.Y, g.X, g.Y))
with aspect_tools._zone_cursor(zones, ["Zone", "SHAPE@XY"], SR83, gt, W, H, None, "polygons") as cur:
    rows = list(cur)
check("zone cursor: SHAPE@XY comes back as an (x, y) tuple in the raster's SR",
      len(rows) == 1 and isinstance(rows[0][1], tuple) and abs(rows[0][1][0] - (g.X + 100)) < 1.0, rows)

# ---------------------------------------------------------------- the line tools' engine
tx = lst.datum_transformation(SR27, SR83)
check("line tools: engine uses the same toolbox-wide transformation", tx == _ct.transformation_name(SR27, SR83), (tx, _ct.transformation_name(SR27, SR83)))
q = lst.project_geometry(arcpy.PointGeometry(arcpy.Point(XA, YA), SR27), SR83).firstPoint
check("line tools: engine projection carries the shift", abs(q.X - g.X) < 1e-6 and abs(q.Y - g.Y) < 1e-6)

print("\n{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
