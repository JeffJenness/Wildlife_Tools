# -*- coding: utf-8 -*-
"""Bottleneck Analysis -- the widest route through a corridor, and the
width profile along it.

A modernized port of the CorridorDesigner Evaluation Tools' "Bottleneck
Analysis" (``cmdBottleneck.cls`` / ``CorridorAnalyses.bas RunBottleneck`` /
``frmGraph.frm``; manual pp. 25-41). A narrow spot in a corridor is a
bottleneck; severe bottlenecks can make a corridor useless for edge- or
development-sensitive species. This tool finds the route between two
wildland blocks that MAXIMIZES THE MINIMUM corridor width encountered
(the maximin route -- the exact dual of Patch Analysis's minimax gap),
then measures the corridor width at regular stations along that route so
the width profile can be graphed and thresholded.

THE ORIGINAL'S METHOD, replaced wholesale (with Jeff's blessing -- he
always considered it a hack): rasterize at long-edge/1000, approximate
the centerline as the zero-flow-accumulation ridges of the
distance-to-edge surface, force cost paths onto those ridge cells with a
10^9 off-ridge penalty, spray candidate routes from sampled block-gate
points, and pick the candidate with the largest minimum width. The ridge
approximation is fragile (cell-size dependent, disconnects on diagonals)
and the candidate bundle carries no guarantee of containing the true
widest route.

THIS VERSION computes the answer exactly, in vector, with no Spatial
Analyst:

1. MEDIAL AXIS from a Voronoi diagram: sample the (repaired, simplified)
   corridor-plus-block-shells boundary at a regular spacing and take
   ``scipy.spatial.Voronoi`` of the samples. The Voronoi edges lying
   inside the polygon converge to the true medial axis as the sampling
   densifies (a classical result), and each Voronoi vertex's CLEARANCE --
   its distance to the nearest boundary samples, i.e. the radius of its
   empty circle -- comes straight from the same construction.
2. MAXIMIN (widest-path) SEARCH: sort medial edges by clearance
   DESCENDING and union-find until Block A's region connects to Block
   B's -- Kruskal again, run in the opposite direction from Patch
   Analysis. The clearance of the edge that makes the connection IS the
   bottleneck half-width, globally optimal over every strand, branch,
   and loop simultaneously. No candidate bundle, no sampling luck.
3. A UNIQUE CENTRAL ROUTE: among edges at least that wide, one Dijkstra
   weighted by length/clearance picks the shortest route that stays in
   the widest water -- replacing the original's cost-then-shortest tie
   breaking. It is central by construction: it lives on the medial axis.
4. WIDTH PROFILE: the route is stationed at a regular interval and the
   width (2 x exact distance to the boundary) measured at every station
   -- vector-exact, not raster-sampled.

Kept faithfully from the original: block interior holes are ignored
(outer shells only); block parts that do not touch the corridor are
dropped; the width surface is the corridor UNION the block shells, so
widths at the corridor mouths blend into the blocks; the route is
trimmed to run GATE TO GATE (the original launched its candidate routes
at the block boundaries), and narrowness INSIDE a block never counts as
a corridor bottleneck. Corner pinches (diagonal
cell-corner touches in raster-traced corridors) are traversable at
near-zero width -- for THIS tool that is not just repair but semantics:
a route through a pinch honestly reports an essentially zero-width
bottleneck, and the maximin search therefore avoids pinches whenever any
real alternative exists.

Geographic corridors are handled in the suite's corridor-centered
azimuthal equidistant working space; all lengths and widths are reported
in meters. Every output is stamped with a ``bottleneck`` linkage record.
"""
import math
import os

import numpy as np

import mahalanobis as _mh
import patch_analysis as _pa
import land_facet_linkage as _lnk
import tool_metadata as _tm

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org.")

DEFAULT_STATIONS = 1000        # ~point count of the original's export


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


# ---------------------------------------------------------------------------
# pure-numpy / scipy geometry
# ---------------------------------------------------------------------------
def sample_rings(rings, step):
    """Evenly spaced arc-length samples along every ring, ~``step`` apart
    (no per-group cap -- unlike patch_analysis.thin_points, the Voronoi
    sites must cover the whole boundary densely and uniformly)."""
    out = []
    for ring in rings:
        if len(ring) < 3:
            continue
        closed = np.vstack([ring, ring[:1]])
        seg = np.hypot(*np.diff(closed, axis=0).T)
        perim = float(seg.sum())
        if perim <= 0:
            continue
        n = max(8, int(round(perim / step)))
        targets = np.linspace(0.0, perim, n, endpoint=False)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        idx = np.clip(np.searchsorted(cum, targets, side="right") - 1,
                      0, len(seg) - 1)
        frac = (targets - cum[idx]) / np.where(seg[idx] > 0, seg[idx], 1.0)
        out.append(closed[idx] + frac[:, None] * (closed[idx + 1]
                                                  - closed[idx]))
    if not out:
        return np.zeros((0, 2))
    pts = np.vstack(out)
    # qhull dislikes exact duplicates (ring closures, shared corners)
    return np.unique(np.round(pts, 6), axis=0)


def dist_to_edges(pts, edges, chunk_bytes=8e7):
    """Exact minimum point-to-segment distance from each point in ``pts``
    (N, 2) to the edge set (E, 4). The measuring stick for widths."""
    pts = np.atleast_2d(pts)
    n = len(pts)
    out = np.full(n, np.inf)
    if n == 0 or len(edges) == 0:
        return out
    ax, ay = edges[:, 0], edges[:, 1]
    dx, dy = edges[:, 2] - ax, edges[:, 3] - ay
    seg2 = dx * dx + dy * dy
    seg2 = np.where(seg2 <= 0, 1.0, seg2)
    step = max(1, int(chunk_bytes / (len(edges) * 8 + 1)))
    for i0 in range(0, n, step):
        px = pts[i0:i0 + step, 0][:, None]
        py = pts[i0:i0 + step, 1][:, None]
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / seg2, 0.0, 1.0)
        qx = ax + t * dx
        qy = ay + t * dy
        d2 = (px - qx) ** 2 + (py - qy) ** 2
        out[i0:i0 + step] = np.sqrt(d2.min(axis=1))
    return out


def build_medial_graph(rings, boundary_step):
    """The medial-axis graph of the polygon defined by ``rings``.

    Returns ``(verts, graph_edges, clearance)``: the interior Voronoi
    vertices (V, 2), the medial edges as an (M, 2) int array of vertex
    indices, and each vertex's clearance (distance to the boundary
    sampling = radius of its empty circle)."""
    from scipy.spatial import Voronoi, cKDTree

    sites = sample_rings(rings, boundary_step)
    if len(sites) < 8:
        raise ValueError("The corridor boundary is too small to sample.")
    # center before qhull: at map-scale offsets (UTM false eastings /
    # northings ~1e6) the lifted-paraboloid arithmetic silently merges
    # or drops Voronoi vertices (measured: dense boundary rings lose
    # ~40% of their vertices); Voronoi is translation-equivariant, so
    # solve about the centroid and shift back
    mid = sites.mean(axis=0)
    vor = Voronoi(sites - mid)
    verts = vor.vertices + mid
    poly_edges = _pa.edges_of(rings)
    bands = _pa.y_bands(poly_edges)
    inside = _pa.points_inside(verts, poly_edges, bands=bands)
    clearance = cKDTree(sites).query(verts, k=1)[0]

    ridge = np.asarray(vor.ridge_vertices, dtype=int)
    ok = (ridge[:, 0] >= 0) & (ridge[:, 1] >= 0)
    ridge = ridge[ok]
    ok = inside[ridge[:, 0]] & inside[ridge[:, 1]]
    graph_edges = ridge[ok]
    return verts, graph_edges, clearance


class _UnionFind(object):
    def __init__(self, n):
        self.parent = np.arange(n)

    def find(self, i):
        p = self.parent
        while p[i] != i:
            p[i] = p[p[i]]
            i = p[i]
        return i

    def union(self, i, j):
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return False
        self.parent[ri] = rj
        return True


def widest_path(verts, graph_edges, clearance, sources, targets):
    """The maximin route: ``(bottleneck_clearance, vertex_index_path)``.

    Kruskal DESCENDING on edge clearance (min of the two endpoint
    clearances) with virtual super-source/target nodes, then a Dijkstra
    over the restricted (>= bottleneck) subgraph, weighted by
    length/clearance so the unique route stays in the widest water.
    Returns ``(None, None)`` when no route exists (disjoint strands)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra

    if not len(graph_edges) or not len(sources) or not len(targets):
        return None, None
    ecl = np.minimum(clearance[graph_edges[:, 0]],
                     clearance[graph_edges[:, 1]])
    order = np.argsort(-ecl)

    n = len(verts)
    uf = _UnionFind(n + 2)
    S, T = n, n + 1                      # virtual super-source / target
    for s in sources:
        uf.union(int(s), S)
    for t in targets:
        uf.union(int(t), T)
    if uf.find(S) == uf.find(T):
        # a vertex is in both sets (blocks meet); degenerate but legal
        bottleneck = float(ecl.max()) if len(ecl) else 0.0
    else:
        bottleneck = None
        for k in order:
            u, v = graph_edges[k]
            uf.union(int(u), int(v))
            if uf.find(S) == uf.find(T):
                bottleneck = float(ecl[k])
                break
        if bottleneck is None:
            return None, None

    # restricted subgraph: everything at least as wide as the bottleneck
    keep = ecl >= bottleneck * (1.0 - 1e-9)
    ge = graph_edges[keep]
    ecl_k = ecl[keep]
    length = np.hypot(verts[ge[:, 0], 0] - verts[ge[:, 1], 0],
                      verts[ge[:, 0], 1] - verts[ge[:, 1], 1])
    weight = length / np.maximum(ecl_k, 1e-9)
    g = coo_matrix((weight, (ge[:, 0], ge[:, 1])), shape=(n, n)).tocsr()
    dist, pred, _src = dijkstra(g, directed=False, indices=sources,
                                min_only=True, return_predecessors=True)
    sub = dist[targets]
    k = int(np.argmin(sub))
    if not np.isfinite(sub[k]):
        return None, None                # can't happen if Kruskal connected
    node = int(targets[k])
    chain = [node]
    while pred[chain[-1]] >= 0:
        chain.append(int(pred[chain[-1]]))
    return bottleneck, chain[::-1]


def profile_route(path_xy, poly_edges, n_stations):
    """Station the route and measure the width at every station.

    Returns ``(stations, widths, pts)``: planar station distances along
    the route, width = 2 x exact distance to the polygon boundary, and
    the station coordinates (K, 2)."""
    seg = np.hypot(*np.diff(path_xy, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if total <= 0:
        return (np.zeros(1), 2.0 * dist_to_edges(path_xy[:1], poly_edges),
                path_xy[:1])
    stations = np.linspace(0.0, total, max(2, int(n_stations)))
    idx = np.clip(np.searchsorted(cum, stations, side="right") - 1,
                  0, len(seg) - 1)
    frac = (stations - cum[idx]) / np.where(seg[idx] > 0, seg[idx], 1.0)
    pts = path_xy[idx] + frac[:, None] * (path_xy[idx + 1] - path_xy[idx])
    widths = 2.0 * dist_to_edges(pts, poly_edges)
    return stations, widths, pts


def split_at_threshold(stations, widths, pts, threshold):
    """Contiguous above/below-threshold pieces of the profiled route.

    Returns a list of ``(below, coords, start_station, end_station)``
    with the crossing points linearly interpolated between stations so
    piece lengths sum exactly to the route length."""
    below = widths < threshold
    pieces = []
    i = 0
    n = len(stations)
    while i < n:
        j = i
        while j + 1 < n and below[j + 1] == below[i]:
            j += 1
        s0, s1 = stations[i], stations[j]
        coords = [pts[i]]
        coords.extend(pts[i + 1:j + 1])
        # interpolate the entry/exit crossings
        if i > 0:
            f = ((threshold - widths[i - 1])
                 / ((widths[i] - widths[i - 1]) or 1e-12))
            f = min(1.0, max(0.0, f))
            s0 = stations[i - 1] + f * (stations[i] - stations[i - 1])
            coords.insert(0, pts[i - 1] + f * (pts[i] - pts[i - 1]))
        if j + 1 < n:
            f = ((threshold - widths[j])
                 / ((widths[j + 1] - widths[j]) or 1e-12))
            f = min(1.0, max(0.0, f))
            s1 = stations[j] + f * (stations[j + 1] - stations[j])
            coords.append(pts[j] + f * (pts[j + 1] - pts[j]))
        pieces.append((bool(below[i]), np.asarray(coords), float(s0),
                       float(s1)))
        i = j + 1
    return pieces


# ---------------------------------------------------------------------------
# the tool
# ---------------------------------------------------------------------------
def _read_block(dataset, where, label, target_sr, corridor, step_m,
                messages):
    """One wildland block: union of the matching features, OUTER SHELLS
    only, parts not touching the corridor dropped (the original's rule).
    Everything happens in ``target_sr`` -- the CORRIDOR'S OWN coordinate
    system, not the custom working space, because arcpy geometry unions
    between custom-SR geometries raise ValueError (equal-but-separate
    unknown spatial references)."""
    import arcpy

    geoms = []
    with arcpy.da.SearchCursor(dataset, ["SHAPE@"],
                               where_clause=where or None) as cur:
        for (g,) in cur:
            if g is not None:
                geoms.append(g)
    if not geoms:
        raise arcpy.ExecuteError(
            "{}: no polygon features matched{}.".format(
                label, " '{}'".format(where) if where else ""))
    merged = _pa._union_all(geoms).projectAs(target_sr)
    if getattr(merged, "hasCurves", False):
        # curve step in the target SR's own units (degrees on a GCS)
        try:
            if target_sr.type == "Geographic":
                d_step = step_m / 111319.49
            else:
                d_step = step_m / (target_sr.metersPerUnit or 1.0)
        except Exception:                                        # noqa: BLE001
            d_step = step_m
        merged = _pa._densify(merged, d_step)
    merged = _pa._outer_rings_only(merged, target_sr)
    parts = _pa._explode(merged)
    keep = [p for p in parts if not p.disjoint(corridor)]
    if not keep:
        raise arcpy.ExecuteError(
            "{} does not touch the corridor polygon -- a bottleneck "
            "analysis needs both blocks connected by the "
            "corridor.".format(label))
    if len(keep) < len(parts):
        _say(messages, "{}: {} of {} parts touch the corridor and are "
                       "used.".format(label, len(keep), len(parts)))
    return _pa._union_all(keep)


def run_bottleneck(corridor, block_a, block_b,
                   corridor_where=None,
                   block_a_where=None, block_b_where=None,
                   block_a_label="Block A", block_b_label="Block B",
                   out_route=None, out_points=None, out_segments=None,
                   threshold=None, n_stations=DEFAULT_STATIONS,
                   boundary_step=None, simplify_tolerance=None,
                   profile_json=None, messages=None):
    """Run the full Bottleneck Analysis. Returns ``(record, profile)``
    where ``profile`` is a dict with stations/widths/route/stats -- the
    payload the interactive window graphs.

    ``corridor`` / ``block_a`` / ``block_b``: polygon layers or feature
    classes (layer selections honored); the blocks may be narrowed with
    the optional where clauses, so a single feature class carrying both
    blocks works as naturally as two separate ones. ``threshold`` in
    meters classifies the profile (and drives ``out_segments``);
    ``out_route`` / ``out_points`` / ``out_segments`` are optional
    feature-class outputs. ``boundary_step`` (meters) is the Voronoi
    boundary sampling spacing (default: extent diagonal / 2000);
    ``simplify_tolerance`` as in Patch Analysis.
    """
    import arcpy

    _mh.begin_phases([
        ("Reading and preparing the polygons...", 2),
        ("Building the medial-axis (Voronoi) graph...", 3),
        ("Finding the widest route...", 2),
        ("Measuring the width profile...", 1),
        ("Writing outputs...", 1),
    ])

    def _cat(item):
        try:
            return arcpy.Describe(item).catalogPath
        except Exception:                                        # noqa: BLE001
            return str(item) if item is not None else None

    cor_path = _cat(corridor)

    # ---- corridor -------------------------------------------------------
    cor_geoms = [g for (g,) in arcpy.da.SearchCursor(
                     corridor, ["SHAPE@"],
                     where_clause=corridor_where or None)
                 if g is not None]
    if not cor_geoms:
        raise arcpy.ExecuteError(
            "The corridor layer has no polygon features{} (is the "
            "selection empty?).".format(
                " matching '{}'".format(corridor_where)
                if corridor_where else ""))
    cor_union = _pa._union_all(cor_geoms)
    out_sr = cor_union.spatialReference
    working_sr, is_custom = _pa._working_sr(cor_union)
    if is_custom:
        _say(messages, "The corridor is in a geographic coordinate "
                       "system; the analysis runs in a corridor-centered "
                       "azimuthal equidistant projection and all widths "
                       "and lengths are meters.")
    # The analysis runs in the working SR's own linear unit; every
    # reported width/length is METERS. A foot-based projected corridor
    # (US State Plane) previously had its widths reported as-if-meters
    # (~3.3x overstated) and the meters threshold compared against feet.
    try:
        unit_m = float(getattr(working_sr, "metersPerUnit", 1.0) or 1.0)
    except Exception:                                            # noqa: BLE001
        unit_m = 1.0
    if abs(unit_m - 1.0) > 1e-9:
        _say(messages, "The corridor's coordinate system measures in a "
                       "unit of {:.6g} m; widths and lengths are "
                       "converted to METERS for every result.".format(
                           unit_m))
    cor_w = cor_union.projectAs(working_sr)
    ext = cor_w.extent
    diag = math.hypot(ext.width, ext.height)
    if diag <= 0:
        raise arcpy.ExecuteError("The corridor polygon is degenerate.")
    step = diag / 1000.0

    # ---- blocks, and the corridor-union-shells width surface ------------
    # All unions run in the CORRIDOR'S OWN coordinate system: arcpy
    # geometry unions between custom working-SR geometries (the AEQD
    # space used for geographic corridors) raise ValueError. The union is
    # projected to the working space ONCE afterwards.
    shell_a = _read_block(block_a, block_a_where, block_a_label,
                          out_sr, cor_union, step, messages)
    shell_b = _read_block(block_b, block_b_where, block_b_label,
                          out_sr, cor_union, step, messages)
    if not shell_a.disjoint(shell_b):
        _warn(messages, "The two wildland blocks overlap each other -- "
                        "the 'bottleneck' between overlapping blocks is "
                        "not meaningful. Results describe whatever "
                        "route the corridor offers, but check the "
                        "inputs.")

    habitat = cor_union.union(shell_a).union(shell_b) \
        .projectAs(working_sr)
    shell_a_w = shell_a.projectAs(working_sr)
    shell_b_w = shell_b.projectAs(working_sr)
    pinch = min(1.0, max(0.05, diag * 2e-5))
    try:
        buffered = habitat.buffer(pinch)
        if buffered is not None and buffered.area > 0:
            habitat = buffered
    except Exception:                                            # noqa: BLE001
        pass
    habitat = _pa._densify(habitat, step)
    habitat, ok = _pa.ensure_simple(habitat, working_sr)
    if not ok:
        _warn(messages, "The analysis polygon could not be fully "
                        "repaired to simple geometry; results may be "
                        "affected.")
    tol = (diag / 2000.0) if simplify_tolerance is None \
        else max(0.0, float(simplify_tolerance) / unit_m)
    if tol > 0:
        try:
            gen = habitat.generalize(tol)
            if gen is not None and gen.area > 0:
                gen, gen_ok = _pa.ensure_simple(gen, working_sr)
                if gen_ok:
                    habitat = gen
                else:
                    tol = 0.0
                    _warn(messages, "Boundary simplification produced "
                                    "unrepairable geometry; analyzing "
                                    "unsimplified.")
        except Exception:                                        # noqa: BLE001
            pass

    rings = _pa.rings_of(habitat)
    poly_edges = _pa.edges_of(rings)
    _say(messages, "Analysis polygon: {:,} boundary vertices "
                   "(simplification tolerance {}).".format(
                       sum(len(r) for r in rings),
                       "off" if tol <= 0
                       else "{:.1f} m".format(tol * unit_m)))
    _mh.phase_advance()

    # ---- medial-axis graph ----------------------------------------------
    bstep = (diag / 2000.0) if boundary_step is None \
        else max(0.01, float(boundary_step) / unit_m)
    verts, graph_edges, clearance = build_medial_graph(rings, bstep)
    _say(messages, "Medial-axis graph: {:,} Voronoi vertices, {:,} "
                   "edges, from boundary samples every ~{:,.1f} m.".format(
                       len(verts), len(graph_edges), bstep * unit_m))
    _mh.phase_advance()

    # ---- sources / targets: medial vertices inside each block shell -----
    def _inside(shell):
        r = _pa.rings_of(shell)
        e = _pa.edges_of(r)
        return _pa.points_inside(verts, e, bands=_pa.y_bands(e))

    in_a = _inside(shell_a_w)
    in_b = _inside(shell_b_w)
    sources = np.flatnonzero(in_a)
    targets = np.flatnonzero(in_b)
    if not len(sources) or not len(targets):
        raise arcpy.ExecuteError(
            "No medial-axis vertices fall inside {} -- the block barely "
            "overlaps the corridor. Check the inputs.".format(
                block_a_label if not len(sources) else block_b_label))

    # Inside the blocks the clearance is uncapped (huge): the original
    # launched its routes AT the block gates, so narrowness INSIDE a
    # block never counts as a corridor bottleneck -- only the corridor
    # constrains the maximin.
    clearance_eff = clearance.copy()
    clearance_eff[in_a | in_b] = diag
    bottleneck_half, path_idx = widest_path(verts, graph_edges,
                                            clearance_eff,
                                            sources, targets)
    if path_idx is None:
        raise arcpy.ExecuteError(
            "The corridor does not connect {} to {} (they lie in "
            "different corridor strands).".format(block_a_label,
                                                  block_b_label))
    bottleneck_w = 2.0 * bottleneck_half

    # Trim the route to gate-to-gate (the original's routes began at the
    # block boundaries): drop leading vertices inside Block A and
    # trailing vertices inside Block B, keeping one anchor vertex on
    # each side so the profile starts and ends AT the gates.
    path_idx = np.asarray(path_idx, dtype=int)
    pa_flags = in_a[path_idx]
    pb_flags = in_b[path_idx]
    start = 0
    for i in range(len(path_idx)):
        if not pa_flags[i]:
            start = max(0, i - 1)
            break
    end = len(path_idx) - 1
    for i in range(len(path_idx) - 1, -1, -1):
        if not pb_flags[i]:
            end = min(len(path_idx) - 1, i + 1)
            break
    if end - start < 1:
        start, end = 0, len(path_idx) - 1
    path_xy = verts[path_idx[start:end + 1]]

    # clip the anchor segments exactly AT the gates (bisection on the
    # inside test -- the anchor medial vertex can sit well inside a block)
    def _gate_clip(p_in, p_out, shell):
        r = _pa.rings_of(shell)
        e = _pa.edges_of(r)
        lo, hi = 0.0, 1.0                # lo = inside fraction from p_in
        for _ in range(20):
            mid = 0.5 * (lo + hi)
            pt = p_in + mid * (p_out - p_in)
            if _pa.points_inside(pt[None, :], e)[0]:
                lo = mid
            else:
                hi = mid
        return p_in + hi * (p_out - p_in)

    if len(path_xy) >= 2 and pa_flags[start]:
        path_xy = path_xy.copy()
        path_xy[0] = _gate_clip(path_xy[0], path_xy[1], shell_a_w)
    if len(path_xy) >= 2 and pb_flags[end]:
        path_xy = path_xy.copy()
        path_xy[-1] = _gate_clip(path_xy[-1], path_xy[-2], shell_b_w)
    if bottleneck_w <= 4.0 * pinch + 0.01:
        _warn(messages, "The widest available route pinches to "
                        "effectively ZERO width -- the blocks connect "
                        "only through a cell-corner touch of the "
                        "corridor polygon. Treat this corridor as "
                        "having no usable bottleneck width.")
    _mh.phase_advance()

    # ---- width profile --------------------------------------------------
    stations, widths, pts = profile_route(path_xy, poly_edges, n_stations)
    planar_len = float(stations[-1])
    geod_len = _pa._geodesic_m(path_xy, working_sr)
    scale = (geod_len / planar_len) if planar_len > 0 else 1.0
    stations_g = stations * scale
    # Working units -> METERS for everything reported from here on
    # (stations get the geodesic scale, which already lands in meters).
    widths = widths * unit_m
    bottleneck_w *= unit_m

    w_sorted = np.sort(widths)
    stats = {
        "n_stations": int(len(stations)),
        "route_length_m": geod_len,
        "bottleneck_width_m": float(bottleneck_w),
        "min_width_m": float(w_sorted[0]),
        "max_width_m": float(w_sorted[-1]),
        "mean_width_m": float(widths.mean()),
        "median_width_m": float(np.median(widths)),
        "sd_width_m": float(widths.std(ddof=1)) if len(widths) > 1 else 0.0,
    }
    k_min = int(np.argmin(widths))
    stats["min_width_station_m"] = float(stations_g[k_min])
    _say(messages, "")
    _say(messages, "BOTTLENECK ANALYSIS RESULTS")
    _say(messages, "-" * 60)
    _say(messages, "Widest route: {:,.1f} m long; its narrowest point "
                   "is {:,.1f} m wide, {:,.1f} m along the route.".format(
                       geod_len, stats["min_width_m"],
                       stats["min_width_station_m"]))
    _say(messages, "Width along the route: mean {:,.1f}, median {:,.1f}, "
                   "range {:,.1f} - {:,.1f} m.".format(
                       stats["mean_width_m"], stats["median_width_m"],
                       stats["min_width_m"], stats["max_width_m"]))
    threshold_stats = None
    if threshold is not None and threshold > 0:
        below = widths < threshold
        pieces = split_at_threshold(stations_g, widths, pts, threshold)
        below_lens = sorted((p[3] - p[2] for p in pieces if p[0]),
                            reverse=True)
        threshold_stats = {
            "threshold_m": float(threshold),
            "pct_below": float(100.0 * below.mean()),
            "length_below_m": float(sum(below_lens)),
            "n_below_segments": len(below_lens),
            "below_segment_lengths_m": [float(v) for v in below_lens],
        }
        stats["threshold"] = threshold_stats
        _say(messages, "Threshold {:,.1f} m: {:.1f}% of the route is "
                       "narrower ({:,.1f} m in {} stretch{}).".format(
                           threshold, threshold_stats["pct_below"],
                           threshold_stats["length_below_m"],
                           threshold_stats["n_below_segments"],
                           "" if threshold_stats["n_below_segments"] == 1
                           else "es"))
    _say(messages, "-" * 60)
    _mh.phase_advance()

    # ---- linkage record -------------------------------------------------
    crec = _lnk.read(cor_path, None)
    src_analysis = (crec or {}).get("analysis_id")
    analysis_id = src_analysis or _lnk.new_analysis_id()
    record = _lnk.bottleneck_record(
        analysis_id,
        source_corridor=cor_path,
        block_a=block_a_label, block_b=block_b_label,
        params={
            "boundary_step_m": bstep * unit_m,
            "simplify_tolerance_m": tol * unit_m,
            "n_stations": int(n_stations),
            "working_sr": working_sr.name,
            "corridor_where": corridor_where or None,
            "threshold_m": (float(threshold)
                            if threshold is not None else None),
        },
        stats=stats,
        source_analysis_id=src_analysis)

    # ---- feature-class outputs ------------------------------------------
    def _is_memory(path):
        p = str(path).replace("/", "\\").lower()
        return p.startswith("memory\\") or p.startswith("in_memory\\")

    def _fc(path, gtype):
        ws, name = os.path.split(str(path).rstrip("\\/"))
        if ws and not _is_memory(path) and not _mh.is_gdb_workspace(ws) \
                and not name.lower().endswith(".shp"):
            name += ".shp"
        arcpy.management.CreateFeatureclass(
            ws, name, gtype,
            spatial_reference=_mh.clean_sr(out_sr))
        return os.path.join(ws, name)

    def _stamp(path):
        """Record + metadata are skipped for memory workspaces, which
        carry no metadata (the interactive window's quick-look outputs;
        datasets SAVED from the window, or written by the GP tool to real
        workspaces, are stamped normally)."""
        return not _is_memory(path)

    def _line(coords):
        arr = arcpy.Array([arcpy.Point(float(x), float(y))
                           for x, y in coords])
        return arcpy.Polyline(arr, working_sr).projectAs(out_sr)

    written = {}
    if out_route:
        out_route = _fc(out_route, "POLYLINE")
        # one AddFields call, not one AddField per field: each AddField is
        # a full GP launch against the workspace catalog (standing rule)
        arcpy.management.AddFields(out_route, [
            ["Length_m", "DOUBLE", "", "", "", ""],
            ["MinW_m", "DOUBLE", "", "", "", ""],
            ["MeanW_m", "DOUBLE", "", "", "", ""]])
        with arcpy.da.InsertCursor(
                out_route,
                ["SHAPE@", "Length_m", "MinW_m", "MeanW_m"]) as cur:
            cur.insertRow((_line(path_xy), geod_len,
                           stats["min_width_m"], stats["mean_width_m"]))
        if _stamp(out_route):
            _lnk.write(out_route, record, messages)
            _tm.set_entity_and_fields(
                out_route,
                entity="Bottleneck Analysis widest route: the corridor "
                       "centerline route between the wildland blocks that "
                       "maximizes the minimum corridor width (maximin, on "
                       "the corridor's medial axis). " + _CD_CITATION,
                fields=[
                    {"name": "Length_m", "description":
                        "Geodesic route length in meters."},
                    {"name": "MinW_m", "description":
                        "The bottleneck: minimum corridor width along the "
                        "route, meters."},
                    {"name": "MeanW_m", "description":
                        "Mean corridor width along the route, meters."}],
                messages=messages)
        written["route"] = out_route
    if out_points:
        out_points = _fc(out_points, "POINT")
        arcpy.management.AddFields(out_points, [
            ["Station_m", "DOUBLE", "", "", "", ""],
            ["Width_m", "DOUBLE", "", "", "", ""],
            ["Below", "SHORT", "", "", "", ""]])
        with arcpy.da.InsertCursor(
                out_points,
                ["SHAPE@", "Station_m", "Width_m", "Below"]) as cur:
            for s, w, (x, y) in zip(stations_g, widths, pts):
                pt = arcpy.PointGeometry(
                    arcpy.Point(float(x), float(y)),
                    working_sr).projectAs(out_sr)
                flag = (-1 if threshold is None
                        else int(w < threshold))
                cur.insertRow((pt, float(s), float(w), flag))
        if _stamp(out_points):
            _lnk.write(out_points, record, messages)
        written["points"] = out_points
    if out_segments:
        if threshold is None or threshold <= 0:
            _warn(messages, "Segments output requested without a width "
                            "threshold -- skipped (segments are the "
                            "above/below-threshold pieces).")
        else:
            out_segments = _fc(out_segments, "POLYLINE")
            arcpy.management.AddFields(out_segments, [
                ["Class", "TEXT", "", 16, "", ""],
                ["Length_m", "DOUBLE", "", "", "", ""]])
            pieces = split_at_threshold(stations_g, widths, pts, threshold)
            with arcpy.da.InsertCursor(
                    out_segments,
                    ["SHAPE@", "Class", "Length_m"]) as cur:
                for below, coords, s0, s1 in pieces:
                    if len(coords) < 2:
                        continue
                    cur.insertRow((_line(coords),
                                   "Below" if below else "Above",
                                   float(s1 - s0)))
            if _stamp(out_segments):
                _lnk.write(out_segments, record, messages)
            written["segments"] = out_segments

    profile = {
        "stations_m": [float(v) for v in stations_g],
        "widths_m": [float(v) for v in widths],
        "route_xy_working": [[float(x), float(y)] for x, y in pts],
        "working_sr_wkt": working_sr.exportToString(),
        "stats": stats,
        # the full linkage record rides along so the interactive window
        # can stamp it onto datasets the user SAVES (its own quick-look
        # memory outputs carry no metadata)
        "record": record,
    }
    if profile_json:
        import json
        with open(profile_json, "w", encoding="utf-8") as fh:
            json.dump(profile, fh)
        _say(messages, "Profile written to {}".format(profile_json))
    _mh.end_phases()
    return record, profile
