# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award Fund notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Row-strip processing for raster tools whose every step is a bounded
neighborhood (a focal window, a 3 x 3 gradient) or cell by cell.

A tool that used to hold the whole raster in memory reads it instead in
strips of rows, each strip padded above and below by a HALO of as many
rows as the largest neighborhood radius, so every output row sees its full
neighborhood and the result is the same as the whole-raster computation
(bit-identical where the convolution is direct, rounding-level where it is
the overlap-add FFT, whose block layout depends on the array height). The
strip height is chosen so the rows READ fit a memory budget; a small raster
is one strip. Memory is therefore bounded by halo x width, not by the
number of cells. The north-south radius is a constant number of cells even
on a geographic DEM (only the east-west radius varies by row), so a row
halo is exact.

These primitives were written for the TPI engine (tpi_tools.py, which keeps
its own copies, tested by its suites) and are shared here by the other
focal tools: the Vector Ruggedness Measure, the blurred hillshade, the
Dilts local detrending, TRI, the arc-chord ratio, the fractal dimension,
LSRI and the aspect tools. Tools whose result at a cell depends on the whole
surface (flow accumulation, prominence, least-cost paths, connected patches)
cannot use them.

Whole-raster statistics a tool needs before its strip pass (a mean and
standard deviation, a minimum and maximum) are streamed in a first pass over
the same strips.
"""
import math
import os
import time
import uuid

import arcpy
import numpy as np

import hillshade as _hs
import mahalanobis as _mh

STRIP_BUDGET_BYTES = 1 << 30      # working memory per strip (rows READ, halo included)
STRIP_ROWS_OVERRIDE = None        # tests: force this many output rows per strip
_TILE_ROWS = 256                  # our GeoTIFF tile height: strips align to it


def halo_rows(ry):
    """Rows of context a neighborhood of N-S radius ``ry`` cells needs on each
    side of a strip (the kernel's half-height)."""
    return int(math.ceil(max(float(ry), 0.5)))


def strip_rows(height, width, halo, bytes_per_cell, budget=None):
    """Output rows per strip. The budget covers the rows READ (output rows
    plus the halo on each side), and a strip is never shorter than twice the
    halo, so no row is read or convolved more than about twice however large
    the neighborhood. Aligned to the output tile height when there is room."""
    height = int(height)
    if STRIP_ROWS_OVERRIDE:
        return max(1, min(int(STRIP_ROWS_OVERRIDE), height))
    budget = STRIP_BUDGET_BYTES if budget is None else budget
    rows_read = int(budget // max(int(width) * int(bytes_per_cell), 1))
    floor = max(2 * int(halo), 1)
    rows = max(rows_read - 2 * int(halo), floor)
    if rows >= _TILE_ROWS and rows - rows % _TILE_ROWS >= floor:
        rows -= rows % _TILE_ROWS
    return min(rows, height)


def strip_ranges(height, rows):
    """[(y0, y1)] output-row ranges covering the raster."""
    height = int(height)
    rows = max(int(rows), 1)
    return [(y, min(y + rows, height)) for y in range(0, height, rows)]


def block_ranges(height, rows, halo):
    """[(y0, y1, top, bot)]: output rows y0..y1-1 computed from the rows
    top..bot-1 read with the halo (clipped to the raster)."""
    height = int(height)
    halo = int(halo)
    return [(y0, y1, max(0, y0 - halo), min(height, y1 + halo))
            for y0, y1 in strip_ranges(height, rows)]


def strip_note(height, width, rows, halo, bytes_per_cell):
    """The one run message stating the strip layout."""
    n = len(strip_ranges(height, rows))
    read = min(rows + 2 * halo, height)
    mb = read * width * bytes_per_cell / float(2 ** 20)
    mem = ("about {:,.0f} MB".format(mb) if mb < 1024
           else "about {:.1f} GB".format(mb / 1024.0))
    if n == 1:
        return ("Processing the raster as one strip of {:,} rows ({} of "
                "working memory).".format(height, mem))
    h = ("{} halo row{} on each side".format(halo, "" if halo == 1 else "s")
         if halo else "no halo rows")
    return ("Processing in {:,} strips of {:,} rows with {} ({} of working "
            "memory per strip).".format(n, rows, h, mem))


def plan(height, width, halo, bytes_per_cell, messages=None):
    """Choose the strip height, post the strip note, and return
    (rows, block_ranges)."""
    rows = strip_rows(height, width, halo, bytes_per_cell)
    _hs._msg(messages, strip_note(height, width, rows, halo, bytes_per_cell))
    return rows, block_ranges(height, rows, halo)


def eta_text(seconds):
    """'about 2 h 05 min', 'about 12 min' or 'about 45 s'."""
    s = max(float(seconds), 0.0)
    if s >= 3600.0:
        h = int(s // 3600.0)
        m = int(round((s - 3600.0 * h) / 60.0))
        if m == 60:
            h, m = h + 1, 0
        return "about {} h {:02d} min".format(h, m)
    if s >= 90.0:
        return "about {:.0f} min".format(s / 60.0)
    return "about {:.0f} s".format(max(s, 1.0))


class Progress(object):
    """Strip-by-strip progress inside the current phase of the run's
    progressor (mahalanobis.begin_phases / phase_step). The bar advances
    with every strip, or with every part of a strip the tool reports, and
    the label names the strip, its rows and the time the phase has left,
    estimated from the strips done so far (the strips are alike in cost, so
    the estimate settles after the first one). ``blocks`` are the planned
    ranges, (y0, y1, ...) tuples; ``label`` is the phase's text without its
    trailing dots. Harmless outside a geoprocessing run (every arcpy call
    is guarded by the mahalanobis helpers)."""

    def __init__(self, label, blocks):
        self.label = str(label)
        self.ranges = [(int(b[0]), int(b[1])) for b in blocks]
        self.n = max(len(self.ranges), 1)
        self.t0 = time.time()
        self.t_label = 0.0
        self.last = -1.0
        self.last_label = None

    def update(self, i, frac=1.0, label=None, bar_only=False):
        """Strip ``i`` (0-based) is ``frac`` done: 0.0 as it starts, 1.0 when
        it is written; in between for the parts of a strip. ``label`` names
        the current stage of the strip instead of the phase text; with
        ``bar_only`` the caller writes its own label."""
        frac = min(max(float(frac), 0.0), 1.0)
        done = min(i + frac, float(self.n))
        if done < self.last:
            return                          # never move the bar backwards
        self.last = done
        _mh.phase_step(done, self.n)
        if bar_only:
            return
        now = time.time()
        stage = label or self.label
        if (0.0 < frac < 1.0 and now - self.t_label < 1.0
                and stage == self.last_label):
            return                          # parts of a strip: at most once a second
        self.t_label = now
        self.last_label = stage
        _mh._set_label(self.text(i, frac, now - self.t0, stage))

    def text(self, i, frac, elapsed, label=None):
        i = min(max(int(i), 0), self.n - 1)
        y0, y1 = self.ranges[i] if self.ranges else (0, 0)
        if self.n == 1:
            where = "{:.0f}% done".format(100.0 * frac) if frac < 1.0 else "done"
        else:
            where = "strip {:,} of {:,} (rows {:,} to {:,})".format(i + 1, self.n, y0 + 1, y1)
            if 0.0 < frac < 1.0:
                where += ", {:.0f}%".format(100.0 * frac)
        done = i + frac
        if 0.0 < done < self.n and elapsed > 2.0:
            left = elapsed * (self.n - done) / done
            return "{}: {}, {} remaining".format(label or self.label, where, eta_text(left))
        return "{}: {}".format(label or self.label, where)


def row_select(rows, row0, H):
    """Restrict a whole-raster row selector (a slice(None) or a boolean array
    over all rows, as the row-kernel groups yield) to a block of H rows
    starting at raster row ``row0``. Returns a slice or boolean array, or
    None when no row of the block belongs to the group."""
    if isinstance(rows, slice):
        return rows
    sel = np.asarray(rows, dtype=bool)[row0:row0 + H]
    return sel if sel.any() else None


class Source(object):
    """A raster opened for reading by row blocks (GDAL; a format GDAL cannot
    read is exported to a scratch GeoTIFF by hillshade._open_source, as
    before). ``read(top, bot)`` returns (values as float64, valid mask) for
    rows top..bot-1 with the band's NoData value and its mask band applied;
    ``close`` releases the dataset and deletes the scratch copy.
    ``check_size`` False skips the very-large-raster prompt (for a tool's
    own scratch rasters)."""

    def __init__(self, path, scratch, messages, check_size=True):
        self.path = str(path)
        self.ds, self.copy = _hs._open_source(path, scratch, messages,
                                              check_size=check_size)
        self.band = self.ds.GetRasterBand(1)
        self.width = int(self.ds.RasterXSize)
        self.height = int(self.ds.RasterYSize)
        self.gt = self.ds.GetGeoTransform()
        self.proj = self.ds.GetProjection()
        self.nodata = self.band.GetNoDataValue()

    def read(self, top, bot):
        top, bot = int(top), int(bot)
        arr = self.band.ReadAsArray(0, top, self.width, bot - top).astype(np.float64)
        valid = np.isfinite(arr)
        if self.nodata is not None:
            valid &= (arr != self.nodata)
        valid = _mh.mask_band_valid(self.band, valid, 0, top)
        return arr, valid

    def close(self):
        self.band = None
        self.ds = None
        if self.copy:
            try:
                arcpy.management.Delete(self.copy)
            except Exception:
                pass
            self.copy = None


def scratch_tif(scratch, base):
    """A new GeoTIFF path in the scratch folder (not arcpy.CreateUniqueName,
    which opens the rasters it finds and fails on a half-written one)."""
    for _ in range(1000):
        p = os.path.join(scratch, "{}_{}.tif".format(base, uuid.uuid4().hex[:10]))
        if not os.path.exists(p) and not os.path.exists(p + ".aux.xml"):
            return p
    raise arcpy.ExecuteError("Could not find a free scratch name in {}".format(scratch))


class ScratchWriter(object):
    """An output written strip by strip to a scratch GeoTIFF (tiled, LZW,
    BigTIFF when needed) with streaming statistics. ``finish`` stamps the
    statistics on the file and returns (path, out_stats) for the tool's own
    delivery step; ``abandon`` removes the file after a failure."""

    def __init__(self, gdal, scratch, base, gt, proj, width, height,
                 dtype=None, nodata=-9999.0, stats=True):
        self.gdal = gdal
        self.path = scratch_tif(scratch, base)
        self.nodata = nodata
        drv = gdal.GetDriverByName("GTiff")
        self.ds = drv.Create(self.path, int(width), int(height), 1,
                             gdal.GDT_Float32 if dtype is None else dtype,
                             options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
        self.ds.SetGeoTransform(gt)
        try:
            self.ds.SetProjection(proj)
        except Exception:
            pass
        self.band = self.ds.GetRasterBand(1)
        self.band.SetNoDataValue(nodata)
        self.np_dtype = {gdal.GDT_Float32: np.float32, gdal.GDT_Int16: np.int16,
                         gdal.GDT_Byte: np.uint8, gdal.GDT_Int32: np.int32,
                         gdal.GDT_Float64: np.float64}.get(
                             gdal.GDT_Float32 if dtype is None else dtype, np.float32)
        self.stats = _mh._RasterStats() if stats else None

    def write(self, y0, grid, valid):
        """Write the rows of ``grid`` starting at raster row ``y0``; cells
        not ``valid`` become NoData."""
        self.band.WriteArray(
            np.where(valid, grid, self.nodata).astype(self.np_dtype), 0, int(y0))
        if self.stats is not None and valid.any():
            self.stats.add(np.asarray(grid[valid], dtype=np.float64))

    def finish(self, messages=None):
        out_stats = None
        if self.stats is not None:
            out_stats = _mh._finalize_stats(self.band, self.stats, messages)
            if out_stats is not None:
                out_stats["on_raster"] = _mh._write_gtiff_stats(self.band, out_stats)
        self.band.FlushCache()
        self.ds.FlushCache()
        self.band = None
        self.ds = None
        return self.path, out_stats

    def abandon(self):
        self.band = None
        self.ds = None
        try:
            os.remove(self.path)
        except Exception:
            pass


def whole_stats(src, rows):
    """Mean and population SD of a raster's valid cells, streamed in strips
    (two passes: the mean, then the squared deviations, as numpy's std does
    in memory). None when no cell is valid."""
    n = 0
    s = 0.0
    ranges = strip_ranges(src.height, rows)
    for y0, y1 in ranges:
        arr, v = src.read(y0, y1)
        vals = arr[v]
        n += int(vals.size)
        s += float(vals.sum())
    if n == 0:
        return None
    mu = s / n
    ss = 0.0
    for y0, y1 in ranges:
        arr, v = src.read(y0, y1)
        d = arr[v] - mu
        ss += float((d * d).sum())
    return mu, math.sqrt(ss / n)


def whole_minmax(src, rows):
    """Minimum and maximum of a raster's valid cells, streamed in strips.
    None when no cell is valid."""
    lo = hi = None
    for y0, y1 in strip_ranges(src.height, rows):
        arr, v = src.read(y0, y1)
        if v.any():
            a, b = float(arr[v].min()), float(arr[v].max())
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    return None if lo is None else (lo, hi)
