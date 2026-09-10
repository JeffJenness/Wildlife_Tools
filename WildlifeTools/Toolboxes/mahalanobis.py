# -*- coding: utf-8 -*-
"""Mahalanobis distance rasters.

Port of the legacy ArcMap VB6 ``MahalanobisRaster`` routine (Mahal_Functions.bas,
"Land Facet Corridor Tools") to arcpy + NumPy + GDAL.

Mahalanobis distance (Mahalanobis 1936) measures, for every raster cell, how far
that cell's vector of landscape-variable values lies from the multivariate mean of
a set of reference samples, scaled by the covariance among the variables:

    D2 = (x - mu)^T * Sigma^-1 * (x - mu)

where ``x`` is the cell's vector of k variable values, ``mu`` the k-length mean
vector, and ``Sigma^-1`` the inverse of the k x k covariance matrix.

Key differences from the legacy tool (by design, see the session notes):
  * The legacy tool computed D2 by chaining ~2k+3 full-raster Map Algebra /
    Raster Calculator passes. This version computes D2 *cell-by-cell, block
    vectorized* over a GDAL block window, so memory stays bounded no matter how
    large the rasters or how many variables are used.
  * Covariance inversion uses ``numpy.linalg`` (inv, or pinv on request) instead
    of the legacy hand-rolled LU decomposition.
  * Projection / cell-size / extent unification is handled by aligning every
    predictor to one analysis grid with a GDAL warped VRT (lazy, block-read),
    replacing the legacy "UnifyProjections" subsystem.

Predictor inputs always use raster CELL VALUES (never any raster attribute-table
values). A raster carrying an attribute table triggers a warning. Each predictor
may instead be a polygon feature layer + numeric field, which is rasterized to the
analysis grid.

NOTE FOR FIRST RUN: the predictor-alignment and coordinate-system handling
(``_grid_from_predictors`` / ``align_predictors``) is the part most worth
verifying against real, multi-projection data.
"""

import os
import re
import json
from datetime import datetime

import numpy as np
import arcpy

import tool_metadata as tmeta   # shared field-level metadata writer

MAHAL_REFERENCE = ("Mahalanobis, P.C. (1936). On the generalised distance in "
                   "statistics. Proc. National Institute of Sciences of India 2(1):49-55.")

MAHAL_TAGS = ("Mahalanobis distance, habitat model, multivariate, covariance, "
              "landscape variables, raster, wildlife, niche, similarity")

# Stretch symbology template for the output raster (blue -> yellow -> red, Percent
# Clip over the full dataset extent), shipped with the toolbox.
LAYER_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "LayerTemplates", "Mahalanobis_Raster.lyrx")
# Separate ramp for chi-square p-value (typicality) output: "Yellow to Green to Dark Blue"
# (yellow = low p / atypical, dark blue = high p / close match).
LAYER_TEMPLATE_PVALUE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "LayerTemplates", "Mahalanobis_Pvalue.lyrx")

# Output value options (kept in sync with the tool's Output value parameter).
OUT_D2 = "Squared distance (D2): Traditional for ecological modeling"
OUT_D = "Distance (D)"
OUT_PVALUE = "Chi-square p-value"

# Statistics-source options.
SRC_POINTS = "Calculate directly from sample point features"
SRC_CATEGORICAL = "Categorical raster"
SRC_TABLES = "Existing tables"

# Inversion options.
INV_STANDARD = "Standard (inverse)"
INV_PSEUDO = "Pseudo-inverse (SVD)"

# Cell-value sampling options.
SAMPLE_EXACT = "Exact (nearest cell)"
SAMPLE_INTERP = "Interpolated (bilinear)"

# Marker pair wrapping the machine-readable variable-order block embedded in the
# metadata description of every saved statistics table. The Mahalanobis raster tool
# reads this back to auto-populate / validate the predictor list.
_META_OPEN = "[MAHAL_VARIABLES_JSON]"
_META_CLOSE = "[/MAHAL_VARIABLES_JSON]"

# Output NoData sentinel for the D2 / D / p-value raster (float32).
NODATA_OUT = -9999.0

# Target block memory budget for the predictor stack (bytes) -> sets strip height.
_BLOCK_BUDGET = 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# GDAL (lazy import so the toolbox still imports if osgeo is briefly unavailable)
# ---------------------------------------------------------------------------
def _gdal():
    from osgeo import gdal
    gdal.UseExceptions()
    return gdal


# ---------------------------------------------------------------------------
# Geoprocessing environments: the grid-native contract
# ---------------------------------------------------------------------------
# The suite's raster analyses are GRID-NATIVE: every computation (window
# sizes, distances, remaps, areas) runs on the input raster's own grid, and
# the output is that grid. But the arcpy tools used to SHUTTLE data --
# CopyRaster delivering a finished raster, CopyRaster/CopyFeatures making
# scratch copies -- silently honor the Output Coordinate System / Extent /
# Snap Raster / Cell Size / Mask environments, which would re-project, crop,
# or resample a dataset MID-PIPELINE: an input copy no longer matching the
# grid the analysis assumes, or a delivered output whose cells and stamped
# statistics no longer describe the analysis (verified live 2026-08-15:
# with outputCoordinateSystem set, CopyRaster projected a finished HSM
# while CreateFeatureclass ignored the same env -- half-honored at best).
# Standing decision (Jeff, 2026-08-15): NEUTRALIZE these environments
# around every shuttle step, and say so in the tools' help; users project
# the finished product deliberately if they want another CS. (Find Raster
# Extreme Values' Output Coordinates support is deliberate and implemented
# in the tool itself, not via env leakage -- unaffected.)
_GRID_ENVS = ("outputCoordinateSystem", "extent", "snapRaster",
              "cellSize", "mask")


class neutral_grid_env(object):
    """Context manager: temporarily clear the environments that would
    re-project, crop, or resample a dataset in a copy/delivery step."""

    def __enter__(self):
        self._saved = []
        for name in _GRID_ENVS:
            try:
                self._saved.append((name, getattr(arcpy.env, name)))
                setattr(arcpy.env, name, None)
            except Exception:
                pass
        return self

    def __exit__(self, exc_type, exc, tb):
        for name, val in self._saved:
            try:
                setattr(arcpy.env, name, val)
            except Exception:
                pass
        return False


# ---------------------------------------------------------------------------
# Per-pixel validity: GetNoDataValue() alone is NOT enough
# ---------------------------------------------------------------------------
# A raster -- notably a GRID inside a file geodatabase, opened via
# OpenFileGDB -- may declare NO NoData value yet still carry masked cells,
# which ReadAsArray returns as phantom FILL VALUES (0 / -128 / -32768 by
# dtype) indistinguishable from real data. GDAL exposes the truth as the
# band's MASK BAND. Standing rule (2026-08-14, verified on the Black_Bear
# tutorial gdb): every band read screened with GetNoDataValue must also
# honor the mask band, via one of the two helpers below.

def band_validity_mask(band):
    """The band's per-pixel validity as a bool array, or None when nothing
    needs masking. Call while the source dataset is still OPEN -- the
    returned array stays usable after the dataset is released, so this is
    the helper for read sites that build their ``valid`` array after a
    ``finally: src = None`` block. None means: no real mask (GMF_ALL_VALID),
    an unreadable mask, or a mask with every pixel valid."""
    try:
        gdal = _gdal()
        if band.GetMaskFlags() & gdal.GMF_ALL_VALID:
            return None
        m = band.GetMaskBand().ReadAsArray()
        if m is None:
            return None
        m = np.asarray(m) != 0
        return None if m.all() else m
    except Exception:
        return None


def mask_band_valid(band, valid, xoff=0, yoff=0):
    """AND the band's per-pixel validity mask into ``valid`` (bool array),
    in place; returns ``valid``. The mask is read over the window matching
    ``valid``'s shape at ``(xoff, yoff)``, so strip readers can pass their
    window origin. The band must still be open. See band_validity_mask for
    the trap this guards against."""
    try:
        gdal = _gdal()
        if band.GetMaskFlags() & gdal.GMF_ALL_VALID:
            return valid
        m = band.GetMaskBand().ReadAsArray(
            int(xoff), int(yoff), valid.shape[1], valid.shape[0])
        if m is not None:
            m = np.asarray(m)
            if m.shape == valid.shape:
                valid &= (m != 0)
    except Exception:
        pass
    return valid


def _set_label(text):
    """Update the geoprocessing progressor label (no-op outside a GP run)."""
    try:
        arcpy.SetProgressorLabel(text)
    except Exception:
        pass


def _confirm(text, caption="Wildlife Tools"):
    """Show a Yes/No confirmation dialog; return True if the user confirms.

    Uses the native Windows dialog. If no UI is available (e.g. a headless
    script), returns True so batch runs are not blocked. Same helper as the
    .pyt's own ``_confirm`` (duplicated here, not imported, since the engine
    modules are imported BY the .pyt, never the other way around) -- used
    by the shared raster-open helper (``hillshade._open_source``) to ask
    before proceeding on an implausibly large raster."""
    try:
        import ctypes
        MB_YESNO = 0x4
        MB_ICONQUESTION = 0x20
        MB_SETFOREGROUND = 0x10000
        MB_TOPMOST = 0x40000
        IDYES = 6
        flags = MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST
        return ctypes.windll.user32.MessageBoxW(0, text, caption, flags) == IDYES
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Whole-run progress bar: replaces Pro's indeterminate marquee with one real
# "step" progressor spanning a tool's entire execute(), so the bar always
# advances instead of animating in place. Deliberately GLOBAL, not an object
# threaded through call args -- arcpy itself only ever drives one progress
# bar per GP run, so a module-level singleton mirrors that reality and lets
# any nested helper (e.g. a shared delivery routine several calls deep)
# advance it without a `progress` parameter threaded through every signature.
#
# A caller declares its run's phases up front as an ordered list of
# (label, weight) pairs -- weight is a rough relative-cost share, not a
# measured time -- via begin_phases(). phase_advance() jumps to the next
# declared phase (for a step with no internal loop); phase_step() reports
# real fractional progress within the CURRENT phase for a step built around
# an actual N-item loop. end_phases() parks the bar at 100%.
# ---------------------------------------------------------------------------
_PHASE_RESOLUTION = 1000
_phase_bounds = []
_phase_i = -1


def begin_phases(phases):
    """Start a whole-run step progressor. ``phases``: ordered list of
    ``(label, weight)`` -- call once, before any work starts, listing every
    phase the run will actually pass through (branch the list at
    construction time; don't call this more than once per run)."""
    global _phase_bounds, _phase_i
    total_w = float(sum(w for _, w in phases)) or 1.0
    _phase_bounds = []
    acc = 0.0
    for label, w in phases:
        start = acc / total_w
        acc += w
        _phase_bounds.append((label, start, acc / total_w))
    _phase_i = 0
    try:
        arcpy.SetProgressor("step", phases[0][0], 0, _PHASE_RESOLUTION, 1)
    except Exception:
        pass


def phase_advance(label=None):
    """Move to the next declared phase and jump the bar to its start.
    ``label`` overrides the phase's declared text; otherwise that text is
    used. Safe to call with no active run (no-op)."""
    global _phase_i
    if not _phase_bounds:
        _set_label(label or "")
        return
    _phase_i = min(_phase_i + 1, len(_phase_bounds) - 1)
    declared, start, _ = _phase_bounds[_phase_i]
    try:
        arcpy.SetProgressorLabel(label or declared)
        arcpy.SetProgressorPosition(int(start * _PHASE_RESOLUTION))
    except Exception:
        pass


def phase_step(done, total):
    """Report real fractional progress (``done``/``total``, e.g. a strip or
    row index) within the CURRENT phase's share of the bar."""
    if not _phase_bounds:
        return
    _, start, end = _phase_bounds[_phase_i]
    frac = start + (end - start) * (min(done, total) / total if total else 1.0)
    try:
        arcpy.SetProgressorPosition(int(frac * _PHASE_RESOLUTION))
    except Exception:
        pass


def end_phases():
    """Park the bar at 100% and clear the run's phase list."""
    global _phase_bounds, _phase_i
    try:
        arcpy.SetProgressorPosition(_PHASE_RESOLUTION)
    except Exception:
        pass
    _phase_bounds = []
    _phase_i = -1


# ---------------------------------------------------------------------------
# Variable-order metadata (shared with the "Calculate Statistical Matrices" tool)
# ---------------------------------------------------------------------------
def encode_variable_metadata(variables, extra=None):
    """Return a JSON blob (wrapped in markers) describing the ordered variables.

    ``variables`` is a list of dicts with at least ``name`` and ``path``. ``extra``
    is an optional dict (e.g. sample count, source kind) merged into the payload.
    """
    payload = {"variables": [{"name": v["name"], "path": v.get("path", "")}
                             for v in variables]}
    if extra:
        payload.update(extra)
    return _META_OPEN + json.dumps(payload) + _META_CLOSE


def decode_variable_metadata(table):
    """Read the embedded variable-order payload from a table's metadata.

    Returns the decoded dict, or None if the table has no embedded block (e.g. a
    legacy table). Never raises -- a missing/garbled block just yields None.
    """
    try:
        from arcpy import metadata as md
        text = md.Metadata(table).description or ""
    except Exception:
        return None
    i = text.find(_META_OPEN)
    j = text.find(_META_CLOSE)
    if i == -1 or j == -1 or j <= i:
        return None
    try:
        return json.loads(text[i + len(_META_OPEN):j])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Spatial-reference / grid helpers
# ---------------------------------------------------------------------------
def _same_sr(a, b):
    """True if two spatial references are effectively the same."""
    if a is None or b is None:
        return False
    if a.factoryCode and b.factoryCode:
        return a.factoryCode == b.factoryCode
    return (a.name or "") == (b.name or "")


def _cell_size(path):
    """Mean cell size of a raster or raster band (robust to band Describe gaps)."""
    try:
        d = arcpy.Describe(path)
        return (float(d.meanCellWidth) + float(d.meanCellHeight)) / 2.0
    except Exception:
        cx = float(arcpy.management.GetRasterProperties(path, "CELLSIZEX").getOutput(0)
                   .replace(",", "."))
        cy = float(arcpy.management.GetRasterProperties(path, "CELLSIZEY").getOutput(0)
                   .replace(",", "."))
        return (cx + cy) / 2.0


def _resamp_arcpy(sample_method):
    return "BILINEAR" if sample_method == SAMPLE_INTERP else "NEAREST"


def _resamp_gdal(sample_method):
    return "bilinear" if sample_method == SAMPLE_INTERP else "near"


# ---------------------------------------------------------------------------
# Predictor resolution + alignment to a common analysis grid
# ---------------------------------------------------------------------------
def resolve_variables(var_rows, exclude_bands=None):
    """Normalize the raw [dataset, field] value-table rows into descriptors.

    Returns a list of dicts: {name, path, kind ('raster'|'feature'), field}.
    ``var_rows`` is a list of (dataset, field) pairs (field may be '' / None).
    ``exclude_bands`` is an optional set of (raster_catalogPath_lowercased, band_number)
    pairs identifying bands of a multiband raster to leave out.
    """
    exclude_bands = exclude_bands or set()
    out = []
    for dataset, field in var_rows:
        path = arcpy.Describe(dataset).catalogPath
        d = arcpy.Describe(path)
        dt = d.dataType

        # A multiband raster expands to one variable per band, named
        # "<raster>_Band_<X>" (skipping any band listed in exclude_bands).
        if dt in ("RasterDataset", "RasterLayer") and getattr(d, "bandCount", 1) > 1:
            for i, ch in enumerate(d.children, start=1):
                if (path.lower(), i) in exclude_bands:
                    continue
                out.append({"name": "{}_{}".format(d.baseName, ch.name),
                            "path": ch.catalogPath, "kind": "raster", "field": None})
            continue

        # A single band reference ("<raster>\Band_X") -> "<raster>_Band_<X>".
        if dt == "RasterBand":
            try:
                parent = arcpy.Describe(os.path.dirname(path)).baseName
            except Exception:
                parent = os.path.splitext(os.path.basename(os.path.dirname(path)))[0]
            out.append({"name": "{}_{}".format(parent, d.baseName),
                        "path": path, "kind": "raster", "field": None})
            continue

        kind = "raster" if dt in ("RasterDataset", "RasterLayer") else "feature"
        # Field is only meaningful for polygon variables (the numeric attribute to
        # rasterize); normalize to a plain string or None.
        field = None if field in (None, "", "<value>") else str(field)
        out.append({"name": d.baseName, "path": path, "kind": kind, "field": field})
    return out


def warn_raster_attribute_tables(variables, messages):
    """Warn that any predictor raster's attribute table is ignored (cell values used)."""
    for v in variables:
        if v["kind"] != "raster":
            continue
        try:
            if arcpy.Describe(v["path"]).hasRAT:
                messages.addWarningMessage(
                    "Raster '{}' has an attribute table; the tool uses its CELL VALUES, "
                    "not any attribute-table values.".format(v["name"]))
        except Exception:
            pass


def _grid_from_predictors(variables, sample_method, scratch, messages):
    """Establish the analysis grid and export every predictor to a grid-CRS GeoTIFF.

    Returns (grid_sr, cell_size, exported) where ``exported`` is a list parallel to
    ``variables`` of GeoTIFF paths, all in the grid coordinate system. Honors the
    cell size / output coordinate system environments when set, else derives them
    from the first raster predictor.
    """
    # Pick the grid CRS + cell size: prefer environments, else the first raster.
    env_sr = arcpy.env.outputCoordinateSystem
    first_raster = next((v for v in variables if v["kind"] == "raster"), None)

    grid_sr = env_sr
    cell = None
    if arcpy.env.cellSize not in (None, "", "MAXOF", "MINOF"):
        try:
            cell = float(arcpy.env.cellSize)
        except Exception:
            cell = None
    if first_raster is not None:
        rdesc = arcpy.Describe(first_raster["path"])
        if grid_sr is None:
            grid_sr = rdesc.spatialReference
        if cell is None:
            cell = _cell_size(first_raster["path"])
    if grid_sr is None or cell is None:
        raise arcpy.ExecuteError(
            "Cannot determine an analysis grid. Provide at least one raster variable, "
            "or set the Output Coordinate System and Cell Size environments.")

    messages.addMessage("Analysis grid: {} | cell size {:g}".format(
        grid_sr.name, cell))

    snap = None
    exported = []
    for v in variables:
        out_tif = arcpy.CreateUniqueName(
            "mahal_var_{}.tif".format(len(exported)), scratch)
        if v["kind"] == "raster":
            rdesc = arcpy.Describe(v["path"])
            if _same_sr(rdesc.spatialReference, grid_sr) and snap is None:
                arcpy.management.CopyRaster(v["path"], out_tif)
            else:
                prev_snap = arcpy.env.snapRaster
                if snap is not None:
                    arcpy.env.snapRaster = snap
                try:
                    arcpy.management.ProjectRaster(
                        v["path"], out_tif, grid_sr, _resamp_arcpy(sample_method), cell)
                finally:
                    arcpy.env.snapRaster = prev_snap
        else:
            if v["field"] is None:
                raise arcpy.ExecuteError(
                    "Polygon variable '{}' needs a numeric field (typed in the Data Source "
                    "column) to rasterize.".format(v["name"]))
            # The Data Source field is typed text (no dropdown), so verify it exists and
            # is numeric and give a clear message if not.
            flds = {f.name.lower(): f.type for f in arcpy.ListFields(v["path"])}
            if v["field"].lower() not in flds:
                raise arcpy.ExecuteError(
                    "Polygon variable '{}' has no field named '{}'.".format(
                        v["name"], v["field"]))
            if flds[v["field"].lower()] not in ("Double", "Single", "Integer",
                                                "SmallInteger", "BigInteger"):
                raise arcpy.ExecuteError(
                    "Field '{}' on polygon variable '{}' must be numeric.".format(
                        v["field"], v["name"]))
            prev_snap, prev_cell = arcpy.env.snapRaster, arcpy.env.cellSize
            if snap is not None:
                arcpy.env.snapRaster = snap
            arcpy.env.cellSize = cell
            try:
                arcpy.conversion.PolygonToRaster(
                    v["path"], v["field"], out_tif, "CELL_CENTER", "NONE", cell)
            finally:
                arcpy.env.snapRaster, arcpy.env.cellSize = prev_snap, prev_cell
        if snap is None:
            snap = out_tif
        exported.append(out_tif)
    return grid_sr, cell, exported


def _env_extent_bounds():
    """(xmin, ymin, xmax, ymax) from the Processing Extent environment, or None.

    The extent is interpreted in the output coordinate system (Pro's convention).
    """
    ext = arcpy.env.extent
    if ext is None or ext in ("", "MAXOF", "MINOF", "DEFAULT"):
        return None
    try:
        if hasattr(ext, "XMin"):
            return (ext.XMin, ext.YMin, ext.XMax, ext.YMax)
        parts = [float(p) for p in str(ext).split()]
        if len(parts) >= 4:
            return tuple(parts[:4])
    except Exception:
        pass
    return None


def _snap_origin(default_ox, default_oy, messages):
    """Grid origin (upper-left) from the Snap Raster environment, else the default."""
    snap = arcpy.env.snapRaster
    if snap:
        try:
            e = arcpy.Describe(snap).extent
            messages.addMessage("Honoring Snap Raster environment.")
            return e.XMin, e.YMax
        except Exception:
            messages.addWarningMessage("Could not read the Snap Raster environment; ignoring it.")
    return default_ox, default_oy


def _common_grid_bounds(exported, cell, messages):
    """Analysis-grid bounds: predictor-intersection clipped to the Processing Extent
    environment, snapped to the Snap Raster environment (or the first tiff).

    Returns (minx, miny, maxx, maxy, width, height), with bounds exactly
    width/height cells so the warp resolution equals ``cell`` exactly.
    """
    exts = [arcpy.Describe(p).extent for p in exported]
    minx = max(e.XMin for e in exts)
    miny = max(e.YMin for e in exts)
    maxx = min(e.XMax for e in exts)
    maxy = min(e.YMax for e in exts)
    if minx >= maxx or miny >= maxy:
        raise arcpy.ExecuteError(
            "The selected variables do not share a common overlap area.")

    env_ext = _env_extent_bounds()
    if env_ext:
        messages.addMessage("Honoring Processing Extent environment.")
        minx, miny = max(minx, env_ext[0]), max(miny, env_ext[1])
        maxx, maxy = min(maxx, env_ext[2]), min(maxy, env_ext[3])
        if minx >= maxx or miny >= maxy:
            raise arcpy.ExecuteError(
                "The Processing Extent does not overlap the landscape-variable data.")

    ox, oy = _snap_origin(exts[0].XMin, exts[0].YMax, messages)
    minx = ox + np.floor((minx - ox) / cell) * cell
    maxx = ox + np.ceil((maxx - ox) / cell) * cell
    maxy = oy - np.floor((oy - maxy) / cell) * cell
    miny = oy - np.ceil((oy - miny) / cell) * cell
    width = int(round((maxx - minx) / cell))
    height = int(round((maxy - miny) / cell))
    # Make bounds exactly width/height cells so warp resolution == cell exactly.
    maxx = minx + width * cell
    miny = maxy - height * cell
    return minx, miny, maxx, maxy, width, height


def align_predictors(variables, sample_method, scratch, messages):
    """Align every predictor to one analysis grid; return aligned GDAL VRT datasets.

    Returns (vrts, geo) where ``vrts`` is a list of warped-VRT gdal.Dataset objects
    (all identical width/height/geotransform, Float64, NaN-filled NoData) and ``geo``
    is a dict {sr, cell, minx, maxy, width, height} describing the grid.
    """
    gdal = _gdal()
    grid_sr, cell, exported = _grid_from_predictors(
        variables, sample_method, scratch, messages)
    minx, miny, maxx, maxy, width, height = _common_grid_bounds(exported, cell, messages)
    messages.addMessage("Aligned grid: {} cols x {} rows".format(width, height))

    vrts = []
    for p in exported:
        vrt = gdal.Warp(
            "", p, format="VRT",
            outputType=gdal.GDT_Float64,
            outputBounds=(minx, miny, maxx, maxy),
            width=width, height=height,
            resampleAlg=_resamp_gdal(sample_method),
            dstNodata=float("nan"))
        vrts.append(vrt)
    geo = {"sr": grid_sr, "cell": cell, "minx": minx, "maxy": maxy,
           "width": width, "height": height}
    return vrts, geo


# ---------------------------------------------------------------------------
# Sampling values at point locations (for the Sample-points statistics source)
# ---------------------------------------------------------------------------
def read_point_xy(in_points, grid_sr):
    """Return point coordinates as an (n, 2) array, in the grid coordinate system.

    Honors any selection on a feature layer (arcpy SearchCursor respects it).
    """
    coords = []
    with arcpy.da.SearchCursor(in_points, ["SHAPE@XY"],
                               spatial_reference=grid_sr) as cur:
        for (xy,) in cur:
            if xy is None:
                continue
            x, y = xy
            if x is None or y is None:
                continue
            coords.append((x, y))
    return np.asarray(coords, dtype=float)


def _read_px(band, r, c, width, height):
    """One cell value from a GDAL band, or NaN if (r, c) is outside the grid."""
    if r < 0 or c < 0 or r >= height or c >= width:
        return np.nan
    return float(band.ReadAsArray(int(c), int(r), 1, 1)[0, 0])


def sample_point_vector(bands, geo, x, y, interp):
    """Sample every aligned predictor band at one (x, y) -> (k,) vector.

    NaN is returned for any band where the location is outside the grid (or, in
    bilinear mode, where any of the four neighbors is NoData).
    """
    cell, minx, maxy = geo["cell"], geo["minx"], geo["maxy"]
    width, height = geo["width"], geo["height"]
    cc = (x - minx) / cell - 0.5          # cell-center fractional coordinates
    rr = (maxy - y) / cell - 0.5
    out = np.empty(len(bands))
    for j, band in enumerate(bands):
        if not interp:
            out[j] = _read_px(band, np.floor(rr + 0.5), np.floor(cc + 0.5), width, height)
        else:
            c0, r0 = int(np.floor(cc)), int(np.floor(rr))
            fx, fy = cc - c0, rr - r0
            v00 = _read_px(band, r0, c0, width, height)
            v01 = _read_px(band, r0, c0 + 1, width, height)
            v10 = _read_px(band, r0 + 1, c0, width, height)
            v11 = _read_px(band, r0 + 1, c0 + 1, width, height)
            out[j] = ((v00 * (1 - fx) + v01 * fx) * (1 - fy)
                      + (v10 * (1 - fx) + v11 * fx) * fy)
    return out


def sample_vrts_at_points(vrts, geo, xy, sample_method):
    """Sample every aligned predictor at each point.

    Returns (samples_valid, n_valid, n_total, n_outside_extent, n_nodata):
      * samples_valid: (n_valid, k) array of points valid in every variable
      * n_outside_extent: points falling outside the common (intersection) extent of
        the landscape rasters -- i.e. outside the extent of one or more of them
      * n_nodata: points inside the extent but on a NoData cell of >=1 variable

    ``exact`` uses the cell the point falls in; ``interpolated`` uses bilinear from
    the four nearest cell centers (a NoData neighbor invalidates that point).
    """
    cell = geo["cell"]
    minx, maxy = geo["minx"], geo["maxy"]
    width, height = geo["width"], geo["height"]
    interp = (sample_method == SAMPLE_INTERP)

    # Fractional pixel coordinates (cell-center convention).
    colf = (xy[:, 0] - minx) / cell - 0.5
    rowf = (maxy - xy[:, 1]) / cell - 0.5
    # The cell that contains each point (for the extent test).
    ccol = np.floor((xy[:, 0] - minx) / cell)
    crow = np.floor((maxy - xy[:, 1]) / cell)
    inside_extent = (ccol >= 0) & (ccol < width) & (crow >= 0) & (crow < height)

    k = len(vrts)
    n = len(xy)
    samples = np.full((n, k), np.nan, dtype=float)
    bands = [v.GetRasterBand(1) for v in vrts]

    for i in range(n):
        samples[i] = sample_point_vector(bands, geo, xy[i, 0], xy[i, 1], interp)
    valid = np.all(np.isfinite(samples), axis=1)
    n_outside = int((~inside_extent).sum())
    n_nodata = int((inside_extent & ~valid).sum())
    return samples[valid], int(valid.sum()), n, n_outside, n_nodata


# ---------------------------------------------------------------------------
# Statistics: mean vector + covariance matrix
# ---------------------------------------------------------------------------
def stats_from_samples(samples):
    """(n, k) samples -> (mean (k,), cov (k, k), corr (k, k)). Sample cov (ddof=1)."""
    mean = samples.mean(axis=0)
    cov = np.cov(samples, rowvar=False, ddof=1)
    cov = np.atleast_2d(cov)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(samples, rowvar=False)
    return mean, cov, np.atleast_2d(corr)


def _gdal_source_path(raster, scratch):
    """Return ``(path, tmp_copy)``: a path ``gdal.Open`` / ``gdal.Warp`` can read for
    ``raster``, and the scratch GeoTIFF that the caller must delete afterward (or None if
    the native path was directly usable).

    GDAL cannot open every arcpy raster by its native path -- rasters inside a file
    geodatabase, mosaic datasets, and some in-memory layers all fail on the catalog
    path. File-geodatabase rasters ARE directly readable through the OpenFileGDB
    driver's subdataset syntax ('OpenFileGDB:"x.gdb":name', GDAL 3.7+; also valid as
    a gdal.Warp source), which avoids the slow whole-raster export, so that is tried
    second. Only when neither works is the raster exported to a scratch GeoTIFF
    (arcpy can read anything) and that path returned, with ``tmp_copy`` set so the
    caller can clean it up once the GDAL dataset / VRT built from it has been
    released.
    """
    gdal = _gdal()
    try:
        path = arcpy.Describe(raster).catalogPath
    except Exception:
        path = raster
    try:
        ds = gdal.Open(path)
    except RuntimeError:
        ds = None
    if ds is not None:
        ds = None
        return path, None
    low = str(path).lower()
    if ".gdb" in low:
        i = low.rindex(".gdb")
        gdb, name = path[:i + 4], path[i + 4:].strip("\\/")
        if name:
            sub = 'OpenFileGDB:"{}":{}'.format(gdb, name)
            try:
                ds = gdal.Open(sub)
            except RuntimeError:
                ds = None
            if ds is not None:
                ds = None
                return sub, None
    tif = arcpy.CreateUniqueName("mahal_src.tif", scratch)
    with neutral_grid_env():        # the copy must match the source grid
        arcpy.management.CopyRaster(raster, tif)
    return tif, tif


def _fmt_num(v):
    """Format a raster cell value for messages: integers without a trailing .0."""
    f = float(v)
    return str(int(f)) if f == int(f) else repr(f)


def resolve_category_values(cat_raster, cat_field, cat_value, messages=None):
    """Map a raster-attribute-table field + value to the matching cell values.

    Returns ``(values, label)``: ``values`` is the list of numeric cell values
    whose attribute-table rows have ``cat_field`` equal to ``cat_value`` (e.g.
    every Landfire EVT code whose EVT_LF is 'Tree'), and ``label`` is a
    human-readable description for messages.

    When ``cat_field`` is empty or is the Value field itself, ``cat_value`` is
    interpreted the classic way -- as literal numeric cell value(s) -- so
    scripts and models that pass a bare number keep working, and so do rasters
    with no attribute table at all. Multiple literal values may be separated
    by semicolons or commas ("3;4;5"), matching geoprocessing multivalue
    convention; all the listed values join the reference sample.
    """
    field = (cat_field or "").strip()
    if not field or field.lower() == "value":
        parts = [p.strip() for p in re.split(r"[;,]", str(cat_value or ""))
                 if p.strip()]
        if not parts:
            raise arcpy.ExecuteError("Enter the category cell value to use.")
        return parts, None
    try:
        cat_path = arcpy.Describe(cat_raster).catalogPath
    except Exception:
        cat_path = cat_raster
    sel = "" if cat_value is None else str(cat_value).strip()
    values, seen = [], set()
    try:
        with arcpy.da.SearchCursor(cat_path, ["Value", field]) as cur:
            for val, attr in cur:
                if val is None:
                    continue
                text = _fmt_num(attr) if isinstance(attr, (int, float)) else str(attr)
                if text.strip() == sel and val not in seen:
                    seen.add(val)
                    values.append(float(val))
    except Exception as e:
        raise arcpy.ExecuteError(
            "Could not read field '{}' from the categorical raster's attribute "
            "table ({}). Choose a field from the raster's attribute table, or "
            "leave the field blank and enter the cell value directly.".format(field, e))
    if not values:
        raise arcpy.ExecuteError(
            "No rows in the categorical raster's attribute table have {} = '{}'. "
            "Check the category value (values are matched exactly).".format(field, sel))
    label = "{} = '{}' ({} raster value{})".format(
        field, sel, len(values), "" if len(values) == 1 else "s")
    if messages is not None:
        messages.addMessage("Category {}: cell value{} [{}]".format(
            label, "" if len(values) == 1 else "s",
            ", ".join(_fmt_num(v) for v in values[:25])
            + (", ..." if len(values) > 25 else "")))
    return values, label


def stats_from_categorical(vrts, geo, cat_path, cat_value, sample_method,
                           scratch, messages, value_caps=None, cat_label=None):
    """Mean/cov/corr from cells where a categorical raster matches ``cat_value``.

    ``cat_value`` may be a single numeric value or a list/tuple of them (e.g. the
    set of cell values a raster-attribute-table category maps to); a cell matching
    ANY of the values joins the reference sample. ``cat_label`` (optional) is a
    human-readable name for the category used in messages (e.g. "EVT_LF = 'Tree'").

    The category raster is aligned to the analysis grid (nearest), then predictors
    are accumulated (streaming, block by block) over matching, fully-valid cells.

    ``value_caps`` (optional): ``{variable_index: max_valid_value}`` -- sample
    cells where a capped variable exceeds its cap are EXCLUDED and counted,
    with a warning. This is the per-cell screen for lost-NoData sentinels
    (a gdb multi-band density raster can drop its NoData declaration, so the
    65535 sentinel arrives scaled as 6.55 -- a mean-based check only fired
    above ~8% contamination, while a single sentinel cell already inflates a
    density variance of ~0.05 by ~36 per cell).
    """
    gdal = _gdal()
    width, height = geo["width"], geo["height"]
    minx, maxy, cell = geo["minx"], geo["maxy"], geo["cell"]
    cat_src, cat_tmp = _gdal_source_path(cat_path, scratch)
    # dstSRS is REQUIRED: the outputBounds are grid coordinates, and without a
    # target SRS gdal.Warp interprets them in the SOURCE's coordinate system --
    # a categorical raster in a different CS (e.g. Landfire in CONUS Albers vs
    # a UTM analysis grid) then reads an empty window and NO cells match. The
    # aligned predictor VRTs carry the grid's exact WKT; fall back to the
    # arcpy spatial reference if a VRT reports none (OpenFileGDB trap).
    grid_wkt = vrts[0].GetProjection() if vrts else ""
    if not grid_wkt:
        try:
            grid_wkt = geo["sr"].exportToString()
        except Exception:
            grid_wkt = ""
    cat_vrt = gdal.Warp(
        "", cat_src, format="VRT",
        outputType=gdal.GDT_Float64, dstSRS=(grid_wkt or None),
        outputBounds=(minx, geo["maxy"] - height * cell, minx + width * cell, maxy),
        width=width, height=height, resampleAlg="near", dstNodata=float("nan"))
    cat_band = cat_vrt.GetRasterBand(1)
    raw_values = (list(cat_value) if isinstance(cat_value, (list, tuple, set))
                  else [cat_value])
    try:
        targets = [float(v) for v in raw_values]
    except (TypeError, ValueError):
        raise arcpy.ExecuteError(
            "Categorical value '{}' is not numeric; raster cell values are numeric.".format(
                cat_value))
    if not targets:
        raise arcpy.ExecuteError("No category cell values to match.")
    if cat_label is None:
        cat_label = (_fmt_num(targets[0]) if len(targets) == 1 else
                     "{} values [{}]".format(
                         len(targets), ", ".join(_fmt_num(t) for t in targets[:12])
                         + (", ..." if len(targets) > 12 else "")))

    k = len(vrts)
    n = 0
    n_capped = 0
    shift = None                    # center subtracted before accumulating (stability)
    s1 = np.zeros(k)                # sum of (X - shift)
    s2 = np.zeros((k, k))           # sum of (X - shift)(X - shift)^T
    bands = [v.GetRasterBand(1) for v in vrts]
    for yoff, rows in _row_strips(height, width, k):
        cat = cat_band.ReadAsArray(0, yoff, width, rows).astype(float)
        stack = np.empty((k, rows, width))
        for j, band in enumerate(bands):
            stack[j] = band.ReadAsArray(0, yoff, width, rows)
        mask = np.zeros(cat.shape, dtype=bool)
        for t in targets:
            mask |= np.isclose(cat, t)
        mask &= np.all(np.isfinite(stack), axis=0)
        if value_caps:
            for j, cap in value_caps.items():
                with np.errstate(invalid="ignore"):
                    over = mask & (stack[j] > cap)
                m = int(over.sum())
                if m:
                    n_capped += m
                    mask &= ~over
        if not mask.any():
            continue
        X = stack[:, mask]                  # (k, m)
        if shift is None:
            shift = X.mean(axis=1)          # center on the first matching block's mean
        dev = X - shift[:, None]
        n += X.shape[1]
        s1 += dev.sum(axis=1)
        s2 += dev @ dev.T
    # The category VRT has been fully read; release it and remove any scratch copy.
    cat_band = None
    cat_vrt = None
    if cat_tmp:
        try:
            arcpy.management.Delete(cat_tmp)
        except Exception:
            pass
    if n_capped and messages is not None:
        messages.addWarningMessage(
            "{:,} sample cells held a value above the valid maximum for "
            "one of the variables and were excluded -- a raster has "
            "probably lost its NoData declaration (a file geodatabase may "
            "not preserve it on a multi-band raster), so NoData sentinels "
            "arrive as data.".format(n_capped))
    if n < k + 1:
        raise arcpy.ExecuteError(
            "Only {} cells matched category {}; need at least {} for a {}-variable "
            "covariance.{}".format(
                n, cat_label, k + 1, k,
                (" Check that the categorical raster actually overlaps the "
                 "analysis extent (the grid set by the landscape variables and "
                 "environments)." if n == 0 else "")))
    # Covariance from the shifted sums: because the deviations are near zero, this
    # subtraction is well-conditioned -- avoiding the catastrophic cancellation a raw
    # one-pass X@X.T suffers on large-magnitude, small-variance data. Add the shift back
    # to recover the true mean, and symmetrize to erase any residual round-off asymmetry.
    mean_dev = s1 / n
    cov = (s2 - n * np.outer(mean_dev, mean_dev)) / (n - 1)
    cov = (cov + cov.T) / 2.0
    mean = shift + mean_dev
    # Correlation from covariance.
    d = np.sqrt(np.diag(cov))
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = cov / np.outer(d, d)
    try:
        cat_name = arcpy.Describe(cat_path).baseName
    except Exception:
        cat_name = os.path.basename(str(cat_path).rstrip("\\/")) or str(cat_path)
    messages.addMessage("Categorical source: {:,} cells of {} matched category {}.".format(
        n, cat_name, cat_label))
    return mean, cov, corr, n


# ---------------------------------------------------------------------------
# Reading mean / covariance from existing tables
# ---------------------------------------------------------------------------
def read_stats_tables(mean_table, cov_table):
    """Read (mean (k,), cov (k, k)) from tables written by this toolbox.

    Schema: each table has a TEXT 'Variable' field plus k DOUBLE value columns
    named Col0..Col{k-1}; rows are in variable order. The mean table uses a single
    value column 'Mean'.
    """
    mean_rows = []
    fields = [f.name for f in arcpy.ListFields(mean_table)]
    mean_field = "Mean" if "Mean" in fields else fields[-1]
    with arcpy.da.SearchCursor(mean_table, [mean_field]) as cur:
        for (m,) in cur:
            mean_rows.append(float(m))
    mean = np.asarray(mean_rows, dtype=float)
    k = len(mean)

    cov_cols = ["Col{}".format(i) for i in range(k)]
    have = [f.name for f in arcpy.ListFields(cov_table)]
    if not all(c in have for c in cov_cols):
        # Fall back to the trailing k numeric columns.
        numeric = [f.name for f in arcpy.ListFields(cov_table)
                   if f.type in ("Double", "Single", "Integer", "SmallInteger")]
        cov_cols = numeric[-k:]
    if len(cov_cols) != k:
        raise arcpy.ExecuteError(
            "The covariance table does not have {} value columns to match the {} "
            "variables in the mean vector.".format(k, k))
    cov = np.empty((k, k), dtype=float)
    rows_read = 0
    with arcpy.da.SearchCursor(cov_table, cov_cols) as cur:
        for r, row in enumerate(cur):
            if r >= k:
                raise arcpy.ExecuteError(
                    "The covariance table has more rows than the mean vector ({} "
                    "variables); the mean and covariance tables must describe the same "
                    "variables in the same order.".format(k))
            cov[r, :] = [float(x) for x in row]
            rows_read = r + 1
    if rows_read != k:
        raise arcpy.ExecuteError(
            "The covariance table has {} rows but the mean vector has {} variables; they "
            "must match.".format(rows_read, k))
    return mean, cov


# ---------------------------------------------------------------------------
# Covariance inversion (NumPy)
# ---------------------------------------------------------------------------
def invert_covariance(cov, method, messages):
    """Invert the covariance matrix; report the condition number and a sanity check.

    Returns the inverse. Raises arcpy.ExecuteError with a clear message on a
    singular matrix when using the standard inverse.
    """
    cond = np.linalg.cond(cov)
    messages.addMessage("Covariance condition number: {:.4g}".format(cond))
    # float64 is ~16 digits, so cond beyond ~1e15 means the matrix is numerically
    # singular -- np.linalg.inv often returns garbage WITHOUT raising in that case, so
    # we gate on the condition number rather than trusting LinAlgError alone.
    SINGULAR = 1e15
    if method == INV_PSEUDO:
        inv = np.linalg.pinv(cov)
    else:
        if cond > SINGULAR:
            raise arcpy.ExecuteError(
                "Covariance matrix is singular (condition number {:.3g}) and cannot be "
                "reliably inverted. This usually means a variable has no variance (all "
                "sample values identical) or two variables are perfectly correlated. Use "
                "the pseudo-inverse option or remove the offending variable.".format(cond))
        if cond > 1e12:
            messages.addWarningMessage(
                "Covariance matrix is ill-conditioned (cond = {:.4g}); variables are "
                "nearly collinear. Consider the pseudo-inverse option or dropping a "
                "variable.".format(cond))
        try:
            inv = np.linalg.inv(cov)
        except np.linalg.LinAlgError:
            raise arcpy.ExecuteError(
                "Covariance matrix is singular and cannot be inverted. This usually means "
                "a variable has no variance (all sample values identical) or two variables "
                "are perfectly correlated. Use the pseudo-inverse option or remove the "
                "offending variable.")
    if not np.allclose(cov @ inv, np.eye(cov.shape[0]), atol=1e-6):
        messages.addWarningMessage(
            "Inversion sanity check (Sigma * Sigma^-1 ~ I) did not hold tightly; "
            "results may be numerically unstable.")
    return inv


# ---------------------------------------------------------------------------
# Block-vectorized D2 engine
# ---------------------------------------------------------------------------
def _row_strips(height, width, k):
    """Yield (yoff, rows) strips sized to keep the predictor stack within budget."""
    per_row = max(1, width * k * 8)
    strip = max(1, min(height, int(_BLOCK_BUDGET / per_row)))
    for yoff in range(0, height, strip):
        yield yoff, min(strip, height - yoff)


def _mask_vrt(geo, messages):
    """Return ``(vrt, tmp_copy)`` for the Mask environment aligned to the grid, or
    ``(None, None)`` if there is no mask or it could not be read.

    The dataset (not just its band) must be returned so the caller can keep it alive
    for the duration of the block loop -- a freed VRT invalidates its bands. ``tmp_copy``
    is a scratch GeoTIFF the caller must delete after the loop (None if not needed); it
    backs the VRT, so it can only be removed once the VRT is released.
    """
    mask = arcpy.env.mask
    if not mask:
        return None, None
    gdal = _gdal()
    mask_tmp = None
    try:
        mask_path, mask_tmp = _gdal_source_path(mask, arcpy.env.scratchFolder)
        width, height, cell = geo["width"], geo["height"], geo["cell"]
        minx, maxy = geo["minx"], geo["maxy"]
        vrt = gdal.Warp(
            "", mask_path, format="VRT", outputType=gdal.GDT_Float64,
            outputBounds=(minx, maxy - height * cell, minx + width * cell, maxy),
            width=width, height=height, resampleAlg="near", dstNodata=float("nan"))
        messages.addMessage("Honoring Mask environment.")
        return vrt, mask_tmp
    except Exception as e:
        messages.addWarningMessage("Could not apply the Mask environment: {}".format(e))
        if mask_tmp:
            try:
                arcpy.management.Delete(mask_tmp)
            except Exception:
                pass
        return None, None


class _RasterStats(object):
    """Streaming min / max / mean / std accumulator over the valid output cells.

    Lets us record the statistics from values we are already computing, so the output's
    statistics can be set directly (arcpy SetRasterProperties) instead of paying for a
    full-raster CalculateStatistics scan -- which, on file-geodatabase float rasters,
    is pathologically slow (tens of seconds even for tiny rasters) and scans the data we
    just generated. Uses population variance (ddof=0), matching CalculateStatistics.
    """
    __slots__ = ("n", "vmin", "vmax", "vsum", "vsumsq")

    def __init__(self):
        self.n = 0
        self.vmin = np.inf
        self.vmax = -np.inf
        self.vsum = 0.0
        self.vsumsq = 0.0

    def add(self, values):
        v = np.asarray(values, dtype=np.float64).ravel()
        if v.size == 0:
            return
        self.n += int(v.size)
        self.vmin = min(self.vmin, float(v.min()))
        self.vmax = max(self.vmax, float(v.max()))
        self.vsum += float(v.sum())
        self.vsumsq += float(np.square(v).sum())

    def result(self):
        if self.n == 0:
            return None
        mean = self.vsum / self.n
        var = max(self.vsumsq / self.n - mean * mean, 0.0)
        return {"min": self.vmin, "max": self.vmax, "mean": mean,
                "std": float(np.sqrt(var)), "count": self.n}


def clean_sr(sr):
    """A copy of ``sr`` stripped of its dataset-specific XY domain,
    resolution and tolerance, for creating OUTPUT feature classes.
    A ``Describe(...).spatialReference`` carries its source dataset's
    XY domain along with it, and some datasets record a domain hugged
    tightly around their own data -- ``CreateFeatureclass`` then passes
    that cramped domain to the output, and any geometry reaching past
    it (shapes kept whole at a boundary, radials, arrays, corridor
    outlines) fails to insert with ``RuntimeError: The coordinates or
    measures are out of bounds`` (2026-09-02, the Repeating Shapes
    Pinalenos crash). Rebuilding from the CS definition alone (the
    part before the first ';' of ``exportToString``) lets the
    geodatabase assign its default, effectively unlimited domain.
    None / plain strings / already-clean SRs pass through unharmed.
    An identical twin lives in ``voronoi_tools.clean_sr`` for the
    geometric-family engines, whose tools reload that module (not
    this one) on every execute.
    """
    import arcpy

    try:
        if sr is None or not hasattr(sr, "exportToString"):
            return sr
        s = arcpy.SpatialReference()
        s.loadFromString(str(sr.exportToString()).split(";")[0])
        return s
    except Exception:                                        # noqa: BLE001
        return sr


def is_memory_workspace(path):
    """True if ``path`` IS ArcGIS's in-memory workspace (``memory`` or the
    legacy ``in_memory``) or points at a dataset inside it, e.g. the
    Geoprocessing pane's auto-suggested ``memory\\Tool_output`` defaults
    (2026-08-26, the Classification Accuracy memory-output crash). Only a
    LEADING ``memory``/``in_memory`` element counts -- a disk folder that
    happens to be named ``memory`` does not. Datasets here behave like
    geodatabase datasets (extensionless names, nullable fields), so the
    gdb tests below also return True for them; but the workspace is NOT a
    folder on disk -- side files (.lyrx and friends) must go to
    ``arcpy.env.scratchFolder`` instead.
    """
    low = str(path or "").lower().replace("/", "\\").rstrip("\\")
    return low.split("\\", 1)[0] in ("memory", "in_memory")


def is_gdb_workspace(path):
    """True if ``path`` IS a geodatabase-style container (.gdb or .sde),
    or the in-memory workspace, whose datasets follow the same rules
    (extensionless names, nullable fields).

    Normalizes case, slash direction and -- critically -- trailing separators
    first: a workspace typed as ``...\\Data.gdb\\`` otherwise fails a bare
    ``endswith(".gdb")`` test, and the dataset that should have landed in the
    geodatabase is created with the wrong format or in the wrong place
    (2026-08-07, the Land Facet Mahalanobis statistics tables). Use this for
    every .gdb/.sde suffix decision; never test the raw string. Where memory
    must be told apart from a real geodatabase (side files, layer files),
    test ``is_memory_workspace`` explicitly.
    """
    low = str(path or "").lower().replace("/", "\\").rstrip("\\")
    return low.endswith((".gdb", ".sde")) or is_memory_workspace(low)


def in_gdb_container(path):
    """True if ``path`` points INTO (or at) a geodatabase or the in-memory
    workspace, boundary-aware.

    Unlike a bare ``".gdb" in path`` substring test, a folder merely NAMED like
    ``My.gdb_exports`` does not count -- its ".gdb" is not followed by a
    separator. Feature-dataset paths (``x.gdb\\fds\\fc``) do count.
    """
    low = str(path or "").lower().replace("/", "\\").rstrip("\\")
    for ext in (".gdb", ".sde"):
        if low.endswith(ext) or (ext + "\\") in low:
            return True
    return is_memory_workspace(low)


def lyrx_dir_for(ws):
    """The directory that should receive a dataset's side ``.lyrx`` file:
    next to the geodatabase for gdb datasets, the folder itself otherwise,
    and ``None`` for the in-memory workspace, which has no on-disk home --
    callers skip the side file and the tool's output still works, just
    without default symbology (2026-08-26, memory-output sweep)."""
    if is_memory_workspace(ws):
        return None
    return os.path.dirname(ws) if is_gdb_workspace(ws) else ws


def ensure_label_class(lyrx, messages=None):
    """Give every feature layer in ``lyrx`` a default label class if it has
    none, and return ``lyrx`` (unchanged on any failure).

    A layer file written by MakeFeatureLayer -> SaveToLayerFile carries NO
    ``labelClasses`` at all, and when the GP framework applies such a file
    as an output parameter's symbology the map layer is created WITHOUT a
    label class. Pro's Labeling ribbon then has nothing to bind to: its
    Label Class box shows -- and keeps showing -- some OTHER layer's label
    properties, and the layer cannot be labeled (Jeff, 2026-09-09, the
    Black Bear corridor; every symbolized feature output of ours had the
    same defect, and Export Features / Project carry it on to copies). One
    arcpy.mp round trip materializes Pro's own geometry-aware default label
    class (an Arcade expression on the display field) and ``save()`` writes
    it into the file, so call this LAST in every feature-class .lyrx
    builder, after the renderer patch. Files that already have label
    classes are left untouched."""
    import json
    try:
        with open(lyrx, encoding="utf-8") as fh:
            doc = json.load(fh)
        defs = [ld for ld in doc.get("layerDefinitions", [])
                if ld.get("type") == "CIMFeatureLayer"]
        if not defs or all(ld.get("labelClasses") for ld in defs):
            return lyrx
        import arcpy
        lf = arcpy.mp.LayerFile(lyrx)
        for lyr in lf.listLayers():
            try:
                if not (lyr.isFeatureLayer and lyr.supports("SHOWLABELS")):
                    continue
                lyr.listLabelClasses()          # materializes the default
                cim = lyr.getDefinition("V3")
                # A hand-built layer definition has no display field, so
                # the default class comes back with an EMPTY expression;
                # label by ObjectID rather than ship a class Pro rejects.
                for lc in cim.labelClasses or []:
                    if not lc.expression:
                        lc.expression = "$feature." + arcpy.Describe(
                            lyr).OIDFieldName
                        lc.expressionEngine = "Arcade"
                lyr.setDefinition(cim)
            except Exception:
                pass
        lf.save()
    except Exception as e:
        if messages is not None:
            try:
                messages.addWarningMessage(
                    "Could not add a default label class to the symbology "
                    "layer file ({}); the output can still be labeled after "
                    "re-adding it to the map.".format(e))
            except Exception:
                pass
    return lyrx


def resolve_raster_output(path, messages=None):
    """Folder-based raster outputs default to GeoTIFF (Jeff's no-more-GRID
    convention, 2026-07-19). Without this, CopyRaster writes an extensionless
    name outside a geodatabase as the ancient Esri GRID format, whose
    13-character name limit fails with ERROR 000472. Geodatabase paths
    (.gdb/.sde) are left alone; on disk a missing extension becomes .tif
    (ArcGIS writes a .tfw world file with it) and a user-typed extension is
    honored. Callers should deliver to the RETURNED path and hand it back to
    their tool's output parameter so add-to-map targets the real dataset."""
    if not path:
        return path
    # No dataset path legitimately ends with a separator; a typed trailing
    # backslash would otherwise survive into the delivered path (or turn a
    # folder output into "name\\.tif").
    p = str(path).rstrip("\\/")
    if in_gdb_container(p):
        return p
    if not os.path.splitext(p)[1]:
        if messages is not None:
            messages.addMessage(
                "No raster format specified for '{}' outside a geodatabase "
                "-- saving as GeoTIFF: {}.tif (the legacy Esri GRID format "
                "is not used).".format(os.path.basename(p),
                                       os.path.basename(p)))
        return p + ".tif"
    return p


def pin_selection(item):
    """The item's CURRENT selection as a set of OIDs, captured NOW, or
    None when there is no selection (or the item is a bare path).

    STANDING RULE (2026-08-23, found in Select Random Records): a live
    layer OBJECT's selection can be silently DROPPED by unrelated
    arcpy calls later in the run (headless Pro 3.7; nondeterministic
    timing). Any tool that consumes an input's selection AFTER other
    work -- especially sequential multi-input tools -- must pin every
    input's selection UP FRONT with this helper and consume the pinned
    OIDs (see ``pinned_layer``) instead of trusting the live layer."""
    try:
        fids = getattr(arcpy.Describe(item), "FIDSet", "") or ""
    except Exception:                                              # noqa: BLE001
        return None
    vals = [v for v in str(fids).replace(",", ";").split(";")
            if str(v).strip()]
    return {int(v) for v in vals} if vals else None


def pinned_layer(path, oids, name):
    """A scratch feature layer holding exactly ``oids`` of ``path``
    (an explicit OID definition query -- immune to live-selection
    loss). The caller deletes it with arcpy.management.Delete."""
    fld = arcpy.AddFieldDelimiters(path,
                                   arcpy.Describe(path).oidFieldName)
    where = "{} IN ({})".format(
        fld, ",".join(str(v) for v in sorted(oids)))
    arcpy.management.MakeFeatureLayer(path, name, where)
    return name


def selection_info(item, path):
    """``(n_total, n_selected, has_selection)`` for one Feature Layer GP
    input.

    ``item`` MUST be the ORIGINAL parameter value (a layer name/reference
    when the input came from the map -- the only way arcpy can know about a
    selection at all); ``path`` is its resolved catalog path, safe to count
    against directly regardless of selection state. A dataset picked via
    the GP dialog's Browse button (no map layer involved) has no selection
    concept at all -- for it, ``n_selected`` always equals ``n_total`` and
    ``has_selection`` is False, same as a layer with an empty selection.
    Never raises; an unreadable count comes back as ``(None, None, False)``.
    """
    try:
        n_total = int(arcpy.management.GetCount(path)[0])
    except Exception:                                              # noqa: BLE001
        return None, None, False
    has_selection = False
    n_selected = n_total
    try:
        fids = getattr(arcpy.Describe(item), "FIDSet", "") or ""
        if fids.strip():
            has_selection = True
            n_selected = int(arcpy.management.GetCount(item)[0])
    except Exception:                                              # noqa: BLE001
        pass
    return n_total, n_selected, has_selection


def default_stretch_lyrx(out_raster, colors, messages=None):
    """Create a .lyrx beside ``out_raster`` with a linear multi-stop stretch
    (minimum-maximum off the dataset's own stored statistics) running low to
    high through ``colors``, a sequence of >=2 ``(r, g, b)`` tuples. Returns
    the .lyrx path -- set it as the tool's output parameter ``.symbology`` in
    the .pyt (the officially documented way a GP tool colors its own output
    the moment it's added to the map at the end of the run) -- or None on
    failure. Never raises.

    Same proven pattern as ``write_raster_layer_file`` (MakeRasterLayer ->
    SaveToLayerFile -> patch colorRamp/stretchType/statsType, keeping the
    real stretchStats SaveToLayerFile captured -- a colorizer with no stats
    will not render).

    **2026-07-20 HISTORY:** an earlier version of this helper
    (``embed_default_symbology``, since removed) tried to persist an
    equivalent colorizer into the output raster's own METADATA, on the
    theory that this is the mechanism behind ArcGIS Pro auto-symbolizing an
    Aspect/Slope/Hillshade raster with built-in symbology every time it's
    added to ANY future map, including a brand-new one long after the tool
    ran. That theory was FALSIFIED by a controlled experiment: embedding the
    exact, byte-for-byte metadata captured from a real auto-symbolizing
    Aspect raster onto a different (VRM) raster, in a fresh Pro session,
    fresh map, first touch -- and it still did not auto-symbolize, while the
    original Aspect raster (added to the very same map) did. So whatever
    actually drives Aspect's behavior is NOT the metadata LayerFile block;
    it lives somewhere else undiscovered. Do not resurrect the
    metadata-embedding approach without first re-establishing causation the
    same way (a controlled swap test), not correlation.
    """
    try:
        path = arcpy.Describe(out_raster).catalogPath
        name = os.path.splitext(os.path.basename(path))[0]
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    ws = os.path.dirname(path)
    lyr_dir = lyrx_dir_for(ws)
    if lyr_dir is None:
        return None
    out_lyrx = os.path.join(lyr_dir, name + "_symbology.lyrx")
    tmp = "jenness_symb_tmp"
    try:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass
        arcpy.management.MakeRasterLayer(path, tmp)
        if os.path.exists(out_lyrx):      # SaveToLayerFile refuses to overwrite
            os.remove(out_lyrx)
        arcpy.management.SaveToLayerFile(tmp, out_lyrx, "ABSOLUTE")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    finally:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass

    try:
        with open(out_lyrx, encoding="utf-8") as f:
            lyr = json.load(f)
        out_ld = lyr["layerDefinitions"][0]
        out_ld["name"] = name
        col = out_ld.get("colorizer") or {}
        col["stretchType"] = "MinimumMaximum"
        col["statsType"] = "Dataset"
        col["dra"] = False
        col["colorScheme"] = ""
        space = {"type": "CIMICCColorSpace", "url": "Default RGB"}
        segments = [
            {
                "type": "CIMLinearContinuousColorRamp",
                "colorSpace": space,
                "fromColor": {"type": "CIMRGBColor",
                             "values": list(colors[i]) + [100]},
                "toColor": {"type": "CIMRGBColor",
                           "values": list(colors[i + 1]) + [100]},
            }
            for i in range(len(colors) - 1)
        ]
        col["colorRamp"] = segments[0] if len(segments) == 1 else {
            "type": "CIMMultipartColorRamp",
            "colorRamps": segments,
            "weights": [1] * len(segments),
        }
        out_ld["colorizer"] = col
        with open(out_lyrx, "w", encoding="utf-8") as f:
            json.dump(lyr, f)
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage(
                "Layer file created but symbology could not be applied: {}".format(e))
    return out_lyrx


_ASPECT_RAMP_STOPS = [(41, 147, 162), (255, 255, 190), (51, 23, 44),
                      (255, 255, 190), (41, 147, 162)]
_ASPECT_RAMP_WEIGHTS = [0.21293969849246236, 0.28077889447236176,
                        0.29522613065326647, 0.21105527638190943]
_ASPECT_BACKGROUND = [255, 235, 190, 100]     # pale tan mask for flat (-1)


def default_aspect_lyrx(out_raster, messages=None):
    """Create a .lyrx beside ``out_raster`` symbolizing an aspect DIRECTION
    raster (0-360 compass) with the cyclic aspect color scheme -- bluegreen ->
    yellow -> brown -> yellow -> bluegreen, so 0 and 360 degrees share the same
    color -- and the flat sentinel -1 drawn separately in a pale-tan mask color
    (a background value, not part of the ramp). Ported from Jeff's
    Aspect_AZ_NM.lyrx template. Returns the .lyrx path or None; never raises.

    Uses a FIXED 0-360 stretch so a given direction always maps to the same
    color regardless of the data's actual spread (the whole point of a cyclic
    aspect ramp)."""
    try:
        path = arcpy.Describe(out_raster).catalogPath
        name = os.path.splitext(os.path.basename(path))[0]
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    ws = os.path.dirname(path)
    lyr_dir = lyrx_dir_for(ws)
    if lyr_dir is None:
        return None
    out_lyrx = os.path.join(lyr_dir, name + "_symbology.lyrx")
    tmp = "jenness_aspect_tmp"
    try:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass
        arcpy.management.MakeRasterLayer(path, tmp)
        if os.path.exists(out_lyrx):      # SaveToLayerFile refuses to overwrite
            os.remove(out_lyrx)
        arcpy.management.SaveToLayerFile(tmp, out_lyrx, "ABSOLUTE")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    finally:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass

    try:
        with open(out_lyrx, encoding="utf-8") as f:
            lyr = json.load(f)
        out_ld = lyr["layerDefinitions"][0]
        out_ld["name"] = name
        space = {"type": "CIMICCColorSpace", "url": "Default RGB"}
        segs = [{
            "type": "CIMLinearContinuousColorRamp",
            "colorSpace": space,
            "fromColor": {"type": "CIMRGBColor",
                          "values": list(_ASPECT_RAMP_STOPS[i]) + [100]},
            "toColor": {"type": "CIMRGBColor",
                        "values": list(_ASPECT_RAMP_STOPS[i + 1]) + [100]},
        } for i in range(len(_ASPECT_RAMP_STOPS) - 1)]
        out_ld["colorizer"] = {
            "type": "CIMRasterStretchColorizer",
            "resamplingType": "NearestNeighbor",
            "noDataColor": {"type": "CIMRGBColor", "values": [255, 255, 255, 0]},
            "backgroundColor": {"type": "CIMRGBColor",
                                "values": list(_ASPECT_BACKGROUND)},
            "backgroundValue": -1,
            "displayBackgroundValue": True,
            "colorRamp": {"type": "CIMMultipartColorRamp",
                          "colorRamps": segs,
                          "weights": list(_ASPECT_RAMP_WEIGHTS)},
            "colorScheme": "Aspect (cyclic)",
            "gammaValue": 1,
            "minPercent": 0.0, "maxPercent": 0.0,
            "standardDeviationParam": 2,
            "statsType": "Dataset",
            "dra": False,
            "stretchClasses": [
                {"type": "CIMRasterStretchClass", "label": "-1 (flat)",
                 "value": -1},
                {"type": "CIMRasterStretchClass", "label": "180", "value": 180},
                {"type": "CIMRasterStretchClass", "label": "360", "value": 360},
            ],
            "stretchStats": {"type": "StatsHistogram", "min": 0.0, "max": 360.0,
                             "mean": 180.0, "stddev": 104.0,
                             "limitMin": 0.0, "limitMax": 360.0},
            "stretchType": "MinimumMaximum",
        }
        with open(out_lyrx, "w", encoding="utf-8") as f:
            json.dump(lyr, f)
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage(
                "Layer file created but symbology could not be applied: {}".format(e))
    return out_lyrx


def default_unique_value_lyrx(out_raster, classes, field="Class_Name",
                              messages=None):
    """Create a .lyrx beside ``out_raster`` with a UNIQUE VALUES renderer keyed
    on ``field`` (default the Class_Name string field of the raster's attribute
    table), one entry per class -- ``classes`` is a list of dicts with 'value',
    'name', and optional 'color' [r, g, b]. Returns the .lyrx path (set it as the
    tool's output parameter ``.symbology`` in the .pyt), or None. Never raises.

    A GDAL colormap on a Byte raster is inherently a full 0-255 table, so every
    value with no class shows as a black legend swatch under Colormap symbology.
    A unique-value renderer on Class_Name lists only the classes that exist.
    Same MakeRasterLayer -> SaveToLayerFile -> patch-colorizer pattern as
    ``default_stretch_lyrx``."""
    try:
        path = arcpy.Describe(out_raster).catalogPath
        name = os.path.splitext(os.path.basename(path))[0]
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    ws = os.path.dirname(path)
    lyr_dir = lyrx_dir_for(ws)
    if lyr_dir is None:
        return None
    out_lyrx = os.path.join(lyr_dir, name + "_symbology.lyrx")
    tmp = "jenness_uv_tmp"
    try:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass
        arcpy.management.MakeRasterLayer(path, tmp)
        if os.path.exists(out_lyrx):      # SaveToLayerFile refuses to overwrite
            os.remove(out_lyrx)
        arcpy.management.SaveToLayerFile(tmp, out_lyrx, "ABSOLUTE")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    finally:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass

    try:
        with open(out_lyrx, encoding="utf-8") as f:
            lyr = json.load(f)
        out_ld = lyr["layerDefinitions"][0]
        out_ld["name"] = name

        def _rgb(c):
            col = list(c.get("color") or [156, 156, 156])
            try:
                a = float(c.get("alpha", 100))     # 0-100 percent opacity
            except (TypeError, ValueError):
                a = 100.0
            a = min(max(a, 0.0), 100.0)            # CIM alpha is also 0-100
            return {"type": "CIMRGBColor",
                    "values": [int(col[0]), int(col[1]), int(col[2]), a]}

        uv_classes = []
        for c in sorted(classes, key=lambda c: int(c["value"])):
            label = str(c.get("name", c["value"]))
            uv_classes.append({
                "type": "CIMRasterUniqueValueClass",
                "values": [label],
                "label": label,
                "color": _rgb(c),
                "editable": True,
            })
        out_ld["colorizer"] = {
            "type": "CIMRasterUniqueValueColorizer",
            "resamplingType": "NearestNeighbor",
            "noDataColor": {"type": "CIMRGBColor", "values": [255, 255, 255, 0]},
            "fieldName": field,
            "groups": [{
                "type": "CIMRasterUniqueValueGroup",
                "classes": uv_classes,
            }],
        }
        with open(out_lyrx, "w", encoding="utf-8") as f:
            json.dump(lyr, f)
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage(
                "Layer file created but unique-value symbology could not be "
                "applied: {}".format(e))
    return out_lyrx


def _multipart_rgb_ramp(colors):
    """A CIMMultipartColorRamp interpolating low->high through ``colors``
    (>=2 (r, g, b) tuples), in RGB. Shared by the stretch and unclassed helpers."""
    space = {"type": "CIMICCColorSpace", "url": "Default RGB"}
    segs = [{
        "type": "CIMLinearContinuousColorRamp", "colorSpace": space,
        "fromColor": {"type": "CIMRGBColor", "values": list(colors[i]) + [100]},
        "toColor": {"type": "CIMRGBColor", "values": list(colors[i + 1]) + [100]},
    } for i in range(len(colors) - 1)]
    if len(segs) == 1:
        return segs[0]
    return {"type": "CIMMultipartColorRamp", "colorRamps": segs,
            "weights": [1] * len(segs)}


def default_unclassed_lyrx(fc, field, colors, messages=None):
    """Create a .lyrx beside polyline feature class ``fc`` symbolizing it with
    ArcGIS Pro's "Unclassed Colors" on ``field``, using a low->high multi-stop
    ramp through ``colors`` ((r, g, b) tuples). Returns the .lyrx path (set it as
    a feature output parameter's ``.symbology`` in the .pyt) or None. Never
    raises. Templated from a real Pro Unclassed-Colors renderer: a
    CIMClassBreaksRenderer with classBreakType "UnclassedColor" plus a
    CIMColorVisualVariable (minValue/maxValue) that drives the continuous color;
    the field's data range is read from the layer."""
    try:
        path = arcpy.Describe(fc).catalogPath
        name = os.path.splitext(os.path.basename(path))[0]
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    ws = os.path.dirname(path)
    lyr_dir = lyrx_dir_for(ws)
    if lyr_dir is None:
        return None
    out_lyrx = os.path.join(lyr_dir, name + "_symbology.lyrx")

    lo = hi = None
    try:
        with arcpy.da.SearchCursor(fc, [field]) as cur:
            for (v,) in cur:
                if v is None:
                    continue
                lo = v if lo is None else min(lo, v)
                hi = v if hi is None else max(hi, v)
    except Exception:
        pass
    if lo is None:
        lo = 0.0
    if hi is None or hi <= lo:
        hi = lo + 1.0

    tmp = "jenness_unclassed_tmp"
    try:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass
        arcpy.management.MakeFeatureLayer(path, tmp)
        if os.path.exists(out_lyrx):      # SaveToLayerFile refuses to overwrite
            os.remove(out_lyrx)
        arcpy.management.SaveToLayerFile(tmp, out_lyrx, "ABSOLUTE")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    finally:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass

    try:
        with open(out_lyrx, encoding="utf-8") as f:
            lyr = json.load(f)
        ld = lyr["layerDefinitions"][0]
        ld["name"] = name
        base_sym = ld.get("renderer", {}).get("symbol")     # Pro's default line symbol
        ramp = _multipart_rgb_ramp(colors)
        brk_sym = base_sym or {"type": "CIMSymbolReference"}
        ld["renderer"] = {
            "type": "CIMClassBreaksRenderer",
            "barrierWeight": "High",
            "classBreakType": "UnclassedColor",
            "classificationMethod": "DefinedInterval",
            "colorRamp": ramp,
            "field": field,
            "heading": field,
            "minimumBreak": lo,
            "minimumLabel": "{:.6f}".format(lo),
            "polygonSymbolColorTarget": "Fill",
            "breaks": [{
                "type": "CIMClassBreak", "label": "{:.6f}".format(hi),
                "patch": "Default", "symbol": brk_sym, "upperBound": hi,
            }],
            "showInAscendingOrder": True,
            "defaultSymbolPatch": "Default",
            "defaultSymbol": brk_sym,
            "defaultLabel": "<null>",
            "numberFormat": {
                "type": "CIMNumericFormat", "alignmentOption": "esriAlignRight",
                "alignmentWidth": 0, "roundingOption": "esriRoundNumberOfDecimals",
                "roundingValue": 6},
            "alwaysUpdateClassLabels": True,
            "sampleSize": 10000,
            "normalizationType": "Nothing",
            "useExclusionSymbol": False,
            "exclusionSymbolPatch": "Default",
            "visualVariables": [{
                "type": "CIMColorVisualVariable",
                "minValue": lo, "maxValue": hi, "colorRamp": ramp}],
        }
        with open(out_lyrx, "w", encoding="utf-8") as f:
            json.dump(lyr, f)
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage(
                "Layer file created but symbology could not be applied: {}".format(e))
    return ensure_label_class(out_lyrx, messages)


def _apply_known_statistics(raster, stats, messages):
    """Set band statistics on ``raster`` from precomputed values (no scan).

    Returns True on success. On False (no stats, or the call failed) the caller should
    fall back to arcpy.management.CalculateStatistics.
    """
    if not stats:
        return False
    try:
        arcpy.management.SetRasterProperties(
            raster,
            statistics=[[1, stats["min"], stats["max"], stats["mean"], stats["std"]]])
        if messages is not None:
            messages.addMessage(
                "Set raster statistics from the computed values (min {:.6g}, max {:.6g}, "
                "mean {:.6g}, std {:.6g}).".format(
                    stats["min"], stats["max"], stats["mean"], stats["std"]))
        return True
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage(
                "Could not set precomputed statistics ({}); falling back to Calculate "
                "Statistics.".format(e))
        return False


# Number of histogram bins to embed in the output layer file (matches ArcGIS Pro's
# default raster-stretch histogram resolution).
_HIST_BINS = 256


def _finalize_stats(out_band, stats_acc, messages):
    """Return the ``stats_acc`` result (min/max/mean/std/count) augmented with a
    ``_HIST_BINS``-bin histogram read from ``out_band``.

    The histogram is what the Percent Clip stretch needs to find its 0.5 % clip bounds;
    we read it from the freshly written *scratch GeoTIFF* band with GDAL (fast and local),
    avoiding the pathologically slow CalculateStatistics on the file-geodatabase output.
    The histogram keys are omitted for an empty or single-value (constant) raster.
    """
    result = stats_acc.result()
    if result is not None and result["max"] > result["min"]:
        try:
            counts = out_band.GetHistogram(result["min"], result["max"], _HIST_BINS,
                                           include_out_of_range=0, approx_ok=0)
            nsamp = int(sum(counts))
            if nsamp > 0:
                # Keep the raw counts (for GDAL SetDefaultHistogram) plus nsamples and
                # resolution; the layer-file densities are derived from these.
                result["hist_counts"] = [int(c) for c in counts]
                result["nsamples"] = nsamp
                result["resolution"] = (result["max"] - result["min"]) / (_HIST_BINS - 1)
        except Exception as e:
            if messages is not None:
                messages.addWarningMessage(
                    "Could not build the symbology histogram: {}".format(e))
    return result


def _write_gtiff_stats(out_band, stats):
    """Write statistics + a default histogram into a GDAL band so arcpy CopyRaster carries
    them into the output raster -- including file-geodatabase rasters, which GDAL cannot
    write to directly but CopyRaster *can* populate from the scratch GeoTIFF's PAM.

    Returns True if a histogram was written (which means the output will carry one, so the
    caller must NOT then call SetRasterProperties -- that erases the stored histogram).
    """
    if not stats:
        return False
    try:
        out_band.SetStatistics(float(stats["min"]), float(stats["max"]),
                               float(stats["mean"]), float(stats["std"]))
    except Exception:
        pass
    counts = stats.get("hist_counts")
    if not counts:
        return False
    try:
        out_band.SetDefaultHistogram(float(stats["min"]), float(stats["max"]), list(counts))
        return True
    except Exception:
        return False


def _stretch_stats_dict(stats):
    """A CIM ``stretchStats`` (StatsHistogram) block from accumulated stats + histogram.

    Returns None if there is no usable histogram (empty / constant raster). Injecting this
    into the output layer file's stretch colorizer makes the Percent Clip render correctly
    immediately, independent of the raster's stored histogram.
    """
    if not stats or "hist_counts" not in stats:
        return None
    nsamp = float(stats["nsamples"])
    densities = [c / nsamp for c in stats["hist_counts"]]
    return {"type": "StatsHistogram",
            "min": stats["min"], "max": stats["max"],
            "mean": stats["mean"], "stddev": stats["std"],
            "limitMin": stats["min"], "limitMax": stats["max"],
            "histogram": densities,
            "nsamples": stats["nsamples"],
            "resolution": stats["resolution"]}


def compute_mahalanobis(vrts, geo, mean, inv_cov, out_type, scratch, messages,
                        value_caps=None):
    """Compute the D2 / D / p-value raster, block by block, into a scratch GeoTIFF.

    Returns ``(scratch_path, stats)`` where ``stats`` is the dataset min/max/mean/std
    accumulated over the valid output cells (or None if every cell was NoData). The caller
    copies the scratch GeoTIFF to the user's output and applies ``stats`` directly. Honors
    the Mask environment.
    """
    gdal = _gdal()
    width, height = geo["width"], geo["height"]
    cell, minx, maxy = geo["cell"], geo["minx"], geo["maxy"]
    k = len(vrts)
    df = k
    mask_vrt, mask_tmp = _mask_vrt(geo, messages)   # keep dataset alive for the loop
    mask_band = mask_vrt.GetRasterBand(1) if mask_vrt is not None else None

    if out_type == OUT_PVALUE:
        from scipy.stats import chi2

    out_path = arcpy.CreateUniqueName("mahal_out.tif", scratch)
    drv = gdal.GetDriverByName("GTiff")
    out_ds = drv.Create(out_path, width, height, 1, gdal.GDT_Float32,
                        options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    out_ds.SetGeoTransform((minx, cell, 0.0, maxy, 0.0, -cell))
    try:
        out_ds.SetProjection(vrts[0].GetProjection())
    except Exception:
        pass
    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(NODATA_OUT)

    bands = [v.GetRasterBand(1) for v in vrts]
    mean = mean.reshape(k, 1)
    stats = _RasterStats()
    strips = list(_row_strips(height, width, k))
    # Determinate progress bar over the block strips (replaces the indeterminate marquee).
    try:
        arcpy.SetProgressor("step", "Computing Mahalanobis values...", 0, len(strips), 1)
    except Exception:
        pass
    for idx, (yoff, rows) in enumerate(strips):
        stack = np.empty((k, rows * width))
        for j, band in enumerate(bands):
            stack[j] = band.ReadAsArray(0, yoff, width, rows).reshape(-1)
        valid = np.all(np.isfinite(stack), axis=0)
        if value_caps:
            # per-cell lost-NoData sentinel screen (see stats_from_categorical)
            for j, cap in value_caps.items():
                with np.errstate(invalid="ignore"):
                    valid &= ~(stack[j] > cap)
        if mask_band is not None:
            mblock = mask_band.ReadAsArray(0, yoff, width, rows).reshape(-1)
            valid = valid & np.isfinite(mblock)
        out = np.full(rows * width, NODATA_OUT, dtype=np.float32)
        if valid.any():
            d = stack[:, valid] - mean                  # (k, m)
            tmp = inv_cov @ d                            # Sigma^-1 (x-mu)
            d2 = np.einsum("ij,ij->j", d, tmp)           # quadratic form -> (m,)
            d2 = np.maximum(d2, 0.0)
            if out_type == OUT_D:
                res = np.sqrt(d2)
            elif out_type == OUT_PVALUE:
                res = chi2.sf(d2, df)
            else:
                res = d2
            out[valid] = res.astype(np.float32)
            stats.add(out[valid])      # record statistics from values we already have
        out_band.WriteArray(out.reshape(rows, width), 0, yoff)
        try:
            arcpy.SetProgressorPosition(idx + 1)
        except Exception:
            pass
    try:
        arcpy.ResetProgressor()
    except Exception:
        pass
    # Release the mask VRT and delete its scratch copy (if one was exported).
    mask_band = None
    mask_vrt = None
    if mask_tmp:
        try:
            arcpy.management.Delete(mask_tmp)
        except Exception:
            pass
    out_band.FlushCache()
    out_ds.FlushCache()
    # Read the histogram off the scratch band (fast, local), then write the stats + histogram
    # into the band's PAM so CopyRaster carries them into the output raster.
    result = _finalize_stats(out_band, stats, messages)
    if result is not None:
        result["on_raster"] = _write_gtiff_stats(out_band, result)
    out_band.FlushCache()
    out_ds.FlushCache()
    out_ds = None      # closing the dataset writes the .aux.xml (PAM) to disk
    return out_path, result


# ---------------------------------------------------------------------------
# Saving statistics tables
# ---------------------------------------------------------------------------
_TABLE_ENTITY = {
    "mean": "Mean vector for a Mahalanobis analysis: the multivariate mean (center) of "
            "the landscape variables across the reference sample.",
    "cov": "Covariance matrix of the landscape variables for a Mahalanobis analysis -- a "
           "square, symmetric matrix whose rows and columns are the variables in the "
           "same order.",
    "invcov": "Inverse covariance matrix for a Mahalanobis analysis (the inverse of the "
              "covariance matrix; square and symmetric).",
    "corr": "Correlation matrix of the landscape variables -- a square, symmetric matrix "
            "whose rows and columns are the variables in the same order.",
}


def _apply_table_field_metadata(out_table, is_vector, kind, messages):
    """Field metadata for a Mahalanobis statistics table. Describes the 'Variable'
    label field (and 'Mean' for the mean vector) but NOT the symmetric matrix value
    columns (Col0, Col1, ...), whose cells stand equally for a row and a column
    variable -- so no single field description (which also implies a row meaning) fits."""
    if is_vector:
        fields = [
            {"name": "Variable", "description": "The landscape variable named in this row."},
            {"name": "Mean", "description": "The mean (average) value of this variable across "
                                            "the reference sample; the center used in the "
                                            "Mahalanobis distance calculation."}]
    else:
        fields = [
            {"name": "Variable",
             "description": "The landscape variable for this row of the matrix. The matrix is "
                            "square: the value columns (Col0, Col1, ...) are the same variables "
                            "in the same order, so each cell is the statistic for this row "
                            "variable paired with that column variable."}]
    tmeta.set_entity_and_fields(out_table, entity=_TABLE_ENTITY.get(kind), fields=fields,
                                messages=messages)


def _matrix_to_table(matrix, out_table, variables, is_vector, source_extra, messages, kind=None):
    """Write a mean vector (is_vector) or a k x k matrix to a new table.

    Embeds the ordered-variable JSON block in the table metadata so the raster tool
    can round-trip it.
    """
    out_ws, out_name = os.path.split(out_table)
    if arcpy.Exists(out_table):
        arcpy.management.Delete(out_table)
    arcpy.management.CreateTable(out_ws, out_name)
    k = len(variables)
    # one AddFields call, not one AddField per field (standing rule)
    if is_vector:
        arcpy.management.AddFields(out_table, [
            ["Variable", "TEXT", "", 128, "", ""],
            ["Mean", "DOUBLE", "", "", "", ""]])
        cols = ["Variable", "Mean"]
        with arcpy.da.InsertCursor(out_table, cols) as ins:
            for i, v in enumerate(variables):
                ins.insertRow([v["name"], float(matrix[i])])
    else:
        value_cols = ["Col{}".format(i) for i in range(k)]
        arcpy.management.AddFields(
            out_table,
            [["Variable", "TEXT", "", 128, "", ""]]
            + [[c, "DOUBLE", "", "", "", ""] for c in value_cols])
        with arcpy.da.InsertCursor(out_table, ["Variable"] + value_cols) as ins:
            for i, v in enumerate(variables):
                ins.insertRow([v["name"]] + [float(x) for x in matrix[i]])
    # Field-level metadata first (it synchronizes), then the dataset summary/description.
    _apply_table_field_metadata(out_table, is_vector, kind, messages)

    try:
        from arcpy import metadata as md
        m = md.Metadata(out_table)
        m.title = out_name
        m.tags = MAHAL_TAGS
        m.description = ("Mahalanobis statistics table.\n"
                         + encode_variable_metadata(variables, source_extra))
        m.save()
    except Exception as e:
        messages.addWarningMessage("Could not write table metadata: {}".format(e))


def save_optional_tables(opts, mean, cov, inv_cov, corr, variables, source_extra,
                         messages):
    """Save whichever of mean/cov/inv-cov/correlation tables the user requested.

    ``opts`` maps {'mean','cov','invcov','corr'} -> output table path or None.
    """
    if opts.get("mean"):
        _matrix_to_table(mean, opts["mean"], variables, True, source_extra, messages, kind="mean")
        messages.addMessage("Saved mean vector table: {}".format(opts["mean"]))
    if opts.get("cov"):
        _matrix_to_table(cov, opts["cov"], variables, False, source_extra, messages, kind="cov")
        messages.addMessage("Saved covariance table: {}".format(opts["cov"]))
    if opts.get("invcov"):
        _matrix_to_table(inv_cov, opts["invcov"], variables, False, source_extra, messages, kind="invcov")
        messages.addMessage("Saved inverse-covariance table: {}".format(opts["invcov"]))
    if opts.get("corr") and corr is not None:
        _matrix_to_table(corr, opts["corr"], variables, False, source_extra, messages, kind="corr")
        messages.addMessage("Saved correlation table: {}".format(opts["corr"]))


# ---------------------------------------------------------------------------
# Output raster metadata
# ---------------------------------------------------------------------------
def _write_raster_metadata(out_raster, variables, stats_source, out_type, n_used):
    from arcpy import metadata as md
    now = datetime.now()
    var_list = ", ".join(v["name"] for v in variables)
    summary = ("Mahalanobis distance raster generated by the Jenness Enterprises "
               "Wildlife Tools on {:%B} {}, {}. {}").format(now, now.day, now.year,
                                                            MAHAL_REFERENCE)
    description = (
        "Per-cell {ot} from a {k}-variable Mahalanobis analysis. "
        "Variables (in order): {vars}. Mean vector and covariance matrix were derived "
        "from: {src}{n}. Each cell value is (x - mu)^T * Sigma^-1 * (x - mu)"
        "{transform}.").format(
            ot=out_type, k=len(variables), vars=var_list, src=stats_source,
            n=("" if n_used is None else " ({:,} samples)".format(n_used)),
            transform=("" if out_type == OUT_D2
                       else ", transformed to " + out_type))
    m = md.Metadata(out_raster)
    m.title = arcpy.Describe(out_raster).baseName
    m.tags = MAHAL_TAGS
    m.summary = summary
    # num_variables = chi-square degrees of freedom; out_type lets a downstream
    # chi-square transform confirm the input is a squared-distance (D2) raster.
    extra = {"num_variables": len(variables), "out_type": out_type}
    m.description = description + "\n" + encode_variable_metadata(variables, extra)
    m.save()


# ---------------------------------------------------------------------------
# Output symbology (blue -> yellow -> red, Percent Clip, full-extent stats)
# ---------------------------------------------------------------------------
def write_raster_layer_file(out_raster, messages, stretch_stats=None, template=None):
    """Create a .lyrx beside the output raster, styled by the stretch template.

    Builds a base layer file carrying the raster's data connection (so it is a usable
    standalone layer file), then splices in the template's stretch colorizer. Returns
    the .lyrx path (also used as the output symbology), or None on failure.

    ``template`` is the .lyrx whose color ramp / scheme to apply (defaults to the blue ->
    yellow -> red ``LAYER_TEMPLATE``; the p-value output passes ``LAYER_TEMPLATE_PVALUE``).
    ``stretch_stats`` (a CIM StatsHistogram dict from ``_stretch_stats_dict``) is injected
    into the colorizer so the Percent Clip stretch has the histogram it needs to compute
    its clip bounds -- the output raster itself need not store one.
    """
    template = template or LAYER_TEMPLATE
    if not os.path.exists(template):
        messages.addWarningMessage("Layer-file template not found; skipping layer file.")
        return None
    try:
        desc = arcpy.Describe(out_raster)
        name = desc.baseName
        ws = desc.path
    except Exception:
        return None
    lyr_dir = lyrx_dir_for(ws)
    if lyr_dir is None:
        return None
    out_lyrx = os.path.join(lyr_dir, name + ".lyrx")
    tmp = "mahal_tmp_raster_layer"

    try:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass
        arcpy.management.MakeRasterLayer(out_raster, tmp)
        if os.path.exists(out_lyrx):      # SaveToLayerFile refuses to overwrite
            os.remove(out_lyrx)
        arcpy.management.SaveToLayerFile(tmp, out_lyrx, "RELATIVE")
    except Exception as e:
        messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    finally:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass

    # Apply the template's ramp + Percent Clip to the output's OWN stretch colorizer,
    # keeping the real stretch statistics that SaveToLayerFile captured (a stretch
    # colorizer with no statistics will not render -- the cause of a blank symbology).
    try:
        with open(template, encoding="utf-8") as f:
            tmpl_col = json.load(f)["layerDefinitions"][0]["colorizer"]
        with open(out_lyrx, encoding="utf-8") as f:
            lyr = json.load(f)
        out_ld = lyr["layerDefinitions"][0]
        out_col = out_ld.get("colorizer")
        if isinstance(out_col, dict) and out_col.get("type") == "CIMRasterStretchColorizer":
            out_col["colorRamp"] = tmpl_col["colorRamp"]
            out_col["stretchType"] = tmpl_col.get("stretchType", out_col.get("stretchType"))
            out_col["statsType"] = tmpl_col.get("statsType", out_col.get("statsType"))
            out_col["minPercent"] = tmpl_col.get("minPercent", 0.5)
            out_col["maxPercent"] = tmpl_col.get("maxPercent", 0.5)
            if "colorScheme" in tmpl_col:
                out_col["colorScheme"] = tmpl_col["colorScheme"]
            if "noDataColor" in tmpl_col:
                out_col["noDataColor"] = tmpl_col["noDataColor"]
            # Replace the stretch statistics with our own (which include the histogram the
            # Percent Clip needs). SaveToLayerFile writes a degenerate stretchStats
            # (histogram [-1]) when the raster stores no histogram -- the blank-stretch bug.
            if stretch_stats:
                out_col["stretchStats"] = stretch_stats
        else:
            out_ld["colorizer"] = tmpl_col          # fallback: use the template wholesale
        out_ld["name"] = name
        with open(out_lyrx, "w", encoding="utf-8") as f:
            json.dump(lyr, f)
    except Exception as e:
        messages.addWarningMessage(
            "Layer file created but symbology could not be applied: {}".format(e))
    return out_lyrx


# ---------------------------------------------------------------------------
# Top-level entry point (called by the .pyt tool)
# ---------------------------------------------------------------------------
def run_mahalanobis_raster(var_rows, sample_method, stats_source, out_type,
                           out_raster, inversion=INV_STANDARD,
                           points=None, use_selected=False,
                           cat_raster=None, cat_field=None, cat_value=None,
                           mean_table=None, cov_table=None,
                           save_opts=None, exclude_bands=None, messages=None):
    """Build a Mahalanobis distance raster. See the tool docstring for parameters.

    ``var_rows`` is a list of (dataset, field) pairs in variable order. A multiband
    raster expands to one variable per band; ``exclude_bands`` (a set of
    (raster_catalogPath_lower, band_number)) drops specific bands.
    """
    scratch = arcpy.env.scratchFolder
    out_raster = resolve_raster_output(out_raster, messages)
    variables = resolve_variables(var_rows, exclude_bands)
    warn_raster_attribute_tables(variables, messages)
    k = len(variables)
    messages.addMessage("{} Mahalanobis variables; statistics source: {}.".format(
        k, stats_source))

    _set_label("Aligning landscape rasters to the analysis grid...")
    vrts, geo = align_predictors(variables, sample_method, scratch, messages)
    _set_label("Computing mean vector and covariance...")

    corr = None
    n_used = None
    if stats_source == SRC_TABLES:
        mean, cov = read_stats_tables(mean_table, cov_table)
        if len(mean) != k:
            raise arcpy.ExecuteError(
                "The mean/covariance tables describe {} variables but {} variables were "
                "selected. They must match in count and order.".format(len(mean), k))
        messages.addMessage("Loaded mean vector and covariance matrix from tables.")
    elif stats_source == SRC_CATEGORICAL:
        cat_values, cat_label = resolve_category_values(
            cat_raster, cat_field, cat_value, messages)
        mean, cov, corr, n_used = stats_from_categorical(
            vrts, geo, cat_raster, cat_values, sample_method, scratch, messages,
            cat_label=cat_label)
        if cat_label:
            stats_source = "{} ({})".format(stats_source, cat_label)
    else:  # SRC_POINTS
        xy = read_point_xy(points, geo["sr"])
        if len(xy) == 0:
            raise arcpy.ExecuteError("No usable sample points were found.")
        samples, n_valid, n_total, n_outside, n_nodata = sample_vrts_at_points(
            vrts, geo, xy, sample_method)
        if n_outside:
            messages.addWarningMessage(
                "{} of {} sample points fell OUTSIDE the extent of one or more landscape "
                "rasters and were excluded from the statistics.".format(n_outside, n_total))
        if n_nodata:
            messages.addWarningMessage(
                "{} sample points were inside the extent but landed on NoData cells of at "
                "least one landscape raster and were excluded.".format(n_nodata))
        if n_valid < k + 1:
            raise arcpy.ExecuteError(
                "Only {} of {} points had valid values for all {} variables; need at "
                "least {}.".format(n_valid, n_total, k, k + 1))
        messages.addMessage(
            "{} of {} sample points were used (valid for all {} variables).".format(
                n_valid, n_total, k))
        mean, cov, corr = stats_from_samples(samples)
        n_used = n_valid

    inv_cov = invert_covariance(cov, inversion, messages)

    if save_opts:
        source_extra = {"source": stats_source,
                        "n": (int(n_used) if n_used is not None else None)}
        save_optional_tables(save_opts, mean, cov, inv_cov, corr, variables,
                             source_extra, messages)

    messages.addMessage("Computing Mahalanobis raster ({})...".format(out_type))
    scratch_tif, out_stats = compute_mahalanobis(
        vrts, geo, mean, inv_cov, out_type, scratch, messages)

    # Save the output, then build statistics and pyramids as separate, labeled phases so
    # the progress bar shows what is happening (instead of keeping the stale "Computing
    # Mahalanobis values..." text). Pyramids are suppressed inside CopyRaster and built
    # explicitly below so the long pyramid phase carries its own "Building pyramids..."
    # label. (These are single GP calls, so the bar is indeterminate during them.)
    _set_label("Saving output raster...")
    prev_pyramid = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        arcpy.management.CopyRaster(scratch_tif, out_raster)
    finally:
        arcpy.env.pyramid = prev_pyramid
    # Statistics + histogram (needed by the Percent Clip stretch) come from the values
    # accumulated during the block loop -- avoiding the CalculateStatistics full-raster scan,
    # which is pathologically slow on file-geodatabase float rasters. When the histogram was
    # carried with the raster (via the scratch GeoTIFF + CopyRaster), do NOT call
    # SetRasterProperties -- it erases the stored histogram. Otherwise set the stats directly,
    # falling back to CalculateStatistics only if that fails.
    _set_label("Setting raster statistics...")
    if out_stats and out_stats.get("on_raster"):
        messages.addMessage(
            "Statistics and histogram were written with the raster (no scan needed).")
    elif not _apply_known_statistics(out_raster, out_stats, messages):
        try:
            arcpy.management.CalculateStatistics(out_raster)
        except Exception as e:
            messages.addWarningMessage("Could not calculate raster statistics: {}".format(e))
    try:
        _write_raster_metadata(out_raster, variables, stats_source, out_type, n_used)
    except Exception as e:
        messages.addWarningMessage("Could not write raster metadata: {}".format(e))
    try:
        arcpy.management.Delete(scratch_tif)
    except Exception:
        pass
    _set_label("Building pyramids...")
    try:
        arcpy.management.BuildPyramids(out_raster)
    except Exception as e:
        messages.addWarningMessage("Could not build pyramids: {}".format(e))
    messages.addMessage("Mahalanobis raster complete: {}".format(out_raster))
    # Build the Percent Clip layer file; returned as output symbology. The p-value output
    # uses the yellow->green->dark-blue ramp, the D2/D outputs the blue->yellow->red ramp.
    _set_label("Creating layer file and symbology...")
    template = LAYER_TEMPLATE_PVALUE if out_type == OUT_PVALUE else LAYER_TEMPLATE
    return write_raster_layer_file(out_raster, messages,
                                   stretch_stats=_stretch_stats_dict(out_stats),
                                   template=template)


# ---------------------------------------------------------------------------
# Mahalanobis distance at point locations
# ---------------------------------------------------------------------------
# Output field name per output-value choice (kept <= 10 chars for shapefile safety).
POINT_FIELD = {OUT_D2: "Mahal_D2", OUT_D: "Mahal_D", OUT_PVALUE: "Mahal_pval"}


def _unique_field(fc, base):
    """A field name based on ``base`` that is valid for fc's workspace and unused."""
    ws = os.path.dirname(fc)
    try:
        base = arcpy.ValidateFieldName(base, ws)
    except Exception:
        pass
    existing = {f.name.lower() for f in arcpy.ListFields(fc)}
    if base.lower() not in existing:
        return base
    i = 1
    while True:
        cand = "{}_{}".format(base[:8], i)
        try:
            cand = arcpy.ValidateFieldName(cand, ws)
        except Exception:
            pass
        if cand.lower() not in existing:
            return cand
        i += 1


_POINT_FIELD_DESC = {
    OUT_D2: "Squared Mahalanobis distance from this point's landscape-variable values to "
            "the multivariate mean of the reference sample: D2 = (x - mu)' * Sigma^-1 * "
            "(x - mu).",
    OUT_D: "Mahalanobis distance: the square root of the squared distance (D2).",
    OUT_PVALUE: "Chi-square p-value -- the upper-tail probability P(chi-square "
                "with k degrees of freedom > D2), from 0 to 1; values near 1 indicate "
                "conditions close to the reference mean.",
}


def _write_points_metadata(out_points, variables, stats_source, fields, n_used):
    from arcpy import metadata as md
    # Field-level metadata first (it synchronizes), then the dataset summary/description.
    tmeta.set_entity_and_fields(
        out_points,
        entity="Input point features with per-point Mahalanobis distance values from a "
               "multivariate habitat (Mahalanobis) analysis.",
        fields=[{"name": f, "description": _POINT_FIELD_DESC.get(t, "Mahalanobis value.")}
                for t, f in fields])
    now = datetime.now()
    var_list = ", ".join(v["name"] for v in variables)
    summary = ("Mahalanobis distance values at point locations, generated by the Jenness "
               "Enterprises Wildlife Tools on {:%B} {}, {}. {}").format(
                   now, now.day, now.year, MAHAL_REFERENCE)
    field_descr = "; ".join("'{}' = {}".format(f, t) for t, f in fields)
    description = (
        "Per-point Mahalanobis values from a {k}-variable analysis ({fd}). "
        "Variables (in order): {vars}. Mean vector and covariance matrix were derived from: "
        "{src}{n}. Each value derives from the squared distance D2 = "
        "(x - mu)^T * Sigma^-1 * (x - mu); points outside the data extent or on NoData "
        "are null.").format(
            k=len(variables), fd=field_descr, vars=var_list, src=stats_source,
            n=("" if n_used is None else " ({:,} samples)".format(n_used)))
    m = md.Metadata(out_points)
    m.title = arcpy.Describe(out_points).baseName
    m.tags = MAHAL_TAGS
    m.summary = summary
    m.description = description + "\n" + encode_variable_metadata(variables)
    m.save()


def run_mahalanobis_points(target_points, var_rows, sample_method, stats_source, out_types,
                           out_points, inversion=INV_STANDARD,
                           stats_points=None, use_selected=False,
                           cat_raster=None, cat_field=None, cat_value=None,
                           mean_table=None, cov_table=None,
                           save_opts=None, exclude_bands=None, messages=None):
    """Compute one or more Mahalanobis values at each target point.

    ``out_types`` is an iterable of OUT_D2 / OUT_D / OUT_PVALUE (a bare string is also
    accepted). The output is a copy of ``target_points`` with one DOUBLE field per requested
    value (Mahal_D2 / Mahal_D / Mahal_pval); points outside the landscape data are null.
    The mean vector and covariance come from the same three sources as the raster tool.
    ``exclude_bands`` drops specific bands of a multiband raster (see resolve_variables).
    """
    if isinstance(out_types, str):
        out_types = [out_types]
    selected = [t for t in (OUT_D2, OUT_D, OUT_PVALUE) if t in out_types]
    if not selected:
        raise arcpy.ExecuteError("Select at least one output value (D2, D, or p-value).")
    scratch = arcpy.env.scratchFolder
    variables = resolve_variables(var_rows, exclude_bands)
    warn_raster_attribute_tables(variables, messages)
    k = len(variables)
    messages.addMessage("{} Mahalanobis variables; statistics source: {}.".format(
        k, stats_source))

    _set_label("Aligning landscape rasters to the analysis grid...")
    vrts, geo = align_predictors(variables, sample_method, scratch, messages)
    _set_label("Computing mean vector and covariance...")

    corr = None
    n_used = None
    if stats_source == SRC_TABLES:
        mean, cov = read_stats_tables(mean_table, cov_table)
        if len(mean) != k:
            raise arcpy.ExecuteError(
                "The mean/covariance tables describe {} variables but {} variables were "
                "selected. They must match in count and order.".format(len(mean), k))
        messages.addMessage("Loaded mean vector and covariance matrix from tables.")
    elif stats_source == SRC_CATEGORICAL:
        cat_values, cat_label = resolve_category_values(
            cat_raster, cat_field, cat_value, messages)
        mean, cov, corr, n_used = stats_from_categorical(
            vrts, geo, cat_raster, cat_values, sample_method, scratch, messages,
            cat_label=cat_label)
        if cat_label:
            stats_source = "{} ({})".format(stats_source, cat_label)
    else:  # SRC_POINTS -- statistics from a point sample (defaults to the target points)
        sp = stats_points or target_points
        xy = read_point_xy(sp, geo["sr"])
        if len(xy) == 0:
            raise arcpy.ExecuteError("No usable statistics sample points were found.")
        samples, n_valid, n_total, n_outside, n_nodata = sample_vrts_at_points(
            vrts, geo, xy, sample_method)
        if n_outside:
            messages.addWarningMessage(
                "{} of {} statistics points fell outside the extent of one or more landscape "
                "rasters and were excluded.".format(n_outside, n_total))
        if n_nodata:
            messages.addWarningMessage(
                "{} statistics points landed on NoData cells and were excluded.".format(n_nodata))
        if n_valid < k + 1:
            raise arcpy.ExecuteError(
                "Only {} of {} statistics points were valid for all {} variables; need at "
                "least {}.".format(n_valid, n_total, k, k + 1))
        messages.addMessage("{} statistics points were used.".format(n_valid))
        mean, cov, corr = stats_from_samples(samples)
        n_used = n_valid

    inv_cov = invert_covariance(cov, inversion, messages)

    if save_opts:
        source_extra = {"source": stats_source,
                        "n": (int(n_used) if n_used is not None else None)}
        save_optional_tables(save_opts, mean, cov, inv_cov, corr, variables,
                             source_extra, messages)

    # Output = copy of the target points + one Mahalanobis value field per requested type.
    messages.addMessage("Calculating Mahalanobis values at target points ({})...".format(
        ", ".join(selected)))
    arcpy.management.CopyFeatures(target_points, out_points)
    fields = []  # list of (out_type, field name), in canonical order
    for t in selected:
        f = _unique_field(out_points, POINT_FIELD[t])
        fields.append((t, f))
    # one AddFields call, not one AddField per field (standing rule)
    arcpy.management.AddFields(out_points, [
        [f, "DOUBLE", "", "", "", ""] for _t, f in fields])

    df = k
    if OUT_PVALUE in selected:
        from scipy.stats import chi2
    bands = [v.GetRasterBand(1) for v in vrts]
    interp = (sample_method == SAMPLE_INTERP)
    n_val = n_null = 0
    try:
        n_total = int(arcpy.management.GetCount(out_points).getOutput(0))
        arcpy.SetProgressor("step", "Calculating Mahalanobis values at points...",
                            0, max(n_total, 1), 1)
    except Exception:
        pass
    cols = ["SHAPE@XY"] + [f for _t, f in fields]
    with arcpy.da.UpdateCursor(out_points, cols, spatial_reference=geo["sr"]) as cur:
        for row in cur:
            try:
                arcpy.SetProgressorPosition(n_val + n_null + 1)
            except Exception:
                pass
            xy = row[0]
            vals = None
            if xy is not None and xy[0] is not None:
                vec = sample_point_vector(bands, geo, xy[0], xy[1], interp)
                if np.all(np.isfinite(vec)):
                    d = vec - mean
                    d2 = max(float(d @ inv_cov @ d), 0.0)
                    vals = []
                    for t in selected:
                        if t == OUT_D:
                            vals.append(float(np.sqrt(d2)))
                        elif t == OUT_PVALUE:
                            vals.append(float(chi2.sf(d2, df)))
                        else:
                            vals.append(d2)
            if vals is None:
                for i in range(len(selected)):
                    row[i + 1] = None
                n_null += 1
            else:
                for i, v in enumerate(vals):
                    row[i + 1] = v
                n_val += 1
            cur.updateRow(row)
    try:
        arcpy.ResetProgressor()
    except Exception:
        pass
    messages.addMessage(
        "Computed {} point values into field(s) {}; {} point(s) outside the data or on "
        "NoData were set to null.".format(
            n_val, ", ".join("'{}'".format(f) for _t, f in fields), n_null))

    try:
        _write_points_metadata(out_points, variables, stats_source, fields, n_used)
    except Exception as e:
        messages.addWarningMessage("Could not write output metadata: {}".format(e))
    messages.addMessage("Mahalanobis at points complete: {}".format(out_points))
    return out_points


# ---------------------------------------------------------------------------
# Statistics tables from datasets (mean / covariance / inverse / correlation)
# ---------------------------------------------------------------------------
def run_covariance_from_datasets(var_rows, sample_method, stats_source, out_tables,
                                 inversion=INV_STANDARD,
                                 points=None, use_selected=False,
                                 cat_raster=None, cat_field=None, cat_value=None,
                                 exclude_bands=None, messages=None):
    """Compute mean / covariance / inverse-covariance / correlation tables for a set
    of landscape variables, writing whichever tables were requested.

    ``out_tables`` maps {'mean','cov','invcov','corr'} -> output table path; only the
    requested keys are present. Each table embeds the ordered-variable metadata so the
    Mahalanobis Raster / at Points tools can consume the mean + covariance pair through
    their "Existing tables" source. Statistics come from sample points or a categorical
    raster (not existing tables -- that would be circular). ``exclude_bands`` drops
    specific bands of a multiband raster (see resolve_variables). Returns the list of
    written table paths.
    """
    if not out_tables:
        raise arcpy.ExecuteError(
            "Specify at least one output table (mean, covariance, inverse covariance, "
            "or correlation).")
    scratch = arcpy.env.scratchFolder
    variables = resolve_variables(var_rows, exclude_bands)
    warn_raster_attribute_tables(variables, messages)
    k = len(variables)
    messages.addMessage("{} Mahalanobis variables; statistics source: {}.".format(
        k, stats_source))

    _set_label("Aligning landscape rasters to the analysis grid...")
    vrts, geo = align_predictors(variables, sample_method, scratch, messages)
    _set_label("Computing mean vector and covariance...")

    corr = None
    n_used = None
    if stats_source == SRC_CATEGORICAL:
        cat_values, cat_label = resolve_category_values(
            cat_raster, cat_field, cat_value, messages)
        mean, cov, corr, n_used = stats_from_categorical(
            vrts, geo, cat_raster, cat_values, sample_method, scratch, messages,
            cat_label=cat_label)
        if cat_label:
            stats_source = "{} ({})".format(stats_source, cat_label)
    else:  # SRC_POINTS
        xy = read_point_xy(points, geo["sr"])
        if len(xy) == 0:
            raise arcpy.ExecuteError("No usable sample points were found.")
        samples, n_valid, n_total, n_outside, n_nodata = sample_vrts_at_points(
            vrts, geo, xy, sample_method)
        if n_outside:
            messages.addWarningMessage(
                "{} of {} sample points fell OUTSIDE the extent of one or more landscape "
                "rasters and were excluded from the statistics.".format(n_outside, n_total))
        if n_nodata:
            messages.addWarningMessage(
                "{} sample points were inside the extent but landed on NoData cells of at "
                "least one landscape raster and were excluded.".format(n_nodata))
        if n_valid < k + 1:
            raise arcpy.ExecuteError(
                "Only {} of {} points had valid values for all {} variables; need at "
                "least {}.".format(n_valid, n_total, k, k + 1))
        messages.addMessage(
            "{} of {} sample points were used (valid for all {} variables).".format(
                n_valid, n_total, k))
        mean, cov, corr = stats_from_samples(samples)
        n_used = n_valid

    # The inverse is only needed -- and only worth risking a singular-matrix failure for --
    # when the inverse-covariance table was actually requested.
    inv_cov = None
    if out_tables.get("invcov"):
        inv_cov = invert_covariance(cov, inversion, messages)

    _set_label("Saving statistics tables...")
    source_extra = {"source": stats_source,
                    "n": (int(n_used) if n_used is not None else None)}
    save_optional_tables(out_tables, mean, cov, inv_cov, corr, variables,
                         source_extra, messages)
    written = [out_tables[key] for key in ("mean", "cov", "invcov", "corr")
               if out_tables.get(key)]
    messages.addMessage("Statistics tables complete: {}".format(", ".join(written)))
    return written


# ---------------------------------------------------------------------------
# Chi-square / p-value transform of an existing squared-distance (D2) raster
# ---------------------------------------------------------------------------
def read_recorded_df(in_raster):
    """Read (num_variables, out_type) from a raster's embedded metadata block.

    Rasters created by the Mahalanobis Raster tool record ``num_variables`` (the
    chi-square degrees of freedom) and ``out_type``. Returns (df, out_type) with either
    element None when absent. Never raises.
    """
    meta = decode_variable_metadata(in_raster)
    if not meta:
        return None, None
    n = meta.get("num_variables")
    try:
        n = int(n) if n is not None else None
    except Exception:
        n = None
    return n, meta.get("out_type")


def _write_chi_metadata(in_raster, out_raster, df):
    from arcpy import metadata as md
    now = datetime.now()
    try:
        src_name = arcpy.Describe(in_raster).baseName
    except Exception:
        src_name = str(in_raster)
    summary = ("Chi-square p-value raster generated by the Jenness Enterprises "
               "Wildlife Tools on {:%B} {}, {}. {}").format(now, now.day, now.year,
                                                            MAHAL_REFERENCE)
    description = (
        "Per-cell chi-square p-value transformed from the squared Mahalanobis distance "
        "raster '{src}', using df = {df} degrees of freedom. Each cell is the upper-tail "
        "probability P(chi-square_{df} > D2): a 0 to 1 similarity score where values near 1 "
        "indicate a close match to the reference conditions. This tool uses the "
        "statistically standard df = number of variables; some ecological sources (Clark "
        "et al. 1993; Farber and Kadmon 2003) use number of variables minus 1, which only "
        "rescales the p-values because the transform is monotonic in D2.").format(
            src=src_name, df=df)
    # Carry forward the source variable list (if recorded) and stamp this as a p-value
    # raster so a downstream transform can recognize it should NOT be re-transformed.
    src_meta = decode_variable_metadata(in_raster) or {}
    src_vars = src_meta.get("variables") or []
    variables = [{"name": v.get("name", ""), "path": v.get("path", "")} for v in src_vars]
    extra = {"out_type": OUT_PVALUE, "chi_square_df": int(df)}
    m = md.Metadata(out_raster)
    m.title = arcpy.Describe(out_raster).baseName
    m.tags = MAHAL_TAGS
    m.summary = summary
    m.description = description + "\n" + encode_variable_metadata(variables, extra)
    m.save()


def run_chi_square_transform(in_raster, df, out_raster, messages=None):
    """Transform a squared-distance (D2) raster into a chi-square p-value raster.

    For every cell, p = P(chi-square_df > D2) = ``scipy.stats.chi2.sf(D2, df)``. Computed
    block by block (memory-bounded); NoData and negative inputs are preserved as NoData /
    upper-tail 1.0 respectively. ``df`` defaults (in the tool) to the number of variables
    recorded in the input raster's metadata. Returns the output .lyrx (symbology) or None.
    """
    from scipy.stats import chi2
    gdal = _gdal()
    out_raster = resolve_raster_output(out_raster, messages)
    df = int(df)
    if df < 1:
        raise arcpy.ExecuteError(
            "Degrees of freedom must be a positive integer (the number of variables).")

    scratch = arcpy.env.scratchFolder
    # GDAL cannot open every arcpy raster by path (file-geodatabase rasters, mosaics,
    # in-memory layers); _gdal_source_path exports to a scratch GeoTIFF when needed.
    in_path, in_copy = _gdal_source_path(in_raster, scratch)
    try:
        src = gdal.Open(in_path)
    except RuntimeError:
        src = None
    if src is None:
        raise arcpy.ExecuteError("Could not open the input raster: {}".format(in_raster))
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    in_nodata = band.GetNoDataValue()

    out_path = arcpy.CreateUniqueName("mahal_chi.tif", scratch)
    drv = gdal.GetDriverByName("GTiff")
    out_ds = drv.Create(out_path, width, height, 1, gdal.GDT_Float32,
                        options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    out_ds.SetGeoTransform(src.GetGeoTransform())
    try:
        out_ds.SetProjection(src.GetProjection())
    except Exception:
        pass
    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(NODATA_OUT)

    messages.addMessage(
        "Transforming D2 raster to chi-square p-values (df = {}).".format(df))
    _set_label("Computing chi-square p-values...")
    stats = _RasterStats()
    strips = list(_row_strips(height, width, 1))
    try:
        arcpy.SetProgressor("step", "Computing chi-square p-values...", 0, len(strips), 1)
    except Exception:
        pass
    for idx, (yoff, rows) in enumerate(strips):
        block = band.ReadAsArray(0, yoff, width, rows).astype(np.float64)
        out = np.full((rows, width), NODATA_OUT, dtype=np.float32)
        valid = np.isfinite(block)
        if in_nodata is not None:
            valid &= (block != in_nodata)
        valid = mask_band_valid(band, valid, 0, yoff)
        if valid.any():
            d2 = np.maximum(block[valid], 0.0)
            out[valid] = chi2.sf(d2, df).astype(np.float32)
            stats.add(out[valid])      # record statistics from values we already have
        out_band.WriteArray(out, 0, yoff)
        try:
            arcpy.SetProgressorPosition(idx + 1)
        except Exception:
            pass
    try:
        arcpy.ResetProgressor()
    except Exception:
        pass
    out_band.FlushCache()
    out_ds.FlushCache()
    # Read the histogram, then write stats + histogram into the band's PAM so CopyRaster
    # carries them into the output raster.
    out_stats = _finalize_stats(out_band, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _write_gtiff_stats(out_band, out_stats)
    out_band.FlushCache()
    out_ds.FlushCache()
    src = None
    out_ds = None      # closing the dataset writes the .aux.xml (PAM) to disk

    # Save / stats / metadata / pyramids / symbology -- mirror run_mahalanobis_raster.
    _set_label("Saving output raster...")
    prev_pyramid = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        arcpy.management.CopyRaster(out_path, out_raster)
    finally:
        arcpy.env.pyramid = prev_pyramid
    # Statistics + histogram from the accumulated p-values. If the histogram was carried with
    # the raster, skip SetRasterProperties (it would erase the histogram); otherwise set the
    # stats directly, falling back to the scan only if that fails.
    _set_label("Setting raster statistics...")
    if out_stats and out_stats.get("on_raster"):
        messages.addMessage(
            "Statistics and histogram were written with the raster (no scan needed).")
    elif not _apply_known_statistics(out_raster, out_stats, messages):
        try:
            arcpy.management.CalculateStatistics(out_raster)
        except Exception as e:
            messages.addWarningMessage("Could not calculate raster statistics: {}".format(e))
    try:
        _write_chi_metadata(in_raster, out_raster, df)
    except Exception as e:
        messages.addWarningMessage("Could not write raster metadata: {}".format(e))
    try:
        arcpy.management.Delete(out_path)
    except Exception:
        pass
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass
    _set_label("Building pyramids...")
    try:
        arcpy.management.BuildPyramids(out_raster)
    except Exception as e:
        messages.addWarningMessage("Could not build pyramids: {}".format(e))
    messages.addMessage("Chi-square p-value raster complete: {}".format(out_raster))
    _set_label("Creating layer file and symbology...")
    return write_raster_layer_file(out_raster, messages,
                                   stretch_stats=_stretch_stats_dict(out_stats),
                                   template=LAYER_TEMPLATE_PVALUE)
