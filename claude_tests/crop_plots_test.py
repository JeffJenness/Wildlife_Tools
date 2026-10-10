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

"""Headless checks of crop_plots.py with Pro's arcpy (no Pro UI).
usage: python test_engine.py <Toolboxes folder> <scratch folder>
"""
import json
import math
import os
import sys

sys.path.insert(0, sys.argv[1])
scratch = sys.argv[2]
os.makedirs(scratch, exist_ok=True)

import arcpy  # noqa: E402
import crop_plots as cp  # noqa: E402

arcpy.env.overwriteOutput = True
gdb = os.path.join(scratch, "crop_test.gdb")
if not arcpy.Exists(gdb):
    arcpy.management.CreateFileGDB(scratch, "crop_test.gdb")
utm = arcpy.SpatialReference(26912)       # NAD83 UTM 12N
wgs = arcpy.SpatialReference(4326)
fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


# --- a field polygon (UTM) and a WGS84 copy ------------------------------------
field = os.path.join(gdb, "field")
arcpy.management.CreateFeatureclass(gdb, "field", "POLYGON", spatial_reference=utm)
arcpy.management.AddField(field, "FieldRate", "DOUBLE")
x0, y0 = 440000.0, 3895000.0
with arcpy.da.InsertCursor(field, ["SHAPE@", "FieldRate"]) as ins:
    ins.insertRow([arcpy.Polygon(arcpy.Array([arcpy.Point(x0, y0), arcpy.Point(x0, y0 + 600),
                                              arcpy.Point(x0 + 800, y0 + 600), arcpy.Point(x0 + 800, y0),
                                              arcpy.Point(x0, y0)]), utm), 100.0])
    ins.insertRow([arcpy.Polygon(arcpy.Array([arcpy.Point(x0 + 800, y0), arcpy.Point(x0 + 800, y0 + 600),
                                              arcpy.Point(x0 + 1200, y0 + 600), arcpy.Point(x0 + 1200, y0),
                                              arcpy.Point(x0 + 800, y0)]), utm), None])

# --- PLOT DESIGNER --------------------------------------------------------------
plots = os.path.join(gdb, "plots")
pts = os.path.join(gdb, "plots_pts")
lines = os.path.join(gdb, "plots_lines")
out, p_pts, p_lines = cp.run_plot_designer(
    plots, long_edge=40, short_edge=20, units="Meters", bearing=30, n_long=2, n_short=2,
    rates="0,50,100,150", n_reps=3, field_layer=field, anchor_x=x0 + 100, anchor_y=y0 + 100,
    anchor_sr=utm, rep_layout="Side by side (across the plot direction)", rep_gap=5,
    out_points=pts, out_lines=lines, seed=7)
n = int(arcpy.management.GetCount(out)[0])
check(n == 12, "plot designer wrote 12 polygons (got %d)" % n)
check(int(arcpy.management.GetCount(p_pts)[0]) == 12, "12 center points")
check(int(arcpy.management.GetCount(p_lines)[0]) == 12, "12 centerlines")
rates_per_rep = {}
with arcpy.da.SearchCursor(out, ["SHAPE@", "Rep", "Plot", "Rate", "Bearing", "Long_m", "Short_m", "Center_Lon"]) as cur:
    for g, rep, plot, rate, brg, lm, sm, lon in cur:
        rates_per_rep.setdefault(rep, []).append(rate)
        area = g.getArea("GEODESIC", "SQUAREMETERS")
        if rep == 1 and plot == 1:
            check(abs(area - 800.0) < 0.5, "plot area geodesic ~800 m2 (got %.3f)" % area)
            # the long edge: distance between corner 0 and corner 1
            pt0 = g.firstPoint
            parts = [p for p in g.getPart(0)]
            d01 = arcpy.PointGeometry(parts[0], utm).angleAndDistanceTo(arcpy.PointGeometry(parts[1], utm), "GEODESIC")
            check(abs(d01[1] - 40.0) < 0.02, "long edge geodesic 40 m (got %.4f) bearing %.2f" % (d01[1], d01[0]))
            check(abs(((d01[0] - 30.0) + 180) % 360 - 180) < 0.05, "long edge bearing 30 deg (got %.3f)" % d01[0])
for rep, rl in rates_per_rep.items():
    check(sorted(rl) == [0.0, 50.0, 100.0, 150.0], "rep %d has every rate once" % rep)
# replications must not overlap
geoms = [r[0] for r in arcpy.da.SearchCursor(out, ["SHAPE@"])]
overlap = 0
for i in range(len(geoms)):
    for j in range(i + 1, len(geoms)):
        if not geoms[i].disjoint(geoms[j]) and geoms[i].intersect(geoms[j], 4).area > 0.01:
            overlap += 1
check(overlap == 0, "no overlapping plots (%d overlaps)" % overlap)

# default anchor from the field layer, geographic output CS via env
arcpy.env.outputCoordinateSystem = wgs
out2, _, _ = cp.run_plot_designer(os.path.join(gdb, "plots_geo"), 30, 10, "Feet", 0, 3, 1, [1, 2, 3], 2,
                                  field_layer=field, rep_layout="End to end", rep_gap=0)
arcpy.env.outputCoordinateSystem = None
d = arcpy.Describe(out2)
check(d.spatialReference.type == "Geographic", "env output CS honored (geographic)")
with arcpy.da.SearchCursor(out2, ["SHAPE@", "Rep", "Plot"]) as cur:
    rows = list(cur)
check(len(rows) == 6, "6 plots end to end")
g = rows[0][0]
check(abs(g.getArea("GEODESIC", "SQUAREMETERS") - 30 * 10 * 0.3048 ** 2) < 0.05, "feet converted (area %.3f)" % g.getArea("GEODESIC", "SQUAREMETERS"))

# --- CREATE ARRAY ALONG AXES ----------------------------------------------------
src = arcpy.Polygon(arcpy.Array([arcpy.Point(x0 + 10, y0 + 10), arcpy.Point(x0 + 10, y0 + 60),
                                 arcpy.Point(x0 + 90, y0 + 60), arcpy.Point(x0 + 90, y0 + 10),
                                 arcpy.Point(x0 + 10, y0 + 10)]), utm)
arr = os.path.join(gdb, "array1")
path, ncop = cp.run_create_array(90, 10, "Meters", 2, 1, in_geometry_json=src.JSON, out_fc=arr)
check(ncop == 4, "array along one axis: 1 original + 2 forward + 1 back = 4 (got %d)" % ncop)
cents = {}
with arcpy.da.SearchCursor(path, ["Axis_1_ID", "Axis_2_ID", "Center_X", "Center_Y", "Area_Ha", "SHAPE@"]) as cur:
    for k1, k2, cx, cy, ha, g in cur:
        cents[(k1, k2)] = (cx, cy, ha, g)
dx = cents[(1, 0)][0] - cents[(0, 0)][0]
dy = cents[(1, 0)][1] - cents[(0, 0)][1]
# bearing 90 is TRUE east; UTM grid east differs by the meridian convergence
# (~0.38 deg here), so the axis-aligned 80 x 50 rectangle spans 80.33 m along
# true east and the step is 90.33 m, with a ~0.6 m northward component
check(90.2 < dx < 90.5 and 0.3 < abs(dy) < 0.9, "step along true east = tilted extent + gap (dx %.3f, dy %.3f)" % (dx, dy))
check(abs(cents[(0, 0)][2] - 0.4) < 1e-3, "area 0.4 ha (got %.5f)" % cents[(0, 0)][2])
check(cents[(0, 0)][3].equals(src) or cents[(0, 0)][3].symmetricDifference(src).area < 0.01, "original kept unchanged")
# two axes + clip + features input with selection
arcpy.management.MakeFeatureLayer(field, "field_lyr")
arcpy.management.SelectLayerByAttribute("field_lyr", "NEW_SELECTION", "FieldRate = 100")
srcfc = os.path.join(gdb, "src")
arcpy.management.CreateFeatureclass(gdb, "src", "POLYGON", spatial_reference=utm)
with arcpy.da.InsertCursor(srcfc, ["SHAPE@"]) as ins:
    ins.insertRow([src])
arr2 = os.path.join(gdb, "array2")
path2, ncop2 = cp.run_create_array(0, 0, "Meters", 20, 0, in_features=srcfc, bearing2=90, gap2=5, units2="Meters",
                                   n_right=20, n_left=0, clip_features="field_lyr", out_fc=arr2)
check(ncop2 < 21 * 21, "clip removed copies outside the selected field polygon (%d kept)" % ncop2)
with arcpy.da.SearchCursor(path2, ["SHAPE@", "Axis_1_ID", "Axis_2_ID"]) as cur:
    allin = all(g.extent.XMax <= x0 + 800 + 0.01 and g.extent.YMax <= y0 + 600 + 0.01 for g, a, b in cur)
check(allin, "every copy lies inside the clip polygon")
# shapefile output + point source
shp = os.path.join(scratch, "array_pts.shp")
psrc = arcpy.PointGeometry(arcpy.Point(x0 + 50, y0 + 50), utm)
path3, ncop3 = cp.run_create_array(45, 25, "Feet", 3, 3, in_geometry_json=psrc.JSON, out_fc=shp)
check(ncop3 == 7 and arcpy.Exists(shp), "point array to shapefile (7 points)")
with arcpy.da.SearchCursor(path3, ["SHAPE@XY", "Axis_1_ID"]) as cur:
    pp = {k: xy for xy, k in cur}
step = math.hypot(pp[1][0] - pp[0][0], pp[1][1] - pp[0][1])
check(abs(step - 25 * 0.3048) < 0.01, "point step = gap only (25 ft = %.4f m, got %.4f)" % (25 * 0.3048, step))

# --- FUSE -----------------------------------------------------------------------
fused = os.path.join(gdb, "fused")
fpath, nf, npl = cp.run_fuse(field, plots, "Rate", fused, field_rate_field="FieldRate", standard_rate=55,
                             transfer_plot_fields=True)
check(npl == 12, "fuse added 12 plots")
tot_field = sum(g.area for (g,) in arcpy.da.SearchCursor(field, ["SHAPE@"]))
tot_fused = sum(g.area for (g,) in arcpy.da.SearchCursor(fpath, ["SHAPE@"]))
check(abs(tot_field - tot_fused) < 1.0, "fused area equals field area (field %.1f, fused %.1f): no double counting" % (tot_field, tot_fused))
rates = sorted({r for (r,) in arcpy.da.SearchCursor(fpath, ["Rate"], "Source = 'Field'")})
check(rates == [55.0, 100.0], "field rates: 100 from the attribute, 55 standard for the null (got %s)" % rates)
fnames = [f.name for f in arcpy.ListFields(fpath)]
check("Plot_ID" in fnames and "Rep" in fnames, "plot attributes transferred (%s)" % ",".join(fnames))
# plots must not overlap field ground
with arcpy.da.SearchCursor(fpath, ["SHAPE@", "Source"]) as cur:
    fg = [g for g, s in cur if s == "Field"]
    pg = [g for g, s in cur if s == "Plot"]
ov = sum(1 for f in fg for p in pg if not f.disjoint(p) and f.intersect(p, 4).area > 0.01)
check(ov == 0, "no plot overlaps field ground (%d)" % ov)
# shapefile fuse
fshp = os.path.join(scratch, "fused.shp")
cp.run_fuse(field, plots, "Rate", fshp, standard_rate=1)
check(arcpy.Exists(fshp), "fuse to shapefile")

# --- ADD RANDOM NUMBERS ---------------------------------------------------------
arcpy.management.MakeFeatureLayer(plots, "plots_lyr")
arcpy.management.SelectLayerByAttribute("plots_lyr", "NEW_SELECTION", "Rep = 2")
w, fname = cp.run_add_random_numbers("plots_lyr", "Treatment", "Random permutation of 1 to N (each record unique)", seed=3)
check(w == 4 and fname == "Treatment", "permutation wrote 4 selected rows")
vals = sorted(v for (v,) in arcpy.da.SearchCursor(plots, ["Treatment"], "Rep = 2"))
check(vals == [1, 2, 3, 4], "permutation 1..4 (got %s)" % vals)
others = {v for (v,) in arcpy.da.SearchCursor(plots, ["Treatment"], "Rep <> 2")}
check(others == {None}, "unselected rows untouched")
arcpy.management.SelectLayerByAttribute("plots_lyr", "CLEAR_SELECTION")
w, fname = cp.run_add_random_numbers("plots_lyr", "Norm", "normal", mean=10, sd=2, seed=1)
vals = [v for (v,) in arcpy.da.SearchCursor(plots, ["Norm"])]
check(w == 12 and 5 < sum(vals) / 12 < 15, "normal values written to a new double field (mean %.2f)" % (sum(vals) / 12))
w, fname = cp.run_add_random_numbers(plots, "Treatment", "Random integers within a range", low=1, high=3, seed=2)
vals = {v for (v,) in arcpy.da.SearchCursor(plots, ["Treatment"])}
check(vals <= {1, 2, 3}, "integer range 1-3 (got %s)" % sorted(vals))
try:
    cp.run_add_random_numbers(plots, "Treatment", "Random decimals between 0 and 1")
    check(False, "decimals into an integer field must fail")
except arcpy.ExecuteError as e:
    check("integer field" in str(e), "decimals into an integer field refused")
w, _ = cp.run_add_random_numbers(plots, "Const", "Constant value", constant=7)
check({v for (v,) in arcpy.da.SearchCursor(plots, ["Const"])} == {7}, "constant 7 into new integer field")
# shapefile + dbf field name rule
w, fname = cp.run_add_random_numbers(shp, "VeryLongFieldName", "Random decimals within a range", low=2, high=4, seed=5)
check(len(fname) <= 10, "shapefile field name truncated (%s)" % fname)

print("\nFAILURES:", len(fails))
for f in fails:
    print("  -", f)
