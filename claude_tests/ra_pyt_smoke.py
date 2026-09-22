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

"""Smoke test: the Random Sample Array Generator through the .pyt
itself (ImportToolbox -> arcpy.jenness.RandomSampleArrayGenerator).

Run with ArcGIS Pro's python.
"""
import os
import sys
import tempfile

import numpy as np  # noqa: F401

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
check("toolbox imports and lists the tool",
      hasattr(arcpy.jenness, "RandomSampleArrayGenerator"))

TMP = tempfile.mkdtemp(prefix="ra_smoke_")
GDB = arcpy.management.CreateFileGDB(TMP, "s.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
fc = arcpy.management.CreateFeatureclass(
    GDB, "sq", "POLYGON", spatial_reference=SR).getOutput(0)
with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as ins:
    ins.insertRow([arcpy.Polygon(arcpy.Array(
        [arcpy.Point(*c) for c in
         [(0, 0), (0, 1000), (1000, 1000), (1000, 0), (0, 0)]]),
        SR)])

out = os.path.join(GDB, "smoke_arrays")
arcpy.jenness.RandomSampleArrayGenerator(
    boundary=fc,
    strata_method="No stratification (the whole area is one stratum)",
    alloc_method="Fixed total number of arrays",
    n_arrays=3,
    array_shape="Grid (rows and columns of points)",
    n_rows=4, n_cols=4, point_spacing=40,
    orientation_mode="Random orientation for each array",
    must_fit=True, min_spacing=100, linear_units="Meters",
    random_seed=99, out_fc=out)
n = int(arcpy.management.GetCount(out).getOutput(0))
check("GP-level run: 3 arrays x 16 points", n == 48, str(n))
fields = {f.name for f in arcpy.ListFields(out)}
check("fields present",
      {"Array_ID", "Point_ID", "Row", "Col"} <= fields, str(fields))

try:
    arcpy.management.Delete(GDB)
except Exception:
    pass
print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
