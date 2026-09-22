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

import sys

import numpy as np
import arcpy

sys.path.insert(0, r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife"
                   r"\Wildlife_Tools\WildlifeTools\Toolboxes")
import kernel_density as kd

arcpy.env.overwriteOutput = True
SR = arcpy.SpatialReference(32612)


class Msgs:
    def addMessage(self, t):
        print("   msg:", t)

    def addWarningMessage(self, t):
        print("   WARN:", t)


# points in memory
pts = arcpy.management.CreateFeatureclass(
    "memory", "pts", "POINT", spatial_reference=SR).getOutput(0)
rng = np.random.default_rng(5)
with arcpy.da.InsertCursor(pts, ["SHAPE@XY"]) as ins:
    for x, y in rng.normal((500000, 3800000), 300, (60, 2)):
        ins.insertRow([(float(x), float(y))])

# Jeff's crash case: memory OUTPUT, proportion surface
out = kd.run_kernel_density(
    points=pts, pop_field=None,
    out_raster=r"memory\kd_mem_test",
    cell_size="30", search_radius="500 Meters",
    kernel="Quartic (Esri KD method)", distance_method="Planar",
    out_value="Proportion under curve", area_unit="Square map units",
    messages=Msgs())
print("memory proportion run OK ->", out)

# poisoned-interpreter probe: the next dict call must not explode
probe = {"a": 1}
assert probe.get("a") == 1
print("interpreter state clean after run")

# memory contours over the memory raster (marker absent -> density path)
outs = kd.run_probability_contours(
    kd=r"memory\kd_mem_test",
    out_lines=r"memory\kd_mem_lines", out_polys=None, messages=Msgs())
print("memory contours run OK ->", outs)
probe2 = {"b": 2}
assert probe2.get("b") == 2
print("interpreter state clean after contours")
print()
print("MEMORY-WORKSPACE CRASH FIXED")
