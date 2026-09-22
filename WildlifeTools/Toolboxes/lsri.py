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
import time
from concurrent.futures import ThreadPoolExecutor

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
# (center, candidate-segment) pairs one work block materializes -- the
# flat numpy work arrays run ~200-250 bytes/pair across the several
# same-length arrays in play, so a 1-million-pair block is ~250 MB, and
# with _QUERY_THREADS blocks in flight at once peak usage stays in the
# low single-digit GiB regardless of raster size or contour density. See
# the query() docstring for the real crash this bounding was found from.
_BLOCK_PAIRS = 1_000_000

# Blocks are clipped in parallel threads. numpy releases the GIL for the
# large elementwise operations that dominate the clip math, and the
# KD-tree gather is C code, so threads scale nearly linearly: measured
# 3.7x at 4 threads and 5.4x at 8 on a 4.6-billion-pair Grand Canyon run
# (2026-09-21), results identical to rounding. Capped so a many-core
# workstation does not queue dozens of 250 MB blocks at once.
_QUERY_THREADS = max(1, min(8, os.cpu_count() or 1))

# Defense-in-depth cap on subdivided pieces (the KD-tree points). The
# input-segment cap above does not bound these: subdivision multiplies,
# and an input with very long segments (roads, transmission lines --
# "LSRI on Contours" accepts any polylines) combined with a small radius
# could otherwise explode into billions of pieces and exhaust memory in
# _subdivide or the KD-tree build.
_MAX_PIECES = 20_000_000

# Defense-in-depth cap on output raster cells. A mistyped cell size, an
# output cell size much finer than the DEM, or a stray Extent environment
# in the wrong units could otherwise request an absurd grid.
_MAX_OUT_CELLS = 2_000_000_000

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
    here was measurably slow at millions of segments (~2.6 s at 1.5M),
    since it ran even for the (usual) majority of segments that don't
    need subdividing at all."""
    ndim = p0s.shape[1] if len(p0s) else 2
    n = len(p0s)
    if n == 0:
        return p0s, p1s
    seg = p1s - p0s
    seg_len = np.sqrt(np.sum(seg * seg, axis=1))
    # Zero-length segments fall in 'short' automatically (0 <= max_len).
    short = seg_len <= max_len
    out0 = [p0s[short]]
    out1 = [p1s[short]]

    long = ~short
    if np.any(long):
        n_pieces = np.ceil(seg_len[long] / max_len).astype(np.int64)
        total = int(n_pieces.sum())
        if total + int(short.sum()) > _MAX_PIECES:
            raise arcpy.ExecuteError(
                "Preparing the neighborhood search would subdivide the "
                "input's long segments into more than {:,} pieces (pieces "
                "are capped at the neighborhood radius, and this radius is "
                "very small relative to the input's segment lengths). Use "
                "a larger neighborhood radius, or simplify the input to "
                "fewer/shorter line segments.".format(_MAX_PIECES))
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

    def count(self, centers):
        """Per-center candidate-piece counts (an int array; no Python
        lists), i.e. the amount of work ``query`` will do for these
        centers. Cheap relative to the clipping itself, and the caller can
        sum it over every strip up front to drive a whole-run progress
        estimate."""
        if self.tree is None or len(centers) == 0:
            return np.zeros(len(centers), dtype=np.int64)
        # Every point of a piece lies within HALF the piece's length of
        # the piece's midpoint (the KD-tree point), so radius +
        # max_piece_len/2 provably catches every piece that can touch the
        # circle. The previous full-length slack searched ~(4/2.25)x the
        # necessary area at the piece-length == radius cap, i.e. ~40%
        # more candidate pairs than needed -- verified identical results.
        return self.tree.query_ball_point(
            centers, r=self._query_r(), workers=-1,
            return_length=True).astype(np.int64)

    def _query_r(self):
        return self.radius + self.max_piece_len / 2.0

    def _clip_block(self, centers, lo, hi):
        """Exact total clipped length per center for centers[lo:hi]
        (returned as an array of length hi-lo). Runs on a worker thread.

        The candidate gather uses ``sparse_distance_matrix`` on a small
        KD-tree of the block's own centers: it returns flat (i, j) index
        arrays straight from C, where ``query_ball_point`` returns a
        list of Python lists that then had to be flattened by a Python
        generator (measured ~96 ns/pair, versus ~35 ns here). The clip
        arithmetic is unchanged."""
        blk = centers[lo:hi]
        ct = cKDTree(blk, leafsize=64)
        m = ct.sparse_distance_matrix(self.tree, self._query_r(),
                                      output_type="ndarray")
        cell_idx = m["i"].astype(np.int64)
        piece_idx = m["j"].astype(np.int64)
        del m
        P0 = self.p0s[piece_idx]
        d = self.p1s[piece_idx] - P0
        f = P0 - blk[cell_idx]
        a = np.einsum("ij,ij->i", d, d)
        b = 2.0 * np.einsum("ij,ij->i", f, d)
        c = np.einsum("ij,ij->i", f, f) - self.radius * self.radius
        disc = b * b - 4.0 * a * c
        valid = disc >= 0.0
        sq = np.sqrt(np.maximum(disc, 0.0))
        with np.errstate(divide="ignore", invalid="ignore"):
            t1 = (-b - sq) / (2.0 * a)
            t2 = (-b + sq) / (2.0 * a)
        t_lo = np.clip(np.minimum(t1, t2), 0.0, 1.0)
        t_hi = np.clip(np.maximum(t1, t2), 0.0, 1.0)
        frac = np.where(valid & (t_hi > t_lo) & (a > 0), t_hi - t_lo, 0.0)
        return np.bincount(cell_idx, weights=frac * np.sqrt(np.maximum(a, 0.0)),
                           minlength=hi - lo)

    def query(self, centers, counts=None, progress=None):
        """``centers``: (M, ndim) array, same ndim as this engine's segments.
        Returns, for each center, the exact total segment length within
        ``radius``.

        MEMORY-BOUNDED, THREADED design. ``counts`` (from ``count``; computed
        here if not given) splits ``centers`` into blocks of at most
        ``_BLOCK_PAIRS`` candidate pairs, and ``_QUERY_THREADS`` worker
        threads clip the blocks concurrently. EVERY per-pair structure is
        bounded by the block budget. (REAL CRASH this design guards
        against: a rugged, geographic DEM with a fine 10 m contour interval
        generated 4.2 million contour segments, and a single 1024-row
        strip produced 2.69 BILLION candidate pairs -- ~60 GiB for one
        flat work array alone if materialized at once.)

        ``progress(pairs_done)`` is called on the calling thread after each
        block completes, with the cumulative number of candidate pairs
        clipped so far in this call."""
        n_out = len(centers)
        result = np.zeros(n_out, dtype=np.float64)
        if self.tree is None or n_out == 0:
            return result
        if counts is None:
            counts = self.count(centers)
        if int(counts.sum()) == 0:
            return result

        cum = np.cumsum(counts)
        blocks = []
        lo = 0
        while lo < n_out:
            prev = int(cum[lo - 1]) if lo > 0 else 0
            hi = int(np.searchsorted(cum, prev + _BLOCK_PAIRS, side="right"))
            hi = min(max(hi, lo + 1), n_out)   # always make progress
            if int(cum[hi - 1]) - prev > 0:
                blocks.append((lo, hi))
            lo = hi

        done = 0
        with ThreadPoolExecutor(max_workers=_QUERY_THREADS) as ex:
            # map() yields in submission order; blocks are all queued up
            # front (cheap: they are just index pairs) and each result is
            # only the block's own hi-lo values, so at most a few blocks'
            # work arrays are alive at any moment.
            for (lo, hi), part in zip(blocks, ex.map(
                    lambda b: self._clip_block(centers, b[0], b[1]), blocks)):
                result[lo:hi] += part
                done += int(cum[hi - 1]) - (int(cum[lo - 1]) if lo > 0 else 0)
                if progress is not None:
                    progress(done)
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

    # Per-FEATURE numpy slicing (arr[:-1] / arr[1:]) instead of a
    # per-vertex Python append loop -- a multi-million-segment contour
    # network spends several seconds in the loop version.
    p0_parts, p1_parts = [], []
    n_segments = 0
    layer.ResetReading()
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        pts = geom.GetPoints()
        if pts is None or len(pts) < 2:
            continue
        n_segments += len(pts) - 1
        # Defense-in-depth cap (see _polyline_segments): a pathological
        # DEM/interval combination (e.g. an absurdly fine interval over
        # dense noise) could otherwise generate an unbounded number of
        # contour segments and exhaust memory building these arrays.
        if n_segments > _MAX_SEGMENTS:
            raise arcpy.ExecuteError(
                "Contouring this DEM at this interval produced more than "
                "{:,} segments, which this tool does not support in one "
                "run. Use a larger contour interval, or a smaller "
                "processing extent.".format(_MAX_SEGMENTS))
        arr = np.asarray(pts, dtype=np.float64)[:, :2]
        p0_parts.append(arr[:-1])
        p1_parts.append(arr[1:])
    layer.ResetReading()
    return (np.concatenate(p0_parts) if p0_parts else np.zeros((0, 2)),
           np.concatenate(p1_parts) if p1_parts else np.zeros((0, 2)),
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
        out_dir, out_name, "POLYLINE", spatial_reference=_mh.clean_sr(sr))
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

def _check_output_size(width, height):
    """Defense-in-depth guard on the output raster size (see
    ``_MAX_OUT_CELLS``)."""
    if int(width) * int(height) > _MAX_OUT_CELLS:
        raise arcpy.ExecuteError(
            "The output raster would have {:,} cells ({:,} columns x {:,} "
            "rows), more than this tool supports ({:,}). Check the cell "
            "size and any Extent environment setting.".format(
                int(width) * int(height), int(width), int(height),
                _MAX_OUT_CELLS))


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
                    options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform(gt)
    try:
        ds.SetProjection(proj_wkt)
    except Exception:
        pass
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(NODATA_OUT)

    stats = _mh._RasterStats()
    # Pixel-center x coordinates; assumes a north-up, non-rotated grid
    # (gt[2] == gt[4] == 0), like every other raster tool in this toolbox.
    xs = gt[0] + (np.arange(width) + 0.5) * gt[1]
    strips = [(y, min(_STRIP_ROWS, height - y)) for y in range(0, height, _STRIP_ROWS)]

    def strip_centers(yoff, rows):
        row_idx = yoff + np.arange(rows)
        ys = gt[3] + (row_idx + 0.5) * gt[5]
        gx, gy = np.meshgrid(xs, ys)
        cx = gx.ravel()
        cy = gy.ravel()
        flat_valid = None
        if valid_mask is not None:
            # Skip NoData cells BEFORE the (expensive) neighborhood query
            # instead of querying everything and masking afterward --
            # study-area DEMs clipped to a boundary are routinely 30-60%
            # NoData, and this cuts the query (and any ECEF conversion)
            # cost proportionally.
            flat_valid = valid_mask[yoff:yoff + rows, :].ravel()
            cx = cx[flat_valid]
            cy = cy[flat_valid]
        if geographic:
            ex, ey, ez = _hs._ecef(cx, cy, semi_major, semi_minor)
            centers = np.column_stack([ex, ey, ez])
        else:
            centers = np.column_stack([cx, cy])
        return centers, flat_valid

    # Pass 1: count the candidate pairs in every strip, so the bar and the
    # time estimate below cover the WHOLE calculation rather than one
    # strip at a time (a DEM shorter than _STRIP_ROWS is a single strip,
    # and used to sit at the phase's start for the entire run). The
    # counts are kept (int32, 4 bytes per cell) and handed to query() so
    # the tree is not searched twice.
    strip_counts = []
    total_pairs = 0
    for yoff, rows in strips:
        centers, _ = strip_centers(yoff, rows)
        cnt = engine.count(centers)
        strip_counts.append(cnt.astype(np.int32))
        total_pairs += int(cnt.sum())
        del centers
    _hs._msg(messages, "Neighborhood search: {:,} output cells, {:,} candidate "
                       "segment comparisons, {} threads.".format(
                           int(sum(len(c) for c in strip_counts)), total_pairs,
                           _QUERY_THREADS))

    # Pass 2: clip, with progress by pairs done and a running estimate.
    label = "Calculating total contour length within radius"
    state = {"done_before": 0, "t0": time.time(), "t_label": 0.0}

    def report(done_in_strip):
        done = state["done_before"] + done_in_strip
        _mh.phase_step(done, total_pairs)
        now = time.time()
        if now - state["t_label"] < 1.0 and done < total_pairs:
            return
        state["t_label"] = now
        elapsed = now - state["t0"]
        pct = 100.0 * done / total_pairs if total_pairs else 100.0
        if done > 0 and elapsed > 2.0 and done < total_pairs:
            remaining = elapsed * (total_pairs - done) / done
            eta = ("about {:.0f} min remaining".format(remaining / 60.0)
                   if remaining >= 90 else
                   "about {:.0f} s remaining".format(max(remaining, 1.0)))
            _hs._set_label("{}: {:.0f}% of {:,} comparisons, {}".format(
                label, pct, total_pairs, eta))
        else:
            _hs._set_label("{}: {:.0f}% of {:,} comparisons".format(
                label, pct, total_pairs))

    for idx, (yoff, rows) in enumerate(strips):
        centers, flat_valid = strip_centers(yoff, rows)
        cnt = strip_counts[idx].astype(np.int64)
        lens = (engine.query(centers, counts=cnt, progress=report)
                * mult).astype(np.float32)
        state["done_before"] += int(cnt.sum())
        if flat_valid is None:
            out_arr = lens.reshape(rows, width)
            stats.add(lens)
        else:
            out_flat = np.full(rows * width, NODATA_OUT, dtype=np.float32)
            out_flat[flat_valid] = lens
            out_arr = out_flat.reshape(rows, width)
            if lens.size:
                stats.add(lens)
        band.WriteArray(out_arr, 0, yoff)
        _mh.phase_step(state["done_before"], total_pairs)
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
    valid = _mh.mask_band_valid(band, valid)
    scaled = np.where(valid, arr * z_factor, NODATA_OUT)
    del arr   # only 'scaled' and the boolean 'valid' mask are needed now
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
    del scaled   # the MEM dataset holds its own copy now

    p0s, p1s, ogr_ds, ogr_layer = _contour_segments_from_band(
        gdal, ogr, z_band, proj_wkt, NODATA_OUT, contour_interval, base_contour)
    # Contouring is done -- release the whole-DEM float64 MEM copy before
    # the memory-heavy neighborhood-query phase.
    z_band = None
    z_ds = None
    _hs._msg(messages, "Generated {:,} contour segments: interval {:g}, "
                       "base {:g}, z-factor {:g}{}.".format(
                           len(p0s), contour_interval, base_contour, z_factor,
                           " (elevation values scaled by the z-factor before "
                           "contouring)" if z_factor != 1.0 else ""))

    if out_contours:
        _mh.phase_advance("Saving the contour feature class...")
        _save_contours(out_contours, ogr_layer, sr, messages)
    # Segment arrays are extracted (and any requested save is done) --
    # release the OGR in-memory contour geometries too.
    ogr_layer = None
    ogr_ds = None

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
        _check_output_size(out_width, out_height)
        # Sample the native valid-mask at the native cell CONTAINING each
        # output cell's CENTER (+0.5) -- the same location the length
        # engine is queried at -- so the NoData flag and the queried
        # point always agree along NoData boundaries.
        row_src = np.clip(((np.arange(out_height) + 0.5) * scale_y).astype(np.int64),
                          0, height - 1)
        col_src = np.clip(((np.arange(out_width) + 0.5) * scale_x).astype(np.int64),
                          0, width - 1)
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


def _polyline_segments(fc, messages):
    """Flatten every part of every feature in ``fc`` into (p0s, p1s)
    segment endpoint arrays, in the feature class's own spatial reference.
    Passing a Feature LAYER with an active selection restricts this to the
    selected features (standard arcpy Feature Layer behavior).

    Per-PART numpy slicing (arr[:-1] / arr[1:]) instead of a per-vertex
    Python append loop. Stops with a clear error past ``_MAX_SEGMENTS``
    segments, checked incrementally as they're read (not after building
    the whole set) -- a defense-in-depth cap against a feature class
    engineered (or simply malformed) to have an absurd vertex count,
    which would otherwise grow these arrays without bound and could
    exhaust memory. 5 million segments is already far beyond any
    realistic contour/habitat dataset this tool is meant for."""
    p0_parts, p1_parts = [], []
    n_features = 0
    n_segments = 0
    with arcpy.da.SearchCursor(fc, ["SHAPE@"]) as cur:
        for (shape,) in cur:
            if shape is None:
                continue
            # True-curve polylines expose only arc endpoints as vertices;
            # densify so the segment sampling follows the arc, not the chord.
            if getattr(shape, "hasCurves", False):
                try:
                    shape = shape.densify(
                        "DISTANCE", max(shape.length / 500.0, 1e-6),
                        max(shape.length / 500.0, 1e-6))
                except Exception:
                    pass
            n_features += 1
            for part in shape:
                pts = [(pt.X, pt.Y) for pt in part if pt is not None]
                if len(pts) < 2:
                    continue
                n_segments += len(pts) - 1
                if n_segments > _MAX_SEGMENTS:
                    raise arcpy.ExecuteError(
                        "The input has more than {:,} polyline segments, "
                        "which this tool does not support in one run. "
                        "Select a subset of features, split the data, or "
                        "generalize/simplify it first.".format(_MAX_SEGMENTS))
                arr = np.asarray(pts, dtype=np.float64)
                p0_parts.append(arr[:-1])
                p1_parts.append(arr[1:])
    _hs._msg(messages, "Read {:,} polyline feature(s), {:,} segments.".format(
        n_features, n_segments))
    if n_features == 0:
        raise arcpy.ExecuteError(
            "No input polylines were found (check the feature selection, "
            "if any).")
    if n_segments == 0:
        raise arcpy.ExecuteError(
            "The input features contain no line segments (all geometries "
            "are empty or single-vertex), so there is nothing to measure.")
    return np.concatenate(p0_parts), np.concatenate(p1_parts)


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

    p0s, p1s = _polyline_segments(in_polylines, messages)

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
    _check_output_size(width, height)
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
