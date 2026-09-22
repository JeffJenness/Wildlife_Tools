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

"""Smoke test: Maximum Inscribed Geometry through the .pyt.

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
      hasattr(arcpy.jenness, "MaximumInscribedGeometry"))

TMP = tempfile.mkdtemp(prefix="ig_smoke_")
GDB = arcpy.management.CreateFileGDB(TMP, "s.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
X0, Y0 = 500000.0, 3800000.0
fc = arcpy.management.CreateFeatureclass(
    GDB, "poly", "POLYGON", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as ins:
    ins.insertRow([arcpy.Polygon(arcpy.Array(
        [arcpy.Point(X0 + x, Y0 + y) for x, y in
         [(0, 0), (200, 0), (200, 100), (0, 100), (0, 0)]]), SR)])

out = os.path.join(GDB, "shapes")
arcpy.jenness.MaximumInscribedGeometry(
    in_features=fc, geometry_type="All four", out_features=out)
kinds = {}
with arcpy.da.SearchCursor(out, ["Shape_Kind", "Radius_m",
                                 "Length_m", "Width_m"]) as cur:
    for row in cur:
        kinds[row[0]] = row
check("GP run: 4 shapes written", len(kinds) == 4, str(set(kinds)))
check("GP run: circle r = 50",
      abs(kinds["Circle"][1] - 50.0) < 0.5,
      str(kinds.get("Circle")))
check("GP run: rectangle 200 x 100",
      abs(kinds["Rectangle"][2] - 200.0) < 2.0
      and abs(kinds["Rectangle"][3] - 100.0) < 2.0,
      str(kinds.get("Rectangle")))

try:
    arcpy.management.Delete(GDB)
except Exception:
    pass
print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
