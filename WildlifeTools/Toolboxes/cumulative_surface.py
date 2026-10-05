# -*- coding: utf-8 -*-
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

"""Cumulative Surface -- how many input layers occur on each cell?

A modernized port of the CorridorDesigner Evaluation Tools' Cumulative
Surface Tool (``clsCombineAreas.cls`` / ``LinkagesFunctions2.CombineLayers``;
manual pp. 101-105). Any mix of POLYGON layers and RASTERS goes in together;
the output is a single integer raster whose cell value counts how many of
those layers are PRESENT at that cell -- a way of estimating the relative
importance (or the number of species served) of preserving that ground. It
was built to combine patch polygons and corridor rasters, but combines any
polygon/raster mix.

PRESENCE, exactly as the original defined it:

* A RASTER is present where it holds a value that is neither NoData NOR ZERO.
  The original ran every raster through ``SetNull(value == 0)`` first, so a
  corridor raster's 0 background never counts as presence. Kept deliberately
  (Jeff, 2026-08-20) -- but worth knowing when combining arbitrary rasters
  whose 0 is meaningful.
* A POLYGON layer is present where any of its (optionally: selected) polygons
  covers the cell center -- the same cell-center test Polygon To Raster uses.
  Each LAYER counts at most once per cell no matter how many of its polygons
  overlap there; that is the difference from Esri's Count Overlapping
  Features, which counts features.
* With "use only selected polygons" on, a polygon layer that has NO selection
  contributes ALL its polygons -- the original's documented fallback, kept.

THE GRID, exactly as the original built it: every raster input must share
one coordinate system (the tool refuses otherwise -- resampling between
projections would silently shift presence); polygon layers may be in any
KNOWN coordinate system and are projected on the fly. The output uses the
first raster's coordinate system (or the first layer's, when no rasters),
a cell size equal to the SMALLEST cell size of the input rasters (or 1/1000
of the longest extent edge when all inputs are polygons -- overridable), and
the UNION of every input's extent. This port additionally snaps the grid to
the smallest-cell raster's own cell origin so raster presence never shifts
by a half cell.

The output is a small integer raster with a value attribute table
(Value / Count / Class_Name) and a ready legend: an optional label word
(e.g. "Species") appended to each class ("3 Species"), 0 drawn white, and
the counts shaded along the color-blind-friendly VIRIDIS ramp -- dark
violet (fewest layers) through teal to bright yellow (most), replacing the
original's red-green-confusable red-yellow-green at Jeff's direction.
Optionally cells where NO layer occurs become NoData instead of 0.

Needs no Spatial Analyst: polygons are rasterized with GDAL and the counting
is a plain numpy sum.
"""
import math
import os

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import kernel_density as _kd
import clip_tools as _ct
import hsm_tools as _hsm
import tpi_tools as _tpi
import land_facet_linkage as _lnk

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "CorridorDesigner: ArcGIS tools for designing and evaluating corridors. "
    "http://corridordesign.org.")

# Legend colors. The original shaded red -> yellow -> green -- the classic
# red-green color-blindness confusion pair -- so this port uses the viridis
# ramp instead (Jeff, 2026-08-20): perceptually ordered, color-blind safe,
# dark violet (fewest layers) through teal to bright yellow (most). 0 stays
# white, as in the original.
_WHITE = (255, 255, 255)
_VIRIDIS = ((68, 1, 84),      # 0.00  dark violet
            (59, 82, 139),    # 0.25  blue
            (33, 145, 140),   # 0.50  teal
            (94, 201, 98),    # 0.75  green
            (253, 231, 37))   # 1.00  bright yellow
_TEAL = _VIRIDIS[2]

# Cells in either output type that mean "outside the analysis, no answer".
_NODATA_BYTE = 255
_NODATA_INT = -1

# A friendly ceiling long before numpy MemoryErrors get cryptic.
_MAX_CELLS = 1_600_000_000


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def _lerp(c0, c1, t):
    return tuple(int(round(c0[i] + (c1[i] - c0[i]) * t)) for i in range(3))


def ramp_color(i, n):
    """The legend color for the ``i``-th (0-based) of ``n`` NONZERO count
    values, low to high along the viridis ramp: dark violet = fewest
    layers, bright yellow = most. A single nonzero value draws in the
    mid-ramp teal (readable alone against the white 0 class, where either
    ramp end would be too dark or too pale)."""
    if n <= 1:
        return _TEAL
    t = i / (n - 1.0)
    seg = min(int(t * 4), 3)
    return _lerp(_VIRIDIS[seg], _VIRIDIS[seg + 1], t * 4 - seg)


def class_label(value, legend_label):
    """The legend text for one count value -- the count plus the optional
    label word, e.g. ``3 Species`` (the original prefixed a space)."""
    word = (legend_label or "").strip()
    return "{}{}".format(value, " " + word if word else "")


# ---------------------------------------------------------------------------
#  Input inspection
# ---------------------------------------------------------------------------
def _classify_inputs(datasets, messages):
    """Split the mixed multivalue into rasters and polygon inputs, in the
    user's order. Returns ``[(kind, original_value, describe, path, name)]``
    with kind 'raster' or 'polygons'. Errors on empty input, a non-polygon
    feature class, an unknown coordinate system, or rasters in different
    coordinate systems (the original refused those too)."""
    import arcpy

    items = [d for d in (datasets or []) if d not in (None, "")]
    if not items:
        raise arcpy.ExecuteError("No input layers were given.")
    out = []
    first_raster_sr = None
    first_raster_name = None
    for ds in items:
        d, path, name = _ct._describe(ds)
        sr = getattr(d, "spatialReference", None)
        if sr is None or getattr(sr, "name", "Unknown") == "Unknown":
            raise arcpy.ExecuteError(
                "{}: the coordinate system is unknown. Every input must "
                "have a defined coordinate system (the original tool "
                "required this too -- presence cannot be placed on the "
                "shared grid otherwise).".format(name))
        if _ct._is_raster(d):
            if first_raster_sr is None:
                first_raster_sr, first_raster_name = sr, name
            elif not _kd._same_cs(first_raster_sr, sr):
                raise arcpy.ExecuteError(
                    "All raster inputs must share one coordinate system, "
                    "but {} ({}) differs from {} ({}). Project the rasters "
                    "to a common coordinate system first (Clip Data to "
                    "Analysis Area can batch-project them).".format(
                        name, sr.name, first_raster_name,
                        first_raster_sr.name))
            out.append(("raster", ds, d, path, name))
        else:
            shp = getattr(d, "shapeType", None)
            if shp != "Polygon":
                raise arcpy.ExecuteError(
                    "{}: only POLYGON feature classes and rasters can be "
                    "combined (this input is {}).".format(
                        name, shp or getattr(d, "dataType", "not a feature "
                                             "class")))
            out.append(("polygons", ds, d, path, name))
    return out


def _dataset_extent(d, path):
    """A dataset's extent, never None: Describe's cached extent first; a
    fresh Describe of the catalog path when that is missing (seen 2026-09-13
    on feature classes written moments earlier in the same session); and,
    as the last resort, the union of the features' own envelopes."""
    import arcpy

    ext = getattr(d, "extent", None)
    if ext is None and path:
        try:
            ext = arcpy.Describe(path).extent
        except Exception:                                        # noqa: BLE001
            ext = None
    if ext is None and path:
        xmin = ymin = xmax = ymax = None
        sr = getattr(d, "spatialReference", None)
        with arcpy.da.SearchCursor(path, ["SHAPE@"]) as cur:
            for (g,) in cur:
                if g is None:
                    continue
                e = g.extent
                xmin = e.XMin if xmin is None else min(xmin, e.XMin)
                ymin = e.YMin if ymin is None else min(ymin, e.YMin)
                xmax = e.XMax if xmax is None else max(xmax, e.XMax)
                ymax = e.YMax if ymax is None else max(ymax, e.YMax)
        if xmin is not None:
            ext = arcpy.Extent(xmin, ymin, xmax, ymax, spatial_reference=sr)
    if ext is None:
        raise arcpy.ExecuteError(
            "{}: the extent of this input could not be determined (it may "
            "be empty).".format(os.path.basename(str(path))))
    return ext


def _projected_extent(d, out_sr, path=None):
    """A dataset's extent as (xmin, ymin, xmax, ymax) in ``out_sr`` --
    projected through the densified extent polygon, so a curved projection
    edge cannot under-cover the true footprint."""
    import arcpy

    ext = _dataset_extent(d, path)
    sr = getattr(ext, "spatialReference", None) or d.spatialReference
    if sr is None or _kd._same_cs(sr, out_sr):
        return ext.XMin, ext.YMin, ext.XMax, ext.YMax
    poly = arcpy.Polygon(
        arcpy.Array([arcpy.Point(ext.XMin, ext.YMin),
                     arcpy.Point(ext.XMin, ext.YMax),
                     arcpy.Point(ext.XMax, ext.YMax),
                     arcpy.Point(ext.XMax, ext.YMin),
                     arcpy.Point(ext.XMin, ext.YMin)]), sr)
    step = max(ext.width, ext.height) / 100.0
    if step > 0:
        try:
            poly = poly.densify("DISTANCE", step, step)
        except Exception:                                        # noqa: BLE001
            pass
    pext = _ct.project_geometry(poly, out_sr).extent
    return pext.XMin, pext.YMin, pext.XMax, pext.YMax


def _build_grid(inputs, out_sr, cell_size, opened, messages):
    """The shared analysis grid: ``(gt, width, height, csx, csy, note)``.

    Cell size = the user's override, else the smallest raster cell, else
    1/1000 of the union extent's longest edge (the original's rules). The
    union extent covers every input; with rasters present the grid is
    additionally snapped to the smallest-cell raster's own cell origin.
    ``opened`` is the orchestrator's ``{path: (dataset, tmp)}`` cache --
    rasters are opened ONCE for the whole run (the old open-read-delete-
    reopen doubled a whole-raster scratch export on formats GDAL can only
    reach through the CopyRaster fallback)."""
    import arcpy

    xmin = ymin = xmax = ymax = None
    best = None          # (cell, origin_x, origin_y) of the smallest-cell raster
    for kind, ds, d, path, name in inputs:
        if kind == "raster":
            src = opened[path][0]
            gt = src.GetGeoTransform()
            w, h = src.RasterXSize, src.RasterYSize
            cx, cy = abs(gt[1]), abs(gt[5])
            x0, y0 = gt[0], gt[3]
            x1, y1 = x0 + gt[1] * w, y0 + gt[5] * h
            exmin, exmax = min(x0, x1), max(x0, x1)
            eymin, eymax = min(y0, y1), max(y0, y1)
            if best is None or min(cx, cy) < min(best[0]):
                best = ((cx, cy), x0, y0)
        else:
            exmin, eymin, exmax, eymax = _projected_extent(d, out_sr, path)
        xmin = exmin if xmin is None else min(xmin, exmin)
        ymin = eymin if ymin is None else min(ymin, eymin)
        xmax = exmax if xmax is None else max(xmax, exmax)
        ymax = eymax if ymax is None else max(ymax, eymax)

    if not (xmax > xmin and ymax > ymin):
        raise arcpy.ExecuteError("The combined extent of the inputs is "
                                 "empty.")

    if cell_size and float(cell_size) > 0:
        csx = csy = float(cell_size)
        note = "cell size {} (user override)".format(csx)
    elif best is not None:
        csx, csy = best[0]
        note = "cell size {:g} (the smallest input raster's)".format(
            min(csx, csy))
    else:
        csx = csy = max(xmax - xmin, ymax - ymin) / 1000.0
        note = ("cell size {:g} (1/1000 of the longest extent edge -- no "
                "raster inputs)".format(csx))

    if best is not None:
        # Snap outward to the smallest-cell raster's own cell edges so its
        # presence never shifts by a fraction of a cell.
        ox, oy = best[1], best[2]
        xmin = ox + math.floor((xmin - ox) / csx) * csx
        ymax = oy - math.floor((oy - ymax) / csy) * csy
    width = max(1, int(math.ceil((xmax - xmin) / csx - 1e-9)))
    height = max(1, int(math.ceil((ymax - ymin) / csy - 1e-9)))
    if width * height > _MAX_CELLS:
        raise arcpy.ExecuteError(
            "The output grid would be {:,} x {:,} cells ({:,} total) -- too "
            "large to hold in memory. Give a larger cell size.".format(
                width, height, width * height))
    gt = (xmin, csx, 0.0, ymax, 0.0, -csy)
    return gt, width, height, csx, csy, note


# ---------------------------------------------------------------------------
#  Presence layers
# ---------------------------------------------------------------------------
def _raster_presence(counts, src, path, gt, width, height, messages):
    """Add one raster's presence (value neither NoData nor zero) to
    ``counts`` on the shared grid, nearest-neighbor. ``src`` is the
    already-open GDAL dataset (opened once per run). Returns the number of
    present cells."""
    import arcpy

    gdal = _mh._gdal()
    xmin, csx = gt[0], gt[1]
    ymax, csy = gt[3], -gt[5]
    # A warped VRT resamples lazily, so only one strip is ever
    # materialized. LIFETIME RULE (see the GDAL traps memory): the
    # source dataset must stay referenced until the VRT is released,
    # else reads silently return NaN -- the caller owns ``src`` and keeps
    # it alive past this call.
    vrt = gdal.Warp(
        "", src, format="VRT",
        outputBounds=(xmin, ymax - csy * height,
                      xmin + csx * width, ymax),
        width=width, height=height, resampleAlg="near",
        outputType=gdal.GDT_Float64, dstNodata=float("nan"))
    if vrt is None:
        raise arcpy.ExecuteError(
            "Could not align {} to the analysis grid.".format(path))
    n_present = 0
    try:
        slab = max(1, int(8_000_000 // max(1, width)))
        for r0 in range(0, height, slab):
            nr = min(slab, height - r0)
            a = vrt.GetRasterBand(1).ReadAsArray(0, r0, width, nr)
            with np.errstate(invalid="ignore"):
                pres = np.isfinite(a) & (a != 0.0)
            counts[r0:r0 + nr][pres] += 1
            n_present += int(pres.sum())
            del a, pres
    finally:
        vrt = None
    return n_present


def _polygon_geoms(ds, path, name, use_selected, out_sr, densify_step,
                   messages, pin=None, dq=None):
    """One polygon input's geometries, projected to ``out_sr`` and
    true-curve densified. With ``use_selected`` the map layer's selection
    is honored via ``pin`` -- its selected OIDs, captured at RUN ENTRY (a
    live layer's selection can be dropped while earlier inputs are
    processed; standing rule, see ``mahalanobis.pin_selection``). An
    absent/empty selection falls back to ALL polygons, the original's
    rule. ``dq`` is the layer's definition query, also captured at run
    entry: the polygons are read through the catalog path, which knows
    nothing of the layer, so the query rides along as the cursor's where
    clause (without it the tool counted polygons the layer had filtered
    out -- 2026-10-04). "All polygons" always means all the polygons the
    LAYER shows. Returns ``(geoms, n_total, n_selected, selected_used)``."""
    import arcpy

    try:
        if dq:
            with arcpy.da.SearchCursor(path, ["OID@"], where_clause=dq) as c0:
                n_total = sum(1 for _ in c0)
        else:
            n_total = int(arcpy.management.GetCount(path)[0])
    except Exception:                                            # noqa: BLE001
        n_total = None
    has_sel = pin is not None
    n_selected = len(pin) if pin else n_total
    use_sel = bool(use_selected) and has_sel and bool(pin)
    if bool(use_selected) and has_sel and not use_sel:
        _say(messages,
             "  {}: the selection selects nothing; all polygons are used "
             "(the original tool's fallback).".format(name))
    if use_selected and not has_sel and n_total:
        _say(messages,
             "  {}: no selection is active; all {} polygons are "
             "used.".format(name, n_total))

    # the PINNED OIDs filter a plain path cursor -- nothing depends on
    # the live selection surviving this long
    cols = ["OID@", "SHAPE@"]
    cur = None
    last = None
    for s in ([path, ds, str(ds)] if not use_sel else [path]):
        try:
            # the layer itself already applies its own definition query
            cur = arcpy.da.SearchCursor(
                s, cols, where_clause=(dq if s is path else None))
            break
        except Exception as e:                                   # noqa: BLE001
            last = e
    if cur is None:
        raise last

    geoms = []
    n_curved = 0
    with cur:
        for oid, g in cur:
            if g is None:
                continue
            if use_sel and oid not in pin:
                continue
            gsr = g.spatialReference
            if gsr is not None and not _kd._same_cs(gsr, out_sr):
                try:
                    g = _ct.project_geometry(g, out_sr)
                except Exception:                                # noqa: BLE001
                    pass
            if getattr(g, "hasCurves", False):
                try:
                    g = g.densify("DISTANCE", densify_step, densify_step)
                    n_curved += 1
                except Exception:                                # noqa: BLE001
                    pass
            geoms.append(g)
    if n_curved:
        _say(messages,
             "  {}: {} true-curve polygon{} densified before "
             "rasterizing.".format(name, n_curved,
                                   " was" if n_curved == 1 else "s were"))
    return geoms, n_total, n_selected, use_sel


def _rasterize_counts(gdal, geoms, out_sr, gt, proj, w, h, scratch):
    """Int32 (H, W) grid of how many of ``geoms`` cover each cell center:
    the same scratch-shapefile rasterization as ``hsm_tools._rasterize_mask``
    but burned ADDITIVELY, so every polygon feature adds one where it lies.
    One rasterization per layer whatever the feature count. A multipart
    feature is one feature (its parts add once, together); several
    single-part features that overlap each add their own one."""
    import arcpy
    from osgeo import ogr

    sample_sr = geoms[0].spatialReference
    same = (sample_sr is None or out_sr is None
            or (sample_sr.factoryCode == out_sr.factoryCode
                and sample_sr.name == out_sr.name))
    if not same:
        geoms = [_ct.project_geometry(g, out_sr) for g in geoms]
    shp = arcpy.CreateUniqueName("cum_feat.shp", scratch)
    with _mh.neutral_grid_env():
        arcpy.management.CopyFeatures(geoms, shp)
    drv = gdal.GetDriverByName("MEM")
    mds = drv.Create("", w, h, 1, gdal.GDT_Int32)
    mds.SetGeoTransform(gt)
    try:
        mds.SetProjection(proj)
    except Exception:                                            # noqa: BLE001
        pass
    vec = ogr.Open(shp)
    try:
        gdal.RasterizeLayer(mds, [1], vec.GetLayer(0), burn_values=[1],
                            options=["MERGE_ALG=ADD"])
    finally:
        vec = None
    mds.FlushCache()
    cnt = mds.GetRasterBand(1).ReadAsArray().astype(np.int32)
    mds = None
    try:
        arcpy.management.Delete(shp)
    except Exception:                                            # noqa: BLE001
        pass
    return cnt


# ---------------------------------------------------------------------------
#  Output
# ---------------------------------------------------------------------------
def _write_count_raster(gdal, path, gt, proj, counts, valid, classes,
                        n_layers):
    """Write the count raster (Byte when it fits, else Int32) with NoData,
    a color table matching the legend, and a Value/Count/Class_Name
    attribute table -- same recipe as ``tpi_tools._write_class_raster`` but
    with a nonzero NoData value, because 0 (no layers) is a real class
    here."""
    byte_ok = n_layers < _NODATA_BYTE
    nodata = _NODATA_BYTE if byte_ok else _NODATA_INT
    dtype = gdal.GDT_Byte if byte_ok else gdal.GDT_Int32
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, counts.shape[1], counts.shape[0], 1, dtype,
                    options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform(gt)
    try:
        ds.SetProjection(proj)
    except Exception:                                            # noqa: BLE001
        pass
    band = ds.GetRasterBand(1)
    colors = {int(c["value"]): c["color"] for c in classes}
    names = {int(c["value"]): c["name"] for c in classes}
    if byte_ok:
        # Set BEFORE writing pixels (GTiff locks the photometric
        # interpretation once data lands).
        ct = gdal.ColorTable()
        for v in range(256):
            ct.SetColorEntry(v, (0, 0, 0, 0))
        for v, col in colors.items():
            ct.SetColorEntry(v, (col[0], col[1], col[2], 255))
        band.SetRasterColorTable(ct)

    arr = counts.astype(np.uint8 if byte_ok else np.int32)
    if not valid.all():
        arr[~valid] = nodata
    band.WriteArray(arr)
    band.SetNoDataValue(nodata)

    uniq, ucnt = np.unique(arr[valid], return_counts=True)
    found = dict(zip(uniq.tolist(), ucnt.tolist()))
    present = [int(c["value"]) for c in classes
               if found.get(int(c["value"]), 0) > 0]
    rat = gdal.RasterAttributeTable()
    rat.CreateColumn("Value", gdal.GFT_Integer, gdal.GFU_MinMax)
    rat.CreateColumn("Count", gdal.GFT_Integer, gdal.GFU_PixelCount)
    rat.CreateColumn("Class_Name", gdal.GFT_String, gdal.GFU_Name)
    rat.CreateColumn("Red", gdal.GFT_Integer, gdal.GFU_Red)
    rat.CreateColumn("Green", gdal.GFT_Integer, gdal.GFU_Green)
    rat.CreateColumn("Blue", gdal.GFT_Integer, gdal.GFU_Blue)
    rat.SetRowCount(len(present))
    for row, v in enumerate(present):
        col = colors.get(v) or (180, 180, 180)
        rat.SetValueAsInt(row, 0, v)
        rat.SetValueAsInt(row, 1, int(found.get(v, 0)))
        rat.SetValueAsString(row, 2, names.get(v, str(v)))
        rat.SetValueAsInt(row, 3, col[0])
        rat.SetValueAsInt(row, 4, col[1])
        rat.SetValueAsInt(row, 5, col[2])
    try:
        band.SetDefaultRAT(rat)
    except Exception:                                            # noqa: BLE001
        pass
    band.FlushCache()
    ds.FlushCache()
    band = None
    ds = None
    return present


# ---------------------------------------------------------------------------
#  Orchestrator
# ---------------------------------------------------------------------------
def run_cumulative_surface(datasets, out_raster, per_feature=False,
                           legend_label="", zero_to_nodata=False,
                           cell_size=None, messages=None, use_selected=None):
    """Build the cumulative "how many layers occur here" raster.

    ``datasets`` -- any mix of polygon feature layers/classes and rasters
    (the multivalue GP parameter's values, or plain paths). Each polygon
    MAP LAYER's selection is always honored (an empty selection means all
    polygons, as the original documented) -- Pro's own per-layer "Use the
    selected records" switch is the control. ``per_feature`` makes every
    polygon FEATURE count as its own layer, so a cell's value is the number
    of overlapping polygons (one feature class holding one corridor per
    land facet is then eighteen strands, not one layer); unchecked, a layer
    counts once wherever any of its polygons lies. ``use_selected`` is the
    retired pre-2026-09-14 switch, accepted and ignored so old scripts
    still run. ``legend_label`` is the optional
    word appended to each legend class ("Species" -> "3 Species").
    ``zero_to_nodata`` turns cells where no layer occurs into NoData instead
    of the white 0 class. ``cell_size`` overrides the original's automatic
    rule (smallest raster cell, else extent/1000).

    Returns ``(out_path, lyrx_path_or_None)``.
    """
    import arcpy

    gdal = _mh._gdal()
    out_raster = _mh.resolve_raster_output(out_raster, messages)
    inputs = _classify_inputs(datasets, messages)
    n_rasters = sum(1 for k, *_ in inputs if k == "raster")
    n_polys = len(inputs) - n_rasters
    # pin every polygon layer's selection UP FRONT: a later input's
    # live selection can be dropped while earlier inputs are being
    # processed (standing rule, see mahalanobis.pin_selection)
    pins = [(_mh.pin_selection(ds) if kind != "raster" else None)
            for kind, ds, _d, _path, _name in inputs]
    # ... and its definition query, which a path-based read would lose
    dqs = [(_mh.definition_query(ds) if kind != "raster" else None)
           for kind, ds, _d, _path, _name in inputs]
    if per_feature:
        _say(messages, "Each polygon feature counts as its own layer "
                       "(overlapping polygons add up).")

    # Output coordinate system: the first raster's, else the first layer's.
    out_sr = None
    for kind, ds, d, path, name in inputs:
        if kind == "raster":
            out_sr = d.spatialReference
            break
    if out_sr is None:
        out_sr = inputs[0][2].spatialReference

    _mh.begin_phases(
        [("Preparing the analysis grid...", 1)]
        + [("Adding {} of {}: {}...".format(i, len(inputs), name), 2)
           for i, (k, ds, d, p, name) in enumerate(inputs, start=1)]
        + [("Saving the output raster...", 2)])

    _say(messages, "Combining {} layer{} ({} raster{}, {} polygon "
                   "layer{}).".format(
                       len(inputs), "" if len(inputs) == 1 else "s",
                       n_rasters, "" if n_rasters == 1 else "s",
                       n_polys, "" if n_polys == 1 else "s"))

    # Open every raster ONCE for the whole run (grid probe + presence
    # read); released in the finally below.
    scratch0 = arcpy.env.scratchFolder
    opened = {}
    for kind, ds, d, path, name in inputs:
        if kind == "raster" and path not in opened:
            opened[path] = _hs._open_source(path, scratch0, messages)

    try:
        gt, width, height, csx, csy, note = _build_grid(
            inputs, out_sr, cell_size, opened, messages)
        proj = _kd._osr_sr(out_sr).ExportToWkt()
        _say(messages,
             "Analysis grid: {:,} columns x {:,} rows, {}, in {}.".format(
                 width, height, note, out_sr.name))
        _mh.phase_advance()

        counts = np.zeros((height, width), dtype=np.uint16)
        scratch = arcpy.env.scratchFolder
        densify_step = min(csx, csy) * 0.5
        sources_rec = []
        source_ids = []
        n_units = 0        # layers, or features in per-feature mode
        for i, (kind, ds, d, path, name) in enumerate(inputs, start=1):
            entry = {"path": path, "type": kind}
            rec = _lnk.read(path, None)
            entry["record_kind"] = (rec or {}).get("record")
            entry["analysis_id"] = (rec or {}).get("analysis_id")
            if entry["analysis_id"]:
                source_ids.append(entry["analysis_id"])
            if kind == "raster":
                n_units += 1
                n_present = _raster_presence(counts, opened[path][0], path,
                                             gt, width, height, messages)
                _say(messages,
                     "  {} of {}: {} (raster) -- present on {:,} cells (value "
                     "not NoData and not 0).".format(i, len(inputs), name,
                                                     n_present))
            else:
                geoms, n_total, n_selected, sel_used = _polygon_geoms(
                    ds, path, name, True, out_sr, densify_step,
                    messages, pin=pins[i - 1], dq=dqs[i - 1])
                entry.update({"n_total": n_total, "n_selected": n_selected,
                              "selected": sel_used,
                              "per_feature": bool(per_feature),
                              "n_units": len(geoms) if per_feature else 1})
                if geoms and per_feature:
                    cnt = _rasterize_counts(gdal, geoms, out_sr, gt, proj,
                                            width, height, scratch)
                    counts += cnt.astype(counts.dtype)   # uint16 grid
                    n_present = int((cnt > 0).sum())
                    n_units += len(geoms)
                    del cnt
                elif geoms:
                    mask = _hsm._rasterize_mask(gdal, geoms, out_sr, gt, proj,
                                                width, height, scratch)
                    counts[mask] += 1
                    n_present = int(mask.sum())
                    n_units += 1
                    del mask
                else:
                    n_present = 0
                if sel_used:
                    ftxt = "{} selected of {} polygons".format(n_selected,
                                                               n_total)
                elif n_total is not None:
                    ftxt = "all {} polygon{}".format(
                        n_total, "" if n_total == 1 else "s")
                else:
                    ftxt = "all polygons"
                _say(messages,
                     "  {} of {}: {} ({}{}) -- present on {:,} cells.".format(
                         i, len(inputs), name, ftxt,
                         ", each counted separately" if per_feature else "",
                         n_present))
                if n_present == 0:
                    _warn(messages,
                          "{} contributed no cells -- its polygons may not "
                          "overlap the analysis extent, or the selection may "
                          "be empty.".format(name))
            sources_rec.append(entry)
            _mh.phase_advance()
    finally:
        # Release every GDAL handle, then delete the scratch copies
        # (a still-open dataset keeps its copy file-locked).
        for _p, (srcd, tmp) in list(opened.items()):
            opened[_p] = (None, tmp)
        for _p, (_srcd, tmp) in opened.items():
            if tmp:
                try:
                    arcpy.management.Delete(tmp)
                except Exception:
                    pass


    max_count = int(counts.max()) if counts.size else 0
    valid = np.ones(counts.shape, dtype=bool)
    if zero_to_nodata:
        valid = counts > 0
        _say(messages,
             "Cells where no layer occurs become NoData ({:,} of {:,} "
             "cells).".format(int((~valid).sum()), counts.size))

    # Legend classes for every value that can occur (0..max), viridis
    # colors; the writer keeps only the values actually present.
    nonzero = list(range(1, max_count + 1))
    classes = []
    if not zero_to_nodata:
        classes.append({"value": 0, "name": class_label(0, legend_label),
                        "color": _WHITE})
    for j, v in enumerate(nonzero):
        classes.append({"value": v, "name": class_label(v, legend_label),
                        "color": ramp_color(j, len(nonzero))})

    tmp = arcpy.CreateUniqueName("cumulative.tif", scratch)
    present = _write_count_raster(gdal, tmp, gt, proj, counts, valid,
                                  classes, max(n_units, max_count))
    del counts, valid

    src_list = ", ".join(
        "{} ({})".format(e["path"].rstrip("\\/").rsplit("\\", 1)[-1]
                         .rsplit("/", 1)[-1],
                         "raster" if e["type"] == "raster" else "polygons")
        for e in sources_rec)
    word = (legend_label or "").strip()
    _tpi._deliver_class_output(
        tmp, out_raster, messages,
        title=os.path.basename(str(out_raster)),
        summary=(
            "Created by Cumulative Surface: each cell counts how many of "
            "the {} input {} occur there (rasters count where their "
            "value is neither NoData nor 0; polygon{} where a polygon "
            "covers the cell center). Inputs: {}.{}{}{}".format(
                n_units if per_feature else len(inputs),
                "layers and polygon features" if per_feature else "layers",
                " features" if per_feature else " layers", src_list,
                " Every polygon feature was counted as its own layer."
                if per_feature else "",
                " Cells where no layer occurs are NoData."
                if zero_to_nodata else "",
                " Legend label: {!r}.".format(word) if word else "")),
        description=(
            "Cumulative surface -- a raster whose cell value equals the "
            "number of input polygon layers or rasters present on that "
            "cell, a means of estimating the relative importance (or "
            "number of species served) of preserving that ground. "
            + ("Every polygon FEATURE was counted as its own layer "
               "(overlapping polygons add up). "
               if per_feature else
               "Each LAYER counts at most once per cell. ")
            + "A modernized port of the CorridorDesigner Evaluation Tools' "
            "Cumulative Surface Tool. " + _CD_CITATION),
        tags=("cumulative surface, overlap, count, corridor, patches, "
              "richness, CorridorDesigner, Corridor Designer, Jenness "
              "Enterprises"),
        citation=_CD_CITATION,
        class_names={int(c["value"]): c["name"] for c in classes})

    mixed = len(set(source_ids)) > 1
    if mixed:
        _warn(messages,
              "The inputs carry DIFFERENT analysis ids -- this surface "
              "combines genuinely separate analyses (different species or "
              "runs), which is normally exactly the point of this tool.")
    _lnk.write(out_raster, _lnk.cumulative_record(
        sources=sources_rec, mixed_analysis=mixed,
        source_analysis_ids=sorted(set(source_ids)),
        params={"zero_to_nodata": bool(zero_to_nodata),
                "legend_label": word or None,
                "per_feature": bool(per_feature),
                "selections_honored": True,
                "cell_size": csx,
                "grid": {"width": width, "height": height,
                         "spatial_reference": out_sr.name}}), messages)

    lyrx = _mh.default_unique_value_lyrx(
        out_raster,
        [c for c in classes if int(c["value"]) in present],
        field="Class_Name", messages=messages)

    _mh.phase_advance()
    _say(messages,
         "Cumulative surface written: values 0-{} across {:,} x {:,} "
         "cells.".format(max_count, width, height))
    _mh.end_phases()
    return out_raster, lyrx
