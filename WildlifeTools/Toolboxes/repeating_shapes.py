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

"""Repeating Shapes -- tessellations and sampling lattices.

A modernized port of Jeff Jenness's classic Repeating Shapes extension
(``repeat_shapes.cls`` + the eight ``modMake*`` generators): tessellate an
area of interest with HEXAGONS, SQUARES, TRIANGLES or RECTANGLES
(brick courses with an adjustable percent offset), or lay out POINTS or
CIRCLES in a square or triangular arrangement -- with a free orientation
angle -- over an extent or a polygon area of interest. The area of
interest may also be POLYLINE or POINT features (Jeff, 2026-08-21):
polygons enclose an area, while lines/points SELECT -- every shape
touching a line (hexagons over a river) or containing a point
(observation locations) is kept whole; for point lattices, which have
no extent of their own, the lattice's implied Voronoi tile (a square,
or a hexagon for the triangular arrangement) plays the shape's role.

SIZES CARRY THEIR OWN UNITS (Jeff, 2026-08-21): the dialog offers real
area units (square meters, hectares, square kilometers, square feet,
acres, square miles) and linear units (meters, kilometers, feet, miles),
converted to the output coordinate system internally -- say "100
hectares" or "500 feet" without knowing the CS. Every measure of the
original dialogs is accepted: hexagons by area / edge / width (across
flats) / diameter (corner to corner); squares by area / edge; triangles
by area / edge / height; rectangles by Edge 1 (width) + Edge 2 (height)
+ percent brick offset (cumulative per course, 50 = running bond);
circles by area / radius / diameter / circumference plus a center
spacing (default: touching); points by center spacing.

GEOGRAPHIC data gets a genuinely better answer than the original's raw
map units: since truly regular equal-area tessellations of a curved
surface are impossible (Euler's formula forces 12 pentagons into any
hex-dominant sphere tiling), the tool computes in an automatic
AOI-centered Lambert azimuthal EQUAL-AREA working projection -- cell
AREAS are exact, shape regularity absorbs the imperceptible regional
distortion (~(d/2R)^2) -- and the output is projected back to the input
geographic CS. Continental/global tessellation belongs to discrete
global grid systems (ISEA3H, H3); the tool warns when the area of
interest approaches that scale.

WHAT CHANGED FROM THE ORIGINAL (besides the Pro dialog): the entire
lattice is generated as numpy arrays in a handful of vectorized
operations, where the original built each shape as an individual COM
geometry inside nested row loops and then DELETED the extras one feature
at a time through an edit session. arcpy is touched only to clip border
shapes (when clipping is requested) and to write the final features. The
original's edge behavior -- keep every WHOLE shape that intersects the
area of interest -- is the default, with two additions: keep only shapes
whose center is inside, or CLIP shapes to the area.
"""
import math
import os

import numpy as np

import mahalanobis as _mh
import patch_analysis as _pa
import voronoi_tools as _vt

# shape-type keywords (dialog value list)
HEXAGONS = "Hexagons"
SQUARES = "Squares"
TRIANGLES = "Triangles"
RECTANGLES = "Rectangles (brick courses)"
CIRCLES_SQ = "Circles - square pattern"
CIRCLES_TRI = "Circles - triangular pattern"
POINTS_SQ = "Points - square pattern"
POINTS_TRI = "Points - triangular pattern"
SHAPE_TYPES = [HEXAGONS, SQUARES, TRIANGLES, RECTANGLES, CIRCLES_SQ,
               CIRCLES_TRI, POINTS_SQ, POINTS_TRI]

# edge policies
KEEP_INTERSECT = "Keep whole shapes intersecting the area (original)"
KEEP_CENTER = "Keep shapes whose center is inside the area"
CLIP = "Clip shapes to the area"
EDGE_POLICIES = [KEEP_INTERSECT, KEEP_CENTER, CLIP]

_CIRCLE_SEGS = 48

# ---------------------------------------------------------------------------
#  units
# ---------------------------------------------------------------------------
LINEAR_UNITS = ["Meters", "Kilometers", "Feet", "Miles"]
AREA_UNITS = ["Square Meters", "Hectares", "Square Kilometers",
              "Square Feet", "Acres", "Square Miles"]

_LIN_M = {"Meters": 1.0, "Kilometers": 1000.0, "Feet": 0.3048,
          "Miles": 1609.344}
_AREA_M2 = {"Square Meters": 1.0, "Hectares": 10000.0,
            "Square Kilometers": 1.0e6, "Square Feet": 0.3048 ** 2,
            "Acres": 4046.8564224, "Square Miles": 1609.344 ** 2}

# Jeff's coupling: picking an area unit snaps the linear unit to its
# natural partner.
AREA_TO_LINEAR = {"Square Meters": "Meters", "Hectares": "Meters",
                  "Square Kilometers": "Kilometers",
                  "Square Feet": "Feet", "Acres": "Feet",
                  "Square Miles": "Miles"}

# measures each shape accepts (dialog enablement + interlock)
MEASURES_FOR = {
    HEXAGONS: ["area", "edge", "width", "diameter"],
    SQUARES: ["area", "edge"],
    TRIANGLES: ["area", "edge", "height"],
    RECTANGLES: ["width", "height"],
    CIRCLES_SQ: ["area", "radius", "diameter", "circumference",
                 "spacing"],
    CIRCLES_TRI: ["area", "radius", "diameter", "circumference",
                  "spacing"],
    POINTS_SQ: ["spacing"],
    POINTS_TRI: ["spacing"],
}


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def derive_measures(shape_type, measure, value):
    """All of a shape's measures from any ONE of them, in one consistent
    linear unit (areas in that unit squared) -- the original dialogs'
    fill-the-rest-automatically interlock, exposed for the Pro dialog.

    Identities: hexagon edge e -> width (across flats) sqrt(3) e,
    diameter (corner to corner) 2 e, area (3 sqrt(3)/2) e^2; equilateral
    triangle edge e -> height (sqrt(3)/2) e, area (sqrt(3)/4) e^2;
    square area e^2; circle radius r -> diameter 2 r, circumference
    2 pi r, area pi r^2. Spacing is independent of circle size and is
    left untouched (returned only when it IS the driving measure).

    A measure the shape does not have raises a clean ValueError naming
    the valid ones (a square once quietly read ``width`` as an AREA).
    """
    v = float(value)
    if v <= 0:
        raise ValueError("Measures must be positive.")
    valid = MEASURES_FOR.get(shape_type)
    if valid is None:
        raise ValueError("Unknown shape type: {}".format(shape_type))
    if measure not in valid:
        raise ValueError(
            "'{}' is not a size measure of {} -- give one of: "
            "{}.".format(measure, shape_type.lower(),
                         ", ".join(valid)))
    if shape_type == HEXAGONS:
        e = {"edge": v, "width": v / math.sqrt(3.0), "diameter": v / 2.0,
             "area": math.sqrt(v / (3.0 * math.sqrt(3.0) / 2.0)),
             }[measure]
        return {"edge": e, "width": math.sqrt(3.0) * e,
                "diameter": 2.0 * e,
                "area": 1.5 * math.sqrt(3.0) * e * e}
    if shape_type == SQUARES:
        e = v if measure == "edge" else math.sqrt(v)
        return {"edge": e, "area": e * e}
    if shape_type == TRIANGLES:
        e = {"edge": v, "height": 2.0 * v / math.sqrt(3.0),
             "area": math.sqrt(4.0 * v / math.sqrt(3.0))}[measure]
        return {"edge": e, "height": math.sqrt(3.0) / 2.0 * e,
                "area": math.sqrt(3.0) / 4.0 * e * e}
    if shape_type in (CIRCLES_SQ, CIRCLES_TRI):
        if measure == "spacing":
            return {"spacing": v}
        r = {"radius": v, "diameter": v / 2.0,
             "circumference": v / (2.0 * math.pi),
             "area": math.sqrt(v / math.pi)}[measure]
        return {"radius": r, "diameter": 2.0 * r,
                "circumference": 2.0 * math.pi * r,
                "area": math.pi * r * r}
    if shape_type in (POINTS_SQ, POINTS_TRI):
        return {"spacing": v}
    if shape_type == RECTANGLES:
        return {measure: v}          # width/height independent
    raise ValueError("Unknown shape type: {}".format(shape_type))


def resolve_size(shape_type, area=None, edge=None, height=None,
                 width=None, diameter=None, radius=None,
                 circumference=None, spacing=None, offset_pct=50.0):
    """Normalize the size inputs (already in CS units) to the lattice's
    native measures: hex/sq/tri -> {'edge'}; rectangles -> {'width',
    'height', 'offset'}; circles -> {'spacing', 'radius'}; points ->
    {'spacing'}. Any one sufficient measure works (the original forms'
    fill-any-box behavior)."""
    def first(*pairs):
        for name, val in pairs:
            if val:
                return derive_measures(shape_type, name, float(val))
        return None

    if shape_type in (HEXAGONS, SQUARES, TRIANGLES):
        # forward only the measures this shape actually HAS -- a
        # square given width= must fall through to the clean error
        # below, not have the value read as some other measure
        valid = MEASURES_FOR[shape_type]
        d = first(*[(name, val) for name, val in
                    (("edge", edge), ("width", width),
                     ("diameter", diameter), ("height", height),
                     ("area", area)) if name in valid])
        if d is None or "edge" not in d:
            raise ValueError(
                "Give any one size measure for the {} ({}).".format(
                    shape_type.lower().rstrip("s"), ", ".join(valid)))
        return {"edge": d["edge"]}
    if shape_type == RECTANGLES:
        if not (width and height):
            raise ValueError(
                "Give the rectangle Edge 1 (width) and Edge 2 (height).")
        off = 50.0 if offset_pct in (None, "") else float(offset_pct)
        if not (0.0 <= off <= 100.0):
            raise ValueError("The brick offset must be 0-100 percent.")
        return {"width": float(width), "height": float(height),
                "offset": off}
    if shape_type in (CIRCLES_SQ, CIRCLES_TRI):
        d = first(("radius", radius), ("diameter", diameter),
                  ("circumference", circumference), ("area", area))
        if d is None:
            raise ValueError(
                "Give the circle radius, diameter, circumference, or "
                "area.")
        sp = float(spacing) if spacing else 2.0 * d["radius"]
        if sp <= 0:
            raise ValueError("The spacing must be positive.")
        return {"spacing": sp, "radius": d["radius"]}
    if shape_type in (POINTS_SQ, POINTS_TRI):
        if not spacing:
            raise ValueError("Give the spacing between points.")
        return {"spacing": float(spacing)}
    raise ValueError("Unknown shape type: {}".format(shape_type))


# ---------------------------------------------------------------------------
#  vectorized lattice generation (all in un-rotated "lattice space")
# ---------------------------------------------------------------------------
def _index_ranges(corners_l, sx, sy):
    xmin, ymin = corners_l.min(axis=0)
    xmax, ymax = corners_l.max(axis=0)
    i0 = int(math.floor(xmin / sx)) - 2
    i1 = int(math.ceil(xmax / sx)) + 2
    j0 = int(math.floor(ymin / sy)) - 2
    j1 = int(math.ceil(ymax / sy)) + 2
    return i0, i1, j0, j1


def _grid(i0, i1, j0, j1):
    ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
    return ii.ravel(), jj.ravel()


def build_lattice(shape_type, size, corners_l):
    """(centers (N,2), verts (N,K,2) or None) in lattice space."""
    if shape_type == HEXAGONS:
        s = size["edge"]
        w, h = 1.5 * s, math.sqrt(3.0) * s
        i0, i1, j0, j1 = _index_ranges(corners_l, w, h)
        ii, jj = _grid(i0, i1, j0, j1)
        cx = ii * w
        cy = jj * h + (ii % 2) * (h / 2.0)
        centers = np.column_stack([cx, cy])
        ang = np.radians(np.arange(6) * 60.0)
        offs = np.column_stack([s * np.cos(ang), s * np.sin(ang)])
        return centers, centers[:, None, :] + offs[None, :, :]

    if shape_type == SQUARES:
        s = size["edge"]
        i0, i1, j0, j1 = _index_ranges(corners_l, s, s)
        ii, jj = _grid(i0, i1, j0, j1)
        x0, y0 = ii * s, jj * s
        verts = np.stack([np.column_stack([x0, y0]),
                          np.column_stack([x0 + s, y0]),
                          np.column_stack([x0 + s, y0 + s]),
                          np.column_stack([x0, y0 + s])], axis=1)
        return verts.mean(axis=1), verts

    if shape_type == RECTANGLES:
        w, h = size["width"], size["height"]
        off = size.get("offset", 50.0) / 100.0
        i0, i1, j0, j1 = _index_ranges(corners_l, w, h)
        # the cumulative stagger below is wrapped into [0, w) by the
        # modulo, so each course shifts right by LESS than one brick:
        # a fixed two-column margin covers the frame. (A pad derived
        # from the ABSOLUTE row index j -- which is the raw northing
        # divided by the brick height, ~39,000 at UTM scale -- once
        # inflated the candidate grid ~380-fold for nothing.)
        ii, jj = _grid(i0 - 2, i1 + 2, j0, j1)
        # CUMULATIVE stagger, as the original: each course shifts by
        # offset% of Edge 1 relative to the course below (50 = running
        # bond, 33 = staircase)
        x0 = ii * w + (jj * off * w) % w
        y0 = jj * h
        verts = np.stack([np.column_stack([x0, y0]),
                          np.column_stack([x0 + w, y0]),
                          np.column_stack([x0 + w, y0 + h]),
                          np.column_stack([x0, y0 + h])], axis=1)
        return verts.mean(axis=1), verts

    if shape_type == TRIANGLES:
        s = size["edge"]
        h = math.sqrt(3.0) / 2.0 * s
        # sheared lattice x = (i + j/2)s -- the i-range depends on j
        xmin, ymin = corners_l.min(axis=0)
        xmax, ymax = corners_l.max(axis=0)
        j0 = int(math.floor(ymin / h)) - 2
        j1 = int(math.ceil(ymax / h)) + 2
        i0 = int(math.floor(xmin / s - j1 / 2.0)) - 2
        i1 = int(math.ceil(xmax / s - j0 / 2.0)) + 2
        ii, jj = _grid(i0, i1, j0, j1)
        px = (ii + jj / 2.0) * s
        py = jj * h
        up = np.stack([np.column_stack([px, py]),
                       np.column_stack([px + s, py]),
                       np.column_stack([px + s / 2.0, py + h])], axis=1)
        dn = np.stack([np.column_stack([px + s, py]),
                       np.column_stack([px + 1.5 * s, py + h]),
                       np.column_stack([px + s / 2.0, py + h])], axis=1)
        verts = np.concatenate([up, dn], axis=0)
        return verts.mean(axis=1), verts

    if shape_type in (POINTS_SQ, CIRCLES_SQ):
        d = size["spacing"]
        i0, i1, j0, j1 = _index_ranges(corners_l, d, d)
        ii, jj = _grid(i0, i1, j0, j1)
        centers = np.column_stack([ii * d, jj * d])
    else:                                   # triangular arrangements
        d = size["spacing"]
        h = math.sqrt(3.0) / 2.0 * d
        i0, i1, j0, j1 = _index_ranges(corners_l, d, h)
        ii, jj = _grid(i0, i1, j0, j1)
        centers = np.column_stack([(ii + (jj % 2) * 0.5) * d, jj * h])

    if shape_type in (CIRCLES_SQ, CIRCLES_TRI):
        r = size["radius"]
        ang = np.linspace(0.0, 2.0 * math.pi, _CIRCLE_SEGS,
                          endpoint=False)
        offs = np.column_stack([r * np.cos(ang), r * np.sin(ang)])
        return centers, centers[:, None, :] + offs[None, :, :]
    return centers, None


def _rotate(xy, deg, about):
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    v = np.asarray(xy, dtype=np.float64) - about
    return np.stack([v[..., 0] * c - v[..., 1] * s,
                     v[..., 0] * s + v[..., 1] * c],
                    axis=-1) + about


# ---------------------------------------------------------------------------
#  vectorized keep tests
# ---------------------------------------------------------------------------
def _keep_mask(centers, verts, bnd_edges, bands, policy, rings=None):
    n = len(centers)
    e_xmin = np.minimum(bnd_edges[:, 0], bnd_edges[:, 2])
    e_xmax = np.maximum(bnd_edges[:, 0], bnd_edges[:, 2])
    e_ymin = np.minimum(bnd_edges[:, 1], bnd_edges[:, 3])
    e_ymax = np.maximum(bnd_edges[:, 1], bnd_edges[:, 3])

    if verts is None:
        keep = _pa.points_inside(centers, bnd_edges, bands=bands)
        return keep, np.zeros(n, dtype=bool)

    vx_min = verts[..., 0].min(axis=1)
    vx_max = verts[..., 0].max(axis=1)
    vy_min = verts[..., 1].min(axis=1)
    vy_max = verts[..., 1].max(axis=1)
    border = np.zeros(n, dtype=bool)
    step = max(1, int(4e7 // max(len(bnd_edges), 1)))
    for k0 in range(0, n, step):
        k1 = min(n, k0 + step)
        hit = ((e_xmin[None, :] <= vx_max[k0:k1, None])
               & (e_xmax[None, :] >= vx_min[k0:k1, None])
               & (e_ymin[None, :] <= vy_max[k0:k1, None])
               & (e_ymax[None, :] >= vy_min[k0:k1, None]))
        border[k0:k1] = hit.any(axis=1)

    center_in = _pa.points_inside(centers, bnd_edges, bands=bands)
    if policy == KEEP_CENTER:
        return center_in, np.zeros(n, dtype=bool)

    keep = center_in & ~border
    idx = np.flatnonzero(border)
    if len(idx):
        vin = _pa.points_inside(verts[idx].reshape(-1, 2), bnd_edges,
                                bands=bands).reshape(len(idx), -1)
        touch = vin.any(axis=1)
        todo = idx[~touch]
        if len(todo):
            k = verts.shape[1]
            p = verts[todo].reshape(-1, 2)
            q = verts[todo][:, list(range(1, k)) + [0], :].reshape(-1, 2)
            cross = _pa._segments_properly_cross(
                p, q, bnd_edges).reshape(len(todo), k).any(axis=1)
            touch2 = np.zeros(len(idx), dtype=bool)
            touch2[np.isin(idx, todo[cross])] = True
            still = todo[~cross]
            if len(still):
                # no vertex inside and no edge crossed: the shape
                # either misses the area entirely or SWALLOWS a whole
                # ring of it -- test one representative vertex PER
                # RING (an island of a multipart area can lie
                # entirely inside one big shape)
                reps = np.array([r[0] for r in rings]) if rings \
                    else bnd_edges[:1, :2]
                for si in still:
                    if _pts_in_convex(reps, verts[si]).any():
                        touch2[np.flatnonzero(idx == si)[0]] = True
            touch = touch | touch2
        keep_b = np.zeros(n, dtype=bool)
        keep_b[idx[touch]] = True
        keep = keep | keep_b
    return keep, border & keep


def _point_in_convex(pt, poly):
    v = poly - pt
    cross = v[:, 0] * np.roll(v[:, 1], -1) - v[:, 1] * np.roll(v[:, 0],
                                                               -1)
    return bool((cross >= 0).all() or (cross <= 0).all())


# ---------------------------------------------------------------------------
#  line / point areas of interest (Jeff, 2026-08-21: "it would be
#  reasonable to generate hexagons over a river or observation
#  locations") -- polygons ENCLOSE an area; polylines and points instead
#  SELECT the shapes that touch them
# ---------------------------------------------------------------------------
def _pts_in_convex(pts, poly):
    """Mask over ``pts`` (M, 2): which lie inside the CONVEX polygon
    ``poly`` (V, 2) -- the vectorized sibling of ``_point_in_convex``."""
    a = poly
    b = np.roll(poly, -1, axis=0)
    cr = ((b[:, 0] - a[:, 0])[:, None]
          * (pts[None, :, 1] - a[:, 1][:, None])
          - (b[:, 1] - a[:, 1])[:, None]
          * (pts[None, :, 0] - a[:, 0][:, None]))
    return (cr >= 0.0).all(axis=0) | (cr <= 0.0).all(axis=0)


def _read_features(boundary, work_sr, shape_kind, messages):
    """The (selected) line/point area-of-interest features projected to
    ``work_sr``: ``("point", (N, 2) array)`` or ``("line", [(V, 2) array
    per path])``. True-curve polylines are densified after projection
    (standing rule: JSON vertex walks collapse curves to arc
    endpoints)."""
    import json

    import arcpy

    try:
        path = arcpy.Describe(boundary).catalogPath
    except Exception:                                            # noqa: BLE001
        path = str(boundary)
    n_total, n_sel, has_sel = _mh.selection_info(boundary, path)
    if has_sel:
        _say(messages, "The area-of-interest layer has an active "
                       "selection: {} of {} features are used.".format(
                           n_sel, n_total))
    pts, parts = [], []
    with arcpy.da.SearchCursor(boundary, ["SHAPE@"]) as cur:
        for (g,) in cur:
            if g is None:
                continue
            g = _vt._project_geom(g, work_sr)
            if shape_kind == "Point":
                p = g.firstPoint
                if p is not None:
                    pts.append([p.X, p.Y])
            elif shape_kind == "Multipoint":
                pts.extend([[p[0], p[1]] for p in
                            json.loads(g.JSON).get("points", [])])
            else:                                             # Polyline
                if getattr(g, "hasCurves", False):
                    d = max(g.length / 500.0, 1e-9)
                    try:
                        g = g.densify("DISTANCE", d, d)
                    except Exception:                        # noqa: BLE001
                        pass
                for pth in json.loads(g.JSON).get("paths", []):
                    a = np.asarray(pth, dtype=np.float64)[:, :2]
                    if len(a) >= 2:
                        parts.append(a)
    if shape_kind in ("Point", "Multipoint"):
        if not pts:
            raise arcpy.ExecuteError(
                "The area-of-interest input contains no features (empty "
                "dataset, or an active selection selecting nothing).")
        arr = np.asarray(pts, dtype=np.float64)
        _say(messages, "Area of interest: {:,} point location{} -- "
                       "keeping the shapes that contain them.".format(
                           len(arr), "" if len(arr) == 1 else "s"))
        return "point", arr
    if not parts:
        raise arcpy.ExecuteError(
            "The area-of-interest input contains no features (empty "
            "dataset, or an active selection selecting nothing).")
    _say(messages, "Area of interest: {:,} polyline path{} -- keeping "
                   "the shapes that touch them.".format(
                       len(parts), "" if len(parts) == 1 else "s"))
    return "line", parts


def _keep_touch_features(centers, verts, kind, data):
    """Which shapes (convex, uniform size) touch the AOI features:
    contain an AOI point, or are crossed/entered by an AOI line."""
    from scipy.spatial import cKDTree

    n = len(centers)
    keep = np.zeros(n, dtype=bool)
    circum = float(np.hypot(verts[0, :, 0] - centers[0, 0],
                            verts[0, :, 1] - centers[0, 1]).max()) * 1.0001

    if kind == "point":
        cand = cKDTree(data).query_ball_point(centers, circum)
        for i, lst in enumerate(cand):
            if lst and _pts_in_convex(data[np.asarray(lst)],
                                      verts[i]).any():
                keep[i] = True
        return keep

    # lines: bbox prefilter -> proper-crossing test -> line-inside-shape
    p_seg = np.vstack([a[:-1] for a in data])
    q_seg = np.vstack([a[1:] for a in data])
    line_edges = np.hstack([p_seg, q_seg])
    e_xmin = np.minimum(line_edges[:, 0], line_edges[:, 2])
    e_xmax = np.maximum(line_edges[:, 0], line_edges[:, 2])
    e_ymin = np.minimum(line_edges[:, 1], line_edges[:, 3])
    e_ymax = np.maximum(line_edges[:, 1], line_edges[:, 3])
    vx_min = verts[..., 0].min(axis=1)
    vx_max = verts[..., 0].max(axis=1)
    vy_min = verts[..., 1].min(axis=1)
    vy_max = verts[..., 1].max(axis=1)
    near = np.zeros(n, dtype=bool)
    step = max(1, int(4e7 // max(len(line_edges), 1)))
    for k0 in range(0, n, step):
        k1 = min(n, k0 + step)
        hit = ((e_xmin[None, :] <= vx_max[k0:k1, None])
               & (e_xmax[None, :] >= vx_min[k0:k1, None])
               & (e_ymin[None, :] <= vy_max[k0:k1, None])
               & (e_ymax[None, :] >= vy_min[k0:k1, None]))
        near[k0:k1] = hit.any(axis=1)
    idx = np.flatnonzero(near)
    if not len(idx):
        return keep
    k = verts.shape[1]
    for c0 in range(0, len(idx), step):
        sub = idx[c0:c0 + step]
        p = verts[sub].reshape(-1, 2)
        q = verts[sub][:, list(range(1, k)) + [0], :].reshape(-1, 2)
        cross = _pa._segments_properly_cross(
            p, q, line_edges).reshape(len(sub), k).any(axis=1)
        keep[sub[cross]] = True
    # a line lying ENTIRELY inside one shape crosses no edge: any line
    # vertex inside the shape
    rest = idx[~keep[idx]]
    if len(rest):
        allv = np.vstack(data)
        cand = cKDTree(allv).query_ball_point(centers[rest], circum)
        for j, lst in enumerate(cand):
            if lst and _pts_in_convex(allv[np.asarray(lst)],
                                      verts[rest[j]]).any():
                keep[rest[j]] = True
    return keep


def _sample_segments(parts, step):
    """All part vertices plus intermediate samples every ``step`` along
    each segment (vectorized per distinct subdivision count)."""
    out = list(parts)
    for a in parts:
        p, q = a[:-1], a[1:]
        seg_len = np.hypot(q[:, 0] - p[:, 0], q[:, 1] - p[:, 1])
        nseg = np.maximum(1, np.ceil(seg_len / max(step, 1e-12))
                          .astype(np.int64))
        for cnt in np.unique(nseg):
            if cnt == 1:
                continue
            sel = nseg == cnt
            t = np.linspace(0.0, 1.0, int(cnt) + 1)[1:-1]
            mid = (p[sel][:, None, :]
                   + (q[sel] - p[sel])[:, None, :] * t[None, :, None])
            out.append(mid.reshape(-1, 2))
    return np.vstack(out)


def _keep_tiles_features(centers, shape_type, size, kind, data):
    """Which lattice points' implied TILE (the lattice's Voronoi cell:
    a square of edge ``spacing`` for the square arrangement, a hexagon
    for the triangular) touches the AOI features -- a point lattice has
    no extent of its own, so the tile plays the shape's role. Membership
    = nearest lattice center, which IS the Voronoi assignment."""
    from scipy.spatial import cKDTree

    s = float(size["spacing"])
    r = s / math.sqrt(2.0) if shape_type == POINTS_SQ \
        else s / math.sqrt(3.0)
    samples = data if kind == "point" else _sample_segments(data, 0.5 * s)
    d, idx = cKDTree(centers).query(samples)
    keep = np.zeros(len(centers), dtype=bool)
    keep[np.unique(idx[d <= r * 1.0001])] = True
    return keep


def _laea_for(gcs_sr, lon0, lat0):
    """A Lambert azimuthal EQUAL-AREA projection centered on the area of
    interest, built on the input's OWN geographic CS (same datum, so no
    transformation is ever involved).

    Truly regular equal-area tessellations of a curved surface are
    impossible (Euler's formula puts 12 pentagons into any hex-dominant
    sphere tiling), so something must flex: computing in an AOI-centered
    LAEA keeps cell AREAS exact and lets shape regularity absorb the
    (imperceptible at regional scales, ~(d/2R)^2) distortion. Global-
    scale work belongs to discrete global grids (ISEA3H, H3), a
    different tool."""
    import arcpy

    gcs_wkt = gcs_sr.exportToString().split(";")[0]
    wkt = ('PROJCS["Repeating_Shapes_LAEA",{gcs},'
           'PROJECTION["Lambert_Azimuthal_Equal_Area"],'
           'PARAMETER["False_Easting",0.0],'
           'PARAMETER["False_Northing",0.0],'
           'PARAMETER["Central_Meridian",{lon}],'
           'PARAMETER["Latitude_Of_Origin",{lat}],'
           'UNIT["Meter",1.0]]').format(gcs=gcs_wkt, lon=lon0, lat=lat0)
    sr = arcpy.SpatialReference()
    sr.loadFromString(wkt)
    return sr


# ---------------------------------------------------------------------------
#  orchestrator
# ---------------------------------------------------------------------------
def run_repeating_shapes(shape_type, out_fc, boundary=None, extent=None,
                         area=None, edge=None, height=None, width=None,
                         diameter=None, radius=None, circumference=None,
                         spacing=None, offset_pct=50.0,
                         area_units="Square Meters",
                         linear_units="Meters",
                         orientation=0.0, edge_policy=KEEP_INTERSECT,
                         messages=None):
    """Generate the lattice. Sizes are in ``area_units``/``linear_units``
    and are converted to the output CS internally. Returns the output
    path."""
    import arcpy

    if shape_type not in SHAPE_TYPES:
        raise arcpy.ExecuteError(
            "Unknown shape type: {}".format(shape_type))
    orientation = float(orientation or 0.0)
    extent = _vt.parse_extent(extent) or extent
    is_points = shape_type in (POINTS_SQ, POINTS_TRI)
    if area_units not in _AREA_M2:
        raise arcpy.ExecuteError(
            "Unknown area unit: {}".format(area_units))
    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown linear unit: {}".format(linear_units))

    # coordinate system: env else boundary/extent CS
    src_sr = None
    if boundary not in (None, ""):
        src_sr = arcpy.Describe(boundary).spatialReference
    elif isinstance(extent, arcpy.Extent) \
            and extent.spatialReference is not None:
        src_sr = extent.spatialReference
    out_sr = arcpy.env.outputCoordinateSystem or src_sr
    if out_sr is None or getattr(out_sr, "name", "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "No coordinate system: give a boundary/extent with one, or "
            "set the Output Coordinate System environment.")
    geographic = out_sr.type == "Geographic"

    _mh.begin_phases([("Reading the area of interest...", 1),
                      ("Generating the lattice...", 2),
                      ("Filtering and writing the shapes...", 4)])

    # ---- working space ---------------------------------------------------
    # Geographic output: an AOI-centered Lambert azimuthal EQUAL-AREA
    # working projection keeps cell areas exact (regular equal-area
    # tessellations of a curved surface are impossible; shape regularity
    # absorbs the imperceptible regional distortion instead). Everything
    # is computed there and the results are projected back to the input
    # geographic CS on write.
    if geographic:
        lon0 = lat0 = None
        try:
            if boundary not in (None, ""):
                be0 = arcpy.Describe(boundary).extent
                lon0 = 0.5 * (be0.XMin + be0.XMax)
                lat0 = 0.5 * (be0.YMin + be0.YMax)
            elif isinstance(extent, arcpy.Extent):
                lon0 = 0.5 * (extent.XMin + extent.XMax)
                lat0 = 0.5 * (extent.YMin + extent.YMax)
            elif extent not in (None, ""):
                parts = [float(v) for v in str(extent).split()[:4]]
                lon0 = 0.5 * (parts[0] + parts[2])
                lat0 = 0.5 * (parts[1] + parts[3])
        except Exception:                                        # noqa: BLE001
            pass
        if lon0 is None:
            raise arcpy.ExecuteError(
                "Give an area-of-interest layer or an extent (or set "
                "the Extent environment).")
        work_sr = _laea_for(out_sr, lon0, lat0)
        _say(messages,
             "Geographic data: computing in an automatic EQUAL-AREA "
             "working projection (Lambert azimuthal, centered on the "
             "area at {:.4f}, {:.4f}); cell areas are exact and the "
             "output is written back in {}.".format(lon0, lat0,
                                                    out_sr.name))
    else:
        work_sr = out_sr

    # area of interest -----------------------------------------------------
    # polygons ENCLOSE the area; polyline/point features (a river,
    # observation locations) instead SELECT the shapes that touch them
    bnd = None
    feat_kind = feat_data = None
    if boundary not in (None, ""):
        shp_kind = str(getattr(arcpy.Describe(boundary), "shapeType",
                               "Polygon"))
        if shp_kind == "Polygon":
            bnd = _vt._read_boundary(boundary, work_sr, messages)
        else:
            feat_kind, feat_data = _read_features(boundary, work_sr,
                                                  shp_kind, messages)
    env_ext = arcpy.env.extent
    if env_ext not in (None, "", "DEFAULT", "MAXOF", "MINOF") \
            and not isinstance(env_ext, str):
        esr = env_ext.spatialReference
        if esr is None or esr.name in (None, "", "Unknown"):
            ext_poly = _vt._rect_polygon(env_ext.XMin, env_ext.YMin,
                                         env_ext.XMax, env_ext.YMax,
                                         out_sr)
        else:
            ext_poly = env_ext.polygon
        ext = _vt._project_geom(ext_poly, work_sr).extent \
            if not _vt._same_cs(ext_poly.spatialReference, work_sr) \
            else ext_poly.extent
        frame = (ext.XMin, ext.YMin, ext.XMax, ext.YMax)
        _say(messages, "Area of interest: the Extent geoprocessing "
                       "environment.")
    elif extent not in (None, ""):
        e = extent if isinstance(extent, arcpy.Extent) else None
        if e is None:
            parts = [float(v) for v in str(extent).split()[:4]]
            e = arcpy.Extent(parts[0], parts[1], parts[2], parts[3],
                             None, None, None, None, out_sr)
        esr = e.spatialReference
        ep = _vt._rect_polygon(e.XMin, e.YMin, e.XMax, e.YMax,
                               esr if esr is not None
                               and esr.name not in (None, "", "Unknown")
                               else out_sr)
        if not _vt._same_cs(ep.spatialReference, work_sr):
            ep = _vt._project_geom(ep, work_sr)
        fe = ep.extent
        frame = (fe.XMin, fe.YMin, fe.XMax, fe.YMax)
    elif bnd is not None:
        be = bnd.extent
        frame = (be.XMin, be.YMin, be.XMax, be.YMax)
    elif feat_data is not None:
        allc = feat_data if feat_kind == "point" else np.vstack(feat_data)
        frame = (float(allc[:, 0].min()), float(allc[:, 1].min()),
                 float(allc[:, 0].max()), float(allc[:, 1].max()))
    else:
        raise arcpy.ExecuteError(
            "Give an area-of-interest layer or an extent (or set the "
            "Extent environment).")

    if geographic and max(frame[2] - frame[0],
                          frame[3] - frame[1]) > 6.0e6:
        _warn(messages,
              "The area of interest spans thousands of kilometers -- "
              "shape regularity degrades far from the projection "
              "center. Continental/global tessellation belongs to "
              "discrete global grid systems (ISEA3H, H3), which accept "
              "12 pentagons in exchange for global equal-area coverage.")

    # ---- unit conversion to working-CS units ----------------------------
    m_per_cs = float(work_sr.metersPerUnit or 1.0)
    lf = _LIN_M[linear_units] / m_per_cs           # linear value -> CS
    af = _AREA_M2[area_units] / (m_per_cs ** 2)    # area value -> CS^2

    def cvt(v, f):
        return float(v) * f if v not in (None, "") else None

    try:
        size = resolve_size(
            shape_type, area=cvt(area, af), edge=cvt(edge, lf),
            height=cvt(height, lf), width=cvt(width, lf),
            diameter=cvt(diameter, lf), radius=cvt(radius, lf),
            circumference=cvt(circumference, lf),
            spacing=cvt(spacing, lf), offset_pct=offset_pct)
    except ValueError as e:
        raise arcpy.ExecuteError(str(e))
    size_note = ", ".join(
        "{} {:g} CS units".format(k, v) for k, v in sorted(size.items()))
    _say(messages, "Sizes ({} / {}): {}.".format(area_units,
                                                 linear_units, size_note))

    if bnd is not None:
        # standing rule: densify true-curve polygons (gdb buffer circles,
        # arcs) before vertex-walking -- ``rings_of`` walks JSON vertices,
        # so an undensified circle collapses to its arc endpoints
        if getattr(bnd, "hasCurves", False):
            # only the LINEAR measures set the step: "area" is squared
            # units and the brick "offset" is a percent, not a length
            dstep = 0.5 * min([v for k, v in size.items()
                               if k not in ("area", "offset") and v]
                              or [1.0])
            try:
                bnd = bnd.densify("DISTANCE", dstep, dstep)
            except Exception:                                    # noqa: BLE001
                pass
        clip_geom = bnd
        rings = _pa.rings_of(bnd)
        bnd_edges = _pa.edges_of(rings)
        bands = _pa.y_bands(bnd_edges)
    elif feat_data is not None:
        clip_geom = bnd_edges = bands = None
    else:
        clip_geom = _vt._rect_polygon(*frame, work_sr)
        rings = [np.array([[frame[0], frame[1]], [frame[0], frame[3]],
                           [frame[2], frame[3]], [frame[2], frame[1]]])]
        bnd_edges = _pa.edges_of(rings)
        bands = _pa.y_bands(bnd_edges)
    _mh.phase_advance()

    # lattice --------------------------------------------------------------
    about = np.array([0.5 * (frame[0] + frame[2]),
                      0.5 * (frame[1] + frame[3])])
    corners = np.array([[frame[0], frame[1]], [frame[0], frame[3]],
                        [frame[2], frame[3]], [frame[2], frame[1]]])
    corners_l = _rotate(corners, -orientation, about)
    centers_l, verts_l = build_lattice(shape_type, size, corners_l)
    centers = _rotate(centers_l, orientation, about)
    verts = _rotate(verts_l, orientation, about) \
        if verts_l is not None else None
    _say(messages,
         "{:,} candidate shape{} generated (orientation {:g} "
         "degrees).".format(len(centers),
                            "" if len(centers) == 1 else "s",
                            orientation))
    _mh.phase_advance()

    # filter ---------------------------------------------------------------
    if feat_data is not None:
        # line/point AOI: no inside/outside exists, so the edge policies
        # do not apply -- keep whatever touches the features
        if not is_points and edge_policy in (KEEP_CENTER, CLIP):
            _warn(messages,
                  "'{}' needs a polygon area of interest; with {} "
                  "features every shape touching them is kept "
                  "whole.".format(edge_policy, feat_kind))
        if is_points:
            keep = _keep_tiles_features(centers, shape_type, size,
                                        feat_kind, feat_data)
            policy = ("points whose lattice tile touches the {} "
                      "features".format(feat_kind))
        else:
            keep = _keep_touch_features(centers, verts, feat_kind,
                                        feat_data)
            policy = "shapes touching the {} features".format(feat_kind)
        border = np.zeros(len(centers), dtype=bool)
        kept = np.flatnonzero(keep)
        do_clip = False
    else:
        policy = edge_policy if not is_points else KEEP_CENTER
        keep, border = _keep_mask(centers, verts, bnd_edges, bands,
                                  policy if policy in EDGE_POLICIES
                                  else KEEP_INTERSECT, rings=rings)
        kept = np.flatnonzero(keep)
        do_clip = (policy == CLIP) and verts is not None

    # write ----------------------------------------------------------------
    out_fc = str(out_fc).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_fc)
    if ws and not _mh.in_gdb_container(out_fc) \
            and not name.lower().endswith(".shp"):
        name += ".shp"
    res = arcpy.management.CreateFeatureclass(
        ws, name, "POINT" if is_points else "POLYGON",
        spatial_reference=_vt.clean_sr(out_sr))
    out_path = res.getOutput(0)
    rows = [["Cell_ID", "LONG", "Cell ID", "", "", ""]]
    if not is_points:
        rows.append(["Area_Ha", "DOUBLE", "Area (geodesic hectares)",
                     "", "", ""])
    arcpy.management.AddFields(out_path, rows)

    n = 0
    if is_points:
        xy = centers[kept]
        if geographic and len(xy):
            # one batched projection back to the output CS instead of
            # a PointGeometry .projectAs round-trip per lattice point
            import random_points as _rp
            xy = _rp._project_xy(xy, work_sr, out_sr)
        with arcpy.da.InsertCursor(out_path,
                                   ["SHAPE@XY", "Cell_ID"]) as ins:
            for x, y in xy:
                n += 1
                ins.insertRow([(float(x), float(y)), n])
    else:
        n_clipped = 0
        is_shp = str(out_path).lower().endswith(".shp")
        with arcpy.da.InsertCursor(
                out_path, ["SHAPE@", "Cell_ID", "Area_Ha"]) as ins:
            for j, i in enumerate(kept):
                if (j + 1) % 500 == 0:
                    _mh.phase_step(j + 1, len(kept))
                ring = verts[i]
                geom = arcpy.Polygon(
                    arcpy.Array([arcpy.Point(x, y) for x, y in ring]
                                + [arcpy.Point(ring[0, 0], ring[0, 1])]),
                    work_sr)
                if do_clip and border[i]:
                    geom = geom.intersect(clip_geom, 4)
                    if geom is None or geom.area == 0:
                        continue
                    n_clipped += 1
                if geographic:
                    geom = geom.projectAs(out_sr)
                n += 1
                try:
                    ha = geom.getArea("GEODESIC", "HECTARES")
                except Exception:                                # noqa: BLE001
                    # shapefiles cannot hold nulls (a None insert
                    # raises), so the classic -999 stands in
                    ha = -999.0 if is_shp else None
                ins.insertRow([geom, n, ha])
        if do_clip and n_clipped:
            _say(messages, "{:,} border shape{} clipped to the "
                           "area.".format(n_clipped,
                                          "" if n_clipped == 1 else "s"))
    _say(messages, "{:,} {} written ({}).".format(
        n, shape_type.lower(),
        "points" if is_points else policy.lower()))

    _stamp(out_path, shape_type, size, orientation, boundary, frame,
           policy, n, area_units, linear_units, feat_kind)
    _mh.end_phases()
    return out_path


def _stamp(out_path, shape_type, size, orientation, boundary, frame,
           policy, n, area_units, linear_units, feat_kind=None):
    import arcpy

    try:
        size_txt = ", ".join("{} {:g}".format(k, v)
                             for k, v in sorted(size.items()))
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = (
            "Created by Repeating Shapes: {n} {t} ({sz} in output-CS "
            "units, from sizes given in {au}/{lu}; orientation {o:g} "
            "degrees; {p}) over {aoi}.".format(
                n=n, t=shape_type.lower(), sz=size_txt, au=area_units,
                lu=linear_units, o=orientation, p=policy.lower(),
                aoi=("{} features from {}".format(
                        feat_kind or "polygon",
                        os.path.basename(str(boundary))))
                    if boundary not in (None, "") else
                    "the extent {:.6g}, {:.6g} to {:.6g}, {:.6g}".format(
                        *frame)))
        md.description = (
            "Repeating-shapes lattice: a regular tessellation or "
            "sampling arrangement covering the area of interest, from "
            "the Jenness Enterprises Repeating Shapes tool (a modernized "
            "port of the classic ArcGIS extension of the same name by "
            "Jeff Jenness).")
        md.tags = ("repeating shapes, tessellation, hexagons, sampling "
                   "grid, lattice, Jenness Enterprises")
        md.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                      "(http://www.jennessent.com).")
        md.save()
    except Exception:                                            # noqa: BLE001
        pass
