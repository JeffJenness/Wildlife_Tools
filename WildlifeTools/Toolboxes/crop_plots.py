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

"""Crop plot design tools -- a port of the CropCentric ArcMap extension
written by Jeff Jenness with Matt Wiebers (Jenness and Wiebers 2018),
rebuilt for ArcGIS Pro at every license level.

PLOT DESIGNER (``run_plot_designer``): a block of rectangular research
plots -- ``n_long`` plots end to end along a plot direction (bearing) and
``n_short`` plots side by side across it, each ``long_edge`` by
``short_edge`` -- repeated ``n_reps`` times, each replication receiving
the user's rate values in a fresh random order. The block starts at an
ANCHOR point (the back-left corner of the first plot, facing along the
plot direction) and the replications are laid side by side across the
direction or end to end along it, with a gap between them. Outputs: the
plot polygons, and optionally their center points and long-axis
centerlines, all carrying Rep, Plot, Plot_ID, Rate, center coordinates
and the bearing.

FUSE PLOTS AND FIELD (``run_fuse``): combines a field (boundary)
polygon layer and a research-plot polygon layer into one feature class.
The field polygons are dissolved by rate (a field attribute or one
standard rate), every plot is cut out of them, and the plots are added
with their own rates and attributes -- a single prescription map with no
double-counted ground.

CREATE ARRAY ALONG AXES (``run_create_array``): copies the input
features (a selected graphic in the window version) along one or two
axes, stepping by the features' own extent along each axis plus a
separation distance, forward and backward along axis 1 and right and
left along axis 2, optionally clipped to selected polygons.

ADD RANDOM NUMBERS (``run_add_random_numbers``): fills a numeric field of
the selected records with a constant, a random permutation of 1..N, random
integers or decimals in a range, decimals in [0, 1), or draws from a
normal distribution.

All distances are geodesic-true: the geometry is built in an azimuthal
equidistant projection centered on the anchor (or on the sources), on the
output coordinate system's own geographic datum, and projected to the
output coordinate system at the end.
"""
import json
import math
import os
import random

import arcpy

import clip_tools as _ct
import mahalanobis as _mh
import tool_metadata

CITATION = ("Jenness, J., and M. Wiebers. 2018. CropCentric ArcGIS tools, "
            "version 2.2.455. Jenness Enterprises, Flagstaff, Arizona, USA.")
CREDITS = ("Tool by Jeff Jenness and Matt Wiebers, Jenness Enterprises "
           "(http://www.jennessent.com), after the CropCentric ArcGIS tools "
           "(Jenness and Wiebers 2018).")

LENGTH_UNITS = ["Meters", "Feet", "US survey feet", "Yards", "Kilometers",
                "Miles"]
_UNIT_M = {"meters": 1.0, "meter": 1.0, "m": 1.0,
           "feet": 0.3048, "foot": 0.3048, "ft": 0.3048,
           "us survey feet": 1200.0 / 3937.0, "survey feet": 1200.0 / 3937.0,
           "yards": 0.9144, "yard": 0.9144,
           "kilometers": 1000.0, "kilometer": 1000.0, "km": 1000.0,
           "miles": 1609.344, "mile": 1609.344}

REP_SIDE_BY_SIDE = "SIDE_BY_SIDE"
REP_END_TO_END = "END_TO_END"
REP_LAYOUTS = ["Side by side (across the plot direction)",
               "End to end (along the plot direction)"]


def canon_rep_layout(s):
    if s in (REP_SIDE_BY_SIDE, REP_END_TO_END):
        return s
    t = str(s or "").strip().lower()
    if not t or t.startswith("side"):
        return REP_SIDE_BY_SIDE
    if t.startswith("end"):
        return REP_END_TO_END
    return None


# ---------------------------------------------------------------------------
#  small helpers
# ---------------------------------------------------------------------------
def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def to_meters(value, unit):
    """``value`` in ``unit`` -> meters. Unknown unit names raise."""
    key = str(unit or "Meters").strip().lower()
    if key not in _UNIT_M:
        raise arcpy.ExecuteError("Unknown length unit '{}'.".format(unit))
    return float(value) * _UNIT_M[key]


def parse_rates(text_or_list):
    """Rate values from a comma- or semicolon-separated string (or a
    list). Returns a list of floats; raises with the offending token."""
    if text_or_list is None:
        return []
    if isinstance(text_or_list, (list, tuple)):
        items = list(text_or_list)
    else:
        items = str(text_or_list).replace(";", ",").split(",")
    out = []
    for raw in items:
        s = str(raw).strip()
        if not s:
            continue
        try:
            out.append(float(s))
        except ValueError:
            raise arcpy.ExecuteError(
                "'{}' is not a number: rate values must be numbers "
                "separated by commas.".format(s))
    return out


def _gcs_of(sr):
    if sr is None:
        return None
    try:
        return sr if sr.type == "Geographic" else sr.GCS
    except Exception:                                            # noqa: BLE001
        return sr


def _aeqd_for(gcs_sr, lon0, lat0, name="CropCentric_AEQD"):
    """An azimuthal EQUIDISTANT projection centered on (lon0, lat0), built on
    the output's own geographic CS (same datum, no transformation). Every
    distance from the center is exact and every bearing from the center is
    true; the scale error at distance d is about (d/R)^2 / 6 -- below a
    millimeter per kilometer at field-trial scales."""
    gcs_wkt = gcs_sr.exportToString().split(";")[0]
    wkt = ('PROJCS["{name}",{gcs},PROJECTION["Azimuthal_Equidistant"],'
           'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],'
           'PARAMETER["Central_Meridian",{lon}],'
           'PARAMETER["Latitude_Of_Origin",{lat}],UNIT["Meter",1.0]]').format(
               name=name, gcs=gcs_wkt, lon=lon0, lat=lat0)
    sr = arcpy.SpatialReference()
    sr.loadFromString(wkt)
    return sr


def _lonlat(x, y, sr):
    """(lon, lat) of a point given in ``sr``."""
    p = arcpy.PointGeometry(arcpy.Point(float(x), float(y)), sr)
    if sr.type != "Geographic":
        p = p.projectAs(_gcs_of(sr))
    return p.firstPoint.X, p.firstPoint.Y


def _sr_ok(sr, what):
    if sr is None or sr.name in ("", "Unknown"):
        raise arcpy.ExecuteError(
            "{} has an unknown coordinate system. Define it first; the tool "
            "needs real distances.".format(what))


def _resolve_out_sr(*candidates):
    """The output coordinate system: the Output Coordinate System
    environment, else the first candidate with a known CS."""
    env = arcpy.env.outputCoordinateSystem
    if env is not None and env.name not in ("", "Unknown"):
        return env
    for c in candidates:
        if c is not None and getattr(c, "name", "") not in ("", "Unknown"):
            return c
    return None


def _shift_geometry(geom, dx, dy):
    """A copy of ``geom`` translated by (dx, dy) in its own coordinate
    system -- rings, paths, holes and parts preserved (through the Esri
    JSON form, so ring orientation is untouched). Curves must have been
    densified first."""
    j = json.loads(geom.JSON)

    def mv(coords):
        return [[c[0] + dx, c[1] + dy] + list(c[2:]) for c in coords]

    if "rings" in j:
        j["rings"] = [mv(r) for r in j["rings"]]
    elif "paths" in j:
        j["paths"] = [mv(p) for p in j["paths"]]
    elif "points" in j:
        j["points"] = mv(j["points"])
    elif "x" in j:
        j["x"] = j["x"] + dx
        j["y"] = j["y"] + dy
    return arcpy.AsShape(j, True)


def _coords(geom):
    """Every vertex of ``geom`` as (x, y) pairs (from its Esri JSON)."""
    j = json.loads(geom.JSON)
    out = []
    for key in ("rings", "paths"):
        for part in j.get(key, []):
            out.extend((c[0], c[1]) for c in part)
    for c in j.get("points", []):
        out.append((c[0], c[1]))
    if "x" in j:
        out.append((j["x"], j["y"]))
    return out


def _flatten_curves(geom, step_m):
    """Replace true curves by vertices (gdb circles collapse to their
    arc endpoints otherwise -- standing rule)."""
    try:
        if getattr(geom, "hasCurves", False):
            return geom.densify("DISTANCE", step_m, step_m)
    except Exception:                                            # noqa: BLE001
        pass
    return geom


def _out_path_parts(out_fc):
    out_fc = str(out_fc).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_fc)
    if ws and not _mh.in_gdb_container(out_fc) \
            and not name.lower().endswith(".shp"):
        name += ".shp"
    return ws, name


def _create_fc(out_fc, geom_type, sr, field_rows):
    ws, name = _out_path_parts(out_fc)
    res = arcpy.management.CreateFeatureclass(
        ws, name, geom_type, spatial_reference=_mh.clean_sr(sr))
    path = res.getOutput(0)
    if field_rows:
        arcpy.management.AddFields(path, field_rows)
    return path


def _is_shp(path):
    return str(path).lower().endswith(".shp")


def _stamp(out_path, title, summary, description, tags):
    try:
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = summary
        md.description = description + " " + CITATION
        md.tags = tags + ", Jenness Enterprises"
        md.credits = CREDITS
        md.save()
    except Exception:                                            # noqa: BLE001
        pass


def _geodesic_measure(geom):
    """(area_ha, length_m) of a geometry, geodesic; None where not
    applicable."""
    area = length = None
    try:
        if geom.type == "polygon":
            area = geom.getArea("GEODESIC", "HECTARES")
            length = geom.getLength("GEODESIC", "METERS")
        elif geom.type == "polyline":
            length = geom.getLength("GEODESIC", "METERS")
    except Exception:                                            # noqa: BLE001
        pass
    return area, length


# ---------------------------------------------------------------------------
#  PLOT DESIGNER
# ---------------------------------------------------------------------------
PLOT_FIELDS = [
    ["Unique_ID", "LONG", "Unique ID", "", "", ""],
    ["Rep", "LONG", "Replication", "", "", ""],
    ["Plot", "LONG", "Plot within replication", "", "", ""],
    ["Plot_ID", "TEXT", "Plot ID (R<rep>P<plot>)", "24", "", ""],
    ["Rate", "DOUBLE", "Rate (treatment value)", "", "", ""],
    ["Center_X", "DOUBLE", "Center X (output CS)", "", "", ""],
    ["Center_Y", "DOUBLE", "Center Y (output CS)", "", "", ""],
    ["Center_Lon", "DOUBLE", "Center longitude", "", "", ""],
    ["Center_Lat", "DOUBLE", "Center latitude", "", "", ""],
    ["Bearing", "DOUBLE", "Plot direction (degrees from north)", "", "", ""],
    ["Long_m", "DOUBLE", "Long edge (meters)", "", "", ""],
    ["Short_m", "DOUBLE", "Short edge (meters)", "", "", ""],
]
PLOT_FIELD_NAMES = [r[0] for r in PLOT_FIELDS]
PLOT_FIELD_DOCS = {
    "Unique_ID": "Sequential ID across every plot of every replication.",
    "Rep": "Replication (block) number, 1-based.",
    "Plot": "Plot number within its replication, 1-based, counted along "
            "the plot direction first and then across it.",
    "Plot_ID": "Replication and plot together: R<rep>P<plot>.",
    "Rate": "The rate (treatment value) assigned to the plot -- the rate "
            "list in a fresh random order for each replication.",
    "Center_X": "X of the plot center in the output coordinate system.",
    "Center_Y": "Y of the plot center in the output coordinate system.",
    "Center_Lon": "Longitude of the plot center (decimal degrees).",
    "Center_Lat": "Latitude of the plot center (decimal degrees).",
    "Bearing": "The plot direction: the geodesic bearing of the long edge, "
               "degrees clockwise from north.",
    "Long_m": "Length of the plot's long edge in meters.",
    "Short_m": "Length of the plot's short edge in meters.",
}


def design_plots(anchor_lon, anchor_lat, out_sr, long_m, short_m, bearing,
                 n_long, n_short, rates, n_reps, rep_layout, rep_gap_m,
                 rng):
    """The plot block as a list of dicts (see PLOT_FIELDS), geometry in
    ``out_sr``. Pure geometry: no datasets touched."""
    gcs = _gcs_of(out_sr)
    local = _aeqd_for(gcs, anchor_lon, anchor_lat)
    b = math.radians(float(bearing))
    ux, uy = math.sin(b), math.cos(b)          # along the plot direction
    vx, vy = math.cos(b), -math.sin(b)         # across it, to the right
    n_long, n_short, n_reps = int(n_long), int(n_short), int(n_reps)
    per_rep = n_long * n_short
    if len(rates) != per_rep:
        raise arcpy.ExecuteError(
            "{} rate value(s) were given but each replication has {} plots "
            "({} along the direction x {} across). Give exactly one rate "
            "per plot.".format(len(rates), per_rep, n_long, n_short))
    if rep_layout == REP_END_TO_END:
        rep_dx, rep_dy = ((n_long * long_m + rep_gap_m) * ux,
                          (n_long * long_m + rep_gap_m) * uy)
    else:
        rep_dx, rep_dy = ((n_short * short_m + rep_gap_m) * vx,
                          (n_short * short_m + rep_gap_m) * vy)

    def pt(x, y):
        return arcpy.Point(x, y)

    rows = []
    uid = 0
    for r in range(n_reps):
        order = list(rates)
        rng.shuffle(order)
        ox, oy = r * rep_dx, r * rep_dy
        k = 0
        for i in range(n_long):
            for j in range(n_short):
                cx0 = ox + i * long_m * ux + j * short_m * vx
                cy0 = oy + i * long_m * uy + j * short_m * vy
                c1 = (cx0 + long_m * ux, cy0 + long_m * uy)
                c2 = (c1[0] + short_m * vx, c1[1] + short_m * vy)
                c3 = (cx0 + short_m * vx, cy0 + short_m * vy)
                ring = arcpy.Array([pt(cx0, cy0), pt(*c1), pt(*c2),
                                    pt(*c3), pt(cx0, cy0)])
                poly = arcpy.Polygon(ring, local).projectAs(out_sr)
                mx = cx0 + 0.5 * long_m * ux + 0.5 * short_m * vx
                my = cy0 + 0.5 * long_m * uy + 0.5 * short_m * vy
                center = arcpy.PointGeometry(pt(mx, my), local).projectAs(
                    out_sr)
                l0 = (cx0 + 0.5 * short_m * vx, cy0 + 0.5 * short_m * vy)
                l1 = (l0[0] + long_m * ux, l0[1] + long_m * uy)
                line = arcpy.Polyline(arcpy.Array([pt(*l0), pt(*l1)]),
                                      local).projectAs(out_sr)
                lon, lat = _lonlat(mx, my, local)
                uid += 1
                k += 1
                rows.append({
                    "Unique_ID": uid, "Rep": r + 1, "Plot": k,
                    "Plot_ID": "R{}P{}".format(r + 1, k),
                    "Rate": float(order[k - 1]),
                    "Center_X": center.firstPoint.X,
                    "Center_Y": center.firstPoint.Y,
                    "Center_Lon": lon, "Center_Lat": lat,
                    "Bearing": float(bearing) % 360.0,
                    "Long_m": float(long_m), "Short_m": float(short_m),
                    "poly": poly, "center": center, "line": line})
    return rows


def default_anchor(field_layer):
    """Lower-left corner of the field layer's extent (its selection's
    extent when features are selected), in the layer's CS: (x, y, sr)."""
    d = arcpy.Describe(field_layer)
    sr = d.spatialReference
    _sr_ok(sr, "The field layer")
    ext = None
    try:
        fids = getattr(d, "FIDSet", "") or ""
        if str(fids).strip():
            with arcpy.da.SearchCursor(field_layer, ["SHAPE@"]) as cur:
                for (g,) in cur:
                    if g is None:
                        continue
                    e = g.extent
                    ext = e if ext is None else arcpy.Extent(
                        min(ext.XMin, e.XMin), min(ext.YMin, e.YMin),
                        max(ext.XMax, e.XMax), max(ext.YMax, e.YMax))
    except Exception:                                            # noqa: BLE001
        ext = None
    if ext is None:
        ext = d.extent
    return ext.XMin, ext.YMin, sr


def run_plot_designer(out_fc, long_edge, short_edge, units, bearing, n_long,
                      n_short, rates, n_reps, field_layer=None,
                      anchor_x=None, anchor_y=None, anchor_sr=None,
                      rep_layout=REP_SIDE_BY_SIDE, rep_gap=0.0,
                      out_points=None, out_lines=None, seed=None,
                      messages=None):
    """Write the plot polygons (and optional center points / centerlines).
    Returns (out_path, points_path, lines_path)."""
    rates = parse_rates(rates)
    rep_layout = canon_rep_layout(rep_layout) or REP_SIDE_BY_SIDE
    long_m = to_meters(long_edge, units)
    short_m = to_meters(short_edge, units)
    gap_m = to_meters(rep_gap or 0.0, units)
    if long_m <= 0 or short_m <= 0:
        raise arcpy.ExecuteError("Plot edges must be positive distances.")
    if int(n_long) < 1 or int(n_short) < 1 or int(n_reps) < 1:
        raise arcpy.ExecuteError(
            "Plots along, plots across and replications must all be at "
            "least 1.")
    field_sr = None
    if field_layer not in (None, ""):
        field_sr = arcpy.Describe(field_layer).spatialReference
    have_anchor = anchor_x not in (None, "") and anchor_y not in (None, "")
    if not have_anchor and field_layer in (None, ""):
        raise arcpy.ExecuteError(
            "Give an anchor point (X and Y) or a field layer whose extent "
            "supplies one.")
    if have_anchor:
        ax, ay = float(anchor_x), float(anchor_y)
        a_sr = anchor_sr or field_sr or arcpy.env.outputCoordinateSystem
        if isinstance(a_sr, str) and a_sr:
            s = arcpy.SpatialReference()
            s.loadFromString(a_sr)
            a_sr = s
        _sr_ok(a_sr, "The anchor point")
    else:
        ax, ay, a_sr = default_anchor(field_layer)
        _say(messages, "No anchor point given: using the lower-left corner "
                       "of the field layer's extent ({:.3f}, {:.3f}).".format(
                           ax, ay))
    out_sr = _resolve_out_sr(field_sr, a_sr)
    _sr_ok(out_sr, "The output")
    lon, lat = _lonlat(ax, ay, a_sr)
    rng = random.Random(int(seed)) if seed not in (None, "") else random.Random()

    rows = design_plots(lon, lat, out_sr, long_m, short_m, bearing, n_long,
                        n_short, rates, n_reps, rep_layout, gap_m, rng)
    _say(messages, "{:,} plots: {} replication(s) x {} plots ({} along the "
                   "direction x {} across), each {:g} x {:g} m, bearing "
                   "{:g} deg.".format(len(rows), int(n_reps),
                                      int(n_long) * int(n_short), int(n_long),
                                      int(n_short), long_m, short_m,
                                      float(bearing) % 360.0))

    def write(target, geom_type, key):
        path = _create_fc(target, geom_type, out_sr, PLOT_FIELDS)
        with arcpy.da.InsertCursor(path, ["SHAPE@"] + PLOT_FIELD_NAMES) as ins:
            for row in rows:
                ins.insertRow([row[key]] + [row[f] for f in PLOT_FIELD_NAMES])
        summary = ("Created by Plot Designer: {n} plots in {reps} "
                   "replication(s); {nl} plots along the direction x {ns} "
                   "across, {lm:g} x {sm:g} m, bearing {b:g} deg; anchor at "
                   "lon {lon:.6f}, lat {lat:.6f}; replications {lay}, gap "
                   "{gap:g} m; rates {rates}.".format(
                       n=len(rows), reps=int(n_reps), nl=int(n_long),
                       ns=int(n_short), lm=long_m, sm=short_m,
                       b=float(bearing) % 360.0, lon=lon, lat=lat,
                       lay=("side by side" if rep_layout == REP_SIDE_BY_SIDE
                            else "end to end"), gap=gap_m,
                       rates=", ".join("{:g}".format(v) for v in rates)))
        _stamp(path, os.path.basename(path), summary,
               "Research plot layout from the Plot Designer tool: a block of "
               "rectangular plots replicated with the rate values randomly "
               "re-ordered in every replication.",
               "crop plots, plot design, field trial, replication, rate")
        tool_metadata.set_entity_and_fields(
            path, entity="Research plot {} from the Plot Designer tool".format(
                {"poly": "polygons", "center": "center points",
                 "line": "centerlines"}[key]),
            fields=[{"name": f, "description": PLOT_FIELD_DOCS[f]}
                    for f in PLOT_FIELD_NAMES], messages=messages)
        return path

    out_path = write(out_fc, "POLYGON", "poly")
    pts_path = write(out_points, "POINT", "center") if out_points else None
    lin_path = write(out_lines, "POLYLINE", "line") if out_lines else None
    return out_path, pts_path, lin_path


# ---------------------------------------------------------------------------
#  CREATE ARRAY ALONG AXES
# ---------------------------------------------------------------------------
ARRAY_FIELDS_BASE = [
    ["Array_ID", "LONG", "Array ID", "", "", ""],
    ["Axis_1_ID", "LONG", "Step along axis 1 (0 = original)", "", "", ""],
    ["Axis_2_ID", "LONG", "Step along axis 2 (0 = original)", "", "", ""],
    ["Source_FID", "LONG", "ObjectID of the source feature", "", "", ""],
    ["Center_X", "DOUBLE", "Center X (output CS)", "", "", ""],
    ["Center_Y", "DOUBLE", "Center Y (output CS)", "", "", ""],
    ["Center_Lon", "DOUBLE", "Center longitude", "", "", ""],
    ["Center_Lat", "DOUBLE", "Center latitude", "", "", ""],
    ["Treatment", "LONG", "Treatment (fill with Add Random Numbers)", "", "", ""],
    ["Block", "LONG", "Block", "", "", ""],
]
ARRAY_FIELD_DOCS = {
    "Array_ID": "Sequential ID of the copy.",
    "Axis_1_ID": "Step number along axis 1: 0 is the original position, "
                 "positive steps are forward, negative backward.",
    "Axis_2_ID": "Step number along axis 2: 0 is the original position, "
                 "positive steps are to the right, negative to the left.",
    "Source_FID": "ObjectID of the input feature this copy was made from "
                  "(-1 when the source was a graphic).",
    "Center_X": "X of the copy's centroid in the output coordinate system.",
    "Center_Y": "Y of the copy's centroid in the output coordinate system.",
    "Center_Lon": "Longitude of the copy's centroid (decimal degrees).",
    "Center_Lat": "Latitude of the copy's centroid (decimal degrees).",
    "Area_Ha": "Geodesic area in hectares (polygons).",
    "Length_m": "Geodesic length in meters (polygons: perimeter; lines).",
    "Treatment": "Empty (0) field for a treatment code -- fill it with the "
                 "Add Random Numbers tool.",
    "Block": "Empty (0) field for a block number.",
}


def _source_geometries(in_features, in_geometry_json, messages):
    """[(fid, geometry)] of the array's sources: the input features (a
    layer's selection honored; a graphics layer converted first) or one
    geometry passed as Esri JSON (the window's selected graphic)."""
    if in_geometry_json not in (None, ""):
        try:
            g = arcpy.AsShape(json.loads(in_geometry_json)
                              if isinstance(in_geometry_json, str)
                              else in_geometry_json, True)
        except Exception as e:                                   # noqa: BLE001
            raise arcpy.ExecuteError(
                "The source geometry could not be read: {}".format(e))
        return [(-1, g)]
    if in_features in (None, ""):
        raise arcpy.ExecuteError("Give input features or a source geometry.")
    d = arcpy.Describe(in_features)
    src = in_features
    tmp = None
    if getattr(d, "dataType", "") == "GraphicsLayer" or \
            "graphic" in str(getattr(d, "dataType", "")).lower():
        tmp = arcpy.CreateUniqueName("array_src", "memory")
        try:
            arcpy.conversion.GraphicsToFeatures(in_features, "POLYGON", tmp)
        except Exception:                                        # noqa: BLE001
            try:
                arcpy.conversion.GraphicsToFeatures(in_features, "POLYLINE",
                                                    tmp)
            except Exception:                                    # noqa: BLE001
                arcpy.conversion.GraphicsToFeatures(in_features, "POINT",
                                                    tmp)
        src = tmp
        _say(messages, "Graphics layer converted to features for the array.")
    out = []
    with arcpy.da.SearchCursor(src, ["OID@", "SHAPE@"]) as cur:
        for fid, g in cur:
            if g is not None:
                out.append((int(fid), g))
    if tmp:
        try:
            arcpy.management.Delete(tmp)
        except Exception:                                        # noqa: BLE001
            pass
    if not out:
        raise arcpy.ExecuteError("No input features to copy.")
    return out


def _clip_union(clip_features, local_sr):
    """Union of the clip polygons (selection + definition query honored),
    in ``local_sr``; None when no clip features."""
    if clip_features in (None, ""):
        return None
    geoms = []
    with arcpy.da.SearchCursor(clip_features, ["SHAPE@"]) as cur:
        for (g,) in cur:
            if g is None or g.type != "polygon":
                continue
            geoms.append(_flatten_curves(g, 0.5))
    if not geoms:
        raise arcpy.ExecuteError(
            "The clip layer has no polygons to clip to (check its "
            "selection).")
    # union in the clip layer's OWN coordinate system: arcpy's union()
    # raises a bare ValueError on geometries in a custom (unregistered)
    # projection such as the local AEQD, while difference() and
    # intersect() work there -- so union first, then project once
    u = geoms[0]
    for g in geoms[1:]:
        u = u.union(g)
    return _ct.project_geometry(u, local_sr)


def array_geometries(sources, bearing1, gap1_m, n_forward, n_back,
                     bearing2=None, gap2_m=0.0, n_right=0, n_left=0,
                     clip_union=None, local_sr=None):
    """[(geom_local, fid, k1, k2)] -- every copy of every source in the
    local CS, (0, 0) being the originals. ``sources`` are (fid, geometry)
    already in ``local_sr``."""
    b1 = math.radians(float(bearing1))
    u1 = (math.sin(b1), math.cos(b1))
    n_forward, n_back = int(n_forward or 0), int(n_back or 0)
    n_right, n_left = int(n_right or 0), int(n_left or 0)
    if bearing2 in (None, ""):
        bearing2 = float(bearing1) + 90.0
    b2 = math.radians(float(bearing2))
    u2 = (math.sin(b2), math.cos(b2))
    pts = []
    for _fid, g in sources:
        pts.extend(_coords(g))
    if not pts:
        raise arcpy.ExecuteError("The source geometry is empty.")
    p1 = [x * u1[0] + y * u1[1] for x, y in pts]
    p2 = [x * u2[0] + y * u2[1] for x, y in pts]
    step1 = (max(p1) - min(p1)) + float(gap1_m or 0.0)
    step2 = (max(p2) - min(p2)) + float(gap2_m or 0.0)
    if (n_forward or n_back) and step1 <= 0:
        raise arcpy.ExecuteError("The step along axis 1 is zero.")
    if (n_right or n_left) and step2 <= 0:
        raise arcpy.ExecuteError("The step along axis 2 is zero.")
    out = []
    for k1 in range(-n_back, n_forward + 1):
        for k2 in range(-n_left, n_right + 1):
            dx = k1 * step1 * u1[0] + k2 * step2 * u2[0]
            dy = k1 * step1 * u1[1] + k2 * step2 * u2[1]
            for fid, g in sources:
                c = g if (k1 == 0 and k2 == 0) else _shift_geometry(g, dx, dy)
                if clip_union is not None:
                    if clip_union.disjoint(c):
                        continue
                    dim = {"polygon": 4, "polyline": 2}.get(c.type, 1)
                    c = c.intersect(clip_union, dim)
                    if c is None or (c.type in ("polygon", "polyline")
                                     and (c.area if c.type == "polygon"
                                          else c.length) == 0):
                        continue
                out.append((c, fid, k1, k2))
    return out, step1, step2


def run_create_array(bearing1, gap1, units1, n_forward, n_back,
                     in_features=None, in_geometry_json=None,
                     bearing2=None, gap2=0.0, units2=None, n_right=0,
                     n_left=0, clip_features=None, out_fc=None,
                     to_graphics=False, messages=None):
    """Write the array feature class. Returns (out_path, n_copies)."""
    sources = _source_geometries(in_features, in_geometry_json, messages)
    src_sr = sources[0][1].spatialReference
    _sr_ok(src_sr, "The source")
    kinds = {g.type for _f, g in sources}
    if len(kinds) > 1:
        raise arcpy.ExecuteError(
            "All source features must share one geometry type (found {}).".format(
                ", ".join(sorted(kinds))))
    kind = kinds.pop()
    geom_type = {"polygon": "POLYGON", "polyline": "POLYLINE",
                 "point": "POINT", "multipoint": "MULTIPOINT"}.get(kind)
    if geom_type is None:
        raise arcpy.ExecuteError("Unsupported source geometry: {}.".format(kind))
    in_sr = None
    if in_features not in (None, ""):
        try:
            in_sr = arcpy.Describe(in_features).spatialReference
        except Exception:                                        # noqa: BLE001
            in_sr = None
    out_sr = _resolve_out_sr(in_sr, src_sr)
    _sr_ok(out_sr, "The output")

    # the local frame: centered on the sources' extent
    ext = None
    for _f, g in sources:
        e = g.extent
        ext = e if ext is None else arcpy.Extent(
            min(ext.XMin, e.XMin), min(ext.YMin, e.YMin),
            max(ext.XMax, e.XMax), max(ext.YMax, e.YMax))
    cx, cy = (ext.XMin + ext.XMax) / 2.0, (ext.YMin + ext.YMax) / 2.0
    lon, lat = _lonlat(cx, cy, src_sr)
    local = _aeqd_for(_gcs_of(out_sr), lon, lat, "Array_AEQD")
    span = max(ext.width, ext.height) or 1.0
    local_sources = []
    for fid, g in sources:
        g = _flatten_curves(g, span / 400.0)
        local_sources.append((fid, _ct.project_geometry(g, local)))
    gap1_m = to_meters(gap1 or 0.0, units1)
    gap2_m = to_meters(gap2 or 0.0, units2 or units1)
    clip_u = _clip_union(clip_features, local)
    copies, step1, step2 = array_geometries(
        local_sources, bearing1, gap1_m, n_forward, n_back, bearing2, gap2_m,
        n_right, n_left, clip_u, local)
    _say(messages, "Step along axis 1 = {:,.3f} m (extent {:,.3f} m + gap "
                   "{:,.3f} m){}.".format(
                       step1, step1 - gap1_m, gap1_m,
                       "; axis 2 = {:,.3f} m".format(step2)
                       if (int(n_right or 0) or int(n_left or 0)) else ""))
    if not copies:
        raise arcpy.ExecuteError(
            "No copies remain -- every copy fell outside the clip polygons.")

    fields = list(ARRAY_FIELDS_BASE)
    measure = []
    if kind == "polygon":
        measure = [["Area_Ha", "DOUBLE", "Area (geodesic hectares)", "", "", ""],
                   ["Length_m", "DOUBLE", "Perimeter (geodesic meters)", "", "", ""]]
    elif kind == "polyline":
        measure = [["Length_m", "DOUBLE", "Length (geodesic meters)", "", "", ""]]
    fields = fields[:8] + measure + fields[8:]
    names = [r[0] for r in fields]
    target = out_fc if out_fc not in (None, "") else arcpy.CreateUniqueName(
        "Array_along_axes", "memory")
    path = _create_fc(target, geom_type, out_sr, fields)
    shp = _is_shp(path)
    n = 0
    with arcpy.da.InsertCursor(path, ["SHAPE@"] + names) as ins:
        for g, fid, k1, k2 in copies:
            g_out = _ct.project_geometry(g, out_sr)
            c = g_out.centroid if kind != "point" else g_out.firstPoint
            clon, clat = _lonlat(c.X, c.Y, out_sr)
            area, length = _geodesic_measure(g_out)
            n += 1
            vals = {"Array_ID": n, "Axis_1_ID": k1, "Axis_2_ID": k2,
                    "Source_FID": fid, "Center_X": c.X, "Center_Y": c.Y,
                    "Center_Lon": clon, "Center_Lat": clat,
                    "Area_Ha": area, "Length_m": length,
                    "Treatment": 0, "Block": 0}
            row = [vals.get(f) for f in names]
            if shp:
                row = _mh.shp_safe_attrs(row, [r[1] for r in fields])
            ins.insertRow([g_out] + row)
    _say(messages, "{:,} copies written (axis 1: {} forward, {} back; axis 2: "
                   "{} right, {} left).".format(n, int(n_forward or 0),
                                                int(n_back or 0),
                                                int(n_right or 0),
                                                int(n_left or 0)))
    summary = ("Created by Create Array along Axes: {n} copies of {src}; axis 1 "
               "bearing {b1:g} deg, step {s1:,.3f} m ({f} forward, {bk} back); "
               "axis 2 bearing {b2} deg, step {s2:,.3f} m ({r} right, {l} "
               "left){clip}.".format(
                   n=n, src=(str(in_features) if in_features not in (None, "")
                             else "a selected graphic"),
                   b1=float(bearing1) % 360.0, s1=step1,
                   f=int(n_forward or 0), bk=int(n_back or 0),
                   b2=("{:g}".format(float(bearing2) % 360.0)
                       if bearing2 not in (None, "") else "axis 1 + 90"),
                   s2=step2, r=int(n_right or 0), l=int(n_left or 0),
                   clip=(", clipped to " + str(clip_features))
                   if clip_features not in (None, "") else ""))
    _stamp(path, os.path.basename(path), summary,
           "Copies of the input features stepped along one or two axes by "
           "their extent plus a separation distance (Create Array along "
           "Axes).", "array, replicates, plots, axes, copies")
    tool_metadata.set_entity_and_fields(
        path, entity="Copies of the input features arrayed along one or two "
                     "axes (Create Array along Axes)",
        fields=[{"name": f, "description": ARRAY_FIELD_DOCS[f]}
                for f in names if f in ARRAY_FIELD_DOCS], messages=messages)
    if to_graphics:
        try:
            lyr = arcpy.CreateUniqueName("array_for_graphics")
            arcpy.management.MakeFeatureLayer(path, lyr)
            gname = os.path.splitext(os.path.basename(path))[0] + " graphics"
            arcpy.conversion.FeaturesToGraphics(lyr, gname)
            _say(messages, "Added the copies to the map as graphics ('{}')."
                 .format(gname))
        except Exception as e:                                   # noqa: BLE001
            _warn(messages, "The copies were written, but could not be added "
                            "to the map as graphics: {}".format(e))
    return path, n


# ---------------------------------------------------------------------------
#  FUSE PLOTS AND FIELD
# ---------------------------------------------------------------------------
_SKIP_FIELD_TYPES = ("OID", "Geometry", "Blob", "Raster")


def _transfer_fields(desc, exclude_names):
    """[(src_name, add_field_row)] for the attribute fields to carry over."""
    skip = {n.lower() for n in exclude_names if n}
    for attr in ("shapeFieldName", "OIDFieldName", "areaFieldName",
                 "lengthFieldName"):
        v = getattr(desc, attr, None)
        if v:
            skip.add(str(v).lower())
    out = []
    for f in desc.fields:
        if f.type in _SKIP_FIELD_TYPES or f.name.lower() in skip:
            continue
        if f.name.lower() in ("shape_length", "shape_area", "shape_leng",
                              "shape_are"):
            continue
        ftype = {"SmallInteger": "SHORT", "Integer": "LONG",
                 "BigInteger": "BIGINTEGER", "Single": "FLOAT",
                 "Double": "DOUBLE", "String": "TEXT", "Date": "DATE",
                 "GUID": "GUID"}.get(f.type)
        if ftype is None:
            continue
        length = str(f.length) if ftype == "TEXT" else ""
        out.append((f.name, [f.name, ftype, f.aliasName or f.name, length,
                             "", ""]))
    return out


def run_fuse(field_layer, plot_layer, plot_rate_field, out_fc,
             field_rate_field=None, standard_rate=None,
             transfer_plot_fields=True, messages=None):
    """Write the fused feature class. Returns (out_path, n_field, n_plots)."""
    fd = arcpy.Describe(field_layer)
    pd = arcpy.Describe(plot_layer)
    if fd.shapeType != "Polygon" or pd.shapeType != "Polygon":
        raise arcpy.ExecuteError("Both inputs must be POLYGON layers.")
    _sr_ok(fd.spatialReference, "The field layer")
    _sr_ok(pd.spatialReference, "The plot layer")
    try:
        if fd.catalogPath == pd.catalogPath:
            raise arcpy.ExecuteError(
                "The field layer and the plot layer are the same dataset.")
    except AttributeError:
        pass
    out_sr = _resolve_out_sr(fd.spatialReference)
    use_field_rate = field_rate_field not in (None, "")
    if not use_field_rate and standard_rate in (None, ""):
        raise arcpy.ExecuteError(
            "Give a field rate attribute, a standard rate, or both.")
    std = float(standard_rate) if standard_rate not in (None, "") else None

    # 1. field polygons grouped by rate --------------------------------------
    groups = {}
    order = []
    n_field = 0
    cols = ["SHAPE@"] + ([field_rate_field] if use_field_rate else [])
    with arcpy.da.SearchCursor(field_layer, cols) as cur:
        for row in cur:
            g = row[0]
            if g is None or g.area == 0:
                continue
            n_field += 1
            rate = None
            if use_field_rate and row[1] is not None:
                try:
                    rate = float(row[1])
                except (TypeError, ValueError):
                    rate = None
            if rate is None:
                if std is None:
                    raise arcpy.ExecuteError(
                        "A field polygon has no rate value and no standard "
                        "rate was given.")
                rate = std
            g = _flatten_curves(g, max(g.extent.width, g.extent.height) / 400.0)
            g = _ct.project_geometry(g, out_sr)
            key = round(rate, 10)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(g)
    if n_field == 0:
        raise arcpy.ExecuteError("The field layer has no polygons to fuse "
                                 "(check its selection).")
    _say(messages, "{:,} field polygon(s) in {} rate group(s).".format(
        n_field, len(groups)))
    dissolved = []
    for key in order:
        u = groups[key][0]
        for g in groups[key][1:]:
            u = u.union(g)
        dissolved.append([key, u])

    # 2. plots -----------------------------------------------------------------
    exclude = [plot_rate_field, "Rate", "Source", "Plot_FID"]
    xfer = _transfer_fields(pd, exclude) if transfer_plot_fields else []
    shp = _is_shp(_create_target_name(out_fc))
    used = {"rate", "source", "plot_fid"}
    xfer_rows = []
    for src_name, row in xfer:
        name = row[0]
        try:
            name = arcpy.ValidateFieldName(name, os.path.dirname(
                _create_target_name(out_fc)) or arcpy.env.workspace or "")
        except Exception:                                        # noqa: BLE001
            pass
        if shp:
            name = name[:10]
        base, i = name, 2
        while name.lower() in used:
            suffix = "_{}".format(i)
            name = (base[:10 - len(suffix)] if shp else base) + suffix
            i += 1
        used.add(name.lower())
        xfer_rows.append((src_name, [name] + row[1:]))
    plots = []
    pcols = ["OID@", "SHAPE@", plot_rate_field] + [s for s, _r in xfer_rows]
    n_plots = 0
    n_plot_rate_missing = 0
    with arcpy.da.SearchCursor(plot_layer, pcols) as cur:
        for row in cur:
            g = row[1]
            if g is None or g.area == 0:
                continue
            n_plots += 1
            rate = row[2]
            if rate is None:
                n_plot_rate_missing += 1
                rate = std if std is not None else 0.0
            g = _flatten_curves(g, max(g.extent.width, g.extent.height) / 400.0)
            g = _ct.project_geometry(g, out_sr)
            plots.append((int(row[0]), float(rate), g, list(row[3:])))
    if n_plots == 0:
        raise arcpy.ExecuteError("The plot layer has no polygons to fuse "
                                 "(check its selection).")
    if n_plot_rate_missing:
        _warn(messages, "{:,} plot(s) have no rate value; {}.".format(
            n_plot_rate_missing,
            "the standard rate was used" if std is not None else "0 was used"))

    # 3. cut every plot out of the field ---------------------------------------
    _mh.begin_phases([("Cutting plots out of the field", 3.0),
                      ("Writing the fused feature class", 1.0)])
    for i, (_fid, _rate, pg, _attrs) in enumerate(plots):
        if (i + 1) % 50 == 0:
            _mh.phase_step(i + 1, len(plots))
        for entry in dissolved:
            fg = entry[1]
            if fg is None or fg.disjoint(pg):
                continue
            entry[1] = fg.difference(pg)
    _mh.phase_advance()

    # 4. write -------------------------------------------------------------------
    fields = [["Rate", "DOUBLE", "Rate", "", "", ""],
              ["Source", "TEXT", "Source (Field or Plot)", "8", "", ""],
              ["Plot_FID", "LONG", "Plot ObjectID (-1 = field)", "", "", ""]]
    fields += [r for _s, r in xfer_rows]
    names = [r[0] for r in fields]
    types = [r[1] for r in fields]
    path = _create_fc(out_fc, "POLYGON", out_sr, fields)
    n_written_field = 0
    with arcpy.da.InsertCursor(path, ["SHAPE@"] + names) as ins:
        for key, fg in dissolved:
            if fg is None or fg.area == 0:
                continue
            row = [float(key), "Field", -1] + [None] * len(xfer_rows)
            if shp:
                row = _mh.shp_safe_attrs(row, types)
            ins.insertRow([fg] + row)
            n_written_field += 1
        for fid, rate, pg, attrs in plots:
            row = [rate, "Plot", fid] + attrs
            if shp:
                row = _mh.shp_safe_attrs(row, types)
            ins.insertRow([pg] + row)
    _mh.end_phases()
    _say(messages, "Fused: {:,} field polygon(s) (one per rate, plots cut "
                   "out) + {:,} plot(s).".format(n_written_field, n_plots))
    summary = ("Created by Fuse Plots and Field: field layer {f} ({fr}) "
               "dissolved by rate with {n} plot(s) from {p} (rate field {pr}) "
               "cut out and added.".format(
                   f=str(field_layer),
                   fr=("rate field " + str(field_rate_field)) if use_field_rate
                   else "standard rate {:g}".format(std),
                   n=n_plots, p=str(plot_layer), pr=str(plot_rate_field)))
    _stamp(path, os.path.basename(path), summary,
           "Field polygons dissolved by rate with the research plots cut out "
           "of them and added with their own rates: one prescription map "
           "(Fuse Plots and Field).", "crop plots, prescription, rate, fuse")
    docs = [{"name": "Rate", "description": "The rate: the field's rate for "
             "field ground, the plot's rate for plot ground."},
            {"name": "Source", "description": "'Field' for dissolved field "
             "ground, 'Plot' for a research plot."},
            {"name": "Plot_FID", "description": "ObjectID of the plot in the "
             "plot layer; -1 for field ground."}]
    docs += [{"name": r[0], "description": "Copied from the plot layer's "
              "field '{}' (blank for field ground).".format(s)}
             for s, r in xfer_rows]
    tool_metadata.set_entity_and_fields(
        path, entity="Field and research plots fused into one prescription "
                     "map (Fuse Plots and Field)", fields=docs,
        messages=messages)
    return path, n_written_field, n_plots


def _create_target_name(out_fc):
    ws, name = _out_path_parts(out_fc)
    return os.path.join(ws, name) if ws else name


# ---------------------------------------------------------------------------
#  ADD RANDOM NUMBERS
# ---------------------------------------------------------------------------
MODE_CONSTANT = "CONSTANT"
MODE_PERMUTATION = "PERMUTATION"
MODE_INT_RANGE = "INT_RANGE"
MODE_DEC_01 = "DEC_01"
MODE_DEC_RANGE = "DEC_RANGE"
MODE_NORMAL = "NORMAL"
MODES = [
    "Constant value",
    "Random permutation of 1 to N (each record unique)",
    "Random integers within a range",
    "Random decimals between 0 and 1",
    "Random decimals within a range",
    "Random decimals from a normal distribution (mean, standard deviation)",
]
_MODE_KEYS = [MODE_CONSTANT, MODE_PERMUTATION, MODE_INT_RANGE, MODE_DEC_01,
              MODE_DEC_RANGE, MODE_NORMAL]
INTEGER_MODES = (MODE_PERMUTATION, MODE_INT_RANGE)
DECIMAL_MODES = (MODE_DEC_01, MODE_DEC_RANGE, MODE_NORMAL)


def canon_mode(s):
    if s in _MODE_KEYS:
        return s
    t = str(s or "").strip().lower()
    if not t or t.startswith("constant"):
        return MODE_CONSTANT
    if "permutation" in t:
        return MODE_PERMUTATION
    if "integer" in t:
        return MODE_INT_RANGE
    if "normal" in t:
        return MODE_NORMAL
    if "between 0" in t:
        return MODE_DEC_01
    if "decimal" in t:
        return MODE_DEC_RANGE
    return None


def _field_kind(in_table, name):
    """'int', 'float', 'other' or None (missing) for a field."""
    for f in arcpy.ListFields(in_table):
        if f.name.lower() == str(name).lower():
            if f.type in ("SmallInteger", "Integer", "BigInteger"):
                return "int", f.name
            if f.type in ("Single", "Double"):
                return "float", f.name
            return "other", f.name
    return None, name


def run_add_random_numbers(in_table, field_name, mode, constant=None, low=None,
                           high=None, mean=None, sd=None, seed=None,
                           messages=None):
    """Fill ``field_name`` of the selected records. Returns (n_written,
    field_name)."""
    mode = canon_mode(mode)
    if mode is None:
        raise arcpy.ExecuteError("Unknown value mode.")
    rng = random.Random(int(seed)) if seed not in (None, "") else random.Random()
    n = int(arcpy.management.GetCount(in_table)[0])
    if n == 0:
        raise arcpy.ExecuteError("No records to write to (if the layer has a "
                                 "selection, nothing is selected).")

    # the field --------------------------------------------------------------
    field_name = str(field_name or "").strip()
    if not field_name:
        raise arcpy.ExecuteError("Give a field to write to.")
    kind, real_name = _field_kind(in_table, field_name)
    want_int = mode in INTEGER_MODES or (
        mode == MODE_CONSTANT and float(constant) == int(float(constant)))
    if kind is None:
        try:
            path = arcpy.Describe(in_table).catalogPath
        except Exception:                                        # noqa: BLE001
            path = in_table
        ws = os.path.dirname(str(path))
        try:
            real_name = arcpy.ValidateFieldName(field_name, ws)
        except Exception:                                        # noqa: BLE001
            real_name = field_name
        if _mh.is_shapefile_target(str(path)) or str(path).lower().endswith(".dbf"):
            real_name = real_name[:10]
        arcpy.management.AddField(path, real_name, "LONG" if want_int else "DOUBLE")
        _say(messages, "Added the {} field '{}'.".format(
            "integer" if want_int else "double-precision", real_name))
        kind = "int" if want_int else "float"
    elif kind == "other":
        raise arcpy.ExecuteError(
            "'{}' is not a numeric field; choose or create a numeric field."
            .format(real_name))
    elif kind == "int" and not want_int:
        raise arcpy.ExecuteError(
            "'{}' is an integer field, but this mode writes decimals. Choose "
            "a double-precision field or a new field name.".format(real_name))

    # the values ---------------------------------------------------------------
    if mode == MODE_CONSTANT:
        c = float(constant)
        gen = (lambda: int(c)) if (kind == "int") else (lambda: c)
    elif mode == MODE_PERMUTATION:
        pool = rng.sample(range(1, n + 1), n)
        it = iter(pool)
        gen = lambda: next(it)                                    # noqa: E731
    elif mode == MODE_INT_RANGE:
        lo, hi = int(float(low)), int(float(high))
        if hi < lo:
            lo, hi = hi, lo
        gen = lambda: rng.randint(lo, hi)                         # noqa: E731
    elif mode == MODE_DEC_01:
        gen = rng.random
    elif mode == MODE_DEC_RANGE:
        lo, hi = float(low), float(high)
        if hi < lo:
            lo, hi = hi, lo
        gen = lambda: rng.uniform(lo, hi)                         # noqa: E731
    else:
        mu, sigma = float(mean), float(sd)
        if sigma < 0:
            raise arcpy.ExecuteError("The standard deviation cannot be negative.")
        gen = lambda: rng.gauss(mu, sigma)                        # noqa: E731

    written = 0
    with arcpy.da.UpdateCursor(in_table, [real_name]) as cur:
        for row in cur:
            row[0] = gen()
            cur.updateRow(row)
            written += 1
    label = {MODE_CONSTANT: "the constant {}".format(constant),
             MODE_PERMUTATION: "a random permutation of 1 to {:,}".format(n),
             MODE_INT_RANGE: "random integers from {} to {}".format(low, high),
             MODE_DEC_01: "random decimals between 0 and 1",
             MODE_DEC_RANGE: "random decimals from {} to {}".format(low, high),
             MODE_NORMAL: "random normal values (mean {}, SD {})".format(mean, sd)
             }[mode]
    _say(messages, "Wrote {} into '{}' of {:,} record(s){}.".format(
        label, real_name, written,
        " (seed {})".format(seed) if seed not in (None, "") else ""))
    return written, real_name
