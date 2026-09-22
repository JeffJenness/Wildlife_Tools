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

"""Centerline and Width -- collapse polygons to their centerlines,
carrying the local polygon WIDTH along every result.

A license-free replacement for (and extension of) Esri's Polygon To
Centerline (Topographic Production; Standard/Advanced + extension):
the polygon's MEDIAL AXIS is built exactly as in the suite's
Bottleneck Analysis (dense boundary sampling -> Voronoi -> interior
vertices and ridges, each vertex carrying its clearance = half the
local width), then either

* the MAIN CENTERLINE -- the longest path through the skeleton (its
  length-weighted diameter, found with the classic double Dijkstra
  sweep) -- or
* the FULL BRANCHED SKELETON, split into branch features at junctions,
  with short boundary-noise spurs pruned by a minimum branch length
  and each branch flagged whether it belongs to the main line.

Improvements over Esri's tool: any license level; HOLES are handled
natively (the skeleton simply routes around islands -- no Eliminate
Polygon Part destruction first); every output carries real WIDTH
attributes (min/mean/max per line, exact width at every station
point); the user transfers ANY of the source polygon's attribute
fields (not just an FID); true-curve inputs are densified; geographic
data runs in a feature-centered equidistant projection so widths and
lengths are honest meters; shapefile output is allowed.

Optional companion outputs: STATION POINTS (evenly spaced along each
centerline, each with its distance along the line and the exact
polygon width there -- the width profile), and SEGMENTS (the
centerline diced at those stations, each piece carrying its width
statistics -- ready for graduated-color width mapping).

Ends of the main centerline (and, in skeleton mode, every branch tip)
can be EXTENDED to the polygon boundary, so river centerlines reach
their mouths. Smoothing (optional) relaxes the raw Voronoi jaggedness
without letting the line leave the polygon.
"""
import math
import os

import numpy as np

import mahalanobis as _mh
import patch_analysis as _pa
import bottleneck_analysis as _ba
import repeating_shapes as _rs
import tool_metadata as _tm
import voronoi_tools as _vt

_say = _ba._say
_warn = _ba._warn
LINEAR_UNITS = _rs.LINEAR_UNITS
_LIN_M = _rs._LIN_M

MODE_TRUNK = "TRUNK"
MODE_SKELETON = "SKELETON"
MODES = ["Main centerline only",
         "Full branched skeleton (with tributary branches)"]


def canon_mode(s):
    if s in (MODE_TRUNK, MODE_SKELETON):
        return s
    t = str(s or "").strip().lower()
    if not t or t.startswith("main"):
        return MODE_TRUNK
    if t.startswith("full"):
        return MODE_SKELETON
    return None


# ---------------------------------------------------------------------------
#  pure graph work (numpy / scipy; unit tested)
# ---------------------------------------------------------------------------
def _components(n, edges):
    """Component label per vertex (scipy connected_components)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    g = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])),
                   shape=(n, n))
    return connected_components(g, directed=False)[1]


def trunk_path(verts, edges, comp_mask, clearance=None):
    """The main centerline of the skeleton restricted to ``comp_mask``
    vertices. The two ENDPOINTS come from the classic double Dijkstra
    sweep on LENGTH (the skeleton's diameter -- exact on trees, the
    medial axis of a hole-free polygon, and an excellent trunk with
    cycles). The ROUTE between them is then found with edge weights of
    length / clearance when ``clearance`` is given -- so where
    channels split and rejoin around an island, the centerline follows
    the WIDER channel (the same weighting as Bottleneck Analysis)
    instead of blindly taking the shorter side. Returns a vertex-index
    array."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra

    keep = comp_mask[edges[:, 0]] & comp_mask[edges[:, 1]]
    e = edges[keep]
    if not len(e):
        idx = np.flatnonzero(comp_mask)
        return idx[:1]
    n = len(verts)
    w = np.hypot(verts[e[:, 0], 0] - verts[e[:, 1], 0],
                 verts[e[:, 0], 1] - verts[e[:, 1], 1])
    g_len = coo_matrix((w, (e[:, 0], e[:, 1])),
                       shape=(n, n)).tocsr()

    def farthest(src, graph):
        dist = dijkstra(graph, directed=False, indices=src)
        dist[~comp_mask] = -np.inf
        dist[~np.isfinite(dist)] = -np.inf
        return int(np.argmax(dist))

    a = farthest(int(np.flatnonzero(comp_mask)[0]), g_len)
    b = farthest(a, g_len)

    if clearance is not None:
        ecl = np.minimum(clearance[e[:, 0]], clearance[e[:, 1]])
        g_route = coo_matrix((w / np.maximum(ecl, 1e-9),
                              (e[:, 0], e[:, 1])),
                             shape=(n, n)).tocsr()
    else:
        g_route = g_len
    _dist, pred = dijkstra(g_route, directed=False, indices=a,
                           return_predecessors=True)
    chain = [b]
    while pred[chain[-1]] >= 0:
        chain.append(int(pred[chain[-1]]))
    return np.asarray(chain[::-1], dtype=int)


def prune_spurs(verts, edges, min_len, protect=None):
    """Iteratively remove LEAF chains shorter than ``min_len`` that end
    at a junction (degree >= 3). Chains ending at another leaf (an
    isolated path -- e.g. the whole trunk) are never pruned; edges in
    ``protect`` (a bool mask over edges) are never pruned. Returns a
    keep-mask over the edges."""
    n = len(verts)
    keep = np.ones(len(edges), dtype=bool)
    if min_len is None or min_len <= 0:
        return keep
    if protect is None:
        protect = np.zeros(len(edges), dtype=bool)
    lengths = np.hypot(verts[edges[:, 0], 0] - verts[edges[:, 1], 0],
                       verts[edges[:, 0], 1] - verts[edges[:, 1], 1])
    for _round in range(1000):
        adj = [[] for _ in range(n)]
        for k in np.flatnonzero(keep):
            u, v = int(edges[k, 0]), int(edges[k, 1])
            adj[u].append((v, k))
            adj[v].append((u, k))
        deg = np.array([len(a) for a in adj])
        removed_any = False
        for leaf in np.flatnonzero(deg == 1):
            # walk from the leaf to the first non-degree-2 vertex
            chain_edges = []
            length = 0.0
            prev = -1
            cur = int(leaf)
            ok_junction = False
            while True:
                nxt = [(v, k) for v, k in adj[cur] if v != prev]
                if len(adj[cur]) >= 3:
                    ok_junction = True
                    break
                if not nxt:
                    break                     # ended at another leaf
                v, k = nxt[0]
                chain_edges.append(k)
                length += lengths[k]
                prev, cur = cur, v
                if length >= min_len:
                    break
            if ok_junction and length < min_len and chain_edges \
                    and not protect[chain_edges].any():
                keep[chain_edges] = False
                removed_any = True
        if not removed_any:
            break
    return keep


def chains_of(verts, edges):
    """Decompose the (pruned) skeleton into maximal chains between
    node points (degree != 2). Pure cycles (a donut's ring skeleton)
    come back as closed chains. Returns a list of vertex-index
    arrays."""
    n = len(verts)
    adj = [[] for _ in range(n)]
    for k, (u, v) in enumerate(edges):
        adj[int(u)].append((int(v), k))
        adj[int(v)].append((int(u), k))
    deg = np.array([len(a) for a in adj])
    used = np.zeros(len(edges), dtype=bool)
    chains = []

    def walk(start, v0, k0):
        chain = [start, v0]
        used[k0] = True
        prev, cur = start, v0
        while deg[cur] == 2:
            nxt = [(v, k) for v, k in adj[cur]
                   if not used[k]]
            if not nxt:
                break
            v, k = nxt[0]
            used[k] = True
            chain.append(v)
            prev, cur = cur, v
        return np.asarray(chain, dtype=int)

    for start in np.flatnonzero(deg != 2):
        for v0, k0 in adj[int(start)]:
            if not used[k0]:
                chains.append(walk(int(start), v0, k0))
    # anything left is a pure cycle (walks consume edges, so re-check)
    for k in np.flatnonzero(~used):
        if used[k]:
            continue
        u = int(edges[k, 0])
        chain = walk(u, int(edges[k, 1]), k)
        if chain[0] != chain[-1]:
            chain = np.concatenate([chain, chain[:1]])
        chains.append(chain)
    return chains


def smooth_chain(xy, window, poly_edges, bands):
    """Centered moving average along the chain (endpoints fixed),
    reverted wherever the smoothed vertex would leave the polygon."""
    m = len(xy)
    if m < 5 or window is None or window <= 0:
        return xy
    seg = np.hypot(*np.diff(xy, axis=0).T)
    mean_seg = float(seg.mean()) if len(seg) else 0.0
    if mean_seg <= 0:
        return xy
    k = int(round(window / mean_seg))
    k = max(1, min(k, (m - 2) // 2))
    if k < 1:
        return xy
    csum = np.cumsum(np.vstack([[0.0, 0.0], xy]), axis=0)
    out = xy.copy()
    for i in range(1, m - 1):
        lo = max(0, i - k)
        hi = min(m - 1, i + k)
        out[i] = (csum[hi + 1] - csum[lo]) / (hi + 1 - lo)
    inside = _pa.points_inside(out[1:-1], poly_edges, bands=bands)
    bad = np.flatnonzero(~inside) + 1
    out[bad] = xy[bad]
    return out


def extend_to_boundary(xy, poly_edges, tip_clearance, at_start):
    """Extend one end of the chain along its end tangent until it
    meets the polygon boundary; falls back to the nearest boundary
    point when the ray finds nothing within a sane reach. Returns the
    new endpoint, or None to leave the chain alone."""
    if len(xy) < 2:
        return None
    if at_start:
        p, q = xy[1], xy[0]
    else:
        p, q = xy[-2], xy[-1]
    d = q - p
    h = math.hypot(d[0], d[1])
    if h <= 0:
        return None
    d = d / h
    reach = max(4.0 * tip_clearance, 4.0 * h, 1e-9)
    # ray q + t*d against every boundary segment
    ax, ay = poly_edges[:, 0], poly_edges[:, 1]
    ex, ey = poly_edges[:, 2] - ax, poly_edges[:, 3] - ay
    denom = d[0] * ey - d[1] * ex
    ok = np.abs(denom) > 1e-300
    t = np.where(ok, ((ax - q[0]) * ey - (ay - q[1]) * ex)
                 / np.where(ok, denom, 1.0), np.inf)
    u = np.where(np.abs(ex) + np.abs(ey) > 0,
                 np.where(np.abs(ex) >= np.abs(ey),
                          (q[0] + t * d[0] - ax)
                          / np.where(ex == 0, 1.0, ex),
                          (q[1] + t * d[1] - ay)
                          / np.where(ey == 0, 1.0, ey)),
                 np.inf)
    hit = ok & (t > 1e-9) & (t <= reach) & (u >= -1e-9) & (u <= 1 + 1e-9)
    if hit.any():
        tbest = float(t[hit].min())
        return q + tbest * d
    # fallback: the nearest boundary point from the tip
    px, py = q
    seg2 = ex * ex + ey * ey
    seg2 = np.where(seg2 <= 0, 1.0, seg2)
    tt = np.clip(((px - ax) * ex + (py - ay) * ey) / seg2, 0.0, 1.0)
    qx = ax + tt * ex
    qy = ay + tt * ey
    d2 = (px - qx) ** 2 + (py - qy) ** 2
    j = int(np.argmin(d2))
    if d2[j] <= (reach * reach):
        return np.array([qx[j], qy[j]])
    return None


def station_chain(xy, spacing, n_min=2):
    """(stations, pts, cum) -- distances along the chain every
    ``spacing`` (always including both ends), the interpolated station
    coordinates, and the chain's cumulative vertex distances."""
    seg = np.hypot(*np.diff(xy, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if total <= 0:
        return np.zeros(1), xy[:1].copy(), cum
    n = max(n_min, int(math.ceil(total / spacing)) + 1)
    stations = np.linspace(0.0, total, n)
    idx = np.clip(np.searchsorted(cum, stations, side="right") - 1,
                  0, len(seg) - 1)
    frac = (stations - cum[idx]) / np.where(seg[idx] > 0, seg[idx], 1.0)
    pts = xy[idx] + frac[:, None] * (xy[idx + 1] - xy[idx])
    return stations, pts, cum


def slice_chain(xy, cum, s0, s1):
    """The chain's vertices between distances s0..s1, with exact
    interpolated endpoints."""
    seg = np.diff(cum)

    def at(s):
        i = int(np.clip(np.searchsorted(cum, s, side="right") - 1,
                        0, len(seg) - 1))
        f = (s - cum[i]) / (seg[i] if seg[i] > 0 else 1.0)
        return xy[i] + f * (xy[i + 1] - xy[i])

    inner = np.flatnonzero((cum > s0 + 1e-9) & (cum < s1 - 1e-9))
    coords = [at(s0)] + [xy[i] for i in inner] + [at(s1)]
    return np.asarray(coords)


# ---------------------------------------------------------------------------
#  attribute transfer: reserved/colliding names become Src_<name>
# ---------------------------------------------------------------------------
# names an output feature class owns or auto-manages -- a transferred
# field may not land under these, but its VALUES still transfer under
# a Src_ rename (Jeff, 2026-08-24: OBJECTID / Shape_Length /
# Shape_Area carry the SOURCE polygon's values, not silent drops)
_RESERVED_OUT = {"objectid", "oid", "fid", "shape", "shape_length",
                 "shape_area", "shape_leng", "globalid"}
_FIXED_OUT = {"line_id", "src_fid", "part", "istrunk", "length_m",
              "minw_m", "meanw_m", "maxw_m", "station_m", "width_m",
              "seg_id", "from_m", "to_m"}


def _transfer_plan(in_features, requested, any_shp, messages,
                   fixed_names=None):
    """[(source name, output name, esri type, text length)] for the
    requested transfer fields, in order. OID fields transfer as LONG;
    reserved names (OBJECTID, Shape_Length, ...) and names colliding
    with the CALLING tool's own output fields are renamed Src_<name>
    (truncated and uniquified for shapefiles' 10-character limit);
    geometry/blob/raster fields cannot transfer and are reported.
    ``fixed_names`` is the calling tool's own output-field set
    (lower-case); it defaults to the centerline tool's for
    backward compatibility -- every borrowing tool must pass its
    own, or transferred fields named like its outputs collide at
    AddFields time."""
    import arcpy

    if not requested:
        return []
    fixed = {str(n).lower() for n in fixed_names} \
        if fixed_names is not None else set(_FIXED_OUT)
    have = {f.name.lower(): f for f in arcpy.ListFields(in_features)}
    used = fixed | _RESERVED_OUT
    plan = []
    for name in requested:
        f = have.get(str(name).lower())
        if f is None:
            continue
        if f.type in ("Geometry", "Blob", "Raster"):
            _say(messages,
                 "Field '{}' is a {} field and cannot transfer as an "
                 "attribute -- skipped.".format(f.name,
                                                f.type.lower()))
            continue
        ftype = "LONG" if f.type == "OID" \
            else _vt._TYPE_MAP.get(f.type, "TEXT")
        out = f.name
        if out.lower() in _RESERVED_OUT \
                or out.lower() in fixed:
            out = "Src_" + f.name
        if any_shp:
            out = out[:10]
        base = out
        k = 1
        while out.lower() in used:
            k += 1
            tag = str(k)
            out = (base[:10 - len(tag)] if any_shp
                   else base) + tag
        used.add(out.lower())
        if out != f.name:
            _say(messages,
                 "Field '{}' transferred as '{}' (reserved or "
                 "conflicting name on the output).".format(f.name,
                                                           out))
        plan.append((f.name, out, ftype,
                     f.length if ftype == "TEXT" else ""))
    return plan


# ---------------------------------------------------------------------------
#  the tool
# ---------------------------------------------------------------------------
def run_centerline(in_features, out_lines, mode=None,
                   transfer_fields=None, min_branch=None,
                   boundary_step=None, smoothing=None, extend_ends=True,
                   out_points=None, station_spacing=None,
                   out_segments=None, linear_units="Meters",
                   messages=None):
    """Build centerlines (and widths) for every polygon in
    ``in_features`` (layer selections honored). All width/length
    FIELDS are meters; the ``linear_units`` apply to the four distance
    INPUTS (minimum branch length, detail, smoothing, station
    spacing). Returns the output line path."""
    import arcpy

    mode = canon_mode(mode)
    if mode is None:
        raise arcpy.ExecuteError("Unknown centerline mode.")
    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown distance unit: {}".format(linear_units))
    to_m = _LIN_M[linear_units]
    min_branch_m = float(min_branch) * to_m \
        if min_branch not in (None, "", 0) else None
    bstep_m = float(boundary_step) * to_m \
        if boundary_step not in (None, "", 0) else None
    smooth_m = float(smoothing) * to_m \
        if smoothing not in (None, "") else None
    spacing_m = float(station_spacing) * to_m \
        if station_spacing not in (None, "", 0) else None

    _mh.begin_phases([
        ("Reading the polygons...", 1),
        ("Building centerlines...", 5),
        ("Writing outputs...", 2),
    ])

    desc = arcpy.Describe(in_features)
    if getattr(desc, "shapeType", "") != "Polygon":
        raise arcpy.ExecuteError(
            "The input must be a polygon layer or feature class.")
    out_sr = desc.spatialReference
    if out_sr is None or getattr(out_sr, "name", "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "The input has no coordinate system.")

    def _is_shp_dest(path):
        if not path:
            return False
        p = str(path).replace("/", "\\").rstrip("\\")
        return bool(os.path.dirname(p)) \
            and not _mh.in_gdb_container(p)
    any_shp = any(_is_shp_dest(p) for p in (out_lines, out_points,
                                            out_segments))
    plan = _transfer_plan(in_features, transfer_fields, any_shp,
                          messages)
    fields = [p[0] for p in plan]          # source names, cursor order

    n_total, n_sel, has_sel = _mh.selection_info(in_features,
                                                 str(in_features))
    if has_sel:
        _say(messages, "The input layer has an active selection: {} of "
                       "{} polygons are used.".format(n_sel, n_total))

    # read everything up front (the standing vanishing-selection rule:
    # materialize before any other arcpy work)
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
    _say(messages, "{:,} polygon{} to process.".format(
        len(feats), "" if len(feats) == 1 else "s"))
    _mh.phase_advance()

    # ---- per-feature centerline construction -----------------------------
    # results: list of dicts per output LINE feature
    lines = []
    line_id = 0
    n_amb_cycles = 0
    for fi, (oid, geom, attrs) in enumerate(feats):
        working_sr, is_custom = _pa._working_sr(geom)
        try:
            unit_m = float(getattr(working_sr, "metersPerUnit", 1.0)
                           or 1.0)
        except Exception:                                        # noqa: BLE001
            unit_m = 1.0
        gw = geom.projectAs(working_sr)
        if getattr(gw, "hasCurves", False):
            d = max(gw.length / 500.0, 1e-9)
            try:
                gw = gw.densify("DISTANCE", d, d)
            except Exception:                                    # noqa: BLE001
                pass
        parts = _pa._explode(gw)
        for pi, part in enumerate(parts):
            ext = part.extent
            diag = math.hypot(ext.width, ext.height)
            if diag <= 0:
                continue
            part = _pa._densify(part, diag / 1000.0)
            part, ok = _pa.ensure_simple(part, working_sr)
            if not ok:
                _warn(messages,
                      "Polygon OID {}: geometry could not be fully "
                      "repaired; its centerline may be "
                      "affected.".format(oid))
            rings = _pa.rings_of(part)
            if not rings:
                continue
            poly_edges = _pa.edges_of(rings)
            bands = _pa.y_bands(poly_edges)
            bstep = (diag / 2000.0) if bstep_m is None \
                else max(1e-6, bstep_m / unit_m)
            try:
                verts, edges, clearance = _ba.build_medial_graph(
                    rings, bstep)
            except ValueError:
                _warn(messages,
                      "Polygon OID {}: too small to build a "
                      "centerline at this detail -- skipped.".format(
                          oid))
                continue
            if not len(edges):
                _warn(messages,
                      "Polygon OID {}: no interior skeleton (degenerate "
                      "or sliver polygon) -- skipped.".format(oid))
                continue
            comp = _components(len(verts), edges)
            counts = np.bincount(comp[np.unique(edges)])
            main_comp = int(np.argmax(counts))
            comp_mask = comp == main_comp
            n_comp = int((counts > 0).sum())
            if n_comp > 1:
                _warn(messages,
                      "Polygon OID {}: the skeleton splits into {} "
                      "pieces at this detail (pinch points thinner "
                      "than the boundary sampling) -- the largest "
                      "piece is used. A smaller Detail value keeps "
                      "thin necks connected.".format(oid, n_comp))

            trunk = trunk_path(verts, edges, comp_mask, clearance)
            trunk_edge = np.zeros(len(edges), dtype=bool)
            on_trunk = np.zeros(len(verts), dtype=bool)
            on_trunk[trunk] = True
            pos = {int(v): i for i, v in enumerate(trunk)}
            for k, (u, v) in enumerate(edges):
                pu, pv = pos.get(int(u)), pos.get(int(v))
                if pu is not None and pv is not None \
                        and abs(pu - pv) == 1:
                    trunk_edge[k] = True

            if mode == MODE_TRUNK:
                chain_list = [trunk] if len(trunk) >= 2 else []
                trunk_flags = [True] * len(chain_list)
            else:
                keep_e = edges[comp_mask[edges[:, 0]]
                               & comp_mask[edges[:, 1]]]
                keep_te = trunk_edge[comp_mask[edges[:, 0]]
                                     & comp_mask[edges[:, 1]]]
                default_prune = 2.0 * bstep if min_branch_m is None \
                    else min_branch_m / unit_m
                keep_mask = prune_spurs(verts, keep_e, default_prune,
                                        protect=keep_te)
                pruned = keep_e[keep_mask]
                pruned_te = keep_te[keep_mask]
                chain_list = chains_of(verts, pruned)
                te_set = {tuple(sorted(e)) for e in
                          pruned[pruned_te].tolist()}
                trunk_flags = []
                for ch in chain_list:
                    pairs = {tuple(sorted((int(a), int(b))))
                             for a, b in zip(ch[:-1], ch[1:])}
                    trunk_flags.append(bool(pairs & te_set))

            # vertex degrees ONCE per part, from the edge set the
            # chains were actually built from -- in skeleton mode the
            # PRUNED set, so branch tips exposed by spur pruning
            # count as free tips (degree 1) and extend properly
            deg_end = None
            if extend_ends:
                deg_src = edges if mode == MODE_TRUNK else pruned
                deg_end = np.bincount(
                    np.asarray(deg_src, dtype=np.int64).ravel(),
                    minlength=len(verts))

            for ch, is_trunk in zip(chain_list, trunk_flags):
                if len(ch) < 2:
                    continue
                xy = verts[ch].copy()
                closed = bool(ch[0] == ch[-1])
                if closed and mode == MODE_SKELETON:
                    n_amb_cycles += 1
                sm = (2.0 * bstep) if smooth_m is None \
                    else smooth_m / unit_m
                if sm > 0 and not closed:
                    xy = smooth_chain(xy, sm, poly_edges, bands)
                ext0 = ext1 = 0.0
                if extend_ends and not closed:
                    # only free tips (chain ends that are skeleton
                    # leaves) get extended -- junction ends stay put
                    for at_start, vidx in ((True, int(ch[0])),
                                           (False, int(ch[-1]))):
                        if deg_end[vidx] != 1:
                            continue
                        tip = extend_to_boundary(
                            xy, poly_edges, float(clearance[vidx]),
                            at_start)
                        if tip is not None:
                            if at_start:
                                ext0 = math.hypot(tip[0] - xy[0, 0],
                                                  tip[1] - xy[0, 1])
                                xy = np.vstack([tip[None, :], xy])
                            else:
                                ext1 = math.hypot(tip[0] - xy[-1, 0],
                                                  tip[1] - xy[-1, 1])
                                xy = np.vstack([xy, tip[None, :]])
                lines.append({
                    "oid": oid, "part": pi + 1, "attrs": attrs,
                    "xy": xy, "closed": closed,
                    "is_trunk": bool(is_trunk),
                    "working_sr": working_sr, "unit_m": unit_m,
                    "poly_edges": poly_edges,
                    # width statistics exclude the boundary-extension
                    # stubs, whose widths taper to zero AT the boundary
                    "ext0": ext0, "ext1": ext1,
                })
        _mh.phase_step(fi + 1, len(feats))
    if not lines:
        raise arcpy.ExecuteError(
            "No centerlines could be built from the input polygons.")
    if n_amb_cycles:
        _say(messages,
             "{} closed ring branch{} (skeletons circling a hole) "
             "written as closed lines.".format(
                 n_amb_cycles, "" if n_amb_cycles == 1 else "es"))
    _mh.phase_advance()

    # ---- measure + write -------------------------------------------------
    out_names = [p[1] for p in plan]
    xfer_rows = [[out, ftype,
                  ("Source field " + src_name) if out != src_name
                  else src_name, flen, "", ""]
                 for src_name, out, ftype, flen in plan]

    def _fc(path, gtype):
        p = str(path).replace("/", "\\").rstrip("\\")
        ws, name = os.path.split(p)
        if ws and not _mh.in_gdb_container(p) \
                and not name.lower().endswith(".shp"):
            name += ".shp"
        res = arcpy.management.CreateFeatureclass(
            ws, name, gtype, spatial_reference=_vt.clean_sr(out_sr))
        return res.getOutput(0)

    out_lines = _fc(out_lines, "POLYLINE")
    arcpy.management.AddFields(out_lines, [
        ["Line_ID", "LONG", "Centerline ID", "", "", ""],
        ["Src_FID", "LONG", "Source polygon Object ID", "", "", ""],
        ["Part", "LONG", "Source polygon part", "", "", ""],
        ["IsTrunk", "SHORT", "1 = on the main centerline", "", "", ""],
        ["Length_m", "DOUBLE", "Geodesic length (m)", "", "", ""],
        ["MinW_m", "DOUBLE", "Minimum width (m)", "", "", ""],
        ["MeanW_m", "DOUBLE", "Mean width (m)", "", "", ""],
        ["MaxW_m", "DOUBLE", "Maximum width (m)", "", "", ""]]
        + xfer_rows)
    pts_path = seg_path = None
    if out_points:
        pts_path = _fc(out_points, "POINT")
        arcpy.management.AddFields(pts_path, [
            ["Line_ID", "LONG", "Centerline ID", "", "", ""],
            ["Src_FID", "LONG", "Source polygon Object ID", "", "", ""],
            ["Station_m", "DOUBLE", "Distance along the line (m)",
             "", "", ""],
            ["Width_m", "DOUBLE", "Polygon width here (m)", "", "", ""]]
            + xfer_rows)
    if out_segments:
        seg_path = _fc(out_segments, "POLYLINE")
        arcpy.management.AddFields(seg_path, [
            ["Line_ID", "LONG", "Centerline ID", "", "", ""],
            ["Src_FID", "LONG", "Source polygon Object ID", "", "", ""],
            ["Seg_ID", "LONG", "Segment sequence", "", "", ""],
            ["From_m", "DOUBLE", "Segment start station (m)", "", "", ""],
            ["To_m", "DOUBLE", "Segment end station (m)", "", "", ""],
            ["MinW_m", "DOUBLE", "Minimum width (m)", "", "", ""],
            ["MeanW_m", "DOUBLE", "Mean width (m)", "", "", ""],
            ["MaxW_m", "DOUBLE", "Maximum width (m)", "", "", ""]]
            + xfer_rows)

    def _geo(coords, working_sr, gtype="line"):
        if gtype == "line":
            arr = arcpy.Array([arcpy.Point(float(x), float(y))
                               for x, y in coords])
            return arcpy.Polyline(arr, working_sr).projectAs(out_sr)
        return arcpy.PointGeometry(
            arcpy.Point(float(coords[0]), float(coords[1])),
            working_sr).projectAs(out_sr)

    line_cols = ["SHAPE@", "Line_ID", "Src_FID", "Part", "IsTrunk",
                 "Length_m", "MinW_m", "MeanW_m",
                 "MaxW_m"] + out_names
    pt_cols = ["SHAPE@", "Line_ID", "Src_FID", "Station_m",
               "Width_m"] + out_names
    seg_cols = ["SHAPE@", "Line_ID", "Src_FID", "Seg_ID", "From_m",
                "To_m", "MinW_m", "MeanW_m", "MaxW_m"] + out_names

    # default station spacing: the MAIN CENTERLINE's length / 100,
    # per polygon part, applied as the same ABSOLUTE spacing to every
    # branch of that part -- uniform station density across the
    # skeleton instead of 100 stations on every tiny spur (Jeff's
    # 2026-08-24 finding: 3,909 branches x 100 stations = 395k points
    # and a 7-minute write)
    trunk_len = {}
    for rec in lines:
        key = (rec["oid"], rec["part"])
        seg = np.hypot(*np.diff(rec["xy"], axis=0).T)
        t = float(seg.sum())
        trunk_len[key] = trunk_len.get(key, 0.0) \
            + (t if rec["is_trunk"] else 0.0)
    # a file geodatabase allows only ONE open write transaction, so the
    # three outputs are buffered and written one cursor at a time
    line_rows, pt_rows, seg_rows = [], [], []
    for rec in lines:
        line_id += 1
        xy = rec["xy"]
        unit_m = rec["unit_m"]
        wsr = rec["working_sr"]
        pe = rec["poly_edges"]
        seg = np.hypot(*np.diff(xy, axis=0).T)
        total = float(seg.sum())
        if total <= 0:
            continue
        if spacing_m:
            sp = spacing_m / unit_m
        else:
            base = trunk_len.get((rec["oid"], rec["part"]), 0.0)
            sp = max((base if base > 0 else total) / 100.0, 1e-9)
        stations, pts, cum = station_chain(xy, sp)
        widths = 2.0 * _ba.dist_to_edges(pts, pe) * unit_m
        geod_len = _pa._geodesic_m(xy, wsr)
        scale = (geod_len / total) if total > 0 else 1.0
        stations_g = stations * scale
        core = (stations >= rec["ext0"] - 1e-9) \
            & (stations <= total - rec["ext1"] + 1e-9)
        wstat = widths[core] if core.any() else widths
        line_rows.append(tuple(
            [_geo(xy, wsr), line_id, rec["oid"], rec["part"],
             int(rec["is_trunk"]), float(geod_len),
             float(wstat.min()), float(wstat.mean()),
             float(wstat.max())] + list(rec["attrs"])))
        if pts_path:
            for s, w, p in zip(stations_g, widths, pts):
                pt_rows.append(tuple(
                    [_geo(p, wsr, "point"), line_id, rec["oid"],
                     float(s), float(w)] + list(rec["attrs"])))
        if seg_path:
            for si in range(len(stations) - 1):
                piece = slice_chain(xy, cum, float(stations[si]),
                                    float(stations[si + 1]))
                if len(piece) < 2:
                    continue
                w3 = 2.0 * _ba.dist_to_edges(
                    np.vstack([piece[0], piece[len(piece) // 2],
                               piece[-1]]), pe) * unit_m
                seg_rows.append(tuple(
                    [_geo(piece, wsr), line_id, rec["oid"],
                     si + 1, float(stations_g[si]),
                     float(stations_g[si + 1]), float(w3.min()),
                     float(w3.mean()), float(w3.max())]
                    + list(rec["attrs"])))

    with arcpy.da.InsertCursor(out_lines, line_cols) as cur:
        for row in line_rows:
            cur.insertRow(row)
    if pts_path:
        with arcpy.da.InsertCursor(pts_path, pt_cols) as cur:
            for row in pt_rows:
                cur.insertRow(row)
    if seg_path:
        with arcpy.da.InsertCursor(seg_path, seg_cols) as cur:
            for row in seg_rows:
                cur.insertRow(row)
    n_lines = len(line_rows)
    n_pts = len(pt_rows)
    n_segs = len(seg_rows)

    _say(messages, "{:,} centerline{} written{}{}.".format(
        n_lines, "" if n_lines == 1 else "s",
        "" if not n_pts else "; {:,} station points".format(n_pts),
        "" if not n_segs else "; {:,} segments".format(n_segs)))

    # ---- provenance ------------------------------------------------------
    for path, what in ((out_lines, "centerlines"),
                       (pts_path, "width station points"),
                       (seg_path, "width segments")):
        if not path:
            continue
        try:
            md = arcpy.metadata.Metadata(path)
            md.title = os.path.basename(str(path))
            md.summary = (
                "Created by Centerline and Width: {} of {} ({}) from "
                "{}.".format(
                    what,
                    "the main centerline" if mode == MODE_TRUNK
                    else "the full branched skeleton",
                    "widths in meters",
                    os.path.basename(str(
                        getattr(desc, "catalogPath", in_features)))))
            md.description = (
                "Polygon centerlines from the Jenness Enterprises "
                "Centerline and Width tool: the polygon's medial axis "
                "(dense boundary sampling, Voronoi diagram, interior "
                "vertices and ridges), reduced to its length-weighted "
                "longest path (the main centerline) or decomposed "
                "into branch features at junctions with short spurs "
                "pruned. Every line carries minimum / mean / maximum "
                "polygon width in meters; station points carry the "
                "exact width at each station; segments carry width "
                "statistics per piece. Holes are handled natively "
                "(the skeleton routes around islands). Geographic "
                "inputs are processed in a feature-centered "
                "equidistant projection, so widths and lengths are "
                "true meters.")
            md.save()
        except Exception:                                        # noqa: BLE001
            pass
    _tm.set_entity_and_fields(
        out_lines,
        entity="Polygon centerlines with width statistics (medial "
               "axis; meters).",
        fields=[
            {"name": "Line_ID", "description":
                "Centerline ID, shared with the station-point and "
                "segment outputs."},
            {"name": "Src_FID", "description":
                "Object ID of the source polygon."},
            {"name": "Part", "description":
                "Part number within a multipart source polygon."},
            {"name": "IsTrunk", "description":
                "1 when the line lies on the main centerline (the "
                "skeleton's longest path); 0 for tributary branches."},
            {"name": "Length_m", "description":
                "Geodesic line length in meters."},
            {"name": "MinW_m", "description":
                "Minimum polygon width along the line, meters."},
            {"name": "MeanW_m", "description":
                "Mean polygon width along the line, meters."},
            {"name": "MaxW_m", "description":
                "Maximum polygon width along the line, meters."}],
        messages=messages)
    _mh.end_phases()
    return out_lines
