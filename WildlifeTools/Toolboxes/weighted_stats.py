# -*- coding: utf-8 -*-
"""Weighted Summary Statistics -- record statistics AND geometry-weighted
statistics of numeric attribute fields on polyline or polygon features.

The geometry-weighted statistics (weighted by polyline length or polygon
area) are the ecologically correct way to summarize per-unit-landscape
attributes: an unweighted mean treats every polygon as one equal vote, so
a 1-hectare sliver counts exactly as much as a 5,000-hectare block, and
the result changes whenever the landscape is subdivided differently.  The
area-weighted mean is the value of an average hectare -- the expected
value experienced by an animal (or a random point) placed on the
landscape -- and it is invariant to how the polygons happen to be drawn.
Both families are reported side by side so the difference itself is
visible (a large gap means the attribute is correlated with feature size,
which is diagnostic in its own right).

Port of the numeric-statistics side of the CorridorDesigner Evaluation
Tools' "Summary Statistics" (Corridor Summarize) tool: the Mean_WBL /
Mean_WBA weighted-by-length / weighted-by-area statistics family.  The
categorical side of that tool lives in the Attribute Summary Reports
window (per-class counts, proportions, lengths and areas).

Weights are GEODESIC lengths (meters) and areas (square meters), so the
statistics are correct in any coordinate system, projected or geographic.

No Spatial Analyst required (no raster involvement at all).
"""

import os
import time

import numpy as np

import mahalanobis as _mh


NULL_CASE = "<Null>"


# ---------------------------------------------------------------------------
# pure math (no arcpy) -- unit-tested directly
# ---------------------------------------------------------------------------

def weighted_summary(values, weights):
    """All statistics of ``values`` (1-D float array) with ``weights``.

    Returns a dict with the unweighted family computed over ALL values
    (n, min, max, range, sum, mean, sd, var, median -- sd/var are the
    n-1 sample forms, None when n < 2) and the weighted family computed
    over the values whose weight is > 0 (w_total, w_mean, w_sd, w_var,
    w_median).  The weighted variance uses the reliability-weights
    analogue of the n-1 correction,

        var_w = sum(w*(x-mw)**2) / (W - sum(w**2)/W),   W = sum(w)

    which reduces exactly to the ordinary n-1 sample variance when all
    weights are equal.  The weighted median is the value at which the
    cumulative weight first reaches half the total (averaging across the
    boundary when it lands exactly on it, which likewise reduces to the
    ordinary median under equal weights).
    """
    v = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    out = {
        "n": int(v.size),
        "min": None, "max": None, "range": None, "sum": None,
        "mean": None, "sd": None, "var": None, "median": None,
        "w_total": float(w[w > 0].sum()) if w.size else 0.0,
        "w_mean": None, "w_sd": None, "w_var": None, "w_median": None,
    }
    if v.size == 0:
        return out

    out["min"] = float(v.min())
    out["max"] = float(v.max())
    out["range"] = out["max"] - out["min"]
    out["sum"] = float(v.sum())
    out["mean"] = float(v.mean())
    out["median"] = float(np.median(v))
    if v.size >= 2:
        out["var"] = float(v.var(ddof=1))
        out["sd"] = float(np.sqrt(out["var"]))

    mask = w > 0
    if not mask.any():
        return out
    vw = v[mask]
    ww = w[mask]
    W = float(ww.sum())
    wmean = float((ww * vw).sum() / W)
    out["w_mean"] = wmean
    denom = W - float((ww * ww).sum()) / W
    if denom > 0:
        out["w_var"] = float((ww * (vw - wmean) ** 2).sum() / denom)
        out["w_sd"] = float(np.sqrt(out["w_var"]))
    out["w_median"] = _weighted_median(vw, ww, W)
    return out


def _weighted_median(v, w, W):
    order = np.argsort(v, kind="stable")
    vs = v[order]
    cum = np.cumsum(w[order])
    half = W / 2.0
    k = int(np.searchsorted(cum, half))
    if k >= vs.size:
        k = vs.size - 1
    # cumulative weight lands EXACTLY on half: average across the boundary
    if abs(cum[k] - half) <= 1e-9 * W and k + 1 < vs.size:
        return float((vs[k] + vs[k + 1]) / 2.0)
    return float(vs[k])


# ---------------------------------------------------------------------------
# arcpy engine
# ---------------------------------------------------------------------------

def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        try:
            messages.addWarningMessage(text)
            return
        except Exception:
            pass
    _say(messages, "WARNING: " + text)


def _shape_weight(shp, is_polygon, geodesic, fallbacks=None):
    """Geodesic meters / square meters (planar map units as fallback).

    In geodesic mode the planar fallback is UNIT-MIXING (square DEGREES
    among square meters on a geographic input -- effectively zero weight),
    so each fall-through is counted in ``fallbacks`` (a one-element list)
    and reported once at the end instead of passing silently."""
    if shp is None:
        return 0.0
    try:
        if geodesic:
            if is_polygon:
                return abs(shp.getArea("GEODESIC", "SQUAREMETERS"))
            return abs(shp.getLength("GEODESIC", "METERS"))
    except Exception:
        if fallbacks is not None:
            fallbacks[0] += 1
    try:
        return abs(shp.area) if is_polygon else abs(shp.length)
    except Exception:
        return 0.0


def _case_display(v):
    import datetime
    if v is None:
        return NULL_CASE
    if isinstance(v, datetime.datetime):
        return (v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second)
                == (0, 0, 0) else v.strftime("%Y-%m-%d %H:%M:%S"))
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def run_weighted_stats(in_features, fields, out_table, case_field=None,
                       messages=None):
    """Compute the table.  ``in_features`` may be a layer (its selection
    is honored) or a dataset path; ``fields`` is the ordered list of
    numeric field names; ``case_field`` optionally groups the rows."""
    import arcpy

    fields = [f for f in (fields or []) if f]
    if not fields:
        raise ValueError("Pick at least one numeric field to summarize.")

    desc = arcpy.Describe(in_features)
    shape_type = str(getattr(desc, "shapeType", ""))
    if shape_type not in ("Polyline", "Polygon"):
        raise ValueError(
            "The input must be a POLYLINE or POLYGON feature class -- the "
            "weighted statistics weight each record by its length or area, "
            "which a {} feature does not have. For plain record statistics "
            "on any table, use Esri's Summary Statistics tool."
            .format(shape_type or "non-feature"))
    is_polygon = shape_type == "Polygon"

    sr = getattr(desc, "spatialReference", None)
    geodesic = bool(sr is not None and getattr(sr, "name", "Unknown")
                    != "Unknown")
    if geodesic:
        basis = ("geodesic area in square meters" if is_polygon
                 else "geodesic length in meters")
    else:
        basis = ("planar area in squared map units" if is_polygon
                 else "planar length in map units")
        _warn(messages,
              "The input has an unknown coordinate system; weights fall "
              "back to PLANAR measures in map units.")
    _say(messages, "Weight basis: {}.".format(basis))

    try:
        total_feat = int(arcpy.management.GetCount(in_features)[0])
    except Exception:
        total_feat = 0
    if total_feat:
        _say(messages, "Analyzing {:,} feature(s){}."
             .format(total_feat,
                     " (the layer's current selection)"
                     if getattr(desc, "FIDSet", "") else ""))

    _mh.begin_phases([("Reading features", 3.0),
                      ("Computing statistics and writing the table", 1.0)])
    _t0 = time.perf_counter()

    # ---- one pass over the features -----------------------------------
    # groups: case display -> field name -> ([values], [weights])
    groups = {}
    nulls = {}                      # case -> field -> null count
    case_order = []                 # first-appearance order (sorted later)
    cursor_fields = ["SHAPE@"] + fields + ([case_field] if case_field else [])
    done = 0
    geo_fallbacks = [0]
    with arcpy.da.SearchCursor(in_features, cursor_fields) as cur:
        for row in cur:
            done += 1
            if total_feat and done % 500 == 0:
                _mh.phase_step(done, total_feat)
            wt = _shape_weight(row[0], is_polygon, geodesic, geo_fallbacks)
            ck = _case_display(row[-1]) if case_field else None
            if ck not in groups:
                groups[ck] = {f: ([], []) for f in fields}
                nulls[ck] = {f: 0 for f in fields}
                case_order.append(ck)
            g, nl = groups[ck], nulls[ck]
            for i, f in enumerate(fields):
                v = row[1 + i]
                if v is None:
                    nl[f] += 1
                    continue
                vals, wts = g[f]
                vals.append(float(v))
                wts.append(wt)

    _say(messages, "Read {:,} feature(s) and measured their geometry "
         "in {:.1f} s.".format(done, time.perf_counter() - _t0))
    if geo_fallbacks[0]:
        _warn(messages,
              "{:,} feature(s) could not be measured geodesically "
              "(corrupt or degenerate geometry?) and fell back to a "
              "PLANAR measure in map units -- on a geographic input that "
              "means effectively zero weight among square-meter weights. "
              "Check those features (Repair Geometry) before trusting "
              "the weighted family.".format(geo_fallbacks[0]))
    _mh.phase_advance()
    _t0 = time.perf_counter()

    # ---- output table -------------------------------------------------
    out_table = str(out_table).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_table)
    if not _mh.in_gdb_container(out_table) and \
            not name.lower().endswith(".dbf"):
        name += ".dbf"                       # folder tables need .dbf
        out_table = os.path.join(ws, name)
    arcpy.management.CreateTable(ws, name)
    _say(messages, "Created the output table in {:.1f} s."
         .format(time.perf_counter() - _t0))
    _t0 = time.perf_counter()

    w_word = "Area" if is_polygon else "Length"
    w_unit = ("m2" if is_polygon else "m") if geodesic else "map units"
    dbl = [
        ("MIN", "Minimum"),
        ("MAX", "Maximum"),
        ("RANGE", "Range"),
        ("SUM", "Sum"),
        ("MEAN", "Mean (each record one equal vote)"),
        ("STD_DEV", "Standard Deviation (sample, n-1)"),
        ("VARIANCE", "Variance (sample, n-1)"),
        ("MEDIAN", "Median"),
        ("W_TOTAL", "Total {} of Records Used ({})".format(
            w_word.lower(), w_unit)),
        ("W_MEAN", "{}-Weighted Mean (per-unit-landscape)".format(w_word)),
        ("W_STD_DEV", "{}-Weighted Standard Deviation".format(w_word)),
        ("W_VARIANCE", "{}-Weighted Variance".format(w_word)),
        ("W_MEDIAN", "{}-Weighted Median".format(w_word)),
    ]
    # ONE AddFields call for the whole schema: each individual AddField is
    # a full GP-tool launch that locks and rewrites the geodatabase
    # catalog (seconds apiece in a crowded gdb) -- 17 of them was the
    # long "Computing statistics" pause Jeff hit
    fdesc = []
    if case_field:
        fdesc.append(["CASE_VAL", "TEXT",
                      "Case Value [{}]".format(case_field), 254, "", ""])
    fdesc.append(["FIELD_NAME", "TEXT", "Attribute Field", 64, "", ""])
    fdesc.append(["N", "LONG", "Count of Records (non-null)", "", "", ""])
    fdesc.append(["N_NULL", "LONG", "Count of Null Values", "", "", ""])
    for fname, alias in dbl:
        fdesc.append([fname, "DOUBLE", alias, "", "", ""])
    arcpy.management.AddFields(out_table, fdesc)
    _say(messages, "Added the {} field(s) in {:.1f} s."
         .format(len(fdesc), time.perf_counter() - _t0))
    _t0 = time.perf_counter()

    def _case_sort_key(ck):
        try:
            return (0, float(ck), "")
        except (TypeError, ValueError):
            return (1, 0.0, ck if ck is not None else "")

    ordered_cases = (sorted(case_order, key=_case_sort_key)
                     if case_field else case_order)

    ins_fields = ((["CASE_VAL"] if case_field else [])
                  + ["FIELD_NAME", "N", "N_NULL"]
                  + [f for f, _ in dbl])
    rows_written = 0
    total_rows = max(len(ordered_cases) * len(fields), 1)
    with arcpy.da.InsertCursor(out_table, ins_fields) as ins:
        for ck in ordered_cases:
            for f in fields:
                vals, wts = groups[ck][f]
                s = weighted_summary(vals, wts)
                row = ([ck] if case_field else []) + [
                    f, s["n"], nulls[ck][f],
                    s["min"], s["max"], s["range"], s["sum"], s["mean"],
                    s["sd"], s["var"], s["median"],
                    s["w_total"], s["w_mean"], s["w_sd"], s["w_var"],
                    s["w_median"]]
                ins.insertRow(row)
                rows_written += 1
                _mh.phase_step(rows_written, total_rows)

    _mh.end_phases()
    _say(messages, "Wrote {:,} statistics row(s) to {} in {:.1f} s.".format(
        rows_written, out_table, time.perf_counter() - _t0))
    _t0 = time.perf_counter()
    if is_polygon:
        _say(messages,
             "MEAN treats each polygon as one equal vote; W_MEAN weights "
             "each polygon by its area, giving the value of an average "
             "unit of land -- the per-unit-landscape statistic. A large "
             "difference between them means the attribute is correlated "
             "with polygon size.")
    else:
        _say(messages,
             "MEAN treats each polyline as one equal vote; W_MEAN weights "
             "each polyline by its length, giving the per-unit-length "
             "statistic. A large difference between them means the "
             "attribute is correlated with feature length.")

    # ---- provenance metadata (standing rule) --------------------------
    try:
        md = arcpy.metadata.Metadata(out_table)
        md.title = "Weighted Summary Statistics"
        md.summary = (
            "Record and {}-weighted statistics of field(s) {} from '{}'"
            "{}. Weights: {}."
            .format(w_word.lower(), ", ".join(fields),
                    os.path.basename(str(getattr(desc, "catalogPath",
                                                 in_features))),
                    ", grouped by '{}'".format(case_field)
                    if case_field else "", basis))
        md.description = (
            "Each attribute field is summarized twice: once treating "
            "every record as one equal vote (MEAN, STD_DEV, VARIANCE, "
            "MEDIAN) and once weighting every record by its geodesic {} "
            "(W_MEAN, W_STD_DEV, W_VARIANCE, W_MEDIAN). For attributes "
            "that describe a per-unit-landscape property (habitat "
            "suitability, canopy cover, resistance), the weighted "
            "statistics are the ecologically correct description of the "
            "landscape: they report the value of an average unit of land "
            "rather than an average mapping unit, and they do not change "
            "when the same landscape is subdivided into different "
            "polygons. Produced by the Weighted Summary Statistics tool "
            "(Jenness Enterprises Wildlife Tools), a port of the "
            "CorridorDesigner Evaluation Tools' Summary Statistics "
            "weighted-statistics family."
            .format("area" if is_polygon else "length"))
        md.save()
        _say(messages, "Stamped provenance metadata in {:.1f} s."
             .format(time.perf_counter() - _t0))
    except Exception:
        pass

    return out_table
