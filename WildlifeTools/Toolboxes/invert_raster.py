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
the Shannon's Index raster; inverting a Land Facet Density raster for direct
least-cost use is this suite's extension, and the tool handles either.

TWO WAYS THIS VERSION DEPARTS FROM THE ORIGINAL, both deliberate (Jeff,
2026-08-07):

MULTI-BAND INPUT -> ONE SINGLE-BAND RASTER PER BAND. The original inverted one
chosen band per run. A Land Facet Density raster carries a band per facet, and
its inversions are terminal cost surfaces for CorridorDesigner / Cost Distance,
which expect single-band -- the same follow-the-consumer reasoning as the Land
Facet Mahalanobis outputs. Each output is named from the facet label in the
density raster's linkage record when it has one.

STORED DENSITY VALUES ARE UNSCALED FIRST. Density rasters store integers scaled
by 10,000. Adding 0.1 to a 0..10000 integer bears no resemblance to adding 0.1
to a 0..1 proportion -- the constant would be meaningless and the cost curve
unrecognizable. When the input carries a density linkage record, each band is
multiplied by its recorded scale_factor before the constant is added, exactly
reproducing the original's curve; the run messages report it.

CAUTION KEPT FROM THE ORIGINAL'S SILENCE: 1/(x + c) with x < -c produces
NEGATIVE costs, which least-cost tools reject; the original wrote them without
comment. This version still writes them (any raster may be inverted) but counts
and warns.
"""
import os

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import topographic_tools as _tt
import land_facet_linkage as _lnk
import land_facet_mahalanobis as _lfm

DEFAULT_CONSTANT = 0.1

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org. Land Facet Corridor Designer: Beier, P., and "
    "B. Brost. 2010, Conservation Biology 24:701-710; Brost, B. M., and P. "
    "Beier. 2012, Ecological Applications 22:87-103; Jenness, J., B. Brost, "
    "and P. Beier. 2013, http://www.jennessent.com/arcgis/land_facets.htm.")


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def run_invert(in_raster, out_workspace, out_prefix="Inv",
               add_constant=DEFAULT_CONSTANT, facet_values=None,
               messages=None):
    """Invert every band of ``in_raster`` into its own single-band cost raster.

    Returns the list of written raster paths. ``add_constant`` of None means no
    constant (the original's unchecked "Add" box). ``facet_values`` restricts
    the run: facet ids when the input carries a density linkage record, plain
    band numbers otherwise.
    """
    import arcpy

    c = float(add_constant) if add_constant is not None else 0.0
    scratch = arcpy.env.scratchFolder

    rec = _lnk.read(in_raster, _lnk.DENSITY)
    scale = (rec or {}).get("scale_factor")
    band_info = {b.get("band"): b for b in ((rec or {}).get("bands") or [])
                 if b.get("band")}

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

        # ---- which bands ------------------------------------------------
        bands = list(range(1, nbands + 1))
        if facet_values:
            want = set()
            for v in facet_values:
                try:
                    want.add(int(v))
                except (TypeError, ValueError):
                    pass
            if rec:
                bands = [b for b in bands
                         if band_info.get(b, {}).get("facet_id") in want]
                kind = "facet"
            else:
                bands = [b for b in bands if b in want]
                kind = "band"
            if not bands:
                raise arcpy.ExecuteError(
                    "None of the requested {} values occur in the "
                    "raster.".format(kind))

        if rec:
            _say(messages,
                 "Read the land facet density linkage record: values are "
                 "stored scaled by {:,.0f} and will be converted back to 0-1 "
                 "proportions before the constant is added, so the cost curve "
                 "matches the original 1/(x + c) on true proportions.".format(
                     1.0 / scale if scale else 1.0))
        _say(messages, "Inverting {} band{}: out = 1 / (x{}).".format(
            len(bands), "" if len(bands) == 1 else "s",
            " + {:g}".format(c) if c else ""))
        if not c:
            _warn(messages,
                  "No constant is being added before inverting, so any cell "
                  "whose value is exactly zero becomes NoData in the output. "
                  "The manual strongly recommends a small constant (0.1).")

        def _label(b):
            info = band_info.get(b)
            if info:
                return _lnk.facet_label(info)
            return "Band {}".format(b) if nbands > 1 else base

        def _name(b):
            info = band_info.get(b)
            if info:
                return "{}_{}".format(out_prefix, _lfm.safe_name(
                    _lnk.facet_label(info)))
            if nbands > 1:
                return "{}_{}_Band_{}".format(out_prefix, base, b)
            return "{}_{}".format(out_prefix, base)

        _mh.begin_phases(
            [("Reading the input raster...", 1)]
            + [("Inverting {} of {}: {}".format(i, len(bands), _label(b)), 4)
               for i, b in enumerate(bands, start=1)])

        prefix_used = set()
        written = []
        gdal = _mh._gdal()
        for i, b in enumerate(bands, start=1):
            _mh.phase_advance()
            band = src.GetRasterBand(b)
            a = band.ReadAsArray(0, 0, width_px, height_px)
            nod = band.GetNoDataValue()
            a = np.asarray(a, dtype=np.float32)

            valid = np.isfinite(a)
            if nod is not None:
                with np.errstate(invalid="ignore"):
                    valid &= (a != np.float32(nod))
            with np.errstate(invalid="ignore"):
                valid &= (a != np.float32(_tt.NODATA_OUT))
            if scale:
                # Stored densities cannot exceed 1/scale (10,000); anything
                # larger is a NoData sentinel whose declaration was lost (a
                # file geodatabase may not preserve it on a multi-band raster).
                over = valid & (a > np.float32(1.0 / scale))
                n_over = int(over.sum())
                if n_over:
                    _warn(messages,
                          "{:,} cells of {} hold values above the stored "
                          "maximum and were treated as NoData -- the density "
                          "raster has probably lost its NoData declaration "
                          "in a geodatabase.".format(n_over, _label(b)))
                    valid &= ~over
                del over
                a *= np.float32(scale)

            if c:
                a += np.float32(c)
            zero = valid & (a == np.float32(0.0))
            n_zero = int(zero.sum())
            neg = valid & (a < np.float32(0.0))
            n_neg = int(neg.sum())
            valid &= ~zero
            del zero, neg

            out = np.full(a.shape, np.float32(_tt.NODATA_OUT))
            with np.errstate(divide="ignore", invalid="ignore"):
                out[valid] = np.float32(1.0) / a[valid]
            del a

            if n_zero:
                _warn(messages,
                      "{:,} cells of {} were exactly zero after the constant "
                      "and became NoData (division by zero).".format(
                          n_zero, _label(b)))
            if n_neg:
                _warn(messages,
                      "{:,} cells of {} were NEGATIVE after the constant, so "
                      "their inverted values are negative. Least-cost tools "
                      "reject negative costs; add a larger constant or check "
                      "the input.".format(n_neg, _label(b)))

            name = _name(b)
            if name.lower() in prefix_used:
                name = "{}_B{}".format(name, b)
            prefix_used.add(name.lower())
            out_path = _mh.resolve_raster_output(
                os.path.join(out_workspace, name), messages)

            tmp = arcpy.CreateUniqueName("invert.tif", scratch)
            ds_out, oband = _tt._make_output(gdal, tmp, gt, proj,
                                             width_px, height_px)
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
            del out, valid

            info = band_info.get(b) or {}
            unscale_note = (
                " Stored values were converted to 0-1 proportions (x {:g}) "
                "before the constant was added.".format(scale) if scale else "")
            _tt._deliver_output(
                tmp, out_path, out_stats, messages,
                title=os.path.basename(str(out_path)),
                summary=(
                    "Created by Invert Raster from {src} ({lab}): each cell is "
                    "1 / (x{c}), turning a surface where high values are good "
                    "into a least-cost surface where they are cheap to "
                    "cross.{un}").format(
                        src=str(in_raster), lab=_label(b),
                        c=" + {:g}".format(c) if c else "",
                        un=unscale_note),
                description=(
                    "Numerically inverted raster -- the reciprocal 1/(x + c) "
                    "of a surface in which high values mean good habitat (for "
                    "example a land facet density or a diversity index), so "
                    "that good ground carries LOW cost and can be used "
                    "directly as the resistance surface in least-cost "
                    "corridor modelling. The small constant prevents division "
                    "by zero. A modernized port of the Land Facet Corridor "
                    "Designer's Invert Raster tool. {cite}").format(
                        cite=_CD_CITATION),
                tags=("invert, reciprocal, cost surface, resistance, "
                      "least-cost, corridor, connectivity, Corridor Designer, "
                      "Land Facet, Jenness Enterprises"),
                citation=_CD_CITATION)

            # No machine-readable linkage record here: inverted rasters are
            # terminal products for CorridorDesigner, and nothing in this
            # chain reads them back. Stamping a borrowed record kind would
            # misdescribe the dataset; the Esri summary above carries the
            # provenance (source, band/facet, constant, unscaling).
            written.append(out_path)
            _say(messages, "Wrote {}".format(out_path))

        _mh.end_phases()
        _say(messages, "{} of {} inverted raster{} written.".format(
            len(written), len(bands), "" if len(bands) == 1 else "s"))
        return written
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass
