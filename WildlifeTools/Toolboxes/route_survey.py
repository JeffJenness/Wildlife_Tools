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
    if text in (None, ""):
        return MODE_SIMPLE
    t = str(text).lower()
    for m in MODES:
        # 18 chars: the two "Start and End at ..." modes first
        # differ at character 17
        if t.startswith(m.lower()[:18]):
            return m
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
    if geographic:
        e = desc.extent
        work_sr = _rs._laea_for(out_sr, 0.5 * (e.XMin + e.XMax),
                                0.5 * (e.YMin + e.YMax))
        _say(messages,
             "Geographic data: routing in an automatic equal-area "
             "working projection (Lambert azimuthal); the output is "
             "written back in {}.".format(out_sr.name))
    else:
        work_sr = out_sr
    m_per_cs = float(work_sr.metersPerUnit or 1.0)

    n_phases = [("Reading the points...", 1),
                ("Solving the route...", 3),
                ("Writing the route...", 1)]
    _mh.begin_phases(n_phases)

    stops, fids = _read_stops(in_points, work_sr)
    if len(stops) == 0:
        raise arcpy.ExecuteError("The input holds no points.")
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
        arcpy.management.AddFields(points_path, [
            ["Visit_Order", "LONG", "Visit order", "", "", ""],
            ["Src_FID", "LONG", "Source point Object ID", "", "",
             ""],
            ["Leg_km", "DOUBLE",
             "Length of the leg leaving this stop (km)", "", "",
             ""]])
        # stop k sits at path vertex offset+k; its leg runs to the
        # next vertex (the next stop, the end point, or the anchor)
        offset = 0 if mode == MODE_SIMPLE else 1
        if mode == MODE_LOOP and anchor is None:
            offset = 0
        stops_out = stops[order]
        if geographic:
            stops_out = _rp._project_xy(stops_out, work_sr, out_sr)
        with arcpy.da.InsertCursor(
                points_path,
                ["SHAPE@XY", "Visit_Order", "Src_FID",
                 "Leg_km"]) as ins:
            for k in range(len(order)):
                v = offset + k
                if v + 1 < len(path_work):
                    leg = float(np.hypot(
                        path_work[v + 1][0] - path_work[v][0],
                        path_work[v + 1][1] - path_work[v][1])) \
                        * m_per_cs / 1000.0
                else:
                    leg = 0.0
                ins.insertRow([
                    (float(stops_out[k, 0]), float(stops_out[k, 1])),
                    k + 1, int(fids[order[k]]), leg])
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
            "routes. Jenness Enterprises Wildlife Tools "
            "(www.jennessent.com).")
        md.save()
    except Exception:                                    # noqa: BLE001
        pass
