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

"""Combinatorial no-crash sweep for the Random Sample Array Generator:
every VALID design combination (AOI kind x strata x allocation), the
full geometry grid (shape x orientation x containment x spacing mode x
boundary gap x circle options), geographic-CRS variants, and a
multipart polyline -- each run must complete without an exception and
every written array must be COMPLETE (exactly the designed number of
points, in both containment modes).

Run with ArcGIS Pro's python.
"""
import os
import shutil
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE),
                                "WildlifeTools", "Toolboxes"))

import random_arrays as ra  # noqa: E402
import arcpy  # noqa: E402

PASS = 0
FAIL = 0
RUN_NO = 0


class Msgs:
    def __init__(self):
        self.warn = []

    def addMessage(self, t):
        pass

    def addWarningMessage(self, t):
        self.warn.append(str(t))


TMP = tempfile.mkdtemp(prefix="ra_sweep_")
arcpy.env.overwriteOutput = True
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
GCS = arcpy.SpatialReference(4326)


def make_fc(name, shape, geoms, sr, attrs=None):
    fc = arcpy.management.CreateFeatureclass(
        GDB, name, shape, spatial_reference=sr).getOutput(0)
    fields = ["SHAPE@"]
    if attrs:
        arcpy.management.AddFields(fc, [["grp", "TEXT", "grp", 16,
                                         "", ""],
                                        ["pop", "LONG", "pop", "",
                                         "", ""]])
        fields += ["grp", "pop"]
    with arcpy.da.InsertCursor(fc, fields) as ins:
        for i, g in enumerate(geoms):
            row = [g] + (list(attrs[i]) if attrs else [])
            ins.insertRow(row)
    return fc


def poly(coords, sr):
    return arcpy.Polygon(arcpy.Array([arcpy.Point(*c)
                                      for c in coords]), sr)


def line(paths, sr):
    return arcpy.Polyline(
        arcpy.Array([arcpy.Array([arcpy.Point(*c) for c in p])
                     for p in paths]), sr)


# --- projected fixtures -----------------------------------------------
POLY = make_fc("p", "POLYGON",
               [poly([(0, 0), (0, 1000), (1000, 1000), (1000, 0),
                      (0, 0)], SR),
                poly([(2000, 0), (2000, 1000), (3000, 1000),
                      (3000, 0), (2000, 0)], SR)],
               SR, attrs=[("A", 2), ("B", 3)])
LINES = make_fc("l", "POLYLINE",
                [line([[(-2000, 0), (0, 0)]], SR),
                 line([[(0, 0), (2000, 500)]], SR),
                 line([[(0, 0), (2000, -500)]], SR),
                 line([[(0, 3000), (2000, 3000)],
                       [(2000, 3000), (3500, 4000)]], SR)],
                SR, attrs=[("A", 2), ("A", 1), ("B", 2), ("B", 1)])
EXTENT = arcpy.Extent(0, 0, 1000, 1000, None, None, None, None, SR)

arr = np.ones((60, 60), dtype=np.int32)
arr[:, 30:] = 2
arr[25:35, 25:35] = 9
ras = arcpy.NumPyArrayToRaster(arr, arcpy.Point(0, 0), 10, 10,
                               value_to_nodata=9)
arcpy.management.DefineProjection(ras, SR)
RAS = os.path.join(GDB, "cats")
ras.save(RAS)
arcpy.management.BuildRasterAttributeTable(RAS, "Overwrite")
arcpy.management.AddFields(RAS, [["grp", "TEXT", "grp", 16, "", ""],
                                 ["pop", "LONG", "pop", "", "", ""]])
with arcpy.da.UpdateCursor(RAS, ["Value", "grp", "pop"]) as cur:
    for row in cur:
        row[1] = "A" if row[0] == 1 else "B"
        row[2] = 2
        cur.updateRow(row)

# --- geographic fixtures ----------------------------------------------
d = 0.02
GPOLY = make_fc("gp", "POLYGON",
                [poly([(-111, 35), (-111, 35 + d), (-111 + d, 35 + d),
                       (-111 + d, 35), (-111, 35)], GCS)], GCS)
GLINES = make_fc("gl", "POLYLINE",
                 [line([[(-111, 35.05), (-111 + d, 35.05)]], GCS)],
                 GCS)
garr = np.ones((40, 40), dtype=np.int32)
garr[:, 20:] = 2
gras = arcpy.NumPyArrayToRaster(garr, arcpy.Point(-111, 34.9),
                                0.0005, 0.0005, value_to_nodata=0)
arcpy.management.DefineProjection(gras, GCS)
GRAS = os.path.join(GDB, "gcats")
gras.save(GRAS)
arcpy.management.BuildRasterAttributeTable(GRAS, "Overwrite")


def npp_of(kw):
    sh = ra.canon_shape(kw.get("array_shape"))
    if sh == ra.SHAPE_GRID:
        return int(kw.get("n_rows")) * int(kw.get("n_cols"))
    return int(kw.get("array_points"))


def run(tag, **kw):
    """One engine run: must not raise; every written array complete."""
    global PASS, FAIL, RUN_NO
    RUN_NO += 1
    out = os.path.join(GDB, "o{}".format(RUN_NO))
    m = Msgs()
    kw.setdefault("linear_units", "Meters")
    kw.setdefault("spacing_type", "Absolute spatial distance")
    kw.setdefault("random_seed", 1234)
    kw.setdefault("max_attempts", 300)
    try:
        ra.run_random_arrays(out, messages=m, **kw)
        counts = {}
        with arcpy.da.SearchCursor(out, ["Array_ID"]) as cur:
            for (aid,) in cur:
                counts[aid] = counts.get(aid, 0) + 1
        npp = npp_of(kw)
        bad = {a: c for a, c in counts.items() if c != npp}
        if bad:
            FAIL += 1
            print("  FAIL  {}  incomplete arrays {} (npp {})".format(
                tag, bad, npp))
        else:
            PASS += 1
    except Exception:
        FAIL += 1
        print("  FAIL  {}  RAISED:".format(tag))
        print("        " + traceback.format_exc(
            limit=3).replace("\n", "\n        "))


GRID = dict(array_shape="Grid", n_rows=2, n_cols=2, point_spacing=30)
TRI = dict(array_shape="Triangular", array_points=5, point_spacing=30)
CIRC = dict(array_shape="Circular", array_points=6, radius=40)
LINE1 = dict(array_shape="Along the polyline", array_points=3,
             point_spacing=50)

# ---------------------------------------------------------------------------
print("== design sweep: every valid strata x allocation, per AOI ==")
S_NONE = "No stratification"
DESIGNS_NONE = [dict(strata_method=S_NONE, alloc_method="Fixed total",
                     n_arrays=2),
                dict(strata_method=S_NONE, alloc_method="By density",
                     density=0.02, density_units="Hectare")]
STRAT_ALLOCS = [dict(alloc_method="Equal count", n_per_stratum=1),
                dict(alloc_method="Count proportional to stratum area",
                     n_arrays=2),
                dict(alloc_method="Count equal to a population field",
                     population_field="pop"),
                dict(alloc_method="Count proportional to a population "
                                  "field",
                     n_arrays=2, population_field="pop"),
                dict(alloc_method="By density", density=0.02,
                     density_units="Hectare")]

for dz in DESIGNS_NONE:
    run("polygon none {}".format(dz["alloc_method"]),
        boundary=POLY, must_fit=True, **GRID, **dz)
for sm in ("Stratify by individual polygon",
           "Stratify by strata ID field"):
    for al in STRAT_ALLOCS:
        kw = dict(al)
        kw["strata_method"] = sm
        if "ID field" in sm:
            kw["strata_field"] = "grp"
        run("polygon {} / {}".format(sm, al["alloc_method"]),
            boundary=POLY, must_fit=True, **GRID, **kw)

for dz in DESIGNS_NONE:
    kw = dict(dz)
    if kw["alloc_method"] == "By density":
        kw["density_units"] = "Kilometer"
        kw["density"] = 0.5
    run("polyline none {}".format(dz["alloc_method"]),
        boundary=LINES, must_fit=True, **LINE1, **kw)
for sm in ("Stratify by individual polyline",
           "Stratify by strata ID field"):
    for al in STRAT_ALLOCS:
        kw = dict(al)
        kw["strata_method"] = sm
        if "ID field" in sm:
            kw["strata_field"] = "grp"
        if kw["alloc_method"] == "By density":
            kw["density_units"] = "Kilometer"
            kw["density"] = 0.5
        run("polyline {} / {}".format(sm, al["alloc_method"]),
            boundary=LINES, must_fit=True, **LINE1, **kw)

for dz in DESIGNS_NONE:
    run("raster none {}".format(dz["alloc_method"]),
        boundary=RAS, must_fit=True, **GRID, **dz)
for sm in ("Stratify by raster category",
           "Stratify by contiguous raster region",
           "Stratify by strata ID field"):
    for al in STRAT_ALLOCS:
        kw = dict(al)
        kw["strata_method"] = sm
        if "ID field" in sm:
            kw["strata_field"] = "grp"
        if kw["alloc_method"] == "By density":
            kw["density"] = 0.12    # the per-stratum data areas are
            #                         small; a tiny density rounds to
            #                         zero everywhere (tested below)
        run("raster {} / {}".format(sm, al["alloc_method"]),
            boundary=RAS, must_fit=True, **GRID, **kw)

# a density too low for every stratum must raise the CLEAN
# zero-arrays error (a user-facing message, not a crash)
try:
    ra.run_random_arrays(
        os.path.join(GDB, "zz_zero"), boundary=RAS,
        strata_method="Stratify by raster category",
        alloc_method="By density", density=0.0001,
        density_units="Hectare", array_shape="Grid", n_rows=2,
        n_cols=2, point_spacing=30, must_fit=True,
        linear_units="Meters",
        spacing_type="Absolute spatial distance", messages=Msgs())
    print("  FAIL  zero-density: expected the clean zero-arrays error")
    FAIL += 1
except arcpy.ExecuteError as e:
    ok = "zero arrays" in str(e)
    PASS += ok
    FAIL += not ok
    if not ok:
        print("  FAIL  zero-density: wrong error: {}".format(e))
except Exception:
    FAIL += 1
    print("  FAIL  zero-density: RAISED a non-clean exception:")
    print("        " + traceback.format_exc(
        limit=3).replace("\n", "\n        "))
RUN_NO += 1

for dz in DESIGNS_NONE:
    if dz["alloc_method"] == "By density":
        continue                    # density needs an AOI measure
    run("extent none {}".format(dz["alloc_method"]),
        extent=EXTENT, must_fit=True, **GRID, **dz)

# ---------------------------------------------------------------------------
print("== geometry sweep: shape x orientation x fit x spacing x gap ==")
BASE = dict(boundary=POLY, strata_method=S_NONE,
            alloc_method="Fixed total", n_arrays=2)
for shape_kw, shname in ((GRID, "grid"), (TRI, "tri")):
    for om in ("Fixed orientation angle",
               "Random orientation for each array"):
        for fit in (True, False):
            gaps = (None, 50) if fit else (None,)
            for gap in gaps:
                for spc in (None, 120):
                    run("poly {} {} fit={} gap={} spc={}".format(
                            shname, om.split()[0], fit, gap, spc),
                        orientation_mode=om, orientation_angle=15,
                        must_fit=fit, boundary_gap=gap,
                        min_spacing=spc, **BASE, **shape_kw)
for inc in (True, False):
    for rs in (True, False):
        for fit in (True, False):
            run("poly circle inc={} rand={} fit={}".format(
                    inc, rs, fit),
                include_center=inc, randomize_start=rs, must_fit=fit,
                **BASE, **CIRC)

# ---------------------------------------------------------------------------
print("== polyline geometry: shapes x spacing types x align ==")
LBASE = dict(boundary=LINES, strata_method=S_NONE,
             alloc_method="Fixed total", n_arrays=2)
for shape_kw, shname in ((GRID, "grid"), (TRI, "tri"),
                         (CIRC, "circle"), (LINE1, "line1d")):
    for st in ("Absolute spatial distance",
               "Distance measured along the polyline",
               "Network distance through connected polylines"):
        for gap in (None, 40):
            run("line {} {} gap={}".format(shname, st.split()[0],
                                           gap),
                spacing_type=st, min_spacing=150, boundary_gap=gap,
                must_fit=True, **LBASE, **shape_kw)
for shape_kw, shname in ((GRID, "grid"), (TRI, "tri")):
    run("line {} align".format(shname),
        orientation_mode="Align each array to the polyline direction",
        must_fit=True, **LBASE, **shape_kw)
run("line 1d multipart stratify-individual",
    boundary=LINES, strata_method="Stratify by individual polyline",
    alloc_method="Equal count", n_per_stratum=1, must_fit=True,
    **LINE1)

# ---------------------------------------------------------------------------
print("== raster geometry ==")
RBASE = dict(boundary=RAS, strata_method=S_NONE,
             alloc_method="Fixed total", n_arrays=2)
for shape_kw, shname in ((GRID, "grid"), (TRI, "tri"),
                         (CIRC, "circle")):
    for fit in (True, False):
        gaps = (None, 30) if fit else (None,)
        for gap in gaps:
            run("raster {} fit={} gap={}".format(shname, fit, gap),
                must_fit=fit, boundary_gap=gap, **RBASE, **shape_kw)

# ---------------------------------------------------------------------------
print("== geographic coordinate systems ==")
GG = dict(array_shape="Grid", n_rows=2, n_cols=2, point_spacing=60)
GC = dict(array_shape="Circular", array_points=6, radius=80)
GT = dict(array_shape="Triangular", array_points=5, point_spacing=60)
GL = dict(array_shape="Along the polyline", array_points=3,
          point_spacing=100)
run("geographic polygon grid", boundary=GPOLY,
    strata_method=S_NONE, alloc_method="Fixed total", n_arrays=2,
    must_fit=True, **GG)
run("geographic polygon circle+spacing", boundary=GPOLY,
    strata_method=S_NONE, alloc_method="Fixed total", n_arrays=2,
    must_fit=True, min_spacing=200, include_center=True, **GC)
run("geographic polygon tri extend", boundary=GPOLY,
    strata_method=S_NONE, alloc_method="Fixed total", n_arrays=2,
    must_fit=False, **GT)
run("geographic line grid align", boundary=GLINES,
    strata_method=S_NONE, alloc_method="Fixed total", n_arrays=2,
    orientation_mode="Align each array to the polyline direction",
    must_fit=True, **GG)
run("geographic line 1-D", boundary=GLINES,
    strata_method=S_NONE, alloc_method="Fixed total", n_arrays=2,
    must_fit=True, **GL)
run("geographic raster grid", boundary=GRAS,
    strata_method=S_NONE, alloc_method="Fixed total", n_arrays=2,
    must_fit=True, **GG)
run("geographic raster circle extend", boundary=GRAS,
    strata_method=S_NONE, alloc_method="Fixed total", n_arrays=2,
    must_fit=False, **GC)
run("geographic extent grid",
    extent=arcpy.Extent(-111, 35, -111 + d, 35 + d, None, None,
                        None, None, GCS),
    strata_method=S_NONE, alloc_method="Fixed total", n_arrays=2,
    must_fit=True, **GG)

# ---------------------------------------------------------------------------
print()
print("{} runs: {} passed, {} failed".format(RUN_NO, PASS, FAIL))
try:
    arcpy.management.Delete(GDB)
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass
sys.exit(1 if FAIL else 0)
