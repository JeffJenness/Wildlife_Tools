# -*- coding: utf-8 -*-
"""Identify Termini Polygons -- where the corridors will begin and end.

A modernized port of the Land Facet Corridor Designer's ArcMap "Identify
Termini Polygons" tool (``cmdTermini.cls`` / ``LandFacetFunctions.bas``
``CalcLandFacetTermini``; manual pp. 44-46). Land facet corridors do not run
from the edge of one Wildland Block to the edge of the other -- they run
between the concentrations of the facet INSIDE the blocks. This tool finds
those concentrations:

1. within each Wildland Block, take the cells of the analysis surface
   (typically one facet's Land Facet Density band, or a Diversity surface)
   whose value exceeds a threshold;
2. aggregate them into regions -- cells touching along an edge OR at a corner
   belong to the same region (the manual's stated rule, realized here directly
   as an 8-connected component labelling);
3. within each block, measure every region against the LARGEST region in that
   block and keep those at least a given percentage of its size (default 50%,
   the original's default).

DIFFERENCES FROM THE ORIGINAL, all deliberate:

* THE BLOCK-2 BUG IS FIXED. The original gated Wildland Block 2's clipping and
  size filtering on Block 1's polygon count (``LandFacetFunctions.bas`` line
  363), so whenever Block 1 had no cells above the threshold, Block 2's raw
  polygons were written unclipped and unfiltered.
* ANY NUMBER OF WILDLAND BLOCKS. The original required a layer of exactly two
  polygons. Here every FEATURE of the block layer is one block (selection
  honored), each analyzed independently -- the schema below lets the future
  corridor-creation tools build every block pair's corridor in one run.
* ONE FEATURE CLASS FOR ALL CATEGORIES. Fed this suite's multi-band Land
  Facet Density raster, the tool runs every band (facet) in a single pass
  and writes ONE polygon feature class with a ``Category`` field, rather
  than the original's one-shapefile-per-facet-run. Group or filter by
  ``Category`` to recover the per-category termini. The field is named
  ``Category``, not ``Facet``, because this tool is equally at home on a
  species habitat-suitability surface, where "facet" would be the wrong
  word entirely.
* THE DIVERSITY THRESHOLD MATCHES THE PUBLISHED INTENT. Manual p. 13 defines
  diversity termini from "the half of all cells inside each Wildland Block
  with the highest H'" -- a PER-BLOCK median of IN-BLOCK cells, which the
  original left the user to approximate by hand (typically, and wrongly, with
  a whole-raster median). The "median within each block" threshold mode
  computes it exactly.
* AREAS ARE GEODESIC (square meters), so the relative-size comparison is
  honest on geographic rasters, where the original compared areas in square
  degrees.
* Stored density values are UNSCALED first (x 1/10,000 back to proportions)
  when the source carries a density linkage record, so the default threshold
  of 0 means "any density at all", exactly as the manual recommends.
* A THIRD THRESHOLD MODE, ``MODE_WHOLE_BLOCK``, is not in the original at
  all: it skips thresholding entirely and writes each Wildland Block polygon
  itself as that block's one terminus, per category. This is the baseline
  case -- connect the protected areas directly, with no attempt to target a
  quality concentration inside them -- and it is also how a user with no
  land-facet or diversity surface at all still gets a valid Least-Cost
  Corridor input from the Wildland Blocks alone.

No Spatial Analyst required (the original needed it). Every output is stamped
with a ``termini`` linkage record so the corridor tools and the Land Facet
Data Report know its place in the chain.
"""
import os

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import topographic_tools as _tt
import land_facet_linkage as _lnk
import land_facet_mahalanobis as _lfm
import invert_raster as _inv
import tool_metadata as _tm

# Threshold modes.
MODE_FIXED = "fixed"            # value > threshold_value (density default: 0)
MODE_MEDIAN = "block_median"    # value > median of in-block values, per block
MODE_WHOLE_BLOCK = "whole_block"  # the block polygon itself, no thresholding

DEFAULT_PCT = 50.0              # the original's relative-size default
NO_THRESHOLD = -9999.0          # sentinel: whole-block mode applies none
                                # (arcpy's shapefile cursor rejects both None
                                # and NaN in a DOUBLE field with "the field
                                # is not nullable" -- verified 2026-08-09)

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org. Land Facet Corridor Designer: Beier, P., and "
    "B. Brost. 2010, Conservation Biology 24:701-710; Brost, B. M., and P. "
    "Beier. 2012, Ecological Applications 22:87-103; Jenness, J., B. Brost, "
    "and P. Beier. 2013, http://www.jennessent.com/arcgis/land_facets.htm.")

# Fill colors for the generated unique-values symbology (cycled per facet).
_PALETTE = [(230, 76, 0), (0, 112, 255), (76, 230, 0), (169, 0, 230),
            (255, 170, 0), (0, 197, 255), (255, 0, 197), (114, 137, 68),
            (0, 168, 132), (168, 112, 0), (115, 178, 255), (230, 0, 0)]


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def _ogr_mem_driver(ogr):
    """The OGR in-memory vector driver ('MEM' in GDAL >= 3.11, 'Memory'
    before)."""
    for name in ("MEM", "Memory"):
        drv = ogr.GetDriverByName(name)
        if drv is not None:
            return drv
    raise RuntimeError("No OGR in-memory driver available.")


def _prepare_blocks(blocks, rast_sr, scratch, messages):
    """The Wildland Blocks as (geometries in the raster's SR, shapefile path
    for rasterizing, scratch files to delete).

    Copying to a shapefile honors any selection on the layer and densifies
    true-curve boundaries (a shapefile cannot store arcs, so geodatabase
    buffer circles arrive as vertices rather than collapsing to arc
    endpoints). Every FEATURE is one block.
    """
    import arcpy

    shp = arcpy.CreateUniqueName("termini_blocks.shp", scratch)
    # Neutralized copies: an Output Coordinate System environment would
    # project scratch features off the raster's grid (see
    # mahalanobis.neutral_grid_env).
    with _mh.neutral_grid_env():
        arcpy.management.CopyFeatures(blocks, shp)
    cleanup = [shp]

    geoms = []
    with arcpy.da.SearchCursor(shp, ["SHAPE@"]) as cur:
        for (g,) in cur:
            if g is not None:
                geoms.append(g)
    if not geoms:
        raise arcpy.ExecuteError(
            "The Wildland Block layer has no polygon features (check any "
            "selection).")

    blk_sr = geoms[0].spatialReference
    same_sr = (blk_sr is None or rast_sr is None
               or blk_sr.factoryCode == rast_sr.factoryCode
               and blk_sr.name == rast_sr.name)
    if not same_sr:
        geoms = [g.projectAs(rast_sr) for g in geoms]
        shp2 = arcpy.CreateUniqueName("termini_blocks_prj.shp", scratch)
        with _mh.neutral_grid_env():
            arcpy.management.CopyFeatures(geoms, shp2)
        cleanup.append(shp2)
        _say(messages, "Projected {} Wildland Block feature{} to the "
                       "raster's coordinate system.".format(
                           len(geoms), "" if len(geoms) == 1 else "s"))
        shp = shp2

    # Overlapping blocks would make cell ownership ambiguous (a cell can only
    # count toward one block); warn, later block wins.
    if len(geoms) <= 25:
        for i in range(len(geoms)):
            for j in range(i + 1, len(geoms)):
                try:
                    inter = geoms[i].intersect(geoms[j], 4)
                    if inter is not None and inter.area > 0:
                        _warn(messages,
                              "Wildland Block features {} and {} overlap; "
                              "cells in the overlap are analyzed as part of "
                              "block {} only.".format(i + 1, j + 1, j + 1))
                except Exception:                                # noqa: BLE001
                    pass
    return geoms, shp, cleanup


def _rasterize_blocks(gdal, ogr, shp, w, h, gt, proj, n_blocks):
    """An int32 grid, 0 outside every block, k inside block k (1-based,
    shapefile feature order; a cell counts toward the block containing its
    center, the later block winning any overlap)."""
    drv = gdal.GetDriverByName("MEM")
    mds = drv.Create("", w, h, 1, gdal.GDT_Int32)
    mds.SetGeoTransform(gt)
    try:
        mds.SetProjection(proj)
    except Exception:                                            # noqa: BLE001
        pass
    vec = ogr.Open(shp)
    try:
        lyr = vec.GetLayer(0)
        for b in range(n_blocks):
            lyr.SetAttributeFilter("FID = {}".format(b))
            gdal.RasterizeLayer(mds, [1], lyr, burn_values=[b + 1])
        lyr.SetAttributeFilter(None)
    finally:
        vec = None
    mds.FlushCache()
    out = mds.GetRasterBand(1).ReadAsArray()
    mds = None
    return out


def _polygons_from_labels(gdal, ogr, lab, gt, proj, rast_sr):
    """{label: arcpy Polygon} for every nonzero label in ``lab``.

    The label grid is polygonized 4-connected; parts of one label that touch
    only at a corner come back as separate polygons and are unioned into a
    single (multipart) geometry, which is exactly the original's
    combine-everything-that-touches behavior.
    """
    import arcpy
    from osgeo import osr

    drv = gdal.GetDriverByName("MEM")
    lds = drv.Create("", lab.shape[1], lab.shape[0], 1, gdal.GDT_Int32)
    lds.SetGeoTransform(gt)
    try:
        lds.SetProjection(proj)
    except Exception:                                            # noqa: BLE001
        pass
    band = lds.GetRasterBand(1)
    # SetNoDataValue MUST precede WriteArray on a MEM band: the reverse order
    # leaves Polygonize reading all zeros (GDAL 3.12, verified 2026-08-08)
    # even though ReadAsArray still returns the data.
    band.SetNoDataValue(0)
    band.WriteArray(lab)
    band.FlushCache()

    vdrv = _ogr_mem_driver(ogr)
    vds = vdrv.CreateDataSource("termini_polys")
    srs = None
    if proj:
        try:
            srs = osr.SpatialReference(wkt=proj)
        except Exception:                                        # noqa: BLE001
            srs = None
    vlyr = vds.CreateLayer("polys", srs=srs, geom_type=ogr.wkbPolygon)
    vlyr.CreateField(ogr.FieldDefn("LBL", ogr.OFTInteger))
    # No mask: nodata is NOT honored as an implicit mask, so the background
    # comes back as one big label-0 polygon and is dropped in the loop below.
    gdal.Polygonize(band, None, vlyr, 0)
    vlyr.ResetReading()
    band = None
    lds = None

    geoms = {}
    for feat in vlyr:
        lbl = feat.GetField("LBL")
        g = feat.GetGeometryRef()
        if lbl is None or lbl <= 0 or g is None:
            continue
        # FromWKT (unlike FromWKB) accepts the spatial reference, which the
        # later intersect against the block geometry needs.
        poly = arcpy.FromWKT(g.ExportToWkt(), rast_sr)
        prev = geoms.get(lbl)
        geoms[lbl] = poly if prev is None else prev.union(poly)
    vds = None
    return geoms


def _geodesic_area_m2(geom):
    """Geodesic area in square meters, falling back to planar SR units when
    the geometry's coordinate system cannot support a geodesic measure."""
    try:
        a = geom.getArea("GEODESIC", "SQUAREMETERS")
        if a is not None and np.isfinite(a):
            return float(a), True
    except Exception:                                            # noqa: BLE001
        pass
    return float(getattr(geom, "area", 0.0) or 0.0), False


def write_layer_file(out_fc, facet_names, messages=None):
    """A .lyrx beside ``out_fc`` symbolizing the termini by Unique Values on
    the ``Category`` field (semi-transparent fills, one hue per category),
    built by saving a layer file from the real output and replacing its
    renderer. Returns the .lyrx path, or None on any failure."""
    import json
    import arcpy

    try:
        ws = os.path.dirname(str(out_fc))
        name = os.path.splitext(os.path.basename(str(out_fc)))[0]
        lyr_dir = _mh.lyrx_dir_for(ws)
        if lyr_dir is None:
            return None
        out_lyrx = os.path.join(lyr_dir, name + "_symbology.lyrx")

        classes = []
        for i, f in enumerate(sorted(facet_names)):
            r, g, b = _PALETTE[i % len(_PALETTE)]
            classes.append({
                "type": "CIMUniqueValueClass", "label": str(f),
                "visible": True,
                "values": [{"type": "CIMUniqueValue",
                            "fieldValues": [str(f)]}],
                "symbol": {
                    "type": "CIMSymbolReference",
                    "symbol": {
                        "type": "CIMPolygonSymbol",
                        "symbolLayers": [
                            {"type": "CIMSolidStroke", "enable": True,
                             "capStyle": "Round", "joinStyle": "Round",
                             "width": 1.2,
                             "color": {"type": "CIMRGBColor",
                                       "values": [int(r * 0.6), int(g * 0.6),
                                                  int(b * 0.6), 100]}},
                            {"type": "CIMSolidFill", "enable": True,
                             "color": {"type": "CIMRGBColor",
                                       "values": [r, g, b, 50]}},
                        ]}}})
        renderer = {"type": "CIMUniqueValueRenderer", "fields": ["Category"],
                    "groups": [{"type": "CIMUniqueValueGroup",
                                "heading": "Category", "classes": classes}],
                    "useDefaultSymbol": False,
                    "polygonSymbolColorTarget": "ColorTargetFill"}

        tmp = "jenness_termini_tmp_layer"
        try:
            arcpy.management.Delete(tmp)
        except Exception:                                        # noqa: BLE001
            pass
        arcpy.management.MakeFeatureLayer(out_fc, tmp)
        try:
            arcpy.management.SaveToLayerFile(tmp, out_lyrx, "ABSOLUTE")
        finally:
            try:
                arcpy.management.Delete(tmp)
            except Exception:                                    # noqa: BLE001
                pass

        with open(out_lyrx, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        layer = doc["layerDefinitions"][0]
        layer["renderer"] = renderer
        layer["name"] = name
        if "featureTable" in layer:
            layer["featureTable"]["displayField"] = "Category"
        with open(out_lyrx, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        return out_lyrx
    except Exception as e:                                       # noqa: BLE001
        _warn(messages, "Could not create the symbology layer file: "
                        "{}".format(e))
        return None


def run_termini(in_raster, blocks, out_fc, threshold_mode=MODE_FIXED,
                threshold_value=0.0, pct=DEFAULT_PCT, facet_values=None,
                messages=None):
    """Identify the termini polygons of ``in_raster`` within each Wildland
    Block feature of ``blocks`` and write them to the polygon feature class
    ``out_fc``. Returns the symbology .lyrx path (or None).

    ``threshold_mode``: MODE_FIXED keeps cells > ``threshold_value``;
    MODE_MEDIAN keeps cells above the median of the in-block cells, computed
    per block (the manual's diversity rule). ``pct``: keep regions at least
    this percent of the largest region in their block (per facet).
    ``facet_values`` restricts a multi-band run: facet ids when the source
    carries a density record, plain band numbers otherwise.
    """
    import arcpy
    from osgeo import ogr
    from scipy import ndimage

    thr_fixed = float(threshold_value or 0.0)
    pct = float(pct if pct is not None else DEFAULT_PCT)
    if not 0.0 <= pct <= 100.0:
        raise arcpy.ExecuteError(
            "The relative size threshold must be between 0 and 100 percent.")
    if threshold_mode not in (MODE_FIXED, MODE_MEDIAN, MODE_WHOLE_BLOCK):
        raise arcpy.ExecuteError(
            "Unknown threshold mode: {}".format(threshold_mode))

    # A trailing separator would make os.path.split misparse the dataset name.
    out_fc = str(out_fc).rstrip("\\/")
    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()

    # The linkage record governing the source (parent record for a band path):
    # supplies the band-facet table + stored-integer scale for a density
    # raster, and the analysis id to chain from for anything in the chain.
    rec, path_band = _inv.source_density_record(in_raster)
    scale = (rec or {}).get("scale_factor")
    if rec is None:
        p = str(in_raster)
        m = _inv._BAND_RE.match(os.path.basename(p))
        chain_rec = _lnk.read(os.path.dirname(p) if m else p, None)
    else:
        chain_rec = rec
    analysis_id = (chain_rec or {}).get("analysis_id") or _lnk.new_analysis_id()
    src_analysis = (chain_rec or {}).get("analysis_id")

    rast_sr = arcpy.Describe(in_raster).spatialReference
    base = _lfm.safe_name(
        os.path.splitext(os.path.basename(str(in_raster)))[0])

    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    block_cleanup = []
    try:
        nbands = src.RasterCount
        w, h = src.RasterXSize, src.RasterYSize
        if w < 1 or h < 1:
            raise arcpy.ExecuteError("The input raster is empty.")
        gt = src.GetGeoTransform()
        proj = src.GetProjection()

        # ---- which bands ---------------------------------------------------
        if path_band:
            band_nos = [path_band]
        else:
            band_nos = list(range(1, nbands + 1))
        if facet_values and not path_band:
            want = set()
            for v in facet_values:
                try:
                    want.add(int(v))
                except (TypeError, ValueError):
                    pass
            if rec:
                band_nos = [b for b in band_nos
                            if (_inv._facet_of(rec, b) or {}).get("facet_id")
                            in want]
                kind = "facet"
            else:
                band_nos = [b for b in band_nos if b in want]
                kind = "band"
            if not band_nos:
                raise arcpy.ExecuteError(
                    "None of the requested {} values occur in the "
                    "raster.".format(kind))

        def _label(b):
            finfo = _inv._facet_of(rec, b)
            if finfo:
                return _lnk.facet_label(finfo)
            return base if nbands == 1 else "Band {}".format(b)

        def _facet_id(b):
            finfo = _inv._facet_of(rec, b)
            return finfo.get("facet_id") if finfo else b

        if scale:
            _say(messages,
                 "The input carries a Land Facet Density record: stored "
                 "values (scaled by {:,.0f}) are converted back to 0-1 "
                 "proportions before thresholding, so a threshold of 0 means "
                 "'any density at all'.".format(1.0 / scale))

        # ---- the Wildland Blocks -------------------------------------------
        _mh.begin_phases(
            [("Preparing the Wildland Blocks...", 2)]
            + [("Surface {} of {}: {}".format(i, len(band_nos), _label(b)), 4)
               for i, b in enumerate(band_nos, start=1)]
            + [("Writing the termini polygons...", 2)])

        block_geoms, blk_shp, block_cleanup = _prepare_blocks(
            blocks, rast_sr, scratch, messages)
        n_blocks = len(block_geoms)
        blockarr = _rasterize_blocks(gdal, ogr, blk_shp, w, h, gt, proj,
                                     n_blocks)
        n_in = int((blockarr > 0).sum())
        if n_in == 0:
            raise arcpy.ExecuteError(
                "The Wildland Blocks do not overlap the raster. Check that "
                "both cover the same area (and coordinate system).")
        _say(messages,
             "{} Wildland Block{} covering {:,} raster cells.".format(
                 n_blocks, "" if n_blocks == 1 else "s", n_in))

        # ---- find candidate regions, facet by facet ------------------------
        eight = np.ones((3, 3), int)
        rows = []          # candidate termini, filtered per (facet, block)
        geodesic_all = True
        for b in band_nos:
            _mh.phase_advance()
            band = src.GetRasterBand(b)
            a = band.ReadAsArray(0, 0, w, h).astype(np.float64)
            nod = band.GetNoDataValue()
            valid = np.isfinite(a)
            if nod is not None:
                with np.errstate(invalid="ignore"):
                    valid &= (a != float(nod))
            # Also exclude the suite's NODATA_OUT sentinel: a surface that
            # lost its NoData declaration (the geodatabase scenario warned
            # about below) would otherwise drag per-block medians and
            # region statistics toward -9999.
            with np.errstate(invalid="ignore"):
                valid &= (a != float(_tt.NODATA_OUT))
            valid = _mh.mask_band_valid(band, valid)
            if scale:
                over = valid & (a > 1.0 / scale)
                n_over = int(over.sum())
                if n_over:
                    _warn(messages,
                          "{:,} cells of {} hold values above the stored "
                          "maximum and were treated as NoData -- the density "
                          "raster has probably lost its NoData declaration "
                          "in a geodatabase.".format(n_over, _label(b)))
                    valid &= ~over
                del over
                a *= scale

            glab = np.zeros((h, w), np.int32)
            offset = 0
            info = {}       # global label -> dict
            for blk in range(1, n_blocks + 1):
                vm = (blockarr == blk) & valid
                if not vm.any():
                    _warn(messages,
                          "{}: Wildland Block {} contains no valid raster "
                          "cells.".format(_label(b), blk))
                    continue
                if threshold_mode == MODE_WHOLE_BLOCK:
                    # No thresholding, labelling or clipping: the block
                    # polygon itself IS the terminus. Appended straight to
                    # ``rows`` (bypassing the ``info``/``glab`` machinery
                    # below, which exists only to polygonize thresholded
                    # cells) so it survives the relative-size filter
                    # trivially -- it is always its own group's only, and
                    # therefore largest, region.
                    geom = block_geoms[blk - 1]
                    area, was_geodesic = _geodesic_area_m2(geom)
                    geodesic_all &= was_geodesic
                    if area <= 0:
                        continue
                    rows.append({
                        # Sentinel, not None/NaN: arcpy's shapefile cursor
                        # rejects BOTH None and NaN into a DOUBLE field with
                        # "The field is not nullable" (verified 2026-08-09;
                        # DBF has no true NULL for numerics) -- -9999 is this
                        # suite's existing not-applicable convention.
                        "category": _label(b), "category_id": _facet_id(b),
                        "band": b, "block": blk, "thr": NO_THRESHOLD,
                        "cells": int(vm.sum()), "mean": float(a[vm].mean()),
                        "max": float(a[vm].max()),
                        "geom": geom, "area": area,
                    })
                    continue
                if threshold_mode == MODE_MEDIAN:
                    thr = float(np.median(a[vm]))
                else:
                    thr = thr_fixed
                sel = vm & (a > thr)
                n_sel = int(sel.sum())
                if n_sel == 0:
                    _warn(messages,
                          "{}: no cells above the threshold ({:g}) in "
                          "Wildland Block {}.".format(_label(b), thr, blk))
                    continue
                lab, n_lab = ndimage.label(sel, structure=eight)
                if not n_lab:
                    continue
                idx = np.arange(1, n_lab + 1)
                counts = np.bincount(lab.ravel(), minlength=n_lab + 1)[1:]
                means = ndimage.mean(a, labels=lab, index=idx)
                maxes = ndimage.maximum(a, labels=lab, index=idx)
                glab[sel] = lab[sel] + offset
                for k in range(n_lab):
                    info[offset + k + 1] = {
                        "category": _label(b), "category_id": _facet_id(b),
                        "band": b, "block": blk, "thr": thr,
                        "cells": int(counts[k]), "mean": float(means[k]),
                        "max": float(maxes[k]),
                    }
                offset += n_lab
                if threshold_mode == MODE_MEDIAN:
                    _say(messages,
                         "{}: Wildland Block {} median threshold {:g}; "
                         "{:,} cells above it in {} region{}.".format(
                             _label(b), blk, thr, n_sel, n_lab,
                             "" if n_lab == 1 else "s"))
                del lab
            del a, valid

            if not info:
                del glab
                continue

            # Vectorize this facet's regions and clip each to its block, as
            # the original did (a boundary cell's square can overhang the
            # block polygon).
            geoms = _polygons_from_labels(gdal, ogr, glab, gt, proj, rast_sr)
            del glab
            for lbl, meta in info.items():
                g = geoms.get(lbl)
                if g is None:
                    continue
                try:
                    clip = g.intersect(block_geoms[meta["block"] - 1], 4)
                except Exception:                                # noqa: BLE001
                    clip = g
                if clip is None:
                    continue
                area, was_geodesic = _geodesic_area_m2(clip)
                geodesic_all &= was_geodesic
                if area <= 0:
                    continue
                meta["geom"] = clip
                meta["area"] = area
                rows.append(meta)
            geoms = None

        # ---- relative-size filter, per facet per block ---------------------
        _mh.phase_advance()
        groups = {}
        for rrow in rows:
            groups.setdefault((rrow["band"], rrow["block"]), []).append(rrow)
        kept = []
        for (bnd, blk), grp in sorted(groups.items()):
            biggest = max(r["area"] for r in grp)
            # Cross-multiplied (area*100 >= biggest*pct), NOT
            # "area >= biggest*pct/100.0": a region that IS the group's
            # biggest must always survive at pct=100, but multiplying then
            # dividing by 100.0 can round-trip to a value a hair above the
            # original area, dropping it. Cross-multiplying puts area and
            # biggest through the same single rounding, so area==biggest
            # compares equal regardless of floating-point noise.
            threshold = biggest * pct
            surv = [r for r in grp if r["area"] * 100.0 >= threshold]
            for r in surv:
                r["pctmax"] = 100.0 * r["area"] / biggest if biggest else 0.0
            kept.extend(surv)
            _say(messages,
                 "{}, Wildland Block {}: {} of {} region{} kept (>= {:g}% "
                 "of the largest, {:,.1f} sq m).".format(
                     grp[0]["category"], blk, len(surv), len(grp),
                     "" if len(grp) == 1 else "s", pct, biggest))
        if not kept:
            if threshold_mode == MODE_WHOLE_BLOCK:
                raise arcpy.ExecuteError(
                    "No termini polygons were written. Check that the "
                    "Wildland Blocks overlap the analysis surface and that "
                    "each block polygon has a positive area.")
            raise arcpy.ExecuteError(
                "No termini polygons were found in any Wildland Block. "
                "Nothing exceeded the threshold -- check that the analysis "
                "surface covers the blocks and that the threshold ({}) suits "
                "it.".format("per-block median"
                             if threshold_mode == MODE_MEDIAN
                             else "{:g}".format(thr_fixed)))
        if not geodesic_all:
            _warn(messages,
                  "Areas could not be measured geodesically (unknown "
                  "coordinate system?) and are planar, in the raster's "
                  "linear units squared.")

        # ---- per-facet Wildland Block coverage ------------------------------
        # A corridor needs a terminus at BOTH ends: a facet with termini in
        # only some blocks (no eligible cells there, or every region clipped
        # away) cannot anchor a corridor to the missing block. Say so
        # explicitly, and carry the per-block counts in the linkage record so
        # the Corridor Analysis Data Report can show them.
        coverage = {b: {blk: 0 for blk in range(1, n_blocks + 1)}
                    for b in band_nos}
        for r in kept:
            coverage[r["band"]][r["block"]] += 1
        for b in band_nos:
            have = [blk for blk in range(1, n_blocks + 1) if coverage[b][blk]]
            missing = [blk for blk in range(1, n_blocks + 1)
                       if not coverage[b][blk]]
            if not have:
                _warn(messages,
                      "{}: NO termini polygons in any Wildland Block.".format(
                          _label(b)))
            elif missing:
                _warn(messages,
                      "{}: termini in Wildland Block{} {} only -- NONE in "
                      "Block{} {}. A corridor for this facet has nothing to "
                      "connect to in the missing block{}.".format(
                          _label(b),
                          "" if len(have) == 1 else "s",
                          ", ".join(str(k) for k in have),
                          "" if len(missing) == 1 else "s",
                          ", ".join(str(k) for k in missing),
                          "" if len(missing) == 1 else "s"))

        # ---- write ----------------------------------------------------------
        kept.sort(key=lambda r: (r["band"], r["block"], -r["area"]))
        out_ws, out_name = os.path.split(str(out_fc))
        arcpy.management.CreateFeatureclass(out_ws, out_name, "POLYGON",
                                            spatial_reference=rast_sr)
        fields = [("Category", "TEXT", 64), ("CategoryID", "LONG", None),
                  ("Block", "LONG", None), ("Terminus", "LONG", None),
                  ("Area_SqM", "DOUBLE", None), ("Cells", "LONG", None),
                  ("PctOfMax", "DOUBLE", None), ("MeanValue", "DOUBLE", None),
                  ("MaxValue", "DOUBLE", None), ("Threshold", "DOUBLE", None)]
        # one AddFields call, not one AddField per field (standing rule)
        arcpy.management.AddFields(out_fc, [
            [nm, tp, "", ln or "", "", ""] for nm, tp, ln in fields])

        with arcpy.da.InsertCursor(
                out_fc, ["SHAPE@", "Category", "CategoryID", "Block",
                         "Terminus", "Area_SqM", "Cells", "PctOfMax",
                         "MeanValue", "MaxValue", "Threshold"]) as cur:
            for i, r in enumerate(kept, start=1):
                cur.insertRow([r["geom"], r["category"],
                               int(r["category_id"] or 0), r["block"], i,
                               r["area"], r["cells"], r["pctmax"], r["mean"],
                               r["max"], r["thr"]])
                if i % 100 == 0:
                    _mh.phase_step(i, len(kept))
        _say(messages, "{:,} termini polygon{} written.".format(
            len(kept), "" if len(kept) == 1 else "s"))

        # ---- metadata + linkage record --------------------------------------
        facet_names = sorted({r["category"] for r in kept})
        if threshold_mode == MODE_WHOLE_BLOCK:
            mode_text = "no threshold -- each block polygon used whole"
        elif threshold_mode == MODE_MEDIAN:
            mode_text = ("value > median of the in-block cells, computed "
                         "per Wildland Block")
        else:
            mode_text = "value > {:g}".format(thr_fixed)
        try:
            _tm.set_entity_and_fields(
                out_fc,
                entity=("One polygon per corridor terminus: a concentration "
                        "of the analysis surface inside a Wildland Block, "
                        "at least {:g}% of the size of the largest "
                        "concentration in its block.".format(pct)),
                fields=[
                    {"name": "Category", "description":
                        "The category this terminus belongs to -- a land "
                        "facet, a species habitat-suitability surface, or "
                        "any other analysis surface. One feature class "
                        "holds every category's termini; group or filter "
                        "by this field to recover the per-category sets."},
                    {"name": "CategoryID", "description":
                        "The category's id from the source raster's "
                        "linkage record, or the band number when the "
                        "source carries none."},
                    {"name": "Block", "description":
                        "Which Wildland Block feature contains this terminus "
                        "(1-based, in the block layer's feature order)."},
                    {"name": "Terminus", "description":
                        "Sequential terminus number across the whole "
                        "output."},
                    {"name": "Area_SqM", "description":
                        "The terminus polygon's area in square meters "
                        "(geodesic), measured after clipping to the block "
                        "boundary."},
                    {"name": "Cells", "description":
                        "How many raster cells exceeded the threshold in "
                        "this region (before clipping to the block "
                        "boundary)."},
                    {"name": "PctOfMax", "description":
                        "This polygon's area as a percentage of the largest "
                        "terminus polygon in the same Wildland Block (for "
                        "the same category). The retention rule keeps "
                        "polygons at or above the size threshold."},
                    {"name": "MeanValue", "description":
                        "Mean of the analysis surface over the region's "
                        "cells (density rasters: as a 0-1 proportion)."},
                    {"name": "MaxValue", "description":
                        "Maximum of the analysis surface over the region's "
                        "cells (density rasters: as a 0-1 proportion)."},
                    {"name": "Threshold", "description":
                        "The threshold this region had to exceed -- the "
                        "fixed value, or the block's own median in "
                        "median mode. -9999 (not a real threshold value) "
                        "when the whole Wildland Block polygon was used "
                        "as its own terminus, since no threshold was "
                        "applied."},
                ],
                messages=messages)
            from arcpy import metadata as md
            m = md.Metadata(out_fc)
            m.title = os.path.basename(str(out_fc))
            m.tags = ("termini, corridor, wildland blocks, land facet, "
                      "connectivity, Corridor Designer, Land Facet Corridor "
                      "Designer, Jenness Enterprises")
            m.summary = (
                "Created by Identify Termini Polygons from {src}: cells with "
                "{mode}, aggregated into regions (edge- or corner-touching "
                "cells connect), keeping regions at least {pct:g}% of the "
                "size of the largest region in their Wildland Block "
                "({blocks}).").format(
                    src=str(in_raster), mode=mode_text, pct=pct,
                    blocks=str(blocks))
            m.description = (
                "Corridor termini polygons -- the concentrations of a "
                "quality surface (a species habitat-suitability surface, a "
                "land facet, or facet diversity) inside each Wildland "
                "Block that least-cost corridors run between. Regions of "
                "the analysis surface above the threshold are kept when "
                "they reach the relative size threshold against the "
                "largest region in their block, each block analyzed "
                "separately. One feature class holds every category's "
                "termini (the Category field). A modernized, generalized "
                "port of the Land Facet Corridor Designer's Identify "
                "Termini Polygons tool. " + _CD_CITATION)
            m.credits = ("Tool by Jeff Jenness, Brian Brost and "
                         "Paul Beier. Method: " + _CD_CITATION)
            m.save()
        except Exception as e:                                   # noqa: BLE001
            _warn(messages, "Could not write output metadata: {}".format(e))

        # Every ANALYZED band appears in the record -- including those that
        # produced no termini at all -- with per-block counts, so the Data
        # Report can flag facets that cannot anchor a corridor.
        facets_meta = []
        for b in band_nos:
            facets_meta.append({
                "category_id": _facet_id(b), "label": _label(b), "band": b,
                "n_termini": sum(coverage[b].values()),
                "by_block": [{"block": blk, "n_termini": coverage[b][blk]}
                             for blk in range(1, n_blocks + 1)],
            })
        try:
            blocks_path = arcpy.Describe(blocks).catalogPath
        except Exception:                                        # noqa: BLE001
            blocks_path = str(blocks)
        _lnk.write(out_fc, _lnk.termini_record(
            analysis_id=analysis_id, source_raster=in_raster,
            blocks=blocks_path, threshold_mode=threshold_mode,
            relative_size_pct=pct, source_analysis_id=src_analysis,
            categories=facets_meta,
            threshold_value=(None if threshold_mode in (MODE_MEDIAN,
                                                        MODE_WHOLE_BLOCK)
                             else thr_fixed),
            n_blocks=n_blocks), messages)

        out_lyrx = write_layer_file(out_fc, facet_names, messages)
        _mh.end_phases()
        return out_lyrx
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:                                    # noqa: BLE001
                pass
        for junk in block_cleanup:
            try:
                arcpy.management.Delete(junk)
            except Exception:                                    # noqa: BLE001
                pass
