# -*- coding: utf-8 -*-
"""Least-Cost Corridor -- combine cost-distance surfaces into a corridor.

A modernized, generalized port of the original CorridorDesigner Python
toolbox's ``CorridorModel`` tool (``cor_lccaGeneral.py``): given a cost
surface (high values = hard to cross) and points marking where a corridor
should enter and leave two protected areas, it computes the accumulated cost
of reaching every cell from each area, combines the two surfaces into a
corridor raster (zero along the literal least-cost path, rising away from
it), and extracts that raster at a series of percentile-width thresholds as
polygons -- the classic "0.1% / 1% / 2% / ... corridor" family.

THIS TOOL IS NOT LAND-FACET-SPECIFIC. Its traditional and still most common
use is a species HABITAT SUITABILITY MODEL, built from ordinary wildlife
habitat variables (landcover, elevation, distance to roads, ...): high
suitability inverted to low cost, corridors built between two protected
Wildland Blocks. The Land Facet chain in this suite is a different, newer
use of the SAME machinery -- corridors between land facets, whose purpose is
explicitly to avoid depending on any particular future vegetation type as
the climate changes, since land facets are defined purely from the
landscape's topographic shape. The tool works identically either way; the
FACET / FACET_ID fields on its input and output are simply inherited from
Identify Termini Polygons's own schema (a "facet" there is documented as
"the category the termini were grouped by" -- a land facet, a single
species' HSM surface, or any other quality surface).

TWO STEPS THE ORIGINAL PERFORMED SEPARATELY ARE ALREADY HANDLED ELSEWHERE
IN THIS SUITE: turning a "high is good" surface into a cost surface is
Invert Raster (``1/(x+c)`` in place of the original's ``100 - hsm``, but
the same purpose); finding the start/end points within each protected area
is Identify Termini Polygons (generalized from the original's single-species
patch/core logic to any number of protected areas and any number of
categories in one pass). This tool is the piece that remains: turning two
cost surfaces from those two tools into a corridor.

BLOCKS, NOT TERMINI, ARE THE CORRIDOR ENDPOINTS. A corridor connects two
WILDLAND BLOCKS -- the actual protected areas -- never two termini polygons
within the same block, since a block is already fully protected and there
is nothing to connect within it. Termini only mark where, within a block,
the corridor should touch down. With more than two blocks, a corridor is
built for EVERY PAIR (generalizing the original, which only ever supported
two), unless the "Wildland Block pairs" parameter restricts the run.

NO SPATIAL ANALYST. The original's ``CostDistance``/``Corridor`` pair is
replaced with an 8-connected weighted graph over the cost raster (edge
weight = the average of the two endpoint cells' cost, times the true
distance between their centers) solved with
``scipy.sparse.csgraph.dijkstra(..., min_only=True)`` -- which natively
gives "the accumulated cost to the NEAREST of these source cells", exactly
CostDistance's multi-source semantics. The graph for one cost surface is
built once and reused for every Wildland Block (one Dijkstra solve per
block, not per pair), then every block PAIR's corridor is just
``dist_a + dist_b``, renormalized to zero at its minimum -- Esri's own
``Corridor`` formula.

GEODESIC DISTANCES on a geographic (latitude/longitude) raster: rather than
approximate, every edge's true ground distance is computed from its two
endpoint cells' actual coordinates via ``wildlife_geom.haversine_distance``
(the same spherical-geodesic function used elsewhere in this toolbox),
vectorized across the whole graph build. North-south edges come out
essentially constant; east-west and diagonal edges correctly shrink toward
the poles.

PERCENTILE BANDS are computed directly with ``numpy.percentile`` on the
finite corridor values across the whole surface -- the continuous version
of the original's ``Slice`` into 1000 equal-COUNT classes followed by
cumulative ``ExtractByAttributes`` calls, without discretization. Each
band's connected regions (8-connected, matching Identify Termini Polygons's
convention) become separate polygon rows, all in ONE feature class across
every category, block pair and percentile -- filter or group by Category,
Block_A/Block_B and Percentile to recover any single corridor.
"""
import itertools
import math
import os

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import land_facet_linkage as _lnk
import land_facet_mahalanobis as _lfm
import land_facet_termini as _term
import topographic_tools as _tt
import tool_metadata as _tm
import wildlife_geom as _wg

DEFAULT_PERCENTILES = [0.1, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org. Land Facet Corridor Designer: Beier, P., and "
    "B. Brost. 2010, Conservation Biology 24:701-710; Brost, B. M., and P. "
    "Beier. 2012, Ecological Applications 22:87-103; Jenness, J., B. Brost, "
    "and P. Beier. 2013, http://www.jennessent.com/arcgis/land_facets.htm.")

_EDGE_DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


# ---------------------------------------------------------------------------
# cost-raster discovery (multi-facet workspace mode)
# ---------------------------------------------------------------------------
# Mahalanobis out_types that are ALREADY oriented as a cost surface (low =
# near the facet's typical conditions = cheap) and must never be inverted;
# the p-value output is the opposite (high = typical = cheap, like density)
# and is not usable as a cost surface without a transform Least-Cost
# Corridor does not apply on its own.
_MAHAL_COST_READY_TYPES = {_mh.OUT_D2, _mh.OUT_D}


def discover_cost_rasters(workspace, messages=None):
    """``{facet_id: {"path", "record", "facet"}}`` for every usable cost
    raster in ``workspace``, one per facet.

    TWO kinds are recognized. A Land Facet Mahalanobis distance raster
    (D or D2 out_type) is ALREADY a valid cost surface -- low distance means
    close to the facet's typical conditions, exactly the "cheap to cross"
    sense a corridor needs -- and is used AS IS, no inversion. An Invert
    Raster output (its own INVERTED linkage record) is used when no such
    Mahalanobis raster exists for that facet. When BOTH exist for the same
    facet, the Mahalanobis raster wins and a warning explains why (this is
    almost always leftover clutter from having also inverted the Density
    raster the Mahalanobis analysis was built from -- a different, and
    weaker, cost surface than the multivariate Mahalanobis one). A
    Mahalanobis p-value raster is skipped with a warning: unlike D/D2, high
    p-values mean "typical", so a p-value surface behaves like density and
    would need to be inverted (or otherwise transformed) before use here,
    which this discovery step does not do automatically.
    """
    import arcpy

    mahal, inverted = {}, {}
    for dirpath, _dirs, files in arcpy.da.Walk(workspace, datatype="RasterDataset"):
        for f in files:
            path = os.path.join(dirpath, f)
            rec = _lnk.read(path, None)
            if not rec:
                continue
            kind = rec.get("record")
            if kind not in (_lnk.MAHALANOBIS, _lnk.INVERTED):
                continue
            finfo = rec.get("facet") or None
            fid = finfo.get("facet_id") if finfo else None
            if fid is None and kind == _lnk.INVERTED:
                # An inverted raster with no facet -- an inverted HSM or
                # diversity surface, the suite's own documented species
                # path -- keys by its band (single-band sources record
                # band 1, matching the category id termini assign a
                # facet-less surface). Skipping it silently dead-ended
                # the HSM -> Invert Raster -> corridor workflow.
                fid = rec.get("band") or 1
                finfo = {"facet_id": fid,
                         "category_label": os.path.splitext(f)[0]}
            if fid is None:
                continue
            if kind == _lnk.MAHALANOBIS:
                if rec.get("out_type") not in _MAHAL_COST_READY_TYPES:
                    _warn(messages,
                          "Facet {}: '{}' is a Mahalanobis p-value raster, "
                          "not a distance raster -- high p-values mean "
                          "'typical', so (unlike D or D2) it is not usable "
                          "as a cost surface as is. Skipped; re-run Land "
                          "Facet Mahalanobis with D or D2 output, or invert "
                          "it deliberately first.".format(
                              fid, os.path.basename(path)))
                    continue
                mahal[fid] = {"path": path, "record": rec, "facet": finfo}
            else:
                inverted[fid] = {"path": path, "record": rec, "facet": finfo}

    out = {}
    for fid in set(mahal) | set(inverted):
        if fid in mahal and fid in inverted:
            _warn(messages,
                  "Facet {}: both a Mahalanobis distance raster and an "
                  "inverted raster were found in the workspace; using the "
                  "Mahalanobis raster ('{}') -- it is already a valid cost "
                  "surface (low distance = near-ideal = cheap) and needs "
                  "no inversion. The inverted raster ('{}') was not "
                  "used.".format(
                      fid, os.path.basename(mahal[fid]["path"]),
                      os.path.basename(inverted[fid]["path"])))
            out[fid] = mahal[fid]
        else:
            out[fid] = mahal.get(fid) or inverted.get(fid)

    if messages is not None and out:
        n_mahal = sum(1 for v in out.values()
                     if v["record"].get("record") == _lnk.MAHALANOBIS)
        _say(messages, "Found {} cost raster{} in the workspace ({} "
                       "Mahalanobis distance, {} inverted).".format(
                           len(out), "" if len(out) == 1 else "s",
                           n_mahal, len(out) - n_mahal))
    return out


# ---------------------------------------------------------------------------
# termini reading
# ---------------------------------------------------------------------------
def _require_termini_fields(in_termini):
    """Friendly failure when ``in_termini`` is not actually an Identify
    Termini Polygons output (e.g. the raw Wildland Blocks layer was picked
    by mistake) -- ``arcpy.da.SearchCursor`` would otherwise raise a bare
    ``RuntimeError: Cannot find field`` with no guidance."""
    import arcpy

    have = {f.name for f in arcpy.ListFields(in_termini)}
    missing = [f for f in ("Category", "CategoryID", "Block") if f not in have]
    if missing:
        raise arcpy.ExecuteError(
            "'{}' is missing the field{} {} -- this does not look like an "
            "Identify Termini Polygons output. Run Identify Termini "
            "Polygons first (on your cost surface and Wildland Blocks) and "
            "use ITS output here, not the Wildland Blocks layer "
            "itself.".format(
                in_termini, "" if len(missing) == 1 else "s",
                ", ".join(missing)))


def _read_termini(in_termini):
    """``{category_id: {"label", "blocks": {block: [geom, ...]}}}``."""
    import arcpy

    _require_termini_fields(in_termini)
    groups = {}
    with arcpy.da.SearchCursor(
            in_termini, ["Category", "CategoryID", "Block", "SHAPE@"]) as cur:
        for category, cid, block, geom in cur:
            if geom is None:
                continue
            g = groups.setdefault(cid, {"label": category, "blocks": {}})
            g["blocks"].setdefault(block, []).append(geom)
    return groups


def _parse_block_pairs(tokens):
    if not tokens:
        return None
    want = set()
    for tok in tokens:
        text = str(tok).strip().strip("'")
        for sep in ("-", ",", ":"):
            if sep in text:
                a, b = text.split(sep, 1)
                try:
                    want.add(frozenset((int(a), int(b))))
                except ValueError:
                    pass
                break
    return want or None


# ---------------------------------------------------------------------------
# the graph + cost-distance engine
# ---------------------------------------------------------------------------
def _build_cost_graph(cost, valid, gt, geodesic):
    """8-connected weighted graph over ``cost`` (H, W): edge weight = the
    average of the two endpoint cells' cost times the true distance between
    their centers. Cells outside ``valid`` get no edges. Returns a CSR
    sparse matrix sized (H*W, H*W)."""
    from scipy.sparse import coo_matrix

    H, W = cost.shape
    n = H * W
    cellsize_x = abs(gt[1])
    cellsize_y = abs(gt[5])
    diag = math.hypot(cellsize_x, cellsize_y)
    planar_dist = {(0, 1): cellsize_x, (1, 0): cellsize_y,
                   (1, 1): diag, (1, -1): diag}

    def lonlat(r, c):
        return (gt[0] + (c + 0.5) * gt[1] + (r + 0.5) * gt[2],
                gt[3] + (c + 0.5) * gt[4] + (r + 0.5) * gt[5])

    rows_all, cols_all, data_all = [], [], []
    for dr, dc in _EDGE_DIRECTIONS:
        r0 = max(0, -dr); r1 = H - max(0, dr)
        c0 = max(0, -dc); c1 = W - max(0, dc)
        if r0 >= r1 or c0 >= c1:
            continue
        rr, cc = np.meshgrid(np.arange(r0, r1), np.arange(c0, c1), indexing="ij")
        rr2, cc2 = rr + dr, cc + dc
        ok = valid[rr, cc] & valid[rr2, cc2]
        if not ok.any():
            continue
        r_a, c_a = rr[ok], cc[ok]
        r_b, c_b = rr2[ok], cc2[ok]
        if geodesic:
            lon_a, lat_a = lonlat(r_a, c_a)
            lon_b, lat_b = lonlat(r_b, c_b)
            dist = _wg.haversine_distance(lat_a, lon_a, lat_b, lon_b)
        else:
            dist = planar_dist[(dr, dc)]
        w = 0.5 * (cost[r_a, c_a] + cost[r_b, c_b]) * dist
        rows_all.append((r_a * W + c_a).ravel())
        cols_all.append((r_b * W + c_b).ravel())
        data_all.append(np.broadcast_to(w, r_a.shape).ravel().astype(np.float64))

    if not rows_all:
        raise ValueError("no traversable edges")
    rows_all = np.concatenate(rows_all)
    cols_all = np.concatenate(cols_all)
    data_all = np.concatenate(data_all)
    return coo_matrix((data_all, (rows_all, cols_all)), shape=(n, n)).tocsr()


def _rasterize_source_mask(geoms, rast_sr, gdal, ogr, gt, proj, w, h, scratch):
    """Boolean (H, W) mask: True where a cell center falls inside the union
    of ``geoms`` (projected to ``rast_sr`` first if they came from a
    different coordinate system)."""
    import arcpy

    sample_sr = geoms[0].spatialReference
    same = (sample_sr is None or rast_sr is None
            or (sample_sr.factoryCode == rast_sr.factoryCode
                and sample_sr.name == rast_sr.name))
    if not same:
        geoms = [g.projectAs(rast_sr) for g in geoms]

    shp = arcpy.CreateUniqueName("lcc_src.shp", scratch)
    arcpy.management.CopyFeatures(geoms, shp)
    drv = gdal.GetDriverByName("MEM")
    mds = drv.Create("", w, h, 1, gdal.GDT_Byte)
    mds.SetGeoTransform(gt)
    try:
        mds.SetProjection(proj)
    except Exception:                                            # noqa: BLE001
        pass
    vec = ogr.Open(shp)
    try:
        gdal.RasterizeLayer(mds, [1], vec.GetLayer(0), burn_values=[1])
    finally:
        vec = None
    mds.FlushCache()
    mask = mds.GetRasterBand(1).ReadAsArray().astype(bool)
    mds = None
    try:
        arcpy.management.Delete(shp)
    except Exception:                                            # noqa: BLE001
        pass
    return mask


def _deliver_corridor_raster(gdal, corridor, valid, gt, proj, w, h, out_path,
                             source_cost_raster, label, geodesic, scratch,
                             messages):
    import arcpy

    tmp = arcpy.CreateUniqueName("corridor.tif", scratch)
    ds_out, oband = _tt._make_output(gdal, tmp, gt, proj, w, h)
    out = np.where(valid, corridor, _tt.NODATA_OUT).astype(np.float32)
    stats = _mh._RasterStats()
    slab = max(1, int(4_000_000 // max(1, w)))
    for r0 in range(0, h, slab):
        v = out[r0:r0 + slab][valid[r0:r0 + slab]]
        if v.size:
            stats.add(v.astype(np.float64))
    oband.WriteArray(out)
    oband.FlushCache()
    out_stats = _mh._finalize_stats(oband, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(oband, out_stats)
    oband.FlushCache()
    ds_out.FlushCache()
    oband = None
    ds_out = None

    _tt._deliver_output(
        tmp, out_path, out_stats, messages,
        title=os.path.basename(str(out_path)),
        summary=(
            "Created by Least-Cost Corridor for {lab}: the combined "
            "accumulated cost of reaching every cell from both Wildland "
            "Blocks, zero along the least-cost path between them and "
            "rising away from it.{geo}".format(
                lab=label,
                geo=" Distances are true geodesic ground distances "
                    "(geographic coordinate system)." if geodesic else "")),
        description=(
            "Least-cost corridor surface: the sum of the accumulated cost "
            "distance from each of two Wildland Blocks (over the cost "
            "surface {src}), renormalized to zero at the literal "
            "least-cost path. Sliced at percentile thresholds to produce "
            "the corridor polygon output. A modernized, generalized port "
            "of the CorridorDesigner CorridorModel tool. {cite}").format(
                src=str(source_cost_raster), cite=_CD_CITATION),
        tags=("corridor, least-cost, cost distance, connectivity, "
              "wildland block, CorridorDesigner, Land Facet, "
              "Jenness Enterprises"),
        citation=_CD_CITATION)


def _create_corridor_fc(out_ws, out_name, sr):
    import arcpy

    arcpy.management.CreateFeatureclass(out_ws, out_name, "POLYGON",
                                        spatial_reference=sr)
    out_fc = os.path.join(out_ws, out_name)
    fields = [("Category", "TEXT", 64), ("CategoryID", "LONG", None),
              ("Block_A", "LONG", None), ("Block_B", "LONG", None),
              ("Percentile", "DOUBLE", None), ("CorridorID", "LONG", None),
              ("Area_SqM", "DOUBLE", None), ("Cells", "LONG", None)]
    # one AddFields call, not one AddField per field (standing rule)
    arcpy.management.AddFields(out_fc, [
        [nm, tp, "", ln or "", "", ""] for nm, tp, ln in fields])
    return out_fc


def run_least_cost_corridor(in_termini, cost_raster=None, cost_workspace=None,
                            out_workspace=None, out_prefix="Cor", out_fc=None,
                            percentiles=None, block_pairs=None,
                            facet_values=None, messages=None):
    """Build least-cost corridors between every pair of Wildland Blocks
    represented in ``in_termini`` (Identify Termini Polygons's output), for
    every facet/category present. Returns the number of corridor rasters
    written.

    Cost surfaces come from EITHER ``cost_raster`` (a single raster -- the
    termini feature class must then reduce to exactly one facet) OR
    ``cost_workspace`` (Invert Raster's per-facet fan-out output; each
    raster is matched to a termini facet by the facet id in its own linkage
    record). ``percentiles`` is a list of percent-of-valid-cells thresholds
    (default 0.1, 1, 2, ..., 10, matching the original CorridorModel).
    ``block_pairs`` restricts which Wildland Block pairs are built (raw
    tokens like "1-2"); default is every pair. ``facet_values`` restricts
    which facets are processed (raw facet-id tokens); default is every
    facet present in both the termini feature class and the cost source.
    """
    import arcpy
    from scipy.sparse.csgraph import dijkstra
    from scipy import ndimage
    from osgeo import ogr

    percentiles = sorted(set(float(p) for p in (percentiles or DEFAULT_PERCENTILES)))
    if not percentiles or any(p <= 0 or p > 100 for p in percentiles):
        raise arcpy.ExecuteError(
            "Percentile thresholds must be greater than 0 and at most 100.")
    if not out_workspace:
        raise arcpy.ExecuteError(
            "An output workspace is required for the corridor cost rasters.")
    out_fc = str(out_fc or "").rstrip("\\/")
    if not out_fc:
        raise arcpy.ExecuteError(
            "An output feature class is required for the corridor polygons.")
    if not cost_raster and not cost_workspace:
        raise arcpy.ExecuteError(
            "Supply either a single cost raster or a cost-raster workspace.")

    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()

    # Resolve to a real catalog path BEFORE anything gets stamped into a
    # linkage record. A map-layer parameter's value (the common case when
    # the termini output is already loaded) stringifies to just its LAYER
    # NAME, not a path -- fine for arcpy's own calls below (which resolve a
    # layer against the map automatically), but useless as "source_termini"
    # for a report run in a different session: the reader could not find
    # the source at all, so the chain walk stopped at this dataset instead
    # of continuing back through Termini/Density/Clustering (found from
    # Jeff's real report, 2026-08-09).
    try:
        in_termini = arcpy.Describe(in_termini).catalogPath
    except Exception:                                            # noqa: BLE001
        pass

    trec = _lnk.read(in_termini, _lnk.TERMINI)
    groups = _read_termini(in_termini)
    if facet_values:
        want = set()
        for v in facet_values:
            try:
                want.add(int(v))
            except (TypeError, ValueError):
                pass
        groups = {k: v for k, v in groups.items() if k in want}
        if not groups:
            raise arcpy.ExecuteError(
                "None of the requested facet values occur in the termini "
                "feature class.")
    if not groups:
        raise arcpy.ExecuteError(
            "The termini feature class has no usable features.")

    want_pairs = _parse_block_pairs(block_pairs)

    single_mode = bool(cost_raster)
    if single_mode:
        if len(groups) != 1:
            raise arcpy.ExecuteError(
                "A single cost raster was supplied, but {} categories are "
                "present in the termini feature class. Restrict "
                "'Categories to process' to one category, or supply a "
                "cost-raster workspace instead.".format(len(groups)))
        cost_map = None
    else:
        cost_map = discover_cost_rasters(cost_workspace, messages)
        if not cost_map:
            raise arcpy.ExecuteError(
                "No cost rasters carrying a Land Facet linkage record were "
                "found in the workspace. Run Invert Raster first, or "
                "supply a single cost raster instead.")

    analysis_id = (trec or {}).get("analysis_id") or _lnk.new_analysis_id()
    src_analysis = (trec or {}).get("analysis_id")

    out_ws, out_name = os.path.split(out_fc)
    fc_state = {"created": False, "sr": None}
    corridor_id = [0]
    n_rasters = [0]
    n_pieces_grand = [0]
    corridors_meta = []

    facet_items = sorted(groups.items(),
                         key=lambda kv: (kv[0] is None, kv[0]))
    _mh.begin_phases(
        [("Reading the termini feature class...", 1)]
        + [("Category {} of {}: {}".format(i, len(facet_items), g["label"]), 6)
           for i, (fid, g) in enumerate(facet_items, start=1)]
        + [("Finishing...", 1)])
    _mh.phase_advance()

    def _insert_pieces(mask_p, pct, label, fid, b1, b2, gt, proj, rast_sr):
        lab_grid, n_lab = ndimage.label(mask_p, structure=np.ones((3, 3), int))
        if not n_lab:
            return 0
        if not fc_state["created"]:
            _create_corridor_fc(out_ws, out_name, rast_sr)
            fc_state["created"] = True
            fc_state["sr"] = rast_sr
        polys = _term._polygons_from_labels(gdal, ogr, lab_grid, gt, proj, rast_sr)
        counts = np.bincount(lab_grid.ravel(),
                             minlength=n_lab + 1)[1:]
        reproj = (fc_state["sr"] is not None and rast_sr is not None
                 and (fc_state["sr"].factoryCode != rast_sr.factoryCode
                      or fc_state["sr"].name != rast_sr.name))
        rows = []
        for k in range(1, n_lab + 1):
            geom = polys.get(k)
            if geom is None:
                continue
            if reproj:
                geom = geom.projectAs(fc_state["sr"])
            area, _geo = _term._geodesic_area_m2(geom)
            if area <= 0:
                continue
            corridor_id[0] += 1
            rows.append((geom, label, int(fid or 0), b1, b2, pct,
                        corridor_id[0], area, int(counts[k - 1])))
        if not rows:
            return 0
        with arcpy.da.InsertCursor(
                out_fc, ["SHAPE@", "Category", "CategoryID", "Block_A",
                         "Block_B", "Percentile", "CorridorID", "Area_SqM",
                         "Cells"]) as cur:
            for r in rows:
                cur.insertRow(r)
        return len(rows)

    for fid, g in facet_items:
        _mh.phase_advance()
        label = g["label"]
        blocks_dict = g["blocks"]
        if len(blocks_dict) < 2:
            _warn(messages, "{}: termini exist in fewer than 2 Wildland "
                            "Blocks; no corridor can be built.".format(label))
            continue

        if single_mode:
            cpath = cost_raster
        else:
            entry = cost_map.get(fid)
            if entry is None:
                _warn(messages, "{}: no matching cost raster found in the "
                                "workspace (facet id {}); skipped.".format(
                                    label, fid))
                continue
            cpath = entry["path"]

        src, in_copy = _hs._open_source(cpath, scratch, messages)
        try:
            band = src.GetRasterBand(1)
            w, h = src.RasterXSize, src.RasterYSize
            gt = src.GetGeoTransform()
            proj = src.GetProjection()
            cost = band.ReadAsArray(0, 0, w, h).astype(np.float64)
            nod = band.GetNoDataValue()
            vmask = _mh.band_validity_mask(band)   # capture while src is open
        finally:
            src = None
            if in_copy:
                try:
                    arcpy.management.Delete(in_copy)
                except Exception:                                # noqa: BLE001
                    pass
        rast_sr = arcpy.Describe(cpath).spatialReference

        valid = np.isfinite(cost)
        if nod is not None:
            with np.errstate(invalid="ignore"):
                valid &= (cost != float(nod))
        if vmask is not None:
            valid &= vmask
        n_neg = int((valid & (cost < 0)).sum())
        if n_neg:
            _warn(messages,
                  "{}: {:,} cells of the cost raster are negative and were "
                  "treated as impassable (costs must be zero or "
                  "positive).".format(label, n_neg))
        valid &= (cost >= 0)
        if not valid.any():
            _warn(messages, "{}: the cost raster has no usable "
                            "cells; skipped.".format(label))
            continue

        geodesic = bool(rast_sr is not None and rast_sr.type == "Geographic")
        if geodesic:
            _say(messages, "{}: the cost raster's coordinate system is "
                           "geographic -- using true geodesic "
                           "distances.".format(label))

        try:
            graph = _build_cost_graph(cost, valid, gt, geodesic)
        except ValueError:
            _warn(messages, "{}: the cost raster has no traversable "
                            "adjacent cells; skipped.".format(label))
            continue

        block_dist = {}
        for block, geoms in sorted(blocks_dict.items()):
            mask = _rasterize_source_mask(geoms, rast_sr, gdal, ogr, gt,
                                          proj, w, h, scratch)
            mask &= valid
            src_idx = np.flatnonzero(mask.ravel())
            if src_idx.size == 0:
                _warn(messages, "{}: Wildland Block {}'s termini fall "
                                "entirely on NoData or unusable cells of "
                                "the cost raster; that block is skipped "
                                "for this facet.".format(label, block))
                continue
            d = dijkstra(graph, directed=False, indices=src_idx,
                        min_only=True)
            block_dist[block] = d.reshape(h, w)

        pairs = list(itertools.combinations(sorted(block_dist), 2))
        if want_pairs is not None:
            pairs = [p for p in pairs if frozenset(p) in want_pairs]
        if not pairs:
            _warn(messages, "{}: no Wildland Block pairs to "
                            "connect.".format(label))
            continue

        for (b1, b2) in pairs:
            d1, d2 = block_dist[b1], block_dist[b2]
            both = np.isfinite(d1) & np.isfinite(d2)
            if not both.any():
                _warn(messages,
                      "{}: no path exists between Wildland Blocks {} and "
                      "{} (the cost surface is disconnected between "
                      "them); skipped.".format(label, b1, b2))
                continue

            corridor = np.full((h, w), np.nan, dtype=np.float64)
            corridor[both] = d1[both] + d2[both]
            corridor[both] -= np.nanmin(corridor[both])

            pair_label = "{} (Wildland Block {} <-> {})".format(
                label, b1, b2)
            out_raster = _mh.resolve_raster_output(os.path.join(
                out_workspace, "{}_{}_B{}_B{}".format(
                    out_prefix, _lfm.safe_name(label), b1, b2)), messages)
            _deliver_corridor_raster(gdal, corridor, both, gt, proj, w, h,
                                     out_raster, cpath, pair_label, geodesic,
                                     scratch, messages)
            _lnk.write(out_raster, _lnk.corridor_record(
                analysis_id=analysis_id, source_termini=in_termini,
                source_cost_raster=cpath,
                category={"category_id": fid, "label": label},
                block_a=b1, block_b=b2, percentiles=percentiles,
                source_analysis_id=src_analysis, geodesic=geodesic),
                messages)
            n_rasters[0] += 1

            vals = corridor[both]
            n_pieces = 0
            for p in percentiles:
                thr = float(np.percentile(vals, p))
                mask_p = both & (corridor <= thr)
                n_pieces += _insert_pieces(mask_p, p, label, fid, b1, b2,
                                          gt, proj, rast_sr)
            n_pieces_grand[0] += n_pieces
            corridors_meta.append({
                "category_id": fid, "label": label, "block_a": b1,
                "block_b": b2, "cost_raster": str(cpath),
                "n_pieces": n_pieces,
            })
            _say(messages, "{}: corridor written ({:,} percentile polygon "
                           "piece{}).".format(
                               pair_label, n_pieces,
                               "" if n_pieces == 1 else "s"))

    _mh.phase_advance("Finishing...")
    if not fc_state["created"]:
        raise arcpy.ExecuteError(
            "No corridors could be built. Every facet/block-pair "
            "combination either had fewer than 2 Wildland Blocks with "
            "termini, or the cost surface was disconnected between the "
            "blocks.")

    try:
        _tm.set_entity_and_fields(
            out_fc,
            entity=("One polygon per connected region of a least-cost "
                    "corridor at a given percentile-width threshold, "
                    "between a pair of Wildland Blocks."),
            fields=[
                {"name": "Category", "description":
                    "The category (from the termini feature class's "
                    "Category field) this corridor belongs to -- a land "
                    "facet, a species habitat-suitability surface, or any "
                    "other quality surface."},
                {"name": "CategoryID", "description":
                    "The category's id, from the termini feature class."},
                {"name": "Block_A", "description":
                    "One of the two Wildland Blocks (feature order in the "
                    "block layer) this corridor connects."},
                {"name": "Block_B", "description":
                    "The other Wildland Block this corridor connects."},
                {"name": "Percentile", "description":
                    "The percentile-of-valid-cells threshold this polygon "
                    "represents (e.g. 1.0 = the smallest 1% of the "
                    "combined cost-distance surface)."},
                {"name": "CorridorID", "description":
                    "Sequential id across the whole output."},
                {"name": "Area_SqM", "description":
                    "Geodesic area of this polygon, in square meters."},
                {"name": "Cells", "description":
                    "Raster cell count of this connected region."},
            ], messages=messages)
        from arcpy import metadata as md
        m = md.Metadata(out_fc)
        m.title = os.path.basename(out_fc)
        m.tags = ("corridor, least-cost, cost distance, connectivity, "
                  "wildland block, CorridorDesigner, Land Facet, "
                  "Jenness Enterprises")
        m.summary = (
            "Created by Least-Cost Corridor from {term}: {n} corridor "
            "raster{ns} across {pc} percentile threshold{ps} each "
            "({pctlist}).").format(
                term=str(in_termini), n=n_rasters[0],
                ns="" if n_rasters[0] == 1 else "s",
                pc=len(percentiles), ps="" if len(percentiles) == 1 else "s",
                pctlist=", ".join("{:g}%".format(p) for p in percentiles))
        m.description = (
            "Least-cost corridor polygons between pairs of Wildland "
            "Blocks, at a series of percentile-width thresholds. A "
            "modernized, generalized port of the CorridorDesigner "
            "CorridorModel tool -- built for species habitat-suitability "
            "surfaces (its traditional use) and equally for Land Facet "
            "Corridor Designer surfaces. " + _CD_CITATION)
        m.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                     "(http://www.jennessent.com). Method: " + _CD_CITATION)
        m.save()
    except Exception as e:                                       # noqa: BLE001
        _warn(messages, "Could not write output metadata: {}".format(e))

    _lnk.write(out_fc, _lnk.corridor_summary_record(
        analysis_id=analysis_id, source_termini=in_termini,
        corridors=corridors_meta, percentiles=percentiles,
        source_analysis_id=src_analysis), messages)

    _say(messages, "{:,} corridor raster{} and {:,} polygon piece{} "
                   "written.".format(
                       n_rasters[0], "" if n_rasters[0] == 1 else "s",
                       n_pieces_grand[0],
                       "" if n_pieces_grand[0] == 1 else "s"))
    _mh.end_phases()
    return n_rasters[0]
