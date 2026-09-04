# -*- coding: utf-8 -*-
"""Core GRTS (generalized random-tessellation stratified) sample
selection -- the algorithm of Stevens & Olsen (2004), as implemented
by the spsurvey package (Dumelle et al. 2023) and reviewed against
both papers page by page.

This module is deliberately arcpy-free: it works on plain numpy
coordinate arrays and weight arrays so the mathematics can be
verified headlessly (see claude_tests). The arcpy-facing tool wraps
it with frame readers, the LAEA working projection, and the output
writer.

The construction, in the papers' own terms:

1.  The frame is placed in a square with a RANDOM OFFSET (Stevens &
    Olsen 2004 sec. 2: the offset guarantees any close pair of units
    can fall in different quadrants of some level).
2.  Hierarchical randomization: the square is recursively divided
    into quadrants, and at every node the four children are placed
    in an independently random order -- the composition of the
    digit-interweaving map with random per-node permutations of
    {0,1,2,3}.  The result is a randomized base-4 address whose
    ordering maps two-dimensional space onto a line while tending
    to keep neighbors together.
3.  Every unit is a segment on that line with length equal to its
    inclusion probability (the F-stretch of sec. 2.2); the segments
    sum to the sample size M.
4.  A systematic sample with unit spacing and a random start
    u, u+1, ..., u+M-1 picks the M units.  No two systematic points
    can land in one segment because every segment has length <= 1.
5.  Reverse hierarchical ordering (sec. 2.3, Table 1): the selected
    sites are re-ordered by the base-4 digit REVERSAL of their
    selection order, so that ANY leading run of the final list --
    sites 1..m for any m -- is itself spatially well balanced.
    Replacement sites are consumed from the tail in exactly this
    order.

Infinite (areal/linear) frames use the same line: grid cells stand
in for units, weighted by the measure (area or length) they contain,
subdivided lazily wherever a systematic point lands until the cell
is small, then the point is placed uniformly within the cell's piece
of the frame (the random-tessellation heritage of the name).
"""

import math

import numpy as np

__all__ = ["grts_finite", "grts_areal", "grts_linear",
           "reverse_hierarchical_order", "balance_metrics"]

# depth of the base-4 address used for finite frames; 2^26 cells per
# axis resolves ~1.5 cm on a 1,000 km frame -- far below any real
# coordinate precision
_KEY_DEPTH = 26


# ---------------------------------------------------------------------------
# hierarchical randomization
# ---------------------------------------------------------------------------

def _random_square(pts, rng, pad=None):
    """The randomly offset bounding square of Stevens & Olsen: a
    square of side 2L (L = the frame's larger span) whose origin is
    shifted by a random amount in [0, L) on each axis, so the frame
    always fits yet every pair of points can be split by some grid
    line.  Returns (origin_xy, side)."""
    xmin, ymin = pts[:, 0].min(), pts[:, 1].min()
    xmax, ymax = pts[:, 0].max(), pts[:, 1].max()
    span = max(xmax - xmin, ymax - ymin)
    if pad is not None:
        span = max(span, float(pad))
    span = span if span > 0 else 1.0
    side = 2.0 * span
    ox = xmin - rng.uniform(0.0, span)
    oy = ymin - rng.uniform(0.0, span)
    return (ox, oy), side


def _interleaved_keys(pts, origin, side, rng, depth=_KEY_DEPTH):
    """Randomized hierarchical base-4 addresses for point units.

    The plain address interleaves the x and y binary digits (Stevens
    & Olsen's f0); hierarchical randomization then applies an
    independent random permutation of {0,1,2,3} at every tree node.
    Materializing a permutation per node is infeasible (4^depth
    nodes), but only the nodes actually OCCUPIED by points matter:
    we peel one base-4 digit per level, and within every distinct
    address prefix draw a fresh random permutation to remap that
    level's digits.  Equivalent to the full construction restricted
    to the occupied tree, which is all the ordering can see."""
    n = len(pts)
    fx = np.clip((pts[:, 0] - origin[0]) / side, 0.0, 1.0 - 1e-15)
    fy = np.clip((pts[:, 1] - origin[1]) / side, 0.0, 1.0 - 1e-15)
    ix = (fx * (1 << depth)).astype(np.uint64)
    iy = (fy * (1 << depth)).astype(np.uint64)

    # base-4 digit at each level: 2*ybit + xbit, most significant first
    digits = np.empty((depth, n), dtype=np.uint8)
    for lev in range(depth):
        shift = np.uint64(depth - 1 - lev)
        digits[lev] = (((iy >> shift) & np.uint64(1)) << np.uint8(1)) \
            | ((ix >> shift) & np.uint64(1)).astype(np.uint8)

    # hierarchical randomization: permute digit values independently
    # within every distinct prefix at every level
    keys = np.zeros(n, dtype=np.float64)
    prefix = np.zeros(n, dtype=np.int64)   # group id of the prefix
    scale = 0.25
    for lev in range(depth):
        d = digits[lev].astype(np.int64)
        # one random permutation per prefix group, drawn in a single
        # vectorized call (the per-group Python loop cost ~28 s at
        # 1M points; note this changes the seed->design stream
        # relative to versions before 2026-09-04)
        groups, inv = np.unique(prefix, return_inverse=True)
        perms = rng.permuted(
            np.tile(np.arange(4, dtype=np.int64),
                    (len(groups), 1)), axis=1)
        d_rand = perms[inv, d]
        keys += d_rand * scale
        scale *= 0.25
        prefix = prefix * 4 + d_rand
        # stop early once every unit is alone in its prefix
        if lev >= 2 and len(groups) == n:
            # remaining digits cannot change the ORDER between units,
            # only positions within already-singleton cells
            break
    return keys


def _systematic_pick(order, sizes, M, rng):
    """Systematic sampling with unit spacing along the ordered line
    of segments ``sizes`` (which sum to M): pick the segments hit by
    u, u+1, ..., u+M-1.  Returns the indices of ``order`` hit, in
    line order."""
    csum = np.cumsum(sizes[order])
    u = rng.uniform(0.0, 1.0)
    hits = u + np.arange(M)
    picked = np.searchsorted(csum, hits, side="left")
    picked = np.clip(picked, 0, len(order) - 1)
    if len(np.unique(picked)) != M:
        # numerically impossible when every size <= 1, but guard
        raise RuntimeError("systematic selection produced duplicates")
    return order[picked]


def reverse_hierarchical_order(M):
    """Stevens & Olsen (2004) sec. 2.3 / Table 1: the permutation
    that re-orders the M systematically selected sites so any
    leading run of the result is spatially balanced.  Position i in
    selection order is ranked by the base-4 digit REVERSAL of i.
    Returns an index array: result[k] = selection-order position of
    the site that comes k-th in reverse hierarchical order."""
    if M <= 1:
        return np.arange(M)
    k = max(1, int(math.ceil(math.log(M, 4))))
    idx = np.arange(M, dtype=np.int64)
    rev = np.zeros(M, dtype=np.int64)
    tmp = idx.copy()
    for _ in range(k):
        rev = rev * 4 + (tmp % 4)
        tmp //= 4
    return np.argsort(rev, kind="stable")


# ---------------------------------------------------------------------------
# inclusion probabilities
# ---------------------------------------------------------------------------

def _inclusion_probs(n, weights):
    """Scale positive ``weights`` to inclusion probabilities summing
    to ``n``, peeling off certainty units (prob >= 1 -> exactly 1)
    iteratively, as finite-population PPS sampling requires."""
    w = np.asarray(weights, dtype=np.float64)
    if np.any(w < 0) or not np.all(np.isfinite(w)):
        raise ValueError("weights must be finite and non-negative")
    if (w > 0).sum() < n:
        raise ValueError(
            "sample size exceeds the number of units with positive "
            "weight")
    pi = np.zeros(len(w), dtype=np.float64)
    certain = np.zeros(len(w), dtype=bool)
    while True:
        free = ~certain & (w > 0)
        n_free = n - certain.sum()
        pi[free] = n_free * w[free] / w[free].sum()
        over = free & (pi >= 1.0)
        if not over.any():
            break
        certain |= over
        pi[certain] = 1.0
    return pi


# ---------------------------------------------------------------------------
# finite frames
# ---------------------------------------------------------------------------

def grts_finite(pts, n, rng, weights=None, n_over=0, legacy=None):
    """GRTS sample from a finite point frame.

    pts: (N, 2) array. n: base sample size. weights: optional
    positive per-unit weights for unequal (PPS) inclusion
    probability; None = equal. n_over: replacement sites. legacy:
    optional boolean mask of units that MUST be in the sample
    (Dumelle et al. 2023 sec. 2.2.1: their line lengths are raised
    to one and the rest rescaled; n INCLUDES them).

    Returns dict with 'index' (selected unit indices in reverse
    hierarchical order, legacy first per spsurvey convention),
    'use' ('Legacy'/'Base'/'Over' per site), 'prob' (the DESIGN
    inclusion probabilities, pre-legacy-rescale, for weighting),
    'M' etc."""
    pts = np.asarray(pts, dtype=np.float64)
    N = len(pts)
    M = int(n) + int(n_over)
    if M > N:
        raise ValueError("n + n_over exceeds the frame size")
    w = np.ones(N) if weights is None else np.asarray(
        weights, dtype=np.float64)

    # SELECTION uses inclusion probabilities for the full M
    # (base+over is one GRTS draw of size M, per spsurvey), but the
    # ANALYSIS weights are the BASE design's pi (spsurvey's ip =
    # n/N for equal probability), so 1/pi summed over the base
    # sample estimates the frame total -- the Horvitz-Thompson
    # invariant.  Using the M-draw pi for weights biased HT totals
    # low by n/(n+n_over) (found in review, verified against R).
    pi = _inclusion_probs(M, w)
    pi_base = _inclusion_probs(int(n), w)
    sizes = pi.copy()

    if legacy is not None:
        legacy = np.asarray(legacy, dtype=bool)
        n_l = int(legacy.sum())
        if n_l > int(n):
            raise ValueError("more legacy sites than the base size")
        # raise legacy line lengths to 1; rescale the others so the
        # total stays M (selection only -- 'prob' keeps the design
        # values for analysis weights)
        rest = ~legacy
        denom = M - sizes[legacy].sum()
        scale = (M - n_l) / denom if denom > 0 else 1.0
        sizes[rest] *= scale
        sizes[legacy] = 1.0
        sizes *= M / sizes.sum()   # exact re-normalization

    origin, side = _random_square(pts, rng)
    keys = _interleaved_keys(pts, origin, side, rng)
    order = np.argsort(keys, kind="stable")
    sel = _systematic_pick(order, sizes, M, rng)

    rho = reverse_hierarchical_order(M)
    sel = sel[rho]

    # Legacy sites are certainties of the BASE sample: the base is
    # the legacy sites plus the first (n - n_legacy) NON-legacy
    # sites in reverse hierarchical order, and the tail is purely
    # the ordered replacement list (a legacy site landing late in
    # RHO order must not consume a replacement slot).
    if legacy is not None and legacy.any():
        is_leg = legacy[sel]
    else:
        is_leg = np.zeros(M, dtype=bool)
    n_l = int(is_leg.sum())
    use = np.array(["Over"] * M, dtype=object)
    use[is_leg] = "Legacy"
    free = np.flatnonzero(~is_leg)
    use[free[:int(n) - n_l]] = "Base"
    # write order: Legacy first (spsurvey convention), then Base,
    # then Over -- each group keeping its reverse hierarchical
    # order.  Without this a legacy site landing late in RHO order
    # was written AFTER replacement sites (found in review).
    rank = np.where(use == "Legacy", 0,
                    np.where(use == "Base", 1, 2))
    ro = np.argsort(rank, kind="stable")
    sel, use = sel[ro], use[ro]
    return {"index": sel, "use": use, "prob": pi_base[sel],
            "M": M, "n": int(n), "n_over": int(n_over),
            "origin": origin, "side": side}


# ---------------------------------------------------------------------------
# infinite frames: lazy quadtree over a measure function
# ---------------------------------------------------------------------------

def _clip_polygon_rect(rings, xmin, ymin, xmax, ymax):
    """Sutherland-Hodgman clip of each ring to an axis-aligned
    rectangle; returns the total SIGNED area of the clipped rings
    (outer rings CCW positive, holes CW negative -- the caller's
    ring orientation convention carries the subtraction)."""
    total = 0.0
    for ring in rings:
        poly = ring
        for edge in range(4):
            if len(poly) < 3:
                poly = np.empty((0, 2))
                break
            out = []
            for i in range(len(poly)):
                a = poly[i - 1]
                b = poly[i]
                if edge == 0:
                    ain, bin_ = a[0] >= xmin, b[0] >= xmin
                elif edge == 1:
                    ain, bin_ = a[0] <= xmax, b[0] <= xmax
                elif edge == 2:
                    ain, bin_ = a[1] >= ymin, b[1] >= ymin
                else:
                    ain, bin_ = a[1] <= ymax, b[1] <= ymax
                if bin_:
                    if not ain:
                        out.append(_edge_hit(a, b, edge, xmin, ymin,
                                             xmax, ymax))
                    out.append(b)
                elif ain:
                    out.append(_edge_hit(a, b, edge, xmin, ymin,
                                         xmax, ymax))
            poly = np.asarray(out) if out else np.empty((0, 2))
        if len(poly) >= 3:
            x, y = poly[:, 0], poly[:, 1]
            total += 0.5 * float(
                np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    return total


def _edge_hit(a, b, edge, xmin, ymin, xmax, ymax):
    if edge == 0:
        t = (xmin - a[0]) / (b[0] - a[0])
        return (xmin, a[1] + t * (b[1] - a[1]))
    if edge == 1:
        t = (xmax - a[0]) / (b[0] - a[0])
        return (xmax, a[1] + t * (b[1] - a[1]))
    if edge == 2:
        t = (ymin - a[1]) / (b[1] - a[1])
        return (a[0] + t * (b[0] - a[0]), ymin)
    t = (ymax - a[1]) / (b[1] - a[1])
    return (a[0] + t * (b[0] - a[0]), ymax)


class ArealMeasure(object):
    """Measure = polygon area within a cell.  ``rings``: list of
    (V, 2) arrays, outer rings counterclockwise, holes clockwise
    (signed shoelace carries the holes).  ``intensity(x, y)`` may be
    supplied later for variable-density designs; v1 is uniform."""

    def __init__(self, rings):
        self.rings = [np.asarray(r, dtype=np.float64) for r in rings]
        self._bboxes = [(r[:, 0].min(), r[:, 1].min(),
                         r[:, 0].max(), r[:, 1].max())
                        for r in self.rings]

    def bounds_points(self):
        pts = np.vstack([r for r in self.rings])
        return pts

    def cell_measure(self, xmin, ymin, xmax, ymax):
        rings = []
        for r, bb in zip(self.rings, self._bboxes):
            if bb[0] > xmax or bb[2] < xmin \
                    or bb[1] > ymax or bb[3] < ymin:
                continue
            rings.append(r)
        if not rings:
            return 0.0
        return max(0.0, _clip_polygon_rect(rings, xmin, ymin,
                                           xmax, ymax))

    def point_in(self, x, y):
        # even-odd across all rings (holes flip parity)
        inside = False
        for r, bb in zip(self.rings, self._bboxes):
            if x < bb[0] or x > bb[2] or y < bb[1] or y > bb[3]:
                continue
            xs, ys = r[:, 0], r[:, 1]
            j = len(r) - 1
            for i in range(len(r)):
                if ((ys[i] > y) != (ys[j] > y)) and \
                        (x < (xs[j] - xs[i]) * (y - ys[i])
                         / (ys[j] - ys[i]) + xs[i]):
                    inside = not inside
                j = i
        return inside

    def draw_point(self, xmin, ymin, xmax, ymax, rng, tries=2000):
        for _ in range(tries):
            x = rng.uniform(xmin, xmax)
            y = rng.uniform(ymin, ymax)
            if self.point_in(x, y):
                return x, y
        # rejection failed (frame piece under ~1/2000 of the cell:
        # a sliver).  Probe a fine deterministic grid before falling
        # back -- the bare cell-center fallback could land OUTSIDE
        # the polygon entirely (found in review).
        for g in (16, 48):
            xs = xmin + (xmax - xmin) * (np.arange(g) + 0.5) / g
            ys = ymin + (ymax - ymin) * (np.arange(g) + 0.5) / g
            for y in ys:
                for x in xs:
                    if self.point_in(x, y):
                        return float(x), float(y)
        return 0.5 * (xmin + xmax), 0.5 * (ymin + ymax)


class LinearMeasure(object):
    """Measure = polyline length within a cell.  ``segs``: (S, 4)
    array of segments (x1, y1, x2, y2)."""

    def __init__(self, segs):
        self.segs = np.asarray(segs, dtype=np.float64)
        self._lens = np.hypot(self.segs[:, 2] - self.segs[:, 0],
                              self.segs[:, 3] - self.segs[:, 1])

    def bounds_points(self):
        return np.vstack([self.segs[:, :2], self.segs[:, 2:]])

    def _clip_ts(self, xmin, ymin, xmax, ymax):
        """Liang-Barsky parameter ranges of every segment inside the
        cell; returns (idx, t0, t1) arrays."""
        s = self.segs
        x1, y1 = s[:, 0], s[:, 1]
        dx = s[:, 2] - x1
        dy = s[:, 3] - y1
        t0 = np.zeros(len(s))
        t1 = np.ones(len(s))
        ok = np.ones(len(s), dtype=bool)
        for p, q in ((-dx, x1 - xmin), (dx, xmax - x1),
                     (-dy, y1 - ymin), (dy, ymax - y1)):
            with np.errstate(divide="ignore", invalid="ignore"):
                r = q / p
            par = p == 0
            ok &= ~(par & (q < 0))
            neg = (p < 0) & ok
            pos = (p > 0) & ok
            t0 = np.where(neg, np.maximum(t0, r), t0)
            t1 = np.where(neg, t1, np.where(pos, np.minimum(t1, r),
                                            t1))
            t0 = np.where(pos, t0, t0)
        ok &= t0 <= t1
        idx = np.flatnonzero(ok)
        return idx, t0[idx], t1[idx]

    def cell_measure(self, xmin, ymin, xmax, ymax):
        idx, t0, t1 = self._clip_ts(xmin, ymin, xmax, ymax)
        if not len(idx):
            return 0.0
        return float(((t1 - t0) * self._lens[idx]).sum())

    def draw_point(self, xmin, ymin, xmax, ymax, rng, tries=None):
        idx, t0, t1 = self._clip_ts(xmin, ymin, xmax, ymax)
        lens = (t1 - t0) * self._lens[idx]
        tot = lens.sum()
        if tot <= 0 or not len(idx):
            return 0.5 * (xmin + xmax), 0.5 * (ymin + ymax)
        pick = np.searchsorted(np.cumsum(lens),
                               rng.uniform(0.0, tot))
        pick = min(pick, len(idx) - 1)
        t = rng.uniform(t0[pick], t1[pick])
        s = self.segs[idx[pick]]
        return (s[0] + t * (s[2] - s[0]), s[1] + t * (s[3] - s[1]))


def _grts_infinite(measure, n, rng, n_over=0, min_cell_frac=1e-6,
                   max_depth=30):
    """GRTS over an infinite (areal or linear) frame described by a
    ``measure`` object.  Lazy quadtree: cells are subdivided -- with
    a fresh random child order at every node -- only where a
    systematic point lands, until the cell holds a vanishing share
    of the line; the point is then placed uniformly within the
    cell's piece of the frame.  Uniform intensity (equal
    probability); the F-stretch for variable intensity plugs in at
    ``cell_measure`` if ever needed."""
    M = int(n) + int(n_over)
    pts0 = measure.bounds_points()
    origin, side = _random_square(pts0, rng)
    total = measure.cell_measure(origin[0], origin[1],
                                 origin[0] + side, origin[1] + side)
    if total <= 0:
        raise ValueError("the frame has zero measure")
    unit = total / M          # measure per systematic step
    u = rng.uniform(0.0, 1.0) * unit
    hits = u + np.arange(M) * unit
    out = np.empty((M, 2), dtype=np.float64)

    def descend(x0, y0, sz, m_cell, lo, hit_idx, depth):
        """Place every systematic hit in [lo, lo + m_cell) that
        belongs to this cell."""
        if not len(hit_idx):
            return
        if depth >= max_depth or m_cell <= unit * min_cell_frac \
                or m_cell <= 0:
            for h in hit_idx:
                out[h] = measure.draw_point(x0, y0, x0 + sz, y0 + sz,
                                            rng)
            return
        half = sz * 0.5
        quads = ((x0, y0), (x0 + half, y0),
                 (x0, y0 + half), (x0 + half, y0 + half))
        orderq = rng.permutation(4)
        acc = lo
        for qi in orderq:
            qx, qy = quads[qi]
            m_q = measure.cell_measure(qx, qy, qx + half, qy + half)
            if m_q > 0:
                sel = hit_idx[(hits[hit_idx] >= acc)
                              & (hits[hit_idx] < acc + m_q)]
                descend(qx, qy, half, m_q, acc, sel, depth + 1)
                acc += m_q
        # child measures sum to the parent's only up to float
        # rounding; a hit in the residual sliver [acc, lo + m_cell)
        # would otherwise be left as uninitialized memory (found in
        # review) -- place it within this cell's frame piece
        leftover = hit_idx[hits[hit_idx] >= acc]
        for h in leftover:
            out[h] = measure.draw_point(x0, y0, x0 + sz, y0 + sz,
                                        rng)

    descend(origin[0], origin[1], side, total, 0.0,
            np.arange(M, dtype=np.int64), 0)

    rho = reverse_hierarchical_order(M)
    out = out[rho]
    # Base = the first n sites in REVERSE HIERARCHICAL order (the
    # papers' contract: any leading run is balanced); the tail is
    # the ordered replacement list
    use = np.array(["Base"] * M, dtype=object)
    use[int(n):] = "Over"
    prob = np.full(M, float(n) / total)   # per-unit-measure density
    return {"xy": out, "use": use, "prob_density": prob, "M": M,
            "n": int(n), "n_over": int(n_over), "total_measure":
            float(total), "origin": origin, "side": side}


def grts_areal(rings, n, rng, n_over=0):
    """Equal-probability GRTS point sample from polygons.  ``rings``:
    outer rings counterclockwise, holes clockwise."""
    return _grts_infinite(ArealMeasure(rings), n, rng, n_over)


def grts_linear(segs, n, rng, n_over=0):
    """Equal-probability GRTS point sample along polylines.
    ``segs``: (S, 4) segment array."""
    return _grts_infinite(LinearMeasure(segs), n, rng, n_over)


# ---------------------------------------------------------------------------
# spatial balance
# ---------------------------------------------------------------------------

def balance_metrics(sites, frame_pts, frame_w=None):
    """Voronoi-based spatial balance of ``sites`` against a frame
    represented by (test) points with weights: v_i = total weight
    nearest each site, scaled so sum(v) = n; E[v_i] = 1 under the
    design (Stevens & Olsen 2004 sec. 3).  Returns (var_v, pielou):
    Var(v_i) (their zeta) and the Pielou evenness loss of Dumelle
    et al. (0 = perfectly balanced)."""
    from scipy.spatial import cKDTree

    sites = np.asarray(sites, dtype=np.float64)
    frame_pts = np.asarray(frame_pts, dtype=np.float64)
    n = len(sites)
    w = np.ones(len(frame_pts)) if frame_w is None \
        else np.asarray(frame_w, dtype=np.float64)
    if n < 1 or len(frame_pts) == 0 or w.sum() <= 0:
        return float("nan"), float("nan")
    _, near = cKDTree(sites).query(frame_pts)
    v = np.bincount(near, weights=w, minlength=n)
    v = v * (n / v.sum())
    var_v = float(np.var(v))
    p = v / n
    nz = p > 0
    pielou = float(1.0 + (p[nz] * np.log(p[nz])).sum()
                   / math.log(n)) if n > 1 else 0.0
    return var_v, pielou
