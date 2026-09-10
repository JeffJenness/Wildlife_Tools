# -*- coding: utf-8 -*-
"""Patch Analysis -- the shortest stepping-stone route through a corridor.

A modernized port of the CorridorDesigner Evaluation Tools' "Patch Analysis"
(``cmdCompare.cls`` / ``CorridorAnalyses.bas`` ``ImplementKruskall``; manual
pp. 18-24). Given a corridor polygon, the termini it connects, and (optionally)
patches of good habitat inside it, the tool finds the route through the
corridor that minimizes the patch-to-patch gaps an animal must cross, and
reports the critical (longest) gap for every pair of termini.

WHY A MINIMUM SPANNING TREE. The path between two nodes through a minimum
spanning tree is the MINIMAX path -- it minimizes the longest single edge
traversed. Ecologically that is exactly the question this tool answers: not
"shortest total travel" but "what is the largest expanse of unsuitable habitat
the species must cross in one push?" The original implemented a variation of
Kruskal's algorithm (O'Rourke 1998, pp. 174-177) that stopped as soon as its
two habitat blocks connected; because the minimax property holds for EVERY
pair of nodes in the tree simultaneously, this version runs Kruskal to the
full spanning forest and reads off the optimal route for every termini pair
in a single pass -- any number of termini, one analysis.

DIFFERENCES FROM THE ORIGINAL, all deliberate:

* VECTOR SHORTEST PATHS, NOT RASTER COST PATHS. The original computed
  around-the-corner distances with Spatial Analyst least-cost paths on a
  rasterized corridor (600 cells on the long edge), constraining every bend
  to 45-degree increments and taking over an hour on real corridors. This
  version computes EXACT Euclidean shortest paths inside the corridor
  polygon with a visibility graph: a shortest path bends only at reflex
  vertices of the corridor boundary, so the graph of mutually-visible
  vertices plus Dijkstra yields the true constrained distance. No Spatial
  Analyst, no raster resolution parameter, no 45-degree bias.
* ANY NUMBER OF TERMINI. The original required exactly two habitat blocks
  (start could only exit, end could only enter). Here every named terminus
  is a terminal node; the spanning forest yields each pair's minimax route.
  Termini in different corridor strands are reported as not connected.
* MULTI-POLYGON TERMINI. Features sharing a name-field value are ONE
  terminus (the original forced a manual merge first).
* A FEATURE CLASS, NOT GRAPHICS. Connector segments are written to a
  polyline feature class carrying the segment lengths table the original
  emitted separately (``segment_lengths.dbf``), with named endpoints.
* GEODESIC LENGTHS (meters), honest on geographic data. Paths are computed
  planarly in a working projected space (the corridor's own CS when
  projected, otherwise a species-scale azimuthal equidistant centered on
  the corridor), and every reported length is geodesic.

The original's other rules are kept: patches are clipped to the corridor and
touching patches merged; termini holes are ignored (outer rings only);
polygon pairs in different corridor strands are never analyzed (the
original's IsInSameStrand check -- rediscovered the hard way on Jeff's
19-strand corridor, where skipping it sent thousands of unreachable pairs
into the visibility graph). The original's third-polygon rejection rule is
now IMPLICIT rather than tested: a connector passing through polygon C is
always at least as long as each of its two hops via C, so Kruskal adds the
hops first and rejects the through connector as a cycle -- no geometry test
needed. Spanning-tree weights are planar working-SR lengths (the working
space is corridor-scale, so ordering matches geodesic ordering); the
REPORTED lengths are geodesic, computed only for the edges that survive
into the tree.

Every output is stamped with a ``patch_analysis`` linkage record so the
Corridor Analysis Data Report can render the full analysis later.
"""
import json
import math
import os

import numpy as np

import mahalanobis as _mh
import land_facet_linkage as _lnk
import tool_metadata as _tm

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org. MST/Kruskal: O'Rourke, J. 1998. "
    "Computational Geometry in C, 2nd Ed. Cambridge University Press.")

# Numerical tolerances (working-SR meters unless noted).
_EPS_TOUCH = 1e-6          # distances below this are "touching"
_EPS_AREA = 1e-9           # degenerate-ring area threshold

# Candidate sight-lines processed per chunk in CorridorGraph.build.
# Benchmarked 2026-08-16 on the black bear 1% corridor (7M candidates,
# 25k-800k sweep): per-chunk overhead is unmeasurable even at 25k, the
# mid-range differences were dominated by ambient machine load, and one
# giant pass was reliably the worst (cache spill). 25k wins on a
# tiebreak that is not noise: progress updates land ~every half second.
# Module-level so it can be re-benchmarked/tuned.
BUILD_CHUNK = 25_000


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


# ---------------------------------------------------------------------------
# pure-numpy planar geometry
# ---------------------------------------------------------------------------
def rings_of(geom):
    """An arcpy polygon (curves already densified) as a list of (n, 2)
    float64 arrays, one per ring, WITHOUT the closing duplicate vertex.
    Esri ring orientation is preserved (exterior clockwise, holes
    counter-clockwise)."""
    rings = []
    for ring in json.loads(geom.JSON).get("rings", []):
        a = np.asarray(ring, dtype=np.float64)[:, :2]
        if len(a) > 1 and np.allclose(a[0], a[-1]):
            a = a[:-1]
        if len(a) >= 3:
            rings.append(a)
    return rings


def _signed_area(ring):
    x, y = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    return 0.5 * float(np.sum(x * y2 - x2 * y))


def reflex_points(rings):
    """The free-space reflex vertices of a corridor polygon's rings -- the
    only places a shortest path inside the polygon can bend.

    With Esri orientation (exterior rings clockwise, holes counter-
    clockwise) the rule collapses to one test for BOTH ring kinds: a vertex
    is reflex with respect to the polygon interior exactly when
    cross(v - prev, next - v) > 0. (Exterior CW: convex turns are right
    turns, cross < 0, so cross > 0 marks interior angles > 180 degrees.
    Hole CCW: vertices convex with respect to the hole poke INTO the free
    space, and those are its left turns, cross > 0.) Rings in the wrong
    orientation are flipped first using the Esri sign convention
    (exterior rings have negative shoelace area in standard math axes)."""
    pts = []
    for ring in rings:
        area = _signed_area(ring)
        if abs(area) < _EPS_AREA:
            continue
        v = ring
        prev = np.roll(v, 1, axis=0)
        nxt = np.roll(v, -1, axis=0)
        d1 = v - prev
        d2 = nxt - v
        cross = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
        keep = cross > 0
        pts.append(v[keep])
    if not pts:
        return np.zeros((0, 2))
    return np.vstack(pts)


def edges_of(rings):
    """All boundary segments of ``rings`` as an (E, 4) array
    [x1, y1, x2, y2]."""
    segs = []
    for ring in rings:
        nxt = np.roll(ring, -1, axis=0)
        segs.append(np.hstack([ring, nxt]))
    if not segs:
        return np.zeros((0, 4))
    return np.vstack(segs)


def y_bands(edges, nbands=None):
    """A Y-band spatial index over ``edges``: ``(bounds, [edge-idx arrays])``
    with every edge listed in each band its y-range overlaps. An edge can
    interact with a horizontal ray (point-in-polygon) or a candidate
    segment ONLY when their y-ranges overlap, so the banded variants of
    the tests below sweep a small fraction of the edge list instead of all
    of it -- the difference between minutes and hours on a real corridor.
    Returns None for degenerate input (callers fall back to unbanded)."""
    if len(edges) < 64:
        return None
    ylo = np.minimum(edges[:, 1], edges[:, 3])
    yhi = np.maximum(edges[:, 1], edges[:, 3])
    y0, y1 = float(ylo.min()), float(yhi.max())
    if not y1 > y0:
        return None
    if nbands is None:
        nbands = int(np.clip(len(edges) // 24, 8, 256))
    bounds = np.linspace(y0, y1, nbands + 1)
    idx = [np.flatnonzero((yhi >= bounds[b]) & (ylo <= bounds[b + 1]))
           for b in range(nbands)]
    return bounds, idx


def _band_of(yvals, bounds):
    return np.clip(np.searchsorted(bounds, yvals) - 1, 0, len(bounds) - 2)


def points_inside(points, edges, chunk_bytes=8e7, bands=None):
    """Even-odd (crossing-number) point-in-polygon over ``edges`` for an
    (N, 2) array of points; holes handled automatically. Chunked to bound
    memory. Points exactly ON an edge are NOT guaranteed either way --
    callers pair this with an on-boundary tolerance test. ``bands`` (from
    ``y_bands``) restricts each point's sweep to the edges sharing its
    y-band; the result is identical."""
    if bands is not None:
        points = np.atleast_2d(points)
        out = np.zeros(len(points), dtype=bool)
        if len(points) == 0 or len(edges) == 0:
            return out
        bounds, idx = bands
        pb = _band_of(points[:, 1], bounds)
        for b in np.unique(pb):
            sel = pb == b
            if len(idx[b]):
                out[sel] = points_inside(points[sel], edges[idx[b]],
                                         chunk_bytes)
        return out
    points = np.atleast_2d(points)
    n = len(points)
    out = np.zeros(n, dtype=bool)
    if n == 0 or len(edges) == 0:
        return out
    x1, y1, x2, y2 = edges[:, 0], edges[:, 1], edges[:, 2], edges[:, 3]
    step = max(1, int(chunk_bytes / (len(edges) * 8 + 1)))
    for i0 in range(0, n, step):
        px = points[i0:i0 + step, 0][:, None]
        py = points[i0:i0 + step, 1][:, None]
        straddle = (y1 > py) != (y2 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xs = (x2 - x1) * (py - y1) / (y2 - y1) + x1
        hits = straddle & (px < xs)
        out[i0:i0 + step] = (hits.sum(axis=1) % 2).astype(bool)
    return out


def points_near_boundary(points, edges, eps, chunk_bytes=8e7):
    """True where a point lies within ``eps`` of any edge (point-to-segment
    distance). Companion to ``points_inside`` for on-boundary robustness."""
    points = np.atleast_2d(points)
    n = len(points)
    out = np.zeros(n, dtype=bool)
    if n == 0 or len(edges) == 0:
        return out
    ax, ay = edges[:, 0], edges[:, 1]
    dx, dy = edges[:, 2] - ax, edges[:, 3] - ay
    seg2 = dx * dx + dy * dy
    seg2 = np.where(seg2 <= 0, 1.0, seg2)
    step = max(1, int(chunk_bytes / (len(edges) * 8 + 1)))
    for i0 in range(0, n, step):
        px = points[i0:i0 + step, 0][:, None]
        py = points[i0:i0 + step, 1][:, None]
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / seg2, 0.0, 1.0)
        qx = ax + t * dx
        qy = ay + t * dy
        d2 = (px - qx) ** 2 + (py - qy) ** 2
        out[i0:i0 + step] = (d2.min(axis=1) <= eps * eps)
    return out


def _segments_properly_cross(p, q, edges, chunk_bytes=8e7, bands=None):
    """For candidate segments p[i]->q[i] ((N, 2) each): True where the
    segment PROPERLY crosses any edge (strict sign changes on both sides).
    Endpoint contact and collinear overlap do NOT count -- candidate
    endpoints are polygon vertices, which legitimately touch edges.
    ``bands`` restricts each segment's sweep to the edges overlapping its
    y-range; the result is identical."""
    p = np.atleast_2d(p)
    q = np.atleast_2d(q)
    n = len(p)
    out = np.zeros(n, dtype=bool)
    if n == 0 or len(edges) == 0:
        return out
    if bands is not None:
        bounds, idx = bands
        b0 = _band_of(np.minimum(p[:, 1], q[:, 1]), bounds)
        b1 = _band_of(np.maximum(p[:, 1], q[:, 1]), bounds)
        for b in range(len(idx)):
            if not len(idx[b]):
                continue
            sel = (b0 <= b) & (b1 >= b) & ~out
            if sel.any():
                sub = _segments_properly_cross(p[sel], q[sel],
                                               edges[idx[b]], chunk_bytes)
                out[np.flatnonzero(sel)[sub]] = True
        return out
    ex1, ey1, ex2, ey2 = edges[:, 0], edges[:, 1], edges[:, 2], edges[:, 3]
    edx, edy = ex2 - ex1, ey2 - ey1
    step = max(1, int(chunk_bytes / (len(edges) * 8 * 4 + 1)))
    for i0 in range(0, n, step):
        px, py = p[i0:i0 + step, 0][:, None], p[i0:i0 + step, 1][:, None]
        qx, qy = q[i0:i0 + step, 0][:, None], q[i0:i0 + step, 1][:, None]
        d1 = edx * (py - ey1) - edy * (px - ex1)
        d2 = edx * (qy - ey1) - edy * (qx - ex1)
        sdx, sdy = qx - px, qy - py
        d3 = sdx * (ey1 - py) - sdy * (ex1 - px)
        d4 = sdx * (ey2 - py) - sdy * (ex2 - px)
        cross = (d1 * d2 < 0) & (d3 * d4 < 0)
        out[i0:i0 + step] = cross.any(axis=1)
    return out


def visible(p, q, edges, eps, bands=None):
    """Visibility of candidate segments p[i]->q[i] inside the polygon whose
    boundary is ``edges``: no proper edge crossing, and three interior
    sample points each inside the polygon (or within ``eps`` of its
    boundary, so wall-hugging segments between adjacent reflex vertices
    survive the ray-cast's on-boundary ambiguity)."""
    p = np.atleast_2d(p)
    q = np.atleast_2d(q)
    ok = ~_segments_properly_cross(p, q, edges, bands=bands)
    if not ok.any():
        return ok
    idx = np.flatnonzero(ok)
    for frac in (0.5, 0.25, 0.75):
        if not len(idx):
            break
        samples = p[idx] + frac * (q[idx] - p[idx])
        good = points_inside(samples, edges, bands=bands)
        bad = ~good
        if bad.any():
            near = points_near_boundary(samples[bad], edges, eps)
            good[np.flatnonzero(bad)[near]] = True
        ok[idx[~good]] = False
        idx = idx[good]
    return ok


def thin_points(rings, step, cap=300):
    """Evenly spaced sample points ALONG the ring boundaries (arc-length
    interpolation, not a vertex subset), for use as visibility-GRAPH
    endpoints. Interpolating means a long straight edge with no
    intermediate vertices still offers departure points every ``step``,
    so polygons need no geometric densification at all -- only true
    curves are densified upstream (the standing gdb-circle rule).

    Tier-1 direct connectors use the full vertex/edge set and exact
    closest points; only constrained (tier-2) path ENDPOINTS are
    quantized to these samples, costing at most ~half the sample spacing
    on an obstructed pair's gap length -- while the candidate-segment
    count, which is quadratic in these points, stays bounded by ``cap``
    per group. (A wildland-block terminus clipped to a real corridor can
    carry thousands of boundary vertices; on Jeff's black bear corridor
    the terminus-terminus candidate block alone was ~16M segments.)"""
    perims = []
    for ring in rings:
        if len(ring) < 3:
            perims.append(0.0)
            continue
        seg = np.hypot(*(np.roll(ring, -1, axis=0) - ring).T)
        perims.append(float(seg.sum()))
    total = sum(perims)
    if total <= 0:
        return np.zeros((0, 2))
    spacing = max(step, total / max(1, cap))
    out = []
    for ring, perim in zip(rings, perims):
        if len(ring) < 3 or perim <= 0:
            continue
        closed = np.vstack([ring, ring[:1]])
        seg = np.hypot(*np.diff(closed, axis=0).T)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        n = max(3, int(perim / spacing))
        targets = np.linspace(0.0, perim, n, endpoint=False)
        idx = np.clip(np.searchsorted(cum, targets, side="right") - 1,
                      0, len(seg) - 1)
        frac = (targets - cum[idx]) / np.where(seg[idx] > 0, seg[idx], 1.0)
        out.append(closed[idx] + frac[:, None] * (closed[idx + 1]
                                                  - closed[idx]))
    if not out:
        return np.zeros((0, 2))
    if sum(len(o) for o in out) > cap:
        # many-ring groups (clipped termini can shatter into dozens of
        # sliver rings) still honor the per-group bound that keeps the
        # graph quadratic in CAP -- but per RING, never below one point:
        # the old uniform-index subsample over the stacked array could
        # delete a sliver ring's only samples, silently disconnecting its
        # group from a strand it genuinely occupies (a falsely
        # "NOT CONNECTED" pair). The cap is a soft bound: one point per
        # ring wins over the exact budget.
        ring_perims = [p for r, p in zip(rings, perims)
                       if len(r) >= 3 and p > 0]
        tot = sum(ring_perims) or 1.0
        extra = max(0, cap - len(out))
        thinned = []
        for o, p in zip(out, ring_perims):
            a = min(len(o), max(1, 1 + int(round(extra * p / tot))))
            thinned.append(o[np.linspace(0, len(o) - 1, a).astype(int)])
        return np.vstack(thinned)
    return np.vstack(out)


def closest_pair(pts_a, edges_a, pts_b, edges_b):
    """The true closest points between two polygon boundaries:
    ``(distance, (xa, ya), (xb, yb))``. The minimum distance between two
    polylines is attained at a vertex of one against a segment of the
    other, so testing both directions is exact."""
    best = (math.inf, None, None)

    def _one_way(pts, edges, pts_first):
        nonlocal best
        if len(pts) == 0 or len(edges) == 0:
            return
        ax, ay = edges[:, 0], edges[:, 1]
        dx, dy = edges[:, 2] - ax, edges[:, 3] - ay
        seg2 = dx * dx + dy * dy
        seg2 = np.where(seg2 <= 0, 1.0, seg2)
        step = max(1, int(8e7 / (len(edges) * 8 + 1)))
        for i0 in range(0, len(pts), step):
            px = pts[i0:i0 + step, 0][:, None]
            py = pts[i0:i0 + step, 1][:, None]
            t = np.clip(((px - ax) * dx + (py - ay) * dy) / seg2, 0.0, 1.0)
            qx = ax + t * dx
            qy = ay + t * dy
            d2 = (px - qx) ** 2 + (py - qy) ** 2
            j = np.argmin(d2, axis=1)
            rows = np.arange(len(j))
            dmin = np.sqrt(d2[rows, j])
            k = int(np.argmin(dmin))
            if dmin[k] < best[0]:
                pt = (float(px[k, 0]), float(py[k, 0]))
                on_edge = (float(qx[k, j[k]]), float(qy[k, j[k]]))
                if pts_first:
                    best = (float(dmin[k]), pt, on_edge)
                else:
                    best = (float(dmin[k]), on_edge, pt)

    _one_way(pts_a, edges_b, True)
    _one_way(pts_b, edges_a, False)
    return best


def _polygons_overlap(edges_a, edges_b):
    """True when two polygon boundaries OVERLAP (cross, or one contains
    the other). ``closest_pair`` is vertex-vs-edge only, which is exact
    for DISJOINT boundaries but reports a POSITIVE distance (up to ~half
    the vertex spacing) for crossing ones -- a patch overlapping a
    terminus block then produced a phantom positive-length "gap crossing"
    where habitat is actually continuous. Cheap bbox prefilter first."""
    if len(edges_a) == 0 or len(edges_b) == 0:
        return False
    ax0 = min(edges_a[:, 0].min(), edges_a[:, 2].min())
    ax1 = max(edges_a[:, 0].max(), edges_a[:, 2].max())
    ay0 = min(edges_a[:, 1].min(), edges_a[:, 3].min())
    ay1 = max(edges_a[:, 1].max(), edges_a[:, 3].max())
    bx0 = min(edges_b[:, 0].min(), edges_b[:, 2].min())
    bx1 = max(edges_b[:, 0].max(), edges_b[:, 2].max())
    by0 = min(edges_b[:, 1].min(), edges_b[:, 3].min())
    by1 = max(edges_b[:, 1].max(), edges_b[:, 3].max())
    if ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0:
        return False
    # boundary crossing: any edge of A properly crosses any edge of B
    if _segments_properly_cross(edges_a[:, :2], edges_a[:, 2:4],
                                edges_b).any():
        return True
    # containment: no crossings, so one vertex decides fully-inside
    if points_inside(edges_a[:1, :2], edges_b)[0]:
        return True
    return bool(points_inside(edges_b[:1, :2], edges_a)[0])


# ---------------------------------------------------------------------------
# the visibility-graph shortest-path engine
# ---------------------------------------------------------------------------
class CorridorGraph(object):
    """Exact Euclidean shortest paths between vertex groups inside a
    corridor polygon.

    ``corridor_rings``: the corridor's rings (numpy arrays, Esri
    orientation). ``groups``: ``{key: (n, 2) array}`` of each group's
    boundary vertices (already densified and clipped to the corridor).
    Distances/paths honor the corridor boundary exactly; group polygons are
    NOT obstacles (a path may pass over one -- dominated edges are culled
    later at the Kruskal stage)."""

    def __init__(self, corridor_rings, groups, messages=None):
        self.edges = edges_of(corridor_rings)
        span = 1.0
        if len(self.edges):
            xs = np.concatenate([self.edges[:, 0], self.edges[:, 2]])
            ys = np.concatenate([self.edges[:, 1], self.edges[:, 3]])
            span = math.hypot(xs.max() - xs.min(), ys.max() - ys.min())
        self.eps = max(span * 1e-9, 1e-9)
        self.bands = y_bands(self.edges)
        self.reflex = reflex_points(corridor_rings)
        self.groups = {}
        self.slices = {}
        coords = [self.reflex] if len(self.reflex) else []
        n0 = len(self.reflex)
        for key, pts in groups.items():
            pts = np.atleast_2d(np.asarray(pts, dtype=np.float64))
            self.groups[key] = pts
            self.slices[key] = (n0, n0 + len(pts))
            coords.append(pts)
            n0 += len(pts)
        self.coords = (np.vstack(coords) if coords
                       else np.zeros((0, 2)))
        self._graph = None
        self._messages = messages

    @staticmethod
    def _chunked_triu(R, chunk):
        """(ii, jj) chunks of the upper-triangle pairs of range(R),
        generated LAZILY -- np.triu_indices materialized all R(R-1)/2
        int64 pairs up front (~20 GB at R = 50k, reachable with
        simplification off), before any chunk was processed."""
        buf_i, buf_j, count = [], [], 0
        for i in range(R - 1):
            js = np.arange(i + 1, R)
            pos = 0
            while pos < len(js):
                take = min(chunk - count, len(js) - pos)
                buf_i.append(np.full(take, i, dtype=np.int64))
                buf_j.append(js[pos:pos + take])
                count += take
                pos += take
                if count >= chunk:
                    yield (np.concatenate(buf_i), np.concatenate(buf_j))
                    buf_i, buf_j, count = [], [], 0
        if count:
            yield (np.concatenate(buf_i), np.concatenate(buf_j))

    @staticmethod
    def _chunked_product(ai, bi, chunk):
        """(ii, jj) chunks of the cartesian product ai x bi, lazily."""
        if not len(ai) or not len(bi):
            return
        per = max(1, chunk // len(bi))
        for a0 in range(0, len(ai), per):
            sub = ai[a0:a0 + per]
            ii = np.repeat(sub, len(bi))
            jj = np.tile(bi, len(sub))
            for c0 in range(0, len(ii), chunk):
                yield ii[c0:c0 + chunk], jj[c0:c0 + chunk]

    def _candidate_total(self):
        """Arithmetic count of candidate pairs (the progress denominator)."""
        R = len(self.reflex)
        total = R * (R - 1) // 2 if R > 1 else 0
        keys = list(self.slices)
        sizes = [self.slices[k][1] - self.slices[k][0] for k in keys]
        total += sum(s * R for s in sizes)
        for x in range(len(sizes)):
            for y in range(x + 1, len(sizes)):
                total += sizes[x] * sizes[y]
        return total

    def _candidate_chunks(self, chunk):
        """Candidate node-index pairs to visibility-test, in chunks:
        reflex-reflex, each group's vertices vs reflex, and group-vs-group
        direct pairs."""
        R = len(self.reflex)
        if R > 1:
            yield from self._chunked_triu(R, chunk)
        keys = list(self.slices)
        rr = np.arange(R)
        for key in keys:
            a0, a1 = self.slices[key]
            if R and a1 > a0:
                yield from self._chunked_product(np.arange(a0, a1), rr,
                                                 chunk)
        for x in range(len(keys)):
            a0, a1 = self.slices[keys[x]]
            for y in range(x + 1, len(keys)):
                b0, b1 = self.slices[keys[y]]
                if a1 > a0 and b1 > b0:
                    yield from self._chunked_product(np.arange(a0, a1),
                                                     np.arange(b0, b1),
                                                     chunk)

    def build(self, progress=None):
        """``progress(candidates_done, candidates_total)`` is called before
        work starts (done=0) and after every processed chunk, so a long
        build can drive a live progress bar instead of looking hung."""
        from scipy.sparse import coo_matrix

        n = len(self.coords)
        rows, cols, data = [], [], []
        total = self._candidate_total()
        done = 0
        if progress:
            progress(0, total)
        chunk = BUILD_CHUNK
        for ii, jj in self._candidate_chunks(chunk):
            n_in_chunk = len(ii)
            p = self.coords[ii]
            q = self.coords[jj]
            length = np.hypot(q[:, 0] - p[:, 0], q[:, 1] - p[:, 1])
            live = length > self.eps
            ii, jj, p, q, length = (ii[live], jj[live], p[live],
                                    q[live], length[live])
            if len(ii):
                # Cheap prefilter before the expensive crossing test: a
                # real visibility edge's midpoint lies inside the
                # polygon. The midpoint is nudged perpendicular to the
                # segment BOTH ways so wall-hugging edges (midpoint
                # exactly on the boundary, where the ray cast is a coin
                # flip) test deterministically: one nudge lands in the
                # interior.
                mid = 0.5 * (p + q)
                direction = (q - p) / length[:, None]
                normal = np.stack([-direction[:, 1], direction[:, 0]],
                                  axis=1)
                off = normal * (self.eps * 100.0 + 1e-9)
                keep = (points_inside(mid + off, self.edges,
                                      bands=self.bands)
                        | points_inside(mid - off, self.edges,
                                        bands=self.bands))
                ii, jj, p, q, length = (ii[keep], jj[keep], p[keep],
                                        q[keep], length[keep])
            if len(ii):
                ok = visible(p, q, self.edges, self.eps,
                             bands=self.bands)
                if ok.any():
                    rows.append(ii[ok])
                    cols.append(jj[ok])
                    data.append(length[ok])
            done += n_in_chunk
            if progress:
                progress(done, total)
        if rows:
            rows = np.concatenate(rows)
            cols = np.concatenate(cols)
            data = np.concatenate(data)
        else:
            rows = cols = np.zeros(0, dtype=int)
            data = np.zeros(0)
        self._graph = coo_matrix((data, (rows, cols)), shape=(n, n)).tocsr()
        return self

    def paths_from(self, key):
        """Multi-source Dijkstra from group ``key``'s vertices. Returns
        ``(dist, pred)`` over all graph nodes (min over the group's
        sources)."""
        from scipy.sparse.csgraph import dijkstra

        if self._graph is None:
            self.build()
        a0, a1 = self.slices[key]
        if a1 <= a0:
            n = len(self.coords)
            return (np.full(n, np.inf),
                    np.full(n, -9999, dtype=np.int32))
        dist, pred, _src = dijkstra(
            self._graph, directed=False, indices=np.arange(a0, a1),
            min_only=True, return_predecessors=True)
        return dist, pred

    def path_to(self, key_b, dist, pred):
        """Given ``paths_from(key_a)`` output, the best path into group
        ``key_b``: ``(planar_length, (k, 2) coords)`` or ``(inf, None)``."""
        b0, b1 = self.slices[key_b]
        if b1 <= b0:
            return math.inf, None
        sub = dist[b0:b1]
        k = int(np.argmin(sub))
        if not np.isfinite(sub[k]):
            return math.inf, None
        node = b0 + k
        chain = [node]
        while pred[chain[-1]] >= 0:
            chain.append(int(pred[chain[-1]]))
        coords = self.coords[np.array(chain[::-1], dtype=int)]
        return float(sub[k]), coords


# ---------------------------------------------------------------------------
# union-find + spanning forest
# ---------------------------------------------------------------------------
class _UnionFind(object):
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return False
        self.parent[ri] = rj
        return True


def spanning_forest(n_nodes, edges):
    """Kruskal over ``edges`` = [(weight, i, j, payload), ...]. Returns the
    forest as ``{frozenset({i, j}): (weight, payload)}``. The minimax path
    between any two connected nodes runs through this forest."""
    forest = {}
    uf = _UnionFind(n_nodes)
    for w, i, j, payload in sorted(edges, key=lambda e: e[0]):
        if uf.union(i, j):
            forest[frozenset((i, j))] = (w, payload)
    return forest


def tree_path(forest, a, b):
    """Node path a..b through the forest (BFS), or None if disconnected."""
    adj = {}
    for pair in forest:
        i, j = tuple(pair)
        adj.setdefault(i, []).append(j)
        adj.setdefault(j, []).append(i)
    if a == b:
        return [a]
    prev = {a: None}
    queue = [a]
    while queue:
        cur = queue.pop(0)
        for nxt in adj.get(cur, []):
            if nxt not in prev:
                prev[nxt] = cur
                if nxt == b:
                    path = [b]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return path[::-1]
                queue.append(nxt)
    return None


# ---------------------------------------------------------------------------
# arcpy-side preparation
# ---------------------------------------------------------------------------
def _working_sr(corridor_geom):
    """The working projected space: the corridor's own CS when projected;
    otherwise a custom azimuthal equidistant centered on the corridor,
    built on the corridor's own geographic CS (same datum, so no
    transformation is ever needed)."""
    import arcpy

    sr = corridor_geom.spatialReference
    if sr is None:
        raise ValueError("The corridor polygon has no spatial reference.")
    if sr.type == "Projected":
        return sr, False
    cen = corridor_geom.centroid
    gcs_wkt = sr.exportToString().split(";")[0]
    wkt = (
        'PROJCS["Corridor_AEQD",{gcs},PROJECTION["Azimuthal_Equidistant"],'
        'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],'
        'PARAMETER["Central_Meridian",{lon:.10f}],'
        'PARAMETER["Latitude_Of_Origin",{lat:.10f}],'
        'UNIT["Meter",1.0]]').format(gcs=gcs_wkt, lon=cen.X, lat=cen.Y)
    out = arcpy.SpatialReference()
    out.loadFromString(wkt)
    return out, True


def _densify(geom, step):
    """Densify TRUE CURVES only (standing rule: gdb circles/arcs otherwise
    collapse to arc endpoints when vertex-walked). Straight-edged
    geometry passes through untouched -- tier-1 closest points are exact
    against edges regardless of vertex density, and tier-2 graph
    endpoints come from ``thin_points``'s arc-length interpolation, so
    nothing else needs added vertices (Jeff, 2026-08-15)."""
    if not getattr(geom, "hasCurves", False):
        return geom
    try:
        return geom.densify("DISTANCE", step, step)
    except Exception:                                            # noqa: BLE001
        return geom


def _outer_rings_only(geom, sr):
    """The polygon rebuilt from exterior rings only (termini holes are
    irrelevant: the tool connects outermost edges, as the original did)."""
    import arcpy

    rings = rings_of(geom)
    keep = [r for r in rings if _signed_area(r) < 0]     # Esri exterior = CW
    if not keep or len(keep) == len(rings):
        return geom
    parts = arcpy.Array()
    for r in keep:
        pts = arcpy.Array([arcpy.Point(x, y) for x, y in r] +
                          [arcpy.Point(r[0][0], r[0][1])])
        parts.add(pts)
    return arcpy.Polygon(parts, sr)


def ensure_simple(poly, sr):
    """``(possibly repaired polygon, is_valid)`` via OGR MakeValid.

    Raster-traced polygons are routinely OGC-invalid (rings passing twice
    through cell-corner touch vertices), and Douglas-Peucker on thin
    shapes can make a ring's sides cross -- either silently corrupts the
    parity-based numpy geometry here. Module-level (2026-08-17) so the
    Bottleneck Analysis engine shares it."""
    import arcpy

    try:
        from osgeo import ogr
        og = ogr.CreateGeometryFromWkt(poly.WKT)
        if og is None:
            return poly, False
        if og.IsValid():
            return poly, True
        fixed = og.MakeValid()
        if fixed is None or fixed.IsEmpty():
            return poly, False
        if ogr.GT_Flatten(fixed.GetGeometryType()) not in (
                ogr.wkbPolygon, ogr.wkbMultiPolygon):
            # MakeValid can hand back a collection with line slivers;
            # keep only the polygonal members
            keep = [fixed.GetGeometryRef(i).Clone()
                    for i in range(fixed.GetGeometryCount())
                    if ogr.GT_Flatten(
                        fixed.GetGeometryRef(i).GetGeometryType())
                    in (ogr.wkbPolygon, ogr.wkbMultiPolygon)]
            if not keep:
                return poly, False
            merged = keep[0]
            for k in keep[1:]:
                merged = merged.Union(k)
            fixed = merged
        cand = arcpy.FromWKT(fixed.ExportToWkt(), sr)
        if cand is not None and cand.area > 0:
            return cand, True
    except Exception:                                            # noqa: BLE001
        pass
    return poly, False


def _geodesic_m(coords, working_sr):
    """Geodesic length in meters of the working-SR polyline ``coords``."""
    import arcpy

    arr = arcpy.Array([arcpy.Point(float(x), float(y)) for x, y in coords])
    pl = arcpy.Polyline(arr, working_sr)
    try:
        val = pl.getLength("GEODESIC", "METERS")
    except Exception:                                            # noqa: BLE001
        val = None
    if not val:
        try:
            unit_m = working_sr.metersPerUnit
        except Exception:                                        # noqa: BLE001
            unit_m = 1.0
        val = pl.length * (unit_m or 1.0)
    return float(val)


def _explode(geom):
    """A (possibly multipart) arcpy polygon as single-part polygons."""
    import arcpy

    sr = geom.spatialReference
    parts = []
    for i in range(geom.partCount):
        part = geom.getPart(i)
        # A "part" from getPart can carry hole rings separated by None --
        # rebuild each part polygon including its holes.
        rings, cur = [], []
        for pt in part:
            if pt is None:
                rings.append(cur)
                cur = []
            else:
                cur.append(pt)
        rings.append(cur)
        arr = arcpy.Array()
        for ring in rings:
            if len(ring) >= 3:
                arr.add(arcpy.Array(ring))
        if arr.count:
            parts.append(arcpy.Polygon(arr, sr))
    # arcpy multipart polygons can also nest disjoint shells in ONE part
    # listing; Dissolve output is well-formed, so partCount is reliable.
    return [p for p in parts if p.area != 0 or p.length > 0]


def _union_all(geoms):
    """Union many geometries with a balanced pairwise merge tree. The old
    sequential chain re-processed the ever-growing accumulator once per
    geometry -- quadratic in accumulated vertices, minutes of GEOS time on
    a many-feature patch map where this takes seconds."""
    if not geoms:
        return None
    work = list(geoms)
    while len(work) > 1:
        merged = []
        for i in range(0, len(work) - 1, 2):
            merged.append(work[i].union(work[i + 1]))
        if len(work) % 2:
            merged.append(work[-1])
        work = merged
    return work[0]


# ---------------------------------------------------------------------------
# the tool
# ---------------------------------------------------------------------------
def run_patch_analysis(corridor, termini, out_fc, name_field=None,
                       patches=None, patch_where=None,
                       simplify_tolerance=None, messages=None):
    """Run the full Patch Analysis. Returns the linkage record dict.

    ``corridor``: polygon layer/FC (selection honored); all features
    dissolve into the one corridor (multipart = multiple strands).
    ``termini``: polygon layer/FC; features grouped into termini by
    ``name_field`` (None -> each feature is its own terminus, labeled by
    OID). ``patches``: optional stepping-stone polygons, filtered by the
    SQL ``patch_where``, clipped to the corridor, touching pieces merged.
    ``simplify_tolerance``: corridor boundary generalization in working-SR
    meters -- None picks an automatic tolerance (0.05% of the corridor
    extent diagonal, enough to strip raster staircase noise without moving
    the boundary at ecological scales), 0 disables.
    """
    import arcpy

    _mh.begin_phases([
        ("Reading and preparing the inputs...", 2),
        ("Direct connectors (closest-point distance matrix)...", 2),
        ("Constrained connectors (visibility graph + Dijkstra)...", 4),
        ("Building the spanning forest and routes...", 1),
        ("Writing the connector feature class...", 1),
    ])

    # ---- resolve catalog paths for the linkage record -------------------
    def _cat(item):
        try:
            return arcpy.Describe(item).catalogPath
        except Exception:                                        # noqa: BLE001
            return str(item) if item is not None else None

    cor_path = _cat(corridor)
    ter_path = _cat(termini)
    pat_path = _cat(patches) if patches else None

    out_fc = str(out_fc or "").rstrip("\\/")
    if not out_fc:
        raise arcpy.ExecuteError("An output feature class is required.")
    out_ws, out_name = os.path.split(out_fc)
    if out_ws and not _mh.is_gdb_workspace(out_ws):
        if not out_name.lower().endswith(".shp"):
            out_name += ".shp"
            out_fc = os.path.join(out_ws, out_name)

    # ---- corridor -------------------------------------------------------
    cor_geoms = [g for (g,) in arcpy.da.SearchCursor(corridor, ["SHAPE@"])
                 if g is not None]
    if not cor_geoms:
        raise arcpy.ExecuteError("The corridor layer has no polygon "
                                 "features (is the selection empty?).")
    cor_union = _union_all(cor_geoms)
    out_sr = cor_union.spatialReference
    working_sr, is_custom = _working_sr(cor_union)
    if is_custom:
        _say(messages, "The corridor is in a geographic coordinate system; "
                       "paths are computed in a corridor-centered azimuthal "
                       "equidistant projection and all lengths reported "
                       "geodesically.")
    cor_w = cor_union.projectAs(working_sr)

    ext = cor_w.extent
    diag = math.hypot(ext.width, ext.height)
    if diag <= 0:
        raise arcpy.ExecuteError("The corridor polygon is degenerate.")
    step = diag / 1000.0

    # Raster-traced corridors touch themselves at cell corners, making
    # rings that pass through a vertex twice -- INVALID geometry that
    # silently corrupts the even-odd and crossing tests everything here
    # rests on (found on Jeff's 1% black bear corridor: a connector left
    # the corridor by 570 m). A tiny positive buffer repairs validity AND
    # keeps corner pinches traversable as narrow throats -- matching the
    # original raster tool, whose 8-connected cost paths crossed diagonal
    # cell touches.
    pinch = min(1.0, max(0.05, diag * 2e-5))
    try:
        buffered = cor_w.buffer(pinch)
        if buffered is not None and buffered.area > 0:
            cor_w = buffered
    except Exception:                                            # noqa: BLE001
        pass

    if getattr(cor_w, "hasCurves", False):
        cor_w = _densify(cor_w, step)

    def _ensure_simple(poly):
        return ensure_simple(poly, working_sr)

    cor_w, ok = _ensure_simple(cor_w)
    if not ok:
        _warn(messages, "The corridor polygon could not be fully repaired "
                        "to simple geometry; results will be checked "
                        "against it at the end of the run.")

    tol = (diag / 2000.0) if simplify_tolerance is None \
        else max(0.0, float(simplify_tolerance))
    if tol > 0:
        try:
            gen = cor_w.generalize(tol)
            if gen is not None and gen.area > 0:
                # Douglas-Peucker on a thin corridor can make its two
                # sides cross; repair, or fall back to the unsimplified
                # boundary rather than analyze corrupt geometry.
                gen, gen_ok = _ensure_simple(gen)
                if gen_ok:
                    cor_w = gen
                else:
                    tol = 0.0
                    _warn(messages,
                          "Boundary simplification produced geometry that "
                          "could not be repaired; analyzing the corridor "
                          "boundary unsimplified instead (slower, exact).")
        except Exception:                                        # noqa: BLE001
            pass

    cor_rings = rings_of(cor_w)
    n_vertices = sum(len(r) for r in cor_rings)
    n_strands = sum(1 for r in cor_rings if _signed_area(r) < 0)
    _say(messages, "Corridor: {} strand{}, {} boundary vertices after "
                   "preparation (simplification tolerance {}).".format(
                       n_strands, "" if n_strands == 1 else "s", n_vertices,
                       "off" if tol <= 0 else "{:.1f} m".format(tol)))
    if n_vertices > 20000:
        _warn(messages, "The corridor boundary still has {} vertices -- the "
                        "visibility analysis may be slow. Consider a larger "
                        "boundary simplification tolerance.".format(n_vertices))

    # ---- termini --------------------------------------------------------
    try:
        term_field_names = {f.name for f in arcpy.ListFields(termini)}
    except Exception:                                            # noqa: BLE001
        term_field_names = set()
    is_termini_fc = {"Category", "CategoryID", "Block"} <= term_field_names

    fields = ["OID@" if not name_field else name_field, "SHAPE@"]
    term_feats = {}
    with arcpy.da.SearchCursor(termini, fields) as cur:
        for val, geom in cur:
            if geom is None:
                continue
            if not name_field:
                label = "Terminus {}".format(val)
            elif isinstance(val, str):
                label = val.strip() or "(blank)"
            elif isinstance(val, (int, float)):
                # a numeric grouping field (e.g. Identify Termini's Block)
                # reads better with the field name in the label
                label = "{} {:g}".format(name_field, val)
            else:
                label = str(val)
            term_feats.setdefault(label, []).append(geom)
    if len(term_feats) < 2:
        hint = ""
        if is_termini_fc and (name_field or "").lower() == "category":
            hint = (" This looks like an Identify Termini Polygons output: "
                    "its Category field names the ANALYSIS SURFACE (the "
                    "same value on every row), not the endpoints -- group "
                    "by 'Block' instead, so each Wildland Block is one "
                    "terminus.")
        raise arcpy.ExecuteError(
            "At least two termini are required ({} found{}). Check the "
            "termini layer, its selection, and the name field.{}".format(
                len(term_feats),
                " grouping by '{}'".format(name_field) if name_field else "",
                hint))

    # An Identify Termini FC can carry SEVERAL categories (one per facet /
    # analysis surface); grouping by Block would merge every category's
    # polygons into each block's terminus, which is rarely intended.
    if is_termini_fc:
        try:
            cats = {row[0] for row in
                    arcpy.da.SearchCursor(termini, ["CategoryID"])}
            if len(cats) > 1:
                _warn(messages,
                      "The termini feature class carries {} categories; "
                      "polygons of ALL categories are merged into each "
                      "terminus. If you meant to analyze one category, "
                      "select or filter it first (e.g. a layer selection "
                      "on CategoryID).".format(len(cats)))
        except Exception:                                        # noqa: BLE001
            pass

    groups = {}          # key -> {"label", "kind", "geom"(working, clipped)}
    unplaced_termini = []
    for label in sorted(term_feats):
        merged = _union_all(term_feats[label]).projectAs(working_sr)
        merged = _outer_rings_only(merged, working_sr)
        merged = _densify(merged, step)
        clipped = merged.intersect(cor_w, 4)
        if clipped is None or clipped.area <= 0:
            unplaced_termini.append(label)
            continue
        key = "T:{}".format(label)
        groups[key] = {"label": label, "kind": "terminus", "geom": clipped}
    for label in unplaced_termini:
        _warn(messages, "Terminus '{}' does not overlap the corridor "
                        "polygon -- it cannot be connected and is left out "
                        "of the analysis.".format(label))
    if sum(1 for g in groups.values() if g["kind"] == "terminus") < 2:
        raise arcpy.ExecuteError(
            "Fewer than two termini overlap the corridor polygon -- "
            "nothing to connect.")

    # ---- patches --------------------------------------------------------
    n_patch_feats = 0
    n_patch_pieces = 0
    if patches:
        # Cheap extent prefilter BEFORE any per-feature projection/densify/
        # union work: a whole-study-area patch map can carry thousands of
        # features with only a handful anywhere near this corridor, and the
        # exact clip further down already guarantees correctness -- this
        # just keeps the preparation cost proportional to the relevant few.
        pat_geoms = []
        n_far = 0
        cor_ext = None
        with arcpy.da.SearchCursor(patches, ["SHAPE@"],
                                   where_clause=patch_where or None) as cur:
            for (geom,) in cur:
                if geom is None:
                    continue
                if cor_ext is None:
                    try:
                        psr = geom.spatialReference
                        same = (psr is None or out_sr is None
                                or (psr.factoryCode == out_sr.factoryCode
                                    and psr.name == out_sr.name))
                        cor_ext = (cor_union.extent if same
                                   else cor_union.projectAs(psr).extent)
                    except Exception:                            # noqa: BLE001
                        cor_ext = False        # unknown: keep everything
                if cor_ext:
                    e = geom.extent
                    if (e.XMax < cor_ext.XMin or e.XMin > cor_ext.XMax
                            or e.YMax < cor_ext.YMin
                            or e.YMin > cor_ext.YMax):
                        n_far += 1
                        continue
                pat_geoms.append(geom)
        n_patch_feats = len(pat_geoms) + n_far
        if n_far:
            _say(messages, "{:,} of {:,} matching patch feature{} lie "
                           "entirely outside the corridor's extent and "
                           "were skipped before processing.".format(
                               n_far, n_patch_feats,
                               "" if n_patch_feats == 1 else "s"))
        if not pat_geoms:
            if n_far:
                _warn(messages, "Every matching patch polygon lies outside "
                                "the corridor -- running with termini only.")
            else:
                _warn(messages, "No patch polygons matched{} -- running "
                                "with termini only.".format(
                                    " the expression '{}'".format(patch_where)
                                    if patch_where else ""))
        else:
            merged = _union_all([g.projectAs(working_sr) for g in pat_geoms])
            merged = _densify(merged, step)
            clipped = merged.intersect(cor_w, 4)
            pieces = _explode(clipped) if (clipped and clipped.area > 0) else []
            pieces.sort(key=lambda p: -p.area)
            for i, piece in enumerate(pieces, start=1):
                key = "P:{}".format(i)
                groups[key] = {"label": "Patch {}".format(i),
                               "kind": "patch", "geom": piece}
            n_patch_pieces = len(pieces)
            _say(messages, "{} patch feature{} -> {} stepping-stone "
                           "piece{} inside the corridor (touching patches "
                           "merged, multi-strand pieces split).".format(
                               n_patch_feats,
                               "" if n_patch_feats == 1 else "s",
                               n_patch_pieces,
                               "" if n_patch_pieces == 1 else "s"))

    keys = sorted(groups, key=lambda k: (groups[k]["kind"] != "terminus",
                                         groups[k]["label"]))
    term_keys = [k for k in keys if groups[k]["kind"] == "terminus"]
    for key in keys:
        g = groups[key]
        g["rings"] = rings_of(g["geom"])
        g["pts"] = (np.vstack(g["rings"]) if g["rings"]
                    else np.zeros((0, 2)))
        g["edges"] = edges_of(g["rings"])

    # ---- strand decomposition -------------------------------------------
    # Pairs of polygons in different corridor strands can never connect --
    # the original's IsInSameStrand rule. Skipping them up front matters
    # enormously: on a many-strand corridor most pairs are cross-strand,
    # and every one skipped avoids a visibility-graph solve.
    cor_edges_all = edges_of(cor_rings)
    eps_geom = max(diag * 1e-9, 1e-9)
    ext_ring_ids = [i for i, r in enumerate(cor_rings)
                    if _signed_area(r) < 0]
    strand_rings = {si: [cor_rings[ri]]
                    for si, ri in enumerate(ext_ring_ids)}
    for i, r in enumerate(cor_rings):
        if _signed_area(r) >= 0:                 # a hole: assign to the
            p0 = r[0:1]                          # INNERMOST containing
            best_si, best_area = None, math.inf  # exterior -- first-found
            for si, ri in enumerate(ext_ring_ids):   # broke island-in-lake
                if points_inside(p0, edges_of([cor_rings[ri]]))[0]:
                    area = abs(_signed_area(cor_rings[ri]))
                    if area < best_area:
                        best_si, best_area = si, area
            if best_si is not None:
                strand_rings[best_si].append(r)
    ext_edges = {si: edges_of([strand_rings[si][0]])
                 for si in strand_rings}
    for k in keys:
        pts = groups[k]["pts"]
        member = set()
        for si in strand_rings:
            ins = points_inside(pts, ext_edges[si])
            if ins.any() or points_near_boundary(
                    pts, ext_edges[si], eps_geom).any():
                member.add(si)
        groups[k]["strands"] = member
    _mh.phase_advance()

    # ---- tier 1: direct closest-point connectors ------------------------
    all_pairs = [(keys[i], keys[j])
                 for i in range(len(keys)) for j in range(i + 1, len(keys))]
    pairs = [(ka, kb) for ka, kb in all_pairs
             if groups[ka]["strands"] & groups[kb]["strands"]]
    n_cross = len(all_pairs) - len(pairs)
    if n_cross:
        _say(messages, "{:,} of {:,} polygon pairs lie in different "
                       "corridor strands and can never connect; they are "
                       "skipped.".format(n_cross, len(all_pairs)))

    connectors = {}      # (ka, kb) -> {"coords", "planar", "direct"}
    candidates = []      # same-strand pairs awaiting the batched test
    for n_done, (ka, kb) in enumerate(pairs):
        ga, gb = groups[ka], groups[kb]
        d, pa, pb = closest_pair(ga["pts"], ga["edges"],
                                 gb["pts"], gb["edges"])
        if not math.isfinite(d):
            continue
        # Overlapping polygons (patch extending into a terminus block) have
        # TRUE distance zero, which vertex-vs-edge sampling misses.
        if d > _EPS_TOUCH and _polygons_overlap(ga["edges"], gb["edges"]):
            d = 0.0
        if d <= _EPS_TOUCH:
            connectors[(ka, kb)] = {
                "coords": np.array([pa, pb]), "planar": 0.0, "direct": True}
        else:
            candidates.append((ka, kb, pa, pb, d))
        if (n_done + 1) % 200 == 0:
            _mh.phase_step(n_done + 1, len(pairs))
    if candidates:
        # ONE vectorized visibility pass over every candidate segment --
        # per-pair GEOS containment calls were the first thing Jeff's
        # 19-strand, many-patch corridor exposed as quadratic pain.
        P = np.array([c[2] for c in candidates])
        Q = np.array([c[3] for c in candidates])
        ok = visible(P, Q, cor_edges_all, eps_geom,
                     bands=y_bands(cor_edges_all))
        for good, (ka, kb, pa, pb, d) in zip(ok, candidates):
            if good:
                connectors[(ka, kb)] = {"coords": np.array([pa, pb]),
                                        "planar": d, "direct": True}
    unresolved = [(ka, kb) for ka, kb in pairs
                  if (ka, kb) not in connectors]
    _say(messages, "Distance matrix: {:,} of {:,} same-strand pairs "
                   "connected by a direct line inside the corridor; {:,} "
                   "need constrained paths.".format(
                       len(connectors), len(pairs), len(unresolved)))
    _mh.phase_advance()

    # ---- tier 2: per-strand visibility graphs for obstructed pairs ------
    if unresolved:
        # Graph endpoints are SAMPLED along group boundaries (bounded per
        # group); an obstructed pair's reported gap can be long by up to
        # ~half the sample spacing. Direct connectors above stay exact.
        graph_step = diag / 500.0
        _say(messages, "Constrained-path endpoints are sampled about every "
                       "{:,.0f} m along polygon boundaries (direct "
                       "connectors are exact).".format(graph_step))
        by_strand = {}
        for ka, kb in unresolved:
            shared = groups[ka]["strands"] & groups[kb]["strands"]
            for si in shared:
                by_strand.setdefault(si, []).append((ka, kb))
        total_w = float(sum(len(v) for v in by_strand.values())) or 1.0
        done_w = 0.0
        for si, strand_pairs in sorted(by_strand.items()):
            active = sorted({k for pr in strand_pairs for k in pr})
            # only the vertices actually inside THIS strand join its graph
            strand_pts = {}
            for k in active:
                g = groups[k]
                if "graph_pts" not in g:
                    g["graph_pts"] = thin_points(g["rings"], graph_step)
                pts = g["graph_pts"]
                ins = points_inside(pts, ext_edges[si])
                ins |= points_near_boundary(pts, ext_edges[si], eps_geom)
                if ins.any():
                    strand_pts[k] = pts[ins]

            # this strand's share of the phase bar, ~85% of it for the
            # sight-line testing (the step users otherwise mistake for a
            # hang -- Jeff, 2026-08-15) and the rest for the path solves
            base = done_w / total_w
            span = len(strand_pairs) / total_w
            milestones = {1: False, 2: False, 3: False}

            def _build_progress(d, t, _si=si, _base=base, _span=span,
                                _ms=milestones):
                frac = (d / t) if t else 1.0
                _mh.phase_step(_base + 0.85 * _span * frac, 1.0)
                try:
                    arcpy.SetProgressorLabel(
                        "Strand {}: testing sight-lines... {:,} of {:,} "
                        "candidates".format(_si + 1, d, t))
                except Exception:                                # noqa: BLE001
                    pass
                if t >= 400_000:
                    if d == 0:
                        _say(messages,
                             "Strand {}: testing {:,} candidate "
                             "sight-lines (this is the long "
                             "step)...".format(_si + 1, t))
                    for quarter in (1, 2, 3):
                        if not _ms[quarter] and frac >= quarter / 4.0:
                            _ms[quarter] = True
                            _say(messages,
                                 "Strand {}: ...{:,} of {:,} candidate "
                                 "sight-lines tested.".format(
                                     _si + 1, d, t))

            graph = CorridorGraph(strand_rings[si], strand_pts, messages)
            graph.build(progress=_build_progress)
            _say(messages, "Strand {}: visibility graph of {:,} nodes "
                           "({:,} reflex vertices), {:,} edges, for {:,} "
                           "pair{}.".format(
                               si + 1, len(graph.coords), len(graph.reflex),
                               graph._graph.nnz, len(strand_pairs),
                               "" if len(strand_pairs) == 1 else "s"))
            try:
                arcpy.SetProgressorLabel(
                    "Strand {}: solving constrained paths...".format(si + 1))
            except Exception:                                    # noqa: BLE001
                pass
            from_cache = {}
            for n_p, (ka, kb) in enumerate(strand_pairs, start=1):
                if ka not in strand_pts or kb not in strand_pts:
                    continue
                if ka not in from_cache:
                    from_cache[ka] = graph.paths_from(ka)
                dist, pred = from_cache[ka]
                planar, coords = graph.path_to(kb, dist, pred)
                _mh.phase_step(
                    base + span * (0.85 + 0.15 * n_p / len(strand_pairs)),
                    1.0)
                if coords is None:
                    continue
                prev = connectors.get((ka, kb))
                if prev is None or planar < prev["planar"]:
                    connectors[(ka, kb)] = {"coords": coords,
                                            "planar": planar,
                                            "direct": False}
            done_w += len(strand_pairs)
    _mh.phase_advance()

    # ---- edge list (planar weights; geodesic computed for tree edges) ---
    # The original also rejected connectors that pass through a third
    # polygon. That test is unnecessary here: such a connector is at least
    # as long as each of its two hops via that polygon, so Kruskal adds the
    # hops first and rejects the through connector as a cycle.
    edges = []
    key_index = {k: i for i, k in enumerate(keys)}
    for (ka, kb), con in sorted(connectors.items()):
        edges.append((con["planar"], key_index[ka], key_index[kb],
                      {"pair": (ka, kb), "coords": con["coords"],
                       "direct": con["direct"], "planar": con["planar"]}))

    # ---- Kruskal spanning forest + per-termini-pair routes --------------
    forest = spanning_forest(len(keys), edges)
    for _e, (_w, payload) in forest.items():
        payload["length_m"] = (0.0 if payload["planar"] <= _EPS_TOUCH
                               else _geodesic_m(payload["coords"],
                                                working_sr))
    pair_results = []
    used_edges = {}          # frozenset(i, j) -> payload
    crit_for = {}            # frozenset(i, j) -> [pair labels]
    stones_used = set()
    for x in range(len(term_keys)):
        for y in range(x + 1, len(term_keys)):
            ka, kb = term_keys[x], term_keys[y]
            la, lb = groups[ka]["label"], groups[kb]["label"]
            pair_label = "{} - {}".format(la, lb)
            node_path = tree_path(forest, key_index[ka], key_index[kb])
            if node_path is None:
                pair_results.append({
                    "from": la, "to": lb, "reachable": False})
                continue
            segs = []
            for i in range(len(node_path) - 1):
                e = frozenset((node_path[i], node_path[i + 1]))
                w, payload = forest[e]
                segs.append((e, payload))
                used_edges[e] = payload
                pay_pairs = payload.setdefault("used_by", [])
                pay_pairs.append(pair_label)
            n_stone_nodes = 0
            for node in node_path[1:-1]:
                stones_used.add(keys[node])
                if groups[keys[node]].get("kind") == "patch":
                    n_stone_nodes += 1
            # zero-length links (touching polygons) are part of the route
            # but are not gaps the animal must cross
            lengths = sorted((p["length_m"] for _e, p in segs
                              if p["length_m"] > _EPS_TOUCH), reverse=True)
            crit = lengths[0] if lengths else 0.0
            if lengths:
                worst = max(segs, key=lambda ep: ep[1]["length_m"])
                crit_for.setdefault(worst[0], []).append(pair_label)
            pair_results.append({
                "from": la, "to": lb, "reachable": True,
                "n_segments": len(lengths),
                "critical_gap_m": crit,
                "segment_lengths_m": lengths,
                # PATCH nodes only -- an intermediate TERMINUS on the
                # spanning-forest path is not a stepping-stone patch
                "n_stones": n_stone_nodes,
            })

    # add unreachable entries for termini that never made it into groups
    for label in unplaced_termini:
        for k in term_keys:
            pair_results.append({"from": label, "to": groups[k]["label"],
                                 "reachable": False,
                                 "note": "terminus does not overlap the "
                                         "corridor"})
    _mh.phase_advance()

    # ---- write the connector feature class ------------------------------
    rows = sorted(used_edges.items(),
                  key=lambda ep: -ep[1]["length_m"])
    sr_out = out_sr
    ws = out_ws or arcpy.env.workspace or arcpy.env.scratchFolder
    out_fc = os.path.join(ws, out_name)
    arcpy.management.CreateFeatureclass(
        ws, out_name, "POLYLINE", spatial_reference=_mh.clean_sr(sr_out))
    field_len = {"From_Name": 128, "To_Name": 128,
                 "Used_By": 254, "Crit_For": 254}
    # one AddFields call, not one AddField per field (standing rule)
    arcpy.management.AddFields(out_fc, [
        ["From_Name", "TEXT", "", field_len["From_Name"], "", ""],
        ["To_Name", "TEXT", "", field_len["To_Name"], "", ""],
        ["Length_m", "DOUBLE", "", "", "", ""],
        ["Rank", "LONG", "", "", "", ""],
        ["Used_By", "TEXT", "", field_len["Used_By"], "", ""],
        ["Crit_For", "TEXT", "", field_len["Crit_For"], "", ""]])

    def _clip_text(text, name):
        text = "; ".join(sorted(set(text)))
        limit = field_len[name]
        return text if len(text) <= limit else text[:limit - 3] + "..."

    n_zero = 0
    worst_excursion = 0.0
    with arcpy.da.InsertCursor(
            out_fc, ["SHAPE@", "From_Name", "To_Name", "Length_m",
                     "Rank", "Used_By", "Crit_For"]) as icur:
        rank = 0
        for e, payload in rows:
            ka, kb = payload["pair"]
            if payload["length_m"] <= _EPS_TOUCH:
                n_zero += 1
                continue
            rank += 1
            arr = arcpy.Array([arcpy.Point(float(x), float(y))
                               for x, y in payload["coords"]])
            line_w = arcpy.Polyline(arr, working_sr)
            # belt-and-braces: no connector may leave the analysis polygon
            try:
                escaped = line_w.difference(cor_w)
                if escaped is not None:
                    worst_excursion = max(worst_excursion, escaped.length)
            except Exception:                                    # noqa: BLE001
                pass
            icur.insertRow((
                line_w.projectAs(sr_out), groups[ka]["label"],
                groups[kb]["label"],
                payload["length_m"], rank,
                _clip_text(payload.get("used_by", []), "Used_By"),
                _clip_text(crit_for.get(e, []), "Crit_For")))
    if worst_excursion > max(2.0 * pinch, 0.5):
        _warn(messages,
              "INTEGRITY CHECK: a route segment leaves the analyzed "
              "corridor polygon by up to {:,.1f} m. This should not "
              "happen -- please report it, and try a smaller (or 0) "
              "boundary simplification tolerance "
              "meanwhile.".format(worst_excursion))
    if n_zero:
        _say(messages, "{} route link{} where polygons touch (zero gap) "
                       "are part of the route but not written as "
                       "segments.".format(n_zero, "" if n_zero == 1 else "s"))

    # ---- report ---------------------------------------------------------
    _say(messages, "")
    _say(messages, "PATCH ANALYSIS RESULTS")
    _say(messages, "-" * 60)
    for res in sorted(pair_results, key=lambda r: (r["from"], r["to"])):
        if not res.get("reachable"):
            _say(messages, "{} - {}:  NOT CONNECTED{}".format(
                res["from"], res["to"],
                " ({})".format(res["note"]) if res.get("note") else
                " by this corridor (different strands, or no route "
                "exists)"))
            continue
        _say(messages, "{} - {}:".format(res["from"], res["to"]))
        _say(messages, "   {} segment{} required; {} stepping-stone "
                       "patch{} used.".format(
                           res["n_segments"],
                           "" if res["n_segments"] == 1 else "s",
                           res["n_stones"],
                           "" if res["n_stones"] == 1 else "es"))
        _say(messages, "   CRITICAL GAP (longest single crossing): "
                       "{:,.1f} m".format(res["critical_gap_m"]))
        shown = res["segment_lengths_m"][:25]
        _say(messages, "   Segment lengths, decreasing (m): {}{}".format(
            ", ".join("{:,.1f}".format(v) for v in shown),
            " ..." if len(res["segment_lengths_m"]) > len(shown) else ""))
    _say(messages, "-" * 60)

    # ---- linkage record + metadata --------------------------------------
    trec = _lnk.read(ter_path, _lnk.TERMINI)
    crec = _lnk.read(cor_path, _lnk.CORRIDOR)
    src_analysis = ((trec or {}).get("analysis_id")
                    or (crec or {}).get("analysis_id"))
    analysis_id = src_analysis or _lnk.new_analysis_id()

    def _sel(item, path):
        n_total, n_sel, has = _mh.selection_info(item, path)
        return {"n_total": n_total, "n_selected": n_sel,
                "selected": bool(has)}

    record = _lnk.patch_analysis_record(
        analysis_id,
        source_corridor=dict(path=cor_path, **_sel(corridor, cor_path)),
        source_termini=dict(path=ter_path, **_sel(termini, ter_path)),
        source_patches=(dict(path=pat_path, where=patch_where or None,
                             n_features=n_patch_feats,
                             n_pieces=n_patch_pieces,
                             **_sel(patches, pat_path)) if patches else None),
        params={
            "name_field": name_field,
            "simplify_tolerance_m": tol,
            "working_sr": working_sr.name,
            "n_termini": len(term_keys),
            "n_direct": sum(1 for c in connectors.values() if c["direct"]),
            "n_constrained": sum(1 for c in connectors.values()
                                 if not c["direct"]),
            "n_strands": len(strand_rings),
            "n_cross_strand_pairs_skipped": n_cross,
        },
        pairs=pair_results,
        stones_used=sorted(groups[k]["label"] for k in stones_used
                           if groups[k]["kind"] == "patch"),
        source_analysis_id=src_analysis)
    _lnk.write(out_fc, record, messages)

    _tm.set_entity_and_fields(
        out_fc,
        entity="Patch Analysis connector segments: the gaps of unsuitable "
               "habitat an animal must cross moving between termini "
               "through this corridor, minimizing the longest single "
               "crossing (minimax route via a minimum spanning tree). " +
               _CD_CITATION,
        fields=[
            {"name": "From_Name",
             "description": "Name of the terminus or stepping-stone patch "
                            "this gap crossing starts from."},
            {"name": "To_Name",
             "description": "Name of the terminus or stepping-stone patch "
                            "this gap crossing ends at."},
            {"name": "Length_m",
             "description": "Geodesic length of the crossing in meters -- "
                            "the expanse of unsuitable habitat crossed in "
                            "one push."},
            {"name": "Rank",
             "description": "Rank of this crossing among all route "
                            "segments, 1 = longest (the corridor's worst "
                            "gap)."},
            {"name": "Used_By",
             "description": "The termini pairs whose optimal route uses "
                            "this crossing."},
            {"name": "Crit_For",
             "description": "The termini pairs for which this crossing is "
                            "the CRITICAL (longest) gap of the whole "
                            "route; empty otherwise."},
        ],
        messages=messages)
    _mh.end_phases()
    return record
