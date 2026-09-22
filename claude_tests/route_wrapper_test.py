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

"""arcpy-level tests of route_survey.run_estimated_route: all three
modes, single-point anchor rules (count + selection), geographic
inputs, multipoint explode, memory-workspace output."""
import os
import sys
import shutil
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))),
    "WildlifeTools", "Toolboxes"))
import arcpy
import route_survey as RS

TMP = tempfile.mkdtemp(prefix="route_test_")
arcpy.env.overwriteOutput = True
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
SR = arcpy.SpatialReference(26912)                 # NAD83 UTM 12N
GCS = arcpy.SpatialReference(4326)

ok = 0


def check(cond, label):
    global ok
    assert cond, label
    ok += 1
    print("  ok:", label)


def make_points(name, coords, sr=SR, multi=False):
    fc = arcpy.management.CreateFeatureclass(
        GDB, name, "MULTIPOINT" if multi else "POINT",
        spatial_reference=sr).getOutput(0)
    with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as ins:
        if multi:
            ins.insertRow([arcpy.Multipoint(arcpy.Array(
                [arcpy.Point(x, y) for x, y in coords]), sr)])
        else:
            for x, y in coords:
                ins.insertRow([arcpy.PointGeometry(
                    arcpy.Point(x, y), sr)])
    return fc


rng = np.random.default_rng(5)
stop_xy = rng.uniform(0, 20000, size=(40, 2)) + [500000, 3600000]
stops = make_points("stops", stop_xy)

# 1. simple mode: endpoints must be input points ------------------------
out1 = os.path.join(GDB, "r_simple")
path1, n1, km1, pts1 = RS.run_estimated_route(
    stops, out1, mode="Simple Shortest Path",
    out_points=os.path.join(GDB, "r_simple_pts"))
check(n1 == 40 and arcpy.Exists(path1), "simple: 40 stops routed")
row = next(arcpy.da.SearchCursor(path1,
                                 ["SHAPE@", "N_Stops", "Length_km"]))
line = row[0]
first = np.array([line.firstPoint.X, line.firstPoint.Y])
last = np.array([line.lastPoint.X, line.lastPoint.Y])
d_first = np.hypot(*(stop_xy - first).T).min()
d_last = np.hypot(*(stop_xy - last).T).min()
check(d_first < 0.01 and d_last < 0.01,
      "simple: route starts AND ends on input points")
check(abs(row[2] - km1) < 1e-6 and row[1] == 40,
      "simple: fields match return values")
orders = [r for r in arcpy.da.SearchCursor(
    pts1, ["Visit_Order", "Src_FID", "Leg_km"])]
check(sorted(o[0] for o in orders) == list(range(1, 41)),
      "simple: Visit_Order runs 1..40")
check(orders[-1][2] == 0.0 if orders[-1][0] == 40 else True,
      "simple: last leg is 0 (open path)")
legs = sum(o[2] for o in sorted(orders))
check(abs(legs - km1) / km1 < 0.01,
      "simple: legs sum ~ total ({:.2f} vs {:.2f} km)".format(
          legs, km1))

# 2. loop mode: anchor rules --------------------------------------------
truck = make_points("truck", [(500000.0, 3595000.0)])
out2 = os.path.join(GDB, "r_loop")
path2, n2, km2, _ = RS.run_estimated_route(
    stops, out2, mode="Start and End at Same Point",
    loop_point=truck)
row = next(arcpy.da.SearchCursor(path2, ["SHAPE@"]))
line = row[0]
check(abs(line.firstPoint.X - 500000) < 0.01 and
      abs(line.firstPoint.Y - 3595000) < 0.01 and
      abs(line.lastPoint.X - 500000) < 0.01 and
      abs(line.lastPoint.Y - 3595000) < 0.01,
      "loop: starts and ends at the anchor")

many = make_points("many", [(500000.0, 3595000.0),
                            (501000.0, 3595000.0),
                            (502000.0, 3595000.0)])
try:
    RS.run_estimated_route(stops, os.path.join(GDB, "r_bad"),
                           mode="Start and End at Same Point",
                           loop_point=many)
    check(False, "loop: multi-point anchor must fail")
except arcpy.ExecuteError as e:
    check("exactly ONE point" in str(e),
          "loop: multi-point anchor refused with friendly message")

lyr = arcpy.management.MakeFeatureLayer(many, "many_lyr").getOutput(0)
arcpy.management.SelectLayerByAttribute(
    lyr, "NEW_SELECTION", "OBJECTID = 2")
path2b, _, _, _ = RS.run_estimated_route(
    stops, os.path.join(GDB, "r_loop2"),
    mode="Start and End at Same Point", loop_point=lyr)
row = next(arcpy.da.SearchCursor(path2b, ["SHAPE@"]))
check(abs(row[0].firstPoint.X - 501000) < 0.01,
      "loop: single SELECTED point on a bigger layer works")

# 3. two-point mode ------------------------------------------------------
start = make_points("start_pt", [(495000.0, 3590000.0)])
end = make_points("end_pt", [(525000.0, 3625000.0)])
out3 = os.path.join(GDB, "r_two")
path3, n3, km3, pts3 = RS.run_estimated_route(
    stops, out3, mode="Start and End at Different Specified Points",
    start_point=start, end_point=end,
    out_points="memory\\rt_pts")
row = next(arcpy.da.SearchCursor(path3, ["SHAPE@"]))
line = row[0]
check(abs(line.firstPoint.X - 495000) < 0.01 and
      abs(line.lastPoint.X - 525000) < 0.01,
      "two-point: route runs start to end")
check(pts3 and int(arcpy.management.GetCount(pts3).getOutput(0))
      == 40, "two-point: memory-workspace points output works")
last_leg = [r for r in arcpy.da.SearchCursor(
    pts3, ["Visit_Order", "Leg_km"])]
last_leg.sort()
check(last_leg[-1][1] > 0,
      "two-point: last stop's leg runs to the end point (> 0)")

# 4. geographic input ----------------------------------------------------
g_xy = np.column_stack([rng.uniform(-112.0, -111.8, 25),
                        rng.uniform(34.0, 34.2, 25)])
gstops = make_points("gstops", g_xy, sr=GCS)
out4 = os.path.join(GDB, "r_geo")
path4, n4, km4, _ = RS.run_estimated_route(
    gstops, out4, mode="Simple Shortest Path")
check(n4 == 25 and
      arcpy.Describe(path4).spatialReference.type == "Geographic",
      "geographic: routed and written back in GCS")
check(10 < km4 < 500, "geographic: plausible length "
                      "({:.1f} km)".format(km4))

# 5. multipoint explode --------------------------------------------------
m_xy = [(500000 + 100 * i, 3600000 + 70 * i) for i in range(6)]
mstops = make_points("mstops", m_xy, multi=True)
out5 = os.path.join(GDB, "r_multi")
path5, n5, km5, _ = RS.run_estimated_route(
    mstops, out5, mode="Simple Shortest Path")
check(n5 == 6, "multipoint: 6 points exploded from one feature")

print("ALL OK ({} checks)".format(ok))
try:
    arcpy.management.Delete(GDB)
except Exception:                                        # noqa: BLE001
    pass                # a layer may still hold the gdb; temp anyway
shutil.rmtree(TMP, ignore_errors=True)
