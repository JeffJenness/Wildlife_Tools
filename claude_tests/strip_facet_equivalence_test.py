# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award Fund notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Strip-engine equivalence for the categorical / land facet tools converted
on 2026-11-10 to row-strip processing: Diversity Indices (every index, several
neighborhoods, the exclude-if-any-NoData rule, classification by an
attribute-table field, a linear-unit neighborhood on a geographic raster),
Land Facet Density (all facets, a subset, both denominator rules, linear
units) and Invert Raster (reciprocal and linear on a single band; the
multi-band density output with its scale record; a facet subset). Compared
cell by cell with the frozen whole-raster engines (claude_tests/
*_whole_ref.py = the shipped code), in strips of 5 rows and automatic.

Identity rules: direct-path convolutions and cell-by-cell arithmetic must be
BIT-identical (density values are stored integers, invert values are
float32 reciprocals); on the FFT path the float32 diversity index may differ
at rounding level (1e-5 of its range).

  --keep   keep the work folder

Run with ArcGIS Pro's Python.
"""
import os
import re
import sys
import tempfile
import time
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TB = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TB)
sys.path.insert(0, HERE)

import arcpy                                   # noqa: E402
import mahalanobis as _mh                      # noqa: E402
_mh._confirm = lambda *a, **k: True
import hillshade as new_hs                     # noqa: E402
import raster_strips as rs                     # noqa: E402
import diversity_tools as new_div              # noqa: E402
import diversity_tools_whole_ref as ref_div    # noqa: E402
import land_facet_density as new_den           # noqa: E402
import land_facet_density_whole_ref as ref_den # noqa: E402
import invert_raster as new_inv                # noqa: E402
import invert_raster_whole_ref as ref_inv      # noqa: E402
import land_facet_linkage as _lnk              # noqa: E402
from tpi_strip_equivalence_test import Msgs, read_raster, stats_of, metadata_of   # noqa: E402
from strip_focal_equivalence_test import kernel_cells   # noqa: E402

passed = failed = 0
_COUNT_LINE = re.compile(r"^[\d,]+ (of [\d,]+ )?cells|^[\d,]+ classes found|^[\d,]+ raster values|^[\d,]+ facets|"
                         r"^Neighborhood: |^  band |^Inverting ")


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + label + (("  [" + detail + "]") if detail else ""), flush=True)
    else:
        failed += 1
        print("  FAIL  " + label + (("  [" + detail + "]") if detail else ""), flush=True)


def read_bands(path):
    """All bands as a float64 array (bands, rows, cols), NoData -> NaN."""
    arr = arcpy.RasterToNumPyArray(path, nodata_to_value=-1).astype(np.float64)
    if arr.ndim == 2:
        arr = arr[None]
    arr[arr == -1] = np.nan
    return arr


def compare(label, a, b, exact, tol=1e-5, bands=False):
    A, B = (read_bands(a), read_bands(b)) if bands else (read_raster(a), read_raster(b))
    na, nb = np.isnan(A), np.isnan(B)
    if A.shape != B.shape or not np.array_equal(na, nb):
        check(label + " NoData masks identical", False,
              "shapes {} / {}".format(A.shape, B.shape) if A.shape != B.shape else
              "{} cells differ in validity".format(int(np.sum(na != nb))))
        return
    fa, fb = A[~na], B[~nb]
    ndiff = int(np.sum(fa != fb))
    scale = max(1.0, float(np.max(np.abs(fa)))) if fa.size else 1.0
    d = float(np.max(np.abs(fa - fb))) / scale if fa.size else 0.0
    if exact:
        check(label + " cells bit-identical", ndiff == 0, "{} of {} cells differ, max rel {:.3g}".format(ndiff, fa.size, d))
    elif bands:
        # stored integers (proportion x 10,000): a rounding-level difference
        # in the convolution can flip a value by one unit at a boundary
        dmax = float(np.max(np.abs(fa - fb))) if fa.size else 0.0
        ok = dmax <= 1.0 and ndiff <= max(1, int(0.001 * fa.size))
        check(label + " stored values within one unit in at most 0.1% of cells (FFT path)", ok,
              "{} of {} cells differ, max {:g} units".format(ndiff, fa.size, dmax))
    else:
        check(label + " cells within rounding (FFT path)", d <= tol,
              "{} of {} cells differ, max rel {:.3g} (tolerance {:g})".format(ndiff, fa.size, d, tol))
    if not bands:
        sa, sb = stats_of(a), stats_of(b)
        rel = max((0.0 if (np.isnan(x) and np.isnan(y)) else abs(x - y) / max(1.0, abs(x)))
              for x, y in zip(sa, sb)) if sa and sb else 0.0
        stol = 1e-6 if exact else 1e-5
        check(label + " statistics within {:g}".format(stol), rel <= stol, "max rel {:.3g}".format(rel))
    check(label + " metadata identical", metadata_of(a) == metadata_of(b))


def run_pair(label, ref_fn, new_fn, out_ref, out_new_fmt, exact, bands=False, strips=(5, None)):
    mr = Msgs()
    try:
        ref_fn(out_ref, mr)
    except Exception:
        check(label + " frozen engine ran", False, traceback.format_exc()[-700:])
        return
    for rows in strips:
        tag = "strip={}".format(rows or "auto")
        rs.STRIP_ROWS_OVERRIDE = rows
        mn = Msgs()
        out_new = out_new_fmt.format("s{}".format(rows or "auto"))
        try:
            new_fn(out_new, mn)
        except Exception:
            rs.STRIP_ROWS_OVERRIDE = None
            check("{} strip engine ran [{}]".format(label, tag), False, traceback.format_exc()[-900:])
            continue
        rs.STRIP_ROWS_OVERRIDE = None
        check("{} strip note posted [{}]".format(label, tag),
              any(s.startswith("Processing in ") or s.startswith("Processing the raster as one strip")
                  for s in mn.info))
        check("{} same warnings [{}]".format(label, tag), sorted(mr.warn) == sorted(mn.warn),
              "" if sorted(mr.warn) == sorted(mn.warn) else "{} vs {}".format(mr.warn, mn.warn))
        a = sorted(s for s in mr.info if _COUNT_LINE.match(s))
        b = sorted(s for s in mn.info if _COUNT_LINE.match(s))
        check("{} count messages identical [{}]".format(label, tag), a == b,
              "" if a == b else "{} vs {}".format(a, b))
        compare("{} [{}]".format(label, tag), out_ref, out_new, exact, bands=bands)


def make_categorical(path, H, W, llx, lly, cell, sr, seed, n_classes=8, holes=True):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:H, 0:W]
    field = (np.sin(x / 9.0) + np.cos(y / 7.0) + rng.normal(0, 0.6, (H, W)))
    a = np.clip(np.floor((field + 2.5) / 5.0 * n_classes), 0, n_classes - 1).astype(np.int32) + 1
    if holes:
        a[10:25, 30:50] = -9999
        a[rng.random((H, W)) < 0.03] = -9999
    ras = arcpy.NumPyArrayToRaster(a, arcpy.Point(llx, lly), cell, cell, -9999)
    arcpy.management.DefineProjection(ras, sr)
    ras.save(path)
    del ras
    return path


def main():
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="stripfacet_")
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = work
    gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
    H, W = 150, 120
    rasters = [
        ("utm", make_categorical(os.path.join(work, "cat_utm.tif"), H, W, 400000.0, 3900000.0, 30.0,
                                 arcpy.SpatialReference(26912), 61), 300.0, os.path.join(work, "out_utm")),
        ("geo", make_categorical(os.path.join(gdb, "cat_geo"), H, W, -112.0, 64.0, 0.01,
                                 arcpy.SpatialReference(4326), 62), 2000.0, gdb),
    ]
    for tag, cat, lin_r, outdir in rasters:
        if not outdir.lower().endswith(".gdb"):
            os.makedirs(outdir)
        print("\n--- {} categorical raster: {} ---".format(tag, cat), flush=True)

        def out(name):
            if outdir.lower().endswith(".gdb"):
                return os.path.join(outdir, "{}_{}".format(tag, name))
            return os.path.join(outdir, "{}_{}.tif".format(tag, name))

        # an attribute table with a grouping field (classes 1-8 -> 3 groups)
        arcpy.management.BuildRasterAttributeTable(cat, "Overwrite")
        arcpy.management.AddField(cat, "Grp", "TEXT", field_length=8)
        with arcpy.da.UpdateCursor(cat, ["Value", "Grp"]) as cur:
            for row in cur:
                row[1] = ["low", "mid", "high"][min(int(row[0]) - 1, 7) // 3]
                cur.updateRow(row)

        # ---- Diversity Indices
        div_cases = [
            ("div_eff1_c3", dict(index="EFFECTIVE", q=1.0, neighborhood="Circle", radius=3.0)),
            ("div_eff0_c3", dict(index="EFFECTIVE", q=0.0, neighborhood="Circle", radius=3.0)),
            ("div_eff2_c3", dict(index="EFFECTIVE", q=2.0, neighborhood="Circle", radius=3.0)),
            ("div_effhalf_c3", dict(index="EFFECTIVE", q=0.5, neighborhood="Circle", radius=3.0)),
            ("div_shannon_rect", dict(index="SHANNON", neighborhood="Rectangle", width=5.0, height=3.0)),
            ("div_gini_ann", dict(index="GINISIMPSON", neighborhood="Annulus", inner_radius=1.0, outer_radius=3.0)),
            ("div_simpson_wedge", dict(index="SIMPSON", neighborhood="Wedge", radius=3.0, start_angle=0.0, end_angle=90.0)),
            ("div_shannon_excl", dict(index="SHANNON", neighborhood="Circle", radius=2.0, exclude_if_any_null=True)),
            ("div_eff1_grp", dict(index="EFFECTIVE", q=1.0, neighborhood="Circle", radius=3.0, class_field="Grp")),
            ("div_eff1_lin", dict(index="EFFECTIVE", q=1.0, neighborhood="Circle", radius=lin_r, nbr_units="Meters")),
        ]
        for name, kw in div_cases:
            r = kw.get("radius", kw.get("outer_radius", max(kw.get("width", 0), kw.get("height", 0)) / 2.0))
            direct = kernel_cells(cat, r, kw.get("nbr_units", "Cells")) <= new_hs._NBR_DIRECT_MAX_CELLS
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_div.run_diversity(cat, o, messages=m, **kw),
                     lambda o, m, kw=kw: new_div.run_diversity(cat, o, messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"), direct)

        # ---- Land Facet Density (multi-band; integers: exact everywhere the
        # convolution is direct)
        den_cases = [
            ("den_all_c3", dict(neighborhood="Circle", radius=3.0)),
            ("den_sub_nn", dict(neighborhood="Circle", radius=2.5, facet_values=[2, 5, 7],
                                denominator=_lnk.DENOM_NON_NODATA)),
            ("den_rect", dict(neighborhood="Rectangle", width=5.0, height=5.0)),
            ("den_lin", dict(neighborhood="Circle", radius=lin_r, nbr_units="Meters", facet_values=[1, 3])),
        ]
        den_out = {}
        for name, kw in den_cases:
            r = kw.get("radius", kw.get("outer_radius", max(kw.get("width", 0), kw.get("height", 0)) / 2.0))
            direct = kernel_cells(cat, r, kw.get("nbr_units", "Cells")) <= new_hs._NBR_DIRECT_MAX_CELLS
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_den.run_density(cat, o, messages=m, **kw),
                     lambda o, m, kw=kw: new_den.run_density(cat, o, messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"), direct, bands=True)
            den_out[name] = out(name + "_ref")

        # ---- Invert Raster: a float surface (reciprocal), a 0-100 surface
        # (linear), the density raster's bands (scale record; facet subset)
        div_ref = out("div_eff1_c3_ref")
        run_pair("{} inv_recip".format(tag),
                 lambda o, m: ref_inv.run_invert(div_ref, out_raster=o, add_constant=0.1, messages=m),
                 lambda o, m: new_inv.run_invert(div_ref, out_raster=o, add_constant=0.1, messages=m),
                 out("inv_recip_ref"), out("inv_recip_{}"), True)
        run_pair("{} inv_linear".format(tag),
                 lambda o, m: ref_inv.run_invert(div_ref, out_raster=o, method=new_inv.METHOD_LINEAR, messages=m),
                 lambda o, m: new_inv.run_invert(div_ref, out_raster=o, method=new_inv.METHOD_LINEAR, messages=m),
                 out("inv_linear_ref"), out("inv_linear_{}"), True)
        if not outdir.lower().endswith(".gdb"):
            # multi-band: each band to its own raster in a workspace folder
            for vname, fv in (("inv_bands", None), ("inv_bands_sub", [2, 5])):
                def fn(eng, o, m, fv=fv):
                    os.makedirs(o, exist_ok=True)
                    return eng.run_invert(den_out["den_all_c3"], out_workspace=o, out_prefix="Inv",
                                          facet_values=fv, add_constant=0.1, messages=m)
                mr = Msgs()
                d_ref = os.path.join(outdir, vname + "_ref")
                paths_ref = fn(ref_inv, d_ref, mr)
                for rows in (5, None):
                    stag = "strip={}".format(rows or "auto")
                    rs.STRIP_ROWS_OVERRIDE = rows
                    mn = Msgs()
                    d_new = os.path.join(outdir, "{}_s{}".format(vname, rows or "auto"))
                    try:
                        paths_new = fn(new_inv, d_new, mn)
                    except Exception:
                        rs.STRIP_ROWS_OVERRIDE = None
                        check("{} {} strip engine ran [{}]".format(tag, vname, stag), False,
                              traceback.format_exc()[-900:])
                        continue
                    rs.STRIP_ROWS_OVERRIDE = None
                    check("{} {} same warnings [{}]".format(tag, vname, stag), sorted(mr.warn) == sorted(mn.warn),
                          "" if sorted(mr.warn) == sorted(mn.warn) else "{} vs {}".format(mr.warn, mn.warn))
                    check("{} {} same number of outputs [{}]".format(tag, vname, stag),
                          len(paths_ref) == len(paths_new), "{} vs {}".format(len(paths_ref), len(paths_new)))
                    for pa, pb in zip(paths_ref, paths_new):
                        compare("{} {} {} [{}]".format(tag, vname, os.path.basename(pa), stag), pa, pb, True)

    print("\n{} passed, {} failed; {:.0f} s; work folder {}".format(passed, failed, time.time() - t0, work), flush=True)
    if "--keep" not in sys.argv and not failed:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
