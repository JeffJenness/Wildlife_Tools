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

"""arcpy-level tests of run_grts_sample: all three frame kinds,
strata, PPS, legacy, min distance, hole avoidance, ring-orientation
cross-check, geographic/LAEA path, seed reproducibility."""
import os
import sys
import shutil
import tempfile
from collections import Counter

import numpy as np

sys.path.insert(0, r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife"
                   r"\Wildlife_Tools\WildlifeTools\Toolboxes")
import arcpy
import grts_sampling as GS

tmp = tempfile.mkdtemp(prefix="grts_")
arcpy.management.CreateFileGDB(tmp, "t.gdb")
gdb = os.path.join(tmp, "t.gdb")
sr = arcpy.SpatialReference(26912)
wgs = arcpy.SpatialReference(4326)


def make_fc(name, gtype, srx, fields=()):
    arcpy.management.CreateFeatureclass(gdb, name, gtype,
                                        spatial_reference=srx)
    p = os.path.join(gdb, name)
    for fname, ftype in fields:
        arcpy.management.AddField(p, fname, ftype)
    return p


def rows(path, cols):
    return list(arcpy.da.SearchCursor(path, cols))


# ---- frames -----------------------------------------------------------
# polygons: two strata; stratum A has a hole
polys = make_fc("polys", "POLYGON", sr, [("Zone", "TEXT")])
sq = lambda x0, y0, w: arcpy.Polygon(arcpy.Array(
    [arcpy.Point(x0, y0), arcpy.Point(x0, y0 + w),
     arcpy.Point(x0 + w, y0 + w), arcpy.Point(x0 + w, y0),
     arcpy.Point(x0, y0)]), sr)
holed = sq(600000, 3600000, 8000).difference(
    sq(603000, 3603000, 2000))
with arcpy.da.InsertCursor(polys, ["SHAPE@", "Zone"]) as ic:
    ic.insertRow([holed, "A"])
    ic.insertRow([sq(612000, 3600000, 4000), "B"])   # 1/4 the area

# lines
lines = make_fc("lines", "POLYLINE", sr)
with arcpy.da.InsertCursor(lines, ["SHAPE@"]) as ic:
    ic.insertRow([arcpy.Polyline(arcpy.Array(
        [arcpy.Point(600000, 3620000),
         arcpy.Point(610000, 3620000)]), sr)])
    ic.insertRow([arcpy.Polyline(arcpy.Array(
        [arcpy.Point(600000, 3622000),
         arcpy.Point(602000, 3622000)]), sr)])       # 1/5 the length

# points, with a PPS weight field
ptfc = make_fc("pts", "POINT", sr, [("W", "DOUBLE")])
rng = np.random.default_rng(3)
pxy = rng.uniform([600000, 3600000], [612000, 3612000],
                  size=(300, 2))
with arcpy.da.InsertCursor(ptfc, ["SHAPE@XY", "W"]) as ic:
    for i, (x, y) in enumerate(pxy):
        ic.insertRow([(float(x), float(y)),
                      4.0 if i < 30 else 1.0])

# legacy = 3 of the frame points
leg = make_fc("legacy", "POINT", sr)
with arcpy.da.InsertCursor(leg, ["SHAPE@XY"]) as ic:
    for i in (10, 20, 30):
        ic.insertRow([(float(pxy[i, 0]), float(pxy[i, 1]))])

# geographic polygons
gpolys = make_fc("gpolys", "POLYGON", wgs)
with arcpy.da.InsertCursor(gpolys, ["SHAPE@"]) as ic:
    ic.insertRow([arcpy.Polygon(arcpy.Array(
        [arcpy.Point(-110.5, 32.5), arcpy.Point(-110.5, 33.0),
         arcpy.Point(-110.0, 33.0), arcpy.Point(-110.0, 32.5),
         arcpy.Point(-110.5, 32.5)]), wgs)])

# ---- 1. areal, stratified proportional, with hole + n_over -----------
out1 = os.path.join(gdb, "s_areal")
GS.run_grts_sample(polys, out1, 40, n_over=10,
                   stratum_field="Zone", random_seed=11)
r1 = rows(out1, ["SHAPE@XY", "SiteID", "Site_Use", "Stratum",
                 "Design_Wgt"])
assert len(r1) == 50
by = Counter((s, u) for _, _, u, s, _ in r1)
# A:B areas = 60 km2 : 16 km2 -> apportion 40 to (32, 8), 10 to (8, 2)
assert by[("A", "Base")] == 32 and by[("B", "Base")] == 8, by
assert by[("A", "Over")] == 8 and by[("B", "Over")] == 2, by
for (x, y), _, u, s, w in r1:
    if s == "A":
        assert not (603000 < x < 605000 and 3603000 < y < 3605000), \
            "site in the hole"
        assert 600000 - 1 <= x <= 608000 + 1
print("1. areal stratified: counts (32+8 base, 8+2 over), hole "
     "avoided, orientation check passed")

# ---- 2. weights: base sites of A represent its area ------------------
a_base = [w for _, _, u, s, w in r1 if s == "A" and u != "Over"]
tot = sum(a_base)
assert abs(tot - 60e6 * (40 / 32) * (32 / 40) / 1) / 60e6 < 2.5, tot
print("2. design weights plausible (stratum A base sum "
      "{:.3g} m2 vs area 6e7)".format(tot))

# ---- 3. linear frame: length-proportional between lines --------------
out2 = os.path.join(gdb, "s_lines")
GS.run_grts_sample(lines, out2, 24, random_seed=5)
r2 = rows(out2, ["SHAPE@XY"])
assert len(r2) == 24
short = sum(1 for (x, y), in r2 if abs(y - 3622000) < 1)
assert 1 <= short <= 8, short     # expect ~4 of 24
print("3. linear: 24 sites on-line, short line got {}".format(short))

# ---- 4. finite + PPS + legacy + mindis -------------------------------
out3 = os.path.join(gdb, "s_pts")
GS.run_grts_sample(ptfc, out3, 25, n_over=5, aux_field="W",
                   legacy_sites=leg, min_spacing=300,
                   linear_units="Meters", random_seed=21)
r3 = rows(out3, ["SHAPE@XY", "Site_Use", "Design_Wgt", "Src_FID"])
assert len(r3) == 30
uses = Counter(u for _, u, _, _ in r3)
assert uses["Legacy"] == 3 and uses["Over"] == 5 \
    and uses["Base"] == 22, uses
srcs = {s for _, _, _, s in r3}
assert len(srcs) == 30, "duplicate source points"
print("4. finite PPS + legacy(3) + mindis: uses {}".format(dict(uses)))

# ---- 5. geographic (LAEA) path ---------------------------------------
out4 = os.path.join(gdb, "s_geo")
GS.run_grts_sample(gpolys, out4, 20, random_seed=8)
r4 = rows(out4, ["SHAPE@XY"])
assert len(r4) == 20
for (x, y), in r4:
    assert -110.5 - 1e-6 <= x <= -110.0 + 1e-6
    assert 32.5 - 1e-6 <= y <= 33.0 + 1e-6
d = arcpy.Describe(out4)
assert d.spatialReference.type == "Geographic"
print("5. geographic frame: 20 sites inside, written back in WGS84")

# ---- 6. reproducibility ----------------------------------------------
out5 = os.path.join(gdb, "s_rep")
GS.run_grts_sample(polys, out5, 15, stratum_field="Zone",
                   random_seed=99)
a = rows(out5, ["SHAPE@XY", "SiteID"])
arcpy.management.Delete(out5)
GS.run_grts_sample(polys, out5, 15, stratum_field="Zone",
                   random_seed=99)
b = rows(out5, ["SHAPE@XY", "SiteID"])
assert all(np.allclose(p1, p2) and s1 == s2
           for (p1, s1), (p2, s2) in zip(a, b))
print("6. seed-reproducible end to end")

arcpy.management.Delete(gdb)
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK")
