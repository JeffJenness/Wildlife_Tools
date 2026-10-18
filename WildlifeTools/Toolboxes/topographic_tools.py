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

"""Topographic Analysis tools for the Jenness Enterprises toolbox.

First tool: **Surface Area and Ratio** -- a port of the VB6 ``CalcSurfaceArea``
(Surface_Area / DEM Surface Tools), implementing the 8-triangle method of
Jenness, J. S. (2004), "Calculating landscape surface area from digital
elevation models", Wildlife Society Bulletin 32:829-839.

Method: within each cell, eight 3-D triangles connect the cell center to
eight points interpolated on the cell boundary -- the four edge midpoints
(elevation = mean of the two adjacent cell centers) and the four corners.
Each triangle's area comes from Heron's formula on its three 3-D leg
lengths; their sum is the cell's surface area (m^2). The surface RATIO is
that area divided by the cell's planimetric (flat) area, and the FLAT AREA
output is the planimetric cell area itself -- constant on projected rasters,
varying by row on geographic rasters, where all distances are spheroidal
ECEF chords between half-cell-offset points and the cell area is the sum of
eight 3-D chord triangles (so cell areas are true at any latitude).

Corner interpolation (the one deliberate change from the legacy tool, per
Jeff): the ORIGINAL method sets each corner elevation to the midpoint of the
focal and diagonal cells ((E + C)/2 for the NE corner of focus E). The new
optional method uses the mean of all FOUR cells that share the corner
((B + C + E + F)/4) -- the true bilinear estimate at that corner (the edge
midpoints are already bilinear-exact). On a tilted plane the two methods are
identical; they differ only where the surface curves.

Outputs are float32, NoData -9999; surface area and ratio are NoData
wherever any cell of the 3x3 window is NoData (and on the outer edge); the
flat-area raster is written for EVERY cell, matching the legacy tool.
"""

import math
import os

import numpy as np

import arcpy
import mahalanobis as _mh
import hillshade as _hs

NODATA_OUT = -9999.0
_STRIP_ROWS = 1024


def mask_band_valid(gdal, band, valid, xoff=0, yoff=0):
    """Delegate to :func:`mahalanobis.mask_band_valid` (the canonical home
    of the mask-band validity rule); kept here because callers historically
    reach shared raster helpers through ``_tt``. ``gdal`` is accepted for
    signature compatibility and unused -- the canonical helper imports it."""
    return _mh.mask_band_valid(band, valid, xoff, yoff)

AREA_UNITS = ["Sq. Meters", "Sq. Feet", "Hectares", "Acres",
              "Sq. Kilometers", "Sq. Miles"]
_AREA_MULT = {
    "Sq. Meters": 1.0,
    "Sq. Feet": 1.0 / (0.3048 ** 2),
    "Hectares": 1.0 / 10000.0,
    "Acres": 1.0 / ((0.3048 ** 2) * 43560.0),
    "Sq. Kilometers": 1.0 / 1.0e6,
    "Sq. Miles": 1.0 / ((0.3048 ** 2) * (5280.0 ** 2)),
}
CORNER_METHODS = ["Diagonal midpoint (2 cells, original method)",
                  "Corner average (4 cells)"]

_CITATION = ("Jenness, J. S. 2004. Calculating landscape surface area from "
             "digital elevation models. Wildlife Society Bulletin 32:829-839.")


def _tri_area_3d(p, q, r):
    """Areas of 3-D triangles given three (x, y, z) triples of arrays."""
    ux, uy, uz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
    vx, vy, vz = r[0] - p[0], r[1] - p[1], r[2] - p[2]
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return 0.5 * np.sqrt(cx * cx + cy * cy + cz * cz)


# The 8-triangle surface-area table (squared half-cell distances + true cell
# area) now comes from hillshade.GroundGeometry.half_rows / half_row, which
# builds it for ANY coordinate system (geographic per row, projected on a
# lattice of unprojected cell centers).


def _heron(l1, l2, l3):
    s = (l1 + l2 + l3) / 2.0
    prod = s * (s - l1) * (s - l2) * (s - l3)
    return np.sqrt(np.maximum(prod, 0.0))


def _eight_triangle_area(NW, N_, NE, W_, C_, E_, SW, S_, SE, g, four_cell):
    """Jenness (2004) 8-triangle 3-D surface area for a block of focal cells,
    given the nine neighbor-elevation arrays (meters, focal = ``C_``) and a
    dict ``g`` of the per-row SQUARED half-distance arrays (keys vA2/hA2/dA2/
    h2/dB2/hB2/vB2), each already broadcast to the elevation arrays' shape.
    The four edge midpoints (B/D/F/H) are the 2-cell means in both corner
    methods; the four corners (A/C/G/I) are the 2-cell diagonal midpoints
    (legacy) or the 4-cell averages (``four_cell``). Shared by
    ``run_surface_area`` and ``run_acr`` so the triangulation lives in one
    place."""
    zB = (N_ + C_) / 2.0
    zD = (W_ + C_) / 2.0
    zF = (E_ + C_) / 2.0
    zH = (S_ + C_) / 2.0
    if four_cell:
        zA = (NW + N_ + W_ + C_) / 4.0
        zC = (N_ + NE + E_ + C_) / 4.0
        zG = (W_ + SW + S_ + C_) / 4.0
        zI = (E_ + S_ + SE + C_) / 4.0
    else:
        zA = (NW + C_) / 2.0
        zC = (NE + C_) / 2.0
        zG = (SW + C_) / 2.0
        zI = (SE + C_) / 2.0

    # Eight spoke legs (center to boundary points).
    sA = np.sqrt(g["dA2"] + (zA - C_) ** 2)
    sB = np.sqrt(g["vA2"] + (zB - C_) ** 2)
    sC = np.sqrt(g["dA2"] + (zC - C_) ** 2)
    sD = np.sqrt(g["h2"] + (zD - C_) ** 2)
    sF = np.sqrt(g["h2"] + (zF - C_) ** 2)
    sG = np.sqrt(g["dB2"] + (zG - C_) ** 2)
    sH = np.sqrt(g["vB2"] + (zH - C_) ** 2)
    sI = np.sqrt(g["dB2"] + (zI - C_) ** 2)

    # Eight outer legs (between adjacent boundary points).
    oAB = np.sqrt(g["hA2"] + (zA - zB) ** 2)
    oBC = np.sqrt(g["hA2"] + (zC - zB) ** 2)
    oAD = np.sqrt(g["vA2"] + (zA - zD) ** 2)
    oCF = np.sqrt(g["vA2"] + (zC - zF) ** 2)
    oDG = np.sqrt(g["vB2"] + (zG - zD) ** 2)
    oFI = np.sqrt(g["vB2"] + (zI - zF) ** 2)
    oGH = np.sqrt(g["hB2"] + (zG - zH) ** 2)
    oHI = np.sqrt(g["hB2"] + (zI - zH) ** 2)

    return (_heron(sA, sB, oAB) + _heron(sC, sB, oBC) +
            _heron(sA, sD, oAD) + _heron(sC, sF, oCF) +
            _heron(sD, sG, oDG) + _heron(sF, sI, oFI) +
            _heron(sG, sH, oGH) + _heron(sH, sI, oHI))


def _resolve_output(path, messages):
    """Shared no-more-GRID output resolution (see mahalanobis.py)."""
    return _mh.resolve_raster_output(path, messages)


def _make_output(gdal, path, gt, proj, width, height):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, width, height, 1, gdal.GDT_Float32,
                    options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform(gt)
    try:
        ds.SetProjection(proj)
    except Exception:
        pass
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(NODATA_OUT)
    return ds, band


def _deliver_output(out_scratch, out_raster, out_stats, messages,
                    title, summary, description, tags, citation=None):
    """CopyRaster to the destination, apply stats, metadata, pyramids."""
    prev_pyramid = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        # Grid-native contract: the delivered raster IS the analysis grid.
        # Without this, the Output Coordinate System / Extent / Snap Raster /
        # Cell Size / Mask environments would make CopyRaster re-project,
        # crop, or resample the finished raster while its stamped statistics
        # still describe the analysis grid (see mahalanobis.neutral_grid_env).
        try:
            with _mh.neutral_grid_env():
                arcpy.management.CopyRaster(out_scratch, out_raster)
        except Exception as e:
            ext = os.path.splitext(str(out_raster))[1]
            if ext and ext.lower() not in (".tif", ".tiff", ".img"):
                raise arcpy.ExecuteError(
                    "Could not write {}: the '{}' format may not support "
                    "this raster's 32-bit floating-point values. Use .tif "
                    "or .img, or save into a geodatabase. ({})".format(
                        out_raster, ext, e))
            raise
    finally:
        arcpy.env.pyramid = prev_pyramid
    if out_stats and out_stats.get("on_raster"):
        _hs._msg(messages, "Statistics and histogram were written with the "
                           "raster (no scan needed).")
    elif not _mh._apply_known_statistics(out_raster, out_stats, messages):
        try:
            arcpy.management.CalculateStatistics(out_raster)
        except Exception as e:
            if messages is not None:
                messages.addWarningMessage(
                    "Could not calculate raster statistics: {}".format(e))
    try:
        md = arcpy.metadata.Metadata(out_raster)
        md.title = title
        md.tags = tags
        md.summary = summary
        md.description = description
        md.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                      "(http://www.jennessent.com). Method: " +
                      (citation or _CITATION))
        md.save()
    except Exception:
        pass
    try:
        arcpy.management.Delete(out_scratch)
    except Exception:
        pass
    _mh.phase_advance()
    try:
        arcpy.management.BuildPyramids(out_raster,
                                       resample_technique="BILINEAR")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage(
                "Could not build pyramids: {}".format(e))


def run_surface_area(in_raster, out_area=None, out_ratio=None, out_flat=None,
                     elev_units="Meters", area_units="Sq. Meters",
                     flat_units="Sq. Meters",
                     corner_method=None, detrend=False, detrend_radius=1.5,
                     detrend_units="Cells", messages=None):
    """Compute any combination of the Surface Area, Surface Ratio, and Flat
    Area rasters (at least one required). See the module docstring.

    When ``detrend`` is True, the DEM is first locally detrended (Dilts et al.
    2023) by subtracting a circular neighborhood mean of radius
    ``detrend_radius`` ``detrend_units`` (default 1.5 cells = a 3x3 mean), so
    the surface ratio measures fine-scale surface roughness with the smooth
    underlying topography removed (Dilts's SAPR_L). The surface RATIO is the
    meaningful ruggedness output in that mode; the surface-area and flat-area
    outputs then describe the detrended residual surface."""
    gdal = _mh._gdal()

    if not (out_area or out_ratio or out_flat):
        raise arcpy.ExecuteError(
            "At least one output raster (surface area, surface ratio, or "
            "flat area) must be specified.")
    if out_area and area_units not in _AREA_MULT:
        raise arcpy.ExecuteError("Unknown area unit: {}.".format(area_units))
    if out_flat and flat_units not in _AREA_MULT:
        raise arcpy.ExecuteError("Unknown area unit: {}.".format(flat_units))
    corner_method = corner_method or CORNER_METHODS[0]
    if corner_method not in CORNER_METHODS:
        raise arcpy.ExecuteError(
            "Unknown corner interpolation method: {}.".format(corner_method))
    four_cell = (corner_method == CORNER_METHODS[1])
    out_area = _resolve_output(out_area, messages)
    out_ratio = _resolve_output(out_ratio, messages)
    out_flat = _resolve_output(out_flat, messages)

    phases = [("Calculating surface areas...", 8)]
    for key, wanted, name in (("area", out_area, "surface area"),
                              ("ratio", out_ratio, "surface ratio"),
                              ("flat", out_flat, "flat area")):
        if wanted:
            phases.append(("Saving the {} raster...".format(name), 1))
            phases.append(("Building pyramids for the {} raster...".format(name), 1))
            if key in ("area", "ratio"):
                phases.append(("Creating layer file and symbology for the "
                               "{} raster...".format(name), 1))
    _mh.begin_phases(phases)

    scratch = arcpy.env.scratchFolder
    detrend_scratch = None
    detrend_meta = ""
    work_raster = in_raster
    if detrend:
        detrend_scratch, dnote = _detrended_dem(
            in_raster, detrend_radius, detrend_units, messages)
        work_raster = detrend_scratch
        detrend_meta = (
            " LOCAL (detrended) variant: before the surface-area computation "
            "the DEM was detrended by subtracting a circular neighborhood mean "
            "(radius {}), leaving only fine-scale surface variation, so the "
            "surface ratio measures surface roughness with the smooth "
            "underlying topography removed (Dilts et al. 2023, SAPR_L). "
            "{}").format(dnote, _DETREND_CITATION)
    src, in_copy = _hs._open_source(work_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    in_nodata = band.GetNoDataValue()
    sr = arcpy.Describe(work_raster).spatialReference

    geom = _hs.ground_geometry(gt, width, height, sr, proj, messages)
    geographic = geom.geographic
    if geographic:
        elev_mpu = 1.0                  # legacy: geographic DEMs are meters
    else:
        elev_mpu = _hs._ELEV_METERS.get(elev_units, 1.0)
        _hs._msg(messages, "Elevation units = {}.".format(elev_units))
    _hs._msg(messages, "Surface area method: 8-triangle ({}). Corner "
                       "interpolation: {}.".format(_CITATION, corner_method))

    mult_area = _AREA_MULT.get(area_units, 1.0)
    mult_flat = _AREA_MULT.get(flat_units, 1.0)

    outputs = {}                        # key -> (scratch, band, ds, stats)
    for key, wanted in (("area", out_area), ("ratio", out_ratio),
                        ("flat", out_flat)):
        if wanted:
            path = arcpy.CreateUniqueName("surf_{}.tif".format(key), scratch)
            ds, oband = _make_output(gdal, path, gt, proj, width, height)
            outputs[key] = {"scratch": path, "ds": ds, "band": oband,
                            "stats": _mh._RasterStats()}
    # Release the loop locals: a lingering reference would keep the LAST
    # dataset open past its close below, so CopyRaster would read a GTiff
    # whose tiles were still in GDAL's block cache (all-NoData on disk).
    ds = oband = None

    strips = [(y, min(_STRIP_ROWS, height - y))
              for y in range(0, height, _STRIP_ROWS)]

    for idx, (yoff, rows) in enumerate(strips):
        top = max(yoff - 1, 0)
        bot = min(yoff + rows + 1, height)
        pad = band.ReadAsArray(0, top, width, bot - top).astype(np.float64)
        valid = np.isfinite(pad)
        if in_nodata is not None:
            valid &= (pad != in_nodata)
        valid = _mh.mask_band_valid(band, valid, 0, top)
        pad = np.where(valid, pad * elev_mpu, 0.0)

        out_arrs = {k: np.full((rows, width), NODATA_OUT, dtype=np.float32)
                    for k in outputs}
        if "flat" in outputs:
            # Flat area is valid for EVERY cell (legacy behavior).
            flat_vals = geom.half_rows(yoff, yoff + rows)["pix_m2"] * mult_flat
            out_arrs["flat"][:] = flat_vals.astype(np.float32)
            outputs["flat"]["stats"].add(np.asarray(flat_vals).ravel())

        r0 = yoff if yoff > 0 else 1
        r1 = (yoff + rows) if (yoff + rows) < height else height - 1
        if ("area" in outputs or "ratio" in outputs) and (
                r1 > r0 or (r1 == r0 and r0 < height - 1)):
            sl = slice(r0 - top, r1 - top)
            up = slice(r0 - top - 1, r1 - top - 1)
            dn = slice(r0 - top + 1, r1 - top + 1)

            NW, N_, NE = pad[up, :-2], pad[up, 1:-1], pad[up, 2:]
            W_, C_, E_ = pad[sl, :-2], pad[sl, 1:-1], pad[sl, 2:]
            SW, S_, SE = pad[dn, :-2], pad[dn, 1:-1], pad[dn, 2:]

            # per-cell 8-triangle table, interior columns (as the slices)
            g = {k: v[:, 1:-1] for k, v in geom.half_rows(r0, r1).items()}
            pixm2 = g["pix_m2"]

            area = _eight_triangle_area(NW, N_, NE, W_, C_, E_, SW, S_, SE,
                                        g, four_cell)

            ok = (valid[up, :-2] & valid[up, 1:-1] & valid[up, 2:] &
                  valid[sl, :-2] & valid[sl, 1:-1] & valid[sl, 2:] &
                  valid[dn, :-2] & valid[dn, 1:-1] & valid[dn, 2:])

            if "area" in outputs:
                vals = area * mult_area
                center = out_arrs["area"][(r0 - yoff):(r1 - yoff), 1:-1]
                center[ok] = vals[ok].astype(np.float32)
                if ok.any():
                    outputs["area"]["stats"].add(vals[ok])
            if "ratio" in outputs:
                ratio = area / pixm2
                center = out_arrs["ratio"][(r0 - yoff):(r1 - yoff), 1:-1]
                center[ok] = ratio[ok].astype(np.float32)
                if ok.any():
                    outputs["ratio"]["stats"].add(ratio[ok])

        for k in outputs:
            outputs[k]["band"].WriteArray(out_arrs[k], 0, yoff)
        _mh.phase_step(idx + 1, len(strips))

    src = None
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass
    if detrend_scratch:
        try:
            arcpy.management.Delete(detrend_scratch)
        except Exception:
            pass

    # Finalize and deliver each requested output.
    geo_note = _hs.ground_note_for_metadata(geom)
    corner_note = "Corner interpolation: {}.".format(corner_method)
    deliveries = {
        "area": (out_area, "surface area, 3D area, topography, terrain, "
                 "Jenness Enterprises",
                 "Created by Surface Area and Ratio from {src}: surface "
                 "area in {u}, elevation units {e}. {c} {g}",
                 "Surface area raster: the three-dimensional area of the "
                 "land surface within each cell, from the 8-triangle method "
                 "of Jenness (2004). Larger values indicate steeper or more "
                 "convoluted terrain. " + _CITATION),
        "ratio": (out_ratio, "surface ratio, ruggedness, topography, "
                  "terrain, Jenness Enterprises",
                  "Created by Surface Area and Ratio from {src}: surface "
                  "area / flat area (unitless), elevation units {e}. {c} {g}",
                  "Surface ratio raster: the ratio of each cell's "
                  "three-dimensional surface area to its planimetric (flat) "
                  "area -- a measure of topographic roughness (1 = flat). "
                  "From the 8-triangle method of Jenness (2004). " + _CITATION),
        "flat": (out_flat, "flat area, planimetric area, cell area, "
                 "Jenness Enterprises",
                 "Created by Surface Area and Ratio from {src}: planimetric "
                 "cell area in {u}. {g}",
                 "Flat (planimetric) area raster: the map area of each "
                 "cell. Constant on projected rasters; varies by row on "
                 "geographic rasters, where cell areas are computed on the "
                 "spheroid. " + _CITATION),
    }
    unit_for = {"area": area_units, "ratio": "", "flat": flat_units}
    name_for = {"area": "surface area", "ratio": "surface ratio", "flat": "flat area"}
    delivered = {"area": out_area, "ratio": out_ratio, "flat": out_flat}
    lyrx = {}
    for key, info in outputs.items():
        info["band"].FlushCache()
        info["ds"].FlushCache()
        out_stats = _mh._finalize_stats(info["band"], info["stats"], messages)
        if out_stats is not None:
            out_stats["on_raster"] = _mh._write_gtiff_stats(info["band"],
                                                            out_stats)
        info["band"].FlushCache()
        info["ds"].FlushCache()
        info["band"] = None
        info["ds"] = None               # close -> flush tiles + write PAM
        dest, tags, summary_fmt, description = deliveries[key]
        _mh.phase_advance("Saving the {} raster...".format(name_for[key]))
        _deliver_output(
            info["scratch"], dest, out_stats, messages,
            title=os.path.basename(str(dest)),
            summary=summary_fmt.format(
                src=str(in_raster), u=unit_for[key],
                e=("Meters (geographic DEM)" if geographic else elev_units),
                c=corner_note, g=geo_note) + detrend_meta,
            description=description + detrend_meta, tags=tags)
        if key in ("area", "ratio"):
            # Blue (low) -> yellow -> red (high) output-parameter symbology.
            # Flat area isn't a measure of ruggedness/magnitude in the same
            # sense, so it's left with no special default symbology.
            _mh.phase_advance("Creating layer file and symbology for the "
                              "{} raster...".format(name_for[key]))
            lyrx[key] = _mh.default_stretch_lyrx(
                dest, [(95, 135, 193), (249, 254, 204), (224, 70, 53)],
                messages=messages)
        _hs._msg(messages, "{} raster complete: {}".format(
            {"area": "Surface area", "ratio": "Surface ratio",
             "flat": "Flat area"}[key], dest))
    _mh.end_phases()
    return delivered, lyrx


# ----------------------------------------------------------------------------
#  Vector Ruggedness Measure (VRM)
# ----------------------------------------------------------------------------

RADIUS_UNITS = _hs.RADIUS_UNITS          # ["Cells", "Meters", "Kilometers", "Feet", "Miles"]
_RADIUS_METERS = _hs._RADIUS_METERS

_VRM_CITATION = (
    "Sappington, J. M., K. M. Longshore, and D. B. Thompson. 2007. "
    "Quantifying landscape ruggedness for animal habitat analysis: a case "
    "study using bighorn sheep in the Mojave Desert. Journal of Wildlife "
    "Management 71(5):1419-1426.")

# ----------------------------------------------------------------------------
#  Local detrending (Dilts et al. 2023) -- shared "3rd-generation" pre-process
# ----------------------------------------------------------------------------
#
# Dilts et al. (2023) showed that the surface ratio (Jenness 2004, a "1st
# generation" index correlated with slope) and the "2nd generation" indices
# VRM (Sappington 2007) and ACR (Du Preez 2015) are all still inflated by
# SMOOTH folds -- ridgelines and drainages read as "rugged" merely because
# adjacent cells face different directions, even where the surface itself is
# smooth. Their fix is a simple, powerful pre-process: subtract a smoothed copy
# of the DEM from the DEM to leave a "local-difference" surface that keeps only
# the fine-scale variation (the smooth underlying topography removed), then run
# the ordinary metric on THAT. The resulting "local" variants (Dilts writes
# them SAPR_L / VRM_L / ACR_L) also remove the correlation with curvature, so
# they map surface roughness per se -- passing all three of Dilts's "ideal
# ruggedness" criteria (independent of slope, of smooth folding, and increasing
# only with true vertical distortion). The smoothing here is a circular
# neighborhood mean; a radius of 1.5 cells (the default) is exactly the 3x3
# mean Dilts used (all eight neighbors sit within 1.5 cells), while a larger
# radius removes broader-scale trend before the roughness is measured.

_DETREND_CITATION = (
    "Dilts, T. E., M. E. Blum, K. T. Shoemaker, P. J. Weisberg, and K. M. "
    "Stewart. 2023. Improved topographic ruggedness indices more accurately "
    "model fine-scale ecological patterns. Landscape Ecology 38:1395-1410 "
    "(https://doi.org/10.1007/s10980-023-01646-6).")


def _radius_row_cells(gt, height, sr, radius, radius_units, width=None,
                      proj=None):
    """(rx_rows, ry, note): circular-neighborhood radius expressed in CELLS,
    per row -- constant for Cells units, otherwise sized per row from the
    true ground cell size on the spheroid (GroundGeometry; a projected
    raster is unprojected, so Web Mercator and the like get the right number
    of cells). ``note`` is a short human-readable description of the
    radius."""
    if radius_units == "Cells":
        return (np.full(height, float(radius)), float(radius),
                "{:g} cells".format(radius))
    radius_m = radius * _RADIUS_METERS[radius_units]
    g = _hs.ground_geometry(gt, width or 1, height, sr, proj).row
    rx_rows = radius_m / np.maximum(g["h"], 1e-9)
    ry = radius_m / max(float(np.mean(g["vA"])), 1e-9)
    note = "{:g} {} = {:,.1f} m (sized per row on the spheroid)".format(
        radius, radius_units.lower(), radius_m)
    return rx_rows, ry, note


def _detrended_dem(in_raster, radius, radius_units, messages):
    """Write a scratch 'local-difference' DEM (Dilts et al. 2023): the input
    DEM minus its circular-neighborhood mean, leaving only fine-scale surface
    variation. Returns (scratch_path, note); the caller runs its ordinary
    metric on the scratch and is responsible for deleting it. Elevation units
    are preserved (the subtraction is linear), so downstream unit handling is
    unchanged."""
    if float(radius) <= 0:
        raise arcpy.ExecuteError("The detrending radius must be positive.")
    if radius_units not in RADIUS_UNITS:
        raise arcpy.ExecuteError(
            "Unknown detrending radius unit: {}.".format(radius_units))
    gdal = _mh._gdal()
    scratch = arcpy.env.scratchFolder
    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    try:
        band = src.GetRasterBand(1)
        width, height = src.RasterXSize, src.RasterYSize
        if width < 3 or height < 3:
            raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        in_nodata = band.GetNoDataValue()
        sr = arcpy.Describe(in_raster).spatialReference
        z = band.ReadAsArray(0, 0, width, height).astype(np.float64)
        valid = np.isfinite(z)
        if in_nodata is not None:
            valid &= (z != in_nodata)
        valid = _mh.mask_band_valid(band, valid)
        rx_rows, ry, note = _radius_row_cells(
            gt, height, sr, radius, radius_units, width, proj)
        smoothed = _hs._blur_grid(z, valid, rx_rows, ry, "Average")
        resid = np.where(valid & np.isfinite(smoothed),
                         z - smoothed, NODATA_OUT).astype(np.float32)
        out_path = arcpy.CreateUniqueName("detrend.tif", scratch)
        ds, oband = _make_output(gdal, out_path, gt, proj, width, height)
        oband.WriteArray(resid)
        oband.FlushCache()
        ds.FlushCache()
        oband = None
        ds = None
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass
    _hs._msg(messages, "Local detrending (Dilts et al. 2023): subtracted a "
                       "circular neighborhood mean (radius {}) to remove the "
                       "smooth underlying topography before the index is "
                       "computed.".format(note))
    return out_path, note


def _row_gradient_geometry(gt, width, height, sr, proj, elev_mpu,
                           messages=None):
    """``(ground, geographic, zscale)``: the raster's ``GroundGeometry``
    (true ground spans for every cell, any coordinate system -- see
    hillshade.GroundGeometry; its note is posted to ``messages``), whether
    it is geographic, and the elevation scale to meters."""
    ground = _hs.ground_geometry(gt, width, height, sr, proj, messages)
    return ground, ground.geographic, float(elev_mpu)


def _unit_normals(z, valid, ground):
    """Unit normal vectors (nx, ny, nz) for the WHOLE grid ``z`` (meters,
    already z-scaled), via the Horn gradient: n = (-dz/dx, -dz/dy, 1) /
    ||...||. This is the same vector Sappington's slope/aspect trig
    decomposition produces (a global rotation of coordinate convention
    changes no vector's length, and VRM depends only on the length of the
    SUM of these vectors) -- but reuses the toolbox's tested, geodesic-correct
    per-cell ground spans (``ground`` is a hillshade.GroundGeometry; the
    grid is processed in row strips). Returns (nx, ny, nz, ok) full-grid
    arrays; ``ok`` is False wherever the cell's own 3x3 window touches
    invalid/out-of-bounds data (NaN elsewhere in nx/ny/nz)."""
    H, W = z.shape
    zp = np.full((H + 2, W + 2), np.nan)
    zp[1:-1, 1:-1] = z
    vp = np.zeros((H + 2, W + 2), dtype=bool)
    vp[1:-1, 1:-1] = valid

    nx = np.full((H, W), np.nan)
    ny = np.full((H, W), np.nan)
    nz = np.full((H, W), np.nan)
    for r0 in range(0, H, _hs.GEOM_STRIP):
        r1 = min(H, r0 + _hs.GEOM_STRIP)
        g = ground.rows(r0, r1)
        up, sl, dn = slice(r0, r1), slice(r0 + 1, r1 + 1), slice(r0 + 2, r1 + 2)
        NW, N_, NE = zp[up, :-2], zp[up, 1:-1], zp[up, 2:]
        W_, E_ = zp[sl, :-2], zp[sl, 2:]
        SW, S_, SE = zp[dn, :-2], zp[dn, 1:-1], zp[dn, 2:]
        with np.errstate(invalid="ignore"):
            dzdx = ((NE + 2.0 * E_ + SE) - (NW + 2.0 * W_ + SW)) / \
                   (2.0 * g["hA"] + 4.0 * g["h"] + 2.0 * g["hB"])
            dzdy = ((SW + 2.0 * S_ + SE) - (NW + 2.0 * N_ + NE)) / \
                   (4.0 * g["vA"] + 4.0 * g["vB"])
            norm = np.sqrt(dzdx * dzdx + dzdy * dzdy + 1.0)
            nx[r0:r1] = -dzdx / norm
            ny[r0:r1] = -dzdy / norm
            nz[r0:r1] = 1.0 / norm

    ok = (vp[:-2, :-2] & vp[:-2, 1:-1] & vp[:-2, 2:] &
          vp[1:-1, :-2] & vp[1:-1, 1:-1] & vp[1:-1, 2:] &
          vp[2:, :-2] & vp[2:, 1:-1] & vp[2:, 2:])
    nx = np.where(ok, nx, np.nan)
    ny = np.where(ok, ny, np.nan)
    nz = np.where(ok, nz, np.nan)
    return nx, ny, nz, ok


def _circular_vector_sums(nx, ny, nz, ok, rx_rows, ry):
    """Per-cell resultant-vector sums (Rx, Ry, Rz) and valid-neighbor count
    (n) over a circular, NoData-ignoring neighborhood -- the multi-field
    analog of ``hillshade._blur_grid``, sharing one uniform kernel per
    distinct row-radius group via ``hillshade._row_kernel_groups`` (only
    "Average" weighting makes sense for a vector resultant sum; VRM's
    definition has no concept of distance weighting). Row grouping uses the
    EXACT radius when it is constant across rows (Cells units or a projected
    raster) -- see ``_row_kernel_groups`` for why naive rounding there would
    be a real bug, not just an approximation."""
    fields = [np.where(ok, nx, 0.0), np.where(ok, ny, 0.0),
              np.where(ok, nz, 0.0), ok.astype(np.float64)]
    outs = [np.zeros_like(f) for f in fields]
    # Memory-safe size-aware neighborhood convolution (direct for small kernels,
    # overlap-add FFT for large) -- see hillshade.nbr_conv. A whole-grid
    # fftconvolve here raised MemoryError on large regional rasters.
    for k, rows in _hs._row_kernel_groups(rx_rows, ry, "Average"):
        for f, out in zip(fields, outs):
            out[rows] = _hs.nbr_conv(f, k)[rows]
    return outs[0], outs[1], outs[2], outs[3]


def run_vrm(in_raster, out_raster, elev_units="Meters", radius=1.5,
           radius_units="Cells", detrend=False, detrend_radius=1.5,
           detrend_units="Cells", messages=None):
    """Vector Ruggedness Measure (VRM): the 3-D analog of Fisher's circular
    variance (1993, eq. 2.10-2.11) applied to unit vectors orthogonal to the
    terrain surface, after Sappington et al. (2007) and Hobson (1972). See
    the module docstring.

    When ``detrend`` is True the DEM is first locally detrended (Dilts et al.
    2023) by subtracting a circular neighborhood mean of radius
    ``detrend_radius`` ``detrend_units`` (default 1.5 cells = a 3x3 mean), so
    the vectors reflect only fine-scale surface roughness with the smooth
    underlying topography removed (Dilts's VRM_L); this stops smooth ridgelines
    and drainages from reading as rugged. The detrending neighborhood is
    independent of, and typically smaller than, the VRM ``radius``.

    For each cell, the unit normal vectors of every valid cell within
    ``radius`` (in ``radius_units``; a circular, NoData-ignoring
    neighborhood, geodesic per row on geographic DEMs) are summed; the
    output is 1 minus the length of that resultant divided by the number of
    vectors -- 0 where the neighborhood is flat and uniformly oriented, up
    to 1 where it is maximally dispersed. The neighborhood is CIRCULAR
    (Euclidean distance), matching the Blurred Hillshade radius convention,
    rather than Sappington's original literal N x N square; because a
    diagonal neighbor sits sqrt(2) cells away, a radius of 1.5 cells (the
    default) is the smallest that reproduces their classic 3x3 neighborhood
    exactly (all 8 surrounding cells plus the focal cell) -- radius = 1
    cells is a smaller "plus" shape (4 orthogonal neighbors only). Elevation
    data is read out to ``radius`` + 1 cell so every neighbor's own
    orthogonal vector (which needs its own 3x3 window) can be computed.
    """
    gdal = _mh._gdal()
    radius = float(radius)
    if radius <= 0:
        raise arcpy.ExecuteError("The radius must be positive.")
    if radius_units not in RADIUS_UNITS:
        raise arcpy.ExecuteError("Unknown radius unit: {}.".format(radius_units))
    out_raster = _mh.resolve_raster_output(out_raster, messages)

    _mh.begin_phases([
        ("Reading the DEM...", 1),
        ("Computing orthogonal vectors...", 3),
        ("Calculating vector dispersion...", 5),
        ("Saving the VRM raster...", 1),
        ("Building pyramids...", 1),
        ("Creating layer file and symbology...", 1),
    ])

    scratch = arcpy.env.scratchFolder
    detrend_scratch = None
    detrend_meta = ""
    work_raster = in_raster
    if detrend:
        detrend_scratch, dnote = _detrended_dem(
            in_raster, detrend_radius, detrend_units, messages)
        work_raster = detrend_scratch
        detrend_meta = (
            " LOCAL (detrended) variant: the DEM was detrended by subtracting "
            "a circular neighborhood mean (radius {}) before computing VRM, so "
            "this measures fine-scale surface roughness with the smooth "
            "underlying topography removed (Dilts et al. 2023, VRM_L). "
            "{}").format(dnote, _DETREND_CITATION)
    src, in_copy = _hs._open_source(work_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    in_nodata = band.GetNoDataValue()
    sr = arcpy.Describe(work_raster).spatialReference

    elev_mpu = 1.0 if sr.type == "Geographic" else _hs._ELEV_METERS.get(elev_units, 1.0)
    geom, geographic, zscale = _row_gradient_geometry(gt, width, height, sr,
                                                      proj, elev_mpu, messages)
    if not geographic:
        _hs._msg(messages, "Elevation units = {}.".format(elev_units))

    if radius_units == "Cells":
        rx_rows = np.full(height, radius)
        ry = radius
        _hs._msg(messages, "VRM radius: {:g} cells.".format(radius))
    else:
        radius_m = radius * _RADIUS_METERS[radius_units]
        rx_rows = radius_m / np.maximum(geom.row["h"], 1e-9)
        ry = radius_m / max(float(np.mean(geom.row["vA"])), 1e-9)
        _hs._msg(messages, "VRM radius: {:g} {} = {:,.1f} m, sized per row "
                           "on the spheroid ({:.1f}-{:.1f} cells across, "
                           "{:.1f} cells down).".format(
                               radius, radius_units.lower(), radius_m,
                               float(rx_rows.min()), float(rx_rows.max()),
                               ry))

    z = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(z)
    if in_nodata is not None:
        valid &= (z != in_nodata)
    valid = _mh.mask_band_valid(band, valid)
    z = np.where(valid, z * zscale, 0.0)
    src = None
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass
    if detrend_scratch:
        try:
            arcpy.management.Delete(detrend_scratch)
        except Exception:
            pass

    _mh.phase_advance("Computing orthogonal vectors...")
    nx, ny, nz, ok = _unit_normals(z, valid, geom)
    del z

    _mh.phase_advance("Calculating vector dispersion...")
    Rx, Ry, Rz, n = _circular_vector_sums(nx, ny, nz, ok, rx_rows, ry)
    del nx, ny, nz, ok
    # n is an exact integer count of contributing cells; fftconvolve leaves
    # ~1e-16-scale roundoff noise that a bare `n > 0` test cannot distinguish
    # from a true zero, so round before testing (otherwise a handful of
    # cells with NO valid neighbor get a spurious, numerically unstable
    # "valid" VRM value from dividing by that noise).
    n = np.rint(n)

    with np.errstate(invalid="ignore", divide="ignore"):
        R = np.sqrt(Rx * Rx + Ry * Ry + Rz * Rz)
        vrm = 1.0 - R / n
    np.clip(vrm, 0.0, 1.0, out=vrm)
    out_valid = n > 0.0
    n_out = int(out_valid.sum())
    _hs._msg(messages, "Vector Ruggedness Measure ({}): {:,} of {:,} cells "
                       "have at least one valid neighbor vector.".format(
                           _VRM_CITATION, n_out, width * height))

    out_path = arcpy.CreateUniqueName("vrm.tif", scratch)
    ds, oband = _make_output(gdal, out_path, gt, proj, width, height)
    oband.WriteArray(np.where(out_valid, vrm, NODATA_OUT).astype(np.float32))
    oband.FlushCache()
    ds.FlushCache()
    stats = _mh._RasterStats()
    if out_valid.any():
        stats.add(vrm[out_valid].astype(np.float64))
    out_stats = _mh._finalize_stats(oband, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(oband, out_stats)
    oband.FlushCache()
    ds.FlushCache()
    oband = None
    ds = None                            # close -> flush tiles + write PAM

    _mh.phase_advance("Saving the VRM raster...")
    _deliver_output(
        out_path, out_raster, out_stats, messages,
        title=os.path.basename(str(out_raster)),
        summary=("Created by Vector Ruggedness Measure from {src}: radius "
                 "{r:g} {u}, elevation units {e}.").format(
                     src=str(in_raster), r=radius, u=radius_units.lower(),
                     e=("Meters (geographic DEM)" if geographic else elev_units)
                     ) + detrend_meta,
        description=(
            "Vector Ruggedness Measure (VRM) raster: a topographic "
            "roughness index (Sappington et al. 2007) that quantifies the "
            "3-D dispersion of the unit vectors orthogonal to the terrain "
            "surface within a neighborhood of each cell -- the spherical "
            "analog of circular variance (Fisher 1993). Values range from "
            "0 (a flat and/or uniformly sloped neighborhood) to 1 "
            "(maximally rugged); unlike slope alone, VRM distinguishes "
            "steep-but-even terrain from steep, broken terrain. " +
            _VRM_CITATION + detrend_meta),
        tags=("vector ruggedness measure, VRM, terrain ruggedness, "
             "roughness, topography, Jenness Enterprises"),
        citation=_VRM_CITATION)
    # Blue (low ruggedness) -> yellow -> red (high ruggedness) output-
    # parameter symbology.
    _mh.phase_advance("Creating layer file and symbology...")
    out_lyrx = _mh.default_stretch_lyrx(
        out_raster, [(95, 135, 193), (249, 254, 204), (224, 70, 53)],
        messages=messages)
    _mh.end_phases()
    _hs._msg(messages, "Vector Ruggedness Measure complete: {}".format(out_raster))
    return out_lyrx


# ----------------------------------------------------------------------------
#  Arc-Chord Ratio (ACR) rugosity  (Du Preez 2015)
# ----------------------------------------------------------------------------
#
# ACR is a "2nd generation" rugosity index (like VRM) that removes the slope
# dependence of the plain surface ratio. Rugosity = contoured (3-D surface)
# area / planar area, where the ordinary Surface Ratio (Jenness 2004) uses the
# HORIZONTAL cell footprint as the planar area -- so a smooth but steeply
# tilted surface scores as rough. Du Preez (2015) instead projects the surface
# onto its PLANE OF BEST FIT (POBF) before measuring the planar area; a smooth
# tilted plane then has rugosity 1 regardless of its slope. For the raster
# (per-cell) case, Du Preez's own steps (his Table 1b) are: (1-2) contoured
# area = the Jenness (2004) eight-triangle 3x3 surface area; (4) POBF slope =
# the standard 3x3 (Horn) slope; (5) POBF planar area = horizontal cell area /
# cos(slope); (6) ACR = contoured area / planar area. That reduces exactly to
#
#       ACR = (Jenness Surface Ratio) x cos(3x3 best-fit-plane slope)
#
# i.e. our Surface Ratio "de-tilted" by the cosine of the fitted-plane slope.
# The cosine is nz, the vertical component of the Horn unit normal already used
# by VRM (nz = 1/sqrt(dzdx^2 + dzdy^2 + 1) = cos slope), so the whole tool is
# just the two existing machineries multiplied together. ACR ranges from 1
# (flat OR smoothly sloped) upward. Like the Surface Ratio it is an inherently
# 3x3 measure; broader-scale roughness comes from the optional Dilts (2023)
# local detrending (ACR_L), not a neighborhood radius.

_ACR_CITATION = (
    "Du Preez, C. 2015. A new arc-chord ratio (ACR) rugosity index for "
    "quantifying three-dimensional landscape structural complexity. Landscape "
    "Ecology 30:181-192 (https://doi.org/10.1007/s10980-014-0118-8).")


def run_acr(in_raster, out_raster, elev_units="Meters", corner_method=None,
            detrend=False, detrend_radius=1.5, detrend_units="Cells",
            messages=None):
    """Arc-Chord Ratio (ACR) rugosity (Du Preez 2015): the ratio of each
    cell's 3-D surface area to the area of that surface projected onto its 3x3
    plane of best fit -- equivalently the Jenness (2004) surface ratio times
    the cosine of the 3x3 best-fit-plane slope, which removes the slope
    inflation of the plain surface ratio (a smooth tilted plane has ACR = 1).
    Values run from 1 (flat or smoothly sloped) upward. When ``detrend`` is
    True the DEM is first locally detrended (Dilts et al. 2023, ACR_L) by
    subtracting a circular neighborhood mean of radius ``detrend_radius``
    ``detrend_units`` (default 1.5 cells = a 3x3 mean), removing the smooth
    underlying topography so ridgelines and drainages no longer read as rugged.
    Returns the output's default-symbology .lyrx path (or None)."""
    gdal = _mh._gdal()
    corner_method = corner_method or CORNER_METHODS[0]
    if corner_method not in CORNER_METHODS:
        raise arcpy.ExecuteError(
            "Unknown corner interpolation method: {}.".format(corner_method))
    four_cell = (corner_method == CORNER_METHODS[1])
    out_raster = _mh.resolve_raster_output(out_raster, messages)

    _mh.begin_phases([
        ("Reading the DEM...", 1),
        ("Calculating surface areas and best-fit-plane slope...", 6),
        ("Saving the ACR raster...", 1),
        ("Building pyramids...", 1),
        ("Creating layer file and symbology...", 1),
    ])

    scratch = arcpy.env.scratchFolder
    detrend_scratch = None
    detrend_meta = ""
    work_raster = in_raster
    if detrend:
        detrend_scratch, dnote = _detrended_dem(
            in_raster, detrend_radius, detrend_units, messages)
        work_raster = detrend_scratch
        detrend_meta = (
            " LOCAL (detrended) variant: the DEM was detrended by subtracting "
            "a circular neighborhood mean (radius {}) before computing ACR, so "
            "this measures fine-scale surface roughness with the smooth "
            "underlying topography removed (Dilts et al. 2023, ACR_L). "
            "{}").format(dnote, _DETREND_CITATION)

    src, in_copy = _hs._open_source(work_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    in_nodata = band.GetNoDataValue()
    sr = arcpy.Describe(work_raster).spatialReference
    geographic = (sr.type == "Geographic")
    elev_mpu = 1.0 if geographic else _hs._ELEV_METERS.get(elev_units, 1.0)

    ground, _gg, zscale = _row_gradient_geometry(gt, width, height, sr, proj,
                                                 elev_mpu, messages)
    if not geographic:
        _hs._msg(messages, "Elevation units = {}.".format(elev_units))

    z = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(z)
    if in_nodata is not None:
        valid &= (z != in_nodata)
    valid = _mh.mask_band_valid(band, valid)
    zwork = np.where(valid, z * elev_mpu, 0.0)      # meters, for area and slope
    src = None
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass
    if detrend_scratch:
        try:
            arcpy.management.Delete(detrend_scratch)
        except Exception:
            pass

    _mh.phase_advance("Calculating surface areas and best-fit-plane slope...")
    # Surface ratio via the shared 8-triangle method over the whole grid, with
    # a one-cell NaN/False border so edge and NoData-adjacent cells are masked.
    zp = np.full((height + 2, width + 2), 0.0)
    zp[1:-1, 1:-1] = zwork
    vp = np.zeros((height + 2, width + 2), dtype=bool)
    vp[1:-1, 1:-1] = valid
    NW, N_, NE = zp[:-2, :-2], zp[:-2, 1:-1], zp[:-2, 2:]
    W_, C_, E_ = zp[1:-1, :-2], zp[1:-1, 1:-1], zp[1:-1, 2:]
    SW, S_, SE = zp[2:, :-2], zp[2:, 1:-1], zp[2:, 2:]
    g = ground.half_rows(0, height)              # per-cell 8-triangle table
    with np.errstate(invalid="ignore"):
        area = _eight_triangle_area(NW, N_, NE, W_, C_, E_, SW, S_, SE,
                                    g, four_cell)
        ratio = area / g["pix_m2"]

    # cos(best-fit-plane slope) = nz, the vertical component of the Horn unit
    # normal (VRM's machinery). ACR = surface ratio x cos(slope).
    _nx, _ny, nz, ok = _unit_normals(zwork, valid, ground)
    del _nx, _ny

    with np.errstate(invalid="ignore"):
        acr = ratio * nz
    # ACR's theoretical minimum is 1 (a flat or smoothly sloped surface). The
    # 3-D surface area (8-triangle) and the planar area (horizontal area /
    # cos slope) come from slightly different surface models, so on curved
    # cells their product can dip a hair below 1; clamp to the documented
    # floor, exactly as VRM is clamped to its own [0, 1] range above.
    out_valid = ok & np.isfinite(acr)
    acr = np.where(out_valid, np.maximum(acr, 1.0), NODATA_OUT).astype(np.float32)
    n_out = int(out_valid.sum())
    _hs._msg(messages, "Arc-Chord Ratio ({}): {:,} of {:,} cells have a "
                       "defined value.".format(
                           _ACR_CITATION, n_out, width * height))

    out_path = arcpy.CreateUniqueName("acr.tif", scratch)
    ds, oband = _make_output(gdal, out_path, gt, proj, width, height)
    oband.WriteArray(acr)
    oband.FlushCache()
    ds.FlushCache()
    stats = _mh._RasterStats()
    if out_valid.any():
        stats.add(acr[out_valid].astype(np.float64))
    out_stats = _mh._finalize_stats(oband, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(oband, out_stats)
    oband.FlushCache()
    ds.FlushCache()
    oband = None
    ds = None

    elev_label = "Meters (geographic DEM)" if geographic else elev_units
    _mh.phase_advance("Saving the ACR raster...")
    _deliver_output(
        out_path, out_raster, out_stats, messages,
        title=os.path.basename(str(out_raster)),
        summary=("Created by Arc-Chord Ratio (ACR) from {src}: elevation "
                 "units {e}; corner interpolation {c}.").format(
                     src=str(in_raster), e=elev_label, c=corner_method)
                 + detrend_meta,
        description=(
            "Arc-Chord Ratio (ACR) rugosity raster (Du Preez 2015): each "
            "cell's 3-D surface area divided by the area of its plane of best "
            "fit -- equivalently the Jenness (2004) surface ratio times the "
            "cosine of the 3x3 best-fit-plane slope. Unlike the plain surface "
            "ratio, ACR is decoupled from slope, so a smooth tilted surface "
            "has ACR = 1 (the minimum) and larger values indicate genuine "
            "surface roughness. " + _ACR_CITATION + " " + _CITATION +
            detrend_meta),
        tags=("arc-chord ratio, ACR, rugosity, ruggedness, roughness, "
              "topography, Jenness Enterprises"),
        citation=_ACR_CITATION)
    _mh.phase_advance("Creating layer file and symbology...")
    out_lyrx = _mh.default_stretch_lyrx(
        out_raster, [(95, 135, 193), (249, 254, 204), (224, 70, 53)],
        messages=messages)
    _mh.end_phases()
    _hs._msg(messages, "Arc-Chord Ratio complete: {}".format(out_raster))
    return out_lyrx


# ----------------------------------------------------------------------------
#  Terrain Ruggedness Index (TRI)
# ----------------------------------------------------------------------------
#
# One tool, four selectable measures of how much a cell's neighborhood
# departs from flat -- deliberately offered together because "ruggedness"
# means different things (see the tool help). All share one CIRCULAR moving
# neighborhood, radius in cells or ground units (geodesic per row on
# geographic DEMs, exactly like VRM). The default radius 1.5 cells reproduces
# the classic 3x3 (8-neighbor) definitions (the four diagonals sit sqrt(2)
# ~= 1.41 cells away, so 1.5 includes them; a radius of 1 gives a 4-neighbor
# "plus").
#
#   Riley et al. 1999  : sqrt( sum_i (z_i - z0)^2 )         [DEM elev. units]
#   Wilson et al. 2007 : mean_i |z_i - z0|                  [DEM elev. units]
#   Slope (Jenness)    : sqrt( sum_i ((z_i - z0)/d_i)^2 )   [dimensionless]
#   Std. deviation     : population SD of the window elevs  [DEM elev. units]
#
# where z0 is the focal cell, z_i a neighbor, and d_i the ground distance
# between their centers. The three elevation-unit methods keep the DEM's own
# z-units (meters or feet -- we do NOT force meters, so a feet DEM yields a
# feet output, labeled as such in the metadata). The Slope method scales
# elevations to meters internally so its rise/run is a correct dimensionless
# ratio regardless of the DEM's units. The Slope method is Jeff's own idea
# (lecture slides 62-63): dividing each elevation difference by the distance
# to that neighbor turns it into the actual slope toward that neighbor,
# correcting the standard TRI's habit of counting a difference to a far
# (diagonal) cell the same as a difference to a near (orthogonal) one.
# Dividing by distance IS taking the slope, so slides 62 and 63 are the same
# measure.

TRI_METHODS = [
    "Riley et al. 1999 (root of summed squared differences)",
    "Wilson et al. 2007 (mean absolute difference)",
    "Slope (distance-weighted, Jenness)",
    "Standard deviation of neighborhood elevations",
]

_TRI_LECTURE = ("https://docs.google.com/presentation/d/"
                "179rA7gG0r11feha-xtizP18XytDU3I7H")
_TRI_RILEY_CIT = (
    "Riley, S. J., S. D. DeGloria, and R. Elliot. 1999. A terrain ruggedness "
    "index that quantifies topographic heterogeneity. Intermountain Journal "
    "of Sciences 5(1-4):23-27.")
_TRI_WILSON_CIT = (
    "Wilson, M. F. J., B. O'Connell, C. Brown, J. C. Guinan, and A. J. "
    "Grehan. 2007. Multiscale terrain analysis of multibeam bathymetry data "
    "for habitat mapping on the continental slope. Marine Geodesy "
    "30(1-2):3-35 (https://doi.org/10.1080/01490410701295962).")
_TRI_SLOPE_CIT = (
    "Slope / distance-weighted variant: Jenness, J. Topographic ruggedness "
    "[lecture]. Jenness Enterprises (" + _TRI_LECTURE + ").")
_TRI_STD_CIT = (
    "Neighborhood standard deviation is a common ruggedness measure; see "
    "Jenness, J. Topographic ruggedness [lecture]. Jenness Enterprises (" +
    _TRI_LECTURE + ").")
_TRI_CITATION = {0: _TRI_RILEY_CIT, 1: _TRI_WILSON_CIT,
                 2: _TRI_SLOPE_CIT, 3: _TRI_STD_CIT}


# TRI's neighbor distances come from GroundGeometry.rows (per cell, any
# coordinate system).


_TRI_MAX_R = 500          # bounding cell radius cap (a ~1000-cell-wide window)


def run_tri(in_raster, out_raster, method=None, radius=1.5,
            radius_units="Cells", elev_units="Meters", messages=None):
    """Terrain Ruggedness Index: one of four neighborhood ruggedness measures
    (see ``TRI_METHODS`` and the module comment) over a CIRCULAR neighborhood
    of ``radius`` in ``radius_units`` (Cells / Meters / Kilometers / Feet /
    Miles; geodesic per row on geographic DEMs, like VRM). The Riley, Wilson,
    and standard-deviation outputs are in the DEM's own elevation units (feet
    stays feet); the Slope output is a dimensionless rise/run. Returns the
    output's default-symbology .lyrx path (or None)."""
    gdal = _mh._gdal()

    method = method or TRI_METHODS[0]
    if method not in TRI_METHODS:
        raise arcpy.ExecuteError("Unknown TRI method: {}.".format(method))
    m_idx = TRI_METHODS.index(method)
    is_riley, is_wilson, is_slope, is_std = (m_idx == 0, m_idx == 1,
                                             m_idx == 2, m_idx == 3)
    radius = float(radius) if radius else 0.0
    if radius <= 0:
        raise arcpy.ExecuteError("The neighborhood radius must be positive.")
    if radius_units not in RADIUS_UNITS:
        raise arcpy.ExecuteError(
            "Unknown radius unit: {}.".format(radius_units))
    out_raster = _mh.resolve_raster_output(out_raster, messages)

    _mh.begin_phases([
        ("Reading the DEM...", 1),
        ("Calculating terrain ruggedness...", 6),
        ("Saving the TRI raster...", 1),
        ("Building pyramids...", 1),
        ("Creating layer file and symbology...", 1),
    ])

    scratch = arcpy.env.scratchFolder
    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    in_nodata = band.GetNoDataValue()
    sr = arcpy.Describe(in_raster).spatialReference
    geographic = (sr.type == "Geographic")

    elev_mpu = 1.0 if geographic else _hs._ELEV_METERS.get(elev_units, 1.0)
    # Per-cell ground dimensions in meters (any coordinate system) -- for the
    # circular ground-distance neighborhood and the Slope method's neighbor
    # distances.
    ground = _hs.ground_geometry(gt, width, height, sr, proj, messages)
    _g2 = ground.rows(0, height)
    cw2 = np.asarray(_g2["h"], dtype=np.float64)
    ch2 = np.asarray((_g2["vA"] + _g2["vB"]) / 2.0, dtype=np.float64)
    del _g2
    cw2sq, ch2sq = cw2 * cw2, ch2 * ch2

    # Neighborhood geometry. "Cells" -> a fixed cell-space circle (same
    # everywhere, di^2 + dj^2 <= radius^2). A linear unit -> a true ground
    # circle of radius_m meters, tested per row on the spheroid so it stays
    # the right size at any latitude (matching VRM).
    cells_mode = (radius_units == "Cells")
    if cells_mode:
        radius_m = None
        big_r = int(math.ceil(radius))
    else:
        radius_m = radius * _RADIUS_METERS[radius_units]
        min_cell = max(min(float(cw2.min()), float(ch2.min())), 1e-9)
        big_r = int(math.ceil(radius_m / min_cell))
    big_r = max(big_r, 1)
    if big_r > _TRI_MAX_R:
        raise arcpy.ExecuteError(
            "The neighborhood radius works out to about {:,} cells across, "
            "which is too large for this tool. Use a smaller radius or a "
            "coarser DEM.".format(2 * big_r + 1))
    if cells_mode:
        _hs._msg(messages, "TRI method: {}. Circular neighborhood radius "
                           "{:g} cells. {}".format(
                               method, radius,
                               "Elevation assumed meters (geographic DEM)."
                               if geographic else
                               "Elevation units: {}.".format(elev_units)))
    else:
        _hs._msg(messages, "TRI method: {}. Circular neighborhood radius "
                           "{:g} {} = {:,.1f} m ({}up to {} cells across). "
                           "{}".format(
                               method, radius, radius_units.lower(), radius_m,
                               "" if ground.mode == "planar" else
                               "true ground distances, ",
                               2 * big_r + 1,
                               "Elevation assumed meters (geographic DEM)."
                               if geographic else
                               "Elevation units: {}.".format(elev_units)))

    z = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(z)
    if in_nodata is not None:
        valid &= (z != in_nodata)
    valid = _mh.mask_band_valid(band, valid)
    # Elevation-unit methods keep the DEM's native z-units; the Slope method
    # needs meters so its rise/run is dimensionless.
    zwork = np.where(valid, z * elev_mpu if is_slope else z, np.nan)
    src = None
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass

    _mh.phase_advance("Calculating terrain ruggedness...")
    R = big_r
    zp = np.full((height + 2 * R, width + 2 * R), np.nan)
    zp[R:R + height, R:R + width] = zwork
    vp = np.zeros((height + 2 * R, width + 2 * R), dtype=bool)
    vp[R:R + height, R:R + width] = valid
    need_d = is_slope or not cells_mode

    with np.errstate(invalid="ignore", divide="ignore"):
        if is_std:
            s1 = np.zeros((height, width))
            s2 = np.zeros((height, width))
            cnt = np.zeros((height, width))
        else:
            acc = np.zeros((height, width))
            ncnt = np.zeros((height, width))   # valid NEIGHBORS (excl. focal)

        for di in range(-R, R + 1):
            for dj in range(-R, R + 1):
                if not is_std and di == 0 and dj == 0:
                    continue
                d2 = ((dj * dj) * cw2sq + (di * di) * ch2sq
                      if need_d else None)         # squared meters, per cell
                if cells_mode:
                    if di * di + dj * dj > radius * radius:
                        continue
                    cell_mask = None               # every cell in the circle
                else:
                    cell_mask = d2 <= radius_m * radius_m
                    if not cell_mask.any():
                        continue
                nz = zp[R + di:R + di + height, R + dj:R + dj + width]
                nv = vp[R + di:R + di + height, R + dj:R + dj + width]
                member = nv if cell_mask is None else (nv & cell_mask)
                if is_std:
                    s1 += np.where(member, nz, 0.0)
                    s2 += np.where(member, nz * nz, 0.0)
                    cnt += member
                else:
                    pair = member & valid
                    dz = np.where(pair, nz - zwork, 0.0)
                    if is_wilson:
                        acc += np.abs(dz)
                    elif is_slope:
                        acc += np.where(pair, dz * dz / d2, 0.0)
                    else:                          # Riley
                        acc += dz * dz
                    ncnt += pair

        if is_std:
            mean = s1 / cnt
            result = np.sqrt(np.maximum(s2 / cnt - mean * mean, 0.0))
            out_valid = valid & (cnt >= 2)     # focal + >=1 more
        else:
            result = acc / ncnt if is_wilson else np.sqrt(acc)
            out_valid = valid & (ncnt >= 1)

    n_out = int(out_valid.sum())
    _hs._msg(messages, "{:,} of {:,} cells have a defined value ({}).".format(
        n_out, width * height, _TRI_CITATION[m_idx]))

    out_path = arcpy.CreateUniqueName("tri.tif", scratch)
    ds, oband = _make_output(gdal, out_path, gt, proj, width, height)
    oband.WriteArray(np.where(out_valid, result, NODATA_OUT).astype(np.float32))
    oband.FlushCache()
    ds.FlushCache()
    stats = _mh._RasterStats()
    if out_valid.any():
        stats.add(result[out_valid].astype(np.float64))
    out_stats = _mh._finalize_stats(oband, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(oband, out_stats)
    oband.FlushCache()
    ds.FlushCache()
    oband = None
    ds = None

    elev_label = "Meters (geographic DEM)" if geographic else elev_units
    out_units = ("a dimensionless rise/run (slope)" if is_slope else
                 "the DEM's elevation units ({})".format(elev_label))
    radius_desc = ("{:g} cells".format(radius) if cells_mode else
                   "{:g} {}".format(radius, radius_units.lower()))
    _mh.phase_advance("Saving the TRI raster...")
    _deliver_output(
        out_path, out_raster, out_stats, messages,
        title=os.path.basename(str(out_raster)),
        summary=("Created by Terrain Ruggedness Index from {src}: method = "
                 "{m}; circular neighborhood radius {rd}; elevation units "
                 "{e}; output in {u}.").format(
                     src=str(in_raster), m=method, rd=radius_desc,
                     e=elev_label, u=out_units),
        description=(
            "Terrain Ruggedness Index (TRI) raster measuring how much each "
            "cell's neighborhood departs from flat, by the {m} method over a "
            "circular neighborhood of radius {rd}. Higher values are more "
            "rugged. Output is in {u}. NoData cells and the raster edge are "
            "handled by ignoring missing neighbors (a value is produced "
            "wherever the focal cell and at least one neighbor are valid). "
            "{c}").format(m=method, rd=radius_desc, u=out_units,
                          c=_TRI_CITATION[m_idx]),
        tags=("terrain ruggedness index, TRI, ruggedness, roughness, "
              "topographic heterogeneity, topography, Jenness Enterprises"),
        citation=_TRI_CITATION[m_idx])
    _mh.phase_advance("Creating layer file and symbology...")
    out_lyrx = _mh.default_stretch_lyrx(
        out_raster, [(95, 135, 193), (249, 254, 204), (224, 70, 53)],
        messages=messages)
    _mh.end_phases()
    _hs._msg(messages, "Terrain Ruggedness Index complete: {}".format(out_raster))
    return out_lyrx


# ----------------------------------------------------------------------------
#  Fractal Dimension (surface roughness, D in [2, 3])
# ----------------------------------------------------------------------------
#
# Per-cell LOCAL fractal dimension of the terrain surface over a moving square
# window: D = 2 for a smooth surface (a flat OR a smoothly-tilted plane), up
# to 3 for space-filling / infinitely rough. Like VRM (and unlike TRI), FD
# ignores overall slope; it measures how roughness CHANGES ACROSS SCALES
# (self-affine complexity), a different axis than the single-scale ruggedness
# tools. All methods estimate the SAME quantity D but by different models, so
# they agree on ideal fractal surfaces and differ somewhat on real terrain --
# Ju & Lam (2009): "the problem of inconsistent results derived from different
# fractal calculation algorithms remains." Four selectable methods:
#
#   Triangular prism  : how 3-D surface area shrinks toward the flat area as
#                       the measuring square grows (Clarke 1986; local moving-
#                       window / divisor-step sampling per Ju & Lam 2009).
#                       D = 2 - slope of log(area) vs log(step size).
#   Variogram (p=2) /  : how elevation increments grow with lag h,
#   Madogram  (p=1)      V_p(h) = mean |dz|^p ~ h^(p*H); D = 3 - H,
#                       H = slope/p (Gneiting, Sevcikova & Percival 2012; their
#                       R package fractaldim -- a reimplementation of THEIR
#                       method). Madogram (p=1) is their recommended, more
#                       efficient estimator. Amplitude-invariant (needs no
#                       vertical-scale handling).
#   Diff. box counting : box-counting -- N(s) boxes to cover the surface at
#                       box size s; D = -slope of log N vs log s (Sarkar &
#                       Chaudhuri 1994; ceil-from-tile-minimum count =
#                       modification B of Li/Du/Sun 2009; the box is a true
#                       ground cube, NOT their reduced height, and tiles do
#                       not overlap). The WEAKEST/noisiest local estimator
#                       (few box sizes fit a window, coarse integer counts);
#                       its raw slope can imply D just under 2 -- clipped.
#
# TPM and DBC use true 1:1 ground units (elevation scaled to the horizontal
# ground unit; NO vertical exaggeration, NO per-window normalization); the
# variogram/madogram are amplitude-invariant. All output is clipped to [2, 3]
# (a surface's D is geometrically in that range; values outside are estimation
# artifacts). A window needs its full extent of valid data (NoData anywhere in
# the window -> NoData output).

FD_METHODS = [
    "Triangular prism (Clarke 1986; Ju & Lam 2009)",
    "Variogram (Gneiting, Sevcikova & Percival 2012)",
    "Madogram (Gneiting, Sevcikova & Percival 2012)",
    "Differential box counting (Sarkar & Chaudhuri 1994)",
]

_FD_TPM_CIT = (
    "Clarke, K. C. 1986. Computation of the fractal dimension of topographic "
    "surfaces using the triangular prism surface area method. Computers & "
    "Geosciences 12(5):713-722. Local moving-window / divisor-step method: "
    "Ju, W., and N. S.-N. Lam. 2009. An improved algorithm for computing "
    "local fractal dimension using the triangular prism method. Computers & "
    "Geosciences 35(6):1224-1233 "
    "(https://doi.org/10.1016/j.cageo.2008.09.008).")
_FD_VARIO_CIT = (
    "Gneiting, T., H. Sevcikova, and D. B. Percival. 2012. Estimators of "
    "fractal dimension: assessing the roughness of time series and spatial "
    "data. Statistical Science 27(2):247-277 "
    "(https://doi.org/10.1214/11-STS370); implemented in their R package "
    "fractaldim (https://CRAN.R-project.org/package=fractaldim). This tool "
    "reimplements their published variation estimator.")
_FD_DBC_CIT = (
    "Sarkar, N., and B. B. Chaudhuri. 1994. An efficient differential "
    "box-counting approach to compute fractal dimension of image. IEEE "
    "Transactions on Systems, Man, and Cybernetics 24(1):115-120 "
    "(https://doi.org/10.1109/21.259692). Box-counting rule: Li, "
    "J., Q. Du, and C. Sun. 2009. An improved box-counting method for image "
    "fractal dimension estimation. Pattern Recognition 42(11):2460-2469 "
    "(https://doi.org/10.1016/j.patcog.2009.03.001).")
_FD_CITATION = {0: _FD_TPM_CIT, 1: _FD_VARIO_CIT, 2: _FD_VARIO_CIT,
                3: _FD_DBC_CIT}


def _fd_cell_meters(gt, sr, height):
    """A single representative horizontal cell size in meters. Projected:
    exact (mean of the x/y cell dimensions). Geographic: the ground cell size
    at the DEM's centre latitude -- a documented approximation, since FD is a
    scale analysis and the small variation of cell size across a moving window
    is immaterial. Returns (cell_m, geographic)."""
    if sr.type == "Geographic":
        a = float(sr.semiMajorAxis or 6378137.0)
        b = float(sr.semiMinorAxis or 6356752.31424518)
        lon0 = gt[0] + 0.5 * gt[1]
        lat0 = gt[3] + (height // 2 + 0.5) * gt[5]
        pc = np.array(_hs._ecef(np.array([lon0]), np.array([lat0]), a, b)).ravel()
        pe = np.array(_hs._ecef(np.array([lon0 + gt[1]]), np.array([lat0]), a, b)).ravel()
        ps = np.array(_hs._ecef(np.array([lon0]), np.array([lat0 - abs(gt[5])]), a, b)).ravel()
        cw = float(np.sqrt(((pe - pc) ** 2).sum()))
        ch = float(np.sqrt(((ps - pc) ** 2).sum()))
        return 0.5 * (cw + ch), True
    mpu = float(sr.metersPerUnit or 1.0)
    return 0.5 * (abs(gt[1]) + abs(gt[5])) * mpu, False


def _fd_divisor_steps(wsize):
    """Ju & Lam (2009) divisor steps: step sizes S dividing (wsize-1) so each
    fully tiles the window, with at least a 2x2 tiling."""
    m = wsize - 1
    return [S for S in range(1, m // 2 + 1) if m % S == 0]


def _fd_progress(total):
    """A work-weighted sub-progress reporter for the current phase. `total` is
    the total number of work units; the returned ``step(units)`` advances the
    bar. Work is very uneven across step sizes (a step size of 1 tiles the
    window far more finely than 12), so progress is weighted by actual tile
    count rather than by step index."""
    state = {"done": 0.0, "total": float(max(total, 1))}

    def step(units):
        state["done"] += units
        _mh.phase_step(state["done"], state["total"])
    return step


def _fd_comb_sum(grid, stride, n, wsize, H, W, prog=None):
    """Sum `grid` (keyed by tile top-left) over the n x n tiles at `stride`
    that fill each focal cell's window. Returns the (nrf, ncf) valid-focal
    block. Shared by the triangular-prism and box-counting methods. Reports
    `n` work units per outer row to `prog` (so the whole call is n^2 units)."""
    nrf, ncf = H - wsize + 1, W - wsize + 1
    out = np.zeros((nrf, ncf))
    for k in range(n):
        ks = k * stride
        for l in range(n):
            ls = l * stride
            out += grid[ks:ks + nrf, ls:ls + ncf]
        if prog is not None:
            prog(n)
    return out


def _fd_box_sum(a, bh, bw):
    """Sum of every bh x bw block of `a`, keyed by top-left (summed-area
    table). Used by the variation estimator."""
    C = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    C[1:, 1:] = np.cumsum(np.cumsum(a, axis=0), axis=1)
    H2, W2 = a.shape[0] - bh + 1, a.shape[1] - bw + 1
    return (C[bh:bh + H2, bw:bw + W2] - C[0:H2, bw:bw + W2]
            - C[bh:bh + H2, 0:W2] + C[0:H2, 0:W2])


def _fd_block_range(z, s):
    """Max - min over every s x s block of `z`, keyed by top-left (O(s)
    sliding reductions, not a Python per-window loop). shape (H-s+1, W-s+1)."""
    H, W = z.shape
    mr = z[:, :W - s + 1].copy()
    nr = mr.copy()
    for k in range(1, s):
        col = z[:, k:W - s + 1 + k]
        mr = np.maximum(mr, col)
        nr = np.minimum(nr, col)
    mx = mr[:H - s + 1, :].copy()
    mn = nr[:H - s + 1, :].copy()
    for k in range(1, s):
        mx = np.maximum(mx, mr[k:H - s + 1 + k, :])
        mn = np.minimum(mn, nr[k:H - s + 1 + k, :])
    return mx - mn


def _fd_heron(a, b, c):
    s = 0.5 * (a + b + c)
    return np.sqrt(np.maximum(s * (s - a) * (s - b) * (s - c), 0.0))


def _fd_prism_area(z, S, cell):
    """Total 4-triangle prism top area of every S x S square (side S*cell,
    center = mean of the 4 corners), keyed by top-left. Clarke (1986)."""
    L = S * cell
    zA, zB = z[:-S, :-S], z[:-S, S:]
    zC, zD = z[S:, S:], z[S:, :-S]
    zE = 0.25 * (zA + zB + zC + zD)
    e2, sp2 = L * L, 0.5 * L * L

    def tri(zP, zQ):
        pq = np.sqrt(e2 + (zP - zQ) ** 2)
        pe = np.sqrt(sp2 + (zP - zE) ** 2)
        qe = np.sqrt(sp2 + (zQ - zE) ** 2)
        return _fd_heron(pq, pe, qe)

    return tri(zA, zB) + tri(zB, zC) + tri(zC, zD) + tri(zD, zA)


def _fd_tpm(z, wsize, cell, H, W):
    steps = _fd_divisor_steps(wsize)
    logS = np.log(np.asarray(steps, float))
    prog = _fd_progress(sum(((wsize - 1) // S) ** 2 for S in steps))
    logA = None
    for i, S in enumerate(steps):
        A = _fd_comb_sum(_fd_prism_area(z, S, cell), S, (wsize - 1) // S,
                         wsize, H, W, prog)
        if logA is None:
            logA = np.empty((len(steps),) + A.shape)
        logA[i] = np.log(A)
    dx = logS - logS.mean()
    slope = (dx[:, None, None] * (logA - logA.mean(axis=0))).sum(axis=0) / \
        float((dx * dx).sum())
    return 2.0 - slope


def _fd_default_max_lag(wsize):
    """Default longest lag for the variogram / madogram: one third of the
    window, so that every lag still has plenty of cell pairs inside it."""
    return max(2, wsize // 3)


def _fd_variation(z, wsize, p, H, W, hmax=None):
    hmax = int(hmax) if hmax else _fd_default_max_lag(wsize)
    lags = list(range(1, hmax + 1))
    logh = np.log(np.asarray(lags, float))
    nrf, ncf = H - wsize + 1, W - wsize + 1
    logV = np.empty((len(lags), nrf, ncf))
    maxvar = np.zeros((nrf, ncf))
    prog = _fd_progress(len(lags))
    for i, h in enumerate(lags):
        dh = np.abs(z[:, h:] - z[:, :-h]) ** p
        dv = np.abs(z[h:, :] - z[:-h, :]) ** p
        Vp = (_fd_box_sum(dh, wsize, wsize - h)
              + _fd_box_sum(dv, wsize - h, wsize)) / (2.0 * wsize * (wsize - h))
        maxvar = np.maximum(maxvar, Vp)
        logV[i] = np.log(np.maximum(Vp, 1e-300))
        prog(1)
    dx = logh - logh.mean()
    slope = (dx[:, None, None] * (logV - logV.mean(axis=0))).sum(axis=0) / \
        float((dx * dx).sum())
    D = 3.0 - slope / p
    D[maxvar <= 1e-12] = 2.0                 # flat window -> D = 2
    return D


def _fd_dbc(z, wsize, cell, H, W):
    box_sizes = _fd_dbc_box_sizes(wsize)
    logS = np.log(np.asarray(box_sizes, float))
    prog = _fd_progress(sum((wsize // s) ** 2 for s in box_sizes))
    logN = None
    for i, s in enumerate(box_sizes):
        rng = _fd_block_range(z, s)
        nr = np.where(rng > 1e-12, np.ceil(rng / (s * cell)), 1.0)
        N = _fd_comb_sum(nr, s, wsize // s, wsize, H, W, prog)
        if logN is None:
            logN = np.empty((len(box_sizes),) + N.shape)
        logN[i] = np.log(N)
    dx = logS - logS.mean()
    slope = (dx[:, None, None] * (logN - logN.mean(axis=0))).sum(axis=0) / \
        float((dx * dx).sum())
    return -slope


def _fd_dbc_box_sizes(wsize):
    return [s for s in (2, 3, 4, 6, 8, 12, 16, 24)
            if s <= wsize // 2 and wsize // s >= 2]


def _fd_report_steps(messages, m_idx, wsize, hmax=None):
    """Tell the user which step sizes / lags / box sizes the chosen method
    derives from the window size, and why (the user never enters them)."""
    def cells(v):
        return ", ".join(str(x) for x in v)
    if m_idx == 0:
        steps = _fd_divisor_steps(wsize)
        _hs._msg(messages,
                 "Triangular prism step sizes used: {} cells ({} steps). A "
                 "window of {} cells spans {} cell intervals, and each step "
                 "must divide {} evenly, at least twice, so that the prisms "
                 "tile the window exactly.".format(
                     cells(steps), len(steps), wsize, wsize - 1, wsize - 1))
        if len(steps) == 3:
            _warn(messages,
                  "Only three step sizes divide {} evenly, the fewest any "
                  "window size gives, so each cell's dimension is fitted to "
                  "three points and will be noisier. Window sizes of 13, 17, "
                  "25, 33, 37, 49, or 61 give four or more.".format(wsize - 1))
    elif m_idx in (1, 2):
        dflt = _fd_default_max_lag(wsize)
        if hmax and int(hmax) != dflt:
            _hs._msg(messages,
                     "Lag distances used: 1 through {} cells ({} lags), the "
                     "Maximum lag you entered. (Left blank, the tool would "
                     "use 1 through {}, one third of the {}-cell "
                     "window.)".format(int(hmax), int(hmax), dflt, wsize))
        else:
            _hs._msg(messages,
                     "Lag distances used: 1 through {} cells ({} lags), up to "
                     "one third of the {}-cell window so that every lag has "
                     "plenty of cell pairs inside the window. Enter a Maximum "
                     "lag to choose a different range.".format(dflt, dflt, wsize))
    else:
        sizes = _fd_dbc_box_sizes(wsize)
        _hs._msg(messages,
                 "Box sizes used: {} cells ({} sizes), the sizes from the "
                 "series 2, 3, 4, 6, 8, 12, 16, 24 that fit at least twice "
                 "across the {}-cell window.".format(
                     cells(sizes), len(sizes), wsize))


def run_fractal_dimension(in_raster, out_raster, method=None, window_size=25,
                          elev_units="Meters", messages=None, max_lag=None):
    """Per-cell local fractal dimension (surface roughness, D in [2, 3]) by a
    selectable method over a moving square window. See ``FD_METHODS`` and the
    module comment. Returns the output's default-symbology .lyrx path."""
    gdal = _mh._gdal()
    method = method or FD_METHODS[0]
    if method not in FD_METHODS:
        raise arcpy.ExecuteError("Unknown fractal-dimension method: {}.".format(method))
    m_idx = FD_METHODS.index(method)
    is_tpm, is_vario, is_mado, is_dbc = (m_idx == 0, m_idx == 1,
                                         m_idx == 2, m_idx == 3)
    try:
        wsize = int(window_size)
    except (TypeError, ValueError):
        raise arcpy.ExecuteError("The window size must be a whole number of cells.")
    if wsize < 9 or wsize % 2 == 0:
        raise arcpy.ExecuteError(
            "The window size must be an odd number of cells, at least 9 "
            "(e.g. 13, 17, 25, 33, 37, 49, or 61).")
    hmax = None
    if (is_vario or is_mado) and max_lag not in (None, ""):
        try:
            hmax = int(max_lag)
        except (TypeError, ValueError):
            raise arcpy.ExecuteError("The maximum lag must be a whole number of cells.")
        if hmax < 2 or hmax > wsize // 2:
            raise arcpy.ExecuteError(
                "The maximum lag must be between 2 and {} cells (half of the "
                "{}-cell window).".format(wsize // 2, wsize))
    # Every odd window >= 9 yields at least three prism steps (1, 2 and
    # (wsize-1)/2 always divide wsize-1), so no window size is refused; the
    # steps actually used are reported below, with a warning at the minimum.
    out_raster = _mh.resolve_raster_output(out_raster, messages)

    _mh.begin_phases([
        ("Reading the DEM...", 1),
        ("Calculating fractal dimension...", 6),
        ("Saving the fractal-dimension raster...", 1),
        ("Building pyramids...", 1),
        ("Creating layer file and symbology...", 1),
    ])

    scratch = arcpy.env.scratchFolder
    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < wsize or height < wsize:
        raise arcpy.ExecuteError(
            "The DEM must be at least {0} x {0} cells for a window of {0}.".format(wsize))
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    in_nodata = band.GetNoDataValue()
    sr = arcpy.Describe(in_raster).spatialReference

    cell_m, geographic = _fd_cell_meters(gt, sr, height)
    elev_mpu = 1.0 if geographic else _hs._ELEV_METERS.get(elev_units, 1.0)
    _hs._msg(messages, "FD method: {}. Window {} x {} cells. {} Cell size "
                       "{:.3f} m; elevation {}.".format(
                           method, wsize, wsize,
                           "Geographic DEM: representative cell size at the "
                           "centre latitude." if geographic else "Projected DEM.",
                           cell_m,
                           "assumed meters" if geographic else elev_units))
    _fd_report_steps(messages, m_idx, wsize, hmax)

    z = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(z)
    if in_nodata is not None:
        valid &= (z != in_nodata)
    valid = _mh.mask_band_valid(band, valid)
    # Fill NoData with the valid mean so the vectorized math never sees NaN;
    # any window touching NoData is masked out of the result below, so the
    # fill value never reaches the output.
    fill = float(z[valid].mean()) if valid.any() else 0.0
    z = np.where(valid, z, fill) * elev_mpu
    src = None
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass

    _mh.phase_advance("Calculating fractal dimension...")
    if is_tpm:
        Dblk = _fd_tpm(z, wsize, cell_m, height, width)
    elif is_vario:
        Dblk = _fd_variation(z, wsize, 2.0, height, width, hmax)
    elif is_mado:
        Dblk = _fd_variation(z, wsize, 1.0, height, width, hmax)
    else:
        Dblk = _fd_dbc(z, wsize, cell_m, height, width)
    np.clip(Dblk, 2.0, 3.0, out=Dblk)        # a surface's D is in [2, 3]

    # A window is usable only if it has no NoData cell anywhere inside it.
    hw = (wsize - 1) // 2
    inv_win = _fd_box_sum((~valid).astype(np.float64), wsize, wsize)
    ok = inv_win < 0.5
    out = np.full((height, width), NODATA_OUT, dtype=np.float32)
    center = out[hw:hw + Dblk.shape[0], hw:hw + Dblk.shape[1]]
    center[ok] = Dblk[ok].astype(np.float32)
    out_valid = np.zeros((height, width), dtype=bool)
    out_valid[hw:hw + Dblk.shape[0], hw:hw + Dblk.shape[1]] = ok
    n_out = int(out_valid.sum())
    _hs._msg(messages, "{:,} of {:,} cells have a fully-valid window ({}).".format(
        n_out, width * height, _FD_CITATION[m_idx]))

    out_path = arcpy.CreateUniqueName("fd.tif", scratch)
    ds, oband = _make_output(gdal, out_path, gt, proj, width, height)
    oband.WriteArray(out)
    oband.FlushCache()
    ds.FlushCache()
    stats = _mh._RasterStats()
    if out_valid.any():
        stats.add(out[out_valid].astype(np.float64))
    out_stats = _mh._finalize_stats(oband, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(oband, out_stats)
    oband.FlushCache()
    ds.FlushCache()
    oband = None
    ds = None

    _mh.phase_advance("Saving the fractal-dimension raster...")
    _deliver_output(
        out_path, out_raster, out_stats, messages,
        title=os.path.basename(str(out_raster)),
        summary=("Created by Fractal Dimension from {src}: method = {m}; "
                 "window {w} x {w} cells{lg}; elevation units {e}.").format(
                     src=str(in_raster), m=method, w=wsize,
                     lg=("; lags 1 through {} cells".format(
                         hmax or _fd_default_max_lag(wsize))
                         if (is_vario or is_mado) else ""),
                     e=("Meters (geographic DEM)" if geographic else elev_units)),
        description=(
            "Local fractal dimension (surface roughness) raster: for each "
            "cell, the fractal dimension D of the terrain within a {w} x {w} "
            "cell window, by the {m} method. D ranges from 2 (a smooth "
            "surface -- flat OR a smoothly tilted plane) to 3 (space-filling, "
            "maximally rough); like the Vector Ruggedness Measure, it is "
            "independent of overall slope and instead measures how roughness "
            "varies across scales. Values are clipped to [2, 3]. All methods "
            "estimate the same D by different models and can give somewhat "
            "different values on real terrain. {c}").format(
                w=wsize, m=method, c=_FD_CITATION[m_idx]),
        tags=("fractal dimension, surface roughness, terrain ruggedness, "
              "self-affine, topographic complexity, topography, "
              "Jenness Enterprises"),
        citation=_FD_CITATION[m_idx])
    _mh.phase_advance("Creating layer file and symbology...")
    out_lyrx = _mh.default_stretch_lyrx(
        out_raster, [(95, 135, 193), (249, 254, 204), (224, 70, 53)],
        messages=messages)
    _mh.end_phases()
    _hs._msg(messages, "Fractal Dimension complete: {}".format(out_raster))
    return out_lyrx


# ----------------------------------------------------------------------------
#  Topographic Wetness Index (TWI / CTI)
# ----------------------------------------------------------------------------
#
# TWI = ln( SCA / (tan(beta) + eps) ), where SCA is the specific catchment
# area (upslope contributing area per unit contour length) and tan(beta) is
# the local slope gradient (Beven & Kirkby 1979).  On a raster every cell is
# treated as its own watershed outlet, so the "unit contour length" is the
# cell size and one cell size cancels:
#
#     catchment area (m^2) = (flow accumulation + 1) x cellsize^2
#     SCA = catchment area / unit contour length = (flowacc + 1) x cellsize
#
# The +1 counts the cell's own catchment (a cell drains at least itself) and
# also keeps SCA > 0 so the log is defined; eps (default 0.001, the HYDRO1k
# value; user set) is
# added to the slope gradient so flat cells do not divide by zero.  High TWI =
# large upslope area and/or gentle slope (valley bottoms, converging, wet);
# low TWI = small area and/or steep (ridges, diverging, dry).
#
# Everything is computed internally with numpy/scipy -- no Spatial Analyst,
# no GDAL hydrology (GDAL has none) -- so the tool ships to any machine with
# only the ArcGIS Pro Python install:
#   * Fill sinks   : priority-flood (Barnes, Lehman & Mulla 2014)
#   * Flow direction (D8): steepest descent on the FILLED DEM (O'Callaghan &
#     Mark 1984); flats route toward their flood-order predecessor (the spill)
#   * Flow accumulation  : topological accumulation over the D8 receiver graph
#   * Slope (tan beta)   : Horn gradient of the ORIGINAL (unfilled) DEM
#
# The fill is bit-identical to Esri's Fill and the flow direction / flow
# accumulation match Esri D8 to ~99.9% (the rest is flat tie-breaking); for
# exact Esri parity a user can feed in their own flow-accumulation raster.
# Slope comes from the original DEM (filling is only to keep flow from getting
# stuck), from true ground distances on the spheroid in ANY coordinate system
# (hillshade.GroundGeometry: per row on geographic DEMs, per cell from a
# lattice of unprojected cell centers on projected ones).

# Flow routing on the filled surface. D8 sends each cell's water to its single
# steepest lower neighbor; D-Infinity (Tarboton 1997) to the two neighbors
# that bracket the steepest facet direction, in proportion to the angle; MFD
# (Quinn et al. 1991, with the adaptive partition exponent of Qin et al. 2007
# that Esri's MFD option implements) to every lower neighbor, weighted by
# slope^p and the contour length toward that neighbor. The specific catchment
# area is (accumulation + 1) x cell size for all three: the divisor is the
# width of the cell whose value is being computed (Tarboton 1997, p. 3:
# "a = A/L, taking L as the pixel width"), not the number of receivers.
FLOW_METHODS = ["D8 (single flow direction)",
                "MFD (multiple flow direction; Qin et al. 2007)",
                "DINF (D-Infinity; Tarboton 1997)"]


def _flow_key(method):
    m = str(method or "").strip().upper()
    if m.startswith("MFD"):
        return "MFD"
    if m.startswith("DINF") or m.startswith("D-INF") or m.startswith("D_INF"):
        return "DINF"
    return "D8"

_TWI_CIT_TWI = (
    "Beven, K. J., and M. J. Kirkby. 1979. A physically based, variable "
    "contributing area model of basin hydrology. Hydrological Sciences "
    "Bulletin 24(1):43-69. https://doi.org/10.1080/02626667909491834")
_TWI_CIT_FILL = (
    "Barnes, R., C. Lehman, and D. Mulla. 2014. Priority-flood: an optimal "
    "depression-filling and watershed-labeling algorithm for digital "
    "elevation models. Computers & Geosciences 62:117-127. "
    "https://doi.org/10.1016/j.cageo.2013.04.024")
_TWI_CIT_DINF = (
    "Tarboton, D. G. 1997. A new method for the determination of flow "
    "directions and upslope areas in grid digital elevation models. Water "
    "Resources Research 33(2):309-319. https://doi.org/10.1029/96WR03137")
_TWI_CIT_MFD = (
    "Quinn, P., K. Beven, P. Chevallier, and O. Planchon. 1991. The prediction "
    "of hillslope flow paths for distributed hydrological modelling using "
    "digital terrain models. Hydrological Processes 5(1):59-79. "
    "https://doi.org/10.1002/hyp.3360050106; Qin, C., A.-X. Zhu, T. Pei, "
    "B. Li, C. Zhou, and L. Yang. 2007. An adaptive approach to selecting a "
    "flow-partition exponent for a multiple-flow-direction algorithm. "
    "International Journal of Geographical Information Science 21(4):443-458. "
    "https://doi.org/10.1080/13658810601073240")
_TWI_CIT_D8 = (
    "O'Callaghan, J. F., and D. M. Mark. 1984. The extraction of drainage "
    "networks from digital elevation data. Computer Vision, Graphics, and "
    "Image Processing 28(3):323-344. "
    "https://doi.org/10.1016/S0734-189X(84)80011-0")
_TWI_CITATION = _TWI_CIT_TWI


# (The former single-column projected-DEM helper is superseded by
# hillshade.GroundGeometry, which handles every projection per cell.)


def _twi_tanbeta(z, valid, ground):
    """tan(beta) = Horn gradient magnitude of ``z`` (meters, z-scaled) over
    the per-cell ground spans of ``ground`` (a hillshade.GroundGeometry; the
    grid is processed in row strips). NoData neighbors fall back to the
    focal value so the gradient is defined for every valid cell."""
    H, W = z.shape
    zp = np.full((H + 2, W + 2), np.nan)
    zp[1:-1, 1:-1] = np.where(valid, z, np.nan)
    ctr = np.where(valid, z, np.nan)
    tanb = np.full((H, W), np.nan)
    for r0 in range(0, H, _hs.GEOM_STRIP):
        r1 = min(H, r0 + _hs.GEOM_STRIP)
        g = ground.rows(r0, r1)
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


def _twi_priority_flood(z, valid, messages=None):
    """Barnes et al. 2014 priority-flood. Returns (filled, order): the
    depression-filled surface (same units as ``z``) and each cell's pop-order
    (int64, -1 where invalid), used to route flow across flats."""
    import heapq
    from scipy.ndimage import binary_dilation
    H, W = z.shape
    Wp = W + 2
    zpad = np.full((H + 2, Wp), np.inf, dtype=np.float64)
    zpad[1:-1, 1:-1] = np.where(valid, z, np.inf)
    vpad = np.zeros((H + 2, Wp), dtype=bool)
    vpad[1:-1, 1:-1] = valid
    closed2d = ~vpad
    seed2d = binary_dilation(closed2d, structure=np.ones((3, 3), bool)) & vpad

    filledL = zpad.ravel().tolist()
    closedL = closed2d.ravel().tolist()
    orderL = [-1] * ((H + 2) * Wp)
    deltas = (-Wp - 1, -Wp, -Wp + 1, -1, 1, Wp - 1, Wp, Wp + 1)
    heap = []
    push = heapq.heappush
    pop = heapq.heappop
    cnt = 0
    for i in np.flatnonzero(seed2d.ravel()).tolist():
        push(heap, (filledL[i], cnt, i))
        cnt += 1
        closedL[i] = True

    total = int(valid.sum()) or 1
    step = max(1, total // 40)
    o = 0
    while heap:
        e, _, idx = pop(heap)
        orderL[idx] = o
        o += 1
        if messages is not None and o % step == 0:
            _mh.phase_step(o, total)
        for d in deltas:
            n = idx + d
            if not closedL[n]:
                closedL[n] = True
                if filledL[n] < e:
                    filledL[n] = e
                push(heap, (filledL[n], cnt, n))
                cnt += 1
    filled = np.array(filledL, dtype=np.float64).reshape(H + 2, Wp)[1:-1, 1:-1]
    order = np.array(orderL, dtype=np.int64).reshape(H + 2, Wp)[1:-1, 1:-1]
    return filled, order


def _twi_flat_distance(fp, valid, has):
    """Flat handling shared by every router. A flat cell (valid, no strictly
    lower neighbor) drains across cells of equal filled elevation to the
    nearest EXIT: a non-flat cell of the same elevation (the spill), or the
    raster edge / NoData, which flat cells touching it drain into directly
    (as Esri's Flow Direction does). Returns (dist, terminal, order):
    ``dist`` = steps to the nearest exit (0 for non-flat cells and edge
    terminals, inf where unreachable), ``terminal`` = flat cells that drain
    off the map, ``order`` = the accumulation processing rank (higher first:
    elevation descending, then distance descending, so every cell is summed
    before the cell it drains to)."""
    from collections import deque
    from scipy import ndimage
    H, W = valid.shape
    fc = fp[1:-1, 1:-1]
    flat = valid & (~has)
    interior = ndimage.binary_erosion(valid, structure=np.ones((3, 3), bool),
                                      border_value=0)
    terminal = flat & ~interior
    dist = np.full((H + 2, W + 2), np.inf)
    dist[1:-1, 1:-1] = np.where(valid & (~flat | terminal), 0.0, np.inf)
    Wp = W + 2
    # multi-source BFS across flat cells of equal elevation, seeded by the
    # flat cells that touch an exit
    todo = flat & ~terminal
    seed = np.zeros((H, W), bool)
    for dr, dc in _FLOW_DIRS:
        nd = dist[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
        ne = fp[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
        seed |= todo & (nd == 0) & (ne == fc)
    dl = dist.ravel().tolist()
    fl = fp.ravel().tolist()
    tl = np.pad(todo, 1, constant_values=False).ravel().tolist()
    deltas = [dr * Wp + dc for dr, dc in _FLOW_DIRS]
    q = deque()
    for idx in np.flatnonzero(np.pad(seed, 1, constant_values=False).ravel()).tolist():
        dl[idx] = 1.0
        q.append(idx)
    while q:
        idx = q.popleft()
        d1 = dl[idx] + 1.0
        e = fl[idx]
        for dd in deltas:
            n = idx + dd
            if tl[n] and dl[n] == np.inf and fl[n] == e:
                dl[n] = d1
                q.append(n)
    dist = np.array(dl).reshape(H + 2, Wp)
    dc_ = dist[1:-1, 1:-1]
    key_d = np.where(np.isfinite(dc_), dc_, 0.0)
    rank = np.empty(H * W, np.int64)
    sort_idx = np.lexsort((key_d.ravel(), np.where(valid, fc, np.inf).ravel()))
    rank[sort_idx] = np.arange(H * W)
    order = np.where(valid, rank.reshape(H, W), -1)
    return dist, terminal, order


def _twi_receivers(filled, valid, geom):
    """D8 receiver (flat downstream index, -1 = terminal) for every cell:
    steepest descent on the FILLED surface; flats drain to the neighbor
    nearest the exit (``_twi_flat_distance``). ``geom`` supplies the true
    per-row ground distances (planar projected / geodesic geographic).
    Returns (recv, order): ``order`` is the accumulation processing rank."""
    H, W = filled.shape
    fp = np.full((H + 2, W + 2), np.inf)
    fp[1:-1, 1:-1] = np.where(valid, filled, np.inf)
    fc = fp[1:-1, 1:-1]
    h = geom["h"]; vU = geom["vA"]; vB = geom["vB"]
    dU = np.hypot(h, vU); dB = np.hypot(h, vB)
    # Evaluation order = tie-breaking order. When two or more neighbors are
    # equally steep (common on integer DEMs: 7 percent of the cells of a
    # 30 m integer DEM), the FIRST one evaluated wins (strict >). This order
    # -- W, N, S, E, SW, NW, NE, SE -- reproduces Esri's Flow Direction choice
    # on 97-98 percent of tied cells (checked on four windows of an integer
    # DEM, 2026-09-23); the previous E-first order matched about half.
    dirs = [(0, -1, h), (-1, 0, vU), (1, 0, vB), (0, 1, h),
            (1, -1, dB), (-1, -1, dU), (-1, 1, dU), (1, 1, dB)]

    best_s = np.zeros((H, W))
    bdr = np.zeros((H, W), np.int64)
    bdc = np.zeros((H, W), np.int64)
    has = np.zeros((H, W), bool)
    for dr, dc, dist in dirs:
        nf = fp[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
        with np.errstate(invalid="ignore"):
            drop = fc - nf
            s = np.where(drop > 0, drop / dist[:, None], -1.0)
        upd = s > best_s
        best_s = np.where(upd, s, best_s)
        bdr = np.where(upd, dr, bdr)
        bdc = np.where(upd, dc, bdc)
        has |= upd

    fdist, terminal, order = _twi_flat_distance(fp, valid, has)
    fdr, fdc, take = _twi_flat_route(fp, fdist, valid, has, terminal)
    bdr = np.where(take, fdr, bdr)
    bdc = np.where(take, fdc, bdc)
    has = has | take

    rows = np.arange(H)[:, None]
    cols = np.arange(W)[None, :]
    rr = rows + bdr
    cc = cols + bdc
    recv = np.where(has & valid, rr * W + cc, -1).astype(np.int64)
    return recv.ravel(), order


def _twi_accumulate(recv, order, valid):
    """Flow accumulation = number of upstream cells (excl. self), matching Esri
    FlowAccumulation.  Processes valid cells in DECREASING flood order so each
    cell is summed before its (always lower-order) receiver -- O(n), no queue
    bookkeeping."""
    H, W = order.shape
    of = order.ravel()
    vf = valid.ravel()
    idx = np.flatnonzero(vf)
    idx_sorted = idx[np.argsort(of[idx], kind="stable")[::-1]]
    accL = [0.0] * (H * W)
    recvA = recv.tolist()
    for i in idx_sorted.tolist():
        r = recvA[i]
        if r >= 0:
            accL[r] += accL[i] + 1.0
    return np.array(accL, dtype=np.float64).reshape(H, W)


# Neighbor order shared by the multi-direction routers: E, W, N, S, NE, NW,
# SE, SW as (drow, dcol).
_FLOW_DIRS = ((0, 1), (0, -1), (-1, 0), (1, 0), (-1, 1), (-1, -1), (1, 1),
              (1, -1))


def _twi_flat_route(fp, fdist, valid, has, terminal):
    """For flat cells: the neighbor of equal elevation with the smallest
    distance to an exit. Returns (dr, dc, take); take=False = terminal (edge
    flats, and any flat with no reachable exit)."""
    H, W = valid.shape
    fc = fp[1:-1, 1:-1]
    flat = valid & (~has) & (~terminal)
    best = np.full((H, W), np.inf)
    fdr = np.zeros((H, W), np.int64)
    fdc = np.zeros((H, W), np.int64)
    fhas = np.zeros((H, W), bool)
    for dr, dc in _FLOW_DIRS:
        nd = fdist[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
        ne = fp[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
        cand = flat & (ne == fc) & (nd < best)
        best = np.where(cand, nd, best)
        fdr = np.where(cand, dr, fdr)
        fdc = np.where(cand, dc, fdc)
        fhas |= cand
    return fdr, fdc, flat & fhas


def _twi_flow_weights(filled, valid, geom, method):
    """Fractional flow weights for MFD or D-Infinity on the FILLED surface.

    Returns (wts, order): an (8, H, W) float64 array with the share of each
    cell's water sent to each neighbor in ``_FLOW_DIRS`` order (shares sum to
    1 for every routed cell; all zero for terminals), and the accumulation
    processing rank. Flats drain to the equal-elevation neighbor nearest the
    exit (``_twi_flat_distance``). Distances are the per-row ground spans in
    ``geom`` (h = east-west, vA = to the row above, vB = to the row below),
    so the routing is right on geographic DEMs too.
    """
    H, W = filled.shape
    fp = np.full((H + 2, W + 2), np.inf)
    fp[1:-1, 1:-1] = np.where(valid, filled, np.inf)
    fc = fp[1:-1, 1:-1]
    h = geom["h"][:, None]
    vU = geom["vA"][:, None]
    vB = geom["vB"][:, None]
    nb = {}
    for dr, dc in _FLOW_DIRS:
        nb[(dr, dc)] = fp[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
    wts = np.zeros((8, H, W), np.float64)
    key = _flow_key(method)

    if key == "DINF":
        # Eight triangular facets (cardinal leg, then the leg from the cardinal
        # neighbor to the diagonal neighbor). d1, d2 = the two leg lengths.
        facets = [((0, 1), (-1, 1), h, vU), ((-1, 0), (-1, 1), vU, h),
                  ((-1, 0), (-1, -1), vU, h), ((0, -1), (-1, -1), h, vU),
                  ((0, -1), (1, -1), h, vB), ((1, 0), (1, -1), vB, h),
                  ((1, 0), (1, 1), vB, h), ((0, 1), (1, 1), h, vB)]
        best_s = np.zeros((H, W))
        best_k = np.full((H, W), -1, np.int64)
        best_p = np.zeros((H, W))
        with np.errstate(invalid="ignore", divide="ignore"):
            for k, (card, diag, d1, d2) in enumerate(facets):
                e1 = nb[card]
                e2 = nb[diag]
                s1 = (fc - e1) / d1
                s2 = (e1 - e2) / d2
                r = np.arctan2(s2, s1)
                s = np.hypot(s1, s2)
                rmax = np.arctan2(d2, d1) * np.ones((H, W))
                low = r < 0
                r = np.where(low, 0.0, r)
                s = np.where(low, s1, s)
                high = r > rmax
                r = np.where(high, rmax, r)
                s = np.where(high, (fc - e2) / np.hypot(d1, d2), s)
                s = np.where(np.isfinite(s), s, -np.inf)
                upd = valid & (s > best_s)
                best_s = np.where(upd, s, best_s)
                best_k = np.where(upd, k, best_k)
                best_p = np.where(upd, r / rmax, best_p)
        has = best_k >= 0
        dir_index = {d: i for i, d in enumerate(_FLOW_DIRS)}
        for k, (card, diag, _d1, _d2) in enumerate(facets):
            sel = has & (best_k == k)
            if not sel.any():
                continue
            wts[dir_index[card]][sel] += (1.0 - best_p[sel])
            wts[dir_index[diag]][sel] += best_p[sel]
    else:
        # MFD: every lower neighbor, weight = slope^p x contour length, with
        # p = 8.9 x min(e, 1) + 1.1 from the steepest downslope gradient e
        # (Qin et al. 2007); contour lengths 0.5 x cell span for cardinal
        # neighbors and 0.354 x cell span for diagonals (Quinn et al. 1991).
        v = 0.5 * (vU + vB)
        dg = np.sqrt(h * v)
        dist = {(0, 1): h, (0, -1): h, (-1, 0): vU, (1, 0): vB,
                (-1, 1): np.hypot(h, vU), (-1, -1): np.hypot(h, vU),
                (1, 1): np.hypot(h, vB), (1, -1): np.hypot(h, vB)}
        clen = {(0, 1): 0.5 * v, (0, -1): 0.5 * v, (-1, 0): 0.5 * h,
                (1, 0): 0.5 * h, (-1, 1): 0.354 * dg, (-1, -1): 0.354 * dg,
                (1, 1): 0.354 * dg, (1, -1): 0.354 * dg}
        slopes = []
        with np.errstate(invalid="ignore"):
            for d in _FLOW_DIRS:
                drop = fc - nb[d]
                s = np.where(np.isfinite(drop) & (drop > 0), drop / dist[d], 0.0)
                slopes.append(s)
        emax = np.max(np.stack(slopes), axis=0)
        pexp = 8.9 * np.minimum(emax, 1.0) + 1.1
        total = np.zeros((H, W))
        for i, d in enumerate(_FLOW_DIRS):
            w = np.where(slopes[i] > 0, np.power(slopes[i], pexp) * clen[d], 0.0)
            wts[i] = w
            total += w
        has = valid & (total > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            for i in range(8):
                wts[i] = np.where(has, wts[i] / total, 0.0)

    # flats: everything to the equal-elevation neighbor nearest the exit
    fdist, terminal, order = _twi_flat_distance(fp, valid, has)
    fdr, fdc, take = _twi_flat_route(fp, fdist, valid, has, terminal)
    if take.any():
        for i, (dr, dc) in enumerate(_FLOW_DIRS):
            sel = take & (fdr == dr) & (fdc == dc)
            wts[i][sel] = 1.0
    wts[:, ~valid] = 0.0
    return wts, order


def _twi_accumulate_w(wts, order, valid):
    """Fractional flow accumulation (upslope area in cells, excluding the cell
    itself) from the (8, H, W) weights of ``_twi_flow_weights``. Cells are
    processed in decreasing flood order, so every cell is complete before it
    passes its area on."""
    H, W = order.shape
    Wp = W + 2
    deltas = [dr * Wp + dc for dr, dc in _FLOW_DIRS]
    wl = []
    for k in range(8):
        wp = np.zeros((H + 2, Wp))
        wp[1:-1, 1:-1] = wts[k]
        wl.append(wp.ravel().tolist())
    op = np.full((H + 2, Wp), -1, np.int64)
    op[1:-1, 1:-1] = np.where(valid, order, -1)
    of = op.ravel()
    idx = np.flatnonzero(of >= 0)
    idx_sorted = idx[np.argsort(of[idx], kind="stable")[::-1]].tolist()
    accL = [0.0] * ((H + 2) * Wp)
    w0, w1, w2, w3, w4, w5, w6, w7 = wl
    d0, d1, d2, d3, d4, d5, d6, d7 = deltas
    for i in idx_sorted:
        a = accL[i] + 1.0
        w = w0[i]
        if w:
            accL[i + d0] += w * a
        w = w1[i]
        if w:
            accL[i + d1] += w * a
        w = w2[i]
        if w:
            accL[i + d2] += w * a
        w = w3[i]
        if w:
            accL[i + d3] += w * a
        w = w4[i]
        if w:
            accL[i + d4] += w * a
        w = w5[i]
        if w:
            accL[i + d5] += w * a
        w = w6[i]
        if w:
            accL[i + d6] += w * a
        w = w7[i]
        if w:
            accL[i + d7] += w * a
    return np.array(accL, dtype=np.float64).reshape(H + 2, Wp)[1:-1, 1:-1]


def _twi_read_aligned(path, width, height, scratch, messages, what):
    """Read an optional pre-computed raster that must share the DEM's grid."""
    gdal = _mh._gdal()
    src, copy = _hs._open_source(path, scratch, messages)
    try:
        if src.RasterXSize != width or src.RasterYSize != height:
            raise arcpy.ExecuteError(
                "The {w} raster is {a} x {b} but the DEM is {c} x {d}; they "
                "must share the same grid (extent, cell size, alignment)."
                .format(w=what, a=src.RasterXSize, b=src.RasterYSize,
                        c=width, d=height))
        band = src.GetRasterBand(1)
        arr = band.ReadAsArray(0, 0, width, height).astype(np.float64)
        nod = band.GetNoDataValue()
        # Contract is (arr, nod): bake mask-band-only NoData (gdb GRID
        # phantom-fill trap) into the array as the nod value so callers'
        # value screens keep working unchanged.
        vmask = _mh.band_validity_mask(band)
        if vmask is not None:
            if nod is None:
                nod = NODATA_OUT
            arr[~vmask] = nod
    finally:
        src = None
        if copy:
            try:
                arcpy.management.Delete(copy)
            except Exception:
                pass
    return arr, nod


def run_twi(in_raster, out_raster, elev_units="Meters",
            slope_epsilon=0.001, in_filled_dem=None, in_flow_accumulation=None,
            out_filled_dem=None, out_flow_accumulation=None, messages=None,
            flow_method="D8"):
    """Topographic Wetness Index (TWI / CTI): TWI = ln(SCA / (tan(beta) +
    eps)).  See the section header for the full method and citations.
    ``flow_method`` (D8 / MFD / DINF, see FLOW_METHODS) selects the routing
    used when the tool computes the flow accumulation itself."""
    gdal = _mh._gdal()
    eps = float(slope_epsilon)
    fkey = _flow_key(flow_method)
    flabel = {"D8": "D8", "MFD": "MFD (multiple flow direction)",
              "DINF": "D-Infinity"}[fkey]
    fcit = {"D8": _TWI_CIT_D8, "MFD": _TWI_CIT_MFD, "DINF": _TWI_CIT_DINF}[fkey]
    if eps <= 0:
        raise arcpy.ExecuteError("The value added to slope must be positive.")
    have_fac = bool(in_flow_accumulation)
    have_fill = bool(in_filled_dem)
    save_fill = bool(out_filled_dem) and not have_fill and not have_fac
    save_fac = bool(out_flow_accumulation) and not have_fac
    out_raster = _mh.resolve_raster_output(out_raster, messages)
    if save_fill:
        out_filled_dem = _mh.resolve_raster_output(out_filled_dem, messages)
    if save_fac:
        out_flow_accumulation = _mh.resolve_raster_output(
            out_flow_accumulation, messages)

    phases = [("Reading the DEM...", 1)]
    if not have_fac:
        phases += [("Filling sinks (priority-flood)...", 6),
                   ("Tracing {} flow directions...".format(flabel), 2),
                   ("Accumulating flow...", 2)]
    phases += [("Computing slope and TWI...", 2),
               ("Saving the TWI raster...", 1),
               ("Building pyramids...", 1),
               ("Creating layer file and symbology...", 1)]
    _mh.begin_phases(phases)

    scratch = arcpy.env.scratchFolder
    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    in_nodata = band.GetNoDataValue()
    sr = arcpy.Describe(in_raster).spatialReference
    geographic = (sr.type == "Geographic")

    elev_mpu = _hs._ELEV_METERS.get(elev_units, 1.0)
    ground, _geo, zscale = _row_gradient_geometry(gt, width, height, sr, proj,
                                                  elev_mpu, messages)
    slope_geom = ground                  # true ground spans, per cell
    base_geom = ground.row               # per-row table for flow routing / SCA
    slope_used = {"geographic": "Geodesic (spheroidal, per row)",
                  "geodesic": "Geodesic (true ground distances, lattice)",
                  }.get(ground.mode, "Planar (coordinate system could not be "
                                     "unprojected)")
    # planimetric ground cell size (m) per row for the specific catchment area
    sca_cell = np.sqrt(base_geom["h"] * base_geom["vA"])

    zraw = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(zraw)
    if in_nodata is not None:
        valid &= (zraw != in_nodata)
    valid = _mh.mask_band_valid(band, valid)
    src = None
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass
    nvalid = int(valid.sum())
    if nvalid == 0:
        raise arcpy.ExecuteError("The DEM has no valid cells.")
    _hs._msg(messages, "DEM: {:,} x {:,} cells ({:,} valid); {}; slope "
             "method: {}; flow routing: {}.".format(
                 width, height, nvalid,
                 "geographic" if geographic else "projected", slope_used,
                 "supplied accumulation" if have_fac else flabel))

    # ---- flow accumulation (supplied, or fill -> D8 -> accumulate) ----
    filled = None
    if have_fac:
        acc, _n = _twi_read_aligned(in_flow_accumulation, width, height,
                                    scratch, messages, "flow-accumulation")
        acc = np.where(np.isfinite(acc) & (acc >= 0), acc, 0.0)
        _hs._msg(messages, "Using supplied flow-accumulation raster (skipping "
                 "fill, flow direction, and accumulation).")
    else:
        _mh.phase_advance("Filling sinks (priority-flood)...")
        if have_fill:
            # The supplied surface IS the routing surface: the tool's own
            # sink fill is skipped, so sinks left in it on purpose (a fill
            # with a Z limit) survive. The routers tolerate them: a cell with
            # no lower neighbor and no equal-elevation exit gets no receiver
            # and simply ends the flow (a terminal, like a raster-edge cell).
            farr, fnod = _twi_read_aligned(in_filled_dem, width, height,
                                           scratch, messages, "filled-DEM")
            fmask = np.isfinite(farr)
            if fnod is not None:
                fmask &= (farr != fnod)
            filled = np.where(fmask, farr, zraw)
            del farr, fmask
            _hs._msg(messages, "Using supplied filled DEM as the routing "
                     "surface (the tool's own sink fill is skipped; any "
                     "sink the supplied surface still holds ends the flow "
                     "that reaches it).")
        else:
            filled, order = _twi_priority_flood(zraw, valid, messages)
            del order                  # routing computes its own order
        _mh.phase_advance("Tracing {} flow directions...".format(flabel))
        if fkey == "D8":
            recv, porder = _twi_receivers(filled, valid, base_geom)
            _mh.phase_advance("Accumulating flow...")
            acc = _twi_accumulate(recv, porder, valid)
            del recv
        else:
            wts, porder = _twi_flow_weights(filled, valid, base_geom, fkey)
            _mh.phase_advance("Accumulating flow...")
            acc = _twi_accumulate_w(wts, porder, valid)
            del wts
        del porder

    # ---- slope (original DEM) and TWI ----
    _mh.phase_advance("Computing slope and TWI...")
    zm = np.where(valid, zraw * zscale, np.nan)
    tanb = _twi_tanbeta(zm, valid, slope_geom)
    del zm
    sca = (acc + 1.0) * sca_cell[:, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        twi = np.log(sca / (tanb + eps))
    out_valid = valid & np.isfinite(twi)
    twi = np.where(out_valid, twi, NODATA_OUT).astype(np.float32)
    _hs._msg(messages, "TWI ({}): {:,} cells; SCA = (flow accumulation + 1) x "
             "cell size, constant added to slope {:g}, flow routing {}.".format(
                 _TWI_CITATION, int(out_valid.sum()), eps,
                 "supplied accumulation" if have_fac else flabel))

    def _write(arr, path, valid_mask, kind):
        op = arcpy.CreateUniqueName("twi_%s.tif" % kind, scratch)
        ds, ob = _make_output(gdal, op, gt, proj, width, height)
        ob.WriteArray(np.where(valid_mask, arr, NODATA_OUT).astype(np.float32))
        st = _mh._RasterStats()
        if valid_mask.any():
            st.add(arr[valid_mask].astype(np.float64))
        os_ = _mh._finalize_stats(ob, st, messages)
        if os_ is not None:
            os_["on_raster"] = _mh._write_gtiff_stats(ob, os_)
        ob.FlushCache(); ds.FlushCache(); ob = None; ds = None
        return op, os_

    out_path, out_stats = _write(twi.astype(np.float64), None, out_valid, "twi")

    _mh.phase_advance("Saving the TWI raster...")
    _deliver_output(
        out_path, out_raster, out_stats, messages,
        title=os.path.basename(str(out_raster)),
        summary=("Created by Topographic Wetness Index from {src}: SCA = "
                 "(flow accumulation + 1) x cell size, constant added to slope {e:g}, "
                 "slope method {sm}. Flow: {fl}.").format(
                     src=str(in_raster), e=eps, sm=slope_used,
                     fl=("supplied flow accumulation" if have_fac else
                         "internal priority-flood fill + " + flabel)),
        description=(
            "Topographic Wetness Index (TWI), also called the Compound "
            "Topographic Index (CTI): ln( specific catchment area / (tan(slope)"
            " + added constant) ). High values mark cells with large upslope "
            "contributing area and/or gentle slope (valley bottoms, "
            "convergence zones -- prone to wetness); low values mark small "
            "area and/or steep slope (ridges, divergence zones -- drier). "
            "Specific catchment area = (flow accumulation + 1) x cell size, "
            "with the unit contour length taken as the cell size (each cell is "
            "its own outlet). " + _TWI_CITATION),
        tags=("topographic wetness index, TWI, compound topographic index, "
              "CTI, specific catchment area, wetness, hydrology, topography, "
              "Jenness Enterprises"),
        citation=_TWI_CITATION)

    if save_fac:
        fp, fs = _write(acc, None, valid, "fac")
        _deliver_output(
            fp, out_flow_accumulation, fs, messages,
            title=os.path.basename(str(out_flow_accumulation)),
            summary=("Created by Topographic Wetness Index from {src}: {fl} "
                     "flow accumulation (upslope area in cells, excl. "
                     "self).").format(src=str(in_raster), fl=flabel),
            description=("{fl} flow accumulation (upslope area in cells "
                         "draining to each cell, excluding the cell itself"
                         "{frac}), from a priority-flood-filled DEM. ".format(
                             fl=flabel,
                             frac=("" if fkey == "D8" else
                                   "; fractional, because each cell's area is "
                                   "shared among its downslope neighbors")) +
                         fcit),
            tags=("flow accumulation, {}, upslope area, hydrology, topography, "
                  "Jenness Enterprises".format(flabel)),
            citation=fcit)
    if save_fill and filled is not None:
        pp, ps = _write(filled, None, valid, "fill")
        _deliver_output(
            pp, out_filled_dem, ps, messages,
            title=os.path.basename(str(out_filled_dem)),
            summary=("Created by Topographic Wetness Index from {src}: "
                     "depression-filled DEM (priority-flood).").format(
                         src=str(in_raster)),
            description=("Depression-filled (hydrologically conditioned) DEM "
                         "produced by the priority-flood algorithm; sinks are "
                         "raised to their lowest spill point so flow routing "
                         "does not get stuck. " + _TWI_CIT_FILL),
            tags=("filled DEM, fill sinks, priority-flood, depression filling, "
                  "hydrology, topography, Jenness Enterprises"),
            citation=_TWI_CIT_FILL)

    _mh.phase_advance("Creating layer file and symbology...")
    # dry (low TWI) tan -> pale -> wet (high TWI) blue
    out_lyrx = _mh.default_stretch_lyrx(
        out_raster, [(198, 153, 86), (238, 238, 210), (33, 102, 172)],
        messages=messages)
    _mh.end_phases()
    _hs._msg(messages, "Topographic Wetness Index complete: {}".format(
        out_raster))
    return out_lyrx


# ---------------------------------------------------------------------------
#  Slope Zonal Statistics as Table
# ---------------------------------------------------------------------------
# The slope-targeted sibling of Aspect Zonal Statistics as Table (and of
# Esri's Zonal Statistics as Table; no Spatial Analyst / Image Analyst
# needed). It REUSES the aspect tool's zone-gathering machinery (integer
# raster zones, per-zone feature windows with the overlap guarantee, polygon
# center-in / polyline cell-crossing rules) via the generic feed callback,
# but accumulates LINEAR statistics computed in ANGLE space: terrain
# steepness is an angle, and percent slope is its tangent -- a convex
# transform -- so averaging raw percent values biases every moment statistic
# high. Values convert through the arctangent BEFORE the sums, and the
# results convert back to the source unit only for display (tan of the mean
# angle, never the mean of the tangents). Order statistics (median,
# percentiles, min, max) are transform-invariant and reported in source
# units; the SD and CI half-angle stay in degrees, which have no meaningful
# nonlinear conversion.

def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


SLOPE_UNITS_Z = ["Degrees (-90 to 90)",
                 "Percent slope (rise/run × 100)",
                 "Rise:run ratio",
                 "Gradians (gons)"]

SLOPE_ZONAL_STATS = [
    ("ALL",    "All statistics"),
    ("MEAN",   "Mean Slope"),
    ("MEDIAN", "Median Slope"),
    ("SD",     "Standard Deviation"),
    ("MIN",    "Minimum Slope"),
    ("MAX",    "Maximum Slope"),
    ("RANGE",  "Range"),
    ("PCTL",   "5th and 95th Percentiles"),
    ("CI95",   "Mean Slope 95% Confidence Interval"),
]
SLOPE_ZONAL_LABELS = [lbl for _, lbl in SLOPE_ZONAL_STATS]
SLOPE_ZONAL_KEY = {lbl: k for k, lbl in SLOPE_ZONAL_STATS}
_SZORDER = ["MEAN", "MEDIAN", "SD", "MIN", "MAX", "RANGE", "PCTL", "CI95"]

_SZBINS = 9000                     # 0.02-degree bins over -90..90
_SZMAX_ZONES = 10000


def _slope_unit_key(label):
    lab = str(label).lower()
    if lab.startswith("percent"):
        return "percent"
    if lab.startswith("rise"):
        return "ratio"
    if lab.startswith("grad"):
        return "gradians"
    return "degrees"


def _slope_to_deg(v, unit):
    """Array conversion of source-unit slope values to DEGREES (sign kept)."""
    if unit == "percent":
        return np.degrees(np.arctan(v / 100.0))
    if unit == "ratio":
        return np.degrees(np.arctan(v))
    if unit == "gradians":
        return v * 0.9
    return v


def _slope_from_deg(d, unit):
    """Scalar conversion of a degree value back to the source unit."""
    if d is None:
        return None
    if unit == "percent":
        return math.tan(math.radians(max(min(d, 89.999), -89.999))) * 100.0
    if unit == "ratio":
        return math.tan(math.radians(max(min(d, 89.999), -89.999)))
    if unit == "gradians":
        return d / 0.9
    return d


def _slope_unit_word(unit):
    return {"percent": "percent", "ratio": "rise:run",
            "gradians": "gons"}.get(unit, "degrees")


def _t95_quantile(df):
    """Two-sided 95% t quantile (the t value for a 95% confidence interval of
    a mean; Zar 1999 ch. 7), exact for every df via scipy. (Until 2026-09-24
    this was Zar's table through 30 df and coarse steps above it, which used
    the value for the high end of each step and so made the interval up to
    1% too narrow at 31, 41, 61 and 121 df.)"""
    if df < 1:
        return float("nan")
    from scipy.stats import t as _t
    return float(_t.ppf(0.975, df))


class _SlopeZoneAcc(object):
    """Per-zone linear accumulator with the same duck-typed interface the
    shared zone runners expect (zones / _zone / add_counts): degree-space
    moment sums + a 0.02-degree histogram over -90..90 for the order
    statistics, plus exact source-unit min/max."""

    def __init__(self):
        self.zones = {}

    def _zone(self, key):
        z = self.zones.get(key)
        if z is None:
            import arcpy
            if len(self.zones) >= _SZMAX_ZONES:
                raise arcpy.ExecuteError(
                    "More than {:,} zones -- reduce the number of distinct "
                    "zone values.".format(_SZMAX_ZONES))
            z = {"n": 0, "sdeg": 0.0, "sdeg2": 0.0,
                 "minv": None, "maxv": None, "neg": 0, "nodata": 0,
                 "bad": 0, "flat": 0,
                 "hist": np.zeros(_SZBINS, np.float64)}
            self.zones[key] = z
        return z

    def add_counts(self, key, flat=0, nodata=0, bad=0):
        z = self._zone(key)
        z["flat"] += int(flat)
        z["nodata"] += int(nodata)
        z["bad"] += int(bad)

    def add_slopes(self, key, deg, src):
        """Accumulate parallel arrays of degree-space and source-unit
        values (already validated / abs-applied)."""
        z = self._zone(key)
        if deg.size == 0:
            return
        z["n"] += int(deg.size)
        z["sdeg"] += float(np.sum(deg))
        z["sdeg2"] += float(np.sum(deg * deg))
        mn, mx = float(np.min(src)), float(np.max(src))
        z["minv"] = mn if z["minv"] is None else min(z["minv"], mn)
        z["maxv"] = mx if z["maxv"] is None else max(z["maxv"], mx)
        b = np.floor((deg + 90.0) * (_SZBINS / 180.0)).astype(np.int64)
        np.clip(b, 0, _SZBINS - 1, out=b)
        z["hist"] += np.bincount(b, minlength=_SZBINS)


def _hist_quantile_deg(hist, p):
    """Degree value at cumulative fraction ``p`` of the -90..90 histogram."""
    total = float(hist.sum())
    if total <= 0:
        return None
    target = p * total
    cum = np.cumsum(hist)
    b = int(np.searchsorted(cum, target, side="left"))
    b = min(b, hist.size - 1)
    return (b + 0.5) * (180.0 / _SZBINS) - 90.0


def _slope_zone_row(z, unit, ignore_nodata):
    """Field-name -> value dict for one zone (None = NULL)."""
    out = {f: None for f in ("MEAN", "MEDIAN", "SD_DEG", "MIN", "MAX",
                             "RANGE", "P05", "P95", "CI95_HALF_DEG",
                             "CI95_LO", "CI95_HI")}
    n = z["n"]
    if (not ignore_nodata and z["nodata"] > 0) or n == 0:
        return out
    mean_deg = z["sdeg"] / n
    out["MEAN"] = _slope_from_deg(mean_deg, unit)
    out["MEDIAN"] = _slope_from_deg(_hist_quantile_deg(z["hist"], 0.5), unit)
    out["MIN"] = z["minv"]
    out["MAX"] = z["maxv"]
    if z["minv"] is not None and z["maxv"] is not None:
        out["RANGE"] = z["maxv"] - z["minv"]
    out["P05"] = _slope_from_deg(_hist_quantile_deg(z["hist"], 0.05), unit)
    out["P95"] = _slope_from_deg(_hist_quantile_deg(z["hist"], 0.95), unit)
    if n > 1:
        ss = max(z["sdeg2"] - n * mean_deg * mean_deg, 0.0)
        sd = math.sqrt(ss / (n - 1))
        out["SD_DEG"] = sd
        if sd > 0:
            half = _t95_quantile(n - 1) * sd / math.sqrt(n)
            out["CI95_HALF_DEG"] = half
            out["CI95_LO"] = _slope_from_deg(max(mean_deg - half, -90.0), unit)
            out["CI95_HI"] = _slope_from_deg(min(mean_deg + half, 90.0), unit)
    return out


def _slope_units_from_history(text):
    """'percent', 'degrees', or None from geoprocessing-history XML: the
    last Slope / Surface Parameters process wins. A run from the
    Geoprocessing pane records the parameter's DISPLAY label ("Percent
    rise", "Degree"); a run from Python records the keyword (PERCENT_RISE,
    DEGREE). Both are matched -- a pane-made raster was missed until
    2026-10-14. A raster NAME containing the word is not enough: only the
    two-word label or the keyword counts for percent, and only the whole
    word for degrees."""
    import re as _re
    found = None
    for m in _re.finditer(r"<Process\b.*?</Process>", text or "",
                          _re.S | _re.I):
        seg = m.group(0)
        low = seg.lower()
        if "slope" not in low and "surfaceparameters" not in low:
            continue
        if _re.search(r"percent[\s_]+rise", low):
            found = "percent"
        elif _re.search(r"\bdegree\b", low):
            found = "degrees"
    return found


def _detect_slope_units(in_raster):
    """'percent', 'degrees', or None from the raster's geoprocessing lineage
    (see _slope_units_from_history). Best effort; never raises."""
    import arcpy
    try:
        # A LAYER name must resolve to the dataset path first, and this
        # arcpy has no Metadata.lineage attribute -- the geoprocessing
        # history lives inside md.xml.
        try:
            path = arcpy.Describe(in_raster).catalogPath
        except Exception:
            path = in_raster
        md = arcpy.metadata.Metadata(path)
        text = (md.xml or "") + " " + (getattr(md, "lineage", "") or "")
    except Exception:
        return None
    return _slope_units_from_history(text)


def _at_zone_extent(in_zone_data, sr):
    """The zone dataset's extent in ``sr`` (see aspect_tools)."""
    import aspect_tools
    return aspect_tools._zone_extent_in(in_zone_data, sr)


def run_slope_zonal(in_zone_data, zone_field, in_raster, out_table,
                    ignore_nodata=True, statistics_type="ALL",
                    slope_units=SLOPE_UNITS_Z[0], use_absolute=True,
                    messages=None):
    """Slope Zonal Statistics as Table: per-zone linear statistics of a slope
    raster, computed in ANGLE space and reported in the source unit -- the
    slope-targeted sibling of Esri's Zonal Statistics as Table, requiring no
    Spatial Analyst or Image Analyst extension.

    ``slope_units`` names the raster's unit (degrees / percent / rise:run /
    gradians); percent and ratio values pass through the arctangent BEFORE
    the mean/SD/CI (averaging raw percent slopes biases statistics high),
    and single-value results convert back for display. ``use_absolute`` True
    (the default) treats negative slopes as positive (absolute steepness);
    False keeps signs, letting downhill balance uphill. Out-of-range values
    (beyond +/-90 degrees or +/-100 gon) are excluded, counted, and reported.
    Zone semantics, the overlap guarantee, ``ignore_nodata``, and the
    cell-gathering rules are identical to Aspect Zonal Statistics as Table;
    point zones use ordinary bilinear interpolation computed on the slope
    ANGLES. Returns the output table path."""
    import arcpy
    import aspect_tools as _at

    unit = _slope_unit_key(slope_units)
    stat_key = SLOPE_ZONAL_KEY.get(statistics_type, statistics_type or "ALL")
    if stat_key != "ALL" and stat_key not in _SZORDER:
        stat_key = "ALL"
    keys = _SZORDER if stat_key == "ALL" else [stat_key]

    _mh.begin_phases([
        ("Reading the zones...", 1),
        ("Gathering raster cells per zone...", 8),
        ("Writing the output table...", 2),
    ])

    dz = arcpy.Describe(in_zone_data)
    zone_is_raster = dz.dataType in ("RasterDataset", "RasterLayer",
                                     "MosaicDataset", "MosaicLayer")
    shape_type = None if zone_is_raster else dz.shapeType

    if use_absolute:
        _say(messages, "Negative slopes are treated as POSITIVE (absolute "
                       "steepness); the count of originally negative values "
                       "is reported per zone in NEG_COUNT.")
    else:
        _say(messages, "Negative slopes keep their sign (downhill can "
                       "balance uphill in the mean); NEG_COUNT reports how "
                       "many values were negative.")

    # Valid range per unit (percent / ratio are unbounded).
    if unit == "degrees":
        lim = 90.0 + 1e-6
    elif unit == "gradians":
        lim = 100.0 + 1e-6
    else:
        lim = None

    acc = _SlopeZoneAcc()
    zone_order = []

    def touch(key):
        if key not in acc.zones:
            zone_order.append(key)
        acc._zone(key)

    # Only the zones' windows are read, so the large-raster check applies to
    # the zones' extent rather than to the whole raster.
    src, in_copy = _hs._open_source(in_raster, arcpy.env.scratchFolder,
                                    messages, check_size=False)
    try:
        band = src.GetRasterBand(1)
        W, H = src.RasterXSize, src.RasterYSize
        gt = src.GetGeoTransform()
        nodata = band.GetNoDataValue()
        rsr = arcpy.Describe(in_raster).spatialReference
        _hs.check_extent_window(src, in_raster,
                                _at_zone_extent(in_zone_data, rsr), messages)

        total_bad = 0
        total_neg = 0

        def feed(key, vals):
            nonlocal total_bad, total_neg
            if key not in acc.zones:
                zone_order.append(key)
            z = acc._zone(key)
            v = np.asarray(vals, np.float64)
            if lim is not None:
                bad = ~np.isfinite(v) | (np.abs(v) > lim)
            else:
                bad = ~np.isfinite(v)
            nb = int(np.count_nonzero(bad))
            if nb:
                z["bad"] += nb
                total_bad += nb
                v = v[~bad]
            neg = int(np.count_nonzero(v < 0.0))
            z["neg"] += neg
            total_neg += neg
            if use_absolute:
                v = np.abs(v)
            deg = _slope_to_deg(v, unit)
            acc.add_slopes(key, deg, v)

        if zone_is_raster:
            _at._run_zonal_raster(in_zone_data, band, gt, W, H, nodata, rsr,
                                  acc, zone_order, feed, messages)
        elif shape_type == "Point":
            _slope_zonal_points(in_zone_data, zone_field, band, gt, W, H,
                                nodata, rsr, unit, lim, use_absolute, acc,
                                zone_order, touch, messages)
        elif shape_type in ("Polyline", "Polygon"):
            _at._run_zonal_shapes(in_zone_data, zone_field, shape_type, band,
                                  gt, W, H, nodata, rsr, acc, zone_order,
                                  touch, feed, messages)
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

    if total_bad:
        _warn(messages,
              "{:,} value(s) fall outside the valid slope range for {} and "
              "were excluded -- this may not be a slope raster in the "
              "chosen unit.".format(total_bad, _slope_unit_word(unit)))
    if total_neg:
        _say(messages, "{:,} negative slope value(s) were encountered "
                       "(see NEG_COUNT).".format(total_neg))

    _mh.phase_advance("Writing the output table...")
    out_table = _write_slope_zonal_table(
        out_table, in_zone_data, zone_field, zone_is_raster, acc, zone_order,
        keys, unit, ignore_nodata, in_raster, statistics_type, use_absolute,
        messages)
    _mh.end_phases()
    return out_table


def _slope_zonal_points(in_zone, zone_field, band, gt, W, H, nodata, rsr,
                        unit, lim, use_absolute, acc, zone_order, touch,
                        messages):
    """Point zones: ordinary bilinear interpolation computed on the slope
    ANGLES of the four surrounding cell centers (NoData / out-of-range
    neighbors excluded, weights renormalized)."""
    import arcpy
    d = arcpy.Describe(in_zone)
    if d.shapeType != "Point":
        raise arcpy.ExecuteError(
            "Multipoint zone features are not supported -- use single-part "
            "points.")
    _say(messages, "Point zones: the slope at each point is interpolated "
                   "bilinearly on the slope ANGLES.")
    keys_, xs, ys = [], [], []
    n_null = 0
    import aspect_tools as _at2
    with _at2._zone_cursor(in_zone, [zone_field, "SHAPE@XY"], rsr, gt, W, H,
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
    if not keys_:
        _warn(messages, "No usable zone points were found.")
        return
    import aspect_tools as _at
    xs = np.asarray(xs, np.float64)
    ys = np.asarray(ys, np.float64)
    csx, csy = gt[1], abs(gt[5])
    c0, r0, c1, r1 = _at._zone_window(
        (xs.min(), ys.min(), xs.max(), ys.max()), gt, W, H)
    if c1 <= c0 or r1 <= r0:
        _warn(messages, "None of the points fall within the slope raster.")
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
    oor = np.zeros(arr.shape, bool)       # present but outside the valid range
    if lim is not None:
        oor = valid & (np.abs(arr) > lim)
        valid &= ~oor
    sval = np.where(valid, arr, 0.0)
    # NEG_COUNT reports values negative in the SOURCE, before the absolute-
    # value option (as for cell zones): interpolate the signed angles too,
    # and count a point negative when its signed interpolated slope is < 0.
    dval_signed = _slope_to_deg(sval, unit)
    if use_absolute:
        sval = np.abs(sval)
    dval = _slope_to_deg(sval, unit)

    colf = (xs - x0w) / csx - 0.5
    rowf = (y0w - ys) / csy - 0.5
    cL = np.floor(colf).astype(np.int64)
    rT = np.floor(rowf).astype(np.int64)
    fx = colf - cL
    fy = rowf - rT
    dsum = np.zeros(len(keys_))
    ssum = np.zeros(len(keys_))
    wsum = np.zeros(len(keys_))
    lost = np.zeros(len(keys_), bool)     # lost a neighbor to the range check
    for dr in (0, 1):
        for dc in (0, 1):
            cc = cL + dc
            rr = rT + dr
            w = (fx if dc else 1.0 - fx) * (fy if dr else 1.0 - fy)
            inb = (cc >= 0) & (cc < Ww) & (rr >= 0) & (rr < Hw) & (w > 0)
            ccs = np.clip(cc, 0, Ww - 1)
            rrs = np.clip(rr, 0, Hw - 1)
            ok = inb & valid[rrs, ccs]
            lost |= inb & oor[rrs, ccs]
            dsum += np.where(ok, w * dval[rrs, ccs], 0.0)
            ssum += np.where(ok, w * dval_signed[rrs, ccs], 0.0)
            wsum += np.where(ok, w, 0.0)

    n_lost = int(np.count_nonzero(lost))
    if n_lost:
        n_lost_all = int(np.count_nonzero(lost & (wsum <= 1e-12)))
        _warn(messages,
              "{:,} point(s) had a value outside the valid slope range for {} "
              "among their four surrounding cells; those cells were left out "
              "of the interpolation{} -- this may not be a slope raster in the "
              "chosen unit.".format(
                  n_lost, _slope_unit_word(unit),
                  " ({:,} point(s) were left with no value)".format(n_lost_all)
                  if n_lost_all else ""))
    n_out = 0
    per_zone_deg = {}
    per_zone_neg = {}
    for i, k in enumerate(keys_):
        touch(k)
        if wsum[i] > 1e-12:
            per_zone_deg.setdefault(k, []).append(dsum[i] / wsum[i])
            if ssum[i] / wsum[i] < 0.0:
                per_zone_neg[k] = per_zone_neg.get(k, 0) + 1
        else:
            acc.add_counts(k, nodata=1)
            n_out += 1
    for k, degs in per_zone_deg.items():
        dd = np.asarray(degs, np.float64)
        z = acc._zone(k)
        z["neg"] += per_zone_neg.get(k, 0)
        src_units = np.asarray(
            [_slope_from_deg(float(x), unit) for x in dd], np.float64)
        acc.add_slopes(k, dd, src_units)
    if n_out:
        _say(messages, "{:,} point(s) fall on NoData or outside the raster "
                       "and were excluded.".format(n_out))


def _write_slope_zonal_table(out_table, in_zone_data, zone_field,
                             zone_is_raster, acc, zone_order, keys, unit,
                             ignore_nodata, in_raster, statistics_type,
                             use_absolute, messages):
    import arcpy
    uw = _slope_unit_word(unit)
    # A table in a plain folder is a dBASE table: it needs the .dbf extension
    # and field names of at most 10 characters.
    out_table = _mh.dbf_table_path(out_table)
    dbf = _mh.is_shapefile_target(out_table)
    ws, name = os.path.split(str(out_table))
    arcpy.management.CreateTable(ws, name)

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
    taken = {fld.name.lower() for fld in arcpy.ListFields(out_table)}
    base_zf, k2 = zf_name, 0
    while zf_name.lower() in taken:
        k2 += 1
        zf_name = "{}_{}".format(base_zf, k2)
    # one AddFields call for the whole schema (standing rule: a per-field
    # AddField loop is a full GP launch per field)
    fdesc = [[zf_name, zf_type,
              zone_field if not zone_is_raster else "Zone Value",
              zf_len or "", "", ""],
             ["COUNT", "LONG", "Count of Cells Used", "", "", ""],
             ["NEG_COUNT", "LONG", "Values Negative in the Source",
              "", "", ""]]

    fields_for = {
        "MEAN":   [("MEAN", "Mean Slope ({})".format(uw))],
        "MEDIAN": [("MEDIAN", "Median Slope ({})".format(uw))],
        "SD":     [("SD_DEG", "Standard Deviation (degrees, of the slope "
                              "angles)")],
        "MIN":    [("MIN", "Minimum Slope ({})".format(uw))],
        "MAX":    [("MAX", "Maximum Slope ({})".format(uw))],
        "RANGE":  [("RANGE", "Range ({})".format(uw))],
        "PCTL":   [("P05", "5th Percentile ({})".format(uw)),
                   ("P95", "95th Percentile ({})".format(uw))],
        "CI95":   [("CI95_HALF_DEG", "95% CI Half-Width (degrees)"),
                   ("CI95_LO", "95% CI Lower Bound ({})".format(uw)),
                   ("CI95_HI", "95% CI Upper Bound ({})".format(uw))],
    }
    stat_fields, stat_keys = [], []
    for k in keys:
        for fkey, alias in fields_for[k]:
            fname = "CI95_HALF" if (dbf and fkey == "CI95_HALF_DEG") else fkey
            fdesc.append([fname, "DOUBLE", alias, "", "", ""])
            stat_fields.append(fname)
            stat_keys.append(fkey)
    arcpy.management.AddFields(out_table, fdesc)

    try:
        ordered = sorted(zone_order)
    except TypeError:
        ordered = zone_order

    # A dBASE table cannot hold NULL: statistics that cannot be computed
    # for a zone are written as -999 there (the toolbox's convention).
    nul = -999.0 if dbf else None
    with arcpy.da.InsertCursor(
            out_table, [zf_name, "COUNT", "NEG_COUNT"] + stat_fields) as cur:
        for key in ordered:
            z = acc.zones[key]
            row_stats = _slope_zone_row(z, unit, ignore_nodata)
            cur.insertRow([key, min(z["n"], 2147483647),
                           min(z["neg"], 2147483647)]
                          + [nul if row_stats[f] is None else row_stats[f]
                             for f in stat_keys])
    _say(messages, "Wrote {:,} zone row(s) to {}.{}".format(
        len(ordered), out_table,
        " Statistics that could not be computed are -999 (a dBASE table "
        "cannot hold NULL)." if dbf else ""))

    try:
        md = arcpy.metadata.Metadata(out_table)
        md.title = "Slope Zonal Statistics"
        md.summary = (
            "Slope statistics of '{}' summarized per zone from '{}' "
            "(statistic: {}; units: {}; negative slopes {}). Mean, SD and "
            "CI computed on the slope ANGLES and converted for display."
            .format(os.path.basename(str(in_raster)),
                    os.path.basename(str(in_zone_data)),
                    statistics_type, uw,
                    "treated as positive" if use_absolute
                    else "kept signed"))
        md.description = (
            "Zonal summary of a slope raster with the mean, standard "
            "deviation and confidence interval computed in ANGLE space: "
            "percent slope is the tangent of the slope angle, a convex "
            "transform, so averaging raw percent values biases steepness "
            "statistics high. Produced by the Slope Zonal Statistics as "
            "Table tool (Jenness Enterprises Wildlife and Forestry Tools), the "
            "slope-targeted sibling of Esri's Zonal Statistics as Table; "
            "unlike that tool it needs no Spatial Analyst or Image Analyst "
            "extension. Reference: Zar, J.H. 1999. Biostatistical Analysis, "
            "4th ed. (t-based confidence interval).")
        md.save()
    except Exception:
        pass
    return out_table


# ---------------------------------------------------------------------------
#  Extract Slope Values to Points
# ---------------------------------------------------------------------------
def _slope_from_deg_array(d, unit):
    """Array conversion of degree values back to the source unit (sign
    kept); the array twin of ``_slope_from_deg``."""
    d = np.asarray(d, np.float64)
    if unit == "percent":
        return np.tan(np.radians(np.clip(d, -89.999, 89.999))) * 100.0
    if unit == "ratio":
        return np.tan(np.radians(np.clip(d, -89.999, 89.999)))
    if unit == "gradians":
        return d / 0.9
    return d


def _slope_unit_limit(unit):
    """The largest magnitude a slope value can have in ``unit`` (None when
    the unit is unbounded: percent and rise:run)."""
    return {"degrees": 90.0, "gradians": 100.0}.get(unit)


def slope_unit_mismatch(in_raster, slope_units):
    """(detected, chosen) when the raster's geoprocessing history records a
    slope unit other than the one chosen, else None. The history only ever
    says degrees or percent (what Esri's Slope and Surface Parameters
    write), so a rise:run or gradian choice on such a raster counts as a
    mismatch too. Best effort; never raises."""
    try:
        detected = _detect_slope_units(in_raster)
    except Exception:
        detected = None
    chosen = _slope_unit_key(slope_units)
    if detected and detected != chosen:
        return detected, chosen
    return None


def unit_mismatch_text(detected, slope_units):
    """The warning shown in the dialog and written to the run report when
    the chosen unit disagrees with the raster's geoprocessing history."""
    return ("The raster's geoprocessing history records its values as {}, "
            "but the unit chosen is '{}'. The values are read as {}, as "
            "chosen; make sure that is what you want.".format(
                "percent slope" if detected == "percent" else "degrees",
                slope_units, _slope_unit_word(_slope_unit_key(slope_units))))


def run_extract_slope_points(in_points, in_raster, out_points,
                             interpolate=False, field_name="Slope",
                             slope_units=SLOPE_UNITS_Z[0], messages=None,
                             in_place=False, from_dem=False,
                             dem_method=None, window_size=3,
                             elev_units="Meters"):
    """Extract the slope at each point of ``in_points`` from a slope raster
    -- or, with ``from_dem``, derive it from a DEM (see
    ``aspect_tools.derive_from_dem``: Horn's 3 x 3 method, the one Esri's
    Slope tool uses, or a least-squares quadratic over ``window_size``
    cells, with the DEM's elevations in ``elev_units``; ``slope_units`` is
    then the OUTPUT unit) -- into a new field -- of a copied point feature
    class, or, with ``in_place``, of the input feature class itself -- like
    Esri's Extract Values to Points, with no Spatial Analyst, and with the
    interpolation done on the slope ANGLES.

    ``interpolate`` False: the value of the cell each point falls in, exactly
    as stored, in the raster's own unit. True: a bilinear interpolation of
    the four surrounding cell centers computed on the slope angles -- each
    neighbor is converted to degrees first (arctangent for percent and
    rise:run, a rescaling for gradians), the weighted mean is taken, and the
    result is converted back to the raster's unit. Averaging raw percent
    values would bias the result steep, because percent slope is the
    tangent of the angle, a convex function (the Slope Zonal Statistics
    rule). NoData neighbors, and neighbors outside the valid range for the
    unit (beyond +/-90 degrees or +/-100 gon), are left out and the weights
    renormalized. NoData / outside-the-raster points get NULL in a
    geodatabase or -9999 in a shapefile.

    ``slope_units`` is one of ``SLOPE_UNITS_Z``; when the raster's
    geoprocessing history records a different unit the run warns and goes
    on with the unit chosen. In place, the layer's selection and definition
    query decide which points get a value; the other rows stay NULL.

    Returns the actual field name created (sanitized/uniquified)."""
    import arcpy
    import aspect_tools as _at      # lazy: aspect_tools imports this module
    unit = _slope_unit_key(slope_units)
    uw = _slope_unit_word(unit)
    lim = _slope_unit_limit(unit)
    from_dem = bool(from_dem)
    dem_method = dem_method or _at.DEM_METHODS[0]
    n_win = 3 if _at.dem_method_key(dem_method) == "horn" else int(window_size or 3)

    _mh.begin_phases([
        ("Adding the field to the input features..." if in_place
         else "Copying the point features...", 2),
        ("Reading the DEM..." if from_dem else "Reading the slope raster...", 1),
        ("Computing slope from the DEM at the points..." if from_dem
         else "Sampling slope at the points...", 4),
        ("Writing values and field metadata...", 2),
    ])
    target, path, fld, null_ok = _at._point_target(
        in_points, out_points, field_name, "Slope", in_place, messages)

    _mh.phase_advance("Reading the DEM..." if from_dem
                      else "Reading the slope raster...")
    w = _at._points_in_raster_window(target, in_raster, messages,
                                     "DEM" if from_dem else "slope",
                                     margin=(n_win // 2 + 2) if from_dem else 2)
    arr, valid, n_pts = w["arr"], w["valid"], w["n_pts"]
    xs, ys = w["xs"], w["ys"]
    x0w, y0w, csx, csy = w["x0w"], w["y0w"], w["csx"], w["csy"]

    dem_info = None
    mismatch_note = None
    if from_dem:
        # slope in degrees at the cells the sampling step needs, then into
        # the chosen output unit (the interpolation below goes back to
        # degrees, so this round trip is exact)
        deg_arr, valid, dem_info = _at.derive_from_dem(
            w, "slope", dem_method, n_win, elev_units, interpolate, messages)
        arr = np.where(valid, _slope_from_deg_array(
            np.where(valid, deg_arr, 0.0), unit), np.nan).astype(np.float32)
        _say(messages, "Slope computed from the DEM with {} (elevation units "
                       "{}), in {}.".format(dem_info["method_text"],
                                            elev_units, uw))
    else:
        # The unit in the raster's history against the unit chosen.
        mismatch = slope_unit_mismatch(in_raster, slope_units)
        if mismatch:
            mismatch_note = unit_mismatch_text(mismatch[0], slope_units)
            _warn(messages, mismatch_note)

    # Cells outside the valid range for the unit (a percent raster read as
    # degrees shows up here; never for values derived from a DEM).
    oor = np.zeros(arr.shape, bool)
    if lim is not None and arr.size and not from_dem:
        oor = valid & (np.abs(arr) > lim)
    n_oor = int(np.count_nonzero(oor))
    if n_oor:
        _warn(messages,
              "{:,} cell(s) in the sampled area hold values outside the "
              "valid range for {} (beyond +/-{:g}) -- this may not be a "
              "slope raster in that unit. {}".format(
                  n_oor, uw, lim,
                  "Such cells are left out of the interpolation."
                  if interpolate else
                  "Points on such cells receive the stored value anyway."))

    _mh.phase_advance("Computing slope from the DEM at the points..."
                      if from_dem else "Sampling slope at the points...")
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
            ok = inside & valid[ris, cis]
            vals[ok] = arr[ris[ok], cis[ok]].astype(np.float64)
        else:
            # Bilinear on the ANGLES of the 4 surrounding cell centers;
            # NoData and out-of-range neighbors excluded, weights
            # renormalized over the rest.
            usable = valid & ~oor
            deg = _slope_to_deg(np.where(usable, arr, 0.0).astype(np.float64),
                                unit)
            fx = colf - 0.5
            fy = rowf - 0.5
            c0 = np.floor(fx).astype(np.int64)
            r0 = np.floor(fy).astype(np.int64)
            tx = (fx - c0).astype(np.float64)
            ty = (fy - r0).astype(np.float64)
            dsum = np.zeros(n_pts)
            wsum = np.zeros(n_pts)
            for dr, dc, wt in ((0, 0, (1 - tx) * (1 - ty)),
                               (0, 1, tx * (1 - ty)),
                               (1, 0, (1 - tx) * ty),
                               (1, 1, tx * ty)):
                cc = c0 + dc
                rr = r0 + dr
                inside = ((cc >= 0) & (cc < Ww) & (rr >= 0) & (rr < Hw)
                          & (wt > 0))
                ccs = np.clip(cc, 0, Ww - 1)
                rrs = np.clip(rr, 0, Hw - 1)
                ok = inside & usable[rrs, ccs]
                dsum += np.where(ok, wt * deg[rrs, ccs], 0.0)
                wsum += np.where(ok, wt, 0.0)
            has = wsum > 1e-12
            mean_deg = np.where(has, dsum / np.where(has, wsum, 1.0), np.nan)
            vals = np.where(has, _slope_from_deg_array(mean_deg, unit),
                            np.nan)

    _mh.phase_advance("Writing values and field metadata...")
    n_nodata, _unused = _at._write_point_values(target, fld, w["oids"], vals,
                                                null_ok)

    _say(messages, "Sampled {:,} point(s): {:,} with a value, {:,} on NoData "
                   "or outside the raster ({}).".format(
                       n_pts, n_pts - n_nodata, n_nodata,
                       "NULL" if null_ok else "-9999"))
    if interpolate:
        _say(messages, "Values are in {}: each point's value is a bilinear "
                       "interpolation of the four surrounding cell centers "
                       "computed on the slope ANGLES and converted back to "
                       "{}.".format(uw, uw))
    elif from_dem:
        _say(messages, "Values are the slopes of the cells the points fall "
                       "in, in {}.".format(uw))
    else:
        _say(messages, "Values are the stored values of the cells the points "
                       "fall in, in {}.".format(uw))
    if in_place:
        beyond = _at._rows_beyond(target, path, n_pts)
        if beyond:
            _say(messages, "The field was added to the whole feature class; "
                           "{:,} feature(s) outside the layer's selection or "
                           "definition query were left NULL.".format(beyond))

    # Field-level metadata.
    try:
        import tool_metadata
        method = ("Bilinear interpolation of the four surrounding cell "
                  "centers computed on the slope angles (each cell converted "
                  "to degrees, averaged, converted back; NoData and "
                  "out-of-range neighbors excluded)" if interpolate
                  else "Value of the raster cell containing the point")
        if dem_info is not None:
            source_note = (
                "Source DEM: {}. Slope was derived from the DEM by {} over "
                "true ground distances ({}); elevation units {}.".format(
                    str(in_raster), dem_info["method_text"],
                    _hs.ground_note_for_metadata(dem_info), elev_units))
            unit_note = "Slope at the point, in {} ('{}')".format(uw, slope_units)
        else:
            source_note = "Source raster: {}.{}".format(
                str(in_raster), " Warning at run time: " + mismatch_note
                if mismatch_note else "")
            unit_note = ("Slope at the point, in {} (the unit of the source "
                         "raster, '{}')".format(uw, slope_units))
        tool_metadata.set_entity_and_fields(
            arcpy.Describe(path).catalogPath,
            fields=[{"name": fld, "description":
                     "{}; {} = NoData or outside the raster. Method: {}. {}"
                     .format(unit_note, "NULL" if null_ok else "-9999",
                             method, source_note)}],
            messages=messages)
    except Exception:
        pass
    _mh.end_phases()
    _say(messages, "Extract Slope Values to Points complete: {}".format(path))
    return fld
