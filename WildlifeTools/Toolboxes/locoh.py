# -*- coding: utf-8 -*-
"""Local Convex Hull (LoCoH) home-range construction.

Port of the legacy ArcMap VB6 ``LoCoH_Standard`` routine (LoCoH.bas) to arcpy.

Standard (k-) LoCoH (Getz & Wilmers 2004):
  1. For every point, take the focal point plus its k-1 nearest neighbours
     (k points total) and build their convex hull.
  2. Sort the hulls by area, ascending.
  3. Union the hulls cumulatively in that order. Each cumulative union is an
     isopleth; the proportion of all points it encloses is its utilization level.

Parity notes vs. the legacy tool:
  - Nearest-neighbour distances use the Haversine formula for geographic data
    (x=lon, y=lat) and planar distance for projected data -- see wildlife_geom.
  - Hull areas (which drive the sort order) use the spheroid-corrected spherical
    area for geographic data, planar area for projected data.
  - Point counts use an "intersects" test against the cumulative union, matching
    the legacy esriSpatialRelIntersects spatial filter.
"""

import os
import json
from datetime import datetime

import numpy as np
import arcpy

import wildlife_geom as wg

GETZ_WILMERS_DOI = "https://doi.org/10.1111/j.0906-7590.2004.03835.x"

# Same tags authored for the tool metadata, applied to output feature classes.
LOCOH_TAGS = ("home range, LoCoH, local convex hull, utilization distribution, isopleth, "
              "nearest neighbor, wildlife, telemetry, animal movement, point pattern, Getz, Wilmers")

# Symbology template for the cumulative-isopleth output, shipped with the toolbox.
LAYER_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "LayerTemplates", "Cumulative_LoCoH_Standard.lyrx")


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
def read_points(in_features):
    """Read non-empty point geometries into an (N, 2) array of (x, y).

    Returns (xy, spatial_reference, is_geographic). For geographic data x is
    longitude and y is latitude.
    """
    desc = arcpy.Describe(in_features)
    sr = desc.spatialReference
    is_geographic = (sr is not None and sr.type == "Geographic")

    coords = []
    with arcpy.da.SearchCursor(in_features, ["SHAPE@XY"]) as cur:
        for (xy,) in cur:
            if xy is None:
                continue
            x, y = xy
            if x is None or y is None:
                continue
            coords.append((x, y))

    return np.asarray(coords, dtype=float), sr, is_geographic


# ---------------------------------------------------------------------------
# Geometry helpers (arcpy side)
# ---------------------------------------------------------------------------
def _extract_rings(polygon):
    """Return all rings of an arcpy polygon as closed lists of (x, y) tuples.

    Interior rings (holes) are included; the ``None`` separators arcpy uses
    between rings within a part are honoured.
    """
    rings = []
    for part in polygon:
        current = []
        for pnt in part:
            if pnt is None:           # ring separator within a part
                if current:
                    if current[0] != current[-1]:
                        current.append(current[0])
                    rings.append(current)
                    current = []
            else:
                current.append((pnt.X, pnt.Y))
        if current:
            if current[0] != current[-1]:
                current.append(current[0])
            rings.append(current)
    return rings


def _polygon_area(geom, is_geographic):
    """Area of an arcpy polygon: spheroid-corrected if geographic, else planar."""
    if is_geographic:
        return wg.geodesic_area(_extract_rings(geom))
    return geom.area


def _convex_hull(xy_subset, sr):
    """Convex hull (arcpy geometry) of an (m, 2) coordinate array."""
    arr = arcpy.Array([arcpy.Point(float(x), float(y)) for x, y in xy_subset])
    return arcpy.Multipoint(arr, sr).convexHull()


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------
def build_local_hulls(xy, k, sr, is_geographic, messages=None):
    """Build one convex hull per point from its k nearest neighbours.

    Returns (hulls, areas) as parallel lists, already sorted by area ascending.
    Degenerate hulls (collinear/duplicate neighbours that do not form a polygon)
    are skipped with a warning.
    """
    n = xy.shape[0]
    x = xy[:, 0]
    y = xy[:, 1]

    hulls = []
    areas = []
    skipped = 0

    for i in range(n):
        if is_geographic:
            dist = wg.haversine_distance(y[i], x[i], y, x)
        else:
            dist = wg.euclidean_distance(x[i], y[i], x, y)
        # k nearest including the focal point itself (distance 0).
        nearest = np.argsort(dist, kind="stable")[:k]
        hull = _convex_hull(xy[nearest], sr)
        if hull is None or hull.type != "polygon" or hull.area == 0:
            skipped += 1
            continue
        hulls.append(hull)
        areas.append(_polygon_area(hull, is_geographic))

        if messages is not None and (i + 1) % 250 == 0:
            messages.addMessage("  ...hulls built for {:,} of {:,} points".format(i + 1, n))

    if skipped and messages is not None:
        messages.addWarningMessage(
            "{} hull(s) were degenerate (collinear or duplicate points) and skipped.".format(skipped))

    order = np.argsort(np.asarray(areas), kind="stable")
    hulls_sorted = [hulls[j] for j in order]
    areas_sorted = [areas[j] for j in order]
    return hulls_sorted, areas_sorted


def cumulative_unions(hulls_sorted, messages=None):
    """Return the running cumulative union of hulls (ascending area order)."""
    cumulative = []
    acc = hulls_sorted[0]
    cumulative.append(acc)
    for idx, hull in enumerate(hulls_sorted[1:], start=1):
        acc = acc.union(hull)
        cumulative.append(acc)
        if messages is not None and (idx + 1) % 250 == 0:
            messages.addMessage("  ...unioned {:,} of {:,} hulls".format(idx + 1, len(hulls_sorted)))
    return cumulative


def cumulative_point_counts(cumulative, xy, sr):
    """Count input points intersecting each cumulative isopleth.

    Cumulative unions are nested, so each point is assigned to the first (lowest)
    isopleth it intersects; the count at index i is the number of points assigned
    at or below i. Matches the legacy "intersects" semantics.
    """
    point_geoms = [arcpy.PointGeometry(arcpy.Point(float(px), float(py)), sr) for px, py in xy]
    n = len(point_geoms)
    m = len(cumulative)
    first_idx = [m] * n          # m == "never enclosed" (should not happen)
    remaining = list(range(n))

    for i, poly in enumerate(cumulative):
        if not remaining:
            break
        still = []
        for q in remaining:
            if poly.disjoint(point_geoms[q]):
                still.append(q)
            else:
                first_idx[q] = i
        remaining = still

    counts = np.zeros(m, dtype=np.int64)
    for fi in first_idx:
        if fi < m:
            counts[fi] += 1
    counts = np.cumsum(counts)   # cumulative because unions are nested
    return counts


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
# Field definitions as (geodatabase name, type, shapefile name). Shapefile (.dbf)
# field names are capped at 10 characters, so short names are used only when the
# output is a shapefile; geodatabase outputs keep the full descriptive names.
_FIELD_DEFS = [
    ("Area", "DOUBLE", "Area"),
    ("Point_Count", "LONG", "Point_Cnt"),
    ("Point_Proportion", "DOUBLE", "Point_Prop"),
]


def _is_shapefile(out_path):
    return str(out_path).lower().endswith(".shp")


def _field_names(out_path):
    """Ordered [area, count, proportion] field names for the target format."""
    shp = _is_shapefile(out_path)
    return [(d[2] if shp else d[0]) for d in _FIELD_DEFS]


def _create_polygon_fc(out_path, sr):
    out_ws, out_name = os.path.split(out_path)
    arcpy.management.CreateFeatureclass(out_ws, out_name, "POLYGON", spatial_reference=sr)
    shp = _is_shapefile(out_path)
    for gdb_name, ftype, shp_name in _FIELD_DEFS:
        arcpy.management.AddField(out_path, shp_name if shp else gdb_name, ftype)
    if shp:
        # Shapefiles are created with a default "Id" field; drop it now that our own
        # fields exist (a shapefile must always retain at least one attribute field).
        if any(f.name.lower() == "id" for f in arcpy.ListFields(out_path)):
            arcpy.management.DeleteField(out_path, "Id")
    return out_path


def _write_rows(out_path, rows):
    """rows: iterable of (geometry, area, point_count, point_proportion)."""
    names = _field_names(out_path)
    with arcpy.da.InsertCursor(out_path, ["SHAPE@"] + names) as cur:
        for geom, area, count, prop in rows:
            cur.insertRow([geom, area, int(count), prop])


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def _long_date():
    now = datetime.now()
    return "{:%B} {}, {}".format(now, now.day, now.year)


def write_metadata(out_path, in_base_name, k, n_points, is_geographic, individual_hulls, is_isopleth):
    from arcpy import metadata as md

    coord_kind = "geographic" if is_geographic else "projected"
    method = "Haversine (spherical) formula" if is_geographic else "planar (Euclidean) distance"
    product = ("dissolved cumulative isopleth polygons" if is_isopleth
               else "individual local convex hulls")

    summary = (
        'Local Convex Hulls, calculated according to methods described by Wayne M. '
        'Getz and Christopher C. Wilmers in their 2004 manuscript "A local '
        'nearest-neighbor convex-hull construction of home ranges and utilization '
        'distributions" (see {doi}). Hulls calculated from {base} on {date}.'
    ).format(doi=GETZ_WILMERS_DOI, base=in_base_name, date=_long_date())

    description = (
        "This feature class contains the {product} from a Standard (k-) Local "
        "Convex Hull (LoCoH) home-range analysis. "
        "Input point features: '{base}' ({coord} coordinate system; "
        "nearest-neighbour distances computed using the {method}). "
        "Number of nearest neighbours (k): {k} (each local hull is built from a "
        "focal point and its {km1} nearest neighbours). "
        "{n:,} points were used in the analysis. "
        "The optional individual-hull output was {hulls}."
    ).format(product=product, base=in_base_name, coord=coord_kind, method=method,
             k=k, km1=k - 1, n=n_points,
             hulls=("also created" if individual_hulls else "not created"))

    m = md.Metadata(out_path)
    m.title = arcpy.Describe(out_path).baseName
    m.tags = LOCOH_TAGS
    m.summary = summary
    m.description = description
    m.save()


def write_layer_file(out_fc, messages=None):
    """Create a .lyrx beside out_fc, styled by the cumulative-isopleth template
    (graduated colors by point proportion).

    Works for geodatabase and shapefile outputs: the template renderer is retargeted
    to the output's proportion field (Point_Proportion in a gdb, Point_Prop in a
    shapefile). The .lyrx is written to the folder containing the output workspace
    (layer files cannot live inside a gdb). Returns the .lyrx path, or None on failure.
    """
    if not os.path.exists(LAYER_TEMPLATE):
        if messages is not None:
            messages.addWarningMessage("Layer-file template not found; skipping layer file.")
        return None
    ws = os.path.dirname(out_fc)
    name = arcpy.Describe(out_fc).baseName
    lyr_dir = os.path.dirname(ws) if ws.lower().endswith((".gdb", ".sde")) else ws
    out_lyrx = os.path.join(lyr_dir, name + ".lyrx")
    prop_field = _field_names(out_fc)[2]   # Point_Proportion (gdb) or Point_Prop (shp)
    tmp = "loc_locoh_tmp_layer"            # unique name; renamed in the .lyrx below

    # Step 1: base layer file carrying the correct data connection for this output.
    try:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass
        arcpy.management.MakeFeatureLayer(out_fc, tmp)
        arcpy.management.SaveToLayerFile(tmp, out_lyrx, "RELATIVE")
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage("Could not create layer file: {}".format(e))
        return None
    finally:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass

    # Step 2: splice in the template renderer (retargeted to the proportion field)
    # and carry over layer-level styling such as transparency.
    try:
        with open(LAYER_TEMPLATE, encoding="utf-8") as f:
            tmpl_ld = json.load(f)["layerDefinitions"][0]
        renderer = tmpl_ld["renderer"]
        renderer["field"] = prop_field
        renderer["heading"] = prop_field
        for vv in renderer.get("visualVariables", []):
            vei = vv.get("valueExpressionInfo")
            if vei and "expression" in vei:
                vei["expression"] = "$feature." + prop_field
        with open(out_lyrx, encoding="utf-8") as f:
            lyr = json.load(f)
        out_ld = lyr["layerDefinitions"][0]
        out_ld["renderer"] = renderer
        out_ld["name"] = name
        if "transparency" in tmpl_ld:                 # layer transparency (e.g. 20%)
            out_ld["transparency"] = tmpl_ld["transparency"]
        with open(out_lyrx, "w", encoding="utf-8") as f:
            json.dump(lyr, f)
    except Exception as e:
        if messages is not None:
            messages.addWarningMessage(
                "Layer file created but symbology could not be applied: {}".format(e))
    return out_lyrx


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_locoh_standard(in_features, k, out_isopleths, out_hulls=None, messages=None):
    def log(msg):
        if messages is not None:
            messages.addMessage(msg)

    k = int(k)
    xy, sr, is_geographic = read_points(in_features)
    n = xy.shape[0]
    if n == 0:
        raise arcpy.ExecuteError("Input contains no non-empty point features.")
    if k < 3:
        raise arcpy.ExecuteError("Number of nearest neighbours (k) must be at least 3.")
    if k > n:
        raise arcpy.ExecuteError(
            "k ({}) cannot exceed the number of points ({}).".format(k, n))

    log("Read {:,} points ({} coordinate system).".format(
        n, "geographic" if is_geographic else "projected"))

    log("Pass 1: building local convex hulls...")
    hulls_sorted, areas_sorted = build_local_hulls(xy, k, sr, is_geographic, messages)
    if not hulls_sorted:
        raise arcpy.ExecuteError("No valid convex hulls could be built from the input.")

    log("Pass 2: building cumulative isopleths...")
    cumulative = cumulative_unions(hulls_sorted, messages)

    log("Pass 3: counting enclosed points and measuring areas...")
    counts = cumulative_point_counts(cumulative, xy, sr)
    iso_areas = [_polygon_area(geom, is_geographic) for geom in cumulative]
    proportions = counts / float(n)

    in_base = arcpy.Describe(in_features).baseName

    # --- primary output: dissolved cumulative isopleths -----------------
    # Write largest -> smallest so nested isopleths stack with smaller on top.
    log("Writing isopleth polygons: {}".format(out_isopleths))
    _create_polygon_fc(out_isopleths, sr)
    iso_rows = [(cumulative[i], iso_areas[i], counts[i], proportions[i])
                for i in range(len(cumulative) - 1, -1, -1)]
    _write_rows(out_isopleths, iso_rows)
    write_metadata(out_isopleths, in_base, k, n, is_geographic,
                   individual_hulls=bool(out_hulls), is_isopleth=True)
    iso_lyrx = write_layer_file(out_isopleths, messages)
    if iso_lyrx:
        log("Created layer file: {}".format(iso_lyrx))

    # --- optional output: individual hulls ------------------------------
    # Each hull carries the cumulative count/proportion at its area rank
    # (legacy _LoCoH_Polys semantics); Area is the individual hull's own area.
    if out_hulls:
        log("Writing individual hulls: {}".format(out_hulls))
        _create_polygon_fc(out_hulls, sr)
        hull_rows = [(hulls_sorted[i], areas_sorted[i], counts[i], proportions[i])
                     for i in range(len(hulls_sorted))]
        _write_rows(out_hulls, hull_rows)
        write_metadata(out_hulls, in_base, k, n, is_geographic,
                       individual_hulls=True, is_isopleth=False)

    log("LoCoH (Standard) complete: {:,} isopleths.".format(len(cumulative)))
    return iso_lyrx
