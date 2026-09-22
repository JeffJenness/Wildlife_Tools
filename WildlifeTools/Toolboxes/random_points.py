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

"""Random Point Generator -- uniform random points within polygons,
ALONG polylines, or within the data cells of an integer raster.

A modernized port of Jeff Jenness's classic Random Point Generator
extension. Constraints Esri's Create Random Points only half covers:
a MINIMUM SEPARATION between points AND a MINIMUM SEPARATION FROM THE
AREA BOUNDARY (for polylines the latter is measured along the line
from each end; for rasters it is the distance from NoData/edge cells).

THE SAMPLING DESIGN IS STRATA x ALLOCATION, replicating the
stratification model of Esri's Create Spatial Sampling Locations plus
a density option (canonicalized by prefix, see ``canon_strata`` /
``canon_alloc``):

STRATA: none (the whole area is one stratum); each individual polygon
or polyline; each raster CATEGORY (the attribute table's Value field);
each CONTIGUOUS RASTER REGION (cells of one value connected by shared
edges -- scipy 4-connected labeling); or grouped by a STRATA ID FIELD
(an integer or text attribute for features, or a raster
attribute-table field -- members need not be contiguous).

ALLOCATION of counts to strata: EQUAL (the same number per stratum);
PROPORTIONAL TO STRATUM AREA (length for lines) with exact
largest-remainder apportionment of a total; EQUAL TO A POPULATION
FIELD (each stratum's own number; summed over members for grouped
strata); PROPORTIONAL TO A POPULATION FIELD; or BY DENSITY -- points
per acre / square mile / hectare / square kilometer of geodesic area,
or per foot / mile / meter / kilometer of geodesic line length.
Unstratified sampling offers the fixed total (equivalent to
proportional-to-area over one stratum) and density.

ALGORITHM: polygons use vectorized batch rejection sampling over the
bounding box filtered by the suite's banded even-odd point-in-polygon
test; polylines invert the arc-length CDF (exact, no rejection);
rasters stream in row blocks, pick cells with TRUE-AREA weights (so
geographic rasters are honest about shrinking cells), and jitter
uniformly within the cell. A minimum separation admits candidates
through a spatial HASH GRID with cell r/sqrt(2) -- at most one point
per cell, so each admission checks a 5x5 neighborhood, O(1) per point
(a Simple Sequential Inhibition process). The Advanced
attempts-per-point budget bounds how hard the tool tries before
accepting a shortfall.

GEOGRAPHIC data: vector inputs are handled in the suite's automatic
AOI-centered Lambert azimuthal EQUAL-AREA working projection; raster
candidates stay in the raster's own grid but their separations are
tested in that same projection. Density counts always come from
geodesic areas/lengths (true row areas for rasters).
"""
import itertools
import math
import os

import numpy as np

import clip_tools as _ct
import mahalanobis as _mh
import patch_analysis as _pa
import repeating_shapes as _rs
import voronoi_tools as _vt

# --- sampling design: STRATA DEFINITION x ALLOCATION METHOD -----------
# (replicates the stratification model of Esri's Create Spatial
# Sampling Locations, plus a density allocation)
STRATA_NONE = "NONE"
STRATA_INDIV = "INDIV"                 # each polygon/polyline
STRATA_CATEGORY = "CATEGORY"           # each raster Value
STRATA_REGION = "REGION"               # each contiguous raster region
STRATA_IDFIELD = "IDFIELD"             # grouped by a strata ID field

_S_NONE = "No stratification (the whole area is one stratum)"
STRATA_POLYGON = [_S_NONE, "Stratify by individual polygon",
                  "Stratify by strata ID field"]
STRATA_POLYLINE = [_S_NONE, "Stratify by individual polyline",
                   "Stratify by strata ID field"]
STRATA_RASTER = [_S_NONE, "Stratify by raster category (Value field)",
                 "Stratify by contiguous raster region",
                 "Stratify by strata ID field"]
STRATA_EXTENT = [_S_NONE]


def canon_strata(s):
    if s in (STRATA_NONE, STRATA_INDIV, STRATA_CATEGORY,
             STRATA_REGION, STRATA_IDFIELD):
        return s
    t = str(s or "").strip().lower()
    if not t or t.startswith("no strat"):
        return STRATA_NONE
    if t.startswith("stratify by individual"):
        return STRATA_INDIV
    if t.startswith("stratify by raster category"):
        return STRATA_CATEGORY
    if t.startswith("stratify by contiguous"):
        return STRATA_REGION
    if t.startswith("stratify by strata"):
        return STRATA_IDFIELD
    return None


ALLOC_TOTAL = "TOTAL"                  # unstratified fixed total
ALLOC_EQUAL = "EQUAL"
ALLOC_PROP_AREA = "PROP_AREA"
ALLOC_EQUAL_FIELD = "EQUAL_FIELD"
ALLOC_PROP_FIELD = "PROP_FIELD"
ALLOC_DENSITY = "DENSITY"

_A_DENS_AREA = "By density (points per area unit)"
_A_DENS_LINE = "By density (points per length unit)"
ALLOC_UNSTRAT = ["Fixed total number of points", _A_DENS_AREA]
ALLOC_UNSTRAT_LINE = ["Fixed total number of points", _A_DENS_LINE]
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


def canon_alloc(s):
    if s in (ALLOC_TOTAL, ALLOC_EQUAL, ALLOC_PROP_AREA,
             ALLOC_EQUAL_FIELD, ALLOC_PROP_FIELD, ALLOC_DENSITY):
        return s
    t = str(s or "").strip().lower()
    if t.startswith("fixed total"):
        return ALLOC_TOTAL
    if t.startswith("equal count"):
        return ALLOC_EQUAL
    if t.startswith("count proportional to stratum"):
        return ALLOC_PROP_AREA
    if t.startswith("count equal to"):
        return ALLOC_EQUAL_FIELD
    if t.startswith("count proportional to a population"):
        return ALLOC_PROP_FIELD
    if t.startswith("by density") or t.startswith("density"):
        return ALLOC_DENSITY
    return None


# how a minimum separation is measured on POLYLINES
SPACING_SPATIAL = "SPATIAL"
SPACING_ALONG = "ALONG"
SPACING_NETWORK = "NETWORK"
SPACING_TYPES = ["Absolute spatial distance",
                 "Distance measured along the polyline",
                 "Network distance through connected polylines"]


def canon_spacing(s):
    if s in (SPACING_SPATIAL, SPACING_ALONG, SPACING_NETWORK):
        return s
    t = str(s or "").strip().lower()
    if not t or t.startswith("absolute"):
        return SPACING_SPATIAL
    if t.startswith("distance measured along") \
            or t.startswith("distance along") or t.startswith("along"):
        return SPACING_ALONG
    if t.startswith("network"):
        return SPACING_NETWORK
    return None


def _apportion(total, weights):
    """Largest-remainder apportionment of ``total`` by ``weights``
    (exact: the counts sum to ``total`` whenever any weight is
    positive)."""
    w = np.asarray(weights, dtype=np.float64)
    w = np.where(np.isfinite(w) & (w > 0), w, 0.0)
    n = len(w)
    if total <= 0 or w.sum() <= 0:
        return np.zeros(n, dtype=np.int64)
    q = total * w / w.sum()
    base = np.floor(q).astype(np.int64)
    rem = int(total - base.sum())
    if rem > 0:
        base[np.argsort(-(q - base))[:rem]] += 1
    return base


DENSITY_UNITS = ["Acre", "Square Mile", "Hectare", "Square Kilometer"]
_DENS_M2 = {"Acre": 4046.8564224,
            "Square Mile": 1609.344 * 1609.344,
            "Hectare": 10000.0,
            "Square Kilometer": 1.0e6}
DENSITY_UNITS_LINE = ["Foot", "Mile", "Meter", "Kilometer"]
_DENS_LIN_M = {"Foot": 0.3048, "Mile": 1609.344, "Meter": 1.0,
               "Kilometer": 1000.0}

LINEAR_UNITS = _rs.LINEAR_UNITS          # Meters/Kilometers/Feet/Miles
_LIN_M = _rs._LIN_M


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


# ---------------------------------------------------------------------------
#  shared pieces
# ---------------------------------------------------------------------------
def _prep_rings(geom):
    """(edges, bands, bbox) for a work-SR polygon, densifying true
    curves first (standing rule -- JSON ring walks collapse curves)."""
    if getattr(geom, "hasCurves", False):
        d = max(geom.length / 500.0, 1e-9)
        try:
            geom = geom.densify("DISTANCE", d, d)
        except Exception:                                        # noqa: BLE001
            pass
    rings = _pa.rings_of(geom)
    if not rings:
        return None
    edges = _pa.edges_of(rings)
    bands = _pa.y_bands(edges)
    ext = geom.extent
    return edges, bands, (ext.XMin, ext.YMin, ext.XMax, ext.YMax)


def _shrink(geom, gap_cs, messages, label):
    """The polygon inset by ``gap_cs`` (negative buffer -- exact and
    hole-aware), or None when nothing remains."""
    if not gap_cs or gap_cs <= 0:
        return geom
    try:
        small = geom.buffer(-abs(gap_cs))
    except Exception:                                            # noqa: BLE001
        small = None
    if small is None or getattr(small, "area", 0) <= 0:
        _warn(messages,
              "{}: the minimum distance from the boundary leaves no "
              "interior at all -- no points can be placed "
              "there.".format(label))
        return None
    return small


class _SpacingGrid:
    """Hash-grid admission for a minimum separation r: cell size
    r/sqrt(2) means a cell can hold at most ONE admitted point, and any
    conflict lies within the surrounding 5x5 block -- O(1) per test."""

    def __init__(self, r):
        self.r2 = r * r
        self.cell = r / math.sqrt(2.0)
        self.grid = {}

    def try_admit(self, x, y):
        ci = int(math.floor(x / self.cell))
        cj = int(math.floor(y / self.cell))
        g = self.grid
        for di in (-2, -1, 0, 1, 2):
            for dj in (-2, -1, 0, 1, 2):
                p = g.get((ci + di, cj + dj))
                if p is not None:
                    dx = x - p[0]
                    dy = y - p[1]
                    if dx * dx + dy * dy < self.r2:
                        return False
        g[(ci, cj)] = (x, y)
        return True


class _AlongSpacing:
    """Along-the-line admission for a minimum separation r: points
    constrain each other ONLY when they lie on the same polyline part,
    measured by arc length along that part -- points on different
    lines (a neighboring drainage) or disconnected parts never block
    each other. Sorted-list insertion, O(log n) per test."""

    def __init__(self, r):
        self.r = r
        self.by_part = {}

    def try_admit(self, part, s):
        import bisect

        lst = self.by_part.setdefault(part, [])
        i = bisect.bisect_left(lst, s)
        if i > 0 and s - lst[i - 1] < self.r:
            return False
        if i < len(lst) and lst[i] - s < self.r:
            return False
        lst.insert(i, s)
        return True


class _NetworkSpacing:
    """Network-distance admission for a minimum separation r on
    polylines: points block each other when the shortest path THROUGH
    THE CONNECTED LINES is under r. Networks are built per GROUP (a
    feature's own parts, a strata-ID group, or the whole class when
    unstratified) -- the minimum set of lines necessary -- so separate
    groups never interact. Junctions form where part ENDPOINTS
    coincide with other parts' endpoints or interiors (T/Y
    confluences) within a snap tolerance; lines that merely cross
    without a shared endpoint are NOT connected. Admission runs a
    radius-bounded Dijkstra over the junction graph and checks a
    per-part sorted registry of admitted arc positions."""

    def __init__(self, r):
        self.r = r
        self.by_part = {}          # part uid -> sorted admitted s
        self.jn_on_part = {}       # part uid -> sorted [(s, node)]
        self.node_locs = []        # node -> [(part uid, s), ...]
        self.adj = {}              # node -> [(node, weight), ...]
        self.n_junctions = 0

    @staticmethod
    def _project(a, cum, x, y):
        """(distance, arc position) of the closest point on the part
        chain ``a`` (vertex cumulative ``cum``) to (x, y)."""
        p, q = a[:-1], a[1:]
        v = q - p
        L2 = (v * v).sum(axis=1)
        L2[L2 == 0] = 1e-300
        t = np.clip(((x - p[:, 0]) * v[:, 0]
                     + (y - p[:, 1]) * v[:, 1]) / L2, 0.0, 1.0)
        cx = p[:, 0] + t * v[:, 0]
        cy = p[:, 1] + t * v[:, 1]
        d = np.hypot(cx - x, cy - y)
        i = int(np.argmin(d))
        seg_len = math.sqrt(L2[i])
        return float(d[i]), float(cum[i] + t[i] * seg_len)

    def build(self, groups, messages=None):
        """``groups``: list of part lists [(uid, (V, 2) array), ...];
        connectivity is found WITHIN each group only."""
        from scipy.spatial import cKDTree

        for parts in groups:
            if not parts:
                continue
            info = {}
            for uid, a in parts:
                seg = np.hypot(a[1:, 0] - a[:-1, 0],
                               a[1:, 1] - a[:-1, 1])
                info[uid] = (a, np.concatenate([[0.0],
                                                np.cumsum(seg)]))
            eps = []                       # (uid, s, x, y)
            for uid, (a, cum) in info.items():
                eps.append((uid, 0.0, a[0, 0], a[0, 1]))
                eps.append((uid, float(cum[-1]), a[-1, 0], a[-1, 1]))
            coords = np.array([(e[2], e[3]) for e in eps])
            span = float(max(np.ptp(coords[:, 0]),
                             np.ptp(coords[:, 1]), 1.0))
            tol = max(1e-3, span * 1e-9)
            bboxes = {uid: (a[:, 0].min() - tol, a[:, 1].min() - tol,
                            a[:, 0].max() + tol, a[:, 1].max() + tol)
                      for uid, (a, _c) in info.items()}
            # cluster coincident endpoints
            parent = list(range(len(eps)))

            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for i, j in cKDTree(coords).query_pairs(tol):
                parent[find(i)] = find(j)
            clusters = {}
            for i in range(len(eps)):
                clusters.setdefault(find(i), []).append(i)
            cl = []
            for idxs in clusters.values():
                cl.append(([(eps[i][0], eps[i][1]) for i in idxs],
                           float(np.mean([eps[i][2] for i in idxs])),
                           float(np.mean([eps[i][3] for i in idxs]))))
            # T/Y junction candidates from a KD-tree over the cluster
            # centers, queried once per part with that part's own bbox
            # half-diagonal (a point inside a bbox is always within
            # the half-diagonal of its center) -- instead of testing
            # every cluster against every part, O(parts^2) on big
            # networks where nearly every endpoint is a cluster
            cand = [[] for _ in cl]
            if cl:
                ctree = cKDTree(np.array([(cx, cy)
                                          for _l, cx, cy in cl]))
                for uid in info:
                    b = bboxes[uid]
                    hx = 0.5 * (b[2] - b[0])
                    hy = 0.5 * (b[3] - b[1])
                    for k in ctree.query_ball_point(
                            (b[0] + hx, b[1] + hy),
                            math.hypot(hx, hy)):
                        cand[k].append(uid)
            for (locs, cx, cy), near in zip(cl, cand):
                on = {u for u, _s in locs}
                # T/Y junctions: this point touching another part's
                # interior
                for uid in near:
                    if uid in on:
                        continue
                    b = bboxes[uid]
                    if not (b[0] <= cx <= b[2] and b[1] <= cy <= b[3]):
                        continue
                    a, cum = info[uid]
                    d, s_pr = self._project(a, cum, cx, cy)
                    if d <= tol:
                        locs.append((uid, s_pr))
                if len(locs) < 2:
                    continue               # a dead end routes nothing
                node = len(self.node_locs)
                self.node_locs.append(locs)
                for uid, s in locs:
                    self.jn_on_part.setdefault(uid, []).append(
                        (s, node))
                self.n_junctions += 1
        for lst in self.jn_on_part.values():
            lst.sort()
            for (s1, n1), (s2, n2) in zip(lst, lst[1:]):
                w = s2 - s1
                self.adj.setdefault(n1, []).append((n2, w))
                self.adj.setdefault(n2, []).append((n1, w))
        if messages is not None and self.n_junctions:
            _say(messages,
                 "{:,} junction{} connect the line network{}.".format(
                     self.n_junctions,
                     "" if self.n_junctions == 1 else "s",
                     "" if len(groups) == 1 else "s"))

    def try_admit(self, part, s):
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
        self.by_part.setdefault(part, []).insert(
            bisect.bisect_left(self.by_part.setdefault(part, []), s),
            s)
        return True


def _admit_batch(cand, metric, spacing_grid, got, extras, ex_got,
                 need, n_got, tried, attempt_budget):
    """Feed candidates (with metric-space twins for the distance test)
    through the spacing grid. Returns (n_got, tried, admitted_any)."""
    admitted_any = False
    for k in range(len(cand)):
        if attempt_budget is not None and tried >= attempt_budget:
            break
        tried += 1
        mx, my = metric[k]
        if spacing_grid.try_admit(float(mx), float(my)):
            got.append(cand[k])
            if extras is not None:
                ex_got.append(extras[k])
            n_got += 1
            admitted_any = True
            if n_got >= need:
                break
    return n_got, tried, admitted_any


# ---------------------------------------------------------------------------
#  polygons: batch rejection sampling
# ---------------------------------------------------------------------------
def _sample_in(prep, need, rng, spacing_grid, max_draws,
               attempt_budget=None):
    """Up to ``need`` uniform points inside the prepared polygon.
    Returns (points, starved)."""
    edges, bands, (x0, y0, x1, y1) = prep
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return np.empty((0, 2)), False
    got = []
    n_got = 0
    draws = 0
    tried = 0
    acc = 0.25
    dry_batches = 0
    while n_got < need and draws < max_draws and dry_batches < 30:
        if attempt_budget is not None and tried >= attempt_budget:
            break
        m = int(min(2_000_000,
                    max(2048, 1.5 * (need - n_got) / max(acc, 0.005))))
        pts = np.column_stack([x0 + rng.random(m) * w,
                               y0 + rng.random(m) * h])
        draws += m
        inside = pts[_pa.points_inside(pts, edges, bands=bands)]
        acc = 0.7 * acc + 0.3 * (len(inside) / m)
        if not len(inside):
            dry_batches += 1
            continue
        if spacing_grid is None:
            take = inside[:need - n_got]
            got.extend(take)
            n_got += len(take)
            dry_batches = 0
        else:
            n_got, tried, admitted_any = _admit_batch(
                inside, inside, spacing_grid, got, None, None, need,
                n_got, tried, attempt_budget)
            dry_batches = 0 if admitted_any else dry_batches + 1
    pts = np.array(got) if got else np.empty((0, 2))
    return pts, n_got < need


# ---------------------------------------------------------------------------
#  polylines: exact arc-length CDF inversion
# ---------------------------------------------------------------------------
def _line_parts(geom):
    """A work-SR polyline's paths as (V, 2) arrays, curves densified."""
    import json

    if getattr(geom, "hasCurves", False):
        d = max(geom.length / 500.0, 1e-9)
        try:
            geom = geom.densify("DISTANCE", d, d)
        except Exception:                                        # noqa: BLE001
            pass
    out = []
    for pth in json.loads(geom.JSON).get("paths", []):
        a = np.asarray(pth, dtype=np.float64)[:, :2]
        if len(a) >= 2:
            out.append(a)
    return out


_PART_UID = itertools.count()          # globally unique line-part ids


def _line_sampler(parts, gap):
    """Uniform-by-arc-length sampler over ``parts`` (a list of
    (uid, (V, 2) array) pairs), each part inset by ``gap`` at BOTH
    ends (the polyline analog of the boundary separation). Tracks
    each sub-segment's part uid and true arc position so along-line
    spacing can be tested. Returns None when nothing remains."""
    P0, DIR, SUB, PART, S0 = [], [], [], [], []
    for uid, a in parts:
        p, q = a[:-1], a[1:]
        seg = np.hypot(q[:, 0] - p[:, 0], q[:, 1] - p[:, 1])
        keep = seg > 0
        p, q, seg = p[keep], q[keep], seg[keep]
        if not len(seg):
            continue
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        lo = gap if gap else 0.0
        hi = cum[-1] - (gap if gap else 0.0)
        if hi <= lo:
            continue
        s0 = np.clip(cum[:-1], lo, hi)
        s1 = np.clip(cum[1:], lo, hi)
        sub = s1 - s0
        m = sub > 0
        if not m.any():
            continue
        d = (q - p) / seg[:, None]
        off = (s0 - cum[:-1])[m]
        P0.append(p[m] + d[m] * off[:, None])
        DIR.append(d[m])
        SUB.append(sub[m])
        PART.append(np.full(int(m.sum()), uid, dtype=np.int64))
        S0.append(s0[m])
    if not P0:
        return None
    P0 = np.vstack(P0)
    DIR = np.vstack(DIR)
    SUB = np.concatenate(SUB)
    cum = np.concatenate([[0.0], np.cumsum(SUB)])
    return {"p0": P0, "dir": DIR, "cum": cum,
            "part": np.concatenate(PART), "s0": np.concatenate(S0),
            "total": float(cum[-1])}


def _line_at(sampler, u):
    """(xy, part uid, true arc position within the part) at arc
    positions ``u`` of the sampler's concatenated allowed length."""
    idx = np.clip(np.searchsorted(sampler["cum"], u, side="right") - 1,
                  0, len(sampler["dir"]) - 1)
    t = u - sampler["cum"][idx]
    xy = sampler["p0"][idx] + sampler["dir"][idx] * t[:, None]
    return xy, sampler["part"][idx], sampler["s0"][idx] + t


def _sample_line(sampler, need, rng, spacing, attempt_budget):
    """Up to ``need`` uniform points along the sampler's lines.
    ``spacing`` is None, a _SpacingGrid (absolute spatial distance) or
    an _AlongSpacing (distance measured along the polyline)."""
    if sampler is None or sampler["total"] <= 0 or need <= 0:
        return np.empty((0, 2)), need > 0
    if spacing is None:
        xy, _p, _s = _line_at(sampler, rng.random(need)
                              * sampler["total"])
        return xy, False
    along = isinstance(spacing, (_AlongSpacing, _NetworkSpacing))
    got = []
    n_got = 0
    tried = 0
    dry = 0
    while n_got < need and dry < 30:
        if attempt_budget is not None and tried >= attempt_budget:
            break
        m = int(min(500_000, max(1024, 4 * (need - n_got))))
        xy, part, s = _line_at(sampler, rng.random(m)
                               * sampler["total"])
        admitted_any = False
        for k in range(len(xy)):
            if attempt_budget is not None and tried >= attempt_budget:
                break
            tried += 1
            ok = spacing.try_admit(int(part[k]), float(s[k])) if along \
                else spacing.try_admit(float(xy[k, 0]),
                                       float(xy[k, 1]))
            if ok:
                got.append(xy[k])
                n_got += 1
                admitted_any = True
                if n_got >= need:
                    break
        dry = 0 if admitted_any else dry + 1
    pts = np.array(got) if got else np.empty((0, 2))
    return pts, n_got < need


# ---------------------------------------------------------------------------
#  rasters: block-streamed, true-area-weighted cell sampling
# ---------------------------------------------------------------------------
def _nodata_sentinel(dtype, taken):
    """An integer representable in ``dtype`` that is not a category."""
    info = np.iinfo(dtype)
    for v in (info.min, info.max):
        if v not in taken:
            return int(v)
    v = info.min
    while v in taken:
        v += 1
    return int(v)


class _RasterPool:
    """Stratum-aware random cell sampler. ``strata`` is a list of
    (label, [values]) -- each stratum is a SET of category values, so
    no-stratification, per-category and strata-ID-field groupings all
    stream in row blocks; ``regions=True`` instead splits every
    category into its CONTIGUOUS regions (cells connected by shared
    edges, Esri's rule) with scipy's 4-connected labeling, which needs
    the whole raster in memory. One pass builds per-block, per-stratum
    TRUE-AREA weights (geographic rows shrink by the authalic band
    area); draws then multinomial the blocks, re-read only the blocks
    they hit, choose cells area-weighted and jitter uniformly within
    each cell. Small rasters are a single cached block, so nothing is
    read twice."""

    def __init__(self, raster, strata, gap_m, messages, regions=False):
        import arcpy

        values = sorted({v for _lab, vals in strata for v in vals})

        self.r = raster
        self.values = list(values)
        self.vset = set(values)
        self.sr = raster.spatialReference
        self.geographic = self.sr.type == "Geographic"
        self.ncols = int(raster.width)
        self.nrows = int(raster.height)
        self.cw = float(raster.meanCellWidth)
        self.ch = float(raster.meanCellHeight)
        ext = raster.extent
        self.xmin = float(ext.XMin)
        self.ymax = float(ext.YMax)
        # the Extent environment clips the READ WINDOW (snapped out to
        # whole cells) -- a small analysis extent on a huge raster
        # reads, and counts against the in-memory limits, only the
        # window
        win = self._env_window(ext)
        if win is not None:
            c0, c1, r0, r1 = win
            if c1 <= c0 or r1 <= r0:
                raise arcpy.ExecuteError(
                    "The Extent environment does not overlap the "
                    "raster.")
            self.xmin += c0 * self.cw
            self.ymax -= r0 * self.ch
            self.ncols = c1 - c0
            self.nrows = r1 - r0
            _say(messages,
                 "Extent environment: reading a {:,} x {:,} cell "
                 "window of the raster.".format(self.nrows,
                                                self.ncols))
        if self.geographic:
            a = float(self.sr.semiMajorAxis or 6378137.0)
            b = float(self.sr.semiMinorAxis or 6356752.314245)
            self.R = (2.0 * a + b) / 3.0
        else:
            self.mpu = float(self.sr.metersPerUnit or 1.0)
        self.block_rows = min(self.nrows,
                              max(1, int(6.0e7 // max(self.ncols, 1))))
        self.nblocks = math.ceil(self.nrows / self.block_rows)
        self._cache = {}
        self._cells = {}     # (block, stratum) -> flat candidates
        self._cells_n = 0    # total cached candidate cells
        self._gap_m = gap_m
        if gap_m and self.nblocks > 1:
            raise arcpy.ExecuteError(
                "The minimum distance from the boundary is supported "
                "for up to about 60 million cells (this read covers "
                "{:,}) -- set the Extent environment to the area you "
                "actually need (only that window is read), or drop "
                "the constraint.".format(self.nrows * self.ncols))
        # contiguous-region strata need the whole raster at once
        self.lab = None
        if regions:
            if self.nblocks > 1:
                raise arcpy.ExecuteError(
                    "Stratifying by contiguous raster region is "
                    "supported for up to about 60 million cells (this "
                    "read covers {:,}) -- set the Extent environment "
                    "to the area you actually need; only that window "
                    "is read.".format(self.nrows * self.ncols))
            from scipy.ndimage import label as _cc_label
            arr, allowed, _rw = self._block(0)
            cross = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
            self.lab = np.zeros(arr.shape, dtype=np.int64)
            new_strata = []
            next_id = 0
            for v in values:
                lb, n_reg = _cc_label(arr == v, structure=cross)
                m = lb > 0
                self.lab[m] = lb[m] + next_id
                for k in range(1, n_reg + 1):
                    new_strata.append(("{} #{}".format(v, k), [v],
                                       next_id + k))
                next_id += n_reg
            strata = new_strata
            _say(messages,
                 "{:,} contiguous regions found across {:,} "
                 "categories.".format(len(strata), len(values)))
        self.strata = strata               # (label, values[, region])
        # streaming pass: per-block x per-stratum true-area weights
        self.W = np.zeros((self.nblocks, len(strata)))
        for bidx in range(self.nblocks):
            arr, allowed, rw = self._block(bidx)
            for si, st in enumerate(strata):
                m = allowed & self._member(arr, bidx, st)
                self.W[bidx, si] = (m * rw[:, None]).sum()
        self.area_m2 = self.W.sum(axis=0)      # per-stratum true area

    def _member(self, arr, bidx, st):
        """Mask of this stratum's cells within a block's array."""
        if self.lab is not None and len(st) > 2:
            r0 = bidx * self.block_rows
            return self.lab[r0:r0 + arr.shape[0], :] == st[2]
        vals = st[1]
        return (arr == vals[0]) if len(vals) == 1 \
            else np.isin(arr, vals)

    def _env_window(self, ext):
        """(col0, col1, row0, row1) of the Extent environment within
        the raster grid, or None when no extent is set."""
        import arcpy

        env_ext = arcpy.env.extent
        if env_ext in (None, "", "DEFAULT", "MAXOF", "MINOF") \
                or isinstance(env_ext, str):
            return None
        esr = env_ext.spatialReference
        if esr is not None and esr.name not in (None, "", "Unknown") \
                and not _vt._same_cs(esr, self.sr):
            rect = _vt._rect_polygon(env_ext.XMin, env_ext.YMin,
                                     env_ext.XMax, env_ext.YMax, esr)
            env_ext = _vt._project_geom(rect, self.sr).extent
        c0 = int(math.floor((env_ext.XMin - self.xmin) / self.cw))
        c1 = int(math.ceil((env_ext.XMax - self.xmin) / self.cw))
        r0 = int(math.floor((self.ymax - env_ext.YMax) / self.ch))
        r1 = int(math.ceil((self.ymax - env_ext.YMin) / self.ch))
        return (max(0, c0), min(self.ncols, c1),
                max(0, r0), min(self.nrows, r1))

    def _row_areas(self, r0, nr):
        """True area (m^2) of one cell in each of these rows."""
        if not self.geographic:
            return np.full(nr, self.cw * self.ch * self.mpu * self.mpu)
        lat_top = np.radians(self.ymax - (r0 + np.arange(nr))
                             * self.ch)
        lat_bot = np.radians(self.ymax - (r0 + np.arange(nr) + 1)
                             * self.ch)
        dlam = math.radians(self.cw)
        return (self.R * self.R * dlam
                * np.abs(np.sin(lat_top) - np.sin(lat_bot)))

    def _block(self, bidx):
        """(values array, allowed mask, per-row cell areas)."""
        import arcpy

        if bidx in self._cache:
            if self.nblocks > 1:           # LRU: refresh recency
                self._cache[bidx] = self._cache.pop(bidx)
            return self._cache[bidx]
        r0 = bidx * self.block_rows
        nr = min(self.block_rows, self.nrows - r0)
        sent = _nodata_sentinel(
            arcpy.RasterToNumPyArray(self.r, arcpy.Point(
                self.xmin, self.ymax - (r0 + 1) * self.ch),
                1, 1).dtype, self.vset)
        arr = arcpy.RasterToNumPyArray(
            self.r, arcpy.Point(self.xmin,
                                self.ymax - (r0 + nr) * self.ch),
            self.ncols, nr, nodata_to_value=sent)
        allowed = np.isin(arr, self.values)
        if self._gap_m:
            from scipy.ndimage import distance_transform_edt
            if self.geographic:
                lat0 = math.radians(self.ymax - 0.5 * self.nrows
                                    * self.ch)
                sy = math.radians(self.ch) * self.R
                sx = math.radians(self.cw) * self.R * math.cos(lat0)
            else:
                sy = self.ch * self.mpu
                sx = self.cw * self.mpu
            # pad with a NoData ring so the raster (or window) EDGE
            # counts as boundary too
            padded = np.zeros((allowed.shape[0] + 2,
                               allowed.shape[1] + 2), dtype=bool)
            padded[1:-1, 1:-1] = allowed
            dist = distance_transform_edt(padded,
                                          sampling=(sy, sx))[1:-1,
                                                             1:-1]
            allowed = allowed & (dist >= self._gap_m)
        rw = self._row_areas(r0, nr)
        out = (arr, allowed, rw)
        self._cache[bidx] = out
        if self.nblocks > 1 and self.nrows * self.ncols > 6.0e7:
            # small LRU of recently read blocks -- the multinomial
            # tends to revisit the same blocks batch after batch
            while len(self._cache) > 3:
                self._cache.pop(next(iter(self._cache)))
        return out

    def _stratum_cells(self, bidx, si):
        """Cached flat candidates for one (block, stratum): (rows,
        cols, cell values, normalized cumulative area weights). Built
        once -- ``allowed`` never changes after __init__ -- so the
        min-spacing rejection loop stops rebuilding the mask/nonzero/
        normalize over the whole block for every batch. Bounded by
        total candidate count, oldest entries evicted first."""
        key = (bidx, si)
        hit = self._cells.get(key)
        if hit is not None:
            self._cells[key] = self._cells.pop(key)    # LRU refresh
            return hit
        arr, allowed, rw = self._block(bidx)
        m2 = allowed & self._member(arr, bidx, self.strata[si])
        rows, cols = np.nonzero(m2)
        if len(rows):
            p = rw[rows]
            p = p / p.sum()
            cdf = p.cumsum()
            cdf /= cdf[-1]
        else:
            cdf = np.empty(0)
        hit = (rows.astype(np.int32), cols.astype(np.int32),
               arr[rows, cols], cdf)
        self._cells[key] = hit
        self._cells_n += len(rows)
        while self._cells_n > 1.2e8 and len(self._cells) > 1:
            old = next(iter(self._cells))
            self._cells_n -= len(self._cells.pop(old)[0])
        return hit

    def draw(self, si, m, rng):
        """m candidate points for stratum index si: (xy in raster CS,
        cell Value per point). Cells are picked by searchsorted over
        the cached CDF with one uniform draw each -- numpy's own
        weighted replace=True ``Generator.choice`` algorithm, so the
        picks (and the RNG stream) are identical to it."""
        w = self.W[:, si]
        tot = w.sum()
        if tot <= 0 or m <= 0:
            return np.empty((0, 2)), np.empty(0, dtype=np.int64)
        per_block = rng.multinomial(m, w / tot)
        XY, VAL = [], []
        for bidx in np.flatnonzero(per_block):
            k = int(per_block[bidx])
            rows, cols, vals, cdf = self._stratum_cells(bidx, si)
            if not len(rows):
                continue
            pick = np.minimum(
                cdf.searchsorted(rng.random(k), side="right"),
                len(rows) - 1)
            rr = rows[pick].astype(np.int64) + bidx * self.block_rows
            cc = cols[pick].astype(np.int64)
            x = self.xmin + (cc + rng.random(k)) * self.cw
            y = self.ymax - (rr + rng.random(k)) * self.ch
            XY.append(np.column_stack([x, y]))
            VAL.append(vals[pick].astype(np.int64))
        if not XY:
            return np.empty((0, 2)), np.empty(0, dtype=np.int64)
        return np.vstack(XY), np.concatenate(VAL)


def _geo_transformation(from_sr, to_sr):
    """The geographic transformation for this datum pair -- the first
    valid choice from the Geographic Transformations environment, else
    Esri's own best suggestion -- or None when the datums already
    match. Resolve ONCE and pass to ``_project_xy``: a bare projectAs
    between different datums silently skips the datum shift (a NAD27
    raster written to WGS84 lands ~100-200 m off)."""
    import arcpy

    try:
        env_t = [t for t in
                 str(arcpy.env.geographicTransformations or "")
                 .split(";") if t]
        tfm, _fb = _ct.transformation_for(from_sr, to_sr, env_t)
        return tfm
    except Exception:                                            # noqa: BLE001
        return None


def _project_xy(xy, from_sr, to_sr, transformation=None):
    """Batch-project (N, 2) coordinates via one multipoint (order is
    preserved; used to test raster candidates' separation in the
    equal-area working projection and to write reprojected outputs).
    ``transformation`` is the datum shift from ``_geo_transformation``
    (resolved once by the caller), or None when none is needed."""
    import json

    import arcpy

    if len(xy) == 0:
        return xy
    mp = arcpy.Multipoint(
        arcpy.Array([arcpy.Point(float(x), float(y)) for x, y in xy]),
        from_sr)
    prj = mp.projectAs(to_sr, transformation) if transformation \
        else mp.projectAs(to_sr)
    out = np.asarray(json.loads(prj.JSON)["points"],
                     dtype=np.float64)[:, :2]
    if len(out) != len(xy):                       # paranoid fallback
        out = np.array([[p.firstPoint.X, p.firstPoint.Y] for p in
                        ((arcpy.PointGeometry(
                            arcpy.Point(float(x), float(y)),
                            from_sr).projectAs(to_sr, transformation)
                          if transformation else
                          arcpy.PointGeometry(
                              arcpy.Point(float(x), float(y)),
                              from_sr).projectAs(to_sr))
                         for x, y in xy)])
    return out


def _sample_raster(pool, ci, need, rng, spacing_grid, attempt_budget,
                   metric_sr):
    """Up to ``need`` points for category index ci (None = whole
    raster). Returns (xy, values, starved)."""
    if need <= 0:
        return np.empty((0, 2)), np.empty(0, dtype=np.int64), False
    if spacing_grid is None:
        xy, vals = pool.draw(ci, need, rng)
        return xy, vals, len(xy) < need
    got, ex = [], []
    n_got = 0
    tried = 0
    dry = 0
    tfm = _geo_transformation(pool.sr, metric_sr) \
        if metric_sr is not None else None
    while n_got < need and dry < 30:
        if attempt_budget is not None and tried >= attempt_budget:
            break
        m = int(min(200_000, max(1024, 4 * (need - n_got))))
        xy, vals = pool.draw(ci, m, rng)
        if not len(xy):
            dry += 1
            continue
        metric = _project_xy(xy, pool.sr, metric_sr,
                             transformation=tfm) \
            if metric_sr is not None else xy
        n_got, tried, admitted_any = _admit_batch(
            xy, metric, spacing_grid, got, vals, ex, need, n_got,
            tried, attempt_budget)
        dry = 0 if admitted_any else dry + 1
    xy = np.array(got) if got else np.empty((0, 2))
    vals = np.array(ex, dtype=np.int64) if ex \
        else np.empty(0, dtype=np.int64)
    return xy, vals, n_got < need


# ---------------------------------------------------------------------------
#  main entry
# ---------------------------------------------------------------------------
def run_random_points(out_fc, boundary=None, extent=None,
                      strata_method=None, strata_field=None,
                      alloc_method=None, n_points=None,
                      n_per_stratum=None, population_field=None,
                      density=None, density_units="Hectare",
                      min_spacing=None, spacing_type=None,
                      boundary_gap=None, linear_units="Meters",
                      random_seed=None, max_attempts=1000,
                      messages=None):
    """Generate the points. The sampling design is STRATA x
    ALLOCATION (see the module docstring); distances are in
    ``linear_units``; density is points per ``density_units`` (an area
    unit, or a length unit for polylines). Returns the output path."""
    import arcpy

    extent = _vt.parse_extent(extent) or extent
    strata = canon_strata(strata_method)
    if strata is None:
        raise arcpy.ExecuteError(
            "Unknown stratification method: {}".format(strata_method))
    alloc = canon_alloc(alloc_method)
    if alloc is None:
        alloc = ALLOC_TOTAL if strata == STRATA_NONE else ALLOC_EQUAL
    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown distance unit: {}".format(linear_units))

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
    if strata != STRATA_NONE and kind == "extent":
        raise arcpy.ExecuteError(
            "Stratified sampling needs a feature class or raster (an "
            "extent has no strata).")
    if strata == STRATA_INDIV and kind == "raster":
        raise arcpy.ExecuteError(
            "Stratify by individual polygon/polyline needs a feature "
            "class; for rasters use categories, contiguous regions or "
            "a strata ID field.")
    if strata in (STRATA_CATEGORY, STRATA_REGION) and kind != "raster":
        raise arcpy.ExecuteError(
            "Stratifying by raster category or contiguous region "
            "needs an integer raster.")
    if strata == STRATA_IDFIELD and not strata_field:
        raise arcpy.ExecuteError(
            "Stratify by strata ID field needs the Strata ID field.")
    if alloc in (ALLOC_EQUAL_FIELD, ALLOC_PROP_FIELD) \
            and not population_field:
        raise arcpy.ExecuteError(
            "This allocation method needs the Population field.")
    if alloc in (ALLOC_EQUAL_FIELD, ALLOC_PROP_FIELD) \
            and strata == STRATA_NONE:
        raise arcpy.ExecuteError(
            "Population-field allocation needs stratified sampling.")
    if alloc == ALLOC_DENSITY:
        table = _DENS_LIN_M if kind == "polyline" else _DENS_M2
        if density_units not in table:
            raise arcpy.ExecuteError(
                "Unknown density unit for this input: {} (expected "
                "one of {}).".format(density_units,
                                     ", ".join(sorted(table))))

    rng = np.random.default_rng(
        int(random_seed) if random_seed not in (None, "") else None)
    attempts = max(1, int(max_attempts or 1000))

    _mh.begin_phases([("Reading the area of interest...", 1),
                      ("Generating random points...", 4),
                      ("Writing the points...", 2)])

    if kind == "raster":
        smode = canon_spacing(spacing_type)
        if smode is None:
            raise arcpy.ExecuteError(
                "Unknown spacing-measurement choice: {}".format(
                    spacing_type))
        if smode in (SPACING_ALONG, SPACING_NETWORK) \
                and min_spacing not in (None, "", 0):
            _warn(messages,
                  "Along-the-line and network spacing apply to "
                  "polyline areas of interest only -- using absolute "
                  "spatial distance.")
        return _run_raster(out_fc, boundary, desc, strata,
                           strata_field, alloc, n_points,
                           n_per_stratum, population_field, density,
                           density_units, min_spacing, boundary_gap,
                           linear_units, rng, attempts, messages)

    # ---- vector / extent: coordinate system + working projection ---------
    src_sr = None
    if desc is not None:
        src_sr = desc.spatialReference
    elif isinstance(extent, arcpy.Extent) \
            and extent.spatialReference is not None:
        src_sr = extent.spatialReference
    out_sr = arcpy.env.outputCoordinateSystem or src_sr
    if out_sr is None or getattr(out_sr, "name", "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "No coordinate system: give a boundary/extent with one, or "
            "set the Output Coordinate System environment.")
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
             "{:.4f}, {:.4f}); density is exact, separation distances "
             "are true to second order, and the output is written back "
             "in {}.".format(lon0, lat0, out_sr.name))
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
    smode = canon_spacing(spacing_type)
    if smode is None:
        raise arcpy.ExecuteError(
            "Unknown spacing-measurement choice: {}".format(
                spacing_type))
    if smode in (SPACING_ALONG, SPACING_NETWORK) \
            and kind != "polyline":
        if r_cs is not None:
            _warn(messages,
                  "Along-the-line and network spacing apply to "
                  "polyline areas of interest only -- using absolute "
                  "spatial distance.")
        smode = SPACING_SPATIAL
    if r_cs is None:
        spacing = None
    elif smode == SPACING_ALONG:
        spacing = _AlongSpacing(r_cs)
        _say(messages,
             "Minimum spacing is measured ALONG each polyline -- "
             "points on separate lines (or disconnected parts) never "
             "block each other.")
    elif smode == SPACING_NETWORK:
        spacing = _NetworkSpacing(r_cs)   # graph built once the
        #                                   lines are read
        _say(messages,
             "Minimum spacing is measured through the CONNECTED LINE "
             "NETWORK -- lines joined at shared endpoints or "
             "confluences constrain each other by travel distance; "
             "unconnected lines never do.")
    else:
        spacing = _SpacingGrid(r_cs)

    core = _run_lines_core if kind == "polyline" else _run_polygons_core
    rows, starved_any, want_total = core(
        boundary, extent, desc, work_sr, out_sr, strata, strata_field,
        alloc, n_points, n_per_stratum, population_field, density,
        density_units, gap_cs, r_cs, spacing, rng, attempts, m_per_cs,
        linear_units, min_spacing, messages)
    _mh.phase_advance()

    src_field = strata in (STRATA_INDIV, STRATA_IDFIELD)
    out_path = _write_points(out_fc, out_sr, work_sr, geographic, rows,
                             src_field=src_field, cat_field=False,
                             strat_field=strata != STRATA_NONE,
                             messages=messages,
                             starved_any=starved_any)
    _stamp(out_path, kind, strata_method or strata,
           alloc_method or alloc, sum(len(x[0]) for x in rows),
           want_total, boundary, min_spacing, boundary_gap,
           linear_units, density, density_units, random_seed)
    _mh.end_phases()
    return out_path


# ---------------------------------------------------------------------------
#  allocation (shared by all input kinds)
# ---------------------------------------------------------------------------
def _alloc_counts(alloc, labels, measures, pops, n_points,
                  n_per_stratum, density, density_units, unit_m,
                  unit_word, messages):
    """Per-stratum counts: measures are geodesic m^2 (or m of line);
    pops are population-field totals (None unless field-based)."""
    import arcpy

    k = len(labels)
    if alloc in (ALLOC_TOTAL, ALLOC_PROP_AREA):
        n = max(0, int(n_points or 0))
        if n <= 0:
            raise arcpy.ExecuteError(
                "Give a total number of points greater than zero.")
        counts = _apportion(n, measures)
    elif alloc == ALLOC_EQUAL:
        n = max(0, int(n_per_stratum or 0))
        if n <= 0:
            raise arcpy.ExecuteError(
                "Give a per-stratum number of points greater than "
                "zero.")
        counts = np.full(k, n, dtype=np.int64)
    elif alloc == ALLOC_EQUAL_FIELD:
        counts = np.array([max(0, int(round(p or 0))) for p in pops],
                          dtype=np.int64)
    elif alloc == ALLOC_PROP_FIELD:
        n = max(0, int(n_points or 0))
        if n <= 0:
            raise arcpy.ExecuteError(
                "Give a total number of points greater than zero.")
        counts = _apportion(n, [max(0.0, float(p or 0))
                                for p in pops])
    else:                                       # ALLOC_DENSITY
        d = float(density or 0)
        if d <= 0:
            raise arcpy.ExecuteError(
                "Give a density greater than zero.")
        counts = np.array([int(round(d * m / unit_m))
                           for m in measures], dtype=np.int64)
        _say(messages,
             "Density {:g} points per {} of {} (geodesic) -> {:,} "
             "points in all.".format(d, density_units.lower(),
                                     unit_word, int(counts.sum())))
    if counts.sum() <= 0:
        raise arcpy.ExecuteError(
            "The requested counts work out to zero points.")
    return counts


def _check_pop(v, field):
    import arcpy

    if v is not None and float(v) < 0:
        raise arcpy.ExecuteError(
            "The population field '{}' contains negative values -- "
            "counts cannot be negative.".format(field))
    return float(v or 0)


# ---------------------------------------------------------------------------
#  polygons / extent core
# ---------------------------------------------------------------------------
def _run_polygons_core(boundary, extent, desc, work_sr, out_sr, strata,
                       strata_field, alloc, n_points, n_per_stratum,
                       population_field, density, density_units,
                       gap_cs, r_cs, spacing, rng, attempts, m_per_cs,
                       linear_units, min_spacing, messages):
    import arcpy

    def geod_area(g):
        try:
            return abs(g.getArea("GEODESIC", "SQUAREMETERS"))
        except Exception:                                        # noqa: BLE001
            return abs(g.area) * m_per_cs * m_per_cs

    env_rect = _env_rect(work_sr, out_sr)
    # strata_list entries: [label, members [(oid, geom, measure)],
    #                       measure_total, pop_total]
    if strata == STRATA_NONE:
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
        if strata == STRATA_IDFIELD:
            cols.append(strata_field)
        if population_field and alloc in (ALLOC_EQUAL_FIELD,
                                          ALLOC_PROP_FIELD):
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
                if strata == STRATA_IDFIELD:
                    key = row[ci]
                    ci += 1
                pop = 0.0
                if len(cols) > ci:
                    pop = _check_pop(row[ci], population_field)
                gw = _vt._project_geom(g, work_sr)
                if env_rect is not None:
                    gw = gw.intersect(env_rect, 4)
                    if gw is None or gw.area <= 0:
                        continue
                a = geod_area(gw)
                if key not in groups:
                    label = "polygon OID {}".format(oid) \
                        if strata == STRATA_INDIV \
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

    counts = _alloc_counts(
        alloc, [s[0] for s in strata_list],
        [s[2] for s in strata_list], [s[3] for s in strata_list],
        n_points, n_per_stratum, density, density_units,
        _DENS_M2.get(density_units, 1.0), "area", messages)
    want_total = int(counts.sum())

    if r_cs is not None and want_total > 0:
        area_cs = sum(abs(g.area) for s in strata_list
                      for _oid, g, _a in s[1])
        n_jam = 0.547 * area_cs / (math.pi * (r_cs / 2.0) ** 2)
        if want_total > n_jam:
            _warn(messages,
                  "{:,} points at a minimum spacing of {:g} {} exceeds "
                  "the theoretical random-packing limit (~{:,.0f}) for "
                  "this area -- the tool will place as many as fit "
                  "within the placement-attempts budget.".format(
                      want_total, float(min_spacing),
                      linear_units.lower(), n_jam))

    rows = []
    placed = 0
    starved_any = False
    for si, (label, members, _mt, _pt) in enumerate(strata_list):
        need = int(counts[si])
        if need <= 0:
            continue
        strat_label = None if strata == STRATA_NONE else \
            (str(members[0][0]) if strata == STRATA_INDIV
             else label.replace("stratum '", "").rstrip("'"))
        member_counts = _apportion(need, [m[2] for m in members])
        got_stratum = 0
        for (oid, geom, _a), mneed in zip(members, member_counts):
            mneed = int(mneed)
            if mneed <= 0:
                continue
            small = _shrink(geom, gap_cs, messages,
                            "polygon OID {}".format(oid)
                            if oid is not None else label)
            if small is None:
                starved_any = True
                continue
            prep = _prep_rings(small)
            if prep is None:
                starved_any = True
                continue
            budget = attempts * mneed if spacing is not None else None
            max_draws = max(10_000_000, 400 * mneed,
                            4 * (budget or 0))
            pts, starved = _sample_in(prep, mneed, rng, spacing,
                                      max_draws, budget)
            starved_any = starved_any or starved
            _starve_warn(starved, spacing, messages,
                         label if oid is None
                         else "polygon OID {}".format(oid),
                         len(pts), mneed, attempts)
            if len(pts):
                rows.append((pts, oid, None, strat_label))
                placed += len(pts)
                got_stratum += len(pts)
        _mh.phase_step(placed, want_total)
    return rows, starved_any, want_total


# ---------------------------------------------------------------------------
#  polylines core
# ---------------------------------------------------------------------------
def _run_lines_core(boundary, extent, desc, work_sr, out_sr, strata,
                    strata_field, alloc, n_points, n_per_stratum,
                    population_field, density, density_units, gap_cs,
                    r_cs, spacing, rng, attempts, m_per_cs,
                    linear_units, min_spacing, messages):
    import arcpy

    n_total, n_sel, has_sel = _mh.selection_info(boundary,
                                                 str(boundary))
    if has_sel:
        _say(messages, "The line layer has an active selection: {} of "
                       "{} polylines are used.".format(n_sel, n_total))
    # honor the Extent environment exactly as the polygon and raster
    # paths do: polylines are clipped to it before their geodesic
    # lengths enter the allocation weights
    env_rect = _env_rect(work_sr, out_sr)
    if env_rect is not None:
        _say(messages, "Extent environment: the polylines are "
                       "clipped to it.")
    try:
        work_m = float(getattr(work_sr, "metersPerUnit", 1.0) or 1.0)
    except Exception:                                            # noqa: BLE001
        work_m = 1.0
    cols = ["OID@", "SHAPE@"]
    if strata == STRATA_IDFIELD:
        cols.append(strata_field)
    if population_field and alloc in (ALLOC_EQUAL_FIELD,
                                      ALLOC_PROP_FIELD):
        cols.append(population_field)
    groups = {}
    order = []
    with arcpy.da.SearchCursor(boundary, cols) as cur:
        for row in cur:
            oid, g = row[0], row[1]
            if g is None:
                continue
            ci = 2
            key = 0 if strata == STRATA_NONE else oid
            if strata == STRATA_IDFIELD:
                key = row[ci]
                ci += 1
            pop = 0.0
            if len(cols) > ci:
                pop = _check_pop(row[ci], population_field)
            gw = _vt._project_geom(g, work_sr)
            if env_rect is not None:
                try:
                    gw = gw.intersect(env_rect, 2)
                except Exception:                                # noqa: BLE001
                    gw = None
                if gw is None or gw.length <= 0:
                    continue
            try:
                lg = abs(gw.getLength("GEODESIC", "METERS"))
            except Exception:                                    # noqa: BLE001
                lg = abs(gw.length) * work_m
            parts = [(next(_PART_UID), a) for a in
                     _line_parts(gw)]
            if not parts:
                continue
            if key not in groups:
                label = "the lines" if strata == STRATA_NONE else \
                    ("polyline OID {}".format(oid)
                     if strata == STRATA_INDIV
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
    if strata != STRATA_NONE:
        _say(messages, "{:,} strat{} defined.".format(
            len(strata_list), "um" if len(strata_list) == 1 else "a"))
    if isinstance(spacing, _NetworkSpacing):
        # build the junction graphs over the MINIMUM sets of lines:
        # one network per stratum group (whole class when
        # unstratified, an ID group's joined segments, or a single
        # feature's parts -- the multipart Y case)
        spacing.build([[part for _oid, parts, _lg in s[1]
                        for part in parts] for s in strata_list],
                      messages)
    _mh.phase_advance()

    counts = _alloc_counts(
        alloc, [s[0] for s in strata_list],
        [s[2] for s in strata_list], [s[3] for s in strata_list],
        n_points, n_per_stratum, density, density_units,
        _DENS_LIN_M.get(density_units, 1.0), "line length", messages)
    want_total = int(counts.sum())

    if r_cs is not None and want_total > 0:
        len_cs = sum(np.hypot(*(a[1:] - a[:-1]).T).sum()
                     for s in strata_list for _o, parts, _l in s[1]
                     for _uid, a in parts)
        n_jam = 0.7476 * len_cs / r_cs + sum(
            len(s[1]) for s in strata_list)      # Renyi's parking
        if want_total > n_jam:
            _warn(messages,
                  "{:,} points at a minimum spacing of {:g} {} exceeds "
                  "the packing limit (~{:,.0f}) for this much line -- "
                  "the tool will place as many as fit within the "
                  "placement-attempts budget.".format(
                      want_total, float(min_spacing),
                      linear_units.lower(), n_jam))

    rows = []
    placed = 0
    starved_any = False
    for si, (label, members, _mt, _pt) in enumerate(strata_list):
        need = int(counts[si])
        if need <= 0:
            continue
        strat_label = None if strata == STRATA_NONE else \
            (str(members[0][0]) if strata == STRATA_INDIV
             else label.replace("stratum '", "").rstrip("'"))
        member_counts = _apportion(need, [m[2] for m in members])
        for (oid, parts, _lg), mneed in zip(members, member_counts):
            mneed = int(mneed)
            if mneed <= 0:
                continue
            sampler = _line_sampler(parts, gap_cs)
            if sampler is None:
                if gap_cs:
                    _warn(messages,
                          "polyline OID {}: shorter than twice the "
                          "boundary separation -- no points placed "
                          "there.".format(oid))
                starved_any = True
                continue
            budget = attempts * mneed if spacing is not None else None
            pts, starved = _sample_line(sampler, mneed, rng, spacing,
                                        budget)
            starved_any = starved_any or starved
            _starve_warn(starved, spacing, messages,
                         "polyline OID {}".format(oid), len(pts),
                         mneed, attempts)
            if len(pts):
                rows.append((pts,
                             oid if strata != STRATA_NONE else None,
                             None, strat_label))
                placed += len(pts)
        _mh.phase_step(placed, want_total)
    return rows, starved_any, want_total


# ---------------------------------------------------------------------------
#  raster core
# ---------------------------------------------------------------------------
def _run_raster(out_fc, boundary, desc, strata, strata_field, alloc,
                n_points, n_per_stratum, population_field, density,
                density_units, min_spacing, boundary_gap, linear_units,
                rng, attempts, messages):
    import arcpy

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
        raise arcpy.ExecuteError("The raster has no coordinate system.")
    out_sr = arcpy.env.outputCoordinateSystem or rsr

    # the attribute table: Value + optional strata/population fields
    vat_cols = ["Value"]
    if strata == STRATA_IDFIELD:
        vat_cols.append(strata_field)
    if population_field and alloc in (ALLOC_EQUAL_FIELD,
                                      ALLOC_PROP_FIELD):
        vat_cols.append(population_field)
    values = []
    key_of = {}
    pop_of = {}
    with arcpy.da.SearchCursor(path, vat_cols) as cur:
        for row in cur:
            v = int(row[0])
            values.append(v)
            ci = 1
            if strata == STRATA_IDFIELD:
                key_of[v] = row[ci]
                ci += 1
            if len(vat_cols) > ci:
                pop_of[v] = _check_pop(row[ci], population_field)
    if not values:
        raise arcpy.ExecuteError(
            "The raster attribute table is empty.")
    _say(messages, "{:,} categories in the attribute table (Value "
                   "field).".format(len(values)))

    # strata as (label, [values]) sets
    if strata == STRATA_NONE:
        strata_def = [("the raster", list(values))]
    elif strata in (STRATA_CATEGORY, STRATA_REGION):
        strata_def = [(str(v), [v]) for v in values]
    else:                                       # IDFIELD groups
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
        if boundary_gap not in (None, "", 0) else None
    r_m = float(min_spacing) * _LIN_M[linear_units] \
        if min_spacing not in (None, "", 0) else None
    metric_sr = None
    if geographic:
        ext = r.extent
        metric_sr = _rs._laea_for(
            rsr, 0.5 * (ext.XMin + ext.XMax),
            0.5 * (ext.YMin + ext.YMax))
        _say(messages,
             "Geographic raster: cells are picked with their TRUE "
             "areas (density exact) and separations are tested in an "
             "automatic equal-area working projection.")
        if gap_m:
            _warn(messages,
                  "The boundary separation on a geographic raster "
                  "uses the raster's average cell size at the center "
                  "latitude -- exact to a fraction of a cell at "
                  "regional scales.")

    pool = _RasterPool(r, strata_def, gap_m, messages,
                       regions=strata == STRATA_REGION)
    spacing = None
    if r_m is not None:
        mpu = 1.0 if geographic else float(rsr.metersPerUnit or 1.0)
        spacing = _SpacingGrid(r_m if geographic else r_m / mpu)
    _mh.phase_advance()

    # per-stratum population totals (regions inherit their category's)
    pops = []
    for st in pool.strata:
        pops.append(sum(pop_of.get(v, 0.0) for v in st[1]))
    counts = _alloc_counts(
        alloc, [st[0] for st in pool.strata], pool.area_m2, pops,
        n_points, n_per_stratum, density, density_units,
        _DENS_M2.get(density_units, 1.0), "cell area", messages)
    want_total = int(counts.sum())
    if strata != STRATA_NONE:
        _say(messages, "{:,} strat{} defined.".format(
            len(pool.strata),
            "um" if len(pool.strata) == 1 else "a"))

    if r_m is not None:
        n_jam = 0.547 * pool.area_m2.sum() \
            / (math.pi * (r_m / 2.0) ** 2)
        if want_total > n_jam:
            _warn(messages,
                  "{:,} points at a minimum spacing of {:g} {} exceeds "
                  "the theoretical random-packing limit (~{:,.0f}) for "
                  "the raster's data area -- the tool will place as "
                  "many as fit within the placement-attempts "
                  "budget.".format(want_total, float(min_spacing),
                                   linear_units.lower(), n_jam))

    rows = []
    placed = 0
    starved_any = False
    for si, st in enumerate(pool.strata):
        need = int(counts[si])
        if need <= 0:
            continue
        label = "stratum '{}'".format(st[0]) \
            if strata != STRATA_NONE else st[0]
        if pool.area_m2[si] <= 0:
            _warn(messages,
                  "{}: no usable cells (all NoData, or all within the "
                  "boundary separation).".format(label))
            starved_any = True
            continue
        budget = attempts * need if spacing is not None else None
        xy, vals, starved = _sample_raster(pool, si, need, rng,
                                           spacing, budget, metric_sr)
        starved_any = starved_any or starved
        _starve_warn(starved, spacing, messages, label, len(xy), need,
                     attempts)
        if len(xy):
            rows.append((xy, None, vals,
                         st[0] if strata != STRATA_NONE else None))
            placed += len(xy)
        _mh.phase_step(placed, want_total)
    _mh.phase_advance()

    out_path = _write_points(out_fc, out_sr, rsr,
                             not _vt._same_cs(rsr, out_sr), rows,
                             src_field=False, cat_field=True,
                             strat_field=strata != STRATA_NONE,
                             messages=messages,
                             starved_any=starved_any)
    _stamp(out_path, "raster", strata, alloc, placed, want_total,
           boundary, min_spacing, boundary_gap, linear_units, density,
           density_units, None)
    _mh.end_phases()
    return out_path


# ---------------------------------------------------------------------------
#  shared write / warn / stamp
# ---------------------------------------------------------------------------
def _env_rect(work_sr, out_sr):
    import arcpy

    env_ext = arcpy.env.extent
    if env_ext in (None, "", "DEFAULT", "MAXOF", "MINOF") \
            or isinstance(env_ext, str):
        return None
    esr = env_ext.spatialReference
    rect = _vt._rect_polygon(env_ext.XMin, env_ext.YMin, env_ext.XMax,
                             env_ext.YMax,
                             esr if esr is not None
                             and esr.name not in (None, "", "Unknown")
                             else out_sr)
    return _vt._project_geom(rect, work_sr) \
        if not _vt._same_cs(rect.spatialReference, work_sr) else rect


def _starve_warn(starved, spacing, messages, label, got, need,
                 attempts):
    if not starved:
        return
    if spacing is not None:
        _warn(messages,
              "{}: only {:,} of {:,} points fit within {:,} placement "
              "attempts per point -- raise the attempts in the "
              "Advanced section to try harder, or relax the "
              "spacing/boundary-distance constraints.".format(
                  label, got, need, attempts))
    else:
        _warn(messages,
              "{}: only {:,} of {:,} points could be placed (the "
              "usable area is empty or vanishingly thin).".format(
                  label, got, need))


def _write_points(out_fc, out_sr, work_sr, reproject, rows, src_field,
                  cat_field, strat_field, messages, starved_any):
    """rows: list of (xy array in work/raster SR, src_fid, values,
    stratum label)."""
    import arcpy

    out_fc = str(out_fc).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_fc)
    if ws and not _mh.in_gdb_container(out_fc) \
            and not name.lower().endswith(".shp"):
        name += ".shp"
    res = arcpy.management.CreateFeatureclass(
        ws, name, "POINT", spatial_reference=_vt.clean_sr(out_sr))
    out_path = res.getOutput(0)
    field_rows = [["Point_ID", "LONG", "Point ID", "", "", ""]]
    if strat_field:
        field_rows.append(["Stratum", "TEXT", "Stratum", 64, "", ""])
    if src_field:
        field_rows.append(["Src_FID", "LONG",
                           "Source feature Object ID", "", "", ""])
    if cat_field:
        field_rows.append(["Category", "LONG",
                           "Raster category (Value)", "", "", ""])
    arcpy.management.AddFields(out_path, field_rows)

    total = sum(len(x[0]) for x in rows)
    n_written = 0
    # batch-project each row's coordinates in one call (chunked so a
    # multipoint never carries millions of vertices), datum shift
    # resolved once -- not one PointGeometry.projectAs per point
    tfm = _geo_transformation(work_sr, out_sr) if reproject else None
    cols = ["SHAPE@XY", "Point_ID"]
    if strat_field:
        cols.append("Stratum")
    if src_field:
        cols.append("Src_FID")
    if cat_field:
        cols.append("Category")
    with arcpy.da.InsertCursor(out_path, cols) as ins:
        for xy, fid, vals, strat in rows:
            for j0 in range(0, len(xy), 100_000):
                chunk = xy[j0:j0 + 100_000]
                pts = _project_xy(chunk, work_sr, out_sr,
                                  transformation=tfm) \
                    if reproject else chunk
                for k in range(len(pts)):
                    n_written += 1
                    row = [(float(pts[k, 0]), float(pts[k, 1])),
                           n_written]
                    if strat_field:
                        row.append(str(strat)[:64]
                                   if strat is not None else None)
                    if src_field:
                        row.append(fid)
                    if cat_field:
                        row.append(int(vals[j0 + k])
                                   if vals is not None else None)
                    ins.insertRow(row)
                    if n_written % 2000 == 0:
                        _mh.phase_step(n_written, total)
    _say(messages, "{:,} random point{} written{}.".format(
        n_written, "" if n_written == 1 else "s",
        "" if not starved_any
        else " (fewer than requested -- see the warnings above)"))
    return out_path


def _stamp(out_path, kind, strata_txt, alloc_txt, n, want, boundary,
           min_spacing, boundary_gap, linear_units, density,
           density_units, random_seed):
    import arcpy

    try:
        bits = ["{}; {}".format(str(strata_txt).lower(),
                                str(alloc_txt).lower())]
        if density not in (None, "", 0):
            bits.append("density {:g} per {}".format(
                float(density), str(density_units).lower()))
        if min_spacing not in (None, "", 0):
            bits.append("minimum spacing {:g} {}".format(
                float(min_spacing), linear_units.lower()))
        if boundary_gap not in (None, "", 0):
            bits.append("minimum {:g} {} from the boundary".format(
                float(boundary_gap), linear_units.lower()))
        if random_seed not in (None, ""):
            bits.append("seed {}".format(random_seed))
        aoi = {"polygon": "polygon features from ",
               "polyline": "polyline features from ",
               "raster": "the integer raster "}.get(kind)
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = (
            "Created by Random Point Generator: {n:,} of {w:,} "
            "requested points ({b}) over {aoi}.".format(
                n=n, w=want, b="; ".join(bits),
                aoi=(aoi + os.path.basename(str(boundary)))
                    if aoi and boundary not in (None, "")
                    else "an extent"))
        md.description = (
            "Uniform random points from the Jenness Enterprises "
            "Random Point Generator tool (a modernized port of the "
            "classic ArcView extension): within polygons, along "
            "polylines, or within the data cells of an integer "
            "raster's categories. Points are uniformly distributed by "
            "area (length for polylines; true cell area for rasters), "
            "a Simple Sequential Inhibition process when a minimum "
            "separation is requested; an optional minimum distance "
            "from the boundary is enforced by insetting the area (or "
            "the line ends, or the NoData edge) before sampling. On "
            "geographic coordinate systems the work happens in an "
            "automatic equal-area projection so density is exact "
            "everywhere.")
        md.save()
    except Exception:                                            # noqa: BLE001
        pass
