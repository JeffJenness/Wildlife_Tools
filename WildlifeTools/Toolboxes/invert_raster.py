# -*- coding: utf-8 -*-
"""Invert Raster -- turn a "high is good" surface into a least-cost surface.

A modernized port of the Land Facet Corridor Designer's ArcMap "Invert Raster"
tool (``cmdInvert.cls`` / ``TopoCor_Functions.InvertRaster``; manual p.47).
Least-cost corridor tools treat LOW values as easy to cross, but the surfaces
this family produces measure quality the other way up -- more diversity, more
density = better. The fix is the numeric reciprocal:

    out = 1 / (x + c)

with ``c`` a small constant (default 0.1, the manual's strong recommendation
and the exact diversity-corridor resistance formula, 1/(H' + 0.1), manual
p.12) that stops a zero cell from dividing to infinity. A cell where x + c is
exactly zero becomes NoData, as in the original. The manual aimed this tool at
the Shannon's Index raster; inverting Land Facet Density for direct least-cost
use is this suite's extension, and the tool handles either.

A SECOND METHOD, ``100 - x`` (METHOD_LINEAR, added 2026-08-15 at Jeff's
request), reproduces the ArcMap CorridorDesigner CorridorModel tool's
internal suitability-to-cost conversion for 0-100 habitat suitability
models -- cost = 100 - HSM, with a resulting cost of exactly 0 bumped to 1
so perfect habitat is never free to cross, exactly as that tool's
``Minus_sa``/``Con_sa`` pair behaved. The two methods encode different
assumptions: the reciprocal punishes poor habitat far more steeply than the
linear complement, so corridors from a reciprocal cost surface hug good
habitat more tightly. Use linear when ArcMap-comparable corridors are
wanted from an HSM; the reciprocal remains the default and the right choice
for density, diversity, and other proportion/index surfaces.

THE DIALOG FOLLOWS THE INPUT'S SHAPE (Jeff, 2026-08-07, final form). A
single-band input (a diversity raster, or one band of a density raster given
as ``dens.tif\\Band_3``) asks for a plain output raster path, like the
original. A MULTI-BAND input -- this suite's own Land Facet Density raster --
asks for a workspace and a name prefix instead, and fans out to ONE
SINGLE-BAND cost raster per band in a single run, each named from the facet
recorded in the density raster's metadata. Terminal least-cost consumers
expect single-band, so a multi-band inverted raster is never produced.

STORED DENSITY VALUES ARE UNSCALED FIRST. Density rasters store 0-1
proportions as integers scaled by 10,000; adding 0.1 to a raw stored value
would make the constant meaningless and the cost curve unrecognizable. When
the input (or, for a band path, its parent) carries a density linkage record,
values are converted back to true proportions BEFORE the constant is added,
reproducing the published curve exactly.

EVERY OUTPUT IS STAMPED with an ``inverted`` linkage record (see
``land_facet_linkage.inverted_record``) so the product knows its place in the
Land Facet chain: what it was inverted from, which facet's band, the constant,
whether unscaling was applied, and the analysis id chained from the source.

CAUTION KEPT FROM THE ORIGINAL'S SILENCE: 1/(x + c) with x < -c produces
NEGATIVE costs, which least-cost tools reject; the original wrote them without
comment. Densities and diversity indices are never negative, so the case is
unreachable from this chain's own outputs -- but the tool inverts any raster,
so it still writes such values faithfully, counting and warning.
"""
import os
import re

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import topographic_tools as _tt
import land_facet_linkage as _lnk
import land_facet_mahalanobis as _lfm

DEFAULT_CONSTANT = 0.1

# Inversion methods. RECIPROCAL is the Land Facet original (manual p.12,
# 1/(H' + 0.1)); LINEAR is the CorridorDesigner CorridorModel's internal
# cost conversion (cost = 100 - HSM, zero cost bumped to 1), offered so
# corridors can be built ArcMap-comparable from a 0-100 suitability model.
# The two encode different assumptions: the reciprocal punishes poor
# habitat far more steeply than the linear complement.
METHOD_RECIPROCAL = "reciprocal"
METHOD_LINEAR = "linear"
FORMULA_LINEAR = "100 - x"

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org. Land Facet Corridor Designer: Beier, P., and "
    "B. Brost. 2010, Conservation Biology 24:701-710; Brost, B. M., and P. "
    "Beier. 2012, Ecological Applications 22:87-103; Jenness, J., B. Brost, "
    "and P. Beier. 2013, http://www.jennessent.com/arcgis/land_facets.htm.")

_BAND_RE = re.compile(r"^band[_ ]?(\d+)$", re.IGNORECASE)


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def source_density_record(in_raster):
    """The density linkage record governing ``in_raster``, or None.

    For a band path (``<parent>\\Band_<n>``) the record lives on the PARENT
    dataset; returns ``(record, band_number)`` with band_number None when the
    input is the whole dataset.
    """
    path = str(in_raster)
    m = _BAND_RE.match(os.path.basename(path))
    if m:
        # The parsed band number is returned even when the parent has no
        # density record -- the linkage record otherwise stamped "band": 1
        # for ANY record-less Band_<n> path.
        return _lnk.read(os.path.dirname(path), _lnk.DENSITY), int(m.group(1))
    return _lnk.read(path, _lnk.DENSITY), None


def _facet_of(rec, band_no):
    """The record's band entry for ``band_no`` (or None)."""
    for b in ((rec or {}).get("bands") or []):
        if b.get("band") == band_no:
            return b
    return None


def _invert_values(a, nod, scale, c, label, messages, vmask=None,
                   method=METHOD_RECIPROCAL):
    """The shared core: mask, unscale, then invert by ``method`` --
    reciprocal ``1/(x + c)`` (the Land Facet original) or linear
    ``100 - x`` (the ArcMap CorridorModel conversion; the constant is not
    used, and a resulting cost of exactly 0 is bumped to 1 as that tool
    did, so perfect habitat still carries a small positive cost).

    Returns ``(out, valid)`` -- a float32 grid with NODATA_OUT where invalid,
    and the validity mask -- after reporting zero/negative diagnostics.
    ``vmask`` is the band's per-pixel validity (mahalanobis.
    band_validity_mask) for sources whose NoData lives only in the mask
    band (gdb GRID phantom-fill trap).
    """
    a = np.asarray(a, dtype=np.float32)
    valid = np.isfinite(a)
    if vmask is not None:
        valid &= vmask
    if nod is not None:
        with np.errstate(invalid="ignore"):
            valid &= (a != np.float32(nod))
    with np.errstate(invalid="ignore"):
        valid &= (a != np.float32(_tt.NODATA_OUT))
    if scale:
        # Stored densities cannot exceed 1/scale (10,000); anything larger is
        # a NoData sentinel whose declaration was lost (a file geodatabase may
        # not preserve it on a multi-band raster).
        over = valid & (a > np.float32(1.0 / scale))
        n_over = int(over.sum())
        if n_over:
            _warn(messages,
                  "{:,} cells of {} hold values above the stored maximum and "
                  "were treated as NoData -- the density raster has probably "
                  "lost its NoData declaration in a geodatabase.".format(
                      n_over, label))
            valid &= ~over
        del over
        a *= np.float32(scale)

    if method == METHOD_LINEAR:
        out = np.full(a.shape, np.float32(_tt.NODATA_OUT))
        res = np.float32(100.0) - a[valid]
        n_neg = int((res < 0).sum())
        n_zero = int((res == np.float32(0.0)).sum())
        res[res == np.float32(0.0)] = np.float32(1.0)
        out[valid] = res
        if valid.any() and float(a[valid].max()) <= 1.5:
            _warn(messages,
                  "The valid values of {} never exceed 1.5 -- this does not "
                  "look like a 0-100 suitability model, so 100 - x will "
                  "produce a nearly flat cost surface (~99-100 everywhere). "
                  "The linear method is meant for 0-100 habitat suitability "
                  "models; use the reciprocal for proportion or index "
                  "surfaces.".format(label))
        if n_zero:
            _say(messages,
                 "{:,} cells of {} scored a perfect 100, giving cost 0; "
                 "they were set to cost 1 (as the original ArcMap "
                 "CorridorModel did) so travel is never free.".format(
                     n_zero, label))
        if n_neg:
            _warn(messages,
                  "{:,} cells of {} exceed 100, so their linear costs are "
                  "NEGATIVE. Least-cost tools reject negative costs; the "
                  "linear method expects a 0-100 suitability model.".format(
                      n_neg, label))
        return out, valid

    if c:
        a += np.float32(c)
    zero = valid & (a == np.float32(0.0))
    n_zero = int(zero.sum())
    n_neg = int((valid & (a < np.float32(0.0))).sum())
    valid &= ~zero
    del zero

    out = np.full(a.shape, np.float32(_tt.NODATA_OUT))
    with np.errstate(divide="ignore", invalid="ignore"):
        out[valid] = np.float32(1.0) / a[valid]

    if n_zero:
        _warn(messages,
              "{:,} cells of {} were exactly zero after the constant and "
              "became NoData (division by zero).".format(n_zero, label))
    if n_neg:
        _warn(messages,
              "{:,} cells of {} were NEGATIVE after the constant, so their "
              "inverted values are negative. Least-cost tools reject negative "
              "costs; add a larger constant or check the input.".format(
                  n_neg, label))
    return out, valid


def _formula_text(method, c):
    """The human-readable formula for messages and metadata."""
    if method == METHOD_LINEAR:
        return FORMULA_LINEAR
    return "1 / (x{})".format(" + {:g}".format(c) if c else "")


def _deliver(gdal, out, valid, gt, proj, width_px, height_px, out_path,
             in_raster, label, c, scale, scratch, messages,
             method=METHOD_RECIPROCAL):
    """Write ``out`` to a scratch GeoTIFF with self-computed statistics and
    deliver it (CopyRaster, Esri metadata, symmetry with every other tool)."""
    import arcpy

    tmp = arcpy.CreateUniqueName("invert.tif", scratch)
    ds_out, oband = _tt._make_output(gdal, tmp, gt, proj, width_px, height_px)
    stats = _mh._RasterStats()
    slab = max(1, int(4_000_000 // max(1, width_px)))
    for r0 in range(0, height_px, slab):
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

    unscale_note = (
        " Stored values were converted to 0-1 proportions (x {:g}) "
        "first.".format(scale) if scale else "")
    if method == METHOD_LINEAR:
        desc_core = (
            "Numerically inverted raster -- the linear complement 100 - x of "
            "a 0-100 habitat suitability model, so that good habitat carries "
            "LOW cost and the raster can be used directly as the resistance "
            "surface in least-cost corridor modelling. A cost of exactly 0 "
            "(perfect habitat) is set to 1, exactly as the original ArcMap "
            "CorridorDesigner CorridorModel tool converted suitability to "
            "cost internally.")
    else:
        desc_core = (
            "Numerically inverted raster -- the reciprocal 1/(x + c) of a "
            "surface in which high values mean good habitat (for example a "
            "land facet density band or a diversity index), so that good "
            "ground carries LOW cost and can be used directly as the "
            "resistance surface in least-cost corridor modelling. The small "
            "constant prevents division by zero.")
    _tt._deliver_output(
        tmp, out_path, out_stats, messages,
        title=os.path.basename(str(out_path)),
        summary=(
            "Created by Invert Raster from {src} ({lab}): each cell is "
            "{f}, turning a surface where high values are good into a "
            "least-cost surface where they are cheap to cross.{un}").format(
                src=str(in_raster), lab=label,
                f=_formula_text(method, c), un=unscale_note),
        description=(
            "{core} A modernized port of the "
            "Land Facet Corridor Designer's Invert Raster tool. {cite}").format(
                core=desc_core, cite=_CD_CITATION),
        tags=("invert, reciprocal, cost surface, resistance, least-cost, "
              "corridor, connectivity, Corridor Designer, Land Facet, "
              "Jenness Enterprises"),
        citation=_CD_CITATION)


def run_invert(in_raster, out_raster=None, out_workspace=None,
               out_prefix="Inv", add_constant=DEFAULT_CONSTANT,
               facet_values=None, method=METHOD_RECIPROCAL, messages=None):
    """Invert ``in_raster`` per cell. Returns the written paths.

    ``method`` is METHOD_RECIPROCAL (``1/(x + c)``, the Land Facet
    original and the default) or METHOD_LINEAR (``100 - x``, the ArcMap
    CorridorDesigner CorridorModel's internal suitability-to-cost
    conversion, for 0-100 habitat suitability models; the constant is
    ignored and a cost of exactly 0 is bumped to 1 as that tool did).
    A single-band input needs ``out_raster``; a multi-band input needs
    ``out_workspace`` (+ ``out_prefix``) and fans out to one single-band
    raster per band. ``add_constant`` of None means no constant (the
    original's unchecked "Add" box). ``facet_values`` (multi-band only)
    restricts the run: facet ids when the input carries a density record,
    plain band numbers otherwise.
    """
    import arcpy

    method = str(method or METHOD_RECIPROCAL).strip().lower()
    method = METHOD_LINEAR if method.startswith("lin") else METHOD_RECIPROCAL
    linear = method == METHOD_LINEAR
    c = 0.0 if linear else (
        float(add_constant) if add_constant is not None else 0.0)
    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()

    rec, path_band = source_density_record(in_raster)
    scale = (rec or {}).get("scale_factor")
    # The analysis id chains from the source when it carries ANY linkage
    # record -- a density raster, but equally a Diversity Indices surface (the
    # published interspersion-corridor input). The density-specific record
    # above additionally supplies the scale factor and band-facet table.
    if rec is None:
        p = str(in_raster)
        m = _BAND_RE.match(os.path.basename(p))
        chain_rec = _lnk.read(os.path.dirname(p) if m else p, None)
    else:
        chain_rec = rec
    analysis_id = (chain_rec or {}).get("analysis_id") or _lnk.new_analysis_id()
    src_analysis = (chain_rec or {}).get("analysis_id")

    if (chain_rec or {}).get("record") == _lnk.MAHALANOBIS:
        out_type = chain_rec.get("out_type")
        if out_type in (_mh.OUT_D2, _mh.OUT_D):
            _warn(messages,
                  "The input is a Land Facet Mahalanobis distance raster "
                  "({}). It is ALREADY a valid cost surface -- low values "
                  "mean close to the facet's typical conditions, which is "
                  "the 'cheap to cross' sense a corridor needs -- so "
                  "inverting it REVERSES that meaning: the least typical "
                  "cells would become the cheapest to cross. Feed it "
                  "directly to Least-Cost Corridor instead, without "
                  "inverting.".format(out_type))
        elif out_type == _mh.OUT_PVALUE:
            _say(messages,
                 "The input is a Land Facet Mahalanobis p-value raster: "
                 "high values mean typical (like a density proportion), so "
                 "inverting it to a cost surface -- unlike a D or D2 "
                 "distance raster -- is the correct direction.")

    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    try:
        nbands = src.RasterCount
        width_px, height_px = src.RasterXSize, src.RasterYSize
        if width_px < 1 or height_px < 1:
            raise arcpy.ExecuteError("The input raster is empty.")
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        base = _lfm.safe_name(
            os.path.splitext(os.path.basename(str(in_raster)))[0])

        if scale:
            _say(messages,
                 "The input carries a Land Facet Density record: stored values "
                 "(scaled by {:,.0f}) are converted back to 0-1 "
                 "proportions first.".format(1.0 / scale))
        if linear and scale:
            _warn(messages,
                  "The linear method (100 - x) is meant for 0-100 habitat "
                  "suitability models, but this input is a density raster "
                  "of 0-1 proportions -- the result will be a nearly flat "
                  "cost surface (~99-100 everywhere). Use the reciprocal "
                  "method for density and diversity surfaces.")
        if not linear and not c:
            _warn(messages,
                  "No constant is being added before inverting, so any cell "
                  "whose value is exactly zero becomes NoData in the output. "
                  "The manual strongly recommends a small constant (0.1).")

        # ---- single band: one plain output raster --------------------------
        if nbands == 1:
            if not out_raster:
                raise arcpy.ExecuteError(
                    "The input is single band; supply the output raster name.")
            out_raster = _mh.resolve_raster_output(out_raster, messages)
            band_no = path_band or 1
            finfo = _facet_of(rec, band_no)
            label = (_lnk.facet_label(finfo) if finfo else base)
            _mh.begin_phases([("Reading the input raster...", 2),
                              ("Inverting {}...".format(label), 3),
                              ("Saving the output raster...", 2)])
            band = src.GetRasterBand(1)
            a = band.ReadAsArray(0, 0, width_px, height_px)
            nod = band.GetNoDataValue()
            _mh.phase_advance()
            _say(messages, "Inverting {}: out = {}.".format(
                label, _formula_text(method, c)))
            out, valid = _invert_values(a, nod, scale, c, label, messages,
                                        vmask=_mh.band_validity_mask(band),
                                        method=method)
            del a
            _mh.phase_advance()
            _deliver(gdal, out, valid, gt, proj, width_px, height_px,
                     out_raster, in_raster, label, c, scale, scratch, messages,
                     method=method)
            del out, valid
            _lnk.write(out_raster, _lnk.inverted_record(
                analysis_id=analysis_id, source_raster=in_raster,
                constant=(None if linear else c), scale_applied=bool(scale),
                source_analysis_id=src_analysis,
                facet=finfo, band=band_no,
                formula=(FORMULA_LINEAR if linear else None)), messages)
            _mh.end_phases()
            return [out_raster]

        # ---- multi-band: one single-band raster per band --------------------
        if not out_workspace:
            raise arcpy.ExecuteError(
                "The input has {} bands; supply the output workspace (and "
                "optionally a name prefix) so each band can be written as its "
                "own single-band cost raster.".format(nbands))

        band_nos = list(range(1, nbands + 1))
        if facet_values:
            want = set()
            for v in facet_values:
                try:
                    want.add(int(v))
                except (TypeError, ValueError):
                    pass
            if rec:
                band_nos = [b for b in band_nos
                            if (_facet_of(rec, b) or {}).get("facet_id") in want]
                kind = "facet"
                have = {(_facet_of(rec, b) or {}).get("facet_id")
                        for b in band_nos}
            else:
                band_nos = [b for b in band_nos if b in want]
                kind = "band"
                have = set(band_nos)
            missing = want - have
            if missing:
                _warn(messages,
                      "Requested {} value{} {} do{} not occur in the raster "
                      "and {} skipped.".format(
                          kind, "" if len(missing) == 1 else "s",
                          ", ".join(str(m) for m in sorted(missing,
                                                           key=str)),
                          "es" if len(missing) == 1 else "",
                          "was" if len(missing) == 1 else "were"))
            if not band_nos:
                raise arcpy.ExecuteError(
                    "None of the requested {} values occur in the "
                    "raster.".format(kind))

        def _label(b):
            finfo = _facet_of(rec, b)
            return _lnk.facet_label(finfo) if finfo else "Band {}".format(b)

        def _name(b):
            finfo = _facet_of(rec, b)
            if finfo:
                return "{}_{}".format(out_prefix,
                                      _lfm.safe_name(_lnk.facet_label(finfo)))
            return "{}_{}_Band_{}".format(out_prefix, base, b)

        _say(messages, "Inverting {} band{}: out = {}.".format(
            len(band_nos), "" if len(band_nos) == 1 else "s",
            _formula_text(method, c)))
        _mh.begin_phases(
            [("Reading the input raster...", 1)]
            + [("Inverting {} of {}: {}".format(i, len(band_nos), _label(b)), 4)
               for i, b in enumerate(band_nos, start=1)])

        used = set()
        written = []
        for i, b in enumerate(band_nos, start=1):
            _mh.phase_advance()
            band = src.GetRasterBand(b)
            a = band.ReadAsArray(0, 0, width_px, height_px)
            nod = band.GetNoDataValue()
            out, valid = _invert_values(a, nod, scale, c, _label(b), messages,
                                        vmask=_mh.band_validity_mask(band),
                                        method=method)
            del a
            name = _name(b)
            if name.lower() in used:
                # Long labels can truncate to the same safe token; the band
                # number restores distinct names.
                name = "{}_B{}".format(name, b)
            used.add(name.lower())
            out_path = _mh.resolve_raster_output(
                os.path.join(out_workspace, name), messages)
            _deliver(gdal, out, valid, gt, proj, width_px, height_px,
                     out_path, in_raster, _label(b), c, scale, scratch,
                     messages, method=method)
            del out, valid
            _lnk.write(out_path, _lnk.inverted_record(
                analysis_id=analysis_id, source_raster=in_raster,
                constant=(None if linear else c), scale_applied=bool(scale),
                source_analysis_id=src_analysis,
                facet=_facet_of(rec, b), band=b,
                formula=(FORMULA_LINEAR if linear else None)), messages)
            written.append(out_path)
            _say(messages, "Wrote {}".format(out_path))

        _mh.end_phases()
        _say(messages, "{} of {} inverted raster{} written.".format(
            len(written), len(band_nos), "" if len(band_nos) == 1 else "s"))
        return written
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass
