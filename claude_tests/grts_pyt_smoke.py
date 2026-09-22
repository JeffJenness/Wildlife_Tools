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

"""GP-level smoke test: ImportToolbox the source .pyt and run the
Spatially Balanced Sample (GRTS) tool end to end -- exercises the
dialog class wiring (parameters, execute, derived output)."""
import os
import sys
import shutil
import tempfile

import arcpy

PYT = (r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife\Wildlife_Tools"
       r"\WildlifeTools\Toolboxes\JennessEnterprisesTools.pyt")

tmp = tempfile.mkdtemp(prefix="grts_smoke_")
arcpy.management.CreateFileGDB(tmp, "t.gdb")
gdb = os.path.join(tmp, "t.gdb")
sr = arcpy.SpatialReference(26912)
arcpy.management.CreateFeatureclass(gdb, "aoi", "POLYGON",
                                    spatial_reference=sr)
with arcpy.da.InsertCursor(os.path.join(gdb, "aoi"),
                           ["SHAPE@"]) as ic:
    ic.insertRow([arcpy.Polygon(arcpy.Array(
        [arcpy.Point(600000, 3600000), arcpy.Point(600000, 3610000),
         arcpy.Point(612000, 3610000), arcpy.Point(612000, 3600000),
         arcpy.Point(600000, 3600000)]), sr)])

arcpy.ImportToolbox(PYT)
out = os.path.join(gdb, "sites")
res = arcpy.jenness.SpatiallyBalancedSample(
    in_frame=os.path.join(gdb, "aoi"),
    n_base=12, n_over=3, random_seed=42, out_fc=out)
n_written = int(res.getOutput(1))
assert n_written == 15, n_written
var_v = float(res.getOutput(2))
pielou = float(res.getOutput(3))
assert 0.0 <= var_v < 1.0, var_v
assert 0.0 <= pielou < 1.0, pielou
rows = list(arcpy.da.SearchCursor(
    out, ["SiteID", "Site_Use", "Design_Wgt"]))
assert len(rows) == 15
assert rows[0][0] == "Site-0001"
assert sum(1 for r in rows if r[1] == "Base") == 12
assert sum(1 for r in rows if r[1] == "Over") == 3
assert all(r[2] > 0 for r in rows)
print("GP smoke OK: 15 sites (12 base + 3 over), SiteIDs ordered, "
      "weights positive, derived outputs n={} Var(v)={:.3f} "
      "Pielou={:.4f}".format(n_written, var_v, pielou))

arcpy.management.Delete(gdb)
shutil.rmtree(tmp, ignore_errors=True)
