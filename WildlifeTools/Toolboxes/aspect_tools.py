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

"""Aspect Tools -- circular-statistics geoprocessing for aspect rasters.

The first tool here is Aspect Focal Statistics: a neighborhood (focal)
statistics tool built specifically for aspect, i.e. a CIRCULAR variable where
0 deg and 360 deg are the same direction and the ordinary (linear) mean,
variance, etc. are wrong. It mirrors Esri's Focal Statistics tool (all the same
neighborhood shapes) but computes circular statistics, needs no Spatial
Analyst, and works on both projected and geographic aspect rasters using the
same geodesic per-row neighborhood machinery as the rest of this toolbox.

Aspect convention: degrees, 0-360 measured CLOCKWISE from north (compass), with
-1 meaning flat (no aspect). Flat cells carry no direction, so they are IGNORED
in every neighborhood; the fill value for an all-flat neighborhood depends on
the statistic (see ``_ALLFLAT_FILL``), or the whole cell can be set to NoData.

D-infinity flow-direction rasters (Esri Flow Direction, DINF) are essentially
aspect too -- the direction of steepest descent -- but reported in the
MATHEMATICAL convention (0 deg = east, increasing counter-clockwise). All the
dispersion/concentration statistics are identical either way (they are
invariant under rotation and reflection of the circle); only the two direction-
valued outputs differ, and the tool converts those to compass when told the
input is in the mathematical convention.

Circular-statistics references (see Issues with Directional Data, Jenness 2012, and
the sources it cites): Mardia & Jupp 2000; Fisher 1993; Batschelet 1981;
Zar 1999. The von Mises concentration estimate is Best and Fisher's (1981,
p. 495) piecewise approximation (Fisher 1993 eq. 4.40), with their
small-sample correction (p. 498; Fisher 1993 eq. 4.41, applied for n <= 15);
the confidence interval of the mean direction is Fisher's eqs. 4.42-4.43.
"""

import math
import os
import re

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import topographic_tools as _tt

NODATA_OUT = _tt.NODATA_OUT

# ---------------------------------------------------------------------------
#  Choices
# ---------------------------------------------------------------------------
# key -> label (dropdown order). ONE statistic per run (mirrors Focal Stats).
ASPECT_STATS = [
    ("MEAN", "Mean Direction"),
    ("MEDIAN", "Median Direction"),
    ("RESULTANT", "Mean Resultant Length"),
    ("CIRCVAR", "Circular Variance"),
    ("ANGVAR", "Angular Variance"),
    ("CIRCSD", "Circular Standard Deviation"),
    ("ANGDEV", "Angular Deviation"),
    ("RANGE", "Circular Range"),
    ("RAYLEIGH", "Rayleigh Test p-value"),
    ("KAPPA", "von Mises Concentration (kappa)"),
    ("PROPDEF", "Proportion With Defined Aspect"),
]
STAT_LABELS = [lbl for _, lbl in ASPECT_STATS]
STAT_KEY = {lbl: k for k, lbl in ASPECT_STATS}
STAT_LABEL = {k: lbl for k, lbl in ASPECT_STATS}

# Direction-valued outputs (0-360; convention-dependent; -1 = flat sentinel).
_DIRECTION_STATS = {"MEAN", "MEDIAN"}
# Order-statistics (need a per-cell neighborhood gather, not a linear sum).
_ORDER_STATS = {"MEDIAN", "RANGE"}

# Fill value when EVERY cell in a neighborhood is flat (no direction at all).
#   direction  -> -1  (the aspect "flat" sentinel; matches the input convention)
#   dispersion ->  0  (no directional variation)
#   resultant  ->  1  (the exact complement of variance: R-bar = 1 <=> V = 0)
#   Rayleigh p ->  1  (no detectable preferred direction)
#   kappa      -> -1  (its consistent limit is +inf, which is unusable)
#   proportion ->  0  (no cell has a defined aspect)
_ALLFLAT_FILL = {
    "MEAN": -1.0, "MEDIAN": -1.0,
    "RESULTANT": 1.0, "RAYLEIGH": 1.0, "KAPPA": -1.0,
    "CIRCVAR": 0.0, "ANGVAR": 0.0, "CIRCSD": 0.0, "ANGDEV": 0.0,
    "RANGE": 0.0, "PROPDEF": 0.0,
}

NEIGHBORHOODS = ["Rectangle", "Circle", "Annulus", "Wedge", "Irregular", "Weight"]
NBR_UNITS = _hs.RADIUS_UNITS             # ["Cells","Meters","Kilometers","Feet","Miles"]
_UNIT_M = dict(_hs._RADIUS_METERS)
_UNIT_M["Cells"] = None
INPUT_CONVENTIONS = ["Compass (0 = north, clockwise)",
                     "Mathematical (0 = east, counter-clockwise; e.g. D-infinity)"]
# Earlier releases called the first choice "Sentinel (...)"; the .pyt maps
# that legacy string to FLAT_HANDLING[0] so existing scripts keep working.
FLAT_HANDLING = ["Flat value (-1 / 0 / 1 by statistic)", "NoData"]

# ---- Aspect Transformation ------------------------------------------------
# key -> label (checklist order; the user may pick several at once).
ASPECT_TRANSFORMS = [
    ("NS", "Reclassify: North / South"),
    ("EW", "Reclassify: East / West"),
    ("NESW", "Reclassify: North / East / South / West"),
    ("EIGHT", "Reclassify: 8 directions (N / NE / E / SE / S / SW / W / NW)"),
    ("NORTHNESS", "Northness (cosine of aspect; 1 = north, -1 = south)"),
    ("EASTNESS", "Eastness (sine of aspect; 1 = east, -1 = west)"),
    ("TRIGDEV", "Trigonometric deviation from a bearing (1 = toward, -1 = away)"),
    ("NUMDEV", "Numeric deviation from a bearing (degrees, 0 - 180)"),
]
TRANSFORM_LABELS = [lbl for _, lbl in ASPECT_TRANSFORMS]
TRANSFORM_KEY = {lbl: k for k, lbl in ASPECT_TRANSFORMS}
_TLABEL = {k: lbl for k, lbl in ASPECT_TRANSFORMS}     # key -> label
_RECLASS_KEYS = {"NS", "EW", "NESW", "EIGHT"}
_BEARING_KEYS = {"TRIGDEV", "NUMDEV"}     # use the target-bearing parameter

FLAT_TRANSFORM = ["Reclassifications keep a Flat class (trig transforms are "
                  "NoData at flat cells)",
                  "All transforms: flat cells -> NoData"]

_NODATA_INT = -9999                        # int NoData (distinct from -1 = flat)
_FLAT_COLOR = [255, 235, 190]              # pale tan, matches the aspect lyrx mask

# Reclass class codes -> (name, RGB) for the value attribute table + symbology.
# Directional colors follow the aspect color wheel used by the menu icon.
_RECLASS_DEFS = {
    "NS":   {1: ("North", [230, 74, 69]), 2: ("South", [63, 182, 166])},
    "EW":   {1: ("East", [231, 198, 59]), 2: ("West", [106, 92, 192])},
    "NESW": {1: ("North", [230, 74, 69]), 2: ("East", [231, 198, 59]),
             3: ("South", [63, 182, 166]), 4: ("West", [106, 92, 192])},
    "EIGHT": {1: ("North", [230, 74, 69]), 2: ("Northeast", [238, 139, 61]),
              3: ("East", [231, 198, 59]), 4: ("Southeast", [143, 194, 74]),
              5: ("South", [63, 182, 166]), 6: ("Southwest", [62, 143, 208]),
              7: ("West", [106, 92, 192]), 8: ("Northwest", [197, 84, 166])},
}
_RECLASS_SUFFIX = {"NS": "_NS", "EW": "_EW", "NESW": "_NESW", "EIGHT": "_8Dir"}

# Derived output parameter per transformation (Aspect Transformation), so a
# ModelBuilder model can connect one transformation directly to the next
# tool: key -> (parameter name, display name).
TRANSFORM_OUTPUT_PARAMS = [
    ("NS", "out_ns", "North / South raster"),
    ("EW", "out_ew", "East / West raster"),
    ("NESW", "out_nesw", "North / East / South / West raster"),
    ("EIGHT", "out_8dir", "8-direction raster"),
    ("NORTHNESS", "out_northness", "Northness raster"),
    ("EASTNESS", "out_eastness", "Eastness raster"),
    ("TRIGDEV", "out_trigdev", "Trigonometric deviation raster"),
    ("NUMDEV", "out_numdev", "Numeric deviation raster"),
]

_TW_CITATION = (
    "Trimble, G. R., Jr., and S. Weitzman. 1956. Site index studies of upland "
    "oaks in the northern Appalachians. Forest Science 2:162-173.")
_BEERS_CITATION = (
    "Beers, T. W., P. E. Dress, and L. C. Wensel. 1966. Aspect transformation "
    "in site productivity research. Journal of Forestry 64(10):691-692.")
_POLAR_CITATION = (
    "Jenness, J. 2014. Polar plots for ArcGIS. Jenness Enterprises. "
    "http://www.jennessent.com/arcgis/polar_plots.htm.")
# Northness / Eastness: the sine/cosine decomposition of aspect.
_NE_CITATION = (
    "Jenness, J. 2012. Issues with directional data. Remotely Wild, "
    "newsletter of the Spatial Ecology and Telemetry Working Group of The "
    "Wildlife Society.")

_KAPPA_CAP = 1.0e4                        # cap the von Mises kappa (avoids +inf)


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)

_CIRC_CITATION = (
    "Circular statistics follow Mardia, K. V., and P. E. Jupp. 2000. "
    "Directional Statistics (Wiley); Fisher, N. I. 1993. Statistical Analysis "
    "of Circular Data (Cambridge Univ. Press); Batschelet, E. 1981. Circular "
    "Statistics in Biology (Academic Press); and Zar, J. H. 1999. "
    "Biostatistical Analysis, 4th ed. (Prentice Hall); the Rayleigh p is "
    "Zar's eq. 27.4. See also Jenness, J. 2012. Issues with directional "
    "data. Remotely Wild, newsletter of the Spatial Ecology and Telemetry "
    "Working Group of The Wildlife Society.")


# ---------------------------------------------------------------------------
#  Source-type detection (aspect vs. flow direction)
# ---------------------------------------------------------------------------
_D8_CODES = {1, 2, 4, 8, 16, 32, 64, 128}


def _lineage_source_kind(in_raster):
    """Classify a raster from the geoprocessing tool that WROTE it, as recorded
    in its metadata lineage: one <Process ToolSource=".../ToolName"> element per
    tool run, its text the tool's arguments. Only the most recent
    direction-producing process counts, and only by its tool name and
    arguments -- never by words that merely appear somewhere in the metadata
    (a TWI raster's description mentions "flow direction" and "D-Infinity",
    which must not make it look like a D-infinity flow-direction raster).
    Returns ('dinf'|'d8'|'aspect', reason) or None when the lineage says
    nothing (copied, exported or third-party rasters often lose it)."""
    import arcpy
    try:
        try:
            path = arcpy.Describe(in_raster).catalogPath
        except Exception:
            path = in_raster
        xml = arcpy.metadata.Metadata(path).xml or ""
    except Exception:
        return None
    found = None
    for m in re.finditer(r'<Process\b([^>]*)>(.*?)</Process>', xml, re.S | re.I):
        src = re.search(r'ToolSource="([^"]*)"', m.group(1), re.I)
        tool = re.split(r"[\\/]", src.group(1))[-1].lower() if src else ""
        args = m.group(2)
        if tool == "flowdirection":
            if re.search(r"\bDINF\b", args, re.I):
                found = ("dinf", "geoprocessing history (Flow Direction, D-infinity)")
            else:
                found = ("d8", "geoprocessing history (Flow Direction, D8 or MFD)")
        elif (tool == "aspect"
              or (tool == "surfaceparameters"
                  and re.search(r"\bASPECT\b", args, re.I))
              # our focal tool writes directions only for these two statistics
              or (tool == "aspectfocalstatistics"
                  and re.search(r"(Mean|Median) Direction", args, re.I))):
            found = ("aspect", "geoprocessing history ({})".format(
                re.split(r"[\\/]", src.group(1))[-1]))
    return found



def _detect_source_kind(in_raster, arr=None, nodata=None):
    """Best-effort classification of ``in_raster`` as one of 'aspect', 'dinf',
    'd8', or 'unknown', from geoprocessing lineage in the raster metadata and,
    as a fallback, the pixel values themselves. Lineage is often absent (copied
    or exported rasters lose it), so this only ever drives an informational
    message -- never correctness."""
    import arcpy
    kind = _lineage_source_kind(in_raster)
    if kind:
        return kind
    # Value fallback: a D8 raster holds only the powers of two 1..128. Check the
    # cheap max FIRST (a reduction, no copy/sort) and only run the expensive
    # np.unique when every value could still be a D8 code -- an ordinary aspect
    # raster (max ~360 > 128) short-circuits here, avoiding a whole-grid sort.
    if arr is not None:
        finite = np.isfinite(arr)
        if nodata is not None:
            finite &= (arr != nodata)
        finite &= (arr >= 0)
        if finite.any():
            mx = float(np.max(arr, where=finite, initial=-1.0))
            if 0.0 < mx <= 128.0:
                u = np.unique(arr[finite])
                if u.size <= 8 and np.all(u == np.rint(u)) and \
                        set(int(v) for v in u) <= _D8_CODES:
                    return "d8", "pixel values (only the D8 codes 1-128 are present)"
    return "unknown", ""


# ---------------------------------------------------------------------------
#  Neighborhood kernels
# ---------------------------------------------------------------------------
def _parse_kernel_file(path):
    """Read an Esri-style ASCII kernel file: first line 'ncols nrows', then the
    matrix. 0 (or blank) = excluded; other values are weights (Irregular files
    are all 0/1). Returns a float64 weight array centered on its middle cell."""
    with open(path, "r") as fh:
        tokens = fh.read().split()
    if len(tokens) < 2:
        import arcpy
        raise arcpy.ExecuteError("Kernel file is empty or malformed: " + str(path))
    ncols, nrows = int(float(tokens[0])), int(float(tokens[1]))
    vals = [float(t) for t in tokens[2:2 + ncols * nrows]]
    import arcpy
    if len(vals) < ncols * nrows:
        raise arcpy.ExecuteError(
            "Kernel file {} declares {}x{} cells but has only {} values.".format(
                path, ncols, nrows, len(vals)))
    k = np.array(vals, dtype=np.float64).reshape(nrows, ncols)
    # Pad to odd size so the focal cell sits at the geometric center.
    pr = 0 if nrows % 2 else 1
    pc = 0 if ncols % 2 else 1
    if pr or pc:
        k = np.pad(k, ((0, pr), (0, pc)))
    return k


def _build_kernel(nbr, hx, hy, hx_in=None, hy_in=None, a0=None, a1=None):
    """Weight array (float64) for one neighborhood, dimensions given as
    half-extents IN CELLS (hx horizontal, hy vertical; hx_in/hy_in = inner
    half-extents for an annulus; a0/a1 = wedge start/end angles, degrees,
    counter-clockwise from east). Always odd-sized and centered on the focal
    cell."""
    nx = max(1, int(math.ceil(hx - 1e-9)))
    ny = max(1, int(math.ceil(hy - 1e-9)))
    dc = np.arange(-nx, nx + 1, dtype=np.float64)[None, :]
    dr = np.arange(-ny, ny + 1, dtype=np.float64)[:, None]
    if nbr == "Rectangle":
        k = ((np.abs(dc) <= hx + 1e-9) & (np.abs(dr) <= hy + 1e-9))
    elif nbr == "Circle":
        d2 = (dc / max(hx, 1e-9)) ** 2 + (dr / max(hy, 1e-9)) ** 2
        k = d2 <= 1.0 + 1e-9
    elif nbr == "Annulus":
        d2o = (dc / max(hx, 1e-9)) ** 2 + (dr / max(hy, 1e-9)) ** 2
        d2i = (dc / max(hx_in, 1e-9)) ** 2 + (dr / max(hy_in, 1e-9)) ** 2
        k = (d2o <= 1.0 + 1e-9) & (d2i > 1.0 + 1e-9)
    elif nbr == "Wedge":
        d2 = (dc / max(hx, 1e-9)) ** 2 + (dr / max(hy, 1e-9)) ** 2
        inside = d2 <= 1.0 + 1e-9
        ang = np.degrees(np.arctan2(-dr + 0.0 * dc, dc + 0.0 * dr)) % 360.0
        span = (a1 - a0) % 360.0
        if span == 0.0:
            span = 360.0
        rel = (ang - a0) % 360.0
        center = (dc == 0) & (dr == 0)
        k = inside & ((rel <= span + 1e-9) | center)
    else:
        import arcpy
        raise arcpy.ExecuteError("Unknown neighborhood type: " + str(nbr))
    return k.astype(np.float64)


def _neighborhood_groups(nbr, P, gt, height, sr, units, kernel_arr, messages,
                         width=None, proj=None):
    """Return (groups, note): ``groups`` is a list of (kernel, row_mask). One
    group for Cells units (constant cell size); several for a linear-unit
    neighborhood (the true ground cell size varies by row -- on a geographic
    raster and on a projected one with scale distortion alike -- so the
    neighborhood spans a different number of cells per row: the same
    per-row treatment VRM and the blur tool use)."""
    import arcpy
    if nbr in ("Irregular", "Weight"):
        note = "kernel file, {}x{} cells".format(
            kernel_arr.shape[1], kernel_arr.shape[0])
        return [(kernel_arr, slice(None))], note

    def conv(length):
        rx, ry, _ = _tt._radius_row_cells(gt, height, sr, float(length), units,
                                          width, proj)
        return rx, ry

    hx_in = hy_in = None
    if nbr == "Rectangle":
        hxr, _ = conv(P["width"] / 2.0)
        _, hy = conv(P["height"] / 2.0)
        note = "rectangle {:g} x {:g} {}".format(P["width"], P["height"],
                                                 units.lower())
    elif nbr == "Circle":
        hxr, hy = conv(P["radius"])
        note = "circle radius {:g} {}".format(P["radius"], units.lower())
    elif nbr == "Annulus":
        hxr, hy = conv(P["outer"])
        hxr_in, hy_in = conv(P["inner"])
        note = "annulus {:g}-{:g} {}".format(P["inner"], P["outer"], units.lower())
    elif nbr == "Wedge":
        hxr, hy = conv(P["radius"])
        note = "wedge radius {:g} {}, {:g} to {:g} deg".format(
            P["radius"], units.lower(), P["start"], P["end"])
    else:
        raise arcpy.ExecuteError("Unknown neighborhood type: " + str(nbr))

    if units != "Cells":
        # a neighborhood in linear units is sized from true ground
        # distances: post the ground-geometry note once per run
        import hillshade as _hs_note
        _hs_note.ground_geometry(gt, width or 1, height, sr, proj, messages)
    key = np.rint(hxr)
    groups = []
    for kv in np.unique(key):
        rows = (key == kv)
        hx = float(np.mean(hxr[rows]))
        hxi = float(np.mean(hxr_in[rows])) if nbr == "Annulus" else None
        k = _build_kernel(
            nbr, hx, float(hy), hxi,
            float(hy_in) if hy_in is not None else None,
            P.get("start"), P.get("end"))
        groups.append((k, rows))
    if len(groups) > 1:
        _say(messages,
            "Geographic raster: the neighborhood spans a varying number of "
            "cells by latitude; computed with {} per-row kernel groups.".format(
                len(groups)))
    return groups, note


# ---------------------------------------------------------------------------
#  Order statistics over a neighborhood (median direction, circular range)
# ---------------------------------------------------------------------------
def _offsets(kernel):
    ny, nx = kernel.shape[0] // 2, kernel.shape[1] // 2
    rr, cc = np.nonzero(kernel > 0)
    return [(int(r) - ny, int(c) - nx) for r, c in zip(rr, cc)]


def _order_stat(rad, dvalid, kernel, stat, messages):
    """Circular median or range (degrees) per cell over ``kernel``'s footprint,
    ignoring flat/NoData cells. Row-blocked to bound memory."""
    H, W = rad.shape
    ny, nx = kernel.shape[0] // 2, kernel.shape[1] // 2
    offs = _offsets(kernel)
    K = len(offs)
    if stat == "MEDIAN" and K > 500 and messages is not None:
        _warn(messages,
            "The median direction over a large neighborhood ({} cells) is "
            "computationally heavy and may run slowly.".format(K))
    a = np.where(dvalid, rad, np.nan).astype(np.float32)
    ap = np.pad(a, ((ny, ny), (nx, nx)), constant_values=np.nan)
    out = np.full((H, W), np.nan, np.float32)
    block = max(1, min(H, int(4_000_000 / max(1, K * W))))
    n_blocks = (H + block - 1) // block
    for bi, r0 in enumerate(range(0, H, block), 1):
        if n_blocks > 1:
            try:
                import arcpy
                arcpy.SetProgressorLabel(
                    "Computing {} block {:,} of {:,}...".format(
                        "median direction" if stat == "MEDIAN"
                        else "circular range", bi, n_blocks))
            except Exception:
                pass
        r1 = min(H, r0 + block)
        bh = r1 - r0
        stack = np.empty((K, bh, W), np.float32)
        for i, (dr, dc) in enumerate(offs):
            stack[i] = ap[ny + r0 + dr: ny + r1 + dr, nx + dc: nx + dc + W]
        if stat == "RANGE":
            out[r0:r1] = _circular_range(stack)
        else:
            out[r0:r1] = _circular_median(stack)
    return out


def _circular_range(stack):
    """Smallest arc (degrees) containing all angles, per column of ``stack``
    (axis 0 = samples, radians, NaN = missing). 360 - largest angular gap."""
    s = np.sort(stack, axis=0)                       # NaNs sort to the end
    m = np.sum(np.isfinite(stack), axis=0)
    twopi = 2.0 * math.pi
    bh, W = stack.shape[1], stack.shape[2]
    out = np.full((bh, W), np.nan)
    valid = m >= 1
    out[valid] = 0.0                                 # 1 sample -> range 0
    multi = m >= 2
    if not np.any(multi):
        return np.degrees(out)
    diffs = np.diff(s, axis=0)                        # gaps between neighbors
    diffs = np.where(np.isfinite(diffs), diffs, -np.inf)
    max_inner = np.max(diffs, axis=0)
    # wrap gap = first + 2pi - last valid; index the last valid per column
    idx_last = np.clip(m - 1, 0, stack.shape[0] - 1)
    last = np.take_along_axis(s, idx_last[None], axis=0)[0]
    first = s[0]
    wrap = first + twopi - last
    max_gap = np.maximum(max_inner, wrap)
    rng = twopi - max_gap
    out[multi] = rng[multi]
    return np.degrees(np.clip(out, 0.0, twopi))


def _circular_median(stack):
    """Sample circular median (radians->degrees) per column: the observed angle
    minimizing the summed circular distance to the others (Fisher 1993,
    Mardia & Jupp 2000). O(K^2) in the neighborhood size K."""
    K, bh, W = stack.shape
    twopi = 2.0 * math.pi
    finite = np.isfinite(stack)
    m = np.sum(finite, axis=0)
    best_cost = np.full((bh, W), np.inf)
    best_val = np.full((bh, W), np.nan)
    for i in range(K):
        ai = stack[i]
        d = np.abs(ai[None] - stack)                 # (K,bh,W)
        d = np.minimum(d, twopi - d)
        d = np.where(finite, d, 0.0)                 # missing others contribute 0
        cost = np.sum(d, axis=0)
        cost = np.where(np.isfinite(ai), cost, np.inf)   # i must itself exist
        take = cost < best_cost
        best_cost = np.where(take, cost, best_cost)
        best_val = np.where(take, ai, best_val)
    best_val = np.where(m >= 1, best_val, np.nan)
    return np.degrees(best_val) % 360.0


# ---------------------------------------------------------------------------
#  Circular-statistics from the resultant sums
# ---------------------------------------------------------------------------
def _kappa_small_sample(kml, n):
    """Best and Fisher's (1981, p. 498) correction of the maximum-likelihood
    kappa for small samples (Fisher 1993 eq. 4.41, applied for n <= 15),
    where the uncorrected estimate is biased upward. Vectorized; ``n`` may be fractional (an effective sample size).
    Values with n > 15 pass through unchanged."""
    k = np.asarray(kml, dtype=np.float64)
    nn = np.broadcast_to(np.asarray(n, dtype=np.float64), k.shape)
    small = (nn >= 1.0) & (nn <= 15.0) & (k > 0.0)
    if not small.any():
        return k
    out = k.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        lo = small & (k < 2.0)
        out[lo] = np.maximum(k[lo] - 2.0 / (nn[lo] * k[lo]), 0.0)
        hi = small & (k >= 2.0)
        out[hi] = (nn[hi] - 1.0) ** 3 * k[hi] / (nn[hi] ** 3 + nn[hi])
    return out


def _fisher_interval_suitable(kappa, n):
    """Fisher's (1993) Table 4.3: whether the standard-error confidence
    interval (rather than a bootstrap) is the recommended method for this
    kappa and sample size."""
    if kappa >= 2.0:
        return True
    if kappa >= 1.5:
        return n >= 10
    if kappa >= 1.0:
        return n >= 15
    if kappa >= 0.4:
        return n >= 25
    return False


def _kappa_from_rbar(rbar):
    """von Mises concentration, the piecewise approximation to the
    maximum-likelihood estimate (Best and Fisher 1981 p. 495; Fisher 1993
    eq. 4.40), vectorized and capped. Callers apply the small-sample
    correction (_kappa_small_sample) with their own n."""
    r = np.clip(rbar, 0.0, 0.999999)
    k = np.empty_like(r)
    lo = r < 0.53
    mid = (r >= 0.53) & (r < 0.85)
    hi = r >= 0.85
    k[lo] = 2.0 * r[lo] + r[lo] ** 3 + 5.0 * r[lo] ** 5 / 6.0
    k[mid] = -0.4 + 1.39 * r[mid] + 0.43 / (1.0 - r[mid])
    denom = (r[hi] ** 3 - 4.0 * r[hi] ** 2 + 3.0 * r[hi])
    k[hi] = 1.0 / np.where(np.abs(denom) < 1e-12, 1e-12, denom)
    return np.clip(k, 0.0, _KAPPA_CAP)


def _rayleigh_p(n, rbar):
    """Rayleigh test p-value (uniformity vs. a preferred direction), Zar 1999
    eq. 27.4 (derived from Greenwood and Durand 1955), with Rayleigh's
    R = n * rbar:
        P = exp[ sqrt(1 + 4n + 4(n^2 - R^2)) - (1 + 2n) ]
    The same formula Topography along Lines uses (unified 2026-09-24; this
    function previously used a series approximation, within 0.0002 of it).
    n = number of directions, rbar = mean resultant length. Computed in
    float64 whatever the input type (Aspect Focal Statistics passes float32)."""
    n = np.asarray(n, dtype=np.float64)
    rbar = np.asarray(rbar, dtype=np.float64)
    R = n * rbar
    with np.errstate(over="ignore", invalid="ignore"):
        p = np.exp(np.sqrt(np.maximum(1.0 + 4.0 * n + 4.0 * (n * n - R * R), 0.0))
                   - (1.0 + 2.0 * n))
    return np.clip(p, 0.0, 1.0)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def run_aspect_focal(in_raster, out_raster, statistic,
                     neighborhood="Circle", radius=3.0, width=3.0, height=3.0,
                     inner_radius=1.0, outer_radius=3.0,
                     start_angle=0.0, end_angle=90.0,
                     nbr_units="Cells", kernel_file=None,
                     input_convention=INPUT_CONVENTIONS[0],
                     flat_handling=FLAT_HANDLING[0], messages=None,
                     ignore_nodata=True):
    """Compute one circular focal statistic on an aspect raster. See the module
    docstring and the tool help for conventions and the all-flat fill values.

    ``ignore_nodata`` (Esri's Ignore NoData in calculations): True leaves
    NoData cells out of each neighborhood and uses the rest (a NoData cell
    with any valid neighbor gets a value); False makes a cell NoData when ANY
    cell of its neighborhood (kernel weight > 0, the processing cell included)
    is NoData -- and, as in Esri's Focal Statistics, the area beyond the
    raster edge counts as NoData, so a band one neighborhood radius wide
    around the edge becomes NoData. Flat cells (-1) are data, not NoData."""
    import arcpy
    if statistic not in STAT_LABEL:
        # allow passing the label
        statistic = STAT_KEY.get(statistic, statistic)
    if statistic not in STAT_LABEL:
        raise arcpy.ExecuteError("Unknown statistic: {}".format(statistic))
    if neighborhood not in NEIGHBORHOODS:
        raise arcpy.ExecuteError("Unknown neighborhood: {}".format(neighborhood))
    if neighborhood not in ("Irregular", "Weight") and nbr_units not in NBR_UNITS:
        raise arcpy.ExecuteError("Unknown neighborhood unit: {}".format(nbr_units))
    is_math = str(input_convention).lower().startswith("math")
    as_nodata = str(flat_handling).lower().startswith("nodata")
    out_raster = _mh.resolve_raster_output(out_raster, messages)
    gdal = _mh._gdal()
    scratch = arcpy.env.scratchFolder

    _mh.begin_phases([
        ("Reading the aspect raster...", 1),
        ("Computing {}...".format(STAT_LABEL[statistic]), 5),
        ("Writing the output raster...", 2),
        ("Saving the output raster...", 2),
        ("Building pyramids...", 1),
        ("Creating layer file and symbology...", 1),
    ])

    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    try:
        band = src.GetRasterBand(1)
        width_px, height_px = src.RasterXSize, src.RasterYSize
        if width_px < 1 or height_px < 1:
            raise arcpy.ExecuteError("The aspect raster is empty.")
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        in_nodata = band.GetNoDataValue()
        sr = arcpy.Describe(in_raster).spatialReference
        # float32 throughout: aspect values (0-360) are exact in float32, and
        # halving every whole-grid array matters on large regional rasters.
        a = band.ReadAsArray(0, 0, width_px, height_px).astype(np.float32)
        vmask = _mh.band_validity_mask(band)   # capture while src is open
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass

    valid = np.isfinite(a)
    if in_nodata is not None:
        valid &= (a != in_nodata)
    if vmask is not None:
        valid &= vmask

    # Source-type detection + user guidance.
    kind, why = _detect_source_kind(in_raster, a, in_nodata)
    if kind == "dinf" and not is_math:
        _warn(messages,
            "This looks like a D-infinity flow-direction raster ({}). "
            "D-infinity reports the direction of steepest descent -- "
            "essentially aspect -- but in the MATHEMATICAL convention "
            "(0 = east, counter-clockwise), not compass. Dispersion and "
            "concentration statistics are unaffected, but Mean/Median "
            "Direction will be wrong unless you set 'Input aspect convention' "
            "to Mathematical (D-infinity).".format(why))
    elif kind == "d8":
        _warn(messages,
            "This looks like a D8 flow-direction raster ({}). A D8 raster "
            "stores flow codes (1, 2, 4, ... 128), NOT direction in degrees, "
            "so these circular statistics would be meaningless. Use an aspect "
            "or D-infinity raster instead.".format(why))

    # Flat (-1) vs. directional cells. Any negative valid value is treated as
    # the flat/no-aspect sentinel; directions are 0-360.
    flat = valid & (a < 0.0)
    dvalid = valid & (a >= 0.0)
    n_bad = int(np.count_nonzero(valid & ((a < -1.001) | (a > 360.001))))
    if n_bad:
        _warn(messages,
              "{:,} cell(s) hold values outside the aspect range (-1 to 360 "
              "degrees) -- this does not appear to be an aspect raster. "
              "Values below 0 are treated as flat and values above 360 as "
              "directions, which is only meaningful for true aspect "
              "data.".format(n_bad))

    rad = np.radians(np.where(dvalid, a, 0.0)).astype(np.float32)
    sin_f = np.where(dvalid, np.sin(rad), 0.0).astype(np.float32)
    cos_f = np.where(dvalid, np.cos(rad), 0.0).astype(np.float32)
    dvalid_f = dvalid.astype(np.float32)
    flat_f = flat.astype(np.float32)
    # Ignore NoData unchecked: count each neighborhood's valid cells (flat
    # cells are data). correlate() pads with 0, so cells beyond the raster
    # edge count as missing -- Esri's rule.
    valid_f = None if ignore_nodata else valid.astype(np.float32)
    shape = a.shape
    del a, valid                          # raw grid no longer needed

    # Neighborhood kernel(s).
    kernel_arr = None
    if neighborhood in ("Irregular", "Weight"):
        if not kernel_file:
            raise arcpy.ExecuteError(
                "The {} neighborhood needs a kernel file.".format(neighborhood))
        kernel_arr = _parse_kernel_file(kernel_file)
        if neighborhood == "Irregular":
            kernel_arr = (kernel_arr != 0).astype(np.float64)
        if np.any(kernel_arr < 0):
            raise arcpy.ExecuteError(
                "The kernel file contains negative weights; weights must be "
                "zero or positive for circular statistics.")
        if not np.any(kernel_arr > 0):
            raise arcpy.ExecuteError(
                "The kernel file contains no positive weights.")
    P = {"radius": radius, "width": width, "height": height,
         "inner": inner_radius, "outer": outer_radius,
         "start": start_angle, "end": end_angle}
    groups, note = _neighborhood_groups(
        neighborhood, P, gt, height_px, sr, nbr_units, kernel_arr, messages,
        width=width_px, proj=proj)
    weighted = (neighborhood == "Weight")
    _say(messages, "Neighborhood: {}.".format(note))

    _mh.phase_advance("Computing {}...".format(STAT_LABEL[statistic]))

    # Resultant sums (S, C) + directional/flat weighted counts, per group, via
    # the shared memory-safe size-aware neighborhood convolution (direct spatial
    # correlation for small neighborhoods, overlap-add FFT for large ones) --
    # see hillshade.nbr_conv. A whole-grid fftconvolve raised MemoryError on
    # large regional aspect rasters even for a small neighborhood.
    S = np.zeros(shape, np.float32)
    C = np.zeros(shape, np.float32)
    Ndir = np.zeros(shape, np.float32)
    Nflat = np.zeros(shape, np.float32)
    # A Weight kernel's Rayleigh test needs the sum of squared weights too:
    # Kish's effective sample size (sum w)^2 / sum w^2 depends only on the
    # RELATIVE weights, so a kernel of 1s and one of 10s give the same p.
    kish = weighted and statistic in ("RAYLEIGH", "KAPPA")
    Nw2 = np.zeros(shape, np.float32) if kish else None
    # Cells whose neighborhood includes NoData or reaches past the edge
    # (only when Ignore NoData is unchecked).
    any_nd = None if ignore_nodata else np.zeros(shape, bool)
    single = (len(groups) == 1)
    conv_done = [0]
    conv_total = ((4 + (1 if kish else 0) + (0 if ignore_nodata else 1))
                  * len(groups) + 1)       # fields per group + assembly

    def _tick():
        conv_done[0] += 1
        _mh.phase_step(conv_done[0], conv_total)

    for k, rows in groups:
        kf = k.astype(np.float32)
        if single:
            S = _hs.nbr_conv(sin_f, kf); _tick()
            C = _hs.nbr_conv(cos_f, kf); _tick()
            Ndir = _hs.nbr_conv(dvalid_f, kf); _tick()
            Nflat = _hs.nbr_conv(flat_f, kf); _tick()
            if kish:
                Nw2 = _hs.nbr_conv(dvalid_f, kf * kf); _tick()
        else:
            S[rows] = _hs.nbr_conv(sin_f, kf)[rows]; _tick()
            C[rows] = _hs.nbr_conv(cos_f, kf)[rows]; _tick()
            Ndir[rows] = _hs.nbr_conv(dvalid_f, kf)[rows]; _tick()
            Nflat[rows] = _hs.nbr_conv(flat_f, kf)[rows]; _tick()
            if kish:
                Nw2[rows] = _hs.nbr_conv(dvalid_f, kf * kf)[rows]; _tick()
        if not ignore_nodata:
            kb = (k > 0).astype(np.float32)
            nv = _hs.nbr_conv(valid_f, kb)
            short = nv < (np.float32(kb.sum()) - np.float32(0.5))
            if single:
                any_nd = short
            else:
                any_nd[rows] = short[rows]
            del nv, short
            _tick()
    del sin_f, cos_f, dvalid_f, flat_f, valid_f
    if statistic not in _ORDER_STATS:
        del rad
    if not weighted:
        np.rint(Ndir, out=Ndir)
        np.rint(Nflat, out=Nflat)

    has_dir = Ndir > (1e-6 if weighted else 0.5)
    ntot = Ndir + Nflat
    all_flat = (~has_dir) & (ntot > (1e-6 if weighted else 0.5))
    no_valid = ntot <= (1e-6 if weighted else 0.5)

    # Everything below stays float32 end to end. Any np.where(cond, f32_array,
    # python_float) silently promotes to float64 -- an 8-byte-per-cell copy that
    # DOUBLES memory on a billion-cell regional raster -- so scalars are cast to
    # float32 and fills happen in place on the one output array.
    NANF = np.float32(np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        R = np.sqrt(S * S + C * C)
        Rbar = np.where(has_dir, R / np.where(Ndir == 0, np.float32(1.0), Ndir),
                        np.float32(0.0))
    del R
    Rbar = np.clip(Rbar, np.float32(0.0), np.float32(1.0))

    # Assemble the requested statistic on directional cells.
    if statistic == "MEAN":
        out = np.where(has_dir,
                       np.degrees(np.arctan2(S, C)) % np.float32(360.0), NANF)
    elif statistic == "RESULTANT":
        out = np.where(has_dir, Rbar, NANF)
    elif statistic == "CIRCVAR":
        out = np.where(has_dir, np.float32(1.0) - Rbar, NANF)
    elif statistic == "ANGVAR":
        out = np.where(has_dir, np.float32(2.0) * (np.float32(1.0) - Rbar), NANF)
    elif statistic == "CIRCSD":
        with np.errstate(invalid="ignore", divide="ignore"):
            csd = np.degrees(np.sqrt(np.maximum(np.float32(-2.0) * np.log(
                np.clip(Rbar, np.float32(1e-12), np.float32(1.0))),
                np.float32(0.0))))
        out = np.where(has_dir, csd, NANF)
        del csd
    elif statistic == "ANGDEV":
        out = np.where(has_dir,
                       np.degrees(np.sqrt(np.maximum(
                           np.float32(2.0) * (np.float32(1.0) - Rbar),
                           np.float32(0.0)))), NANF)
    elif statistic == "RAYLEIGH":
        if kish:
            # Kish (1965) effective sample size for weighted observations:
            # equals the cell count when all weights are equal, and does not
            # change when every weight is multiplied by the same constant.
            with np.errstate(divide="ignore", invalid="ignore"):
                n_test = np.where(Nw2 > 1e-12, (Ndir * Ndir) / Nw2,
                                  np.float32(1.0)).astype(np.float32)
        else:
            n_test = Ndir
        p = _rayleigh_p(np.where(n_test == 0, np.float32(1.0), n_test), Rbar)
        out = np.where(has_dir, p.astype(np.float32), NANF)
        del p
    elif statistic == "KAPPA":
        # Small-sample correction (Fisher 1993 eq. 4.41) for neighborhoods
        # of 15 or fewer directional cells; with a Weight kernel n is the
        # Kish effective sample size, as for the Rayleigh test.
        if kish:
            with np.errstate(divide="ignore", invalid="ignore"):
                n_k = np.where(Nw2 > 1e-12, (Ndir * Ndir) / Nw2,
                               np.float32(1.0)).astype(np.float32)
        else:
            n_k = Ndir
        kap = _kappa_small_sample(_kappa_from_rbar(Rbar), n_k)
        out = np.where(has_dir, kap.astype(np.float32), NANF)
        del kap, n_k
    elif statistic == "PROPDEF":
        out = np.where(ntot > 0,
                       Ndir / np.where(ntot == 0, np.float32(1.0), ntot), NANF)
    elif statistic in _ORDER_STATS:
        out = np.full(shape, np.nan, np.float32)
        for k, rows in groups:
            g = _order_stat(rad, dvalid, k, statistic, messages)
            out[rows] = g[rows]
    out = out.astype(np.float32, copy=False)
    _tick()                               # assembly complete
    del S, C, Rbar
    # The counts fed the assembly; free them before the write phase (three
    # whole-grid float32 arrays matter on a billion-cell raster).
    del Ndir, Nflat, ntot

    # Convert direction outputs to compass when the input was mathematical
    # (in place, on the affected cells only).
    if statistic in _DIRECTION_STATS and is_math:
        conv = has_dir & np.isfinite(out)
        out[conv] = (np.float32(90.0) - out[conv]) % np.float32(360.0)
        del conv
    if statistic in _DIRECTION_STATS:
        # float32 `x % 360` returns exactly 360.0 when x is a tiny negative
        # (e.g. a mean direction a hair west of north), so wrap it: 360 == 0.
        out[out >= np.float32(360.0)] = np.float32(0.0)

    # Apply the all-flat fill (or NoData), then NoData for no-valid cells --
    # all in place.
    fill = _ALLFLAT_FILL[statistic]
    out[all_flat] = NANF if as_nodata else np.float32(fill)
    out[no_valid] = NANF
    if any_nd is not None:
        n_nd = int(np.count_nonzero(any_nd & ~no_valid))
        out[any_nd] = NANF
        _say(messages,
             "Ignore NoData is off: {:,} cell(s) set to NoData because their "
             "neighborhood includes a NoData cell or extends past the edge "
             "of the raster.".format(n_nd))
        del any_nd

    out_valid = np.isfinite(out)
    n_out = int(out_valid.sum())
    n_flat_nbr = int(all_flat.sum())
    _say(messages,
        "{}: {:,} of {:,} cells written{}.".format(
            STAT_LABEL[statistic], n_out, width_px * height_px,
            ("; {:,} all-flat neighborhood(s) {}".format(
                n_flat_nbr, "set to NoData" if as_nodata
                else "set to {:g}".format(fill)) if n_flat_nbr else "")))
    if statistic in _DIRECTION_STATS:
        _say(messages,
            "Direction is reported in COMPASS degrees (0 = north, clockwise); "
            "flat neighborhoods are {}.".format(
                "NoData" if as_nodata else "-1"))

    # Write, stats, metadata, symbology. Stats accumulate in row slabs and the
    # NoData fill is in place -- no whole-grid float64 copies (this is the write
    # path that must survive a billion-cell raster).
    _mh.phase_advance("Writing the output raster...")
    out_path = arcpy.CreateUniqueName("aspectfocal.tif", scratch)
    ds, oband = _tt._make_output(gdal, out_path, gt, proj, width_px, height_px)
    stats = _mh._RasterStats()
    slab = max(1, int(4_000_000 // max(1, width_px)))
    for r0 in range(0, height_px, slab):
        r1 = min(height_px, r0 + slab)
        v = out[r0:r1][out_valid[r0:r1]]
        if v.size:
            stats.add(v.astype(np.float64))
        _mh.phase_step(r1, height_px)
    out[~out_valid] = np.float32(NODATA_OUT)
    oband.WriteArray(out)
    oband.FlushCache()
    out_stats = _mh._finalize_stats(oband, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(oband, out_stats)
    oband.FlushCache()
    ds.FlushCache()
    oband = None
    ds = None

    _mh.phase_advance("Saving the output raster...")
    conv_note = ""
    if statistic in _DIRECTION_STATS:
        conv_note = (" Input read in the {} convention; direction output is "
                     "compass (0=N, clockwise).".format(
                         "mathematical (D-infinity)" if is_math else "compass"))
    _tt._deliver_output(
        out_path, out_raster, out_stats, messages,
        title=os.path.basename(str(out_raster)),
        summary=("Created by Aspect Focal Statistics from {src}: statistic "
                 "{stat}, {nbr}.{cv} All-flat neighborhoods {flat}.").format(
                     src=str(in_raster), stat=STAT_LABEL[statistic], nbr=note,
                     cv=conv_note,
                     flat=("set to NoData" if as_nodata
                           else "set to {:g}".format(fill))),
        description=(
            "Circular focal statistic '{stat}' of an aspect raster, computed "
            "over a {nbr} neighborhood. Aspect is a circular variable, so "
            "ordinary Focal Statistics is invalid; this tool uses circular "
            "statistics and needs no Spatial Analyst. Flat cells (aspect -1) "
            "carry no direction and are ignored. {cite}").format(
                stat=STAT_LABEL[statistic], nbr=note, cite=_CIRC_CITATION),
        tags=("aspect, circular statistics, focal statistics, neighborhood, "
              "directional, mean direction, circular variance, Jenness "
              "Enterprises"),
        citation=_CIRC_CITATION)

    _mh.phase_advance("Creating layer file and symbology...")
    out_lyrx = None
    try:
        if statistic in _DIRECTION_STATS:
            # Cyclic aspect color scheme (0 and 360 share a color); flat (-1)
            # drawn separately in a mask color.
            out_lyrx = _mh.default_aspect_lyrx(out_raster, messages=messages)
        else:
            # Blue (low) -> yellow -> red (high) for magnitude/dispersion stats.
            out_lyrx = _mh.default_stretch_lyrx(
                out_raster, [(95, 135, 193), (249, 254, 204), (224, 70, 53)],
                messages=messages)
    except Exception as e:
        _warn(messages,
            "Could not set output symbology: {}".format(e))
    _mh.end_phases()
    _say(messages, "Aspect Focal Statistics complete: {}".format(out_raster))
    return out_lyrx


# ---------------------------------------------------------------------------
#  Extract Aspect Values to Points
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
#  Extract ... Values to Points: the point-sampling machinery shared by the
#  aspect tool below and Extract Slope Values to Points (topographic_tools)
# ---------------------------------------------------------------------------
WRITE_TO_NEW = "A new point feature class (a copy of the input)"
WRITE_TO_INPUT = "A new field in the input feature class"
WRITE_TO_OPTIONS = [WRITE_TO_NEW, WRITE_TO_INPUT]


def write_to_input(write_to):
    """True when the ``write_to`` choice asks for the field to be added to
    the input feature class itself (the label, or a plain 'input' /
    'existing' / 'in_place' word from a script)."""
    s = str(write_to or "").strip().lower()
    return (s.startswith("a new field") or s in ("input", "existing",
                                                 "in_place", "in place",
                                                 "true"))


def _point_target(in_points, out_points, field_name, default_field,
                  in_place, messages):
    """Where the values go and the field that holds them.

    ``in_place`` False: ``in_points`` is copied to ``out_points`` (an active
    selection is honored and every attribute carried) and the field is added
    to the copy. True: the field is added to the INPUT feature class itself
    and ``out_points`` is ignored; the input must be a feature class or a
    layer on one without a join (a joined layer cannot take a new field).

    Returns (target, path, fld, null_ok): the dataset to read and write
    through (the layer itself in place, so its selection and definition
    query apply), its catalog path, the field actually created (sanitized
    and uniquified against the existing schema), and whether the field can
    hold NULL (a geodatabase) or needs -9999 (a shapefile)."""
    import arcpy
    d = arcpy.Describe(in_points)
    if d.shapeType != "Point":
        raise arcpy.ExecuteError(
            "The input must be a POINT feature class (multipoint and other "
            "shapes are not supported); got {}.".format(d.shapeType))
    want = str(field_name or "").strip() or default_field
    if in_place:
        try:
            path = d.catalogPath
        except Exception:
            path = str(in_points)
        names = [f.name for f in arcpy.ListFields(in_points)]
        if any("." in n for n in names):
            raise arcpy.ExecuteError(
                "The input layer has a join, so a field cannot be added to "
                "it. Remove the join, or write the values to a new feature "
                "class instead.")
        target = in_points
    else:
        if not out_points:
            raise arcpy.ExecuteError(
                "An output feature class is required when the values go to "
                "a new feature class.")
        arcpy.management.CopyFeatures(in_points, out_points)
        target = path = str(out_points)
    # Boundary-aware: the old bare substring test wrongly counted a FOLDER
    # merely named like "x.gdb_backup" as a geodatabase (nullable fields).
    null_ok = _mh.in_gdb_container(path)
    ws = os.path.dirname(str(path))
    shp = (_mh.is_shapefile_target(str(path))
           or str(path).lower().endswith(".dbf"))

    def legal(name):
        try:
            name = arcpy.ValidateFieldName(name, ws)
        except Exception:
            pass
        return name[:10] if shp else name

    taken = {f.name.lower() for f in arcpy.ListFields(path)}
    fld = legal(want)
    base, n = fld, 2
    while fld.lower() in taken:
        suffix = "_{}".format(n)
        stem = base[:10 - len(suffix)] if shp else base
        fld = legal(stem + suffix)
        n += 1
    arcpy.management.AddField(path, fld, "DOUBLE", field_alias=want)
    if fld != want:
        _warn(messages, "The value field was created as '{}' (the requested "
                        "name '{}' was not valid for this workspace or was "
                        "already taken).".format(fld, want))
    if in_place:
        _say(messages, "Added the field '{}' to the input feature class "
                       "{}.".format(fld, path))
    return target, path, fld, null_ok


def _points_in_raster_window(target, in_raster, messages, what, margin=2):
    """The target's points in the RASTER's coordinate system, and the window
    of the raster that covers them.

    A cursor's spatial_reference= reprojects WITHOUT a datum transformation
    (NAD27 points on a NAD83 raster would land 100-200 m off), so when the
    datums differ the geometry is read natively and projected here with the
    transformation clip_tools chooses; the same datum takes the fast cursor
    path. Only the window of cells around the points (+``margin`` cells) is
    read, so the large-raster check applies to that window, not the whole
    raster. ``what`` names the raster in messages ("aspect", "slope", "DEM").

    Returns a dict: oids, xs, ys, n_pts, arr (float32 window), valid (usable
    cells: finite, not NoData, not masked), x0w, y0w, csx, csy, nodata, plus
    gt_w (the window's geotransform), c0w/r0w (its offset in the raster),
    proj (the raster's projection WKT) and rsr (its arcpy spatial
    reference) for tools that derive values from a DEM."""
    import arcpy
    import clip_tools as _ct
    rsr = arcpy.Describe(in_raster).spatialReference
    psr = arcpy.Describe(target).spatialReference
    same = (True if psr is None or psr.name in ("Unknown", "")
            else _ct._same_datum(psr, rsr))
    oids, xs, ys = [], [], []
    if same:
        with arcpy.da.SearchCursor(target, ["OID@", "SHAPE@XY"],
                                   spatial_reference=rsr) as cur:
            for oid, xy in cur:
                if xy is None:
                    continue
                oids.append(oid)
                xs.append(xy[0])
                ys.append(xy[1])
    else:
        try:
            ext = arcpy.Describe(target).extent
        except Exception:
            ext = None
        tfm = _ct.transformation_name(psr, rsr, ext)
        _warn(messages, "The points ({}) and the {} raster ({}) are on "
                        "different datums. The points were projected onto "
                        "the raster with the transformation {}.".format(
                            psr.GCS.name, what, rsr.GCS.name,
                            tfm or "(none available: coordinates were "
                                   "re-projected without a datum shift)"))
        with arcpy.da.SearchCursor(target, ["OID@", "SHAPE@"]) as cur:
            for oid, g in cur:
                if g is None:
                    continue
                try:
                    pt = _ct.project_geometry(g, rsr, ext).firstPoint
                except Exception:
                    pt = None
                if pt is None:
                    continue
                oids.append(oid)
                xs.append(pt.X)
                ys.append(pt.Y)
    n_pts = len(oids)
    if n_pts == 0:
        _warn(messages, "The input contains no point features (if the layer "
                        "has a selection, nothing is selected).")
    xs = np.asarray(xs, np.float64)
    ys = np.asarray(ys, np.float64)

    src, in_copy = _hs._open_source(in_raster, arcpy.env.scratchFolder,
                                    messages, check_size=False)
    try:
        band = src.GetRasterBand(1)
        W, H = src.RasterXSize, src.RasterYSize
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        nodata = band.GetNoDataValue()
        csx, csy = gt[1], abs(gt[5])
        margin = max(int(margin), 1)
        if n_pts:
            c_lo = int(np.floor((xs.min() - gt[0]) / csx)) - margin
            c_hi = int(np.ceil((xs.max() - gt[0]) / csx)) + margin
            r_lo = int(np.floor((gt[3] - ys.max()) / csy)) - margin
            r_hi = int(np.ceil((gt[3] - ys.min()) / csy)) + margin
            c0w = max(0, c_lo)
            r0w = max(0, r_lo)
            c1w = min(W, c_hi)
            r1w = min(H, r_hi)
            _hs.check_window_size(in_raster, c1w - c0w, r1w - r0w, messages)
        else:
            c0w = r0w = 0
            c1w = r1w = 0
        if c1w > c0w and r1w > r0w:
            arr = band.ReadAsArray(c0w, r0w, c1w - c0w,
                                   r1w - r0w).astype(np.float32)
        else:
            arr = np.zeros((0, 0), np.float32)
            if n_pts:
                _warn(messages, "None of the points fall within the {} "
                                "raster's extent.".format(what))
        x0w = gt[0] + c0w * csx                 # window origin
        y0w = gt[3] - r0w * csy
        wmask = (_mh.mask_band_valid(band, np.ones(arr.shape, bool),
                                     c0w, r0w)
                 if arr.size else None)         # capture while src is open
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass
    valid = np.isfinite(arr)
    if nodata is not None:
        valid &= (arr != nodata)
    if wmask is not None:
        valid &= wmask
    return {"oids": oids, "xs": xs, "ys": ys, "n_pts": n_pts, "arr": arr,
            "valid": valid, "x0w": x0w, "y0w": y0w, "csx": csx, "csy": csy,
            "nodata": nodata, "gt_w": (x0w, csx, 0.0, y0w, 0.0, -csy),
            "c0w": c0w, "r0w": r0w, "proj": proj, "rsr": rsr}


def _write_point_values(target, fld, oids, vals, null_ok, flat_value=None):
    """Writes ``vals`` (NaN = no value) into ``fld`` of ``target``, matched
    by OID; a row with no entry (a point with no geometry) gets no value.
    Returns (n_nodata, n_flat): rows given NULL (or -9999 in a shapefile),
    and rows equal to ``flat_value`` (None: not counted)."""
    import arcpy
    val_of = {oid: (None if not np.isfinite(v) else float(v))
              for oid, v in zip(oids, vals)}
    null_val = None if null_ok else -9999.0
    n_nodata = n_flat = 0
    with arcpy.da.UpdateCursor(target, ["OID@", fld]) as cur:
        for row in cur:
            v = val_of.get(row[0])
            if v is None:
                row[1] = null_val
                n_nodata += 1
            else:
                row[1] = v
                if flat_value is not None and v == flat_value:
                    n_flat += 1
            cur.updateRow(row)
    return n_nodata, n_flat


def _rows_beyond(target, path, n_pts):
    """How many rows of the feature class at ``path`` the run did not reach
    (hidden by the layer's selection or definition query), or 0."""
    import arcpy
    try:
        if str(target) == str(path):
            return 0
        total = int(arcpy.management.GetCount(path)[0])
        return max(total - n_pts, 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
#  Slope and aspect straight from a DEM, at the cells the points need
# ---------------------------------------------------------------------------
# Both point tools can take a DEM instead of a finished slope or aspect
# raster. The value is then derived here at exactly the cells the sampling
# step needs (the containing cell, or the four cells around each point) and
# handed to the same nearest / bilinear code the raster path uses -- so a
# DEM run gives the same numbers as "make the raster, then extract", for the
# same method. Two methods:
#   Horn (3 x 3): the third-order finite difference of Esri's Slope and
#     Aspect tools (planar method), down to their NoData rule (a NoData
#     neighbor contributes nothing and its side is rescaled; fewer than 7
#     valid neighbors gives NoData), over TRUE ground distances
#     (GroundGeometry) -- identical to Esri's planar tools wherever the
#     projection is true to scale, and still right on Web Mercator or a
#     lat/long grid, where Esri's planar method is not.
#   Quadratic least squares over an n x n window (n = 3 ... 15): the
#     Evans-Young surface z = a x^2 + b y^2 + c xy + d x + e y + f fitted by
#     least squares to the valid cells of the window; slope and aspect come
#     from d and e at the center. At 3 x 3 it is the equal-weight estimator;
#     wider windows generalize and smooth. (Esri's Surface Parameters tool
#     fits its quadratic in geocentric coordinates and offers an exact
#     biquadratic; to extract values made exactly that way, run Surface
#     Parameters first and extract from its raster.)
DEM_METHODS = ["Horn (3 x 3; the method of Esri's Slope and Aspect tools)",
               "Quadratic least squares (choose the window size)"]
WINDOW_SIZES = [3, 5, 7, 9, 11, 13, 15]


def dem_method_key(method):
    """'quadratic' or 'horn' from a DEM_METHODS label or a plain word."""
    return "quadratic" if "quad" in str(method or "").lower() else "horn"


def _esri_horn_sums(nb, nv):
    """Esri's planar Slope/Aspect rule on (K, 3, 3) elevations ``nb`` with
    valid mask ``nv``: a NoData neighbor contributes nothing and its column
    or row sum is rescaled by 4 / (weighted count of its valid cells);
    fewer than 7 valid neighbors, or an invalid center, gives no value.
    Returns (gx, gy, ok): the dz/dx and dz/dy numerators -- (east column -
    west column) and (south row - north row), each weighted 1-2-1 -- to be
    divided by the Horn denominators (8 x cell size on a square grid)."""
    z = np.where(nv, nb, 0.0)
    v = nv.astype(np.float64)
    a, b, c = z[:, 0, 0], z[:, 0, 1], z[:, 0, 2]       # north row
    d, f = z[:, 1, 0], z[:, 1, 2]
    g, h, i = z[:, 2, 0], z[:, 2, 1], z[:, 2, 2]       # south row
    va, vb, vc = v[:, 0, 0], v[:, 0, 1], v[:, 0, 2]
    vd, vf = v[:, 1, 0], v[:, 1, 2]
    vg, vh, vi = v[:, 2, 0], v[:, 2, 1], v[:, 2, 2]
    w_e = vc + 2.0 * vf + vi
    w_w = va + 2.0 * vd + vg
    w_s = vg + 2.0 * vh + vi
    w_n = va + 2.0 * vb + vc
    nvalid = va + vb + vc + vd + vf + vg + vh + vi
    ok = nv[:, 1, 1] & (nvalid >= 7) & (w_e > 0) & (w_w > 0) & (w_s > 0) & (w_n > 0)
    safe = lambda w: np.where(w > 0, w, 1.0)
    gx = (c + 2.0 * f + i) * 4.0 / safe(w_e) - (a + 2.0 * d + g) * 4.0 / safe(w_w)
    gy = (g + 2.0 * h + i) * 4.0 / safe(w_s) - (a + 2.0 * b + c) * 4.0 / safe(w_n)
    return gx, gy, ok


def _quadratic_gradients(nb, nv, hx, hy):
    """Least-squares quadratic z = a x^2 + b y^2 + c xy + d x + e y + f over
    the valid cells of each (n x n) window in ``nb`` (K, n, n), x east and
    y SOUTH in meters from the center cell (``hx``, ``hy``: the (K,) cell
    spans); returns (dzdx, dzdy, ok) at the centers, ok = center valid and
    at least six valid cells with a solvable fit."""
    K, n, _n = nb.shape
    R = n // 2
    j = np.arange(-R, R + 1, dtype=np.float64)
    X = np.broadcast_to(j[None, None, :] * hx[:, None, None], (K, n, n)).reshape(K, n * n)
    Y = np.broadcast_to(j[None, :, None] * hy[:, None, None], (K, n, n)).reshape(K, n * n)
    Z = np.where(nv, nb, 0.0).reshape(K, n * n)
    Wt = nv.reshape(K, n * n).astype(np.float64)
    A = np.stack([X * X, Y * Y, X * Y, X, Y, np.ones_like(X)], axis=2)   # (K, n2, 6)
    AtA = np.einsum("kpi,kp,kpj->kij", A, Wt, A)
    Atz = np.einsum("kpi,kp,kp->ki", A, Wt, Z)
    ok = nv[:, R, R] & (Wt.sum(axis=1) >= 6)
    dzdx = np.zeros(K)
    dzdy = np.zeros(K)
    if K:
        AtA_safe = np.where(ok[:, None, None], AtA, np.eye(6)[None])
        try:
            coef = np.linalg.solve(AtA_safe, Atz[:, :, None])[:, :, 0]
            good = ok & np.all(np.isfinite(coef), axis=1)
        except np.linalg.LinAlgError:
            coef = np.zeros((K, 6))
            good = np.zeros(K, bool)
            for k in np.flatnonzero(ok):
                try:
                    sol, _res, rank, _sv = np.linalg.lstsq(
                        A[k][nv.reshape(K, n * n)[k]],
                        Z[k][nv.reshape(K, n * n)[k]], rcond=None)
                    if rank == 6 and np.all(np.isfinite(sol)):
                        coef[k] = sol
                        good[k] = True
                except np.linalg.LinAlgError:
                    pass
        ok = good
        dzdx = np.where(ok, coef[:, 3], 0.0)
        dzdy = np.where(ok, coef[:, 4], 0.0)
    return dzdx, dzdy, ok


def _needed_cells(xs, ys, x0w, y0w, csx, csy, Hw, Ww, interpolate):
    """The unique (rows, cols) of the window cells the sampling step will
    read for these points: the containing cell, or the four cells around
    each point for bilinear interpolation (exactly as the samplers index)."""
    colf = (xs - x0w) / csx
    rowf = (y0w - ys) / csy
    if not interpolate:
        ci = np.floor(colf).astype(np.int64)
        ri = np.floor(rowf).astype(np.int64)
    else:
        c0 = np.floor(colf - 0.5).astype(np.int64)
        r0 = np.floor(rowf - 0.5).astype(np.int64)
        ci = np.concatenate([c0, c0 + 1, c0, c0 + 1])
        ri = np.concatenate([r0, r0, r0 + 1, r0 + 1])
    inside = (ci >= 0) & (ci < Ww) & (ri >= 0) & (ri < Hw)
    if not inside.any():
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    flat = np.unique(ri[inside] * Ww + ci[inside])
    return flat // Ww, flat % Ww


def derive_from_dem(w, kind, method, window_size, elev_units, interpolate,
                    messages):
    """Slope (degrees) or aspect (compass degrees, -1 flat) derived from the
    DEM window ``w`` (from ``_points_in_raster_window`` read with a margin
    of at least window_size // 2 + 1) at the cells the sampling step needs,
    as (arr, valid, info): a float32 array of the window's shape (NaN
    elsewhere), its valid mask, and a dict describing the method and the
    ground geometry (``note``, ``method_text``, ``window``)."""
    import arcpy
    key = dem_method_key(method)
    n = 3 if key == "horn" else int(window_size or 3)
    if n < 3 or n % 2 == 0 or n > max(WINDOW_SIZES):
        raise arcpy.ExecuteError(
            "The window size must be an odd number of cells from 3 to {}; "
            "got {}.".format(max(WINDOW_SIZES), window_size))
    R = n // 2
    arr, valid = w["arr"], w["valid"]
    Hw, Ww = arr.shape
    n_pts = w["n_pts"]
    zscale = _hs._ELEV_METERS.get(elev_units, 1.0)
    out = np.full(arr.shape, np.nan, dtype=np.float32)
    out_valid = np.zeros(arr.shape, dtype=bool)
    info = {"window": n, "method": key, "note": "", "mode": "planar"}
    method_text = ("Horn's third-order finite difference over the 3 x 3 "
                   "neighborhood (the method of Esri's Slope and Aspect "
                   "tools, planar method, including its NoData rule)"
                   if key == "horn" else
                   "least-squares quadratic surface fitted to the {n} x {n} "
                   "neighborhood (Evans-Young; slope and aspect from its "
                   "first derivatives at the center)".format(n=n))
    info["method_text"] = method_text
    if not (n_pts and Hw and Ww):
        return out, out_valid, info

    # true ground spans at the cells (any coordinate system)
    ground = _hs.ground_geometry(w["gt_w"], Ww, Hw, w["rsr"], w["proj"],
                                 messages, what="DEM")
    info["mode"] = ground.mode
    info["note"] = ground.info.get("note") or ""
    for k in ("projection", "scale_min", "scale_max", "scale_x", "scale_y"):
        info[k] = ground.info.get(k)
    rows, cols = _needed_cells(w["xs"], w["ys"], w["x0w"], w["y0w"],
                               w["csx"], w["csy"], Hw, Ww, interpolate)
    # cells whose full window lies inside the read window
    inner = (rows >= R) & (rows < Hw - R) & (cols >= R) & (cols < Ww - R)
    rows, cols = rows[inner], cols[inner]
    K = len(rows)
    if K == 0:
        return out, out_valid, info
    spans = ground.at_cells(cols, rows)
    z = np.where(valid, arr, np.nan).astype(np.float64) * zscale
    # gather the K windows
    rr = rows[:, None, None] + np.arange(-R, R + 1)[None, :, None]
    cc = cols[:, None, None] + np.arange(-R, R + 1)[None, None, :]
    nb = z[rr, cc]
    nv = valid[rr, cc] & np.isfinite(nb)
    if key == "horn":
        gx, gy, ok = _esri_horn_sums(nb, nv)
        dzdx = gx / (2.0 * spans["hA"] + 4.0 * spans["h"] + 2.0 * spans["hB"])
        dzdy = gy / (4.0 * spans["vA"] + 4.0 * spans["vB"])
    else:
        hy = (spans["vA"] + spans["vB"]) / 2.0
        dzdx, dzdy, ok = _quadratic_gradients(nb, nv, spans["h"], hy)
    with np.errstate(invalid="ignore"):
        if kind == "slope":
            val = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
        else:
            # Esri's aspect: degrees from atan2(dz/dy, -dz/dx), then to
            # compass; a zero gradient is flat (-1)
            asp = np.degrees(np.arctan2(dzdy, -dzdx))
            comp = np.where(asp < 0.0, 90.0 - asp,
                            np.where(asp > 90.0, 360.0 - asp + 90.0,
                                     90.0 - asp)) % 360.0
            flat = (dzdx == 0.0) & (dzdy == 0.0)
            val = np.where(flat, -1.0, comp)
    out[rows[ok], cols[ok]] = val[ok].astype(np.float32)
    out_valid[rows[ok], cols[ok]] = True
    return out, out_valid, info


# ---------------------------------------------------------------------------
#  Extract Aspect Values to Points
# ---------------------------------------------------------------------------
def run_extract_aspect_points(in_points, in_raster, out_points,
                              interpolate=False, field_name="Aspect",
                              input_convention=INPUT_CONVENTIONS[0],
                              messages=None, in_place=False, from_dem=False,
                              dem_method=DEM_METHODS[0], window_size=3,
                              elev_units="Meters"):
    """Extract the aspect value at each point of ``in_points`` from an aspect
    raster -- or, with ``from_dem``, derive it from a DEM (see
    ``derive_from_dem``: Horn's 3 x 3 method, the one Esri's Aspect tool
    uses, or a least-squares quadratic over ``window_size`` cells, with the
    DEM's elevations in ``elev_units``) -- into a new field -- of a copied
    point feature class, or, with ``in_place``, of the input feature class
    itself -- the circular analog of Esri's Extract Values to Points, with
    no Spatial Analyst.

    ``interpolate`` False: the value of the cell each point falls in, exactly as
    stored (including -1 for flat). True: a BILINEAR-WEIGHTED MEAN DIRECTION
    from the four surrounding cell centers -- each neighbor's unit vector
    (sin, cos) weighted by the ordinary bilinear weights and the resultant
    converted back to a direction. A linear bilinear average would be wrong for
    a circular value (359 and 1 must interpolate to ~0, not 180). Flat (-1)
    neighbors carry no direction and are excluded (weights renormalized over
    the directional neighbors); if every contributing neighbor is flat the
    result is -1; NoData neighbors are likewise excluded.

    Points are read in the RASTER's coordinate system (the raster is never
    projected); the raster is read only over the points' extent. Values are
    written as compass degrees; with the Mathematical input convention (a
    D-infinity flow-direction raster) they are converted to compass. NoData /
    outside-the-raster points get NULL in a geodatabase or -9999 in a
    shapefile. In place, the layer's selection and definition query decide
    which points get a value; the other rows of the feature class stay NULL.

    Returns the actual field name created (sanitized/uniquified)."""
    import arcpy
    from_dem = bool(from_dem)
    is_math = (not from_dem) and str(input_convention).lower().startswith("math")
    n_win = 3 if dem_method_key(dem_method) == "horn" else int(window_size or 3)

    _mh.begin_phases([
        ("Adding the field to the input features..." if in_place
         else "Copying the point features...", 2),
        ("Reading the DEM..." if from_dem else "Reading the aspect raster...", 1),
        ("Computing aspect from the DEM at the points..." if from_dem
         else "Sampling aspect at the points...", 4),
        ("Writing values and field metadata...", 2),
    ])
    target, path, fld, null_ok = _point_target(
        in_points, out_points, field_name, "Aspect", in_place, messages)

    _mh.phase_advance("Reading the DEM..." if from_dem
                      else "Reading the aspect raster...")
    w = _points_in_raster_window(target, in_raster, messages,
                                 "DEM" if from_dem else "aspect",
                                 margin=(n_win // 2 + 2) if from_dem else 2)
    arr, aw, n_pts = w["arr"], w["valid"], w["n_pts"]
    xs, ys = w["xs"], w["ys"]
    x0w, y0w, csx, csy = w["x0w"], w["y0w"], w["csx"], w["csy"]

    dem_info = None
    if from_dem:
        arr, aw, dem_info = derive_from_dem(w, "aspect", dem_method, n_win,
                                            elev_units, interpolate, messages)
        _say(messages, "Aspect computed from the DEM with {} (elevation units "
                       "{}).".format(dem_info["method_text"], elev_units))
        kind, why = "aspect", "derived from the DEM"
    else:
        kind, why = _detect_source_kind(in_raster, arr if arr.size else None,
                                        w["nodata"])
    if kind == "d8":
        _warn(messages, "This looks like a D8 flow-direction raster ({}) -- "
                        "flow codes 1-128, not degrees; not valid aspect "
                        "input.".format(why))
    elif kind == "dinf" and not is_math:
        _warn(messages, "This looks like a D-infinity flow-direction raster "
                        "({}); set the input convention to Mathematical so "
                        "its angles are converted to compass "
                        "directions.".format(why))
    # Sanity check: an aspect raster holds -1 (flat) and 0-360 only.
    n_bad = int(np.count_nonzero(aw & ((arr < -1.001) | (arr > 360.001))))
    if n_bad:
        _warn(messages,
              "{:,} cell(s) in the sampled area hold values outside the "
              "aspect range (-1 to 360 degrees) -- this does not appear to "
              "be an aspect raster. Values below 0 are treated as flat and "
              "values above 360 as directions, which is only meaningful for "
              "true aspect data.".format(n_bad))

    _mh.phase_advance("Computing aspect from the DEM at the points..."
                      if from_dem else "Sampling aspect at the points...")
    vals = np.full(n_pts, np.nan)               # NaN = NoData/outside
    Hw, Ww = arr.shape
    if n_pts and Hw and Ww:
        colf = (xs - x0w) / csx                 # fractional cols in the window
        rowf = (y0w - ys) / csy
        if not interpolate:
            ci = np.floor(colf).astype(np.int64)
            ri = np.floor(rowf).astype(np.int64)
            inside = (ci >= 0) & (ci < Ww) & (ri >= 0) & (ri < Hw)
            cis = np.clip(ci, 0, Ww - 1)
            ris = np.clip(ri, 0, Hw - 1)
            ok = inside & aw[ris, cis]
            vals[ok] = arr[ris[ok], cis[ok]]
        else:
            # Bilinear-weighted mean direction from the 4 surrounding cell
            # centers, ignoring NoData; flat (-1) neighbors excluded from the
            # direction but counted so an all-flat set of neighbors gives -1.
            fx = colf - 0.5
            fy = rowf - 0.5
            c0 = np.floor(fx).astype(np.int64)
            r0 = np.floor(fy).astype(np.int64)
            tx = (fx - c0).astype(np.float64)
            ty = (fy - r0).astype(np.float64)
            Ssum = np.zeros(n_pts)
            Csum = np.zeros(n_pts)
            wdir = np.zeros(n_pts)
            wvalid = np.zeros(n_pts)
            for dr, dc, wt in ((0, 0, (1 - tx) * (1 - ty)),
                               (0, 1, tx * (1 - ty)),
                               (1, 0, (1 - tx) * ty),
                               (1, 1, tx * ty)):
                cc = c0 + dc
                rr = r0 + dr
                inside = (cc >= 0) & (cc < Ww) & (rr >= 0) & (rr < Hw)
                ccs = np.clip(cc, 0, Ww - 1)
                rrs = np.clip(rr, 0, Hw - 1)
                v = arr[rrs, ccs].astype(np.float64)
                valid = inside & aw[rrs, ccs] & (wt > 0)
                dirok = valid & (v >= 0.0)
                rad_v = np.radians(v)
                Ssum += np.where(dirok, wt * np.sin(rad_v), 0.0)
                Csum += np.where(dirok, wt * np.cos(rad_v), 0.0)
                wdir += np.where(dirok, wt, 0.0)
                wvalid += np.where(valid, wt, 0.0)
            # No direction when the neighbors' arrows cancel exactly (a
            # resultant of zero length has no bearing) -- the same rule
            # the zonal tool's point zones use; such points get -1.
            has_dir = (wdir > 1e-12) & (np.hypot(Ssum, Csum) > 1e-12)
            some_valid = wvalid > 1e-12
            mean_dir = np.degrees(np.arctan2(Ssum, Csum)) % 360.0
            mean_dir[mean_dir >= 360.0] = 0.0
            vals = np.where(has_dir, mean_dir,
                            np.where(some_valid, -1.0, np.nan))
    # Mathematical (D-infinity) convention -> compass, for real directions only.
    if is_math and n_pts:
        conv = np.isfinite(vals) & (vals >= 0.0)
        vals[conv] = (90.0 - vals[conv]) % 360.0
        vals[conv & (vals >= 360.0)] = 0.0

    _mh.phase_advance("Writing values and field metadata...")
    n_nodata, n_flat = _write_point_values(target, fld, w["oids"], vals,
                                           null_ok, flat_value=-1.0)

    _say(messages, "Sampled {:,} point(s): {:,} with a direction, {:,} flat "
                   "or with no defined direction (-1), {:,} on NoData or "
                   "outside the raster ({}).".format(
                       n_pts, n_pts - n_flat - n_nodata, n_flat, n_nodata,
                       "NULL" if null_ok else "-9999"))
    _say(messages, "Values are {} aspect in COMPASS degrees (0 = north, "
                   "clockwise); -1 = flat{}.".format(
                       "bilinear weighted-mean-direction" if interpolate
                       else "cell-value",
                       " -- aspect derived from the DEM at each cell first"
                       if from_dem else ""))
    if in_place:
        beyond = _rows_beyond(target, path, n_pts)
        if beyond:
            _say(messages, "The field was added to the whole feature class; "
                           "{:,} feature(s) outside the layer's selection or "
                           "definition query were left NULL.".format(beyond))

    # Field-level metadata.
    try:
        import tool_metadata
        method = ("Bilinear weighted mean direction of the four surrounding "
                  "cell centers (circular interpolation; flat and NoData "
                  "neighbors excluded)" if interpolate
                  else "Value of the raster cell containing the point")
        conv_note = (" Input was read in the mathematical (D-infinity) "
                     "convention and converted to compass." if is_math else "")
        if dem_info is not None:
            conv_note = (" Aspect was derived from the DEM by {} over true "
                         "ground distances ({}); elevation units {}.".format(
                             dem_info["method_text"],
                             _hs.ground_note_for_metadata(dem_info),
                             elev_units))
        tool_metadata.set_entity_and_fields(
            arcpy.Describe(path).catalogPath,
            fields=[{"name": fld, "description":
                     "Aspect at the point, in compass degrees (0-360, 0 = "
                     "north, clockwise); -1 = flat (no aspect); {} = NoData "
                     "or outside the raster. Method: {}. Source {}: {}.{}"
                     .format("NULL" if null_ok else "-9999", method,
                             "DEM" if from_dem else "raster",
                             str(in_raster), conv_note)}],
            messages=messages)
    except Exception:
        pass
    _mh.end_phases()
    _say(messages, "Extract Aspect Values to Points complete: {}".format(
        path))
    return fld


# ---------------------------------------------------------------------------
#  Aspect Transformation
# ---------------------------------------------------------------------------
def _make_typed(gdal, path, gt, proj, width, height, dtype, nodata):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, width, height, 1, dtype,
                    options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform(gt)
    try:
        ds.SetProjection(proj)
    except Exception:
        pass
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    return ds, band


def _reclass_codes(compass, dvalid, key):
    """Integer class codes (int32) for a reclass transform over the compass-
    degree array. Directional cells get 1..N; non-directional cells get 0 here
    (the caller overwrites them with the flat sentinel or NoData).

    NS/EW use EXACT degree comparisons rather than the sign of cos/sin, because
    at the exact cardinals floating-point cos(90 deg) / sin(180 deg) are tiny
    NONZERO values whose sign is essentially noise -- e.g. cos(90) < 0 would
    (wrongly and inconsistently) send due-east to South while due-west went to
    North. The degree test is exact and consistent: the east/west boundary goes
    to North, and the north/south boundary goes to East, as documented."""
    a = np.mod(np.where(dvalid, compass, 0.0), 360.0)   # 0..360) so 360 -> 0
    if key == "NS":                       # northward half; E/W boundary -> North
        codes = np.where((a <= 90.0) | (a >= 270.0), 1, 2).astype(np.int32)
    elif key == "EW":                     # eastward half; N/S boundary -> East
        codes = np.where(a <= 180.0, 1, 2).astype(np.int32)
    elif key == "NESW":
        idx = np.floor(((a + 45.0) % 360.0) / 90.0).astype(np.int32)  # 0..3
        codes = (idx + 1).astype(np.int32)                             # 1..4
    elif key == "EIGHT":
        idx = np.floor(((a + 22.5) % 360.0) / 45.0).astype(np.int32)   # 0..7
        codes = (idx + 1).astype(np.int32)                             # 1..8
    else:
        codes = np.zeros(compass.shape, np.int32)
    return np.where(dvalid, codes, 0).astype(np.int32)


def run_aspect_transform(in_raster, out_workspace, transformations,
                         base_name=None, target_bearing=45.0,
                         input_convention=INPUT_CONVENTIONS[0],
                         flat_handling=FLAT_TRANSFORM[1], messages=None,
                         out_map=None, lyrx_map=None):
    """Generate one or more transformations of an aspect raster (see the tool
    help). ``transformations`` is a list of transform keys or labels; each
    produces its own raster in ``out_workspace``. Returns the list of output
    paths created."""
    import arcpy
    keys = []
    for t in (transformations or []):
        k = t if t in TRANSFORM_KEY.values() else TRANSFORM_KEY.get(t)
        if k and k not in keys:
            keys.append(k)
    if not keys:
        raise arcpy.ExecuteError("Choose at least one transformation.")
    is_math = str(input_convention).lower().startswith("math")
    # "keep" option mentions a Flat class; anything else that says nodata drops
    # flats. Default (unrecognized) is keep -- never silently discard flat cells.
    _fl = str(flat_handling).lower()
    flat_nodata = ("nodata" in _fl) and ("keep" not in _fl) \
        and ("flat class" not in _fl)
    gdal = _mh._gdal()
    scratch = arcpy.env.scratchFolder
    try:
        bearing = float(target_bearing)
    except (TypeError, ValueError):
        bearing = 45.0
    bearing %= 360.0

    if base_name:
        base = _legal_base(base_name)
    else:
        try:
            base = os.path.splitext(os.path.basename(
                arcpy.Describe(in_raster).catalogPath))[0]
        except Exception:
            base = "aspect"

    phases = [("Reading the aspect raster...", 1)]
    for k in keys:
        phases.append(("Computing {}...".format(_TLABEL[k]), 2))
    _mh.begin_phases(phases)

    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    try:
        band = src.GetRasterBand(1)
        width, height = src.RasterXSize, src.RasterYSize
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        in_nodata = band.GetNoDataValue()
        a = band.ReadAsArray(0, 0, width, height).astype(np.float32)
        vmask = _mh.band_validity_mask(band)   # capture while src is open
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass

    valid = np.isfinite(a)
    if in_nodata is not None:
        valid &= (a != in_nodata)
    if vmask is not None:
        valid &= vmask
    kind, why = _detect_source_kind(in_raster, a, in_nodata)
    if kind == "dinf" and not is_math:
        _warn(messages, "This looks like a D-infinity flow-direction raster "
                        "({}); set the input convention to Mathematical so the "
                        "transformations use compass directions.".format(why))
    elif kind == "d8":
        _warn(messages, "This looks like a D8 flow-direction raster ({}) -- "
                        "flow codes 1-128, not degrees; not valid aspect "
                        "input.".format(why))
    n_bad = int(np.count_nonzero(valid & ((a < -1.001) | (a > 360.001))))
    if n_bad:
        _warn(messages, "{:,} cell(s) hold values outside the aspect range "
                        "(-1 to 360 degrees) -- this does not appear to be an "
                        "aspect raster.".format(n_bad))

    flat = valid & (a < 0.0)
    dvalid = valid & (a >= 0.0)
    # Work in compass degrees (float32 -- the input is already float32, so this
    # loses no precision while halving the largest shared array; the trig
    # outputs are still written as double-precision rasters). Convert a
    # mathematical (D-infinity) raster to compass once.
    compass = np.where(dvalid, a, np.float32(0.0)).astype(np.float32)
    if is_math:
        compass = np.where(dvalid, (np.float32(90.0) - compass) % np.float32(360.0),
                           np.float32(0.0)).astype(np.float32)
    del a, valid

    ws = str(out_workspace)
    # is_gdb_workspace, not a bare endswith: this is a USER-TYPED workspace, and
    # a trailing separator ("...\Data.gdb\") would otherwise flip it to the
    # folder branch and write .tif names inside the geodatabase.
    is_gdb = _mh.is_gdb_workspace(ws)
    outputs = []

    for key in keys:
        label = _TLABEL[key]
        _mh.phase_advance("Computing {}...".format(label))
        if key in _RECLASS_KEYS:
            suffix = _RECLASS_SUFFIX[key]
        elif key == "NORTHNESS":
            suffix = "_Northness"
        elif key == "EASTNESS":
            suffix = "_Eastness"
        elif key == "TRIGDEV":
            suffix = "_TrigDev{:g}".format(bearing)
        else:
            suffix = "_DevFrom{:g}".format(bearing)
        out_name = _unique_ras_name(base + suffix, ws, is_gdb)
        out_path = os.path.join(ws, out_name)

        if key in _RECLASS_KEYS:
            out_path, lyrx = _write_reclass(
                gdal, key, compass, dvalid, flat, flat_nodata, gt, proj,
                width, height, out_path, in_raster, is_math, scratch, messages)
        else:
            out_path, lyrx = _write_trig(
                gdal, key, compass, dvalid, flat, flat_nodata, bearing, gt,
                proj, width, height, out_path, in_raster, is_math, scratch,
                messages)
        if out_path:
            outputs.append(out_path)
            if out_map is not None:
                out_map[key] = out_path     # per-transformation outputs
            if lyrx_map is not None and lyrx:
                lyrx_map[key] = lyrx        # its symbology layer file

    _mh.end_phases()
    _say(messages, "Aspect Transformation complete: created {} raster(s).".format(
        len(outputs)))
    return outputs


def _legal_base(name):
    out = []
    for ch in str(name):
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    b = "".join(out).strip("_") or "aspect"
    if b[0].isdigit():
        b = "r" + b
    return b


def _unique_ras_name(base, ws, is_gdb):
    import arcpy
    stem = _legal_base(base)
    ext = "" if is_gdb else ".tif"
    name = stem + ext
    n = 2
    while arcpy.Exists(os.path.join(ws, name)):
        name = "{}_{}{}".format(stem, n, ext)
        n += 1
    return name


def _write_reclass(gdal, key, compass, dvalid, flat, flat_nodata, gt, proj,
                   width, height, out_path, in_raster, is_math, scratch, messages):
    import arcpy
    # codes has 0 at every non-directional cell; fill those in place (avoids a
    # second whole-grid int32 array).
    codes = _reclass_codes(compass, dvalid, key)
    if flat_nodata:
        codes[~dvalid] = _NODATA_INT
    else:
        codes[flat] = -1
        codes[~dvalid & ~flat] = _NODATA_INT
    tmp = arcpy.CreateUniqueName("asp_reclass.tif", scratch)
    ds, ob = _make_typed(gdal, tmp, gt, proj, width, height, gdal.GDT_Int32,
                         _NODATA_INT)
    ob.WriteArray(codes)
    ob.FlushCache(); ds.FlushCache(); ob = None; ds = None
    prev = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        # Grid-native delivery: no environment (Output Coordinate System,
        # Extent, Snap Raster, Cell Size, Mask) may re-project, crop or
        # resample the finished raster (same rule as the trig outputs).
        with _mh.neutral_grid_env():
            arcpy.management.CopyRaster(tmp, out_path,
                                        pixel_type="32_BIT_SIGNED")
    except Exception as e:
        _warn(messages, "Could not write {}: {}".format(out_path, e))
        arcpy.env.pyramid = prev
        return None, None
    finally:
        arcpy.env.pyramid = prev
    try:
        arcpy.management.Delete(tmp)
    except Exception:
        pass

    # Value attribute table + Class field.
    defs = dict(_RECLASS_DEFS[key])
    names = {code: nm for code, (nm, _rgb) in defs.items()}
    if not flat_nodata:
        names[-1] = "Flat"
    try:
        arcpy.management.BuildRasterAttributeTable(out_path, "Overwrite")
        existing = {f.name.lower() for f in arcpy.ListFields(out_path)}
        if "class" not in existing:
            arcpy.management.AddField(out_path, "Class", "TEXT", field_length=16)
        with arcpy.da.UpdateCursor(out_path, ["Value", "Class"]) as cur:
            for row in cur:
                row[1] = names.get(int(row[0]), "")
                cur.updateRow(row)
    except Exception as e:
        _warn(messages, "Created {} but could not build its class table: "
                        "{}".format(out_path, e))

    # Metadata + unique-value symbology.
    _stamp_metadata(out_path, "Aspect Transformation ({})".format(
        _TLABEL[key]), in_raster, is_math,
        "Reclassified aspect into named direction classes; the Value "
        "attribute table's Class field holds the class name{}.".format(
            "" if flat_nodata else " (flat cells = -1 = 'Flat')"), messages)
    classes = [{"value": c, "name": nm, "color": defs[c][1]}
               for c, (nm, _rgb) in defs.items()]
    if not flat_nodata:
        classes.append({"value": -1, "name": "Flat", "color": _FLAT_COLOR})
    try:
        lyrx = _mh.default_unique_value_lyrx(out_path, classes, field="Class",
                                             messages=messages)
    except Exception:
        lyrx = None
    _build_pyramids(out_path, "NEAREST", messages)
    _say(messages, "  Created {}".format(out_path))
    return out_path, lyrx


def _write_trig(gdal, key, compass, dvalid, flat, flat_nodata, bearing, gt,
                proj, width, height, out_path, in_raster, is_math, scratch,
                messages):
    import arcpy
    rad = np.radians(compass)
    if key == "NORTHNESS":
        v = np.cos(rad)
    elif key == "EASTNESS":
        v = np.sin(rad)
    elif key == "TRIGDEV":
        v = np.cos(np.radians(compass - bearing))
    else:                                   # NUMDEV, degrees 0..180
        d = np.abs((compass - bearing + 180.0) % 360.0 - 180.0)
        v = d
    # Flat cells have no direction and are ALWAYS NoData for the trigonometric
    # transforms: -1 would be indistinguishable from a real value (due south for
    # northness, due west for eastness, "facing away" for the deviation), so a
    # -1 flat sentinel would be ambiguous here. (The flat toggle only affects
    # whether the reclassifications keep a Flat class.)
    out = np.where(dvalid, v, np.nan).astype(np.float64)
    out_valid = np.isfinite(out)

    tmp = arcpy.CreateUniqueName("asp_trig.tif", scratch)
    ds, ob = _make_typed(gdal, tmp, gt, proj, width, height, gdal.GDT_Float64,
                         float(NODATA_OUT))
    stats = _mh._RasterStats()
    if out_valid.any():
        stats.add(out[out_valid])
    ob.WriteArray(np.where(out_valid, out, NODATA_OUT))
    ob.FlushCache()
    out_stats = _mh._finalize_stats(ob, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(ob, out_stats)
    ob.FlushCache(); ds.FlushCache(); ob = None; ds = None

    label = _TLABEL[key]
    cite = {"NORTHNESS": _NE_CITATION, "EASTNESS": _NE_CITATION,
            "TRIGDEV": _TW_CITATION + " " + _BEERS_CITATION,
            "NUMDEV": _POLAR_CITATION}[key]
    rng = {"NORTHNESS": "1 = due north, -1 = due south",
           "EASTNESS": "1 = due east, -1 = due west",
           "TRIGDEV": "1 = facing {:g} deg, -1 = facing away".format(bearing),
           "NUMDEV": "degrees from {:g} deg (0 - 180)".format(bearing)}[key]
    flatnote = "flat cells = NoData"
    try:
        _tt._deliver_output(
            tmp, out_path, out_stats, messages,
            title=os.path.basename(str(out_path)),
            summary=("Created by Aspect Transformation ({}) from {}: {}; "
                     "{}.{}").format(label, str(in_raster), rng, flatnote,
                     " Input read in the mathematical (D-infinity) convention."
                     if is_math else ""),
            description=("Aspect transformation '{}' ({}). {}".format(
                label, rng, cite)),
            tags="aspect, transformation, {}, Jenness Enterprises".format(key),
            citation=cite)
    except Exception as e:
        _warn(messages, "Could not write {}: {}".format(out_path, e))
        return None, None
    # Diverging blue-white-red for the +/-1 transforms; blue-yellow-red for the
    # 0-180 numeric deviation.
    ramp = ([(43, 131, 186), (255, 255, 191), (215, 25, 28)]
            if key in ("NORTHNESS", "EASTNESS", "TRIGDEV")
            else [(43, 131, 186), (255, 255, 191), (215, 25, 28)])
    try:
        lyrx = _mh.default_stretch_lyrx(out_path, ramp, messages=messages)
    except Exception:
        lyrx = None
    _say(messages, "  Created {}".format(out_path))
    return out_path, lyrx


def _stamp_metadata(out_path, title, in_raster, is_math, desc, messages):
    import arcpy
    try:
        md = arcpy.metadata.Metadata(out_path)
        md.title = title
        md.summary = ("Created by Aspect Transformation from {}.{}").format(
            str(in_raster), " Input read in the mathematical (D-infinity) "
            "convention (converted to compass)." if is_math else "")
        md.description = desc + " " + _CIRC_CITATION
        md.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                      "(http://www.jennessent.com).")
        md.save()
    except Exception:
        pass


def _build_pyramids(out_path, technique, messages):
    import arcpy
    try:
        arcpy.management.BuildPyramids(out_path, resample_technique=technique)
    except Exception as e:
        _warn(messages, "Could not build pyramids for {}: {}".format(
            out_path, e))


# ---------------------------------------------------------------------------
#  Aspect Zonal Statistics as Table
# ---------------------------------------------------------------------------
# The circular analog of Esri's Zonal Statistics as Table, computed entirely
# internally (no Spatial Analyst / Image Analyst). Zones come from an integer
# raster or from point / polyline / polygon features; every zone's aspect
# cells are gathered and summarized with the same circular statistics the
# Polar Plots tool reports. Feature zones are processed ONE ZONE AT A TIME so
# overlapping features in different zones are each analyzed in full (the
# historic Zonal Statistics rasterize-everything-first overlap bug can not
# occur here); overlapping features in the SAME zone count each cell once.
# Flat cells (aspect -1) carry no direction and are excluded from the
# statistics, but are counted and reported per zone (FLAT_COUNT / FLAT_PCT).

ZONAL_STATS = [
    ("ALL",      "All circular statistics"),
    ("MEAN",     "Mean Direction"),
    ("RBAR",     "Mean Resultant Length (r-bar)"),
    ("RLEN",     "Resultant Length"),
    ("CVAR",     "Circular Variance"),
    ("AVAR",     "Angular Variance"),
    ("CSD",      "Circular Standard Deviation"),
    ("ADEV",     "Angular Deviation"),
    ("MEDIAN",   "Median Direction"),
    ("RANGE",    "Circular Range"),
    ("KAPPA",    "von Mises Concentration (kappa)"),
    ("RAYLEIGH", "Rayleigh Test (Z and p)"),
    ("CI95",     "Mean Direction 95% Confidence Interval"),
]
ZONAL_LABELS = [lbl for _, lbl in ZONAL_STATS]
ZONAL_KEY = {lbl: k for k, lbl in ZONAL_STATS}

# key -> ordered (field, alias, ftype) tuples added to the output table.
_ZFIELDS = {
    "MEAN":     [("MEAN_DIR", "Mean Direction (degrees)", "DOUBLE")],
    "RBAR":     [("RBAR", "Mean Resultant Length (0-1)", "DOUBLE")],
    "RLEN":     [("R_LENGTH", "Resultant Length", "DOUBLE")],
    "CVAR":     [("CIRC_VAR", "Circular Variance", "DOUBLE")],
    "AVAR":     [("ANG_VAR", "Angular Variance", "DOUBLE")],
    "CSD":      [("CIRC_SD", "Circular Standard Deviation (degrees)", "DOUBLE")],
    "ADEV":     [("ANG_DEV", "Angular Deviation (degrees)", "DOUBLE")],
    "MEDIAN":   [("MEDIAN_DIR", "Median Direction (degrees)", "DOUBLE")],
    "RANGE":    [("CIRC_RANGE", "Circular Range (degrees)", "DOUBLE")],
    "KAPPA":    [("VM_KAPPA", "von Mises Concentration (kappa)", "DOUBLE")],
    "RAYLEIGH": [("RAYLEIGH_Z", "Rayleigh Z", "DOUBLE"),
                 ("RAYLEIGH_P", "Rayleigh p (uniformity)", "DOUBLE")],
    "CI95":     [("CI95_HALF", "95% CI Half-Angle (degrees)", "DOUBLE"),
                 ("CI95_LO", "95% CI Lower Bound (degrees)", "DOUBLE"),
                 ("CI95_HI", "95% CI Upper Bound (degrees)", "DOUBLE")],
}
_ZORDER = ["MEAN", "RBAR", "RLEN", "CVAR", "AVAR", "CSD", "ADEV",
           "MEDIAN", "RANGE", "KAPPA", "RAYLEIGH", "CI95"]

_ZBINS = 3600                       # 0.1-degree bins for median/range
_ZMAX_ZONES = 50000                 # histogram memory backstop


class _ZoneAcc(object):
    """Per-zone accumulator: exact trig sums for the resultant-based
    statistics plus a fine 0.1-degree histogram for the order statistics.
    Zone keys may be ints (raster zones) or any hashable field value
    (feature zones)."""

    def __init__(self):
        self.zones = {}             # key -> dict of accumulators

    def _zone(self, key):
        z = self.zones.get(key)
        if z is None:
            if len(self.zones) >= _ZMAX_ZONES:
                import arcpy
                raise arcpy.ExecuteError(
                    "More than {:,} zones -- reduce the number of distinct "
                    "zone values.".format(_ZMAX_ZONES))
            z = {"sin": 0.0, "cos": 0.0, "n": 0, "flat": 0, "nodata": 0,
                 "bad": 0, "hist": np.zeros(_ZBINS, np.float64)}
            self.zones[key] = z
        return z

    def add_compass(self, key, compass):
        """Accumulate an array of valid compass directions (degrees 0-360)."""
        z = self._zone(key)
        if compass.size == 0:
            return
        rad = np.radians(compass)
        z["sin"] += float(np.sum(np.sin(rad)))
        z["cos"] += float(np.sum(np.cos(rad)))
        z["n"] += int(compass.size)
        b = np.floor(compass * (_ZBINS / 360.0)).astype(np.int64)
        np.clip(b, 0, _ZBINS - 1, out=b)
        z["hist"] += np.bincount(b, minlength=_ZBINS)

    def add_counts(self, key, flat=0, nodata=0, bad=0):
        z = self._zone(key)
        z["flat"] += int(flat)
        z["nodata"] += int(nodata)
        z["bad"] += int(bad)


def _hist_circ_median_deg(hist):
    """Circular median from a 0.1-degree histogram in O(B) via prefix sums
    over the doubled array (the observed bin minimizing the count-weighted
    summed circular distance; Fisher 1993, Mardia & Jupp 2000)."""
    total = float(hist.sum())
    if total <= 0:
        return None
    B = hist.size
    half = B // 2
    w2 = np.concatenate([hist, hist])
    j2 = np.arange(2 * B, dtype=np.float64)
    cw = np.concatenate([[0.0], np.cumsum(w2)])
    cwj = np.concatenate([[0.0], np.cumsum(w2 * j2)])
    c = np.arange(B)
    n1 = cw[c + half + 1] - cw[c]
    a1 = cwj[c + half + 1] - cwj[c]
    term1 = a1 - c * n1                       # offsets 0..half: distance o
    n2 = cw[c + B] - cw[c + half + 1]
    a2 = cwj[c + B] - cwj[c + half + 1]
    term2 = (B + c) * n2 - a2                 # offsets half+1..B-1: B - o
    cost = term1 + term2
    cost[hist <= 0] = np.inf                  # median must be an observed bin
    b = int(np.argmin(cost))
    return (b + 0.5) * (360.0 / B)


def _hist_circ_range_deg(hist):
    """Circular range (the smallest arc containing every observation) from
    the occupied 0.1-degree bins: 360 minus the largest empty gap."""
    occ = np.nonzero(hist > 0)[0]
    if occ.size == 0:
        return None
    if occ.size == 1:
        return 0.0
    binw = 360.0 / hist.size
    gaps = np.diff(occ) * binw
    wrap = 360.0 - (occ[-1] - occ[0]) * binw
    return max(0.0, 360.0 - max(float(gaps.max()), wrap))


def _zone_stats_row(z, ignore_nodata):
    """The statistics dict for one zone's accumulators (None = NULL)."""
    out = {k: None for grp in _ZFIELDS.values() for k, _a, _t in grp}
    n = z["n"]
    if (not ignore_nodata and z["nodata"] > 0) or n == 0:
        return out
    r_len = math.hypot(z["sin"], z["cos"])
    rbar = min(r_len / n, 1.0)
    if r_len > 1e-12:
        out["MEAN_DIR"] = math.degrees(math.atan2(z["sin"], z["cos"])) % 360.0
    out["RBAR"] = rbar
    out["R_LENGTH"] = r_len
    out["CIRC_VAR"] = 1.0 - rbar
    out["ANG_VAR"] = 2.0 * (1.0 - rbar)
    out["CIRC_SD"] = math.degrees(
        math.sqrt(max(-2.0 * math.log(max(rbar, 1e-12)), 0.0)))
    out["ANG_DEV"] = math.degrees(math.sqrt(max(2.0 * (1.0 - rbar), 0.0)))
    out["MEDIAN_DIR"] = _hist_circ_median_deg(z["hist"])
    out["CIRC_RANGE"] = _hist_circ_range_deg(z["hist"])
    kappa = float(_kappa_small_sample(
        _kappa_from_rbar(np.asarray([rbar])), float(n))[0])
    out["VM_KAPPA"] = kappa
    out["_KAPPA_ADJ"] = n <= 15          # private flags for the run report
    out["_CI_CAUTION"] = False
    out["RAYLEIGH_Z"] = n * rbar * rbar
    out["RAYLEIGH_P"] = float(_rayleigh_p(np.asarray([float(n)]),
                                          np.asarray([rbar]))[0])
    # Fisher 1993 sec. 4.5.5: circular standard error 1/sqrt(n R-bar kappa)
    # (eq. 4.42), half-angle arcsin(1.96 s.e.) (eq. 4.43); undefined once
    # 1.96 s.e. reaches 1.
    if n > 1 and rbar > 0 and kappa > 0 and out["MEAN_DIR"] is not None:
        x = 1.96 / math.sqrt(n * rbar * kappa)
        if 0.0 < x < 1.0:
            half = math.degrees(math.asin(x))
            out["CI95_HALF"] = half
            out["CI95_LO"] = (out["MEAN_DIR"] - half) % 360.0
            out["CI95_HI"] = (out["MEAN_DIR"] + half) % 360.0
            out["_CI_CAUTION"] = not _fisher_interval_suitable(kappa, n)
    return out


def _split_compass(vals, is_math):
    """Partition a float array into (compass_directions, n_flat, n_bad):
    -1 = flat, other negatives / >360 = out-of-range 'bad' (excluded), and
    everything else converted to compass degrees when ``is_math``."""
    flat = np.abs(vals + 1.0) < 1e-9
    bad = (~flat) & ((vals < 0.0) | (vals > 360.0 + 1e-9))
    good = ~(flat | bad)
    v = vals[good]
    if is_math:
        v = (90.0 - v) % 360.0
    else:
        v = np.mod(v, 360.0)
    return v, int(np.count_nonzero(flat)), int(np.count_nonzero(bad))


def _zone_extent_in(in_zone_data, sr):
    """The zone dataset's extent in spatial reference ``sr`` (an arcpy
    Extent), or None when it cannot be determined."""
    import arcpy
    try:
        ext = arcpy.Describe(in_zone_data).extent
        try:
            ext = ext.projectAs(sr)
        except Exception:
            pass
        return ext
    except Exception:
        return None


def _zone_window(ext, gt, W, H, margin=2):
    """Window (c0, r0, c1, r1) of the aspect grid covering ``ext``
    (xmin, ymin, xmax, ymax) plus a margin, clamped to the raster."""
    csx, csy = gt[1], abs(gt[5])
    c0 = int(math.floor((ext[0] - gt[0]) / csx)) - margin
    c1 = int(math.ceil((ext[2] - gt[0]) / csx)) + margin
    r0 = int(math.floor((gt[3] - ext[3]) / csy)) - margin
    r1 = int(math.ceil((gt[3] - ext[1]) / csy)) + margin
    return max(0, c0), max(0, r0), min(W, c1), min(H, r1)


def _poly_paths(geom):
    """One simple matplotlib Path per RING of a polygon geometry (exterior
    and interior rings alike). The caller applies the even-odd rule --
    a point is inside when it falls inside an odd number of rings -- so
    holes subtract. (A single compound path did NOT subtract holes: on the
    Coconino stands it counted every hole's cells, up to 41% too many;
    found and fixed 2026-09-24.) Returns None for an empty geometry."""
    from matplotlib.path import Path as MplPath
    rings, cur = [], []
    for part in geom:
        for pt in part:
            if pt is None:                    # separator: interior ring next
                if len(cur) >= 3:
                    rings.append(MplPath(cur))
                cur = []
                continue
            cur.append((pt.X, pt.Y))
        if len(cur) >= 3:
            rings.append(MplPath(cur))
        cur = []
    return rings or None


def _polygon_zone_mask_rows_gdal(geoms, c0, r0, c1, r1, gt, chunk_rows):
    """Yield (row0, mask_chunk) boolean chunks of the window where cell
    CENTERS fall inside any of the zone's polygons -- the same rule as
    ``_polygon_zone_mask_rows``, but by GDAL's scanline rasterizer (C code,
    one pass per polygon over only the rows it covers) instead of testing
    every window cell against every polygon. On the Coconino stands (5,966
    polygons, 1.09 million vertices, 75 zones, 18.6 million cells) the old
    test took over 15 minutes; this takes seconds. Interior rings are holes.
    Raises on any GDAL/OGR problem so the caller can fall back."""
    from osgeo import gdal, ogr
    ogr.UseExceptions()
    gdal.UseExceptions()
    drv = ogr.GetDriverByName("MEM") or ogr.GetDriverByName("Memory")
    vds = drv.CreateDataSource("zone")
    lyr = vds.CreateLayer("zone", geom_type=ogr.wkbMultiPolygon)
    defn = lyr.GetLayerDefn()
    for g in geoms:
        wkb = g.WKB
        if not wkb:
            continue
        f = ogr.Feature(defn)
        f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(wkb)))
        lyr.CreateFeature(f)
        f = None
    mdrv = gdal.GetDriverByName("MEM")
    w = c1 - c0
    for rs in range(r0, r1, chunk_rows):
        re_ = min(r1, rs + chunk_rows)
        h = re_ - rs
        m = mdrv.Create("", w, h, 1, gdal.GDT_Byte)
        m.SetGeoTransform((gt[0] + c0 * gt[1], gt[1], 0.0,
                           gt[3] + rs * gt[5], 0.0, gt[5]))
        gdal.RasterizeLayer(m, [1], lyr, burn_values=[1])
        mask = m.GetRasterBand(1).ReadAsArray() > 0
        m = None
        yield rs, mask
    vds = None


def _polygon_zone_mask_rows(paths, c0, r0, c1, r1, gt, chunk_rows):
    """Yield (row0, mask_chunk) boolean chunks of the window where cell
    CENTERS fall inside any of the zone's polygon paths."""
    csx, csy = gt[1], abs(gt[5])
    xs = gt[0] + (np.arange(c0, c1) + 0.5) * csx
    for rs in range(r0, r1, chunk_rows):
        re_ = min(r1, rs + chunk_rows)
        ys = gt[3] - (np.arange(rs, re_) + 0.5) * csy
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel()])
        inside = np.zeros(pts.shape[0], bool)
        for rings in paths:
            if not rings:
                continue
            poly = np.zeros(pts.shape[0], bool)
            for ring in rings:
                poly ^= ring.contains_points(pts)       # even-odd: holes out
            inside |= poly
        yield rs, inside.reshape(re_ - rs, c1 - c0)


def _line_cells(geom, gt, W, H):
    """Set of (row, col) cells a polyline passes through, by densified
    sampling at one-third of a cell (marks every cell the line crosses)."""
    csx, csy = gt[1], abs(gt[5])
    step = min(csx, csy) / 3.0
    cells = set()
    for part in geom:
        prev = None
        for pt in part:
            if pt is None:
                prev = None
                continue
            if prev is not None:
                dx, dy = pt.X - prev.X, pt.Y - prev.Y
                dist = math.hypot(dx, dy)
                k = max(1, int(math.ceil(dist / step)))
                for i in range(k + 1):
                    x = prev.X + dx * i / k
                    y = prev.Y + dy * i / k
                    c = int((x - gt[0]) / csx)
                    r = int((gt[3] - y) / csy)
                    if 0 <= c < W and 0 <= r < H:
                        cells.add((r, c))
            prev = pt
    return cells


def run_aspect_zonal(in_zone_data, zone_field, in_raster, out_table,
                     ignore_nodata=True, statistics_type="ALL",
                     input_convention=INPUT_CONVENTIONS[0], messages=None):
    """Aspect Zonal Statistics as Table: circular statistics of an aspect
    raster summarized per zone, written to a standalone table -- the circular
    analog of Esri's Zonal Statistics as Table, requiring no Spatial Analyst
    or Image Analyst extension.

    Zones: an INTEGER raster (each distinct value is a zone; sampled at the
    aspect grid's cell centers, nearest neighbor) or point / polyline /
    polygon features grouped by ``zone_field``. Feature zones are processed
    one zone at a time, each over its own window of the aspect raster, so
    overlapping features in different zones are each analyzed in full.
    Polygons take every aspect cell whose CENTER falls inside the zone;
    polylines take every cell the line passes through; points take a
    bilinear WEIGHTED MEAN DIRECTION interpolated at each point (circular
    interpolation -- 358 and 2 average to 0, never 180).

    Flat cells (aspect = -1) carry no direction: they are EXCLUDED from the
    statistics and counted per zone (FLAT_COUNT, FLAT_PCT). Values outside
    -1..360 are excluded, counted, and reported as a might-not-be-aspect
    warning. ``ignore_nodata`` False makes any zone containing NoData get
    NULL statistics (Esri parity). D-infinity (mathematical-convention)
    rasters are converted per ``input_convention``.

    Returns the output table path."""
    import arcpy
    is_math = str(input_convention).lower().startswith("math")
    stat_key = ZONAL_KEY.get(statistics_type, statistics_type or "ALL")
    if stat_key not in ("ALL",) and stat_key not in _ZORDER:
        stat_key = "ALL"
    keys = _ZORDER if stat_key == "ALL" else [stat_key]

    _mh.begin_phases([
        ("Reading the zones...", 1),
        ("Gathering raster cells per zone...", 8),
        ("Writing the output table...", 2),
    ])

    dz = arcpy.Describe(in_zone_data)
    zone_is_raster = dz.dataType in ("RasterDataset", "RasterLayer",
                                     "MosaicDataset", "MosaicLayer")
    shape_type = None if zone_is_raster else dz.shapeType

    # ---- source-kind detection (informational) ---------------------------
    try:
        kind, why = _detect_source_kind(in_raster)
    except Exception:
        kind, why = "unknown", ""
    if kind == "d8":
        _warn(messages,
              "The value raster looks like a D8 FLOW DIRECTION raster ({}). "
              "D8 codes (1, 2, 4, ... 128) are categories, not angles -- "
              "circular statistics on them are meaningless. Use an aspect or "
              "D-infinity raster instead.".format(why))
    elif kind == "dinf" and not is_math:
        _warn(messages,
              "The value raster looks like a D-INFINITY flow-direction "
              "raster ({}), which reports directions in the MATHEMATICAL "
              "convention, but the input convention is set to Compass. "
              "Direction-valued results may be rotated/reflected; consider "
              "the Mathematical setting.".format(why))
    if is_math:
        _say(messages, "Mathematical input convention: directions are "
                       "converted to compass (0 = north, clockwise) before "
                       "the statistics.")
    _say(messages, "Flat cells (aspect = -1) carry no direction and are "
                   "EXCLUDED from the circular statistics; each zone's flat "
                   "cells are counted in FLAT_COUNT and FLAT_PCT.")

    acc = _ZoneAcc()
    zone_order = []                 # first-seen order for the output rows

    def touch(key):
        if key not in acc.zones:
            zone_order.append(key)
        acc._zone(key)

    # ---- open the aspect raster ------------------------------------------
    # Only the zones' windows are read, so the large-raster check applies to
    # the zones' extent rather than to the whole raster.
    src, in_copy = _hs._open_source(in_raster, arcpy.env.scratchFolder,
                                    messages, check_size=False)
    try:
        band = src.GetRasterBand(1)
        W, H = src.RasterXSize, src.RasterYSize
        gt = src.GetGeoTransform()
        nodata = band.GetNoDataValue()
        csx, csy = gt[1], abs(gt[5])
        rsr = arcpy.Describe(in_raster).spatialReference
        _hs.check_extent_window(src, in_raster,
                                _zone_extent_in(in_zone_data, rsr), messages)

        total_flat = 0
        total_bad = 0

        def feed(key, vals):
            """Accumulate raw aspect values (already NoData-masked counted
            separately) into a zone."""
            nonlocal total_flat, total_bad
            good, n_flat, n_bad = _split_compass(vals, is_math)
            acc.add_compass(key, good)
            acc.add_counts(key, flat=n_flat, bad=n_bad)
            total_flat += n_flat
            total_bad += n_bad

        if zone_is_raster:
            _run_zonal_raster(in_zone_data, band, gt, W, H, nodata, rsr,
                              acc, zone_order, feed, messages)
        elif shape_type == "Point":
            _run_zonal_points(in_zone_data, zone_field, band, gt, W, H,
                              nodata, rsr, is_math, acc, zone_order, touch,
                              messages)
        elif shape_type in ("Polyline", "Polygon"):
            _run_zonal_shapes(in_zone_data, zone_field, shape_type, band, gt,
                              W, H, nodata, rsr, acc, zone_order, touch,
                              feed, messages)
        else:
            raise arcpy.ExecuteError(
                "Unsupported zone shape type: {} (points, polylines, "
                "polygons, or an integer raster).".format(shape_type))
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass

    if total_flat:
        _say(messages, "{:,} flat cell(s)/point(s) (aspect = -1) were "
                       "excluded from the statistics.".format(total_flat))
    if total_bad:
        _warn(messages,
              "{:,} value(s) fall outside the aspect range (-1 to 360 "
              "degrees) and were excluded -- this may not be an aspect "
              "raster.".format(total_bad))

    _mh.phase_advance("Writing the output table...")
    out_table = _write_zonal_table(
        out_table, in_zone_data, zone_field, zone_is_raster, acc, zone_order,
        keys, ignore_nodata, in_raster, statistics_type, messages)
    _mh.end_phases()
    return out_table


def _nan_mask_invalid(band, arr, xoff, yoff):
    """NaN out cells the band's validity mask marks invalid (the gdb-GRID
    mask-band-only NoData trap) in a float window read at (xoff, yoff) --
    downstream np.isfinite screens then treat them as NoData."""
    v = _mh.mask_band_valid(band, np.ones(arr.shape, bool), xoff, yoff)
    if not v.all():
        arr[~v] = np.nan
    return arr


def _run_zonal_raster(in_zone, band, gt, W, H, nodata, rsr, acc, zone_order,
                      feed, messages):
    """Integer-raster zones: every aspect cell is assigned the zone value at
    its center (nearest neighbor in the zone grid), streamed in row blocks."""
    import arcpy
    dz = arcpy.Describe(in_zone)
    try:
        if not dz.isInteger:
            raise arcpy.ExecuteError(
                "The zone raster must be an INTEGER raster (each distinct "
                "value is a zone).")
    except AttributeError:
        pass
    zsr = dz.spatialReference
    if (zsr and rsr and zsr.name not in ("Unknown", "") and
            rsr.name not in ("Unknown", "") and zsr.name != rsr.name):
        raise arcpy.ExecuteError(
            "The zone raster ({}) and the input raster ({}) are in "
            "different coordinate systems -- project one to match the other "
            "first (Project Raster).".format(zsr.name, rsr.name))

    zsrc, zcopy = _hs._open_source(in_zone, arcpy.env.scratchFolder, messages)
    try:
        zband = zsrc.GetRasterBand(1)
        zW, zH = zsrc.RasterXSize, zsrc.RasterYSize
        zgt = zsrc.GetGeoTransform()
        znodata = zband.GetNoDataValue()
        zcsx, zcsy = zgt[1], abs(zgt[5])

        # Aspect-grid window covering the zone raster's extent.
        zext = (zgt[0], zgt[3] - zH * zcsy, zgt[0] + zW * zcsx, zgt[3])
        c0, r0, c1, r1 = _zone_window(zext, gt, W, H, margin=0)
        if c1 <= c0 or r1 <= r0:
            _warn(messages, "The zone raster does not overlap the input "
                            "raster; the output table will be empty.")
            return
        csx, csy = gt[1], abs(gt[5])
        xs = gt[0] + (np.arange(c0, c1) + 0.5) * csx
        zc = np.floor((xs - zgt[0]) / zcsx).astype(np.int64)
        cok = (zc >= 0) & (zc < zW)

        _mh.phase_advance("Gathering raster cells per zone...")
        block = 512
        nblocks = max(1, (r1 - r0 + block - 1) // block)
        done = 0
        for rs in range(r0, r1, block):
            re_ = min(r1, rs + block)
            ys = gt[3] - (np.arange(rs, re_) + 0.5) * csy
            zr = np.floor((zgt[3] - ys) / zcsy).astype(np.int64)
            rok = (zr >= 0) & (zr < zH)
            if not (rok.any() and cok.any()):
                done += 1
                continue
            arr = band.ReadAsArray(c0, rs, c1 - c0, re_ - rs).astype(np.float64)
            # Read the needed zone rows/cols once, then gather per-cell.
            zr0, zr1 = int(zr[rok].min()), int(zr[rok].max()) + 1
            zc0, zc1 = int(zc[cok].min()), int(zc[cok].max()) + 1
            zarr = zband.ReadAsArray(zc0, zr0, zc1 - zc0, zr1 - zr0)
            zri = np.clip(zr - zr0, 0, zr1 - zr0 - 1)
            zci = np.clip(zc - zc0, 0, zc1 - zc0 - 1)
            zvals = zarr[zri[:, None], zci[None, :]]
            inzone = rok[:, None] & cok[None, :]
            if znodata is not None:
                inzone &= (zvals != znodata)
            # Integer zone grids from a gdb can carry mask-band-only NoData
            # (phantom fill would otherwise become a spurious zone value).
            zvalid = _mh.mask_band_valid(
                zband, np.ones(zarr.shape, bool), zc0, zr0)
            if not zvalid.all():
                inzone &= zvalid[zri[:, None], zci[None, :]]
            if not inzone.any():
                done += 1
                _mh.phase_step(done, nblocks)
                continue
            afinite = np.isfinite(arr)
            if nodata is not None:
                afinite &= (arr != nodata)
            afinite = _mh.mask_band_valid(band, afinite, c0, rs)
            for zv in np.unique(zvals[inzone]):
                key = int(zv)
                sel = inzone & (zvals == zv)
                if key not in acc.zones:
                    zone_order.append(key)
                    acc._zone(key)
                nd = int(np.count_nonzero(sel & ~afinite))
                if nd:
                    acc.add_counts(key, nodata=nd)
                feed(key, arr[sel & afinite])
            done += 1
            _mh.phase_step(done, nblocks)
    finally:
        zsrc = None
        if zcopy:
            try:
                arcpy.management.Delete(zcopy)
            except Exception:
                pass


def _run_zonal_points(in_zone, zone_field, band, gt, W, H, nodata, rsr,
                      is_math, acc, zone_order, touch, messages):
    """Point zones: the aspect at each point is a bilinear WEIGHTED MEAN
    DIRECTION from the four surrounding cell centers (flat and NoData
    neighbors excluded, weights renormalized); each point contributes one
    direction to its zone."""
    import arcpy
    d = arcpy.Describe(in_zone)
    if d.shapeType != "Point":
        raise arcpy.ExecuteError(
            "Multipoint zone features are not supported -- use single-part "
            "points.")
    _say(messages, "Point zones: the aspect at each point is interpolated "
                   "as a bilinear weighted mean direction (circular "
                   "interpolation).")
    keys_, xs, ys = [], [], []
    n_null = 0
    with _zone_cursor(in_zone, [zone_field, "SHAPE@XY"], rsr, gt, W, H,
                      messages, "points") as cur:
        for key, xy in cur:
            if xy is None:
                continue
            if key is None:
                n_null += 1
                continue
            keys_.append(key)
            xs.append(xy[0])
            ys.append(xy[1])
    if n_null:
        _warn(messages, "{:,} point(s) have a NULL zone value and were "
                        "skipped.".format(n_null))
    n_pts = len(keys_)
    if n_pts == 0:
        _warn(messages, "No usable zone points were found.")
        return
    xs = np.asarray(xs, np.float64)
    ys = np.asarray(ys, np.float64)
    csx, csy = gt[1], abs(gt[5])

    c0, r0, c1, r1 = _zone_window(
        (xs.min(), ys.min(), xs.max(), ys.max()), gt, W, H)
    if c1 <= c0 or r1 <= r0:
        _warn(messages, "None of the points fall within the aspect raster.")
        for k in keys_:
            touch(k)
            acc.add_counts(k, nodata=1)
        return
    _mh.phase_advance("Gathering raster cells per zone...")
    arr = band.ReadAsArray(c0, r0, c1 - c0, r1 - r0).astype(np.float64)
    Hw, Ww = arr.shape
    x0w = gt[0] + c0 * csx
    y0w = gt[3] - r0 * csy

    valid = np.isfinite(arr)
    if nodata is not None:
        valid &= (arr != nodata)
    valid = _mh.mask_band_valid(band, valid, c0, r0)
    flatm = valid & (np.abs(arr + 1.0) < 1e-9)
    badm = valid & ~flatm & ((arr < 0.0) | (arr > 360.0 + 1e-9))
    dirm = valid & ~flatm & ~badm
    comp = np.where(dirm, (90.0 - arr) % 360.0 if is_math
                    else np.mod(arr, 360.0), 0.0)
    sinA = np.where(dirm, np.sin(np.radians(comp)), 0.0)
    cosA = np.where(dirm, np.cos(np.radians(comp)), 0.0)

    colf = (xs - x0w) / csx - 0.5          # cell-center coordinates
    rowf = (y0w - ys) / csy - 0.5
    cL = np.floor(colf).astype(np.int64)
    rT = np.floor(rowf).astype(np.int64)
    fx = colf - cL
    fy = rowf - rT
    ssum = np.zeros(n_pts)
    csum = np.zeros(n_pts)
    wdir = np.zeros(n_pts)
    wflat = np.zeros(n_pts)
    for dr in (0, 1):
        for dc in (0, 1):
            cc = cL + dc
            rr = rT + dr
            w = (fx if dc else 1.0 - fx) * (fy if dr else 1.0 - fy)
            inb = (cc >= 0) & (cc < Ww) & (rr >= 0) & (rr < Hw) & (w > 0)
            ccs = np.clip(cc, 0, Ww - 1)
            rrs = np.clip(rr, 0, Hw - 1)
            dm = inb & dirm[rrs, ccs]
            fm = inb & flatm[rrs, ccs]
            ssum += np.where(dm, w * sinA[rrs, ccs], 0.0)
            csum += np.where(dm, w * cosA[rrs, ccs], 0.0)
            wdir += np.where(dm, w, 0.0)
            wflat += np.where(fm, w, 0.0)

    n_flat_pts = 0
    n_nodata_pts = 0
    per_zone = {}
    for i in range(n_pts):
        k = keys_[i]
        touch(k)
        if wdir[i] > 1e-12:
            r = math.hypot(ssum[i], csum[i])
            if r > 1e-12:
                per_zone.setdefault(k, []).append(
                    math.degrees(math.atan2(ssum[i], csum[i])) % 360.0)
                continue
        if wflat[i] > 1e-12:
            acc.add_counts(k, flat=1)
            n_flat_pts += 1
        else:
            acc.add_counts(k, nodata=1)
            n_nodata_pts += 1
    for k, dirs in per_zone.items():
        acc.add_compass(k, np.asarray(dirs, np.float64))
    if n_flat_pts:
        _say(messages, "{:,} point(s) fall on entirely flat cells and were "
                       "excluded (counted in FLAT_COUNT).".format(n_flat_pts))
    if n_nodata_pts:
        _say(messages, "{:,} point(s) fall on NoData or outside the raster "
                       "and were excluded.".format(n_nodata_pts))


def _raster_extent_polygon(gt, W, H, rsr):
    """The raster's footprint as a polygon in its own spatial reference."""
    import arcpy
    csy = abs(gt[5])
    x0, x1 = gt[0], gt[0] + W * gt[1]
    y1, y0 = gt[3], gt[3] - H * csy
    return arcpy.Polygon(arcpy.Array([
        arcpy.Point(x0, y0), arcpy.Point(x0, y1), arcpy.Point(x1, y1),
        arcpy.Point(x1, y0), arcpy.Point(x0, y0)]), rsr)


def _zone_cursor(in_zone, fields, rsr, gt, W, H, messages, what):
    """SearchCursor over the zone features restricted (when this arcpy
    supports spatial_filter) to features INTERSECTING the raster's extent:
    zones entirely outside contribute nothing, so fetching and projecting
    them -- the dominant cost when a small raster meets a large feature
    class -- is skipped at the geodatabase level. Features skipped this way
    do not appear in the output table (matching Esri's
    intersection-of-extents behavior)."""
    import arcpy
    import clip_tools as _ct
    # A cursor's spatial_reference= reprojects WITHOUT a datum transformation
    # (verified 2026-09-23: NAD27 zones on a NAD83 raster landed ~200 m off),
    # so read the geometry natively and project it here with the datum shift.
    zsr = arcpy.Describe(in_zone).spatialReference
    native = ["SHAPE@" if f in ("SHAPE@XY", "SHAPE@") else f for f in fields]
    want_xy = [f == "SHAPE@XY" for f in fields]
    same = _ct._same_datum(zsr, rsr) if zsr else True
    try:
        cur = arcpy.da.SearchCursor(
            in_zone, native, spatial_reference=(rsr if same else None),
            spatial_filter=_raster_extent_polygon(gt, W, H, rsr))
        _say(messages, "Only zone {} intersecting the raster extent are "
                       "analyzed (others are skipped and do not appear in "
                       "the output).".format(what))
    except Exception:
        cur = arcpy.da.SearchCursor(in_zone, native,
                                    spatial_reference=(rsr if same else None))
    return _ProjectingCursor(cur, rsr, want_xy, same)


class _ProjectingCursor(object):
    """Wraps a SearchCursor so its geometry columns come back in the raster's
    spatial reference WITH the datum transformation applied (and as (x, y)
    tuples where SHAPE@XY was asked for)."""

    def __init__(self, cur, rsr, want_xy, same):
        self._cur, self._rsr, self._xy, self._same = cur, rsr, want_xy, same

    def __enter__(self):
        return self

    def __exit__(self, *a):
        try:
            del self._cur
        except Exception:
            pass
        return False

    def __iter__(self):
        import clip_tools as _ct
        for row in self._cur:
            out = list(row)
            for i, xy in enumerate(self._xy):
                g = out[i]
                if g is None or not hasattr(g, "spatialReference"):
                    continue
                if not self._same:
                    g = _ct.project_geometry(g, self._rsr)
                if xy:
                    c = g.centroid if g.type != "point" else g.firstPoint
                    out[i] = (c.X, c.Y) if c is not None else None
                else:
                    out[i] = g
            yield tuple(out)


def _run_zonal_shapes(in_zone, zone_field, shape_type, band, gt, W, H,
                      nodata, rsr, acc, zone_order, touch, feed, messages):
    """Polyline / polygon zones, one ZONE at a time over its own window of
    the aspect raster: polygons take cells whose centers fall inside;
    polylines take every cell the line passes through. Overlapping features
    in different zones are each analyzed in full; within one zone, cells
    count once."""
    import arcpy
    groups = {}
    n_null = 0
    n_curved = 0
    min_cs = min(gt[1], abs(gt[5]))
    with _zone_cursor(in_zone, [zone_field, "SHAPE@"], rsr, gt, W, H,
                      messages, "features") as cur:
        for key, geom in cur:
            if geom is None:
                continue
            if key is None:
                n_null += 1
                continue
            # TRUE-CURVE geometries (circles, arcs) expose only their arc
            # endpoints when the vertices are read, which would collapse a
            # circle to a degenerate 2-3 point 'polygon' containing nothing.
            # Densify curves into straight segments no longer than one cell
            # so the boundary error is far below the cell size.
            if getattr(geom, "hasCurves", False):
                try:
                    geom = geom.densify("DISTANCE", min_cs, min_cs * 0.05)
                    n_curved += 1
                except Exception:
                    pass
            groups.setdefault(key, []).append(geom)
    if n_curved:
        _say(messages, "{:,} feature(s) contain true curves (circles/arcs) "
                       "and were densified to one-cell segments for the "
                       "cell test.".format(n_curved))
    if n_null:
        _warn(messages, "{:,} feature(s) have a NULL zone value and were "
                        "skipped.".format(n_null))
    if not groups:
        _warn(messages, "No usable zone features were found.")
        return
    if shape_type == "Polygon":
        _say(messages, "Polygon zones: every raster cell whose CENTER falls "
                       "inside the zone is used.")
    else:
        _say(messages, "Polyline zones: every raster cell the line passes "
                       "through is used.")

    _mh.phase_advance("Gathering raster cells per zone...")
    nz = len(groups)
    nfeat = sum(len(g) for g in groups.values())
    _say(messages, "{:,} zone(s) from {:,} feature(s); each zone's cells are "
                   "gathered in turn.".format(nz, nfeat))
    import time as _time
    t_all = _time.time()
    use_gdal = True
    done_feat = 0
    for zi, (key, geoms) in enumerate(groups.items()):
        touch(key)
        t_zone = _time.time()
        _mh.phase_step(done_feat, nfeat)
        try:
            arcpy.SetProgressorLabel(
                "Gathering cells: zone {:,} of {:,}, {} ({:,} feature{})..."
                .format(zi + 1, nz, key, len(geoms),
                        "" if len(geoms) == 1 else "s"))
        except Exception:
            pass
        ext = None
        for g in geoms:
            e = g.extent
            ext = (e.XMin, e.YMin, e.XMax, e.YMax) if ext is None else (
                min(ext[0], e.XMin), min(ext[1], e.YMin),
                max(ext[2], e.XMax), max(ext[3], e.YMax))
        c0, r0, c1, r1 = _zone_window(ext, gt, W, H)
        if c1 <= c0 or r1 <= r0:
            done_feat += len(geoms)
            _mh.phase_step(done_feat, nfeat)
            continue
        n_before = acc.zones[key]["n"] if key in acc.zones else 0

        def eat(vals_block):
            fin = np.isfinite(vals_block)
            if nodata is not None:
                fin &= (vals_block != nodata)
            nd = int(vals_block.size - np.count_nonzero(fin))
            if nd:
                acc.add_counts(key, nodata=nd)
            feed(key, vals_block[fin])

        if shape_type == "Polygon":
            chunk = max(1, 4_000_000 // max(1, c1 - c0))
            chunks = None
            if use_gdal:
                try:
                    chunks = list(_polygon_zone_mask_rows_gdal(
                        geoms, c0, r0, c1, r1, gt, chunk))
                except Exception as e:
                    use_gdal = False
                    _warn(messages, "Fast polygon rasterization unavailable "
                                    "({}); using the slower cell-by-cell "
                                    "test.".format(e))
            if chunks is None:
                paths = [_poly_paths(g) for g in geoms]
                chunks = _polygon_zone_mask_rows(paths, c0, r0, c1, r1,
                                                 gt, chunk)
            for rs, mask in chunks:
                if not mask.any():
                    continue
                # read only the columns this chunk's mask actually uses
                cols = np.flatnonzero(mask.any(axis=0))
                ca, cb = int(cols[0]), int(cols[-1]) + 1
                sub = mask[:, ca:cb]
                arr = band.ReadAsArray(c0 + ca, rs, cb - ca,
                                       sub.shape[0]).astype(np.float64)
                arr = _nan_mask_invalid(band, arr, c0 + ca, rs)
                eat(arr[sub])
        else:
            cells = set()
            for g in geoms:
                cells |= _line_cells(g, gt, W, H)
            if cells:
                rc = np.asarray(sorted(cells), np.int64)
                rows, cols = rc[:, 0], rc[:, 1]
                for rs in range(int(rows.min()), int(rows.max()) + 1, 512):
                    re_ = min(int(rows.max()) + 1, rs + 512)
                    sel = (rows >= rs) & (rows < re_)
                    if not sel.any():
                        continue
                    cA, cB = int(cols[sel].min()), int(cols[sel].max()) + 1
                    arr = band.ReadAsArray(cA, rs, cB - cA,
                                           re_ - rs).astype(np.float64)
                    arr = _nan_mask_invalid(band, arr, cA, rs)
                    eat(arr[rows[sel] - rs, cols[sel] - cA])
        done_feat += len(geoms)
        _mh.phase_step(done_feat, nfeat)
        n_after = acc.zones[key]["n"] if key in acc.zones else 0
        _say(messages, "  Zone {:,} of {:,}: {} -- {:,} feature{}, {:,} cells, "
                       "{:.1f} s.".format(zi + 1, nz, key, len(geoms),
                                          "" if len(geoms) == 1 else "s",
                                          int(n_after - n_before),
                                          _time.time() - t_zone))
    _say(messages, "All {:,} zones gathered in {:.1f} s.".format(
        nz, _time.time() - t_all))


def _write_zonal_table(out_table, in_zone_data, zone_field, zone_is_raster,
                       acc, zone_order, keys, ignore_nodata, in_raster,
                       statistics_type, messages):
    """Create the output table, write one row per zone, and stamp metadata."""
    import arcpy
    # A table in a plain folder is a dBASE table and needs the .dbf extension
    # (its field names already fit dBASE's 10-character limit).
    out_table = _mh.dbf_table_path(out_table)
    dbf = _mh.is_shapefile_target(out_table)
    ws, name = os.path.split(str(out_table))
    arcpy.management.CreateTable(ws, name)

    # Zone-identifier field mirrors the source: raster zones get 'Value'.
    if zone_is_raster:
        zf_name, zf_type, zf_len = "Value", "LONG", None
    else:
        zf_name, zf_type, zf_len = zone_field, "DOUBLE", None
        for f in arcpy.ListFields(in_zone_data):
            if f.name.lower() == str(zone_field).lower():
                if f.type in ("String", "GlobalID", "Guid", "Date"):
                    zf_type, zf_len = "TEXT", max(f.length or 50, 20)
                elif f.type in ("Integer", "SmallInteger", "BigInteger",
                                "OID"):
                    zf_type = "LONG"
                break
        try:
            zf_name = arcpy.ValidateFieldName(zf_name, ws)
        except Exception:
            pass
    # The new table already owns OBJECTID (and friends): uniquify the zone
    # field name against the freshly created schema so an ObjectID zone
    # field becomes e.g. OBJECTID_1 instead of failing.
    taken = {fld.name.lower() for fld in arcpy.ListFields(out_table)}
    base_zf, k = zf_name, 0
    while zf_name.lower() in taken:
        k += 1
        zf_name = "{}_{}".format(base_zf, k)
    # one AddFields call for the whole schema (standing rule: a per-field
    # AddField loop is a full GP launch per field)
    fdesc = [[zf_name, zf_type,
              zone_field if not zone_is_raster else "Zone Value",
              zf_len or "", "", ""],
             ["COUNT", "LONG", "Count of Directional Cells", "", "", ""],
             ["FLAT_COUNT", "LONG", "Flat Cells (aspect = -1)", "", "", ""],
             ["FLAT_PCT", "DOUBLE", "Flat Cells (%)", "", "", ""]]
    stat_fields = []
    for k in keys:
        for fname, alias, ftype in _ZFIELDS[k]:
            fdesc.append([fname, ftype, alias, "", "", ""])
            stat_fields.append(fname)
    arcpy.management.AddFields(out_table, fdesc)

    # Numeric zone keys sort ascending; anything else keeps first-seen order.
    try:
        ordered = sorted(zone_order)
    except TypeError:
        ordered = zone_order

    big = False
    n_adj = n_caution = 0
    # A dBASE table cannot hold NULL: statistics that cannot be computed
    # for a zone are written as -999 there (the toolbox's convention).
    nul = -999.0 if dbf else None
    with arcpy.da.InsertCursor(
            out_table, [zf_name, "COUNT", "FLAT_COUNT", "FLAT_PCT"]
            + stat_fields) as cur:
        for key in ordered:
            z = acc.zones[key]
            stats = _zone_stats_row(z, ignore_nodata)
            if stats.get("VM_KAPPA") is not None and stats.get("_KAPPA_ADJ"):
                n_adj += 1
            if stats.get("_CI_CAUTION"):
                n_caution += 1
            n, flat = z["n"], z["flat"]
            denom = n + flat
            fpct = (100.0 * flat / denom) if denom > 0 else nul
            if n > 2147483647 or flat > 2147483647:
                big = True
            row = [key,
                   min(n, 2147483647), min(flat, 2147483647), fpct]
            row += [nul if stats[f] is None else stats[f] for f in stat_fields]
            cur.insertRow(row)
    if dbf:
        _say(messages, "Statistics that could not be computed are -999 (a "
                       "dBASE table cannot hold NULL).")
    if big:
        _warn(messages, "A zone exceeded the 32-bit COUNT limit; its count "
                        "was clamped to 2,147,483,647.")
    if n_adj and ("KAPPA" in keys or "CI95" in keys):
        _say(messages, "{:,} zone(s) have 15 or fewer directional cells; "
                       "their von Mises concentration carries the "
                       "small-sample correction of Best and Fisher (1981; "
                       "Fisher 1993, eq. 4.41)."
                       .format(n_adj))
    if n_caution and "CI95" in keys:
        _warn(messages, "{:,} zone(s) have a confidence interval outside the "
                        "range Fisher (1993, Table 4.3) recommends for the "
                        "standard-error method (weak concentration or few "
                        "cells); he advises a bootstrap interval there. Read "
                        "those intervals with caution.".format(n_caution))
    _say(messages, "Wrote {:,} zone row(s) to {}.".format(
        len(ordered), out_table))

    # Output metadata: conceptual description + per-run provenance (standing
    # rule). Failure to stamp must never fail the tool.
    try:
        md = arcpy.metadata.Metadata(out_table)
        md.title = "Aspect Zonal Statistics"
        md.summary = (
            "Circular (directional) statistics of the aspect raster '{}' "
            "summarized per zone from '{}' (statistic: {}). Flat cells "
            "(aspect = -1) were excluded from the statistics and counted in "
            "FLAT_COUNT/FLAT_PCT.").format(
                os.path.basename(str(in_raster)),
                os.path.basename(str(in_zone_data)),
                statistics_type)
        md.description = (
            "Zonal summary of an aspect raster using CIRCULAR statistics "
            "(aspect is a circular variable: 0 and 360 degrees are the same "
            "direction, so ordinary zonal means are wrong). Produced by the "
            "Aspect Zonal Statistics as Table tool (Jenness Enterprises "
            "Wildlife and Forestry Tools), the circular analog of Esri's Zonal Statistics "
            "as Table; unlike that tool it needs no Spatial Analyst or Image "
            "Analyst extension. " + _CIRC_CITATION)
        md.save()
    except Exception:
        pass
    return out_table
