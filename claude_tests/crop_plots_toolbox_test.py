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

"""Run the four crop plot tools THROUGH the toolbox (arcpy.jenness.*),
as the GP pane and scripts do. Reuses the test gdb from test_engine.py.
usage: python test_toolbox.py <pyt path> <scratch folder>
"""
import os
import sys

import arcpy

pyt, scratch = sys.argv[1], sys.argv[2]
arcpy.env.overwriteOutput = True
arcpy.ImportToolbox(pyt)
gdb = os.path.join(scratch, "crop_test.gdb")
field = os.path.join(gdb, "field")
fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def run(fn, *args, **kw):
    r = fn(*args, **kw)
    msgs = r.getMessages()
    print("  ---- messages ----\n" + "\n".join("   | " + ln for ln in msgs.splitlines()[:14]))
    return r, msgs


# names as the toolbox exposes them
tools = [t for t in dir(arcpy.jenness) if not t.startswith("_")]
for name in ("PlotDesigner", "CreateArrayAlongAxes", "FusePlotsAndField", "AddRandomNumbers"):
    check(name in tools, "toolbox exposes " + name)

r, m = run(arcpy.jenness.PlotDesigner, os.path.join(gdb, "tb_plots"), 30, 15, "Meters", 45, 3, 2,
           "1,2,3,4,5,6", 2, field, None, None, None, "Side by side (across the plot direction)", 10,
           os.path.join(gdb, "tb_plots_pts"), None, 11)
check(r.status == 4 and "View online help files" in m, "PlotDesigner ran with the help link in its messages")
check(int(arcpy.management.GetCount(os.path.join(gdb, "tb_plots"))[0]) == 12, "12 plots via toolbox")

r, m = run(arcpy.jenness.CreateArrayAlongAxes, os.path.join(gdb, "tb_plots"), 45, 5, "Meters", 1, 1,
           os.path.join(gdb, "tb_array"), None, 0, 0, 0, field, False, None)
check(r.status == 4, "CreateArrayAlongAxes ran on a feature class input")
check(int(arcpy.management.GetCount(os.path.join(gdb, "tb_array"))[0]) > 12, "array made copies of all 12 plots")

r, m = run(arcpy.jenness.FusePlotsAndField, field, os.path.join(gdb, "tb_plots"), "Rate",
           os.path.join(gdb, "tb_fused"), "FieldRate", 77, True)
check(r.status == 4, "FusePlotsAndField ran")

r, m = run(arcpy.jenness.AddRandomNumbers, os.path.join(gdb, "tb_plots"), "Treatment",
           "Random permutation of 1 to N (each record unique)", None, None, None, None, None, 5)
check(r.status == 4, "AddRandomNumbers ran")
vals = sorted(v for (v,) in arcpy.da.SearchCursor(os.path.join(gdb, "tb_plots"), ["Treatment"]))
check(vals == list(range(1, 13)), "permutation 1..12 (got %s)" % vals)

# a clean failure must come back as an error message, not a traceback
try:
    r = arcpy.jenness.AddRandomNumbers(os.path.join(gdb, "tb_plots"), "Plot_ID", "Constant value", 3)
    m = r.getMessages()
except arcpy.ExecuteError:
    m = arcpy.GetMessages()
check("not a numeric field" in m and "Traceback" not in m, "text field refused cleanly")

print("\nFAILURES:", len(fails))
for f in fails:
    print("  -", f)
