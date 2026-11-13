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

"""Strip-engine memory test (2026-11-10): the row-strip tpi_tools.py must
hold its working memory near the strip budget instead of growing with the
raster. A 4000 x 4000 synthetic DEM (16 M cells) is run through run_tpi
(raw + SD outputs) and Landform run_classification with the budget forced
to 256 MiB; tracemalloc's peak (numpy allocations are traced) is compared
with the frozen whole-raster engine's peak on the same inputs, which the
2026-11-10 probes measured at about 130 bytes per cell (2 GiB here).

Run with ArcGIS Pro's Python:
  "C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" claude_tests/tpi_strip_memory_test.py
"""
import json
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
import tpi_tools as new                        # noqa: E402
import tpi_tools_whole_ref as ref              # noqa: E402

passed = failed = 0


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
        self.info, self.warn = [], []

    def addMessage(self, s):
        self.info.append(str(s))

    def addWarningMessage(self, s):
        self.warn.append(str(s))

    def addErrorMessage(self, s):
        self.warn.append("ERROR " + str(s))


def peak_of(fn):
    tracemalloc.start()
    t0 = time.time()
    fn()
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak, time.time() - t0


def main():
    work = tempfile.mkdtemp(prefix="tpimem_")
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = work
    n = 4000
    cells = n * n
    rng = np.random.default_rng(1)
    yy, xx = np.mgrid[0:n, 0:n]
    z = (1500.0 + 200.0 * np.sin(xx / 40.0) + 120.0 * np.cos(yy / 55.0)
         + rng.normal(0.0, 4.0, (n, n))).astype(np.float32)
    dem = os.path.join(work, "dem.tif")
    ras = arcpy.NumPyArrayToRaster(z, arcpy.Point(400000.0, 3900000.0), 30.0, 30.0, -9999)
    arcpy.management.DefineProjection(ras, arcpy.SpatialReference(26912))
    ras.save(dem)
    del ras, z, xx, yy
    system = json.load(open(os.path.join(TB, "classification_seeds",
                                         "weiss_10_class_landform.json"), encoding="utf-8"))

    def tpi(engine, tag):
        return lambda: engine.run_tpi(dem, out_raw=os.path.join(work, tag + "_raw.tif"),
                                      out_dev=os.path.join(work, tag + "_dev.tif"),
                                      outer=3.0, units="Cells", messages=Msgs())

    def landform(engine, tag):
        sources = {"tpi_small": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": 3.0,
                                 "inner": 0.0, "units": "Cells", "tpi_type": "dev"},
                   "tpi_large": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": 15.0,
                                 "inner": 0.0, "units": "Cells", "tpi_type": "dev"},
                   "slope": {"kind": "slope", "dem": dem, "elev_units": "Meters",
                             "slope_units": "Degrees"}}
        return lambda: engine.run_classification("landform", dem, os.path.join(work, tag + "_lf.tif"),
                                                 system, sources, messages=Msgs())

    print("4000 x 4000 DEM = {:,} cells".format(cells))
    budget = 256 * 2 ** 20
    old_budget = new.STRIP_BUDGET_BYTES
    new.STRIP_BUDGET_BYTES = budget
    try:
        for name, mk in (("run_tpi raw+dev r=3", tpi), ("Landform 3/15 cells", landform)):
            p_ref, t_ref = peak_of(mk(ref, "ref_" + name[:4]))
            p_new, t_new = peak_of(mk(new, "new_" + name[:4]))
            print("  {:<22} whole-raster peak {:6.0f} MB in {:5.1f} s | strips peak {:6.0f} MB in {:5.1f} s "
                  "({:.0f} vs {:.0f} bytes/cell)".format(
                      name, p_ref / 2 ** 20, t_ref, p_new / 2 ** 20, t_new,
                      p_ref / cells, p_new / cells))
            check(name + ": strip peak under 2x the 256 MiB budget", p_new <= 2 * budget,
                  "{:.0f} MB".format(p_new / 2 ** 20))
            check(name + ": strip peak at most a quarter of the whole-raster peak",
                  p_new * 4 <= p_ref, "{:.0f} vs {:.0f} MB".format(p_new / 2 ** 20, p_ref / 2 ** 20))
    finally:
        new.STRIP_BUDGET_BYTES = old_budget
    print("\n{} passed, {} failed".format(passed, failed))
    if failed == 0:
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
