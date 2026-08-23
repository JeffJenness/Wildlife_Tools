# -*- coding: utf-8 -*-
"""Fill Corridor Holes -- close small interior holes in polygon outputs.

A modernized port of the original CorridorDesigner "Fill holes in corridor"
tool. The original used a positive/negative ``Buffer_analysis`` trick (which
needed the Spatial Analyst extension check even though it never actually used
SA) to morphologically close narrow gaps. This version instead measures every
interior ring (hole) of every input feature directly and fills only the ones
SMALLER than a user threshold, leaving genuinely large holes -- a lake, a
real excluded area -- untouched. This is the opposite direction from a
literal reading of "holes above a threshold removed": Jeff confirmed
2026-08-09 that filling SMALL holes and keeping LARGE ones is the intended,
useful behavior, matching every hole-cleanup convention in GIS (including
Esri's own ``Eliminate Polygon Part``, whose only supported condition is
"eliminate parts smaller than X").

NOT ESRI'S ``EliminatePolygonPart``. That tool was tested directly against
this need and rejected: its area threshold is PLANAR ONLY, and on a
geographic (lat/lon) input it does not correctly convert an areal-unit
string at all -- a 50,000 sq m threshold eliminated a real ~111,000 sq m
hole outright (verified empirically, 2026-08-09). Least-Cost Corridor
output, the primary input this tool targets, is very often geographic (its
own progress messages already say so on a geographic cost raster). So this
tool measures every hole's TRUE GEODESIC area directly via
``Polygon.getArea("GEODESIC", "SQUAREMETERS")`` -- exact on any coordinate
system, projected or geographic -- and reconstructs the geometry by hand via
each polygon's ``__geo_interface__`` (Esri's own ring/hole grouping,
avoiding any hand-rolled ring-orientation logic) rather than relying on a
GP tool with a silent geodesic blind spot.

NOT LAND-FACET-SPECIFIC. Works on any polygon feature class -- a Least-Cost
Corridor output, a dissolved species HSM corridor, or any other polygon
dataset with unwanted small holes. Accepts multiple input feature classes in
one run; each gets its own output (workspace + name suffix), with all
attribute fields preserved unchanged (a straight ``CopyFeatures`` schema
copy, then only the geometry is edited in place).

METADATA: when an input already carries a Land Facet linkage record (most
commonly a ``corridor`` record, since that is the expected typical input),
the SAME record -- same kind, same analysis id -- is stamped on the output
with a ``post_processing`` step appended (see
``land_facet_linkage.add_post_processing_step``), so the Land Facet Data
Report keeps describing the output as the same analysis product while also
noting it was hole-cleaned. An input with no record gets none on the output
either -- there is nothing to chain from.
"""
import os

import arcpy

import mahalanobis as _mh
import land_facet_linkage as _lnk
import land_facet_termini as _term

DEFAULT_SUFFIX = "_holes_removed"

UNIT_CHOICES = ["Square Meters", "Square Kilometers", "Square Feet",
                "Square Miles"]
DEFAULT_UNITS = UNIT_CHOICES[0]

# Exact conversions (1 ft = 0.3048 m and 1 mile = 5280 ft are exact by
# definition; the sq-ft and sq-mile factors follow from squaring those).
_UNIT_FACTORS_M2 = {
    "Square Meters": 1.0,
    "Square Kilometers": 1_000_000.0,
    "Square Feet": 0.09290304,
    "Square Miles": 2_589_988.110336,
}


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def threshold_to_m2(value, units):
    """Convert a user threshold (in ``units``, one of ``UNIT_CHOICES``) to
    square meters, the unit every hole's geodesic area is measured in."""
    factor = _UNIT_FACTORS_M2.get(units)
    if factor is None:
        raise arcpy.ExecuteError(
            "Unrecognized size unit '{}' -- expected one of: {}.".format(
                units, ", ".join(UNIT_CHOICES)))
    return float(value) * factor


def _fill_small_holes(geom, threshold_m2):
    """Return ``(new_geom, n_removed, n_total_holes, was_geodesic)``.

    ``new_geom`` is the SAME object as ``geom`` (no rebuild) when nothing
    qualifies for removal -- avoids an unnecessary reconstruction round-trip
    on the (typical) majority of features that have no small holes at all.
    Every exterior ring is kept unconditionally; only interior rings (holes)
    are ever candidates for removal. Densifies true-curve geometry first
    (this suite's standing rule) so a curved hole (e.g. a geodatabase circle)
    is measured and reconstructed from real vertices, not just its arc
    endpoints.
    """
    if getattr(geom, "hasCurves", False):
        ext = geom.extent
        step = max((ext.width + ext.height) / 1000.0, 1e-9)
        try:
            geom = geom.densify("DISTANCE", step, step)
        except Exception:                                          # noqa: BLE001
            pass

    sr = geom.spatialReference
    # Iterate arcpy parts directly (exterior ring, then interior rings
    # separated by None points) rather than __geo_interface__: the GeoJSON
    # round trip flattens coordinates to 2-D, silently zeroing Z and M on
    # exactly the features that get rebuilt. Z/M-awareness must be read off
    # the POINTS (None / NaN when absent) -- arcpy geometry objects expose
    # no hasZ/hasM here.
    parts = []
    for part in geom:
        rings = [[]]
        for pt in part:
            if pt is None:
                rings.append([])
            else:
                rings[-1].append(pt)
        parts.append([r for r in rings if r])

    was_geodesic = True
    n_removed = 0
    n_total = 0
    new_parts = []
    changed = False
    for rings in parts:
        if not rings:
            continue
        new_rings = [rings[0]]                                  # exterior, always kept
        for ring in rings[1:]:
            n_total += 1
            ring_poly = arcpy.Polygon(
                arcpy.Array([arcpy.Point(pt.X, pt.Y) for pt in ring]), sr)
            area, is_geo = _term._geodesic_area_m2(ring_poly)
            was_geodesic = was_geodesic and is_geo
            if area < threshold_m2:
                n_removed += 1
                changed = True
                continue
            new_rings.append(ring)
        new_parts.append(new_rings)

    if not changed:
        return geom, 0, n_total, was_geodesic

    arr = arcpy.Array()
    for rings in new_parts:
        pts = []
        for i, ring in enumerate(rings):
            if i:
                pts.append(None)          # ring separator within a part
            pts.extend(ring)
        arr.add(arcpy.Array(pts))
    probe = next((p for rings in new_parts for r in rings for p in r), None)
    has_z = probe is not None and probe.Z is not None
    has_m = (probe is not None and probe.M is not None
             and probe.M == probe.M)                    # NaN = no M
    rebuilt = arcpy.Polygon(arr, sr, has_z, has_m)
    return rebuilt, n_removed, n_total, was_geodesic


def _resolve_fc_output(out_workspace, name):
    """A geodatabase takes the bare name; a folder needs a real vector
    format extension (shapefile) -- mirrors ``mahalanobis.
    resolve_raster_output``'s folder-vs-gdb split for feature classes."""
    if _mh.in_gdb_container(out_workspace):
        return os.path.join(out_workspace, name)
    if not os.path.splitext(name)[1]:
        name = name + ".shp"
    return os.path.join(out_workspace, name)


def run_fill_corridor_holes(in_features_list, threshold_value, threshold_units,
                            out_workspace, name_suffix=DEFAULT_SUFFIX,
                            messages=None):
    """Fill (remove) holes smaller than the threshold in every input polygon
    feature class, writing one new feature class per input -- named
    ``<input base name><name_suffix>`` in ``out_workspace`` -- with every
    attribute field preserved unchanged. Returns the list of output paths.
    """
    if not in_features_list:
        raise arcpy.ExecuteError("No input feature classes were supplied.")
    if threshold_value is None or float(threshold_value) <= 0:
        raise arcpy.ExecuteError(
            "The hole-size threshold must be greater than zero.")
    threshold_m2 = threshold_to_m2(threshold_value, threshold_units or DEFAULT_UNITS)
    suffix = name_suffix or DEFAULT_SUFFIX

    resolved = []
    for item in in_features_list:
        try:
            path = arcpy.Describe(item).catalogPath
        except Exception:                                          # noqa: BLE001
            path = str(item)
        # pin every input's selection UP FRONT: a later input's live
        # selection can be dropped while the earlier inputs are being
        # processed (standing rule, see mahalanobis.pin_selection)
        resolved.append((item, path, _mh.pin_selection(item)))

    _mh.begin_phases([(os.path.basename(p), 4)
                      for _item, p, _pin in resolved])

    out_paths = []
    for item, src, pin in resolved:
        _mh.phase_advance()
        desc = arcpy.Describe(item)
        if desc.shapeType != "Polygon":
            _warn(messages, "{}: not a polygon feature class; "
                            "skipped.".format(os.path.basename(src)))
            continue

        base = _mh_legalize(os.path.splitext(os.path.basename(src))[0])
        out_name = base + suffix
        out_path = _resolve_fc_output(out_workspace, out_name)

        # The selection was PINNED at run entry (a live layer's
        # selection can be dropped while earlier inputs are processed);
        # copy from an explicit OID scratch layer when one exists, so
        # exactly the pinned features are used. A layer with no
        # selection, or a bare dataset path, copies everything.
        n_total_src, n_selected_src, has_selection = _mh.selection_info(item, src)
        if pin:
            has_selection = True
            n_selected_src = len(pin)
            plyr = _mh.pinned_layer(src, pin, "fch_pin")
            try:
                arcpy.management.CopyFeatures(plyr, out_path)
            finally:
                try:
                    arcpy.management.Delete(plyr)
                except Exception:                                  # noqa: BLE001
                    pass
        else:
            arcpy.management.CopyFeatures(item, out_path)

        total_rows = int(arcpy.management.GetCount(out_path)[0])
        if has_selection:
            _say(messages, "{}: {:,} of {:,} feature{} selected -- "
                           "processing only the selection.".format(
                               os.path.basename(src), n_selected_src,
                               n_total_src, "" if n_total_src == 1 else "s"))
        n_features_total = 0
        n_features_affected = 0
        n_holes_removed = 0
        n_holes_total = 0
        geodesic_all = True

        with arcpy.da.UpdateCursor(out_path, ["SHAPE@"]) as cur:
            for i, row in enumerate(cur):
                _mh.phase_step(i, max(total_rows, 1))
                geom = row[0]
                if geom is None:
                    continue
                n_features_total += 1
                new_geom, removed, total, was_geo = _fill_small_holes(
                    geom, threshold_m2)
                geodesic_all = geodesic_all and was_geo
                n_holes_total += total
                if removed:
                    n_holes_removed += removed
                    n_features_affected += 1
                    cur.updateRow([new_geom])

        _say(messages, "{}: {:,} of {:,} hole{} removed, across {:,} of "
                       "{:,} feature{}.".format(
                           os.path.basename(src), n_holes_removed,
                           n_holes_total, "" if n_holes_total == 1 else "s",
                           n_features_affected, n_features_total,
                           "" if n_features_total == 1 else "s"))
        if not geodesic_all:
            _warn(messages, "{}: hole areas were measured planar, not "
                            "geodesic, for at least one feature (its "
                            "coordinate system does not support a geodesic "
                            "measure).".format(os.path.basename(src)))

        detail = ("holes smaller than {:g} {} filled ({:,} of {:,} holes "
                  "removed, across {:,} of {:,} features)").format(
                      float(threshold_value), threshold_units or DEFAULT_UNITS,
                      n_holes_removed, n_holes_total, n_features_affected,
                      n_features_total)
        if has_selection:
            detail += "; run on a selection of {:,} of {:,} features".format(
                n_selected_src, n_total_src)

        src_rec = _lnk.read(src, None)
        if src_rec:
            _lnk.write(out_path, _lnk.add_post_processing_step(
                src_rec, tool="Fill Corridor Holes", detail=detail),
                messages)

        try:
            from arcpy import metadata as md
            m = md.Metadata(out_path)
            m.title = os.path.basename(out_path)
            m.summary = "Created by Fill Corridor Holes from {}: {}.".format(
                src, detail)
            m.description = (
                "A copy of \"{}\" with small interior holes closed -- every "
                "attribute field is unchanged; only holes smaller than the "
                "threshold were filled. Holes at or above the threshold "
                "were left as real, meaningful gaps.".format(src))
            m.save()
        except Exception as e:                                      # noqa: BLE001
            _warn(messages, "Could not write output metadata: {}".format(e))

        out_paths.append(out_path)

    _mh.end_phases()
    if not out_paths:
        raise arcpy.ExecuteError(
            "No output was written -- every input was skipped (see "
            "messages above).")
    return out_paths


def _mh_legalize(name):
    """Dataset-name-safe token, reusing the geodatabase naming rule (no
    leading digit) without importing the .pyt's private helper."""
    out = []
    for ch in str(name):
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    s = "".join(out).strip("_") or "poly"
    if s[0].isdigit():
        s = "P" + s
    return s
