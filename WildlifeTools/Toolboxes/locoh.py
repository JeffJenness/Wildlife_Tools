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
GETZ_2007_DOI = "https://doi.org/10.1371/journal.pone.0000207"

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


def _density_per_sqkm(count, area, is_geographic, dist_mpu):
    """Point density in points per square kilometer.

    `area` is the value from _polygon_area (square meters for geographic data,
    square CRS-units for projected data); it is converted to square meters, then
    to km2, so the result is always points/km2.
    """
    area_m2 = area if is_geographic else area * (dist_mpu ** 2)
    return (count * 1.0e6 / area_m2) if area_m2 > 0 else 0.0


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
STANDARD_FIELDS = [
    ("Area", "DOUBLE", "Area"),
    ("Point_Count", "LONG", "Point_Cnt"),
    ("Point_Proportion", "DOUBLE", "Point_Prop"),
    ("Pts_per_SqKm", "DOUBLE", "Pts_SqKm"),
]
ADAPTIVE_FIELDS = [
    ("Area", "DOUBLE", "Area"),
    ("Point_Count", "LONG", "Point_Cnt"),
    ("Point_Proportion", "DOUBLE", "Point_Prop"),
    ("Adaptive_Cumulative_Dist", "DOUBLE", "Cum_Dist"),
    ("Pts_per_SqKm", "DOUBLE", "Pts_SqKm"),
]
FIXEDSPHERE_FIELDS = [
    ("Area", "DOUBLE", "Area"),
    ("Point_Count", "LONG", "Point_Cnt"),
    ("Point_Proportion", "DOUBLE", "Point_Prop"),
    ("Sphere_Point_Count", "LONG", "Sphere_Pts"),
    ("Pts_per_SqKm", "DOUBLE", "Pts_SqKm"),
]


def _is_shapefile(out_path):
    return str(out_path).lower().endswith(".shp")


def _field_names(out_path, field_defs):
    """Ordered field names for the target format (short names for shapefiles)."""
    shp = _is_shapefile(out_path)
    return [(d[2] if shp else d[0]) for d in field_defs]


def _create_polygon_fc(out_path, sr, field_defs):
    out_ws, out_name = os.path.split(out_path)
    arcpy.management.CreateFeatureclass(out_ws, out_name, "POLYGON", spatial_reference=sr)
    shp = _is_shapefile(out_path)
    for gdb_name, ftype, shp_name in field_defs:
        arcpy.management.AddField(out_path, shp_name if shp else gdb_name, ftype)
    if shp:
        # Shapefiles are created with a default "Id" field; drop it now that our own
        # fields exist (a shapefile must always retain at least one attribute field).
        if any(f.name.lower() == "id" for f in arcpy.ListFields(out_path)):
            arcpy.management.DeleteField(out_path, "Id")
    return out_path


def _write_rows(out_path, rows, field_defs):
    """rows: iterable of (geometry, value1, value2, ...) matching field_defs order.

    LONG fields are cast to int; other numeric values are written as-is.
    """
    names = _field_names(out_path, field_defs)
    with arcpy.da.InsertCursor(out_path, ["SHAPE@"] + names) as cur:
        for row in rows:
            geom = row[0]
            vals = [int(v) if fd[1] == "LONG" else v
                    for v, fd in zip(row[1:], field_defs)]
            cur.insertRow([geom] + vals)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def _long_date():
    now = datetime.now()
    return "{:%B} {}, {}".format(now, now.day, now.year)


def _apply_metadata(out_path, summary, description):
    """Set Title (= feature class name), Tags, Summary, and Description."""
    from arcpy import metadata as md
    m = md.Metadata(out_path)
    m.title = arcpy.Describe(out_path).baseName
    m.tags = LOCOH_TAGS
    m.summary = summary
    m.description = description
    m.save()


def write_metadata(out_path, in_base_name, k, n_points, is_geographic, individual_hulls, is_isopleth):
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

    _apply_metadata(out_path, summary, description)


def write_metadata_adaptive(out_path, in_base_name, a_report, report_unit, used_max,
                            n_points, is_geographic, individual_hulls, is_isopleth):
    coord_kind = "geographic" if is_geographic else "projected"
    method = ("Haversine (spherical) formula" if is_geographic
              else "planar distance converted to meters")
    product = ("dissolved cumulative isopleth polygons" if is_isopleth
               else "individual local convex hulls")
    a_phrase = ("set automatically to the maximum distance between any pair of points "
                "({a:,.2f} {unit})" if used_max
                else "specified as {a:,.2f} {unit}").format(a=a_report, unit=report_unit)

    summary = (
        'Adaptive (a-) Local Convex Hulls, calculated according to methods described by '
        'Wayne M. Getz et al. in their 2007 manuscript "LoCoH: nonparametric kernel '
        'methods for constructing home ranges and utilization distributions" (see {doi2007}), '
        'building on Getz and Wilmers (2004, see {doi2004}). Hulls calculated from {base} on {date}.'
    ).format(doi2007=GETZ_2007_DOI, doi2004=GETZ_WILMERS_DOI, base=in_base_name, date=_long_date())

    description = (
        "This feature class contains the {product} from an Adaptive (a-) Local Convex "
        "Hull (LoCoH) home-range analysis. Each local hull is built from a focal point "
        "and its nearest neighbours, adding neighbours in order of increasing distance "
        "until their cumulative distance reaches the maximum cumulative length a. "
        "The value of a was {a_phrase}. "
        "Input point features: '{base}' ({coord} coordinate system; distances computed "
        "using the {method}). {n:,} points were used in the analysis. "
        "Cumulative distances are reported in {unit}. "
        "The optional individual-hull output was {hulls}."
    ).format(product=product, a_phrase=a_phrase, base=in_base_name, coord=coord_kind,
             method=method, n=n_points, unit=report_unit,
             hulls=("also created" if individual_hulls else "not created"))

    _apply_metadata(out_path, summary, description)


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
    prop_field = "Point_Prop" if _is_shapefile(out_fc) else "Point_Proportion"
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
    dist_mpu = None if is_geographic else float(sr.metersPerUnit)
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
    _create_polygon_fc(out_isopleths, sr, STANDARD_FIELDS)
    iso_rows = [(cumulative[i], iso_areas[i], counts[i], proportions[i],
                 _density_per_sqkm(counts[i], iso_areas[i], is_geographic, dist_mpu))
                for i in range(len(cumulative) - 1, -1, -1)]
    _write_rows(out_isopleths, iso_rows, STANDARD_FIELDS)
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
        _create_polygon_fc(out_hulls, sr, STANDARD_FIELDS)
        hull_rows = [(hulls_sorted[i], areas_sorted[i], counts[i], proportions[i],
                      _density_per_sqkm(counts[i], areas_sorted[i], is_geographic, dist_mpu))
                     for i in range(len(hulls_sorted))]
        _write_rows(out_hulls, hull_rows, STANDARD_FIELDS)
        write_metadata(out_hulls, in_base, k, n, is_geographic,
                       individual_hulls=True, is_isopleth=False)

    log("LoCoH (Standard) complete: {:,} isopleths.".format(len(cumulative)))
    return iso_lyrx


# ---------------------------------------------------------------------------
# Adaptive (a-) LoCoH
# ---------------------------------------------------------------------------
def _focal_distances_m(xy, i, is_geographic, dist_mpu):
    """Distances (meters) from point i to all points."""
    x = xy[:, 0]
    y = xy[:, 1]
    if is_geographic:
        return wg.haversine_distance(y[i], x[i], y, x)            # already meters
    return wg.euclidean_distance(x[i], y[i], x, y) * dist_mpu     # CRS units -> meters


def _max_pairwise_distance_m(xy, is_geographic, dist_mpu):
    """Maximum distance (meters) between any pair of points (Trainor et al. heuristic)."""
    n = xy.shape[0]
    mx = 0.0
    for i in range(n - 1):
        d = _focal_distances_m(xy, i, is_geographic, dist_mpu)
        m = float(np.max(d[i + 1:]))
        if m > mx:
            mx = m
    return mx


def build_adaptive_hulls(xy, sr, is_geographic, dist_mpu, a_meters, messages=None):
    """Build one hull per point from its adaptive (cumulative-distance <= a) neighbours.

    Returns (hulls, areas, counts, cumdists) as parallel lists sorted by point count
    then area (legacy a-LoCoH ordering). `counts` is each hull's own neighbour count
    (used for the sort); `cumdists` is the cumulative neighbour distance in meters.
    """
    n = xy.shape[0]
    hulls, areas, counts, cumdists = [], [], [], []
    skipped = 0
    for i in range(n):
        d = _focal_distances_m(xy, i, is_geographic, dist_mpu)
        order = np.argsort(d, kind="stable")
        cum = np.cumsum(d[order])
        # include neighbours (focal first, distance 0) while cumulative distance <= a
        sel_count = int(np.count_nonzero(cum <= a_meters))
        # Getz et al. 2007: always include at least the focal point and its two
        # nearest neighbours, so every hull can form a polygon.
        sel_count = max(sel_count, min(3, len(order)))
        sel = order[:sel_count]
        hull = _convex_hull(xy[sel], sr)
        if hull is None or hull.type != "polygon" or hull.area == 0:
            skipped += 1
            continue
        hulls.append(hull)
        areas.append(_polygon_area(hull, is_geographic))
        counts.append(sel_count)
        cumdists.append(float(cum[sel_count - 1]))
        if messages is not None and (i + 1) % 250 == 0:
            messages.addMessage("  ...hulls built for {:,} of {:,} points".format(i + 1, n))

    if skipped and messages is not None:
        messages.addWarningMessage(
            "{} hull(s) were degenerate (collinear or duplicate points) and skipped.".format(skipped))

    # Getz et al. (2007) a-/r-LoCoH ordering: most points first (descending point
    # count), smallest area first as the tie-break, so isopleths build from the
    # densest core outward. (This corrects the legacy tool's reversed ordering.)
    order = sorted(range(len(hulls)), key=lambda j: (-counts[j], areas[j]))
    return ([hulls[j] for j in order], [areas[j] for j in order],
            [counts[j] for j in order], [cumdists[j] for j in order])


def run_locoh_adaptive(in_features, a_meters, report_factor, report_unit,
                       out_isopleths, out_hulls=None, messages=None):
    """Adaptive (a-) LoCoH.

    a_meters: the maximum cumulative length in meters, or None to use the maximum
        distance between any pair of points.
    report_factor: meters per reporting unit (1.0 for meters); Adaptive_Cumulative_Dist
        is reported as meters / report_factor.
    report_unit: name of the reporting unit (e.g. "Meters", "Feet") for metadata.
    """
    def log(msg):
        if messages is not None:
            messages.addMessage(msg)

    xy, sr, is_geographic = read_points(in_features)
    n = xy.shape[0]
    if n == 0:
        raise arcpy.ExecuteError("Input contains no non-empty point features.")
    dist_mpu = None if is_geographic else float(sr.metersPerUnit)

    log("Read {:,} points ({} coordinate system).".format(
        n, "geographic" if is_geographic else "projected"))

    used_max = a_meters is None
    if used_max:
        log("Computing the maximum distance between any pair of points...")
        a_meters = _max_pairwise_distance_m(xy, is_geographic, dist_mpu)
        log("  maximum pairwise distance a = {:,.2f} m".format(a_meters))
    if a_meters <= 0:
        raise arcpy.ExecuteError("The maximum cumulative length (a) must be greater than zero.")

    log("Pass 1: building adaptive local convex hulls (a = {:,.2f} m)...".format(a_meters))
    hulls_sorted, areas_sorted, counts_sorted, cumdists_sorted = build_adaptive_hulls(
        xy, sr, is_geographic, dist_mpu, a_meters, messages)
    if not hulls_sorted:
        raise arcpy.ExecuteError(
            "No valid hulls were built: every focal point had fewer than 3 neighbours "
            "within the cumulative length a. Try a larger value.")

    log("Pass 2: building cumulative isopleths...")
    cumulative = cumulative_unions(hulls_sorted, messages)

    log("Pass 3: counting enclosed points and measuring areas...")
    counts = cumulative_point_counts(cumulative, xy, sr)
    iso_areas = [_polygon_area(geom, is_geographic) for geom in cumulative]
    proportions = counts / float(n)
    cumdist_report = [cd / report_factor for cd in cumdists_sorted]   # meters -> report unit
    a_report = a_meters / report_factor

    in_base = arcpy.Describe(in_features).baseName

    # --- primary output: dissolved cumulative isopleths (largest -> smallest) ---
    log("Writing isopleth polygons: {}".format(out_isopleths))
    _create_polygon_fc(out_isopleths, sr, ADAPTIVE_FIELDS)
    iso_rows = [(cumulative[i], iso_areas[i], counts[i], proportions[i], cumdist_report[i],
                 _density_per_sqkm(counts[i], iso_areas[i], is_geographic, dist_mpu))
                for i in range(len(cumulative) - 1, -1, -1)]
    _write_rows(out_isopleths, iso_rows, ADAPTIVE_FIELDS)
    write_metadata_adaptive(out_isopleths, in_base, a_report, report_unit, used_max,
                            n, is_geographic, bool(out_hulls), is_isopleth=True)
    iso_lyrx = write_layer_file(out_isopleths, messages)
    if iso_lyrx:
        log("Created layer file: {}".format(iso_lyrx))

    # --- optional output: individual hulls ---
    if out_hulls:
        log("Writing individual hulls: {}".format(out_hulls))
        _create_polygon_fc(out_hulls, sr, ADAPTIVE_FIELDS)
        hull_rows = [(hulls_sorted[i], areas_sorted[i], counts[i], proportions[i], cumdist_report[i],
                      _density_per_sqkm(counts[i], areas_sorted[i], is_geographic, dist_mpu))
                     for i in range(len(hulls_sorted))]
        _write_rows(out_hulls, hull_rows, ADAPTIVE_FIELDS)
        write_metadata_adaptive(out_hulls, in_base, a_report, report_unit, used_max,
                                n, is_geographic, True, is_isopleth=False)

    log("LoCoH (Adaptive) complete: {:,} isopleths.".format(len(cumulative)))
    return iso_lyrx


# ---------------------------------------------------------------------------
# Fixed-sphere (r-) LoCoH
# ---------------------------------------------------------------------------
def _max_nearest_neighbor_distance_m(xy, is_geographic, dist_mpu):
    """Greatest nearest-neighbour distance (meters): for each point, the distance to
    its closest other point, then the maximum of those. Getz r1 = half this value."""
    n = xy.shape[0]
    mx = 0.0
    for i in range(n):
        d = _focal_distances_m(xy, i, is_geographic, dist_mpu)
        d[i] = np.inf                      # exclude self
        m = float(np.min(d))
        if m > mx:
            mx = m
    return mx


def build_fixed_sphere_hulls(xy, sr, is_geographic, dist_mpu, r_meters, messages=None):
    """Build one hull per point from the points within radius r (the "sphere of
    influence"), always including at least the focal point and its two nearest
    neighbours (Getz et al. 2007).

    Returns (hulls, areas, counts, sphere_counts) sorted most-points-first, area
    ascending for ties. `counts` is the hull's point count (used for ordering);
    `sphere_counts` is the true number of points within radius r.
    """
    n = xy.shape[0]
    hulls, areas, counts, sphere_counts = [], [], [], []
    skipped = 0
    for i in range(n):
        d = _focal_distances_m(xy, i, is_geographic, dist_mpu)
        order = np.argsort(d, kind="stable")
        within = int(np.count_nonzero(d <= r_meters))     # points within r (incl. focal)
        hull_count = max(within, min(3, n))               # Getz: at least focal + 2 nearest
        sel = order[:hull_count]
        hull = _convex_hull(xy[sel], sr)
        if hull is None or hull.type != "polygon" or hull.area == 0:
            skipped += 1
            continue
        hulls.append(hull)
        areas.append(_polygon_area(hull, is_geographic))
        counts.append(hull_count)
        sphere_counts.append(within)
        if messages is not None and (i + 1) % 250 == 0:
            messages.addMessage("  ...hulls built for {:,} of {:,} points".format(i + 1, n))

    if skipped and messages is not None:
        messages.addWarningMessage(
            "{} hull(s) were degenerate (collinear or duplicate points) and skipped.".format(skipped))

    # Getz et al. 2007 a-/r-LoCoH ordering: most points first, smallest area for ties.
    order = sorted(range(len(hulls)), key=lambda j: (-counts[j], areas[j]))
    return ([hulls[j] for j in order], [areas[j] for j in order],
            [counts[j] for j in order], [sphere_counts[j] for j in order])


def write_metadata_fixed_sphere(out_path, in_base_name, r_report, report_unit, used_auto,
                                n_points, is_geographic, individual_hulls, is_isopleth):
    coord_kind = "geographic" if is_geographic else "projected"
    method = ("Haversine (spherical) formula" if is_geographic
              else "planar distance converted to meters")
    product = ("dissolved cumulative isopleth polygons" if is_isopleth
               else "individual local convex hulls")
    r_phrase = ("set automatically to half the maximum nearest-neighbour distance "
                "(r = {r:,.2f} {unit})" if used_auto
                else "specified as r = {r:,.2f} {unit}").format(r=r_report, unit=report_unit)

    summary = (
        'Fixed-sphere (r-) Local Convex Hulls, calculated according to methods described by '
        'Wayne M. Getz et al. in their 2007 manuscript "LoCoH: nonparametric kernel methods '
        'for constructing home ranges and utilization distributions" (see {doi2007}), building '
        'on Getz and Wilmers (2004, see {doi2004}). Hulls calculated from {base} on {date}.'
    ).format(doi2007=GETZ_2007_DOI, doi2004=GETZ_WILMERS_DOI, base=in_base_name, date=_long_date())

    description = (
        "This feature class contains the {product} from a Fixed-sphere (r-) Local Convex "
        "Hull (LoCoH) home-range analysis. Each local hull is the convex hull of a focal "
        "point and all points within a sphere of influence of radius r (always including at "
        "least the focal point and its two nearest neighbours). The radius r was {r_phrase}. "
        "Input point features: '{base}' ({coord} coordinate system; distances computed using "
        "the {method}). {n:,} points were used in the analysis. "
        "The optional individual-hull output was {hulls}."
    ).format(product=product, r_phrase=r_phrase, base=in_base_name, coord=coord_kind,
             method=method, n=n_points,
             hulls=("also created" if individual_hulls else "not created"))

    _apply_metadata(out_path, summary, description)


def run_locoh_fixed_sphere(in_features, r_meters, report_factor, report_unit,
                           out_isopleths, out_hulls=None, messages=None):
    """Fixed-sphere (r-) LoCoH.

    r_meters: the sphere radius in meters, or None to use half the maximum
        nearest-neighbour distance (Getz r1 heuristic).
    report_factor / report_unit: meters per reporting unit and its name (the radius
        is recorded in metadata in this unit).
    """
    def log(msg):
        if messages is not None:
            messages.addMessage(msg)

    xy, sr, is_geographic = read_points(in_features)
    n = xy.shape[0]
    if n == 0:
        raise arcpy.ExecuteError("Input contains no non-empty point features.")
    dist_mpu = None if is_geographic else float(sr.metersPerUnit)

    log("Read {:,} points ({} coordinate system).".format(
        n, "geographic" if is_geographic else "projected"))

    used_auto = r_meters is None
    if used_auto:
        log("Computing the radius (half the maximum nearest-neighbour distance)...")
        max_nn = _max_nearest_neighbor_distance_m(xy, is_geographic, dist_mpu)
        r_meters = max_nn / 2.0
        log("  max nearest-neighbour distance = {:,.2f} m; radius r = {:,.2f} m".format(max_nn, r_meters))
    if r_meters <= 0:
        raise arcpy.ExecuteError("The radius (r) must be greater than zero.")

    log("Pass 1: building fixed-sphere local convex hulls (r = {:,.2f} m)...".format(r_meters))
    hulls_sorted, areas_sorted, counts_sorted, sphere_sorted = build_fixed_sphere_hulls(
        xy, sr, is_geographic, dist_mpu, r_meters, messages)
    if not hulls_sorted:
        raise arcpy.ExecuteError("No valid hulls were built from the input.")

    log("Pass 2: building cumulative isopleths...")
    cumulative = cumulative_unions(hulls_sorted, messages)

    log("Pass 3: counting enclosed points and measuring areas...")
    counts = cumulative_point_counts(cumulative, xy, sr)
    iso_areas = [_polygon_area(geom, is_geographic) for geom in cumulative]
    proportions = counts / float(n)
    r_report = r_meters / report_factor

    in_base = arcpy.Describe(in_features).baseName

    log("Writing isopleth polygons: {}".format(out_isopleths))
    _create_polygon_fc(out_isopleths, sr, FIXEDSPHERE_FIELDS)
    iso_rows = [(cumulative[i], iso_areas[i], counts[i], proportions[i], sphere_sorted[i],
                 _density_per_sqkm(counts[i], iso_areas[i], is_geographic, dist_mpu))
                for i in range(len(cumulative) - 1, -1, -1)]
    _write_rows(out_isopleths, iso_rows, FIXEDSPHERE_FIELDS)
    write_metadata_fixed_sphere(out_isopleths, in_base, r_report, report_unit, used_auto,
                                n, is_geographic, bool(out_hulls), is_isopleth=True)
    iso_lyrx = write_layer_file(out_isopleths, messages)
    if iso_lyrx:
        log("Created layer file: {}".format(iso_lyrx))

    if out_hulls:
        log("Writing individual hulls: {}".format(out_hulls))
        _create_polygon_fc(out_hulls, sr, FIXEDSPHERE_FIELDS)
        hull_rows = [(hulls_sorted[i], areas_sorted[i], counts[i], proportions[i], sphere_sorted[i],
                      _density_per_sqkm(counts[i], areas_sorted[i], is_geographic, dist_mpu))
                     for i in range(len(hulls_sorted))]
        _write_rows(out_hulls, hull_rows, FIXEDSPHERE_FIELDS)
        write_metadata_fixed_sphere(out_hulls, in_base, r_report, report_unit, used_auto,
                                    n, is_geographic, True, is_isopleth=False)

    log("LoCoH (Fixed Sphere) complete: {:,} isopleths.".format(len(cumulative)))
    return iso_lyrx
