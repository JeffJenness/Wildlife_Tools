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

"""GP-level smoke test: ImportToolbox the source .pyt and run
Estimated Shortest Path through Points end to end -- exercises the
dialog class wiring (parameters, modes, execute, derived outputs)."""
import os
import sys
import shutil
import tempfile

import arcpy

PYT = (r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife\Wildlife_Tools"
       r"\WildlifeTools\Toolboxes\JennessEnterprisesTools.pyt")

tmp = tempfile.mkdtemp(prefix="route_smoke_")
arcpy.management.CreateFileGDB(tmp, "s.gdb")
gdb = os.path.join(tmp, "s.gdb")
sr = arcpy.SpatialReference(26912)
pts = arcpy.management.CreateFeatureclass(
    gdb, "pts", "POINT", spatial_reference=sr).getOutput(0)
import numpy as np
rng = np.random.default_rng(9)
with arcpy.da.InsertCursor(pts, ["SHAPE@XY"]) as ins:
    for x, y in rng.uniform(0, 15000, size=(30, 2)) + [600000,
                                                       3600000]:
        ins.insertRow([(float(x), float(y))])
truck = arcpy.management.CreateFeatureclass(
    gdb, "truck", "POINT", spatial_reference=sr).getOutput(0)
with arcpy.da.InsertCursor(truck, ["SHAPE@XY"]) as ins:
    ins.insertRow([(600000.0, 3595000.0)])

arcpy.ImportToolbox(PYT)
res = arcpy.jenness.EstimatedShortestPath(
    in_points=pts, mode="Start and End at Same Point",
    loop_point=truck, out_route=os.path.join(gdb, "route"),
    out_points=os.path.join(gdb, "route_pts"))
n = int(res.getOutput(2))
km = float(res.getOutput(3))
assert n == 30, n
assert 10 < km < 500, km
assert int(arcpy.management.GetCount(
    os.path.join(gdb, "route_pts")).getOutput(0)) == 30
row = next(arcpy.da.SearchCursor(os.path.join(gdb, "route"),
                                 ["SHAPE@", "N_Stops"]))
assert row[1] == 30
line = row[0]
assert abs(line.firstPoint.X - line.lastPoint.X) < 0.01 \
    and abs(line.firstPoint.Y - line.lastPoint.Y) < 0.01
print("GP smoke OK: round trip, 30 stops, {:.2f} km, loop closed, "
      "derived outputs n={} km={:.2f}".format(km, n, km))

try:
    arcpy.management.Delete(gdb)
except Exception:                                        # noqa: BLE001
    pass
shutil.rmtree(tmp, ignore_errors=True)
