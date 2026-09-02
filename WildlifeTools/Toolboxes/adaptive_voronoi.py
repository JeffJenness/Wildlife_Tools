# -*- coding: utf-8 -*-
"""Adaptive Voronoi Polygons -- Lloyd's algorithm inside a boundary.

Repeatedly: build Voronoi polygons around the current points (the same
engines as Voronoi (Thiessen) Polygons within Boundary), clip them to the
boundary polygon, move each point to its clipped cell's centroid, and
tessellate again. This is LLOYD'S ALGORITHM (Lloyd 1982), and the fixed
point it converges to is a CENTROIDAL VORONOI TESSELLATION (Du, Faber &
Gunzburger 1999): every generator sits at its own cell's centroid. With
uniform density the optimal cells approach congruent regular hexagons
(Gersho's conjecture), which is why the result looks like a honeycomb of
compact, nearly equal-area partitions -- the ecologically defensible way
to cut an analysis area into workable pieces, since "equal area" means
more when each area is round rather than a narrow slice.

Seeds are either the features of a point feature class or ``n_random``
points drawn uniformly inside the boundary (equal-area sampling on the
sphere for geographic data; an optional random seed makes runs
reproducible). ALL points always participate -- there is deliberately no
"only points inside the boundary" option here; a point is lost only when
its clipped cell vanishes entirely outside the boundary, and the
iteration simply continues with the survivors.

STOPPING: whichever comes first of (a) the iteration limit, (b) the time
limit, (c) convergence -- no centroid moved farther than the tolerance
(default: 0.05% of the mean cell diameter). Lloyd converges
geometrically, so (c) usually fires long before (a).

SPEED. arcpy geometry is kept OUT of the hot loop: each iteration only
the cells whose bounding box meets a boundary edge (found through the
same y-band edge index Patch Analysis uses -- a border ring that scales
as sqrt(N), not N) are clipped with arcpy; every interior cell gets a
pure-numpy shoelace centroid. Real output geometries are materialized
once, on the final iteration. Geographic data runs on the authalic
sphere only (exact great-circle tessellation, per-cell locally
equal-area centroids); the ellipsoidal refinement of the parent tool is
deliberately omitted here for speed -- the sub-0.5% spherical error is
irrelevant to where Lloyd settles.

References: Lloyd, S. 1982. Least squares quantization in PCM. IEEE
Transactions on Information Theory 28:129-137,
https://doi.org/10.1109/TIT.1982.1056489. Du, Q., V. Faber, and M.
Gunzburger. 1999. Centroidal Voronoi tessellations: applications and
algorithms. SIAM Review 41:637-676,
https://doi.org/10.1137/S0036144599352836.
"""
import math
import os
import time

import numpy as np

import mahalanobis as _mh
import patch_analysis as _pa
import voronoi_tools as _vt

_CITATION = (
    "Lloyd, S. 1982, IEEE Transactions on Information Theory 28:129-137, "
    "https://doi.org/10.1109/TIT.1982.1056489; Du, Q., V. Faber, and M. "
    "Gunzburger. 1999, SIAM Review 41:637-676, "
    "https://doi.org/10.1137/S0036144599352836.")


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


# ---------------------------------------------------------------------------
#  numpy centroids (signed shoelace; holes wound opposite subtract
#  correctly when rings are summed)
# ---------------------------------------------------------------------------
def _ring_area_centroid(ring):
    """(signed_area, cx, cy) of one ring ((N, 2) array, open or closed)."""
    r = np.asarray(ring, dtype=np.float64)
    if len(r) >= 2 and (r[0] == r[-1]).all():
        r = r[:-1]
    if len(r) < 3:
        return 0.0, 0.0, 0.0
    x, y = r[:, 0], r[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y2 - x2 * y
    a = 0.5 * cross.sum()
    if abs(a) < 1e-300:
        return 0.0, float(x.mean()), float(y.mean())
    cx = ((x + x2) * cross).sum() / (6.0 * a)
    cy = ((y + y2) * cross).sum() / (6.0 * a)
    return float(a), float(cx), float(cy)


def _rings_area_centroid(rings):
    """Area-weighted centroid over rings (holes subtract themselves)."""
    A = CX = CY = 0.0
    for ring in rings:
        a, cx, cy = _ring_area_centroid(ring)
        A += a
        CX += a * cx
        CY += a * cy
    if abs(A) < 1e-300:
        return 0.0, None, None
    return A, CX / A, CY / A


def _geom_rings(geom):
    """All rings of an arcpy polygon as (N, 2) arrays (holes included --
    arcpy winds them opposite the exterior, so signed sums are correct)."""
    out = []
    for part in geom:
        cur = []
        for pt in part:
            if pt is None:
                if len(cur) >= 3:
                    out.append(np.asarray(cur))
                cur = []
            else:
                cur.append((pt.X, pt.Y))
        if len(cur) >= 3:
            out.append(np.asarray(cur))
    return out


# ---------------------------------------------------------------------------
#  seeds
# ---------------------------------------------------------------------------
def _random_seeds(n, bnd_rings, bnd_edges, bands, extent, geographic, rng,
                  messages):
    """n uniform points inside the boundary (equal-area on the sphere)."""
    import arcpy

    xmin, ymin, xmax, ymax = extent
    got = []
    tries = 0
    while len(got) < n and tries < 200:
        m = max(4 * (n - len(got)), 256)
        xs = rng.uniform(xmin, xmax, m)
        if geographic:
            s0, s1 = math.sin(math.radians(ymin)), \
                math.sin(math.radians(ymax))
            ys = np.degrees(np.arcsin(rng.uniform(s0, s1, m)))
        else:
            ys = rng.uniform(ymin, ymax, m)
        pts = np.column_stack([xs, ys])
        inside = _pa.points_inside(pts, bnd_edges, bands=bands)
        got.extend(pts[inside].tolist())
        tries += 1
    if len(got) < n:
        raise arcpy.ExecuteError(
            "Could not place {} random points inside the boundary (is its "
            "area a tiny sliver of its extent?).".format(n))
    return np.asarray(got[:n], dtype=np.float64)


# ---------------------------------------------------------------------------
#  the iteration
# ---------------------------------------------------------------------------
def run_adaptive_voronoi(boundary, out_fc, in_points=None, n_random=None,
                         iterations=100, time_limit_min=None,
                         tolerance=None, out_points=None, random_seed=None,
                         messages=None):
    """Lloyd's algorithm inside ``boundary``. Returns the output path."""
    import arcpy

    bdesc = arcpy.Describe(boundary)
    src_sr = bdesc.spatialReference
    if src_sr is None or getattr(src_sr, "name", "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "The boundary has no defined coordinate system.")
    out_sr = arcpy.env.outputCoordinateSystem or src_sr
    geographic = out_sr.type == "Geographic"
    iterations = max(1, int(iterations or 1))

    _mh.begin_phases([("Reading the inputs...", 1),
                      ("Iterating Lloyd's algorithm...", 8),
                      ("Writing the final polygons...", 2)])
    _say(messages,
         "Adaptive Voronoi (Lloyd's algorithm), {} engine.".format(
             "exact-sphere" if geographic else "planar"))

    bnd = _vt._read_boundary(boundary, out_sr, messages)
    # true-curve boundaries (gdb buffer circles, arcs) serialize as
    # curveRings, which rings_of cannot parse -- a pure-curve
    # boundary would come back as ZERO rings and crash below, and a
    # partly-curved one would silently drop its curved rings.
    # Densify first, per the standing curve rule.
    if getattr(bnd, "hasCurves", False):
        be0 = bnd.extent
        d = max(math.hypot(be0.width, be0.height) / 500.0, 1e-9)
        try:
            bnd = bnd.densify("DISTANCE", d, d)
        except Exception:                                        # noqa: BLE001
            pass

    # seeds ---------------------------------------------------------------
    seed_fids = None
    if in_points not in (None, ""):
        coords, oids, _rows = _vt._read_points(in_points, out_sr, [],
                                               messages)
        seed_fids = oids.astype(int)
    elif n_random:
        coords = None            # after the numpy boundary is built
    else:
        raise arcpy.ExecuteError(
            "Give either a point feature class or a number of random "
            "points.")

    # geographic: one unwrap window for everything (see voronoi_tools).
    # lon0 is the CIRCULAR mean of the boundary vertices (an extent
    # midpoint is meaningless for a dateline-straddling boundary), and
    # the boundary itself is unwrapped into the same window so the numpy
    # rings/edges below and the arcpy clip all agree with the points.
    lon0 = 0.0
    if geographic:
        vr = _pa.rings_of(bnd)
        if vr:
            lam = np.radians(np.concatenate([r[:, 0] for r in vr]))
            lon0 = math.degrees(math.atan2(np.sin(lam).mean(),
                                           np.cos(lam).mean()))
        else:
            be = bnd.extent
            lon0 = 0.5 * (be.XMin + be.XMax)
        bnd = _vt._unwrap_polygon(bnd, lon0, out_sr)
        if coords is not None:
            coords[:, 0] = _vt._unwrap_lon(coords[:, 0], lon0)

    # numpy boundary structures (built once; the hot loop never touches
    # the arcpy boundary except to clip border cells)
    bnd_rings = _pa.rings_of(bnd)
    bnd_edges = _pa.edges_of(bnd_rings)
    bands = _pa.y_bands(bnd_edges)
    ex_all = np.concatenate([r for r in bnd_rings])
    bxmin, bymin = ex_all[:, 0].min(), ex_all[:, 1].min()
    bxmax, bymax = ex_all[:, 0].max(), ex_all[:, 1].max()
    # per-edge bounding boxes for the border-cell test
    e_xmin = np.minimum(bnd_edges[:, 0], bnd_edges[:, 2])
    e_xmax = np.maximum(bnd_edges[:, 0], bnd_edges[:, 2])
    e_ymin = np.minimum(bnd_edges[:, 1], bnd_edges[:, 3])
    e_ymax = np.maximum(bnd_edges[:, 1], bnd_edges[:, 3])
    # ... bucketed by y-band ONCE, so each cell's bbox test sweeps only
    # the edges of the bands its y-range overlaps instead of all of them
    bb_bounds = bb_idx = None
    if len(bnd_edges) >= 64 and bymax > bymin:
        nbb = int(np.clip(len(bnd_edges) // 24, 8, 256))
        bb_bounds = np.linspace(bymin, bymax, nbb + 1)
        bb_idx = [np.flatnonzero((e_ymax >= bb_bounds[b])
                                 & (e_ymin <= bb_bounds[b + 1]))
                  for b in range(nbb)]

    rng = np.random.default_rng(
        int(random_seed) if random_seed not in (None, "") else None)
    if coords is None:
        coords = _random_seeds(int(n_random), bnd_rings, bnd_edges, bands,
                               (bxmin, bymin, bxmax, bymax), geographic,
                               rng, messages)
        _say(messages, "{:,} random seed points placed inside the "
                       "boundary{}".format(
                           len(coords),
                           " (seed {})".format(random_seed)
                           if random_seed not in (None, "") else "."))

    # fixed frame: boundary extent U seed extent, padded 10%
    xmin = min(bxmin, coords[:, 0].min())
    xmax = max(bxmax, coords[:, 0].max())
    ymin = min(bymin, coords[:, 1].min())
    ymax = max(bymax, coords[:, 1].max())
    padx, pady = 0.10 * max(xmax - xmin, 1e-9), 0.10 * max(ymax - ymin,
                                                           1e-9)
    frame = (xmin - padx, ymin - pady, xmax + padx, ymax + pady)

    # default tolerance: 0.05% of the mean cell diameter
    if tolerance in (None, "", 0):
        mean_cell = math.sqrt(
            max((bxmax - bxmin) * (bymax - bymin), 1e-300)
            / max(len(coords), 1))
        tolerance = 5e-4 * mean_cell
    tolerance = float(tolerance)

    deadline = (time.monotonic() + float(time_limit_min) * 60.0
                if time_limit_min not in (None, "", 0) else None)

    def _lat_scale(ring):
        """Locally equal-area x-scaling for geographic shoelace."""
        return math.cos(math.radians(float(np.mean(ring[:, 1]))))

    def _tessellate(pts):
        if geographic:
            return _vt._spherical_cells(pts, lon0, False, out_sr, None,
                                        frame)
        return _vt._planar_cells(pts, frame, None)

    def _cell_state(ring_pts):
        """('inside'|'border'|'outside') for one cell ring vs boundary."""
        r = np.asarray(ring_pts)
        cxmin, cymin = r[:, 0].min(), r[:, 1].min()
        cxmax, cymax = r[:, 0].max(), r[:, 1].max()
        hit = False
        if bb_bounds is None:
            hit = ((e_xmin <= cxmax) & (e_xmax >= cxmin)
                   & (e_ymin <= cymax) & (e_ymax >= cymin)).any()
        elif cymax >= bb_bounds[0] and cymin <= bb_bounds[-1]:
            b0 = int(np.clip(np.searchsorted(bb_bounds, cymin) - 1,
                             0, len(bb_idx) - 1))
            b1 = int(np.clip(np.searchsorted(bb_bounds, cymax) - 1,
                             0, len(bb_idx) - 1))
            for b in range(b0, b1 + 1):
                ii = bb_idx[b]
                if len(ii) and ((e_xmin[ii] <= cxmax)
                                & (e_xmax[ii] >= cxmin)
                                & (e_ymin[ii] <= cymax)
                                & (e_ymax[ii] >= cymin)).any():
                    hit = True
                    break
        if hit:
            return "border"
        return ("inside"
                if _pa.points_inside(r[:1], bnd_edges, bands=bands)[0]
                else "outside")

    # ---- Lloyd loop -----------------------------------------------------
    n0 = len(coords)
    stop_reason = "iteration limit"
    it_done = 0
    cv_first = None
    clip_cache_sr = out_sr
    for it in range(1, iterations + 1):
        if deadline is not None and time.monotonic() > deadline:
            stop_reason = "time limit"
            break
        rings = _tessellate(coords)
        new_pts = []
        keep = []
        areas = []
        for i, ring in enumerate(rings):
            if not ring:
                continue
            state = _cell_state(ring)
            if state == "outside":
                continue
            if state == "inside":
                r = np.asarray(ring)
                if geographic:
                    s = _lat_scale(r)
                    rs = np.column_stack([r[:, 0] * s, r[:, 1]])
                    a, cx, cy = _ring_area_centroid(rs)
                    if cx is None or a == 0.0:
                        continue
                    new_pts.append((cx / s, cy))
                else:
                    a, cx, cy = _ring_area_centroid(r)
                    if a == 0.0:
                        continue
                    new_pts.append((cx, cy))
                areas.append(abs(a))
                keep.append(i)
            else:
                cell = arcpy.Polygon(
                    arcpy.Array([arcpy.Point(x, y) for x, y in ring]
                                + [arcpy.Point(ring[0][0], ring[0][1])]),
                    clip_cache_sr)
                clipped = cell.intersect(bnd, 4)
                if clipped is None or clipped.area == 0:
                    continue
                gr = _geom_rings(clipped)
                if geographic:
                    s = _lat_scale(np.vstack(gr))
                    grs = [np.column_stack([g[:, 0] * s, g[:, 1]])
                           for g in gr]
                    a, cx, cy = _rings_area_centroid(grs)
                    if cx is None:
                        continue
                    new_pts.append((cx / s, cy))
                else:
                    a, cx, cy = _rings_area_centroid(gr)
                    if cx is None:
                        continue
                    new_pts.append((cx, cy))
                areas.append(abs(a))
                keep.append(i)
        if not new_pts:
            raise arcpy.ExecuteError(
                "Every cell fell outside the boundary -- check that the "
                "seeds and the boundary overlap.")
        # keep[j] is the index (into the previous coords) that produced
        # new_pts[j] -- appended in lockstep above.
        new_pts = np.asarray(new_pts, dtype=np.float64)
        keep = np.asarray(keep, dtype=int)
        prev_kept = coords[keep]
        if seed_fids is not None:
            seed_fids = seed_fids[keep]
        areas = np.asarray(areas, dtype=np.float64)

        # coincident centroids would crash qhull next round
        rounded = np.round(new_pts / max(tolerance, 1e-12))
        _u, uidx = np.unique(rounded, axis=0, return_index=True)
        if len(uidx) < len(new_pts):
            uidx = np.sort(uidx)
            new_pts = new_pts[uidx]
            prev_kept = prev_kept[uidx]
            areas = areas[uidx]
            if seed_fids is not None:
                seed_fids = seed_fids[uidx]

        no_losses = len(new_pts) == len(coords)
        coords = new_pts
        it_done = it

        cv = float(areas.std() / areas.mean()) if areas.size else 0.0
        if cv_first is None:
            cv_first = cv
        max_move = float(np.hypot(coords[:, 0] - prev_kept[:, 0],
                                  coords[:, 1] - prev_kept[:, 1]).max())
        if no_losses and max_move < tolerance:
            stop_reason = ("converged (no centroid moved more than the "
                           "tolerance)")
            break
        if it % 10 == 0 or it == 1:
            _say(messages,
                 "  iteration {:>4}: {:,} cells, area CV {:.3f}".format(
                     it, len(coords), cv))
        _mh.phase_step(it, iterations)
    _mh.phase_advance()

    # ---- final polygons -------------------------------------------------
    try:
        bnd_name = arcpy.Describe(boundary).name
    except Exception:                                            # noqa: BLE001
        bnd_name = os.path.basename(str(boundary))
    if in_points not in (None, ""):
        try:
            seed_desc = "source points from {}".format(
                arcpy.Describe(in_points).name)
        except Exception:                                        # noqa: BLE001
            seed_desc = "source points from {}".format(
                os.path.basename(str(in_points)))
    else:
        seed_desc = "{:,} random source points".format(int(n_random))
        if random_seed not in (None, ""):
            seed_desc += " (random seed {})".format(random_seed)
    provenance = (
        "This Adaptive Voronoi tessellation was generated within polygon "
        "features from {bnd}, using {seeds} over {it} iteration{s} "
        "({stop}).".format(bnd=bnd_name, seeds=seed_desc, it=it_done,
                           s="" if it_done == 1 else "s",
                           stop=stop_reason))

    rings = _tessellate(coords)
    out_path, kept = _write_output(out_fc, out_sr, rings, coords,
                                   seed_fids, bnd, bnd_edges, bands,
                                   (e_xmin, e_xmax, e_ymin, e_ymax),
                                   geographic, provenance, messages)
    if out_points:
        _write_points_fc(out_points, out_sr, coords, seed_fids, messages,
                         kept=kept)

    areas_final = []
    with arcpy.da.SearchCursor(out_path, ["Area_Ha"]) as cur:
        for (a,) in cur:
            if a is not None:
                areas_final.append(a)
    if areas_final:
        af = np.asarray(areas_final)
        _say(messages, "")
        _say(messages,
             "Stopped after {} iteration{} ({}). {} of {} seed point{} "
             "survive.".format(
                 it_done, "" if it_done == 1 else "s", stop_reason,
                 len(coords), n0, "" if n0 == 1 else "s"))
        _say(messages,
             "Cell areas: mean {:,.2f} ha, min {:,.2f}, max {:,.2f}; "
             "coefficient of variation {:.3f} (first iteration "
             "{:.3f}).".format(af.mean(), af.min(), af.max(),
                               float(af.std() / af.mean()),
                               cv_first if cv_first is not None else
                               float("nan")))
    _mh.end_phases()
    return out_path


def _write_output(out_fc, out_sr, rings, coords, seed_fids, bnd,
                  bnd_edges, bands, ebox, geographic, provenance,
                  messages):
    import arcpy

    e_xmin, e_xmax, e_ymin, e_ymax = ebox
    out_fc = str(out_fc).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_fc)
    if ws and not _mh.in_gdb_container(out_fc) \
            and not name.lower().endswith(".shp"):
        name += ".shp"
    res = arcpy.management.CreateFeatureclass(
        ws, name, "POLYGON", spatial_reference=_vt.clean_sr(out_sr))
    out_path = res.getOutput(0)
    rows = [["Cell_ID", "LONG", "Cell ID", "", "", ""],
            ["Area_Ha", "DOUBLE", "Area (geodesic hectares)", "", "", ""]]
    if seed_fids is not None:
        rows.insert(1, ["Seed_FID", "LONG", "Seed point FID", "", "", ""])
    arcpy.management.AddFields(out_path, rows)

    fields = ["SHAPE@", "Cell_ID"] \
        + (["Seed_FID"] if seed_fids is not None else []) + ["Area_Ha"]
    is_shp = out_path.lower().endswith(".shp")
    n = 0
    kept = []            # indices into rings/coords that got a cell --
    #                      the points output numbers from THIS list so
    #                      Cell_ID never desyncs when a cell drops
    with arcpy.da.InsertCursor(out_path, fields) as ins:
        for i, ring in enumerate(rings):
            if not ring:
                continue
            r = np.asarray(ring)
            cxmin, cymin = r[:, 0].min(), r[:, 1].min()
            cxmax, cymax = r[:, 0].max(), r[:, 1].max()
            hit = ((e_xmin <= cxmax) & (e_xmax >= cxmin)
                   & (e_ymin <= cymax) & (e_ymax >= cymin))
            cell = arcpy.Polygon(
                arcpy.Array([arcpy.Point(x, y) for x, y in ring]
                            + [arcpy.Point(ring[0][0], ring[0][1])]),
                out_sr)
            if hit.any():
                cell = cell.intersect(bnd, 4)
                if cell is None or cell.area == 0:
                    continue
            elif not _pa.points_inside(r[:1], bnd_edges,
                                       bands=bands)[0]:
                continue
            n += 1
            kept.append(i)
            vals = [cell, n]
            if seed_fids is not None:
                if i < len(seed_fids):
                    vals.append(int(seed_fids[i]))
                else:
                    vals.append(-999 if is_shp else None)
            try:
                vals.append(cell.getArea("GEODESIC", "HECTARES"))
            except Exception:                                    # noqa: BLE001
                # shapefiles cannot store nulls
                vals.append(-999 if is_shp else None)
            ins.insertRow(vals)
    _say(messages, "{:,} adaptive Voronoi cell{} written.".format(
        n, "" if n == 1 else "s"))

    try:
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = (
            "Created by Adaptive Voronoi Polygons (Lloyd's algorithm): "
            "{} centroidal-Voronoi cell(s) inside the boundary, computed "
            "{}.".format(n, "exactly on the sphere" if geographic
                         else "planar in the output CS"))
        md.description = (
            "Centroidal Voronoi tessellation of the boundary polygon: "
            "Voronoi cells whose generating points coincide with their "
            "own centroids, giving compact, nearly equal-area partitions "
            "in a honeycomb-like arrangement. " + _CITATION
            + "\n\n" + provenance)
        md.tags = ("Voronoi, Lloyd, centroidal Voronoi tessellation, "
                   "CVT, partition, equal area, Jenness Enterprises")
        md.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                      "(http://www.jennessent.com). Method: " + _CITATION)
        md.save()
    except Exception:                                            # noqa: BLE001
        pass
    return out_path, kept


def _write_points_fc(out_points, out_sr, coords, seed_fids, messages,
                     kept=None):
    """``kept``: the cell-writer's kept indices into ``coords`` -- the
    two outputs share IDENTICAL Cell_ID numbering (a clipped-away cell
    would otherwise desync every later ID and orphan its point)."""
    import arcpy

    out_points = str(out_points).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_points)
    if ws and not _mh.in_gdb_container(out_points) \
            and not name.lower().endswith(".shp"):
        name += ".shp"
    res = arcpy.management.CreateFeatureclass(
        ws, name, "POINT", spatial_reference=_vt.clean_sr(out_sr))
    path = res.getOutput(0)
    is_shp = path.lower().endswith(".shp")
    rows = [["Cell_ID", "LONG", "Cell ID", "", "", ""]]
    if seed_fids is not None:
        rows.append(["Seed_FID", "LONG", "Seed point FID", "", "", ""])
    arcpy.management.AddFields(path, rows)
    fields = ["SHAPE@XY", "Cell_ID"] \
        + (["Seed_FID"] if seed_fids is not None else [])
    idx = list(kept) if kept is not None else list(range(len(coords)))
    with arcpy.da.InsertCursor(path, fields) as ins:
        for cell_id, i in enumerate(idx, 1):
            x, y = coords[i]
            vals = [(float(x), float(y)), cell_id]
            if seed_fids is not None:
                if i < len(seed_fids):
                    vals.append(int(seed_fids[i]))
                else:
                    vals.append(-999 if is_shp else None)
            ins.insertRow(vals)
    _say(messages, "Final center points written: {}".format(path))
    return path
