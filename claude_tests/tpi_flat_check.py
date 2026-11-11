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

"""Brute-force verifier for the quantization floor of the TPI engine
(tpi_tools.focal_stats, 2026-11-10).

The engine reports a standardized TPI of 0 where a neighborhood's standard
deviation is within tpi_tools._SD_FLOOR_ULPS float32 steps of its own mean
elevation. The frozen oracle (tpi_tools_whole_ref) instead divides by that
rounding-level SD, so the two legitimately disagree there. The test helpers
here recompute, cell by cell and straight from the elevations, the mean and
SD over exactly the kernel the engine uses for that row, and accept a
disputed cell only when the engine reported 0 AND the brute-force SD is
below the floor. Nothing is waved through.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TB = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
if TB not in sys.path:
    sys.path.insert(0, TB)

import tpi_tools as new                        # noqa: E402
import hillshade as _hs                        # noqa: E402


def _err_bound(D, sd, k):
    """k * eps * (D / sd)^2, capped at 1e-3 (the cap is reached for any
    D / sd above about 1e6, so the ratio never overflows)."""
    if not (sd > 0.0) or D > 1e6 * sd:
        return 1e-3
    return min(1e-3, k * float(np.finfo(np.float64).eps) * (D / sd) ** 2)


def floor_sd(mean):
    """The engine's SD floor for a neighborhood whose mean is ``mean``."""
    return new._SD_FLOOR_ULPS * float(np.spacing(np.float32(abs(float(mean)))))


def row_kernel(radii, r):
    """The engine's kernel for raster row r (constant radii: one exact
    kernel; varying radii: the whole-cell group the row belongs to)."""
    rxo, ryo, rxi, ryi = radii
    for rows, rx, ri in new._row_groups(rxo, rxi):
        if isinstance(rows, slice) or bool(rows[r]):
            return new._annulus_kernel(rx, ryo, ri, ryi)
    raise RuntimeError("row %d belongs to no kernel group" % r)


def nbhd_stats(z, valid, radii, r, c):
    """(mean, population SD, count) of the valid cells in the engine's
    neighborhood of (r, c), computed the stable way."""
    k = row_kernel(radii, r)
    ny, nx = k.shape[0] // 2, k.shape[1] // 2
    H, W = z.shape
    r0, r1 = max(0, r - ny), min(H, r + ny + 1)
    c0, c1 = max(0, c - nx), min(W, c + nx + 1)
    kk = k[r0 - (r - ny):k.shape[0] - ((r + ny + 1) - r1), c0 - (c - nx):k.shape[1] - ((c + nx + 1) - c1)]
    win = z[r0:r1, c0:c1]
    m = (kk > 0) & valid[r0:r1, c0:c1] & np.isfinite(win)
    vals = win[m].astype(np.float64)
    if vals.size == 0:
        return float("nan"), float("nan"), 0
    return float(vals.mean()), float(vals.std()), int(vals.size)


def is_floored_cell(z, valid, radii, r, c):
    """True when the engine must report SD 0 at (r, c): brute-force SD below
    the floor for that neighborhood's mean. Returns (flag, mean, sd, floor)."""
    mean, sd, n = nbhd_stats(z, valid, radii, r, c)
    if n == 0 or not np.isfinite(mean):
        return False, mean, sd, float("nan")
    fl = floor_sd(mean)
    return sd < fl, mean, sd, fl


def explain_mismatch(want, got, base, bvalid, radii, tol, max_cells=2000, seed=0):
    """Compare ``want`` (oracle) with ``got`` (engine) like the tests' close():
    NaN masks must agree and finite values must be within ``tol`` of the
    scale (max(1, max |want|)). Every cell beyond the tolerance must be
    explained by the brute-force statistics over the engine's kernel on
    ``base`` (the elevations, or the raw TPI for the TPI-over-TPI type):
    either the engine reported 0 and the brute-force SD is below the floor
    (a legitimately floored cell), or the engine's value matches the exact
    standardized value (focal minus brute mean, over brute SD) to 1e-9,
    i.e. the oracle is the one carrying rounding error, or, just above the
    floor, the engine's value is within the one-pass rounding-error bound
    32 eps (D / sd)^2 of the exact value (D = the raster's elevation range;
    capped at 1e-3). Up to ``max_cells`` disputed cells are checked (a
    random sample beyond that, stated in the result). Returns a dict: ok,
    n_mismatch, n_checked, n_floored, n_exact, n_near, worst_rel_near_floor,
    worst_bound_use (largest error / bound ratio), bad (first few unexplained
    cells as (r, c, want, got, sd, floor)), max_rel_elsewhere."""
    want = np.asarray(want, dtype=np.float64)
    got = np.asarray(got, dtype=np.float64)
    out = {"ok": False, "n_mismatch": 0, "n_checked": 0, "n_floored": 0, "n_exact": 0, "n_near": 0,
           "worst_rel_near_floor": 0.0, "worst_bound_use": 0.0, "bad": [], "max_rel_elsewhere": 0.0}
    bfin = np.asarray(base, dtype=np.float64)[np.asarray(bvalid, dtype=bool)]
    bfin = bfin[np.isfinite(bfin)]
    D = float(np.max(bfin) - np.min(bfin)) if bfin.size else 0.0
    if want.shape != got.shape:
        out["bad"] = ["shape {} vs {}".format(want.shape, got.shape)]
        return out
    na, nb = np.isnan(want), np.isnan(got)
    if not np.array_equal(na, nb):
        out["bad"] = ["NaN masks differ in {} cells".format(int(np.sum(na != nb)))]
        return out
    fin = ~na
    if not fin.any():
        out["ok"] = True
        return out
    scale = max(1.0, float(np.max(np.abs(want[fin]))))
    diff = np.zeros(want.shape)
    diff[fin] = np.abs(want[fin] - got[fin]) / scale
    mism = diff > tol
    out["n_mismatch"] = int(mism.sum())
    if fin.any():
        elsewhere = diff[fin & ~mism]
        out["max_rel_elsewhere"] = float(elsewhere.max()) if elsewhere.size else 0.0
    cells = np.argwhere(mism)
    if len(cells) > max_cells:
        rng = np.random.default_rng(seed)
        cells = cells[rng.choice(len(cells), size=max_cells, replace=False)]
    bad = []
    for r, c in cells:
        r, c = int(r), int(c)
        mean, sd, n = nbhd_stats(base, bvalid, radii, r, c)
        if n == 0 or not np.isfinite(mean):
            bad.append((r, c, float(want[r, c]), float(got[r, c]), sd, None))
            continue
        fl = floor_sd(mean)
        # the engine applies the floor to its own one-pass variance, which
        # near the floor carries the relative error modelled below, so a cell
        # within that margin of the floor may legitimately fall either way
        margin = _err_bound(D, sd, 16.0)
        if sd < fl * (1.0 - margin):
            if got[r, c] == 0.0:
                out["n_floored"] += 1
            else:
                bad.append((r, c, float(want[r, c]), float(got[r, c]), sd, fl))
            continue
        if sd < fl * (1.0 + margin) and got[r, c] == 0.0:
            out["n_floored"] += 1
            continue
        exact = (float(base[r, c]) - mean) / sd
        rel_err = abs(float(got[r, c]) - exact) / max(1.0, abs(exact))
        if rel_err <= tol:
            out["n_exact"] += 1
            continue
        # Just above the floor the one-pass variance (sum of squares minus
        # the squared mean, both of order D^2 where D is how far the block's
        # elevations stray from their mean) carries a rounding error of about
        # eps * D^2, i.e. a relative error of eps * (D / sd)^2 in the variance;
        # the standardized TPI inherits half of that. With D bounded by the
        # raster's elevation range and a safety factor, the engine's value
        # must sit within that bound (capped at 1e-3) of the exact value.
        bound = max(tol, _err_bound(D, sd, 32.0))
        if rel_err <= bound:
            out["n_near"] += 1
            out["worst_rel_near_floor"] = max(out["worst_rel_near_floor"], rel_err)
            out["worst_bound_use"] = max(out["worst_bound_use"], rel_err / bound)
        else:
            bad.append((r, c, float(want[r, c]), float(got[r, c]), sd, fl))
    out["n_checked"] = int(len(cells))
    out["bad"] = bad
    out["ok"] = not bad
    return out


class _NoMsgs(object):
    def addMessage(self, s): pass
    def addWarningMessage(self, s): pass
    def addErrorMessage(self, s): pass


def open_source(path):
    """The engine's own reader for a raster on disk (GeoTIFF or geodatabase)."""
    import arcpy
    import tempfile
    scratch = arcpy.env.scratchFolder or tempfile.gettempdir()
    return new._Source(path, scratch, _NoMsgs())


def read_dem(path):
    """(z float64 with NaN where NoData, valid mask) for a whole raster."""
    src = open_source(path)
    try:
        z, valid = src.read(0, src.height)
    finally:
        src.close()
    z = np.where(valid, z, np.nan)
    return z, valid


def radii_for_raster(path, outer, inner=0.0, units="Cells"):
    """The engine's per-row radii (cells) for a neighborhood on this raster,
    built exactly as run_tpi / run_classification build them."""
    import arcpy
    src = open_source(path)
    try:
        gt, width, height, proj = src.gt, src.width, src.height, src.proj
    finally:
        src.close()
    sr = arcpy.Describe(path).spatialReference
    ground = None
    if units != "Cells":
        ground = _hs.ground_geometry(gt, width, height, sr, proj)
    return new._cell_radii(gt, height, sr, outer, inner, units, width, proj, ground)
