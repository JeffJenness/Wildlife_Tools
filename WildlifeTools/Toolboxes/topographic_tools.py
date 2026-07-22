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


def _surface_row_geometry(gt, height, sr):
    """Per-row geometry for the 8-triangle method: dict of arrays (length
    ``height``) of SQUARED half-distances -- vA2/hA2/dA2/h2/dB2/hB2/vB2 --
    plus ``pix_m2`` (true planimetric cell area, m^2).

    Projected rasters use constant planar values; geographic rasters compute
    spheroidal ECEF chords between points offset half a cell from the
    column-0 cell center (the legacy table exactly), with the cell area as
    the sum of the eight 3-D corner triangles.
    """
    geographic = (sr.type == "Geographic")
    if not geographic:
        mpu = float(sr.metersPerUnit or 1.0)
        cw = gt[1] * mpu
        ch = abs(gt[5]) * mpu
        const = np.full(height, 1.0)
        hx2 = (cw / 2.0) ** 2
        hy2 = (ch / 2.0) ** 2
        d2 = hx2 + hy2
        return {"vA2": const * hy2, "hA2": const * hx2, "dA2": const * d2,
                "h2": const * hx2, "dB2": const * d2, "hB2": const * hx2,
                "vB2": const * hy2, "pix_m2": const * (cw * ch)}, False

    a = float(sr.semiMajorAxis or 6378137.0)
    b = float(sr.semiMinorAxis or 6356752.31424518)
    halfx = gt[1] / 2.0
    halfy = abs(gt[5]) / 2.0
    x0 = gt[0] + 0.5 * gt[1]
    rows = np.arange(height, dtype=np.float64)
    y0 = gt[3] + (rows + 0.5) * gt[5]

    above = _hs._ecef(x0, y0 + halfy, a, b)
    below = _hs._ecef(x0, y0 - halfy, a, b)
    left = _hs._ecef(x0 - halfx, y0, a, b)
    right = _hs._ecef(x0 + halfx, y0, a, b)
    nw = _hs._ecef(x0 - halfx, y0 + halfy, a, b)
    ne = _hs._ecef(x0 + halfx, y0 + halfy, a, b)
    sw = _hs._ecef(x0 - halfx, y0 - halfy, a, b)
    se = _hs._ecef(x0 + halfx, y0 - halfy, a, b)
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
            "vB2": d2(focal, below), "pix_m2": pix}, True


def _heron(l1, l2, l3):
    s = (l1 + l2 + l3) / 2.0
    prod = s * (s - l1) * (s - l2) * (s - l3)
    return np.sqrt(np.maximum(prod, 0.0))


def _resolve_output(path, messages):
    """Shared no-more-GRID output resolution (see mahalanobis.py)."""
    return _mh.resolve_raster_output(path, messages)


def _make_output(gdal, path, gt, proj, width, height):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, width, height, 1, gdal.GDT_Float32,
                    options=["TILED=YES", "COMPRESS=LZW"])
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
        try:
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
                     corner_method=None, messages=None):
    """Compute any combination of the Surface Area, Surface Ratio, and Flat
    Area rasters (at least one required). See the module docstring."""
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
    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    in_nodata = band.GetNoDataValue()
    sr = arcpy.Describe(in_raster).spatialReference

    geom, geographic = _surface_row_geometry(gt, height, sr)
    if geographic:
        elev_mpu = 1.0                  # legacy: geographic DEMs are meters
        _hs._msg(messages, "Geographic DEM: half-cell distances and true "
                           "cell areas computed per row on the spheroid "
                           "(a = {:.1f} m, b = {:.1f} m).".format(
                               float(sr.semiMajorAxis),
                               float(sr.semiMinorAxis)))
    else:
        elev_mpu = _hs._ELEV_METERS.get(elev_units, 1.0)
        _hs._msg(messages, "Projected DEM: cell area {:.4f} sq m; elevation "
                           "units = {}.".format(
                               float(geom["pix_m2"][0]), elev_units))
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
        pad = np.where(valid, pad * elev_mpu, 0.0)

        out_arrs = {k: np.full((rows, width), NODATA_OUT, dtype=np.float32)
                    for k in outputs}
        rr_all = slice(yoff, yoff + rows)
        if "flat" in outputs:
            # Flat area is valid for EVERY cell (legacy behavior).
            flat_vals = (geom["pix_m2"][rr_all] * mult_flat)[:, None]
            out_arrs["flat"][:] = np.repeat(
                flat_vals, width, axis=1).astype(np.float32)
            outputs["flat"]["stats"].add(
                np.repeat(geom["pix_m2"][rr_all] * mult_flat, width))

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

            rr = slice(r0, r1)
            vA2 = geom["vA2"][rr][:, None]
            hA2 = geom["hA2"][rr][:, None]
            dA2 = geom["dA2"][rr][:, None]
            h2 = geom["h2"][rr][:, None]
            dB2 = geom["dB2"][rr][:, None]
            hB2 = geom["hB2"][rr][:, None]
            vB2 = geom["vB2"][rr][:, None]
            pixm2 = geom["pix_m2"][rr][:, None]

            # Interpolated boundary-point elevations. Edge midpoints are the
            # 2-cell means in both methods; corners follow the chosen method.
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
            sA = np.sqrt(dA2 + (zA - C_) ** 2)
            sB = np.sqrt(vA2 + (zB - C_) ** 2)
            sC = np.sqrt(dA2 + (zC - C_) ** 2)
            sD = np.sqrt(h2 + (zD - C_) ** 2)
            sF = np.sqrt(h2 + (zF - C_) ** 2)
            sG = np.sqrt(dB2 + (zG - C_) ** 2)
            sH = np.sqrt(vB2 + (zH - C_) ** 2)
            sI = np.sqrt(dB2 + (zI - C_) ** 2)

            # Eight outer legs (between adjacent boundary points).
            oAB = np.sqrt(hA2 + (zA - zB) ** 2)
            oBC = np.sqrt(hA2 + (zC - zB) ** 2)
            oAD = np.sqrt(vA2 + (zA - zD) ** 2)
            oCF = np.sqrt(vA2 + (zC - zF) ** 2)
            oDG = np.sqrt(vB2 + (zG - zD) ** 2)
            oFI = np.sqrt(vB2 + (zI - zF) ** 2)
            oGH = np.sqrt(hB2 + (zG - zH) ** 2)
            oHI = np.sqrt(hB2 + (zI - zH) ** 2)

            area = (_heron(sA, sB, oAB) + _heron(sC, sB, oBC) +
                    _heron(sA, sD, oAD) + _heron(sC, sF, oCF) +
                    _heron(sD, sG, oDG) + _heron(sF, sI, oFI) +
                    _heron(sG, sH, oGH) + _heron(sH, sI, oHI))

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

    # Finalize and deliver each requested output.
    geo_note = ("Cell distances and areas were computed per row on the "
                "spheroid (geodesic method) because the DEM is in a "
                "geographic coordinate system." if geographic else
                "Planar cell dimensions were used (projected DEM).")
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
                c=corner_note, g=geo_note),
            description=description, tags=tags)
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


def _row_gradient_geometry(gt, height, sr, elev_mpu):
    """Per-row Horn-gradient geometry (NOT half/squared, unlike the surface-
    area table): a dict of arrays (length ``height``) with keys hA/h/hB
    (horizontal spans of the row above/at/below) and vA/vB (vertical spans up/
    down), all in meters -- exactly the table Enhanced Hillshade uses.
    Returns (geom, geographic, zscale)."""
    geographic = (sr.type == "Geographic")
    zscale = float(elev_mpu)
    if geographic:
        a = float(sr.semiMajorAxis or 6378137.0)
        b = float(sr.semiMinorAxis or 6356752.31424518)
        geom = _hs._geographic_row_distances(gt, height, a, b)
    else:
        mpu = float(sr.metersPerUnit or 1.0)
        cw = gt[1] * mpu
        ch = abs(gt[5]) * mpu
        const = np.full(height, 1.0)
        geom = {"hA": const * cw, "h": const * cw, "hB": const * cw,
                "vA": const * ch, "vB": const * ch}
    return geom, geographic, zscale


def _unit_normals(z, valid, geom):
    """Unit normal vectors (nx, ny, nz) for the WHOLE grid ``z`` (meters,
    already z-scaled), via the Horn gradient: n = (-dz/dx, -dz/dy, 1) /
    ||...||. This is the same vector Sappington's slope/aspect trig
    decomposition produces (a global rotation of coordinate convention
    changes no vector's length, and VRM depends only on the length of the
    SUM of these vectors) -- but reuses the toolbox's tested, geodesic-correct
    per-row gradient. Returns (nx, ny, nz, ok) full-grid arrays; ``ok`` is
    False wherever the cell's own 3x3 window touches invalid/out-of-bounds
    data (NaN elsewhere in nx/ny/nz)."""
    H, W = z.shape
    zp = np.full((H + 2, W + 2), np.nan)
    zp[1:-1, 1:-1] = z
    vp = np.zeros((H + 2, W + 2), dtype=bool)
    vp[1:-1, 1:-1] = valid

    NW, N_, NE = zp[:-2, :-2], zp[:-2, 1:-1], zp[:-2, 2:]
    W_, C_, E_ = zp[1:-1, :-2], zp[1:-1, 1:-1], zp[1:-1, 2:]
    SW, S_, SE = zp[2:, :-2], zp[2:, 1:-1], zp[2:, 2:]

    hA = geom["hA"][:, None]
    h = geom["h"][:, None]
    hB = geom["hB"][:, None]
    vA = geom["vA"][:, None]
    vB = geom["vB"][:, None]

    with np.errstate(invalid="ignore"):
        dzdx = ((NE + 2.0 * E_ + SE) - (NW + 2.0 * W_ + SW)) / \
               (2.0 * hA + 4.0 * h + 2.0 * hB)
        dzdy = ((SW + 2.0 * S_ + SE) - (NW + 2.0 * N_ + NE)) / (4.0 * vA + 4.0 * vB)
        norm = np.sqrt(dzdx * dzdx + dzdy * dzdy + 1.0)
        nx = -dzdx / norm
        ny = -dzdy / norm
        nz = 1.0 / norm

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
    from scipy.signal import fftconvolve

    fields = [np.where(ok, nx, 0.0), np.where(ok, ny, 0.0),
              np.where(ok, nz, 0.0), ok.astype(np.float64)]
    outs = [np.zeros_like(f) for f in fields]
    for k, rows in _hs._row_kernel_groups(rx_rows, ry, "Average"):
        for f, out in zip(fields, outs):
            c = fftconvolve(f, k, mode="same")
            out[rows] = c[rows]
    return outs[0], outs[1], outs[2], outs[3]


def run_vrm(in_raster, out_raster, elev_units="Meters", radius=1.5,
           radius_units="Cells", messages=None):
    """Vector Ruggedness Measure (VRM): the 3-D analog of Fisher's circular
    variance (1993, eq. 2.10-2.11) applied to unit vectors orthogonal to the
    terrain surface, after Sappington et al. (2007) and Hobson (1972). See
    the module docstring.

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
    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 3 or height < 3:
        raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    in_nodata = band.GetNoDataValue()
    sr = arcpy.Describe(in_raster).spatialReference

    elev_mpu = 1.0 if sr.type == "Geographic" else _hs._ELEV_METERS.get(elev_units, 1.0)
    geom, geographic, zscale = _row_gradient_geometry(gt, height, sr, elev_mpu)
    if geographic:
        _hs._msg(messages, "Geographic DEM: gradients computed per row on the "
                           "spheroid.")
    else:
        _hs._msg(messages, "Projected DEM: cell = {:.3f} x {:.3f} m; "
                           "elevation units = {}.".format(
                               float(geom["h"][0]), float(geom["vA"][0]),
                               elev_units))

    if radius_units == "Cells":
        rx_rows = np.full(height, radius)
        ry = radius
        _hs._msg(messages, "VRM radius: {:g} cells.".format(radius))
    else:
        radius_m = radius * _RADIUS_METERS[radius_units]
        if geographic:
            rx_rows = radius_m / np.maximum(geom["h"], 1e-9)
            ry = radius_m / max(float(np.mean(geom["vA"])), 1e-9)
            _hs._msg(messages, "VRM radius: {:g} {} = {:,.1f} m, sized per row "
                               "on the spheroid ({:.1f}-{:.1f} cells across, "
                               "{:.1f} cells down).".format(
                                   radius, radius_units.lower(), radius_m,
                                   float(rx_rows.min()), float(rx_rows.max()),
                                   ry))
        else:
            rx_rows = np.full(height, radius_m / geom["h"][0])
            ry = radius_m / geom["vA"][0]
            _hs._msg(messages, "VRM radius: {:g} {} = {:,.1f} m = {:.1f} x "
                               "{:.1f} cells.".format(
                                   radius, radius_units.lower(), radius_m,
                                   float(rx_rows[0]), ry))

    z = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(z)
    if in_nodata is not None:
        valid &= (z != in_nodata)
    z = np.where(valid, z * zscale, 0.0)
    src = None
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
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
                     e=("Meters (geographic DEM)" if geographic else elev_units)),
        description=(
            "Vector Ruggedness Measure (VRM) raster: a topographic "
            "roughness index (Sappington et al. 2007) that quantifies the "
            "3-D dispersion of the unit vectors orthogonal to the terrain "
            "surface within a neighborhood of each cell -- the spherical "
            "analog of circular variance (Fisher 1993). Values range from "
            "0 (a flat and/or uniformly sloped neighborhood) to 1 "
            "(maximally rugged); unlike slope alone, VRM distinguishes "
            "steep-but-even terrain from steep, broken terrain. " +
            _VRM_CITATION),
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
