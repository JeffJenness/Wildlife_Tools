# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Strip-engine equivalence test (2026-11-10): the row-strip tpi_tools.py
against a frozen copy of the whole-raster engine it replaced
(tpi_tools_whole_ref.py = Toolboxes/tpi_tools.py as shipped in 60cf85b).

Level 1 (arrays): every TPI type, slope and the classification on synthetic
grids with several NoData layouts, radii on the direct-correlation path and
on the FFT path, circle and annulus, constant and per-row varying radii, at
forced strip heights of 1, 2, 7 and 64 rows and as one strip.
Level 2 (arcpy, end to end): run_tpi and run_classification of both engines
on a UTM GeoTIFF, a geographic file-geodatabase raster and a polar
stereographic GeoTIFF, on-the-fly and supplied-raster modes, every save
output, compared cell by cell plus attribute tables, metadata and
statistics.

Identity rules (Jeff, 2026-11-10): direct-correlation kernels (<= 225
cells), percentile counting, slope and classes must be BIT-identical; the
FFT path (larger kernels) and streamed whole-raster statistics may differ at
rounding level (reported; tolerance 1e-8 relative). Since the centered-SD
fix (Jeff, 2026-11-10, second commit) the SD-based types (dev, dev_tpi and
the saved 'dev' TPI rasters) may also differ at rounding level on the
direct path: the oracle squares raw elevations, the engine squares
centered ones. The engine also floors the SD of a neighborhood that is
flat to a few float32 steps (standardized TPI 0) where the oracle divides
by rounding noise; every such disputed cell is verified by brute force
(tpi_flat_check) and counted, never waved through.

Run with ArcGIS Pro's Python:
  "C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" claude_tests/tpi_strip_equivalence_test.py [--quick]
"""
import json
import math
import os
import shutil
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
import hillshade as _hs                        # noqa: E402
import tpi_tools as new                        # noqa: E402
import tpi_tools_whole_ref as ref              # noqa: E402
import tpi_flat_check as fc                    # noqa: E402

QUICK = "--quick" in sys.argv
passed = failed = 0
worst = {"fft": 0.0, "stats": 0.0}
floored = {"cells": 0, "cases": 0}


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + label + (("  [" + detail + "]") if detail else ""))
    else:
        failed += 1
        print("  FAIL  " + label + (("  [" + detail + "]") if detail else ""))


class Msgs(object):
    def __init__(self):
        self.info, self.warn, self.err = [], [], []

    def addMessage(self, s):
        self.info.append(str(s))

    def addWarningMessage(self, s):
        self.warn.append(str(s))

    def addErrorMessage(self, s):
        self.err.append(str(s))


# ----------------------------------------------------------------- helpers
def same(a, b):
    """Bit-identical, NaN == NaN."""
    a = np.asarray(a)
    b = np.asarray(b)
    if a.shape != b.shape:
        return False
    return bool(np.array_equal(a, b, equal_nan=True))


def close(a, b, rel=1e-9):
    """NaN masks identical and finite values within rel of the scale;
    returns (ok, max_rel_diff)."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        return False, float("inf")
    na, nb = np.isnan(a), np.isnan(b)
    if not np.array_equal(na, nb):
        return False, float("inf")
    fa, fb = a[~na], b[~nb]
    if fa.size == 0:
        return True, 0.0
    scale = max(1.0, float(np.max(np.abs(fa))))
    d = float(np.max(np.abs(fa - fb))) / scale
    return d <= rel, d


def kernel_cells(rxo, ryo):
    """Cells in the largest kernel a run uses (per-row radii are rounded to
    whole cells by _row_groups when they vary)."""
    rxo = np.asarray(rxo, dtype=np.float64)
    if float(np.ptp(rxo)) < 1e-9:
        rx = max(float(rxo[0]), 0.5)
    else:
        rx = float(np.max(np.maximum(np.rint(rxo), 1.0)))
    ry = max(float(ryo), 0.5)
    return (2 * int(math.ceil(rx)) + 1) * (2 * int(math.ceil(ry)) + 1)


def dem_grid(H, W, seed, holes):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    z = (1200.0 + 80.0 * np.sin(xx / 9.0) + 50.0 * np.cos(yy / 13.0)
         + 30.0 * np.sin((xx + 2 * yy) / 21.0) + rng.normal(0.0, 3.0, (H, W)))
    valid = np.ones((H, W), dtype=bool)
    if holes == "scattered":
        valid &= rng.random((H, W)) > 0.05
    elif holes == "block":
        valid[H // 3:H // 3 + max(1, H // 6), W // 4:W // 4 + max(1, W // 5)] = False
    elif holes == "border":
        b = min(3, H // 4, W // 4)
        if b:
            valid[:b, :] = False
            valid[-b:, :] = False
            valid[:, :b] = False
            valid[:, -b:] = False
    elif holes == "band":
        if H >= 12:
            valid[H // 2 - 2:H // 2 + 3, :] = False
    elif holes == "mixed":
        valid &= rng.random((H, W)) > 0.03
        valid[H // 4:H // 4 + max(1, H // 8), :] = False
        valid[0, :] = False
    z = np.where(valid, z, -9999.0)
    return z, valid


def tpi_strips(z, valid, rxo, ryo, rxi, ryi, tpi_type, rows, dem_stats=None):
    """The new engine's block path driven the way run_tpi / run_classification
    drive it, assembled into a whole grid."""
    H, W = z.shape
    halo = new._tpi_halo(tpi_type, ryo)
    groups = (new._kernel_groups(rxo, ryo, rxi, ryi)
              if tpi_type in ("raw", "dev", "dev_tpi") else None)
    out = np.full((H, W), np.nan)
    for y0, y1 in new.strip_ranges(H, rows):
        top = max(0, y0 - halo)
        bot = min(H, y1 + halo)
        zb, vb = z[top:bot], valid[top:bot]
        if tpi_type == "demstd":
            g = np.full(zb.shape, np.nan)
            mu, sd = dem_stats
            g[vb] = (zb[vb] - mu) / sd if sd > 0 else 0.0
        else:
            g = new.tpi_representation(np.where(vb, zb, 0.0), vb, rxo[top:bot], ryo,
                                       rxi[top:bot], ryi, tpi_type, None, row0=top,
                                       groups=groups)
        out[y0:y1] = g[y0 - top:y1 - top]
    return out


def slope_strips(z, valid, ground, zs, units, rows):
    H, W = z.shape
    out = np.full((H, W), np.nan)
    for y0, y1 in new.strip_ranges(H, rows):
        top = max(0, y0 - 1)
        bot = min(H, y1 + 1)
        g = new._slope_block(z[top:bot], valid[top:bot], ground, top, zs, units)
        out[y0:y1] = g[y0 - top:y1 - top]
    return out


def brute_pctl(z, valid, rxo, ryo, rxi, ryi):
    """Wilson & Gallant 'pctl' cell by cell: 100 x (valid neighbors strictly
    below the focal cell) / (valid cells in the neighborhood, focal included),
    with focal_percentile's inside test. NaN where invalid or empty."""
    H, W = z.shape
    out = np.full((H, W), np.nan)
    nyo = int(math.ceil(float(ryo)))
    for r in range(H):
        rx = max(float(rxo[r]), 1e-9)
        ri = max(float(rxi[r]), 1e-9)
        nxo = int(math.ceil(float(np.max(rxo))))
        for c in range(W):
            if not valid[r, c]:
                continue
            den = cnt = 0
            for dr in range(-nyo, nyo + 1):
                rr = r + dr
                if rr < 0 or rr >= H:
                    continue
                for dc in range(-nxo, nxo + 1):
                    cc = c + dc
                    if cc < 0 or cc >= W or not valid[rr, cc]:
                        continue
                    inside = (dc / rx) ** 2 + (dr / max(float(ryo), 1e-9)) ** 2 <= 1.0 + 1e-9
                    if inside and (float(rxi[r]) > 0 or ryi > 0):
                        inside = ((dc / ri) ** 2 + (dr / max(float(ryi), 1e-9)) ** 2) > 1.0 + 1e-9
                    if not inside:
                        continue
                    den += 1
                    if z[rr, cc] < z[r, c]:
                        cnt += 1
            if den > 0:
                out[r, c] = 100.0 * cnt / den
    return out


def load_seed(name):
    return json.load(open(os.path.join(TB, "classification_seeds", name), encoding="utf-8"))


# ================================================================== LEVEL 1
def level1():
    global passed
    print("\n=== Level 1: block primitives against the whole-grid engine ===")
    t0 = time.time()
    shapes = [(3, 3), (7, 5), (40, 33), (97, 131)] + ([] if QUICK else [(300, 200)])
    holes_list = ["none", "scattered", "block", "border", "band", "mixed"]
    radii = [1.0, 1.5, 3.0, 7.0, 8.0, 15.0] + ([] if QUICK else [25.0])
    strip_heights = [1, 2, 7, 64, None]
    types = ["raw", "dev", "pctl", "dev_tpi", "demstd"]
    n_cases = 0
    for (H, W) in shapes:
        for holes in holes_list:
            z, valid = dem_grid(H, W, seed=H * 7 + W, holes=holes)
            if not valid.any():
                continue
            zv = z[valid]
            dem_stats = (float(zv.mean()), float(zv.std()))
            for r in radii:
                if QUICK and r > 8 and H < 90:
                    continue
                for shape in ("Circle", "Annulus"):
                    inner = r / 2.0 if shape == "Annulus" else 0.0
                    for varying in (False, True):
                        if varying:
                            if H < 7:
                                continue
                            rxo = np.linspace(r * 0.8, r * 1.3, H)
                            rxi = rxo * (inner / r) if r else np.zeros(H)
                        else:
                            rxo = np.full(H, r)
                            rxi = np.full(H, inner)
                        ryo, ryi = r, inner
                        direct = kernel_cells(rxo, ryo) <= _hs._NBR_DIRECT_MAX_CELLS
                        for t in types:
                            if t == "pctl" and r >= 15 and H >= 90 and QUICK:
                                continue
                            if t == "demstd" and (r != radii[0] or shape != "Circle" or varying):
                                continue        # neighborhood-independent
                            z0 = np.where(valid, z, 0.0)
                            if t == "pctl" and int(math.ceil(float(np.max(rxo)))) > W:
                                # the shipped percentile code crashes when the
                                # radius exceeds the raster WIDTH (negative slice
                                # end): compare with a brute-force percentile
                                want = brute_pctl(z, valid, rxo, ryo, rxi, ryi)
                            else:
                                want = ref.tpi_representation(z0, valid, rxo, ryo, rxi, ryi, t, None)
                            for rows in strip_heights:
                                rr = rows or H
                                got = tpi_strips(z, valid, rxo, ryo, rxi, ryi, t, rr, dem_stats)
                                label = "{}x{} {:<9} r={:<4g} {:<7} {} {:<7} strip={}".format(
                                    H, W, holes, r, shape, "vary" if varying else "const", t, rows or "whole")
                                n_cases += 1
                                if (direct and t not in ("dev", "dev_tpi")) or t in ("pctl", "demstd"):
                                    ok = same(want, got)
                                    if not ok:
                                        _c, d = close(want, got)
                                        check(label + " bit-identical", False, "max rel diff {:.3g}".format(d))
                                    elif n_cases % 97 == 0:
                                        check(label + " bit-identical", True)
                                    else:
                                        passed += 1
                                else:
                                    ok, d = close(want, got, rel=1e-8)
                                    note = ""
                                    if ok:
                                        worst["fft"] = max(worst["fft"], d)
                                    elif t in ("dev", "dev_tpi"):
                                        # quantization floor: verify every disputed cell
                                        if t == "dev":
                                            base, bvalid = z0, valid
                                        else:
                                            base = ref.tpi_representation(z0, valid, rxo, ryo, rxi, ryi, "raw", None)
                                            bvalid = np.isfinite(base)
                                        ex = fc.explain_mismatch(want, got, base, bvalid, (rxo, ryo, rxi, ryi), 1e-8)
                                        ok = ex["ok"]
                                        if ok:
                                            floored["cells"] += ex["n_checked"]
                                            floored["cases"] += 1
                                            worst["fft"] = max(worst["fft"], ex["max_rel_elsewhere"])
                                            note = ("; {} disputed cells verified by brute force ({} floored, {} match exact "
                                                    "arithmetic, {} within the near-floor error bound, worst {:.2g} = {:.0%} of "
                                                    "the bound)").format(
                                                ex["n_checked"], ex["n_floored"], ex["n_exact"], ex["n_near"],
                                                ex["worst_rel_near_floor"], ex["worst_bound_use"])
                                        else:
                                            note = "; unexplained cells: {}".format(ex["bad"][:3])
                                    if not ok or n_cases % 97 == 0:
                                        check(label + " rounding-level path within 1e-8", ok,
                                              "max rel diff {:.3g}{}".format(d, note))
                                    else:
                                        passed += 1
    print("  level 1 TPI cases: {}  (worst FFT-path relative difference {:.3g}; {} quantization-flat "
          "cells floored in {} cases, each verified by brute force)".format(
              n_cases, worst["fft"], floored["cells"], floored["cases"]))

    # ---- slope: projected (UTM) and geographic (geodesic per-row spans)
    print("  slope")
    for (H, W) in [(7, 5), (97, 131)] + ([] if QUICK else [(300, 200)]):
        for holes in ("none", "mixed"):
            z, valid = dem_grid(H, W, seed=3, holes=holes)
            for label, sr, gt in (
                    ("UTM12N", arcpy.SpatialReference(26912), (400000.0, 30.0, 0.0, 3900000.0, 0.0, -30.0)),
                    ("WGS84@65N", arcpy.SpatialReference(4326), (-112.0, 0.01, 0.0, 66.0, 0.0, -0.01)),
                    ("WebMerc", arcpy.SpatialReference(3857), (-12400000.0, 50.0, 0.0, 4200000.0, 0.0, -50.0))):
                proj = sr.exportToString()
                for units in ("Degrees", "Percent rise"):
                    want = ref.slope_grid(z, valid, gt, sr, proj, "Meters", units)
                    ground, _g, zs = __import__("topographic_tools")._row_gradient_geometry(
                        gt, W, H, sr, proj, 1.0)
                    for rows in (1, 2, 7, 64, None):
                        got = slope_strips(z, valid, ground, zs, units, rows or H)
                        check("slope {}x{} {} {} {} strip={} bit-identical".format(
                            H, W, holes, label, units, rows or "whole"), same(want, got))

    # ---- classification of per-strip rows equals the whole classification
    print("  classify")
    for seed_name, roles in (("weiss_10_class_landform.json", ("tpi_small", "tpi_large", "slope")),
                             ("weiss_6_class_slope_position.json", ("tpi", "slope")),
                             ("basic_7_class_standardized.json", ("raster",))):
        system = load_seed(seed_name)
        H, W = 97, 131
        z, valid = dem_grid(H, W, seed=11, holes="mixed")
        rng = np.random.default_rng(5)
        resolved = {}
        for role in roles:
            g = rng.normal(0.0, 2.0, (H, W))
            if role == "slope":
                g = np.abs(g) * 10.0
            g[~valid] = np.nan
            g[rng.random((H, W)) < 0.02] = np.nan
            resolved[role] = g
        want = ref.classify(system, resolved, valid)
        for rows in (1, 7, 64, None):
            got = np.zeros((H, W), dtype=np.int32)
            for y0, y1 in new.strip_ranges(H, rows or H):
                got[y0:y1] = new.classify(system, {k: v[y0:y1] for k, v in resolved.items()},
                                          valid[y0:y1])
            check("classify {} strip={} identical".format(seed_name, rows or "whole"), same(want, got))

    # ---- the strip plan itself
    print("  strip plan")
    check("one strip when the raster fits the budget",
          new.strip_rows(400, 300, 15) == 400)
    r = new.strip_rows(30400, 30400, 15)
    check("Shay's raster: 15-cell halo -> 190 output rows under the 1 GiB budget (220 read)",
          r == 190 and (r + 30) * 30400 * new.STRIP_BYTES_PER_CELL <= new.STRIP_BUDGET_BYTES,
          "rows={}".format(r))
    check("huge halo: strip = twice the halo", new.strip_rows(30400, 30400, 500) == 1000,
          "rows={}".format(new.strip_rows(30400, 30400, 500)))
    check("tile alignment when there is room", new.strip_rows(100000, 1000, 15) == 6656,
          "rows={}".format(new.strip_rows(100000, 1000, 15)))
    check("strip ranges cover the raster exactly",
          new.strip_ranges(100, 7)[0] == (0, 7) and new.strip_ranges(100, 7)[-1] == (98, 100)
          and sum(b - a for a, b in new.strip_ranges(100, 7)) == 100)
    old = new.STRIP_ROWS_OVERRIDE
    new.STRIP_ROWS_OVERRIDE = 9
    check("override honored", new.strip_rows(100, 100, 50) == 9)
    new.STRIP_ROWS_OVERRIDE = old
    note = new.strip_note(30400, 30400, 128, 15)
    check("strip note names the layout", "strips of 128 rows with 15 halo rows on each side" in note, note)
    print("  level 1 done in {:.1f} s".format(time.time() - t0))


# ================================================================== LEVEL 2
def make_dem(path, H, W, sr, llx, lly, cell, seed, holes="mixed"):
    """Write a synthetic DEM (float32, NoData -9999) with arcpy so it carries
    a proper spatial reference; returns the path."""
    z, valid = dem_grid(H, W, seed, holes)
    ras = arcpy.NumPyArrayToRaster(np.where(valid, z, -9999.0).astype(np.float32),
                                   arcpy.Point(llx, lly), cell, cell, -9999)
    arcpy.management.DefineProjection(ras, sr)
    ras.save(path)
    del ras
    return path


def read_raster(path):
    """Float64 array with NaN NoData (any format arcpy reads)."""
    arr = arcpy.RasterToNumPyArray(path, nodata_to_value=np.nan).astype(np.float64)
    return arr


def read_int_raster(path):
    return arcpy.RasterToNumPyArray(path, nodata_to_value=0).astype(np.int32)


def rat_rows(path):
    try:
        fields = [f.name for f in arcpy.ListFields(path)]
        want = [f for f in ("Value", "Count", "Class_Name") if f in fields]
        with arcpy.da.SearchCursor(path, want) as cur:
            return sorted(tuple(r) for r in cur)
    except Exception as e:
        return "ERROR " + str(e)


def metadata_of(path):
    """Summary, description and tags (the title is the output's own file
    name, which carries the engine tag)."""
    try:
        md = arcpy.metadata.Metadata(path)
        return (md.summary or "", md.description or "", md.tags or "")
    except Exception as e:
        return "ERROR " + str(e)


def stats_of(path):
    out = []
    for p in ("MINIMUM", "MAXIMUM", "MEAN", "STD"):
        try:
            out.append(float(arcpy.management.GetRasterProperties(path, p)[0]))
        except Exception:
            out.append(float("nan"))
    return out


def compare_float_outputs(label, a_path, b_path, direct):
    a, b = read_raster(a_path), read_raster(b_path)
    if direct:
        ok = same(a, b)
        if not ok:
            _c, d = close(a, b)
            check(label + " cells bit-identical", False, "max rel diff {:.3g}, {} cells differ".format(
                d, int(np.sum(~np.isclose(a, b, rtol=0, atol=0, equal_nan=True)))))
        else:
            check(label + " cells bit-identical", True)
    else:
        ok, d = close(a, b, rel=1e-6)          # float32 outputs: one ulp is ~6e-8
        worst["fft"] = max(worst["fft"], d)
        ndiff = int(np.sum(~np.isclose(a, b, rtol=0, atol=0, equal_nan=True)))
        check(label + " cells within float32 rounding (FFT path)", ok,
              "max rel diff {:.3g}, {} of {} cells differ".format(d, ndiff, a.size))
    sa, sb = stats_of(a_path), stats_of(b_path)
    rel = max(abs(x - y) / max(1.0, abs(x)) for x, y in zip(sa, sb))
    worst["stats"] = max(worst["stats"], rel)
    check(label + " statistics within 1e-6", rel <= 1e-6, "max rel diff {:.3g}".format(rel))
    ma, mb = metadata_of(a_path), metadata_of(b_path)
    check(label + " metadata identical", ma == mb, "" if ma == mb else "{} vs {}".format(ma, mb))


def compare_class_outputs(label, a_path, b_path):
    a, b = read_int_raster(a_path), read_int_raster(b_path)
    ndiff = int(np.sum(a != b))
    check(label + " classes identical", ndiff == 0, "{} of {} cells differ".format(ndiff, a.size))
    ra, rb = rat_rows(a_path), rat_rows(b_path)
    check(label + " attribute table identical", ra == rb, "" if ra == rb else "{} vs {}".format(ra, rb))
    ma, mb = metadata_of(a_path), metadata_of(b_path)
    check(label + " metadata identical", ma == mb, "" if ma == mb else "{} vs {}".format(ma, mb))


def level2():
    print("\n=== Level 2: run_tpi / run_classification end to end (arcpy) ===")
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="tpistrip_")
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = work
    gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
    arcpy.env.workspace = gdb
    H, W = (120, 90) if QUICK else (220, 180)
    dems = [
        ("utm", make_dem(os.path.join(work, "dem_utm.tif"), H, W,
                         arcpy.SpatialReference(26912), 400000.0, 3900000.0, 30.0, 21),
         "Meters", (300.0, "Meters"), os.path.join(work, "out_utm")),
        ("geo", make_dem(os.path.join(gdb, "dem_geo"), H, W,
                         arcpy.SpatialReference(4326), -112.0, 64.0, 0.01, 22),
         "Meters", (2000.0, "Meters"), gdb),
        ("polar", make_dem(os.path.join(work, "dem_polar.tif"), H, W,
                           arcpy.SpatialReference(3031), -1500000.0, 1200000.0, 500.0, 23),
         "Meters", (5000.0, "Meters"), os.path.join(work, "out_polar")),
    ]
    for tag, dem, eu, (lin_r, lin_u), outdir in dems:
        if not outdir.lower().endswith(".gdb"):
            os.makedirs(outdir)
        print("\n--- {} DEM: {} ---".format(tag, dem))

        def out(engine_tag, name):
            if outdir.lower().endswith(".gdb"):
                return os.path.join(outdir, "{}_{}_{}".format(tag, engine_tag, name))
            return os.path.join(outdir, "{}_{}_{}.tif".format(tag, engine_tag, name))

        def run_both(name, fn, direct, strip_rows_list=(7, None)):
            """fn(engine, tag) -> dict label -> output path. Runs the frozen
            engine once and the new one per strip setting; compares."""
            mr = Msgs()
            try:
                want = fn(ref, "ref", mr)
            except Exception:
                check("{} {} frozen engine ran".format(tag, name), False, traceback.format_exc()[-800:])
                return None
            for rows in strip_rows_list:
                new.STRIP_ROWS_OVERRIDE = rows
                etag = "s{}".format(rows or "auto")
                mn = Msgs()
                try:
                    got = fn(new, etag, mn)
                except Exception:
                    check("{} {} strip engine ran (strip={})".format(tag, name, rows or "auto"),
                          False, traceback.format_exc()[-800:])
                    new.STRIP_ROWS_OVERRIDE = None
                    continue
                new.STRIP_ROWS_OVERRIDE = None
                sfx = " [strip={}]".format(rows or "auto")
                check("{} {} strip note posted{}".format(tag, name, sfx),
                      any(s.startswith("Processing in ") or s.startswith("Processing the raster as one strip")
                          for s in mn.info))
                check("{} {} same warnings{}".format(tag, name, sfx), sorted(mr.warn) == sorted(mn.warn),
                      "" if sorted(mr.warn) == sorted(mn.warn) else "{} vs {}".format(mr.warn, mn.warn))
                for key in want:
                    a, b = want[key], got[key]
                    if key.startswith("class:"):
                        compare_class_outputs("{} {} {}{}".format(tag, name, key, sfx), a, b)
                    else:
                        # bit-identity is required only for the outputs whose
                        # arithmetic is unchanged: raw TPI and slope on the direct
                        # path, percentile and DEM-scale z-score anywhere; the
                        # SD-based outputs use centered elevations
                        compare_float_outputs("{} {} {}{}".format(tag, name, key, sfx), a, b,
                                              direct=(direct and (key.endswith(":raw") or key.endswith(":slope")))
                                              or key.endswith(":pctl") or key.endswith(":demstd")
                                              or key.endswith(":slope"))
            return want

        # ---- TPI tool, radius 3 cells (direct) and a linear radius (FFT on
        # these cell sizes), circle and annulus, all five outputs
        for (r, u, shape, direct, name) in ((3.0, "Cells", "Circle", True, "tpi_r3"),
                                            (2.5, "Cells", "Annulus", True, "tpi_ann"),
                                            (lin_r, lin_u, "Circle", False, "tpi_lin")):
            def fn(engine, etag, m, r=r, u=u, shape=shape, name=name):
                keys = ["raw", "dev", "pctl", "demstd", "devtpi"]
                paths = {k: out(etag, name + "_" + k) for k in keys}
                engine.run_tpi(dem, out_raw=paths["raw"], out_dev=paths["dev"], out_pctl=paths["pctl"],
                               out_demstd=paths["demstd"], out_devtpi=paths["devtpi"], shape=shape,
                               outer=r, inner=(r / 2.0 if shape == "Annulus" else 0.0), units=u,
                               elev_units=eu, messages=m)
                return {"float:" + k: p for k, p in paths.items()}
            run_both(name, fn, direct)

        # ---- Slope Position on the fly (dev TPI + slope), with both saves
        sys6 = load_seed("weiss_6_class_slope_position.json")

        def fn_sp(engine, etag, m):
            paths = {"class:out": out(etag, "sp"), "float:tpi": out(etag, "sp_tpi"),
                     "float:slope": out(etag, "sp_slope")}
            sources = {"tpi": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": 3.0,
                               "inner": 0.0, "units": "Cells", "tpi_type": "dev",
                               "save": paths["float:tpi"]},
                       "slope": {"kind": "slope", "dem": dem, "elev_units": eu,
                                 "slope_units": "Degrees", "save": paths["float:slope"]}}
            engine.run_classification("slope_position", dem, paths["class:out"], sys6, sources,
                                      elev_units=eu, messages=m)
            return paths
        saved_sp = run_both("slopepos_onfly", fn_sp, direct=True)

        # ---- Slope Position, percentile TPI (annulus, cells) + percent slope
        sys6p = load_seed("weiss_6_class_slope_position_pctl.json")

        def fn_spp(engine, etag, m):
            paths = {"class:out": out(etag, "spp")}
            sources = {"tpi": {"kind": "tpi", "dem": dem, "shape": "Annulus", "outer": 3.0,
                               "inner": 1.0, "units": "Cells", "tpi_type": "pctl"},
                       "slope": {"kind": "slope", "dem": dem, "elev_units": eu,
                                 "slope_units": "Percent rise"}}
            engine.run_classification("slope_position", dem, paths["class:out"], sys6p, sources,
                                      elev_units=eu, messages=m)
            return paths
        run_both("slopepos_pctl", fn_spp, direct=True)

        # ---- Landform on the fly: small 3 cells (direct), large in linear
        # units (FFT), DEM-scale z-score large TPI variant too
        sys10 = load_seed("weiss_10_class_landform.json")

        def fn_lf(engine, etag, m):
            paths = {"class:out": out(etag, "lf"), "float:tpi_small": out(etag, "lf_ts"),
                     "float:tpi_large": out(etag, "lf_tl"), "float:slope": out(etag, "lf_sl")}
            sources = {"tpi_small": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": 3.0,
                                     "inner": 0.0, "units": "Cells", "tpi_type": "dev",
                                     "save": paths["float:tpi_small"]},
                       "tpi_large": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": lin_r,
                                     "inner": 0.0, "units": lin_u, "tpi_type": "dev",
                                     "save": paths["float:tpi_large"]},
                       "slope": {"kind": "slope", "dem": dem, "elev_units": eu,
                                 "slope_units": "Degrees", "save": paths["float:slope"]}}
            engine.run_classification("landform", dem, paths["class:out"], sys10, sources,
                                      elev_units=eu, messages=m)
            return paths
        saved_lf = run_both("landform_onfly", fn_lf, direct=False)

        def fn_lf2(engine, etag, m):
            paths = {"class:out": out(etag, "lf2")}
            sources = {"tpi_small": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": 2.0,
                                     "inner": 0.0, "units": "Cells", "tpi_type": "dev_tpi"},
                       "tpi_large": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": 6.0,
                                     "inner": 0.0, "units": "Cells", "tpi_type": "demstd"},
                       "slope": {"kind": "slope", "dem": dem, "elev_units": eu,
                                 "slope_units": "Degrees"}}
            engine.run_classification("landform", dem, paths["class:out"], sys10, sources,
                                      elev_units=eu, messages=m)
            return paths
        run_both("landform_devtpi_demstd", fn_lf2, direct=True)

        # ---- Landform from SUPPLIED rasters (the frozen run's saves feed both)
        if saved_lf:
            def fn_lfx(engine, etag, m):
                paths = {"class:out": out(etag, "lfx")}
                sources = {"tpi_small": {"kind": "tpi", "raster": saved_lf["float:tpi_small"]},
                           "tpi_large": {"kind": "tpi", "raster": saved_lf["float:tpi_large"]},
                           "slope": {"kind": "slope", "raster": saved_lf["float:slope"]}}
                engine.run_classification("landform", saved_lf["float:tpi_small"], paths["class:out"],
                                          sys10, sources, elev_units=eu, messages=m)
                return paths
            run_both("landform_existing", fn_lfx, direct=True)

        # ---- Slope Position from a supplied PERCENTILE raster (observed-max warning path)
        if saved_sp:
            def fn_spx(engine, etag, m):
                paths = {"class:out": out(etag, "spx")}
                sources = {"tpi": {"kind": "tpi", "raster": saved_sp["float:tpi"],
                                   "declared_type": "pctl"},
                           "slope": {"kind": "slope", "raster": saved_sp["float:slope"],
                                     "declared_units": "Degrees"}}
                engine.run_classification("slope_position", saved_sp["float:tpi"], paths["class:out"],
                                          sys6p, sources, elev_units=eu, messages=m)
                return paths
            run_both("slopepos_existing_pctl", fn_spx, direct=True)

        # ---- General Raster, standardized (whole-raster z-score of the DEM)
        sys7 = load_seed("basic_7_class_standardized.json")

        def fn_gen(engine, etag, m):
            paths = {"class:out": out(etag, "gen")}
            sources = {"raster": {"kind": "external", "raster": dem, "standardize": True}}
            engine.run_classification("general_raster", dem, paths["class:out"], sys7, sources,
                                      elev_units=eu, messages=m)
            return paths
        run_both("general_standardized", fn_gen, direct=False)

    print("  level 2 done in {:.1f} s; outputs in {}".format(time.time() - t0, work))
    return work


def main():
    t0 = time.time()
    if "--level2" not in sys.argv:
        level1()
    work = None
    if "--level1" not in sys.argv:
        work = level2()
    print("\n{} passed, {} failed; worst FFT-path relative difference {:.3g}, worst "
          "statistics relative difference {:.3g}; {:.0f} s".format(
              passed, failed, worst["fft"], worst["stats"], time.time() - t0))
    if work and failed == 0 and "--keep" not in sys.argv:
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
