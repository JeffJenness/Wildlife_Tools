# -*- coding: utf-8 -*-
"""Land Facet Density -- the proportion of a neighborhood holding each land facet.

A redesign of the ArcMap "Calculate Density Surface" tool (``cmdDensity.cls`` /
``TopoCor_Functions.CreateDensityRaster``). The original computed ONE focal
facet per run into a single-band raster, so a 30-facet analysis meant 30 runs
and 30 files (Land_Facet_Tools.pdf p.30, step 3(c)(i)). This version computes
every facet in a single pass and writes ONE MULTI-BAND raster, one band per
facet, with a linkage record naming which band is which.

The density surface feeds two later steps (manual p.40): high-density areas
inside the Wildland Blocks become corridor termini, and density is one of the
variables in the Mahalanobis cost surface -- where its characteristic value is
100%, not its mean (see ``land_facet_linkage.IDEAL_DENSITY``).

THE DENOMINATOR (the one semantic that must not drift). The original divides by
every neighborhood cell that lies INSIDE THE RASTER, regardless of whether that
cell holds data:

    CalcDensityFromArray = lngValueCount / lngSampleTotalCount

Cells beyond the raster edge are excluded from both numerator and denominator,
so an edge window is renormalized and an edge cell can still reach 1.0. Cells
that are NoData *inside* the raster stay in the denominator and simply never
match, so NoData dilutes density. (In the VB6 that happens indirectly: the
NoData mask is discarded and each NoData pixel is replaced by a value computed
to be absent from the search set.)

Keeping that rule matters more than it looks. It makes density INVARIANT to how
populated the rest of the raster is -- which is what lets this tool's output be
compared against the original's, even though the original ran on a per-class
raster where every other class was NoData and this one runs on a combined raster
where those same cells hold real facet IDs. The alternative rule (divide by
non-NoData cells only) is NOT invariant: it would exclude those neighbors in the
original and include them here, giving very different numbers for the same
landscape. It is offered, but it is not comparable across workflows and it is
not the default.

Needs no Spatial Analyst. Part of the Land Facet Corridor Designer tool family.
"""
import os

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import aspect_tools as _at
import topographic_tools as _tt
import land_facet_linkage as _lnk

NEIGHBORHOODS = _at.NEIGHBORHOODS
NBR_UNITS = _at.NBR_UNITS

# Stored as scaled integers rather than floating point. Density is a ratio of
# small counts -- a radius-5 circle holds about 81 cells, so the value can only
# take ~82 distinct levels -- so 1/10000 resolution is far finer than the data
# can actually resolve, while halving the file against Float32. That matters
# here because the band count multiplies everything: 30 facets over a 1-billion
# cell grid is ~63 GB at UInt16 and ~126 GB at Float32.
SCALE = 10000
SCALE_FACTOR = 1.0 / SCALE
STORE_NODATA = 65535

# One neighborhood pass per facet, and one output band per facet. Warn early,
# refuse at the point where the output would be unmanageable rather than after
# an hour of work.
_SOFT_FACETS = 30
_HARD_FACETS = 200

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


def _make_multiband(gdal, path, gt, proj, width, height, nbands, use_nodata):
    """Create the scaled-integer, band-interleaved multi-band output.

    INTERLEAVE=BAND because every consumer reads one whole band at a time (the
    Mahalanobis run for one facet), and because this tool writes a band at a
    time too -- both are contiguous under band interleave and strided under
    pixel interleave.
    """
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, width, height, nbands, gdal.GDT_UInt16,
                    options=["TILED=YES", "COMPRESS=LZW", "INTERLEAVE=BAND",
                             "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform(gt)
    try:
        ds.SetProjection(proj)
    except Exception:
        pass
    if use_nodata:
        for b in range(1, nbands + 1):
            ds.GetRasterBand(b).SetNoDataValue(STORE_NODATA)
    return ds


def _facets_from_record(rec, cats):
    """Match the stamped facet table against the values actually present."""
    present = set(cats.tolist())
    out = []
    for f in rec.get("facets") or []:
        fid = f.get("facet_id")
        if fid is None or fid not in present:
            continue
        out.append({"facet_id": fid,
                    "category_value": f.get("category_value"),
                    "category_label": f.get("category_label"),
                    "cluster": f.get("cluster")})
    return out


def run_density(in_raster, out_raster, facet_values=None,
                neighborhood="Circle", radius=3.0, width=3.0, height=3.0,
                inner_radius=1.0, outer_radius=3.0,
                start_angle=0.0, end_angle=90.0,
                nbr_units="Cells", kernel_file=None,
                denominator=_lnk.DENOM_IN_RASTER,
                messages=None):
    """Compute a multi-band neighborhood-proportion raster for a facet raster.

    ``facet_values`` optionally restricts the output to a subset of facet IDs;
    by default every distinct value in the raster gets a band.
    """
    import arcpy

    if neighborhood not in NEIGHBORHOODS:
        raise arcpy.ExecuteError("Unknown neighborhood: {}".format(neighborhood))
    if neighborhood not in ("Irregular", "Weight") and nbr_units not in NBR_UNITS:
        raise arcpy.ExecuteError("Unknown neighborhood unit: {}".format(nbr_units))
    if denominator not in (_lnk.DENOM_IN_RASTER, _lnk.DENOM_NON_NODATA):
        raise arcpy.ExecuteError("Unknown denominator rule: {}".format(denominator))

    out_raster = _mh.resolve_raster_output(out_raster, messages)
    gdal = _mh._gdal()
    scratch = arcpy.env.scratchFolder

    _mh.begin_phases([
        ("Reading the land facet raster...", 1),
        ("Identifying the land facets...", 1),
        ("Computing neighborhood proportions...", 8),
        ("Saving the output raster...", 2),
        ("Writing the linkage metadata...", 1),
    ])

    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    try:
        band = src.GetRasterBand(1)
        width_px, height_px = src.RasterXSize, src.RasterYSize
        if width_px < 1 or height_px < 1:
            raise arcpy.ExecuteError("The land facet raster is empty.")
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        in_nodata = band.GetNoDataValue()
        sr = arcpy.Describe(in_raster).spatialReference
        a = band.ReadAsArray(0, 0, width_px, height_px)
        vmask = _mh.band_validity_mask(band)   # capture while src is open
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass

    a = np.asarray(a)
    is_float = np.issubdtype(a.dtype, np.floating)
    valid = np.ones(a.shape, dtype=bool)
    if is_float:
        valid &= np.isfinite(a)
    if in_nodata is not None:
        with np.errstate(invalid="ignore"):
            valid &= (a != in_nodata)
    # The clustering tool writes -9999 for outliers and over-confusion cells;
    # honour it even when the raster never declared it as its NoData value.
    with np.errstate(invalid="ignore"):
        valid &= (a != _tt.NODATA_OUT)
    if vmask is not None:
        valid &= vmask
    shape = a.shape

    # ---- which facets get bands ---------------------------------------------
    _mh.phase_advance("Identifying the land facets...")
    rec = _lnk.read(in_raster, _lnk.FACETS)
    cats = np.unique(a[valid])
    if cats.size == 0:
        raise arcpy.ExecuteError(
            "The land facet raster has no valid (non-NoData) cells.")

    entries = _facets_from_record(rec, cats) if rec else []
    if entries:
        _say(messages,
             "Read the land facet linkage record from the input: {:,} facets, "
             "analysis {}.".format(len(entries), rec.get("analysis_id")))
    else:
        if rec is None:
            _say(messages,
                 "The input carries no land facet linkage record, so facets are "
                 "taken from its distinct cell values and labelled generically. "
                 "Rasters made by the Land Facet Clustering tool carry the "
                 "record and produce fully labelled output.")
        else:
            _warn(messages,
                  "The input's linkage record names facets, but NONE of them "
                  "occur among the raster's cell values, so the record was "
                  "ignored -- it may belong to a different raster. Facets are "
                  "taken from the distinct cell values instead.")
            rec = None      # do not claim that record's analysis id downstream
        entries = [{"facet_id": (int(c) if float(c).is_integer() else float(c)),
                    "category_value": None, "category_label": None,
                    "cluster": None} for c in cats]

    if facet_values:
        want = set()
        for v in facet_values:
            try:
                want.add(int(v))
            except (TypeError, ValueError):
                want.add(v)
        matched = [e for e in entries if e["facet_id"] in want]
        missing = want - {e["facet_id"] for e in matched}
        if missing:
            # A stale dialog pick list (input swapped after choosing) used
            # to narrow the run SILENTLY; name what was asked for but absent.
            _warn(messages,
                  "Requested facet value{} {} do{} not occur in the raster "
                  "and {} skipped.".format(
                      "" if len(missing) == 1 else "s",
                      ", ".join(str(m) for m in sorted(missing, key=str)),
                      "es" if len(missing) == 1 else "",
                      "was" if len(missing) == 1 else "were"))
        entries = matched
        if not entries:
            raise arcpy.ExecuteError(
                "None of the requested facet values occur in the raster.")

    n_facets = len(entries)
    if n_facets > _HARD_FACETS:
        raise arcpy.ExecuteError(
            "The raster holds {:,} distinct facet values, which would make a "
            "{:,}-band output. That is almost certainly a continuous raster "
            "rather than a land facet raster. Restrict the facets, or check "
            "the input.".format(n_facets, n_facets))
    if n_facets > _SOFT_FACETS:
        _warn(messages,
              "{:,} facets means a {:,}-band output and one neighborhood pass "
              "per facet. Expect a long run and a large file.".format(
                  n_facets, n_facets))
    est_gb = (width_px * height_px * n_facets * 2) / float(1 << 30)
    _say(messages, "{:,} facets -> {:,} bands, about {:.1f} GB before "
                   "compression.".format(n_facets, n_facets, est_gb))

    # ---- neighborhood --------------------------------------------------------
    kernel_arr = None
    if neighborhood in ("Irregular", "Weight"):
        if not kernel_file:
            raise arcpy.ExecuteError(
                "The {} neighborhood needs a kernel file.".format(neighborhood))
        kernel_arr = _at._parse_kernel_file(kernel_file)
        if neighborhood == "Irregular":
            kernel_arr = (kernel_arr != 0).astype(np.float64)
        if np.any(kernel_arr < 0):
            raise arcpy.ExecuteError(
                "The kernel file contains negative weights; weights must be "
                "zero or positive.")
        if not np.any(kernel_arr > 0):
            raise arcpy.ExecuteError(
                "The kernel file contains no positive weights.")
    P = {"radius": radius, "width": width, "height": height,
         "inner": inner_radius, "outer": outer_radius,
         "start": start_angle, "end": end_angle}
    groups, note = _at._neighborhood_groups(
        neighborhood, P, gt, height_px, sr, nbr_units, kernel_arr, messages)
    single = (len(groups) == 1)
    _say(messages, "Neighborhood: {}.".format(note))

    # ---- denominator ---------------------------------------------------------
    # nbr_conv zero-pads, so convolving an all-ones grid yields, at every cell,
    # the number of kernel cells that fall inside the raster -- exactly the
    # original's in-raster count, edge renormalization included.
    _mh.phase_advance("Computing neighborhood proportions...")
    faithful = (denominator == _lnk.DENOM_IN_RASTER)
    denom = np.zeros(shape, np.float32)
    ref = (np.ones(shape, np.float32) if faithful
           else valid.astype(np.float32))
    for kf_arr, rows in groups:
        sel = slice(None) if single else rows
        denom[sel] = _hs.nbr_conv(ref, kf_arr.astype(np.float32))[sel]
    del ref
    undefined = ~(denom > np.float32(0))
    n_undef = int(undefined.sum())
    use_nodata = n_undef > 0
    if use_nodata:
        _warn(messages,
              "{:,} cells have no usable neighborhood and were set to NoData. "
              "Note that a file geodatabase may not preserve the NoData value "
              "on a multi-band raster; a GeoTIFF destination will.".format(n_undef))
    denom_safe = np.where(undefined, np.float32(1.0), denom)
    del denom

    # ---- one band per facet, written as we go --------------------------------
    # Never hold all bands at once: 30 bands over a billion cells is tens of GB.
    # Each iteration keeps only the indicator and its convolution.
    tmp = arcpy.CreateUniqueName("lf_density.tif", scratch)
    ds = _make_multiband(gdal, tmp, gt, proj, width_px, height_px,
                         n_facets, use_nodata)
    n_conv = max(1, len(groups) * n_facets)
    done = 0
    stats_ok = True
    try:
        for bi, entry in enumerate(entries, start=1):
            fid = entry["facet_id"]
            ind = ((a == fid) & valid).astype(np.float32)
            ck = np.zeros(shape, np.float32)
            for kf_arr, rows in groups:
                sel = slice(None) if single else rows
                ck[sel] = _hs.nbr_conv(ind, kf_arr.astype(np.float32))[sel]
                done += 1
                _mh.phase_step(done, n_conv)
            del ind
            # In place on ck: a separate `prop` array would add one more
            # full-grid float32 (~4 GB on a billion-cell grid) of peak memory
            # for no gain. Same operations in the same order and dtype, so the
            # stored values are bit-identical to the two-array version.
            with np.errstate(invalid="ignore", divide="ignore"):
                ck /= denom_safe
            np.clip(ck, 0.0, 1.0, out=ck)
            ck *= np.float32(SCALE)
            np.rint(ck, out=ck)
            stored = ck.astype(np.uint16)
            del ck
            if use_nodata:
                stored[undefined] = STORE_NODATA
            oband = ds.GetRasterBand(bi)
            oband.WriteArray(stored)
            # Band descriptions do not survive CopyRaster into a geodatabase --
            # set them anyway for the GeoTIFF case, but the linkage record is
            # what downstream tools actually read.
            oband.SetDescription("Facet_{}_density".format(fid))
            oband.FlushCache()

            # Record this band's statistics from the values we already hold, so
            # CopyRaster carries them through and no CalculateStatistics scan is
            # ever needed (it is pathologically slow on geodatabase rasters, and
            # would rescan tens of GB here).
            acc = _mh._RasterStats()
            slab = max(1, int(4_000_000 // max(1, width_px)))
            for r0 in range(0, height_px, slab):
                chunk = stored[r0:r0 + slab]
                if use_nodata:
                    chunk = chunk[undefined[r0:r0 + slab] == False]  # noqa: E712
                if chunk.size:
                    acc.add(chunk.astype(np.float64).ravel())
            try:
                st = _mh._finalize_stats(oband, acc, messages)
                if st is not None:
                    _mh._write_gtiff_stats(oband, st)
                else:
                    stats_ok = False
            except Exception as e:                                # noqa: BLE001
                stats_ok = False
                _warn(messages, "Could not record statistics for band {}: "
                                "{}".format(bi, e))
            oband.FlushCache()
            oband = None
            del stored
            _say(messages, "  band {} of {}: {}".format(
                bi, n_facets, _lnk.facet_label(entry)))
        ds.FlushCache()
    finally:
        ds = None
    del denom_safe, undefined, a, valid

    # ---- deliver -------------------------------------------------------------
    _mh.phase_advance("Saving the output raster...")
    denom_text = ("every neighborhood cell inside the raster (the original "
                  "ArcMap rule, so NoData cells dilute the proportion)"
                  if faithful else
                  "only neighborhood cells holding data (NoData cells ignored)")
    _tt._deliver_output(
        tmp, out_raster, {"on_raster": True} if stats_ok else None, messages,
        title=os.path.basename(str(out_raster)),
        summary=(
            "Created by Land Facet Density from {src}: {n} band{s}, one per land "
            "facet, over a {nbr} neighborhood. Values are stored as integers "
            "scaled by {sc:,} (divide by {sc:,} for the 0-1 proportion). The "
            "denominator was {den}. Band-to-facet assignments are recorded in "
            "this dataset's Supplemental Information.").format(
                src=str(in_raster), n=n_facets, s="" if n_facets == 1 else "s",
                nbr=note, sc=SCALE, den=denom_text),
        description=(
            "Land facet density -- for each land facet, the proportion of the "
            "surrounding neighborhood that is composed of that facet, ranging "
            "from 0 to 1. One band per facet. High-density areas within the "
            "Wildland Blocks are used to place corridor termini, and density is "
            "one of the variables in the Mahalanobis cost surface, where its "
            "characteristic (ideal) value is 100% rather than its mean. "
            "Redesigned from the ArcMap Calculate Density Surface tool, which "
            "produced one single-band raster per facet. Needs no Spatial "
            "Analyst. {cite}").format(cite=_CD_CITATION),
        tags=("land facet, density, neighborhood proportion, focal, moving "
              "window, corridor, connectivity, termini, Mahalanobis, Corridor "
              "Designer, Land Facet, Jenness Enterprises"),
        citation=_CD_CITATION)

    # ---- linkage record ------------------------------------------------------
    _mh.phase_advance("Writing the linkage metadata...")
    bands = [{"band": i, "facet_id": e["facet_id"],
              "category_value": e["category_value"],
              "category_label": e["category_label"],
              "cluster": e["cluster"]}
             for i, e in enumerate(entries, start=1)]
    record = _lnk.density_record(
        analysis_id=(rec or {}).get("analysis_id") or _lnk.new_analysis_id(),
        source_facet_raster=in_raster,
        source_analysis_id=(rec or {}).get("analysis_id"),
        bands=bands,
        neighborhood={"shape": neighborhood, "units": nbr_units,
                      "radius": radius, "width": width, "height": height,
                      "inner_radius": inner_radius, "outer_radius": outer_radius,
                      "start_angle": start_angle, "end_angle": end_angle,
                      "description": note},
        denominator=denominator,
        scale_factor=SCALE_FACTOR,
        variables=(rec or {}).get("variables") or [],
        grid={"width": width_px, "height": height_px})
    if _lnk.write(out_raster, record, messages):
        _say(messages,
             "Linkage record written: the Mahalanobis tool can identify each "
             "band's facet and will treat 1.0 (100%) as the ideal density.")
    _mh.end_phases()
    return None
