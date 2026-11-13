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

"""Strip-engine memory test for the tools converted on 2026-11-10 (second
round): each must hold its working memory near the strip budget instead of
growing with the raster. A 4000 x 4000 synthetic DEM (16 M cells), an aspect
raster and a categorical raster on the same grid are run with the budget
forced to 256 MiB; tracemalloc's peak (numpy allocations are traced) is
compared with the frozen whole-raster engine's peak on the same inputs.

Run with ArcGIS Pro's Python:
  "C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" claude_tests/strip_memory_test.py
"""
import os
import shutil
import sys
import tempfile
import time
import tracemalloc

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TB = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TB)
sys.path.insert(0, HERE)

import arcpy                                   # noqa: E402
import mahalanobis as _mh                      # noqa: E402
_mh._confirm = lambda *a, **k: True
import raster_strips as rs                     # noqa: E402
import hillshade as new_hs                     # noqa: E402
import hillshade_whole_ref as ref_hs           # noqa: E402
import topographic_tools as new_tt             # noqa: E402
import topographic_tools_whole_ref as ref_tt   # noqa: E402
import aspect_tools as new_as                  # noqa: E402
import aspect_tools_whole_ref as ref_as        # noqa: E402
import diversity_tools as new_div              # noqa: E402
import diversity_tools_whole_ref as ref_div    # noqa: E402
import hsm_tools as new_hsm                    # noqa: E402
import hsm_tools_whole_ref as ref_hsm          # noqa: E402
from tpi_strip_equivalence_test import Msgs    # noqa: E402

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + label + (("  [" + detail + "]") if detail else ""), flush=True)
    else:
        failed += 1
        print("  FAIL  " + label + (("  [" + detail + "]") if detail else ""), flush=True)


def peak_of(fn):
    tracemalloc.start()
    t0 = time.time()
    fn()
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak, time.time() - t0


def main():
    work = tempfile.mkdtemp(prefix="stripmem_")
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = work
    n = 4000
    cells = n * n
    rng = np.random.default_rng(1)
    yy, xx = np.mgrid[0:n, 0:n]
    z = (1500.0 + 200.0 * np.sin(xx / 40.0) + 120.0 * np.cos(yy / 55.0)
         + rng.normal(0.0, 4.0, (n, n))).astype(np.float32)
    sr = arcpy.SpatialReference(26912)
    origin = arcpy.Point(400000.0, 3900000.0)
    dem = os.path.join(work, "dem.tif")
    ras = arcpy.NumPyArrayToRaster(z, origin, 30.0, 30.0, -9999)
    arcpy.management.DefineProjection(ras, sr)
    ras.save(dem)
    del ras, z
    asp = os.path.join(work, "aspect.tif")
    a = rng.uniform(0.0, 360.0, (n, n)).astype(np.float32)
    a[rng.random((n, n)) < 0.03] = -1.0
    ras = arcpy.NumPyArrayToRaster(a, origin, 30.0, 30.0, -9999)
    arcpy.management.DefineProjection(ras, sr)
    ras.save(asp)
    del ras, a
    cat = os.path.join(work, "cat.tif")
    c = (np.clip(np.floor((np.sin(xx / 50.0) + np.cos(yy / 70.0) + 2.0) / 4.0 * 6), 0, 5) + 1).astype(np.int32)
    ras = arcpy.NumPyArrayToRaster(c, origin, 30.0, 30.0, -9999)
    arcpy.management.DefineProjection(ras, sr)
    ras.save(cat)
    del ras, c, xx, yy

    def o(tag, name):
        return os.path.join(work, "{}_{}.tif".format(tag, name))

    cases = [
        ("VRM r=3 cells", lambda eng, tag: eng[0].run_vrm(dem, o(tag, "vrm"), radius=3.0, messages=Msgs()),
         (ref_tt, new_tt)),
        ("TRI Riley r=1.5", lambda eng, tag: eng[0].run_tri(dem, o(tag, "tri"), radius=1.5, messages=Msgs()),
         (ref_tt, new_tt)),
        ("Fractal TPM w=9", lambda eng, tag: eng[0].run_fractal_dimension(dem, o(tag, "fd"), window_size=9,
                                                                           messages=Msgs()),
         (ref_tt, new_tt)),
        ("Blur DEM r=5 cells", lambda eng, tag: eng[0].run_blurred_hillshade(dem, o(tag, "blur"), blur_radius=5.0,
                                                                              messages=Msgs()),
         (ref_hs, new_hs)),
        ("Shadows az315 alt25", lambda eng, tag: eng[0].run_enhanced_hillshade(dem, o(tag, "shadow"), altitude=25.0,
                                                                                model_shadows=True, messages=Msgs()),
         (ref_hs, new_hs)),
        ("Aspect mean r=3", lambda eng, tag: eng[0].run_aspect_focal(asp, o(tag, "asp"), "MEAN", radius=3.0,
                                                                      messages=Msgs()),
         (ref_as, new_as)),
        ("Diversity eff q=1 r=3", lambda eng, tag: eng[0].run_diversity(cat, o(tag, "div"), radius=3.0,
                                                                         messages=Msgs()),
         (ref_div, new_div)),
        ("Normalize HSM", lambda eng, tag: eng[0].run_normalize(dem, 1200.0, 1900.0, o(tag, "norm"), messages=Msgs()),
         (ref_hsm, new_hsm)),
    ]
    print("4000 x 4000 rasters = {:,} cells".format(cells))
    budget = 256 * 2 ** 20
    old_budget = rs.STRIP_BUDGET_BYTES
    rs.STRIP_BUDGET_BYTES = budget
    try:
        for name, fn, (ref, new) in cases:
            p_ref, t_ref = peak_of(lambda: fn((ref,), "ref"))
            p_new, t_new = peak_of(lambda: fn((new,), "new"))
            print("  {:<24} whole-raster peak {:6.0f} MB in {:5.1f} s | strips peak {:6.0f} MB in {:5.1f} s "
                  "({:.0f} vs {:.0f} bytes/cell)".format(
                      name, p_ref / 2 ** 20, t_ref, p_new / 2 ** 20, t_new, p_ref / cells, p_new / cells),
                  flush=True)
            check(name + ": strip peak under 2x the 256 MiB budget", p_new <= 2 * budget,
                  "{:.0f} MB".format(p_new / 2 ** 20))
            # the strips must not grow with the raster: a quarter of the
            # whole-raster peak where that peak is well above the budget (a
            # cheap cell-by-cell tool on 16 M cells is only ~2x the budget
            # whole, so there the budget bound above is the test)
            if p_ref >= 8 * budget:
                check(name + ": strip peak at most a quarter of the whole-raster peak",
                      p_new * 4 <= p_ref, "{:.0f} vs {:.0f} MB".format(p_new / 2 ** 20, p_ref / 2 ** 20))
    finally:
        rs.STRIP_BUDGET_BYTES = old_budget
    print("\n{} passed, {} failed".format(passed, failed))
    if failed == 0:
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
