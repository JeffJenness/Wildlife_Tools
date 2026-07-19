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
* **Hillshade levels.** The output ranges 0..levels (levels = 255 matches the
  Esri look; small values give a posterized, cartographic hillshade).
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

The gradient is Horn's method with the legacy distance table::

    dZ/dx = ((NE + 2E + SE) - (NW + 2W + SW)) / (2*hA + 4*h + 2*hB)
    dZ/dy = ((SW + 2S + SE) - (NW + 2N + NE)) / (4*vA + 4*vB)
    shade = cos(zenith)*cos(slope) + sin(zenith)*sin(slope)*cos(azR - aspect)
    value = round(levels * shade * hypso_multiplier), clamped at 0

with hA/h/hB the horizontal cell spans of the row above / at / below the
focal row and vA/vB the vertical spans (all meters), and
``azR = radians((360 - azimuth + 90) mod 360)``, ``aspect = atan2(dZdy, -dZdx)``
wrapped to 0..2pi -- identical to the legacy conventions. Any 3x3 window
touching NoData (or the raster edge) is NoData in the output.
"""

import math
import os

import numpy as np

import arcpy
import mahalanobis as _mh

NODATA_OUT = -9999
_STRIP_ROWS = 1024          # rows per processing strip (plus a 1-row halo)

ELEV_UNITS = ["Meters", "Feet"]
_ELEV_METERS = {"Meters": 1.0, "Feet": 0.3048}

# Blurred Hillshade choices.
RADIUS_UNITS = ["Cells", "Meters", "Kilometers", "Feet", "Miles"]
_RADIUS_METERS = {"Meters": 1.0, "Kilometers": 1000.0,
                  "Feet": 0.3048, "Miles": 1609.344}
BLUR_TARGETS = ["DEM (blur before shading)", "Hillshade (blur after shading)"]
BLUR_KERNELS = ["Average", "Gaussian"]


def _open_source(in_raster, scratch, messages):
    """Open ``in_raster`` with GDAL: ``(dataset, tmp_copy_or_None)``.

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
    try:
        return gdal.Open(path), None
    except RuntimeError:
        pass
    low = path.lower()
    if ".gdb" in low:
        i = low.rindex(".gdb")
        gdb, name = path[:i + 4], path[i + 4:].strip("\\/")
        if name:
            try:
                return gdal.Open('OpenFileGDB:"{}":{}'.format(gdb, name)), None
            except RuntimeError:
                pass
    _set_label("Exporting the DEM to a scratch GeoTIFF (GDAL cannot read this "
               "format directly; this can take a while for large rasters)...")
    _msg(messages, "Exporting the DEM to a scratch GeoTIFF for processing...")
    tif = arcpy.CreateUniqueName("hillshade_src.tif", scratch)
    arcpy.management.CopyRaster(in_raster, tif)
    try:
        return gdal.Open(tif), tif
    except RuntimeError:
        raise arcpy.ExecuteError(
            "Could not open the input raster: {}".format(in_raster))


def _msg(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _set_label(text):
    try:
        arcpy.SetProgressorLabel(text)
    except Exception:
        pass


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


def _geographic_row_distances(gt, height, a, b):
    """Per-row meter distances for a geographic raster: dict of arrays
    (length ``height``) with keys hA, h, hB (horizontal spans of the row
    above / at / below each row) and vA, vB (vertical spans up / down).

    Mirrors the legacy table exactly: neighbor points one full cell away from
    the column-0 cell center, converted to spheroidal ECEF, with the focal
    point taken as the average of the four corner ECEFs.
    """
    cellw = gt[1]
    cellh = abs(gt[5])
    x0 = gt[0] + 0.5 * gt[1]
    rows = np.arange(height, dtype=np.float64)
    y0 = gt[3] + (rows + 0.5) * gt[5]

    above = _ecef(x0, y0 + cellh, a, b)
    below = _ecef(x0, y0 - cellh, a, b)
    right = _ecef(x0 + cellw, y0, a, b)
    nw = _ecef(x0 - cellw, y0 + cellh, a, b)
    ne = _ecef(x0 + cellw, y0 + cellh, a, b)
    sw = _ecef(x0 - cellw, y0 - cellh, a, b)
    se = _ecef(x0 + cellw, y0 - cellh, a, b)
    focal = tuple((nw[i] + ne[i] + sw[i] + se[i]) / 4.0 for i in range(3))

    return {
        "vA": _dist3(focal, above),
        "hA": _dist3(above, ne),
        "h": _dist3(focal, right),
        "vB": _dist3(focal, below),
        "hB": _dist3(below, se),
    }


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


def _blur_grid(z, valid, rx_rows, ry, kernel):
    """NoData-ignoring circular mean of ``z`` (full grid). ``valid`` is the
    data mask; invalid cells are excluded from every mean and stay invalid in
    the result (matching Focal Statistics with Ignore NoData). ``rx_rows``
    gives each row's x-radius in cells (varies by latitude on geographic
    rasters); rows are grouped by their rounded radius and each group is
    convolved with its own kernel."""
    from scipy.signal import fftconvolve

    num = np.where(valid, z, 0.0)
    den = valid.astype(np.float64)
    out = np.full(z.shape, np.nan)
    rx_int = np.maximum(np.rint(np.asarray(rx_rows, dtype=np.float64)), 1.0)
    for rxi in np.unique(rx_int):
        k = _blur_kernel(rxi, ry, kernel)
        cn = fftconvolve(num, k, mode="same")
        cd = fftconvolve(den, k, mode="same")
        rows = rx_int == rxi
        with np.errstate(invalid="ignore", divide="ignore"):
            vals = cn / np.maximum(cd, 1e-12)
        out[rows] = vals[rows]
    out[~valid] = np.nan
    return out


# ----------------------------------------------------------------------------
#  Cast-shadow sweep
# ----------------------------------------------------------------------------

def _cast_shadow_mask(z_m, cellw_m, cellh_m, azimuth, altitude):
    """Boolean mask of cells hidden from the sun (True = in shadow).

    ``z_m``: full elevation grid in meters (NaN = NoData; NaN cells neither
    cast nor receive shadows). Single-pass sweep marching away from the sun:
    the carried "shadow surface" drops by tan(altitude) x step distance each
    row and rises to any terrain above it. O(cells).
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
    try:
        arcpy.SetProgressor("step", "Sweeping cast shadows...", 0, nrows, 1)
    except Exception:
        pass
    tick = max(1, nrows // 100)
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
            try:
                arcpy.SetProgressorPosition(r)
            except Exception:
                pass

    for op in reversed(flips):
        if op == "T":
            mask = mask.T
        elif op == "ud":
            mask = mask[::-1]
        else:
            mask = mask[:, ::-1]
    return mask


# ----------------------------------------------------------------------------
#  Main engine
# ----------------------------------------------------------------------------

_MDOW_OFFSETS = (-67.5, -22.5, 22.5, 67.5)


def run_enhanced_hillshade(in_raster, out_raster, elev_units="Meters",
                           azimuth=315.0, altitude=45.0, levels=255,
                           use_hypso=False, hypso_strength=50,
                           vert_exag=1.0, model_shadows=False,
                           shadow_dim=20.0, messages=None):
    """Compute the enhanced hillshade. See the module docstring.

    ``shadow_dim``: percent (0-100) of the unshadowed hillshade value that a
    shadowed cell keeps -- 0 renders shadows black, 100 disables the dimming.
    """
    _run_hillshade("standard", in_raster, out_raster, elev_units, azimuth,
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
    _run_hillshade("mdow", in_raster, out_raster, elev_units, azimuth,
                   altitude, levels, use_hypso, hypso_strength, vert_exag,
                   False, 20.0, messages)


def run_multisun_hillshade(in_raster, out_raster, elev_units="Meters",
                           suns=((280.0, 35.0, 50.0), (350.0, 45.0, 50.0)),
                           levels=255, use_hypso=False, hypso_strength=50,
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
    _run_hillshade("multi", in_raster, out_raster, elev_units, 315.0,
                   45.0, levels, use_hypso, hypso_strength, vert_exag,
                   model_shadows, shadow_dim, messages, suns=suns)


def run_blurred_hillshade(in_raster, out_raster, elev_units="Meters",
                          azimuth=315.0, altitude=45.0, levels=255,
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
        if geographic:
            a = float(sr.semiMajorAxis or 6378137.0)
            b = float(sr.semiMinorAxis or 6356752.31424518)
            dist = _geographic_row_distances(gt, height, a, b)
            rx_rows = radius_m / np.maximum(dist["h"], 1e-9)
            ry = radius_m / max(float(np.mean(dist["vA"])), 1e-9)
            _msg(messages, "Blur radius: {:g} {} = {:,.1f} m, sized per row "
                           "on the spheroid ({:.1f}-{:.1f} cells across, "
                           "{:.1f} cells down; {} kernel).".format(
                               blur_radius, radius_units.lower(), radius_m,
                               float(rx_rows.min()), float(rx_rows.max()),
                               ry, kernel))
        else:
            mpu = float(sr.metersPerUnit or 1.0)
            rx_rows = np.full(height, radius_m / (gt[1] * mpu))
            ry = radius_m / (abs(gt[5]) * mpu)
            _msg(messages, "Blur radius: {:g} {} = {:,.1f} m = {:.1f} x {:.1f} "
                           "cells ({} kernel).".format(
                               blur_radius, radius_units.lower(), radius_m,
                               float(rx_rows[0]), ry, kernel))
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
        _set_label("Blurring the DEM...")
        _msg(messages, "Blurring the DEM (circular mean of the elevations)...")
        zb = _blur_grid(z, valid, rx_rows, ry, kernel)
        del z
        BLUR_ND = -3.0e38
        tmp_dem = arcpy.CreateUniqueName("blurred_dem.tif", scratch)
        drv = gdal.GetDriverByName("GTiff")
        tds = drv.Create(tmp_dem, width, height, 1, gdal.GDT_Float32,
                         options=["TILED=YES", "COMPRESS=LZW"])
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
            _run_hillshade("standard", tmp_dem, out_raster, elev_units,
                           azimuth, altitude, levels, use_hypso,
                           hypso_strength, vert_exag, model_shadows,
                           shadow_dim, messages,
                           tool_label="Blurred Hillshade",
                           src_display=str(in_raster), extra_note=note)
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
        return

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
                            shadow_dim, messages, _finalize=False)
    hds = gdal.Open(hs_tmp)
    arr = hds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    hgt, hproj = hds.GetGeoTransform(), hds.GetProjection()
    hds = None
    hvalid = arr != NODATA_OUT
    _set_label("Blurring the hillshade...")
    _msg(messages, "Blurring the finished hillshade (circular mean of the "
                   "hillshade values)...")
    hb = _blur_grid(arr, hvalid, rx_rows, ry, kernel)
    vals = np.rint(np.where(hvalid, hb, NODATA_OUT))
    out_arr = np.where(hvalid, vals, NODATA_OUT).astype(np.int16)

    out_path = arcpy.CreateUniqueName("hillshade_blurred.tif", scratch)
    drv = gdal.GetDriverByName("GTiff")
    ods = drv.Create(out_path, width, height, 1, gdal.GDT_Int16,
                     options=["TILED=YES", "COMPRESS=LZW"])
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

    _set_label("Saving output raster...")
    prev_pyramid = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        arcpy.management.CopyRaster(out_path, out_raster)
    finally:
        arcpy.env.pyramid = prev_pyramid
    _set_label("Setting raster statistics...")
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
        md.save()
    except Exception:
        pass
    for p in (out_path, hs_tmp):
        try:
            arcpy.management.Delete(p)
        except Exception:
            pass
    _set_label("Building pyramids (bilinear)...")
    try:
        arcpy.management.BuildPyramids(out_raster, resample_technique="BILINEAR")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not build pyramids: {}".format(e))
    _msg(messages, "Blurred Hillshade complete: {}".format(out_raster))


def _run_hillshade(mode, in_raster, out_raster, elev_units, azimuth, altitude,
                   levels, use_hypso, hypso_strength, vert_exag,
                   model_shadows, shadow_dim, messages, suns=None,
                   _finalize=True, tool_label=None, src_display=None,
                   extra_note=""):
    """Shared engine: ``mode`` is "standard" (one sun direction, optional cast
    shadows), "mdow" (four sine-squared-weighted directions, no shadows), or
    "multi" (a weighted blend of standard hillshades from ``suns``, optional
    per-sun cast shadows).

    ``_finalize=False`` stops after the scratch GeoTIFF is written (no
    CopyRaster/pyramids/metadata) and returns its path -- used by the Blurred
    Hillshade's blur-after-shading path. ``tool_label``/``src_display``/
    ``extra_note`` override the tool name, input name, and add provenance
    text in messages and metadata (used when the input is a preprocessed
    scratch dataset standing in for the user's raster)."""
    gdal = _mh._gdal()

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
    if levels < 1:
        raise arcpy.ExecuteError("The number of hillshade levels must be at least 1.")
    if vert_exag <= 0:
        raise arcpy.ExecuteError("The vertical exaggeration must be positive.")
    if use_hypso and not 0.0 <= hypso_strength <= 100.0:
        raise arcpy.ExecuteError("The hypsometric strength must be 0 to 100.")
    if model_shadows and not 0.0 <= shadow_dim <= 100.0:
        raise arcpy.ExecuteError("The shadow brightness must be 0 to 100 percent.")

    scratch = arcpy.env.scratchFolder
    src, in_copy = _open_source(in_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    in_nodata = band.GetNoDataValue()

    # Geographic vs projected, spheroid axes, and meters-per-unit factors.
    sr = arcpy.Describe(in_raster).spatialReference
    geographic = (sr.type == "Geographic")
    if geographic:
        semi_major = float(sr.semiMajorAxis or 6378137.0)
        semi_minor = float(sr.semiMinorAxis or 6356752.31424518)
        elev_mpu = 1.0                      # legacy: geographic DEMs are meters
        dist = _geographic_row_distances(gt, height, semi_major, semi_minor)
        cellw_m = float(np.mean(dist["h"]))
        cellh_m = float(np.mean(dist["vA"]))
        _msg(messages, "Geographic DEM: per-row cell dimensions computed on the "
                       "spheroid (a = {:.1f} m, b = {:.1f} m).".format(
                           semi_major, semi_minor))
    else:
        mpu = float(sr.metersPerUnit or 1.0)
        elev_mpu = _ELEV_METERS.get(elev_units, 1.0)
        cellw_m = gt[1] * mpu
        cellh_m = abs(gt[5]) * mpu
        const = np.full(height, 1.0)
        dist = {"hA": const * cellw_m, "h": const * cellw_m, "hB": const * cellw_m,
                "vA": const * cellh_m, "vB": const * cellh_m}
        _msg(messages, "Projected DEM: cell = {:.3f} x {:.3f} m; elevation units "
                       "= {}.".format(cellw_m, cellh_m, elev_units))
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
        _set_label("Scanning elevation statistics for hypsometric shading...")
        s_min, s_max = band.GetStatistics(0, 1)[:2]
        hyp_lo = s_min * zscale
        hyp_range = max((s_max - s_min) * zscale, 1e-30)
        _msg(messages, "Hypsometric shading: strength {:.0f}% over elevation "
                       "range {:.6g} to {:.6g}.".format(hypso_strength, s_min, s_max))
    s_frac = hypso_strength / 100.0

    # Optional cast shadows need the whole grid at once (shadows are non-local).
    shadow_mask = None                  # standard mode: one mask
    shadow_masks = None                 # multi mode: one mask per sun
    if model_shadows:
        _set_label("Reading the DEM for the cast-shadow sweep...")
        z_full = band.ReadAsArray(0, 0, width, height).astype(np.float32)
        z_full = z_full.astype(np.float64)
        invalid = ~np.isfinite(z_full)
        if in_nodata is not None:
            invalid |= (z_full == in_nodata)
        z_full[invalid] = np.nan
        z_full *= zscale
        geo_note = (" (mean-cell-size approximation on this geographic DEM)"
                    if geographic else "")
        if mode == "multi":
            shadow_masks = []
            for i, (a, al, _w) in enumerate(suns):
                _set_label("Sweeping cast shadows (sun {} of {})...".format(
                    i + 1, len(suns)))
                m = _cast_shadow_mask(z_full, cellw_m, cellh_m, a, al)
                shadow_masks.append(m)
                n_sh = int(m.sum())
                _msg(messages, "Cast shadows, sun {} ({:g} deg / {:g} deg): "
                               "{:,} cells ({:.1f}%) dimmed to {:g}% before "
                               "averaging{}.".format(
                                   i + 1, a, al, n_sh,
                                   100.0 * n_sh / (width * height), shadow_dim,
                                   geo_note))
        else:
            shadow_mask = _cast_shadow_mask(z_full, cellw_m, cellh_m, azimuth, altitude)
            n_sh = int(shadow_mask.sum())
            _msg(messages, "Cast shadows: {:,} cells ({:.1f}%) are hidden from the "
                           "sun and will be dimmed to {:g}% of their unshadowed "
                           "value{}.".format(
                               n_sh, 100.0 * n_sh / (width * height), shadow_dim,
                               geo_note))
        del z_full

    # Output scratch GeoTIFF (Int16; NoData -9999), stats accumulated as we go.
    out_path = arcpy.CreateUniqueName("hillshade.tif", scratch)
    drv = gdal.GetDriverByName("GTiff")
    out_ds = drv.Create(out_path, width, height, 1, gdal.GDT_Int16,
                        options=["TILED=YES", "COMPRESS=LZW"])
    out_ds.SetGeoTransform(gt)
    try:
        out_ds.SetProjection(src.GetProjection())
    except Exception:
        pass
    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(NODATA_OUT)

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
    _set_label("Calculating hillshade...")
    stats = _mh._RasterStats()
    strips = [(y, min(_STRIP_ROWS, height - y)) for y in range(0, height, _STRIP_ROWS)]
    try:
        arcpy.SetProgressor("step", "Calculating hillshade...", 0, len(strips), 1)
    except Exception:
        pass

    for idx, (yoff, rows) in enumerate(strips):
        # Read the strip plus a 1-row halo on each side (clamped at the edges).
        top = max(yoff - 1, 0)
        bot = min(yoff + rows + 1, height)
        pad = band.ReadAsArray(0, top, width, bot - top).astype(np.float64)
        valid = np.isfinite(pad)
        if in_nodata is not None:
            valid &= (pad != in_nodata)
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
            hA = dist["hA"][rr][:, None]
            h = dist["h"][rr][:, None]
            hB = dist["hB"][rr][:, None]
            vA = dist["vA"][rr][:, None]
            vB = dist["vB"][rr][:, None]

            dzdx = ((NE + 2.0 * E_ + SE) - (NW + 2.0 * W_ + SW)) / \
                   (2.0 * hA + 4.0 * h + 2.0 * hB)
            dzdy = ((SW + 2.0 * S_ + SE) - (NW + 2.0 * N_ + NE)) / (4.0 * vA + 4.0 * vB)

            slope = np.arctan(np.hypot(dzdx, dzdy))
            aspect = np.arctan2(dzdy, -dzdx)
            aspect[aspect < 0] += 2.0 * math.pi

            if mode == "mdow":
                # Four hillshades blended with sin^2(aspect - direction)
                # weights; the weights of 45-degree-spaced directions always
                # sum to 2, so /2 is exact (Mark 1992).
                cs = cosz * np.cos(slope)
                ss = sinz * np.sin(slope)
                shade = np.zeros_like(slope)
                for dr in dir_rads:
                    shade += np.sin(aspect - dr) ** 2 * (cs + ss * np.cos(dr - aspect))
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
            vals = np.rint(hs)                   # banker's rounding, like VB CLng
            center = out[(r0 - yoff):(r1 - yoff), 1:-1]
            center[ok] = vals[ok].astype(np.int16)
            if ok.any():
                stats.add(vals[ok])
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

    _set_label("Saving output raster...")
    prev_pyramid = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        arcpy.management.CopyRaster(out_path, out_raster)
    finally:
        arcpy.env.pyramid = prev_pyramid
    _set_label("Setting raster statistics...")
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

    # Dataset metadata: conceptual description + per-run provenance.
    try:
        md = arcpy.metadata.Metadata(out_raster)
        md.title = os.path.basename(str(out_raster))
        geo_note = ("Cell dimensions were computed per row on the spheroid "
                    "(geodesic method) because the DEM is in a geographic "
                    "coordinate system." if geographic else
                    "Planar cell dimensions were used (projected DEM).")
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
                    "to the requested number of levels (fully lit). Unlike the "
                    "standard hillshade, gradients on geographic DEMs are computed "
                    "from true spheroidal cell dimensions at each row's latitude.")
        md.save()
    except Exception:
        pass

    try:
        arcpy.management.Delete(out_path)
    except Exception:
        pass
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass
    _set_label("Building pyramids (bilinear)...")
    try:
        arcpy.management.BuildPyramids(out_raster, resample_technique="BILINEAR")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not build pyramids: {}".format(e))
    _msg(messages, "{} complete: {}".format(
        tool_label or {"mdow": "MDOW hillshade",
                       "multi": "Multi-sun hillshade"}.get(
            mode, "Enhanced hillshade"), out_raster))
