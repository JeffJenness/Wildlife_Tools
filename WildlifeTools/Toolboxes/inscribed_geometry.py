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

"""Maximum Inscribed Geometry -- the largest circle, rectangle, convex
polygon (MCP) or ellipse that fits ENTIRELY INSIDE each polygon.

The deliberate dual of Esri's Minimum Bounding Geometry: where that
tool fits shapes AROUND features, this one fits them INSIDE, with
interior holes acting as barriers. Together with the Longest Line in
Polygon (Fetch) tool this completes the classic "sticks and potatoes"
pair of computational geometry (Hall-Holt et al. 2006).

Algorithms:
- CIRCLE: exact -- the center of the maximum inscribed circle is a
  vertex of the boundary's medial axis (Voronoi diagram of dense
  boundary samples), refined by direct maximization of the true
  clearance (minimum distance to the boundary edges).
- RECTANGLE (free orientation): orientation sweep; at each angle the
  largest axis-aligned rectangle of fully-free grid cells is found
  exactly by the classic histogram dynamic program; the best angle is
  refined on finer grids, and the winner's four sides are pushed
  outward against the true boundary by binary search.
- CONVEX POLYGON (the "potato"): exact continuous potato peeling is
  O(n^7) (Chang and Yap 1986) -- hopeless in practice -- so, in the
  spirit of the mesh-restricted formulation of Aronov, van Kreveld,
  Loeffler and Silveira ("Peeling Meshed Potatoes", Algorithmica
  60:349-367, 2011, DOI 10.1007/s00453-009-9346-8), the problem is
  discretized and solved EXACTLY on the discretization: the
  maximum-area convex polygon whose vertices come from a candidate
  set (interior grid points plus the polygon's own vertices), every
  edge fully interior and no hole swallowed, found by the classic
  rooted dynamic program over angularly sorted candidates with a
  sliding-window maximum over incoming edge directions; the winning
  vertex chain is then polished outward against the true boundary.
  Convex parts short-cut to the part itself (exact).
- ELLIPSE: the conic-linearization method of Hall-Holt, Katz, Kumar,
  Mitchell and Sityon ("Finding large sticks and potatoes in
  polygons", SODA 2006): boundary samples are lifted to R^5, where
  every conic through them becomes a hyperplane; facets of the 5-D
  convex hull yield candidate maximum ellipses through 5 boundary
  samples. Candidates (plus warm starts from the inscribed circle,
  the rectangle's ellipse and the potato's inner Loewner-John
  ellipse) are polished by constrained maximization of the ellipse
  area against the true boundary edges, then shrunk-to-fit so the
  result is ALWAYS truly inside. For convex inputs the John-ellipse
  route is exact.

Geographic (latitude/longitude) polygons are solved in
feature-centered azimuthal-equidistant working projections; output
measures are geodesic meters. Layer selections are honored;
true-curve boundaries are densified automatically; multipart features
report the best shape over their parts.
"""
import math
import os
from collections import deque

import numpy as np

import mahalanobis as _mh
import patch_analysis as _pa
import centerline_width as _cw
import longest_line as _ll
import repeating_shapes as _rs

_say = _cw._say
_warn = _cw._warn
LINEAR_UNITS = _rs.LINEAR_UNITS
_LIN_M = _rs._LIN_M

GEOM_CIRCLE = "Circle"
GEOM_RECT = "Rectangle"
GEOM_MCP = "Convex polygon (MCP)"
GEOM_ELLIPSE = "Ellipse"
GEOM_ALL = "All four"
GEOM_CHOICES = [GEOM_CIRCLE, GEOM_RECT, GEOM_MCP, GEOM_ELLIPSE,
                GEOM_ALL]

_BIG_EXTENT_M = 1.2e6
_TWO_PI = 2.0 * math.pi


# ---------------------------------------------------------------------------
#  pure geometry helpers (numpy / scipy; unit tested without arcpy)
# ---------------------------------------------------------------------------
def _dist_to_edges(points, edges, chunk_bytes=8e7):
    """(N,) minimum distance from each point to the edge set."""
    points = np.atleast_2d(points)
    n = len(points)
    if n == 0:
        return np.zeros(0)
    if len(edges) == 0:
        return np.full(n, np.inf)
    ax, ay = edges[:, 0], edges[:, 1]
    dx, dy = edges[:, 2] - ax, edges[:, 3] - ay
    seg2 = dx * dx + dy * dy
    seg2 = np.where(seg2 <= 0, 1.0, seg2)
    out = np.empty(n)
    step = max(1, int(chunk_bytes / (len(edges) * 8 + 1)))
    for i0 in range(0, n, step):
        px = points[i0:i0 + step, 0][:, None]
        py = points[i0:i0 + step, 1][:, None]
        t = ((px - ax) * dx + (py - ay) * dy) / seg2
        t = np.clip(t, 0.0, 1.0)
        qx = ax + t * dx - px
        qy = ay + t * dy - py
        out[i0:i0 + step] = np.sqrt(
            np.min(qx * qx + qy * qy, axis=1))
    return out


def _segs_cross(segs, edges, chunk_bytes=8e7):
    """(S,) True where a query segment PROPERLY crosses any boundary
    edge (strict interior crossing; shared endpoints, collinear
    overlaps and mere touches do not count)."""
    segs = np.atleast_2d(segs)
    s = len(segs)
    if s == 0 or len(edges) == 0:
        return np.zeros(s, dtype=bool)
    ex1, ey1 = edges[:, 0], edges[:, 1]
    ex2, ey2 = edges[:, 2], edges[:, 3]
    scale = max(1.0, float(np.abs(edges).max()))
    eps = (scale ** 2) * 1e-14
    out = np.zeros(s, dtype=bool)
    step = max(1, int(chunk_bytes / (len(edges) * 8 + 1)))
    for i0 in range(0, s, step):
        ax = segs[i0:i0 + step, 0][:, None]
        ay = segs[i0:i0 + step, 1][:, None]
        bx = segs[i0:i0 + step, 2][:, None]
        by = segs[i0:i0 + step, 3][:, None]
        d1 = (bx - ax) * (ey1 - ay) - (by - ay) * (ex1 - ax)
        d2 = (bx - ax) * (ey2 - ay) - (by - ay) * (ex2 - ax)
        d3 = (ex2 - ex1) * (ay - ey1) - (ey2 - ey1) * (ax - ex1)
        d4 = (ex2 - ex1) * (by - ey1) - (ey2 - ey1) * (bx - ex1)
        cross = (((d1 > eps) & (d2 < -eps))
                 | ((d1 < -eps) & (d2 > eps))) \
            & (((d3 > eps) & (d4 < -eps))
               | ((d3 < -eps) & (d4 > eps)))
        out[i0:i0 + step] = cross.any(axis=1)
    return out


def _sample_boundary(rings, spacing):
    """Points along every ring at roughly ``spacing`` apart (all
    original vertices kept)."""
    pts = []
    for ring in rings:
        nxt = np.roll(ring, -1, axis=0)
        seg = nxt - ring
        lengths = np.hypot(seg[:, 0], seg[:, 1])
        for i in range(len(ring)):
            pts.append(ring[i][None, :])
            n_add = int(lengths[i] // spacing)
            if n_add:
                t = (np.arange(1, n_add + 1)
                     / (n_add + 1.0))[:, None]
                pts.append(ring[i] + t * seg[i])
    return np.vstack(pts)


def _hole_seeds(rings):
    """One interior point per hole ring (Esri convention: exterior
    rings clockwise = negative shoelace area in math axes; holes are
    the positive-area rings)."""
    seeds = []
    for ring in rings:
        if _pa._signed_area(ring) <= 0:      # exterior ring
            continue
        e = _pa.edges_of([ring])
        c = ring.mean(axis=0)
        if _pa.points_inside(c[None, :], e)[0]:
            seeds.append(c)
            continue
        got = False
        n = len(ring)
        for i in range(min(n, 64)):
            tri_c = (ring[i] + ring[(i + 1) % n]
                     + ring[(i + 2) % n]) / 3.0
            if _pa.points_inside(tri_c[None, :], e)[0]:
                seeds.append(tri_c)
                got = True
                break
        if not got:
            # last resort (C-shaped and other strongly nonconvex
            # holes): step a small distance into the hole -- left of
            # each CCW hole edge -- from the edge midpoints and keep
            # the first offset that verifies inside
            nxt = np.roll(ring, -1, axis=0)
            d = nxt - ring
            ln = np.hypot(d[:, 0], d[:, 1])
            keep = ln > 0
            if n > 64:
                pick = np.zeros(n, dtype=bool)
                pick[np.linspace(0, n - 1, 64).astype(int)] = True
                keep &= pick
            if keep.any():
                mids = 0.5 * (ring + nxt)[keep]
                nrm = np.column_stack(
                    [-d[keep, 1], d[keep, 0]]) / ln[keep][:, None]
                diam = float(max(np.ptp(ring[:, 0]),
                                 np.ptp(ring[:, 1]), 1e-300))
                for frac in (1e-7, 1e-5, 1e-3):
                    cand = mids + (diam * frac) * nrm
                    hit = _pa.points_inside(cand, e)
                    if hit.any():
                        seeds.append(cand[int(np.argmax(hit))])
                        got = True
                        break
            # still nothing: SKIP this hole's seed. A missed seed
            # only risks a shape swallowing a degenerate hairline
            # hole; appending a point that FAILED the inside test
            # would plant a phantom hole in free space and suppress
            # valid shapes -- the greater harm.
    if not seeds:
        return np.zeros((0, 2))
    return np.vstack(seeds)


def _points_in_free(points, edges, bands, eps):
    """Inside the polygon (holes excluded); points within ``eps`` of
    the boundary count as inside (shapes may touch the boundary)."""
    inside = _pa.points_inside(points, edges, bands=bands)
    near = _pa.points_near_boundary(points, edges, eps)
    return inside | near


def _seg_interior(segs, edges, bands, eps):
    """(S,) True where each segment lies inside the free space: no
    proper crossing, and several interior sample points all inside
    (or on) the boundary. Multiple samples catch segments that slip
    into the exterior THROUGH a boundary vertex, which the
    proper-crossing test alone cannot see."""
    segs = np.atleast_2d(segs)
    ok = ~_segs_cross(segs, edges)
    for t in (0.11, 0.31, 0.5, 0.69, 0.89):
        live = np.flatnonzero(ok)       # failed rows stop paying
        if len(live) == 0:
            break
        a = segs[live, :2]
        d = segs[live, 2:] - a
        ok[live] = _points_in_free(a + t * d, edges, bands, eps)
    return ok


def _poly_area(chain):
    x, y = chain[:, 0], chain[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    return 0.5 * abs(float(np.sum(x * y2 - x2 * y)))


def _tri_has_seed(o, pa_, pb_, seeds):
    """True when triangle (o, pa_, pb_) contains any seed --
    INCLUSIVE of the triangle boundary, so a seed sitting exactly on
    a fan diagonal still counts (else a chain whose diagonal passes
    through the seed could swallow the hole unnoticed)."""
    if len(seeds) == 0:
        return False
    scale = max(abs(o[0]), abs(o[1]), abs(pa_[0]), abs(pa_[1]),
                abs(pb_[0]), abs(pb_[1]), 1.0)
    tol = (scale ** 2) * 1e-12
    s1 = (pa_[0] - o[0]) * (seeds[:, 1] - o[1]) \
        - (pa_[1] - o[1]) * (seeds[:, 0] - o[0])
    s2 = (pb_[0] - pa_[0]) * (seeds[:, 1] - pa_[1]) \
        - (pb_[1] - pa_[1]) * (seeds[:, 0] - pa_[0])
    s3 = (o[0] - pb_[0]) * (seeds[:, 1] - pb_[1]) \
        - (o[1] - pb_[1]) * (seeds[:, 0] - pb_[0])
    return bool((((s1 >= -tol) & (s2 >= -tol) & (s3 >= -tol))
                 | ((s1 <= tol) & (s2 <= tol)
                    & (s3 <= tol))).any())


def _part_is_convex(rings):
    """True when the part is a single convex ring (no holes)."""
    if len(rings) != 1:
        return False
    ring = rings[0]
    prev = np.roll(ring, 1, axis=0)
    nxt = np.roll(ring, -1, axis=0)
    d1 = ring - prev
    d2 = nxt - ring
    cross = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    tol = 1e-9 * max(1.0, float(np.abs(cross).max()))
    return bool((cross <= tol).all() or (cross >= -tol).all())


# ---------------------------------------------------------------------------
#  kernel: maximum inscribed circle
# ---------------------------------------------------------------------------
def max_inscribed_circle(rings, spacing):
    """(cx, cy, r) of the maximum inscribed circle. The optimal
    center is a medial-axis (Voronoi) vertex of the boundary; dense
    boundary samples put a Voronoi vertex within ~spacing of it, and
    Nelder-Mead refinement of the TRUE clearance (minimum distance to
    the actual edges) finishes the job."""
    from scipy.spatial import Voronoi
    from scipy.optimize import minimize

    edges = _pa.edges_of(rings)
    bands = _pa.y_bands(edges)
    samples = _sample_boundary(rings, spacing)
    if len(samples) < 4:
        return None
    # center before qhull: at map-scale coordinate offsets the
    # lifted-paraboloid arithmetic silently merges/drops Voronoi
    # vertices (dense boundary rings lose ~40% of them)
    mid = samples.mean(axis=0)
    try:
        vor = Voronoi(samples - mid)
    except Exception:                                            # noqa: BLE001
        return None
    cand = vor.vertices + mid
    if len(cand) == 0:
        return None
    inside = _pa.points_inside(cand, edges, bands=bands)
    cand = cand[inside]
    if len(cand) == 0:
        return None
    clear = _dist_to_edges(cand, edges)
    best = int(np.argmax(clear))
    p0, r0 = cand[best].copy(), float(clear[best])

    def negclear(p):
        d = float(_dist_to_edges(p[None, :], edges)[0])
        if not _pa.points_inside(p[None, :], edges,
                                 bands=bands)[0]:
            return d                 # outside: clearance is negative
        return -d

    try:
        res = minimize(negclear, p0, method="Nelder-Mead",
                       options={"xatol": spacing * 1e-3,
                                "fatol": r0 * 1e-9 + 1e-12,
                                "maxiter": 300})
        if -res.fun > r0:
            p0, r0 = res.x, -float(res.fun)
    except Exception:                                            # noqa: BLE001
        pass
    return float(p0[0]), float(p0[1]), r0


# ---------------------------------------------------------------------------
#  kernel: maximum inscribed rectangle
# ---------------------------------------------------------------------------
def _largest_rect_grid(free):
    """Largest all-True axis-aligned rectangle in a boolean grid via
    the histogram-stack dynamic program: (area_cells, r0, c0, r1, c1)
    half-open bounds, or None."""
    nr, nc = free.shape
    heights = np.zeros(nc, dtype=np.int64)
    best = None
    for r in range(nr):
        heights = np.where(free[r], heights + 1, 0)
        stack = []
        for c in range(nc + 1):
            h = int(heights[c]) if c < nc else 0
            start = c
            while stack and stack[-1][1] >= h:
                sc, sh = stack.pop()
                area = sh * (c - sc)
                if best is None or area > best[0]:
                    best = (area, r - sh + 1, sc, r + 1, c)
                start = sc
            if not stack or (stack[-1][1] < h):
                stack.append((start, h))
    return best


def _rect_free_grid(rings, theta, cell, samples=None, notes=None):
    """Boolean grid of cells fully inside the free space at rotation
    ``theta`` (cell centers must be interior with clearance covering
    the half-diagonal). ``samples`` are optional precomputed boundary
    samples in WORLD coordinates (spacing <= cell / 2), shared across
    orientations and rotated here (rotation is rigid, so sampling
    then rotating equals rotating then sampling). When the grid would
    blow past the cell cap, the cell size is CLAMPED UP so the grid
    fits -- a coarser sweep, never a silently skipped orientation --
    and a note is appended once to ``notes``. Returns
    (free, x0, y0, rot, cell_used) in rotated space, where
    p_world = p_rot @ rot."""
    from scipy.spatial import cKDTree

    ct, st = math.cos(-theta), math.sin(-theta)
    rotm = np.array([[ct, -st], [st, ct]])
    rpts = [ring @ rotm.T for ring in rings]
    allp = np.vstack(rpts)
    x0, y0 = allp.min(axis=0)
    x1, y1 = allp.max(axis=0)
    nc = max(2, int(math.ceil((x1 - x0) / cell)))
    nr = max(2, int(math.ceil((y1 - y0) / cell)))
    if nr * nc > 4e6:
        cell = cell * math.sqrt(nr * nc / 4e6)
        nc = max(2, int(math.ceil((x1 - x0) / cell)))
        nr = max(2, int(math.ceil((y1 - y0) / cell)))
        if notes is not None and not notes:
            notes.append(
                "the requested detail would need more than 4 "
                "million rectangle-search grid cells; the search "
                "grid was coarsened to fit (the final expansion "
                "against the true boundary still applies).")
    cxs = x0 + (np.arange(nc) + 0.5) * cell
    cys = y0 + (np.arange(nr) + 0.5) * cell
    gx, gy = np.meshgrid(cxs, cys)
    centers = np.column_stack([gx.ravel(), gy.ravel()])
    r_edges = _pa.edges_of(rpts)
    r_bands = _pa.y_bands(r_edges)
    inside = _pa.points_inside(centers, r_edges, bands=r_bands)
    if samples is None:
        boundary = _sample_boundary(rpts, cell * 0.5)
    else:
        boundary = samples @ rotm.T
    tree = cKDTree(boundary)
    d, _i = tree.query(centers, k=1)
    halfdiag = cell * math.sqrt(0.5)
    # KD distance to samples overestimates true edge distance by at
    # most half the sample spacing -- subtract that margin (samples
    # are spaced <= cell / 2 even when the cell was clamped up, so
    # the margin stays conservative)
    free = (inside & (d - cell * 0.25 >= halfdiag)).reshape(nr, nc)
    # world = rotated @ inv(rotm.T) = rotated @ rotm (rotm orthogonal)
    return free, x0, y0, rotm, cell


def _rect_corners(cx, cy, w, h, theta):
    ct, st = math.cos(theta), math.sin(theta)
    ux, uy = ct, st
    vx, vy = -st, ct
    hw, hh = w / 2.0, h / 2.0
    return np.array(
        [[cx + sx * hw * ux + sy * hh * vx,
          cy + sx * hw * uy + sy * hh * vy]
         for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])


def _rect_ok(cx, cy, w, h, theta, edges, bands, seeds, eps):
    if w <= 0 or h <= 0:
        return False
    corners = _rect_corners(cx, cy, w, h, theta)
    mids = 0.5 * (corners + np.roll(corners, -1, axis=0))
    probes = np.vstack([corners, mids])
    if not _points_in_free(probes, edges, bands, eps).all():
        return False
    sides = np.hstack([corners, np.roll(corners, -1, axis=0)])
    if _segs_cross(sides, edges).any():
        return False
    if len(seeds):
        ct, st = math.cos(theta), math.sin(theta)
        rel = seeds - (cx, cy)
        lu = rel[:, 0] * ct + rel[:, 1] * st
        lv = -rel[:, 0] * st + rel[:, 1] * ct
        if ((np.abs(lu) < w / 2.0) & (np.abs(lv) < h / 2.0)).any():
            return False
    return True


def _expand_rect(cx, cy, w, h, theta, edges, bands, seeds, eps,
                 max_span):
    """Push each rectangle side outward against the true boundary by
    binary search (three sweeps over the four sides)."""
    ct, st = math.cos(theta), math.sin(theta)
    axes = ((ct, st), (-st, ct))         # length axis, width axis
    for _round in range(3):
        for side in range(4):
            on_len = side < 2
            sgn = 1.0 if side % 2 else -1.0
            ax_ = axes[0] if on_len else axes[1]
            lo, hi = 0.0, max_span

            def try_grow(g):
                ncx = cx + sgn * (g / 2.0) * ax_[0]
                ncy = cy + sgn * (g / 2.0) * ax_[1]
                nw = w + g if on_len else w
                nh = h if on_len else h + g
                return _rect_ok(ncx, ncy, nw, nh, theta, edges,
                                bands, seeds, eps)

            if not try_grow(max_span * 1e-9):
                continue
            for _ in range(30):
                mid = 0.5 * (lo + hi)
                if try_grow(mid):
                    lo = mid
                else:
                    hi = mid
            if lo > 0:
                cx += sgn * (lo / 2.0) * ax_[0]
                cy += sgn * (lo / 2.0) * ax_[1]
                if on_len:
                    w += lo
                else:
                    h += lo
    return cx, cy, w, h


def max_inscribed_rect(rings, spacing, fixed_azimuth=None,
                       progress=None, notes=None):
    """(cx, cy, length, width, azimuth_deg): the (approximately)
    largest inscribed rectangle; ``length`` runs along the azimuth.
    Exact on each orientation's grid, then refined on finer grids
    around the winning angle and expanded against the true edges."""
    edges = _pa.edges_of(rings)
    bands = _pa.y_bands(edges)
    seeds = _hole_seeds(rings)
    allp = np.vstack(rings)
    span = float(np.hypot(*(allp.max(axis=0) - allp.min(axis=0))))
    eps = max(spacing * 1e-6, span * 1e-12)

    if fixed_azimuth is not None:
        rounds = [([math.radians(90.0 - float(fixed_azimuth))],
                   spacing),
                  ([math.radians(90.0 - float(fixed_azimuth))],
                   spacing * 0.5)]
    else:
        rounds = [([math.radians(a)
                    for a in np.arange(0.0, 180.0, 6.0)], spacing)]
    best = None                          # (area, cx, cy, w, h, theta)
    ri = 0
    while ri < len(rounds):
        thetas, cell = rounds[ri]
        # boundary samples are rotation-invariant: build them once
        # per cell size and let _rect_free_grid rotate them
        samples = _sample_boundary(rings, cell * 0.5)
        for ti, theta in enumerate(thetas):
            if progress is not None:
                progress("sweeping orientation", ti + 1, len(thetas))
            free, x0, y0, rotm, cella = _rect_free_grid(
                rings, theta, cell, samples=samples, notes=notes)
            if free is None or not free.any():
                continue
            got = _largest_rect_grid(free)
            if got is None or got[0] <= 0:
                continue
            _ac, r0, c0, r1, c1 = got
            w = (c1 - c0) * cella
            h = (r1 - r0) * cella
            rc = np.array([x0 + 0.5 * (c0 + c1) * cella,
                           y0 + 0.5 * (r0 + r1) * cella])
            cxy = rc @ rotm
            area = w * h
            if best is None or area > best[0]:
                best = (area, float(cxy[0]), float(cxy[1]), w, h,
                        theta)
        if best is None:
            return None
        if fixed_azimuth is None and ri < 2:
            th = best[5]
            step = 6.0 if ri == 0 else 1.5
            sub = [th + math.radians(a) for a in
                   np.arange(-step + step / 4.0, step, step / 4.0)]
            rounds.append((sub, cell * 0.5))
        ri += 1
    _area, cx, cy, w, h, theta = best
    cx, cy, w, h = _expand_rect(cx, cy, w, h, theta, edges, bands,
                                seeds, eps, span)
    if w >= h:
        length, width = w, h
        az = (90.0 - math.degrees(theta)) % 180.0
    else:
        length, width = h, w
        az = (-math.degrees(theta)) % 180.0
    return cx, cy, length, width, az


# ---------------------------------------------------------------------------
#  kernel: maximum inscribed convex polygon (the potato)
# ---------------------------------------------------------------------------
def _potato_candidates(rings, spacing, cap):
    """Candidate vertices: the part's own vertices plus an interior
    grid, adaptively thinned to ~cap points."""
    edges = _pa.edges_of(rings)
    bands = _pa.y_bands(edges)
    verts = np.vstack(rings)
    x0, y0 = verts.min(axis=0)
    x1, y1 = verts.max(axis=0)
    span = max(x1 - x0, y1 - y0)
    s = max(spacing, span / 40.0)
    grid = np.zeros((0, 2))
    for _ in range(12):
        xs = np.arange(x0 + s / 2.0, x1, s)
        ys = np.arange(y0 + s / 2.0, y1, s)
        if len(xs) * len(ys) <= cap * 3:
            gx, gy = np.meshgrid(xs, ys)
            grid = np.column_stack([gx.ravel(), gy.ravel()])
            grid = grid[_pa.points_inside(grid, edges, bands=bands)]
            if len(grid) <= cap:
                break
        s *= 1.4
    vkeep = verts
    if len(vkeep) > cap:
        vkeep = verts[np.linspace(0, len(verts) - 1,
                                  cap).astype(int)]
    cand = np.vstack([vkeep, grid])
    return np.unique(np.round(cand, 9), axis=0)


def _potato_dp(cand, F, seeds, progress=None):
    """Exact maximum-area convex polygon on the candidate set: every
    edge must be feasible per ``F`` and no fan triangle may contain a
    hole seed. Rooted DP: the root is the lexicographic-lowest
    vertex of the optimum; candidates above it are processed in
    angular order, and at each vertex a sliding-window maximum over
    the incoming edge directions (the convex-turn condition
    psi_in <= psi_out <= psi_in + pi) yields every outgoing edge's
    best predecessor in amortized constant time. Edges live in an
    append-only registry so parent references never shift.
    Returns (area, [candidate indices of the chain]) or (0, None)."""
    m = len(cand)
    order = np.lexsort((cand[:, 0], cand[:, 1]))
    # the DP consults directions and per-row feasibility O(m^2) times
    # per root: precompute the (m, m) direction matrix
    # DIR[i, j] = angle of cand[j] - cand[i], and F.any once
    diff = cand[None, :, :] - cand[:, None, :]
    DIR = np.arctan2(diff[:, :, 1], diff[:, :, 0]) % _TWO_PI
    F_any = F.any(axis=1)
    best_area = 0.0
    best_chain = None
    for oi in range(m):
        o_idx = int(order[oi])
        if progress is not None:
            progress("anchoring convex search at point", oi + 1, m)
        o = cand[o_idx]
        ids_above = order[oi + 1:]
        feas_above = ids_above[F[o_idx, ids_above]
                               | F_any[ids_above]]
        if len(feas_above) < 2:
            continue
        ang = DIR[o_idx, feas_above]
        srt = np.argsort(ang, kind="stable")
        A = feas_above[srt]              # vertex ids, angular order
        k = len(A)
        pts = cand[A]
        # append-only edge registry
        e_end = []                       # slot (in A) the edge ends at
        e_psi = []
        e_dp = []
        e_par = []                       # parent edge id or -1 (base)
        in_ids = [[] for _ in range(k)]
        close_best = 0.0
        close_edge = -1
        for qc in range(k):
            c_id = int(A[qc])
            pc = pts[qc]
            ids = list(in_ids[qc])
            if F[o_idx, c_id]:
                e_end.append(qc)
                e_psi.append(float(DIR[o_idx, c_id]))
                e_dp.append(0.0)
                e_par.append(-1)
                ids.append(len(e_psi) - 1)
            if not ids:
                continue
            ids.sort(key=lambda e: e_psi[e])
            ipsi = [e_psi[e] for e in ids]
            # outgoing candidates in increasing direction
            tail = A[qc + 1:]
            feas_out = F[c_id, tail]
            if not feas_out.any():
                continue
            qb_arr = np.flatnonzero(feas_out) + qc + 1
            psi_arr = DIR[c_id, tail[feas_out]]
            srt2 = np.argsort(psi_arr, kind="stable")
            outs = list(zip(psi_arr[srt2].tolist(),
                            qb_arr[srt2].tolist()))
            lo_ptr = hi_ptr = 0
            window = deque()             # positions, decreasing dp
            n_in = len(ids)
            for psi, qb in outs:
                while hi_ptr < n_in and ipsi[hi_ptr] <= psi + 1e-15:
                    while window and e_dp[ids[window[-1]]] \
                            <= e_dp[ids[hi_ptr]]:
                        window.pop()
                    window.append(hi_ptr)
                    hi_ptr += 1
                while lo_ptr < hi_ptr \
                        and ipsi[lo_ptr] < psi - math.pi - 1e-15:
                    if window and window[0] == lo_ptr:
                        window.popleft()
                    lo_ptr += 1
                if not window:
                    continue
                par_edge = ids[window[0]]
                pb = pts[qb]
                tri = 0.5 * ((pc[0] - o[0]) * (pb[1] - o[1])
                             - (pc[1] - o[1]) * (pb[0] - o[0]))
                if tri <= 0:
                    continue
                if _tri_has_seed(o, pc, pb, seeds):
                    continue
                total = e_dp[par_edge] + tri
                e_end.append(qb)
                e_psi.append(psi)
                e_dp.append(total)
                e_par.append(par_edge)
                new_id = len(e_psi) - 1
                in_ids[qb].append(new_id)
                if total > close_best and F[o_idx, int(A[qb])]:
                    psi_close = float(DIR[int(A[qb]), o_idx])
                    if (psi_close - psi) % _TWO_PI \
                            <= math.pi + 1e-12:
                        close_best = total
                        close_edge = new_id
        if close_best > best_area and close_edge >= 0:
            chain = []
            e = close_edge
            while e >= 0:
                chain.append(int(A[e_end[e]]))
                e = e_par[e]
            chain.append(o_idx)
            chain.reverse()
            best_area = close_best
            best_chain = chain
    return best_area, best_chain


def _polish_potato(chain_pts, edges, bands, seeds, eps, span):
    """Push each chain vertex outward against the true boundary --
    per vertex, several candidate directions (radial plus the
    compass) are tried and the one with the best area gain wins --
    preserving convexity, edge interior-ness and hole exclusion."""
    chain = chain_pts.copy()
    n = len(chain)
    if n < 3:
        return chain
    diags = math.sqrt(0.5)
    compass = np.array([(1, 0), (diags, diags), (0, 1),
                        (-diags, diags), (-1, 0), (-diags, -diags),
                        (0, -1), (diags, -diags)])

    def vertex_ok(i, p):
        prv = chain[(i - 1) % n]
        nxt = chain[(i + 1) % n]
        nxt2 = chain[(i + 2) % n]
        prv2 = chain[(i - 2) % n]
        for a, b, c in ((prv2, prv, p), (prv, p, nxt),
                        (p, nxt, nxt2)):
            if ((b[0] - a[0]) * (c[1] - b[1])
                    - (b[1] - a[1]) * (c[0] - b[0])) < -abs(eps):
                return False
        segs = np.array([[prv[0], prv[1], p[0], p[1]],
                         [p[0], p[1], nxt[0], nxt[1]]])
        if not _seg_interior(segs, edges, bands, eps).all():
            return False
        if _tri_has_seed(prv, p, nxt, seeds):
            return False
        return True

    def gain(i, p):
        prv = chain[(i - 1) % n]
        nxt = chain[(i + 1) % n]
        old = chain[i]
        return 0.5 * ((prv[0] - nxt[0]) * (p[1] - old[1])
                      - (prv[1] - nxt[1]) * (p[0] - old[0]))

    for _round in range(3):
        centroid = chain.mean(axis=0)
        moved = False
        for i in range(n):
            rad = chain[i] - centroid
            norm = float(np.hypot(*rad))
            dirs = list(compass)
            if norm > 0:
                dirs.append(rad / norm)
            best_p = None
            best_gain = (span * 1e-12) ** 1
            for d in dirs:
                if not vertex_ok(i, chain[i] + d * (span * 1e-9)):
                    continue
                lo, hi = 0.0, span * 0.5
                for _ in range(26):
                    mid = 0.5 * (lo + hi)
                    if vertex_ok(i, chain[i] + d * mid):
                        lo = mid
                    else:
                        hi = mid
                if lo <= 0:
                    continue
                p = chain[i] + d * lo
                g = gain(i, p)
                if g > best_gain:
                    best_gain = g
                    best_p = p
            if best_p is not None:
                chain[i] = best_p
                moved = True
        if not moved:
            break
    return chain


def _snap_chain_inside(chain, edges, bands):
    """Any vertex the polish left epsilon-outside is projected back
    onto the nearest boundary point, so the chain is truly inside."""
    inside = _pa.points_inside(chain, edges, bands=bands)
    for i in np.flatnonzero(~inside):
        p = chain[i]
        ax, ay = edges[:, 0], edges[:, 1]
        dx, dy = edges[:, 2] - ax, edges[:, 3] - ay
        seg2 = dx * dx + dy * dy
        seg2 = np.where(seg2 <= 0, 1.0, seg2)
        t = np.clip(((p[0] - ax) * dx + (p[1] - ay) * dy) / seg2,
                    0.0, 1.0)
        qx = ax + t * dx
        qy = ay + t * dy
        d2 = (qx - p[0]) ** 2 + (qy - p[1]) ** 2
        k = int(np.argmin(d2))
        chain[i] = (qx[k], qy[k])
    return chain


def max_inscribed_potato(rings, spacing, cap=110, progress=None):
    """(M, 2) counter-clockwise convex chain (no closing duplicate)
    of the (approximately) largest inscribed convex polygon."""
    if _part_is_convex(rings):
        ring = rings[0]
        if _pa._signed_area(ring) < 0:
            ring = ring[::-1]
        return ring.copy()

    edges = _pa.edges_of(rings)
    bands = _pa.y_bands(edges)
    seeds = _hole_seeds(rings)
    allp = np.vstack(rings)
    span = float(np.hypot(*(allp.max(axis=0) - allp.min(axis=0))))
    eps = max(spacing * 1e-6, span * 1e-9)
    cand = _potato_candidates(rings, spacing, cap)
    cand = cand[_points_in_free(cand, edges, bands, eps)]
    m = len(cand)
    if m < 3:
        return None
    ii, jj = np.triu_indices(m, k=1)
    segs = np.hstack([cand[ii], cand[jj]])
    feas = _seg_interior(segs, edges, bands, eps)
    F = np.zeros((m, m), dtype=bool)
    F[ii, jj] = feas
    F |= F.T
    area, chain_idx = _potato_dp(cand, F, seeds, progress=progress)
    if chain_idx is None or len(chain_idx) < 3:
        return None
    chain = cand[np.asarray(chain_idx, dtype=int)]
    if _pa._signed_area(chain) < 0:      # ensure CCW in math axes
        chain = chain[::-1]
    chain = _polish_potato(chain, edges, bands, seeds, eps, span)
    chain = _snap_chain_inside(chain, edges, bands)
    # drop collinear runs
    keep = []
    n = len(chain)
    for i in range(n):
        a, b, c = chain[i - 1], chain[i], chain[(i + 1) % n]
        cr = (b[0] - a[0]) * (c[1] - b[1]) \
            - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cr) > (span ** 2) * 1e-14:
            keep.append(i)
    if len(keep) >= 3:
        chain = chain[keep]
    return chain


# ---------------------------------------------------------------------------
#  kernel: maximum inscribed ellipse
# ---------------------------------------------------------------------------
def _ellipse_from_conic(coefs):
    """(cx, cy, a, b, theta) from conic [A, B, C, D, E] with
    A x^2 + 2 B x y + C y^2 + D x + E y + F = 0 for F = +/-1, or
    None if it is not a real ellipse. The 5-point solve fixes F at
    +1, so any ellipse CONTAINING the origin arrives with a
    negative-definite quadratic form (the unit circle solves as
    (-1, 0, -1, 0, 0)); sign-normalize instead of rejecting it --
    those origin-containing conics ARE the inscribed-ellipse
    candidates the R^5 lift exists to produce."""
    A, B, C, D, E = [float(v) for v in coefs]
    F = 1.0
    if A + C < 0:
        A, B, C, D, E, F = -A, -B, -C, -D, -E, -F
    det = A * C - B * B
    if det <= 0 or A <= 0:
        return None
    cx = (-D / 2.0 * C + E / 2.0 * B) / det
    cy = (-E / 2.0 * A + D / 2.0 * B) / det
    val = (A * cx * cx + 2 * B * cx * cy + C * cy * cy
           + D * cx + E * cy + F)
    if val >= 0:
        return None
    kq = -val
    tr = A + C
    disc = math.sqrt(max(0.0, (A - C) ** 2 + 4 * B * B))
    l1 = (tr + disc) / 2.0
    l2 = (tr - disc) / 2.0
    if l2 <= 0:
        return None
    a = math.sqrt(kq / l2)               # semi-major (small eigenvalue)
    b = math.sqrt(kq / l1)
    if abs(B) < 1e-300 and A <= C:
        theta = 0.0
    else:
        theta = 0.5 * math.atan2(2 * B, A - C) + math.pi / 2.0
    # theta = direction of the MAJOR axis (eigenvector of l2)
    return cx, cy, a, b, theta % math.pi


def _ellipse_map_dists(params, edges):
    """Distances from the unit-circle origin to every boundary edge
    after the affine map that takes the ellipse to the unit circle.
    >= 1 everywhere means the ellipse misses every edge."""
    cx, cy, a, b, theta = params
    if a <= 0 or b <= 0 or not (np.isfinite(a) and np.isfinite(b)):
        return np.zeros(len(edges))
    ct, st = math.cos(theta), math.sin(theta)
    p1 = edges[:, :2] - (cx, cy)
    p2 = edges[:, 2:] - (cx, cy)
    u1 = np.column_stack([(p1[:, 0] * ct + p1[:, 1] * st) / a,
                          (-p1[:, 0] * st + p1[:, 1] * ct) / b])
    u2 = np.column_stack([(p2[:, 0] * ct + p2[:, 1] * st) / a,
                          (-p2[:, 0] * st + p2[:, 1] * ct) / b])
    d = u2 - u1
    seg2 = np.einsum("ij,ij->i", d, d)
    seg2 = np.where(seg2 <= 0, 1.0, seg2)
    t = -np.einsum("ij,ij->i", u1, d) / seg2
    t = np.clip(t, 0.0, 1.0)
    q = u1 + t[:, None] * d
    return np.sqrt(np.einsum("ij,ij->i", q, q))


def _ellipse_valid(params, edges, bands, seeds, eps):
    cx, cy, a, b, theta = params
    if a <= 0 or b <= 0:
        return False
    c = np.array([[cx, cy]])
    if not _points_in_free(c, edges, bands, eps)[0]:
        return False
    if _ellipse_map_dists(params, edges).min() < 1.0 - 1e-9:
        return False
    if len(seeds):
        ct, st = math.cos(theta), math.sin(theta)
        rel = seeds - (cx, cy)
        ru = (rel[:, 0] * ct + rel[:, 1] * st) / a
        rv = (-rel[:, 0] * st + rel[:, 1] * ct) / b
        if ((ru * ru + rv * rv) < 1.0).any():
            return False
    return True


def _shrink_to_fit(params, edges):
    cx, cy, a, b, theta = params
    dmin = float(_ellipse_map_dists(params, edges).min())
    if dmin < 1.0:
        f = max(dmin - 1e-12, 1e-12)
        a *= f
        b *= f
    return (cx, cy, a, b, theta)


def _polish_ellipse(params, edges, seeds, span):
    """Constrained maximization of the ellipse area against the true
    boundary edges (SLSQP on center, log-axes and angle)."""
    from scipy.optimize import minimize

    e_sub = edges
    if len(e_sub) > 2500:
        e_sub = e_sub[np.linspace(0, len(e_sub) - 1,
                                  2500).astype(int)]
    cx, cy, a, b, theta = params
    x0 = np.array([cx, cy, math.log(a), math.log(b), theta])

    def unpack(x):
        la = min(max(float(x[2]), -50.0), 50.0)
        lb = min(max(float(x[3]), -50.0), 50.0)
        return (x[0], x[1], math.exp(la), math.exp(lb), x[4])

    def obj(x):
        return -(x[2] + x[3])

    def cons_edges(x):
        return _ellipse_map_dists(unpack(x), e_sub) - 1.0

    cons = [{"type": "ineq", "fun": cons_edges}]
    if len(seeds):
        def cons_seeds(x):
            cx_, cy_, a_, b_, th_ = unpack(x)
            ct, st = math.cos(th_), math.sin(th_)
            rel = seeds - (cx_, cy_)
            ru = (rel[:, 0] * ct + rel[:, 1] * st) / a_
            rv = (-rel[:, 0] * st + rel[:, 1] * ct) / b_
            return ru * ru + rv * rv - 1.0
        cons.append({"type": "ineq", "fun": cons_seeds})
    try:
        res = minimize(obj, x0, method="SLSQP", constraints=cons,
                       options={"maxiter": 120, "ftol": 1e-10})
        if res.success or res.fun < obj(x0):
            return unpack(res.x)
    except Exception:                                            # noqa: BLE001
        pass
    return params


def _john_ellipse(chain):
    """Inner Loewner-John (maximum-area inscribed) ellipse of a
    convex CCW chain: maximize log det(B) subject to
    ||B a_i|| + a_i . d <= b_i. Exact for convex regions."""
    from scipy.optimize import linprog, minimize

    nxt = np.roll(chain, -1, axis=0)
    dxy = nxt - chain
    ln = np.hypot(dxy[:, 0], dxy[:, 1])
    ok = ln > 0
    dxy, ln = dxy[ok], ln[ok]
    v0 = chain[ok]
    # inward normal of a CCW edge is the LEFT normal
    nrm = np.column_stack([-dxy[:, 1], dxy[:, 0]]) / ln[:, None]
    a_out = -nrm                          # a . x <= b form
    b_out = -np.einsum("ij,ij->i", nrm, v0)
    # Chebyshev center for the start
    res = linprog(c=[0, 0, -1],
                  A_ub=np.column_stack([a_out, np.ones(len(a_out))]),
                  b_ub=b_out, bounds=[(None, None)] * 2 + [(0, None)],
                  method="highs")
    if not res.success or res.x[2] <= 0:
        return None
    d0 = res.x[:2]
    r0 = res.x[2]
    x0 = np.array([math.log(r0), 0.0, math.log(r0),
                   d0[0], d0[1]])

    def unpack(x):
        e0 = min(max(float(x[0]), -50.0), 50.0)
        e2 = min(max(float(x[2]), -50.0), 50.0)
        B = np.array([[math.exp(e0), x[1]],
                      [0.0, math.exp(e2)]])
        return B, np.array([x[3], x[4]])

    def obj(x):
        return -(x[0] + x[2])            # -log det B

    def cons(x):
        B, d = unpack(x)
        Ba = a_out @ B
        return b_out - np.sqrt(
            np.einsum("ij,ij->i", Ba, Ba) + 1e-300) - a_out @ d

    try:
        r2 = minimize(obj, x0, method="SLSQP",
                      constraints=[{"type": "ineq", "fun": cons}],
                      options={"maxiter": 200, "ftol": 1e-12})
        B, d = unpack(r2.x)
    except Exception:                                            # noqa: BLE001
        return None
    u, s, vt = np.linalg.svd(B)
    a, b = float(s[0]), float(s[1])
    theta = math.atan2(u[1, 0], u[0, 0]) % math.pi
    return float(d[0]), float(d[1]), a, b, theta


def max_inscribed_ellipse(rings, spacing, circle=None, rect=None,
                          potato=None, progress=None):
    """(cx, cy, a, b, theta): the (approximately) largest inscribed
    ellipse -- conic-linearization candidates through 5 boundary
    samples (Hall-Holt et al. 2006) plus warm starts, polished
    against the true edges and shrunk-to-fit. Guaranteed inside."""
    from scipy.spatial import ConvexHull

    edges = _pa.edges_of(rings)
    bands = _pa.y_bands(edges)
    seeds = _hole_seeds(rings)
    allp = np.vstack(rings)
    lo = allp.min(axis=0)
    hi = allp.max(axis=0)
    span = float(np.hypot(*(hi - lo)))
    eps = max(spacing * 1e-6, span * 1e-12)
    c0 = 0.5 * (lo + hi)
    s0 = max(span / 2.0, 1e-12)

    samples = _sample_boundary(rings, spacing)
    if len(samples) > 1200:
        samples = samples[np.linspace(0, len(samples) - 1,
                                      1200).astype(int)]
    sn = (samples - c0) / s0             # normalized for conditioning

    cands = []
    if len(sn) >= 6:
        lift = np.column_stack([sn[:, 0] ** 2, sn[:, 1] ** 2,
                                sn[:, 0] * sn[:, 1], sn])
        try:
            hull = ConvexHull(lift, qhull_options="QJ")
            facets = hull.simplices
        except Exception:                                        # noqa: BLE001
            facets = np.zeros((0, 5), dtype=int)
        if len(facets):
            if progress is not None:
                progress("screening candidate ellipses", 1, 1)
            P = sn[facets]               # (nf, 5, 2)
            M = np.stack([P[:, :, 0] ** 2,
                          2.0 * P[:, :, 0] * P[:, :, 1],
                          P[:, :, 1] ** 2,
                          P[:, :, 0], P[:, :, 1]], axis=2)
            dets = np.linalg.det(M)
            good = np.abs(dets) > 1e-12
            sols = np.full((len(M), 5), np.nan)
            if good.any():
                sols[good] = np.linalg.solve(
                    M[good],
                    -np.ones((int(good.sum()), 5, 1)))[:, :, 0]
            for coefs in sols[good]:
                e = _ellipse_from_conic(coefs)
                if e is None:
                    continue
                cx, cy, a, b, th = e
                # back to world coordinates
                cands.append((cx * s0 + c0[0], cy * s0 + c0[1],
                              a * s0, b * s0, th))
    # quick screen: keep the biggest few that pass a sample test
    def area_of(p):
        return p[2] * p[3]
    cands.sort(key=area_of, reverse=True)
    screened = []
    for p in cands:
        if len(screened) >= 6:
            break
        q = _shrink_to_fit(p, edges)
        if _ellipse_valid(q, edges, bands, seeds, eps):
            screened.append(q)
    # warm starts
    if circle is not None:
        screened.append((circle[0], circle[1], circle[2] * 0.999,
                         circle[2] * 0.999, 0.0))
    if rect is not None:
        rcx, rcy, rl, rw, raz = rect
        th = math.radians(90.0 - raz)
        screened.append((rcx, rcy, rl / 2.0 * 0.999,
                         rw / 2.0 * 0.999, th % math.pi))
    if potato is not None and len(potato) >= 3:
        je = _john_ellipse(potato)
        if je is not None:
            screened.append(je)
    best = None
    for i, p in enumerate(screened):
        if progress is not None:
            progress("polishing candidate ellipse", i + 1,
                     len(screened))
        q = _polish_ellipse(p, edges, seeds, span)
        q = _shrink_to_fit(q, edges)
        if not _ellipse_valid(q, edges, bands, seeds, eps):
            q = _shrink_to_fit(p, edges)
            if not _ellipse_valid(q, edges, bands, seeds, eps):
                continue
        if best is None or area_of(q) > area_of(best):
            best = q
    if best is None:
        return None
    cx, cy, a, b, th = best
    if b > a:
        a, b = b, a
        th = (th + math.pi / 2.0) % math.pi
    return cx, cy, a, b, th % math.pi


# ---------------------------------------------------------------------------
#  driver
# ---------------------------------------------------------------------------
def _ellipse_ring(cx, cy, a, b, theta, n=96):
    t = np.linspace(0.0, _TWO_PI, n, endpoint=False)
    ct, st = math.cos(theta), math.sin(theta)
    x = cx + a * np.cos(t) * ct - b * np.sin(t) * st
    y = cy + a * np.cos(t) * st + b * np.sin(t) * ct
    return np.column_stack([x, y])


def run_inscribed_geometry(in_features, geometry_type, out_features,
                           transfer_fields=None, detail=None,
                           simplify_tolerance=None,
                           linear_units="Meters",
                           fixed_azimuth=None, messages=None):
    """The largest inscribed shape(s) for every input polygon, as one
    polygon feature class. ``geometry_type`` is one of GEOM_CHOICES;
    "All four" writes one row per shape per feature. Returns the
    output path."""
    import arcpy

    if geometry_type not in GEOM_CHOICES:
        raise arcpy.ExecuteError(
            "Unknown geometry type: {}".format(geometry_type))
    wanted = ([GEOM_CIRCLE, GEOM_RECT, GEOM_MCP, GEOM_ELLIPSE]
              if geometry_type == GEOM_ALL else [geometry_type])
    desc = arcpy.Describe(in_features)
    if getattr(desc, "shapeType", "") != "Polygon":
        raise arcpy.ExecuteError(
            "The input must be a polygon layer or feature class.")
    out_sr = desc.spatialReference
    if out_sr is None or getattr(out_sr, "name",
                                 "Unknown") == "Unknown":
        raise arcpy.ExecuteError("The input has no coordinate system.")
    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown distance unit: {}".format(linear_units))
    detail_m = float(detail) * _LIN_M[linear_units] \
        if detail not in (None, "", 0) else None
    tol_m = float(simplify_tolerance) * _LIN_M[linear_units] \
        if simplify_tolerance not in (None, "", 0) else None
    if tol_m:
        _say(messages,
             "Simplify tolerance {:g} {}: boundaries are simplified "
             "before the search (vertex counts drop and every fit "
             "runs faster); the shapes can shift by roughly the "
             "tolerance.".format(float(simplify_tolerance),
                                 linear_units.lower()))
    if fixed_azimuth not in (None, ""):
        fixed_azimuth = float(fixed_azimuth) % 180.0
    else:
        fixed_azimuth = None

    _mh.begin_phases([
        ("Reading the polygons...", 1),
        ("Fitting the inscribed shapes...", 8),
        ("Writing the shapes...", 1),
    ])
    plan = _cw._transfer_plan(
        in_features, transfer_fields,
        _ll._is_shp_dest(out_features), messages,
        fixed_names={"src_fid", "part", "shape_kind", "area_m2",
                     "centerx", "centery", "radius_m", "length_m",
                     "width_m", "majaxis_m", "minaxis_m", "azimuth",
                     "numverts"})
    fields = [p[0] for p in plan]

    n_total, n_sel, has_sel = _mh.selection_info(in_features,
                                                 str(in_features))
    if has_sel:
        _say(messages, "The input layer has an active selection: {} "
                       "of {} polygons are used.".format(n_sel,
                                                         n_total))
    feats = []
    with arcpy.da.SearchCursor(in_features,
                               ["OID@", "SHAPE@"] + fields) as cur:
        for row in cur:
            if row[1] is not None:
                feats.append((row[0], row[1], row[2:]))
    if not feats:
        raise arcpy.ExecuteError(
            "The input contains no polygons (empty dataset, or an "
            "active selection selecting nothing).")
    _say(messages, "{:,} polygon{}; fitting: {}.".format(
        len(feats), "" if len(feats) == 1 else "s",
        ", ".join(w.lower() for w in wanted)))
    _mh.phase_advance()

    import time as _time
    last_tick = [0.0]

    results = []
    warned_big = False
    rect_notes = []                      # one-shot clamp note
    for fi, (oid, geom, attrs) in enumerate(feats):
        working_sr, is_custom = _pa._working_sr(geom)
        try:
            unit_m = float(getattr(working_sr, "metersPerUnit", 1.0)
                           or 1.0)
        except Exception:                                        # noqa: BLE001
            unit_m = 1.0
        gw = geom.projectAs(working_sr)
        ext = gw.extent
        diag_m = math.hypot(ext.width, ext.height) * unit_m
        if is_custom and diag_m > _BIG_EXTENT_M and not warned_big:
            warned_big = True
            _warn(messages,
                  "Some features span {:,.0f} km -- at this size a "
                  "planar solve in the local equidistant projection "
                  "can err by a fraction of a percent; treat these "
                  "results as close approximations.".format(
                      diag_m / 1000.0))
        if getattr(gw, "hasCurves", False):
            d = max(gw.length / 500.0, 1e-9)
            try:
                gw = gw.densify("DISTANCE", d, d)
            except Exception:                                    # noqa: BLE001
                pass
        if tol_m:
            try:
                g2 = gw.generalize(tol_m / unit_m)
                if g2 is not None and g2.area > 0:
                    g2, g_ok = _pa.ensure_simple(g2, working_sr)
                    if g_ok:
                        gw = g2
            except Exception:                                    # noqa: BLE001
                pass

        def _prog(stage, done, total, _oid=oid):
            now = _time.time()
            if now - last_tick[0] < 0.5:
                return
            last_tick[0] = now
            try:
                arcpy.SetProgressorLabel(
                    "Polygon OID {}: {} {:,} of {:,}...".format(
                        _oid, stage, done, total))
            except Exception:                                    # noqa: BLE001
                pass

        best = {}                        # shape -> (metric, payload)
        for pi, part in enumerate(_pa._explode(gw)):
            rings = _pa.rings_of(part)
            if not rings:
                continue
            allp = np.vstack(rings)
            span = float(np.hypot(
                *(allp.max(axis=0) - allp.min(axis=0))))
            if span <= 0:
                continue
            spacing = (detail_m / unit_m) if detail_m \
                else span / 150.0
            spacing = min(spacing, span / 8.0)
            circ = rect = potato = None
            if GEOM_CIRCLE in wanted or GEOM_ELLIPSE in wanted:
                circ = max_inscribed_circle(rings, spacing)
            if GEOM_RECT in wanted or GEOM_ELLIPSE in wanted:
                rect = max_inscribed_rect(
                    rings, spacing, fixed_azimuth=fixed_azimuth,
                    progress=_prog, notes=rect_notes)
            if GEOM_MCP in wanted or GEOM_ELLIPSE in wanted:
                potato = max_inscribed_potato(rings, spacing,
                                              progress=_prog)
            if GEOM_CIRCLE in wanted and circ is not None:
                if (GEOM_CIRCLE not in best
                        or circ[2] > best[GEOM_CIRCLE][0]):
                    best[GEOM_CIRCLE] = (circ[2],
                                         (pi + 1, circ, working_sr,
                                          unit_m))
            if GEOM_RECT in wanted and rect is not None:
                area = rect[2] * rect[3]
                if (GEOM_RECT not in best
                        or area > best[GEOM_RECT][0]):
                    best[GEOM_RECT] = (area,
                                       (pi + 1, rect, working_sr,
                                        unit_m))
            if GEOM_MCP in wanted and potato is not None:
                area = _poly_area(potato)
                if (GEOM_MCP not in best
                        or area > best[GEOM_MCP][0]):
                    best[GEOM_MCP] = (area,
                                      (pi + 1, potato, working_sr,
                                       unit_m))
            if GEOM_ELLIPSE in wanted:
                ell = max_inscribed_ellipse(
                    rings, spacing, circle=circ, rect=rect,
                    potato=potato, progress=_prog)
                if ell is not None:
                    area = ell[2] * ell[3]
                    if (GEOM_ELLIPSE not in best
                            or area > best[GEOM_ELLIPSE][0]):
                        best[GEOM_ELLIPSE] = (area,
                                              (pi + 1, ell,
                                               working_sr, unit_m))
        if not best:
            _warn(messages,
                  "Polygon OID {}: no inscribed shape could be "
                  "built (degenerate geometry) -- skipped.".format(
                      oid))
            continue
        results.append((oid, attrs, best))
        _mh.phase_step(fi + 1, len(feats))
    if rect_notes:
        _warn(messages, "Rectangle search: " + rect_notes[0])
    if not results:
        raise arcpy.ExecuteError("No inscribed shapes could be built.")
    _mh.phase_advance()

    out_names = [p[1] for p in plan]
    xfer_rows = [[out, ftype,
                  ("Source field " + src) if out != src else src,
                  flen, "", ""] for src, out, ftype, flen in plan]
    out_features = _ll._make_fc(out_features, "POLYGON", out_sr)
    arcpy.management.AddFields(out_features, [
        ["Src_FID", "LONG", "Source polygon Object ID", "", "", ""],
        ["Part", "LONG", "Part holding the shape", "", "", ""],
        ["Shape_Kind", "TEXT", "Inscribed shape kind", 24, "", ""],
        ["Area_m2", "DOUBLE", "Shape area (sq m, geodesic)",
         "", "", ""],
        ["CenterX", "DOUBLE", "Shape center X", "", "", ""],
        ["CenterY", "DOUBLE", "Shape center Y", "", "", ""],
        ["Radius_m", "DOUBLE", "Circle radius (m)", "", "", ""],
        ["Length_m", "DOUBLE", "Rectangle length (m)", "", "", ""],
        ["Width_m", "DOUBLE", "Rectangle width (m)", "", "", ""],
        ["MajAxis_m", "DOUBLE", "Ellipse major axis (m)", "", "", ""],
        ["MinAxis_m", "DOUBLE", "Ellipse minor axis (m)", "", "", ""],
        ["Azimuth", "DOUBLE",
         "Orientation azimuth (degrees, 0-180)", "", "", ""],
        ["NumVerts", "LONG", "Convex polygon vertex count",
         "", "", ""]] + xfer_rows)
    is_shp = _ll._is_shp_dest(str(out_features)) \
        or str(out_features).lower().endswith(".shp")

    def _null(v=None):
        if v is not None:
            return v
        return -999.0 if is_shp else None

    n_rows = 0
    cols = ["SHAPE@", "Src_FID", "Part", "Shape_Kind", "Area_m2",
            "CenterX", "CenterY", "Radius_m", "Length_m", "Width_m",
            "MajAxis_m", "MinAxis_m", "Azimuth",
            "NumVerts"] + out_names
    with arcpy.da.InsertCursor(out_features, cols) as ins:
        for oid, attrs, best in results:
            for kind in wanted:
                if kind not in best:
                    continue
                _metric, (part_n, payload, wsr, unit_m) = best[kind]
                radius = length = width = maj = mnr = az = None
                nverts = None
                if kind == GEOM_CIRCLE:
                    cx, cy, r = payload
                    ring = _ellipse_ring(cx, cy, r, r, 0.0)
                    radius = r * unit_m
                elif kind == GEOM_RECT:
                    cx, cy, ln_, wd_, az_ = payload
                    th = math.radians(90.0 - az_)
                    ring = _rect_corners(cx, cy, ln_, wd_, th)
                    length, width = ln_ * unit_m, wd_ * unit_m
                    az = az_
                elif kind == GEOM_MCP:
                    ring = payload
                    cx, cy = ring.mean(axis=0)
                    nverts = len(ring)
                else:
                    cx, cy, a, b, th = payload
                    ring = _ellipse_ring(cx, cy, a, b, th)
                    maj, mnr = 2 * a * unit_m, 2 * b * unit_m
                    az = (90.0 - math.degrees(th)) % 180.0
                pts = arcpy.Array([arcpy.Point(float(x), float(y))
                                   for x, y in ring] +
                                  [arcpy.Point(float(ring[0][0]),
                                               float(ring[0][1]))])
                shp = arcpy.Polygon(pts, wsr).projectAs(out_sr)
                if shp is None or shp.area == 0:
                    continue
                try:
                    area_m2 = abs(shp.getArea("GEODESIC",
                                              "SQUAREMETERS"))
                except Exception:                                # noqa: BLE001
                    area_m2 = abs(shp.area)
                cpt = arcpy.PointGeometry(
                    arcpy.Point(float(cx), float(cy)),
                    wsr).projectAs(out_sr).firstPoint
                ins.insertRow(tuple(
                    [shp, oid, part_n, kind, float(area_m2),
                     float(cpt.X), float(cpt.Y),
                     _null(radius), _null(length), _null(width),
                     _null(maj), _null(mnr), _null(az),
                     (nverts if nverts is not None
                      else (-999 if is_shp else None))]
                    + list(attrs)))
                n_rows += 1
    _say(messages, "{:,} inscribed shape{} written.".format(
        n_rows, "" if n_rows == 1 else "s"))

    try:
        md = arcpy.metadata.Metadata(out_features)
        md.title = os.path.basename(str(out_features))
        md.summary = (
            "Created by Maximum Inscribed Geometry: the largest {} "
            "inside each input polygon.".format(
                " / ".join(w.lower() for w in wanted)))
        md.description = (
            "From the Jenness Enterprises Maximum Inscribed Geometry "
            "tool -- the dual of Esri's Minimum Bounding Geometry: "
            "the largest circle, rectangle, convex polygon (MCP) or "
            "ellipse fitting entirely inside each polygon, holes "
            "acting as barriers. Methods: exact medial-axis circle; "
            "orientation-swept grid-exact rectangle; discretized "
            "potato peeling after Aronov, van Kreveld, Loeffler and "
            "Silveira (2011), Algorithmica 60:349-367, "
            "https://doi.org/10.1007/s00453-009-9346-8; ellipse by "
            "conic linearization after Hall-Holt, Katz, Kumar, "
            "Mitchell and Sityon (2006), Proc. 17th ACM-SIAM "
            "Symposium on Discrete Algorithms (SODA), 474-483, "
            "https://dl.acm.org/doi/10.5555/1109557.1109610. "
            "Geographic inputs use per-feature azimuthal-equidistant "
            "working projections; measures are geodesic meters.")
        md.save()
    except Exception:                                            # noqa: BLE001
        pass
    _mh.end_phases()
    return out_features
