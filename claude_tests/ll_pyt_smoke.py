# -*- coding: utf-8 -*-
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

"""Smoke test: Longest Line in Polygon (Fetch) through the .pyt.

Run with ArcGIS Pro's python.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PYT = os.path.join(os.path.dirname(HERE), "WildlifeTools",
                   "Toolboxes", "JennessEnterprisesTools.pyt")

import arcpy  # noqa: E402

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  {}".format(name))
    else:
        FAIL += 1
        print("  FAIL  {}  {}".format(name, detail))


arcpy.env.overwriteOutput = True
arcpy.ImportToolbox(PYT)
check("toolbox lists the tool",
      hasattr(arcpy.jenness, "LongestLineFetch"))

TMP = tempfile.mkdtemp(prefix="ll_smoke_")
GDB = arcpy.management.CreateFileGDB(TMP, "s.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
X0, Y0 = 500000.0, 3800000.0
fc = arcpy.management.CreateFeatureclass(
    GDB, "lake", "POLYGON", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as ins:
    ins.insertRow([arcpy.Polygon(arcpy.Array(
        [arcpy.Point(X0 + x, Y0 + y) for x, y in
         [(0, 0), (0, 300), (400, 300), (400, 0), (0, 0)]]), SR)])

out = os.path.join(GDB, "fl")
arcpy.jenness.LongestLineFetch(
    mode="Longest line in each polygon (the fetch)",
    in_features=fc, out_lines=out)
with arcpy.da.SearchCursor(out, ["Length_m", "Azimuth"]) as cur:
    row = next(cur)
check("GP run: fetch line ~ 500 m diagonal",
      abs(row[0] - 500.0) < 1.5, str(row))

stn = arcpy.management.CreateFeatureclass(
    GDB, "stn", "POINT", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(stn, ["SHAPE@"]) as ins:
    ins.insertRow([arcpy.PointGeometry(
        arcpy.Point(X0 + 200, Y0 + 150), SR)])
outr = os.path.join(GDB, "flr")
arcpy.jenness.LongestLineFetch(
    mode="Directional fetch rays from station points",
    in_features=fc, stations=stn, bearing_increment=45.0,
    out_radials=outr)
n = int(arcpy.management.GetCount(outr).getOutput(0))
check("GP run: 8 fetch rays + MaxRay + MaxChord", n == 10, str(n))

try:
    arcpy.management.Delete(GDB)
except Exception:
    pass
print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
