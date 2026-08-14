"""Diversity Indices -- neighborhood diversity of a categorical raster.

A modernized, generalized port of the Land Facet Corridor Designer's ArcMap
Shannon's Index tool (``cmdShannon.cls`` /
``TopoCor_Functions.CreateDiversityRaster``). Over a
moving neighborhood of any shape and size, it summarizes the diversity of the
categorical cell values (each cell is a class -- typically a species or a
vegetation/community type) and writes a continuous diversity raster.

It offers the major diversity measures:

* **Effective number of species** (Hill numbers of order *q*) -- the default,
  and the most defensible modern measure. It reports the number of *equally
  common* classes that would give the same diversity as the actual, uneven
  neighborhood. Its value is set by *q*, which tunes how much rare classes
  count: ``q=0`` = class richness; ``q=1`` = the exponential of Shannon's
  entropy (each class weighted by its abundance); ``q=2`` = the inverse Simpson
  index (common classes dominate). Any non-negative *q* is allowed.
* **Shannon's index** *H* (natural log).
* **Gini-Simpson index** ``1 - sum(p^2)`` -- probability two random cells differ.
* **Simpson's concentration** ``sum(p^2)`` -- Simpson's original 1949 dominance
  measure (higher = LESS diverse). The inverse Simpson ``1/sum(p^2)`` is the
  effective number at ``q=2`` and is offered there rather than duplicated here.

All neighborhood shapes (rectangle, circle, annulus, wedge, kernel file), sizes
in cells or map units, and the geodesic per-row adjustment for geographic
(lat/long) rasters are shared with the other neighborhood tools; no Spatial
Analyst is required.

Part of the Land Facet Corridor Designer tool family. See the tool help for the
full citations (CorridorDesigner: Majka, Jenness & Beier 2007; Land Facets:
Beier & Brost 2010, Jenness, Brost & Beier 2013; diversity theory: Shannon
1948, Simpson 1949, Hill 1973, Jost 2006, Tuomisto 2010).
"""
import math
import os

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import aspect_tools as _at
import topographic_tools as _tt
import land_facet_linkage as _lnk

NEIGHBORHOODS = _at.NEIGHBORHOODS
NBR_UNITS = _at.NBR_UNITS
NODATA_OUT = _tt.NODATA_OUT

# Index dialog labels (value-list order) and their internal keys.
INDEX_LABELS = [
    "Effective Number of Species",
    "Shannon's Index (H)",
    "Gini-Simpson Index (1 - D)",
    "Simpson's Concentration (D)",
]
INDEX_KEY = {
    INDEX_LABELS[0]: "EFFECTIVE",
    INDEX_LABELS[1]: "SHANNON",
    INDEX_LABELS[2]: "GINISIMPSON",
    INDEX_LABELS[3]: "SIMPSON",
}
_ABBR = {"EFFECTIVE": "EffNum", "SHANNON": "Shannon",
         "GINISIMPSON": "GiniSimp", "SIMPSON": "SimpsonD"}

# A categorical raster should have relatively few unique values. Warn past the
# soft limit; refuse past the hard limit (one neighborhood pass per class, so a
# continuous raster would be unusably slow and is almost certainly a mistake).
_SOFT_CLASSES = 500
_HARD_CLASSES = 5000

# Short citation embedded in output metadata; the full, linked references live
# in the tool help sidecar.
_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org. Land Facet Corridor Designer: Beier, P., and "
    "B. Brost. 2010, Conservation Biology 24:701-710; Jenness, J., B. Brost, "
    "and P. Beier. 2013, http://www.jennessent.com/arcgis/land_facets.htm. "
    "Diversity measures: Shannon 1948; Simpson 1949; Hill 1973; Jost 2006; "
    "Tuomisto 2010.")


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def _qkey(q):
    """Return 0, 1 or 2 if q is (essentially) that integer, else None."""
    iq = int(round(q))
    if abs(q - iq) < 1e-9 and iq in (0, 1, 2):
        return iq
    return None


def index_label(index, q):
    """Human-readable description of the chosen measure for messages/metadata."""
    if index == "SHANNON":
        return "Shannon's index (H, natural log)"
    if index == "GINISIMPSON":
        return "Gini-Simpson index (1 - sum p^2)"
    if index == "SIMPSON":
        return "Simpson's concentration (D = sum p^2)"
    qk = _qkey(q)
    named = {0: "richness (count of classes)",
             1: "exponential of Shannon's H",
             2: "inverse Simpson index"}.get(qk)
    return ("effective number of species, order q={:g}{}".format(
        q, " (" + named + ")" if named else ""))


def run_diversity(in_raster, out_raster, index="EFFECTIVE", q=1.0,
                  neighborhood="Circle", radius=3.0, width=3.0, height=3.0,
                  inner_radius=1.0, outer_radius=3.0,
                  start_angle=0.0, end_angle=90.0,
                  nbr_units="Cells", kernel_file=None,
                  exclude_if_any_null=False, messages=None):
    """Compute a neighborhood diversity index for a categorical raster. See the
    module docstring and the tool help for the measures and conventions."""
    import arcpy

    index = str(index).upper()
    if index not in ("EFFECTIVE", "SHANNON", "GINISIMPSON", "SIMPSON"):
        index = INDEX_KEY.get(index, index)
    if index not in ("EFFECTIVE", "SHANNON", "GINISIMPSON", "SIMPSON"):
        raise arcpy.ExecuteError("Unknown index: {}".format(index))
    if neighborhood not in NEIGHBORHOODS:
        raise arcpy.ExecuteError("Unknown neighborhood: {}".format(neighborhood))
    if neighborhood not in ("Irregular", "Weight") and nbr_units not in NBR_UNITS:
        raise arcpy.ExecuteError("Unknown neighborhood unit: {}".format(nbr_units))
    q = float(q)
    if index == "EFFECTIVE" and q < 0:
        raise arcpy.ExecuteError(
            "The order q for the effective number must be zero or positive.")

    label = index_label(index, q)
    out_raster = _mh.resolve_raster_output(out_raster, messages)
    gdal = _mh._gdal()
    scratch = arcpy.env.scratchFolder

    _mh.begin_phases([
        ("Reading the categorical raster...", 1),
        ("Finding the classes...", 1),
        ("Computing the {}...".format(label), 6),
        ("Writing the output raster...", 2),
        ("Saving the output raster...", 2),
        ("Creating layer file and symbology...", 1),
    ])

    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    try:
        band = src.GetRasterBand(1)
        width_px, height_px = src.RasterXSize, src.RasterYSize
        if width_px < 1 or height_px < 1:
            raise arcpy.ExecuteError("The categorical raster is empty.")
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
    if vmask is not None:
        valid &= vmask
    shape = a.shape

    # ---- classes -------------------------------------------------------------
    _mh.phase_advance("Finding the classes...")
    cats = np.unique(a[valid])
    n_classes = int(cats.size)
    if n_classes == 0:
        raise arcpy.ExecuteError("The raster has no valid (non-NoData) cells.")
    if n_classes > _HARD_CLASSES:
        raise arcpy.ExecuteError(
            "The raster has {:,} unique values, which looks like a CONTINUOUS "
            "raster rather than a categorical one. This tool summarizes the "
            "diversity of categorical classes (for example a species or "
            "community raster). If your data really are categorical, reclassify "
            "to fewer classes first.".format(n_classes))
    if is_float and not np.all(cats == np.round(cats)):
        _warn(messages,
            "The raster is floating-point with non-integer class values; each "
            "distinct value is treated as its own class. Categorical rasters are "
            "normally integer.")
    if n_classes > _SOFT_CLASSES:
        _warn(messages,
            "{:,} classes found. This tool makes one neighborhood pass per "
            "class, so a large number of classes will run slowly.".format(
                n_classes))
    else:
        _say(messages, "{:,} classes found.".format(n_classes))

    # ---- neighborhood kernel(s) ---------------------------------------------
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
    weighted = (neighborhood == "Weight")
    thr = np.float32(1e-6 if weighted else 0.5)
    _say(messages, "Neighborhood: {}.".format(note))

    # ---- accumulate per-class neighborhood counts ---------------------------
    _mh.phase_advance("Computing the {}...".format(label))
    qk = _qkey(q)
    need_S1 = (index == "SHANNON") or (index == "EFFECTIVE" and qk == 1)
    need_S2 = (index in ("GINISIMPSON", "SIMPSON")) or (index == "EFFECTIVE" and qk == 2)
    need_rich = (index == "EFFECTIVE" and qk == 0)
    need_Sq = (index == "EFFECTIVE" and qk is None)

    valid_f = valid.astype(np.float32)
    total = np.zeros(shape, np.float32)
    S1 = np.zeros(shape, np.float32) if need_S1 else None
    S2 = np.zeros(shape, np.float32) if need_S2 else None
    rich = np.zeros(shape, np.float32) if need_rich else None
    Sq = np.zeros(shape, np.float32) if need_Sq else None
    inwin = np.zeros(shape, np.float32) if exclude_if_any_null else None
    ones_f = np.ones(shape, np.float32) if exclude_if_any_null else None
    qf = np.float32(q)

    single = (len(groups) == 1)
    n_conv = len(groups) * (1 + n_classes + (1 if exclude_if_any_null else 0))
    done = [0]

    def _tick():
        done[0] += 1
        _mh.phase_step(done[0], n_conv)

    for kf_arr, rows in groups:
        kf = kf_arr.astype(np.float32)
        sel = slice(None) if single else rows
        total[sel] = _hs.nbr_conv(valid_f, kf)[sel]
        _tick()
        if exclude_if_any_null:
            inwin[sel] = _hs.nbr_conv(ones_f, kf)[sel]
            _tick()
        for c in cats:
            ind = ((a == c) & valid).astype(np.float32)
            ck = _hs.nbr_conv(ind, kf)[sel]
            with np.errstate(invalid="ignore", divide="ignore"):
                if need_S1:
                    S1[sel] += np.where(ck > np.float32(0),
                                        ck * np.log(ck), np.float32(0))
                if need_S2:
                    S2[sel] += ck * ck
                if need_rich:
                    rich[sel] += (ck > thr).astype(np.float32)
                if need_Sq:
                    Sq[sel] += np.power(ck, qf)
            _tick()
    del valid_f, ones_f

    # ---- assemble the index (float32 throughout) ----------------------------
    NANF = np.float32(np.nan)
    ONE = np.float32(1.0)
    tot_safe = np.where(total > np.float32(0), total, ONE)
    valid_out = total > thr
    with np.errstate(invalid="ignore", divide="ignore"):
        if index == "SHANNON":
            out = np.where(valid_out,
                           np.log(tot_safe) - S1 / tot_safe, NANF)
        elif index == "GINISIMPSON":
            out = np.where(valid_out,
                           ONE - S2 / (tot_safe * tot_safe), NANF)
        elif index == "SIMPSON":
            out = np.where(valid_out,
                           S2 / (tot_safe * tot_safe), NANF)
        else:  # EFFECTIVE
            if need_rich:
                out = np.where(valid_out, rich, NANF)
            elif need_S1:
                h = np.log(tot_safe) - S1 / tot_safe
                out = np.where(valid_out, np.exp(h), NANF)
            elif need_S2:
                out = np.where(valid_out,
                               (tot_safe * tot_safe)
                               / np.where(S2 > np.float32(0), S2, ONE), NANF)
            else:
                sump = Sq / np.power(tot_safe, qf)
                out = np.where(valid_out,
                               np.power(np.where(sump > np.float32(0), sump, ONE),
                                        np.float32(1.0 / (1.0 - q))), NANF)
    out = out.astype(np.float32, copy=False)

    if exclude_if_any_null:
        out[total < (inwin - thr)] = NANF
        del inwin

    out_valid = np.isfinite(out)
    n_out = int(out_valid.sum())
    _say(messages,
        "{}: {:,} of {:,} cells written.".format(
            index_label(index, q).capitalize(), n_out, width_px * height_px))

    # ---- write, stats, metadata, symbology ----------------------------------
    _mh.phase_advance("Writing the output raster...")
    out_path = arcpy.CreateUniqueName("diversity.tif", scratch)
    ds, oband = _tt._make_output(gdal, out_path, gt, proj, width_px, height_px)
    stats = _mh._RasterStats()
    slab = max(1, int(4_000_000 // max(1, width_px)))
    for r0 in range(0, height_px, slab):
        r1 = min(height_px, r0 + slab)
        v = out[r0:r1][out_valid[r0:r1]]
        if v.size:
            stats.add(v.astype(np.float64))
        _mh.phase_step(r1, height_px)
    out[~out_valid] = np.float32(NODATA_OUT)
    oband.WriteArray(out)
    oband.FlushCache()
    out_stats = _mh._finalize_stats(oband, stats, messages)
    if out_stats is not None:
        out_stats["on_raster"] = _mh._write_gtiff_stats(oband, out_stats)
    oband.FlushCache()
    ds.FlushCache()
    oband = None
    ds = None

    _mh.phase_advance("Saving the output raster...")
    excl = ("Neighborhoods that included any NoData cell were set to NoData. "
            if exclude_if_any_null else
            "NoData cells were ignored within each neighborhood. ")
    _tt._deliver_output(
        out_path, out_raster, out_stats, messages,
        title=os.path.basename(str(out_raster)),
        summary=("Created by Diversity Indices from {src}: {lab}, computed over "
                 "a {nbr} neighborhood on {n:,} classes. {excl}").format(
                     src=str(in_raster), lab=label, nbr=note,
                     n=n_classes, excl=excl),
        description=(
            "Neighborhood diversity of a categorical raster -- the {lab} of the "
            "class values (for example species or community types) within a "
            "{nbr} moving neighborhood. Ported and generalized from the Land "
            "Facet Corridor Designer's ArcMap Shannon's Index tool; needs no "
            "Spatial Analyst. {cite}").format(
                lab=label, nbr=note, cite=_CD_CITATION),
        tags=("diversity, Shannon index, Simpson index, effective number of "
              "species, Hill numbers, richness, evenness, neighborhood, focal, "
              "categorical, species diversity, community, Corridor Designer, "
              "Land Facet, Jenness Enterprises"),
        citation=_CD_CITATION)

    # ---- linkage record ------------------------------------------------------
    # Diversity of the land facets is the published step-3 alternative (its
    # inversion is the interspersion corridor's cost surface), so the output
    # joins the Land Facet chain: the analysis id chains from the input's facet
    # record when it has one. On a plain species/community raster the record
    # simply documents provenance under a fresh id.
    try:
        frec = _lnk.read(in_raster, _lnk.FACETS)
        _lnk.write(out_raster, _lnk.diversity_record(
            analysis_id=(frec or {}).get("analysis_id") or _lnk.new_analysis_id(),
            source_raster=in_raster,
            index_label=label, q=q, neighborhood_note=note,
            source_analysis_id=(frec or {}).get("analysis_id"),
            exclude_if_any_null=exclude_if_any_null), messages)
        if frec:
            _say(messages,
                 "The input carries a land facet record; this diversity surface "
                 "was recorded as part of the same analysis chain.")
    except Exception as e:
        _warn(messages, "Could not write the linkage record: {}".format(e))

    _mh.phase_advance("Creating layer file and symbology...")
    out_lyrx = None
    try:
        # Sequential light -> dark green ramp (low -> high diversity). For
        # Simpson's concentration (dominance), higher means LESS diverse, so use
        # the reverse ramp so "greener" still reads as "more diverse".
        ramp = [(247, 252, 245), (116, 196, 118), (0, 109, 44)]
        if index == "SIMPSON":
            ramp = list(reversed(ramp))
        out_lyrx = _mh.default_stretch_lyrx(out_raster, ramp, messages=messages)
    except Exception as e:
        _warn(messages, "Could not set output symbology: {}".format(e))
    _mh.end_phases()
    return out_lyrx
