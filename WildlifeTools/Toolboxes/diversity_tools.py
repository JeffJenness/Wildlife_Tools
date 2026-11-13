# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

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
import raster_strips as _rs
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


def vat_class_fields(in_raster):
    """Text and integer columns of the raster's attribute table that a user
    could classify by -- everything except the value, count and object-id
    columns. [] when the raster has no attribute table (the dialog then
    offers only "Value"). Names are returned as stored."""
    import arcpy
    try:
        fields = arcpy.ListFields(in_raster)
    except Exception:                                            # noqa: BLE001
        return []
    out = []
    for f in fields or []:
        nm = (f.name or "").strip()
        if not nm or nm.lower() in ("value", "count", "objectid", "oid"):
            continue
        if f.type in ("String", "SmallInteger", "Integer"):
            out.append(nm)
    return out


def _regroup_mapping(arcpy, in_raster, cats, class_field, messages):
    """The regrouping of the raster's classes by ``class_field`` of its
    attribute table: every distinct value of that column becomes one class,
    numbered 1..k in sorted order; a blank entry is its own class; a raster
    value with no row in the table keeps a class of its own, with a warning.
    Returns (per_cat codes aligned with ``cats``, the sorted class codes, a
    note for the messages/metadata); _regroup_apply relabels a block."""
    fields = {f.name.lower(): f.name for f in arcpy.ListFields(in_raster)}
    vname = fields.get("value")
    fname = fields.get(class_field.lower())
    if vname is None or fname is None:
        raise arcpy.ExecuteError(
            "The raster's attribute table has no field named {!r} (or no "
            "Value field); choose Value to classify by the cell values "
            "themselves.".format(class_field))
    lookup = {}
    with arcpy.da.SearchCursor(in_raster, [vname, fname]) as cur:
        for v, lab in cur:
            if v is None:
                continue
            lab = "" if lab is None else str(lab).strip()
            lookup[float(v)] = lab if lab else "(blank)"
    labels_by_cat = []
    missing = 0
    for v in cats.tolist():
        lab = lookup.get(float(v))
        if lab is None:
            lab = "value " + ("{:g}".format(v) if isinstance(v, float) else str(v))
            missing += 1
        labels_by_cat.append(lab)
    names = sorted(set(labels_by_cat), key=lambda t: t.lower())
    code_of = {nm: i + 1 for i, nm in enumerate(names)}
    per_cat = np.array([code_of[lab] for lab in labels_by_cat], dtype=np.int32)
    if missing:
        _warn(messages,
              "{:,} raster value(s) have no row in the attribute table and "
              "were kept as classes of their own.".format(missing))
    _say(messages,
         "{:,} raster values classified by the attribute field {!r} into "
         "{:,} classes: {}.".format(int(cats.size), class_field, len(names),
                                    "; ".join(names)))
    note = " (classified by the attribute field {!r}: {} classes)".format(
        class_field, len(names))
    return per_cat, np.arange(1, len(names) + 1, dtype=np.int32), note


def _regroup_apply(a, valid, cats, per_cat):
    """Relabel a block of cells by the regrouping mapping (int32)."""
    idx = np.searchsorted(cats, a[valid])
    out = np.zeros(a.shape, dtype=np.int32)
    out[valid] = per_cat[idx]
    return out


_DIVERSITY_BYTES_PER_CELL = 64   # working memory per cell of a diversity strip


def run_diversity(in_raster, out_raster, index="EFFECTIVE", q=1.0,
                  neighborhood="Circle", radius=3.0, width=3.0, height=3.0,
                  inner_radius=1.0, outer_radius=3.0,
                  start_angle=0.0, end_angle=90.0,
                  nbr_units="Cells", kernel_file=None,
                  exclude_if_any_null=False, messages=None,
                  class_field=None):
    """Compute a neighborhood diversity index for a categorical raster. See the
    module docstring and the tool help for the measures and conventions.

    ``class_field`` (2026-09-16): a column of the raster's attribute table to
    classify by. None / "Value" = every distinct cell value is its own class
    (the original behavior). Otherwise every cell is relabeled by that
    column's value before the neighborhood counting, so a 26-class
    vegetation raster with an NLCD column becomes its 10 NLCD groups --
    the same regrouping the Cross-Tab Statistics window offers."""
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

    src = _rs.Source(in_raster, scratch, messages)
    try:
        width_px, height_px = src.width, src.height
        if width_px < 1 or height_px < 1:
            raise arcpy.ExecuteError("The categorical raster is empty.")
        gt, proj = src.gt, src.proj
        sr = arcpy.Describe(in_raster).spatialReference
        is_float = np.issubdtype(np.asarray(src.band.ReadAsArray(0, 0, 1, 1)).dtype, np.floating)

        # ---- classes (pass 1: the distinct values, streamed) ----------------
        _mh.phase_advance("Finding the classes...")
        cats = None
        scan = _rs.strip_ranges(height_px, _rs.strip_rows(height_px, width_px, 0, 64))
        scan_prog = _rs.Progress("Finding the classes", scan)
        for si, (y0, y1) in enumerate(scan):
            scan_prog.update(si, 0.0)
            a, valid = src.read(y0, y1)
            # The clustering tool writes -9999 for outlier cells; honor it even
            # when the raster never declared it (the gdb round-trip can lose the
            # declaration) -- same screen as the density/Mahalanobis/invert
            # siblings, without which those cells became a phantom class
            # inflating every neighborhood's richness.
            with np.errstate(invalid="ignore"):
                valid &= (a != NODATA_OUT)
            u = np.unique(a[valid])
            cats = u if cats is None else np.union1d(cats, u)
            del a, valid
        if cats is None:
            cats = np.zeros(0)
        group_note = ""
        per_cat = None
        raw_cats = cats
        if class_field and str(class_field).strip().lower() != "value":
            per_cat, cats, group_note = _regroup_mapping(
                arcpy, in_raster, raw_cats, str(class_field).strip(), messages)
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
        elif not group_note:
            _say(messages, "{:,} classes found.".format(n_classes))

        # ---- neighborhood kernel(s) -----------------------------------------
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
            neighborhood, P, gt, height_px, sr, nbr_units, kernel_arr, messages,
            width=width_px, proj=proj)
        weighted = (neighborhood == "Weight")
        thr = np.float32(1e-6 if weighted else 0.5)
        _say(messages, "Neighborhood: {}.".format(note))

        # ---- pass 2: the index per strip ------------------------------------
        _mh.phase_advance("Computing the {}...".format(label))
        qk = _qkey(q)
        need_S1 = (index == "SHANNON") or (index == "EFFECTIVE" and qk == 1)
        need_S2 = (index in ("GINISIMPSON", "SIMPSON")) or (index == "EFFECTIVE" and qk == 2)
        need_rich = (index == "EFFECTIVE" and qk == 0)
        need_Sq = (index == "EFFECTIVE" and qk is None)
        qf = np.float32(q)
        single = (len(groups) == 1)
        halo = max(int(k.shape[0] // 2) for k, _r in groups)
        rows, blocks = _rs.plan(height_px, width_px, halo, _DIVERSITY_BYTES_PER_CELL, messages)
        writer = _rs.ScratchWriter(gdal, scratch, "diversity", gt, proj, width_px, height_px,
                                   nodata=float(NODATA_OUT))
        n_out = 0
        NANF = np.float32(np.nan)
        ONE = np.float32(1.0)
        ZERO = np.float32(0.0)
        prog = _rs.Progress("Computing the {}".format(label), blocks)
        try:
            for bi, (y0, y1, top, bot) in enumerate(blocks):
                prog.update(bi, 0.0)
                a, valid = src.read(top, bot)
                with np.errstate(invalid="ignore"):
                    valid &= (a != NODATA_OUT)
                if per_cat is not None:
                    a = _regroup_apply(a, valid, raw_cats, per_cat)
                shape = a.shape
                valid_f = valid.astype(np.float32)
                total = np.zeros(shape, np.float32)
                S1 = np.zeros(shape, np.float32) if need_S1 else None
                S2 = np.zeros(shape, np.float32) if need_S2 else None
                rich = np.zeros(shape, np.float32) if need_rich else None
                Sq = np.zeros(shape, np.float32) if need_Sq else None
                inwin = np.zeros(shape, np.float32) if exclude_if_any_null else None
                ones_f = np.ones(shape, np.float32) if exclude_if_any_null else None
                for kf_arr, rows_sel in groups:
                    sel = _rs.row_select(rows_sel, top, shape[0]) if not single else slice(None)
                    if sel is None:
                        continue
                    kf = kf_arr.astype(np.float32)
                    total[sel] = _hs.nbr_conv(valid_f, kf)[sel]
                    if exclude_if_any_null:
                        inwin[sel] = _hs.nbr_conv(ones_f, kf)[sel]
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
                del valid_f, ones_f

                # ---- assemble the index (float32 throughout) ----------------
                tot_safe = np.where(total > np.float32(0), total, ONE)
                valid_out = total > thr
                with np.errstate(invalid="ignore", divide="ignore"):
                    # ln(total) and S1/total are equal when one class fills the
                    # window, but not bit-for-bit in float32: the difference
                    # came out as -2.4e-7 (Jeff, 2026-09-13). Clamp H at 0,
                    # and the derived indices at their theoretical floors, so a
                    # pure window reads exactly 0 (H, Gini-Simpson) or exactly 1
                    # (effective number).
                    if index == "SHANNON":
                        out = np.where(valid_out,
                                       np.maximum(np.log(tot_safe) - S1 / tot_safe, ZERO),
                                       NANF)
                    elif index == "GINISIMPSON":
                        out = np.where(valid_out,
                                       np.maximum(ONE - S2 / (tot_safe * tot_safe), ZERO),
                                       NANF)
                    elif index == "SIMPSON":
                        out = np.where(valid_out,
                                       S2 / (tot_safe * tot_safe), NANF)
                    else:  # EFFECTIVE
                        if need_rich:
                            out = np.where(valid_out, rich, NANF)
                        elif need_S1:
                            h = np.maximum(np.log(tot_safe) - S1 / tot_safe, ZERO)
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
                sl = slice(y0 - top, y1 - top)
                n_out += int(out_valid[sl].sum())
                writer.write(y0, out[sl], out_valid[sl])
                del a, valid, total, S1, S2, rich, Sq, out, out_valid, tot_safe, valid_out
                prog.update(bi)
            out_path, out_stats = writer.finish(messages)
        except Exception:
            writer.abandon()
            raise
    finally:
        src.close()
    _say(messages,
        "{}: {:,} of {:,} cells written.".format(
            index_label(index, q).capitalize(), n_out, width_px * height_px))
    _mh.phase_advance("Writing the output raster...")

    _mh.phase_advance("Saving the output raster...")
    excl = ("Neighborhoods that included any NoData cell were set to NoData. "
            if exclude_if_any_null else
            "NoData cells were ignored within each neighborhood. ")
    _tt._deliver_output(
        out_path, out_raster, out_stats, messages,
        title=os.path.basename(str(out_raster)),
        summary=("Created by Diversity Indices from {src}: {lab}, computed over "
                 "a {nbr} neighborhood on {n:,} classes{grp}. {excl}").format(
                     src=str(in_raster), lab=label, nbr=note,
                     n=n_classes, excl=excl, grp=group_note),
        description=(
            "Neighborhood diversity of a categorical raster -- the {lab} of the "
            "class values (for example species or community types) within a "
            "{nbr} moving neighborhood{grp}. Ported and generalized from the "
            "Land Facet Corridor Designer's ArcMap Shannon's Index tool; needs "
            "no Spatial Analyst. {cite}").format(
                lab=label, nbr=note, cite=_CD_CITATION,
                grp=(", with the cells classified by the attribute field "
                     + str(class_field)) if group_note else ""),
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
