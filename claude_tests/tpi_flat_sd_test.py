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

"""Centered neighborhood variance (2026-11-10).

The standard-deviation TPI needs the neighborhood variance, computed as
E[z^2] - E[z]^2. On a DEM around 1,600 m both terms are about 2.6 million;
the FFT convolution sums z^2 with a relative error near 1e-14, i.e. about
5e-8 absolute in the variance, which is larger than the true variance of a
lake flat to a few millimeters. The standardized TPI there was arithmetic
noise that depended on the FFT block layout (the whole raster versus a
strip gave different values). focal_stats now shifts the elevations by the
block mean before squaring; mean, TPI and variance are translation
invariant, so nothing changes in exact arithmetic and the error shrinks by
the ratio of z^2 to (z - c)^2.

Checks, on a synthetic 900 x 900 DEM around 1,600 m with a 200 x 200 lake
flat to float32 spacing (1.2e-4 m), on the direct path (radius 3) and the
FFT path (radius 16):
  1. translation invariance: the same DEM shifted by +1,000 m gives the
     same mean (minus the shift) everywhere, and the same SD and
     standardized TPI on terrain, to 1e-9 relative; in the lake, where the
     SD is a few microns, the variance agrees to 1e-10 absolute;
  2. lake accuracy: the neighborhood variance at lake cells matches an
     exact per-cell computation to 1e-10, and a strip (block with a row
     offset) agrees with the whole grid to 1e-10;
  3. the raw-TPI path (no SD) is byte-identical to the uncentered sums.
Informational: the uncentered error at the same cells, for comparison.

Run with ArcGIS Pro's Python.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TB = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TB)

import tpi_tools as tp                         # noqa: E402

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + label + (("  [" + detail + "]") if detail else ""))
    else:
        failed += 1
        print("  FAIL  " + label + (("  [" + detail + "]") if detail else ""))


def rel(a, b):
    """Largest difference relative to the largest magnitude present (a
    cell-by-cell ratio would blow up wherever the value passes through 0)."""
    m = np.isfinite(a) & np.isfinite(b)
    if not m.any():
        return 0.0
    return float(np.max(np.abs(a[m] - b[m])) / max(float(np.max(np.abs(a[m]))), 1e-30))


def exact_var(z, k, i, j):
    """Population variance over the kernel footprint around (i, j), computed
    the stable way (deviations from the footprint's own mean)."""
    ny, nx = k.shape[0] // 2, k.shape[1] // 2
    win = z[i - ny:i + ny + 1, j - nx:j + nx + 1]
    vals = win[k > 0]
    return float(np.var(vals))


def main():
    rng = np.random.default_rng(11)
    n = 900
    y, x = np.mgrid[0:n, 0:n]
    z = (1600.0 + 350.0 * np.sin(x / 70.0) * np.cos(y / 55.0)
         + 30.0 * rng.standard_normal((n, n)).cumsum(axis=1) / 10.0
         + 20.0 * rng.standard_normal((n, n)))
    z = z.astype(np.float32).astype(np.float64)
    lake = np.float32(1616.361938)
    z[300:500, 400:600] = float(lake)
    # a few cells one float32 step higher, as real lakes have
    step = float(np.spacing(lake))
    pts = rng.integers(0, 200, size=(60, 2))
    z[300 + pts[:, 0], 400 + pts[:, 1]] += step
    valid = np.ones_like(z, dtype=bool)
    print("DEM %d x %d around 1,600 m; lake of 200 x 200 cells at %.6f m with 60 cells %.3g m higher"
          % (n, n, lake, step))

    for r in (3, 16):
        groups = tp._kernel_groups(np.full(n, float(r)), float(r), np.zeros(n), 0.0)
        k = groups[0][1]
        path = "direct" if k.size <= tp._hs._NBR_DIRECT_MAX_CELLS else "FFT"
        print("\n--- radius %d cells (%s path, kernel %dx%d) ---" % (r, path, k.shape[0], k.shape[1]))

        a = tp.focal_stats(z, valid, groups, want_sd=True)
        b = tp.focal_stats(z + 1000.0, valid, groups, want_sd=True)
        inner = np.zeros_like(valid)
        inner[r + 1:-r - 1, r + 1:-r - 1] = True
        # terrain = inner cells whose neighborhood holds no lake cell. In the
        # lake the SD is a few microns, so a relative test is meaningless
        # there; the lake is held to an absolute 1e-10 in the variance, the
        # arithmetic floor measured below.
        terrain = inner.copy()
        terrain[300 - r - 1:500 + r + 1, 400 - r - 1:600 + r + 1] = False
        lake_in = np.zeros_like(valid)
        lake_in[300 + r + 1:500 - r - 1, 400 + r + 1:600 - r - 1] = True
        d = rel(a["mean"][inner], (b["mean"] - 1000.0)[inner])
        check("translation invariance: mean (everywhere)", d <= 1e-9, "max rel diff %.3g" % d)
        d = rel(a["sd"][terrain], b["sd"][terrain])
        check("translation invariance: sd on terrain", d <= 1e-9, "max rel diff %.3g" % d)
        # the engine's rule: 0 where the SD is 0
        da = np.where(a["sd"] > 0, (z - a["mean"]) / np.where(a["sd"] > 0, a["sd"], 1.0), 0.0)
        db = np.where(b["sd"] > 0, (z + 1000.0 - b["mean"]) / np.where(b["sd"] > 0, b["sd"], 1.0), 0.0)
        d = rel(da[terrain], db[terrain])
        check("translation invariance: standardized TPI on terrain", d <= 1e-9, "max rel diff %.3g" % d)
        d = float(np.max(np.abs(a["var"][lake_in] - b["var"][lake_in])))
        check("translation invariance: lake variance within 1e-10 absolute", d <= 1e-10, "max abs diff %.3g" % d)

        # lake accuracy against the exact per-cell variance, and strip vs whole
        row0 = 283
        blk = tp.focal_stats(z[row0:row0 + 300], valid[row0:row0 + 300], groups, row0=row0, want_sd=True)
        cells = [(i, j) for i in range(330, 470, 23) for j in range(430, 570, 23)]
        worst_new = worst_strip = worst_old = 0.0
        num = np.where(valid, z, 0.0)
        den = valid.astype(np.float64)
        cd = tp._hs.nbr_conv(den, k)
        cn = tp._hs.nbr_conv(num, k)
        cs = tp._hs.nbr_conv(np.where(valid, z * z, 0.0), k)
        with np.errstate(invalid="ignore"):
            var_old = cs / cd - (cn / cd) ** 2
        floored = 0
        for i, j in cells:
            ev = exact_var(z, k, i, j)
            fl = tp._SD_FLOOR_ULPS * float(np.spacing(np.float32(abs(a["mean"][i, j]))))
            if ev < fl * fl:
                # below the quantization floor: the engine must report 0
                floored += 1
                worst_new = max(worst_new, abs(a["var"][i, j]))
            else:
                worst_new = max(worst_new, abs(a["var"][i, j] - ev))
            worst_strip = max(worst_strip, abs(blk["var"][i - row0, j] - a["var"][i, j]))
            worst_old = max(worst_old, abs(var_old[i, j] - ev))
        print("  lake cells: exact variance ~%.3g; uncentered error %.3g (informational); %d of %d below the floor"
              % (np.mean([exact_var(z, k, i, j) for i, j in cells]), worst_old, floored, len(cells)))
        check("lake variance within 1e-10 of the exact value, or 0 below the floor", worst_new <= 1e-10,
              "worst %.3g" % worst_new)
        check("strip and whole-grid lake variance agree within 1e-10", worst_strip <= 1e-10,
              "worst %.3g" % worst_strip)

        # quantization floor: the lake interior (flat to a float32 step or
        # two) has SD 0 and standardized TPI exactly 0; terrain is untouched
        check("lake interior SD is exactly 0 (quantization floor)", bool(np.all(a["sd"][lake_in] == 0.0)),
              "max lake SD %.3g" % float(np.max(a["sd"][lake_in])))
        check("lake interior standardized TPI is exactly 0", bool(np.all(da[lake_in] == 0.0)))
        check("terrain SD never floored", bool(np.all(a["sd"][terrain] > 0)),
              "min terrain SD %.3g" % float(np.min(a["sd"][terrain])))

        # the raw path is untouched
        raw = tp.focal_stats(z, valid, groups, want_sd=False)
        with np.errstate(invalid="ignore"):
            m_un = np.where(cd >= 0.5, cn / np.maximum(cd, 1e-9), np.nan)
        check("raw-TPI path mean byte-identical to the uncentered sums",
              np.array_equal(raw["mean"], m_un, equal_nan=True))

    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
