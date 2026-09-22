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

"""Regression repro for the tight-XY-domain crash (Jeff, 2026-09-02):
a boundary FC whose SR domain hugs its own data made Repeating Shapes
fail at insertRow ('coordinates or measures are out of bounds') for
any shape reaching past the boundary. Recreates the Pinalenos case,
then proves clean_sr() fixes it."""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife"
                   r"\Wildlife_Tools\WildlifeTools\Toolboxes")
import arcpy
import repeating_shapes as RS
import voronoi_tools as VT

tmp = tempfile.mkdtemp(prefix="rs_domain_")
gdb = os.path.join(tmp, "t.gdb")
arcpy.management.CreateFileGDB(tmp, "t.gdb")

# --- boundary FC with a TIGHT XY domain (like Jeff's Pinalenos) -------
sr = arcpy.SpatialReference(26912)          # NAD83 UTM 12N
sr.setDomain(593120, 622440, 3595368, 3624687)   # hugs the data
print("tight domain set:", sr.domain)
arcpy.management.CreateFeatureclass(gdb, "bnd", "POLYGON",
                                    spatial_reference=sr)
bnd = os.path.join(gdb, "bnd")
ring = [(594000, 3596500), (594000, 3623500),
        (617000, 3623500), (617000, 3596500), (594000, 3596500)]
with arcpy.da.InsertCursor(bnd, ["SHAPE@"]) as ic:
    ic.insertRow([arcpy.Polygon(
        arcpy.Array([arcpy.Point(*p) for p in ring]), sr)])
d = arcpy.Describe(bnd)
print("bnd described domain:", d.spatialReference.domain)

# --- sanity: clean_sr strips the domain -------------------------------
cl = VT.clean_sr(d.spatialReference)
dom = [float(v) for v in str(cl.domain).split()]
assert dom[2] - dom[0] > 1e6, "clean_sr did not widen the X domain"
assert cl.factoryCode in (26912, 0) and "UTM_Zone_12N" in cl.name
print("clean_sr domain:", cl.domain[:60], "... OK")

# --- the crashing run: 1000 m hexagons, keep whole intersecting -------
out = os.path.join(gdb, "hexes")
RS.run_repeating_shapes(
    shape_type=RS.HEXAGONS, out_fc=out, boundary=bnd,
    edge=1000.0, area_units="Hectares", linear_units="Meters",
    orientation=35.74, edge_policy=RS.KEEP_INTERSECT, messages=None)
n = int(arcpy.management.GetCount(out).getOutput(0))
assert n > 0, "no hexagons written"

# confirm shapes genuinely reach past the tight domain (the old crash)
xmin = min(r[0].extent.XMin
           for r in arcpy.da.SearchCursor(out, ["SHAPE@"]))
assert xmin < 593120, "no shape reaches past the old domain edge?!"
print("OK: {} hexagons written; westmost reaches {:.0f} "
      "(old domain edge 593120) -- crash fixed".format(n, xmin))

arcpy.management.Delete(gdb)
shutil.rmtree(tmp, ignore_errors=True)
