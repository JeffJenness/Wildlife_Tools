# -*- coding: utf-8 -*-
"""Estimated Shortest Path through Points -- the arcpy-facing wrapper
around route_core.  Reads the stops (and, per mode, a shared
start/end anchor or separate start and end points), solves the route
in an equal-area working projection when the data are geographic,
and writes the route polyline plus an optional visit-ordered copy of
the stops.  Straight-line legs only -- this is a route between
locations as the crow flies, not a road-network route."""

import os

import numpy as np

import mahalanobis as _mh
import random_points as _rp
import repeating_shapes as _rs
import route_core as _rc
import voronoi_tools as _vt

MODE_SIMPLE = "Simple Shortest Path"
MODE_LOOP = "Start and End at Same Point"
MODE_TWO = "Start and End at Different Specified Points"
MODES = [MODE_SIMPLE, MODE_LOOP, MODE_TWO]

MAX_STOPS = 8000


def canon_mode(text):
    """Canonical mode from user/script text. Full names always
    match; abbreviations from scripts match when they reach the
    distinguishing character (the two "Start and End at ..." modes
    first differ at character 17, so e.g. "Simple" and
    "Start and End at Same" work, bare "Start and End" does not)."""
    if text in (None, ""):
        return MODE_SIMPLE
    t = str(text).strip().lower()
    for m in MODES:
        # full names and long forms: first 18 chars decide
        if t.startswith(m.lower()[:18]):
            return m
    if len(t) >= 6:
        # script abbreviations: accept an unambiguous prefix
        hits = [m for m in MODES if m.lower().startswith(t)]
        if len(hits) == 1:
            return hits[0]
    return None


def _say(messages, txt):
    if messages is not None:
        messages.addMessage(txt)
    else:
        print(txt)


def _read_stops(in_points, work_sr):
    """(xy, src_fid) with multipoints exploded."""
    import arcpy

    xy, fid = [], []
    with arcpy.da.SearchCursor(in_points, ["OID@", "SHAPE@"],
                               spatial_reference=work_sr) as cur:
        for oid, geom in cur:
            if geom is None:
                continue
            for part in geom:
                pnt_iter = part if hasattr(part, "__iter__") \
                    else [part]
                for p in pnt_iter:
                    if p is None:
                        continue
                    xy.append((p.X, p.Y))
                    fid.append(oid)
    return (np.asarray(xy, dtype=np.float64).reshape(-1, 2),
            np.asarray(fid, dtype=np.int64))


def _read_single_point(layer, work_sr, label):
    """Exactly one point (or one selected point) from ``layer``."""
    import arcpy

    xy, _fid = _read_stops(layer, work_sr)
    if len(xy) == 0:
        raise arcpy.ExecuteError(
            "The {} layer holds no point.".format(label))
    if len(xy) > 1:
        raise arcpy.ExecuteError(
            "The {} layer must supply exactly ONE point -- either a "
            "single-point feature class, or one selected point on a "
            "larger layer (it currently supplies {}).".format(
                label, len(xy)))
    return xy[0]


def run_estimated_route(in_points, out_route, mode=MODE_SIMPLE,
                        loop_point=None, start_point=None,
                        end_point=None, out_points=None,
                        attempts=3, messages=None):
    """Solve and write the route.  Returns
    (route_path, n_stops, length_km, points_path_or_None)."""
    import arcpy

    mode = canon_mode(mode)
    if mode is None:
        raise arcpy.ExecuteError("Unknown route mode.")
    if mode == MODE_LOOP and loop_point in (None, ""):
        raise arcpy.ExecuteError(
            "Start and End at Same Point: supply the start/end "
            "point feature class.")
    if mode == MODE_TWO and (start_point in (None, "")
                             or end_point in (None, "")):
        raise arcpy.ExecuteError(
            "Start and End at Different Specified Points: supply "
            "BOTH the start point and the end point.")

    desc = arcpy.Describe(in_points)
    shp = str(getattr(desc, "shapeType", ""))
    if shp not in ("Point", "Multipoint"):
        raise arcpy.ExecuteError(
            "The points to visit must be a POINT feature class or "
            "layer (got {}).".format(shp or "?"))

    # ---- coordinate systems (house pattern) -----------------------------
    src_sr = desc.spatialReference
    out_sr = arcpy.env.outputCoordinateSystem or src_sr
    if out_sr is None or getattr(out_sr, "name",
                                 "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "No coordinate system: give points with one, or set the "
            "Output Coordinate System environment.")
    geographic = out_sr.type == "Geographic"

    n_phases = [("Reading the points...", 1),
                ("Solving the route...", 3),
                ("Writing the route...", 1)]
    _mh.begin_phases(n_phases)

    if geographic:
        # center the equal-area working projection on the MEAN of
        # the stops as read in out_sr -- never on Describe(...)
        # .extent, which is in the INPUT's own CS (a UTM input with
        # a geographic output environment fed meters to _laea_for)
        # and is NaN for memory-workspace inputs (both found in
        # review)
        stops_deg, fids = _read_stops(in_points, out_sr)
        if len(stops_deg) == 0:
            raise arcpy.ExecuteError("The input holds no points.")
        work_sr = _rs._laea_for(out_sr,
                                float(stops_deg[:, 0].mean()),
                                float(stops_deg[:, 1].mean()))
        stops = _rp._project_xy(stops_deg, out_sr, work_sr)
        _say(messages,
             "Geographic data: routing in an automatic equal-area "
             "working projection (Lambert azimuthal); the output is "
             "written back in {}.".format(out_sr.name))
    else:
        work_sr = out_sr
        stops, fids = _read_stops(in_points, work_sr)
        if len(stops) == 0:
            raise arcpy.ExecuteError("The input holds no points.")
    m_per_cs = float(work_sr.metersPerUnit or 1.0)
    if len(stops) > MAX_STOPS:
        raise arcpy.ExecuteError(
            "{:,} points is beyond this tool's practical range "
            "(about {:,}). Split the survey into regions and route "
            "each on its own.".format(len(stops), MAX_STOPS))

    anchor = start = end = None
    if mode == MODE_LOOP:
        anchor = _read_single_point(loop_point, work_sr,
                                    "start/end point")
    elif mode == MODE_TWO:
        start = _read_single_point(start_point, work_sr,
                                   "start point")
        end = _read_single_point(end_point, work_sr, "end point")
    _mh.phase_advance()

    # ---- solve ----------------------------------------------------------
    res = _rc.plan_route(
        stops,
        start=(anchor if mode == MODE_LOOP else start),
        end=end, round_trip=(mode == MODE_LOOP),
        attempts=attempts, seed=0)
    order = res["stop_order"]
    path_work = res["path_xy"]
    _mh.phase_advance()

    # ---- write the route ------------------------------------------------
    path_out = path_work
    if geographic:
        path_out = _rp._project_xy(path_work, work_sr, out_sr)
    out_route = str(out_route).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_route)
    if ws and not _mh.in_gdb_container(out_route) \
            and not name.lower().endswith(".shp"):
        name += ".shp"
    if len(path_out) < 2:
        raise arcpy.ExecuteError(
            "The route needs at least two vertices. Give at least "
            "two points to visit, or one point plus a start/end "
            "point.")
    r = arcpy.management.CreateFeatureclass(
        ws, name, "POLYLINE", spatial_reference=_vt.clean_sr(out_sr))
    route_path = r.getOutput(0)
    arcpy.management.AddFields(route_path, [
        ["Route_Type", "TEXT", "Route type", 48, "", ""],
        ["N_Stops", "LONG", "Points visited", "", "", ""],
        ["Length_km", "DOUBLE", "Route length (km)", "", "", ""],
        ["Length_mi", "DOUBLE", "Route length (miles)", "", "", ""]])
    geom = arcpy.Polyline(arcpy.Array(
        [arcpy.Point(float(x), float(y)) for x, y in path_out]),
        out_sr)
    try:
        length_km = float(geom.getLength("GEODESIC", "KILOMETERS"))
    except Exception:                                    # noqa: BLE001
        length_km = res["length"] * m_per_cs / 1000.0
    with arcpy.da.InsertCursor(
            route_path, ["SHAPE@", "Route_Type", "N_Stops",
                         "Length_km", "Length_mi"]) as ins:
        ins.insertRow([geom, mode, int(len(stops)),
                       length_km, length_km / 1.609344])

    # ---- optional visit-ordered copy of the stops -----------------------
    points_path = None
    if out_points not in (None, ""):
        op = str(out_points).replace("/", "\\").rstrip("\\")
        ws2, name2 = os.path.split(op)
        if ws2 and not _mh.in_gdb_container(op) \
                and not name2.lower().endswith(".shp"):
            name2 += ".shp"
        r2 = arcpy.management.CreateFeatureclass(
            ws2, name2, "POINT",
            spatial_reference=_vt.clean_sr(out_sr))
        points_path = r2.getOutput(0)
        # "Visit_Order" is 11 characters -- one over the shapefile
        # limit, where AddFields silently truncates and the cursor
        # then can't find the field (found in review)
        vo_field = "Visit_Ord" \
            if str(points_path).lower().endswith(".shp") \
            else "Visit_Order"
        arcpy.management.AddFields(points_path, [
            [vo_field, "LONG", "Visit order", "", "", ""],
            ["Src_FID", "LONG", "Source point Object ID", "", "",
             ""],
            ["Leg_km", "DOUBLE",
             "Length of the leg leaving this stop (km)", "", "",
             ""]])
        # stop k sits at path vertex offset+k; its leg runs to the
        # next vertex (the next stop, the end point, or the anchor).
        # Legs are GEODESIC like the route total; note the start
        # point's own outgoing leg precedes stop 1 and belongs to no
        # record, so on round trips / fixed-start routes
        # sum(Leg_km) = Length_km minus that first approach leg.
        offset = 0 if mode == MODE_SIMPLE else 1
        stops_out = stops[order]
        if geographic:
            stops_out = _rp._project_xy(stops_out, work_sr, out_sr)

        def _leg_km(v):
            if v + 1 >= len(path_out):
                return 0.0
            try:
                a = arcpy.PointGeometry(
                    arcpy.Point(float(path_out[v][0]),
                                float(path_out[v][1])), out_sr)
                return a.angleAndDistanceTo(
                    arcpy.PointGeometry(
                        arcpy.Point(float(path_out[v + 1][0]),
                                    float(path_out[v + 1][1])),
                        out_sr), "GEODESIC")[1] / 1000.0
            except Exception:                            # noqa: BLE001
                return float(np.hypot(
                    path_work[v + 1][0] - path_work[v][0],
                    path_work[v + 1][1] - path_work[v][1])) \
                    * m_per_cs / 1000.0

        with arcpy.da.InsertCursor(
                points_path,
                ["SHAPE@XY", vo_field, "Src_FID", "Leg_km"]) as ins:
            for k in range(len(order)):
                ins.insertRow([
                    (float(stops_out[k, 0]), float(stops_out[k, 1])),
                    k + 1, int(fids[order[k]]),
                    float(_leg_km(offset + k))])
    _mh.phase_advance()

    # ---- report ---------------------------------------------------------
    mode_txt = {
        MODE_SIMPLE: "open path, both ends chosen by the solver",
        MODE_LOOP: "round trip from the start/end point",
        MODE_TWO: "fixed start to fixed end"}[mode]
    _say(messages,
         "Estimated shortest path ({}): {} stop(s), {:,.2f} km "
         "({:,.2f} mi).".format(mode_txt, len(stops), length_km,
                                length_km / 1.609344))
    _say(messages,
         "Straight-line route between locations (not a road "
         "network). The solver is a polished heuristic -- routes "
         "are typically within a few percent of the true optimum.")

    _stamp(route_path, in_points, mode, len(stops), length_km)
    return route_path, int(len(stops)), float(length_km), points_path


def _stamp(out_path, in_points, mode, n_stops, length_km):
    import arcpy

    try:
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = (
            "Created by Estimated Shortest Path through Points: "
            "{} stop(s) from {}, {} ({:,.2f} km).".format(
                n_stops, os.path.basename(str(in_points)), mode,
                length_km))
        md.description = (
            "A short straight-line route through every input point "
            "(open-path / round-trip traveling-salesperson "
            "heuristic: nearest-neighbor and cheapest-insertion "
            "constructions polished by 2-opt and Or-opt; typically "
            "within a few percent of the true optimum). Legs are "
            "straight lines between locations, not road-network "
            "routes. Jenness Enterprises Wildlife and Forestry Tools "
            "(www.jennessent.com).")
        md.save()
    except Exception:                                    # noqa: BLE001
        pass
