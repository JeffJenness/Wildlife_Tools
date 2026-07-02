# -*- coding: utf-8 -*-
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
                    "cumulative proportion for a proportion-under-the-surface input."},
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


def _kd_write_meta(target, title, summary, description, proportion=False, messages=None):
    """Write short title/tags/summary/description metadata onto an output. Stamps the
    proportion-surface marker when ``proportion`` is True. Never raises."""
    try:
        from arcpy import metadata as md
        m = md.Metadata(target)
        m.title = title
        tags = _KD_TAGS
        if proportion:
            tags += ", " + _PROP_TAG
            description = description + "\n[" + _PROP_TAG + "]"
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
    return arr, valid


def _cumulative_volume(arr, valid, from_below):
    """Per-cell cumulative-volume fraction (0..1). Ties share a value. Returns a
    float64 array (NaN outside ``valid``).

    from_below=True : share of volume with density <= this cell's density
                      (0 at the outer edge, 1 at the peak -- the "proportion under
                      the surface" raster, oriented like the raw density).
    from_below=False: share of volume with density >= this cell's density
                      (enclosed-volume / home-range orientation).
    """
    vals = arr[valid]
    uvals, inv, counts = np.unique(vals, return_inverse=True, return_counts=True)
    vol_per_uval = uvals * counts                       # density-weighted volume per level
    total = vol_per_uval.sum()
    if from_below:
        cum = np.cumsum(vol_per_uval) / total           # density <= u
    else:
        cum = np.cumsum(vol_per_uval[::-1])[::-1] / total   # density >= u
    out = np.full(arr.shape, np.nan, dtype=np.float64)
    out[valid] = cum[inv]
    return out


def compute_contour_levels(arr, valid):
    """The legacy "Probability" contour levels.

    Sort positive densities ascending; step upward accumulating density/total; the
    first cell whose cumulative-from-below fraction reaches each internal threshold
    p in {0, .05, ..., .95} gives that level's contour density. The home-range
    *label* is ``1 - p`` (so internal .05 -> "95%"). Returns a list of dicts with
    keys: density, percentile (label fraction), label (str), primary (bool),
    ascending by density and de-duplicated on density.
    """
    vals = np.sort(arr[valid])
    total = vals.sum()
    frac = np.cumsum(vals) / total
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
    X = 95%, 90%, ..., 5% (the 100% "contour" is just the data edge and is omitted).
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
def run_proportion_surface(kd, out_raster, messages):
    """KD raster -> proportion-under-the-surface raster (0..1).

    Each cell holds the share of the total volume with density <= that cell's
    density, so values run from ~0 at the outer boundary to 1.0 at the peak (the
    same orientation as the raw kernel density). To draw the X% home-range isopleth,
    contour this surface at ``1 - X/100`` (e.g. 0.05 for the 95% isopleth) -- the
    Probability Contours tool reports that value in its ``CumProportion`` field.
    Writes statistics and a histogram with the raster (scratch GeoTIFF + CopyRaster)
    so it renders correctly without the slow CalculateStatistics scan."""
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
        cum = _cumulative_volume(arr, valid, from_below=True)   # 0 at edge -> 1 at peak

        out = np.full(arr.shape, _NODATA, dtype=np.float32)
        out[valid] = cum[valid].astype(np.float32)

        out_path = arcpy.CreateUniqueName("kd_prop.tif", scratch)
        drv = gdal.GetDriverByName("GTiff")
        out_ds = drv.Create(out_path, width, height, 1, gdal.GDT_Float32,
                            options=["TILED=YES", "COMPRESS=LZW"])
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


def _add_level_fields(fc):
    existing = {f.name for f in arcpy.ListFields(fc)}
    #   name             type      length  alias
    specs = [("Density", "DOUBLE", None, "Density level"),
             ("Isopleth", "DOUBLE", None, "Proportion of volume enclosed (isopleth level)"),
             ("CumProportion", "DOUBLE", None, "Cumulative proportion (raster value)"),
             ("Label", "TEXT", 5, None),
             ("Primary", "TEXT", 5, None)]
    for name, ftype, length, alias in specs:
        if name not in existing:
            arcpy.management.AddField(fc, name, ftype, field_length=length,
                                      field_alias=alias)


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
    tmp = arcpy.CreateUniqueName("kd_reproj", arcpy.env.scratchGDB)
    arcpy.management.Project(fc, tmp, out_cs)
    arcpy.management.Delete(fc)
    arcpy.management.CopyFeatures(tmp, fc)
    arcpy.management.Delete(tmp)
    messages.addMessage("Reprojected output to Output Coordinate System: {}.".format(out_cs.name))


def _build_contours(kd, out_lines, out_polys, messages):
    """Generate the probability isopleths (polylines and/or polygon areas) in the
    raster's native coordinate system. Returns a list of outputs."""
    scratch = arcpy.env.scratchFolder
    if _is_marked_proportion(kd):
        # The raster already holds the cumulative volume (proportion under the
        # surface): the home-range isopleths are just fixed proportion contours, so
        # skip the volume-weighted level search and contour the levels directly.
        levels = _proportion_levels()
        messages.addMessage(
            "Input is a proportion-under-the-surface raster; contouring the standard "
            "proportion levels directly ({} levels).".format(len(levels)))
    else:
        # A kernel-density raster (Esri Kernel Density, or our tool's Densities /
        # Expected counts output): derive the volume-weighted contour density levels
        # from the cell values, exactly as before.
        gdal, src, tmp = _open_kd(kd, scratch)
        try:
            arr, valid = _read_positive(src)
            if not valid.any():
                raise arcpy.ExecuteError(
                    "The input raster has no positive-density cells to analyze.")
            levels = compute_contour_levels(arr, valid)
        finally:
            src = None
        if tmp:
            try:
                arcpy.management.Delete(tmp)
            except Exception:
                pass
        messages.addMessage(
            "Input is a kernel-density raster; computed {} volume-weighted contour "
            "levels.".format(len(levels)))

    densities = [lv["density"] for lv in levels]

    checked_3d = arcpy.CheckExtension("3D") == "Available"
    if checked_3d:
        arcpy.CheckOutExtension("3D")
    else:
        arcpy.CheckOutExtension("Spatial")

    # Build the master polyline set once in a file-GDB scratch (full double
    # precision -- a shapefile DBF would truncate the density values), attribute
    # it (with an integer LevelID for robust per-level selection), then derive
    # whichever outputs were requested.
    sgdb = arcpy.env.scratchGDB
    tmp_lines = arcpy.CreateUniqueName("kd_contours", sgdb)
    mh._set_label("Generating probability contours...")
    arcpy.ddd.ContourList(kd, tmp_lines, densities)
    cfield = _contour_value_field(tmp_lines)
    _add_level_fields(tmp_lines)
    arcpy.management.AddField(tmp_lines, "LevelID", "LONG")
    with arcpy.da.UpdateCursor(
            tmp_lines,
            [cfield, "Density", "Isopleth", "CumProportion", "Label", "Primary", "LevelID"]) as cur:
        for row in cur:
            i = _match_index(levels, row[0])
            lv = levels[i]
            row[1] = lv["density"]
            row[2] = lv["isopleth"]
            row[3] = lv["cumprop"]
            row[4] = lv["label"]
            row[5] = "True" if lv["primary"] else "False"
            row[6] = i
            cur.updateRow(row)

    outputs = []
    if out_lines:
        mh._set_label("Saving contour polylines...")
        arcpy.management.CopyFeatures(tmp_lines, out_lines)
        try:
            arcpy.management.DeleteField(out_lines, "LevelID")
        except Exception:
            pass
        outputs.append(out_lines)
        messages.addMessage("Saved contour polylines: {}".format(out_lines))

    if out_polys:
        mh._set_label("Building contour polygon areas...")
        sr = arcpy.Describe(tmp_lines).spatialReference
        arcpy.management.CreateFeatureclass(
            os.path.dirname(out_polys), os.path.basename(out_polys),
            "POLYGON", spatial_reference=sr)
        _add_level_fields(out_polys)
        ins_fields = ["SHAPE@", "Density", "Isopleth", "CumProportion", "Label", "Primary"]
        delim = arcpy.AddFieldDelimiters(tmp_lines, "LevelID")
        with arcpy.da.InsertCursor(out_polys, ins_fields) as ins:
            for i, lv in enumerate(levels):
                lyr = "lvl_lyr"
                arcpy.management.MakeFeatureLayer(
                    tmp_lines, lyr, "{} = {}".format(delim, i))
                if int(arcpy.management.GetCount(lyr)[0]) == 0:
                    arcpy.management.Delete(lyr)
                    continue
                poly = arcpy.CreateUniqueName("kd_poly", sgdb)
                arcpy.management.FeatureToPolygon(lyr, poly)
                arcpy.management.Delete(lyr)
                with arcpy.da.SearchCursor(poly, ["SHAPE@"]) as scur:
                    for srow in scur:
                        if srow[0] is None:
                            continue
                        ins.insertRow([srow[0], lv["density"], lv["isopleth"], lv["cumprop"],
                                       lv["label"], "True" if lv["primary"] else "False"])
                try:
                    arcpy.management.Delete(poly)
                except Exception:
                    pass
        outputs.append(out_polys)
        messages.addMessage("Saved contour polygon areas: {}".format(out_polys))

    try:
        arcpy.management.Delete(tmp_lines)
    except Exception:
        pass
    return outputs


def run_probability_contours(kd, out_lines, out_polys, messages):
    """KD raster -> probability isopleths as polylines and/or polygon areas.

    At least one of ``out_lines`` / ``out_polys`` must be supplied. Polygons are
    built by closing each level's contour loops (FeatureToPolygon), so polygon
    boundaries coincide exactly with the polylines. Contours are computed in the
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
        outputs = _build_contours(kd, out_lines, out_polys, messages)
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
        tmeta.set_entity_and_fields(fc, entity=_CONTOUR_ENTITY, fields=_CONTOUR_FIELDS,
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
                                              options=["TILED=YES", "COMPRESS=LZW"])
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
            ext = ext.projectAs(out_cs)
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


def run_kernel_density(points, pop_field, out_raster, cell_size, search_radius,
                       kernel, distance_method, out_value, area_unit, messages):
    """Custom kernel-density surface from point features (see module docstring and
    the tool help). Returns the output raster path."""
    key = kernel_key(kernel)
    cnorm = _kernel_norm(key)
    kfunc = _KFUNC[key]

    desc = arcpy.Describe(points)
    sr = desc.spatialReference

    # Read points (respects any selection on a layer) + weights, in the input CS.
    fields = ["SHAPE@X", "SHAPE@Y"] + ([pop_field] if pop_field and pop_field.upper() != "NONE" else [])
    xs_l, ys_l, ws_l = [], [], []
    with arcpy.da.SearchCursor(points, fields) as cur:
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
    extent = _resolve_extent(x, y, sr, geographic, h_m, mpu)
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

    # Accumulate density (per m^2).
    dens = np.zeros((nrows, ncols), dtype=np.float64)
    coef = cnorm / (h_m * h_m)
    rnative = h_m / mpu                            # radius in projected units
    mh._set_label("Accumulating kernel density...")
    for i in range(x.size):
        xp, yp = x[i], y[i]
        if geographic:
            clat = math.cos(math.radians(yp)) or 1e-6
            rx = h_m / (_M_PER_DEG * abs(clat)); ry = h_m / _M_PER_DEG
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

    # Cell area (m^2): per-row spheroidal for geographic, constant otherwise.
    if geographic:
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

    _write_grid(out, xmin, ymax, csx, csy, sr, out_raster, messages)

    # env.mask (applied post hoc since we write through GDAL). Extract to a scratch
    # raster first, then copy back -- ExtractByMask cannot reliably read and overwrite
    # the same path (a "cannot overwrite input" / lock error, silently dropping the mask).
    if arcpy.env.mask:
        try:
            from arcpy.sa import ExtractByMask
            arcpy.CheckOutExtension("Spatial")
            masked = ExtractByMask(out_raster, arcpy.env.mask)
            tmp_mask = arcpy.CreateUniqueName("kd_masked.tif", arcpy.env.scratchFolder)
            masked.save(tmp_mask)
            del masked
            arcpy.management.Delete(out_raster)
            arcpy.management.CopyRaster(tmp_mask, out_raster)
            arcpy.management.Delete(tmp_mask)
        except Exception as e:
            messages.addWarningMessage("Could not apply the mask environment: {}".format(e))

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
                   proportion=(out_value == OUTV_PROPORTION), messages=messages)
    return out_raster
