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

"""Hillshade tools for the Jenness Enterprises toolbox (Cartography Tools).

Ports of the VB6 hillshade functions in ``Surface_Area_Functions.bas``
(Surface_Area / Surface Tools extension), using the fast-raster machinery
established in ``mahalanobis.py`` (GDAL block I/O, vectorized numpy,
self-computed statistics + histogram carried through CopyRaster):

* ``run_enhanced_hillshade`` -- ``CalculateHillshadeAlternative``
* ``run_multisun_hillshade`` -- new tool (no VB6 ancestor; concept from Jeff's
  lab exercise "Calculate a Hillshade Based on Two Sun Locations"): a
  weighted average of standard hillshades from up to eight user-chosen sun
  positions, each with its own azimuth, inclination, and weight -- the
  classic Swiss multiple-light-source strategy. Each sun's hillshade is
  clamped at 0 and (optionally) its own cast shadows are dimmed to the
  shadow-brightness percent BEFORE the weighted average; hypsometric shading
  is applied once to the averaged result. With a single sun at weight 100
  this reduces exactly to the Enhanced Hillshade.
* ``run_blurred_hillshade`` -- new tool (concept from Jeff's lab exercise
  "Creating a Blurred Hillshade"): the softening component of the Swiss
  method. Same options as the Enhanced Hillshade plus a circular blur of a
  user radius (cells or linear units; linear radii are sized per row on the
  spheroid for geographic DEMs). Either the DEM is blurred before shading
  (default -- generalizes the terrain itself, per the lab's Focal Statistics
  recipe and "option 1 usually looks a little better") or the finished
  hillshade image is blurred after shading (also softens cast-shadow edges).
  Kernel weighting: uniform Average (the lab recipe) or Gaussian
  (sigma = radius/2, truncated at the radius; smoother falloff).
* ``run_mdow_hillshade`` -- ``CalculateJenMDOWHillshade``: the
  Multi-Directional Oblique-Weighted hillshade of Mark (1992, USGS Open-File
  Report 92-422). Four hillshades are computed with sun directions at the
  user's primary azimuth -67.5, -22.5, +22.5 and +67.5 degrees (the fan is
  centered on the primary direction) and blended per cell with weights
  sin^2(aspect - direction), which favor the directions most oblique to each
  slope and so bring out features a single light source washes out. Because
  the four directions are spaced exactly 45 degrees apart the weights always
  sum to 2, so dividing the weighted sum by 2 is an exact normalization (flat
  cells shade to cos(zenith)). No cast-shadow option (as in the legacy tool:
  a single shadow direction is ambiguous under four suns). The legacy
  "ramp over full range" option only altered the ArcMap renderer stretch --
  never the pixel values -- and is dropped (symbology is left to Pro).

What makes it "enhanced" relative to Esri's Hillshade:

* **Correct on geographic rasters.** For a DEM in a geographic coordinate
  system the cell dimensions (meters) are recomputed for every row from
  spheroidal 3-D chord distances (exact port of the legacy
  ``SpheroidalLatLongToCart`` / per-row distance table), so the gradients are
  right at any latitude. Projected rasters use the constant-cell planar
  formulas (faster).
* **Hillshade gray levels.** ``levels`` is the number of gray levels: the
  output runs 0..levels-1, each value covering an equal share of the 0-1
  illumination range (levels = 256 gives Esri's 0-255; small values give a
  posterized, cartographic hillshade). Changed in v1.99.42: the legacy VB6
  code (and this port until then) used round(levels * shade), which gave
  levels + 1 values with half-width end classes. Outputs of 32 or fewer
  levels get a raster attribute table (Value, Count).
* **Hypsometric shading.** Optionally scales the hillshade by a linear
  elevation weight so low elevations darken: at strength s (0-100), the
  multiplier runs from (1 - s/100) at the minimum elevation up to 1 at the
  maximum. (The legacy code normalized the SCALED elevation against RAW
  statistics, which skewed the weight for feet units or exaggeration != 1;
  this port normalizes consistently in scaled meters.)
* **Cast shadows (optional).** Terrain hidden from the sun is dimmed to a
  user-set percentage of its unshadowed value (0% = black, matching Esri's
  shadow behavior; the default 20% keeps some relief detail visible inside
  shadows). Implemented as a single-pass shadow sweep: marching across the
  grid away from the sun while carrying a running "shadow surface" that drops
  by tan(altitude) x step and rises to any terrain above it -- a cell is
  shadowed exactly when it lies below that carried surface. O(cells), no
  per-cell ray tracing. On geographic rasters the sweep uses mean cell
  dimensions (the shadow option is aesthetic; the hillshade itself remains
  per-row exact).

The gradient is Horn's method (Horn, B. K. P. 1981. Hill shading and the
reflectance map. Proceedings of the IEEE 69:14-47) with the legacy distance
table::

    dZ/dx = ((NE + 2E + SE) - (NW + 2W + SW)) / (2*hA + 4*h + 2*hB)
    dZ/dy = ((SW + 2S + SE) - (NW + 2N + NE)) / (4*vA + 4*vB)
    shade = cos(zenith)*cos(slope) + sin(zenith)*sin(slope)*cos(azR - aspect)
    value = min(floor(levels * shade * hypso_multiplier), levels - 1),
            clamped at 0

with hA/h/hB the horizontal cell spans of the row above / at / below the
focal row and vA/vB the vertical spans (all meters), and
``azR = radians((360 - azimuth + 90) mod 360)``, ``aspect = atan2(dZdy, -dZdx)``
wrapped to 0..2pi -- identical to the legacy conventions. Any 3x3 window
touching NoData (or the raster edge) is NoData in the output. This same Horn
gradient (via the unit normal vector it implies) underlies the VRM tool in
``topographic_tools.py``.
"""

import math
import os

import numpy as np

import arcpy
import mahalanobis as _mh

NODATA_OUT = -9999
_STRIP_ROWS = 1024          # rows per processing strip (plus a 1-row halo)

# The Horn gradient used by every hillshade mode below (and, via the unit
# normal vector it implies, by the VRM tool in topographic_tools.py).
_HORN_CITATION = ("Horn, B. K. P. 1981. Hill shading and the reflectance "
                  "map. Proceedings of the IEEE 69:14-47.")

ELEV_UNITS = ["Meters", "Feet"]
_ELEV_METERS = {"Meters": 1.0, "Feet": 0.3048}

# Blurred Hillshade choices.
RADIUS_UNITS = ["Cells", "Meters", "Kilometers", "Feet", "Miles"]
_RADIUS_METERS = {"Meters": 1.0, "Kilometers": 1000.0,
                  "Feet": 0.3048, "Miles": 1609.344}
BLUR_TARGETS = ["DEM (blur before shading)", "Hillshade (blur after shading)"]
BLUR_KERNELS = ["Average", "Gaussian"]


# Cells above this get a warning + confirm prompt, not a hard block --
# some of this toolbox's own real-world DEMs (e.g. a full-CONUS raster)
# run to ~21 BILLION cells, so this is deliberately set well above that;
# it exists to catch a corrupt/implausible file (or a genuine accidental
# mistake, like the wrong raster selected), not to gate legitimate large
# data. See _check_raster_plausible.
_WARN_RASTER_CELLS = 2_000_000_000


def _check_raster_plausible(ds, in_raster, messages=None, check_size=True):
    """Defense-in-depth plausibility check on a just-opened raster's
    DECLARED dimensions, before any tool reads its pixel data. Not a
    guarantee against a malicious/malformed file -- a crafted file can
    have entirely plausible metadata and still trigger a bug deep in a
    format decoder -- but it costs nothing and catches obviously-absurd
    claimed dimensions (which many crafted/corrupt files do have) before
    this toolbox tries to allocate arrays sized from them. The real
    protection against decoder vulnerabilities is keeping ArcGIS Pro (and
    its bundled GDAL) up to date.

    Negative/zero dimensions (never legitimate) are a hard stop. A very
    large but otherwise plausible raster only warns and asks to confirm --
    it may simply be genuinely large data, and this toolbox has real users
    with real continental-scale rasters."""
    w, h, bands = ds.RasterXSize, ds.RasterYSize, ds.RasterCount
    if w <= 0 or h <= 0 or bands <= 0:
        raise arcpy.ExecuteError(
            "The raster {} reports implausible dimensions ({} x {}, {} "
            "band(s)) and was not opened.".format(in_raster, w, h, bands))
    if check_size:
        _confirm_large(in_raster, w, h, messages, part=False)
    return ds


def _confirm_large(in_raster, w, h, messages, part):
    """Warn and ask to continue when ``w`` x ``h`` cells exceeds the
    large-raster threshold. ``part`` True words it as the portion of the
    raster a windowed tool will actually read."""
    if w * h <= _WARN_RASTER_CELLS:
        return
    if part:
        warning = (
            "The part of the raster {} covering the analysis area is very "
            "large ({:,} x {:,} = {:,} cells). Processing may take a long "
            "time and use a large amount of memory. Continue?").format(
                in_raster, w, h, w * h)
    else:
        warning = (
            "The raster {} is very large ({:,} x {:,} = {:,} cells). "
            "Processing may take a long time and use a large amount of "
            "memory, depending on the tool. Continue?").format(
                in_raster, w, h, w * h)
    if messages is not None:
        messages.addWarningMessage(warning)
    if not _mh._confirm(warning, caption="Large Raster"):
        raise arcpy.ExecuteError(
            "Cancelled: {} exceeds the {:,}-cell size check and "
            "continuing was not confirmed.".format(
                in_raster, _WARN_RASTER_CELLS))


def check_window_size(in_raster, ncols, nrows, messages=None):
    """For tools that read only a WINDOW of the raster (points, zones):
    apply the large-raster warning to the window actually read, not to the
    whole raster. Pair with ``_open_source(..., check_size=False)``."""
    _confirm_large(in_raster, max(0, int(ncols)), max(0, int(nrows)),
                   messages, part=True)


def check_extent_window(ds, in_raster, extent, messages=None):
    """``check_window_size`` for the cells of ``ds`` that an arcpy Extent
    (already in the raster's coordinate system) covers. ``extent`` None or
    unusable -> the whole raster is checked."""
    W, H = ds.RasterXSize, ds.RasterYSize
    try:
        gt = ds.GetGeoTransform()
        csx, csy = gt[1], abs(gt[5])
        c0 = max(0, int((extent.XMin - gt[0]) // csx))
        c1 = min(W, int(-(-(extent.XMax - gt[0]) // csx)))
        r0 = max(0, int((gt[3] - extent.YMax) // csy))
        r1 = min(H, int(-(-(gt[3] - extent.YMin) // csy)))
        ncols, nrows = max(0, c1 - c0), max(0, r1 - r0)
    except Exception:
        ncols, nrows = W, H
    check_window_size(in_raster, ncols, nrows, messages)


def _open_source(in_raster, scratch, messages, check_size=True):
    """Open ``in_raster`` with GDAL: ``(dataset, tmp_copy_or_None)``.

    ``check_size`` False skips the whole-raster large-size prompt, for
    tools that read only a window and check that window themselves
    (``check_window_size`` / ``check_extent_window``).

    Tries the catalog path directly, then the OpenFileGDB raster subdataset
    syntax (GDAL 3.7+; avoids exporting file-geodatabase DEMs), and only as a
    last resort exports a scratch GeoTIFF copy -- the slow path that used to
    run silently before any progress appeared.
    """
    gdal = _mh._gdal()
    try:
        path = arcpy.Describe(in_raster).catalogPath
    except Exception:
        path = str(in_raster)

    # Each `gdal.Open` attempt is narrowly wrapped -- catching only GDAL's
    # own open failure -- so _check_raster_plausible's ExecuteError (a
    # hard-block on corrupt dimensions, or the user declining a large-
    # raster confirm prompt) propagates immediately instead of being
    # treated as "this method failed, try the next fallback" (arcpy's
    # ExecuteError IS a RuntimeError subclass, so a wider try/except would
    # silently swallow it and mask it behind a generic error, or worse,
    # silently retry after a "no" answer).
    try:
        ds = gdal.Open(path)
    except RuntimeError:
        ds = None
    if ds is not None:
        return _check_raster_plausible(ds, in_raster, messages, check_size), None

    parts = _mh.split_gdb_dataset(path)
    if parts:
        try:
            ds = gdal.Open('OpenFileGDB:"{}":{}'.format(*parts))
        except RuntimeError:
            ds = None
        if ds is not None:
            return _check_raster_plausible(ds, in_raster, messages, check_size), None

    _set_label("Exporting the DEM to a scratch GeoTIFF (GDAL cannot read this "
               "format directly; this can take a while for large rasters)...")
    _msg(messages, "Exporting the DEM to a scratch GeoTIFF for processing...")
    tif = arcpy.CreateUniqueName("hillshade_src.tif", scratch)
    with _mh.neutral_grid_env():    # the copy must match the source grid
        arcpy.management.CopyRaster(path, tif)
    try:
        ds = gdal.Open(tif)
    except RuntimeError:
        raise arcpy.ExecuteError(
            "Could not open the input raster: {}".format(in_raster))
    return _check_raster_plausible(ds, in_raster, messages, check_size), tif


def _msg(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _set_label(text):
    try:
        arcpy.SetProgressorLabel(text)
    except Exception:
        pass


def _hillshade_phase_segment(mode, suns, use_hypso, model_shadows):
    """The ordered (label, weight) phases ``_run_hillshade`` will pass
    through for the shading computation itself (everything up to but not
    including delivery) -- factored out so a wrapper (Blurred Hillshade)
    that prepends its own phases and calls ``_run_hillshade`` with
    ``own_phases=False`` can declare this same segment up front."""
    seg = []
    if use_hypso:
        seg.append(("Scanning elevation statistics for hypsometric "
                    "shading...", 1))
    if model_shadows:
        n_suns = len(suns) if mode == "multi" else 1
        seg.append(("Reading the DEM for the cast-shadow sweep...", 1))
        seg.append(("Sweeping cast shadows...", 4 * n_suns))
    seg.append(("Calculating hillshade...", 6))
    return seg


# Posterized outputs (few gray levels) get a raster attribute table so each
# tone's cell count is visible; full-range hillshades (e.g. 256 levels) skip
# it -- it is rarely useful there and costs an extra read of the output.
VAT_MAX_LEVELS = 32


def _build_level_table(out_raster, levels, messages):
    """Build the attribute table for a posterized output. Returns the sorted
    gray values present (from the table), or None when no table was built."""
    if int(levels) > VAT_MAX_LEVELS:
        return None
    try:
        arcpy.management.BuildRasterAttributeTable(out_raster, "Overwrite")
        with arcpy.da.SearchCursor(out_raster, ["Value"]) as cur:
            values = sorted(int(r[0]) for r in cur)
        _msg(messages, "Built a raster attribute table (Value, Count) for the "
                       "{} gray levels.".format(int(levels)))
        return values or None
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage(
                "Could not build the raster attribute table: {}".format(e))
        return None


def _hillshade_lyrx(out_raster, level_values, messages):
    """Black-to-white output symbology. A raster with an attribute table
    opens in Pro as Unique Values with RANDOM colors, so a posterized output
    gets a Unique Values layer file in grays -- black for value 0, white for
    the highest value present; everything else gets the black-to-white
    minimum-maximum stretch."""
    if level_values:
        top = max(level_values)
        classes = []
        for v in level_values:
            g = int(round(255.0 * v / top)) if top > 0 else 0
            classes.append({"value": v, "name": str(v), "color": [g, g, g]})
        lyrx = _mh.default_unique_value_lyrx(out_raster, classes, field="Value",
                                            messages=messages)
        if lyrx:
            return lyrx
    return _mh.default_stretch_lyrx(
        out_raster, [(0, 0, 0), (255, 255, 255)], messages=messages)


# Shared finalize phases: CopyRaster -> stats -> metadata/symbology -> pyramids.
_FINALIZE_PHASES = [
    ("Saving output raster...", 2),
    ("Setting raster statistics...", 1),
    ("Creating layer file and symbology...", 1),
    ("Building pyramids (bilinear)...", 2),
]


# ----------------------------------------------------------------------------
#  Geodesic per-row cell distances (port of the legacy distance table)
# ----------------------------------------------------------------------------

def _ecef(lon_deg, lat_deg, a, b):
    """Geodetic lon/lat (degrees, height 0) -> ECEF meters on the spheroid.
    Exact port of the legacy ``SpheroidalLatLongToCart`` (Iliffe ch. 2)."""
    phi = np.radians(90.0 - np.asarray(lat_deg, dtype=np.float64))   # colatitude
    theta = np.radians(np.asarray(lon_deg, dtype=np.float64))
    e2 = (a * a - b * b) / (a * a)
    nu = a / np.sqrt(1.0 - e2 * np.cos(phi) ** 2)
    x = nu * np.sin(phi) * np.cos(theta)
    y = nu * np.sin(phi) * np.sin(theta)
    z = (1.0 - e2) * nu * np.cos(phi)
    return x, y, z


def _dist3(p, q):
    return np.sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2)


def _tri_area_3d(p, q, r):
    """Areas of 3-D triangles given three (x, y, z) triples of arrays."""
    ux, uy, uz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
    vx, vy, vz = r[0] - p[0], r[1] - p[1], r[2] - p[2]
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return 0.5 * np.sqrt(cx * cx + cy * cy + cz * cz)


def _distances_at_cells(gt, cols, rows, to_ecef):
    """Ground spans (meters) at the cells whose 0-based column and row
    indices are the equal-length arrays ``cols`` and ``rows`` (fractional
    indices allowed): a dict of arrays hA, h, hB (east-west spans of the row
    above / at / below the cell) and vA, vB (north-south spans up / down),
    from neighbor points one full cell away from the cell center converted
    to spheroidal ECEF by ``to_ecef(x, y)``. The legacy table's
    construction: the focal point is the average of the four corner ECEFs."""
    cellw = gt[1]
    cellh = abs(gt[5])
    x0 = gt[0] + (np.asarray(cols, dtype=np.float64) + 0.5) * gt[1]
    y0 = gt[3] + (np.asarray(rows, dtype=np.float64) + 0.5) * gt[5]

    above = to_ecef(x0, y0 + cellh)
    below = to_ecef(x0, y0 - cellh)
    right = to_ecef(x0 + cellw, y0)
    nw = to_ecef(x0 - cellw, y0 + cellh)
    ne = to_ecef(x0 + cellw, y0 + cellh)
    sw = to_ecef(x0 - cellw, y0 - cellh)
    se = to_ecef(x0 + cellw, y0 - cellh)
    focal = tuple((nw[i] + ne[i] + sw[i] + se[i]) / 4.0 for i in range(3))

    return {
        "vA": _dist3(focal, above),
        "hA": _dist3(above, ne),
        "h": _dist3(focal, right),
        "vB": _dist3(focal, below),
        "hB": _dist3(below, se),
    }


def _half_distances_at_cells(gt, cols, rows, to_ecef):
    """The 8-triangle surface-area table at the given cells: SQUARED
    half-cell distances vA2/hA2/dA2/h2/dB2/hB2/vB2 plus ``pix_m2`` (the true
    planimetric cell area, m^2, as the sum of the eight 3-D corner
    triangles), from points offset half a cell from each cell center."""
    halfx = gt[1] / 2.0
    halfy = abs(gt[5]) / 2.0
    x0 = gt[0] + (np.asarray(cols, dtype=np.float64) + 0.5) * gt[1]
    y0 = gt[3] + (np.asarray(rows, dtype=np.float64) + 0.5) * gt[5]

    above = to_ecef(x0, y0 + halfy)
    below = to_ecef(x0, y0 - halfy)
    left = to_ecef(x0 - halfx, y0)
    right = to_ecef(x0 + halfx, y0)
    nw = to_ecef(x0 - halfx, y0 + halfy)
    ne = to_ecef(x0 + halfx, y0 + halfy)
    sw = to_ecef(x0 - halfx, y0 - halfy)
    se = to_ecef(x0 + halfx, y0 - halfy)
    focal = tuple((nw[i] + ne[i] + sw[i] + se[i]) / 4.0 for i in range(3))

    def d2(p, q):
        return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2)

    pix = (_tri_area_3d(focal, above, ne) + _tri_area_3d(focal, ne, right) +
           _tri_area_3d(focal, right, se) + _tri_area_3d(focal, se, below) +
           _tri_area_3d(focal, below, sw) + _tri_area_3d(focal, sw, left) +
           _tri_area_3d(focal, left, nw) + _tri_area_3d(focal, nw, above))
    return {"vA2": d2(focal, above), "hA2": d2(above, ne),
            "dA2": d2(focal, ne), "h2": d2(focal, right),
            "dB2": d2(focal, se), "hB2": d2(below, se),
            "vB2": d2(focal, below), "pix_m2": pix}


def _row_distances_at(gt, height, to_ecef, col):
    """Per-row spans (arrays of length ``height``) at column ``col`` -- see
    ``_distances_at_cells``."""
    return _distances_at_cells(gt, np.full(height, float(col)),
                               np.arange(height, dtype=np.float64), to_ecef)


def _geographic_row_distances(gt, height, a, b):
    """Per-row meter distances for a geographic raster (see
    ``_distances_at_cells``), from the column-0 cell centers exactly as the
    legacy table did -- on a spheroid of revolution every column gives the
    same table, so no representative column has to be chosen."""
    return _row_distances_at(gt, height, lambda x, y: _ecef(x, y, a, b), 0.0)


# ----------------------------------------------------------------------------
#  Ground geometry for ANY raster -- geographic or projected
# ----------------------------------------------------------------------------
# Every tool that needs the ground distance between neighboring cells (slope
# and aspect gradients, hillshade normals, surface area, VRM, TRI, TWI, LSRI,
# the line tools, neighborhood radii in ground units) goes through
# ``GroundGeometry``. A geographic raster's cell centers are converted to
# spheroidal ECEF straight from their lon/lat -- per row, exactly as the
# legacy table did, since on a spheroid of revolution every column is the
# same. A PROJECTED raster's cell centers are first UNPROJECTED to lon/lat
# with GDAL's osr and then converted the same way, so the projection's scale
# distortion drops out: a Web Mercator DEM gets the same true-ground
# distances as the lat/long DEM it was made from. (Planar cell sizes, cell
# size x meters-per-unit, are right only where the projection's scale factor
# is 1; on Web Mercator they understate every slope gradient by cos(lat).)
#
# Unprojecting every cell would be slow on a large raster, and one
# representative column would be wrong for projections whose scale varies
# along the rows (azimuthal and oblique ones). So a projected raster is
# sampled on a LATTICE of cell centers -- one node every ``step`` cells in
# both directions, at most GROUND_LATTICE_MAX_NODES per axis, always
# including the last row and column -- the spans are computed exactly at the
# nodes, and every cell's spans are interpolated bilinearly between them.
# Scale factors vary smoothly, so the interpolation error is far below a
# hundredth of a percent for any projection, including polar stereographic,
# gnomonic, orthographic and oblique Mercator. The per-row ``row`` table
# (center column) remains for neighborhood radii, where it is harmless. The
# help site's "Projection distortion and terrain analysis" page explains all
# of this for users.

GROUND_LATTICE_MAX_NODES = 257       # lattice nodes per axis, at most
GROUND_LATTICE_MIN_STEP = 16         # cells between nodes, at least
GEOM_STRIP = 512                     # rows per strip in whole-grid gradient loops
_GROUND_KEYS = ("hA", "h", "hB", "vA", "vB")
_HALF_KEYS = ("vA2", "hA2", "dA2", "h2", "dB2", "hB2", "vB2", "pix_m2")

_GROUND_CACHE = {}


class _ProjectedGround(object):
    """Callable ``(x, y) -> (X, Y, Z)``: coordinates in a projected raster's
    own units to spheroidal ECEF meters -- unprojected with GDAL osr, then
    ``_ecef`` exactly as the geographic path. Built once per coordinate
    system (see ``_projected_ground``)."""

    def __init__(self, proj_wkt):
        from osgeo import osr
        ps = osr.SpatialReference()
        if ps.ImportFromWkt(proj_wkt) != 0 or not ps.IsProjected():
            raise ValueError("not a projected coordinate system")
        gs = ps.CloneGeogCS()
        for s in (ps, gs):
            try:
                s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            except Exception:
                pass
        self.a = float(gs.GetSemiMajor() or 0.0) or 6378137.0
        self.b = float(gs.GetSemiMinor() or 0.0) or 6356752.31424518
        self.ct = osr.CoordinateTransformation(ps, gs)
        self.name = ps.GetAttrValue("PROJCS") or "projected"
        self.projection = (ps.GetAttrValue("PROJECTION") or "unknown").replace("_", " ")

    def __call__(self, x, y):
        xs, ys = np.broadcast_arrays(np.asarray(x, dtype=np.float64),
                                     np.asarray(y, dtype=np.float64))
        shape = xs.shape
        pts = self.ct.TransformPoints(list(zip(xs.ravel().tolist(),
                                               ys.ravel().tolist())))
        n = len(pts)
        lon = np.fromiter((p[0] for p in pts), np.float64, n).reshape(shape)
        lat = np.fromiter((p[1] for p in pts), np.float64, n).reshape(shape)
        if not (np.all(np.isfinite(lon)) and np.all(np.isfinite(lat))):
            raise ValueError("some cell centers could not be unprojected")
        return _ecef(lon, lat, self.a, self.b)


def _projected_ground(proj_wkt):
    """The cached ``_ProjectedGround`` for a projection WKT (raises when the
    coordinate system cannot be unprojected)."""
    key = str(proj_wkt or "")
    pg = _GROUND_CACHE.get(key)
    if pg is None:
        pg = _ProjectedGround(key)
        if len(_GROUND_CACHE) > 16:
            _GROUND_CACHE.clear()
        _GROUND_CACHE[key] = pg
    return pg


def _interp_index(nodes, q):
    """(index, weight) for linear interpolation of ``q`` between the sorted
    ``nodes``: value = table[index] * (1 - weight) + table[index + 1] * weight
    (a single node gives index 0 and weight 0 everywhere)."""
    n = len(nodes)
    q = np.asarray(q, dtype=np.float64)
    if n == 1:
        return np.zeros(q.shape, dtype=np.int64), np.zeros(q.shape)
    i = np.clip(np.searchsorted(nodes, q, side="right") - 1, 0, n - 2)
    w = (q - nodes[i]) / (nodes[i + 1] - nodes[i])
    return i, np.clip(w, 0.0, 1.0)


class GroundGeometry(object):
    """True-ground cell geometry of a north-up raster in ANY coordinate
    system (see the section comment above).

    ``row``            per-row dict hA/h/hB/vA/vB (meters, arrays of length
                       ``height``): column 0 of a geographic raster, the
                       center column of a projected one -- for neighborhood
                       radii and other per-row needs.
    ``rows(r0, r1)``   the same five spans for EVERY cell of rows r0..r1-1
                       as 2-D arrays of shape (r1 - r0, width).
    ``half_rows(r0, r1)``  the 8-triangle surface-area table for those cells
                       (squared half-cell distances vA2/hA2/dA2/h2/dB2/hB2/vB2
                       and ``pix_m2``, the true planimetric cell area), 2-D.
    ``half_row``       the per-row version of that table (as ``row``).
    ``mode``           "geographic", "geodesic" (a projected raster,
                       unprojected) or "planar" (the fallback when a
                       projected coordinate system cannot be unprojected).
    ``info``           scale statistics and the ``note`` / ``warning`` texts
                       (``ground_geometry`` posts them as run messages).
    """

    def __init__(self, gt, width, height, sr, proj_wkt=None, what="DEM"):
        self.gt = tuple(float(v) for v in gt)
        self.width = int(width)
        self.height = int(height)
        self.what = what
        self.geographic = (sr.type == "Geographic")
        self.mode = None
        self.info = {"mode": None, "scale_x": 1.0, "scale_y": 1.0,
                     "scale_min": 1.0, "scale_max": 1.0,
                     "lattice": (0, 0), "projection": "", "name": "",
                     "note": "", "warning": ""}
        self._to_ecef = None
        self._half_row = None
        self._lat_full = None
        self._lat_half = None
        W, H = self.width, self.height
        gt = self.gt
        if self.geographic:
            a = float(sr.semiMajorAxis or 6378137.0)
            b = float(sr.semiMinorAxis or 6356752.31424518)
            self._to_ecef = lambda x, y: _ecef(x, y, a, b)
            self.row = _row_distances_at(gt, H, self._to_ecef, 0.0)
            self.mode = "geographic"
            self.info["name"] = str(getattr(sr, "name", "") or "")
            self.info["note"] = (
                "Geographic {}: ground distances computed per row on the "
                "spheroid (a = {:.1f} m, b = {:.1f} m).".format(what, a, b))
        else:
            mpu = float(sr.metersPerUnit or 1.0)
            self._cw_nom = abs(gt[1]) * mpu
            self._ch_nom = abs(gt[5]) * mpu
            pg = None
            err = ""
            for candidate in (proj_wkt, None):
                if candidate is None:
                    try:
                        candidate = sr.exportToString()
                    except Exception:
                        candidate = ""
                if not candidate:
                    continue
                try:
                    pg = _projected_ground(candidate)
                    row = _row_distances_at(gt, H, pg, (W - 1) / 2.0)
                    if not all(np.all(np.isfinite(v)) and np.all(v > 0)
                               for v in row.values()):
                        raise ValueError("some cell centers could not be unprojected")
                    self._build_lattice(pg)
                    break
                except Exception as e:
                    pg = None
                    err = str(e) or e.__class__.__name__
            if pg is not None:
                self._to_ecef = pg
                self.row = row
                self.mode = "geodesic"
                self._describe_scale(pg)
            else:
                const = np.full(H, 1.0)
                self.row = {"hA": const * self._cw_nom, "h": const * self._cw_nom,
                            "hB": const * self._cw_nom, "vA": const * self._ch_nom,
                            "vB": const * self._ch_nom}
                self.mode = "planar"
                self.info["name"] = str(getattr(sr, "name", "") or "")
                self.info["warning"] = (
                    "Projected {what}: its coordinate system could not be "
                    "unprojected ({err}), so cell sizes are taken as {cw:.2f} x "
                    "{ch:.2f} m everywhere (planar). If the projection has "
                    "significant scale distortion, gradients and distances "
                    "will be off by that amount.".format(
                        what=what, err=err, cw=self._cw_nom, ch=self._ch_nom))
        self.info["mode"] = self.mode

    # ---- lattice (projected rasters only) ----
    def _build_lattice(self, to_ecef):
        W, H = self.width, self.height
        step = max(GROUND_LATTICE_MIN_STEP,
                   int(math.ceil(max(W, H) / float(GROUND_LATTICE_MAX_NODES - 1))))
        lc = np.unique(np.concatenate([np.arange(0, W, step), [W - 1]])).astype(np.float64)
        lr = np.unique(np.concatenate([np.arange(0, H, step), [H - 1]])).astype(np.float64)
        cc, rr = np.meshgrid(lc, lr)                       # (nr, nc)
        full = _distances_at_cells(self.gt, cc.ravel(), rr.ravel(), to_ecef)
        if not all(np.all(np.isfinite(v)) and np.all(v > 0) for v in full.values()):
            raise ValueError("some lattice cells could not be unprojected")
        self._lc, self._lr = lc, lr
        self._lat_shape = cc.shape
        self._lat_full = {k: v.reshape(cc.shape) for k, v in full.items()}
        self._ci, self._wc = _interp_index(lc, np.arange(W, dtype=np.float64))
        self.info["lattice"] = (len(lr), len(lc))

    def _describe_scale(self, pg):
        full = self._lat_full
        kx = self._cw_nom / full["h"]
        ky = self._ch_nom / ((full["vA"] + full["vB"]) / 2.0)
        mid = self.height // 2
        kcx = self._cw_nom / float(self.row["h"][mid])
        kcy = self._ch_nom / float((self.row["vA"][mid] + self.row["vB"][mid]) / 2.0)
        kmin = float(min(kx.min(), ky.min()))
        kmax = float(max(kx.max(), ky.max()))
        off = max(abs(1.0 - 1.0 / kmin), abs(1.0 - 1.0 / kmax)) * 100.0
        self.info.update(scale_x=kcx, scale_y=kcy, scale_min=kmin,
                         scale_max=kmax, projection=pg.projection, name=pg.name)
        nr, nc = self.info["lattice"]
        self.info["note"] = (
            "Projected {what} ({name}; {proj} projection): true ground distances "
            "computed on the spheroid by unprojecting a lattice of {nr} x {nc} "
            "cell centers and interpolating between them. The projection's "
            "scale factor ranges from {kmin:.4f} to {kmax:.4f} across the raster "
            "({kcx:.4f} east-west and {kcy:.4f} north-south at the center), so "
            "planar cell sizes ({cw:.2f} x {ch:.2f} m) would put gradients and "
            "distances off by up to {off:.2f}%.".format(
                what=self.what, name=pg.name, proj=pg.projection, nr=nr, nc=nc,
                kmin=kmin, kmax=kmax, kcx=kcx, kcy=kcy, cw=self._cw_nom,
                ch=self._ch_nom, off=off))

    def _interp(self, table, r0, r1):
        """Bilinear interpolation of a lattice table to every cell of rows
        r0..r1-1: a (r1 - r0, width) array."""
        rows = np.arange(r0, r1, dtype=np.float64)
        ri, wr = _interp_index(self._lr, rows)
        if len(self._lr) == 1:
            rows_i = np.repeat(table[0][None, :], len(rows), axis=0)
        else:
            rows_i = table[ri] * (1.0 - wr)[:, None] + table[ri + 1] * wr[:, None]
        if len(self._lc) == 1:
            return np.repeat(rows_i[:, :1], self.width, axis=1)
        return (rows_i[:, self._ci] * (1.0 - self._wc)[None, :] +
                rows_i[:, self._ci + 1] * self._wc[None, :])

    # ---- per-cell tables ----
    def rows(self, r0, r1):
        """hA/h/hB/vA/vB for every cell of rows r0..r1-1 (2-D arrays)."""
        r0 = max(int(r0), 0)
        r1 = min(int(r1), self.height)
        n = max(r1 - r0, 0)
        if self.mode != "geodesic":
            return {k: np.broadcast_to(self.row[k][r0:r1, None], (n, self.width))
                    for k in _GROUND_KEYS}
        return {k: self._interp(self._lat_full[k], r0, r1) for k in _GROUND_KEYS}

    @property
    def half_row(self):
        """Per-row 8-triangle table (see ``half_rows``)."""
        if self._half_row is None:
            if self.mode == "planar":
                const = np.full(self.height, 1.0)
                hx2 = (self._cw_nom / 2.0) ** 2
                hy2 = (self._ch_nom / 2.0) ** 2
                d2 = hx2 + hy2
                self._half_row = {
                    "vA2": const * hy2, "hA2": const * hx2, "dA2": const * d2,
                    "h2": const * hx2, "dB2": const * d2, "hB2": const * hx2,
                    "vB2": const * hy2,
                    "pix_m2": const * (self._cw_nom * self._ch_nom)}
            else:
                col = 0.0 if self.geographic else (self.width - 1) / 2.0
                self._half_row = _half_distances_at_cells(
                    self.gt, np.full(self.height, col),
                    np.arange(self.height, dtype=np.float64), self._to_ecef)
        return self._half_row

    def half_rows(self, r0, r1):
        """The 8-triangle table for every cell of rows r0..r1-1: squared
        half-cell distances vA2/hA2/dA2/h2/dB2/hB2/vB2 and pix_m2 (2-D)."""
        r0 = max(int(r0), 0)
        r1 = min(int(r1), self.height)
        n = max(r1 - r0, 0)
        if self.mode != "geodesic":
            hr = self.half_row
            return {k: np.broadcast_to(hr[k][r0:r1, None], (n, self.width))
                    for k in _HALF_KEYS}
        if self._lat_half is None:
            cc, rr = np.meshgrid(self._lc, self._lr)
            half = _half_distances_at_cells(self.gt, cc.ravel(), rr.ravel(),
                                            self._to_ecef)
            self._lat_half = {k: v.reshape(cc.shape) for k, v in half.items()}
        return {k: self._interp(self._lat_half[k], r0, r1) for k in _HALF_KEYS}

    def at_cells(self, cols, rows):
        """The five spans at arbitrary cells (equal-length index arrays),
        computed exactly (no interpolation) -- for tools that need a few
        cells rather than whole rows."""
        cols = np.asarray(cols, dtype=np.float64)
        rows = np.asarray(rows, dtype=np.float64)
        if self.mode == "planar":
            const = np.ones(cols.shape)
            return {"hA": const * self._cw_nom, "h": const * self._cw_nom,
                    "hB": const * self._cw_nom, "vA": const * self._ch_nom,
                    "vB": const * self._ch_nom}
        return _distances_at_cells(self.gt, cols, rows, self._to_ecef)

    def cell_size_at(self, row):
        """(east-west, north-south) ground size in meters of the cells in
        row ``row`` (center column) -- a representative scalar pair for
        tools that need one cell size, e.g. a distance transform."""
        r = min(max(int(row), 0), self.height - 1)
        return (float(self.row["h"][r]),
                float((self.row["vA"][r] + self.row["vB"][r]) / 2.0))


def ground_geometry(gt, width, height, sr, proj_wkt=None, messages=None,
                    what="DEM"):
    """Build the ``GroundGeometry`` of a raster and post its note (and any
    warning) to ``messages``. ``sr`` is the arcpy spatial reference;
    ``proj_wkt`` the raster's projection WKT (GDAL's GetProjection; the
    arcpy string is tried when it is empty)."""
    gg = GroundGeometry(gt, width, height, sr, proj_wkt, what)
    if messages is not None:
        if gg.info["note"]:
            _msg(messages, gg.info["note"])
        if gg.info["warning"]:
            try:
                messages.addWarningMessage(gg.info["warning"])
            except Exception:
                _msg(messages, "WARNING: " + gg.info["warning"])
    return gg


def ground_note_for_metadata(gg):
    """One or two sentences for output-dataset provenance describing how
    cell distances were measured (see ``GroundGeometry``)."""
    info = gg.info if isinstance(gg, GroundGeometry) else dict(gg or {})
    mode = info.get("mode")
    if mode == "geographic":
        return ("Cell dimensions were computed per row on the spheroid "
                "(geodesic method) because the DEM is in a geographic "
                "coordinate system.")
    if mode == "geodesic":
        return ("Cell dimensions were computed on the spheroid by unprojecting "
                "a lattice of cell centers ({} projection; scale factor "
                "{:.4f} to {:.4f} across the raster), so the projection's "
                "scale distortion does not affect the result.".format(
                    info.get("projection", "?"), info.get("scale_min", 1.0),
                    info.get("scale_max", 1.0)))
    return ("Planar cell dimensions were used (projected DEM whose coordinate "
            "system could not be unprojected).")


# ----------------------------------------------------------------------------
#  Circular focal-mean blur (Blurred Hillshade)
# ----------------------------------------------------------------------------

def _blur_kernel(rx, ry, kernel):
    """2-D convolution kernel for an elliptical neighborhood of ``rx`` x
    ``ry`` cells: uniform weights ("Average", the lab's Focal Statistics
    circle) or Gaussian weights with sigma = radius/2, truncated at the
    radius. Weights need no normalization -- the caller divides by the
    convolved valid-mask (normalized convolution)."""
    rx = max(float(rx), 0.5)
    ry = max(float(ry), 0.5)
    nx, ny = int(math.ceil(rx)), int(math.ceil(ry))
    dx = np.arange(-nx, nx + 1, dtype=np.float64)[None, :] / rx
    dy = np.arange(-ny, ny + 1, dtype=np.float64)[:, None] / ry
    d2 = dx * dx + dy * dy
    inside = d2 <= 1.0 + 1e-12
    if kernel == "Gaussian":
        w = np.exp(-2.0 * d2) * inside          # sigma = r/2 -> exp(-d^2/(2*(r/2)^2))
    else:
        w = inside.astype(np.float64)
    return w


def _row_kernel_groups(rx_rows, ry, kernel):
    """Yields (kernel_array, row_selector) pairs partitioning a grid's rows
    by circular-neighborhood radius, for the row-varying-radius convolution
    used by the blur and VRM tools.

    When ``rx_rows`` is CONSTANT across all rows (a projected raster, or any
    raster with radius given in Cells) this yields exactly one group built
    from the EXACT requested radius -- no rounding. Rounding here would be a
    real bug, not just an approximation: ``np.rint`` rounds half-to-even, so
    a radius of 1.5 (the VRM default, chosen specifically to reproduce a
    literal 3x3 neighborhood) would silently become 2.0, quietly changing
    which cells are included. When ``rx_rows`` genuinely VARIES by row (a
    geographic DEM with a linear-unit radius, where cell size itself varies
    continuously by latitude), rows are grouped by whole-cell radius so the
    tool does not build one FFT kernel per row -- an already-documented
    approximation in that case, unlike the constant-radius case above."""
    rx_rows = np.asarray(rx_rows, dtype=np.float64)
    if float(np.ptp(rx_rows)) < 1e-9:
        yield _blur_kernel(rx_rows[0], ry, kernel), slice(None)
        return
    rx_int = np.maximum(np.rint(rx_rows), 1.0)
    for rxi in np.unique(rx_int):
        yield _blur_kernel(rxi, ry, kernel), (rx_int == rxi)


# Kernels up to this footprint (cells) use a direct spatial correlation; larger
# ones use overlap-add FFT. Both are memory-safe; the cutoff only trades speed
# (direct wins for small kernels, FFT for large).
_NBR_DIRECT_MAX_CELLS = 225


def nbr_conv(field, kernel):
    """Memory-safe 'same'-mode CORRELATION of ``field`` with a 2-D ``kernel`` of
    weights -- the shared neighborhood-convolution primitive for every focal
    tool in this toolbox (blur, VRM, TPI family, aspect focal statistics).

    A single whole-grid FFT (``scipy.signal.fftconvolve``) pads to a fast-FFT
    length and allocates several complex arrays many times the raster size, so
    it raises MemoryError on large regional rasters (and is slower than a direct
    convolution for a small kernel). Instead this is SIZE-AWARE:

    * small kernel (footprint <= ``_NBR_DIRECT_MAX_CELLS``): direct spatial
      correlation (``scipy.ndimage.correlate``) -- O(cells x kernel) time,
      O(cells) memory, fastest for small neighborhoods, and exact integer counts;
    * large kernel: overlap-add FFT (``scipy.signal.oaconvolve``), which
      processes the grid in blocks so memory stays bounded while keeping FFT
      speed for big neighborhoods (e.g. a 2 km TPI window).

    The kernels these tools build are symmetric (so convolution == correlation),
    but the FFT path flips the kernel so the result is a true correlation in
    every case; odd, centered kernels make both paths align identically with the
    old ``fftconvolve(..., mode='same')`` result."""
    kh, kw = kernel.shape
    if kh * kw <= _NBR_DIRECT_MAX_CELLS:
        from scipy.ndimage import correlate
        return correlate(field, kernel, mode="constant", cval=0.0)
    from scipy.signal import oaconvolve
    return oaconvolve(field, kernel[::-1, ::-1], mode="same")


def _blur_grid(z, valid, rx_rows, ry, kernel):
    """NoData-ignoring circular mean of ``z`` (full grid). ``valid`` is the
    data mask; invalid cells are excluded from every mean and stay invalid in
    the result (matching Focal Statistics with Ignore NoData). See
    ``_row_kernel_groups`` for how rows are grouped by radius."""
    num = np.where(valid, z, 0.0)
    den = valid.astype(np.float64)
    out = np.full(z.shape, np.nan)
    for k, rows in _row_kernel_groups(rx_rows, ry, kernel):
        cn = nbr_conv(num, k)
        cd = nbr_conv(den, k)
        with np.errstate(invalid="ignore", divide="ignore"):
            vals = cn / np.maximum(cd, 1e-12)
        out[rows] = vals[rows]
    out[~valid] = np.nan
    return out


# ----------------------------------------------------------------------------
#  Cast-shadow sweep
# ----------------------------------------------------------------------------

def _cast_shadow_mask(z_m, cellw_m, cellh_m, azimuth, altitude, sun_progress=(0, 1)):
    """Boolean mask of cells hidden from the sun (True = in shadow).

    ``z_m``: full elevation grid in meters (NaN = NoData; NaN cells neither
    cast nor receive shadows). Single-pass sweep marching away from the sun:
    the carried "shadow surface" drops by tan(altitude) x step distance each
    row and rises to any terrain above it. O(cells).

    ``sun_progress``: ``(i, n)`` -- this is sun ``i`` of ``n`` sharing the
    run's single "Sweeping cast shadows..." phase (multi-sun mode sweeps
    once per sun); used to interpolate this sweep's row position into that
    phase's overall share of the whole-run progress bar.
    """
    tan_alt = math.tan(math.radians(altitude))
    z = np.where(np.isnan(z_m), -np.inf, z_m).astype(np.float64)

    # Direction shadows are cast (away from the sun), as array steps.
    prop = math.radians((azimuth + 180.0) % 360.0)
    dcol = math.sin(prop)             # +east  = +columns
    drow = -math.cos(prop)            # +north = -rows

    flips = []                        # operations to undo on the mask
    if abs(dcol) > abs(drow):
        z = z.T
        drow, dcol = dcol, drow
        cellw_m, cellh_m = cellh_m, cellw_m
        flips.append("T")
    if drow < 0:
        z = z[::-1]
        drow, dcol = -drow, -dcol
        flips.append("ud")
    if dcol < 0:
        z = z[:, ::-1]
        dcol = -dcol
        flips.append("lr")

    # Per row-step: lateral shift in columns and ground distance in meters.
    shift = (dcol / drow) * (cellh_m / cellw_m)
    step_m = math.hypot(cellh_m, shift * cellw_m)
    drop = tan_alt * step_m
    k = int(math.floor(shift))
    f = shift - k

    nrows, ncols = z.shape
    mask = np.zeros((nrows, ncols), dtype=bool)
    carry = z[0].copy()               # first row cannot be shadowed
    shifted = np.empty(ncols, dtype=np.float64)
    tick = max(1, nrows // 100)
    sun_i, sun_n = sun_progress
    for r in range(1, nrows):
        # shifted[j] = carry[j - shift] via linear interpolation; off-grid = -inf.
        shifted.fill(-np.inf)
        if k + 1 < ncols:
            lo = carry[: ncols - (k + 1)]          # carry[j - k - 1]
            hi = carry[1: ncols - k]               # carry[j - k]
            shifted[k + 1:] = (1.0 - f) * hi + f * lo
        cur = shifted - drop
        row = z[r]
        mask[r] = row < (cur - 1e-6)
        np.maximum(row, cur, out=carry)
        if r % tick == 0:
            _mh.phase_step(sun_i * nrows + r, sun_n * nrows)

    for op in reversed(flips):
        if op == "T":
            mask = mask.T
        elif op == "ud":
            mask = mask[::-1]
        else:
            mask = mask[:, ::-1]
    # A NoData cell (-inf above) always tests as below the carried surface;
    # it is not a shadowed cell and must not be counted as one.
    return mask & ~np.isnan(z_m)


# ----------------------------------------------------------------------------
#  Main engine
# ----------------------------------------------------------------------------

_MDOW_OFFSETS = (-67.5, -22.5, 22.5, 67.5)


def run_enhanced_hillshade(in_raster, out_raster, elev_units="Meters",
                           azimuth=315.0, altitude=45.0, levels=256,
                           use_hypso=False, hypso_strength=50,
                           vert_exag=1.0, model_shadows=False,
                           shadow_dim=20.0, messages=None):
    """Compute the enhanced hillshade. See the module docstring.

    ``shadow_dim``: percent (0-100) of the unshadowed hillshade value that a
    shadowed cell keeps -- 0 renders shadows black, 100 disables the dimming.
    """
    return _run_hillshade("standard", in_raster, out_raster, elev_units, azimuth,
                          altitude, levels, use_hypso, hypso_strength, vert_exag,
                          model_shadows, shadow_dim, messages)


def run_mdow_hillshade(in_raster, out_raster, elev_units="Meters",
                       azimuth=270.0, altitude=30.0, levels=256,
                       use_hypso=False, hypso_strength=50,
                       vert_exag=1.0, messages=None):
    """Compute the Multi-Directional Oblique-Weighted (MDOW) hillshade of
    Mark (1992). See the module docstring. ``azimuth`` is the primary
    illumination direction; the four blended sun directions sit at
    -67.5/-22.5/+22.5/+67.5 degrees around it (legacy defaults 270/30/256).
    """
    return _run_hillshade("mdow", in_raster, out_raster, elev_units, azimuth,
                          altitude, levels, use_hypso, hypso_strength, vert_exag,
                          False, 20.0, messages)


def run_multisun_hillshade(in_raster, out_raster, elev_units="Meters",
                           suns=((280.0, 35.0, 50.0), (350.0, 45.0, 50.0)),
                           levels=256, use_hypso=False, hypso_strength=50,
                           vert_exag=1.0, model_shadows=False,
                           shadow_dim=20.0, messages=None):
    """Compute a weighted blend of standard hillshades from multiple sun
    positions. ``suns`` is a sequence of (azimuth, inclination, weight)
    triples; weights are relative (normalized by their sum, so percentages
    summing to 100 work naturally). Each sun's hillshade is clamped at 0 and,
    when ``model_shadows`` is on, its own cast-shadow cells are dimmed to
    ``shadow_dim`` percent BEFORE the weighted average; hypsometric shading
    is applied once to the averaged result.
    """
    return _run_hillshade("multi", in_raster, out_raster, elev_units, 315.0,
                          45.0, levels, use_hypso, hypso_strength, vert_exag,
                          model_shadows, shadow_dim, messages, suns=suns)


def run_blurred_hillshade(in_raster, out_raster, elev_units="Meters",
                          azimuth=315.0, altitude=45.0, levels=256,
                          use_hypso=False, hypso_strength=50,
                          vert_exag=1.0, model_shadows=False,
                          shadow_dim=20.0, blur_radius=10.0,
                          radius_units="Cells", blur_target=None,
                          kernel="Average", messages=None):
    """Blurred (smoothed) hillshade -- the softening component of the Swiss
    method. Same options as the Enhanced Hillshade, plus a circular blur of
    the given radius: either the DEM is blurred before shading (default; the
    lab's Focal Statistics recipe, which generalizes the terrain itself) or
    the finished hillshade image is blurred after shading. Linear radius
    units are converted to cells geodesically (per row) on geographic
    rasters and planarly on projected rasters.
    """
    gdal = _mh._gdal()
    blur_radius = float(blur_radius)
    if blur_radius <= 0:
        raise arcpy.ExecuteError("The blur radius must be positive.")
    if radius_units not in RADIUS_UNITS:
        raise arcpy.ExecuteError("Unknown radius unit: {}.".format(radius_units))
    if kernel not in BLUR_KERNELS:
        raise arcpy.ExecuteError("Unknown kernel weighting: {}.".format(kernel))
    target = blur_target or BLUR_TARGETS[0]
    if target not in BLUR_TARGETS:
        raise arcpy.ExecuteError("Unknown blur target: {}.".format(target))
    dem_first = (target == BLUR_TARGETS[0])
    out_raster = _mh.resolve_raster_output(out_raster, messages)

    if dem_first:
        phases = [("Reading the DEM for blurring...", 1),
                 ("Blurring the DEM...", 3)]
        phases += _hillshade_phase_segment("standard", None, use_hypso, model_shadows)
    else:
        phases = _hillshade_phase_segment("standard", None, use_hypso, model_shadows)
        phases.append(("Blurring the hillshade...", 3))
    phases += _FINALIZE_PHASES
    _mh.begin_phases(phases)

    scratch = arcpy.env.scratchFolder
    src, in_copy = _open_source(in_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    in_nodata = band.GetNoDataValue()
    proj = src.GetProjection()
    sr = arcpy.Describe(in_raster).spatialReference
    geographic = (sr.type == "Geographic")

    # Blur radius in cells, per row (x) and overall (y).
    if radius_units == "Cells":
        rx_rows = np.full(height, blur_radius)
        ry = blur_radius
        _msg(messages, "Blur radius: {:g} cells ({} kernel).".format(
            blur_radius, kernel))
    else:
        radius_m = blur_radius * _RADIUS_METERS[radius_units]
        # true ground cell sizes per row for ANY coordinate system (the
        # hillshade engine below posts the ground-geometry note itself)
        dist = ground_geometry(gt, width, height, sr, proj).row
        rx_rows = radius_m / np.maximum(dist["h"], 1e-9)
        ry = radius_m / max(float(np.mean(dist["vA"])), 1e-9)
        _msg(messages, "Blur radius: {:g} {} = {:,.1f} m, sized per row "
                       "on the spheroid ({:.1f}-{:.1f} cells across, "
                       "{:.1f} cells down; {} kernel).".format(
                           blur_radius, radius_units.lower(), radius_m,
                           float(rx_rows.min()), float(rx_rows.max()),
                           ry, kernel))
    if float(np.max(rx_rows)) < 1.0 and ry < 1.0:
        if messages is not None:
            messages.addWarningMessage(
                "The blur radius is smaller than one cell ({:.2f} x {:.2f} "
                "cells), so no neighboring cells fall inside it and nothing is "
                "blurred: the output is the same as an ordinary hillshade. Use "
                "a radius of at least one cell.".format(
                    float(np.max(rx_rows)), ry))
    note = ", blur radius {:g} {} ({} kernel, {})".format(
        blur_radius, radius_units.lower(), kernel,
        "DEM blurred before shading" if dem_first
        else "hillshade blurred after shading")

    if dem_first:
        # Blur the terrain, then run the standard pipeline on the result.
        _set_label("Reading the DEM for blurring...")
        z = band.ReadAsArray(0, 0, width, height).astype(np.float64)
        valid = np.isfinite(z)
        if in_nodata is not None:
            valid &= (z != in_nodata)
        valid = _mh.mask_band_valid(band, valid)
        _mh.phase_advance("Blurring the DEM...")
        _msg(messages, "Blurring the DEM (circular mean of the elevations)...")
        zb = _blur_grid(z, valid, rx_rows, ry, kernel)
        del z
        BLUR_ND = -3.0e38
        tmp_dem = arcpy.CreateUniqueName("blurred_dem.tif", scratch)
        drv = gdal.GetDriverByName("GTiff")
        tds = drv.Create(tmp_dem, width, height, 1, gdal.GDT_Float32,
                         options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
        tds.SetGeoTransform(gt)
        try:
            tds.SetProjection(proj)
        except Exception:
            pass
        tb = tds.GetRasterBand(1)
        tb.SetNoDataValue(BLUR_ND)
        tb.WriteArray(np.where(valid, zb, BLUR_ND).astype(np.float32))
        tb.FlushCache()
        tds = None
        del zb
        src = None
        try:
            out_lyrx = _run_hillshade(
                "standard", tmp_dem, out_raster, elev_units,
                azimuth, altitude, levels, use_hypso,
                hypso_strength, vert_exag, model_shadows,
                shadow_dim, messages,
                tool_label="Blurred Hillshade",
                src_display=str(in_raster), extra_note=note,
                own_phases=False, first_phase=False)
        finally:
            try:
                arcpy.management.Delete(tmp_dem)
            except Exception:
                pass
            if in_copy:
                try:
                    arcpy.management.Delete(in_copy)
                except Exception:
                    pass
        return out_lyrx

    # Blur-after path: standard hillshade to a scratch GeoTIFF, then blur the
    # finished image and finalize.
    src = None
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass
    hs_tmp = _run_hillshade("standard", in_raster, None, elev_units,
                            azimuth, altitude, levels, use_hypso,
                            hypso_strength, vert_exag, model_shadows,
                            shadow_dim, messages, _finalize=False,
                            own_phases=False)
    hds = gdal.Open(hs_tmp)
    arr = hds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    hgt, hproj = hds.GetGeoTransform(), hds.GetProjection()
    hds = None
    hvalid = arr != NODATA_OUT
    _mh.phase_advance("Blurring the hillshade...")
    _msg(messages, "Blurring the finished hillshade (circular mean of the "
                   "hillshade values)...")
    hb = _blur_grid(arr, hvalid, rx_rows, ry, kernel)
    vals = np.rint(np.where(hvalid, hb, NODATA_OUT))
    out_arr = np.where(hvalid, vals, NODATA_OUT).astype(np.int16)

    out_path = arcpy.CreateUniqueName("hillshade_blurred.tif", scratch)
    drv = gdal.GetDriverByName("GTiff")
    ods = drv.Create(out_path, width, height, 1, gdal.GDT_Int16,
                     options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    ods.SetGeoTransform(hgt)
    try:
        ods.SetProjection(hproj)
    except Exception:
        pass
    ob = ods.GetRasterBand(1)
    ob.SetNoDataValue(NODATA_OUT)
    ob.WriteArray(out_arr)
    ob.FlushCache()
    stats = _mh._RasterStats()
    if hvalid.any():
        stats.add(vals[hvalid])
    out_stats = _mh._finalize_stats(ob, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(ob, out_stats)
    ob.FlushCache()
    ods.FlushCache()
    ods = None

    _mh.phase_advance("Saving output raster...")
    prev_pyramid = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        arcpy.management.CopyRaster(out_path, out_raster)
    finally:
        arcpy.env.pyramid = prev_pyramid
    _mh.phase_advance("Setting raster statistics...")
    if out_stats and out_stats.get("on_raster"):
        _msg(messages, "Statistics and histogram were written with the raster "
                       "(no scan needed).")
    elif not _mh._apply_known_statistics(out_raster, out_stats, messages):
        try:
            arcpy.management.CalculateStatistics(out_raster)
        except Exception as e:
            if messages is not None:
                messages.addWarningMessage(
                    "Could not calculate raster statistics: {}".format(e))
    level_values = _build_level_table(out_raster, levels, messages)
    try:
        md = arcpy.metadata.Metadata(out_raster)
        md.title = os.path.basename(str(out_raster))
        md.tags = ("blurred hillshade, smoothed hillshade, Swiss method, "
                   "shaded relief, cartography, Jenness Enterprises")
        md.summary = (
            "Created by Blurred Hillshade from {src}: azimuth {az:g} deg, "
            "inclination {alt:g} deg, {lev} levels, vertical exaggeration "
            "{ex:g}, elevation units {units}{hyp}{sh}{note}. {geo}").format(
                src=str(in_raster), az=azimuth, alt=altitude, lev=levels,
                ex=vert_exag,
                units=("Meters (geographic DEM)" if geographic else elev_units),
                hyp=(", hypsometric shading {:.0f}%".format(float(hypso_strength))
                     if use_hypso else ""),
                sh=(", cast shadows dimmed to {:g}%".format(float(shadow_dim))
                    if model_shadows else ""),
                note=note,
                geo=("Cell dimensions were computed per row on the spheroid "
                     "(geodesic method) because the DEM is in a geographic "
                     "coordinate system." if geographic else
                     "Planar cell dimensions were used (projected DEM)."))
        md.description = (
            "Blurred (smoothed) hillshade raster: a shaded-relief image "
            "deliberately softened so small terrain features are "
            "de-emphasized -- the blurring component of the classic Swiss "
            "style of relief presentation. Gradients on geographic DEMs are "
            "computed from true spheroidal cell dimensions at each row's "
            "latitude.")
        md.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                      "(http://www.jennessent.com). Gradient method: " +
                      _HORN_CITATION)
        md.save()
    except Exception:
        pass
    # Black (most shaded) to white (highest hillshade value in this
    # raster) output-parameter symbology.
    _mh.phase_advance("Creating layer file and symbology...")
    out_lyrx = _hillshade_lyrx(out_raster, level_values, messages)
    for p in (out_path, hs_tmp):
        try:
            arcpy.management.Delete(p)
        except Exception:
            pass
    _mh.phase_advance("Building pyramids (bilinear)...")
    try:
        arcpy.management.BuildPyramids(out_raster, resample_technique="BILINEAR")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not build pyramids: {}".format(e))
    _mh.end_phases()
    _msg(messages, "Blurred Hillshade complete: {}".format(out_raster))
    return out_lyrx


def _run_hillshade(mode, in_raster, out_raster, elev_units, azimuth, altitude,
                   levels, use_hypso, hypso_strength, vert_exag,
                   model_shadows, shadow_dim, messages, suns=None,
                   _finalize=True, tool_label=None, src_display=None,
                   extra_note="", own_phases=True, first_phase=True):
    """Shared engine: ``mode`` is "standard" (one sun direction, optional cast
    shadows), "mdow" (four sine-squared-weighted directions, no shadows), or
    "multi" (a weighted blend of standard hillshades from ``suns``, optional
    per-sun cast shadows).

    ``_finalize=False`` stops after the scratch GeoTIFF is written (no
    CopyRaster/pyramids/metadata) and returns its path -- used by the Blurred
    Hillshade's blur-after-shading path. ``tool_label``/``src_display``/
    ``extra_note`` override the tool name, input name, and add provenance
    text in messages and metadata (used when the input is a preprocessed
    scratch dataset standing in for the user's raster). ``own_phases=False``
    skips declaring the whole-run progress phases (the Blurred Hillshade
    wrapper already declared a bigger list of its own, covering the blur
    step plus this same shading segment -- see ``_hillshade_phase_segment``).
    ``first_phase=False`` means this call's own shading phases are NOT the
    first in the whole-run phase list (the blur-before-shading path already
    consumed 2 phases ahead of them), so every phase transition below must
    advance rather than assume it's already sitting at the active phase 0."""
    gdal = _mh._gdal()

    if _finalize:
        out_raster = _mh.resolve_raster_output(out_raster, messages)

    azimuth = float(azimuth) % 360.0
    altitude = float(altitude)
    levels = int(levels)
    vert_exag = float(vert_exag)
    hypso_strength = float(hypso_strength)
    shadow_dim = float(shadow_dim)
    if not 0.0 <= altitude <= 90.0:
        raise arcpy.ExecuteError("The inclination to the sun must be 0 to 90 degrees.")
    if mode == "multi":
        if not suns:
            raise arcpy.ExecuteError("At least one sun position is required.")
        suns = [(float(a) % 360.0, float(al), float(w)) for a, al, w in suns]
        for a, al, w in suns:
            if not 0.0 <= al <= 90.0:
                raise arcpy.ExecuteError(
                    "Each inclination to the sun must be 0 to 90 degrees.")
            if w < 0.0:
                raise arcpy.ExecuteError("Sun weights cannot be negative.")
        wsum = sum(w for _, _, w in suns)
        if wsum <= 0.0:
            raise arcpy.ExecuteError("The sun weights must sum to more than 0.")
    if levels < 2:
        raise arcpy.ExecuteError("The number of hillshade gray levels must be at least 2.")
    if levels > 32768:
        raise arcpy.ExecuteError("The number of hillshade gray levels can be at most "
                                 "32768 (the output is a 16-bit integer raster).")
    if vert_exag <= 0:
        raise arcpy.ExecuteError("The vertical exaggeration must be positive.")
    if use_hypso and not 0.0 <= hypso_strength <= 100.0:
        raise arcpy.ExecuteError("The hypsometric strength must be 0 to 100.")
    if model_shadows and not 0.0 <= shadow_dim <= 100.0:
        raise arcpy.ExecuteError("The shadow brightness must be 0 to 100 percent.")

    if own_phases:
        phases = _hillshade_phase_segment(mode, suns, use_hypso, model_shadows)
        if _finalize:
            phases += _FINALIZE_PHASES
        _mh.begin_phases(phases)

    scratch = arcpy.env.scratchFolder
    src, in_copy = _open_source(in_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    in_nodata = band.GetNoDataValue()

    # True-ground cell dimensions for ANY coordinate system (a projected DEM
    # is unprojected on a lattice of cell centers -- see GroundGeometry).
    sr = arcpy.Describe(in_raster).spatialReference
    geographic = (sr.type == "Geographic")
    ground = ground_geometry(gt, width, height, sr, src.GetProjection(),
                             messages)
    cellw_m = float(np.mean(ground.row["h"]))
    cellh_m = float(np.mean(ground.row["vA"]))
    if geographic:
        elev_mpu = 1.0                      # legacy: geographic DEMs are meters
    else:
        elev_mpu = _ELEV_METERS.get(elev_units, 1.0)
        _msg(messages, "Elevation units = {}.".format(elev_units))
    zscale = elev_mpu * vert_exag

    # Sun geometry (legacy conventions).
    zenith = math.radians(90.0 - altitude)
    az_math = (360.0 - azimuth + 90.0) % 360.0
    az_rad = math.radians(az_math)
    cosz, sinz = math.cos(zenith), math.sin(zenith)
    if mode == "mdow":
        mdow_azimuths = [(azimuth + off) % 360.0 for off in _MDOW_OFFSETS]
        dir_rads = [math.radians((360.0 - a + 90.0) % 360.0)
                    for a in mdow_azimuths]
    elif mode == "multi":
        # Per-sun geometry and normalized weights (a true weighted average).
        sun_geom = []
        for a, al, w in suns:
            zen_i = math.radians(90.0 - al)
            ar_i = math.radians((360.0 - a + 90.0) % 360.0)
            sun_geom.append((math.cos(zen_i), math.sin(zen_i), ar_i, w / wsum))

    # Hypsometric weight: multiplier = t*s + (1 - s), t = normalized elevation.
    hyp_lo = hyp_range = None
    if use_hypso:
        if first_phase:
            _set_label("Scanning elevation statistics for hypsometric shading...")
        else:
            _mh.phase_advance("Scanning elevation statistics for hypsometric shading...")
        # ComputeRasterMinMax scans like GetStatistics(0, 1) but does not
        # save the result, so no .aux.xml appears beside the user's DEM.
        try:
            s_min, s_max = band.ComputeRasterMinMax(0)
        except RuntimeError:
            s_min = s_max = None
        if (s_min is None or s_max is None or
                not (math.isfinite(s_min) and math.isfinite(s_max))):
            raise arcpy.ExecuteError(
                "The DEM has no valid elevation values, so hypsometric shading "
                "cannot be applied.")
        hyp_lo = s_min * zscale
        hyp_range = max((s_max - s_min) * zscale, 1e-30)
        _msg(messages, "Hypsometric shading: strength {:.0f}% over elevation "
                       "range {:.6g} to {:.6g}.".format(hypso_strength, s_min, s_max))
    s_frac = hypso_strength / 100.0

    # Optional cast shadows need the whole grid at once (shadows are non-local).
    shadow_mask = None                  # standard mode: one mask
    shadow_masks = None                 # multi mode: one mask per sun
    if model_shadows:
        if first_phase and not use_hypso:
            _set_label("Reading the DEM for the cast-shadow sweep...")
        else:
            _mh.phase_advance("Reading the DEM for the cast-shadow sweep...")
        z_full = band.ReadAsArray(0, 0, width, height).astype(np.float32)
        z_full = z_full.astype(np.float64)
        invalid = ~np.isfinite(z_full)
        if in_nodata is not None:
            invalid |= (z_full == in_nodata)
        vm = _mh.band_validity_mask(band)
        if vm is not None:
            invalid |= ~vm
        z_full[invalid] = np.nan
        z_full *= zscale
        n_valid = max(int(invalid.size - invalid.sum()), 1)
        geo_note = (" (mean-cell-size approximation on this geographic DEM)"
                    if geographic else "")
        if mode == "multi":
            _mh.phase_advance("Sweeping cast shadows...")
            shadow_masks = []
            for i, (a, al, _w) in enumerate(suns):
                _set_label("Sweeping cast shadows (sun {} of {})...".format(
                    i + 1, len(suns)))
                m = _cast_shadow_mask(z_full, cellw_m, cellh_m, a, al,
                                      sun_progress=(i, len(suns)))
                shadow_masks.append(m)
                n_sh = int(m.sum())
                _msg(messages, "Cast shadows, sun {} ({:g} deg / {:g} deg): "
                               "{:,} cells ({:.1f}%) dimmed to {:g}% before "
                               "averaging{}.".format(
                                   i + 1, a, al, n_sh,
                                   100.0 * n_sh / n_valid, shadow_dim,
                                   geo_note))
        else:
            _mh.phase_advance("Sweeping cast shadows...")
            shadow_mask = _cast_shadow_mask(z_full, cellw_m, cellh_m, azimuth, altitude)
            n_sh = int(shadow_mask.sum())
            _msg(messages, "Cast shadows: {:,} cells ({:.1f}%) are hidden from the "
                           "sun and will be dimmed to {:g}% of their unshadowed "
                           "value{}.".format(
                               n_sh, 100.0 * n_sh / n_valid, shadow_dim,
                               geo_note))
        del z_full

    # Output scratch GeoTIFF (Int16; NoData -9999), stats accumulated as we go.
    out_path = arcpy.CreateUniqueName("hillshade.tif", scratch)
    drv = gdal.GetDriverByName("GTiff")
    out_ds = drv.Create(out_path, width, height, 1, gdal.GDT_Int16,
                        options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    out_ds.SetGeoTransform(gt)
    try:
        out_ds.SetProjection(src.GetProjection())
    except Exception:
        pass
    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(NODATA_OUT)

    _msg(messages, "Gradient method: {}".format(_HORN_CITATION))
    if mode == "mdow":
        _msg(messages, "Computing MDOW hillshade: sun directions {} deg "
                       "(sine-squared weighted; primary {:.1f}), inclination "
                       "{:.1f} deg, {} levels, exaggeration {:g}{}.".format(
                           "/".join("{:g}".format(a) for a in mdow_azimuths),
                           azimuth, altitude, levels, vert_exag,
                           ", geodesic" if geographic else ""))
    elif mode == "multi":
        _msg(messages, "Computing multi-sun hillshade from {} sun positions: "
                       "{}; {} levels, exaggeration {:g}{}.".format(
                           len(suns),
                           "; ".join(
                               "az {:g} deg / incl {:g} deg / weight {:.1f}%".format(
                                   a, al, 100.0 * w / wsum)
                               for a, al, w in suns),
                           levels, vert_exag,
                           ", geodesic" if geographic else ""))
    else:
        _msg(messages, "Computing hillshade: azimuth {:.1f} deg, inclination {:.1f} deg, "
                       "{} levels, exaggeration {:g}{}.".format(
                           azimuth, altitude, levels, vert_exag,
                           ", geodesic" if geographic else ""))
    if first_phase and not use_hypso and not model_shadows:
        _set_label("Calculating hillshade...")
    else:
        _mh.phase_advance("Calculating hillshade...")
    stats = _mh._RasterStats()
    strips = [(y, min(_STRIP_ROWS, height - y)) for y in range(0, height, _STRIP_ROWS)]

    for idx, (yoff, rows) in enumerate(strips):
        # Read the strip plus a 1-row halo on each side (clamped at the edges).
        top = max(yoff - 1, 0)
        bot = min(yoff + rows + 1, height)
        pad = band.ReadAsArray(0, top, width, bot - top).astype(np.float64)
        valid = np.isfinite(pad)
        if in_nodata is not None:
            valid &= (pad != in_nodata)
        valid = _mh.mask_band_valid(band, valid, 0, top)
        pad = np.where(valid, pad * zscale, 0.0)

        out = np.full((rows, width), NODATA_OUT, dtype=np.int16)
        # Rows of this strip that have both neighbors inside the raster.
        r0 = yoff if yoff > 0 else 1
        r1 = (yoff + rows) if (yoff + rows) < height else height - 1
        if r1 > r0 or (r1 == r0 and r0 < height - 1):
            sl = slice(r0 - top, r1 - top)       # center rows within `pad`
            up = slice(r0 - top - 1, r1 - top - 1)
            dn = slice(r0 - top + 1, r1 - top + 1)

            NW, N_, NE = pad[up, :-2], pad[up, 1:-1], pad[up, 2:]
            W_, C_, E_ = pad[sl, :-2], pad[sl, 1:-1], pad[sl, 2:]
            SW, S_, SE = pad[dn, :-2], pad[dn, 1:-1], pad[dn, 2:]

            rr = slice(r0, r1)                   # absolute row range
            g = ground.rows(r0, r1)              # per-cell ground spans
            # (the elevation slices above cover the interior columns only)
            hA, h, hB = g["hA"][:, 1:-1], g["h"][:, 1:-1], g["hB"][:, 1:-1]
            vA, vB = g["vA"][:, 1:-1], g["vB"][:, 1:-1]

            dzdx = ((NE + 2.0 * E_ + SE) - (NW + 2.0 * W_ + SW)) / \
                   (2.0 * hA + 4.0 * h + 2.0 * hB)
            dzdy = ((SW + 2.0 * S_ + SE) - (NW + 2.0 * N_ + NE)) / (4.0 * vA + 4.0 * vB)

            slope = np.arctan(np.hypot(dzdx, dzdy))
            aspect = np.arctan2(dzdy, -dzdx)
            aspect[aspect < 0] += 2.0 * math.pi

            if mode == "mdow":
                # Four hillshades blended with sin^2(aspect - direction)
                # weights; the weights of 45-degree-spaced directions always
                # sum to 2, so /2 is exact (Mark 1992). Each sun's illumination
                # is clamped at 0 BEFORE it is weighted, as in Mark's method
                # and every other hillshade mode here: ground that faces away
                # from a sun gets nothing from that sun. (Jeff, 2026-10-03.
                # The VB6 tool, and this port until v1.99.43, blended first
                # and clamped once at the end, so a slope steeper than the sun
                # inclination and turned away from one sun was DARKENED by
                # that sun's negative value -- a miscalculation.)
                cs = cosz * np.cos(slope)
                ss = sinz * np.sin(slope)
                shade = np.zeros_like(slope)
                for dr in dir_rads:
                    sh = cs + ss * np.cos(dr - aspect)
                    np.maximum(sh, 0.0, out=sh)
                    shade += np.sin(aspect - dr) ** 2 * sh
                shade *= 0.5
            elif mode == "multi":
                # Weighted average of standard hillshades: each sun's shade is
                # clamped at 0 and its own cast shadows dimmed BEFORE the
                # average (hypsometric shading applies once, after).
                cslope = np.cos(slope)
                sslope = np.sin(slope)
                shade = np.zeros_like(slope)
                for i, (cz_i, sz_i, ar_i, w_i) in enumerate(sun_geom):
                    sh = cz_i * cslope + sz_i * sslope * np.cos(ar_i - aspect)
                    np.maximum(sh, 0.0, out=sh)
                    if shadow_masks is not None:
                        sh[shadow_masks[i][rr, 1:-1]] *= shadow_dim / 100.0
                    shade += w_i * sh
            else:
                shade = cosz * np.cos(slope) + sinz * np.sin(slope) * np.cos(az_rad - aspect)
            hs = levels * shade
            if use_hypso:
                hs *= ((C_ - hyp_lo) / hyp_range) * s_frac + (1.0 - s_frac)
            np.maximum(hs, 0.0, out=hs)
            if shadow_mask is not None:
                # Shadowed cells keep shadow_dim percent of their unshadowed value.
                hs[shadow_mask[rr, 1:-1]] *= shadow_dim / 100.0

            ok = (valid[up, :-2] & valid[up, 1:-1] & valid[up, 2:] &
                  valid[sl, :-2] & valid[sl, 1:-1] & valid[sl, 2:] &
                  valid[dn, :-2] & valid[dn, 1:-1] & valid[dn, 2:])
            # N gray levels = values 0..N-1, each an equal share of the
            # 0-1 illumination range (full light lands in the top class).
            vals = np.minimum(np.floor(hs), levels - 1)
            center = out[(r0 - yoff):(r1 - yoff), 1:-1]
            center[ok] = vals[ok].astype(np.int16)
            if ok.any():
                stats.add(vals[ok])
        out_band.WriteArray(out, 0, yoff)
        _mh.phase_step(idx + 1, len(strips))

    out_band.FlushCache()
    out_ds.FlushCache()
    out_stats = _mh._finalize_stats(out_band, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(out_band, out_stats)
    out_band.FlushCache()
    out_ds.FlushCache()
    src = None
    out_ds = None                        # closing writes the PAM (.aux.xml)

    if not _finalize:
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass
        return out_path

    _mh.phase_advance("Saving output raster...")
    prev_pyramid = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        arcpy.management.CopyRaster(out_path, out_raster)
    finally:
        arcpy.env.pyramid = prev_pyramid
    _mh.phase_advance("Setting raster statistics...")
    if out_stats and out_stats.get("on_raster"):
        _msg(messages, "Statistics and histogram were written with the raster "
                       "(no scan needed).")
    elif not _mh._apply_known_statistics(out_raster, out_stats, messages):
        try:
            arcpy.management.CalculateStatistics(out_raster)
        except Exception as e:
            if messages is not None:
                messages.addWarningMessage(
                    "Could not calculate raster statistics: {}".format(e))
    level_values = _build_level_table(out_raster, levels, messages)

    # Dataset metadata: conceptual description + per-run provenance.
    try:
        md = arcpy.metadata.Metadata(out_raster)
        md.title = os.path.basename(str(out_raster))
        geo_note = ground_note_for_metadata(ground)
        hyp_note = (", hypsometric shading {:.0f}%".format(hypso_strength)
                    if use_hypso else "")
        units_note = "Meters (geographic DEM)" if geographic else elev_units
        if mode == "multi":
            md.tags = ("multi-sun hillshade, multidirectional hillshade, "
                       "shaded relief, cartography, Jenness Enterprises")
            md.summary = (
                "Created by Multi-Sun Hillshade from {src}: {n} sun positions "
                "({dirs}), {lev} levels, vertical exaggeration {ex:g}, "
                "elevation units {units}{hyp}{sh}. {geo}").format(
                    src=str(in_raster), n=len(suns),
                    dirs="; ".join(
                        "az {:g}/incl {:g}/wt {:.1f}%".format(
                            a, al, 100.0 * w / wsum) for a, al, w in suns),
                    lev=levels, ex=vert_exag, units=units_note, hyp=hyp_note,
                    sh=(", per-sun cast shadows dimmed to {:g}% before "
                        "averaging".format(shadow_dim) if model_shadows else ""),
                    geo=geo_note)
            md.description = (
                "Multi-sun hillshade raster: a weighted average of standard "
                "hillshades computed from multiple sun positions, each with "
                "its own azimuth, inclination, and weight. Blending several "
                "light sources brings out terrain features that a single "
                "sun washes out (the classic Swiss multiple-light-source "
                "relief-shading strategy). Gradients on geographic DEMs are "
                "computed from true spheroidal cell dimensions at each row's "
                "latitude.")
        elif mode == "mdow":
            md.tags = ("MDOW hillshade, multidirectional hillshade, shaded "
                       "relief, cartography, Jenness Enterprises")
            md.summary = (
                "Created by MDOW Hillshade from {src}: sun directions {dirs} "
                "deg (primary {az:g}), inclination {alt:g} deg, {lev} levels, "
                "vertical exaggeration {ex:g}, elevation units {units}{hyp}. "
                "{geo}").format(
                    src=str(in_raster),
                    dirs="/".join("{:g}".format(a) for a in mdow_azimuths),
                    az=azimuth, alt=altitude, lev=levels, ex=vert_exag,
                    units=units_note, hyp=hyp_note, geo=geo_note)
            md.description = (
                "Multi-Directional Oblique-Weighted (MDOW) hillshade raster "
                "(Mark 1992, USGS Open-File Report 92-422): four hillshades "
                "with sun directions fanned around the primary illumination "
                "direction, blended per cell with sine-squared aspect weights "
                "so each slope is lit most strongly from the directions "
                "oblique to it. Brings out terrain features that a "
                "single-direction hillshade washes out. Gradients on "
                "geographic DEMs are computed from true spheroidal cell "
                "dimensions at each row's latitude.")
        else:
            label = tool_label or "Enhanced Hillshade"
            if label == "Blurred Hillshade":
                md.tags = ("blurred hillshade, smoothed hillshade, Swiss "
                           "method, shaded relief, cartography, "
                           "Jenness Enterprises")
            else:
                md.tags = "hillshade, shaded relief, cartography, Jenness Enterprises"
            md.summary = (
                "Created by {tool} from {src}: azimuth {az:g} deg, "
                "inclination {alt:g} deg, {lev} levels, vertical exaggeration "
                "{ex:g}, elevation units {units}{hyp}{sh}{note}. {geo}").format(
                    tool=label, src=src_display or str(in_raster),
                    az=azimuth, alt=altitude, lev=levels,
                    ex=vert_exag, units=units_note, hyp=hyp_note,
                    sh=(", cast shadows dimmed to {:g}%".format(shadow_dim)
                        if model_shadows else ""),
                    note=extra_note, geo=geo_note)
            if label == "Blurred Hillshade":
                md.description = (
                    "Blurred (smoothed) hillshade raster: a shaded-relief "
                    "image deliberately softened so small terrain features "
                    "are de-emphasized -- the blurring component of the "
                    "classic Swiss style of relief presentation. Gradients "
                    "on geographic DEMs are computed from true spheroidal "
                    "cell dimensions at each row's latitude.")
            else:
                md.description = (
                    "Hillshade (shaded relief) raster: illumination of the terrain "
                    "surface from a hypothetical light source, from 0 (fully shaded) "
                    "to one less than the requested number of gray levels (fully "
                    "lit). Unlike the "
                    "standard hillshade, gradients on geographic DEMs are computed "
                    "from true spheroidal cell dimensions at each row's latitude.")
        md.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                      "(http://www.jennessent.com). Gradient method: " +
                      _HORN_CITATION +
                      (" Multi-directional method: Mark, R. K. 1992. A "
                       "multidirectional, oblique-weighted, shaded-relief "
                       "image of the Island of Hawaii. USGS Open-File "
                       "Report 92-422." if mode == "mdow" else ""))
        md.save()
    except Exception:
        pass
    # Black (most shaded) to white (highest hillshade value in this
    # raster) output-parameter symbology.
    _mh.phase_advance("Creating layer file and symbology...")
    out_lyrx = _hillshade_lyrx(out_raster, level_values, messages)

    try:
        arcpy.management.Delete(out_path)
    except Exception:
        pass
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass
    _mh.phase_advance("Building pyramids (bilinear)...")
    try:
        arcpy.management.BuildPyramids(out_raster, resample_technique="BILINEAR")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not build pyramids: {}".format(e))
    _mh.end_phases()
    _msg(messages, "{} complete: {}".format(
        tool_label or {"mdow": "MDOW hillshade",
                       "multi": "Multi-sun hillshade"}.get(
            mode, "Enhanced hillshade"), out_raster))
    return out_lyrx
