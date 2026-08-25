# -*- coding: utf-8 -*-
"""Radiating Lines and Points -- evenly spaced lines radiating from
every input point, with the lines and/or their endpoints as outputs.

A modernized port of Jeff Jenness's ArcView 3.x "Radiating Lines and
Points" extension (rad_lines.avx, v1.1) -- originally developed for
Jan Ekebom, Pasi Laihonen and Tapio Suominen (University of Turku)
for their study of Finnish coastal biodiversity, and cited in Ekebom,
Laihonen and Suominen (2003, Estuarine, Coastal and Shelf Science
57:887-898).

The classic behavior is kept: give either the number of lines or the
bearing interval (they are two views of the same choice), a line
length, and optionally a randomized first bearing (the remaining
lines follow clockwise at the regular interval); outputs are a
polyline feature class of the radiating lines and/or a point feature
class of the origins and endpoints, with the original attribute
design (origin ID, per-origin line number, distance, bearing, and
both ends' coordinates).

Modernized: the line length is in REAL units (meters, kilometers,
feet or miles) -- for geographic data every point gets its own
azimuthal-equidistant projection, so radial distances are true
geodesics and bearings are true azimuths (the original worked in raw
map units and told users to project first); an optional random seed
makes randomized bearings reproducible; and -- new in this version --
the lines can be BLOCKED by any number of polyline and/or polygon
barrier feature classes (layer selections honored): a radiating line
stops at its first crossing of a barrier line or polygon BOUNDARY,
with a Blocked flag recording the truncation. It is the boundary
that blocks -- an origin inside a barrier polygon still radiates,
each line stopping where it reaches the boundary from the inside.
"""
import math
import os

import numpy as np

import mahalanobis as _mh
import patch_analysis as _pa
import centerline_width as _cw
import longest_line as _ll
import repeating_shapes as _rs
import voronoi_tools as _vt

_say = _cw._say
_warn = _cw._warn
LINEAR_UNITS = _rs.LINEAR_UNITS
_LIN_M = _rs._LIN_M


def _layer_list(spec):
    """Normalize a barrier input -- None, a single layer or path, a
    semicolon-delimited multivalue string, or a list of any of those
    -- to a flat list of layers/paths."""
    if spec in (None, ""):
        return []
    if isinstance(spec, (list, tuple)):
        out = []
        for s in spec:
            out.extend(_layer_list(s))
        return out
    if isinstance(spec, str) and ";" in spec:
        return [s.strip().strip("'") for s in spec.split(";")
                if s.strip().strip("'")]
    return [spec]


def _blocker_edges(layers, densify_len, out_sr, messages):
    """One (E, 4) edge array from the barrier layers (polylines and
    polygons; layer selections honored; true curves densified), with
    every edge's coordinates brought into ``out_sr`` -- each layer is
    projected individually, so the barriers may live in any mix of
    coordinate systems."""
    import arcpy
    import json

    all_edges = []
    n_feat = 0
    for lyr in layers:
        if lyr in (None, ""):
            continue
        n_total, n_sel, has_sel = _mh.selection_info(lyr, str(lyr))
        if has_sel:
            _say(messages,
                 "Barrier layer {}: an active selection limits it to "
                 "{} of {} features.".format(
                     os.path.basename(str(lyr)), n_sel, n_total))
        segs = []
        with arcpy.da.SearchCursor(lyr, ["SHAPE@"]) as cur:
            for (g,) in cur:
                if g is None:
                    continue
                n_feat += 1
                if getattr(g, "hasCurves", False):
                    d = max(g.length / 500.0, densify_len)
                    try:
                        g = g.densify("DISTANCE", d, d)
                    except Exception:                            # noqa: BLE001
                        pass
                js = json.loads(g.JSON)
                paths = js.get("paths") or js.get("rings") or []
                for p in paths:
                    a = np.asarray(p, dtype=np.float64)[:, :2]
                    if len(a) >= 2:
                        segs.append(np.column_stack([a[:-1], a[1:]]))
        if not segs:
            continue
        e = np.vstack(segs)
        lsr = arcpy.Describe(lyr).spatialReference
        if lsr is not None and getattr(lsr, "name", "Unknown") \
                != "Unknown" and not _vt._same_cs(lsr, out_sr):
            import random_points as _rp
            a = _rp._project_xy(e[:, :2], lsr, out_sr)
            b = _rp._project_xy(e[:, 2:], lsr, out_sr)
            e = np.column_stack([a, b])
        all_edges.append(e)
    if not all_edges:
        return np.zeros((0, 4)), 0
    return np.vstack(all_edges), n_feat


def run_radiating_lines(points, n_lines=None, bearing_interval=None,
                        line_length=None, linear_units="Meters",
                        randomize_first=False, random_seed=None,
                        block_lines=None, block_polygons=None,
                        transfer_fields=None, out_lines=None,
                        out_points=None, messages=None):
    """Generate the radiating lines and/or endpoint points. Give
    ``n_lines`` or ``bearing_interval`` (either determines the
    other); ``line_length`` in ``linear_units``. Returns the primary
    output path (lines if requested, else points)."""
    import arcpy

    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown distance unit: {}".format(linear_units))
    if line_length in (None, "", 0) or float(line_length) <= 0:
        raise arcpy.ExecuteError(
            "Give a line length greater than zero.")
    length_m = float(line_length) * _LIN_M[linear_units]
    if n_lines not in (None, "", 0):
        k = int(n_lines)
        if k < 1:
            raise arcpy.ExecuteError("Give at least one line.")
        interval = 360.0 / k
    elif bearing_interval not in (None, "", 0):
        interval = float(bearing_interval)
        if interval <= 0 or interval > 360:
            raise arcpy.ExecuteError(
                "Give a bearing interval between 0 and 360 degrees.")
        k = max(1, int(round(360.0 / interval)))
        interval = 360.0 / k
    else:
        raise arcpy.ExecuteError(
            "Give the number of lines or the bearing interval.")
    if not out_lines and not out_points:
        raise arcpy.ExecuteError(
            "Choose at least one output (lines and/or points).")

    pdesc = arcpy.Describe(points)
    if getattr(pdesc, "shapeType", "") != "Point":
        raise arcpy.ExecuteError(
            "The input must be point features.")
    out_sr = pdesc.spatialReference
    if out_sr is None or getattr(out_sr, "name",
                                 "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "The points input has no coordinate system.")
    geographic = out_sr.type == "Geographic"
    try:
        unit_m = float(getattr(out_sr, "metersPerUnit", 1.0) or 1.0)
    except Exception:                                            # noqa: BLE001
        unit_m = 1.0

    rng = np.random.default_rng(
        int(random_seed) if random_seed not in (None, "") else None)

    _mh.begin_phases([
        ("Reading points and barriers...", 1),
        ("Casting the radiating lines...", 4),
        ("Writing outputs...", 1),
    ])
    plan = _cw._transfer_plan(
        points, transfer_fields,
        _ll._is_shp_dest(out_lines) or _ll._is_shp_dest(out_points),
        messages)
    fields = [p[0] for p in plan]

    n_total, n_sel, has_sel = _mh.selection_info(points, str(points))
    if has_sel:
        _say(messages, "The point layer has an active selection: {} "
                       "of {} points are used.".format(n_sel,
                                                       n_total))
    pts = []
    with arcpy.da.SearchCursor(points,
                               ["OID@", "SHAPE@"] + fields) as cur:
        for row in cur:
            if row[1] is not None:
                pts.append((row[0], row[1], row[2:]))
    if not pts:
        raise arcpy.ExecuteError(
            "The points input has no features (empty dataset, or an "
            "active selection selecting nothing).")

    densify_len = (length_m / 500.0) / (111000.0 if geographic
                                        else unit_m)
    blockers = _layer_list(block_lines) + _layer_list(block_polygons)
    edges_nat, n_barrier = _blocker_edges(
        blockers, densify_len, out_sr, messages)
    blocking = len(edges_nat) > 0
    if blocking:
        _say(messages,
             "{:,} barrier feature{} block the lines.".format(
                 n_barrier, "" if n_barrier == 1 else "s"))
    _say(messages,
         "{:,} point{}; {} line{} each, every {:g} degrees, {:g} {} "
         "long{}.".format(
             len(pts), "" if len(pts) == 1 else "s", k,
             "" if k == 1 else "s", interval, float(line_length),
             linear_units.lower(),
             "; first bearing randomized" if randomize_first else ""))
    _mh.phase_advance()

    import random_points as _rp
    line_rows = []
    point_rows = []
    for si, (oid, pgeom, attrs) in enumerate(pts):
        p0 = pgeom.firstPoint
        px, py = float(p0.X), float(p0.Y)
        start = float(rng.uniform(0.0, interval)) \
            if randomize_first else 0.0
        bearings = (start + interval * np.arange(k)) % 360.0
        if geographic:
            wsr, _c = _pa._working_sr(
                arcpy.PointGeometry(arcpy.Point(px, py), out_sr))
            sx = sy = 0.0
            cap = length_m
            to_m = 1.0
            if blocking:
                rlat = length_m / 109000.0
                rlon = length_m / (109000.0 * max(
                    math.cos(math.radians(min(abs(py), 89.0))),
                    0.05))
                m = ~((np.maximum(edges_nat[:, 0], edges_nat[:, 2])
                       < px - rlon)
                      | (np.minimum(edges_nat[:, 0],
                                    edges_nat[:, 2]) > px + rlon)
                      | (np.maximum(edges_nat[:, 1],
                                    edges_nat[:, 3]) < py - rlat)
                      | (np.minimum(edges_nat[:, 1],
                                    edges_nat[:, 3]) > py + rlat))
                e_sub = edges_nat[m]
                if len(e_sub):
                    a = _rp._project_xy(e_sub[:, :2], out_sr, wsr)
                    b = _rp._project_xy(e_sub[:, 2:], out_sr, wsr)
                    e_loc = np.column_stack([a, b])
                else:
                    e_loc = np.zeros((0, 4))
            else:
                e_loc = np.zeros((0, 4))
        else:
            wsr = out_sr
            sx, sy = px, py
            cap = length_m / unit_m
            to_m = unit_m
            if blocking:
                r_nat = cap
                m = ~((np.maximum(edges_nat[:, 0], edges_nat[:, 2])
                       < px - r_nat)
                      | (np.minimum(edges_nat[:, 0],
                                    edges_nat[:, 2]) > px + r_nat)
                      | (np.maximum(edges_nat[:, 1],
                                    edges_nat[:, 3]) < py - r_nat)
                      | (np.minimum(edges_nat[:, 1],
                                    edges_nat[:, 3]) > py + r_nat))
                e_loc = edges_nat[m]
            else:
                e_loc = np.zeros((0, 4))
        if len(e_loc):
            dists, capped = _ll.radial_fetch(sx, sy, bearings, e_loc,
                                             cap)
            blocked = dists < cap - 1e-9
        else:
            dists = np.full(k, cap)
            blocked = np.zeros(k, dtype=bool)
        dists_m = dists * to_m

        brad = np.radians(bearings)
        exs = sx + dists * np.sin(brad)
        eys = sy + dists * np.cos(brad)
        # endpoint coordinates in the OUTPUT coordinate system
        if geographic:
            ends = _rp._project_xy(np.column_stack([exs, eys]), wsr,
                                   out_sr)
        else:
            ends = np.column_stack([exs, eys])
        for j in range(k):
            ex, ey = float(ends[j, 0]), float(ends[j, 1])
            if out_lines:
                shp = arcpy.Polyline(arcpy.Array(
                    [arcpy.Point(px, py), arcpy.Point(ex, ey)]),
                    out_sr)
                line_rows.append(tuple(
                    [shp, oid, j + 1, float(bearings[j]),
                     float(dists_m[j]), int(bool(blocked[j])),
                     px, py, ex, ey] + list(attrs)))
            if out_points:
                point_rows.append(tuple(
                    [arcpy.PointGeometry(arcpy.Point(ex, ey),
                                         out_sr),
                     oid, j + 1, "End Point", float(dists_m[j]),
                     float(bearings[j]), ex, ey] + list(attrs)))
        if out_points:
            point_rows.append(tuple(
                [arcpy.PointGeometry(arcpy.Point(px, py), out_sr),
                 oid, 0, "Origin", None, None, px, py]
                + list(attrs)))
        _mh.phase_step(si + 1, len(pts))
    _mh.phase_advance()

    out_names = [p[1] for p in plan]
    xfer_rows = [[out, ftype,
                  ("Source field " + src) if out != src else src,
                  flen, "", ""] for src, out, ftype, flen in plan]
    primary = None
    if out_lines:
        out_lines = _ll._make_fc(out_lines, "POLYLINE", out_sr)
        arcpy.management.AddFields(out_lines, [
            ["Src_FID", "LONG", "Origin point Object ID", "", "", ""],
            ["Origin_N", "LONG", "Line number around its origin",
             "", "", ""],
            ["Bearing", "DOUBLE", "Azimuth (degrees)", "", "", ""],
            ["Length_m", "DOUBLE", "Line length (m)", "", "", ""],
            ["Blocked", "SHORT", "1 = stopped at a barrier",
             "", "", ""],
            ["Org_X", "DOUBLE", "Origin X", "", "", ""],
            ["Org_Y", "DOUBLE", "Origin Y", "", "", ""],
            ["End_X", "DOUBLE", "End X", "", "", ""],
            ["End_Y", "DOUBLE", "End Y", "", "", ""]] + xfer_rows)
        with arcpy.da.InsertCursor(
                out_lines,
                ["SHAPE@", "Src_FID", "Origin_N", "Bearing",
                 "Length_m", "Blocked", "Org_X", "Org_Y", "End_X",
                 "End_Y"] + out_names) as ins:
            for row in line_rows:
                ins.insertRow(row)
        primary = out_lines
    if out_points:
        out_points = _ll._make_fc(out_points, "POINT", out_sr)
        arcpy.management.AddFields(out_points, [
            ["Src_FID", "LONG", "Origin point Object ID", "", "", ""],
            ["Origin_N", "LONG",
             "Point number around its origin (0 = the origin)",
             "", "", ""],
            ["Location", "TEXT", "Origin / End Point", 10, "", ""],
            ["Length_m", "DOUBLE", "Distance from the origin (m)",
             "", "", ""],
            ["Bearing", "DOUBLE", "Azimuth from the origin (degrees)",
             "", "", ""],
            ["X_Coord", "DOUBLE", "X", "", "", ""],
            ["Y_Coord", "DOUBLE", "Y", "", "", ""]] + xfer_rows)
        is_shp = str(out_points).lower().endswith(".shp")
        with arcpy.da.InsertCursor(
                out_points,
                ["SHAPE@", "Src_FID", "Origin_N", "Location",
                 "Length_m", "Bearing", "X_Coord",
                 "Y_Coord"] + out_names) as ins:
            for row in point_rows:
                row = list(row)
                if is_shp:
                    # the classic extension wrote -999 for the
                    # origin's distance/bearing; shapefiles cannot
                    # hold nulls, so the convention lives on there
                    if row[4] is None:
                        row[4] = -999.0
                    if row[5] is None:
                        row[5] = -999.0
                ins.insertRow(tuple(row))
        if primary is None:
            primary = out_points

    n_l = len(line_rows)
    n_p = len(point_rows)
    _say(messages, "{}{}{} written.".format(
        "{:,} radiating lines".format(n_l) if out_lines else "",
        " and " if (out_lines and out_points) else "",
        "{:,} points ({:,} origins + {:,} end points)".format(
            n_p, len(pts), n_p - len(pts)) if out_points else ""))
    if blocking:
        n_b = sum(1 for r in line_rows if r[5] == 1) if out_lines \
            else None
        if n_b is not None:
            _say(messages,
                 "{:,} of the lines stopped at a barrier.".format(
                     n_b))

    for path, what in ((out_lines if out_lines else None,
                        "radiating lines"),
                       (out_points if out_points else None,
                        "origin and end points")):
        if not path:
            continue
        try:
            md = arcpy.metadata.Metadata(path)
            md.title = os.path.basename(str(path))
            md.summary = (
                "Created by Radiating Lines and Points: {} ({} lines "
                "per point every {:g} degrees, {:g} {} long{}{}).".format(
                    what, k, interval, float(line_length),
                    linear_units.lower(),
                    "; first bearing randomized"
                    if randomize_first else "",
                    "; blocked by barrier features"
                    if blocking else ""))
            md.description = (
                "From the Jenness Enterprises Radiating Lines and "
                "Points tool -- a modernized port of the classic "
                "ArcView 3.x extension (rad_lines.avx), originally "
                "developed for Jan Ekebom, Pasi Laihonen and Tapio "
                "Suominen (University of Turku) and cited in Ekebom "
                "et al. (2003, Estuarine, Coastal and Shelf Science "
                "57:887-898). Evenly spaced lines radiate from every "
                "input point; lengths are true geodesic meters "
                "(geographic points each use their own "
                "azimuthal-equidistant projection), and lines "
                "optionally stop at the first crossing of barrier "
                "polylines or polygon boundaries.")
            md.save()
        except Exception:                                        # noqa: BLE001
            pass
    _mh.end_phases()
    return primary
