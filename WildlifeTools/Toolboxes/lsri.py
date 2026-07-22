# -*- coding: utf-8 -*-
"""Land Surface Ruggedness Index (LSRI) tools for the Jenness Enterprises
toolbox.

Automates Beasom, S. L., E. P. Wiggers, and J. R. Giardino. 1983. A
technique for assessing land surface ruggedness. Journal of Wildlife
Management 47:1163-1166 -- who found terrain ruggedness closely tied to
the total length of contour lines crossing a fixed-size sample area (a
40-ha circle in their study). Beasom et al. measured that total length
directly with a tracing wheel for a validation subset, but for routine use
proposed a faster manual PROXY: a dot-grid transparency overlaid on a
printed topographic map, counting dot/contour-line intersections (their
"Land Surface Ruggedness Index") -- a shortcut that only made sense
because manually tracing every contour line was tedious. These tools
automate the tracing-wheel measurement itself (the more accurate of the
two methods Beasom et al. describe) over a moving circular neighborhood
instead, so the manual dot-grid shortcut is unnecessary.

Similar in spirit to Esri's Line Density tool (also a circular-neighborhood
line measure), but reports TOTAL LENGTH within the neighborhood directly
rather than length-per-unit-area (density); see each tool's help for the
exact relationship (Density x pi x radius^2 = total length, for the
un-weighted case). Implemented independently of Line Density, entirely
with GDAL + numpy + scipy -- no Spatial Analyst or 3D Analyst extension
required:

* Contour generation uses ``gdal.ContourGenerate`` directly (same
  Contour interval / Base contour / Z factor logic as Esri's own Contour
  tool -- see ``run_lsri_on_dem``), unsmoothed (matching Esri's own
  Contour tool, which has no smoothing option either -- and smoothing
  here would understate exactly the contour-line convolutedness this
  index is meant to measure).
* The neighborhood length sum is an EXACT segment-circle clip (closed
  form), not a rasterized approximation -- verified against
  arcpy.sa.LineDensity on a controlled test (a straight line vs. a sinuous
  line of the same footprint but ~28x the true length): both agree with
  true vector length to several significant figures; this hand-rolled
  version is exact since it never rasterizes the input geometry at all.

Two tools share the core engine below:

* ``run_lsri_on_dem`` ("LSRI on DEM") -- generates contours from a DEM on
  the fly and optionally saves them.
* ``run_lsri_on_contours`` ("LSRI on Contours") -- computes LSRI
  directly from an existing contour (or any polyline) feature class.
"""

import os
import math

import numpy as np
from scipy.spatial import cKDTree

import arcpy

import mahalanobis as _mh
import hillshade as _hs

RADIUS_UNITS = _hs.RADIUS_UNITS
_RADIUS_METERS = _hs._RADIUS_METERS
_STRIP_ROWS = _hs._STRIP_ROWS

LENGTH_UNITS = ["Meters", "Feet", "Kilometers", "Miles"]
_LENGTH_MULT = {"Meters": 1.0, "Feet": 1.0 / 0.3048,
               "Kilometers": 1.0 / 1000.0, "Miles": 1.0 / 1609.344}

NODATA_OUT = -9999.0

# Defense-in-depth sanity cap: a feature class or DEM/interval combination
# engineered (or simply malformed) to yield an absurd segment count would
# otherwise grow the segment lists in _polyline_segments/
# _contour_segments_from_band without bound and could exhaust memory. 5
# million segments is already far beyond any realistic contour/habitat
# dataset this tool is meant for.
_MAX_SEGMENTS = 5_000_000

# Memory safety valve for _RadiusLengthEngine.query(): caps how many
# (center, candidate-segment) pairs are materialized into flat arrays at
# once. A dense contour network can otherwise blow past available memory
# building these arrays in one shot for an entire strip's worth of
# centers -- see _RadiusLengthEngine.query docstring for the real crash
# this was found from. ~200-250 bytes live per pair across the several
# same-length arrays in play, so 10 million keeps peak usage in the
# low single-digit GiB regardless of raster size or contour density.
_MAX_PAIRS_PER_CHUNK = 10_000_000

CITATION = ("Beasom, S. L., E. P. Wiggers, and J. R. Giardino. 1983. A "
           "technique for assessing land surface ruggedness. Journal of "
           "Wildlife Management 47:1163-1166.")

LSRI_DESCRIPTION = (
    "Land Surface Ruggedness Index (LSRI): the total length of contour "
    "line within a circular neighborhood of each cell, after Beasom et "
    "al. (1983) -- who found terrain ruggedness closely tied to the "
    "amount of contour line crossing a fixed-size sample area. Higher "
    "values mean more, or more convoluted, contour line nearby, i.e. more "
    "rugged terrain; low, uniform terrain has little or no contour line "
    "nearby.")


# ---------------------------------------------------------------------------
# Core engine: exact total-length-within-radius. No Spatial Analyst -- true
# segment/circle clipping, not a rasterized approximation.
# ---------------------------------------------------------------------------

def _subdivide(p0s, p1s, max_len):
    """Split any segment longer than ``max_len`` into equal pieces no
    longer than it, so a KD-tree midpoint search with a bounded radius
    cannot miss a long segment that only clips the query circle near one
    end. Preprocessing over the INPUT geometry only (small), never called
    per output cell. Works in any number of dimensions (2 for planar map
    units, 3 for ECEF meters on geographic DEMs -- see
    ``_RadiusLengthEngine``).

    Fully vectorized (no per-segment Python loop) -- a per-segment loop
    here was found to dominate total run time on a real-world DEM with a
    fine contour interval over a large elevation range (hundreds of
    thousands of contour segments), since it ran even for the (usual)
    majority of segments that don't need subdividing at all."""
    ndim = p0s.shape[1] if len(p0s) else 2
    n = len(p0s)
    if n == 0:
        return p0s, p1s
    seg = p1s - p0s
    seg_len = np.sqrt(np.sum(seg * seg, axis=1))
    short = (seg_len <= max_len) | (seg_len == 0)
    out0 = [p0s[short]]
    out1 = [p1s[short]]

    long = ~short
    if np.any(long):
        n_pieces = np.ceil(seg_len[long] / max_len).astype(np.int64)
        total = int(n_pieces.sum())
        piece_starts = np.concatenate(([0], np.cumsum(n_pieces)[:-1]))
        local_idx = np.arange(total) - np.repeat(piece_starts, n_pieces)
        n_rep = np.repeat(n_pieces, n_pieces).astype(np.float64)
        t0 = (local_idx / n_rep)[:, None]
        t1 = ((local_idx + 1) / n_rep)[:, None]
        seg_of_piece = np.repeat(np.arange(len(n_pieces)), n_pieces)
        p0_long = p0s[long][seg_of_piece]
        d_long = seg[long][seg_of_piece]
        out0.append(p0_long + d_long * t0)
        out1.append(p0_long + d_long * t1)

    return (np.concatenate(out0, axis=0) if out0 else np.zeros((0, ndim)),
           np.concatenate(out1, axis=0) if out1 else np.zeros((0, ndim)))


class _RadiusLengthEngine(object):
    """Precomputes a KD-tree over subdivided line-segment pieces once, so
    repeated per-strip queries across an output raster don't rebuild it.
    ``query(centers)`` returns, for each center, the exact total segment
    length within ``radius`` -- a closed-form circle/segment clip summed
    over every candidate piece, fully vectorized (no per-cell Python loop).

    Dimension-agnostic (the underlying algebra is just dot products): pass
    2-D (x, y) points for a planar (projected) coordinate system, or 3-D
    ECEF (x, y, z) points -- meters on the reference spheroid -- for a
    geographic (lat/long) DEM, where a raw degree-space Euclidean distance
    would be meaningless. ECEF chord distance is indistinguishable from
    true geodesic distance at the neighborhood radii this tool is used at
    (a few hundred meters to a few kilometers) -- the difference is on the
    order of (radius^3 / earth_radius^2), a fraction of a millimeter even
    at a 10 km radius."""

    def __init__(self, p0s, p1s, radius):
        self.radius = float(radius)
        max_piece = max(self.radius, 1e-9)
        self.p0s, self.p1s = _subdivide(p0s, p1s, max_piece)
        # Coarse progress within the "Preparing contour segments..." phase
        # -- subdivision is now vectorized and fast even for millions of
        # segments, but cKDTree construction is an opaque scipy call with
        # no progress hook of its own, so this at least shows the bar
        # advance once subdivision is done and again once the tree (the
        # part that can't report finer detail) is built.
        _mh.phase_step(1, 2)
        if len(self.p0s):
            d = self.p1s - self.p0s
            self.max_piece_len = float(np.sqrt(np.sum(d * d, axis=1)).max())
            mids = (self.p0s + self.p1s) / 2.0
            self.tree = cKDTree(mids)
        else:
            self.tree = None
            self.max_piece_len = 0.0
        _mh.phase_step(2, 2)

    def query(self, centers):
        """``centers``: (M, ndim) array, same ndim as this engine's segments.

        Processes ``centers`` in cumulative-pair-count chunks (bounded by
        ``_MAX_PAIRS_PER_CHUNK``) rather than materializing every
        (center, candidate-segment) pair for all of ``centers`` at once.
        REAL CRASH this guards against: a rugged, geographic DEM with a
        fine (10 m) contour interval generated 4.2 million contour
        segments; a single 1024-row strip's worth of centers produced
        2.69 BILLION candidate pairs, requesting 60+ GiB for one of
        several same-sized arrays needed at once -- an unbounded-memory
        bug, not a slowness issue (the whole computation is otherwise
        fast once bounded)."""
        n_out = len(centers)
        result = np.zeros(n_out, dtype=np.float64)
        if self.tree is None or n_out == 0:
            return result
        query_r = self.radius + self.max_piece_len
        # workers=-1: use all available cores -- this query was previously
        # single-threaded (scipy's default).
        candidates = self.tree.query_ball_point(centers, r=query_r, workers=-1)
        counts = np.fromiter((len(c) for c in candidates), dtype=np.int64, count=n_out)
        total_pairs = int(counts.sum())
        if total_pairs == 0:
            return result

        cum = np.cumsum(counts)
        lo = 0
        while lo < n_out:
            prev = cum[lo - 1] if lo > 0 else 0
            hi = int(np.searchsorted(cum, prev + _MAX_PAIRS_PER_CHUNK, side="right"))
            hi = min(max(hi, lo + 1), n_out)   # always make progress

            chunk_counts = counts[lo:hi]
            chunk_total = int(chunk_counts.sum())
            if chunk_total > 0:
                cell_idx = np.repeat(np.arange(lo, hi, dtype=np.int64), chunk_counts)
                piece_idx = np.fromiter(
                    (j for c in candidates[lo:hi] for j in c),
                    dtype=np.int64, count=chunk_total)

                P0 = self.p0s[piece_idx]
                P1 = self.p1s[piece_idx]
                C = centers[cell_idx]
                d = P1 - P0
                f = P0 - C
                a = np.sum(d * d, axis=1)
                b = 2.0 * np.sum(f * d, axis=1)
                c = np.sum(f * f, axis=1) - self.radius * self.radius
                disc = b * b - 4.0 * a * c
                valid = disc >= 0.0
                sq = np.sqrt(np.maximum(disc, 0.0))
                with np.errstate(divide="ignore", invalid="ignore"):
                    t1 = (-b - sq) / (2.0 * a)
                    t2 = (-b + sq) / (2.0 * a)
                t_lo = np.clip(np.minimum(t1, t2), 0.0, 1.0)
                t_hi = np.clip(np.maximum(t1, t2), 0.0, 1.0)
                frac = np.where(valid & (t_hi > t_lo) & (a > 0), t_hi - t_lo, 0.0)
                clip_len = frac * np.sqrt(np.maximum(a, 0.0))

                np.add.at(result, cell_idx, clip_len)
            lo = hi
        return result


def _crs_info(sr):
    """``(geographic, mpu, semi_major, semi_minor)`` for a spatial
    reference. ``mpu`` (meters-per-unit) is meaningful only when NOT
    geographic; ``semi_major``/``semi_minor`` (the reference spheroid's
    axes, in meters) only when geographic."""
    geographic = (sr.type == "Geographic")
    if geographic:
        semi_major = float(sr.semiMajorAxis or 6378137.0)
        semi_minor = float(sr.semiMinorAxis or 6356752.31424518)
        mpu = 1.0
    else:
        semi_major = semi_minor = None
        mpu = float(sr.metersPerUnit or 1.0)
    return geographic, mpu, semi_major, semi_minor


def _cell_size_meters(lon0, lat0, cellw_deg, cellh_deg, semi_major, semi_minor):
    """Representative ground cell size in meters at (lon0, lat0), for
    converting a Cells-unit radius on a geographic dataset. An
    approximation -- true ground cell size varies with latitude -- since
    this tool's neighborhood-length engine isn't structured as a per-row
    moving window the way VRM/hillshade's gradient calculations are;
    Meters/Kilometers/Feet/Miles radius units need no such approximation."""
    p_c = np.array(_hs._ecef(np.array([lon0]), np.array([lat0]),
                             semi_major, semi_minor)).ravel()
    p_e = np.array(_hs._ecef(np.array([lon0 + cellw_deg]), np.array([lat0]),
                             semi_major, semi_minor)).ravel()
    p_s = np.array(_hs._ecef(np.array([lon0]), np.array([lat0 - cellh_deg]),
                             semi_major, semi_minor)).ravel()
    cellw_m = float(np.sqrt(np.sum((p_e - p_c) ** 2)))
    cellh_m = float(np.sqrt(np.sum((p_s - p_c) ** 2)))
    return cellw_m, cellh_m


def _to_ecef_xy(p0s, p1s, semi_major, semi_minor):
    """Convert (lon, lat) degree segment endpoints to 3-D ECEF meters on
    the reference spheroid (height 0), so the dimension-agnostic
    ``_RadiusLengthEngine`` can measure true ground distance instead of a
    meaningless raw degree-space Euclidean distance."""
    e0 = np.column_stack(_hs._ecef(p0s[:, 0], p0s[:, 1], semi_major, semi_minor))
    e1 = np.column_stack(_hs._ecef(p1s[:, 0], p1s[:, 1], semi_major, semi_minor))
    return e0, e1


# ---------------------------------------------------------------------------
# Contour generation (GDAL only -- no Spatial Analyst / 3D Analyst).
# ---------------------------------------------------------------------------

def _contour_segments_from_band(gdal, ogr, band, proj_wkt, nodata,
                                interval, base):
    """Run ``gdal.ContourGenerate`` on ``band`` and return
    ``(p0s, p1s, ogr_ds, ogr_layer)`` -- segment endpoint arrays plus the
    OGR layer itself (kept alive so the caller can optionally save it as a
    real feature class). Unsmoothed, matching Esri's own Contour tool."""
    srs = None
    if proj_wkt:
        from osgeo import osr
        srs = osr.SpatialReference()
        srs.ImportFromWkt(proj_wkt)
    ogr_drv = ogr.GetDriverByName("MEM")
    ogr_ds = ogr_drv.CreateDataSource("lsri_contours")
    layer = ogr_ds.CreateLayer("contours", srs=srs, geom_type=ogr.wkbLineString)
    layer.CreateField(ogr.FieldDefn("ID", ogr.OFTInteger))
    layer.CreateField(ogr.FieldDefn("Contour", ogr.OFTReal))

    use_nodata = 1 if nodata is not None else 0
    nodata_val = float(nodata) if nodata is not None else 0.0
    gdal.ContourGenerate(band, float(interval), float(base), [],
                        use_nodata, nodata_val, layer, 0, 1)

    p0s, p1s = [], []
    layer.ResetReading()
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        pts = geom.GetPoints()
        for i in range(len(pts) - 1):
            # Defense-in-depth cap (see _polyline_segments): a pathological
            # DEM/interval combination (e.g. an absurdly fine interval over
            # dense noise) could otherwise generate an unbounded number of
            # contour segments and exhaust memory building these lists.
            if len(p0s) >= _MAX_SEGMENTS:
                raise arcpy.ExecuteError(
                    "Contouring this DEM at this interval produced more than "
                    "{:,} segments, which this tool does not support in one "
                    "run. Use a larger contour interval, or a smaller "
                    "processing extent.".format(_MAX_SEGMENTS))
            p0s.append(pts[i][:2])
            p1s.append(pts[i + 1][:2])
    layer.ResetReading()
    return (np.array(p0s, dtype=np.float64) if p0s else np.zeros((0, 2)),
           np.array(p1s, dtype=np.float64) if p1s else np.zeros((0, 2)),
           ogr_ds, layer)


def _save_contours(out_fc, ogr_layer, sr, messages):
    """Write the in-memory OGR contour layer to a real feature class
    DIRECTLY via arcpy (full double precision, no intermediate file
    format -- a shapefile bridge would truncate values, the same reason
    kernel_density.py avoids one for its own scratch contour lines), so it
    gets the usual Jenness metadata treatment."""
    out_dir = os.path.dirname(out_fc)
    out_name = os.path.basename(out_fc)
    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)
    arcpy.management.CreateFeatureclass(
        out_dir, out_name, "POLYLINE", spatial_reference=sr)
    arcpy.management.AddField(out_fc, "Contour", "DOUBLE")
    ogr_layer.ResetReading()
    with arcpy.da.InsertCursor(out_fc, ["SHAPE@", "Contour"]) as ins:
        for feat in ogr_layer:
            geom = feat.GetGeometryRef()
            if geom is None:
                continue
            pts = geom.GetPoints()
            arr = arcpy.Array([arcpy.Point(x, y) for x, y in pts])
            ins.insertRow([arcpy.Polyline(arr, sr), feat.GetField("Contour")])
    ogr_layer.ResetReading()
    try:
        md = arcpy.metadata.Metadata(out_fc)
        md.title = out_name
        md.tags = "contour lines, elevation contours, Jenness Enterprises"
        md.summary = "Contour polylines generated by the LSRI on DEM tool."
        md.description = (
            "Elevation contour polylines, unsmoothed (following the raster "
            "cell grid exactly, like Esri's own Contour tool with no "
            "smoothing applied). Saved at the user's request alongside "
            "the Land Surface Ruggedness Index raster computed from them.")
        md.save()
    except Exception:
        pass
    _hs._msg(messages, "Saved contour polylines: {}".format(out_fc))


# ---------------------------------------------------------------------------
# Shared output-raster writer.
# ---------------------------------------------------------------------------

def _write_lsri_raster(gdal, engine, mult, valid_mask, gt, proj_wkt,
                       width, height, scratch, messages,
                       geographic=False, semi_major=None, semi_minor=None):
    """Runs the radius-length engine over the whole grid (in row strips,
    like the toolbox's other moving-window tools) and writes a scratch
    GeoTIFF with self-computed stats, masking cells the caller says are
    invalid (e.g. NoData in the source DEM). When ``geographic``, each
    strip's (lon, lat) cell centers are converted to 3-D ECEF meters
    before querying the engine, matching the ECEF segments it was built
    from (see ``_to_ecef_xy``); the output raster's own grid/geotransform
    stays in the source's native (degree) coordinates either way."""
    out_path = arcpy.CreateUniqueName("lsri.tif", scratch)
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(out_path, width, height, 1, gdal.GDT_Float32,
                    options=["TILED=YES", "COMPRESS=LZW"])
    ds.SetGeoTransform(gt)
    try:
        ds.SetProjection(proj_wkt)
    except Exception:
        pass
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(NODATA_OUT)

    stats = _mh._RasterStats()
    strips = [(y, min(_STRIP_ROWS, height - y)) for y in range(0, height, _STRIP_ROWS)]
    for idx, (yoff, rows) in enumerate(strips):
        # Pixel-center coordinates; assumes a north-up, non-rotated grid
        # (gt[2] == gt[4] == 0), like every other raster tool in this toolbox.
        xs = gt[0] + (np.arange(width) + 0.5) * gt[1]
        row_idx = yoff + np.arange(rows)
        ys = gt[3] + (row_idx + 0.5) * gt[5]
        gx, gy = np.meshgrid(xs, ys)
        cx = gx.ravel()
        cy = gy.ravel()
        if geographic:
            ex, ey, ez = _hs._ecef(cx, cy, semi_major, semi_minor)
            centers = np.column_stack([ex, ey, ez])
        else:
            centers = np.column_stack([cx, cy])
        lens = engine.query(centers) * mult
        out_arr = lens.reshape(rows, width)
        if valid_mask is not None:
            row_valid = valid_mask[yoff:yoff + rows, :]
            out_arr = np.where(row_valid, out_arr, NODATA_OUT).astype(np.float32)
            if row_valid.any():
                stats.add(out_arr[row_valid])
        else:
            out_arr = out_arr.astype(np.float32)
            stats.add(out_arr.ravel())
        band.WriteArray(out_arr, 0, yoff)
        _mh.phase_step(idx + 1, len(strips))
    band.FlushCache()
    ds.FlushCache()
    out_stats = _mh._finalize_stats(band, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(band, out_stats)
    band.FlushCache()
    ds.FlushCache()
    ds = None
    return out_path, out_stats


def _deliver_lsri(out_scratch, out_raster, out_stats, messages, summary, tags):
    """CopyRaster -> stats -> metadata -> pyramids -> default symbology."""
    prev_pyramid = arcpy.env.pyramid
    arcpy.env.pyramid = "NONE"
    try:
        arcpy.management.CopyRaster(out_scratch, out_raster)
    finally:
        arcpy.env.pyramid = prev_pyramid
    _mh.phase_advance("Setting raster statistics...")
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
        md.title = os.path.basename(str(out_raster))
        md.tags = tags
        md.summary = summary
        md.description = LSRI_DESCRIPTION
        md.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                     "(http://www.jennessent.com). Method: " + CITATION)
        md.save()
    except Exception:
        pass
    try:
        arcpy.management.Delete(out_scratch)
    except Exception:
        pass
    _mh.phase_advance("Creating layer file and symbology...")
    # Blue (low ruggedness) -> yellow -> red (high ruggedness), same
    # convention as VRM and Surface Area/Ratio.
    out_lyrx = _mh.default_stretch_lyrx(
        out_raster, [(95, 135, 193), (249, 254, 204), (224, 70, 53)],
        messages=messages)
    _mh.phase_advance("Building pyramids...")
    try:
        arcpy.management.BuildPyramids(out_raster, resample_technique="BILINEAR")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not build pyramids: {}".format(e))
    return out_lyrx


# ---------------------------------------------------------------------------
# Tool 2 (built first): LSRI on DEM.
# ---------------------------------------------------------------------------

def run_lsri_on_dem(in_raster, out_raster, contour_interval, base_contour=0.0,
                    z_factor=1.0, radius=None, radius_units="Meters",
                    length_units="Meters", out_contours=None,
                    out_cell_size=None, messages=None):
    """Generate contours from ``in_raster`` on the fly (matching Esri's own
    Contour tool's interval/base/z-factor logic, unsmoothed), then compute
    the Land Surface Ruggedness Index (Beasom et al. 1983): total contour
    length within a circular neighborhood of each cell. Optionally saves
    the derived contours to ``out_contours``. ``out_cell_size``, if given,
    computes the output raster at a coarser (or finer) cell size than the
    input DEM -- in the DEM's own map units -- WITHOUT affecting contour
    accuracy (contours are always traced at the DEM's native resolution;
    only the output query grid changes), a real lever for cutting run time
    on a large, fine-resolution DEM: the query grid is what scales with the
    number of output cells, not the contour tracing itself. Returns the
    output raster's default-symbology .lyrx path (or None)."""
    gdal = _mh._gdal()
    from osgeo import ogr

    if contour_interval is None or contour_interval <= 0:
        raise arcpy.ExecuteError("The contour interval must be a positive number.")
    base_contour = 0.0 if base_contour is None else float(base_contour)
    z_factor = 1.0 if not z_factor else float(z_factor)
    if radius is None or radius <= 0:
        raise arcpy.ExecuteError("The neighborhood radius must be positive.")
    if radius_units not in RADIUS_UNITS:
        raise arcpy.ExecuteError("Unknown radius unit: {}.".format(radius_units))
    if length_units not in LENGTH_UNITS:
        raise arcpy.ExecuteError("Unknown length unit: {}.".format(length_units))
    if out_cell_size is not None and out_cell_size <= 0:
        raise arcpy.ExecuteError("The output cell size must be positive.")
    out_raster = _mh.resolve_raster_output(out_raster, messages)

    sr = arcpy.Describe(in_raster).spatialReference
    geographic, mpu, semi_major, semi_minor = _crs_info(sr)

    phases = [("Reading the DEM...", 1), ("Generating contours...", 3)]
    if out_contours:
        phases.append(("Saving the contour feature class...", 1))
    phases += [("Preparing contour segments...", 1),
              ("Calculating total contour length within radius...", 7),
              ("Saving output raster...", 2),
              ("Setting raster statistics...", 1),
              ("Creating layer file and symbology...", 1),
              ("Building pyramids...", 1)]
    _mh.begin_phases(phases)

    scratch = arcpy.env.scratchFolder
    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    band = src.GetRasterBand(1)
    width, height = src.RasterXSize, src.RasterYSize
    if width < 2 or height < 2:
        raise arcpy.ExecuteError("The DEM must be at least 2 x 2 cells.")
    gt = src.GetGeoTransform()
    proj_wkt = src.GetProjection()
    in_nodata = band.GetNoDataValue()

    if geographic:
        mid_row = height // 2
        lon0 = gt[0] + 0.5 * gt[1]
        lat0 = gt[3] + (mid_row + 0.5) * gt[5]
        cellw_m, cellh_m = _cell_size_meters(
            lon0, lat0, gt[1], abs(gt[5]), semi_major, semi_minor)
    else:
        cellw_m = abs(gt[1]) * mpu
        cellh_m = abs(gt[5]) * mpu
    if radius_units == "Cells":
        radius_m = radius * ((cellw_m + cellh_m) / 2.0)
    else:
        radius_m = radius * _RADIUS_METERS[radius_units]
    # ECEF space is already in meters (geographic); a projected raster's
    # engine works directly in its own native map units.
    radius_map = radius_m if geographic else (radius_m / mpu)

    arr = band.ReadAsArray(0, 0, width, height).astype(np.float64)
    valid = np.isfinite(arr)
    if in_nodata is not None:
        valid &= (arr != in_nodata)
    scaled = np.where(valid, arr * z_factor, NODATA_OUT)
    src = None
    if in_copy:
        try:
            arcpy.management.Delete(in_copy)
        except Exception:
            pass

    _mh.phase_advance("Generating contours...")
    mem_drv = gdal.GetDriverByName("MEM")
    z_ds = mem_drv.Create("", width, height, 1, gdal.GDT_Float64)
    z_ds.SetGeoTransform(gt)
    try:
        z_ds.SetProjection(proj_wkt)
    except Exception:
        pass
    z_band = z_ds.GetRasterBand(1)
    z_band.SetNoDataValue(NODATA_OUT)
    z_band.WriteArray(scaled)
    z_band.FlushCache()

    p0s, p1s, ogr_ds, ogr_layer = _contour_segments_from_band(
        gdal, ogr, z_band, proj_wkt, NODATA_OUT, contour_interval, base_contour)
    _hs._msg(messages, "Generated {:,} contour segments: interval {:g}, "
                       "base {:g}, z-factor {:g}{}.".format(
                           len(p0s), contour_interval, base_contour, z_factor,
                           " (elevation values scaled by the z-factor before "
                           "contouring)" if z_factor != 1.0 else ""))

    if out_contours:
        _mh.phase_advance("Saving the contour feature class...")
        _save_contours(out_contours, ogr_layer, sr, messages)

    _mh.phase_advance("Preparing contour segments...")
    if geographic:
        e0, e1 = _to_ecef_xy(p0s, p1s, semi_major, semi_minor)
        engine = _RadiusLengthEngine(e0, e1, radius_map)
    else:
        engine = _RadiusLengthEngine(p0s, p1s, radius_map)
    mult = _LENGTH_MULT[length_units] * (1.0 if geographic else mpu)

    # Decouple the OUTPUT query grid from the DEM's native resolution.
    # Contours above were already traced at full native resolution (so
    # accuracy is unaffected); only the grid of cells the length engine is
    # queried at changes here. A coarser out_cell_size is the single
    # biggest lever for cutting run time on a large, fine-resolution DEM,
    # since the query grid (not the contour tracing) is what scales with
    # cell count.
    if out_cell_size:
        native_cw, native_ch = abs(gt[1]), abs(gt[5])
        scale_x = out_cell_size / native_cw
        scale_y = out_cell_size / native_ch
        out_gt = (gt[0], out_cell_size, 0.0, gt[3], 0.0, -out_cell_size)
        out_width = max(1, int(math.ceil(width / scale_x)))
        out_height = max(1, int(math.ceil(height / scale_y)))
        row_src = np.clip((np.arange(out_height) * scale_y).astype(np.int64), 0, height - 1)
        col_src = np.clip((np.arange(out_width) * scale_x).astype(np.int64), 0, width - 1)
        out_valid = valid[row_src][:, col_src]
        _hs._msg(messages, "Output raster cell size: {:g} (input DEM: "
                           "{:g} x {:g}) -- {:,} output cells instead of "
                           "{:,}.".format(out_cell_size, native_cw, native_ch,
                                          out_width * out_height, width * height))
    else:
        out_gt, out_width, out_height, out_valid = gt, width, height, valid

    _mh.phase_advance("Calculating total contour length within radius...")
    out_path, out_stats = _write_lsri_raster(
        gdal, engine, mult, out_valid, out_gt, proj_wkt, out_width, out_height,
        scratch, messages,
        geographic=geographic, semi_major=semi_major, semi_minor=semi_minor)

    _mh.phase_advance("Saving output raster...")
    out_lyrx = _deliver_lsri(
        out_path, out_raster, out_stats, messages,
        summary=("Created by LSRI on DEM from {src}: contour interval "
                 "{ci:g}, base contour {bc:g}, z-factor {zf:g}, "
                 "neighborhood radius {r:g} {ru}, length units {lu}{cs}.").format(
                     src=str(in_raster), ci=contour_interval, bc=base_contour,
                     zf=z_factor, r=radius, ru=radius_units.lower(), lu=length_units.lower(),
                     cs=(", output cell size {:g} (input DEM cell size {:g} x "
                         "{:g})".format(out_cell_size, abs(gt[1]), abs(gt[5]))
                         if out_cell_size else "")),
        tags="land surface ruggedness index, LSRI, contour density, terrain ruggedness, "
             "Jenness Enterprises")
    _mh.end_phases()
    _hs._msg(messages, "LSRI on DEM complete: {}".format(out_raster))
    return out_lyrx


# ---------------------------------------------------------------------------
# Tool 1 (built second, tested against Tool 2's saved contours):
# LSRI on Contours.
# ---------------------------------------------------------------------------

def default_cell_size_from_extent(fc):
    """Jeff's default rule for LSRI on Contours: the GREATER of
    the input feature class's width or height, divided by 1000. Returns a
    float, or None if the extent can't be determined."""
    try:
        ext = arcpy.Describe(fc).extent
        span = max(ext.width, ext.height)
        if span > 0:
            return span / 1000.0
    except Exception:
        pass
    return None


def _polyline_segments(fc, sr, messages):
    """Flatten every part of every feature in ``fc`` into (p0s, p1s)
    segment endpoint arrays, in the feature class's own spatial reference.
    Passing a Feature LAYER with an active selection restricts this to the
    selected features (standard arcpy Feature Layer behavior).

    Stops with a clear error past ``_MAX_SEGMENTS`` segments, checked
    incrementally as they're read (not after building the whole list) --
    a defense-in-depth cap against a feature class engineered (or simply
    malformed) to have an absurd vertex count, which would otherwise grow
    these lists without bound and could exhaust memory. 5 million segments
    is already far beyond any realistic contour/habitat dataset this tool
    is meant for."""
    p0s, p1s = [], []
    n_features = 0
    with arcpy.da.SearchCursor(fc, ["SHAPE@"]) as cur:
        for (shape,) in cur:
            if shape is None:
                continue
            n_features += 1
            for part in shape:
                pts = [(pt.X, pt.Y) for pt in part if pt is not None]
                for i in range(len(pts) - 1):
                    if len(p0s) >= _MAX_SEGMENTS:
                        raise arcpy.ExecuteError(
                            "The input has more than {:,} polyline segments, "
                            "which this tool does not support in one run. "
                            "Select a subset of features, split the data, or "
                            "generalize/simplify it first.".format(_MAX_SEGMENTS))
                    p0s.append(pts[i])
                    p1s.append(pts[i + 1])
    _hs._msg(messages, "Read {:,} polyline feature(s), {:,} segments.".format(
        n_features, len(p0s)))
    if n_features == 0:
        raise arcpy.ExecuteError(
            "No input polylines were found (check the feature selection, "
            "if any).")
    return (np.array(p0s, dtype=np.float64) if p0s else np.zeros((0, 2)),
           np.array(p1s, dtype=np.float64) if p1s else np.zeros((0, 2)))


def run_lsri_on_contours(in_polylines, out_raster, cell_size, radius,
                         radius_units="Meters", length_units="Meters",
                         messages=None):
    """Compute the Land Surface Ruggedness Index (Beasom et al. 1983) --
    total polyline length within a circular neighborhood of each cell --
    directly from an existing contour (or any polyline) feature class.
    Returns the output raster's default-symbology .lyrx path (or None)."""
    gdal = _mh._gdal()

    if cell_size is None or cell_size <= 0:
        raise arcpy.ExecuteError("The cell size must be positive.")
    if radius is None or radius <= 0:
        raise arcpy.ExecuteError("The neighborhood radius must be positive.")
    if radius_units not in RADIUS_UNITS:
        raise arcpy.ExecuteError("Unknown radius unit: {}.".format(radius_units))
    if length_units not in LENGTH_UNITS:
        raise arcpy.ExecuteError("Unknown length unit: {}.".format(length_units))
    out_raster = _mh.resolve_raster_output(out_raster, messages)

    desc = arcpy.Describe(in_polylines)
    sr = desc.spatialReference
    geographic, mpu, semi_major, semi_minor = _crs_info(sr)

    _mh.begin_phases([
        ("Reading contour polylines...", 1),
        ("Preparing contour segments...", 1),
        ("Calculating total contour length within radius...", 7),
        ("Saving output raster...", 2),
        ("Setting raster statistics...", 1),
        ("Creating layer file and symbology...", 1),
        ("Building pyramids...", 1),
    ])

    p0s, p1s = _polyline_segments(in_polylines, sr, messages)

    # Representative ground cell size at the data's own center latitude
    # (geographic only) -- used both for a Cells-unit radius and to buffer
    # the default extent, since a meters radius can't directly buffer a
    # degree-space extent.
    cellw_m = cellh_m = None
    if geographic:
        ext0 = desc.extent
        lon0 = (ext0.XMin + ext0.XMax) / 2.0
        lat0 = (ext0.YMin + ext0.YMax) / 2.0
        cellw_m, cellh_m = _cell_size_meters(
            lon0, lat0, cell_size, cell_size, semi_major, semi_minor)

    if radius_units == "Cells":
        if geographic:
            radius_map = radius * ((cellw_m + cellh_m) / 2.0)
        else:
            radius_map = radius * cell_size
    else:
        radius_m = radius * _RADIUS_METERS[radius_units]
        radius_map = radius_m if geographic else (radius_m / mpu)

    env_ext = arcpy.env.extent
    if env_ext and env_ext.XMin is not None:
        xmin, ymin, xmax, ymax = env_ext.XMin, env_ext.YMin, env_ext.XMax, env_ext.YMax
    else:
        # Expand by the radius so cells near the data's edge still get a
        # fully-populated neighborhood (an improvement over both Beasom's
        # manual method and Esri's Line Density, neither of which correct
        # for this at the edge of the study area).
        ext = desc.extent
        if geographic:
            # radius_map is in meters; convert back to an approximate degree
            # buffer at the data's own latitude (a margin only -- it does
            # not need to be exact).
            buf_lon = radius_map * cell_size / max(cellw_m, 1e-9)
            buf_lat = radius_map * cell_size / max(cellh_m, 1e-9)
        else:
            buf_lon = buf_lat = radius_map
        xmin, ymin = ext.XMin - buf_lon, ext.YMin - buf_lat
        xmax, ymax = ext.XMax + buf_lon, ext.YMax + buf_lat

    width = max(1, int(math.ceil((xmax - xmin) / cell_size)))
    height = max(1, int(math.ceil((ymax - ymin) / cell_size)))
    gt = (xmin, cell_size, 0.0, ymax, 0.0, -cell_size)
    try:
        from osgeo import osr
        srs = osr.SpatialReference()
        srs.ImportFromWkt(sr.exportToString())
        proj_wkt = srs.ExportToWkt()
    except Exception:
        proj_wkt = None

    _mh.phase_advance("Preparing contour segments...")
    if geographic:
        e0, e1 = _to_ecef_xy(p0s, p1s, semi_major, semi_minor)
        engine = _RadiusLengthEngine(e0, e1, radius_map)
    else:
        engine = _RadiusLengthEngine(p0s, p1s, radius_map)
    mult = _LENGTH_MULT[length_units] * (1.0 if geographic else mpu)

    _mh.phase_advance("Calculating total contour length within radius...")
    scratch = arcpy.env.scratchFolder
    out_path, out_stats = _write_lsri_raster(
        gdal, engine, mult, None, gt, proj_wkt, width, height, scratch, messages,
        geographic=geographic, semi_major=semi_major, semi_minor=semi_minor)

    _mh.phase_advance("Saving output raster...")
    out_lyrx = _deliver_lsri(
        out_path, out_raster, out_stats, messages,
        summary=("Created by LSRI on Contours from {src}: cell "
                 "size {cs:g}, neighborhood radius {r:g} {ru}, length "
                 "units {lu}.").format(
                     src=str(in_polylines), cs=cell_size, r=radius,
                     ru=radius_units.lower(), lu=length_units.lower()),
        tags="land surface ruggedness index, LSRI, contour density, terrain ruggedness, "
             "Jenness Enterprises")
    _mh.end_phases()
    _hs._msg(messages, "LSRI on Contours complete: {}".format(out_raster))
    return out_lyrx
