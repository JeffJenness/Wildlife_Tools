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

"""Bundled classification-seed tests (2026-09-25).

Every seed in Toolboxes/classification_seeds must validate, and the Land
Facet Corridor Designer 3-class system must reproduce the manual's
boundaries (Canyons TPI <= -6; Slopes -6 < TPI <= 6; Ridges TPI > 6) through
the real classify() engine, with no slope input. Run with ArcGIS Pro's
python (numpy only; no arcpy needed)."""
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TBX = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TBX)

import classification_store as cs  # noqa: E402
import tpi_tools as tt  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    PASS += ok
    FAIL += (not ok)
    print("  {}  {}{}".format("PASS" if ok else "FAIL", name,
                              ("  [" + detail + "]") if detail and not ok else ""))


seeds = sorted(glob.glob(os.path.join(TBX, "classification_seeds", "*.json")))
check("ten bundled seeds", len(seeds) == 10, str(len(seeds)))
for f in seeds:
    s = json.load(open(f, encoding="utf-8"))
    try:
        cs.validate(s)
        ok = True
        err = ""
    except Exception as e:  # noqa: BLE001
        ok = False
        err = str(e)
    check("validates: " + os.path.basename(f), ok, err)

lf = json.load(open(os.path.join(TBX, "classification_seeds",
                                 "landfacet_3_class_topographic_position.json"),
                    encoding="utf-8"))
check("LF3 name", lf["name"] == "Land Facet Corridor Designer 3-Class Topographic Position")
check("LF3 is slope_position with a TPI input only",
      lf["family"] == "slope_position" and [i["role"] for i in lf["inputs"]] == ["tpi"])
tpi = np.array([[-100.0, -6.01, -6.0, -5.99, 0.0],
                [5.99, 6.0, 6.01, 100.0, np.nan]])
valid = ~np.isnan(tpi)
out = tt.classify(lf, {"tpi": tpi}, valid)
want = np.array([[1, 1, 1, 2, 2], [2, 2, 3, 3, tt.NODATA_OUT]])
check("LF3 boundaries: -6 -> Canyons, 6 -> Slopes, 6.01 -> Ridges; NoData stays NoData",
      np.array_equal(out, want), str(out.tolist()))
check("LF3 covers every finite TPI (no gap, no overlap)",
      int((out[valid] > 0).sum()) == int(valid.sum()))

fo = json.load(open(os.path.join(TBX, "classification_seeds",
                                 "forestry_slope_operability_percent.json"),
                    encoding="utf-8"))
check("forestry: general family, one raw raster input",
      fo["family"] == "general" and [i["role"] for i in fo["inputs"]] == ["raster"])
pct = np.array([[0.0, 20.0, 20.01, 30.0, 30.01, 40.0],
                [40.01, 50.0, 50.01, 60.0, 60.01, 150.0]])
outp = tt.classify(fo, {"raster": pct}, np.ones_like(pct, bool))
check("forestry boundaries: 20->1, 30->2, 40->3, 50->4, 60->5, >60->6",
      np.array_equal(outp, np.array([[1, 1, 2, 2, 3, 3], [4, 4, 5, 5, 6, 6]])),
      str(outp.tolist()))

print("\n{} passed, {} failed".format(PASS, FAIL))
