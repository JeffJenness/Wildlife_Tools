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

"""Topographic Position Index (TPI) / Slope Position / Landform classification
apply engine for the Jenness Enterprises toolbox.

Ports Jeff Jenness's ArcView 3.x "TPI v.1.3a" extension and its corrected ArcMap
(VB6) rewrite. TPI is the difference between a cell's elevation and the mean
elevation of a neighborhood around it (Weiss 2001; Guisan et al. 1999). Slope
Position classifies one TPI (plus slope); Landform classifies two TPI scales
(small + large) plus slope; General classifies any single grid. The class rules
live in reusable JSON "classification systems" (see ``classification_store`` and
``docs/classification_system_schema.md``), authored in a separate builder.

Everything is computed internally in numpy -- focal statistics via
NoData-ignoring normalized convolution (like VRM / the blurred hillshade),
slope via the Horn gradient, and the classification via vectorized ``np.select``
(each class = an AND of per-input range conditions; lowest class value wins on
overlap). No Spatial Analyst, no Esri ``Con``. The raster is processed in ROW
STRIPS read with a halo (see the strip section), so memory is bounded by the
strip rather than the raster.

TPI variants (matching ``TPI_Code.bas``):
  * raw          TPI = DEM - FocalMean(DEM)
  * standardized (TPI - meanTPI) / sdTPI          [whole-raster stats]
  * sd           TPI / FocalSD(DEM)  (0 where FocalSD = 0)   [local]
"""

import os
import re
import math
import operator

import numpy as np

import arcpy
import mahalanobis as _mh
import hillshade as _hs
import topographic_tools as _tt
import classification_store as _store

NODATA_OUT = 0                       # class 0 = unclassified / outside data
NBHD_SHAPES = ["Circle", "Annulus"]
RADIUS_UNITS = _tt.RADIUS_UNITS

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt,
        ">=": operator.ge, "=": operator.eq, "!=": operator.ne}

_WEISS_CITATION = ("Weiss, A. 2001. Topographic Position and Landforms "
                   "Analysis. Poster presentation, ESRI User Conference, San "
                   "Diego, CA (http://www.jennessent.com/arcview/"
                   "TPI_Weiss_poster.htm).")
_WG_CITATION = ("Wilson, J. P., and J. C. Gallant. 2000. Terrain analysis: "
                "principles and applications. John Wiley & Sons.")
_TPI_CITATION = (_WEISS_CITATION + " See also Guisan, A., S. B. Weiss, and "
                 "A. D. Weiss. 1999. Plant Ecology 143:107-122 "
                 "(https://doi.org/10.1023/A:1009841519580); Jenness, J. 2006. "
                 "Topographic Position Index extension for ArcView 3.x "
                 "(http://www.jennessent.com/arcview/tpi.htm).")

# ---------------------------------------------------------------------------
#  TPI-type tag: stamped into a Simple-TPI output's metadata so the
#  classification tools can tell what kind of TPI a supplied raster holds and
#  warn about a mismatch. Types: raw (diff), dev (neighborhood SD), pctl
#  (percentile), demstd (DEM-scale SD).
# ---------------------------------------------------------------------------
_TPI_TYPE_MARK = "JE_TPI_TYPE"
_TPI_NBHD_MARK = "JE_TPI_NBHD"
_SLOPE_UNIT_MARK = "JE_SLOPE_UNITS"
TPI_TYPE_LABELS = {
    "raw": "raw TPI (elevation units)",
    "dev": "neighborhood-standardized TPI (standard-deviation units)",
    "demstd": "DEM-standardized TPI (standard-deviation units)",
    "pctl": "percentile TPI",
    "dev_tpi": "TPI-roughness-standardized TPI (experimental)",
}
# The "kind" (comparable scale) of each TPI representation and each classification
# unit -- the mismatch warning fires only on a KIND mismatch. dev, demstd and the
# whole-raster 'standardized' are all standard-deviation-scaled and count as one
# kind (interchangeable for warning purposes; Jeff wanted DEM-scale allowed for
# SD systems).
_TPI_TYPE_KIND = {"raw": "raw", "dev": "sd", "demstd": "sd", "pctl": "pctl",
                  "dev_tpi": "sd"}
_UNIT_KIND = {"raw": "raw", "sd": "sd", "standardized": "sd", "pctl": "pctl"}
_KIND_LABEL = {"raw": "raw (elevation units)",
               "sd": "standard-deviation units", "pctl": "percentile units"}


def _tpi_type_tag(t):
    return "[[{}={}]]".format(_TPI_TYPE_MARK, t)


def _tpi_nbhd_tag(outer, units):
    """Marker recording the neighborhood (outer) radius a TPI was computed
    over, so the Landform tool can warn when the small/large TPI rasters
    appear to be swapped."""
    return "[[{}={:g} {}]]".format(_TPI_NBHD_MARK, float(outer),
                                   str(units).lower())


def _slope_unit_tag(u):
    return "[[{}={}]]".format(_SLOPE_UNIT_MARK, str(u).lower())


def _read_meta_tag(raster, mark, pattern):
    try:
        md = arcpy.metadata.Metadata(raster)
        blob = " ".join(x for x in (md.summary, md.description, md.tags) if x)
    except Exception:
        return None
    m = re.search(r"\[\[" + mark + r"=(" + pattern + r")\]\]", blob or "")
    return m.group(1) if m else None


def read_tpi_type(raster):
    """The TPI type ('raw'/'dev'/'demstd'/'pctl') stamped in a raster's metadata
    by the Topographic Position Index tool, or None if absent/unreadable."""
    return _read_meta_tag(raster, _TPI_TYPE_MARK, r"[a-z_]+")


def read_tpi_nbhd(raster):
    """The neighborhood (outer) radius stamped in a TPI raster's metadata by
    our TPI tools, as (radius, units) -- e.g. (100.0, 'meters') -- or None if
    absent/unreadable (rasters made before this stamp existed have none)."""
    s = _read_meta_tag(raster, _TPI_NBHD_MARK, r"[0-9.eE+-]+ [a-z]+")
    if not s:
        return None
    try:
        v, u = s.split()
        return float(v), u
    except (TypeError, ValueError):
        return None


def read_slope_units(raster):
    """The slope units ('degrees'/'percent') stamped in a raster's metadata, or
    None if absent/unreadable."""
    return _read_meta_tag(raster, _SLOPE_UNIT_MARK, r"[a-z]+")


# Human phrasing for each TPI role, so warnings say WHICH neighborhood's TPI
# the system's expectation applies to (the two Landform scales may legitimately
# use different units, so "the system expects X" alone is ambiguous).
_ROLE_PHRASE = {"tpi": "TPI", "tpi_small": "small-neighborhood TPI",
                "tpi_large": "large-neighborhood TPI"}


def tpi_type_warning(system, role, raster_type):
    """Return a warning if a TPI representation of ``raster_type`` (the stamped/
    declared type of a supplied raster, or the chosen on-the-fly type) is of a
    different KIND than the units the classification ``system`` uses for
    ``role``. None if the type is unknown or the kinds match."""
    if not raster_type:
        return None
    rkind = _TPI_TYPE_KIND.get(raster_type)
    if not rkind:
        return None
    kinds = {_UNIT_KIND.get(cr.get("units", "raw"), cr.get("units", "raw"))
             for c in system.get("classes", [])
             for cr in c.get("criteria", []) if cr.get("input") == role}
    if not kinds or rkind in kinds:
        return None
    rl = _ROLE_PHRASE.get(role, "TPI for input '{}'".format(role))
    return ("The {rl} is {rt}, but the classification system '{sn}' expects "
            "{ex} for its {rl}. Applying it as-is may misclassify -- choose a "
            "matching TPI type. (The tool still runs.)").format(
                rl=rl, rt=TPI_TYPE_LABELS.get(raster_type, raster_type),
                sn=system.get("name", "?"),
                ex=" / ".join(sorted(_KIND_LABEL.get(k, k) for k in kinds)))


def tpi_scale_warning(small, large):
    """Warn when the SMALL-neighborhood TPI's radius is not smaller than the
    LARGE one's -- swapped inputs, or the same scale supplied twice, either of
    which defeats a two-scale landform classification. ``small``/``large`` are
    (radius, units) tuples (from the metadata stamp or the dialog) or None.
    Radii are compared in meters for linear units, directly for Cells; a
    cells-vs-linear pair is not comparable and returns None (no warning)."""
    def _basis(t):
        if not t:
            return None
        try:
            v = float(t[0])
        except (TypeError, ValueError):
            return None
        if v <= 0:
            return None
        u = str(t[1] or "").strip().lower()
        if u == "cells":
            return ("cells", v)
        rm = _tt._RADIUS_METERS.get(u.title())
        return ("m", v * rm) if rm else None

    s, l = _basis(small), _basis(large)
    if not s or not l or s[0] != l[0]:
        return None

    def _fmt(t):
        return "{:g} {}".format(float(t[0]), str(t[1]).lower())

    if s[1] > l[1] * (1.0 + 1e-9):
        return ("The SMALL-neighborhood TPI uses a LARGER neighborhood radius "
                "({}) than the large-neighborhood TPI ({}) -- the two appear "
                "to be swapped. (The tool still runs.)").format(
                    _fmt(small), _fmt(large))
    if abs(s[1] - l[1]) <= 1e-9 * max(s[1], l[1]):
        return ("The small- and large-neighborhood TPIs use the SAME "
                "neighborhood radius ({}); a landform classification needs "
                "two different scales. (The tool still runs.)").format(
                    _fmt(small))
    return None


def slope_units_warning(system, slope_units):
    """Return a warning if the slope raster's units differ from the units the
    system's slope thresholds assume. None if unknown or matching."""
    if not slope_units:
        return None
    want = str(system.get("slopeUnits", "") or "").lower()
    have = str(slope_units).lower()
    if not want or want == have:
        return None
    return ("The slope raster is in {have}, but the classification system '{sn}' "
            "assumes slope in {want}, so its slope thresholds may not apply "
            "correctly. (The tool still runs.)").format(
                have=have, want=want, sn=system.get("name", "?"))


# ---------------------------------------------------------------------------
#  Neighborhood radii and (annular) focal statistics
# ---------------------------------------------------------------------------
def _cell_radii(gt, height, sr, outer, inner, units, width=None, proj=None,
                ground=None):
    """Return (rx_out_rows, ry_out, rx_in_rows, ry_in): the outer/inner radii
    in CELLS, per row. Constant in Cells units; otherwise sized per row from
    the true ground cell size on the spheroid (GroundGeometry -- a projected
    raster is unprojected, so Web Mercator and the like get the right number
    of cells). ``ground`` is a GroundGeometry already built for the raster
    (built here when not given)."""
    outer = float(outer)
    inner = max(float(inner), 0.0)
    if units == "Cells":
        return (np.full(height, outer), outer,
                np.full(height, inner), inner)
    rm = _tt._RADIUS_METERS[units]
    out_m, in_m = outer * rm, inner * rm
    if ground is None:
        ground = _hs.ground_geometry(gt, width or 1, height, sr, proj)
    geom = ground.row
    cw = np.maximum(geom["h"], 1e-9)
    ch = max(float(np.mean(geom["vA"])), 1e-9)
    return (out_m / cw, out_m / ch, in_m / cw, in_m / ch)


def _annulus_kernel(rx_out, ry_out, rx_in=0.0, ry_in=0.0):
    """Elliptical/annular 0/1 kernel: 1 where the cell is inside the outer
    ellipse and outside the inner one (a plain disk when inner = 0)."""
    rx_out = max(float(rx_out), 0.5)
    ry_out = max(float(ry_out), 0.5)
    nx, ny = int(math.ceil(rx_out)), int(math.ceil(ry_out))
    xs = np.arange(-nx, nx + 1, dtype=np.float64)[None, :]
    ys = np.arange(-ny, ny + 1, dtype=np.float64)[:, None]
    inside = ((xs / rx_out) ** 2 + (ys / ry_out) ** 2) <= 1.0 + 1e-9
    if rx_in > 0.0 or ry_in > 0.0:
        rxi = max(float(rx_in), 1e-9)
        ryi = max(float(ry_in), 1e-9)
        inside = inside & (((xs / rxi) ** 2 + (ys / ryi) ** 2) > 1.0 + 1e-9)
    return inside.astype(np.float64)


def _row_groups(rx_out_rows, rx_in_rows):
    """Yield (row_selector, rx_out, rx_in) groups. One group when the radius is
    constant (exact, no rounding); otherwise rows grouped by whole-cell outer
    radius, inner scaled to the group's mean ratio -- the same
    varying-radius approximation the VRM / blur tools use on geographic DEMs."""
    rx_out_rows = np.asarray(rx_out_rows, dtype=np.float64)
    rx_in_rows = np.asarray(rx_in_rows, dtype=np.float64)
    if float(np.ptp(rx_out_rows)) < 1e-9:
        yield slice(None), float(rx_out_rows[0]), float(rx_in_rows[0])
        return
    ratio = np.where(rx_out_rows > 0, rx_in_rows / np.maximum(rx_out_rows, 1e-9), 0.0)
    rx_int = np.maximum(np.rint(rx_out_rows), 1.0)
    for rxi in np.unique(rx_int):
        rows = (rx_int == rxi)
        yield rows, float(rxi), float(rxi * np.mean(ratio[rows]))


# ---------------------------------------------------------------------------
#  Row strips. The engine never holds the whole raster: every quantity here
#  is either a bounded neighborhood statistic (TPI -- the N-S radius in cells
#  is constant even on a geographic DEM, only the E-W radius varies by row;
#  slope -- the 3 x 3 Horn window) or cell-wise (the classification), so a
#  strip of rows read with a halo of neighborhood-radius rows above and below
#  gives the whole-grid result for its own rows. Memory is bounded by the
#  strip, not the raster (2026-11-10: a 924-million-cell lunar DEM needed
#  about 120 GiB the old way). The direct-correlation convolution (small
#  kernels) is bit-identical to the whole-grid computation; the overlap-add
#  FFT path (kernels above hillshade._NBR_DIRECT_MAX_CELLS) differs at
#  rounding level because its blocks depend on the array height -- accepted
#  (Jeff, 2026-11-10). Known limit: memory still scales with halo x width;
#  tiling in both directions would remove it (do only if a user needs it).
# ---------------------------------------------------------------------------
STRIP_BUDGET_BYTES = 1 << 30      # working memory per strip (rows READ, halo included)
STRIP_BYTES_PER_CELL = 160        # measured peak of the SD TPI (130) + held grids
STRIP_ROWS_OVERRIDE = None        # tests: force this many output rows per strip
_TILE_ROWS = 256                  # our GeoTIFF tile height: strips align to it


def halo_rows(ry):
    """Rows of context a neighborhood of N-S radius ``ry`` cells needs on each
    side of a strip (the kernel's half-height, see _annulus_kernel)."""
    return int(math.ceil(max(float(ry), 0.5)))


def strip_rows(height, width, halo, budget=None, bytes_per_cell=None):
    """Output rows per strip. The budget covers the rows READ (output rows
    plus the halo on each side), and a strip is never shorter than twice the
    halo, so no row is read or convolved more than about twice however large
    the neighborhood. Aligned to the output tile height when there is room."""
    height = int(height)
    if STRIP_ROWS_OVERRIDE:
        return max(1, min(int(STRIP_ROWS_OVERRIDE), height))
    budget = STRIP_BUDGET_BYTES if budget is None else budget
    bpc = STRIP_BYTES_PER_CELL if bytes_per_cell is None else bytes_per_cell
    rows_read = int(budget // max(int(width) * bpc, 1))
    floor = max(2 * int(halo), 1)
    rows = max(rows_read - 2 * int(halo), floor)
    if rows >= _TILE_ROWS and rows - rows % _TILE_ROWS >= floor:
        rows -= rows % _TILE_ROWS
    return min(rows, height)


def strip_ranges(height, rows):
    """[(y0, y1)] output-row ranges covering the raster."""
    height = int(height)
    rows = max(int(rows), 1)
    return [(y, min(y + rows, height)) for y in range(0, height, rows)]


def strip_note(height, width, rows, halo):
    """The one run message stating the strip layout (Jeff, 2026-11-10)."""
    n = len(strip_ranges(height, rows))
    read = min(rows + 2 * halo, height)
    mb = read * width * STRIP_BYTES_PER_CELL / float(2 ** 20)
    mem = ("about {:,.0f} MB".format(mb) if mb < 1024
           else "about {:.1f} GB".format(mb / 1024.0))
    if n == 1:
        return ("Processing the raster as one strip of {:,} rows ({} of "
                "working memory).".format(height, mem))
    h = ("{} halo row{} on each side".format(halo, "" if halo == 1 else "s")
         if halo else "no halo rows")
    return ("Processing in {:,} strips of {:,} rows with {} ({} of working "
            "memory per strip).".format(n, rows, h, mem))


def _kernel_groups(rx_out_rows, ry_out, rx_in_rows, ry_in):
    """[(rows, kernel)] for the whole raster: one group with ``rows`` =
    slice(None) when the radius is constant, else one per whole-cell E-W
    radius with ``rows`` a bool array over the full height. Built ONCE per
    run from the full-height radii, so every strip applies exactly the
    kernels the whole-grid computation would."""
    return [(rows, _annulus_kernel(rxo, ry_out, rxi, ry_in))
            for rows, rxo, rxi in _row_groups(rx_out_rows, rx_in_rows)]


# A neighborhood whose standard deviation is within this many float32 steps
# of its own elevation counts as flat (SD = 0, standardized TPI = 0). A lake
# in a Float32 DEM is flat only to the float32 spacing (1.2e-4 m at 1,600 m):
# a few cells one or two steps higher give a "true" SD of 5e-5 m and a
# standardized TPI of +-1 or more from nothing but quantization, enough to
# cross the +-1 SD class thresholds (seen on a Grand Canyon lake, 2026-11-10).
# Four steps is 4.8e-7 of the elevation, far below any DEM's accuracy.
_SD_FLOOR_ULPS = 4.0


def focal_stats(z, valid, groups, row0=0, want_sd=False):
    """NoData-ignoring focal mean (and optionally SD / variance) over an
    (annular) neighborhood, for a BLOCK of rows whose first row is raster
    row ``row0`` (the whole grid when row0 = 0 and the block is the raster).
    ``groups`` come from _kernel_groups. Returns dict with 'mean', 'count',
    and, if requested, 'sd' and 'var'. NaN where the neighborhood holds no
    valid cell. Rows within a kernel's half-height of the block's edge see
    the block's zero padding instead of the raster: callers read a halo and
    keep only the interior rows."""
    H = z.shape[0]
    den = valid.astype(np.float64)
    # Centering (want_sd only): the variance is cs/cd - m^2, the difference of
    # two numbers of order z^2 (~2.6e6 for a 1,600 m DEM). The FFT path sums
    # z^2 with a relative error of ~1e-14, i.e. ~5e-8 absolute in the
    # variance, which swamps the true variance of a lake or any neighborhood
    # flat to a few millimeters, and the standardized TPI there becomes
    # arithmetic noise that depends on the FFT block layout. Shifting the
    # elevations by one constant (the block mean) before squaring leaves the
    # mean, TPI and variance unchanged in exact arithmetic (all three are
    # translation invariant) and shrinks that error by the ratio of z^2 to
    # (z - c)^2, measured as 65,000x on a Grand Canyon lake at 1,616 m. The
    # mean is shifted back before it is returned. Paths without the SD are
    # untouched (the raw and percentile TPI stay bit-identical).
    c = 0.0
    if want_sd:
        zv = z[valid]
        c = float(zv.mean()) if zv.size else 0.0
    zc = z - c if c != 0.0 else z
    num = np.where(valid, zc, 0.0)
    numsq = np.where(valid, zc * zc, 0.0) if want_sd else None

    mean = np.full(z.shape, np.nan)
    cnt = np.zeros(z.shape)
    sd = np.full(z.shape, np.nan) if want_sd else None
    var = np.full(z.shape, np.nan) if want_sd else None
    for rows, k in groups:
        # The N-S radius stays the CONSTANT ry_out/ry_in in every group -- only
        # the E-W radius varies by row on a geographic DEM, so the kernel is an
        # ellipse in pixel space = a true circle on the ground (same convention
        # as hillshade._row_kernel_groups and focal_percentile). Using rxo for
        # ry here would inflate the N-S ground radius by ~1/cos(latitude).
        if isinstance(rows, slice):
            sel = rows
        else:
            sel = rows[row0:row0 + H]
            if not sel.any():
                continue
        # Size-aware neighborhood convolution (direct for small kernels,
        # overlap-add FFT for large TPI windows) -- see hillshade.nbr_conv.
        cd = _hs.nbr_conv(den, k)
        cn = _hs.nbr_conv(num, k)
        cd_r = np.maximum(cd, 0.0)
        ok = cd_r >= 0.5
        with np.errstate(invalid="ignore", divide="ignore"):
            m = np.where(ok, cn / np.maximum(cd_r, 1e-9), np.nan)
        cnt[sel] = cd_r[sel]
        mean[sel] = (m + c)[sel] if c != 0.0 else m[sel]
        if want_sd:
            cs = _hs.nbr_conv(numsq, k)
            with np.errstate(invalid="ignore", divide="ignore"):
                v = np.where(ok, cs / np.maximum(cd_r, 1e-9) - m * m, np.nan)
                v = np.where(np.isfinite(v), np.maximum(v, 0.0), np.nan)
                # quantization floor (see _SD_FLOOR_ULPS): per cell, from the
                # magnitude of its own neighborhood mean, so the result does
                # not depend on the strip layout
                mag = np.abs(m + c) if c != 0.0 else np.abs(m)
                floor = _SD_FLOOR_ULPS * np.spacing(mag.astype(np.float32)).astype(np.float64)
                v = np.where(v < floor * floor, 0.0, v)
            var[sel] = v[sel]
            sd[sel] = np.sqrt(v[sel])
    out = {"mean": mean, "count": cnt}
    if want_sd:
        out["sd"] = sd
        out["var"] = var
    return out


def focal_percentile(z, valid, rx_out_rows, ry_out, rx_in_rows, ry_in,
                     messages=None):
    """Wilson & Gallant (2000) 'pctl' (eq. 3.31): for each cell,
    100 x (count of neighborhood elevations strictly LESS than the focal
    elevation) / (number of cells in the neighborhood). Computed exactly by
    accumulating over the neighborhood offsets (so it also honors a per-row
    varying radius on geographic DEMs). NaN where the neighborhood is empty."""
    H, W = z.shape
    zf = np.where(valid, z, np.nan)
    rxo = np.asarray(rx_out_rows, dtype=np.float64)[:, None]      # (H,1)
    rxi = np.asarray(rx_in_rows, dtype=np.float64)[:, None]
    has_inner = bool((rxi > 0).any() or ry_in > 0)
    nxo = int(math.ceil(float(rxo.max())))
    nyo = int(math.ceil(float(ry_out)))
    count = np.zeros((H, W))
    den = np.zeros((H, W))
    total = (2 * nyo + 1) * (2 * nxo + 1)
    done = 0
    # Accumulate over neighborhood offsets working directly on the OVERLAPPING
    # slices (views) of the grid for each offset -- no per-offset full-grid
    # shift/allocation and no isfinite pass (zf is NaN exactly where invalid, so
    # the neighbor's validity is given by ``valid`` shifted into the source
    # slice). Same result as a shift-and-compare, much less memory churn.
    for dr in range(-nyo, nyo + 1):
        r0d, r1d = max(0, -dr), min(H, H - dr)       # focal (dest) rows
        r0s, r1s = max(0, dr), min(H, H + dr)        # neighbor (src) rows
        if r0d >= r1d:
            done += (2 * nxo + 1)
            continue
        for dc in range(-nxo, nxo + 1):
            done += 1
            with np.errstate(invalid="ignore", divide="ignore"):
                inside = ((dc / np.maximum(rxo[r0d:r1d], 1e-9)) ** 2
                          + (dr / max(ry_out, 1e-9)) ** 2) <= 1.0 + 1e-9  # (nr,1)
                if has_inner:
                    inside = inside & (((dc / np.maximum(rxi[r0d:r1d], 1e-9)) ** 2
                                        + (dr / max(ry_in, 1e-9)) ** 2) > 1.0 + 1e-9)
            if not inside.any():
                continue
            c0d, c1d = max(0, -dc), min(W, W - dc)
            c0s, c1s = max(0, dc), min(W, W + dc)
            if c0d >= c1d:
                continue        # offset wider than the raster: no overlap
            zc = zf[r0d:r1d, c0d:c1d]                 # focal (view)
            zn = zf[r0s:r1s, c0s:c1s]                 # neighbor (view)
            m = (inside & valid[r0d:r1d, c0d:c1d] & valid[r0s:r1s, c0s:c1s])
            den[r0d:r1d, c0d:c1d] += m
            with np.errstate(invalid="ignore"):
                count[r0d:r1d, c0d:c1d] += m & (zn < zc)
        if messages is not None:
            _mh.phase_step(done, total)
    with np.errstate(invalid="ignore", divide="ignore"):
        pctl = np.where(den >= 0.5, 100.0 * count / np.maximum(den, 1e-9), np.nan)
    pctl[~valid] = np.nan
    return pctl


def pctl_reachable_max(rx_out_rows, ry_out, rx_in_rows, ry_in):
    """Highest value a percentile TPI can take with this neighborhood.

    focal_percentile counts the focal cell in its own neighborhood (the
    denominator) but never below itself, so a circle of n cells tops out at
    100 (n - 1) / n -- a cell higher than every neighbor -- e.g. 88.9 for the
    9 cells of a 1.5-cell radius. An annulus excludes the centre and can
    reach 100. Returns (max_value, n_max, varies): n_max is the largest
    interior neighborhood (per-row radii on a geographic DEM can make n vary
    by row, flagged by ``varies``); n_max is None for an annulus. Uses the
    same inside test as focal_percentile."""
    rxo = np.asarray(rx_out_rows, dtype=np.float64)
    rxi = np.asarray(rx_in_rows, dtype=np.float64)
    if bool((rxi > 0).any() or ry_in > 0):
        return 100.0, None, False
    uniq = np.unique(rxo)
    ry = max(float(ry_out), 1e-9)
    nyo = int(math.ceil(float(ry_out)))
    n = np.zeros(uniq.shape)
    for dr in range(-nyo, nyo + 1):
        g = 1.0 + 1e-9 - (dr / ry) ** 2
        if g < 0.0:
            continue
        m = np.floor(np.maximum(uniq, 1e-9) * math.sqrt(g) + 1e-12)
        n += 2.0 * m + 1.0
    n_max = int(n.max())
    if n_max <= 0:
        return 0.0, n_max, False
    return 100.0 * (n_max - 1) / n_max, n_max, bool(n.min() != n.max())


def _disk_radius_for_cells(n_needed):
    """Smallest circle radius (in cells, 0.5-cell steps) whose disk holds at
    least ``n_needed`` cells; for the percentile advice."""
    r = 1.0
    while r < 1000.0:
        k = int(math.ceil(r))
        xs = np.arange(-k, k + 1)
        cnt = int((((xs[None, :] / r) ** 2 + (xs[:, None] / r) ** 2)
                   <= 1.0 + 1e-9).sum())
        if cnt >= n_needed:
            return r
        r += 0.5
    return None


def _describe_conditions(conditions):
    c0 = conditions[0]
    s = "{} {:g}".format(c0["op"], float(c0["value"]))
    if len(conditions) > 1:
        c1 = conditions[1]
        s += " {} {} {:g}".format(c1.get("join", "and"), c1["op"],
                                  float(c1["value"]))
    return s


def _condition_reachable(conditions, hi):
    """True if some value in [0, hi] satisfies the criterion's conditions
    (percentile TPI is never below 0)."""
    cands = {0.0, float(hi)}
    for c in conditions:
        v = float(c["value"])
        for x in (v, v - 1e-6, v + 1e-6):
            if 0.0 <= x <= hi:
                cands.add(x)
    vals = sorted(cands)
    for a, b in zip(vals, vals[1:]):
        cands.add((a + b) / 2.0)
    arr = np.array(sorted(cands), dtype=np.float64)
    return bool(_criterion_mask(arr, conditions).any())


def pctl_class_warnings(system, role, hi, from_raster=False):
    """Warnings for classes whose percentile-TPI criterion on ``role`` no cell
    can satisfy because the percentile cannot exceed ``hi`` (see
    pctl_reachable_max). ``from_raster`` True: ``hi`` is the observed maximum
    of a supplied raster rather than the neighborhood's mathematical limit.
    Values and classes are never changed."""
    out = []
    for c in sorted(system.get("classes", []), key=lambda c: int(c["value"])):
        for cr in c.get("criteria", []):
            if cr.get("input") != role:
                continue
            if _UNIT_KIND.get(cr.get("units", "raw")) != "pctl":
                continue
            conds = cr.get("conditions") or []
            if not conds or _condition_reachable(conds, hi):
                continue
            if from_raster:
                why = ("the highest value in this TPI raster is "
                       "{:.1f}").format(hi)
                advice = ("Use a TPI computed over a larger neighborhood or "
                          "lower the threshold.")
            else:
                why = ("with this neighborhood the highest possible value is "
                       "{:.1f}").format(hi)
                need = None
                for cond in conds:
                    v = float(cond["value"])
                    if cond["op"] in (">", ">=") and v < 100.0:
                        n_req = int(math.floor(100.0 / (100.0 - v))) + 1
                        need = max(need or 0, n_req)
                r = _disk_radius_for_cells(need) if need else None
                advice = ("Use a larger neighborhood (a radius of {:g} cells "
                          "or more) or lower the threshold.").format(r) \
                    if r else "Use a larger neighborhood or lower the threshold."
            out.append(("Class '{}' needs percentile TPI {}, but {}; no cell "
                        "can fall in that class. {}").format(
                            c.get("name", c.get("value")),
                            _describe_conditions(conds), why, advice))
    return out


# ---------------------------------------------------------------------------
#  TPI variants and slope
# ---------------------------------------------------------------------------
def _standardize_whole(grid):
    """(grid - mean) / SD over all finite cells (whole-raster z-score) of a
    grid held in memory. The strip engine streams the same statistics with
    _whole_stats instead; kept for callers that hold a grid."""
    m = np.isfinite(grid)
    if not m.any():
        return np.full_like(grid, np.nan)
    mu = float(grid[m].mean())
    sd = float(grid[m].std())
    out = np.full_like(grid, np.nan)
    out[m] = (grid[m] - mu) / sd if sd > 0 else 0.0
    return out


class _Source(object):
    """A raster opened for reading by row blocks (GDAL; a format GDAL cannot
    read is exported to a scratch GeoTIFF by hillshade._open_source, as
    before). ``read(top, bot)`` returns (values as float64, valid mask) for
    rows top..bot-1 with the band's NoData value and its mask band applied;
    ``close`` releases the dataset and deletes the scratch copy."""

    def __init__(self, path, scratch, messages):
        self.path = str(path)
        self.ds, self.copy = _hs._open_source(path, scratch, messages)
        self.band = self.ds.GetRasterBand(1)
        self.width = int(self.ds.RasterXSize)
        self.height = int(self.ds.RasterYSize)
        self.gt = self.ds.GetGeoTransform()
        self.proj = self.ds.GetProjection()
        self.nodata = self.band.GetNoDataValue()

    def read(self, top, bot):
        top, bot = int(top), int(bot)
        arr = self.band.ReadAsArray(0, top, self.width, bot - top).astype(np.float64)
        valid = np.isfinite(arr)
        if self.nodata is not None:
            valid &= (arr != self.nodata)
        valid = _mh.mask_band_valid(self.band, valid, 0, top)
        return arr, valid

    def close(self):
        self.band = None
        self.ds = None
        if self.copy:
            try:
                arcpy.management.Delete(self.copy)
            except Exception:
                pass
            self.copy = None


def _whole_stats(src, rows):
    """Mean and population SD of a raster's valid cells, streamed in strips
    (two passes: the mean, then the squared deviations, as numpy's std does
    in memory). None when no cell is valid."""
    n = 0
    s = 0.0
    ranges = strip_ranges(src.height, rows)
    for y0, y1 in ranges:
        arr, v = src.read(y0, y1)
        vals = arr[v]
        n += int(vals.size)
        s += float(vals.sum())
    if n == 0:
        return None
    mu = s / n
    ss = 0.0
    for y0, y1 in ranges:
        arr, v = src.read(y0, y1)
        d = arr[v] - mu
        ss += float((d * d).sum())
    return mu, math.sqrt(ss / n)


def _tanbeta_block(z, valid, ground, row0):
    """tan(beta) = Horn gradient magnitude of ``z`` (meters, z-scaled) over
    the per-cell ground spans of ``ground`` (a hillshade.GroundGeometry) for
    a block whose first row is raster row ``row0``: topographic_tools.
    _twi_tanbeta's arithmetic row for row, with the geometry looked up by
    raster row. NoData neighbors fall back to the focal value so the
    gradient is defined for every valid cell. The block's first and last
    rows see NaN padding instead of the raster: callers read a 1-row halo."""
    H, W = z.shape
    zp = np.full((H + 2, W + 2), np.nan)
    zp[1:-1, 1:-1] = np.where(valid, z, np.nan)
    ctr = np.where(valid, z, np.nan)
    tanb = np.full((H, W), np.nan)
    for r0 in range(0, H, _hs.GEOM_STRIP):
        r1 = min(H, r0 + _hs.GEOM_STRIP)
        g = ground.rows(row0 + r0, row0 + r1)
        up, sl, dn = slice(r0, r1), slice(r0 + 1, r1 + 1), slice(r0 + 2, r1 + 2)
        c = ctr[r0:r1]
        parts = [zp[up, :-2], zp[up, 1:-1], zp[up, 2:],
                 zp[sl, :-2], zp[sl, 2:],
                 zp[dn, :-2], zp[dn, 1:-1], zp[dn, 2:]]
        parts = [np.where(np.isnan(p), c, p) for p in parts]
        NW, N_, NE, W_, E_, SW, S_, SE = parts
        with np.errstate(invalid="ignore"):
            dzdx = ((NE + 2.0 * E_ + SE) - (NW + 2.0 * W_ + SW)) / \
                   (2.0 * g["hA"] + 4.0 * g["h"] + 2.0 * g["hB"])
            dzdy = ((SW + 2.0 * S_ + SE) - (NW + 2.0 * N_ + NE)) / \
                   (4.0 * g["vA"] + 4.0 * g["vB"])
            tanb[r0:r1] = np.hypot(dzdx, dzdy)
    tanb[~valid] = np.nan
    return tanb


def _slope_block(z_raw, valid, ground, row0, zs, units="Degrees"):
    """Slope of a block in DEGREES (default) or PERCENT rise: elevation
    scaled to meters by ``zs``, Horn gradient over true ground distances."""
    tanb = _tanbeta_block(np.where(valid, z_raw * zs, np.nan), valid, ground, row0)
    if str(units).lower().startswith("percent"):
        return tanb * 100.0
    return np.degrees(np.arctan(tanb))


def slope_grid(z_raw, valid, gt, sr, proj, elev_units, units="Degrees"):
    """Slope of a whole grid via the Horn gradient, in DEGREES (default) or
    PERCENT rise, over true ground distances on the spheroid in any
    coordinate system (hillshade.GroundGeometry). Elevation scaled to
    meters first."""
    elev_mpu = _hs._ELEV_METERS.get(elev_units, 1.0)
    ground, _g, zs = _tt._row_gradient_geometry(gt, z_raw.shape[1], z_raw.shape[0],
                                                sr, proj, elev_mpu)
    return _slope_block(z_raw, valid, ground, 0, zs, units)


def tpi_representation(z, valid, rx_out_rows, ry_out, rx_in_rows, ry_in, tpi_type,
                       messages=None, row0=0, groups=None, dem_stats=None):
    """Compute ONE TPI representation for a block of the DEM (rows from
    raster row ``row0``; the whole grid when row0 = 0 and the block is the
    raster): 'raw' / 'dev' (neighborhood SD) / 'demstd' (whole-raster
    elevation z-score from ``dem_stats`` = (mean, sd), computed from the
    block itself when not given) / 'pctl' (percentile) / 'dev_tpi'
    (experimental: TPI over the neighborhood SD of TPI). ``rx_out_rows`` /
    ``rx_in_rows`` are the radii of the BLOCK's rows; ``groups`` the
    full-raster kernel groups (built from the block's radii when not given,
    in which case the block is treated as the whole grid). Returns a full
    block grid."""
    if tpi_type == "pctl":
        return focal_percentile(z, valid, rx_out_rows, ry_out, rx_in_rows, ry_in,
                                messages)
    if tpi_type == "demstd":
        g = np.full(z.shape, np.nan)
        if dem_stats is None:
            zv = z[valid]
            dem_stats = (float(zv.mean()), float(zv.std())) if zv.size else None
        if dem_stats is not None:
            mu, sd = dem_stats
            g[valid] = (z[valid] - mu) / sd if sd > 0 else 0.0
        return g
    if groups is None:
        groups = _kernel_groups(rx_out_rows, ry_out, rx_in_rows, ry_in)
        row0 = 0
    fs = focal_stats(z, valid, groups, row0,
                     want_sd=(tpi_type in ("dev", "dev_tpi")))
    ok = valid & (fs["count"] >= 0.5)
    tpi = np.where(ok, z - fs["mean"], np.nan)
    if tpi_type == "raw":
        return tpi
    if tpi_type == "dev":
        sd = fs["sd"]
    elif tpi_type == "dev_tpi":
        sd = focal_stats(np.where(ok, tpi, 0.0), ok, groups, row0,
                         want_sd=True)["sd"]
    else:
        raise arcpy.ExecuteError("Unknown TPI type: {!r}".format(tpi_type))
    with np.errstate(invalid="ignore", divide="ignore"):
        g = np.where(sd > 0, tpi / np.where(sd > 0, sd, 1.0), 0.0)
    g[~np.isfinite(tpi)] = np.nan
    return g


def _tpi_halo(tpi_type, ry_out):
    """Halo rows one TPI representation needs: none for the whole-raster
    z-score, the kernel's half-height otherwise, twice that for the
    experimental TPI-over-TPI (a neighborhood of a neighborhood)."""
    if tpi_type == "demstd":
        return 0
    h = halo_rows(ry_out)
    return 2 * h if tpi_type == "dev_tpi" else h


def _criterion_mask(grid, conditions):
    c0 = conditions[0]
    m = _OPS[c0["op"]](grid, c0["value"])
    if len(conditions) > 1:
        c1 = conditions[1]
        m1 = _OPS[c1["op"]](grid, c1["value"])
        m = (m & m1) if c1.get("join", "and") == "and" else (m | m1)
    return m & np.isfinite(grid)


def classify(system, resolved, valid):
    """Apply a system: for each class (ascending value) the AND of its
    criteria; first (lowest-value) match wins. Returns an int class raster
    (NODATA_OUT where nothing matches)."""
    classes = sorted(system["classes"], key=lambda c: int(c["value"]))
    condlist, choicelist = [], []
    for c in classes:
        mask = valid.copy()
        for crit in c["criteria"]:
            g = resolved[crit["input"]]        # one representation per role
            mask &= _criterion_mask(g, crit["conditions"])
        condlist.append(mask)
        choicelist.append(int(c["value"]))
    out = np.select(condlist, choicelist, default=NODATA_OUT)
    return out.astype(np.int32)


# ---------------------------------------------------------------------------
#  Class-raster output (integer, with colormap + attribute table)
# ---------------------------------------------------------------------------
class _ClassWriter(object):
    """Writes an integer class raster strip by strip: a GeoTIFF with a color
    table (values <= 255) and, when finished, a raster attribute table
    (Value / Count / Class_Name / color) built from the counts gathered as
    the strips were written. ArcGIS reads both, so the layer draws as
    named, colored classes."""

    def __init__(self, gdal, path, gt, proj, width, height, classes):
        self.gdal = gdal
        self.path = path
        self.classes = classes
        self.values = [int(c["value"]) for c in classes]
        self.byte_ok = all(0 <= v <= 255 for v in self.values) and NODATA_OUT == 0
        dtype = gdal.GDT_Byte if self.byte_ok else gdal.GDT_Int32
        drv = gdal.GetDriverByName("GTiff")
        self.ds = drv.Create(path, int(width), int(height), 1, dtype,
                             options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
        self.ds.SetGeoTransform(gt)
        try:
            self.ds.SetProjection(proj)
        except Exception:
            pass
        self.band = self.ds.GetRasterBand(1)
        self.names = {int(c["value"]): c.get("name", str(c["value"])) for c in classes}
        self.colors = {int(c["value"]): c.get("color") for c in classes}
        # per-class opacity: 'alpha' is 0-100 percent in the schema (matching CIM
        # and Pro's own transparency UI); the GDAL color table wants 0-255
        self.alphas = {}
        for c in classes:
            try:
                a = float(c.get("alpha", 100))
            except (TypeError, ValueError):
                a = 100.0
            self.alphas[int(c["value"])] = int(round(2.55 * min(max(a, 0.0), 100.0)))
        # the color table must be set BEFORE writing data (GTiff locks the
        # photometric interpretation once pixels are written)
        if self.byte_ok:
            ct = gdal.ColorTable()
            ct.SetColorEntry(0, (0, 0, 0, 0))
            for v in self.values:
                col = self.colors.get(v) or (180, 180, 180)
                ct.SetColorEntry(v, (int(col[0]), int(col[1]), int(col[2]),
                                     self.alphas.get(v, 255)))
            self.band.SetRasterColorTable(ct)
        self.counts = {v: 0 for v in self.values}

    def write(self, y0, arr):
        """Write the class rows starting at raster row ``y0``."""
        self.band.WriteArray(arr.astype(np.uint8 if self.byte_ok else np.int32),
                             0, int(y0))
        uniq, ucnt = np.unique(arr, return_counts=True)
        for v, n in zip(uniq.tolist(), ucnt.tolist()):
            if v in self.counts:
                self.counts[v] += int(n)

    def finish(self):
        gdal = self.gdal
        band = self.band
        band.SetNoDataValue(NODATA_OUT)
        rat = gdal.RasterAttributeTable()
        rat.CreateColumn("Value", gdal.GFT_Integer, gdal.GFU_MinMax)
        rat.CreateColumn("Count", gdal.GFT_Integer, gdal.GFU_PixelCount)
        rat.CreateColumn("Class_Name", gdal.GFT_String, gdal.GFU_Name)
        rat.CreateColumn("Red", gdal.GFT_Integer, gdal.GFU_Red)
        rat.CreateColumn("Green", gdal.GFT_Integer, gdal.GFU_Green)
        rat.CreateColumn("Blue", gdal.GFT_Integer, gdal.GFU_Blue)
        rat.CreateColumn("Alpha", gdal.GFT_Integer, gdal.GFU_Alpha)
        present = [v for v in self.values if self.counts[v] > 0]
        rat.SetRowCount(len(present))
        for row, v in enumerate(present):
            col = self.colors.get(v) or (180, 180, 180)
            rat.SetValueAsInt(row, 0, v)
            rat.SetValueAsInt(row, 1, self.counts[v])
            rat.SetValueAsString(row, 2, self.names[v])
            rat.SetValueAsInt(row, 3, int(col[0]))
            rat.SetValueAsInt(row, 4, int(col[1]))
            rat.SetValueAsInt(row, 5, int(col[2]))
            rat.SetValueAsInt(row, 6, self.alphas.get(v, 255))
        try:
            band.SetDefaultRAT(rat)
        except Exception:
            pass
        band.FlushCache()
        self.ds.FlushCache()
        self.band = None
        self.ds = None

    def abandon(self):
        """Close and remove the scratch file after a failure."""
        self.band = None
        self.ds = None
        try:
            os.remove(self.path)
        except Exception:
            pass


def _write_class_raster(gdal, path, gt, proj, arr, classes):
    """Write a whole class grid ``arr`` -- see _ClassWriter (kept for callers
    that hold the grid in memory)."""
    w = _ClassWriter(gdal, path, gt, proj, arr.shape[1], arr.shape[0], classes)
    w.write(0, arr)
    w.finish()


def _deliver_class_output(out_scratch, out_raster, messages, title, summary,
                          description, tags, citation, class_names):
    """CopyRaster the class raster to its destination, build its attribute
    table with a Class_Name field, stamp metadata, and build (nearest-neighbor)
    pyramids. Keeps the colormap and skips the continuous-stretch statistics."""
    prev_pyr = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        if arcpy.Exists(out_raster):
            arcpy.management.Delete(out_raster)
        # Grid-native contract (same as topographic_tools._deliver_output):
        # without the neutral env, an Output Coordinate System / Extent /
        # Snap Raster / Cell Size / Mask environment would make CopyRaster
        # re-project, crop, or resample the finished class raster off the
        # analysis grid the tool just built.
        with _mh.neutral_grid_env():
            arcpy.management.CopyRaster(out_scratch, out_raster)
    finally:
        arcpy.env.pyramid = prev_pyr
    try:
        arcpy.management.BuildRasterAttributeTable(out_raster, "OVERWRITE")
        fields = [f.name for f in arcpy.ListFields(out_raster)]
        if "Class_Name" not in fields:
            arcpy.management.AddField(out_raster, "Class_Name", "TEXT",
                                      field_length=80)
        with arcpy.da.UpdateCursor(out_raster, ["Value", "Class_Name"]) as cur:
            for row in cur:
                row[1] = class_names.get(int(row[0]), "")
                cur.updateRow(row)
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage(
                "Could not write the class-name attribute table: {}".format(e))
    try:
        md = arcpy.metadata.Metadata(out_raster)
        md.title = title
        md.tags = tags
        md.summary = summary
        md.description = description
        md.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                      "(http://www.jennessent.com). Method: " + citation)
        md.save()
    except Exception:
        pass
    try:
        arcpy.management.Delete(out_scratch)
    except Exception:
        pass
    try:
        arcpy.management.BuildPyramids(out_raster,
                                       resample_technique="NEAREST")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not build pyramids: {}".format(e))


# ---------------------------------------------------------------------------
#  Orchestrators: one fused strip loop per run. Each strip is read once with
#  its halo, every TPI / slope grid the run needs is computed for its rows,
#  the rows are classified (or written as TPI outputs), and nothing of the
#  strip survives the next one.
# ---------------------------------------------------------------------------
_TPI_TAGS = ("topographic position index, TPI, terrain position, landform, "
             "topography, Jenness Enterprises")


def _scratch_tif(scratch, base):
    """A new GeoTIFF path in the scratch folder. Not arcpy.CreateUniqueName:
    that opens the existing rasters it finds to test them, and with several
    outputs written strip by strip the earlier scratch GeoTIFFs are still
    open and incomplete, which it reports as an error."""
    import uuid
    for _ in range(1000):
        p = os.path.join(scratch, "{}_{}.tif".format(base, uuid.uuid4().hex[:10]))
        if not os.path.exists(p) and not os.path.exists(p + ".aux.xml"):
            return p
    raise arcpy.ExecuteError("Could not find a free scratch name in {}".format(scratch))


class _FloatWriter(object):
    """A continuous-float output (NoData -9999) written strip by strip to a
    scratch GeoTIFF with streaming statistics; ``finish`` stamps the
    statistics and metadata, delivers it to its destination with pyramids,
    and returns the diverging blue-white-red stretch layer file."""

    def __init__(self, gdal, gt, proj, width, height):
        scratch = arcpy.env.scratchFolder
        self.path = _scratch_tif(scratch, "tpi")
        self.ds, self.band = _tt._make_output(gdal, self.path, gt, proj,
                                              int(width), int(height))
        self.stats = _mh._RasterStats()

    def write(self, y0, grid, valid):
        """Write the rows of ``grid`` starting at raster row ``y0``; cells
        not ``valid`` become NoData."""
        self.band.WriteArray(
            np.where(valid, grid, _tt.NODATA_OUT).astype(np.float32), 0, int(y0))
        if valid.any():
            self.stats.add(grid[valid].astype(np.float64))

    def finish(self, out_raster, title, summary, description, messages):
        out_stats = _mh._finalize_stats(self.band, self.stats, messages)
        if out_stats is not None:
            out_stats["on_raster"] = _mh._write_gtiff_stats(self.band, out_stats)
        self.band.FlushCache()
        self.ds.FlushCache()
        self.band = None
        self.ds = None
        _tt._deliver_output(
            self.path, out_raster, out_stats, messages, title=title,
            summary=summary, description=description, tags=_TPI_TAGS,
            citation=_TPI_CITATION)
        # blue (below, valley) -> white (0) -> red (above, ridge)
        return _mh.default_stretch_lyrx(
            out_raster, [(44, 123, 182), (247, 247, 247), (215, 25, 28)],
            messages=messages)

    def abandon(self):
        """Close and remove the scratch file after a failure."""
        self.band = None
        self.ds = None
        try:
            os.remove(self.path)
        except Exception:
            pass


def _write_float_output(gdal, grid, valid, out_raster, gt, proj, width, height,
                        title, summary, description, messages):
    """Write one whole-grid continuous-float TPI raster -- see _FloatWriter
    (kept for callers that hold the grid in memory)."""
    w = _FloatWriter(gdal, gt, proj, width, height)
    w.write(0, grid, valid)
    return w.finish(out_raster, title, summary, description, messages)


def run_classification(family, in_raster, out_raster, system, sources,
                       elev_units="Meters", messages=None):
    """Core apply routine (USE-AS-IS model). ``system`` is a validated system.
    ``sources`` maps each declared role to a spec:
      tpi on-the-fly:  {'kind':'tpi','dem':path,'shape','outer','inner',
                        'units'(nbhd radius units),'tpi_type','save':path|None}
      tpi supplied:    {'kind':'tpi','raster':path,'declared_type':str|None}
      slope on-the-fly:{'kind':'slope','dem':path,'elev_units',
                        'slope_units','save':path|None}
      slope supplied:  {'kind':'slope','raster':path,'declared_units':str|None}
      external:        {'kind':'external','raster':path}
    Classifies the values PRESENT directly -- no per-criterion re-derivation;
    the system's units drive only the compatibility warning (never blocks).
    The rasters are processed in row strips (see the strip section above):
    each strip is read with its halo, every role's grid is computed for its
    rows, the rows are classified and written, and the optional saved TPI /
    slope rasters receive the same rows."""
    gdal = _mh._gdal()
    out_raster = _mh.resolve_raster_output(out_raster, messages)
    _store.validate(system)
    used = {cr.get("input") for c in system["classes"] for cr in c["criteria"]}

    _mh.begin_phases([("Reading inputs...", 1),
                      ("Computing TPI, slope and classes...", 5),
                      ("Saving classified raster...", 2),
                      ("Finishing...", 1)])

    def _step(text):
        _hs._msg(messages, text)
        _hs._set_label(text)

    scratch = arcpy.env.scratchFolder
    opened = {}                      # path -> _Source (one open per raster)
    ref = _Source(in_raster, scratch, messages)
    opened[ref.path] = ref
    width, height, gt, proj = ref.width, ref.height, ref.gt, ref.proj
    sr = arcpy.Describe(in_raster).spatialReference
    ground = None
    if any(s.get("dem") and not s.get("raster")
           and (s.get("kind") == "slope" or s.get("units", "Cells") != "Cells")
           for s in sources.values()):
        # ground distances are used (a slope from the DEM, or a neighborhood
        # in linear units): post the ground-geometry note once per run
        ground = _hs.ground_geometry(gt, width, height, sr, proj, messages)

    def _open(path):
        # One open per unique raster: on-the-fly, every role's spec points at
        # the same DEM; in existing mode the reference raster is also one of
        # the role rasters.
        key = str(path)
        if key not in opened:
            s = _Source(path, scratch, messages)
            if s.width != width or s.height != height:
                s.close()
                raise arcpy.ExecuteError(
                    "Raster {} ({}x{} cells) does not match the reference "
                    "raster's dimensions ({}x{} cells).".format(
                        path, s.width, s.height, width, height))
            opened[key] = s
        return opened[key]

    cw = None
    savers = {}                      # role -> (_FloatWriter, path, note, label)
    try:
        plan = []                    # one entry per used role, in system order
        warnings = []
        nbhd_by_role = {}            # TPI role -> (radius, units) or None
        halo = 0
        for inp in system["inputs"]:
            role = inp["role"]
            if role not in used:
                continue                          # declared but unused by any class
            spec = sources.get(role, {})
            kind = inp.get("kind", "external")
            if not (spec.get("raster") or spec.get("dem")):
                raise arcpy.ExecuteError(
                    "No raster (or DEM) was supplied for the classification input "
                    "'{}' (kind '{}') used by system '{}'. This tool cannot bind "
                    "that input -- edit the system or supply the raster.".format(
                        role, kind, system.get("name", "?")))
            item = {"role": role, "kind": kind, "spec": spec, "mode": "raster"}
            w = None
            if kind == "tpi":
                if spec.get("raster"):
                    _step("Reading TPI raster for '{}'...".format(role))
                    item["src"] = _open(spec["raster"])
                    rtype = spec.get("declared_type") or read_tpi_type(spec["raster"])
                    nbhd_by_role[role] = read_tpi_nbhd(spec["raster"])
                    w = tpi_type_warning(system, role, rtype)
                    # A percentile TPI cannot exceed 100 (n - 1) / n; for a
                    # supplied raster the observed maximum is the practical limit.
                    item["track_max"] = (rtype is None
                                         or _TPI_TYPE_KIND.get(rtype) == "pctl")
                else:
                    tpi_type = spec.get("tpi_type", "dev")
                    _step("Computing TPI ({}) for '{}' over a {} radius {:g} {}...".format(
                        TPI_TYPE_LABELS.get(tpi_type, tpi_type), role,
                        str(spec.get("shape", "Circle")).lower(),
                        float(spec.get("outer", 0) or 0),
                        str(spec.get("units", "Cells")).lower()))
                    item["src"] = _open(spec["dem"])
                    item["mode"] = "tpi"
                    item["tpi_type"] = tpi_type
                    rxo, ryo, rxi, ryi = _cell_radii(
                        gt, height, sr, spec["outer"],
                        spec.get("inner", 0.0) if spec.get("shape") == "Annulus" else 0.0,
                        spec.get("units", "Cells"), width, proj, ground)
                    item["radii"] = (rxo, ryo, rxi, ryi)
                    item["groups"] = (_kernel_groups(rxo, ryo, rxi, ryi)
                                      if tpi_type in ("raw", "dev", "dev_tpi") else None)
                    item["needs_stats"] = (tpi_type == "demstd")
                    halo = max(halo, _tpi_halo(tpi_type, ryo))
                    nbhd_by_role[role] = (spec.get("outer"),
                                          spec.get("units", "Cells"))
                    w = tpi_type_warning(system, role, tpi_type)
                    if tpi_type == "pctl":
                        hi, _n, _var = pctl_reachable_max(rxo, ryo, rxi, ryi)
                        warnings.extend(pctl_class_warnings(system, role, hi))
                    if spec.get("save"):
                        item["save"] = spec["save"]
                        item["save_label"] = "TPI"
                        item["save_note"] = (
                            "TPI ({}) for '{}' from {}  {} {}".format(
                                tpi_type, role, in_raster, _tpi_type_tag(tpi_type),
                                _tpi_nbhd_tag(spec["outer"], spec.get("units", "Cells"))))
            elif kind == "slope":
                if spec.get("raster"):
                    _step("Reading slope raster for '{}'...".format(role))
                    item["src"] = _open(spec["raster"])
                    su = spec.get("declared_units") or read_slope_units(spec["raster"])
                    w = slope_units_warning(system, su)
                else:
                    su = spec.get("slope_units", "Degrees")
                    _step("Computing slope ({}) for '{}'...".format(
                        str(su).lower(), role))
                    item["src"] = _open(spec["dem"])
                    item["mode"] = "slope"
                    item["slope_units"] = su
                    item["zs"] = float(_hs._ELEV_METERS.get(
                        spec.get("elev_units", elev_units), 1.0))
                    if ground is None:
                        ground = _hs.ground_geometry(gt, width, height, sr, proj)
                    halo = max(halo, 1)
                    w = slope_units_warning(system, su)
                    if spec.get("save"):
                        item["save"] = spec["save"]
                        item["save_label"] = "slope"
                        item["save_note"] = "Slope ({}) for '{}'  {}".format(
                            su, role, _slope_unit_tag(su))
            else:                                  # external raster
                _step("Reading raster for '{}'...".format(role))
                item["src"] = _open(spec["raster"])
                item["standardize"] = bool(spec.get("standardize"))
                item["needs_stats"] = item["standardize"]   # whole-raster z-score (general)
            if w:
                warnings.append(w)
            plan.append(item)

        # Landform sanity check: the small-scale TPI's neighborhood should be
        # smaller than the large-scale one's (swapped or duplicated inputs
        # silently produce a wrong classification).
        w = tpi_scale_warning(nbhd_by_role.get("tpi_small"),
                              nbhd_by_role.get("tpi_large"))
        if w:
            warnings.append(w)

        for w in warnings:
            if messages is not None:
                messages.addWarningMessage(w)

        rows = strip_rows(height, width, halo)
        ranges = strip_ranges(height, rows)
        _hs._msg(messages, strip_note(height, width, rows, halo))

        # whole-raster statistics first (the DEM-scale z-score; a general
        # raster classified in 'standardized' units)
        stats_for = {}
        for item in plan:
            if item.get("needs_stats"):
                _step("Computing the whole-raster mean and standard deviation "
                      "for '{}'...".format(item["role"]))
                stats_for[item["role"]] = _whole_stats(item["src"], rows)

        _mh.phase_advance("Computing TPI, slope and classes...")
        _step("Classifying cells into {} classes from the TPI and slope values "
              "using '{}'...".format(len(system["classes"]), system["name"]))
        cls_path = _scratch_tif(scratch, "tpiclass")
        cw = _ClassWriter(gdal, cls_path, gt, proj, width, height, system["classes"])
        for item in plan:
            if item.get("save"):
                try:
                    path = _mh.resolve_raster_output(item["save"], messages)
                    savers[item["role"]] = (_FloatWriter(gdal, gt, proj, width, height),
                                            path, item["save_note"], item["save_label"])
                except Exception as e:
                    if messages is not None:
                        messages.addWarningMessage(
                            "Could not create the intermediate raster {}: {}. The "
                            "classification continues without it.".format(
                                item["save"], e))

        ncls = 0
        nvalid = 0
        obs_max = {}                 # role -> observed maximum of a supplied pctl TPI
        for i, (y0, y1) in enumerate(ranges):
            top = max(0, y0 - halo)
            bot = min(height, y1 + halo)
            sl = slice(y0 - top, y1 - top)
            blocks = {}              # source path -> (values, valid) for rows top..bot
            resolved = {}
            valid_all = None
            for item in plan:
                src = item["src"]
                if src.path not in blocks:
                    blocks[src.path] = src.read(top, bot)
                arr, v = blocks[src.path]
                mode = item["mode"]
                if mode == "tpi":
                    if item["tpi_type"] == "demstd":
                        st = stats_for.get(item["role"])
                        g = np.full(arr.shape, np.nan)
                        if st is not None:
                            mu, sd = st
                            g[v] = (arr[v] - mu) / sd if sd > 0 else 0.0
                    else:
                        rxo, ryo, rxi, ryi = item["radii"]
                        g = tpi_representation(
                            np.where(v, arr, 0.0), v, rxo[top:bot], ryo, rxi[top:bot],
                            ryi, item["tpi_type"], None, row0=top,
                            groups=item["groups"])
                elif mode == "slope":
                    g = _slope_block(arr, v, ground, top, item["zs"],
                                     item["slope_units"])
                else:                              # a supplied raster
                    g = np.where(v, arr, np.nan)
                    if item.get("standardize"):
                        st = stats_for.get(item["role"])
                        m = np.isfinite(g)
                        out = np.full_like(g, np.nan)
                        if st is not None:
                            mu, sd = st
                            out[m] = (g[m] - mu) / sd if sd > 0 else 0.0
                        g = out
                gi = g[sl]
                vi = v[sl]
                resolved[item["role"]] = gi
                valid_all = vi.copy() if valid_all is None else (valid_all & vi)
                if item.get("track_max"):
                    fin = gi[np.isfinite(gi)]
                    if fin.size:
                        obs_max[item["role"]] = max(
                            obs_max.get(item["role"], -np.inf), float(fin.max()))
                if item["role"] in savers:
                    savers[item["role"]][0].write(y0, gi, vi & np.isfinite(gi))
            if valid_all is None:
                valid_all = np.ones((y1 - y0, width), dtype=bool)
            cls = classify(system, resolved, valid_all)
            cw.write(y0, cls)
            ncls += int((cls != NODATA_OUT).sum())
            nvalid += int(valid_all.sum())
            blocks.clear()
            _mh.phase_step(i + 1, len(ranges))

        for item in plan:
            if item.get("track_max") and item["role"] in obs_max:
                for w in pctl_class_warnings(system, item["role"],
                                             obs_max[item["role"]], from_raster=True):
                    warnings.append(w)
                    if messages is not None:
                        messages.addWarningMessage(w)

        for role, (fw, path, note, label) in list(savers.items()):
            # Persist a computed TPI/slope intermediate to the user-chosen path
            # with the SAME float-raster delivery the Topographic Position
            # Index tool uses. The type/units marker is stamped into the
            # metadata summary so the classification tools can read it back.
            # A failed optional save must not abort the classification.
            try:
                _step("Saving generated {} raster for '{}'...".format(label, role))
                fw.finish(path, title=os.path.basename(str(path)), summary=note,
                          description=note, messages=messages)
                _hs._msg(messages, "Saved intermediate raster: {}".format(path))
            except Exception as e:
                fw.abandon()
                if messages is not None:
                    messages.addWarningMessage(
                        "Could not save the intermediate raster {}: {}. The "
                        "classification still completed.".format(path, e))
            savers.pop(role, None)

        _hs._msg(messages, "Classified {:,} of {:,} valid cells into {} classes "
                 "using '{}'.".format(ncls, nvalid, len(system["classes"]),
                                      system["name"]))

        _mh.phase_advance("Saving classified raster...")
        cw.finish()
        cw = None
        wnote = "" if not warnings else " UNIT-COMPATIBILITY WARNINGS: " + \
            " | ".join(warnings)
        _deliver_class_output(
            cls_path, out_raster, messages,
            title=os.path.basename(str(out_raster)),
            summary=("Created by {fam} from {src} using classification system "
                     "'{sys}'.{w}").format(fam=family.replace("_", " ").title(),
                                           src=str(in_raster), sys=system["name"],
                                           w=wnote),
            description=("Topographic classification ({fam}) produced by applying "
                         "the '{sys}' classification system to the supplied or "
                         "computed TPI and slope values. {cit}{w}").format(
                             fam=family.replace("_", " "), sys=system["name"],
                             cit=_TPI_CITATION, w=wnote),
            tags=("topographic position index, TPI, slope position, landform, "
                  "terrain classification, topography, Jenness Enterprises"),
            citation=_TPI_CITATION,
            class_names={int(c["value"]): c.get("name", str(c["value"]))
                         for c in system["classes"]})
        _mh.phase_advance("Finishing...")
        # Unique-value symbology on Class_Name (keeps our per-class colors) instead
        # of the raster's inherent 0-255 Colormap, whose unused values otherwise
        # clutter the legend with black swatches.
        lyrx = _mh.default_unique_value_lyrx(out_raster, system["classes"],
                                             messages=messages)
    except BaseException:
        if cw is not None:
            cw.abandon()
        for fw, _p, _n, _l in savers.values():
            fw.abandon()
        raise
    finally:
        for s in opened.values():
            s.close()
    _mh.end_phases()
    _hs._msg(messages, "Classification complete: {}".format(out_raster))
    return lyrx


def run_tpi(in_raster, out_raw=None, out_dev=None, out_pctl=None,
            out_demstd=None, out_devtpi=None, shape="Circle", outer=10.0,
            inner=0.0, units="Cells", elev_units="Meters", messages=None):
    """Compute the Topographic Position Index of a DEM over one neighborhood,
    writing up to five optional continuous outputs (metrics of Wilson & Gallant
    2000, plus one experimental):
      * out_raw    -- 'diff' (eq. 3.27): elevation minus neighborhood mean, in
                      the DEM's elevation units
      * out_dev    -- 'dev'  (eq. 3.30): (elevation - neighborhood mean) /
                      neighborhood SD of elevation, in standard-deviation units
      * out_pctl   -- 'pctl' (eq. 3.31): 100 x count(neighborhood elevations <
                      focal) / neighborhood cell count
      * out_demstd -- (elevation - DEM mean elevation) / DEM SD of elevation,
                      in standard-deviation units (whole-raster, neighborhood-
                      independent)
      * out_devtpi -- EXPERIMENTAL: raw TPI / neighborhood SD of the raw TPI
                      values (standardized against local TPI roughness rather
                      than local elevation roughness)
    All internal (no Spatial Analyst). The DEM is processed in row strips
    (see the strip section above); every output receives each strip's rows
    as they are computed."""
    gdal = _mh._gdal()
    wanted = []
    if out_raw:
        wanted.append(("raw", _mh.resolve_raster_output(out_raw, messages),
                       "TPI (elevation units)"))
    if out_dev:
        wanted.append(("dev", _mh.resolve_raster_output(out_dev, messages),
                       "TPI (neighborhood std. dev.)"))
    if out_pctl:
        wanted.append(("pctl", _mh.resolve_raster_output(out_pctl, messages),
                       "TPI (percentile)"))
    if out_demstd:
        wanted.append(("demstd", _mh.resolve_raster_output(out_demstd, messages),
                       "TPI (DEM-scale std. dev.)"))
    if out_devtpi:
        wanted.append(("dev_tpi", _mh.resolve_raster_output(out_devtpi, messages),
                       "TPI (TPI-roughness std. dev., experimental)"))
    if not wanted:
        raise arcpy.ExecuteError("Choose at least one output raster.")
    keys = {u for (u, _p, _l) in wanted}
    need_focal = bool(keys & {"raw", "dev", "dev_tpi"})

    phases = [("Reading the DEM...", 1),
              ("Computing TPI...", 6 if "pctl" not in keys else 10)]
    phases += [("Saving {}...".format(l), 1) for (_u, _p, l) in wanted]
    _mh.begin_phases(phases)

    scratch = arcpy.env.scratchFolder
    src = _Source(in_raster, scratch, messages)
    writers = {}
    try:
        width, height = src.width, src.height
        if width < 3 or height < 3:
            raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
        gt = src.gt
        proj = src.proj
        sr = arcpy.Describe(in_raster).spatialReference
        ground = None
        if units != "Cells":
            # a neighborhood in linear units is sized from true ground
            # distances: post the ground-geometry note once per run
            ground = _hs.ground_geometry(gt, width, height, sr, proj, messages)

        rxo, ryo, rxi, ryi = _cell_radii(
            gt, height, sr, outer, inner if shape == "Annulus" else 0.0, units,
            width, proj, ground)
        groups = _kernel_groups(rxo, ryo, rxi, ryi) if need_focal else None
        halo = 0
        for u in keys:
            halo = max(halo, _tpi_halo(u, ryo))
        rows = strip_rows(height, width, halo)
        ranges = strip_ranges(height, rows)
        _hs._msg(messages, strip_note(height, width, rows, halo))

        dem_stats = None
        if "demstd" in keys:
            _hs._set_label("Computing the DEM's mean and standard deviation...")
            dem_stats = _whole_stats(src, rows)
            if dem_stats is None:
                raise arcpy.ExecuteError("The DEM has no valid cells.")

        _hs._msg(messages, "TPI ({}): {} neighborhood, radius {:g} {}{}.".format(
            _TPI_CITATION, shape.lower(), outer, units.lower(),
            (", inner {:g}".format(inner) if shape == "Annulus" and inner else "")))
        if "pctl" in keys:
            hi, n_max, varies = pctl_reachable_max(rxo, ryo, rxi, ryi)
            if n_max is None:
                _hs._msg(messages, "Percentile TPI: the annulus leaves out the "
                         "centre cell, so values can reach 100.")
            else:
                _hs._msg(messages, "Percentile TPI: with a neighborhood of {}{} "
                         "cells including the centre cell, the highest possible "
                         "value is {}{:.1f} (a cell higher than every "
                         "neighbor).".format("at most " if varies else "", n_max,
                                             "at most " if varies else "", hi))

        for (u, _path, _lbl) in wanted:
            writers[u] = _FloatWriter(gdal, gt, proj, width, height)

        _mh.phase_advance("Computing TPI...")
        nvalid = 0
        for i, (y0, y1) in enumerate(ranges):
            top = max(0, y0 - halo)
            bot = min(height, y1 + halo)
            sl = slice(y0 - top, y1 - top)
            z, valid = src.read(top, bot)
            nvalid += int(valid[sl].sum())
            grids = {}
            if need_focal:
                fs = focal_stats(np.where(valid, z, 0.0), valid, groups, top,
                                 want_sd=("dev" in keys))
                ok = valid & (fs["count"] >= 0.5)
                tpi = np.where(ok, z - fs["mean"], np.nan)
                if "raw" in keys:
                    grids["raw"] = tpi
                if "dev" in keys:
                    sd = fs["sd"]
                    with np.errstate(invalid="ignore", divide="ignore"):
                        g = np.where(sd > 0, tpi / np.where(sd > 0, sd, 1.0), 0.0)
                    g[~np.isfinite(tpi)] = np.nan
                    grids["dev"] = g
                if "dev_tpi" in keys:
                    sdt = focal_stats(np.where(ok, tpi, 0.0), ok, groups, top,
                                      want_sd=True)["sd"]
                    with np.errstate(invalid="ignore", divide="ignore"):
                        g = np.where(sdt > 0, tpi / np.where(sdt > 0, sdt, 1.0), 0.0)
                    g[~np.isfinite(tpi)] = np.nan
                    grids["dev_tpi"] = g
            if "demstd" in keys:
                gmean, gsd = dem_stats
                g = np.full(z.shape, np.nan)
                g[valid] = (z[valid] - gmean) / gsd if gsd > 0 else 0.0
                grids["demstd"] = g
            if "pctl" in keys:
                grids["pctl"] = focal_percentile(z, valid, rxo[top:bot], ryo,
                                                 rxi[top:bot], ryi, None)
            for u, w in writers.items():
                g = grids[u][sl]
                w.write(y0, g, valid[sl] & np.isfinite(g))
            grids.clear()
            _mh.phase_step(i + 1, len(ranges))
        if nvalid == 0:
            raise arcpy.ExecuteError("The DEM has no valid cells.")

        descs = {
            "raw": ("Topographic Position Index (TPI): each cell's elevation minus "
                    "the mean elevation of its neighborhood, in the DEM's elevation "
                    "units ({eu}). Positive = above the surroundings (ridges, "
                    "hills); negative = below (valleys, channels). This is Wilson "
                    "and Gallant's (2000) 'diff' (equation 3.27). References: {w} "
                    "{ws}").format(eu=elev_units, w=_WG_CITATION, ws=_WEISS_CITATION),
            "dev": ("TPI standardized at neighborhood scale: (elevation - "
                    "neighborhood mean elevation) / standard deviation of elevation "
                    "within the neighborhood, in standard-deviation units (0 where "
                    "the neighborhood is flat). This is Wilson and Gallant's (2000) "
                    "'dev' (equation 3.30). References: {w} {ws}").format(
                        w=_WG_CITATION, ws=_WEISS_CITATION),
            "pctl": ("TPI in percentile units: 100 x (count of neighborhood "
                     "elevations less than the focal elevation) / (number of cells "
                     "in the neighborhood). Values near 0 mark cells relatively very "
                     "low compared with their neighbors; values near 100 mark "
                     "relatively high cells. This is Wilson and Gallant's (2000) "
                     "'pctl' (equation 3.31). Reference: {w}").format(w=_WG_CITATION),
            "demstd": ("TPI standardized at DEM scale: (elevation - DEM mean "
                       "elevation) / DEM standard deviation of elevation, in "
                       "standard-deviation units (dimensionless), computed over the "
                       "whole raster. Reference: {ws}").format(ws=_WEISS_CITATION),
            "dev_tpi": ("EXPERIMENTAL TPI standardized against local TPI roughness: "
                        "raw TPI (elevation minus neighborhood mean elevation) "
                        "divided by the standard deviation of the raw TPI values "
                        "within the same neighborhood, in standard-deviation units "
                        "(0 where the neighborhood TPI is uniform). Unlike 'dev', "
                        "which divides by the SD of ELEVATION, this divides by the "
                        "SD of the TPI itself; it is offered for exploration and is "
                        "not part of Wilson and Gallant (2000). References: {w} {ws}"
                        ).format(w=_WG_CITATION, ws=_WEISS_CITATION),
        }
        summs = {
            "raw": "TPI (elevation units) from {s}: {sh} neighborhood, radius {r:g} {u}.",
            "dev": "TPI standardized at neighborhood scale from {s}.",
            "pctl": "TPI percentile within neighborhood from {s}.",
            "demstd": "TPI standardized at DEM scale (elevation z-score) from {s}.",
            "dev_tpi": "Experimental TPI standardized by neighborhood SD of TPI from {s}.",
        }
        out_lyrx = {}
        for (u, path, lbl) in wanted:
            _mh.phase_advance("Saving {}...".format(lbl))
            summ = (summs[u].format(s=in_raster, sh=shape.lower(), r=outer,
                                    u=units.lower()) + "  " + _tpi_type_tag(u))
            if u != "demstd":                     # demstd is neighborhood-independent
                summ += " " + _tpi_nbhd_tag(outer, units)
            w = writers.pop(u)
            out_lyrx[u] = w.finish(
                path, title=os.path.basename(str(path)), summary=summ,
                description=descs[u], messages=messages)
    except BaseException:
        for w in writers.values():
            w.abandon()
        raise
    finally:
        src.close()
    _mh.end_phases()
    _hs._msg(messages, "Topographic Position Index complete.")
    return out_lyrx                                # {key: lyrx path}
