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

"""Strip-engine equivalence for the habitat suitability (HSM) tools
converted on 2026-11-10 to row-strip processing: Habitat Suitability Model
(factors on the model's grid and on a different grid, additive and
geometric, saved reclassified factors), Combine Habitat Factors, Reclassify
Features in HSM, Critical Habitat Feature (a polyline feature, a 0/x
presence raster on the same grid, a presence raster on a coarser grid) and
Normalize Existing HSM. Every tool is cell by cell, or an exact distance
transform within a halo, so every output must be BIT-identical to the
frozen whole-raster engine (claude_tests/hsm_tools_whole_ref.py = the
shipped code); warnings and the counting messages must agree.

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
import raster_strips as rs                     # noqa: E402
import hsm_tools as new                        # noqa: E402
import hsm_tools_whole_ref as ref              # noqa: E402
from tpi_strip_equivalence_test import Msgs, read_raster, stats_of, metadata_of   # noqa: E402

passed = failed = 0
_COUNT_LINE = re.compile(r"^[\d,]+ cells|^The critical-feature raster|^Distances are measured|^Combining ")


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + label + (("  [" + detail + "]") if detail else ""), flush=True)
    else:
        failed += 1
        print("  FAIL  " + label + (("  [" + detail + "]") if detail else ""), flush=True)


def compare(label, a, b):
    A, B = read_raster(a), read_raster(b)
    na, nb = np.isnan(A), np.isnan(B)
    if A.shape != B.shape or not np.array_equal(na, nb):
        check(label + " NoData masks identical", False)
        return
    fa, fb = A[~na], B[~nb]
    ndiff = int(np.sum(fa != fb))
    check(label + " cells bit-identical", ndiff == 0,
          "{} of {} cells differ, max abs {:.3g}".format(ndiff, fa.size, float(np.max(np.abs(fa - fb))) if fa.size else 0))
    sa, sb = stats_of(a), stats_of(b)
    rel = max((0.0 if (np.isnan(x) and np.isnan(y)) else abs(x - y) / max(1.0, abs(x)))
              for x, y in zip(sa, sb)) if sa and sb else 0.0
    check(label + " statistics within 1e-6", rel <= 1e-6, "max rel {:.3g}".format(rel))
    check(label + " metadata identical", metadata_of(a) == metadata_of(b))


def run_pair(label, ref_fn, new_fn, outs_ref, outs_new_fmt, strips=(5, None)):
    """ref_fn(outs, msgs) / new_fn(outs, msgs) write the rasters named in
    ``outs`` (a dict label -> path); compared pairwise."""
    mr = Msgs()
    try:
        ref_fn(outs_ref, mr)
    except Exception:
        check(label + " frozen engine ran", False, traceback.format_exc()[-700:])
        return
    for rows in strips:
        tag = "strip={}".format(rows or "auto")
        rs.STRIP_ROWS_OVERRIDE = rows
        mn = Msgs()
        outs_new = {k: v.format("s{}".format(rows or "auto")) for k, v in outs_new_fmt.items()}
        try:
            new_fn(outs_new, mn)
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
        a = [s for s in mr.info if _COUNT_LINE.match(s)]
        b = [s for s in mn.info if _COUNT_LINE.match(s)]
        check("{} count messages identical [{}]".format(label, tag), sorted(a) == sorted(b),
              "" if sorted(a) == sorted(b) else "{} vs {}".format(a, b))
        for k in outs_ref:
            compare("{} {} [{}]".format(label, k, tag), outs_ref[k], outs_new[k])


def make_raster(path, arr, llx, lly, cell, sr, nodata=-9999.0):
    ras = arcpy.NumPyArrayToRaster(arr.astype(np.float32), arcpy.Point(llx, lly), cell, cell, nodata)
    arcpy.management.DefineProjection(ras, sr)
    ras.save(path)
    del ras
    return path


def make_polygons(path, sr, rects):
    arcpy.management.CreateFeatureclass(os.path.dirname(path), os.path.basename(path), "POLYGON",
                                        spatial_reference=sr)
    with arcpy.da.InsertCursor(path, ["SHAPE@"]) as ins:
        for x0, y0, x1, y1 in rects:
            ins.insertRow([arcpy.Polygon(arcpy.Array([arcpy.Point(x0, y0), arcpy.Point(x1, y0),
                                                      arcpy.Point(x1, y1), arcpy.Point(x0, y1),
                                                      arcpy.Point(x0, y0)]), sr)])
    return path


def make_lines(path, sr, lines):
    arcpy.management.CreateFeatureclass(os.path.dirname(path), os.path.basename(path), "POLYLINE",
                                        spatial_reference=sr)
    with arcpy.da.InsertCursor(path, ["SHAPE@"]) as ins:
        for pts in lines:
            ins.insertRow([arcpy.Polyline(arcpy.Array([arcpy.Point(x, y) for x, y in pts]), sr)])
    return path


def main():
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="striphsm_")
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = work
    gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
    sr = arcpy.SpatialReference(26912)
    H, W, cell = 160, 120, 30.0
    llx, lly = 400000.0, 3900000.0
    rng = np.random.default_rng(5)
    y, x = np.mgrid[0:H, 0:W]
    f1 = 500 + 400 * np.sin(x / 11.0) * np.cos(y / 9.0) + rng.normal(0, 60, (H, W))
    f1[20:35, 40:60] = -9999.0                                   # a NoData hole
    f1_path = make_raster(os.path.join(work, "f1.tif"), f1, llx, lly, cell, sr)
    # a second raw factor on a DIFFERENT grid (45 m cells, shifted) -> warp path
    H2, W2 = 110, 85
    y2, x2 = np.mgrid[0:H2, 0:W2]
    f2 = 300 + 300 * np.cos(x2 / 7.0) + 200 * np.sin(y2 / 13.0) + rng.normal(0, 40, (H2, W2))
    f2_path = make_raster(os.path.join(work, "f2.tif"), f2, llx - 100.0, lly - 80.0, 45.0, sr)
    # a reclassified-style factor with a few out-of-range cells
    f3 = np.clip(50 + 40 * np.sin(x / 5.0) + rng.normal(0, 10, (H, W)), -5, 110)
    f3_path = make_raster(os.path.join(work, "f3.tif"), f3, llx, lly, cell, sr)
    remap1 = os.path.join(work, "remap1.txt")
    with open(remap1, "w") as f:
        f.write("-1000 250 : 20\n250 500 : 50\n500 750 : 80\n750 5000 : 100\n")
    remap2 = os.path.join(work, "remap2.txt")
    with open(remap2, "w") as f:                                  # a gap: values 400-450 match nothing
        f.write("-1000 400 : 10\n450 700 : 60\n700 5000 : 90\n")
    polys = make_polygons(os.path.join(gdb, "polys"), sr,
                          [(llx + 600, lly + 900, llx + 1500, lly + 2100), (llx + 2400, lly + 300, llx + 3300, lly + 1200)])
    lines = make_lines(os.path.join(gdb, "lines"), sr,
                       [[(llx + 200, lly + 200), (llx + 3000, lly + 4000), (llx + 3500, lly + 4600)]])
    pres = np.zeros((H, W), dtype=np.float32)
    pres[(x - 60) ** 2 + (y - 80) ** 2 < 15 ** 2] = 3.0            # a disk, 0 elsewhere (0/x coded)
    pres[5:9, 5:9] = -9999.0
    pres_path = make_raster(os.path.join(work, "pres.tif"), pres, llx, lly, cell, sr)
    pres_c = np.full((60, 45), -9999.0, dtype=np.float32)
    yc, xc = np.mgrid[0:60, 0:45]
    pres_c[(xc - 15) ** 2 + (yc - 40) ** 2 < 6 ** 2] = 1.0           # presence on a coarser 75 m grid
    pres_c_path = make_raster(os.path.join(work, "pres_c.tif"), pres_c, llx + 10.0, lly + 20.0, 75.0, sr)

    def outp(name):
        return os.path.join(work, name + ".tif")

    # ---- Habitat Suitability Model: two factors, additive and geometric, saved reclassified factors
    for method in (new.ADDITIVE, new.GEOMETRIC):
        mtag = "add" if method == new.ADDITIVE else "geo"

        def fn(eng, outs, m, method=method):
            saved = []
            eng.run_hsm([(f1_path, remap1, 60.0), (f2_path, remap2, 40.0)], method, outs["hsm"],
                        save_reclassified=True, messages=m, saved_paths=saved)
            # the saved factors land beside the output under fixed names: move them to the labels
            for i, p in enumerate(saved):
                outs["saved{}".format(i)] = p

        outs_ref = {"hsm": outp("hsm_{}_ref".format(mtag))}
        outs_fmt = {"hsm": outp("hsm_{}_{{}}".format(mtag))}
        # saved factors are named from the factor rasters, so each engine run
        # must write into its own folder
        def fn_in_dir(eng, outs, m, method=method):
            d = os.path.join(work, os.path.splitext(os.path.basename(outs["hsm"]))[0])
            os.makedirs(d, exist_ok=True)
            outs["hsm"] = os.path.join(d, "hsm.tif")
            fn(eng, outs, m, method)
        run_pair("hsm {}".format(mtag), lambda o, m: fn_in_dir(ref, o, m), lambda o, m: fn_in_dir(new, o, m),
                 outs_ref, outs_fmt)

    # ---- Combine Habitat Factors (one factor with out-of-range cells)
    run_pair("combine add",
             lambda o, m: ref.run_combine([(f3_path, 50.0), (f1_path, 50.0)], new.ADDITIVE, o["out"], messages=m),
             lambda o, m: new.run_combine([(f3_path, 50.0), (f1_path, 50.0)], new.ADDITIVE, o["out"], messages=m),
             {"out": outp("comb_ref")}, {"out": outp("comb_{}")})
    run_pair("combine geo",
             lambda o, m: ref.run_combine([(f3_path, 30.0), (f1_path, 70.0)], new.GEOMETRIC, o["out"], messages=m),
             lambda o, m: new.run_combine([(f3_path, 30.0), (f1_path, 70.0)], new.GEOMETRIC, o["out"], messages=m),
             {"out": outp("combg_ref")}, {"out": outp("combg_{}")})

    # ---- Normalize Existing HSM
    run_pair("normalize clamp",
             lambda o, m: ref.run_normalize(f1_path, 0.0, 1000.0, o["out"], clamp=True, messages=m),
             lambda o, m: new.run_normalize(f1_path, 0.0, 1000.0, o["out"], clamp=True, messages=m),
             {"out": outp("norm_ref")}, {"out": outp("norm_{}")})
    run_pair("normalize inverted no clamp",
             lambda o, m: ref.run_normalize(f1_path, 1200.0, -100.0, o["out"], clamp=False, messages=m),
             lambda o, m: new.run_normalize(f1_path, 1200.0, -100.0, o["out"], clamp=False, messages=m),
             {"out": outp("normi_ref")}, {"out": outp("normi_{}")})

    # ---- Reclassify Features in HSM
    run_pair("reclassify features",
             lambda o, m: ref.run_reclassify_features(f3_path, polys, 7.0, o["out"], messages=m),
             lambda o, m: new.run_reclassify_features(f3_path, polys, 7.0, o["out"], messages=m),
             {"out": outp("recl_ref")}, {"out": outp("recl_{}")})

    # ---- Critical Habitat Feature: a polyline, a same-grid 0/x raster, a coarser raster
    bands = [(0.0, 100.0, 100.0), (100.0, 300.0, 50.0), (350.0, 600.0, 10.0)]
    for name, crit in (("crit lines", lines), ("crit raster", pres_path), ("crit coarse raster", pres_c_path)):
        run_pair(name,
                 lambda o, m, c=crit: ref.run_critical_feature(f3_path, c, bands, o["out"], messages=m),
                 lambda o, m, c=crit: new.run_critical_feature(f3_path, c, bands, o["out"], messages=m),
                 {"out": outp(name.replace(" ", "_") + "_ref")}, {"out": outp(name.replace(" ", "_") + "_{}")})

    print("\n{} passed, {} failed; {:.0f} s; work folder {}".format(passed, failed, time.time() - t0, work), flush=True)
    if "--keep" not in sys.argv and not failed:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
