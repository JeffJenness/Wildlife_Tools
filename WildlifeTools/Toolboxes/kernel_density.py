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

"""Kernel-density volume tools for the Jenness Enterprises toolbox.

Two analyses, both built on one validated core (verified bit-for-bit against the
legacy ``Analyze_Raster`` VB output):

* **Proportion Under the Surface raster** -- converts a kernel-density (KD) raster
  into a "volume" raster where each cell holds the percent of the total volume
  (sum of densities) enclosed by the isopleth passing through that cell, i.e. the
  proportion contained in all cells whose density is >= this cell's density.
  Peak cells ~0%, the outer edge ~100%. Contour this surface (Esri Contour) at
  50 / 95 / 99 to get home-range isopleths directly.

* **Probability contours** -- the discrete density levels at the standard
  home-range probabilities (5%..100% in 5% steps) as isopleth polylines and/or
  polygon areas, reproducing the legacy tool's "Probabilities" output.

Volume is taken as the sum of cell densities (cell area is constant, so it
cancels in every proportion). Background/zero and NoData cells are excluded, the
same as the legacy tool.
"""

import os
import math
import datetime

import arcpy
import clip_tools as _ct
import numpy as np

import mahalanobis as mh   # reuse the fast stats+histogram raster helpers (standing rule)
import wildlife_geom as wg # reuse vectorized haversine (geodesic) -- pyproj is not installed
import tool_metadata as tmeta   # shared field-level metadata writer


# Home-range labels drawn "primary" (heavier) by the legacy tool.
_PRIMARY = {5, 25, 50, 75, 95}
_NODATA = mh.NODATA_OUT

# Metadata marker stamped on proportion-under-the-surface rasters so the Probability
# Contours tool can recognize them and contour directly (instead of computing
# volume-weighted density levels from a kernel-density raster).
_PROP_TAG = "JENNESS_KD_PROPORTION_SURFACE"
# Markers for what a kernel-density raster's cell values ARE. On a
# latitude/longitude raster the volume under the surface is density x cell
# area, and the cells shrink toward the poles: a DENSITIES raster needs each
# cell weighted by its area, an EXPECTED COUNTS raster already has it in.
_DENS_TAG = "JENNESS_KD_DENSITIES"
_COUNT_TAG = "JENNESS_KD_EXPECTED_COUNTS"
KIND_DENSITIES = "Densities"
KIND_COUNTS = "Expected counts"
KIND_PROPORTION = "Proportion"
VALUE_KINDS = [KIND_DENSITIES, KIND_COUNTS]


_KD_TAGS = "kernel density, home range, utilization distribution"

# Conceptual descriptions (the "what is this kind of data" text, mirroring hover help).
_DESC_KDE = (
    "A kernel density surface estimated from point locations. Each input point is "
    "replaced by a smooth, finite-support kernel that adds weight to nearby cells out "
    "to the search radius (bandwidth) and drops to zero beyond it; overlapping kernels "
    "are summed. Such surfaces are widely used to estimate animal utilization "
    "distributions (home ranges) and the intensity of spatial point patterns.")
_DESC_PROPORTION = (
    "A proportion-under-the-surface (volume) raster derived from a kernel density "
    "surface. Each cell holds the cumulative share of the total volume under the "
    "surface, running from about 0 at the outer boundary to 1.0 at the peak. "
    "Contouring it at one minus a target percentage yields that home-range isopleth "
    "(for example, contour at 0.05 for the 95% isopleth).")
_DESC_CONTOURS = (
    "Probability (volume) isopleths of a kernel density surface: nested contours that "
    "each enclose a given percentage of the total volume under the surface. The X% "
    "isopleth bounds the smallest area containing X% of the estimated use -- the "
    "standard way to delineate kernel home ranges (e.g., the 95% and 50% contours).")
_CONTOUR_ENTITY = (
    "Kernel-density probability (volume) isopleths: nested contours that each enclose "
    "a given percentage of the total volume under the surface.")
_CONTOUR_FIELDS = [
    {"name": "Density",
     "description": "The raster value contoured to produce this isopleth -- the "
                    "kernel-density threshold for a kernel-density input, or the "
                    "cumulative proportion for a proportion-under-the-surface input. "
                    "(For an expected-counts raster in latitude/longitude it is the "
                    "count a cell of average size would hold. The 100% isopleth is "
                    "the edge of the positive cells, and carries the smallest "
                    "positive value.)"},
    {"name": "Isopleth",
     "description": "The home-range isopleth level: the proportion (0-1) of the total "
                    "volume under the surface enclosed by this contour (e.g. 0.95 is "
                    "the 95% isopleth)."},
    {"name": "CumProportion",
     "description": "The cumulative proportion of volume outside this isopleth (1 "
                    "minus Isopleth); equivalently, the value at which to contour a "
                    "proportion-under-the-surface raster to reproduce this isopleth."},
    {"name": "Label",
     "description": "The isopleth level as a percentage label (for example, '95%')."},
    {"name": "Primary",
     "description": "Whether this is a primary (more prominent) isopleth.",
     "coded": [("True", "A primary isopleth (the 5, 25, 50, 75, and 95 percent levels)."),
               ("False", "A secondary (minor) isopleth.")]},
]


def _today():
    n = datetime.datetime.now()
    return "{:%B} {}, {}".format(n, n.day, n.year)


def _kd_write_meta(target, title, summary, description, proportion=False, messages=None,
                   kind_tag=None):
    """Write short title/tags/summary/description metadata onto an output. Stamps the
    proportion-surface marker when ``proportion`` is True, and ``kind_tag``
    (_DENS_TAG / _COUNT_TAG) when given. Never raises.

    Memory-workspace outputs are SKIPPED entirely: they cannot hold
    metadata, and the arcpy metadata layer's save failure leaves the
    interpreter's error indicator set even inside a try/except, detonating
    as a SystemError at the NEXT unrelated call (seen live 2026-09-07,
    'Failed to save metadata' -> crash in _writeback_resolved_raster)."""
    try:
        if mh.is_memory_workspace(str(target)):
            if messages:
                if proportion:
                    messages.addWarningMessage(
                        "In-memory output: metadata cannot be written, so the "
                        "proportion-surface marker was NOT stamped. KD "
                        "Probability Contours will treat this raster as plain "
                        "densities -- write the output to a geodatabase (or "
                        "copy it to one) if you plan to contour it as a "
                        "proportion surface.")
                else:
                    messages.addMessage("In-memory output: metadata skipped.")
            return
        from arcpy import metadata as md
        m = md.Metadata(target)
        m.title = title
        tags = _KD_TAGS
        if proportion:
            tags += ", " + _PROP_TAG
            description = description + "\n[" + _PROP_TAG + "]"
        elif kind_tag:
            tags += ", " + kind_tag
            description = description + "\n[" + kind_tag + "]"
        m.tags = tags
        m.summary = summary
        m.description = description
        m.save()
    except Exception as e:
        if messages:
            messages.addWarningMessage("Could not write output metadata: {}".format(e))


def _is_marked_proportion(raster):
    """True if the raster carries the proportion-under-the-surface marker."""
    try:
        from arcpy import metadata as md
        m = md.Metadata(raster)
        return _PROP_TAG in " ".join([m.tags or "", m.description or "", m.summary or ""])
    except Exception:
        return False


def detect_value_kind(raster):
    """What the raster's metadata says its cell values are:
    ``(kind, source)`` with kind one of KIND_PROPORTION / KIND_DENSITIES /
    KIND_COUNTS, or ``(None, None)`` when the metadata holds no information.

    Looked for, in order: this suite's own markers; the run summary this
    suite has always written ("Output values: ..."); and the raster's
    geoprocessing history, where Esri's Kernel Density records its
    DENSITIES / EXPECTED_COUNTS choice (the most recent kernel-density
    process wins). Never raises."""
    import re
    try:
        from arcpy import metadata as md
        m = md.Metadata(raster)
        text = " ".join([m.tags or "", m.description or "", m.summary or ""])
        if _PROP_TAG in text:
            return KIND_PROPORTION, "this suite's proportion-surface marker"
        if _COUNT_TAG in text:
            return KIND_COUNTS, "this suite's marker in the metadata"
        if _DENS_TAG in text:
            return KIND_DENSITIES, "this suite's marker in the metadata"
        low = text.lower()
        if "output values: expected point counts" in low:
            return KIND_COUNTS, "the run summary in the metadata"
        if "output values: densities" in low:
            return KIND_DENSITIES, "the run summary in the metadata"
        if "output values: proportion under the surface" in low:
            return KIND_PROPORTION, "the run summary in the metadata"
        xml = m.xml or ""
        found = None
        for mm in re.finditer(r"<Process\b([^>]*)>(.*?)</Process>", xml,
                              re.S | re.I):
            proc = (mm.group(1) + " " + mm.group(2)).upper()
            if "KERNELDENSITY" not in proc.replace(" ", "").replace("_", ""):
                continue
            if re.search(r"EXPECTED[_ ]COUNTS", proc):
                found = KIND_COUNTS
            elif re.search(r"\bDENSITIES\b", proc):
                found = KIND_DENSITIES
        if found:
            return found, "the geoprocessing history in the metadata"
    except Exception:
        pass
    return None, None


def resolve_value_kind(kd, value_kind, messages=None):
    """The kind to use: the caller's explicit choice, else what the metadata
    says, else Densities (Esri's default, and the usual case)."""
    if value_kind in (KIND_DENSITIES, KIND_COUNTS):
        return value_kind, "as specified"
    kind, source = detect_value_kind(kd)
    if kind in (KIND_DENSITIES, KIND_COUNTS):
        return kind, "from " + source
    return KIND_DENSITIES, "assumed: the raster's metadata does not say"


def _row_area_weights(kd, gt, height):
    """Relative cell area of each ROW of a latitude/longitude raster (cells
    narrow toward the poles), or None for a projected raster, whose cells
    are all the same size."""
    try:
        sr = arcpy.Describe(kd).spatialReference
        if sr is None or sr.type != "Geographic":
            return None
    except Exception:
        return None
    lat_c = gt[3] + (np.arange(height) + 0.5) * gt[5]
    half = abs(gt[5]) / 2.0
    w = np.abs(np.sin(np.radians(lat_c + half)) - np.sin(np.radians(lat_c - half)))
    return w if float(w.max()) > 0 else None


def _volume_model(kd, gt, arr, value_kind, messages):
    """How to add up the volume under a kernel-density raster:
    ``(order, vol, rescaled)``.

    ``order`` is the array that ranks the cells from sparse to dense and that
    the isopleths are contoured on; ``vol`` is each cell's share of the
    volume (None = the same as ``order``); ``rescaled`` is True when
    ``order`` is not the raster's own values.

    * projected raster: every cell has the same area, so the values serve
      as both (densities and expected counts differ only by a constant).
    * latitude/longitude, DENSITIES: rank by the values; volume = value x
      cell area.
    * latitude/longitude, EXPECTED COUNTS: the values ARE the volume, but
      they rank the cells by count, not density (a cell nearer the pole is
      smaller, so the same density gives it a smaller count). Rank by
      count / relative cell area instead -- the count a cell of average
      size would hold."""
    row_w = _row_area_weights(kd, gt, arr.shape[0])
    if row_w is None:
        return arr, None, False
    w2d = np.repeat((row_w / float(row_w.mean()))[:, None], arr.shape[1], axis=1)
    kind, how = resolve_value_kind(kd, value_kind, messages)
    if kind == KIND_DENSITIES:
        if messages is not None:
            messages.addMessage(
                "Latitude/longitude raster of densities ({}): each cell is "
                "weighted by its true area when the volume is added "
                "up.".format(how))
        return arr, arr * w2d, False
    if messages is not None:
        messages.addMessage(
            "Latitude/longitude raster of expected counts ({}): the values "
            "already include the cell areas, so no area weighting is "
            "applied to the volume; the cells are ranked by count per cell "
            "of average size, which is their density.".format(how))
    return arr / w2d, arr, True


# --------------------------------------------------------------------------- #
# Shared core
# --------------------------------------------------------------------------- #
def _open_kd(kd, scratch):
    """Open a KD raster with GDAL (exporting FGDB/mosaic rasters to a scratch
    GeoTIFF when GDAL cannot read them directly). Returns
    (gdal, src_dataset, tmp_copy_or_None)."""
    gdal = mh._gdal()
    path, tmp = mh._gdal_source_path(kd, scratch)
    try:
        src = gdal.Open(path)
    except RuntimeError:
        src = None
    if src is None:
        raise arcpy.ExecuteError("Could not open the input raster: {}".format(kd))
    return gdal, src, tmp


def _read_positive(src):
    """Read band 1 as a 2-D float64 array plus a mask of positive, valid cells."""
    band = src.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    arr = band.ReadAsArray().astype(np.float64)
    valid = np.isfinite(arr) & (arr > 0.0)
    if nodata is not None:
        valid &= (arr != nodata)
    valid = mh.mask_band_valid(band, valid)
    return arr, valid


def _cumulative_volume(arr, valid, from_below, vol=None):
    """Per-cell cumulative-volume fraction (0..1). Ties share a value. Returns a
    float64 array (NaN outside ``valid``). ``arr`` ranks the cells; ``vol``
    (same shape) is each cell's volume, None when it is ``arr`` itself (see
    ``_volume_model``).

    from_below=True : share of volume with density <= this cell's density
                      (0 at the outer edge, 1 at the peak -- the "proportion under
                      the surface" raster, oriented like the raw density).
    from_below=False: share of volume with density >= this cell's density
                      (enclosed-volume / home-range orientation).
    """
    vals = arr[valid]
    uvals, inv = np.unique(vals, return_inverse=True)
    v = vals if vol is None else vol[valid]
    # volume per density level, summed over the cells at that level
    vol_per_uval = np.bincount(inv.ravel(), weights=v, minlength=uvals.size)
    total = vol_per_uval.sum()
    if from_below:
        cum = np.cumsum(vol_per_uval) / total           # density <= u
    else:
        cum = np.cumsum(vol_per_uval[::-1])[::-1] / total   # density >= u
    out = np.full(arr.shape, np.nan, dtype=np.float64)
    out[valid] = cum[inv]
    return out


def compute_contour_levels(arr, valid, vol=None):
    """The legacy "Probability" contour levels. ``arr`` and ``vol`` as in
    ``_cumulative_volume``.

    Sort positive densities ascending; step upward accumulating density/total; the
    first cell whose cumulative-from-below fraction reaches each internal threshold
    p in {0, .05, ..., .95} gives that level's contour density. The home-range
    *label* is ``1 - p`` (so internal .05 -> "95%"). Returns a list of dicts with
    keys: density, percentile (label fraction), label (str), primary (bool),
    ascending by density and de-duplicated on density.
    """
    vals = arr[valid]
    order = np.argsort(vals, kind="mergesort")
    vals = vals[order]
    v = vals if vol is None else vol[valid][order]
    total = v.sum()
    frac = np.cumsum(v) / total
    seen = set()
    levels = []
    for i in range(0, 20):                              # 0, .05, ..., .95
        p = round(0.05 * i, 2)
        idx = int(np.searchsorted(frac, p, side="left"))
        if idx >= vals.size:
            idx = vals.size - 1
        density = float(np.float32(vals[idx]))          # the raster stores float32
        if density in seen:                             # ties can collapse adjacent levels
            continue
        seen.add(density)
        label_frac = round(1.0 - p, 2)
        pct = int(round(label_frac * 100))
        levels.append({"density": density, "isopleth": label_frac,
                       "cumprop": round(1.0 - label_frac, 2),   # = internal p; the raster value at this isopleth
                       "label": "{}%".format(pct), "primary": pct in _PRIMARY})
    levels.sort(key=lambda d: d["density"])
    return levels


def _proportion_levels():
    """Contour levels for a proportion-under-the-surface raster, whose values are
    already the cumulative-from-below volume (~0 at the edge, 1 at the peak). The X%
    home-range isopleth is simply the contour at raster value (1 - X), so we emit
    X = 95%, 90%, ..., 5%. (The 100% isopleth is the edge of the data; the
    caller adds it from the smoothed positive-cell mask.)
    """
    levels = []
    for i in range(1, 20):                  # raster value p = 0.05 .. 0.95
        p = round(0.05 * i, 2)
        label_frac = round(1.0 - p, 2)
        pct = int(round(label_frac * 100))
        levels.append({"density": p, "isopleth": label_frac, "cumprop": p,
                       "label": "{}%".format(pct), "primary": pct in _PRIMARY})
    levels.sort(key=lambda d: d["density"])
    return levels


# --------------------------------------------------------------------------- #
# Tool 1 -- Proportion Under the Surface raster
# --------------------------------------------------------------------------- #
def run_proportion_surface(kd, out_raster, messages, value_kind=None):
    """KD raster -> proportion-under-the-surface raster (0..1).

    Each cell holds the share of the total volume with density <= that cell's
    density, so values run from ~0 at the outer boundary to 1.0 at the peak (the
    same orientation as the raw kernel density). To draw the X% home-range isopleth,
    contour this surface at ``1 - X/100`` (e.g. 0.05 for the 95% isopleth) -- the
    Probability Contours tool reports that value in its ``CumProportion`` field.
    Writes statistics and a histogram with the raster (scratch GeoTIFF + CopyRaster)
    so it renders correctly without the slow CalculateStatistics scan."""
    out_raster = mh.resolve_raster_output(out_raster, messages)
    scratch = arcpy.env.scratchFolder
    gdal, src, tmp = _open_kd(kd, scratch)
    try:
        width, height = src.RasterXSize, src.RasterYSize
        gt, proj = src.GetGeoTransform(), src.GetProjection()
        arr, valid = _read_positive(src)
        n = int(valid.sum())
        if n == 0:
            raise arcpy.ExecuteError(
                "The input raster has no positive-density cells to analyze.")
        mh._set_label("Computing proportion under the surface...")
        order, vol, _rescaled = _volume_model(kd, gt, arr, value_kind, messages)
        cum = _cumulative_volume(order, valid, from_below=True,
                                 vol=vol)   # 0 at edge -> 1 at peak

        out = np.full(arr.shape, _NODATA, dtype=np.float32)
        out[valid] = cum[valid].astype(np.float32)

        out_path = arcpy.CreateUniqueName("kd_prop.tif", scratch)
        drv = gdal.GetDriverByName("GTiff")
        out_ds = drv.Create(out_path, width, height, 1, gdal.GDT_Float32,
                            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
        out_ds.SetGeoTransform(gt)
        try:
            out_ds.SetProjection(proj)
        except Exception:
            pass
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(_NODATA)
        out_band.WriteArray(out)
        out_band.FlushCache()

        stats = mh._RasterStats()
        stats.add(out[valid])
        out_stats = mh._finalize_stats(out_band, stats, messages)
        if out_stats is not None:
            out_stats["on_raster"] = mh._write_gtiff_stats(out_band, out_stats)
        out_band.FlushCache()
        out_ds.FlushCache()
    finally:
        src = None
    out_ds = None      # closing writes the .aux.xml (PAM) with the stats+histogram

    messages.addMessage(
        "Computed proportion under the surface (0-1) for {:,} positive-density "
        "cells.".format(n))
    mh._set_label("Saving output raster...")
    prev = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        arcpy.management.CopyRaster(out_path, out_raster)
    finally:
        arcpy.env.pyramid = prev
    # GDAL's OpenFileGDB driver can return an EMPTY projection for
    # geodatabase rasters whose coordinate system is perfectly healthy
    # on the arcpy side (seen live 2026-09-07: NAD83 UTM 12N described
    # by arcpy, blank from GDAL) -- the scratch GeoTIFF then carries no
    # CS into the output. Repair from the INPUT's described CS.
    try:
        out_sr = arcpy.Describe(out_raster).spatialReference
        if out_sr is None or getattr(out_sr, "name", "Unknown") in ("", "Unknown"):
            src_sr = arcpy.Describe(kd).spatialReference
            if src_sr is not None and getattr(src_sr, "name", "Unknown") not in ("", "Unknown"):
                arcpy.management.DefineProjection(out_raster, src_sr)
                messages.addMessage(
                    "Applied the input raster's coordinate system ({}) to the "
                    "output (GDAL could not read it from the source).".format(
                        src_sr.name))
    except Exception as e:                                   # noqa: BLE001
        messages.addWarningMessage(
            "Could not verify the output coordinate system: {}".format(e))
    if out_stats and out_stats.get("on_raster"):
        messages.addMessage(
            "Statistics and histogram were written with the raster (no scan needed).")
    elif not mh._apply_known_statistics(out_raster, out_stats, messages):
        try:
            arcpy.management.CalculateStatistics(out_raster)
        except Exception as e:
            messages.addWarningMessage("Could not calculate raster statistics: {}".format(e))
    for p in (out_path, tmp):
        if p:
            try:
                arcpy.management.Delete(p)
            except Exception:
                pass
    try:
        src_name = arcpy.Describe(kd).baseName
    except Exception:
        src_name = str(kd)
    try:
        title = arcpy.Describe(out_raster).baseName
    except Exception:
        title = "Proportion Surface"
    _kd_write_meta(
        out_raster, title=title,
        summary=("Created {}. Proportion-under-the-surface (volume) raster derived from "
                 "the kernel density raster '{}'. Values run from about 0 at the outer "
                 "boundary to 1.0 at the peak; contour at one minus the desired "
                 "percentage to obtain home-range isopleths.").format(_today(), src_name),
        description=_DESC_PROPORTION, proportion=True, messages=messages)
    return out_raster


# --------------------------------------------------------------------------- #
# Tool 2 -- Probability contours (polylines and/or polygon areas)
# --------------------------------------------------------------------------- #
def _contour_value_field(fc):
    for f in arcpy.ListFields(fc):
        if f.name.lower() == "contour":
            return f.name
    return "Contour"


def _cum_field(fc):
    """'CumProportion', or the 10-character 'CumProport' when ``fc`` is (or
    will be) a shapefile -- the same name ArcGIS itself produces when it
    copies the geodatabase field to a shapefile, so lines and polygons
    agree."""
    return "CumProport" if mh.is_shapefile_target(str(fc)) else "CumProportion"


def _contour_fields_for(fc):
    """The field-metadata list with the cumulative-proportion field named as
    it actually is in ``fc``."""
    cum = _cum_field(fc)
    return [dict(d, name=cum) if d["name"] == "CumProportion" else d
            for d in _CONTOUR_FIELDS]


def _add_level_fields(fc):
    """Add the level fields; returns the name of the cumulative-proportion
    field, which depends on the output format."""
    existing = {f.name for f in arcpy.ListFields(fc)}
    cum = _cum_field(fc)
    #   name             type      length  alias
    specs = [("Density", "DOUBLE", None, "Density level"),
             ("Isopleth", "DOUBLE", None, "Proportion of volume enclosed (isopleth level)"),
             (cum, "DOUBLE", None, "Cumulative proportion (raster value)"),
             ("Label", "TEXT", 5, None),
             ("Primary", "TEXT", 5, None)]
    # one AddFields call, not one AddField per field (standing rule)
    todo = [[name, ftype, alias or "", length or "", "", ""]
            for name, ftype, length, alias in specs if name not in existing]
    if todo:
        arcpy.management.AddFields(fc, todo)
    return cum


def _match_index(levels, value):
    """Index of the nearest level for a contour density value."""
    return min(range(len(levels)), key=lambda i: abs(levels[i]["density"] - value))


def _reproject_in_place(fc, out_cs, messages):
    """If the Output Coordinate System environment differs from ``fc``'s, reproject
    ``fc`` to it in place (project to a scratch copy, then copy back to the same
    path). Honors the Geographic Transformations environment via Project."""
    try:
        sr = arcpy.Describe(fc).spatialReference
    except Exception:
        return
    if not out_cs or _same_cs(sr, out_cs):
        return
    # Produce, then swap: the projected copy is written beside the output
    # before the original is removed.
    ws, name = os.path.split(str(fc))
    stem, ext = os.path.splitext(name)
    sibling = arcpy.CreateUniqueName(stem + "_prj" + ext, ws)
    arcpy.management.Project(fc, sibling, out_cs,
                             _ct.transformation_name(sr, out_cs) or "")
    arcpy.management.Delete(fc)
    arcpy.management.Rename(sibling, fc)
    messages.addMessage("Reprojected output to Output Coordinate System: {}.".format(out_cs.name))


def _vector_mem_layer(gdal, ogr, geom_type, field_names):
    """An in-memory OGR layer for the contour generator: (dataset, layer)."""
    drv = gdal.GetDriverByName("MEM") or gdal.GetDriverByName("Memory")
    vds = drv.Create("", 0, 0, 0, gdal.GDT_Unknown)
    lyr = vds.CreateLayer("contours", None, geom_type)
    for nm in field_names:
        lyr.CreateField(ogr.FieldDefn(nm, ogr.OFTReal))
    return vds, lyr


def _level_text(values):
    # 17 significant digits round-trip a double exactly
    return ",".join(repr(float(v)) for v in values)


def _gdal_contour_lines(gdal, band, nodata, densities):
    """Contour ``band`` at the fixed ``densities``: a list of (WKB, value),
    one entry per contour line. GDAL's contour generator needs no extension
    license (Esri's Contour List needs 3D Analyst or Spatial Analyst)."""
    from osgeo import ogr
    vds, lyr = _vector_mem_layer(gdal, ogr, ogr.wkbLineString, ["elev"])
    opts = ["FIXED_LEVELS=" + _level_text(densities), "ELEV_FIELD=elev"]
    if nodata is not None:
        opts.append("NODATA=" + repr(float(nodata)))
    gdal.ContourGenerateEx(band, lyr, options=opts)
    out = []
    lyr.ResetReading()
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None or g.IsEmpty():
            continue
        out.append((bytes(g.ExportToWkb()), float(f.GetField("elev"))))
    lyr = None
    vds = None
    return out


def _gdal_level_polygons(gdal, band, nodata, density, ceiling):
    """The region where ``band`` is at or above ``density``, as a list of
    polygon WKBs (one per separate area, each with its own holes).

    Built as the single polygon band between ``density`` and ``ceiling`` (a
    value above the raster's maximum), so the result is exactly "density >=
    level": a hollow in the surface stays a hole, and an area that runs off
    the edge of the raster is closed along the edge. (Closing each contour
    loop separately, as this tool used to with Feature To Polygon, filled the
    hollows -- and needed an Advanced license.)"""
    from osgeo import ogr
    vds, lyr = _vector_mem_layer(gdal, ogr, ogr.wkbMultiPolygon,
                                 ["emin", "emax"])
    opts = ["FIXED_LEVELS=" + _level_text([density, ceiling]),
            "ELEV_FIELD_MIN=emin", "ELEV_FIELD_MAX=emax", "POLYGONIZE=YES"]
    if nodata is not None:
        opts.append("NODATA=" + repr(float(nodata)))
    gdal.ContourGenerateEx(band, lyr, options=opts)
    parts = []
    lyr.ResetReading()
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None or g.IsEmpty():
            continue
        name = g.GetGeometryName().upper()
        if name == "POLYGON":
            parts.append(bytes(g.ExportToWkb()))
        else:                                   # MULTIPOLYGON / collection
            for k in range(g.GetGeometryCount()):
                sub = g.GetGeometryRef(k)
                if sub is not None and not sub.IsEmpty() and \
                        sub.GetGeometryName().upper() == "POLYGON":
                    parts.append(bytes(sub.ExportToWkb()))
    lyr = None
    vds = None
    return parts


def _create_contour_fc(out_fc, geometry_type, sr, with_contour_field):
    """Create an output feature class with the level fields. Returns the
    insert-cursor field list (after SHAPE@)."""
    arcpy.management.CreateFeatureclass(
        os.path.dirname(out_fc), os.path.basename(out_fc),
        geometry_type, spatial_reference=mh.clean_sr(sr))
    if with_contour_field:
        arcpy.management.AddField(out_fc, "Contour", "DOUBLE")
    cum = _add_level_fields(out_fc)
    if mh.is_shapefile_target(str(out_fc)):
        # a new shapefile carries a default "Id" field; drop it now that
        # our own fields exist
        if any(f.name.lower() == "id" for f in arcpy.ListFields(out_fc)):
            try:
                arcpy.management.DeleteField(out_fc, "Id")
            except Exception:
                pass
    return ((["Contour"] if with_contour_field else [])
            + ["Density", "Isopleth", cum, "Label", "Primary"])


def _outer_edge_band(gdal, src, valid):
    """An in-memory band for the OUTERMOST isopleth, where the surface trails
    off to nothing: the positive-density cells as 1 and everything else as 0,
    lightly smoothed, to be contoured at 0.5.

    The edge of the positive cells is a staircase of cell sides. The smoothed
    mask's 0.5 line follows the same edge without the steps (and stays on it
    wherever the edge is straight), and is always a closed loop. Returns
    (dataset, band); keep the dataset alive while the band is in use."""
    from scipy.ndimage import gaussian_filter
    # A margin of empty cells all round, so the line closes on itself even
    # where the surface reaches the edge of the raster (there it runs along
    # the edge, half a cell outside it).
    pad = 3
    soft = gaussian_filter(np.pad(valid.astype(np.float64), pad),
                           sigma=1.0, mode="constant", cval=0.0)
    h, w = soft.shape
    gt = src.GetGeoTransform()
    zds = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_Float32)
    zds.SetGeoTransform((gt[0] - pad * gt[1], gt[1], gt[2],
                         gt[3] - pad * gt[5], gt[4], gt[5]))
    zb = zds.GetRasterBand(1)
    zb.WriteArray(soft.astype(np.float32))
    zb.FlushCache()
    return zds, zb


def _build_contours(kd, out_lines, out_polys, messages, value_kind=None):
    """Generate the probability isopleths (polylines and/or polygon areas) in the
    raster's native coordinate system. Returns a list of outputs.

    Contouring is done with GDAL (bundled with ArcGIS Pro), so the tool needs
    no extension license and no particular license level."""
    scratch = arcpy.env.scratchFolder
    proportion = _is_marked_proportion(kd)
    gdal, src, tmp = _open_kd(kd, scratch)
    rds = zds = None
    try:
        band = src.GetRasterBand(1)
        nodata = band.GetNoDataValue()
        arr, valid = _read_positive(src)
        if not valid.any():
            raise arcpy.ExecuteError(
                "The input raster has no positive-density cells to analyze.")
        if proportion:
            # The raster already holds the cumulative volume (proportion under
            # the surface): the home-range isopleths are just fixed proportion
            # contours, so skip the volume-weighted level search.
            levels = _proportion_levels()
            messages.addMessage(
                "Input is a proportion-under-the-surface raster; contouring the standard "
                "proportion levels directly ({} levels).".format(len(levels)))
        else:
            # A kernel-density raster (Esri Kernel Density, or our tool's
            # Densities / Expected counts output): derive the volume-weighted
            # contour density levels from the cell values.
            order, vol, rescaled = _volume_model(kd, src.GetGeoTransform(), arr,
                                                 value_kind, messages)
            levels = compute_contour_levels(order, valid, vol=vol)
            messages.addMessage(
                "Input is a kernel-density raster; computed {} volume-weighted contour "
                "levels.".format(len(levels)))
            if rescaled:
                # contour the density-ranked surface, not the raw counts
                h_, w_ = order.shape
                rds = gdal.GetDriverByName("MEM").Create("", w_, h_, 1, gdal.GDT_Float32)
                rds.SetGeoTransform(src.GetGeoTransform())
                band = rds.GetRasterBand(1)
                band.SetNoDataValue(float(_NODATA))      # before writing (MEM)
                band.WriteArray(np.where(valid, order, _NODATA).astype(np.float32))
                band.FlushCache()
                nodata = float(_NODATA)
                arr = order
        vmax = float(arr[valid].max())
        vmin = float(arr[valid].min())
        del arr
        ceiling = max(abs(vmax) * 2.0, 1.0) + abs(vmax)     # above every cell

        # The outermost (100%) isopleth: the edge of the positive cells, where
        # the surface trails off to nothing. Contouring the raster at its
        # smallest positive value gives no usable line there, so this one
        # level is drawn from a lightly smoothed positive/zero mask.
        outer = None
        for lv in levels:
            if lv["label"] == "100%":
                outer = lv
        if outer is None:
            outer = {"density": (0.0 if proportion else float(np.float32(vmin))),
                     "isopleth": 1.0, "cumprop": 0.0, "label": "100%",
                     "primary": False}
            levels = [outer] + levels
        outer["outer"] = True
        inner = [lv for lv in levels if not lv.get("outer")]
        zds, zband = _outer_edge_band(gdal, src, valid)

        try:
            sr = arcpy.Describe(kd).spatialReference
        except Exception:
            sr = None

        def attrs(lv):
            return [lv["density"], lv["isopleth"], lv["cumprop"], lv["label"],
                    "True" if lv["primary"] else "False"]

        outputs = []
        steps = (1 if out_lines else 0) + (len(levels) if out_polys else 0)
        try:
            arcpy.SetProgressor("step", "Generating probability contours...",
                                0, max(steps, 1), 1)
        except Exception:
            pass
        done = 0
        if out_lines:
            mh._set_label("Generating probability contours...")
            lines = _gdal_contour_lines(gdal, band, nodata,
                                        [lv["density"] for lv in inner])
            edge = _gdal_contour_lines(gdal, zband, None, [0.5])
            fields = _create_contour_fc(out_lines, "POLYLINE", sr, True)
            with arcpy.da.InsertCursor(out_lines, ["SHAPE@"] + fields) as ins:
                for wkb, _half in edge:                 # the outer edge first
                    geom = arcpy.FromWKB(bytearray(wkb), sr)
                    if geom is not None:
                        ins.insertRow([geom, outer["density"]] + attrs(outer))
                for wkb, value in lines:
                    geom = arcpy.FromWKB(bytearray(wkb), sr)
                    if geom is None:
                        continue
                    lv = inner[_match_index(inner, value)]
                    ins.insertRow([geom, lv["density"]] + attrs(lv))
            outputs.append(out_lines)
            messages.addMessage("Saved contour polylines: {}".format(out_lines))
            done += 1
            try:
                arcpy.SetProgressorPosition(done)
            except Exception:
                pass

        if out_polys:
            mh._set_label("Building contour polygon areas...")
            fields = _create_contour_fc(out_polys, "POLYGON", sr, False)
            with arcpy.da.InsertCursor(out_polys, ["SHAPE@"] + fields) as ins:
                for lv in levels:
                    if lv.get("outer"):
                        parts = _gdal_level_polygons(gdal, zband, None, 0.5, 10.0)
                    else:
                        parts = _gdal_level_polygons(gdal, band, nodata,
                                                     lv["density"], ceiling)
                    for wkb in parts:
                        geom = arcpy.FromWKB(bytearray(wkb), sr)
                        if geom is None or geom.area == 0:
                            continue
                        ins.insertRow([geom] + attrs(lv))
                    done += 1
                    try:
                        arcpy.SetProgressorPosition(done)
                    except Exception:
                        pass
            outputs.append(out_polys)
            messages.addMessage("Saved contour polygon areas: {}".format(out_polys))
        try:
            arcpy.ResetProgressor()
        except Exception:
            pass
    finally:
        zband = None
        zds = None
        band = None
        rds = None
        src = None
        if tmp:
            try:
                arcpy.management.Delete(tmp)
            except Exception:
                pass
    return outputs


def run_probability_contours(kd, out_lines, out_polys, messages, value_kind=None):
    """KD raster -> probability isopleths as polylines and/or polygon areas.

    At least one of ``out_lines`` / ``out_polys`` must be supplied. Lines and
    polygons both come from GDAL's contour generator (no extension license),
    so polygon boundaries coincide exactly with the polylines; a polygon is
    the region at or above its level, holes included. Contours are computed in the
    raster's native CS; if the Output Coordinate System environment is set, the
    finished outputs are reprojected to it (the only environment this tool honors).
    """
    if not out_lines and not out_polys:
        raise arcpy.ExecuteError(
            "Choose at least one output: contour polylines and/or polygon areas.")
    target_cs = arcpy.env.outputCoordinateSystem
    prev = arcpy.env.outputCoordinateSystem
    arcpy.env.outputCoordinateSystem = None       # contour in the raster's native CS
    try:
        outputs = _build_contours(kd, out_lines, out_polys, messages,
                                  value_kind=value_kind)
    finally:
        arcpy.env.outputCoordinateSystem = prev
    if target_cs:
        for fc in outputs:
            try:
                _reproject_in_place(fc, target_cs, messages)
            except Exception as e:
                messages.addWarningMessage("Could not reproject {}: {}".format(fc, e))

    # Output metadata: conceptual description + run summary (incl. the path decision).
    try:
        src_name = arcpy.Describe(kd).baseName
    except Exception:
        src_name = str(kd)
    if _is_marked_proportion(kd):
        path_note = ("Input was recognized (by its metadata marker) as a proportion-"
                     "under-the-surface raster, so the standard proportion levels were "
                     "contoured directly.")
    else:
        path_note = ("Input was treated as a kernel density raster, so volume-weighted "
                     "contour levels were computed from the cell values.")
    kinds = ([("polylines") for _ in [out_lines] if out_lines]
             + [("polygon areas") for _ in [out_polys] if out_polys])
    summary = " ".join([
        "Created {}.".format(_today()),
        "Probability (volume) isopleths of the kernel density raster '{}'.".format(src_name),
        path_note,
        "Outputs: {}.".format(" and ".join(kinds))])
    for fc in outputs:
        if not mh.is_memory_workspace(str(fc)):
            # memory outputs cannot hold metadata (see _kd_write_meta)
            tmeta.set_entity_and_fields(fc, entity=_CONTOUR_ENTITY,
                                        fields=_contour_fields_for(fc),
                                        messages=messages)
        try:
            title = arcpy.Describe(fc).baseName
        except Exception:
            title = "Probability Contours"
        _kd_write_meta(fc, title=title, summary=summary, description=_DESC_CONTOURS,
                       messages=messages)
    return outputs


# =========================================================================== #
# Kernel Density generator (custom KDE for point inputs)
# =========================================================================== #
# Finite-support radial kernels K(u), u = distance/bandwidth in [0,1] (0 beyond).
_KFUNC = {
    "QUARTIC":      lambda u: (1.0 - u * u) ** 2,        # Esri Kernel Density method
    "UNIFORM":      lambda u: np.ones_like(u),
    "TRIANGULAR":   lambda u: 1.0 - u,
    "EPANECHNIKOV": lambda u: 1.0 - u * u,
    "TRIWEIGHT":    lambda u: (1.0 - u * u) ** 3,
    "TRICUBE":      lambda u: (1.0 - u ** 3) ** 3,
    "COSINE":       lambda u: np.cos(np.pi * u / 2.0),
}
KERNEL_CHOICES = [
    ("Quartic (Esri KD method)", "QUARTIC"),
    ("Uniform", "UNIFORM"),
    ("Triangular", "TRIANGULAR"),
    ("Epanechnikov", "EPANECHNIKOV"),
    ("Triweight", "TRIWEIGHT"),
    ("Tricube", "TRICUBE"),
    ("Cosine", "COSINE"),
]
_KERNEL_KEY = {label: key for label, key in KERNEL_CHOICES}

DIST_PLANAR = "Planar"
DIST_GEODESIC = "Geodesic"

OUTV_DENSITIES = "Densities"
OUTV_COUNTS = "Expected counts"
OUTV_PROPORTION = "Proportion under curve"
OUTPUT_VALUES = [OUTV_DENSITIES, OUTV_COUNTS, OUTV_PROPORTION]

# m^2 per area unit ("MAP" = square map units, resolved per coordinate system).
AREA_UNITS = [
    ("Square map units", "MAP"), ("Square meters", 1.0), ("Square kilometers", 1.0e6),
    ("Square feet", 0.09290304), ("Square miles", 2589988.110336),
    ("Acres", 4046.8564224), ("Hectares", 10000.0),
]
_AREA_M2 = dict(AREA_UNITS)
_M_PER_DEG = 111320.0     # nominal meters per degree of latitude (window sizing only)


def kernel_key(label):
    return _KERNEL_KEY.get(label, "QUARTIC")


def _kernel_norm(key):
    """2-D area-normalization c so that the integral over the plane of
    (c/h^2)*K(d/h) == 1, i.e. c = 1/(2*pi* integral_0^1 K(u) u du). For QUARTIC
    this evaluates to 3/pi, matching Esri exactly."""
    u = np.linspace(0.0, 1.0, 200001)
    return 1.0 / (2.0 * np.pi * float(np.trapezoid(_KFUNC[key](u) * u, u)))


def _weighted_median(values, weights):
    order = np.argsort(values, kind="mergesort")
    v = values[order]
    cw = np.cumsum(weights[order])
    idx = int(np.searchsorted(cw, 0.5 * cw[-1]))
    return float(v[min(idx, v.size - 1)])


def default_bandwidth_m(x_m, y_m, w, dist_to_center):
    """Esri default search radius (Silverman 2-D rule of thumb), in meters:

        SearchRadius = 0.9 * min( SD, sqrt(1/ln2) * Dm ) * n^(-1/5)

    SD = weighted standard distance to the weighted mean center, Dm = weighted
    median of those distances, n = summed weights. ``dist_to_center`` computes the
    distance array (planar meters, or geodesic meters for geographic data)."""
    wsum = float(w.sum())
    xm = float((w * x_m).sum() / wsum)
    ym = float((w * y_m).sum() / wsum)
    d = dist_to_center(xm, ym)
    sd = math.sqrt(float((w * d * d).sum() / wsum))
    dm = _weighted_median(d, w)
    return 0.9 * min(sd, math.sqrt(1.0 / math.log(2.0)) * dm) * (wsum ** -0.2)


def _same_cs(a, b):
    """True if two arcpy spatial references denote the same coordinate system."""
    try:
        if getattr(a, "factoryCode", 0) and getattr(b, "factoryCode", 0):
            return int(a.factoryCode) == int(b.factoryCode)
        return a.name == b.name
    except Exception:
        return False


def _osr_sr(arcpy_sr):
    """Build an osr SpatialReference (traditional GIS axis order) from an arcpy one."""
    from osgeo import osr
    s = osr.SpatialReference()
    code = int(getattr(arcpy_sr, "factoryCode", 0) or 0)
    if not (code > 0 and s.ImportFromEPSG(code) == 0):
        if s.ImportFromWkt(arcpy_sr.exportToString()) != 0:
            raise arcpy.ExecuteError(
                "Could not interpret the coordinate system '{}' for reprojection.".format(
                    getattr(arcpy_sr, "name", "?")))
    try:
        s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except Exception:
        pass
    return s


def _transform_xy(src_sr, dst_sr, x, y):
    """Reproject coordinate arrays from src_sr to dst_sr via GDAL/osr (vectorized)."""
    from osgeo import osr
    ct = osr.CoordinateTransformation(_osr_sr(src_sr), _osr_sr(dst_sr))
    pts = np.asarray(ct.TransformPoints(np.column_stack([x, y]).tolist()))
    return pts[:, 0].copy(), pts[:, 1].copy()      # x', y' (traditional GIS order)


def _projected_to_latlon(sr, xflat, yflat):
    """Unproject projected coordinates to (lat, lon) via GDAL/osr."""
    from osgeo import osr
    src = _osr_sr(sr)
    tgt = src.CloneGeogCS()
    try:
        tgt.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except Exception:
        pass
    ct = osr.CoordinateTransformation(src, tgt)
    pts = np.asarray(ct.TransformPoints(np.column_stack([xflat, yflat]).tolist()))
    return pts[:, 1], pts[:, 0]      # lat, lon


def _write_grid(out2d, xmin, ymax, csx, csy, sr, out_raster, messages):
    """Write a float32 grid (NaN -> NoData) to ``out_raster`` with statistics and a
    histogram carried via a scratch GeoTIFF + CopyRaster (fast-stats standing rule)."""
    gdal = mh._gdal()
    h, w = out2d.shape
    arr = np.where(np.isfinite(out2d), out2d, _NODATA).astype(np.float32)
    scratch = arcpy.env.scratchFolder
    tif = arcpy.CreateUniqueName("kd_gen.tif", scratch)
    ds = gdal.GetDriverByName("GTiff").Create(tif, w, h, 1, gdal.GDT_Float32,
                                              options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform((xmin, csx, 0.0, ymax, 0.0, -csy))
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(_NODATA)
    band.WriteArray(arr)
    band.FlushCache()
    stats = mh._RasterStats()
    finite = arr[arr != _NODATA]
    if finite.size:
        stats.add(finite)
    out_stats = mh._finalize_stats(band, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = mh._write_gtiff_stats(band, out_stats)
    band.FlushCache(); ds.FlushCache(); ds = None

    prev = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        arcpy.management.CopyRaster(tif, out_raster)
    finally:
        arcpy.env.pyramid = prev
    try:
        arcpy.management.DefineProjection(out_raster, sr)
    except Exception:
        pass
    if out_stats and out_stats.get("on_raster"):
        messages.addMessage("Statistics and histogram were written with the raster.")
    elif not mh._apply_known_statistics(out_raster, out_stats, messages):
        try:
            arcpy.management.CalculateStatistics(out_raster)
        except Exception:
            pass
    try:
        arcpy.management.Delete(tif)
    except Exception:
        pass


def _resolve_extent(x, y, sr, geographic, radius_m, mpu):
    """(xmin, ymin, xmax, ymax) in dataset coordinate units. Honors env.extent;
    otherwise the input bounding box expanded by the search radius.

    LIMITATION: for geographic data this uses raw longitudes, so a kernel that straddles
    the antimeridian (+/-180 deg) is clipped at the date line rather than wrapping. This
    is irrelevant for typical study areas; documented as a known help-file limitation."""
    ext = arcpy.env.extent
    if ext:
        try:
            return float(ext.XMin), float(ext.YMin), float(ext.XMax), float(ext.YMax)
        except Exception:
            p = str(ext).split()
            if len(p) >= 4:
                return float(p[0]), float(p[1]), float(p[2]), float(p[3])
    xmin, ymin, xmax, ymax = float(x.min()), float(y.min()), float(x.max()), float(y.max())
    if geographic:
        clat = math.cos(math.radians((ymin + ymax) / 2.0)) or 1e-6
        ex = radius_m / (_M_PER_DEG * abs(clat))
        ey = radius_m / _M_PER_DEG
    else:
        ex = ey = radius_m / mpu
    return xmin - ex, ymin - ey, xmax + ex, ymax + ey


def _resolve_cell_size(cell_size, extent, geographic, mpu, radius_units=None):
    """Cell size (csx, csy) in dataset units. Tool param -> env.cellSize ->
    Esri default (shorter extent side / 250).

    ``radius_units`` is the search radius expressed in the dataset's coordinate units;
    it is used as a last resort when the input extent is degenerate (collinear or
    coincident points) so the cell size never resolves to zero (which would divide by
    zero when sizing the grid)."""
    xmin, ymin, xmax, ymax = extent
    val = None
    if cell_size:
        try:
            val = float(cell_size)
        except Exception:
            try:
                val = float(arcpy.Describe(cell_size).meanCellWidth)
            except Exception:
                val = None
    if val is None:
        env_cs = arcpy.env.cellSize
        if env_cs not in (None, "", "MAXOF", "MINOF"):
            try:
                val = float(env_cs)
            except Exception:
                try:
                    val = float(arcpy.Describe(env_cs).meanCellWidth)
                except Exception:
                    val = None
    if val is None:
        snap = arcpy.env.snapRaster          # fall back to the snap raster's cell size
        if snap:
            try:
                val = float(arcpy.Describe(snap).meanCellWidth)
            except Exception:
                val = None
    if val is None or val <= 0:
        span = min(xmax - xmin, ymax - ymin)
        if span <= 0:                        # collinear points: use the non-zero side
            span = max(xmax - xmin, ymax - ymin)
        if span > 0:
            val = span / 250.0
        elif radius_units and radius_units > 0:   # single/coincident points: derive from radius
            val = radius_units / 50.0
        else:
            raise arcpy.ExecuteError(
                "Could not determine an output cell size: the input points have no "
                "spatial extent. Specify an Output cell size or a search radius.")
    return val, val


def default_cell_size(in_features):
    """Esri-style default Output cell size for the tool dialog: tool param is handled
    by the caller; here we resolve env.cellSize -> env.snapRaster cell size -> the
    shorter side of the extent (env.extent, else the feature extent in the output CS)
    divided by 250. Returns a float, or None if it cannot be determined."""
    cs = arcpy.env.cellSize
    if cs not in (None, "", "MAXOF", "MINOF"):
        try:
            return float(cs)
        except Exception:
            try:
                return float(arcpy.Describe(cs).meanCellWidth)
            except Exception:
                pass
    snap = arcpy.env.snapRaster
    if snap:
        try:
            return float(arcpy.Describe(snap).meanCellWidth)
        except Exception:
            pass
    def _span_over_250(w, h):
        # Shorter side / 250, but fall back to the longer side for a degenerate
        # (zero-width or zero-height) extent; None if there is no extent at all.
        span = min(w, h)
        if span <= 0:
            span = max(w, h)
        return (span / 250.0) if span > 0 else None
    try:
        env_ext = arcpy.env.extent
        if env_ext not in (None, ""):
            try:
                return _span_over_250(float(env_ext.width), float(env_ext.height))
            except Exception:
                pass
        d = arcpy.Describe(in_features)
        ext = d.extent
        out_cs = arcpy.env.outputCoordinateSystem
        if out_cs and not _same_cs(d.spatialReference, out_cs):
            ext = _ct.project_geometry(ext, out_cs)
        return _span_over_250(float(ext.width), float(ext.height))
    except Exception:
        return None


def _snap(extent, csx, csy):
    """Shift the extent origin to align with env.snapRaster, if set."""
    snap = arcpy.env.snapRaster
    if not snap:
        return extent
    try:
        d = arcpy.Describe(snap)
        ox, oy = float(d.extent.XMin), float(d.extent.YMin)
        xmin, ymin, xmax, ymax = extent
        xmin = ox + math.floor((xmin - ox) / csx) * csx
        ymin = oy + math.floor((ymin - oy) / csy) * csy
        return xmin, ymin, xmax, ymax
    except Exception:
        return extent


def _max_local_scale(sr, x, y, mpu):
    """Largest ratio of projected meters to true (geodesic) meters at the
    corners and center of the data: 1.0 where the projection is true to
    scale, about 2 for Web Mercator at 60 degrees. Never below 1."""
    try:
        xs = np.array([x.min(), x.max(), x.min(), x.max(), (x.min() + x.max()) / 2.0])
        ys = np.array([y.min(), y.min(), y.max(), y.max(), (y.min() + y.max()) / 2.0])
        d = max(1000.0 / mpu, 1e-6)                      # about a kilometer
        lat0, lon0 = _projected_to_latlon(sr, xs, ys)
        lat1, lon1 = _projected_to_latlon(sr, xs + d, ys)
        lat2, lon2 = _projected_to_latlon(sr, xs, ys + d)
        ex = wg.haversine_distance(lat0, lon0, lat1, lon1)
        ny = wg.haversine_distance(lat0, lon0, lat2, lon2)
        with np.errstate(invalid="ignore", divide="ignore"):
            k = np.concatenate([d * mpu / ex, d * mpu / ny])
        k = k[np.isfinite(k)]
        return float(max(1.0, k.max() * 1.05)) if k.size else 1.0
    except Exception:
        return 1.0


def _kd_mask(xmin, ymax, csx, csy, ncols, nrows, sr, messages):
    """The Mask environment on the output grid: a boolean array (True = keep),
    or None when no mask is set or it could not be read. Raster masks keep
    their valid cells; feature masks keep the cells whose centers fall inside
    a feature. Uses GDAL only, so no Spatial Analyst license is needed."""
    mask = arcpy.env.mask
    if not mask:
        return None
    gdal = mh._gdal()
    gt = (xmin, csx, 0.0, ymax, 0.0, -csy)
    try:
        wkt = sr.exportToString().split(";")[0]
    except Exception:
        wkt = ""
    try:
        d = arcpy.Describe(mask)
        if hasattr(d, "bandCount"):                       # a raster mask
            path, tmp = mh._gdal_source_path(mask, arcpy.env.scratchFolder)
            try:
                try:
                    src_wkt = d.spatialReference.exportToString().split(";")[0]
                except Exception:
                    src_wkt = None
                vrt = gdal.Warp(
                    "", path, format="VRT", outputType=gdal.GDT_Float64,
                    srcSRS=(src_wkt or None), dstSRS=(wkt or None),
                    outputBounds=(xmin, ymax - nrows * csy, xmin + ncols * csx, ymax),
                    width=ncols, height=nrows, resampleAlg="near",
                    dstNodata=float("nan"))
                keep = np.isfinite(vrt.GetRasterBand(1).ReadAsArray())
                vrt = None
            finally:
                if tmp:
                    try:
                        arcpy.management.Delete(tmp)
                    except Exception:
                        pass
        else:                                             # a feature mask
            from osgeo import ogr
            msr = d.spatialReference
            geoms = []
            with arcpy.da.SearchCursor(mask, ["SHAPE@"]) as cur:
                for (g,) in cur:
                    if g is None:
                        continue
                    if getattr(g, "hasCurves", False):    # true curves: densify
                        step = (csx if (msr is not None and msr.type == sr.type)
                                else max(g.length / 500.0, 1e-9))
                        g = g.densify("DISTANCE", step, step)
                    if msr is not None and not _same_cs(msr, sr):
                        g = _ct.project_geometry(g, sr)
                    geoms.append(g)
            if not geoms:
                messages.addWarningMessage(
                    "The Mask environment has no features; it was ignored.")
                return None
            shp = arcpy.CreateUniqueName("kd_mask.shp", arcpy.env.scratchFolder)
            with mh.neutral_grid_env():
                arcpy.management.CopyFeatures(geoms, shp)
            mds = gdal.GetDriverByName("MEM").Create("", ncols, nrows, 1, gdal.GDT_Byte)
            mds.SetGeoTransform(gt)
            vec = ogr.Open(shp)
            try:
                gdal.RasterizeLayer(mds, [1], vec.GetLayer(0), burn_values=[1])
            finally:
                vec = None
            mds.FlushCache()
            keep = mds.GetRasterBand(1).ReadAsArray().astype(bool)
            mds = None
            try:
                arcpy.management.Delete(shp)
            except Exception:
                pass
        messages.addMessage("Honoring Mask environment.")
        return keep
    except Exception as e:                                # noqa: BLE001
        messages.addWarningMessage(
            "Could not apply the Mask environment: {}".format(e))
        return None


def run_kernel_density(points, pop_field, out_raster, cell_size, search_radius,
                       kernel, distance_method, out_value, area_unit, messages):
    """Custom kernel-density surface from point features (see module docstring and
    the tool help). Returns the output raster path."""
    out_raster = mh.resolve_raster_output(out_raster, messages)
    key = kernel_key(kernel)
    cnorm = _kernel_norm(key)
    kfunc = _KFUNC[key]

    desc = arcpy.Describe(points)
    sr = desc.spatialReference

    # Read points (respects any selection on a layer) + weights, in the input CS.
    fields = ["SHAPE@X", "SHAPE@Y"] + ([pop_field] if pop_field and pop_field.upper() != "NONE" else [])
    xs_l, ys_l, ws_l = [], [], []
    # explode_to_points: every point of a multipoint feature (SHAPE@X/Y is
    # otherwise the feature's centroid); the weight repeats for each of them.
    with arcpy.da.SearchCursor(points, fields, explode_to_points=True) as cur:
        for row in cur:
            if row[0] is None:
                continue
            xs_l.append(row[0]); ys_l.append(row[1])
            ws_l.append(float(row[2]) if len(fields) == 3 and row[2] is not None else 1.0)
    if not xs_l:
        raise arcpy.ExecuteError("No input points were found.")
    x = np.array(xs_l, float); y = np.array(ys_l, float); w = np.array(ws_l, float)
    # Validate population weights up front so a bad field gives a clear, weight-specific
    # message instead of the downstream "points may be coincident" bandwidth error.
    if pop_field and pop_field.upper() != "NONE":
        if np.any(w < 0):
            raise arcpy.ExecuteError(
                "The population field '{}' has negative values; weights must be zero or "
                "positive.".format(pop_field))
        if float(w.sum()) <= 0:
            raise arcpy.ExecuteError(
                "The population field '{}' sums to zero, so there is no weight to "
                "distribute. Check the field values.".format(pop_field))
    messages.addMessage("Read {:,} points (total weight {:.4g}).".format(x.size, float(w.sum())))
    notes = []          # plain-English notes about data-driven choices, for the summary

    # Output Coordinate System environment: project the points (and hence the whole
    # analysis -- grid, distances, extent) into the target CS up front, rather than
    # resampling a finished raster.
    out_cs = arcpy.env.outputCoordinateSystem
    if out_cs and not _same_cs(sr, out_cs):
        messages.addMessage("Honoring Output Coordinate System: {}.".format(out_cs.name))
        # Some projections cannot represent the input points (e.g. a gnomonic,
        # stereographic, or orthographic projection centered on the opposite side of
        # the Earth); osr either raises or returns infinite / NaN. Fail clearly.
        bad_cs = (
            "The input points cannot be represented in the specified Output "
            "Coordinate System ('{}'): one or more points project to an undefined "
            "or infinite location (for example, a gnomonic, stereographic, or "
            "orthographic projection centered on the opposite side of the Earth "
            "from the data). Choose an Output Coordinate System appropriate for "
            "the location of the input points.".format(out_cs.name))
        try:
            x, y = _transform_xy(sr, out_cs, x, y)
        except arcpy.ExecuteError:
            raise
        except Exception:
            raise arcpy.ExecuteError(bad_cs)
        if not (np.isfinite(x).all() and np.isfinite(y).all()) or \
                np.max(np.abs(x)) > 1e15 or np.max(np.abs(y)) > 1e15:
            raise arcpy.ExecuteError(bad_cs)
        sr = out_cs
        notes.append("Output Coordinate System '{}' was applied.".format(out_cs.name))

    geographic = (sr is not None and sr.type == "Geographic")
    mpu = 1.0 if geographic else float(getattr(sr, "metersPerUnit", 1.0) or 1.0)

    # Resolve distance method (geographic data is always geodesic).
    geod = (distance_method == DIST_GEODESIC) or geographic
    if geographic and distance_method == DIST_PLANAR:
        messages.addWarningMessage(
            "Working coordinate system is geographic; planar distances are not "
            "meaningful -- using geodesic.")
        notes.append("Input is in geographic coordinates, so geodesic distances were used.")

    # Geodesic distances need the points in lat/lon (the data may be projected, in
    # which case x/y are NOT degrees). Compute once here -- reused for the Silverman
    # default radius AND the accumulation below.
    plat = plon = None
    if geod:
        if geographic:
            plat, plon = y, x                                  # lat=y, lon=x
        else:
            plat, plon = _projected_to_latlon(sr, x, y)

    # Search radius in meters (+ a distance-to-center helper for the default).
    if geod:
        def dist_to_center(mlon, mlat):
            return wg.haversine_distance(mlat, mlon, plat, plon)   # meters
    else:
        def dist_to_center(xm, ym):
            return np.hypot((x - xm) * mpu, (y - ym) * mpu)        # meters
    if search_radius:
        parts = str(search_radius).strip().split()
        rv = float(parts[0]); ru = parts[1] if len(parts) > 1 else "Unknown"
        if ru.lower() in ("", "unknown"):
            h_m = rv if geographic else rv * mpu
        else:
            h_m = rv * arcpy.LinearUnitConversionFactor(ru, "Meters")
        default_used = False
    elif geod:
        h_m = default_bandwidth_m(plon, plat, w, dist_to_center)   # work in lat/lon
        default_used = True
    else:
        h_m = default_bandwidth_m(x * mpu, y * mpu, w, dist_to_center)
        default_used = True
    if not np.isfinite(h_m) or h_m <= 0:
        raise arcpy.ExecuteError(
            "The search radius is zero or undefined (points may be coincident); "
            "please specify a search radius.")
    messages.addMessage("Search radius = {:.4g} m{}.".format(
        h_m, " (Silverman default)" if default_used else ""))

    # Cell size from the UN-expanded extent (Esri style: env.extent else the data
    # bbox); the grid extent is then expanded by the search radius so tails aren't
    # clipped. (When run from the dialog the cell size is already filled in.)
    eext = arcpy.env.extent
    if eext:
        try:
            cs_ext = (float(eext.XMin), float(eext.YMin), float(eext.XMax), float(eext.YMax))
        except Exception:
            p = str(eext).split()
            cs_ext = ((float(p[0]), float(p[1]), float(p[2]), float(p[3])) if len(p) >= 4
                      else (float(x.min()), float(y.min()), float(x.max()), float(y.max())))
    else:
        cs_ext = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
    radius_units = (h_m / _M_PER_DEG) if geographic else (h_m / mpu)
    csx, csy = _resolve_cell_size(cell_size, cs_ext, geographic, mpu, radius_units)
    # Geodesic distances on PROJECTED data: a true distance of h_m meters can
    # span more (or fewer) projected meters where the projection stretches
    # the ground (Web Mercator at 60 degrees doubles it), so the margin is
    # sized by the local scale, not by h_m alone.
    scale_max = 1.0
    if geod and not geographic:
        scale_max = _max_local_scale(sr, x, y, mpu)
    extent = _resolve_extent(x, y, sr, geographic, h_m * scale_max, mpu)
    extent = _snap(extent, csx, csy)
    xmin, ymin, xmax, ymax = extent
    ncols = max(1, int(round((xmax - xmin) / csx)))
    nrows = max(1, int(round((ymax - ymin) / csy)))
    xmax = xmin + ncols * csx
    ymax = ymin + nrows * csy
    xs = xmin + (np.arange(ncols) + 0.5) * csx
    ys = ymax - (np.arange(nrows) + 0.5) * csy
    messages.addMessage("Output grid: {} cols x {} rows, cell size {:.6g}.".format(ncols, nrows, csx))

    # Geodesic: cell-center lat/lon grids (point lat/lon was computed earlier).
    if geod:
        if geographic:
            LATg = np.repeat(ys[:, None], ncols, axis=1)
            LONg = np.repeat(xs[None, :], nrows, axis=0)
        else:
            X, Y = np.meshgrid(xs, ys)
            lat, lon = _projected_to_latlon(sr, X.ravel(), Y.ravel())
            LATg = lat.reshape(X.shape); LONg = lon.reshape(X.shape)

    # Geodesic on projected data: the TRUE ground size of every cell (meters),
    # from the distances between neighboring cell centers. It sizes each
    # point's search window and gives the cell areas; the projected cell size
    # is wrong for both wherever the projection distorts scale.
    cellw_true = cellh_true = None
    if geod and not geographic:
        if ncols > 1:
            dxm = wg.haversine_distance(LATg[:, 1:], LONg[:, 1:],
                                        LATg[:, :-1], LONg[:, :-1])
            cellw_true = np.concatenate([dxm, dxm[:, -1:]], axis=1)
        else:
            cellw_true = np.full((nrows, ncols), csx * mpu)
        if nrows > 1:
            dym = wg.haversine_distance(LATg[1:, :], LONg[1:, :],
                                        LATg[:-1, :], LONg[:-1, :])
            cellh_true = np.concatenate([dym, dym[-1:, :]], axis=0)
        else:
            cellh_true = np.full((nrows, ncols), csy * mpu)

    # Accumulate density (per m^2).
    dens = np.zeros((nrows, ncols), dtype=np.float64)
    coef = cnorm / (h_m * h_m)
    rnative = h_m / mpu                            # radius in projected units
    mh._set_label("Accumulating kernel density...")
    try:
        arcpy.SetProgressor("step", "Accumulating kernel density...", 0,
                            max(int(x.size), 1), 1)
    except Exception:
        pass
    tick = max(1, int(x.size) // 100)
    for i in range(x.size):
        if i % tick == 0:
            try:
                arcpy.SetProgressorPosition(i)
            except Exception:
                pass
        xp, yp = x[i], y[i]
        if geographic:
            clat = math.cos(math.radians(yp)) or 1e-6
            rx = h_m / (_M_PER_DEG * abs(clat)); ry = h_m / _M_PER_DEG
        elif cellw_true is not None:
            # window half-widths from the true cell size at the point (a 15%
            # margin covers the change of scale across the window)
            jc = min(max(int((xp - xmin) / csx), 0), ncols - 1)
            ic = min(max(int((ymax - yp) / csy), 0), nrows - 1)
            rx = 1.15 * h_m / max(float(cellw_true[ic, jc]), 1e-9) * csx
            ry = 1.15 * h_m / max(float(cellh_true[ic, jc]), 1e-9) * csy
        else:
            rx = ry = rnative
        jmin = max(0, int(math.floor((xp - rx - xmin) / csx)))
        jmax = min(ncols - 1, int(math.ceil((xp + rx - xmin) / csx)))
        imin = max(0, int(math.floor((ymax - (yp + ry)) / csy)))
        imax = min(nrows - 1, int(math.ceil((ymax - (yp - ry)) / csy)))
        if jmin > jmax or imin > imax:
            continue
        if geod:
            D = wg.haversine_distance(plat[i], plon[i],
                                      LATg[imin:imax + 1, jmin:jmax + 1],
                                      LONg[imin:imax + 1, jmin:jmax + 1])
        else:
            dx = (xs[jmin:jmax + 1][None, :] - xp) * mpu
            dy = (ys[imin:imax + 1][:, None] - yp) * mpu
            D = np.hypot(dx, dy)
        inside = D < h_m
        if inside.any():
            contrib = np.zeros_like(D)
            contrib[inside] = w[i] * coef * kfunc(D[inside] / h_m)
            dens[imin:imax + 1, jmin:jmax + 1] += contrib
    try:
        arcpy.ResetProgressor()
    except Exception:
        pass

    # Cell area (m^2): per-row spheroidal for geographic, the true ground area
    # for a geodesic analysis of projected data, constant otherwise.
    if cellw_true is not None:
        area2d = cellw_true * cellh_true
    elif geographic:
        lat_top = np.radians(ys + csy / 2.0); lat_bot = np.radians(ys - csy / 2.0)
        row_area = (wg.HAVERSINE_RADIUS ** 2) * (np.sin(lat_top) - np.sin(lat_bot)) * math.radians(csx)
        area2d = np.repeat(row_area[:, None], ncols, axis=1)
    else:
        area2d = np.full((nrows, ncols), (csx * mpu) * (csy * mpu), dtype=np.float64)

    # Build the requested output surface.
    if out_value == OUTV_DENSITIES:
        factor = _AREA_M2[area_unit]
        if factor == "MAP":
            if geographic:
                messages.addWarningMessage(
                    "'Square map units' is not meaningful for geographic data; "
                    "reporting density per square meter.")
                factor = 1.0
            else:
                factor = mpu * mpu
        out = dens * factor
    elif out_value == OUTV_COUNTS:
        out = dens * area2d
    else:  # proportion under curve (volume-weighted CDF from below; 0 edge -> 1 peak)
        out = np.full(dens.shape, np.nan, dtype=np.float64)   # background -> NoData
        positive = dens > 0
        total_vol = float((dens * area2d).sum())
        if total_vol > 0 and positive.any():
            vol = (dens * area2d).ravel()
            d_flat = dens.ravel()
            uden, inv = np.unique(d_flat, return_inverse=True)
            vsum = np.zeros(uden.size)
            np.add.at(vsum, inv, vol)
            cum = (np.cumsum(vsum) / total_vol)[inv].reshape(dens.shape)
            out[positive] = cum[positive]     # only positive-density cells carry a value

    # Mask environment, applied to the finished grid with GDAL (no Spatial
    # Analyst): cells outside the mask become NoData.
    keep = _kd_mask(xmin, ymax, csx, csy, ncols, nrows, sr, messages)
    if keep is not None:
        out = np.where(keep, out, np.nan)

    _write_grid(out, xmin, ymax, csx, csy, sr, out_raster, messages)

    # Output metadata: conceptual description + plain-English run summary.
    try:
        src_name = arcpy.Describe(points).baseName
    except Exception:
        src_name = str(points)
    if out_value == OUTV_DENSITIES:
        out_sentence = "Output values: densities (per {}).".format(area_unit)
        desc_extra = "Cell values are estimated density (points per chosen area unit)."
    elif out_value == OUTV_COUNTS:
        out_sentence = "Output values: expected point counts per cell."
        desc_extra = ("Cell values are expected point counts per cell (they sum to the "
                      "total number of input points).")
    else:
        out_sentence = "Output values: proportion under the surface (0 to 1)."
        desc_extra = ("Cell values are the proportion of the total volume under the "
                      "surface (about 0 at the edge to 1.0 at the peak).")
    summary = " ".join(
        ["Created {}.".format(_today()),
         "Kernel density surface from {:,} points in '{}'.".format(x.size, src_name),
         "Kernel: {}.".format(kernel),
         "Distances: {}.".format("geodesic" if geod else "planar"),
         "Search radius: {:.4g} m{}.".format(h_m, " (Silverman default)" if default_used else ""),
         "Output cell size: {:.6g}.".format(csx),
         out_sentence]
        + (["Population field: {}.".format(pop_field)]
           if (pop_field and pop_field.upper() != "NONE") else [])
        + notes)
    try:
        title = arcpy.Describe(out_raster).baseName
    except Exception:
        title = "Kernel Density Enhanced"
    _kd_write_meta(out_raster, title=title, summary=summary,
                   description=_DESC_KDE + " " + desc_extra,
                   proportion=(out_value == OUTV_PROPORTION), messages=messages,
                   kind_tag=(_COUNT_TAG if out_value == OUTV_COUNTS
                             else _DENS_TAG if out_value == OUTV_DENSITIES
                             else None))
    return out_raster


# ---------------------------------------------------------------------------
#  default output symbology (2026-09-07, Jeff's spec)
# ---------------------------------------------------------------------------
_KD_SYMB_TEMPLATE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "kd_density_symbology_template.lyrx")


def kd_density_lyrx(out_raster, messages=None):
    """Create a .lyrx beside ``out_raster`` carrying Jeff's kernel-density
    ramp (a dark-purple-to-yellow continuous stretch exported from his
    Oak_Creek_Species_Locations_KDens.lyrx, shipped as
    kd_density_symbology_template.lyrx) with the layer 30% transparent, so
    the surface reads over imagery the moment the tool adds it to the map.
    Same proven mechanics as mahalanobis.default_stretch_lyrx:
    MakeRasterLayer -> SaveToLayerFile (which captures the real
    stretchStats -- a colorizer with no stats will not render), then
    transplant the template's colorizer and transparency onto the saved
    document. Returns the .lyrx path (set it as the output parameter's
    ``.symbology``) or None; never raises."""
    import json
    try:
        path = arcpy.Describe(out_raster).catalogPath
        name = os.path.splitext(os.path.basename(path))[0]
    except Exception as e:                                   # noqa: BLE001
        if messages is not None:
            messages.addWarningMessage(
                "Could not create layer file: {}".format(e))
        return None
    lyr_dir = mh.lyrx_dir_for(os.path.dirname(path))
    if lyr_dir is None or not os.path.exists(_KD_SYMB_TEMPLATE):
        return None
    out_lyrx = os.path.join(lyr_dir, name + "_symbology.lyrx")
    tmp = "jenness_kd_symb_tmp"
    try:
        try:
            arcpy.management.Delete(tmp)
        except Exception:                                    # noqa: BLE001
            pass
        arcpy.management.MakeRasterLayer(path, tmp)
        if os.path.exists(out_lyrx):      # SaveToLayerFile refuses to overwrite
            os.remove(out_lyrx)
        arcpy.management.SaveToLayerFile(tmp, out_lyrx, "ABSOLUTE")
    except Exception as e:                                   # noqa: BLE001
        if messages is not None:
            messages.addWarningMessage(
                "Could not create layer file: {}".format(e))
        return None
    finally:
        try:
            arcpy.management.Delete(tmp)
        except Exception:                                    # noqa: BLE001
            pass
    try:
        with open(out_lyrx, encoding="utf-8") as f:
            lyr = json.load(f)
        with open(_KD_SYMB_TEMPLATE, encoding="utf-8") as f:
            tpl = json.load(f)
        out_ld = lyr["layerDefinitions"][0]
        tpl_ld = tpl["layerDefinitions"][0]
        col = dict(tpl_ld.get("colorizer") or {})
        # keep the stats SaveToLayerFile captured for THIS raster; the
        # template's stats describe Jeff's original dataset
        saved = out_ld.get("colorizer") or {}
        for key in ("stretchStats", "stretchClasses"):
            if key in saved:
                col[key] = saved[key]
            else:
                col.pop(key, None)
        col.pop("colorScheme", None)
        # zero-density cells are honest values (kept 0, not NoData, so
        # raster algebra keeps working) -- but they should not paint the
        # whole analysis rectangle, so display value 0 as transparent
        # (the same thing as Pro's "Display background value" checkbox)
        col["displayBackgroundValue"] = True
        col["backgroundValue"] = 0.0
        col["backgroundColor"] = {"type": "CIMRGBColor",
                                  "values": [255, 255, 255, 0]}
        out_ld["colorizer"] = col
        out_ld["transparency"] = tpl_ld.get("transparency", 30)
        out_ld["name"] = name
        with open(out_lyrx, "w", encoding="utf-8") as f:
            json.dump(lyr, f)
    except Exception as e:                                   # noqa: BLE001
        if messages is not None:
            messages.addWarningMessage(
                "Layer file created but symbology could not be "
                "applied: {}".format(e))
    return out_lyrx


def _kd_stroke(width_pt, rgb):
    return {"type": "CIMSolidStroke", "enable": True,
            "capStyle": "Round", "joinStyle": "Round",
            "lineStyle3D": "Strip", "miterLimit": 10,
            "width": width_pt,
            "color": {"type": "CIMRGBColor",
                      "values": list(rgb) + [100]}}


def kd_contour_lyrx(out_fc, messages=None):
    """Create a .lyrx beside ``out_fc`` (the probability-contour polylines
    or polygon areas) drawing the PRIMARY isopleths (5/25/50/75/95 percent,
    Primary = "True") as heavier dark lines and the minor levels as
    lighter gray ones -- the two-weight display the Primary field was
    designed for. Polygons draw hollow with the same outline weights, so
    the nested isopleths all stay visible. Returns the .lyrx path or
    None; never raises."""
    import json
    try:
        d = arcpy.Describe(out_fc)
        path = d.catalogPath
        shape = (getattr(d, "shapeType", "") or "").lower()
        name = os.path.splitext(os.path.basename(path))[0]
    except Exception as e:                                   # noqa: BLE001
        if messages is not None:
            messages.addWarningMessage(
                "Could not create layer file: {}".format(e))
        return None
    lyr_dir = mh.lyrx_dir_for(os.path.dirname(path))
    if lyr_dir is None:
        return None
    out_lyrx = os.path.join(lyr_dir, name + "_symbology.lyrx")
    tmp = "jenness_kd_ctr_tmp"
    try:
        try:
            arcpy.management.Delete(tmp)
        except Exception:                                    # noqa: BLE001
            pass
        arcpy.management.MakeFeatureLayer(path, tmp)
        if os.path.exists(out_lyrx):      # SaveToLayerFile refuses to overwrite
            os.remove(out_lyrx)
        arcpy.management.SaveToLayerFile(tmp, out_lyrx, "ABSOLUTE")
    except Exception as e:                                   # noqa: BLE001
        if messages is not None:
            messages.addWarningMessage(
                "Could not create layer file: {}".format(e))
        return None
    finally:
        try:
            arcpy.management.Delete(tmp)
        except Exception:                                    # noqa: BLE001
            pass
    try:
        with open(out_lyrx, encoding="utf-8") as f:
            lyr = json.load(f)
        out_ld = lyr["layerDefinitions"][0]

        def symbol(width_pt, rgb):
            stroke = _kd_stroke(width_pt, rgb)
            if shape == "polygon":
                sym = {"type": "CIMPolygonSymbol",
                       "symbolLayers": [stroke]}   # hollow: outline only
            else:
                sym = {"type": "CIMLineSymbol", "symbolLayers": [stroke]}
            return {"type": "CIMSymbolReference", "symbol": sym}

        def uv_class(value, label, sym):
            return {"type": "CIMUniqueValueClass", "label": label,
                    "visible": True, "patch": "Default",
                    "values": [{"type": "CIMUniqueValue",
                                "fieldValues": [value]}],
                    "symbol": sym}

        out_ld["renderer"] = {
            "type": "CIMUniqueValueRenderer",
            "fields": ["Primary"],
            "useDefaultSymbol": False,
            "groups": [{
                "type": "CIMUniqueValueGroup",
                "heading": "Primary isopleth",
                "classes": [
                    uv_class("True",
                             "Primary (5 / 25 / 50 / 75 / 95%)",
                             symbol(1.5, (0, 0, 0))),
                    uv_class("False", "Minor levels",
                             symbol(0.6, (110, 110, 110))),
                ],
            }],
        }
        if shape != "polygon":
            # label the primary isopleths along the lines ("95%", ...),
            # white-haloed for readability over imagery; the minor
            # levels stay unlabeled so twenty levels don't wallpaper
            # the map (turn the where-clause off in Pro to label all)
            fill = {"type": "CIMSolidFill", "enable": True,
                    "color": {"type": "CIMRGBColor",
                              "values": [0, 0, 0, 100]}}
            halo = {"type": "CIMSolidFill", "enable": True,
                    "color": {"type": "CIMRGBColor",
                              "values": [255, 255, 255, 100]}}
            out_ld["labelClasses"] = [{
                "type": "CIMLabelClass",
                "name": "Primary isopleth labels",
                "visibility": True,
                "expression": "$feature.Label",
                "expressionEngine": "Arcade",
                "featuresToLabel": "AllVisibleFeatures",
                "whereClause": "Primary = 'True'",
                "priority": 1,
                "maplexLabelPlacementProperties": {
                    "type": "CIMMaplexLabelPlacementProperties",
                    "featureType": "Line",
                    # Maplex CONTOUR placement (Jeff's spec 2026-09-07):
                    # page label alignment, +/-90 degree maximum angle,
                    # curved ladders, centered curved on the line
                    "lineFeatureType": "Contour",
                    "contourAlignmentType": "Page",
                    "contourMaximumAngle": 90,
                    "contourLadderType": "Curved",
                    "linePlacementMethod": "CenteredCurvedOnLine",
                    "canOverrunFeature": True,
                    "repeatLabel": True,
                    "minimumRepetitionInterval": 200,
                    # "Remove duplicate labels: remove within fixed
                    # distance" of 150 points
                    "thinDuplicateLabels": True,
                    "thinningDistance": 150,
                    "thinningDistanceUnit": "Point",
                },
                "textSymbol": {
                    "type": "CIMSymbolReference",
                    "symbol": {
                        "type": "CIMTextSymbol",
                        "fontFamilyName": "Tahoma",
                        "fontStyleName": "Regular",
                        "height": 8,
                        "horizontalAlignment": "Center",
                        "verticalAlignment": "Center",
                        "haloSize": 1,
                        "haloSymbol": {"type": "CIMPolygonSymbol",
                                       "symbolLayers": [halo]},
                        "symbol": {"type": "CIMPolygonSymbol",
                                   "symbolLayers": [fill]},
                    },
                },
            }]
            out_ld["labelVisibility"] = True
        out_ld["name"] = name
        with open(out_lyrx, "w", encoding="utf-8") as f:
            json.dump(lyr, f)
    except Exception as e:                                   # noqa: BLE001
        if messages is not None:
            messages.addWarningMessage(
                "Layer file created but symbology could not be "
                "applied: {}".format(e))
    return mh.ensure_label_class(out_lyrx, messages)
