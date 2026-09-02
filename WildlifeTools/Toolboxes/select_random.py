# -*- coding: utf-8 -*-
"""Select Random Records -- random and systematic SELECTION of
existing features or table rows.

A replication of Esri's Data Reviewer "Select Random Sample" tool
(which needs the Data Reviewer extension), extended per Jeff Jenness's
classic sampling tools -- and it works at every license level, on
feature layers AND standalone tables.

SAMPLE-SIZE METHODS:
  * a fixed NUMBER of records;
  * a PERCENTAGE of the population;
  * AUTO CALCULATE -- the statistical sample size for estimating a
    proportion at a chosen confidence level and margin of error:
    n = (z/m)^2 * p(1-p) with p = 0.5 (the conservative worst case),
    then the finite-population correction n' = n*N/(n + (N-1)),
    rounded up. 95%/5% needs ~384 records almost regardless of N;
  * a fixed number with selection probability PROPORTIONAL TO
    AREA / LENGTH (geodesic; polygons and polylines) -- larger
    features are proportionally more likely to be chosen
    (probability-proportional-to-size, via Efraimidis-Spirakis
    weighted reservoir keys);
  * SYSTEMATIC selection: every Xth record (optionally from a random
    starting record within the first X), or all odd-numbered or
    even-numbered records -- walked in the order of a SORT FIELD
    (default: the ObjectID field).

THE POPULATION depends on the SELECTION METHOD (Esri's four): New
selection draws from ALL records; Add to the current selection draws
only from the currently UNSELECTED records; Remove from and Select
subset of the current selection consider ONLY the currently selected
records. In every case the population rule applies BEFORE any
calculation -- percentages, Auto Calculate's N, area/length totals
and the systematic walk order all refer to that population alone.

An optional analysis EXTENT and/or POLYGON ANALYSIS BOUNDARY (a
polygon layer -- its own selection honored -- or feature class)
further restrict feature populations to features INTERSECTING them
(default) or ENTIRELY WITHIN them; for entirely-within the boundary
is dissolved first, so a feature straddling two ADJACENT boundary
polygons still counts as inside their union. An optional seed makes
every draw exactly reproducible.
"""
import math
import os

import numpy as np

import mahalanobis as _mh

METHOD_FIXED = "FIXED"
METHOD_PERCENT = "PERCENT"
METHOD_AUTO = "AUTO"
METHOD_PPS = "PPS"
METHOD_EVERY_X = "EVERY_X"
METHOD_ODD = "ODD"
METHOD_EVEN = "EVEN"

SAMPLE_METHODS = [
    "Fixed number of records",
    "Percentage of records",
    "Auto Calculate (from confidence level and margin of error)",
    "Fixed number, probability proportional to area / length",
    "Systematic: every Xth record",
    "Systematic: odd-numbered records",
    "Systematic: even-numbered records"]


def canon_sample(s):
    if s in (METHOD_FIXED, METHOD_PERCENT, METHOD_AUTO, METHOD_PPS,
             METHOD_EVERY_X, METHOD_ODD, METHOD_EVEN):
        return s
    t = str(s or "").strip().lower()
    if t.startswith("fixed number of"):
        return METHOD_FIXED
    if t.startswith("percentage"):
        return METHOD_PERCENT
    if t.startswith("auto"):
        return METHOD_AUTO
    if t.startswith("fixed number, probability") or "proportional" in t:
        return METHOD_PPS
    if "every" in t:
        return METHOD_EVERY_X
    if "odd" in t:
        return METHOD_ODD
    if "even" in t:
        return METHOD_EVEN
    return None


SEL_NEW = "NEW"
SEL_ADD = "ADD"
SEL_REMOVE = "REMOVE"
SEL_SUBSET = "SUBSET"
SELECTION_METHODS = [
    "New selection",
    "Add to the current selection",
    "Remove from the current selection",
    "Select subset from the current selection"]


def canon_selection(s):
    if s in (SEL_NEW, SEL_ADD, SEL_REMOVE, SEL_SUBSET):
        return s
    t = str(s or "").strip().lower()
    if not t or t.startswith("new"):
        return SEL_NEW
    if t.startswith("add"):
        return SEL_ADD
    if t.startswith("remove"):
        return SEL_REMOVE
    if t.startswith("select subset") or t.startswith("subset"):
        return SEL_SUBSET
    return None


REL_INTERSECT = "INTERSECT"
REL_WITHIN = "WITHIN"
EXTENT_RELATIONS = ["Intersecting the extent or boundary",
                    "Entirely within the extent or boundary"]


def canon_relation(s):
    if s in (REL_INTERSECT, REL_WITHIN):
        return s
    t = str(s or "").strip().lower()
    if not t or t.startswith("intersect"):
        return REL_INTERSECT
    if t.startswith("entirely") or t.startswith("within"):
        return REL_WITHIN
    return None


def auto_sample_size(N, confidence, margin):
    """Esri's Auto Calculate: z from the two-sided confidence level,
    n = (z/m)^2 * p(1-p) with p = 0.5, finite-population-corrected
    n' = n*N/(n + (N-1)), rounded up and capped at N."""
    from scipy.stats import norm

    c = float(confidence) / 100.0
    m = float(margin) / 100.0
    if not 0 < c < 1:
        raise ValueError("Confidence level must be between 0 and 100.")
    if not 0 < m < 1:
        raise ValueError("Margin of error must be between 0 and 100.")
    z = float(norm.ppf(1.0 - (1.0 - c) / 2.0))
    n = (z / m) ** 2 * 0.25
    if N <= 0:
        return 0
    n_fpc = n * N / (n + (N - 1))
    return min(int(N), int(math.ceil(n_fpc - 1e-9)))


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


# ---------------------------------------------------------------------------
#  population helpers
# ---------------------------------------------------------------------------
def _current_selection_oids(in_layer):
    """The layer/table view's currently selected OIDs (a set), or
    None when there is no selection."""
    import arcpy

    try:
        fid = arcpy.Describe(in_layer).FIDSet
    except Exception:                                            # noqa: BLE001
        return None
    if fid in (None, ""):
        return None
    return {int(v) for v in str(fid).replace(",", ";").split(";")
            if str(v).strip()}


def _qualify_oids(path, extent, boundary, bnd_sel, relation, out_sr,
                  messages):
    """OIDs of features INTERSECTING / ENTIRELY WITHIN the analysis
    extent and/or the polygon analysis boundary (both filters apply
    when both are given). Runs Select Layer By Location on a scratch
    layer, so the user's own selection is never disturbed. A
    selection on the boundary LAYER is honored via ``bnd_sel`` (its
    OIDs, captured up front -- a live layer object's selection can be
    dropped by unrelated arcpy calls, observed Pro 3.7); for the
    entirely-within test the boundary is dissolved first, so a
    feature straddling two ADJACENT boundary polygons still counts
    as inside their union."""
    import arcpy

    rel = "INTERSECT" if relation == REL_INTERSECT else "WITHIN"
    selectors = []                       # (dataset, cleanup list)
    scratch = []
    if extent is not None:
        esr = getattr(extent, "spatialReference", None)
        sr = esr if esr is not None \
            and getattr(esr, "name", "Unknown") not in (None, "",
                                                        "Unknown") \
            else out_sr
        rect = arcpy.Polygon(arcpy.Array([
            arcpy.Point(extent.XMin, extent.YMin),
            arcpy.Point(extent.XMin, extent.YMax),
            arcpy.Point(extent.XMax, extent.YMax),
            arcpy.Point(extent.XMax, extent.YMin),
            arcpy.Point(extent.XMin, extent.YMin)]), sr)
        rect_fc = arcpy.management.CreateFeatureclass(
            "in_memory", "sel_rect", "POLYGON",
            spatial_reference=_mh.clean_sr(sr)).getOutput(0)
        with arcpy.da.InsertCursor(rect_fc, ["SHAPE@"]) as cur:
            cur.insertRow([rect])
        selectors.append(("the analysis extent", rect_fc))
        scratch.append(rect_fc)
    if boundary not in (None, ""):
        bdesc = arcpy.Describe(boundary)
        if getattr(bdesc, "shapeType", "") != "Polygon":
            raise arcpy.ExecuteError(
                "The polygon analysis boundary must be a POLYGON "
                "feature class (this input is {}).".format(
                    getattr(bdesc, "shapeType", "") or "a table"))
        bpath = getattr(bdesc, "catalogPath", str(boundary))
        src = bpath
        if bnd_sel:
            # rebuild the boundary's selection as an explicit
            # OID-list scratch layer
            boid = arcpy.AddFieldDelimiters(bpath,
                                            bdesc.oidFieldName)
            bsel_lyr = "selrand_bnd_lyr"
            arcpy.management.MakeFeatureLayer(
                bpath, bsel_lyr, "{} IN ({})".format(
                    boid, ",".join(str(v) for v in sorted(bnd_sel))))
            scratch.append(bsel_lyr)
            src = bsel_lyr
            try:
                n_total = int(arcpy.management.GetCount(bpath)[0])
            except Exception:                            # noqa: BLE001
                n_total = 0
            _say(messages,
                 "The boundary layer has an active selection: {} of "
                 "{} polygons form the boundary.".format(
                     len(bnd_sel), n_total))
        if rel == "WITHIN":
            # union the boundary so straddling ADJACENT polygons
            # still counts as entirely within
            dis = arcpy.management.Dissolve(
                src, os.path.join("in_memory",
                                  "sel_bnd_dis")).getOutput(0)
            src = dis
            scratch.append(dis)
        selectors.append(("the polygon boundary", src))
    if not selectors:
        return None
    tmp = "selrand_qualify_lyr"
    arcpy.management.MakeFeatureLayer(path, tmp)
    try:
        first = True
        for _label, sel_fc in selectors:
            arcpy.management.SelectLayerByLocation(
                tmp, rel, sel_fc,
                selection_type="NEW_SELECTION" if first
                else "SUBSET_SELECTION")
            first = False
        oids = {row[0] for row in arcpy.da.SearchCursor(tmp, ["OID@"])}
    finally:
        arcpy.management.Delete(tmp)
        for f in scratch:
            try:
                arcpy.management.Delete(f)
            except Exception:                                # noqa: BLE001
                pass
    _say(messages, "{:,} record{} {} {}.".format(
        len(oids), "" if len(oids) == 1 else "s",
        "intersect" if relation == REL_INTERSECT
        else "lie entirely within",
        " and ".join(lbl for lbl, _f in selectors)))
    return oids


# ---------------------------------------------------------------------------
#  main entry
# ---------------------------------------------------------------------------
def run_select_random(in_layer, sample_method=None, n_records=None,
                      percent=None, confidence=95.0, margin=5.0,
                      interval=None, sort_field=None,
                      random_start=True, selection_method=None,
                      extent=None, boundary=None,
                      extent_relation=None, random_seed=None,
                      messages=None):
    """Compute the sample and apply it as a selection on
    ``in_layer``. Returns (n_selected_now, n_sampled)."""
    import arcpy

    method = canon_sample(sample_method)
    if method is None:
        raise arcpy.ExecuteError(
            "Unknown sampling method: {}".format(sample_method))
    sel = canon_selection(selection_method)
    if sel is None:
        raise arcpy.ExecuteError(
            "Unknown selection method: {}".format(selection_method))
    relation = canon_relation(extent_relation)
    if relation is None:
        raise arcpy.ExecuteError(
            "Unknown extent relationship: {}".format(extent_relation))

    # capture the boundary layer's selection FIRST: a live layer
    # object's selection can be dropped by unrelated arcpy calls
    # (observed Pro 3.7), so the OIDs are pinned before anything else
    bnd_sel = None
    if boundary not in (None, ""):
        bnd_sel = _current_selection_oids(boundary)

    desc = arcpy.Describe(in_layer)
    if getattr(desc, "dataType", "") not in ("FeatureLayer",
                                             "TableView", "Layer"):
        raise arcpy.ExecuteError(
            "The input must be a LAYER or TABLE VIEW (e.g. from the "
            "map) -- a selection on a bare dataset path would not be "
            "visible anywhere. Add the data to the map first.")
    try:
        path = desc.catalogPath
    except Exception:                                            # noqa: BLE001
        path = str(in_layer)
    is_features = getattr(desc, "shapeType", "") not in ("", None)
    shape_type = getattr(desc, "shapeType", "")
    oid_field = desc.oidFieldName
    rng = np.random.default_rng(
        int(random_seed) if random_seed not in (None, "") else None)

    _mh.begin_phases([("Reading the population...", 2),
                      ("Drawing the sample...", 1),
                      ("Applying the selection...", 2)])

    if method == METHOD_PPS:
        if not is_features or shape_type not in ("Polygon",
                                                 "Polyline"):
            raise arcpy.ExecuteError(
                "Probability proportional to area / length needs a "
                "POLYGON or POLYLINE layer (this input is {}).".format(
                    shape_type or "a table"))

    # ---- the population --------------------------------------------------
    cur_sel = _current_selection_oids(in_layer)
    pop_exclude = None
    if sel in (SEL_REMOVE, SEL_SUBSET):
        if not cur_sel:
            raise arcpy.ExecuteError(
                "'{}' needs a current selection on the input, and "
                "there is none.".format(SELECTION_METHODS[
                    [SEL_NEW, SEL_ADD, SEL_REMOVE,
                     SEL_SUBSET].index(sel)]))
        pop_filter = set(cur_sel)
        _say(messages,
             "Population: the {:,} currently selected record{} (the "
             "'{}' method considers only the current "
             "selection).".format(
                 len(cur_sel), "" if len(cur_sel) == 1 else "s",
                 str(selection_method or sel)))
    elif sel == SEL_ADD and cur_sel:
        # only records NOT already selected are eligible -- every
        # sampling method (counts, percentages, Auto Calculate's N,
        # area/length totals, systematic ordering) runs over the
        # unselected records alone
        pop_exclude = set(cur_sel)
        pop_filter = None
        _say(messages,
             "Population: the currently UNSELECTED records (the "
             "'Add to the current selection' method draws only from "
             "records not already selected; {:,} record{} are "
             "already in the selection).".format(
                 len(cur_sel), "" if len(cur_sel) == 1 else "s"))
    else:
        pop_filter = None

    if is_features and (extent not in (None, "")
                        or boundary not in (None, "")):
        e = None
        if extent not in (None, ""):
            e = extent if isinstance(extent, arcpy.Extent) else None
            if e is None:
                parts = [float(v) for v in str(extent).split()[:4]]
                e = arcpy.Extent(parts[0], parts[1], parts[2],
                                 parts[3])
        qual = _qualify_oids(path, e, boundary, bnd_sel, relation,
                             desc.spatialReference, messages)
        if qual is not None:
            pop_filter = qual if pop_filter is None \
                else (pop_filter & qual)
    # a spatial restriction on a standalone table is silently ignored
    # (the dialog hides those options for tables)

    # read the population rows (honoring the filter), plus whatever
    # the method needs (weights, sort keys)
    want_weight = method == METHOD_PPS
    systematic = method in (METHOD_EVERY_X, METHOD_ODD, METHOD_EVEN)
    sfield = str(sort_field) if sort_field not in (None, "") \
        else oid_field
    cols = ["OID@"]
    if want_weight:
        cols.append("SHAPE@")
    if systematic and sfield.upper() != oid_field.upper():
        cols.append(sfield)
    oids, weights, keys = [], [], []
    # the layer's definition query still scopes the population; the
    # cursor runs on the PATH so the current selection does NOT
    # (New/Add must see all rows)
    dq = getattr(desc, "definitionQuery", "") or None
    with arcpy.da.SearchCursor(path, cols, where_clause=dq) as cur:
        for row in cur:
            oid = int(row[0])
            if pop_filter is not None and oid not in pop_filter:
                continue
            if pop_exclude is not None and oid in pop_exclude:
                continue
            oids.append(oid)
            if want_weight:
                g = row[1]
                if g is None:
                    weights.append(0.0)
                elif shape_type == "Polygon":
                    try:
                        weights.append(abs(g.getArea(
                            "GEODESIC", "SQUAREMETERS")))
                    except Exception:                    # noqa: BLE001
                        weights.append(abs(g.area))
                else:
                    try:
                        weights.append(abs(g.getLength(
                            "GEODESIC", "METERS")))
                    except Exception:                    # noqa: BLE001
                        weights.append(abs(g.length))
            if systematic and len(cols) > 1 and not want_weight:
                keys.append(row[1])
    N = len(oids)
    if N == 0:
        raise arcpy.ExecuteError(
            "The population contains no records (empty input, or "
            "nothing passes the extent/selection filters).")
    _say(messages, "Population: {:,} record{}.".format(
        N, "" if N == 1 else "s"))
    _mh.phase_advance()

    # ---- how many --------------------------------------------------------
    if method == METHOD_FIXED or method == METHOD_PPS:
        n = int(n_records or 0)
        if n <= 0:
            raise arcpy.ExecuteError(
                "Give a number of records greater than zero.")
        if n > N:
            _warn(messages,
                  "{:,} records requested but the population has only "
                  "{:,} -- selecting all of them.".format(n, N))
            n = N
    elif method == METHOD_PERCENT:
        p = float(percent or 0)
        if not 0 < p <= 100:
            raise arcpy.ExecuteError(
                "Give a percentage between 0 and 100.")
        n = min(N, max(1, int(round(p / 100.0 * N))))
        _say(messages, "{:g}% of {:,} -> {:,} records.".format(p, N,
                                                               n))
    elif method == METHOD_AUTO:
        try:
            n = auto_sample_size(N, confidence, margin)
        except ValueError as e:
            raise arcpy.ExecuteError(str(e))
        _say(messages,
             "Auto Calculate: {:g}% confidence with a {:g}% margin of "
             "error over {:,} records -> {:,} samples (worst-case "
             "p = 0.5, finite-population corrected).".format(
                 float(confidence), float(margin), N, n))
    else:
        n = None                                     # systematic

    # ---- the draw --------------------------------------------------------
    oid_arr = np.asarray(oids, dtype=np.int64)
    if method in (METHOD_FIXED, METHOD_PERCENT, METHOD_AUTO):
        take = rng.choice(N, size=n, replace=False)
        sampled = oid_arr[take]
    elif method == METHOD_PPS:
        w = np.asarray(weights, dtype=np.float64)
        w[~np.isfinite(w)] = 0.0
        if w.sum() <= 0:
            raise arcpy.ExecuteError(
                "Every feature in the population has zero area / "
                "length -- nothing to weight by.")
        w = np.where(w > 0, w, 0.0)
        # Efraimidis-Spirakis keys: top-n of U^(1/w) is an exact
        # weighted sample without replacement
        with np.errstate(divide="ignore"):
            key = np.where(w > 0,
                           np.power(rng.random(N),
                                    np.where(w > 0, 1.0 / w, np.inf)),
                           -1.0)
        sampled = oid_arr[np.argsort(-key)[:n]]
        tot = w.sum()
        _say(messages,
             "Selection probability proportional to geodesic {} "
             "(population total {:,.1f}).".format(
                 "area" if shape_type == "Polygon" else "length",
                 tot))
    else:
        # systematic: walk the sort-field order
        if sfield.upper() == oid_field.upper():
            order = np.argsort(oid_arr, kind="stable")
        else:
            key_arr = np.asarray(keys, dtype=object)
            try:
                key_arr = key_arr.astype(np.float64)
            except (TypeError, ValueError):
                key_arr = np.asarray([("" if k is None else str(k))
                                      for k in keys])
            order = np.argsort(key_arr, kind="stable")
        walk = oid_arr[order]
        if method == METHOD_EVERY_X:
            x = int(interval or 0)
            if x <= 0:
                raise arcpy.ExecuteError(
                    "Give an interval (X) greater than zero.")
            start = int(rng.integers(0, min(x, N))) \
                if random_start else 0
            sampled = walk[start::x]
            _say(messages,
                 "Every {:,}th record in {} order, starting at "
                 "record {:,}{}.".format(
                     x, sfield, start + 1,
                     " (random start within the first {:,})".format(
                         min(x, N)) if random_start else ""))
        elif method == METHOD_ODD:
            sampled = walk[0::2]
            _say(messages, "Odd-numbered records (1st, 3rd, ...) in "
                           "{} order.".format(sfield))
        else:
            sampled = walk[1::2]
            _say(messages, "Even-numbered records (2nd, 4th, ...) in "
                           "{} order.".format(sfield))
    _mh.phase_advance()

    # ---- apply as a selection --------------------------------------------
    sampled = [int(v) for v in sampled]
    delim_oid = arcpy.AddFieldDelimiters(path, oid_field)
    if not sampled:
        if sel == SEL_NEW:
            arcpy.management.SelectLayerByAttribute(
                in_layer, "CLEAR_SELECTION")
        _warn(messages, "The sample is empty -- {}.".format(
            "the selection was cleared" if sel == SEL_NEW
            else "the current selection is unchanged"))
    else:
        # SUBSET's sample is drawn FROM the current selection, so a
        # new selection of the sample is the identical result --
        # chunk-safe, unlike repeated SUBSET_SELECTION calls
        gp_method = {SEL_NEW: "NEW_SELECTION",
                     SEL_ADD: "ADD_TO_SELECTION",
                     SEL_REMOVE: "REMOVE_FROM_SELECTION",
                     SEL_SUBSET: "NEW_SELECTION"}[sel]
        chunk = 5000
        for k0 in range(0, len(sampled), chunk):
            part = sampled[k0:k0 + chunk]
            where = "{} IN ({})".format(
                delim_oid, ",".join(str(v) for v in part))
            arcpy.management.SelectLayerByAttribute(
                in_layer, gp_method, where)
            if gp_method == "NEW_SELECTION":
                gp_method = "ADD_TO_SELECTION"
            _mh.phase_step(k0 + len(part), len(sampled))

    now = _current_selection_oids(in_layer)
    n_now = len(now) if now else 0
    _say(messages,
         "{:,} record{} sampled; the input now has {:,} record{} "
         "selected.".format(len(sampled),
                            "" if len(sampled) == 1 else "s",
                            n_now, "" if n_now == 1 else "s"))
    if random_seed not in (None, ""):
        _say(messages, "Seed {}: this draw is exactly "
                       "reproducible.".format(random_seed))
    _mh.end_phases()
    return n_now, len(sampled)
