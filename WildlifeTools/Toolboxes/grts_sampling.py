# -*- coding: utf-8 -*-
"""Spatially Balanced Sample (GRTS) -- the arcpy-facing wrapper
around grts_core (Stevens & Olsen 2004; spsurvey, Dumelle et al.
2023). Reads point / polyline / polygon frames (spsurvey's three
resource types exactly), runs the core selection per stratum, and
writes the design sites in reverse hierarchical order with design
weights, so any leading run of the SiteID order is itself a
spatially balanced sample and replacements are consumed from the
tail in order."""

import math
import os

import numpy as np

import grts_core as _gc
import mahalanobis as _mh
import patch_analysis as _pa
import random_points as _rp
import repeating_shapes as _rs
import voronoi_tools as _vt

# allocation of the total across strata (canonical by prefix,
# house convention)
ALLOC_PROP = ("Proportional to stratum size "
              "(area, length, or unit count)")
ALLOC_EQUAL = "Equal count in each stratum"
ALLOC_METHODS = [ALLOC_PROP, ALLOC_EQUAL]

_LIN_M = {"Meters": 1.0, "Kilometers": 1000.0,
          "Feet": 0.3048, "Miles": 1609.344}


def canon_alloc(text):
    if text in (None, ""):
        return ALLOC_PROP
    t = str(text).lower()
    for m in ALLOC_METHODS:
        if t.startswith(m.lower()[:12]):
            return m
    return None


def _say(messages, txt):
    if messages is not None:
        messages.addMessage(txt)
    else:
        print(txt)


def _warn(messages, txt):
    if messages is not None:
        messages.addWarningMessage(txt)
    else:
        print("WARNING:", txt)


# ---------------------------------------------------------------------------
# frame readers (everything in the WORK coordinate system)
# ---------------------------------------------------------------------------

def _densified(geom, work_sr):
    """Cursor-projected geometry, densified when it carries true
    curves (standing rule: curves collapse to arc endpoints in JSON
    ring/path parsers; densify AFTER projecting, in the geometry's
    own units)."""
    if getattr(geom, "hasCurves", False):
        try:
            step = max(geom.length / 500.0, 1e-6)
            geom = geom.densify("DISTANCE", step, step)
        except Exception:                                    # noqa: BLE001
            pass
    return geom


def _read_polygons(frame, work_sr, stratum_field):
    """Per-stratum ring lists, flipped to the core's orientation
    (outer counterclockwise, holes clockwise -- rings_of preserves
    Esri's convention, which is the reverse) + arcpy's own planar
    area per stratum as a cross-check."""
    import arcpy

    fields = ["SHAPE@"] + ([stratum_field] if stratum_field else [])
    strata = {}
    areas = {}
    with arcpy.da.SearchCursor(frame, fields,
                               spatial_reference=work_sr) as cur:
        for row in cur:
            geom = row[0]
            if geom is None:
                continue
            key = row[1] if stratum_field else None
            geom = _densified(geom, work_sr)
            rings = [r[::-1] for r in _pa.rings_of(geom)]
            if not rings:
                continue
            strata.setdefault(key, []).extend(rings)
            areas[key] = areas.get(key, 0.0) + float(geom.area)
    return strata, areas


def _read_lines(frame, work_sr, stratum_field):
    """Per-stratum (S, 4) segment arrays."""
    import arcpy
    import json

    fields = ["SHAPE@"] + ([stratum_field] if stratum_field else [])
    strata = {}
    with arcpy.da.SearchCursor(frame, fields,
                               spatial_reference=work_sr) as cur:
        for row in cur:
            geom = row[0]
            if geom is None:
                continue
            key = row[1] if stratum_field else None
            geom = _densified(geom, work_sr)
            for path in json.loads(geom.JSON).get("paths", []):
                a = np.asarray(path, dtype=np.float64)[:, :2]
                if len(a) < 2:
                    continue
                segs = np.hstack([a[:-1], a[1:]])
                strata.setdefault(key, []).append(segs)
    return {k: np.vstack(v) for k, v in strata.items() if v}


def _read_points(frame, work_sr, stratum_field, aux_field):
    """(xy, oid, stratum, weight) arrays; multipoints exploded."""
    import arcpy

    fields = ["OID@", "SHAPE@"]
    if stratum_field:
        fields.append(stratum_field)
    if aux_field:
        fields.append(aux_field)
    xy, oid, strat, w = [], [], [], []
    with arcpy.da.SearchCursor(frame, fields,
                               spatial_reference=work_sr) as cur:
        for row in cur:
            geom = row[1]
            if geom is None:
                continue
            key = row[2] if stratum_field else None
            wt = row[2 + bool(stratum_field)] if aux_field else 1.0
            if wt is None or not np.isfinite(float(wt)) \
                    or float(wt) < 0:
                raise Exception(
                    "The auxiliary (PPS) field holds a negative or "
                    "missing value on OID {} -- weights must be "
                    "non-negative numbers.".format(row[0]))
            for part in geom:
                pnt_iter = part if hasattr(part, "__iter__") \
                    else [part]
                for p in pnt_iter:
                    if p is None:
                        continue
                    xy.append((p.X, p.Y))
                    oid.append(row[0])
                    strat.append(key)
                    w.append(float(wt))
    return (np.asarray(xy, dtype=np.float64),
            np.asarray(oid, dtype=np.int64),
            strat, np.asarray(w, dtype=np.float64))


# ---------------------------------------------------------------------------
# balance report helpers
# ---------------------------------------------------------------------------

def _test_points_areal(measure, rng, n_test=4000):
    """Uniform test points within the frame for the balance report,
    drawn by BATCH rejection against the vectorized point-in-polygon
    test (the one-dart-at-a-time loop ground for minutes on sliver
    frames whose area is a tiny share of their bounding box)."""
    pts = measure.bounds_points()
    lo = np.array([pts[:, 0].min(), pts[:, 1].min()])
    hi = np.array([pts[:, 0].max(), pts[:, 1].max()])
    out = []
    got = 0
    budget = n_test * 60
    while got < n_test and budget > 0:
        b = min(max(n_test, 20000), budget)
        budget -= b
        cand = rng.uniform(lo, hi, size=(b, 2))
        keep = cand[measure.points_in(cand)]
        if len(keep):
            out.append(keep)
            got += len(keep)
    if not out:
        return np.empty((0, 2))
    return np.vstack(out)[:n_test]


def _test_points_linear(segs, rng, n_test=4000):
    lens = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    csum = np.cumsum(lens)
    picks = np.searchsorted(csum, rng.uniform(0, csum[-1], n_test))
    t = rng.uniform(0, 1, n_test)
    s = segs[picks]
    return np.column_stack([s[:, 0] + t * (s[:, 2] - s[:, 0]),
                            s[:, 1] + t * (s[:, 3] - s[:, 1])])


# ---------------------------------------------------------------------------
# the tool
# ---------------------------------------------------------------------------

def run_grts_sample(in_frame, out_fc, n_base, n_over=0,
                    stratum_field=None, alloc_method=None,
                    aux_field=None, legacy_sites=None,
                    min_spacing=None, linear_units="Meters",
                    max_tries=10, random_seed=None, messages=None):
    """Select a spatially balanced (GRTS) sample from a point,
    polyline, or polygon frame and write the design sites. Returns
    (out_path, total_sites, var_v, pielou_loss) -- the last two are
    the whole-sample spatial-balance metrics of the base sites
    (None when fewer than two base sites)."""
    import arcpy

    alloc = canon_alloc(alloc_method)
    if alloc is None:
        raise arcpy.ExecuteError(
            "Unknown allocation method: {}".format(alloc_method))
    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown distance unit: {}".format(linear_units))
    n_base = int(n_base or 0)
    n_over = int(n_over or 0)
    if n_base < 1:
        raise arcpy.ExecuteError("Give a base sample size of at "
                                 "least 1.")
    if n_over < 0:
        raise arcpy.ExecuteError("Replacement sites cannot be "
                                 "negative.")

    rng = np.random.default_rng(
        int(random_seed) if random_seed not in (None, "") else None)

    desc = arcpy.Describe(in_frame)
    shp = str(getattr(desc, "shapeType", ""))
    if shp not in ("Point", "Multipoint", "Polyline", "Polygon"):
        raise arcpy.ExecuteError(
            "The sampling frame must be a POINT, POLYLINE or POLYGON "
            "feature class or layer (got {}).".format(shp or "?"))
    kind = {"Point": "finite", "Multipoint": "finite",
            "Polyline": "linear", "Polygon": "areal"}[shp]

    if aux_field and kind != "finite":
        raise arcpy.ExecuteError(
            "Unequal (PPS) inclusion probability by a field applies "
            "to POINT frames -- every point is a unit with its own "
            "weight. For polygons or lines, sample intensity is "
            "uniform in this version.")
    if legacy_sites not in (None, "") and kind != "finite":
        raise arcpy.ExecuteError(
            "Legacy sites are supported for POINT frames in this "
            "version.")

    # ---- coordinate systems ---------------------------------------------
    src_sr = desc.spatialReference
    out_sr = arcpy.env.outputCoordinateSystem or src_sr
    if out_sr is None or getattr(out_sr, "name",
                                 "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "No coordinate system: give a frame with one, or set "
            "the Output Coordinate System environment.")
    geographic = out_sr.type == "Geographic"
    if geographic:
        e = desc.extent
        try:
            if desc.spatialReference is not None \
                    and desc.spatialReference.factoryCode \
                    != out_sr.factoryCode:
                e = e.projectAs(out_sr)
        except Exception:                                # noqa: BLE001
            pass
        cx = 0.5 * (e.XMin + e.XMax)
        cy = 0.5 * (e.YMin + e.YMax)
        if not (math.isfinite(cx) and math.isfinite(cy)
                and -180 <= cx <= 180 and -90 <= cy <= 90):
            raise arcpy.ExecuteError(
                "Could not determine the frame's geographic center "
                "(the data source reports no usable extent). Copy "
                "the frame to a geodatabase and try again.")
        if (e.XMax - e.XMin) > 180:
            _warn(messages,
                  "The frame spans more than 180 degrees of "
                  "longitude -- it may straddle the antimeridian, "
                  "where the automatic equal-area projection is "
                  "unreliable. Consider a projected frame.")
        work_sr = _rs._laea_for(out_sr, cx, cy)
        _say(messages,
             "Geographic data: computing in an automatic EQUAL-AREA "
             "working projection (Lambert azimuthal); the output is "
             "written back in {}.".format(out_sr.name))
    else:
        work_sr = out_sr
    m_per_cs = float(work_sr.metersPerUnit or 1.0)
    mindis_cs = None
    if min_spacing not in (None, "", 0):
        mindis_cs = float(min_spacing) * _LIN_M[linear_units] \
            / m_per_cs

    _mh.begin_phases([("Reading the sampling frame...", 1),
                      ("Selecting the spatially balanced sample...",
                       2),
                      ("Writing the design sites...", 1)])

    # ---- read the frame per stratum -------------------------------------
    if kind == "areal":
        strata_rings, strata_area = _read_polygons(
            in_frame, work_sr, stratum_field)
        keys = [k for k in strata_rings if strata_rings[k]]
        measure_of = {}
        for k in keys:
            m = _gc.ArealMeasure(strata_rings[k])
            pts = m.bounds_points()
            span = max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]))
            core_area = m.cell_measure(pts[:, 0].min() - span,
                                       pts[:, 1].min() - span,
                                       pts[:, 0].max() + span,
                                       pts[:, 1].max() + span)
            ref = strata_area.get(k, 0.0)
            if ref > 0 and abs(core_area - ref) > 0.005 * ref:
                raise arcpy.ExecuteError(
                    "Internal ring-orientation check failed for "
                    "stratum {} ({:.6g} vs arcpy {:.6g}) -- please "
                    "report this dataset.".format(k, core_area, ref))
            measure_of[k] = m
        sizes = {k: strata_area.get(k, 0.0) for k in keys}
        # OVERLAPPING polygons are sampled at double intensity (the
        # area measure counts overlap twice; found in review) --
        # detect by comparing the dissolved footprint to the summed
        # feature areas and warn plainly (never fail the run)
        try:
            chk = arcpy.management.Dissolve(
                in_frame, r"memory\grts_overlap_chk").getOutput(0)
            diss = 0.0
            with arcpy.da.SearchCursor(chk, ["SHAPE@AREA"]) as cur:
                diss = sum(r[0] for r in cur)
            arcpy.management.Delete(chk)
            summed = sum(sizes.values())
            if diss > 0 and summed > diss * 1.005:
                _warn(messages,
                      "The frame contains OVERLAPPING polygons "
                      "(features sum to {:,.6g} but cover only "
                      "{:,.6g}): overlapping ground is sampled at "
                      "DOUBLE intensity. Dissolve the frame first "
                      "for uniform sampling.".format(summed, diss))
        except Exception:                                # noqa: BLE001
            pass
    elif kind == "linear":
        strata_segs = _read_lines(in_frame, work_sr, stratum_field)
        keys = list(strata_segs)
        measure_of = {k: _gc.LinearMeasure(strata_segs[k])
                      for k in keys}
        sizes = {k: float(np.hypot(
            strata_segs[k][:, 2] - strata_segs[k][:, 0],
            strata_segs[k][:, 3] - strata_segs[k][:, 1]).sum())
            for k in keys}
    else:
        xy_all, oid_all, strat_all, w_all = _read_points(
            in_frame, work_sr, stratum_field, aux_field)
        if not len(xy_all):
            raise arcpy.ExecuteError("The frame holds no points.")
        keys = sorted(set(strat_all), key=lambda v: str(v))
        idx_of = {k: np.asarray([i for i, s in enumerate(strat_all)
                                 if s == k], dtype=np.int64)
                  for k in keys}
        sizes = {k: (w_all[idx_of[k]].sum() if aux_field
                     else float(len(idx_of[k]))) for k in keys}
    if not keys:
        raise arcpy.ExecuteError("The frame is empty (no usable "
                                 "features{}).".format(
                                     " in any stratum"
                                     if stratum_field else ""))
    _mh.phase_advance()

    # ---- legacy sites (finite frames; read BEFORE allocation so a
    # stratum holding legacy sites can never be dropped) ------------------
    legacy_mask = None
    if legacy_sites not in (None, "") and kind == "finite":
        from scipy.spatial import cKDTree

        lxy = []
        with arcpy.da.SearchCursor(legacy_sites, ["SHAPE@XY"],
                                   spatial_reference=work_sr) as cur:
            lxy = [r[0] for r in cur]
        if lxy:
            lxy = np.asarray(lxy, dtype=np.float64)
            span = max(np.ptp(xy_all[:, 0]), np.ptp(xy_all[:, 1]),
                       1.0)
            d, near = cKDTree(xy_all).query(lxy)
            tol = max(span * 1e-6, 1e-4)
            bad = int((d > tol).sum())
            if bad:
                raise arcpy.ExecuteError(
                    "{} legacy site(s) do not coincide with any "
                    "frame point (nearest is {:.3g} CS units away). "
                    "Legacy sites must BE frame points.".format(
                        bad, float(d.max())))
            legacy_mask = np.zeros(len(xy_all), dtype=bool)
            legacy_mask[near] = True
            if aux_field and (w_all[legacy_mask] <= 0).any():
                raise arcpy.ExecuteError(
                    "A legacy site has a zero PPS weight: its design "
                    "inclusion probability would be 0 and its design "
                    "weight infinite. Give every legacy unit a "
                    "positive weight.")
    leg_of = {k: 0 for k in keys}
    if legacy_mask is not None:
        leg_of = {k: int(legacy_mask[idx_of[k]].sum()) for k in keys}
        if sum(leg_of.values()) > n_base:
            raise arcpy.ExecuteError(
                "{} legacy sites exceed the base sample size of "
                "{}.".format(sum(leg_of.values()), n_base))

    # ---- allocation across strata ---------------------------------------
    if alloc == ALLOC_EQUAL:
        n_of = {k: n_base for k in keys}
        over_of = {k: n_over for k in keys}
        short = [k for k in keys if leg_of[k] > n_base]
        if short:
            raise arcpy.ExecuteError(
                "Stratum {} holds {} legacy sites but the equal "
                "allocation gives each stratum only {} base "
                "sites.".format(short[0], leg_of[short[0]], n_base))
    else:
        weights = [max(sizes[k], 0.0) for k in keys]
        counts = _rp._apportion(n_base, weights)
        overs = _rp._apportion(n_over, weights) if n_over else \
            np.zeros(len(keys), dtype=np.int64)
        n_of = {k: int(c) for k, c in zip(keys, counts)}
        over_of = {k: int(c) for k, c in zip(keys, overs)}
        # a stratum can never be allocated fewer sites than its
        # legacy count -- raise it and take the difference from the
        # largest allocations with slack (guaranteed-in contract)
        for k in keys:
            while n_of[k] < leg_of[k]:
                donors = [j for j in keys
                          if n_of[j] > max(leg_of[j], 1)]
                if not donors:
                    raise arcpy.ExecuteError(
                        "Legacy sites cannot all be honored within "
                        "the base sample size -- increase the "
                        "number of base sites.")
                big = max(donors, key=lambda j: n_of[j])
                n_of[big] -= 1
                n_of[k] += 1
        dropped = [k for k in keys if n_of[k] < 1]
        if dropped:
            _warn(messages,
                  "{} stratum/strata received no sites under "
                  "proportional allocation (too small a share of "
                  "the total size).".format(len(dropped)))
        keys = [k for k in keys if n_of[k] >= 1]
        if not keys:
            raise arcpy.ExecuteError(
                "No stratum received any sites -- the stratum "
                "sizes (or PPS weights) are all zero.")

    # ---- select, per stratum, with min-distance retries ------------------
    def one_draw(seed_rng):
        results = {}
        for k in keys:
            n_k, o_k = n_of[k], over_of[k]
            if kind == "finite":
                idx = idx_of[k]
                lm = legacy_mask[idx] if legacy_mask is not None \
                    else None
                if n_k + o_k > len(idx):
                    raise arcpy.ExecuteError(
                        "Stratum {}: {} sites requested but the "
                        "frame holds only {} points.".format(
                            k, n_k + o_k, len(idx)))
                r = _gc.grts_finite(
                    xy_all[idx], n_k, seed_rng,
                    weights=(w_all[idx] if aux_field else None),
                    n_over=o_k, legacy=lm)
                r["xy"] = xy_all[idx][r["index"]]
                r["oid"] = oid_all[idx][r["index"]]
                r["wgt"] = 1.0 / np.maximum(r["prob"], 1e-12)
            else:
                r = _gc._grts_infinite(measure_of[k], n_k, seed_rng,
                                       n_over=o_k)
                r["oid"] = None
                per_site = sizes[k] / max(n_k, 1)
                r["wgt"] = np.full(r["M"], per_site)
            results[k] = r
        return results

    def base_violations(results):
        if mindis_cs is None:
            return 0
        from scipy.spatial import cKDTree

        v = 0
        for k, r in results.items():
            base_xy = r["xy"][np.asarray(r["use"]) != "Over"]
            if len(base_xy) < 2:
                continue
            t = cKDTree(base_xy)
            pairs = t.query_pairs(mindis_cs)
            v += len(pairs)
        return v

    best = None
    best_v = None
    tries = max(1, int(max_tries or 1)) if mindis_cs is not None \
        else 1
    for attempt in range(tries):
        results = one_draw(rng)
        v = base_violations(results)
        if best is None or v < best_v:
            best, best_v = results, v
        if best_v == 0:
            break
    results = best
    if mindis_cs is not None and best_v:
        _warn(messages,
              "The minimum distance between sites could not be fully "
              "met: {} base-site pair(s) remain closer than {:g} {} "
              "after {} tries. Spatial balance already discourages "
              "close pairs; relax the distance or reduce the sample "
              "size for a clean draw.".format(
                  best_v, float(min_spacing), linear_units.lower(),
                  tries))
    _mh.phase_advance()

    # ---- write ------------------------------------------------------------
    out_fc = str(out_fc).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_fc)
    if ws and not _mh.in_gdb_container(out_fc) \
            and not name.lower().endswith(".shp"):
        name += ".shp"
    res = arcpy.management.CreateFeatureclass(
        ws, name, "POINT", spatial_reference=_vt.clean_sr(out_sr))
    out_path = res.getOutput(0)
    is_shp = out_path.lower().endswith(".shp")
    field_rows = [["SiteID", "TEXT", "Site ID (visit / replacement "
                   "order)", 16, "", ""],
                  ["Site_Use", "TEXT", "Site use", 8, "", ""],
                  ["Design_Wgt", "DOUBLE",
                   "Design weight (1 / inclusion probability)",
                   "", "", ""]]
    if stratum_field:
        field_rows.insert(2, ["Stratum", "TEXT", "Stratum", 64,
                              "", ""])
    if kind == "finite":
        field_rows.append(["Src_FID", "LONG",
                           "Source point Object ID", "", "", ""])
    arcpy.management.AddFields(out_path, field_rows)

    total_m = sum(r["M"] for r in results.values())
    digits = max(4, len(str(total_m)))
    cols = ["SHAPE@XY", "SiteID", "Site_Use"]
    if stratum_field:
        cols.append("Stratum")
    cols.append("Design_Wgt")
    if kind == "finite":
        cols.append("Src_FID")

    site_no = 0
    with arcpy.da.InsertCursor(out_path, cols) as ins:
        for k in keys:
            r = results[k]
            xy_out = r["xy"]
            if geographic and len(xy_out):
                xy_out = _rp._project_xy(xy_out, work_sr, out_sr)
            for i in range(r["M"]):
                site_no += 1
                rec = [(float(xy_out[i, 0]), float(xy_out[i, 1])),
                       "Site-{}".format(str(site_no).zfill(digits)),
                       str(r["use"][i])]
                if stratum_field:
                    rec.append(str(k)[:64] if k is not None
                               else ("" if is_shp else None))
                rec.append(float(r["wgt"][i]))
                if kind == "finite":
                    rec.append(int(r["oid"][i]))
                ins.insertRow(rec)
    _mh.phase_advance()

    # ---- report ------------------------------------------------------------
    _say(messages, "Spatially balanced (GRTS) sample -- {} frame, "
                   "{} strat{}:".format(
                       kind, len(keys),
                       "um" if len(keys) == 1 else "a"))
    pool_xy, pool_tp, pool_tw = [], [], []
    for k in keys:
        r = results[k]
        n_leg = int((np.asarray(r["use"]) == "Legacy").sum())
        label = "  {}".format(k) if stratum_field else "  (all)"
        _say(messages,
             "{}: {} base{}{} site(s); size {:,.6g}".format(
                 label, r["n"],
                 " (incl. {} legacy)".format(n_leg) if n_leg else "",
                 " + {} replacement".format(r["n_over"])
                 if r["n_over"] else "", sizes[k]))
        # spatial balance of the base sites; pooled across strata
        # (each stratum's test points weighted by stratum size) for
        # the whole-sample metric returned to ModelBuilder
        base_xy = r["xy"][np.asarray(r["use"]) != "Over"]
        if kind == "finite":
            tp = xy_all[idx_of[k]]
            tw = w_all[idx_of[k]] if aux_field \
                else np.ones(len(tp))
        elif kind == "areal":
            tp = _test_points_areal(measure_of[k], rng)
            tw = np.full(len(tp), sizes[k] / max(len(tp), 1))
        else:
            tp = _test_points_linear(strata_segs[k], rng)
            tw = np.full(len(tp), sizes[k] / max(len(tp), 1))
        if len(base_xy):
            pool_xy.append(base_xy)
            pool_tp.append(tp)
            pool_tw.append(tw)
        if len(base_xy) >= 2:
            var_v, pei = _gc.balance_metrics(base_xy, tp,
                                             tw if aux_field
                                             else None)
            if math.isfinite(var_v):
                _say(messages,
                     "    spatial balance: Var(v) {:.3f}, Pielou "
                     "loss {:.4f} (0 = perfectly balanced; simple "
                     "random samples typically run 2-3x "
                     "higher)".format(var_v, pei))
    var_v_all = pei_all = None
    if pool_xy and sum(len(x) for x in pool_xy) >= 2:
        var_v_all, pei_all = _gc.balance_metrics(
            np.vstack(pool_xy), np.vstack(pool_tp),
            np.concatenate(pool_tw))
        if not math.isfinite(var_v_all):
            var_v_all = pei_all = None
        elif stratum_field and len(keys) > 1:
            _say(messages,
                 "  whole sample: Var(v) {:.3f}, Pielou loss "
                 "{:.4f}".format(var_v_all, pei_all))
    _say(messages,
         "Sites are written in REVERSE HIERARCHICAL ORDER: any "
         "leading run of SiteIDs is itself spatially balanced, and "
         "replacement sites (Site_Use = Over) are consumed in "
         "SiteID order.")
    if random_seed not in (None, ""):
        _say(messages, "Random seed {}: this design is exactly "
                       "reproducible.".format(random_seed))

    _stamp(out_path, in_frame, kind, n_base, n_over, stratum_field,
           alloc, geographic, random_seed)
    return out_path, site_no, var_v_all, pei_all


def _stamp(out_path, frame, kind, n_base, n_over, stratum_field,
           alloc, geographic, seed):
    import arcpy

    try:
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = (
            "Created by Spatially Balanced Sample (GRTS): {} base "
            "site(s){} from the {} frame {}{}.".format(
                n_base,
                " + {} replacement(s)".format(n_over) if n_over
                else "", kind, os.path.basename(str(frame)),
                ", stratified by {}".format(stratum_field)
                if stratum_field else ""))
        md.description = (
            "Generalized random-tessellation stratified (GRTS) "
            "design of Stevens & Olsen (2004), as in the spsurvey "
            "package (Dumelle et al. 2023): hierarchical "
            "randomization maps the frame onto a line preserving "
            "spatial proximity, a systematic pass with a random "
            "start selects the sites, and reverse hierarchical "
            "ordering writes them so any leading run of SiteIDs is "
            "itself spatially balanced. Allocation: {}. {}"
            "Computed {}. Jenness Enterprises Wildlife Tools "
            "(www.jennessent.com).".format(
                str(alloc).lower(),
                "Random seed {}. ".format(seed)
                if seed not in (None, "") else "",
                "in an automatic Lambert azimuthal equal-area "
                "working projection (geographic input)"
                if geographic else "planar in the output "
                "coordinate system"))
        md.save()
    except Exception:                                        # noqa: BLE001
        pass
