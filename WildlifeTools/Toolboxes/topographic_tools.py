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


def _tri_row_cell_dims(gt, height, sr):
    """(cw_row, ch_row): per-row horizontal and vertical cell dimensions in
    meters (arrays length ``height``), for the Slope method's neighbor
    distances. Constant on projected rasters; per-row on the spheroid for a
    geographic DEM (the neighbor distance a diagonal cell away then correctly
    grows toward the poles). The vertical span (degrees of latitude) is very
    nearly constant, so the focal row's value is used for the small vertical
    offsets of a local window -- the same per-row approximation the other
    tools use for geographic neighborhoods."""
    if sr.type == "Geographic":
        a = float(sr.semiMajorAxis or 6378137.0)
        b = float(sr.semiMinorAxis or 6356752.31424518)
        g = _hs._geographic_row_distances(gt, height, a, b)
        return g["h"], g["vA"]
    mpu = float(sr.metersPerUnit or 1.0)
    cw = np.full(height, abs(gt[1]) * mpu)
    ch = np.full(height, abs(gt[5]) * mpu)
    return cw, ch


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
    # Per-row cell dimensions in meters -- for the circular ground-distance
    # neighborhood and for the Slope method's neighbor distances.
    cw_row, ch_row = _tri_row_cell_dims(gt, height, sr)

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
        min_cell = max(min(float(cw_row.min()), float(ch_row.min())), 1e-9)
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
                               "geodesic per row, " if geographic else "",
                               2 * big_r + 1,
                               "Elevation assumed meters (geographic DEM)."
                               if geographic else
                               "Elevation units: {}.".format(elev_units)))

    z = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(z)
    if in_nodata is not None:
        valid &= (z != in_nodata)
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
                d_row = (np.sqrt((dj * cw_row) ** 2 + (di * ch_row) ** 2)
                         if need_d else None)      # meters, per row
                if cells_mode:
                    if di * di + dj * dj > radius * radius:
                        continue
                    row_mask = None                # every row in the circle
                else:
                    row_in = d_row <= radius_m
                    if not row_in.any():
                        continue
                    row_mask = row_in[:, None]
                nz = zp[R + di:R + di + height, R + dj:R + dj + width]
                nv = vp[R + di:R + di + height, R + dj:R + dj + width]
                member = nv if row_mask is None else (nv & row_mask)
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
                        acc += np.where(pair, (dz / d_row[:, None]) ** 2, 0.0)
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
#                       Chaudhuri 1994; improved box height Li/Du/Sun 2009,
#                       Liu et al. 2014). The WEAKEST/noisiest local estimator
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
    "(https://doi.org/10.1109/21.259692). Improved box height / count: Li, "
    "J., Q. Du, and C. Sun. 2009. An improved box-counting method for image "
    "fractal dimension estimation. Pattern Recognition 42(11):2460-2469; and "
    "Liu, Y., et al. 2014. An improved differential box counting method to "
    "estimate fractal dimensions of gray-level images. Journal of Visual "
    "Communication and Image Representation 25(5):1102-1111.")
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


def _fd_variation(z, wsize, p, H, W):
    hmax = max(2, wsize // 3)
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
    box_sizes = [s for s in (2, 3, 4, 6, 8, 12, 16, 24)
                 if s <= wsize // 2 and wsize // s >= 2]
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


def run_fractal_dimension(in_raster, out_raster, method=None, window_size=25,
                          elev_units="Meters", messages=None):
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
    if is_tpm and len(_fd_divisor_steps(wsize)) < 3:
        raise arcpy.ExecuteError(
            "The triangular prism method needs several step sizes that divide "
            "the window evenly, so (window size minus 1) must have several "
            "whole-number factors; {} does not. You do not enter the step "
            "sizes -- the tool derives them; just pick a good window size. "
            "Try 13, 17, 25, 33, 37, 49, or 61.".format(wsize))
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

    z = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(z)
    if in_nodata is not None:
        valid &= (z != in_nodata)
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
        Dblk = _fd_variation(z, wsize, 2.0, height, width)
    elif is_mado:
        Dblk = _fd_variation(z, wsize, 1.0, height, width)
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
                 "window {w} x {w} cells; elevation units {e}.").format(
                     src=str(in_raster), m=method, w=wsize,
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
# also keeps SCA > 0 so the log is defined; eps (default 0.0001, user set) is
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
# stuck), planar on projected DEMs or geodesic if the user asks (and always
# geodesic -- spheroidal per row -- on geographic DEMs).

SLOPE_METHODS = ["Planar", "Geodesic"]

_TWI_CIT_TWI = (
    "Beven, K. J., and M. J. Kirkby. 1979. A physically based, variable "
    "contributing area model of basin hydrology. Hydrological Sciences "
    "Bulletin 24(1):43-69. https://doi.org/10.1080/02626667909491834")
_TWI_CIT_FILL = (
    "Barnes, R., C. Lehman, and D. Mulla. 2014. Priority-flood: an optimal "
    "depression-filling and watershed-labeling algorithm for digital "
    "elevation models. Computers & Geosciences 62:117-127. "
    "https://doi.org/10.1016/j.cageo.2013.04.024")
_TWI_CIT_D8 = (
    "O'Callaghan, J. F., and D. M. Mark. 1984. The extraction of drainage "
    "networks from digital elevation data. Computer Vision, Graphics, and "
    "Image Processing 28(3):323-344. "
    "https://doi.org/10.1016/S0734-189X(84)80011-0")
_TWI_CITATION = _TWI_CIT_TWI


def _twi_geodesic_projected_geom(gt, width, height, proj):
    """Per-row TRUE-GROUND cell distances for a PROJECTED raster whose slope
    the user wants computed geodesically: unproject the cell centers of a
    representative (raster-center) column to lon/lat with GDAL's osr, then use
    the same spheroidal-ECEF distances as the geographic path.  Returns the
    hA/h/hB/vA/vB dict (meters).  Corrects the projection's scale distortion,
    which is the only meaningful planar-vs-geodesic difference at 3x3 scale."""
    from osgeo import osr
    ps = osr.SpatialReference()
    ps.ImportFromWkt(proj)
    gs = ps.CloneGeogCS()
    for s in (ps, gs):
        try:
            s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        except Exception:
            pass
    # spheroid from the DEM's own datum (arcpy reports 0 for a projected SR)
    a = float(gs.GetSemiMajor()) or 6378137.0
    b = float(gs.GetSemiMinor()) or 6356752.31424518
    ct = osr.CoordinateTransformation(ps, gs)
    cellw = gt[1]
    cellh = abs(gt[5])
    xc = gt[0] + (0.5 * width) * gt[1]          # raster-center column
    rows = np.arange(height, dtype=np.float64)
    y0 = gt[3] + (rows + 0.5) * gt[5]
    xcol = np.full(height, xc)

    def ecef(xs, ys):
        pts = ct.TransformPoints(list(zip(xs.tolist(), ys.tolist())))
        lon = np.fromiter((p[0] for p in pts), np.float64, len(pts))
        lat = np.fromiter((p[1] for p in pts), np.float64, len(pts))
        return _hs._ecef(lon, lat, a, b)

    above = ecef(xcol, y0 + cellh)
    below = ecef(xcol, y0 - cellh)
    right = ecef(xcol + cellw, y0)
    nw = ecef(xcol - cellw, y0 + cellh)
    ne = ecef(xcol + cellw, y0 + cellh)
    sw = ecef(xcol - cellw, y0 - cellh)
    se = ecef(xcol + cellw, y0 - cellh)
    focal = tuple((nw[i] + ne[i] + sw[i] + se[i]) / 4.0 for i in range(3))
    return {"vA": _hs._dist3(focal, above), "hA": _hs._dist3(above, ne),
            "h": _hs._dist3(focal, right), "vB": _hs._dist3(focal, below),
            "hB": _hs._dist3(below, se)}


def _twi_tanbeta(z, valid, geom):
    """tan(beta) = Horn gradient magnitude of ``z`` (meters, z-scaled) over the
    per-row geometry ``geom`` (meters).  NoData neighbors fall back to the
    focal value so the gradient is defined for every valid cell."""
    H, W = z.shape
    zp = np.full((H + 2, W + 2), np.nan)
    zp[1:-1, 1:-1] = np.where(valid, z, np.nan)
    NW, N_, NE = zp[:-2, :-2], zp[:-2, 1:-1], zp[:-2, 2:]
    W_, C_, E_ = zp[1:-1, :-2], zp[1:-1, 1:-1], zp[1:-1, 2:]
    SW, S_, SE = zp[2:, :-2], zp[2:, 1:-1], zp[2:, 2:]
    ctr = np.where(valid, z, np.nan)
    parts = [NW, N_, NE, W_, E_, SW, S_, SE]
    parts = [np.where(np.isnan(p), ctr, p) for p in parts]
    NW, N_, NE, W_, E_, SW, S_, SE = parts
    hA = geom["hA"][:, None]; h = geom["h"][:, None]; hB = geom["hB"][:, None]
    vA = geom["vA"][:, None]; vB = geom["vB"][:, None]
    with np.errstate(invalid="ignore"):
        dzdx = ((NE + 2.0 * E_ + SE) - (NW + 2.0 * W_ + SW)) / \
               (2.0 * hA + 4.0 * h + 2.0 * hB)
        dzdy = ((SW + 2.0 * S_ + SE) - (NW + 2.0 * N_ + NE)) / \
               (4.0 * vA + 4.0 * vB)
        tanb = np.hypot(dzdx, dzdy)
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


def _twi_receivers(filled, order, valid, geom):
    """D8 receiver (flat downstream index, -1 = terminal) for every cell:
    steepest descent on the FILLED surface; flats route to the valid neighbor
    with the smallest flood ``order`` (toward the spill).  ``geom`` supplies
    the true per-row ground distances (planar projected / geodesic
    geographic)."""
    H, W = filled.shape
    fp = np.full((H + 2, W + 2), np.inf)
    fp[1:-1, 1:-1] = np.where(valid, filled, np.inf)
    BIG = np.iinfo(np.int64).max
    op = np.full((H + 2, W + 2), BIG, dtype=np.int64)
    op[1:-1, 1:-1] = np.where(valid, order, BIG)
    fc = fp[1:-1, 1:-1]
    oc = op[1:-1, 1:-1]
    h = geom["h"]; vU = geom["vA"]; vB = geom["vB"]
    dU = np.hypot(h, vU); dB = np.hypot(h, vB)
    dirs = [(0, 1, h), (0, -1, h), (-1, 0, vU), (1, 0, vB),
            (-1, 1, dU), (-1, -1, dU), (1, 1, dB), (1, -1, dB)]

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

    flat = valid & (~has)
    best_o = oc.copy()
    fdr = np.zeros((H, W), np.int64)
    fdc = np.zeros((H, W), np.int64)
    fhas = np.zeros((H, W), bool)
    for dr, dc, _dist in dirs:
        no = op[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
        cand = flat & (no < best_o)
        best_o = np.where(cand, no, best_o)
        fdr = np.where(cand, dr, fdr)
        fdc = np.where(cand, dc, fdc)
        fhas |= cand
    take = flat & fhas
    bdr = np.where(take, fdr, bdr)
    bdc = np.where(take, fdc, bdc)
    has = has | take

    rows = np.arange(H)[:, None]
    cols = np.arange(W)[None, :]
    rr = rows + bdr
    cc = cols + bdc
    recv = np.where(has & valid, rr * W + cc, -1).astype(np.int64)
    return recv.ravel()


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
    finally:
        src = None
        if copy:
            try:
                arcpy.management.Delete(copy)
            except Exception:
                pass
    return arr, nod


def run_twi(in_raster, out_raster, elev_units="Meters", slope_method="Planar",
            slope_epsilon=0.0001, in_filled_dem=None, in_flow_accumulation=None,
            out_filled_dem=None, out_flow_accumulation=None, messages=None):
    """Topographic Wetness Index (TWI / CTI): TWI = ln(SCA / (tan(beta) +
    eps)).  See the section header for the full method and citations."""
    gdal = _mh._gdal()
    eps = float(slope_epsilon)
    if eps <= 0:
        raise arcpy.ExecuteError("The slope epsilon must be positive.")
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
                   ("Tracing D8 flow directions...", 2),
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
    base_geom, _geo, zscale = _row_gradient_geometry(gt, height, sr, elev_mpu)
    # slope geometry: geodesic on geographic always; on projected, honor choice
    if geographic:
        slope_geom = base_geom
        slope_used = "Geodesic (spheroidal, per row)"
    elif str(slope_method) == "Geodesic":
        slope_geom = _twi_geodesic_projected_geom(gt, width, height, proj)
        slope_used = "Geodesic (true ground distances)"
    else:
        slope_geom = base_geom
        slope_used = "Planar"
    # planimetric ground cell size (m) per row for the specific catchment area
    sca_cell = np.sqrt(base_geom["h"] * base_geom["vA"])

    zraw = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(zraw)
    if in_nodata is not None:
        valid &= (zraw != in_nodata)
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
             "method: {}.".format(width, height, nvalid,
             "geographic" if geographic else "projected", slope_used))

    # ---- flow accumulation (supplied, or fill -> D8 -> accumulate) ----
    filled = None
    if have_fac:
        acc, _n = _twi_read_aligned(in_flow_accumulation, width, height,
                                    scratch, messages, "flow-accumulation")
        acc = np.where(np.isfinite(acc) & (acc >= 0), acc, 0.0)
        _hs._msg(messages, "Using supplied flow-accumulation raster (skipping "
                 "fill, flow direction, and accumulation).")
    else:
        fill_input = zraw
        if have_fill:
            farr, fnod = _twi_read_aligned(in_filled_dem, width, height,
                                           scratch, messages, "filled-DEM")
            fmask = np.isfinite(farr)
            if fnod is not None:
                fmask &= (farr != fnod)
            fill_input = np.where(fmask, farr, zraw)
            _hs._msg(messages, "Using supplied filled DEM as the routing "
                     "surface.")
        _mh.phase_advance("Filling sinks (priority-flood)...")
        filled, order = _twi_priority_flood(fill_input, valid, messages)
        _mh.phase_advance("Tracing D8 flow directions...")
        recv = _twi_receivers(filled, order, valid, base_geom)
        _mh.phase_advance("Accumulating flow...")
        acc = _twi_accumulate(recv, order, valid)
        del recv, order

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
             "cell size, slope epsilon {:g}.".format(
                 _TWI_CITATION, int(out_valid.sum()), eps))

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
                 "(flow accumulation + 1) x cell size, slope epsilon {e:g}, "
                 "slope method {sm}. Flow: {fl}.").format(
                     src=str(in_raster), e=eps, sm=slope_used,
                     fl=("supplied flow accumulation" if have_fac else
                         "internal priority-flood fill + D8")),
        description=(
            "Topographic Wetness Index (TWI), also called the Compound "
            "Topographic Index (CTI): ln( specific catchment area / (tan(slope)"
            " + epsilon) ). High values mark cells with large upslope "
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
            summary=("Created by Topographic Wetness Index from {src}: D8 flow "
                     "accumulation (upstream cell count, excl. self).").format(
                         src=str(in_raster)),
            description=("D8 flow accumulation (number of upstream cells "
                         "draining to each cell, excluding the cell itself), "
                         "from a priority-flood-filled DEM. " + _TWI_CIT_D8),
            tags=("flow accumulation, D8, upslope area, hydrology, topography, "
                  "Jenness Enterprises"),
            citation=_TWI_CIT_D8)
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
