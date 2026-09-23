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

"""Concave Hull -- the shape of a cloud of points, by three classic
methods.

The convex hull of a point cloud often wildly overstates the region
the points actually occupy: any concavity -- a bay, an arc, an
L-shaped stand -- is bridged straight across. A CONCAVE hull follows
the cloud's true outline, with a sensitivity control deciding how
tightly: at one extreme the convex hull, at the other a shape eroded
down toward the points themselves.

Three methods, the three classic lineages of the problem:

- CHI SHAPE (characteristic shape; Duckham, Kulik, Worboys and
  Galton 2008, Pattern Recognition 41:3224-3236,
  DOI 10.1016/j.patcog.2008.03.023): start from the Delaunay
  triangulation (whose boundary is the convex hull) and repeatedly
  remove the longest boundary edge whose removal keeps the
  triangulation regular. Guarantees: always ONE simple polygon
  containing EVERY point, degrading gracefully to the convex hull,
  with nested shapes as the parameter shrinks. O(n log n).
- ALPHA SHAPE (Edelsbrunner, Kirkpatrick and Seidel 1983, IEEE
  Trans. Information Theory 29:551-559,
  DOI 10.1109/TIT.1983.1056714): the region an eraser disk of radius
  alpha cannot reach -- computed as the Delaunay triangles whose
  circumradius is at most alpha. The only method that can return
  MULTIPLE disjoint polygons and interior HOLES (two herds; a ring
  of telemetry around a lake).
- K-NEAREST-NEIGHBOURS hull (Moreira and Santos 2007, Proc. GRAPP
  2007, 61-68, DOI 10.5220/0002080800610068): a gift-wrapping walk
  where each next boundary vertex is chosen among the current
  point's k nearest neighbours; k controls smoothness, and the
  algorithm raises k automatically until the polygon closes around
  every point.

Auto parameter selection (deliberately moderate -- Concavity 100
requests each method's true extreme): chi = a two-class threshold
(Otsu on log edge length) separating gap-spanning edges from
density-scale edges, computed from the full nested family; alpha =
the smallest alpha keeping every point covered (and the shape in one
piece unless multiple parts are allowed) -- the "optimal alpha" rule
of CGAL / PostGIS -- inflated by a 50% stability margin; k-NN = a k
that grows with the point count (about the square root of n),
raised further only if the hull cannot close.

Equivalents elsewhere: PostGIS ST_ConcaveHull / SFCGAL
ST_AlphaShape and ST_OptimalAlphaShape, CGAL's Alpha_shape_2, and
the QGIS concave-hull processing algorithms. Esri's closest analog
(Aggregate Points) needs an Advanced license; this tool runs at
every license level.
"""
import heapq
import clip_tools as _ct
import math
import os

import numpy as np

import mahalanobis as _mh
import patch_analysis as _pa
import centerline_width as _cw
import longest_line as _ll
import inscribed_geometry as _ig

_say = _cw._say
_warn = _cw._warn

METHOD_CHI = "Chi shape (characteristic shape)"
METHOD_ALPHA = "Alpha shape"
METHOD_KNN = "k-nearest-neighbours hull"
METHODS = [METHOD_CHI, METHOD_ALPHA, METHOD_KNN]

LINEAR_UNITS = _ig.LINEAR_UNITS
_LIN_M = _ig._LIN_M


# ---------------------------------------------------------------------------
#  shared Delaunay helpers (numpy / scipy; unit tested without arcpy)
# ---------------------------------------------------------------------------
def _delaunay(pts):
    from scipy.spatial import Delaunay
    try:
        return Delaunay(pts)
    except Exception:                                            # noqa: BLE001
        return Delaunay(pts, qhull_options="QJ")


def _ccw_simplices(pts, tri):
    """The Delaunay simplices with every triangle forced
    counter-clockwise."""
    s = tri.simplices.copy()
    a, b, c = pts[s[:, 0]], pts[s[:, 1]], pts[s[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) \
        - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    flip = cross < 0
    s[flip, 1], s[flip, 2] = s[flip, 2], s[flip, 1].copy()
    return s


def _tri_areas(pts, s):
    a, b, c = pts[s[:, 0]], pts[s[:, 1]], pts[s[:, 2]]
    return 0.5 * np.abs(
        (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
        - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))


def _circumradii(pts, s):
    a, b, c = pts[s[:, 0]], pts[s[:, 1]], pts[s[:, 2]]
    la = np.hypot(b[:, 0] - c[:, 0], b[:, 1] - c[:, 1])
    lb = np.hypot(a[:, 0] - c[:, 0], a[:, 1] - c[:, 1])
    lc = np.hypot(a[:, 0] - b[:, 0], a[:, 1] - b[:, 1])
    area = _tri_areas(pts, s)
    # floor the area at a scale-relative epsilon: collinear triangles
    # then give LARGE-BUT-FINITE radii (a 1e-300 floor overflowed the
    # division to inf, and inf rode into Param_m at Concavity 0)
    span = max(float(np.ptp(pts[:, 0])), float(np.ptp(pts[:, 1])))
    area = np.maximum(area, max(1e-12 * span * span, 1e-300))
    return (la * lb * lc) / (4.0 * area)


def _alpha_precomp(pts):
    """(tri, s, radii) computed once per point set -- the alpha
    functions all accept it, so one Delaunay serves parameter
    selection AND the final shape."""
    tri = _delaunay(pts)
    s = _ccw_simplices(pts, tri)
    return tri, s, _circumradii(pts, s)


def _rings_from_kept(pts, s, kept):
    """Boundary rings of the union of the kept (CCW) triangles,
    walked with the interior on the left -- exterior rings come out
    counter-clockwise, holes clockwise. Pinch vertices are resolved
    by taking the outgoing edge that closes the CURRENT fan (the
    largest CCW rotation from the reversed incoming direction), so
    point-touching components come out as separate simple rings.
    Returns a list of (m, 2) arrays (no closing duplicate)."""
    kept_idx = np.flatnonzero(kept)
    if len(kept_idx) == 0:
        return []
    # directed boundary edges a->b (interior on the left): edge is on
    # the boundary when the flipped edge b->a is not present
    edges = set()
    for t in kept_idx:
        v0, v1, v2 = s[t]
        for a, b in ((v0, v1), (v1, v2), (v2, v0)):
            if (b, a) in edges:
                edges.discard((b, a))
            else:
                edges.add((a, b))
    out_edges = {}
    for a, b in edges:
        out_edges.setdefault(a, []).append(b)
    rings = []
    edges = set(edges)
    while edges:
        a0, b0 = next(iter(edges))
        ring = [a0]
        a, b = a0, b0
        edges.discard((a, b))
        guard = 0
        while b != a0 and guard < 4 * len(pts):
            guard += 1
            ring.append(b)
            outs = [v for v in out_edges.get(b, [])
                    if (b, v) in edges]
            if not outs:
                break
            if len(outs) == 1:
                nxt = outs[0]
            else:
                # pinch vertex: stay in the CURRENT fan -- take the
                # LARGEST CCW rotation from the reversed incoming
                # direction (the minimum would wander into the
                # touching fan and weld the parts into a figure-eight)
                din = math.atan2(pts[b][1] - pts[a][1],
                                 pts[b][0] - pts[a][0])
                best, best_ang = None, None
                for v in outs:
                    dv = math.atan2(pts[v][1] - pts[b][1],
                                    pts[v][0] - pts[b][0])
                    ang = (dv - din - math.pi) % (2 * math.pi)
                    if best is None or ang > best_ang:
                        best, best_ang = v, ang
                nxt = best
            a, b = b, nxt
            edges.discard((a, b))
        if len(ring) >= 3:
            rings.append(pts[np.asarray(ring, dtype=int)])
    return rings


def _ring_signed_area(ring):
    x, y = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    return 0.5 * float(np.sum(x * y2 - x2 * y))


# ---------------------------------------------------------------------------
#  chi shapes (Duckham et al. 2008)
# ---------------------------------------------------------------------------
def chi_shape(pts, length_threshold, events_out=None):
    """The characteristic shape for edge-length threshold ``l``:
    boundary edges longer than l are removed longest-first whenever
    removal keeps the triangulation regular (the vertex opposite the
    edge is interior). Returns (ring, area) where ring is an (m, 2)
    CCW array, or (None, 0) for degenerate input. With
    ``events_out`` (a list), every removal appends
    (edge_length, area_after) -- run with threshold 0 to log the
    full nested family."""
    n = len(pts)
    if n < 3:
        return None, 0.0
    tri = _delaunay(pts)
    s = _ccw_simplices(pts, tri)
    nbr = tri.neighbors
    ntri = len(s)
    areas = _tri_areas(pts, s)
    alive = np.ones(ntri, dtype=bool)
    total_area = float(areas.sum())
    # boundary bookkeeping
    b_count = np.zeros(n, dtype=np.int64)      # boundary edges at vertex
    heap = []

    def edge_verts(t, i):
        # neighbors[t][i] faces tri.simplices[t][i]; map that vertex
        # into our CCW-corrected triangle
        v_op = tri.simplices[t][i]
        vs = [v for v in s[t] if v != v_op]
        return v_op, vs[0], vs[1]

    for t in range(ntri):
        for i in range(3):
            if nbr[t][i] == -1:
                v_op, a, b = edge_verts(t, i)
                b_count[a] += 1
                b_count[b] += 1
                ln = float(np.hypot(*(pts[a] - pts[b])))
                heapq.heappush(heap, (-ln, t, i))
    if not heap:
        return None, 0.0
    area = total_area
    while heap:
        neg_ln, t, i = heapq.heappop(heap)
        ln = -neg_ln
        if ln <= length_threshold:
            break
        if not alive[t]:
            continue
        v_op, a, b = edge_verts(t, i)
        if b_count[v_op] > 0:
            continue                     # opposite vertex on boundary
        # remove triangle t: edge (a, b) leaves the boundary, the two
        # edges at v_op join it
        alive[t] = False
        area -= float(areas[t])
        b_count[a] -= 1
        b_count[b] -= 1
        b_count[v_op] += 2
        b_count[a] += 1
        b_count[b] += 1
        if events_out is not None:
            events_out.append((ln, area))
        for j in range(3):
            nt = nbr[t][j]
            if nt == -1 or not alive[nt]:
                continue
            # the shared edge, seen from nt's side
            for m in range(3):
                if nbr[nt][m] == t:
                    _vo, ea, eb = edge_verts(nt, m)
                    ln2 = float(np.hypot(*(pts[ea] - pts[eb])))
                    heapq.heappush(heap, (-ln2, nt, m))
                    break
    # assemble the boundary ring
    rings = []
    kept = alive
    rings = _rings_from_kept(pts, s, kept)
    if not rings:
        return None, 0.0
    ring = max(rings, key=lambda r: abs(_ring_signed_area(r)))
    if _ring_signed_area(ring) < 0:
        ring = ring[::-1]
    return ring, area


def chi_auto_threshold(pts):
    """Automatic chi length: run the full family (threshold 0),
    then split the removal-edge lengths into gap-scale versus
    density-scale classes with Otsu's threshold on log length. The
    returned threshold keeps the gap-spanning edges removed and the
    body intact."""
    events = []
    chi_shape(pts, 0.0, events_out=events)
    if not events:
        return 0.0
    lens = np.array([e[0] for e in events], dtype=np.float64)
    lens = lens[lens > 0]
    if len(lens) < 4:
        return float(lens.min()) if len(lens) else 0.0
    logs = np.log10(lens)
    lo, hi = logs.min(), logs.max()
    if hi - lo < 1e-9:
        return float(lens.min()) * 0.999
    hist, edges = np.histogram(logs, bins=64)
    p = hist.astype(np.float64) / hist.sum()
    omega = np.cumsum(p)
    mids = 0.5 * (edges[:-1] + edges[1:])
    mu = np.cumsum(p * mids)
    mu_t = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma_b = (mu_t * omega - mu) ** 2 \
            / (omega * (1.0 - omega))
    sigma_b[~np.isfinite(sigma_b)] = 0.0
    k = int(np.argmax(sigma_b))
    return float(10.0 ** mids[k])


# ---------------------------------------------------------------------------
#  alpha shapes (Edelsbrunner et al. 1983)
# ---------------------------------------------------------------------------
def alpha_shape(pts, alpha, precomp=None):
    """The (regularized) alpha shape: the union of Delaunay
    triangles with circumradius <= alpha. Returns (rings, area):
    rings is a list of (m, 2) arrays -- exterior rings CCW, holes CW
    -- and may describe several disjoint parts. ``precomp`` is an
    optional (tri, s, radii) from :func:`_alpha_precomp`."""
    n = len(pts)
    if n < 3:
        return [], 0.0
    _tri, s, radii = precomp if precomp is not None \
        else _alpha_precomp(pts)
    kept = radii <= alpha
    if not kept.any():
        return [], 0.0
    area = float(_tri_areas(pts, s)[kept].sum())
    return _rings_from_kept(pts, s, kept), area


def alpha_spectrum_auto(pts, allow_parts, margin=1.5,
                        precomp=None):
    """The automatic alpha: the smallest circumradius in the
    Delaunay spectrum such that every point is covered by a kept
    triangle and (unless ``allow_parts``) the kept triangles form a
    single connected component -- the 'optimal alpha' rule --
    inflated by a stability ``margin``. The exactly-covering alpha
    is the most extreme valid shape (spiky isthmuses, sliver
    fragments), so the automatic default sits 50% above it;
    Concavity 100 requests it exactly (margin = 1)."""
    n = len(pts)
    tri, s, radii = precomp if precomp is not None \
        else _alpha_precomp(pts)
    spectrum = np.unique(radii)

    def ok(alpha):
        kept = radii <= alpha
        if not kept.any():
            return False
        if len(np.unique(s[kept])) < n:
            return False
        if not allow_parts:
            k_idx = np.flatnonzero(kept)
            pos = {int(t): q for q, t in enumerate(k_idx)}
            parent = list(range(len(k_idx)))

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            for q, t in enumerate(k_idx):
                for nt in tri.neighbors[t]:
                    if nt != -1 and int(nt) in pos:
                        ra, rb = find(q), find(pos[int(nt)])
                        if ra != rb:
                            parent[ra] = rb
            roots = {find(q) for q in range(len(k_idx))}
            if len(roots) > 1:
                return False
        return True

    lo, hi = 0, len(spectrum) - 1
    if not ok(spectrum[hi]):
        return float(spectrum[hi])
    while lo < hi:
        mid = (lo + hi) // 2
        if ok(spectrum[mid]):
            hi = mid
        else:
            lo = mid + 1
    # connectivity is not perfectly monotone in alpha: the bisection
    # can land on the top of a non-monotone pocket, so scan downward
    # while ok() still holds (cheap union-find probes) to reach the
    # true minimum of the contiguous valid run
    while lo > 0 and ok(spectrum[lo - 1]):
        lo -= 1
    a = float(spectrum[lo])
    if margin and margin > 1.0:
        a = min(a * float(margin), float(spectrum[-1]))
    return a


def alpha_from_concavity(pts, concavity, allow_parts,
                         precomp=None):
    """Map a 0-100 concavity onto the alpha spectrum: 0 = the
    maximum circumradius (the convex hull), 100 = the exactly
    optimal (smallest valid) alpha, with no stability margin.
    ``precomp`` is an optional (tri, s, radii) from
    :func:`_alpha_precomp`."""
    if precomp is None:
        precomp = _alpha_precomp(pts)
    _tri, _s, radii = precomp
    a_min = alpha_spectrum_auto(pts, allow_parts, margin=1.0,
                                precomp=precomp)
    a_max = float(radii.max())
    if concavity <= 0:
        return a_max
    if concavity >= 100:
        return a_min
    spectrum = np.unique(radii)
    spectrum = spectrum[(spectrum >= a_min) & (spectrum <= a_max)]
    if len(spectrum) == 0:
        return a_min
    idx = int(round((1.0 - concavity / 100.0)
                    * (len(spectrum) - 1)))
    return float(spectrum[idx])


# ---------------------------------------------------------------------------
#  k-nearest-neighbours hull (Moreira and Santos 2007)
# ---------------------------------------------------------------------------
def _knn_walk(pts, k, kd):
    """One gift-wrapping attempt with a fixed k. Returns the ring
    as an index list, or None."""
    n = len(pts)
    k = min(max(int(k), 3), n - 1)
    start = int(np.lexsort((pts[:, 0], pts[:, 1]))[0])
    hull = [start]
    used = np.zeros(n, dtype=bool)
    used[start] = True
    cur = start
    prev_ang = math.pi                   # incoming from the east
    # placed hull edges as coordinates (row j = hull[j] -> hull[j+1]),
    # grown one row per placed vertex and sliced per candidate
    # instead of rebuilt
    edge_arr = np.empty((n + 1, 4))
    n_edges = 0
    for _step in range(6 * n):
        kk = min(k + 1, n)
        _d, idx = kd.query(pts[cur], k=kk)
        idx = np.atleast_1d(idx)
        cands = []
        for v in idx:
            v = int(v)
            if v == cur:
                continue
            if v == start and len(hull) >= 3:
                cands.append(v)
            elif not used[v]:
                cands.append(v)
        if not cands:
            return None
        # sort by clockwise (right-hand) turn from the previous
        # direction
        def cw_key(v):
            ang = math.atan2(pts[v][1] - pts[cur][1],
                             pts[v][0] - pts[cur][0])
            return (prev_ang - ang) % (2.0 * math.pi)
        cands.sort(key=cw_key)
        placed = False
        for v in cands:
            seg = np.array([[pts[cur][0], pts[cur][1],
                             pts[v][0], pts[v][1]]])
            if len(hull) >= 2:
                # skip the edge sharing the walk's current vertex,
                # and (when closing) the one sharing the start
                e0 = 1 if v == start else 0
                prior = edge_arr[e0:n_edges - 1]
                if len(prior) and _ig._segs_cross(seg, prior)[0]:
                    continue
            if v == start:
                return hull
            hull.append(v)
            used[v] = True
            edge_arr[n_edges] = seg[0]
            n_edges += 1
            prev_ang = math.atan2(pts[cur][1] - pts[v][1],
                                  pts[cur][0] - pts[v][0])
            cur = v
            placed = True
            break
        if not placed:
            return None
    return None


def knn_hull(pts, k_start, messages=None, label=""):
    """The Moreira-Santos hull, settled on the smallest k FOUND at
    or above ``k_start`` whose walk closes around every point: a
    fast exponential probe upward on failure, then a binary search
    back down between the last failure and the first success
    (closure is not perfectly monotone in k, so skipped values are
    not re-proven). (A plain
    multiplicative retry ladder overshoots, making the final k
    depend on the starting point -- a k = 3 start could end LESS
    concave than a larger start.) Falls back to the convex hull at
    the top of the range. Returns (ring, k_used)."""
    from scipy.spatial import cKDTree, ConvexHull

    n = len(pts)
    kd = cKDTree(pts)
    tol = 1e-9 * max(1.0, float(np.abs(pts).max()))

    def attempt(k):
        ring_idx = _knn_walk(pts, k, kd)
        if ring_idx is None or len(ring_idx) < 3:
            return None
        ring = pts[np.asarray(ring_idx, dtype=int)]
        edges = np.hstack([ring, np.roll(ring, -1, axis=0)])
        others = np.ones(n, dtype=bool)
        others[np.asarray(ring_idx, dtype=int)] = False
        test = pts[others]
        if len(test) and not (
                _pa.points_inside(test, edges)
                | _pa.points_near_boundary(test, edges, tol)).all():
            return None
        if _ring_signed_area(ring) < 0:
            ring = ring[::-1]
        return ring

    k0 = min(max(int(k_start), 3), max(3, n - 1))
    ring = attempt(k0)
    if ring is not None:
        return ring, k0
    # exponential probe upward for the first success
    lo = k0                              # highest known failure
    hi = None
    ring_hi = None
    kk = k0
    while True:
        kk = max(kk + 1, int(round(kk * 1.5)))
        if kk > n - 1:
            break
        r = attempt(kk)
        if r is not None:
            hi, ring_hi = kk, r
            break
        if messages is not None:
            _say(messages,
                 "{}k = {} failed to close around every point -- "
                 "probing k = {}.".format(
                     (label + ": ") if label else "", lo, kk))
        lo = kk
    if hi is None:
        hull = ConvexHull(pts)
        ring = pts[hull.vertices]
        if _ring_signed_area(ring) < 0:
            ring = ring[::-1]
        return ring, n - 1
    # binary search for the smallest closing k in (lo, hi]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        r = attempt(mid)
        if r is not None:
            hi, ring_hi = mid, r
        else:
            lo = mid
    if messages is not None and hi != k0:
        # "found": closure is not perfectly monotone in k and the
        # probe can jump values, so this is the smallest k the
        # search visited that closed -- not a proven global minimum
        _say(messages,
             "{}closed at k = {} (the smallest workable k found at "
             "or above {}).".format(
                 (label + ": ") if label else "", hi, k0))
    return ring_hi, hi


def knn_k_from_concavity(n, concavity, k_min=3):
    """Map 0-100 concavity onto k: 100 -> ``k_min`` (the tightest
    workable k -- 3 unless the cloud's own closing floor is higher),
    0 -> n - 1 (the convex hull), exponentially between. Passing the
    cloud's real floor as ``k_min`` stretches the dial across the k
    range that actually exists; with the default 3 this is the plain
    Moreira-Santos mapping."""
    k_min = max(3, int(k_min))
    if n <= 4 or k_min >= n - 1:
        return min(max(3, n - 1), max(k_min, 3))
    if concavity >= 100:
        return k_min
    if concavity <= 0:
        return n - 1
    return int(round(k_min * ((n - 1) / float(k_min))
                     ** ((100.0 - concavity) / 100.0)))


# ---------------------------------------------------------------------------
#  driver
# ---------------------------------------------------------------------------
def run_concave_hull(in_features, method, out_features,
                     case_field=None, concavity=None,
                     alpha_radius=None, chi_length=None,
                     k_neighbors=None, linear_units="Meters",
                     allow_parts=False, messages=None):
    """One concave hull per input group (the whole layer, or one
    hull per value of ``case_field``). Returns the output path."""
    import arcpy

    if method not in METHODS:
        raise arcpy.ExecuteError("Unknown method: {}".format(method))
    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown distance unit: {}".format(linear_units))
    desc = arcpy.Describe(in_features)
    if getattr(desc, "shapeType", "") != "Point":
        raise arcpy.ExecuteError(
            "The input must be point features.")
    out_sr = desc.spatialReference
    if out_sr is None or getattr(out_sr, "name",
                                 "Unknown") == "Unknown":
        raise arcpy.ExecuteError("The input has no coordinate system.")
    geographic = out_sr.type == "Geographic"
    conc = None
    if concavity not in (None, ""):
        conc = float(concavity)
        if not 0.0 <= conc <= 100.0:
            raise arcpy.ExecuteError(
                "Concavity must be between 0 and 100.")

    _mh.begin_phases([
        ("Reading the points...", 1),
        ("Building the hulls...", 6),
        ("Writing the hulls...", 1),
    ])
    is_shp = _ll._is_shp_dest(out_features)
    case_plan = None
    if case_field not in (None, ""):
        case_plan = _cw._transfer_plan(
            in_features, [case_field], is_shp, messages,
            fixed_names={"method", "npoints", "param_m", "k_used",
                         "parts", "holes", "area_m2", "perim_m"})
        if not case_plan:
            case_field = None

    n_total, n_sel, has_sel = _mh.selection_info(in_features,
                                                 str(in_features))
    if has_sel:
        _say(messages, "The input layer has an active selection: {} "
                       "of {} points are used.".format(n_sel,
                                                       n_total))
    groups = {}
    fields = ["SHAPE@X", "SHAPE@Y"] \
        + ([case_field] if case_field else [])
    with arcpy.da.SearchCursor(in_features, fields) as cur:
        for row in cur:
            key = row[2] if case_field else None
            groups.setdefault(key, []).append((row[0], row[1]))
    if not groups:
        raise arcpy.ExecuteError(
            "The input contains no points (empty dataset, or an "
            "active selection selecting nothing).")
    _say(messages, "{:,} point{} in {:,} group{}; method: {}.".format(
        n_sel if has_sel else n_total,
        "" if (n_sel if has_sel else n_total) == 1 else "s",
        len(groups), "" if len(groups) == 1 else "s",
        method.lower()))
    _mh.phase_advance()

    results = []
    keys = sorted(groups.keys(), key=lambda v: (v is None, str(v)))
    for gi, key in enumerate(keys):
        label = "group {}".format(key) if case_field else "the points"
        raw = np.asarray(groups[key], dtype=np.float64)
        raw = np.unique(np.round(raw, 12), axis=0)
        if len(raw) < 3:
            _warn(messages,
                  "{}: only {} distinct point{} -- at least 3 are "
                  "needed; skipped.".format(
                      label, len(raw), "" if len(raw) == 1 else "s"))
            continue
        # working projection: per-group AEQD for geographic inputs.
        # The center longitude is a CIRCULAR mean (atan2 of summed
        # unit vectors), so a group straddling the antimeridian gets
        # a center near +/-180, not a bogus one near 0
        if geographic:
            lam = np.radians(raw[:, 0])
            lon0 = math.degrees(math.atan2(
                float(np.sin(lam).sum()), float(np.cos(lam).sum())))
            cen = (lon0, float(raw[:, 1].mean()))
            wsr, _c = _pa._working_sr(arcpy.PointGeometry(
                arcpy.Point(float(cen[0]), float(cen[1])), out_sr))
            import random_points as _rp
            pts = _rp._project_xy(raw, out_sr, wsr)
            unit_m = 1.0
        else:
            wsr = out_sr
            pts = raw
            try:
                unit_m = float(getattr(out_sr, "metersPerUnit", 1.0)
                               or 1.0)
            except Exception:                                    # noqa: BLE001
                unit_m = 1.0
        span = float(np.hypot(*(pts.max(axis=0) - pts.min(axis=0))))
        if span <= 0:
            _warn(messages, "{}: all points coincide -- skipped."
                  .format(label))
            continue
        # center the coordinates: qhull's lifted-paraboloid Delaunay
        # loses precision at map-scale offsets (UTM false eastings),
        # silently corrupting the triangulation
        c0 = pts.mean(axis=0)
        pts = pts - c0
        _mh.phase_step(gi + 1, len(keys))
        try:
            arcpy.SetProgressorLabel(
                "Building the {} for {} ({:,} points)...".format(
                    method.lower(), label, len(pts)))
        except Exception:                                        # noqa: BLE001
            pass

        rings = []
        param_m = None
        k_used = None
        alpha_pre = None
        a_work = None
        if method == METHOD_CHI:
            if chi_length not in (None, "", 0):
                l_work = float(chi_length) * _LIN_M[linear_units] \
                    / unit_m
            elif conc is not None:
                events = []
                chi_shape(pts, 0.0, events_out=events)
                lens = sorted({e[0] for e in events}, reverse=True)
                if lens:
                    idx = int(round(conc / 100.0
                                    * (len(lens) - 1)))
                    l_work = lens[idx] * (1.0 - 1e-12) if conc > 0 \
                        else lens[0] * 1.001
                else:
                    l_work = 0.0
            else:
                l_work = chi_auto_threshold(pts)
            ring, _area = chi_shape(pts, l_work)
            if ring is not None:
                rings = [ring]
            param_m = l_work * unit_m
        elif method == METHOD_ALPHA:
            alpha_pre = _alpha_precomp(pts)
            if alpha_radius not in (None, "", 0):
                a_work = float(alpha_radius) * _LIN_M[linear_units] \
                    / unit_m
            elif conc is not None:
                a_work = alpha_from_concavity(pts, conc, allow_parts,
                                              precomp=alpha_pre)
            else:
                a_work = alpha_spectrum_auto(pts, allow_parts,
                                             precomp=alpha_pre)
            rings, _area = alpha_shape(pts, a_work,
                                       precomp=alpha_pre)
            param_m = a_work * unit_m
        else:
            ring = None
            if k_neighbors not in (None, "", 0):
                k0 = int(k_neighbors)
            elif conc is not None:
                k0 = knn_k_from_concavity(len(pts), conc)
                if 0.0 < conc < 100.0:
                    # find the cloud's own smallest workable k first,
                    # so the dial spans the k range that actually
                    # exists: separated clusters can push the closing
                    # floor far above 3, and against a fixed 3..n-1
                    # mapping nearly every dial position collapses
                    # onto that floor
                    ring_min, k_min = knn_hull(pts, 3)
                    if k_min > 3:
                        k0 = knn_k_from_concavity(len(pts), conc,
                                                  k_min=k_min)
                        _say(messages,
                             "{}the k-NN walk first closes at "
                             "k = {} on this cloud, so Concavity "
                             "runs from k = {} (at 0) down to "
                             "k = {} (at 100) here; Concavity {:g} "
                             "-> k = {}.".format(
                                 (label + ": ") if label else "",
                                 k_min, max(3, len(pts) - 1),
                                 k_min, conc, k0))
                        if k0 <= k_min:
                            ring, k_used = ring_min, k_min
            else:
                # automatic: a moderate k that grows with the point
                # count (Concavity 100 forces the tightest k = 3)
                k0 = max(3, int(round(math.sqrt(len(pts)))))
            if ring is None:
                ring, k_used = knn_hull(pts, k0, messages=messages,
                                        label=label)
            if ring is not None:
                rings = [ring]
        if not rings:
            r_min = (float(alpha_pre[2].min())
                     if alpha_pre is not None and len(alpha_pre[2])
                     else None)
            if (method == METHOD_ALPHA and a_work is not None
                    and r_min is not None and a_work < r_min):
                # the radius kept no triangles -- that is a parameter
                # problem, not degenerate points
                _warn(messages,
                      "{}: alpha {:g} m is smaller than the "
                      "smallest triangle circumradius ({:g} m) -- "
                      "no triangle qualifies; increase the "
                      "radius. Skipped.".format(
                          label, a_work * unit_m, r_min * unit_m))
            else:
                _warn(messages,
                      "{}: no hull could be built (degenerate or "
                      "collinear points) -- skipped.".format(label))
            continue
        results.append((key, [r + c0 for r in rings], wsr, unit_m,
                        len(raw), param_m, k_used))
    if not results:
        raise arcpy.ExecuteError("No hulls could be built.")
    _mh.phase_advance()

    case_rows = []
    case_cols = []
    if case_field and case_plan:
        src, out_name, ftype, flen = case_plan[0]
        case_rows = [[out_name, ftype,
                      ("Source field " + src) if out_name != src
                      else src, flen, "", ""]]
        case_cols = [out_name]
    out_features = _ll._make_fc(out_features, "POLYGON", out_sr)
    arcpy.management.AddFields(out_features, case_rows + [
        ["Method", "TEXT", "Hull method", 40, "", ""],
        ["NPoints", "LONG", "Distinct input points", "", "", ""],
        ["Param_m", "DOUBLE",
         "Length parameter used (m: alpha radius or chi length)",
         "", "", ""],
        ["K_used", "LONG", "k actually used (k-NN method)",
         "", "", ""],
        ["Parts", "LONG", "Exterior parts", "", "", ""],
        ["Holes", "LONG", "Interior holes", "", "", ""],
        ["Area_m2", "DOUBLE", "Hull area (sq m, geodesic)",
         "", "", ""],
        ["Perim_m", "DOUBLE", "Hull perimeter (m, geodesic)",
         "", "", ""]])

    def _null(v):
        if v is not None:
            return v
        return -999 if is_shp else None

    n_rows = 0
    cols = ["SHAPE@"] + case_cols + [
        "Method", "NPoints", "Param_m", "K_used", "Parts", "Holes",
        "Area_m2", "Perim_m"]
    with arcpy.da.InsertCursor(out_features, cols) as ins:
        for (key, rings, wsr, unit_m, n_pts, param_m,
                k_used) in results:
            arrs = []
            n_parts = n_holes = 0
            for ring in rings:
                sa = _ring_signed_area(ring)
                if sa > 0:
                    n_parts += 1
                    esri = ring[::-1]    # Esri exterior = clockwise
                else:
                    n_holes += 1
                    esri = ring[::-1]    # hole CW -> Esri CCW
                arrs.append(arcpy.Array(
                    [arcpy.Point(float(x), float(y))
                     for x, y in esri]
                    + [arcpy.Point(float(esri[0][0]),
                                   float(esri[0][1]))]))
            shp = _ct.project_geometry(
                arcpy.Polygon(arcpy.Array(arrs), wsr), out_sr)
            if shp is None or shp.area == 0:
                continue
            try:
                area_m2 = abs(shp.getArea("GEODESIC",
                                          "SQUAREMETERS"))
                perim_m = abs(shp.getLength("GEODESIC", "METERS"))
            except Exception:                                    # noqa: BLE001
                area_m2 = abs(shp.area)
                perim_m = abs(shp.length)
            row = [shp]
            if case_cols:
                row.append(key)
            row += [method, n_pts,
                    _null(float(param_m) if param_m is not None
                          else None),
                    _null(int(k_used) if k_used is not None
                          else None),
                    n_parts, n_holes, float(area_m2),
                    float(perim_m)]
            ins.insertRow(tuple(row))
            n_rows += 1
    _say(messages, "{:,} concave hull{} written.".format(
        n_rows, "" if n_rows == 1 else "s"))

    try:
        md = arcpy.metadata.Metadata(out_features)
        md.title = os.path.basename(str(out_features))
        md.summary = (
            "Created by Concave Hull: {} of the input points{}."
            .format(method.lower(),
                    " per {}".format(case_field) if case_field
                    else ""))
        md.description = (
            "From the Jenness Enterprises Concave Hull tool: the "
            "shape of a cloud of points. Methods: chi / "
            "characteristic shapes after Duckham, Kulik, Worboys "
            "and Galton (2008), Pattern Recognition 41:3224-3236, "
            "https://doi.org/10.1016/j.patcog.2008.03.023; alpha "
            "shapes after Edelsbrunner, Kirkpatrick and Seidel "
            "(1983), IEEE Transactions on Information Theory "
            "29:551-559, https://doi.org/10.1109/TIT.1983.1056714; "
            "k-nearest-neighbours hulls after Moreira and Santos "
            "(2007), Proc. GRAPP 2007, 61-68, "
            "https://doi.org/10.5220/0002080800610068. Geographic "
            "inputs use per-group azimuthal-equidistant working "
            "projections; areas and perimeters are geodesic.")
        md.save()
    except Exception:                                            # noqa: BLE001
        pass
    _mh.end_phases()
    return out_features
