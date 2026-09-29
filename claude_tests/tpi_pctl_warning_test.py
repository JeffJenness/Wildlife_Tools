# -*- coding: utf-8 -*-
# Copyright (C) 2026 Jeff Jenness, Jenness Enterprises
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See the LICENSE file for details.
"""Percentile-TPI reachability warnings (2026-09-29).

With the focal cell counted, a percentile TPI from a circle of n cells tops
out at 100 (n - 1) / n (88.9 for the 9 cells of a 1.5-cell radius), so the
Weiss percentile system's "Ridge > 90" class is unreachable below a 2-cell
radius. The classification tools must WARN (never change values); the TPI
tool must report the reachable maximum. Run with the ArcGIS Pro Python:

    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" tpi_pctl_warning_test.py
"""
import os
import shutil
import sys
import tempfile

import numpy as np
import arcpy

TB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                  "WildlifeTools", "Toolboxes")
sys.path.insert(0, os.path.abspath(TB))
import classification_store as cs   # noqa: E402
import tpi_tools as tt              # noqa: E402

passed = failed = 0


def check(label, ok):
    global passed, failed
    if ok:
        passed += 1
        print("  pass  " + label)
    else:
        failed += 1
        print("  FAIL  " + label)


class Msgs:
    def __init__(self):
        self.info, self.warn = [], []

    def addMessage(self, s):
        self.info.append(s)

    def addWarningMessage(self, s):
        self.warn.append(s)

    def addErrorMessage(self, s):
        self.warn.append("ERROR " + s)


# ---- pure helpers
print("reachable maximum")
for r, want_n, want in ((1.0, 5, 80.0), (1.5, 9, 800.0 / 9), (2.0, 13, 1200.0 / 13)):
    hi, n, _v = tt.pctl_reachable_max(np.full(4, r), r, np.zeros(4), 0.0)
    check("radius {:g}: n = {} max = {:.1f}".format(r, want_n, want),
          n == want_n and abs(hi - want) < 1e-9)
hi, n, _v = tt.pctl_reachable_max(np.full(4, 5.0), 5.0, np.full(4, 2.0), 2.0)
check("annulus reaches 100", hi == 100.0 and n is None)

seed = os.path.join(TB, "classification_seeds", "weiss_6_class_slope_position_pctl.json")
import json  # noqa: E402
weiss_pctl = json.load(open(seed, encoding="utf-8"))
w = tt.pctl_class_warnings(weiss_pctl, "tpi", 800.0 / 9)
check("1.5-cell circle warns about Ridge only",
      len(w) == 1 and "'Ridge'" in w[0] and "88.9" in w[0] and "radius of 2 cells" in w[0])
check("2-cell circle: no warning", tt.pctl_class_warnings(weiss_pctl, "tpi", 1200.0 / 13) == [])

# ---- engine runs on a synthetic DEM
work = tempfile.mkdtemp(prefix="pctlwarn_")
try:
    gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:40, 0:40]
    z = (1000 + 30 * np.sin(xx / 5.0) + 20 * np.cos(yy / 7.0)
         + rng.normal(0, 2, (40, 40))).astype(np.float32)
    ras = arcpy.NumPyArrayToRaster(z, arcpy.Point(400000, 3900000), 30, 30, -9999)
    arcpy.management.DefineProjection(ras, arcpy.SpatialReference(26912))
    dem = os.path.join(gdb, "dem")
    ras.save(dem)
    del ras

    def run(radius):
        m = Msgs()
        sources = {"tpi": {"kind": "tpi", "dem": dem, "shape": "Circle",
                           "outer": radius, "inner": 0.0, "units": "Cells",
                           "tpi_type": "pctl"},
                   "slope": {"kind": "slope", "dem": dem, "method": "Planar",
                             "elev_units": "Meters", "slope_units": "Degrees"}}
        tt.run_classification("slope_position", dem,
                              os.path.join(gdb, "cls_{:g}".format(radius).replace(".", "_")),
                              weiss_pctl, sources,
                              messages=m)
        return m

    m15 = run(1.5)
    check("classification, 1.5-cell circle: Ridge warning posted",
          any("Class 'Ridge' needs percentile TPI > 90" in s for s in m15.warn))
    m3 = run(3.0)
    check("classification, 3-cell circle: no percentile warning",
          not any("needs percentile TPI" in s for s in m3.warn))

    mt = Msgs()
    tt.run_tpi(dem, out_pctl=os.path.join(gdb, "pctl15"), outer=1.5, messages=mt)
    check("TPI tool reports the 88.9 maximum",
          any("highest possible value is 88.9" in s for s in mt.info))
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\n{} passed, {} failed".format(passed, failed))
sys.exit(1 if failed else 0)
