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

"""Strip-engine equivalence for the focal tools converted on 2026-11-10 to
row-strip processing (raster_strips.py): the Vector Ruggedness Measure (with
and without Dilts local detrending) and the Blurred Hillshade (DEM blurred
before shading, hillshade blurred after shading; Average and Gaussian
kernels; radii in cells and in meters on projected, geographic and polar
DEMs). Each tool is run end to end through arcpy by the frozen whole-raster
engines (claude_tests/topographic_tools_whole_ref.py and
hillshade_whole_ref.py = the shipped code) and by the strip engines with a
forced 5-row strip and with the automatic strip height, and the outputs are
compared cell by cell.

Identity rules: where every convolution is on the direct path (kernel of at
most 225 cells) the outputs must be BIT-identical; on the FFT path a
continuous output may differ at rounding level (1e-6 of its range on the
float32 raster) and an integer hillshade level may flip by one in a few
cells. Statistics, the run messages that report counts, warnings and the
metadata (summary, description, tags) must agree.

  --quick        smaller DEMs
  --real <dem>   also run both engines on a real DEM (e.g. the Grand Canyon
                 window) with the default strip height and compare
  --keep         keep the work folder

Run with ArcGIS Pro's Python.
"""
import os
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
_mh._confirm = lambda *a, **k: True            # headless: no Yes/No dialog on the large-raster warning
import hillshade as new_hs                     # noqa: E402
import topographic_tools as new_tt             # noqa: E402
import raster_strips as rs                     # noqa: E402
import hillshade_whole_ref as ref_hs           # noqa: E402
import topographic_tools_whole_ref as ref_tt   # noqa: E402
import lsri as new_lsri                        # noqa: E402
import lsri_whole_ref as ref_lsri              # noqa: E402
import aspect_tools as new_as                  # noqa: E402
import aspect_tools_whole_ref as ref_as        # noqa: E402
from tpi_strip_equivalence_test import (        # noqa: E402
    make_dem, read_raster, stats_of, metadata_of, Msgs, same, close)

QUICK = "--quick" in sys.argv
passed = failed = 0
worst = {"fft": 0.0, "stats": 0.0}


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + label + (("  [" + detail + "]") if detail else ""), flush=True)
    else:
        failed += 1
        print("  FAIL  " + label + (("  [" + detail + "]") if detail else ""), flush=True)


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


def kernel_cells(dem, radius, units):
    """Cells in the largest kernel the engines build for this radius on this
    DEM (constant radius: the exact kernel; varying: the largest whole-cell
    group), to decide direct versus FFT convolution."""
    ds, copy = new_hs._open_source(dem, arcpy.env.scratchFolder, None, check_size=False)
    gt, w, h, proj = ds.GetGeoTransform(), ds.RasterXSize, ds.RasterYSize, ds.GetProjection()
    ds = None
    if copy:
        try:
            arcpy.management.Delete(copy)
        except Exception:
            pass
    sr = arcpy.Describe(dem).spatialReference
    rx_rows, ry, _note = new_tt._radius_row_cells(gt, h, sr, radius, units, w, proj)
    biggest = 0
    for k, _rows in new_hs._row_kernel_groups(rx_rows, ry, "Average"):
        biggest = max(biggest, int(k.size))
    return biggest


def compare_float(label, a, b, direct, tol=1e-6, stats_tol=None):
    A, B = read_raster(a), read_raster(b)
    na, nb = np.isnan(A), np.isnan(B)
    if A.shape != B.shape or not np.array_equal(na, nb):
        check(label + " NoData masks identical", False,
              "{} cells differ in validity".format(int(np.sum(na != nb)) if A.shape == B.shape else "shape"))
        return
    fa, fb = A[~na], B[~nb]
    ndiff = int(np.sum(fa != fb))
    scale = max(1.0, float(np.max(np.abs(fa)))) if fa.size else 1.0
    d = float(np.max(np.abs(fa - fb))) / scale if fa.size else 0.0
    if direct:
        check(label + " cells bit-identical", ndiff == 0, "{} cells differ, max rel {:.3g}".format(ndiff, d))
    else:
        worst["fft"] = max(worst["fft"], d)
        check(label + " cells within float32 rounding (FFT path)", d <= tol,
              "{} of {} cells differ, max rel {:.3g} (tolerance {:g})".format(ndiff, fa.size, d, tol))
    sa, sb = stats_of(a), stats_of(b)
    rel = max((0.0 if (np.isnan(x) and np.isnan(y)) else abs(x - y) / max(1.0, abs(x)))
              for x, y in zip(sa, sb)) if sa and sb else 0.0
    worst["stats"] = max(worst["stats"], rel)
    # a direction raster's mean and SD move by a whole 360 / n when one cell
    # near the 0/360 wrap flips under rounding, so direction cases pass a
    # looser statistics tolerance
    stol = (1e-6 if direct else max(1e-6, tol)) if stats_tol is None else stats_tol
    check(label + " statistics within {:g}".format(stol), rel <= stol, "max rel {:.3g}".format(rel))
    check(label + " metadata identical", metadata_of(a) == metadata_of(b))


def compare_levels(label, a, b, direct):
    """Integer hillshade levels (Int16 rasters; NoData read as a sentinel and
    turned into NaN)."""
    def read_levels(p):
        arr = arcpy.RasterToNumPyArray(p, nodata_to_value=-32768).astype(np.float64)
        arr[arr == -32768] = np.nan
        return arr
    A, B = read_levels(a), read_levels(b)
    na, nb = np.isnan(A), np.isnan(B)
    if A.shape != B.shape or not np.array_equal(na, nb):
        check(label + " NoData masks identical", False)
        return
    fa, fb = A[~na], B[~nb]
    diff = np.abs(fa - fb)
    ndiff = int(np.sum(diff > 0))
    if direct:
        check(label + " levels bit-identical", ndiff == 0, "{} cells differ, max {:g} levels".format(ndiff, diff.max() if diff.size else 0))
    else:
        ok = (diff.max() <= 1 if diff.size else True) and ndiff <= max(1, int(0.005 * fa.size))
        check(label + " levels within one level in at most 0.5% of cells (FFT path)", ok,
              "{} of {} cells differ, max {:g} levels".format(ndiff, fa.size, diff.max() if diff.size else 0))
    check(label + " metadata identical", metadata_of(a) == metadata_of(b))


import re                                      # noqa: E402

_COUNT_LINE = re.compile(r"^[\d,]+ of [\d,]+ cells|^Neighborhood: |^Aspect Transformation complete|^  Created |^Cast shadows[:,]|^Vector Ruggedness Measure \(|^Arc-Chord Ratio \(|"
                         r"^Local detrending|^TRI method|^FD method|^VRM radius|^Blur radius")


def make_aspect(path, dem, seed):
    """A synthetic aspect raster on the DEM's grid: uniform 0-360 directions,
    5% flat cells (-1), the DEM's NoData holes."""
    z = read_raster(dem)
    rng = np.random.default_rng(seed)
    a = rng.uniform(0.0, 360.0, size=z.shape).astype(np.float32)
    a[rng.random(z.shape) < 0.05] = -1.0
    a[np.isnan(z)] = -9999.0
    d = arcpy.Describe(dem)
    ras = arcpy.NumPyArrayToRaster(a, arcpy.Point(d.extent.XMin, d.extent.YMin),
                                   d.meanCellWidth, d.meanCellHeight, -9999)
    arcpy.management.DefineProjection(ras, d.spatialReference)
    ras.save(path)
    del ras
    return path


def compare_transforms(tag, asp, outdir, lin_r):
    """Aspect Transformation: every transformation at once, both engines,
    strips of 5 rows and automatic; reclassifications (int32) and
    trigonometric outputs (float64) must be bit-identical, the
    reclassifications' Class names too."""
    keys = [k for k, _l in new_as.ASPECT_TRANSFORMS]
    variants = [("asp_tr", dict()),
                ("asp_tr_math_nd", dict(input_convention=new_as.INPUT_CONVENTIONS[1],
                                        flat_handling=new_as.FLAT_TRANSFORM[1], target_bearing=200.0))]
    for vname, kw in variants:
        label = "{} {}".format(tag, vname)
        mr = Msgs()
        ref_map = {}
        try:
            ref_as.run_aspect_transform(asp, outdir, keys, base_name=vname + "_ref", messages=mr,
                                        out_map=ref_map, **kw)
        except Exception:
            check(label + " frozen engine ran", False, traceback.format_exc()[-600:])
            continue
        for rows in (5, None):
            stag = "strip={}".format(rows or "auto")
            rs.STRIP_ROWS_OVERRIDE = rows
            mn = Msgs()
            new_map = {}
            try:
                new_as.run_aspect_transform(asp, outdir, keys, base_name="{}_s{}".format(vname, rows or "auto"),
                                            messages=mn, out_map=new_map, **kw)
            except Exception:
                rs.STRIP_ROWS_OVERRIDE = None
                check("{} strip engine ran [{}]".format(label, stag), False, traceback.format_exc()[-800:])
                continue
            rs.STRIP_ROWS_OVERRIDE = None
            check("{} strip note posted [{}]".format(label, stag),
                  any(s.startswith("Processing in ") or s.startswith("Processing the raster as one strip")
                      for s in mn.info))
            check("{} same warnings [{}]".format(label, stag), sorted(mr.warn) == sorted(mn.warn),
                  "" if sorted(mr.warn) == sorted(mn.warn) else "{} vs {}".format(mr.warn, mn.warn))
            check("{} same outputs [{}]".format(label, stag), sorted(ref_map) == sorted(new_map),
                  "{} vs {}".format(sorted(ref_map), sorted(new_map)))
            for key in keys:
                if key not in ref_map or key not in new_map:
                    continue
                a, b = ref_map[key], new_map[key]
                if key in new_as._RECLASS_KEYS:
                    A = arcpy.RasterToNumPyArray(a, nodata_to_value=-9999).astype(np.int64)
                    B = arcpy.RasterToNumPyArray(b, nodata_to_value=-9999).astype(np.int64)
                    check("{} {} codes bit-identical [{}]".format(label, key, stag), np.array_equal(A, B),
                          "{} cells differ".format(int(np.sum(A != B))))
                    def classes(p):
                        try:
                            with arcpy.da.SearchCursor(p, ["Value", "Class"]) as cur:
                                return sorted(tuple(r) for r in cur)
                        except Exception as e:
                            return str(e)
                    check("{} {} class table identical [{}]".format(label, key, stag), classes(a) == classes(b))
                else:
                    compare_float("{} {} [{}]".format(label, key, stag), a, b, True)


def count_lines(msgs):
    """The run messages that report counts and settings (not the strip note
    or the phase labels): they must agree between the engines."""
    return [s for s in msgs.info if _COUNT_LINE.match(s)]


def run_pair(label, ref_fn, new_fn, out_ref, out_new_fmt, compare, direct, strips=(5, None)):
    """Run the frozen engine once and the strip engine per strip setting;
    compare outputs, warnings and the counted-cells message."""
    mr = Msgs()
    try:
        ref_fn(out_ref, mr)
    except Exception:
        check(label + " frozen engine ran", False, traceback.format_exc()[-600:])
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
            check("{} strip engine ran [{}]".format(label, tag), False, traceback.format_exc()[-800:])
            continue
        rs.STRIP_ROWS_OVERRIDE = None
        check("{} strip note posted [{}]".format(label, tag),
              any(s.startswith("Processing in ") or s.startswith("Processing the raster as one strip")
                  for s in mn.info))
        check("{} same warnings [{}]".format(label, tag), sorted(mr.warn) == sorted(mn.warn),
              "" if sorted(mr.warn) == sorted(mn.warn) else "{} vs {}".format(mr.warn, mn.warn))
        a, b = count_lines(mr), count_lines(mn)
        check("{} count and setting messages identical [{}]".format(label, tag), a == b,
              "" if a == b else "{} vs {}".format(a, b))
        compare("{} [{}]".format(label, tag), out_ref, out_new, direct)


def main():
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="stripfocal_")
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = work
    gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
    H, W = (120, 90) if QUICK else (220, 180)
    dems = [
        ("utm", make_dem(os.path.join(work, "dem_utm.tif"), H, W,
                         arcpy.SpatialReference(26912), 400000.0, 3900000.0, 30.0, 31),
         300.0, os.path.join(work, "out_utm")),
        ("geo", make_dem(os.path.join(gdb, "dem_geo"), H, W,
                         arcpy.SpatialReference(4326), -112.0, 64.0, 0.01, 32),
         2000.0, gdb),
        ("polar", make_dem(os.path.join(work, "dem_polar.tif"), H, W,
                           arcpy.SpatialReference(3031), -1500000.0, 1200000.0, 500.0, 33),
         5000.0, os.path.join(work, "out_polar")),
    ]
    for tag, dem, lin_r, outdir in dems:
        if not outdir.lower().endswith(".gdb"):
            os.makedirs(outdir)
        print("\n--- {} DEM: {} ---".format(tag, dem), flush=True)

        def out(name):
            if outdir.lower().endswith(".gdb"):
                return os.path.join(outdir, "{}_{}".format(tag, name))
            return os.path.join(outdir, "{}_{}.tif".format(tag, name))

        # ---- VRM
        vrm_cases = [
            ("vrm_r15c", dict(radius=1.5, radius_units="Cells")),
            ("vrm_r3c", dict(radius=3.0, radius_units="Cells")),
            ("vrm_r8c", dict(radius=8.0, radius_units="Cells")),
            ("vrm_lin", dict(radius=lin_r, radius_units="Meters")),
            ("vrm_detrend", dict(radius=1.5, radius_units="Cells", detrend=True,
                                 detrend_radius=1.5, detrend_units="Cells")),
            ("vrm_detrend_lin", dict(radius=3.0, radius_units="Cells", detrend=True,
                                     detrend_radius=lin_r, detrend_units="Meters")),
        ]
        for name, kw in vrm_cases:
            direct = kernel_cells(dem, kw["radius"], kw["radius_units"]) <= new_hs._NBR_DIRECT_MAX_CELLS
            if kw.get("detrend"):
                direct = direct and (kernel_cells(dem, kw["detrend_radius"], kw["detrend_units"])
                                     <= new_hs._NBR_DIRECT_MAX_CELLS)
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_tt.run_vrm(dem, o, elev_units="Meters", messages=m, **kw),
                     lambda o, m, kw=kw: new_tt.run_vrm(dem, o, elev_units="Meters", messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"), compare_float, direct)

        # ---- Arc-Chord Ratio (3 x 3 everywhere: always the direct path)
        acr_cases = [
            ("acr", dict()),
            ("acr_4cell", dict(corner_method=new_tt.CORNER_METHODS[1])),
            ("acr_detrend", dict(detrend=True, detrend_radius=1.5, detrend_units="Cells")),
            ("acr_detrend_lin", dict(detrend=True, detrend_radius=lin_r, detrend_units="Meters")),
        ]
        for name, kw in acr_cases:
            direct = True
            if kw.get("detrend"):
                direct = kernel_cells(dem, kw["detrend_radius"], kw["detrend_units"]) <= new_hs._NBR_DIRECT_MAX_CELLS
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_tt.run_acr(dem, o, elev_units="Meters", messages=m, **kw),
                     lambda o, m, kw=kw: new_tt.run_acr(dem, o, elev_units="Meters", messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"), compare_float, True if not kw.get("detrend") else direct)

        # ---- Terrain Ruggedness Index (explicit offset loops: always exact)
        tri_cases = [("tri_m{}_r15c".format(i), dict(method=new_tt.TRI_METHODS[i], radius=1.5, radius_units="Cells"))
                     for i in range(4)]
        tri_cases += [
            ("tri_riley_r4c", dict(method=new_tt.TRI_METHODS[0], radius=4.0, radius_units="Cells")),
            ("tri_slope_lin", dict(method=new_tt.TRI_METHODS[2], radius=lin_r, radius_units="Meters")),
            ("tri_sd_lin", dict(method=new_tt.TRI_METHODS[3], radius=lin_r, radius_units="Meters")),
        ]
        for name, kw in tri_cases:
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_tt.run_tri(dem, o, elev_units="Meters", messages=m, **kw),
                     lambda o, m, kw=kw: new_tt.run_tri(dem, o, elev_units="Meters", messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"), compare_float, True)

        # ---- Fractal Dimension (triangular prism and box counting sum tiles in
        # a fixed order: exact; variogram and madogram use integral images,
        # whose rounding depends on where the strip starts: rounding level)
        fd_cases = [("fd_m{}_w9".format(i), dict(method=new_tt.FD_METHODS[i], window_size=9), i in (0, 3))
                    for i in range(4)]
        fd_cases += [
            ("fd_tpm_w13", dict(method=new_tt.FD_METHODS[0], window_size=13), True),
            ("fd_vario_w13_lag4", dict(method=new_tt.FD_METHODS[1], window_size=13, max_lag=4), False),
        ]
        for name, kw, direct in fd_cases:
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_tt.run_fractal_dimension(dem, o, elev_units="Meters", messages=m, **kw),
                     lambda o, m, kw=kw: new_tt.run_fractal_dimension(dem, o, elev_units="Meters", messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"), compare_float, direct)

        # ---- Surface Area with Dilts detrending (the detrending is the
        # shared strip blur; the surface-area pass already ran in strips)
        for name, kw in (("sar_detrend", dict(detrend=True, detrend_radius=1.5, detrend_units="Cells")),
                         ("sar_detrend_lin", dict(detrend=True, detrend_radius=lin_r, detrend_units="Meters"))):
            direct = kernel_cells(dem, kw["detrend_radius"], kw["detrend_units"]) <= new_hs._NBR_DIRECT_MAX_CELLS
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_tt.run_surface_area(dem, out_ratio=o, elev_units="Meters",
                                                                 messages=m, **kw),
                     lambda o, m, kw=kw: new_tt.run_surface_area(dem, out_ratio=o, elev_units="Meters",
                                                                 messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"), compare_float, direct)

        # ---- Aspect tools, on a synthetic aspect raster (0-360, 5% flats
        # = -1, the DEM's NoData holes)
        asp = make_aspect(os.path.join(outdir, "{}_aspect".format(tag) + ("" if outdir.lower().endswith(".gdb") else ".tif")),
                          dem, 40 + len(tag))
        focal_cases = [("asp_mean_c3", dict(statistic="MEAN", neighborhood="Circle", radius=3.0))]
        for st in ("RESULTANT", "CIRCSD", "RAYLEIGH", "KAPPA", "PROPDEF"):
            focal_cases.append(("asp_{}_c3".format(st.lower()), dict(statistic=st, neighborhood="Circle", radius=3.0)))
        focal_cases += [
            ("asp_median_c2", dict(statistic="MEDIAN", neighborhood="Circle", radius=2.0)),
            ("asp_range_c2", dict(statistic="RANGE", neighborhood="Circle", radius=2.0)),
            ("asp_circvar_rect", dict(statistic="CIRCVAR", neighborhood="Rectangle", width=5.0, height=3.0)),
            ("asp_angdev_ann", dict(statistic="ANGDEV", neighborhood="Annulus", inner_radius=1.0, outer_radius=3.0)),
            ("asp_mean_wedge", dict(statistic="MEAN", neighborhood="Wedge", radius=3.0, start_angle=0.0, end_angle=90.0)),
            ("asp_mean_lin", dict(statistic="MEAN", neighborhood="Circle", radius=lin_r, nbr_units="Meters")),
            ("asp_res_nd", dict(statistic="RESULTANT", neighborhood="Circle", radius=3.0, ignore_nodata=False)),
            ("asp_mean_math", dict(statistic="MEAN", neighborhood="Circle", radius=3.0,
                                   input_convention=new_as.INPUT_CONVENTIONS[1])),
            ("asp_mean_flatnd", dict(statistic="MEAN", neighborhood="Circle", radius=3.0,
                                     flat_handling=new_as.FLAT_HANDLING[1])),
        ]
        for name, kw in focal_cases:
            r = kw.get("radius", kw.get("outer_radius", max(kw.get("width", 0), kw.get("height", 0)) / 2.0))
            direct = kernel_cells(dem, r, kw.get("nbr_units", "Cells")) <= new_hs._NBR_DIRECT_MAX_CELLS
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_as.run_aspect_focal(asp, o, messages=m, **kw),
                     lambda o, m, kw=kw: new_as.run_aspect_focal(asp, o, messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"),
                     lambda l, a, b, d: compare_float(l, a, b, d, tol=1e-5, stats_tol=1e-4), direct)
        compare_transforms(tag, asp, outdir, lin_r)

        # ---- LSRI on DEM (contours are local to a 2 x 2 block; the length
        # engine is rebuilt per strip, so the sums run in a different order:
        # rounding level)
        ci = 20.0 if tag != "polar" else 50.0
        lsri_cases = [
            ("lsri_r3c", dict(contour_interval=ci, radius=3.0, radius_units="Cells")),
            ("lsri_lin", dict(contour_interval=ci, radius=lin_r, radius_units="Meters", length_units="Feet")),
            ("lsri_zf", dict(contour_interval=ci, base_contour=5.0, z_factor=2.0, radius=lin_r, radius_units="Meters")),
        ]
        if not outdir.lower().endswith(".gdb"):
            # a coarser output grid (projected DEMs: cell size in map units)
            cs = {"utm": 60.0, "polar": 1000.0}[tag]
            lsri_cases.append(("lsri_coarse", dict(contour_interval=ci, radius=lin_r, radius_units="Meters",
                                                   out_cell_size=cs)))
        for name, kw in lsri_cases:
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_lsri.run_lsri_on_dem(dem, o, messages=m, **kw),
                     lambda o, m, kw=kw: new_lsri.run_lsri_on_dem(dem, o, messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"), compare_float, False)
        # saved contours: identical in one strip; cut at the strip edges in
        # several (same total length)
        cfc_ref = os.path.join(gdb, "{}_contours_ref".format(tag))
        cfc_new = os.path.join(gdb, "{}_contours_new".format(tag))
        mr, mn = Msgs(), Msgs()
        ref_lsri.run_lsri_on_dem(dem, out("lsri_cfc_ref"), ci, radius=3.0, radius_units="Cells",
                                 out_contours=cfc_ref, messages=mr)
        rs.STRIP_ROWS_OVERRIDE = 5
        try:
            new_lsri.run_lsri_on_dem(dem, out("lsri_cfc_s5"), ci, radius=3.0, radius_units="Cells",
                                     out_contours=cfc_new, messages=mn)
        finally:
            rs.STRIP_ROWS_OVERRIDE = None
        def total_length(fc):
            with arcpy.da.SearchCursor(fc, ["SHAPE@LENGTH"]) as cur:
                return sum((r[0] or 0.0) for r in cur)
        la, lb = total_length(cfc_ref), total_length(cfc_new)
        check("{} saved contours: same total length in strips of 5 rows".format(tag),
              abs(la - lb) <= 1e-6 * max(1.0, la), "{:.6f} vs {:.6f}".format(la, lb))
        compare_float("{} lsri with saved contours [strip=5]".format(tag), out("lsri_cfc_ref"),
                      out("lsri_cfc_s5"), False)

        # ---- Cast shadows (Enhanced and Multi-Sun hillshades): swept per
        # strip with a halo of relief / tan(altitude); the sweep is exact, so
        # the integer levels must be bit-identical
        shadow_cases = [
            ("shadow_315_25", lambda eng, o, m: eng.run_enhanced_hillshade(
                dem, o, azimuth=315.0, altitude=25.0, model_shadows=True, shadow_dim=20.0, messages=m)),
            ("shadow_90_45", lambda eng, o, m: eng.run_enhanced_hillshade(
                dem, o, azimuth=90.0, altitude=45.0, model_shadows=True, shadow_dim=50.0, messages=m)),
            ("shadow_200_10", lambda eng, o, m: eng.run_enhanced_hillshade(
                dem, o, azimuth=200.0, altitude=10.0, model_shadows=True, use_hypso=True, messages=m)),
            ("shadow_multi", lambda eng, o, m: eng.run_multisun_hillshade(
                dem, o, suns=((280.0, 35.0, 50.0), (350.0, 20.0, 30.0), (45.0, 60.0, 20.0)),
                model_shadows=True, messages=m)),
        ]
        for name, fn in shadow_cases:
            run_pair("{} {}".format(tag, name),
                     lambda o, m, fn=fn: fn(ref_hs, o, m), lambda o, m, fn=fn: fn(new_hs, o, m),
                     out(name + "_ref"), out(name + "_{}"), compare_levels, True)

        # ---- Blurred Hillshade
        T_DEM, T_HS = new_hs.BLUR_TARGETS[0], new_hs.BLUR_TARGETS[1]
        blur_cases = [
            ("blur_dem_avg_r3c", dict(blur_target=T_DEM, kernel="Average", blur_radius=3.0, radius_units="Cells")),
            ("blur_dem_gauss_lin", dict(blur_target=T_DEM, kernel="Gaussian", blur_radius=lin_r, radius_units="Meters")),
            ("blur_hs_avg_r25c", dict(blur_target=T_HS, kernel="Average", blur_radius=2.5, radius_units="Cells")),
            ("blur_hs_gauss_r8c", dict(blur_target=T_HS, kernel="Gaussian", blur_radius=8.0, radius_units="Cells")),
        ]
        for name, kw in blur_cases:
            direct = kernel_cells(dem, kw["blur_radius"], kw["radius_units"]) <= new_hs._NBR_DIRECT_MAX_CELLS
            run_pair("{} {}".format(tag, name),
                     lambda o, m, kw=kw: ref_hs.run_blurred_hillshade(dem, o, elev_units="Meters", messages=m, **kw),
                     lambda o, m, kw=kw: new_hs.run_blurred_hillshade(dem, o, elev_units="Meters", messages=m, **kw),
                     out(name + "_ref"), out(name + "_{}"), compare_levels, direct)

    real = arg("--real")
    if real:
        rtag = os.path.splitext(os.path.basename(real))[0]
        rout = os.path.join(work, "real")
        os.makedirs(rout)
        print("\n--- real DEM: {} ---".format(real), flush=True)
        for name, ref_fn, new_fn, cmp_fn, direct in (
            ("vrm_r15c",
             lambda o, m: ref_tt.run_vrm(real, o, radius=1.5, radius_units="Cells", messages=m),
             lambda o, m: new_tt.run_vrm(real, o, radius=1.5, radius_units="Cells", messages=m),
             compare_float, True),
            ("blur_dem_avg_r3c",
             lambda o, m: ref_hs.run_blurred_hillshade(real, o, blur_radius=3.0, radius_units="Cells", messages=m),
             lambda o, m: new_hs.run_blurred_hillshade(real, o, blur_radius=3.0, radius_units="Cells", messages=m),
             compare_levels, True),
            ("acr",
             lambda o, m: ref_tt.run_acr(real, o, messages=m),
             lambda o, m: new_tt.run_acr(real, o, messages=m),
             compare_float, True),
            ("tri_riley_r15c",
             lambda o, m: ref_tt.run_tri(real, o, method=new_tt.TRI_METHODS[0], radius=1.5, messages=m),
             lambda o, m: new_tt.run_tri(real, o, method=new_tt.TRI_METHODS[0], radius=1.5, messages=m),
             compare_float, True),
            ("fd_tpm_w9",
             lambda o, m: ref_tt.run_fractal_dimension(real, o, method=new_tt.FD_METHODS[0], window_size=9, messages=m),
             lambda o, m: new_tt.run_fractal_dimension(real, o, method=new_tt.FD_METHODS[0], window_size=9, messages=m),
             compare_float, True),
            ("shadow_315_25",
             lambda o, m: ref_hs.run_enhanced_hillshade(real, o, azimuth=315.0, altitude=25.0, model_shadows=True,
                                                        messages=m),
             lambda o, m: new_hs.run_enhanced_hillshade(real, o, azimuth=315.0, altitude=25.0, model_shadows=True,
                                                        messages=m),
             compare_levels, True),
        ):
            t1 = time.time()
            run_pair("{} {}".format(rtag, name), ref_fn, new_fn,
                     os.path.join(rout, "{}_{}_ref.tif".format(rtag, name)),
                     os.path.join(rout, "{}_{}_{{}}.tif".format(rtag, name)), cmp_fn, direct, strips=(None,))
            print("  ({:.0f} s)".format(time.time() - t1), flush=True)

    print("\n{} passed, {} failed; worst FFT-path relative difference {:.3g}, worst statistics "
          "relative difference {:.3g}; {:.0f} s; work folder {}".format(
              passed, failed, worst["fft"], worst["stats"], time.time() - t0, work), flush=True)
    if "--keep" not in sys.argv and not failed:
        try:
            import shutil
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
