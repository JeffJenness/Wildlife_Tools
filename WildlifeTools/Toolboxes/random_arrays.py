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

"""Random Sample Array Generator -- randomly placed ARRAYS of sampling
points within polygons, along polylines, or within the data cells of an
integer raster.

The companion of the Random Point Generator: the same areas of
interest, the same STRATA x ALLOCATION sampling design (see
``random_points``), the same working-projection and seed machinery --
but each sample is an ARRAY of points built around a randomly placed
focal location, the standard field design of multiple sampling
stations per plot. Counts, spacing and boundary distances all refer to
the ARRAYS, not the points inside them.

ARRAY SHAPES: a GRID of rows x columns at a point spacing; a
TRIANGULAR array of n points on a triangular lattice taken in order of
increasing distance from the focal point (large n fills a circle); a
CIRCLE of points evenly spaced at a radius (optionally including the
center point, which then counts toward the point total; optionally
starting at a random angle); and, for polylines only, a
ONE-DIMENSIONAL array of points at a fixed interval ALONG the line,
centered at the focal point -- walking straight through junctions it
passes, and when the walk reaches the END of a part where more than
one continuation exists (a Y confluence) one branch is picked
deterministically and a warning reports how many arrays did so.

ORIENTATION (grid and triangular): a fixed angle (degrees clockwise
from north; at zero a grid's columns run north-south), a fresh random
angle for every array, or -- on polylines -- aligned to the local line
direction (rows run along the line).

CONSTRAINTS refer to array FOOTPRINTS: the minimum distance between
arrays is between their NEAREST points (never centers), and the
minimum distance from the area boundary applies to every point of the
array. The "entire array must fit" choice controls containment: when
ON (the default) a placement whose array leaves the polygon (or lands
on NoData, or runs off the end of a line) is rejected and redrawn
within the attempts budget, so every array is complete -- consistent
sampling; when OFF arrays may extend beyond the area (the boundary
distance is then meaningless and ignored). The full array is generated
in either case.
"""
import math

import numpy as np

import mahalanobis as _mh
import patch_analysis as _pa
import random_points as _rp
import repeating_shapes as _rs
import voronoi_tools as _vt

# design vocabulary shared with the Random Point Generator
canon_strata = _rp.canon_strata
canon_alloc = _rp.canon_alloc
canon_spacing = _rp.canon_spacing
STRATA_POLYGON = _rp.STRATA_POLYGON
STRATA_POLYLINE = _rp.STRATA_POLYLINE
STRATA_RASTER = _rp.STRATA_RASTER
STRATA_EXTENT = _rp.STRATA_EXTENT
SPACING_TYPES = _rp.SPACING_TYPES
DENSITY_UNITS = _rp.DENSITY_UNITS
DENSITY_UNITS_LINE = _rp.DENSITY_UNITS_LINE
LINEAR_UNITS = _rp.LINEAR_UNITS
_LIN_M = _rp._LIN_M
_say = _rp._say
_warn = _rp._warn

# allocation display lists reworded for ARRAYS (canon_alloc's prefix
# matching still resolves them)
_A_DENS_AREA = "By density (arrays per area unit)"
_A_DENS_LINE = "By density (arrays per length unit)"
ALLOC_UNSTRAT = ["Fixed total number of arrays", _A_DENS_AREA]
ALLOC_UNSTRAT_LINE = ["Fixed total number of arrays", _A_DENS_LINE]
ALLOC_STRAT = ["Equal count in each stratum",
               "Count proportional to stratum area",
               "Count equal to a population field",
               "Count proportional to a population field",
               _A_DENS_AREA]
ALLOC_STRAT_LINE = ["Equal count in each stratum",
                    "Count proportional to stratum length",
                    "Count equal to a population field",
                    "Count proportional to a population field",
                    _A_DENS_LINE]

# --- array shapes ------------------------------------------------------
SHAPE_GRID = "GRID"
SHAPE_TRI = "TRI"
SHAPE_CIRCLE = "CIRCLE"
SHAPE_LINE = "LINE1D"

_SH_GRID = "Grid (rows and columns of points)"
_SH_TRI = "Triangular (a triangular lattice filling a circle)"
_SH_CIRCLE = "Circular (points evenly spaced on a circle)"
_SH_LINE = "Along the polyline (a one-dimensional array of points)"
SHAPES_AREA = [_SH_GRID, _SH_TRI, _SH_CIRCLE]
SHAPES_LINE = [_SH_GRID, _SH_TRI, _SH_CIRCLE, _SH_LINE]


def canon_shape(s):
    if s in (SHAPE_GRID, SHAPE_TRI, SHAPE_CIRCLE, SHAPE_LINE):
        return s
    t = str(s or "").strip().lower()
    if not t or t.startswith("grid"):
        return SHAPE_GRID
    if t.startswith("triang"):
        return SHAPE_TRI
    if t.startswith("circ"):
        return SHAPE_CIRCLE
    if t.startswith("along"):
        return SHAPE_LINE
    return None


ORIENT_FIXED = "FIXED"
ORIENT_RANDOM = "RANDOM"
ORIENT_ALIGN = "ALIGN"
ORIENT_MODES = ["Fixed orientation angle",
                "Random orientation for each array",
                "Align each array to the polyline direction"]
ORIENT_MODES_AREA = ORIENT_MODES[:2]


def canon_orient(s):
    if s in (ORIENT_FIXED, ORIENT_RANDOM, ORIENT_ALIGN):
        return s
    t = str(s or "").strip().lower()
    if not t or t.startswith("fixed"):
        return ORIENT_FIXED
    if t.startswith("random"):
        return ORIENT_RANDOM
    if t.startswith("align"):
        return ORIENT_ALIGN
    return None


# ---------------------------------------------------------------------------
#  footprint spacing structures: CHECK all of a candidate array's
#  points first, ADD them only when the whole array is admitted.
#  (random_points' _SpacingGrid cannot hold them -- its one-point-per-
#  cell invariant assumes admitted points are themselves >= r apart,
#  and an array's own points are deliberately closer.)
# ---------------------------------------------------------------------------
class _FootprintGrid:
    """Hash grid over EVERY admitted array point; a candidate point
    conflicts when any admitted point lies within r. Cells hold lists
    (cell size r/sqrt(2), so conflicts lie in the 5x5 block)."""

    def __init__(self, r):
        self.r2 = r * r
        self.cell = r / math.sqrt(2.0)
        self.grid = {}

    def check(self, x, y):
        ci = int(math.floor(x / self.cell))
        cj = int(math.floor(y / self.cell))
        g = self.grid
        for di in (-2, -1, 0, 1, 2):
            for dj in (-2, -1, 0, 1, 2):
                for px, py in g.get((ci + di, cj + dj), ()):
                    dx = x - px
                    dy = y - py
                    if dx * dx + dy * dy < self.r2:
                        return False
        return True

    def add(self, x, y):
        ci = int(math.floor(x / self.cell))
        cj = int(math.floor(y / self.cell))
        self.grid.setdefault((ci, cj), []).append((x, y))


class _AlongCheck:
    """Along-the-line footprint spacing: admitted (part, s) positions
    per part; a candidate conflicts when an admitted position on the
    SAME part lies within r along the line."""

    def __init__(self, r):
        self.r = r
        self.by_part = {}

    def check(self, part, s):
        import bisect

        lst = self.by_part.get(part)
        if not lst:
            return True
        i = bisect.bisect_left(lst, s)
        if i > 0 and s - lst[i - 1] < self.r:
            return False
        if i < len(lst) and lst[i] - s < self.r:
            return False
        return True

    def add(self, part, s):
        import bisect

        lst = self.by_part.setdefault(part, [])
        lst.insert(bisect.bisect_left(lst, s), s)


class _NetCheck(_rp._NetworkSpacing):
    """Network-distance footprint spacing: _NetworkSpacing's junction
    graph and admitted registry, split into a pure CHECK and an
    unconditional ADD."""

    def check(self, part, s):
        import bisect
        import heapq

        r = self.r
        lst = self.by_part.get(part, [])
        i = bisect.bisect_left(lst, s)
        if i > 0 and s - lst[i - 1] < r:
            return False
        if i < len(lst) and lst[i] - s < r:
            return False
        best = {}
        heap = []
        for s_j, node in self.jn_on_part.get(part, ()):
            d0 = abs(s - s_j)
            if d0 < r and d0 < best.get(node, 1e300):
                best[node] = d0
                heapq.heappush(heap, (d0, node))
        while heap:
            d, node = heapq.heappop(heap)
            if d > best.get(node, 1e300):
                continue
            for q, s_q in self.node_locs[node]:
                rem = r - d
                lq = self.by_part.get(q)
                if lq:
                    j = bisect.bisect_left(lq, s_q - rem)
                    if j < len(lq) and lq[j] < s_q + rem:
                        return False
            for nb, w in self.adj.get(node, ()):
                nd = d + w
                if nd < r and nd < best.get(nb, 1e300):
                    best[nb] = nd
                    heapq.heappush(heap, (nd, nb))
        return True

    def add(self, part, s):
        import bisect

        lst = self.by_part.setdefault(part, [])
        lst.insert(bisect.bisect_left(lst, s), s)


# ---------------------------------------------------------------------------
#  array offset builders (work-SR linear units, focal point at origin)
# ---------------------------------------------------------------------------
def _offsets_grid(n_rows, n_cols, s):
    """(offsets (k,2), rowcol (k,2) 1-based from the NW corner). At
    zero orientation columns run north-south, rows east-west."""
    offs, rowcol = [], []
    for i in range(n_rows):
        y = ((n_rows - 1) / 2.0 - i) * s
        for j in range(n_cols):
            x = (j - (n_cols - 1) / 2.0) * s
            offs.append((x, y))
            rowcol.append((i + 1, j + 1))
    return (np.asarray(offs, dtype=np.float64),
            np.asarray(rowcol, dtype=np.int64))


def _offsets_tri(n, s):
    """n points of the triangular lattice (spacing s) in order of
    increasing distance from the focal point (ties broken by azimuth,
    deterministic for seed reproducibility). Point 1 is the focal
    point itself; large n fills a circle."""
    if n <= 1:
        return np.zeros((1, 2))
    # lattice basis (s, 0) and (s/2, s*sqrt(3)/2); generate a disc
    # comfortably larger than the n nearest points need
    density = 2.0 / (math.sqrt(3.0) * s * s)
    R = math.sqrt(n / (density * math.pi)) * 1.6 + 2.0 * s
    imax = int(math.ceil(R / (s * math.sqrt(3.0) / 2.0))) + 1
    jmax = int(math.ceil(R / s)) + imax
    pts = []
    for i in range(-imax, imax + 1):
        y = i * s * math.sqrt(3.0) / 2.0
        x0 = (i % 2) * s / 2.0
        for j in range(-jmax, jmax + 1):
            x = x0 + j * s
            if x * x + y * y <= R * R:
                pts.append((x, y))
    a = np.asarray(pts, dtype=np.float64)
    d = np.hypot(a[:, 0], a[:, 1])
    az = np.degrees(np.arctan2(a[:, 0], a[:, 1])) % 360.0
    order = np.lexsort((az, np.round(d, 9)))
    return a[order[:n]]


def _offsets_circle(n, radius, include_center, start_deg):
    """(offsets (n,2), bearings (n,)) -- k = n - include_center points
    evenly spaced on the circle starting at ``start_deg`` (azimuth,
    clockwise from north); the center point, when included, is FIRST
    and counts toward n (its bearing is NaN)."""
    k = n - (1 if include_center else 0)
    offs, brgs = [], []
    if include_center:
        offs.append((0.0, 0.0))
        brgs.append(float("nan"))
    if k > 0:
        step = 360.0 / k
        for i in range(k):
            az = (start_deg + i * step) % 360.0
            rad = math.radians(az)
            offs.append((radius * math.sin(rad),
                         radius * math.cos(rad)))
            brgs.append(az)
    return (np.asarray(offs, dtype=np.float64),
            np.asarray(brgs, dtype=np.float64))


def _rotate_cw(offs, deg):
    """Offsets rotated clockwise (map sense) by ``deg``."""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    x, y = offs[:, 0], offs[:, 1]
    return np.column_stack([x * c + y * s, -x * s + y * c])


def _rotate_to_dir(offs, dx, dy):
    """Offsets rotated so the array's x-axis (row direction) lies
    along the unit-ish direction (dx, dy)."""
    h = math.hypot(dx, dy)
    if h <= 0:
        return offs
    dx, dy = dx / h, dy / h
    x, y = offs[:, 0], offs[:, 1]
    return np.column_stack([x * dx - y * dy, x * dy + y * dx])


# ---------------------------------------------------------------------------
#  polyline walker for the one-dimensional array: emit points at fixed
#  intervals along the line, straight through interior junctions, one
#  deterministic branch (flagged) at Y confluences, fail at dead ends.
# ---------------------------------------------------------------------------
class _LineWalker:
    def __init__(self, groups):
        self.chains = {}
        coords_min = [np.inf, np.inf]
        coords_max = [-np.inf, -np.inf]
        for parts in groups:
            for uid, a in parts:
                seg = np.hypot(a[1:, 0] - a[:-1, 0],
                               a[1:, 1] - a[:-1, 1])
                cum = np.concatenate([[0.0], np.cumsum(seg)])
                self.chains[uid] = (a, cum)
                coords_min[0] = min(coords_min[0], a[:, 0].min())
                coords_min[1] = min(coords_min[1], a[:, 1].min())
                coords_max[0] = max(coords_max[0], a[:, 0].max())
                coords_max[1] = max(coords_max[1], a[:, 1].max())
        span = max(coords_max[0] - coords_min[0],
                   coords_max[1] - coords_min[1], 1.0)
        self.tol = max(1e-3, span * 1e-9)
        self.net = _rp._NetworkSpacing(1.0)
        self.net.build(groups)

    def xy_at(self, uid, s):
        a, cum = self.chains[uid]
        s = min(max(s, 0.0), float(cum[-1]))
        i = int(np.clip(np.searchsorted(cum, s, side="right") - 1,
                        0, len(a) - 2))
        seg = cum[i + 1] - cum[i]
        t = (s - cum[i]) / seg if seg > 0 else 0.0
        return (a[i, 0] + t * (a[i + 1, 0] - a[i, 0]),
                a[i, 1] + t * (a[i + 1, 1] - a[i, 1]))

    def dir_at(self, uid, s):
        a, cum = self.chains[uid]
        i = int(np.clip(np.searchsorted(cum, s, side="right") - 1,
                        0, len(a) - 2))
        return (a[i + 1, 0] - a[i, 0], a[i + 1, 1] - a[i, 1])

    def _continue_at(self, uid, s_end, d):
        """Continuations at the end of part ``uid`` (arrived moving
        ``d``): [(part, s, dir), ...] excluding the way back."""
        node = None
        for s_j, nd in self.net.jn_on_part.get(uid, ()):
            if abs(s_j - s_end) <= self.tol:
                node = nd
                break
        if node is None:
            return []
        opts = []
        for q, s_q in self.net.node_locs[node]:
            _a, cum = self.chains[q]
            L = float(cum[-1])
            if q == uid and abs(s_q - s_end) <= self.tol:
                continue                       # the way back
            if s_q <= self.tol:
                opts.append((q, 0.0, 1))
            elif s_q >= L - self.tol:
                opts.append((q, L, -1))
            else:                              # a T junction interior
                opts.append((q, s_q, 1))
                opts.append((q, s_q, -1))
        opts.sort()
        return opts

    def advance(self, state, dist, visited=None):
        """Move ``dist`` (> 0) along the line from ``state`` = (uid, s,
        d). Returns (new state, ambiguous, ok). ``visited`` -- a dict
        of part uid -> [(lo, hi), ...] intervals already traversed by
        the CURRENT walk -- detects the walk wrapping a closed loop
        (or network cycle) back onto line it has already covered;
        that is treated as running off the line (ok False), so
        must-fit placements on short closed loops are rejected
        instead of silently stacking coincident stations."""
        uid, s, d = state
        ambiguous = False
        crossings = 0
        remaining = float(dist)
        while True:
            _a, cum = self.chains[uid]
            L = float(cum[-1])
            room = (L - s) if d > 0 else s
            if visited is not None:
                step = min(remaining, room)
                if step > 0:
                    lo, hi = (s, s + step) if d > 0 else (s - step, s)
                    for lo2, hi2 in visited.get(uid, ()):
                        if min(hi, hi2) - max(lo, lo2) > self.tol:
                            return (uid, s, d), ambiguous, False
                    visited.setdefault(uid, []).append((lo, hi))
            if remaining <= room + 1e-12:
                return (uid, s + d * remaining, d), ambiguous, True
            remaining -= room
            s_end = L if d > 0 else 0.0
            opts = self._continue_at(uid, s_end, d)
            if not opts:
                return (uid, s_end, d), ambiguous, False
            if len(opts) > 1:
                ambiguous = True
            uid, s, d = opts[0]
            crossings += 1
            if crossings > 1000:
                return (uid, s, d), ambiguous, False

    def walk_array(self, uid, s0, n, step, gap):
        """The 1-D array centered at (uid, s0): n points ``step``
        apart; ``gap`` extra clearance beyond both end points (0 for
        none). Each half is walked OUTWARD from the focal point (so
        the array always passes through it); odd n puts a point on
        the focal location itself, even n straddles it. Returns
        (points [(part, s, x, y)] ordered from one end to the other,
        ambiguous, ok). Both halves (and the gap clearances) share
        one visited-interval record, so an array that wraps a closed
        loop -- from either end, or the two halves meeting around the
        back -- is rejected as running off the line."""
        amb = False
        visited = {}

        def half_walk(direction, offsets):
            nonlocal amb
            out = []
            state = (uid, s0, direction)
            prev = 0.0
            for off in offsets:
                state, a, ok = self.advance(state, off - prev,
                                            visited=visited)
                amb = amb or a
                if not ok:
                    return None
                prev = off
                x, y = self.xy_at(state[0], state[1])
                out.append((state[0], state[1], x, y))
            if gap and gap > 0:
                _st, a, ok = self.advance(state, gap,
                                          visited=visited)
                amb = amb or a
                if not ok:
                    return None
            return out

        if n % 2:
            k_side = (n - 1) // 2
            offs = [(i + 1) * step for i in range(k_side)]
            neg = half_walk(-1, offs)
            if neg is None:
                return [], amb, False
            pos = half_walk(1, offs)
            if pos is None:
                return [], amb, False
            fx, fy = self.xy_at(uid, s0)
            pts = list(reversed(neg)) + [(uid, s0, fx, fy)] + pos
        else:
            k_side = n // 2
            offs = [(i + 0.5) * step for i in range(k_side)]
            neg = half_walk(-1, offs)
            if neg is None:
                return [], amb, False
            pos = half_walk(1, offs)
            if pos is None:
                return [], amb, False
            pts = list(reversed(neg)) + pos
        return pts, amb, True


# ---------------------------------------------------------------------------
#  allocation (the Random Point Generator's, reworded for arrays)
# ---------------------------------------------------------------------------
def _alloc_arrays(alloc, labels, measures, pops, n_arrays,
                  n_per_stratum, density, density_units, unit_m,
                  unit_word, messages):
    import arcpy

    k = len(labels)
    if alloc in (_rp.ALLOC_TOTAL, _rp.ALLOC_PROP_AREA):
        n = max(0, int(n_arrays or 0))
        if n <= 0:
            raise arcpy.ExecuteError(
                "Give a total number of arrays greater than zero.")
        counts = _rp._apportion(n, measures)
    elif alloc == _rp.ALLOC_EQUAL:
        n = max(0, int(n_per_stratum or 0))
        if n <= 0:
            raise arcpy.ExecuteError(
                "Give a per-stratum number of arrays greater than "
                "zero.")
        counts = np.full(k, n, dtype=np.int64)
    elif alloc == _rp.ALLOC_EQUAL_FIELD:
        counts = np.array([max(0, int(round(p or 0))) for p in pops],
                          dtype=np.int64)
    elif alloc == _rp.ALLOC_PROP_FIELD:
        n = max(0, int(n_arrays or 0))
        if n <= 0:
            raise arcpy.ExecuteError(
                "Give a total number of arrays greater than zero.")
        counts = _rp._apportion(n, [max(0.0, float(p or 0))
                                    for p in pops])
    else:                                       # ALLOC_DENSITY
        d = float(density or 0)
        if d <= 0:
            raise arcpy.ExecuteError(
                "Give a density greater than zero.")
        counts = np.array([int(round(d * m / unit_m))
                           for m in measures], dtype=np.int64)
        _say(messages,
             "Density {:g} arrays per {} of {} (geodesic) -> {:,} "
             "arrays in all.".format(d, density_units.lower(),
                                     unit_word, int(counts.sum())))
    if counts.sum() <= 0:
        raise arcpy.ExecuteError(
            "The requested counts work out to zero arrays.")
    return counts


# ---------------------------------------------------------------------------
#  the array stamp: offsets for one candidate placement
# ---------------------------------------------------------------------------
class _ArrayDesign:
    """Everything needed to stamp one array around a focal point, in
    work-SR linear units."""

    def __init__(self, shape, n_rows, n_cols, npp, spacing_cs,
                 radius_cs, orient, angle_deg, randomize_start,
                 include_center, rng):
        self.shape = shape
        self.orient = orient
        self.angle = float(angle_deg or 0.0)
        self.randomize_start = bool(randomize_start)
        self.include_center = bool(include_center)
        self.rng = rng
        self.rowcol = None
        self.spacing_cs = spacing_cs
        self.radius_cs = radius_cs
        self.n_rows = n_rows
        self.n_cols = n_cols
        if shape == SHAPE_GRID:
            self.base, self.rowcol = _offsets_grid(n_rows, n_cols,
                                                   spacing_cs)
            self.npp = n_rows * n_cols
        elif shape == SHAPE_TRI:
            self.base = _offsets_tri(npp, spacing_cs)
            self.npp = int(npp)
        elif shape == SHAPE_CIRCLE:
            self.npp = int(npp)
            self.base = None                   # built per array
        else:                                  # SHAPE_LINE
            self.npp = int(npp)
            self.base = None
        # attempt-invariant work hoisted out of stamp(): the
        # fixed-angle rotation and the non-randomized circle never
        # change between candidate placements
        self.base_fixed = None
        if self.base is not None:
            self.base_fixed = _rotate_cw(self.base, self.angle) \
                if self.angle else self.base
        self.circle_static = None
        if shape == SHAPE_CIRCLE:
            k = self.npp - (1 if self.include_center else 0)
            if not (self.randomize_start and k > 0):
                self.circle_static = _offsets_circle(
                    self.npp, radius_cs, self.include_center, 0.0)

    def max_reach(self):
        """The farthest any point can lie from the focal point."""
        if self.shape == SHAPE_CIRCLE:
            return self.radius_cs
        if self.shape == SHAPE_LINE:
            return (self.npp - 1) / 2.0 * self.spacing_cs
        return float(np.hypot(self.base[:, 0],
                              self.base[:, 1]).max()) \
            if len(self.base) else 0.0

    def min_diameter(self):
        """A SAFE LOWER BOUND on the distance between the array's two
        farthest points, independent of rotation -- used to detect
        placements that cannot possibly fit (a container whose
        bounding-box diagonal is smaller can never hold the array),
        without ever declaring a feasible design impossible."""
        if self.shape == SHAPE_GRID:
            # the opposite corners are actual array points
            return math.hypot((self.n_rows - 1) * self.spacing_cs,
                              (self.n_cols - 1) * self.spacing_cs)
        if self.shape == SHAPE_TRI:
            # the focal point (point 1) and the farthest lattice
            # point are both actual array points
            return self.max_reach()
        if self.shape == SHAPE_CIRCLE:
            k = self.npp - (1 if self.include_center else 0)
            if self.include_center or k >= 2:
                return self.radius_cs      # every chord/center pair
            return 0.0
        return (self.npp - 1) * self.spacing_cs      # SHAPE_LINE

    def stamp(self, fx, fy, line_dir=None):
        """(points (k,2), rowcol, bearings) for one candidate array at
        the focal point -- fresh random rotation / start angle drawn
        per call; fixed orientations reuse the precomputed base."""
        if self.shape == SHAPE_CIRCLE:
            if self.circle_static is not None:
                offs, brgs = self.circle_static
            else:
                k = self.npp - (1 if self.include_center else 0)
                start = float(self.rng.uniform(0.0, 360.0 / k))
                offs, brgs = _offsets_circle(self.npp, self.radius_cs,
                                             self.include_center,
                                             start)
            return offs + (fx, fy), None, brgs
        if self.orient == ORIENT_RANDOM:
            offs = _rotate_cw(self.base, float(self.rng.uniform(
                0.0, 360.0)))
        elif self.orient == ORIENT_ALIGN and line_dir is not None:
            offs = _rotate_to_dir(self.base, line_dir[0], line_dir[1])
        else:
            offs = self.base_fixed
        return offs + (fx, fy), self.rowcol, None

    def focal_is_array_point(self):
        """True when the focal location is itself one of the array's
        points -- the triangular lattice (point 1), an odd-rows x
        odd-columns grid (the center cell), a circle including its
        center, or an odd-count 1-D array -- so in must-fit mode the
        focal point must satisfy every constraint the array points
        do."""
        if self.shape == SHAPE_TRI:
            return True
        if self.shape == SHAPE_GRID:
            return self.n_rows % 2 == 1 and self.n_cols % 2 == 1
        if self.shape == SHAPE_CIRCLE:
            return self.include_center
        return self.npp % 2 == 1               # SHAPE_LINE

    def describe(self, linear_units):
        u = linear_units.lower()
        if self.shape == SHAPE_GRID:
            return ("a {} x {} grid ({} points) at {:g} {} spacing"
                    .format(self.n_rows, self.n_cols, self.npp,
                            self.spacing_cs_user, u))
        if self.shape == SHAPE_TRI:
            return ("{} points on a triangular lattice at {:g} {} "
                    "spacing".format(self.npp, self.spacing_cs_user,
                                     u))
        if self.shape == SHAPE_CIRCLE:
            inc = " including the center point" \
                if self.include_center else ""
            return ("{} points on a circle of radius {:g} {}{}"
                    .format(self.npp, self.radius_cs_user, u, inc))
        return ("{} points every {:g} {} along the line"
                .format(self.npp, self.spacing_cs_user, u))


# ---------------------------------------------------------------------------
#  admission helpers
# ---------------------------------------------------------------------------
def _admit_footprint(spacing, pts, line_ps=None):
    """True when every point of the candidate array clears the
    between-array spacing; on success the points are registered."""
    if spacing is None:
        return True
    if isinstance(spacing, _FootprintGrid):
        for x, y in pts:
            if not spacing.check(float(x), float(y)):
                return False
        for x, y in pts:
            spacing.add(float(x), float(y))
        return True
    # along-line / network: (part, s) footprints
    for part, s in line_ps:
        if not spacing.check(int(part), float(s)):
            return False
    for part, s in line_ps:
        spacing.add(int(part), float(s))
    return True


def _points_in_prep(prep, pts):
    """Boolean: every point inside the prepared polygon."""
    edges, bands, _bbox = prep
    return bool(_pa.points_inside(pts, edges, bands=bands).all())


# ---------------------------------------------------------------------------
#  main entry
# ---------------------------------------------------------------------------
def run_random_arrays(out_fc, boundary=None, extent=None,
                      strata_method=None, strata_field=None,
                      alloc_method=None, n_arrays=None,
                      n_per_stratum=None, population_field=None,
                      density=None, density_units="Hectare",
                      array_shape=None, n_rows=None, n_cols=None,
                      array_points=None, point_spacing=None,
                      radius=None, orientation_mode=None,
                      orientation_angle=None, randomize_start=True,
                      include_center=False, randomize_ids=False,
                      must_fit=True,
                      min_spacing=None, spacing_type=None,
                      boundary_gap=None, linear_units="Meters",
                      random_seed=None, max_attempts=1000,
                      messages=None):
    """Generate the arrays. Counts/spacing/boundary distances refer to
    ARRAYS (footprints); the per-array design comes from
    ``array_shape`` and its parameters. Returns the output path."""
    import arcpy

    extent = _vt.parse_extent(extent) or extent
    strata = canon_strata(strata_method)
    if strata is None:
        raise arcpy.ExecuteError(
            "Unknown stratification method: {}".format(strata_method))
    alloc = canon_alloc(alloc_method)
    if alloc is None:
        alloc = _rp.ALLOC_TOTAL if strata == _rp.STRATA_NONE \
            else _rp.ALLOC_EQUAL
    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown distance unit: {}".format(linear_units))
    shape = canon_shape(array_shape)
    if shape is None:
        raise arcpy.ExecuteError(
            "Unknown array shape: {}".format(array_shape))
    orient = canon_orient(orientation_mode)
    if orient is None:
        orient = ORIENT_FIXED
    must_fit = bool(must_fit) if must_fit is not None else True

    # ---- what kind of area of interest? ----------------------------------
    kind = "extent"
    desc = None
    if boundary not in (None, ""):
        desc = arcpy.Describe(boundary)
        dt = getattr(desc, "dataType", "")
        if dt in ("RasterLayer", "RasterDataset", "RasterBand",
                  "MosaicLayer", "MosaicDataset"):
            kind = "raster"
        else:
            st = getattr(desc, "shapeType", "")
            if st == "Polygon":
                kind = "polygon"
            elif st == "Polyline":
                kind = "polyline"
            else:
                raise arcpy.ExecuteError(
                    "The area of interest must be polygons, polylines "
                    "or an integer raster (this input is "
                    "{}).".format(st or dt))

    # ---- design validity -------------------------------------------------
    if strata != _rp.STRATA_NONE and kind == "extent":
        raise arcpy.ExecuteError(
            "Stratified sampling needs a feature class or raster (an "
            "extent has no strata).")
    if strata == _rp.STRATA_INDIV and kind == "raster":
        raise arcpy.ExecuteError(
            "Stratify by individual polygon/polyline needs a feature "
            "class; for rasters use categories, contiguous regions or "
            "a strata ID field.")
    if strata in (_rp.STRATA_CATEGORY, _rp.STRATA_REGION) \
            and kind != "raster":
        raise arcpy.ExecuteError(
            "Stratifying by raster category or contiguous region "
            "needs an integer raster.")
    if strata == _rp.STRATA_IDFIELD and not strata_field:
        raise arcpy.ExecuteError(
            "Stratify by strata ID field needs the Strata ID field.")
    if alloc in (_rp.ALLOC_EQUAL_FIELD, _rp.ALLOC_PROP_FIELD) \
            and not population_field:
        raise arcpy.ExecuteError(
            "This allocation method needs the Population field.")
    if alloc in (_rp.ALLOC_EQUAL_FIELD, _rp.ALLOC_PROP_FIELD) \
            and strata == _rp.STRATA_NONE:
        raise arcpy.ExecuteError(
            "Population-field allocation needs stratified sampling.")
    if alloc == _rp.ALLOC_DENSITY:
        table = _rp._DENS_LIN_M if kind == "polyline" \
            else _rp._DENS_M2
        if density_units not in table:
            raise arcpy.ExecuteError(
                "Unknown density unit for this input: {} (expected "
                "one of {}).".format(density_units,
                                     ", ".join(sorted(table))))
    if shape == SHAPE_LINE and kind != "polyline":
        raise arcpy.ExecuteError(
            "The along-the-polyline array needs a polyline area of "
            "interest.")
    if orient == ORIENT_ALIGN and kind != "polyline":
        raise arcpy.ExecuteError(
            "Aligning arrays to the polyline direction needs a "
            "polyline area of interest.")
    if shape == SHAPE_GRID:
        if int(n_rows or 0) < 1 or int(n_cols or 0) < 1:
            raise arcpy.ExecuteError(
                "Give at least one row and one column.")
        if float(point_spacing or 0) <= 0:
            raise arcpy.ExecuteError(
                "Give a point spacing greater than zero.")
    elif shape in (SHAPE_TRI, SHAPE_LINE):
        if int(array_points or 0) < 1:
            raise arcpy.ExecuteError(
                "Give the number of points in each array.")
        if float(point_spacing or 0) <= 0:
            raise arcpy.ExecuteError(
                "Give a point spacing greater than zero.")
    else:                                       # circle
        if int(array_points or 0) < 1:
            raise arcpy.ExecuteError(
                "Give the number of points in each array.")
        if float(radius or 0) <= 0:
            raise arcpy.ExecuteError(
                "Give a radius greater than zero.")
        if int(array_points or 0) - (1 if include_center else 0) < 1:
            raise arcpy.ExecuteError(
                "A circular array needs at least one point on the "
                "circle beyond the center point.")
    if not must_fit and boundary_gap not in (None, "", 0):
        _warn(messages,
              "Arrays are allowed to extend beyond the area of "
              "interest, so the minimum distance from the boundary "
              "has no meaning and is ignored.")
        boundary_gap = None

    rng = np.random.default_rng(
        int(random_seed) if random_seed not in (None, "") else None)
    attempts = max(1, int(max_attempts or 1000))

    _mh.begin_phases([("Reading the area of interest...", 1),
                      ("Placing sample arrays...", 4),
                      ("Writing the arrays...", 2)])

    if kind == "raster":
        if canon_spacing(spacing_type) in (_rp.SPACING_ALONG,
                                           _rp.SPACING_NETWORK) \
                and min_spacing not in (None, "", 0):
            _warn(messages,
                  "Along-the-line and network spacing apply to "
                  "polyline areas of interest only -- using absolute "
                  "spatial distance.")
        return _arrays_on_raster(
            out_fc, boundary, desc, strata, strata_field, alloc,
            n_arrays, n_per_stratum, population_field, density,
            density_units, shape, n_rows, n_cols, array_points,
            point_spacing, radius, orient, orientation_angle,
            randomize_start, include_center, must_fit, min_spacing,
            boundary_gap, linear_units, rng, attempts, random_seed,
            messages, strata_txt=strata_method or strata,
            alloc_txt=alloc_method or alloc,
            randomize_ids=randomize_ids)

    # ---- vector / extent: coordinate system + working projection ---------
    src_sr = None
    if desc is not None:
        src_sr = desc.spatialReference
    elif isinstance(extent, arcpy.Extent) \
            and extent.spatialReference is not None:
        src_sr = extent.spatialReference
    out_sr = arcpy.env.outputCoordinateSystem or src_sr
    if out_sr is None or getattr(out_sr, "name",
                                 "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "No coordinate system: give a boundary/extent with one, "
            "or set the Output Coordinate System environment.")
    geographic = out_sr.type == "Geographic"
    if geographic:
        lon0 = lat0 = None
        try:
            if desc is not None:
                be0 = desc.extent
                lon0 = 0.5 * (be0.XMin + be0.XMax)
                lat0 = 0.5 * (be0.YMin + be0.YMax)
            elif isinstance(extent, arcpy.Extent):
                lon0 = 0.5 * (extent.XMin + extent.XMax)
                lat0 = 0.5 * (extent.YMin + extent.YMax)
        except Exception:                                        # noqa: BLE001
            pass
        if lon0 is None:
            raise arcpy.ExecuteError(
                "Give an area-of-interest layer or an extent (or set "
                "the Extent environment).")
        work_sr = _rs._laea_for(out_sr, lon0, lat0)
        _say(messages,
             "Geographic data: computing in an automatic EQUAL-AREA "
             "working projection (Lambert azimuthal, centered at "
             "{:.4f}, {:.4f}); the arrays are built in true meters "
             "and written back in {}.".format(lon0, lat0, out_sr.name))
    else:
        work_sr = out_sr

    m_per_cs = float(work_sr.metersPerUnit or 1.0)
    to_cs = _LIN_M[linear_units] / m_per_cs
    r_cs = float(min_spacing) * to_cs \
        if min_spacing not in (None, "", 0) else None
    gap_cs = float(boundary_gap) * to_cs \
        if boundary_gap not in (None, "", 0) else None
    if r_cs is not None and r_cs <= 0:
        r_cs = None
    if gap_cs is not None and gap_cs <= 0:
        gap_cs = None

    design = _ArrayDesign(
        shape, int(n_rows or 0), int(n_cols or 0),
        int(array_points or 0),
        float(point_spacing or 0) * to_cs,
        float(radius or 0) * to_cs, orient,
        orientation_angle, randomize_start, include_center, rng)
    design.spacing_cs_user = float(point_spacing or 0)
    design.radius_cs_user = float(radius or 0)
    _say(messages, "Each array: {}.".format(
        design.describe(linear_units)))

    smode = canon_spacing(spacing_type)
    if smode is None:
        raise arcpy.ExecuteError(
            "Unknown spacing-measurement choice: {}".format(
                spacing_type))
    if smode in (_rp.SPACING_ALONG, _rp.SPACING_NETWORK) \
            and kind != "polyline":
        if r_cs is not None:
            _warn(messages,
                  "Along-the-line and network spacing apply to "
                  "polyline areas of interest only -- using absolute "
                  "spatial distance.")
        smode = _rp.SPACING_SPATIAL
    spacing = None
    if r_cs is not None:
        if smode == _rp.SPACING_ALONG:
            spacing = _AlongCheck(r_cs)
            _say(messages,
                 "The minimum distance between arrays is measured "
                 "ALONG each polyline (between the arrays' nearest "
                 "points on the same line).")
        elif smode == _rp.SPACING_NETWORK:
            spacing = _NetCheck(r_cs)
            _say(messages,
                 "The minimum distance between arrays is measured "
                 "through the CONNECTED LINE NETWORK (between the "
                 "arrays' nearest points).")
        else:
            spacing = _FootprintGrid(r_cs)
            _say(messages,
                 "The minimum distance between arrays is measured "
                 "between their NEAREST points (footprints, not "
                 "centers).")

    if kind == "polyline":
        rows, starved_any, want_total, n_amb = _arrays_on_lines_core(
            boundary, desc, work_sr, strata, strata_field, alloc,
            n_arrays, n_per_stratum, population_field, density,
            density_units, design, must_fit, gap_cs, spacing, smode,
            rng, attempts, m_per_cs, messages)
        if n_amb:
            _warn(messages,
                  "{:,} array{} crossed a junction where the "
                  "continuation was ambiguous (a Y confluence) -- one "
                  "branch was picked in each case.".format(
                      n_amb, "" if n_amb == 1 else "s"))
    else:
        rows, starved_any, want_total = _arrays_in_polygons_core(
            boundary, extent, desc, work_sr, out_sr, strata,
            strata_field, alloc, n_arrays, n_per_stratum,
            population_field, density, density_units, design,
            must_fit, gap_cs, spacing, rng, attempts, m_per_cs,
            messages)
    _mh.phase_advance()

    src_field = strata in (_rp.STRATA_INDIV, _rp.STRATA_IDFIELD)
    out_path = _write_arrays(
        out_fc, out_sr, work_sr, geographic, rows, design,
        src_field=src_field, cat_field=False,
        strat_field=strata != _rp.STRATA_NONE, messages=messages,
        starved_any=starved_any, want_total=want_total,
        rng=rng, randomize_ids=randomize_ids)
    _stamp_arrays(out_path, kind, strata_method or strata,
                  alloc_method or alloc, design, linear_units,
                  sum(len(a["arrays"]) for a in rows), want_total,
                  boundary, min_spacing, boundary_gap, must_fit,
                  random_seed)
    _mh.end_phases()
    return out_path


# ---------------------------------------------------------------------------
#  polygons / extent core
# ---------------------------------------------------------------------------
def _arrays_in_polygons_core(boundary, extent, desc, work_sr, out_sr,
                             strata, strata_field, alloc, n_arrays,
                             n_per_stratum, population_field, density,
                             density_units, design, must_fit, gap_cs,
                             spacing, rng, attempts, m_per_cs,
                             messages):
    import arcpy

    def geod_area(g):
        try:
            return abs(g.getArea("GEODESIC", "SQUAREMETERS"))
        except Exception:                                        # noqa: BLE001
            return abs(g.area) * m_per_cs * m_per_cs

    env_rect = _rp._env_rect(work_sr, out_sr)
    if strata == _rp.STRATA_NONE:
        if boundary not in (None, ""):
            merged = _vt._read_boundary(boundary, work_sr, messages)
        elif isinstance(extent, arcpy.Extent) or extent not in (None,
                                                                ""):
            e = extent if isinstance(extent, arcpy.Extent) else None
            if e is None:
                parts = [float(v) for v in str(extent).split()[:4]]
                e = arcpy.Extent(parts[0], parts[1], parts[2],
                                 parts[3], None, None, None, None,
                                 out_sr)
            esr = e.spatialReference
            rect = _vt._rect_polygon(e.XMin, e.YMin, e.XMax, e.YMax,
                                     esr if esr is not None
                                     and esr.name not in (None, "",
                                                          "Unknown")
                                     else out_sr)
            merged = _vt._project_geom(rect, work_sr) \
                if not _vt._same_cs(rect.spatialReference, work_sr) \
                else rect
        elif env_rect is not None:
            merged = env_rect
        else:
            raise arcpy.ExecuteError(
                "Give an area-of-interest layer or an extent (or set "
                "the Extent environment).")
        if env_rect is not None and boundary not in (None, ""):
            merged = merged.intersect(env_rect, 4)
            if merged is None or merged.area <= 0:
                raise arcpy.ExecuteError(
                    "The Extent environment leaves none of the area "
                    "of interest.")
        a = geod_area(merged)
        strata_list = [["the area of interest",
                        [(None, merged, a)], a, 0.0]]
    else:
        n_total, n_sel, has_sel = _mh.selection_info(
            boundary, str(boundary))
        if has_sel:
            _say(messages, "The area layer has an active selection: "
                           "{} of {} features are used.".format(
                               n_sel, n_total))
        cols = ["OID@", "SHAPE@"]
        if strata == _rp.STRATA_IDFIELD:
            cols.append(strata_field)
        if population_field and alloc in (_rp.ALLOC_EQUAL_FIELD,
                                          _rp.ALLOC_PROP_FIELD):
            cols.append(population_field)
        groups = {}
        order = []
        with arcpy.da.SearchCursor(boundary, cols) as cur:
            for row in cur:
                oid, g = row[0], row[1]
                if g is None:
                    continue
                ci = 2
                key = oid
                if strata == _rp.STRATA_IDFIELD:
                    key = row[ci]
                    ci += 1
                pop = 0.0
                if len(cols) > ci:
                    pop = _rp._check_pop(row[ci], population_field)
                gw = _vt._project_geom(g, work_sr)
                if env_rect is not None:
                    gw = gw.intersect(env_rect, 4)
                    if gw is None or gw.area <= 0:
                        continue
                a = geod_area(gw)
                if key not in groups:
                    label = "polygon OID {}".format(oid) \
                        if strata == _rp.STRATA_INDIV \
                        else "stratum '{}'".format(key)
                    groups[key] = [label, [], 0.0, 0.0]
                    order.append(key)
                groups[key][1].append((oid, gw, a))
                groups[key][2] += a
                groups[key][3] += pop
        if not groups:
            raise arcpy.ExecuteError(
                "The area input contains no polygons (empty dataset, "
                "or an active selection selecting nothing).")
        strata_list = [groups[k] for k in order]
        _say(messages, "{:,} strat{} defined.".format(
            len(strata_list), "um" if len(strata_list) == 1 else "a"))
    _mh.phase_advance()

    counts = _alloc_arrays(
        alloc, [s[0] for s in strata_list],
        [s[2] for s in strata_list], [s[3] for s in strata_list],
        n_arrays, n_per_stratum, density, density_units,
        _rp._DENS_M2.get(density_units, 1.0), "area", messages)
    want_total = int(counts.sum())

    rows = []
    placed = 0
    starved_any = False
    for si, (label, members, _mt, _pt) in enumerate(strata_list):
        need = int(counts[si])
        if need <= 0:
            continue
        strat_label = None if strata == _rp.STRATA_NONE else \
            (str(members[0][0]) if strata == _rp.STRATA_INDIV
             else label.replace("stratum '", "").rstrip("'"))
        member_counts = _rp._apportion(need, [m[2] for m in members])
        for (oid, geom, _a), mneed in zip(members, member_counts):
            mneed = int(mneed)
            if mneed <= 0:
                continue
            mlabel = "polygon OID {}".format(oid) \
                if oid is not None else label
            prep_focal = _rp._prep_rings(geom)
            if prep_focal is None:
                starved_any = True
                continue
            prep_fit = None
            if must_fit:
                inner = _rp._shrink(geom, gap_cs, messages, mlabel)
                if inner is None:
                    starved_any = True
                    continue
                prep_fit = _rp._prep_rings(inner)
                if prep_fit is None:
                    starved_any = True
                    continue
                # an array wider than the container's bounding-box
                # diagonal can NEVER fit -- say so up front instead
                # of burning the whole attempts budget
                _e, _b, (x0, y0, x1, y1) = prep_fit
                diag = math.hypot(x1 - x0, y1 - y0)
                if design.min_diameter() > diag:
                    _warn(messages,
                          "{}: the array is wider than this area "
                          "can possibly hold (array span {:,.1f} vs. "
                          "a {:,.1f} diagonal{}) -- no arrays can "
                          "fit there. Use a smaller array, or allow "
                          "arrays to extend beyond the area of "
                          "interest.".format(
                              mlabel, design.min_diameter(), diag,
                              " after the boundary distance"
                              if gap_cs else ""))
                    starved_any = True
                    continue
            got, dry = _place_arrays_polygon(
                prep_focal, prep_fit, design, spacing, mneed, rng,
                attempts)
            if len(got) < mneed:
                starved_any = True
                _starve_arrays(messages, mlabel, len(got), mneed,
                               attempts, must_fit, sampler_dry=dry)
            if got:
                rows.append({"arrays": got, "src_fid": oid,
                             "stratum": strat_label})
                placed += len(got)
        _mh.phase_step(placed, want_total)
    return rows, starved_any, want_total


def _place_arrays_polygon(prep_focal, prep_fit, design, spacing, need,
                          rng, attempts):
    """Admit up to ``need`` arrays in one polygon. Each candidate
    focal point is drawn uniformly in the polygon; in must-fit mode
    the whole array must land inside the (gap-inset) polygon -- and
    when the focal location is itself an array point, candidates are
    drawn from the gap-inset polygon directly (a focal point in the
    excluded band could never be admitted). Returns (arrays,
    sampler_dry): the ``dry`` counter tracks CONSECUTIVE EMPTY
    candidate batches only (sampler starvation) -- batches with real
    candidates are bounded by the attempts budget alone, so
    max_attempts genuinely governs hard placements."""
    got = []
    tried = 0
    budget = attempts * need
    dry = 0
    prep_draw = prep_fit if prep_fit is not None \
        and design.focal_is_array_point() else prep_focal
    while len(got) < need and tried < budget and dry < 60:
        m = int(min(4096, max(16, 2 * (need - len(got)))))
        cand, _starved = _rp._sample_in(prep_draw, m, rng, None,
                                        max_draws=200 * m)
        if not len(cand):
            dry += 1
            continue
        dry = 0
        for fx, fy in cand:
            if tried >= budget or len(got) >= need:
                break
            tried += 1
            pts, rowcol, brgs = design.stamp(float(fx), float(fy))
            if prep_fit is not None \
                    and not _points_in_prep(prep_fit, pts):
                continue
            if not _admit_footprint(spacing, pts):
                continue
            got.append({"pts": pts, "rowcol": rowcol, "brgs": brgs,
                        "values": None})
    return got, dry >= 60


# ---------------------------------------------------------------------------
#  polylines core
# ---------------------------------------------------------------------------
def _arrays_on_lines_core(boundary, desc, work_sr, strata,
                          strata_field, alloc, n_arrays,
                          n_per_stratum, population_field, density,
                          density_units, design, must_fit, gap_cs,
                          spacing, smode, rng, attempts, m_per_cs,
                          messages):
    import arcpy

    n_total, n_sel, has_sel = _mh.selection_info(boundary,
                                                 str(boundary))
    if has_sel:
        _say(messages, "The line layer has an active selection: {} of "
                       "{} polylines are used.".format(n_sel, n_total))
    cols = ["OID@", "SHAPE@"]
    if strata == _rp.STRATA_IDFIELD:
        cols.append(strata_field)
    if population_field and alloc in (_rp.ALLOC_EQUAL_FIELD,
                                      _rp.ALLOC_PROP_FIELD):
        cols.append(population_field)
    groups = {}
    order = []
    with arcpy.da.SearchCursor(boundary, cols) as cur:
        for row in cur:
            oid, g = row[0], row[1]
            if g is None:
                continue
            ci = 2
            key = 0 if strata == _rp.STRATA_NONE else oid
            if strata == _rp.STRATA_IDFIELD:
                key = row[ci]
                ci += 1
            pop = 0.0
            if len(cols) > ci:
                pop = _rp._check_pop(row[ci], population_field)
            try:
                lg = abs(g.getLength("GEODESIC", "METERS"))
            except Exception:                                    # noqa: BLE001
                lg = abs(g.length) * m_per_cs
            parts = [(next(_rp._PART_UID), a) for a in
                     _rp._line_parts(_vt._project_geom(g, work_sr))]
            if not parts:
                continue
            if key not in groups:
                label = "the lines" if strata == _rp.STRATA_NONE else \
                    ("polyline OID {}".format(oid)
                     if strata == _rp.STRATA_INDIV
                     else "stratum '{}'".format(key))
                groups[key] = [label, [], 0.0, 0.0]
                order.append(key)
            groups[key][1].append((oid, parts, lg))
            groups[key][2] += lg
            groups[key][3] += pop
    if not groups:
        raise arcpy.ExecuteError(
            "The line input contains no polylines (empty dataset, or "
            "an active selection selecting nothing).")
    strata_list = [groups[k] for k in order]
    if strata != _rp.STRATA_NONE:
        _say(messages, "{:,} strat{} defined.".format(
            len(strata_list), "um" if len(strata_list) == 1 else "a"))

    part_groups = [[part for _oid, parts, _lg in s[1]
                    for part in parts] for s in strata_list]
    walker = None
    if design.shape == SHAPE_LINE:
        walker = _LineWalker(part_groups)
    if isinstance(spacing, _NetCheck):
        spacing.build(part_groups, messages)
    _mh.phase_advance()

    counts = _alloc_arrays(
        alloc, [s[0] for s in strata_list],
        [s[2] for s in strata_list], [s[3] for s in strata_list],
        n_arrays, n_per_stratum, density, density_units,
        _rp._DENS_LIN_M.get(density_units, 1.0), "line length",
        messages)
    want_total = int(counts.sum())

    rows = []
    placed = 0
    starved_any = False
    n_amb = 0
    for si, (label, members, _mt, _pt) in enumerate(strata_list):
        need = int(counts[si])
        if need <= 0:
            continue
        strat_label = None if strata == _rp.STRATA_NONE else \
            (str(members[0][0]) if strata == _rp.STRATA_INDIV
             else label.replace("stratum '", "").rstrip("'"))
        if design.shape == SHAPE_LINE:
            # a 1-D array longer than ALL the line this group has
            # (walks may cross junctions onto connected members, so
            # the group total is the honest ceiling) can never fit
            total_cs = sum(
                float(np.hypot(a[1:, 0] - a[:-1, 0],
                               a[1:, 1] - a[:-1, 1]).sum())
                for _oid, parts, _lg in members
                for _uid, a in parts)
            span = (design.npp - 1) * design.spacing_cs \
                + 2.0 * (gap_cs or 0.0)
            if span > total_cs:
                _warn(messages,
                      "{}: the along-the-line array spans {:,.1f} "
                      "(with the boundary distance) but only "
                      "{:,.1f} of line exists there -- no arrays "
                      "can fit. Use fewer points or a tighter "
                      "spacing.".format(label, span, total_cs))
                starved_any = True
                continue
        member_counts = _rp._apportion(need, [m[2] for m in members])
        for (oid, parts, _lg), mneed in zip(members, member_counts):
            mneed = int(mneed)
            if mneed <= 0:
                continue
            mlabel = "polyline OID {}".format(oid)
            # focal sampler: 2-D arrays keep the boundary separation
            # at the focal point (the array leaves the line); the 1-D
            # array enforces it along the WALK instead
            focal_gap = gap_cs if design.shape != SHAPE_LINE else None
            sampler = _rp._line_sampler(parts, focal_gap)
            if sampler is None:
                if focal_gap:
                    _warn(messages,
                          "{}: shorter than twice the boundary "
                          "separation -- no arrays placed "
                          "there.".format(mlabel))
                starved_any = True
                continue
            got, amb = _place_arrays_line(
                sampler, walker, design, spacing, mneed, rng,
                attempts, gap_cs)
            n_amb += amb
            if len(got) < mneed:
                starved_any = True
                _starve_arrays(messages, mlabel, len(got), mneed,
                               attempts, must_fit)
            if got:
                rows.append({"arrays": got,
                             "src_fid": oid
                             if strata != _rp.STRATA_NONE else None,
                             "stratum": strat_label})
                placed += len(got)
        _mh.phase_step(placed, want_total)
    return rows, starved_any, want_total, n_amb


def _place_arrays_line(sampler, walker, design, spacing, need, rng,
                       attempts, gap_cs):
    """Admit up to ``need`` arrays along one member's lines. Returns
    (arrays, n_ambiguous)."""
    got = []
    tried = 0
    budget = attempts * need
    n_amb = 0
    while len(got) < need and tried < budget:
        m = int(min(2048, max(16, 2 * (need - len(got)))))
        xy, part, s = _rp._line_at(sampler,
                                   rng.random(m) * sampler["total"])
        for k in range(len(xy)):
            if tried >= budget or len(got) >= need:
                break
            tried += 1
            uid = int(part[k])
            if design.shape == SHAPE_LINE:
                pts_ps, amb, ok = walker.walk_array(
                    uid, float(s[k]), design.npp, design.spacing_cs,
                    gap_cs or 0.0)
                if not ok:
                    continue
                pts = np.array([(x, y) for _u, _s, x, y in pts_ps])
                line_ps = [(u, sv) for u, sv, _x, _y in pts_ps]
                if not _admit_footprint(spacing, pts,
                                        line_ps=line_ps):
                    continue
                got.append({"pts": pts, "rowcol": None, "brgs": None,
                            "values": None})
                if amb:
                    n_amb += 1
            else:
                line_dir = None
                if design.orient == ORIENT_ALIGN:
                    if walker is not None:
                        line_dir = walker.dir_at(uid, float(s[k]))
                    else:
                        line_dir = _dir_on_sampler(sampler, uid,
                                                   float(s[k]))
                pts, rowcol, brgs = design.stamp(
                    float(xy[k, 0]), float(xy[k, 1]),
                    line_dir=line_dir)
                line_ps = [(uid, float(s[k]))]
                if not _admit_footprint(spacing, pts,
                                        line_ps=line_ps):
                    continue
                got.append({"pts": pts, "rowcol": rowcol,
                            "brgs": brgs, "values": None})
    return got, n_amb


def _dir_on_sampler(sampler, uid, s):
    """Local line direction at arc position s of part uid, from the
    sampler's sub-segments."""
    m = sampler["part"] == uid
    if not m.any():
        return None
    s0 = sampler["s0"][m]
    d = sampler["dir"][m]
    i = int(np.clip(np.searchsorted(s0, s, side="right") - 1, 0,
                    len(s0) - 1))
    return (float(d[i, 0]), float(d[i, 1]))


# ---------------------------------------------------------------------------
#  raster core
# ---------------------------------------------------------------------------
def _cell_lookup(pool, x, y):
    """(allowed, value) of the cell under a raster-CS point; value is
    None off the data."""
    col = int(math.floor((x - pool.xmin) / pool.cw))
    row = int(math.floor((pool.ymax - y) / pool.ch))
    if not (0 <= col < pool.ncols and 0 <= row < pool.nrows):
        return False, None
    bidx = row // pool.block_rows
    arr, allowed, _rw = pool._block(bidx)
    r_loc = row - bidx * pool.block_rows
    v = int(arr[r_loc, col])
    return bool(allowed[r_loc, col]), (v if v in pool.vset else None)


def _arrays_on_raster(out_fc, boundary, desc, strata, strata_field,
                      alloc, n_arrays, n_per_stratum,
                      population_field, density, density_units, shape,
                      n_rows, n_cols, array_points, point_spacing,
                      radius, orient, orientation_angle,
                      randomize_start, include_center, must_fit,
                      min_spacing, boundary_gap, linear_units, rng,
                      attempts, random_seed, messages,
                      strata_txt=None, alloc_txt=None,
                      randomize_ids=False):
    import arcpy

    # user-facing display strings for the provenance stamp (like the
    # vector path); fall back to the canonical codes
    strata_txt = strata_txt or strata
    alloc_txt = alloc_txt or alloc

    path = getattr(desc, "catalogPath", str(boundary))
    r = arcpy.Raster(path)
    if not r.isInteger:
        raise arcpy.ExecuteError(
            "The raster must be an INTEGER raster with an attribute "
            "table (this one is floating-point).")
    if not r.hasRAT:
        raise arcpy.ExecuteError(
            "The raster has no attribute table -- run Build Raster "
            "Attribute Table on it first.")
    rsr = r.spatialReference
    if rsr is None or getattr(rsr, "name", "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "The raster has no coordinate system.")
    out_sr = arcpy.env.outputCoordinateSystem or rsr

    vat_cols = ["Value"]
    if strata == _rp.STRATA_IDFIELD:
        vat_cols.append(strata_field)
    if population_field and alloc in (_rp.ALLOC_EQUAL_FIELD,
                                      _rp.ALLOC_PROP_FIELD):
        vat_cols.append(population_field)
    values = []
    key_of = {}
    pop_of = {}
    with arcpy.da.SearchCursor(path, vat_cols) as cur:
        for row in cur:
            v = int(row[0])
            values.append(v)
            ci = 1
            if strata == _rp.STRATA_IDFIELD:
                key_of[v] = row[ci]
                ci += 1
            if len(vat_cols) > ci:
                pop_of[v] = _rp._check_pop(row[ci], population_field)
    if not values:
        raise arcpy.ExecuteError(
            "The raster attribute table is empty.")
    _say(messages, "{:,} categories in the attribute table (Value "
                   "field).".format(len(values)))

    if strata == _rp.STRATA_NONE:
        strata_def = [("the raster", list(values))]
    elif strata in (_rp.STRATA_CATEGORY, _rp.STRATA_REGION):
        strata_def = [(str(v), [v]) for v in values]
    else:
        order = []
        by_key = {}
        for v in values:
            k = key_of.get(v)
            if k not in by_key:
                by_key[k] = []
                order.append(k)
            by_key[k].append(v)
        strata_def = [(str(k), by_key[k]) for k in order]

    geographic = rsr.type == "Geographic"
    gap_m = float(boundary_gap) * _LIN_M[linear_units] \
        if boundary_gap not in (None, "", 0) and must_fit else None
    r_m = float(min_spacing) * _LIN_M[linear_units] \
        if min_spacing not in (None, "", 0) else None
    metric_sr = None
    if geographic:
        ext = r.extent
        metric_sr = _rs._laea_for(
            rsr, 0.5 * (ext.XMin + ext.XMax),
            0.5 * (ext.YMin + ext.YMax))
        _say(messages,
             "Geographic raster: focal cells are picked with their "
             "TRUE areas; arrays are built in an automatic equal-area "
             "working projection (true meters) and written back in "
             "the raster's coordinate system.")
        if gap_m:
            _warn(messages,
                  "The boundary separation on a geographic raster "
                  "uses the raster's average cell size at the center "
                  "latitude -- exact to a fraction of a cell at "
                  "regional scales.")

    pool = _rp._RasterPool(r, strata_def, gap_m, messages,
                           regions=strata == _rp.STRATA_REGION)
    mpu = 1.0 if geographic else float(rsr.metersPerUnit or 1.0)
    # array geometry in metric space: LAEA meters for geographic
    # rasters, raster linear units otherwise
    to_metric = 1.0 if geographic else 1.0 / mpu
    design = _ArrayDesign(
        shape, int(n_rows or 0), int(n_cols or 0),
        int(array_points or 0),
        float(point_spacing or 0) * _LIN_M[linear_units] * to_metric,
        float(radius or 0) * _LIN_M[linear_units] * to_metric,
        orient, orientation_angle, randomize_start, include_center,
        rng)
    design.spacing_cs_user = float(point_spacing or 0)
    design.radius_cs_user = float(radius or 0)
    _say(messages, "Each array: {}.".format(
        design.describe(linear_units)))
    spacing = None
    if r_m is not None:
        spacing = _FootprintGrid(r_m * to_metric
                                 if not geographic else r_m)
        _say(messages,
             "The minimum distance between arrays is measured "
             "between their NEAREST points (footprints, not "
             "centers).")
    _mh.phase_advance()

    pops = []
    for st in pool.strata:
        pops.append(sum(pop_of.get(v, 0.0) for v in st[1]))
    counts = _alloc_arrays(
        alloc, [st[0] for st in pool.strata], pool.area_m2, pops,
        n_arrays, n_per_stratum, density, density_units,
        _rp._DENS_M2.get(density_units, 1.0), "cell area", messages)
    want_total = int(counts.sum())
    if strata != _rp.STRATA_NONE:
        _say(messages, "{:,} strat{} defined.".format(
            len(pool.strata),
            "um" if len(pool.strata) == 1 else "a"))

    rows = []
    placed = 0
    starved_any = False
    if must_fit:
        # an array wider than the whole read window can never fit on
        # the data -- warn up front instead of burning the budget
        # (the x-span uses cos(lat) <= 1, overestimating the window,
        # so a feasible design is never declared impossible)
        if geographic:
            rad = math.pi / 180.0
            R_e = 6371008.8
            diag = math.hypot(pool.ncols * pool.cw * rad * R_e,
                              pool.nrows * pool.ch * rad * R_e)
        else:
            diag = math.hypot(pool.ncols * pool.cw,
                              pool.nrows * pool.ch)
        if design.min_diameter() > diag:
            _warn(messages,
                  "The array is wider than the raster (or the "
                  "Extent-environment window) can possibly hold -- "
                  "no arrays can fit. Use a smaller array, or allow "
                  "arrays to extend beyond the data.")
            _mh.phase_advance()
            out_path = _write_arrays(
                out_fc, out_sr, rsr, not _vt._same_cs(rsr, out_sr),
                [], design, src_field=False, cat_field=True,
                strat_field=strata != _rp.STRATA_NONE,
                messages=messages, starved_any=True,
                want_total=want_total,
                rng=rng, randomize_ids=randomize_ids)
            _stamp_arrays(out_path, "raster", strata_txt, alloc_txt,
                          design, linear_units, 0, want_total,
                          boundary, min_spacing, boundary_gap,
                          must_fit, random_seed)
            _mh.end_phases()
            return out_path
    for si, st in enumerate(pool.strata):
        need = int(counts[si])
        if need <= 0:
            continue
        label = "stratum '{}'".format(st[0]) \
            if strata != _rp.STRATA_NONE else st[0]
        if pool.area_m2[si] <= 0:
            _warn(messages,
                  "{}: no usable cells (all NoData, or all within "
                  "the boundary separation).".format(label))
            starved_any = True
            continue
        got, dry = _place_arrays_raster(
            pool, si, design, spacing, need, rng, attempts, must_fit,
            geographic, metric_sr)
        if len(got) < need:
            starved_any = True
            _starve_arrays(messages, label, len(got), need, attempts,
                           must_fit, sampler_dry=dry)
        if got:
            rows.append({"arrays": got, "src_fid": None,
                         "stratum": st[0]
                         if strata != _rp.STRATA_NONE else None})
            placed += len(got)
        _mh.phase_step(placed, want_total)
    _mh.phase_advance()

    out_path = _write_arrays(
        out_fc, out_sr, rsr, not _vt._same_cs(rsr, out_sr), rows,
        design, src_field=False, cat_field=True,
        strat_field=strata != _rp.STRATA_NONE, messages=messages,
        starved_any=starved_any, want_total=want_total,
        rng=rng, randomize_ids=randomize_ids)
    _stamp_arrays(out_path, "raster", strata_txt, alloc_txt, design,
                  linear_units, placed, want_total, boundary,
                  min_spacing, boundary_gap, must_fit, random_seed)
    _mh.end_phases()
    return out_path


def _place_arrays_raster(pool, si, design, spacing, need, rng,
                         attempts, must_fit, geographic, metric_sr):
    """Admit up to ``need`` arrays for one raster stratum. Arrays are
    built in metric space (LAEA for geographic rasters) and their
    points carried back to the raster CS for the containment test and
    the output -- ONE batched projection each way per candidate
    batch, not one per candidate. Returns (arrays, sampler_dry): the
    ``dry`` counter tracks CONSECUTIVE EMPTY candidate batches only
    (sampler starvation) -- batches with real candidates are bounded
    by the attempts budget alone, so max_attempts genuinely governs
    hard placements."""
    got = []
    tried = 0
    budget = attempts * need
    dry = 0
    while len(got) < need and tried < budget and dry < 60:
        m = int(min(2048, max(16, 2 * (need - len(got)))))
        xy, _vals = pool.draw(si, m, rng)
        if not len(xy):
            dry += 1
            continue
        dry = 0
        n_take = int(min(len(xy), budget - tried))
        if geographic:
            focal_metric = _rp._project_xy(xy[:n_take], pool.sr,
                                           metric_sr)
            # stamp the whole batch, then carry every stamped point
            # back to the raster CS in a single projection call
            stamps = [design.stamp(float(focal_metric[k, 0]),
                                   float(focal_metric[k, 1]))
                      for k in range(n_take)]
            back = _rp._project_xy(
                np.concatenate([st[0] for st in stamps]),
                metric_sr, pool.sr).reshape(n_take, -1, 2)
        for k in range(n_take):
            if len(got) >= need:
                break
            tried += 1
            if geographic:
                pts_m, rowcol, brgs = stamps[k]
                pts_r = back[k]
            else:
                pts_m, rowcol, brgs = design.stamp(
                    float(xy[k, 0]), float(xy[k, 1]))
                pts_r = pts_m
            vals = []
            ok = True
            for x, y in pts_r:
                allowed, v = _cell_lookup(pool, float(x), float(y))
                if must_fit and not allowed:
                    ok = False
                    break
                vals.append(v)
            if not ok:
                continue
            if not _admit_footprint(spacing, pts_m):
                continue
            got.append({"pts": pts_r, "rowcol": rowcol, "brgs": brgs,
                        "values": vals})
    return got, dry >= 60


# ---------------------------------------------------------------------------
#  shared write / warn / stamp
# ---------------------------------------------------------------------------
def _starve_arrays(messages, label, got, need, attempts, must_fit,
                   sampler_dry=False):
    extra = " with the whole array required to fit" if must_fit else ""
    if sampler_dry:
        _warn(messages,
              "{}: only {:,} of {:,} arrays could be placed{} -- the "
              "random focal-point sampler repeatedly found no "
              "candidate locations at all (a degenerate or "
              "vanishingly small area), so more attempts would not "
              "help.".format(label, got, need, extra))
        return
    _warn(messages,
          "{}: only {:,} of {:,} arrays could be placed{} within "
          "{:,} placement attempts per array -- raise the attempts "
          "in the Advanced section, relax the constraints, or use a "
          "smaller array.".format(label, got, need, extra, attempts))


def _write_arrays(out_fc, out_sr, work_sr, reproject, rows, design,
                  src_field, cat_field, strat_field, messages,
                  starved_any, want_total, rng=None,
                  randomize_ids=False):
    """rows: list of {"arrays": [{"pts", "rowcol", "brgs",
    "values"}], "src_fid", "stratum"}. With ``randomize_ids`` each
    array's Point_ID values are a fresh random permutation of
    1..m (seed-reproducible), so Point_ID can serve as a RANDOMIZED
    treatment ID -- Row/Col and Bearing stay tied to the geometric
    position, and Point_ID 1 is then no longer the focal/center
    point."""
    import os

    import arcpy

    out_fc = str(out_fc).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_fc)
    if ws and not _mh.in_gdb_container(out_fc) \
            and not name.lower().endswith(".shp"):
        name += ".shp"
    res = arcpy.management.CreateFeatureclass(
        ws, name, "POINT", spatial_reference=_vt.clean_sr(out_sr))
    out_path = res.getOutput(0)
    is_shp = out_path.lower().endswith(".shp")
    field_rows = [["Array_ID", "LONG", "Array ID", "", "", ""],
                  ["Point_ID", "LONG", "Point ID within the array",
                   "", "", ""]]
    if design.shape == SHAPE_GRID:
        field_rows.append(["Row", "LONG", "Array row (1 = north at "
                                          "zero orientation)",
                           "", "", ""])
        field_rows.append(["Col", "LONG", "Array column (1 = west at "
                                          "zero orientation)",
                           "", "", ""])
    if design.shape == SHAPE_CIRCLE:
        field_rows.append(["Bearing", "DOUBLE",
                           "Azimuth from the array center (degrees)",
                           "", "", ""])
    if strat_field:
        field_rows.append(["Stratum", "TEXT", "Stratum", 64, "", ""])
    if src_field:
        field_rows.append(["Src_FID", "LONG",
                           "Source feature Object ID", "", "", ""])
    if cat_field:
        field_rows.append(["Category", "LONG",
                           "Raster category (Value) under the point",
                           "", "", ""])
    arcpy.management.AddFields(out_path, field_rows)

    n_arrays_written = 0
    n_written = 0
    total = sum(len(a["pts"]) for row in rows
                for a in row["arrays"])
    proj = None
    if reproject and total:
        # ONE batched projection for every point of every array
        # instead of a PointGeometry.projectAs per point
        proj = _rp._project_xy(
            np.concatenate([np.asarray(a["pts"], dtype=np.float64)
                            for row in rows for a in row["arrays"]]),
            work_sr, out_sr)
    cols = ["SHAPE@XY", "Array_ID", "Point_ID"]
    if design.shape == SHAPE_GRID:
        cols += ["Row", "Col"]
    if design.shape == SHAPE_CIRCLE:
        cols.append("Bearing")
    if strat_field:
        cols.append("Stratum")
    if src_field:
        cols.append("Src_FID")
    if cat_field:
        cols.append("Category")
    pi = 0
    with arcpy.da.InsertCursor(out_path, cols) as ins:
        for row in rows:
            for a in row["arrays"]:
                n_arrays_written += 1
                pts = a["pts"]
                ids = None
                if randomize_ids and rng is not None and len(pts) > 1:
                    ids = rng.permutation(len(pts)) + 1
                for k in range(len(pts)):
                    n_written += 1
                    if proj is not None:
                        x, y = float(proj[pi, 0]), float(proj[pi, 1])
                    else:
                        x, y = float(pts[k, 0]), float(pts[k, 1])
                    pi += 1
                    rec = [(x, y), n_arrays_written,
                           int(ids[k]) if ids is not None else k + 1]
                    if design.shape == SHAPE_GRID:
                        rc = a["rowcol"][k]
                        rec += [int(rc[0]), int(rc[1])]
                    if design.shape == SHAPE_CIRCLE:
                        # shapefiles cannot store nulls: the house
                        # -999 convention marks the center's bearing
                        b = float(a["brgs"][k])
                        rec.append(-999.0 if (is_shp and math.isnan(b))
                                   else (None if math.isnan(b)
                                         else b))
                    if strat_field:
                        rec.append(str(row["stratum"])[:64]
                                   if row["stratum"] is not None
                                   else None)
                    if src_field:
                        rec.append(row["src_fid"])
                    if cat_field:
                        # house -999 convention for a null category
                        v = a["values"][k] \
                            if a["values"] is not None else None
                        rec.append(-999 if (is_shp and v is None)
                                   else v)
                    ins.insertRow(rec)
                    if n_written % 2000 == 0:
                        _mh.phase_step(n_written, total)
    _say(messages,
         "{:,} sample array{} ({:,} points) written{}.".format(
             n_arrays_written, "" if n_arrays_written == 1 else "s",
             n_written,
             "" if not starved_any
             else " (fewer arrays than requested -- see the warnings "
                  "above)"))
    return out_path


def _stamp_arrays(out_path, kind, strata_txt, alloc_txt, design,
                  linear_units, n, want, boundary, min_spacing,
                  boundary_gap, must_fit, random_seed):
    import os

    import arcpy

    try:
        bits = ["{}; {}".format(str(strata_txt).lower(),
                                str(alloc_txt).lower()),
                design.describe(linear_units)]
        if min_spacing not in (None, "", 0):
            bits.append("minimum {:g} {} between array "
                        "footprints".format(float(min_spacing),
                                            linear_units.lower()))
        if boundary_gap not in (None, "", 0):
            bits.append("minimum {:g} {} from the boundary".format(
                float(boundary_gap), linear_units.lower()))
        bits.append("entire array required to fit"
                    if must_fit else "arrays may extend outside")
        if random_seed not in (None, ""):
            bits.append("seed {}".format(random_seed))
        aoi = {"polygon": "polygon features from ",
               "polyline": "polyline features from ",
               "raster": "the integer raster "}.get(kind)
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = (
            "Created by Random Sample Array Generator: {n:,} of "
            "{w:,} requested arrays ({b}) over {aoi}.".format(
                n=n, w=want, b="; ".join(bits),
                aoi=(aoi + os.path.basename(str(boundary)))
                    if aoi and boundary not in (None, "")
                    else "an extent"))
        md.description = (
            "Randomly placed arrays of sampling points from the "
            "Jenness Enterprises Random Sample Array Generator tool: "
            "each array (a grid of rows and columns, a triangular "
            "lattice, a circle of evenly spaced points, or a "
            "one-dimensional array along a polyline) is built around "
            "a randomly placed focal location within polygons, along "
            "polylines, or within the data cells of an integer "
            "raster, following the Random Point Generator's "
            "stratified sampling design. Spacing and boundary "
            "distances apply to array footprints (nearest points, "
            "not centers). On geographic coordinate systems arrays "
            "are built in an automatic equal-area projection in true "
            "meters.")
        md.save()
    except Exception:                                            # noqa: BLE001
        pass
