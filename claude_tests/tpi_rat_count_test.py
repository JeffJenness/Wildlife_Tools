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

"""Class raster attribute table with more than 2^31 - 1 cells in a class
(2026-11-10). The 21.2 billion-cell All_US Landform run computed every cell
and then died in _ClassWriter.finish: GDAL's integer RAT column is 32-bit and
SetValueAsInt overflowed on the count of the largest class. The writer now
uses a Real column when any count needs it, and the integer column (the
shipped behavior, byte-identical tables) otherwise.

Checks, on a 4 x 4 class raster written through _ClassWriter:
  1. ordinary counts: Count column is GFT_Integer and holds the counts;
  2. counts forced to 3,000,000,000 and 2^31 - 1 + 1: finish() succeeds,
     Count column is GFT_Real and holds the numbers exactly;
  3. a count of exactly 2^31 - 1 still uses the integer column.

Run with ArcGIS Pro's Python.
"""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TB = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TB)

from osgeo import gdal                         # noqa: E402
import tpi_tools as tp                         # noqa: E402

gdal.UseExceptions()
passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + label + (("  [" + detail + "]") if detail else ""))
    else:
        failed += 1
        print("  FAIL  " + label + (("  [" + detail + "]") if detail else ""))


CLASSES = [{"value": 1, "name": "low", "color": [0, 0, 255]},
           {"value": 2, "name": "mid", "color": [0, 255, 0]},
           {"value": 3, "name": "high", "color": [255, 0, 0]}]
GT = (500000.0, 30.0, 0.0, 4000000.0, 0.0, -30.0)
PROJ = 'PROJCS["WGS 84 / UTM zone 12N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",-111],PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",1]]'


def write_raster(path, forced_counts=None):
    w = tp._ClassWriter(gdal, path, GT, PROJ, 4, 4, CLASSES)
    arr = np.array([[1, 1, 2, 2], [1, 1, 2, 2], [3, 3, 3, 2], [3, 3, 3, 1]], dtype=np.uint8)
    w.write(0, arr[:2])
    w.write(2, arr[2:])
    if forced_counts:
        w.counts.update(forced_counts)
    w.finish()
    ds = gdal.Open(path)
    rat = ds.GetRasterBand(1).GetDefaultRAT()
    col = rat.GetColOfUsage(gdal.GFU_PixelCount)
    ctype = rat.GetTypeOfCol(col)
    rows = {}
    for r in range(rat.GetRowCount()):
        v = rat.GetValueAsInt(r, rat.GetColOfUsage(gdal.GFU_MinMax))
        rows[v] = rat.GetValueAsDouble(r, col) if ctype == gdal.GFT_Real else rat.GetValueAsInt(r, col)
    ds = None
    return ctype, rows


def main():
    work = tempfile.mkdtemp(prefix="tpirat_")
    ctype, rows = write_raster(os.path.join(work, "plain.tif"))
    check("ordinary counts: Count column is a 32-bit integer", ctype == gdal.GFT_Integer)
    check("ordinary counts: values right", rows == {1: 5, 2: 5, 3: 6}, str(rows))

    big = {1: 3000000000, 2: 2 ** 31, 3: 6}
    try:
        ctype, rows = write_raster(os.path.join(work, "big.tif"), big)
        check("counts above 2^31 - 1: finish() succeeds", True)
        check("counts above 2^31 - 1: Count column is Real", ctype == gdal.GFT_Real)
        check("counts above 2^31 - 1: values exact", rows == {1: 3000000000.0, 2: 2147483648.0, 3: 6.0}, str(rows))
    except Exception as e:                      # noqa: BLE001
        check("counts above 2^31 - 1: finish() succeeds", False, repr(e))

    edge = {1: 2 ** 31 - 1, 2: 5, 3: 6}
    ctype, rows = write_raster(os.path.join(work, "edge.tif"), edge)
    check("count of exactly 2^31 - 1: still the integer column", ctype == gdal.GFT_Integer)
    check("count of exactly 2^31 - 1: value right", rows.get(1) == 2 ** 31 - 1, str(rows))

    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
