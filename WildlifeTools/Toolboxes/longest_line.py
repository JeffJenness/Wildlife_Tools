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

"""Longest Line in Polygon (Fetch) -- the longest straight line that
fits entirely inside each polygon, and directional fetch from station
points.

A modernized port of Jeff Jenness's ArcView 3.x "Longest Straight
Line" extension (longline.avx): when a polygon is a body of water,
the longest interior straight line is the FETCH -- the longest
straight path over which wind can raise waves. The classic algorithm
is kept -- the optimal line must pass through two polygon vertices
(not necessarily as its endpoints; it may extend past them to the
boundary), holes block the line, and each feature reports its line's
endpoints, length and azimuth -- but the vertex-pair search is
rebuilt on pure vectorized geometry:

* every candidate pair gets a cheap UPPER BOUND -- the chord of the
  polygon's convex hull along the pair's line (the hull has few
  vertices, so millions of bounds vectorize in seconds);
* candidates are visited in descending bound order, each fully
  clipped against every edge only until the best interior length
  found exceeds the next candidate's bound -- an exact early stop.

The full clip is robust to grazing contacts: all edge crossings
partition the line, the midpoint of every gap is tested for
containment, and consecutive interior gaps (separated by a touch at
a convex vertex) merge into one free run.

DIRECTIONAL FETCH FROM STATIONS: rays are cast from each station
point at a fixed bearing increment, each terminated at the first
shoreline crossing or an optional maximum fetch distance (beyond
which sea conditions are effectively fully developed). Stations that
fall outside the polygon (on land) are nudged to the nearest interior
point, with the shift distance and bearing recorded -- the behavior
of the tool's 2014 ancestor, the UOGML Wave Energy extension
(Jenness 2014, for the University of Guam Marine Laboratory,
implementing Ekebom et al. 2003), whose geometric core this mode
generalizes. For geographic data every station gets its OWN
azimuthal-equidistant projection, so radial distances from the
station are TRUE geodesic distances and bearings are true azimuths.

GEOGRAPHIC polygons (longest-line mode) are solved in a
feature-centered azimuthal-equidistant projection with the final
length reported geodesically; very large features draw a warning that
a planar solve may err by a fraction of a percent (the spherical
great-circle mode is a planned follow-up).
"""
import math
import clip_tools as _ct
import os

import numpy as np

import mahalanobis as _mh
import patch_analysis as _pa
import centerline_width as _cw
import repeating_shapes as _rs
import voronoi_tools as _vt

_say = _cw._say
_warn = _cw._warn
LINEAR_UNITS = _rs.LINEAR_UNITS
_LIN_M = _rs._LIN_M

# planar-solve accuracy warning threshold for geographic features
_BIG_EXTENT_M = 1.2e6


# ---------------------------------------------------------------------------
#  pure geometry (numpy / scipy; unit tested without arcpy)
# ---------------------------------------------------------------------------
def _ring_vertices(rings):
    """Unique-ish vertex array from the rings (closing duplicates
    dropped)."""
    out = []
    for r in rings:
        a = np.asarray(r, dtype=np.float64)[:, :2]
        if len(a) >= 2 and np.allclose(a[0], a[-1]):
            a = a[:-1]
        out.append(a)
    v = np.vstack(out)
    return np.unique(np.round(v, 9), axis=0)


def _line_edge_ts(p, d, edges, tol):
    """Sorted, deduped parameters t where the infinite line p + t*d
    crosses (or touches) the edge set."""
    ax, ay = edges[:, 0], edges[:, 1]
    ex, ey = edges[:, 2] - ax, edges[:, 3] - ay
    denom = d[0] * ey - d[1] * ex
    ok = np.abs(denom) > 1e-300
    t = ((ax - p[0]) * ey - (ay - p[1]) * ex) \
        / np.where(ok, denom, 1.0)
    u = ((ax - p[0]) * d[1] - (ay - p[1]) * d[0]) \
        / np.where(ok, denom, 1.0)
    hit = ok & (u >= -1e-9) & (u <= 1.0 + 1e-9)
    ts = np.sort(t[hit])
    if not len(ts):
        return ts
    keep = np.concatenate([[True], np.diff(ts) > tol])
    return ts[keep]


def _free_run(p, d, t_a, t_b, edges, bands, tol):
    """The maximal interior run of the line p + t*d that contains the
    sub-segment [t_a, t_b], or None when that sub-segment is not
    interior. Returns (t_lo, t_hi)."""
    ts = _line_edge_ts(p, d, edges, tol)
    if len(ts) < 2:
        return None
    mids_t = 0.5 * (ts[:-1] + ts[1:])
    mids = p[None, :] + mids_t[:, None] * d[None, :]
    inside = _pa.points_inside(mids, edges, bands=bands)
    t_mid = 0.5 * (t_a + t_b)
    k = int(np.searchsorted(ts, t_mid)) - 1
    if k < 0 or k >= len(inside) or not inside[k]:
        return None
    lo = k
    while lo - 1 >= 0 and inside[lo - 1]:
        lo -= 1
    hi = k
    while hi + 1 < len(inside) and inside[hi + 1]:
        hi += 1
    t_lo, t_hi = float(ts[lo]), float(ts[hi + 1])
    if t_lo > min(t_a, t_b) + tol or t_hi < max(t_a, t_b) - tol:
        return None                      # run does not cover the pair
    return t_lo, t_hi


def longest_interior_line(rings, messages=None, label="",
                          progress=None):
    """(p1, p2, length) of the longest straight line fully inside the
    polygon given by ``rings`` (outer ring(s) plus holes), or None for
    degenerate input. Pure numpy + scipy; no arcpy.

    ``progress``, when given, is called as progress(stage, done,
    total) with stage "screening" (phase 1, over all vertex pairs)
    then "verifying" (phase 2, over the surviving extension
    candidates) -- the caller throttles and displays it."""
    from scipy.spatial import ConvexHull

    verts = _ring_vertices(rings)
    n = len(verts)
    if n < 3:
        return None
    edges = _pa.edges_of(rings)
    bands = _pa.y_bands(edges)
    span = max(float(np.ptp(verts[:, 0])),
               float(np.ptp(verts[:, 1])), 1e-12)
    tol = span * 1e-9

    # center the coordinates before qhull (large absolute offsets --
    # UTM eastings/northings -- degrade its precision); the returned
    # indices map straight back into the raw vertex array
    ctr = verts.mean(axis=0)
    try:
        hull = ConvexHull(verts - ctr)
        hv = verts[hull.vertices]
        hull_edges = np.column_stack([hv, np.roll(hv, -1, axis=0)])
        hull_ok = True
    except Exception:                                            # noqa: BLE001
        # no hull (collinear / degenerate input): fabricated hull
        # edges would give a WRONG upper bound, so phase 2 runs
        # without pruning instead
        hull_edges = None
        hull_ok = False

    def clip(i, j):
        p = verts[i]
        q = verts[j]
        seg = q - p
        h = math.hypot(seg[0], seg[1])
        if h <= tol:
            return None
        d = seg / h
        run = _free_run(p, d, 0.0, h, edges, bands, tol)
        if run is None:
            return None
        t_lo, t_hi = run
        return (p + t_lo * d, p + t_hi * d, t_hi - t_lo)

    if n > 8000:
        _say(messages,
             "{}: {:,} vertices -- the candidate search is "
             "quadratic; consider simplifying the polygon if this "
             "runs long.".format(label or "polygon", n))

    # pair blocks: (ii, jj) int32 index arrays covering all vertex
    # pairs a bounded slab at a time -- materializing every pair at
    # once costs O(n^2) memory (30k vertices = 450M pairs = many GB)
    def pair_blocks(target=24_000_000):
        rows = max(1, int(target // max(n, 1)))
        for r0 in range(0, n, rows):
            r1 = min(r0 + rows, n)
            ii_r = np.repeat(np.arange(r0, r1, dtype=np.int32),
                             n - 1 - np.arange(r0, r1))
            jj_r = np.concatenate(
                [np.arange(i + 1, n, dtype=np.int32)
                 for i in range(r0, r1)]) if r1 > r0 else \
                np.zeros(0, dtype=np.int32)
            if len(ii_r):
                yield ii_r, jj_r

    def screen_batch(sel_i, sel_j, sel_d, best, best_line):
        """The phase-1 chord screen over one descending-length
        batch list; returns the updated (best, best_line)."""
        B = 2048
        for b0 in range(0, len(sel_i), B):
            if sel_d[b0] <= best:
                break
            bi = sel_i[b0:b0 + B]
            bj = sel_j[b0:b0 + B]
            P = verts[bi]
            Q = verts[bj]
            D = Q - P
            denom = D[:, 0][:, None] * ey_[None, :] \
                - D[:, 1][:, None] * ex_[None, :]
            okd = np.abs(denom) > 1e-300
            tt = ((ax_[None, :] - P[:, 0][:, None]) * ey_[None, :]
                  - (ay_[None, :] - P[:, 1][:, None]) * ex_[None, :]) \
                / np.where(okd, denom, 1.0)
            uu = ((ax_[None, :] - P[:, 0][:, None])
                  * D[:, 1][:, None]
                  - (ay_[None, :] - P[:, 1][:, None])
                  * D[:, 0][:, None]) / np.where(okd, denom, 1.0)
            crosses = (okd & (tt > 1e-7) & (tt < 1 - 1e-7)
                       & (uu > 1e-7) & (uu < 1 - 1e-7)).any(axis=1)
            mids = 0.5 * (P + Q)
            m_in = _pa.points_inside(mids, edges, bands=bands)
            good = ~crosses & m_in
            for k in np.flatnonzero(good):
                if sel_d[b0 + k] <= best:
                    break
                got = clip(int(bi[k]), int(bj[k]))
                if got is not None and got[2] > best:
                    best = got[2]
                    best_line = got
        return best, best_line

    # ---- phase 1: the longest VALID CHORD establishes a strong
    # lower bound fast. Pairs are visited in descending vertex-pair
    # distance (in ~3% length bands gathered blockwise, so memory
    # stays bounded); a vectorized screen drops pairs whose chord
    # strictly crosses any edge, and survivors get the exact clip.
    # Once the band ceiling falls below the best exact length, no
    # remaining CHORD can win and phase 2 handles extensions.
    best = 0.0
    best_line = None
    ax_, ay_ = edges[:, 0], edges[:, 1]
    ex_, ey_ = edges[:, 2] - ax_, edges[:, 3] - ay_
    max_len = 0.0
    for ii, jj in pair_blocks():
        d = np.hypot(verts[jj, 0] - verts[ii, 0],
                     verts[jj, 1] - verts[ii, 1])
        if len(d):
            max_len = max(max_len, float(d.max()))
    n_pairs = n * (n - 1) // 2
    done_pairs = 0
    hi = max_len
    while hi > best and hi > tol:
        lo = hi * 0.97
        gi, gj, gd = [], [], []
        for ii, jj in pair_blocks():
            d = np.hypot(verts[jj, 0] - verts[ii, 0],
                         verts[jj, 1] - verts[ii, 1]) \
                .astype(np.float32)
            m = (d > lo) & (d <= hi * (1 + 1e-12))
            if m.any():
                gi.append(ii[m])
                gj.append(jj[m])
                gd.append(d[m])
        if gi:
            gi = np.concatenate(gi)
            gj = np.concatenate(gj)
            gd = np.concatenate(gd)
            order = np.argsort(-gd)
            if progress is not None:
                progress("screening", done_pairs, n_pairs)
            done_pairs += len(order)
            best, best_line = screen_batch(
                gi[order], gj[order], gd[order], best, best_line)
        hi = lo

    # ---- phase 2: extension candidates. Upper bounds for every
    # pair (the convex-hull chord along the pair's line), computed
    # blockwise in hull-size-budgeted chunks, keeping only pairs
    # that can beat phase 1, visited in descending order with an
    # exact early stop.
    if not hull_ok:
        # no certified bound exists -- exhaustively clip every pair
        # (degenerate inputs only, so n is small in practice)
        done_pairs = 0
        for ii_b, jj_b in pair_blocks():
            for k in range(len(ii_b)):
                if progress is not None and done_pairs % 512 == 0:
                    progress("verifying", done_pairs, n_pairs)
                done_pairs += 1
                got = clip(int(ii_b[k]), int(jj_b[k]))
                if got is not None and got[2] > best:
                    best = got[2]
                    best_line = got
        return best_line
    cand_i, cand_j, cand_ub = [], [], []
    hx0, hy0 = hull_edges[:, 0], hull_edges[:, 1]
    hex_, hey = hull_edges[:, 2] - hx0, hull_edges[:, 3] - hy0
    # chunk sized so the (CH, H) float64 temporaries stay ~100 MB
    CH = max(1024, int(6e7 // max(len(hull_edges), 1) // 8))
    done_pairs = 0
    for ii_b, jj_b in pair_blocks():
        for c0 in range(0, len(ii_b), CH):
            if progress is not None:
                progress("bounding", done_pairs, n_pairs)
            ii = ii_b[c0:c0 + CH]
            jj = jj_b[c0:c0 + CH]
            done_pairs += len(ii)
            P = verts[ii]
            D = verts[jj] - P
            h = np.hypot(D[:, 0], D[:, 1])
            good = h > tol
            Dn = D / np.maximum(h, tol)[:, None]
            # intersect each line with every hull edge
            denom = Dn[:, 0][:, None] * hey[None, :] \
                - Dn[:, 1][:, None] * hex_[None, :]
            okd = np.abs(denom) > 1e-300
            tt = ((hx0[None, :] - P[:, 0][:, None]) * hey[None, :]
                  - (hy0[None, :] - P[:, 1][:, None])
                  * hex_[None, :]) / np.where(okd, denom, 1.0)
            uu = ((hx0[None, :] - P[:, 0][:, None])
                  * Dn[:, 1][:, None]
                  - (hy0[None, :] - P[:, 1][:, None])
                  * Dn[:, 0][:, None]) / np.where(okd, denom, 1.0)
            hit = okd & (uu >= -1e-9) & (uu <= 1.0 + 1e-9)
            tmax = np.where(hit, tt, -np.inf).max(axis=1)
            tmin = np.where(hit, tt, np.inf).min(axis=1)
            ub = np.where(np.isfinite(tmax) & np.isfinite(tmin),
                          tmax - tmin, 0.0)
            # pad the bound past float roundoff so it is CERTIFIED
            # (an under-estimated bound would prune the true optimum)
            ub = ub * (1.0 + 1e-9) + tol
            keep = good & (ub > best)
            if keep.any():
                cand_i.append(ii[keep])
                cand_j.append(jj[keep])
                cand_ub.append(ub[keep])
    if cand_i:
        ci = np.concatenate(cand_i)
        cj = np.concatenate(cand_j)
        cu = np.concatenate(cand_ub)
        order = np.argsort(-cu)
        n_cand = len(order)
        if messages is not None and n_cand > 10000:
            _say(messages,
                 "{}: {:,} extension candidates survive the "
                 "screen.".format(label or "polygon", n_cand))
        for kk, k in enumerate(order):
            if progress is not None and kk % 64 == 0:
                progress("verifying", kk, n_cand)
            if cu[k] <= best:
                break                    # exact early stop
            got = clip(int(ci[k]), int(cj[k]))
            if got is not None and got[2] > best:
                best = got[2]
                best_line = got
    if best_line is None:
        return None
    return best_line


def radial_fetch(px, py, bearings_deg, edges, max_dist):
    """Fetch distance along each bearing (azimuth degrees, clockwise
    from north) from the station (px, py): the distance to the first
    edge crossing, capped at ``max_dist``. Returns (dists, capped)."""
    b = np.radians(np.asarray(bearings_deg, dtype=np.float64))
    dx = np.sin(b)
    dy = np.cos(b)
    ax, ay = edges[:, 0], edges[:, 1]
    ex, ey = edges[:, 2] - ax, edges[:, 3] - ay
    denom = dx[:, None] * ey[None, :] - dy[:, None] * ex[None, :]
    ok = np.abs(denom) > 1e-300
    t = ((ax[None, :] - px) * ey[None, :]
         - (ay[None, :] - py) * ex[None, :]) \
        / np.where(ok, denom, 1.0)
    u = ((ax[None, :] - px) * dy[:, None]
         - (ay[None, :] - py) * dx[:, None]) \
        / np.where(ok, denom, 1.0)
    hit = ok & (u >= -1e-9) & (u <= 1.0 + 1e-9) & (t > 1e-9)
    t = np.where(hit, t, np.inf)
    d = t.min(axis=1)
    capped = ~np.isfinite(d) if max_dist is None \
        else (d > max_dist)
    if max_dist is not None:
        d = np.minimum(d, max_dist)
    d[~np.isfinite(d)] = 0.0 if max_dist is None else max_dist
    return d, capped


def longest_through_point(px, py, edges, bands, max_reach=None,
                          verts=None):
    """The EXACT longest chord through the interior point (px, py)
    and the exact longest single ray from it. By the same convexity
    argument as the two-vertex theorem (each reach is p/cos(t-a),
    convex while its touching edge is fixed), the optimum must touch
    a polygon VERTEX -- so the candidate directions are simply the
    directions from the point to every vertex (Jeff's observation,
    2026-08-24). Reaches are optionally capped at ``max_reach`` per
    side. ``verts``, when given, is the precomputed unique boundary
    vertex array for ``edges`` (callers looping over many stations
    extract it once instead of per call). Returns
    ((chord_len, chord_az, c_lo, c_hi),
    (ray_len, ray_az, r_end)) in the local frame."""
    if verts is None:
        verts = np.unique(np.round(np.vstack([edges[:, :2],
                                              edges[:, 2:]]), 9),
                          axis=0)
    span = max(float(np.ptp(edges[:, [0, 2]])),
               float(np.ptp(edges[:, [1, 3]])), 1e-12)
    tol = span * 1e-9
    p = np.array([px, py], dtype=np.float64)
    best_c = (0.0, 0.0, p.copy(), p.copy())
    best_r = (0.0, 0.0, p.copy())
    for v in verts:
        d = v - p
        h = math.hypot(d[0], d[1])
        if h <= tol:
            continue
        d = d / h
        run = _free_run(p, d, 0.0, 0.0, edges, bands, tol)
        if run is None:
            continue
        t_lo, t_hi = run
        if max_reach is not None:
            t_lo = max(t_lo, -max_reach)
            t_hi = min(t_hi, max_reach)
        az = math.degrees(math.atan2(d[0], d[1])) % 360.0
        if t_hi - t_lo > best_c[0]:
            best_c = (t_hi - t_lo, az, p + t_lo * d, p + t_hi * d)
        if t_hi > best_r[0]:
            best_r = (t_hi, az, p + t_hi * d)
    return best_c, best_r


def nudge_inside(px, py, edges, bands, span, units_per_m=1.0):
    """The nearest interior point to (px, py), for stations that fall
    on land: project to the closest edge, then step a whisker to the
    interior side. ``units_per_m`` scales the ~1 cm whisker floor
    into the edges' units (degrees for geographic data -- an
    absolute 0.01 floor there would be a 1.1 km whisker). Returns
    (x, y, shifted)."""
    if _pa.points_inside(np.array([[px, py]]), edges,
                         bands=bands)[0]:
        return px, py, False
    ax, ay = edges[:, 0], edges[:, 1]
    ex, ey = edges[:, 2] - ax, edges[:, 3] - ay
    L2 = ex * ex + ey * ey
    L2 = np.where(L2 <= 0, 1.0, L2)
    tt = np.clip(((px - ax) * ex + (py - ay) * ey) / L2, 0.0, 1.0)
    qx = ax + tt * ex
    qy = ay + tt * ey
    d2 = (px - qx) ** 2 + (py - qy) ** 2
    k = int(np.argmin(d2))
    nx, ny = float(qx[k]), float(qy[k])
    eps = max(span * 1e-7, 0.01 * units_per_m)
    # try both sides of the edge normal, keep the interior one
    nrm = np.array([-ey[k], ex[k]])
    h = math.hypot(nrm[0], nrm[1])
    if h > 0:
        nrm = nrm / h
        for s in (eps, -eps, 4 * eps, -4 * eps):
            cx, cy = nx + s * nrm[0], ny + s * nrm[1]
            if _pa.points_inside(np.array([[cx, cy]]), edges,
                                 bands=bands)[0]:
                return cx, cy, True
    return nx, ny, True


# ---------------------------------------------------------------------------
#  mode A: the longest interior line per polygon
# ---------------------------------------------------------------------------
def run_longest_line(in_features, out_lines, transfer_fields=None,
                     simplify_tolerance=None, linear_units="Meters",
                     messages=None):
    """One line per polygon feature: the longest straight line fully
    inside it (the FETCH on water bodies). Multipart features report
    the best line over their parts; holes block the line. Fields:
    Src_FID, Part, Length_m (geodesic), Azimuth (degrees clockwise
    from north at the first endpoint), X1/Y1/X2/Y2 (output
    coordinates) plus any transferred attributes."""
    import arcpy

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
    tol_m = float(simplify_tolerance) * _LIN_M[linear_units] \
        if simplify_tolerance not in (None, "", 0) else None
    if tol_m:
        _say(messages,
             "Simplify tolerance {:g} {}: boundaries are simplified "
             "before the search (vertex counts drop, the quadratic "
             "search collapses); the answer can shift by roughly "
             "the tolerance.".format(float(simplify_tolerance),
                                     linear_units.lower()))

    _mh.begin_phases([
        ("Reading the polygons...", 1),
        ("Finding the longest lines...", 6),
        ("Writing the lines...", 1),
    ])
    plan = _cw._transfer_plan(
        in_features, transfer_fields,
        _is_shp_dest(out_lines), messages,
        fixed_names={"src_fid", "part", "length_m", "azimuth",
                     "x1", "y1", "x2", "y2"})
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
    _say(messages, "{:,} polygon{} to process.".format(
        len(feats), "" if len(feats) == 1 else "s"))
    _mh.phase_advance()

    results = []
    warned_big = False
    for fi, (oid, geom, attrs) in enumerate(feats):
        working_sr, is_custom = _pa._working_sr(geom)
        try:
            unit_m = float(getattr(working_sr, "metersPerUnit", 1.0)
                           or 1.0)
        except Exception:                                        # noqa: BLE001
            unit_m = 1.0
        gw = _ct.project_geometry(geom, working_sr)
        ext = gw.extent
        diag_m = math.hypot(ext.width, ext.height) * unit_m
        if is_custom and diag_m > _BIG_EXTENT_M and not warned_big:
            warned_big = True
            _warn(messages,
                  "Some features span {:,.0f} km -- at this size a "
                  "planar solve in the local equidistant projection "
                  "can err by a fraction of a percent (and very "
                  "rarely pick a different vertex pair). A true "
                  "spherical mode is planned; treat these results "
                  "as close approximations.".format(diag_m / 1000.0))
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
        best = None
        best_part = 0
        stage_words = {
            "screening": "screening vertex pair",
            "bounding": "bounding extension candidate",
            "verifying": "verifying candidate line",
        }
        import time as _time
        last_tick = [0.0]

        def _prog(stage, done, total, _oid=oid):
            now = _time.time()
            if now - last_tick[0] < 0.5:
                return
            last_tick[0] = now
            try:
                arcpy.SetProgressorLabel(
                    "Polygon OID {}: {} {:,} of {:,}...".format(
                        _oid, stage_words.get(stage, stage), done,
                        total))
            except Exception:                                    # noqa: BLE001
                pass

        for pi, part in enumerate(_pa._explode(gw)):
            rings = _pa.rings_of(part)
            if not rings:
                continue
            n_v = sum(max(0, len(r) - 1) for r in rings)
            if n_v > 2000:
                _say(messages,
                     "Polygon OID {}: {:,} vertices -> up to {:,} "
                     "vertex pairs to consider (the search stops "
                     "early once nothing can beat the best line; "
                     "simplifying the polygon first speeds this "
                     "up).".format(oid, n_v,
                                   n_v * (n_v - 1) // 2))
            got = longest_interior_line(
                rings, messages=messages,
                label="polygon OID {}".format(oid),
                progress=_prog)
            if got is not None and (best is None
                                    or got[2] > best[2]):
                best = got
                best_part = pi + 1
        if best is None:
            _warn(messages,
                  "Polygon OID {}: no interior line could be built "
                  "(degenerate geometry) -- skipped.".format(oid))
            continue
        p1, p2, _length_cs = best
        line2 = np.vstack([p1, p2])
        geod_len = _pa._geodesic_m(line2, working_sr)
        results.append({"oid": oid, "part": best_part,
                        "attrs": attrs, "p1": p1, "p2": p2,
                        "working_sr": working_sr,
                        "length_m": geod_len})
        _mh.phase_step(fi + 1, len(feats))
    if not results:
        raise arcpy.ExecuteError("No longest lines could be built.")
    _mh.phase_advance()

    out_names = [p[1] for p in plan]
    xfer_rows = [[out, ftype,
                  ("Source field " + src) if out != src else src,
                  flen, "", ""] for src, out, ftype, flen in plan]
    out_lines = _make_fc(out_lines, "POLYLINE", out_sr)
    arcpy.management.AddFields(out_lines, [
        ["Src_FID", "LONG", "Source polygon Object ID", "", "", ""],
        ["Part", "LONG", "Part holding the longest line", "", "", ""],
        ["Length_m", "DOUBLE", "Longest line length (m, geodesic)",
         "", "", ""],
        ["Azimuth", "DOUBLE", "Azimuth end 1 to end 2 (degrees)",
         "", "", ""],
        ["X1", "DOUBLE", "End 1 X", "", "", ""],
        ["Y1", "DOUBLE", "End 1 Y", "", "", ""],
        ["X2", "DOUBLE", "End 2 X", "", "", ""],
        ["Y2", "DOUBLE", "End 2 Y", "", "", ""]] + xfer_rows)
    cols = ["SHAPE@", "Src_FID", "Part", "Length_m", "Azimuth",
            "X1", "Y1", "X2", "Y2"] + out_names
    geographic = out_sr.type == "Geographic"
    # shapefiles cannot store NULL: null transferred attributes -> -999 / ""
    xtypes = [p[2] for p in plan]
    shp_out = str(out_lines).lower().endswith(".shp")
    with arcpy.da.InsertCursor(out_lines, cols) as ins:
        for r in results:
            wsr = r["working_sr"]
            g1 = _ct.project_geometry(arcpy.PointGeometry(
                arcpy.Point(float(r["p1"][0]), float(r["p1"][1])),
                wsr), out_sr)
            g2 = _ct.project_geometry(arcpy.PointGeometry(
                arcpy.Point(float(r["p2"][0]), float(r["p2"][1])),
                wsr), out_sr)
            x1, y1 = g1.firstPoint.X, g1.firstPoint.Y
            x2, y2 = g2.firstPoint.X, g2.firstPoint.Y
            if geographic:
                az = g1.angleAndDistanceTo(g2, "GEODESIC")[0]
            else:
                az = math.degrees(math.atan2(x2 - x1, y2 - y1))
            az %= 360.0
            shp = arcpy.Polyline(arcpy.Array(
                [arcpy.Point(x1, y1), arcpy.Point(x2, y2)]), out_sr)
            attrs = list(r["attrs"])
            if shp_out and xtypes:
                attrs = _mh.shp_safe_attrs(attrs, xtypes)
            ins.insertRow(tuple(
                [shp, r["oid"], r["part"], float(r["length_m"]),
                 float(az), float(x1), float(y1), float(x2),
                 float(y2)] + attrs))
    _say(messages, "{:,} longest line{} written.".format(
        len(results), "" if len(results) == 1 else "s"))
    _stamp(out_lines, "longest interior lines (fetch)", in_features)
    _mh.end_phases()
    return out_lines


# ---------------------------------------------------------------------------
#  mode B: directional fetch from station points
# ---------------------------------------------------------------------------
def run_station_fetch(stations, in_features, out_radials,
                      out_stations=None, bearing_increment=10.0,
                      max_fetch=None, transfer_fields=None,
                      simplify_tolerance=None,
                      linear_units="Meters", messages=None):
    """Directional fetch: rays from every station point at the given
    bearing increment, each ending at the first shoreline crossing or
    the optional maximum fetch distance. Geographic stations each get
    their OWN azimuthal-equidistant projection, so radial distances
    are true geodesics. Stations on land are nudged to the nearest
    interior point (Shift_Dist / Shift_Brg record the move)."""
    import arcpy

    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown distance unit: {}".format(linear_units))
    inc = float(bearing_increment or 10.0)
    if inc <= 0 or inc > 120:
        raise arcpy.ExecuteError(
            "Give a bearing increment between 0 and 120 degrees.")
    max_m = float(max_fetch) * _LIN_M[linear_units] \
        if max_fetch not in (None, "", 0) else None

    pdesc = arcpy.Describe(stations)
    if getattr(pdesc, "shapeType", "") != "Point":
        raise arcpy.ExecuteError(
            "The stations input must be point features.")
    wdesc = arcpy.Describe(in_features)
    if getattr(wdesc, "shapeType", "") != "Polygon":
        raise arcpy.ExecuteError(
            "The water-body input must be polygon features.")
    out_sr = pdesc.spatialReference
    if out_sr is None or getattr(out_sr, "name",
                                 "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "The stations input has no coordinate system.")

    _mh.begin_phases([
        ("Reading the water polygons...", 2),
        ("Casting fetch rays...", 5),
        ("Writing outputs...", 1),
    ])
    plan = _cw._transfer_plan(
        stations, transfer_fields,
        _is_shp_dest(out_radials) or _is_shp_dest(out_stations),
        messages,
        fixed_names={"src_fid", "kind", "bearing", "fetch_m",
                     "capped", "fetchmean", "fetchmin", "fetchmax",
                     "fetchsd", "maxf_brg", "maxray_m", "maxray_brg",
                     "maxchrd_m", "maxchrdbrg", "shift_dist",
                     "shift_brg"})
    fields = [p[0] for p in plan]

    n_total, n_sel, has_sel = _mh.selection_info(in_features,
                                                 str(in_features))
    if has_sel:
        _say(messages, "The water layer has an active selection: {} "
                       "of {} polygons are used.".format(n_sel,
                                                         n_total))
    water = [g for (g,) in arcpy.da.SearchCursor(in_features,
                                                 ["SHAPE@"])
             if g is not None]
    if not water:
        raise arcpy.ExecuteError("The water input has no polygons.")
    pts = []
    with arcpy.da.SearchCursor(stations,
                               ["OID@", "SHAPE@"] + fields) as cur:
        for row in cur:
            if row[1] is not None:
                pts.append((row[0], row[1], row[2:]))
    if not pts:
        raise arcpy.ExecuteError(
            "The stations input has no points (empty dataset, or an "
            "active selection selecting nothing).")
    _say(messages, "{:,} station{}; rays every {:g} degrees{}.".format(
        len(pts), "" if len(pts) == 1 else "s", inc,
        "" if max_m is None
        else "; capped at {:,.0f} m".format(max_m)))

    water_sr = water[0].spatialReference
    geographic = water_sr.type == "Geographic"
    try:
        unit_m = float(getattr(water_sr, "metersPerUnit", 1.0) or 1.0)
    except Exception:                                            # noqa: BLE001
        unit_m = 1.0
    # native-SR ring arrays: containment tests + the per-station
    # working projections both start from these
    tol_m = float(simplify_tolerance) * _LIN_M[linear_units] \
        if simplify_tolerance not in (None, "", 0) else None
    tol_nat = None
    if tol_m:
        tol_nat = (tol_m / 109000.0) if geographic \
            else (tol_m / unit_m)
        _say(messages,
             "Simplify tolerance {:g} {}: shorelines are simplified "
             "before the rays are cast; fetch distances can shift by "
             "roughly the tolerance.".format(
                 float(simplify_tolerance), linear_units.lower()))
    all_rings = []
    for g in water:
        gw = g
        if getattr(gw, "hasCurves", False):
            d = max(gw.length / 500.0, 1e-9)
            try:
                gw = gw.densify("DISTANCE", d, d)
            except Exception:                                    # noqa: BLE001
                pass
        if tol_nat:
            try:
                g2 = gw.generalize(tol_nat)
                if g2 is not None and g2.area > 0:
                    gw = g2
            except Exception:                                    # noqa: BLE001
                pass
        all_rings.extend(_pa.rings_of(gw))
    if not all_rings:
        raise arcpy.ExecuteError("The water polygons are degenerate.")
    edges_nat = _pa.edges_of(all_rings)
    bands_nat = _pa.y_bands(edges_nat)
    span_nat = max(float(edges_nat[:, 0].max()
                         - edges_nat[:, 0].min()),
                   float(edges_nat[:, 1].max()
                         - edges_nat[:, 1].min()), 1e-9)
    # unique boundary vertices ONCE (longest_through_point's
    # candidate directions) -- the geographic branch projects this
    # array per station, the projected branch reuses it (and
    # bands_nat) directly instead of rebuilding both per station
    verts_nat = np.unique(np.round(np.vstack([edges_nat[:, :2],
                                              edges_nat[:, 2:]]), 9),
                          axis=0)
    bearings = np.arange(0.0, 360.0, inc)
    _mh.phase_advance()

    import random_points as _rp
    radial_rows = []
    station_rows = []
    for si, (oid, pgeom, attrs) in enumerate(pts):
        # everything happens in the WATER polygons' coordinate system
        pw = _ct.project_geometry(pgeom, water_sr).firstPoint
        px, py = float(pw.X), float(pw.Y)
        nx, ny, shifted = nudge_inside(
            px, py, edges_nat, bands_nat, span_nat,
            units_per_m=(1.0 / 111320.0) if geographic
            else (1.0 / unit_m))
        if geographic:
            # the station's own azimuthal-equidistant projection:
            # radial distances from the center are true geodesics
            wsr, _custom = _pa._working_sr(
                arcpy.PointGeometry(arcpy.Point(nx, ny), water_sr))
            # filter native edges to the reach of the rays
            if max_m is not None:
                rlat = max_m / 109000.0
                m = ~((np.maximum(edges_nat[:, 1], edges_nat[:, 3])
                       < ny - rlat)
                      | (np.minimum(edges_nat[:, 1],
                                    edges_nat[:, 3]) > ny + rlat))
                # spherical longitude reach of the cap: a geodesic
                # circle of radius r at latitude lat spans
                # asin(sin(r/R)/cos(lat)) of longitude; when that
                # degenerates (the cap reaches poleward) every
                # longitude is in reach -- skip the lon test
                rr = max_m / 6371000.0
                ratio = math.sin(min(rr, math.pi / 2.0)) \
                    / max(math.cos(math.radians(ny)), 1e-12)
                if rr < math.pi / 2.0 and ratio < 1.0:
                    rlon = math.degrees(math.asin(ratio))
                    e_lo = np.minimum(edges_nat[:, 0],
                                      edges_nat[:, 2])
                    e_hi = np.maximum(edges_nat[:, 0],
                                      edges_nat[:, 2])
                    hit = (e_hi >= nx - rlon) & (e_lo <= nx + rlon)
                    # a window crossing the antimeridian also admits
                    # edges on the far side of the seam
                    if nx - rlon < -180.0:
                        hit |= e_hi >= nx - rlon + 360.0
                    if nx + rlon > 180.0:
                        hit |= e_lo <= nx + rlon - 360.0
                    m &= hit
                e_sub = edges_nat[m]
            else:
                e_sub = edges_nat
            if not len(e_sub):
                e_sub = edges_nat
            a = _rp._project_xy(e_sub[:, :2], water_sr, wsr)
            b = _rp._project_xy(e_sub[:, 2:], water_sr, wsr)
            e_loc = np.column_stack([a, b])
            # the exact max ray/chord need CLOSED-polygon parity
            # tests, so they use the FULL edge set in this frame
            if len(e_sub) == len(edges_nat):
                e_full = e_loc
            else:
                fa = _rp._project_xy(edges_nat[:, :2], water_sr, wsr)
                fb = _rp._project_xy(edges_nat[:, 2:], water_sr, wsr)
                e_full = np.column_stack([fa, fb])
            v_full = _rp._project_xy(verts_nat, water_sr, wsr)
            bands_full = _pa.y_bands(e_full)
            sx = sy = 0.0
            cap_loc = max_m
            to_m = 1.0
        else:
            wsr = water_sr
            if max_m is not None:
                r_nat = max_m / unit_m
                m = ~((np.maximum(edges_nat[:, 0], edges_nat[:, 2])
                       < nx - r_nat)
                      | (np.minimum(edges_nat[:, 0],
                                    edges_nat[:, 2]) > nx + r_nat)
                      | (np.maximum(edges_nat[:, 1],
                                    edges_nat[:, 3]) < ny - r_nat)
                      | (np.minimum(edges_nat[:, 1],
                                    edges_nat[:, 3]) > ny + r_nat))
                e_loc = edges_nat[m]
                if not len(e_loc):
                    e_loc = edges_nat
                cap_loc = r_nat
            else:
                e_loc = edges_nat
                cap_loc = None
            e_full = edges_nat
            v_full = verts_nat
            bands_full = bands_nat
            sx, sy = nx, ny
            to_m = unit_m
        dists, capped = radial_fetch(sx, sy, bearings, e_loc,
                                     cap_loc)
        dists_m = dists * to_m
        # the EXACT longest ray and longest chord through the
        # station: candidates are the directions to every polygon
        # vertex (the optimum must touch a vertex -- the convexity
        # argument behind the two-vertex theorem, per Jeff)
        (chord_len, chord_az, c_lo, c_hi), \
            (ray_len, ray_az, r_end) = longest_through_point(
                sx, sy, e_full, bands_full, cap_loc, verts=v_full)
        chord_m = chord_len * to_m
        ray_m = ray_len * to_m
        # shift bookkeeping (geodesic meters + azimuth)
        if shifted:
            gp0 = arcpy.PointGeometry(arcpy.Point(px, py), water_sr)
            gp1 = arcpy.PointGeometry(arcpy.Point(nx, ny), water_sr)
            meth = "GEODESIC" if geographic else "PLANAR"
            s_az, s_d = gp0.angleAndDistanceTo(gp1, meth)
            if not geographic:
                s_d *= unit_m
            s_az %= 360.0
        else:
            s_az, s_d = None, 0.0
        # radial + exact-line geometries
        def _line_loc(pa_xy, pb_xy):
            if geographic:
                pA = _ct.project_geometry(arcpy.PointGeometry(
                    arcpy.Point(float(pa_xy[0]), float(pa_xy[1])),
                    wsr), out_sr)
                pB = _ct.project_geometry(arcpy.PointGeometry(
                    arcpy.Point(float(pb_xy[0]), float(pb_xy[1])),
                    wsr), out_sr)
                return arcpy.Polyline(arcpy.Array(
                    [pA.firstPoint, pB.firstPoint]), out_sr)
            return _ct.project_geometry(arcpy.Polyline(arcpy.Array(
                [arcpy.Point(float(pa_xy[0]), float(pa_xy[1])),
                 arcpy.Point(float(pb_xy[0]), float(pb_xy[1]))]),
                water_sr), out_sr)

        brad = np.radians(bearings)
        exs = sx + dists * np.sin(brad)
        eys = sy + dists * np.cos(brad)
        for k in range(len(bearings)):
            radial_rows.append(tuple(
                [_line_loc((sx, sy), (exs[k], eys[k])), oid, "Ray",
                 float(bearings[k]), float(dists_m[k]),
                 int(bool(capped[k]))] + list(attrs)))
        if ray_m > 0:
            rcap = int(cap_loc is not None
                       and abs(ray_len - cap_loc) < 1e-6)
            radial_rows.append(tuple(
                [_line_loc((sx, sy), r_end), oid, "MaxRay",
                 float(ray_az), float(ray_m), rcap] + list(attrs)))
        if chord_m > 0:
            ccap = int(cap_loc is not None
                       and chord_len >= 2 * cap_loc - 1e-6)
            radial_rows.append(tuple(
                [_line_loc(c_lo, c_hi), oid, "MaxChord",
                 float(chord_az), float(chord_m), ccap]
                + list(attrs)))
        k_max = int(np.argmax(dists_m))
        station_rows.append(tuple(
            [_ct.project_geometry(arcpy.PointGeometry(arcpy.Point(nx, ny),
                                                      water_sr), out_sr),
             oid, float(dists_m.mean()), float(dists_m.min()),
             float(dists_m.max()),
             float(dists_m.std(ddof=1)) if len(dists_m) > 1 else 0.0,
             float(bearings[k_max]), float(ray_m), float(ray_az),
             float(chord_m), float(chord_az), float(s_d),
             (float(s_az) if s_az is not None else None)]
            + list(attrs)))
        _mh.phase_step(si + 1, len(pts))
    _mh.phase_advance()

    out_names = [p[1] for p in plan]
    xfer_rows = [[out, ftype,
                  ("Source field " + src) if out != src else src,
                  flen, "", ""] for src, out, ftype, flen in plan]
    out_radials = _make_fc(out_radials, "POLYLINE", out_sr)
    arcpy.management.AddFields(out_radials, [
        ["Src_FID", "LONG", "Station Object ID", "", "", ""],
        ["Kind", "TEXT", "Ray / MaxRay / MaxChord", 10, "", ""],
        ["Bearing", "DOUBLE", "Azimuth (degrees)", "", "", ""],
        ["Fetch_m", "DOUBLE", "Length (m)", "", "", ""],
        ["Capped", "SHORT", "1 = reached the maximum fetch",
         "", "", ""]] + xfer_rows)
    # shapefiles cannot store NULL: null transferred attributes -> -999 / ""
    xtypes = [p[2] for p in plan]

    def _safe(row, path):
        if xtypes and str(path).lower().endswith(".shp"):
            row = list(row)
            row[-len(xtypes):] = _mh.shp_safe_attrs(row[-len(xtypes):], xtypes)
            return tuple(row)
        return row

    with arcpy.da.InsertCursor(
            out_radials, ["SHAPE@", "Src_FID", "Kind", "Bearing",
                          "Fetch_m", "Capped"] + out_names) as ins:
        for row in radial_rows:
            ins.insertRow(_safe(row, out_radials))
    if out_stations:
        out_stations = _make_fc(out_stations, "POINT", out_sr)
        arcpy.management.AddFields(out_stations, [
            ["Src_FID", "LONG", "Station Object ID", "", "", ""],
            ["FetchMean", "DOUBLE", "Mean ray fetch (m)", "", "", ""],
            ["FetchMin", "DOUBLE", "Minimum ray fetch (m)",
             "", "", ""],
            ["FetchMax", "DOUBLE", "Maximum ray fetch (m)",
             "", "", ""],
            ["FetchSD", "DOUBLE", "Ray fetch standard deviation (m)",
             "", "", ""],
            ["MaxF_Brg", "DOUBLE", "Bearing of the longest ray",
             "", "", ""],
            ["MaxRay_m", "DOUBLE",
             "EXACT longest ray from the station (m)", "", "", ""],
            ["MaxRay_Brg", "DOUBLE", "Its azimuth", "", "", ""],
            ["MaxChrd_m", "DOUBLE",
             "EXACT longest line through the station (m)",
             "", "", ""],
            ["MaxChrdBrg", "DOUBLE", "Its azimuth", "", "", ""],
            ["Shift_Dist", "DOUBLE",
             "Station moved this far to reach water (m)", "", "", ""],
            ["Shift_Brg", "DOUBLE", "Bearing of the station move",
             "", "", ""]] + xfer_rows)
        with arcpy.da.InsertCursor(
                out_stations,
                ["SHAPE@", "Src_FID", "FetchMean", "FetchMin",
                 "FetchMax", "FetchSD", "MaxF_Brg", "MaxRay_m",
                 "MaxRay_Brg", "MaxChrd_m", "MaxChrdBrg",
                 "Shift_Dist", "Shift_Brg"] + out_names) as ins:
            is_shp = str(out_stations).lower().endswith(".shp")
            for row in station_rows:
                row = list(row)
                if is_shp and row[12] is None:
                    row[12] = -1.0
                ins.insertRow(_safe(tuple(row), out_stations))
    n_shift = sum(1 for r in station_rows if r[11] > 0)
    if n_shift:
        _say(messages,
             "{} station{} fell outside the water and {} nudged to "
             "the nearest interior point (see Shift_Dist / "
             "Shift_Brg).".format(n_shift,
                                  "" if n_shift == 1 else "s",
                                  "was" if n_shift == 1 else "were"))
    _say(messages, "{:,} fetch rays written{}.".format(
        len(radial_rows),
        "" if not out_stations else "; station summary written"))
    _stamp(out_radials, "directional fetch rays", in_features)
    if out_stations:
        _stamp(out_stations, "station fetch summaries", in_features)
    _mh.end_phases()
    return out_radials


# ---------------------------------------------------------------------------
#  shared helpers
# ---------------------------------------------------------------------------
def _is_shp_dest(path):
    if not path:
        return False
    p = str(path).replace("/", "\\").rstrip("\\")
    return bool(os.path.dirname(p)) and not _mh.in_gdb_container(p)


def _make_fc(path, gtype, out_sr):
    import arcpy

    p = str(path).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(p)
    if ws and not _mh.in_gdb_container(p) \
            and not name.lower().endswith(".shp"):
        name += ".shp"
    res = arcpy.management.CreateFeatureclass(
        ws, name, gtype, spatial_reference=_vt.clean_sr(out_sr))
    return res.getOutput(0)


def _stamp(out_path, what, source):
    import arcpy

    try:
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = (
            "Created by Longest Line in Polygon (Fetch): {} from "
            "{}.".format(what, os.path.basename(str(source))))
        md.description = (
            "From the Jenness Enterprises Longest Line in Polygon "
            "(Fetch) tool -- a modernized port of the classic "
            "ArcView 3.x Longest Straight Line extension: the "
            "longest straight line fully contained in each polygon "
            "(the fetch, on water bodies; holes block the line; the "
            "line passes through two polygon vertices and may extend "
            "past them to the boundary), or directional fetch rays "
            "cast from station points at a fixed bearing increment, "
            "terminated at the first shoreline or a maximum fetch "
            "distance (after the UOGML Wave Energy extension, "
            "Jenness 2014, implementing Ekebom et al. 2003). "
            "Geographic data is handled in azimuthal-equidistant "
            "working projections; lengths are geodesic meters.")
        md.save()
    except Exception:                                            # noqa: BLE001
        pass

