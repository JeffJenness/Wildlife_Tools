# -*- coding: utf-8 -*-
"""The CorridorDesigner habitat-suitability-model (HSM) building chain.

Modernized ports of the five HSM tools from the original CorridorDesigner
Python toolbox (Dan Majka, dan@corridordesign.org, 2006-2007 -- the primary
author of the original tools; these ArcGIS Pro versions are by Jeff Jenness
and Dan Majka):

* ``run_hsm``          -- HSM 1: reclassify factor rasters to a 0-100
  suitability scale via ASCII remap files, then combine by weighted geometric
  or additive mean (``hab_hsmGeneral.py``).
* ``run_combine``      -- HSM 2: the same weighted combine over factors that
  are ALREADY on the 0-100 scale (``hab_combineFactorsGeneral.py``).
* ``run_normalize``    -- HSM 3: linear min-max stretch of an externally built
  HSM onto the 0-100 scale (``hab_convertExistingHsm.py``).
* ``run_reclassify_features`` -- Modify HSM 1: burn polygon features into an
  HSM at a fixed new score (``hab_reclassifyUnsuitableFeatures.py``).
* ``run_critical_feature``    -- Modify HSM 2: scale an HSM by
  distance-band percentages from a critical feature
  (``hab_modifyHsmCriticalFactor.py``).

THE COMBINE MATH, EXACTLY AS THE ORIGINAL (verified against the 2007 source):
weights are integers summing to 100, normalized to w/100, then

    geometric:  HSM = prod( S_i ** (w_i/100) )      (workshop book section 2.4:
                Liebig's Law of the Minimum -- a 0 score in ANY factor zeroes
                the cell, however good the others are)
    additive:   HSM = sum(  S_i *  (w_i/100) )      (equivalent to Esri's
                Weighted Overlay)

With scores on 0-100 and the normalized weights summing to 1, both results
stay on the 0-100 scale.

REMAP FILES are the original tab/space-delimited ASCII format, unchanged so
existing CorridorDesigner remap files (including the AZ species files) drop
straight in::

    1 : 100            a single input value
    762 1219 : 44      an input range (from to : new)

Lines must be in ascending numerical order -- the workshop book documents that
the original tool fails otherwise (p.98); this port validates and says so.
Range boundaries follow first-match: a value on a shared boundary belongs to
the EARLIER line. Input cells matching no line become NoData, the original's
"NODATA" missing-values option.

GEODATABASE REMAP TABLES are accepted interchangeably with the ASCII files
(``parse_remap_source`` dispatches on which kind the path is): a table with
FromValue / ToValue / NewValue fields (reclassify by range) or
OriginalValue / NewValue fields (reclassify by value), as written by the
Create HSM Remap Table ribbon tool. A geodatabase cannot hold loose text
files, so gdb-based projects get remap TABLES living right next to their
rasters. Table rows are sorted rather than order-validated.

DIFFERENCES FROM THE ORIGINALS, all deliberate and user-visible:

* No Spatial Analyst. ``SingleOutputMapAlgebra`` (removed from Pro entirely),
  ``ReclassByASCIIFile``, ``EucDistance``, ``Con`` etc. are reimplemented with
  GDAL + numpy + scipy.
* Unlimited factors (the originals capped at 6 fixed dialog slots).
* The FIRST factor defines the output grid; other factors on a different
  grid are resampled (nearest neighbour) onto it, with a message. The
  originals delegated this silently to the SA environment.
* Critical Feature: distances BEYOND the last distance band leave the HSM
  unchanged (multiplier 100%), with a warning. The original's Reclassify
  used the "DATA" option there, which silently multiplied the HSM by the
  RAW DISTANCE in map units -- almost certainly never intended. Add a final
  band out to a huge distance (the workshop book's own examples do) to
  control the beyond-range behavior explicitly.
* Normalize: an optional clamp (default on) pins stretched values to 0-100;
  the original wrote out-of-range values silently.

Every output carries a Land Facet linkage record (see ``land_facet_linkage``)
so downstream chain tools -- Invert Raster, Identify Termini Polygons,
Least-Cost Corridor, the Corridor Analysis Data Report -- can trace provenance. The
two Modify tools carry the source's own record forward with a
``post_processing`` step appended, exactly like Fill Corridor Holes: the
output is the SAME analysis product, just modified.
"""
import os

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import topographic_tools as _tt
import land_facet_linkage as _lnk
import land_facet_mahalanobis as _lfm

GEOMETRIC = "Geometric Mean"
ADDITIVE = "Additive Mean"

# Patch classes (Create Habitat Patches), by GEODESIC area against the
# user's two hectare thresholds. Class 1 is suitable ground whose patch is
# too small to support breeding; 2 supports at least one breeding event;
# 3 supports a population (the workshop book's patch/core distinction).
PATCH_CLASS_NAMES = {1: "Too small", 2: "Breeding patch",
                     3: "Population patch"}
_PATCH_COLORS = {                    # Class_Name -> (fill RGB, stroke RGB)
    "Too small": ((250, 232, 170), (150, 139, 102)),
    "Breeding patch": ((171, 211, 120), (103, 127, 72)),
    "Population patch": ((56, 128, 62), (34, 77, 37)),
}

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org. Original CorridorDesigner HSM tools by Dan "
    "Majka; ArcGIS Pro versions by Jeff Jenness and Dan Majka.")

_TAGS = ("habitat suitability, HSM, habitat model, factor, weighted overlay, "
         "geometric mean, corridor, connectivity, CorridorDesigner, "
         "Jenness Enterprises")


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


# ---------------------------------------------------------------------------
# remap files
# ---------------------------------------------------------------------------
def parse_remap_file(path):
    """Parse an original-format ASCII remap file into ``[(lo, hi, out)]``.

    Single-value lines become ``(v, v, out)``. Raises ``arcpy.ExecuteError``
    with a user-ready message on a malformed line or on values out of
    ascending order (the workshop book's documented requirement, p.98).
    """
    import arcpy

    rows = []
    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except OSError as e:
        raise arcpy.ExecuteError(
            "Could not read the remap file {}: {}".format(path, e))
    for ln, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        left, sep, right = line.partition(":")
        if not sep:
            raise arcpy.ExecuteError(
                "Line {} of {} has no ':' separator: {!r}. Expected the "
                "original CorridorDesigner format, either 'value : score' or "
                "'from to : score'.".format(ln, os.path.basename(path), line))
        try:
            out = float(right.strip())
        except ValueError:
            raise arcpy.ExecuteError(
                "Line {} of {}: the score after ':' is not a number: "
                "{!r}.".format(ln, os.path.basename(path), right.strip()))
        try:
            nums = [float(t) for t in left.split()]
        except ValueError:
            raise arcpy.ExecuteError(
                "Line {} of {}: could not read the input value(s) before "
                "':' -- {!r}.".format(ln, os.path.basename(path), left.strip()))
        if len(nums) == 1:
            rows.append((nums[0], nums[0], out))
        elif len(nums) == 2:
            if nums[1] < nums[0]:
                raise arcpy.ExecuteError(
                    "Line {} of {}: the range runs backwards ({:g} to "
                    "{:g}).".format(ln, os.path.basename(path),
                                    nums[0], nums[1]))
            rows.append((nums[0], nums[1], out))
        else:
            raise arcpy.ExecuteError(
                "Line {} of {} has {} values before ':'; expected 1 "
                "(a single value) or 2 (a from/to range).".format(
                    ln, os.path.basename(path), len(nums)))
    if not rows:
        raise arcpy.ExecuteError(
            "The remap file {} contains no remap lines.".format(path))
    for i in range(1, len(rows)):
        if rows[i][0] < rows[i - 1][0] or rows[i][1] < rows[i - 1][1]:
            raise arcpy.ExecuteError(
                "The remap file {} is not in ascending numerical order at "
                "line values {:g}..{:g} -- the original tool required "
                "ascending order (workshop book p.98), and so does this "
                "one.".format(os.path.basename(path), rows[i][0], rows[i][1]))
    return rows


_TABLE_RANGE_FIELDS = ("fromvalue", "tovalue", "newvalue")
_TABLE_VALUE_FIELDS = ("originalvalue", "newvalue")


def parse_remap_table(path):
    """Parse a geodatabase remap TABLE into ``[(lo, hi, out)]``.

    Two recognized schemas (written by the Create HSM Remap Table ribbon
    tool, matched case-insensitively): FromValue/ToValue/NewValue
    (reclassify-by-range) or OriginalValue/NewValue (reclassify-by-value,
    each value becoming a (v, v, out) row). Unlike the ASCII format, a
    table has no reliable row order, so rows are SORTED here rather than
    required to be ascending.
    """
    import arcpy

    have = {f.name.lower(): f.name for f in arcpy.ListFields(path)}
    rows = []
    if all(k in have for k in _TABLE_RANGE_FIELDS):
        fields = [have[k] for k in _TABLE_RANGE_FIELDS]
        with arcpy.da.SearchCursor(path, fields) as cur:
            for lo, hi, out in cur:
                if lo is None or hi is None or out is None:
                    continue
                if float(hi) < float(lo):
                    raise arcpy.ExecuteError(
                        "The remap table {} has a backwards range ({:g} to "
                        "{:g}).".format(os.path.basename(path),
                                        float(lo), float(hi)))
                rows.append((float(lo), float(hi), float(out)))
    elif all(k in have for k in _TABLE_VALUE_FIELDS):
        fields = [have[k] for k in _TABLE_VALUE_FIELDS]
        with arcpy.da.SearchCursor(path, fields) as cur:
            for v, out in cur:
                if v is None or out is None:
                    continue
                rows.append((float(v), float(v), float(out)))
    else:
        raise arcpy.ExecuteError(
            "The remap table {} does not have the expected fields: either "
            "FromValue / ToValue / NewValue (reclassify by range) or "
            "OriginalValue / NewValue (reclassify by value). Create remap "
            "tables with the Create HSM Remap Table button on the "
            "ribbon.".format(os.path.basename(path)))
    if not rows:
        raise arcpy.ExecuteError(
            "The remap table {} contains no complete rows.".format(path))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def parse_remap_source(path):
    """Parse a remap source of either kind: an original-format ASCII file
    on disk, or a geodatabase remap table."""
    path = str(path)
    if os.path.isfile(path):
        return parse_remap_file(path)
    return parse_remap_table(path)


def _apply_remap(a, valid, rows, label, messages, unit=""):
    """Map ``a`` through ``rows`` (first-match). Returns ``(scores, matched)``.

    ``matched`` excludes valid cells that hit no remap line; those are
    reported and become NoData (the original's "NODATA" option).
    """
    scores = np.zeros(a.shape, dtype=np.float32)
    unassigned = valid.copy()
    if len(rows) > 1 and all(lo == hi for lo, hi, _out in rows):
        # Value-only remaps (the remap builder's List Unique Values can
        # emit thousands of rows): one vectorized sorted lookup instead of
        # one full-grid pass per row. Rows are ascending by construction
        # (ASCII files are validated ascending; tables are sorted), and
        # searchsorted's 'left' side lands on the FIRST occurrence of a
        # duplicated value, preserving the documented first-match rule.
        vals = np.asarray([r[0] for r in rows], dtype=np.float64)
        outs = np.asarray([r[2] for r in rows], dtype=np.float32)
        idx = np.searchsorted(vals, a.ravel()).reshape(a.shape)
        np.clip(idx, 0, len(vals) - 1, out=idx)
        with np.errstate(invalid="ignore"):
            hit = unassigned & (vals[idx] == a)
        scores[hit] = outs[idx[hit]]
        unassigned &= ~hit
    else:
        for lo, hi, out in rows:
            with np.errstate(invalid="ignore"):
                m = unassigned & (a >= lo) & (a <= hi)
            scores[m] = np.float32(out)
            unassigned &= ~m
    n_miss = int(unassigned.sum())
    if n_miss:
        _warn(messages,
              "{:,} cells of {} matched no remap line and became NoData "
              "(the original's NODATA missing-values behavior). Extend the "
              "remap file if those cells should score.".format(n_miss, label))
    return scores, valid & ~unassigned


# ---------------------------------------------------------------------------
# grid helpers
# ---------------------------------------------------------------------------
def _same_grid(gt_a, proj_a, w_a, h_a, gt_b, proj_b, w_b, h_b):
    if (w_a, h_a) != (w_b, h_b):
        return False
    tol = max(abs(gt_a[1]), abs(gt_a[5])) * 1e-6
    if any(abs(x - y) > tol for x, y in zip(gt_a, gt_b)):
        return False
    return (proj_a or "") == (proj_b or "") or not proj_a or not proj_b


def _read_on_grid(gdal, src, gt, proj, w, h, label, messages):
    """Band 1 of ``src`` as ``(array, valid)`` on the reference grid,
    resampling (nearest) when the grids differ."""
    s_gt = src.GetGeoTransform()
    s_proj = src.GetProjection()
    if _same_grid(gt, proj, w, h, s_gt, s_proj, src.RasterXSize,
                  src.RasterYSize):
        if (s_proj or "") != (proj or ""):
            # Same dimensions and geotransform but one side has no recorded
            # coordinate system: grid agreement cannot actually be verified.
            _warn(messages,
                  "{} (or the reference raster) has no coordinate system "
                  "recorded, so grid agreement cannot be verified -- the "
                  "rasters are combined cell-for-cell on matching "
                  "dimensions alone. Confirm they really share one "
                  "grid.".format(label))
        band = src.GetRasterBand(1)
        a = band.ReadAsArray(0, 0, w, h)
        nod = band.GetNoDataValue()
        mask_band = band        # honor the source's per-pixel validity mask
    else:
        _say(messages,
             "{} is on a different grid than the first factor and is being "
             "resampled onto it (nearest neighbour).".format(label))
        xmin = gt[0]
        ymax = gt[3]
        xmax = xmin + gt[1] * w
        ymin = ymax + gt[5] * h
        # Hold the warped MEM dataset in a local until fully read -- a
        # chained expression here returns silent NaN (GDAL lifetime trap).
        # dstNodata is REQUIRED: without it the MEM destination is
        # zero-filled, and for a source with no NoData tag those off-extent
        # fill cells would pass the valid mask as real score-0 data.
        warped = gdal.Warp(
            "", src, format="MEM", dstSRS=(proj or None),
            outputBounds=(xmin, min(ymin, ymax), xmax, max(ymin, ymax)),
            width=w, height=h, resampleAlg="near",
            dstNodata=float(_tt.NODATA_OUT))
        band = warped.GetRasterBand(1)
        a = band.ReadAsArray(0, 0, w, h)
        nod = band.GetNoDataValue()
        # Warp already wrote dstNodata into source-masked cells, so the
        # nod screen below covers them; the MEM band has no mask to read.
        mask_band = None
        warped = None
    a = np.asarray(a, dtype=np.float64)
    valid = np.isfinite(a)
    if nod is not None:
        with np.errstate(invalid="ignore"):
            valid &= (a != np.float64(nod))
    with np.errstate(invalid="ignore"):
        valid &= (a != np.float64(_tt.NODATA_OUT))
    if mask_band is not None:
        valid = _tt.mask_band_valid(gdal, mask_band, valid)
    return a, valid


def _critical_presence_on_grid(gdal, src, gt, proj, w, h, label, scratch,
                               messages):
    """Boolean (h, w): where the critical-feature RASTER is present, on the
    HSM grid.

    Presence is decided on the SOURCE grid and aggregated to the HSM grid
    with MAX ("any presence inside the target cell"). Nearest-neighbor
    sampling -- the value-factor resample `_read_on_grid` uses -- THINNED
    fine linear features: a 5 m stream mask on a 30 m HSM kept only the
    source cells under each target cell's center (~1 in 6), so the
    distance transform measured to a dotted line and the distance bands
    were systematically wrong exactly along the feature.

    One strip pass over the source builds a coded Byte grid (0 = NoData,
    1 = valid zero, 2 = valid nonzero) and collects the distinct values.
    A two-valued 0/x raster treats 0 as background (presence = code 2);
    anything else counts every data cell (presence = code >= 1), with the
    same >50% sanity warning as before, now computed on source cells.
    MAX-aggregation is exact for both codings: a target cell holding any
    nonzero source cell aggregates to 2, one holding only zeros to 1.
    """
    import arcpy

    sgt = src.GetGeoTransform()
    sw, sh = src.RasterXSize, src.RasterYSize
    band = src.GetRasterBand(1)
    nod = band.GetNoDataValue()
    try:
        mask = band.GetMaskBand()
    except Exception:                                            # noqa: BLE001
        mask = None

    same = (sw == w and sh == h and all(
        abs(sgt[i] - gt[i]) <= 1e-6 * max(1.0, abs(gt[i]))
        for i in range(6)))

    drv = gdal.GetDriverByName("GTiff")
    code_path = None
    if same:
        codes = np.zeros((sh, sw), dtype=np.uint8)
        code_ds = None
    else:
        code_path = arcpy.CreateUniqueName("hsm_critpres.tif", scratch)
        code_ds = drv.Create(code_path, sw, sh, 1, gdal.GDT_Byte,
                             options=["TILED=YES", "COMPRESS=LZW",
                                      "BIGTIFF=IF_SAFER"])
        code_ds.SetGeoTransform(sgt)
        try:
            code_ds.SetProjection(src.GetProjection())
        except Exception:                                        # noqa: BLE001
            pass
        code_band = code_ds.GetRasterBand(1)
        codes = None

    distinct = set()
    many = False
    n_valid = 0
    n_nonzero = 0
    slab = max(1, int(8_000_000 // max(1, sw)))
    for r0 in range(0, sh, slab):
        nr = min(slab, sh - r0)
        a = np.asarray(band.ReadAsArray(0, r0, sw, nr), dtype=np.float64)
        v = np.isfinite(a)
        if nod is not None:
            with np.errstate(invalid="ignore"):
                v &= (a != np.float64(nod))
        with np.errstate(invalid="ignore"):
            v &= (a != np.float64(_tt.NODATA_OUT))
        if mask is not None:
            try:
                mv = mask.ReadAsArray(0, r0, sw, nr)
                if mv is not None:
                    v &= (mv != 0)
            except Exception:                                    # noqa: BLE001
                pass
        strip = np.zeros(a.shape, dtype=np.uint8)
        nz = v & (a != 0.0)
        strip[v] = 1
        strip[nz] = 2
        n_valid += int(v.sum())
        n_nonzero += int(nz.sum())
        if not many:
            distinct.update(np.unique(a[v]).tolist())
            if len(distinct) > 3:
                many = True
                distinct.clear()     # only the <=2-distinct case matters
        if same:
            codes[r0:r0 + nr] = strip
        else:
            code_band.WriteArray(strip, 0, r0)
        del a, v, nz, strip

    if not same:
        code_band.FlushCache()
        code_ds.FlushCache()
        code_band = None
        code_ds = None
        _say(messages,
             "{} is on a different grid than the HSM and is aggregated "
             "onto it (a cell counts as the feature when ANY source "
             "presence falls inside it).".format(label))
        xmin, ymax = gt[0], gt[3]
        xmax = xmin + gt[1] * w
        ymin = ymax + gt[5] * h
        code_src = gdal.Open(code_path)
        # No dstNodata: declaring 0 NoData made GDAL "helpfully" rewrite
        # source code-0 cells to 1 (flooding presence everywhere); the MEM
        # destination zero-initializes, so uncovered ground is code 0
        # (absent) with no declaration needed.
        warped = gdal.Warp(
            "", code_src, format="MEM", dstSRS=(proj or None),
            outputBounds=(xmin, min(ymin, ymax), xmax, max(ymin, ymax)),
            width=w, height=h, resampleAlg="max")
        codes = warped.GetRasterBand(1).ReadAsArray(0, 0, w, h)
        warped = None
        code_src = None
        try:
            arcpy.management.Delete(code_path)
        except Exception:                                        # noqa: BLE001
            pass

    zero_coded = (not many and len(distinct) == 2
                  and any(d == 0.0 for d in distinct))
    if zero_coded:
        other = [d for d in distinct if d != 0.0][0]
        _say(messages,
             "The critical-feature raster is coded {:g}/0; cells with "
             "value 0 are treated as background, not part of the "
             "feature.".format(float(other)))
        target = codes >= 2
    else:
        # not 0/x-coded: every data cell counts as the feature
        target = codes >= 1
        if n_valid:
            _warn(messages,
                  "All {:,} data cells count as the critical feature. If "
                  "this raster stores background as a data value, "
                  "distances will be wrong -- make the background NoData "
                  "first.".format(n_valid))
    return target


def _deliver(gdal, out, valid, gt, proj, w, h, out_path, scratch, messages,
             summary, description, title=None):
    """Scratch GeoTIFF + streamed statistics + CopyRaster delivery, the same
    shape every raster tool in the suite uses."""
    import arcpy

    tmp = arcpy.CreateUniqueName("hsm.tif", scratch)
    ds_out, oband = _tt._make_output(gdal, tmp, gt, proj, w, h)
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
        title=title or os.path.basename(str(out_path)),
        summary=summary, description=description + " " + _CD_CITATION,
        tags=_TAGS, citation=_CD_CITATION)


def _combine_core(gdal, opened, weights, labels, method, gt, proj, w, h,
                  messages, remaps=None, save_dir=None, saved_paths=None,
                  scratch=None):
    """The shared weighted-combine loop over already-opened factor datasets.

    ``remaps`` (run_hsm only): per-factor parsed remap rows; None means the
    factor is already on the 0-100 scale (run_combine). When ``save_dir`` is
    set each reclassified factor is also written there as ``<base>_r``
    (``saved_paths`` collects them).
    """
    import arcpy

    geometric = (method == GEOMETRIC)
    acc = np.ones((h, w), dtype=np.float64) if geometric \
        else np.zeros((h, w), dtype=np.float64)
    valid_all = np.ones((h, w), dtype=bool)
    used_names = set()

    for i, (src, weight, label) in enumerate(zip(opened, weights, labels)):
        _mh.phase_advance()
        a, valid = _read_on_grid(gdal, src, gt, proj, w, h, label, messages)
        if remaps is not None:
            scores, matched = _apply_remap(a, valid, remaps[i], label,
                                           messages)
            valid = matched
            if save_dir:
                name = _lfm.safe_name(os.path.splitext(
                    os.path.basename(label))[0])[:28] + "_r"
                if name.lower() in used_names:
                    name = "{}_{}".format(name, i + 1)
                used_names.add(name.lower())
                rp = _mh.resolve_raster_output(
                    os.path.join(save_dir, name), messages)
                keep = scores.astype(np.float32)
                keep[~valid] = np.float32(_tt.NODATA_OUT)
                _deliver(gdal, keep, valid, gt, proj, w, h, rp, scratch,
                         messages,
                         summary=("Reclassified habitat factor {} (0-100 "
                                  "suitability scores), an intermediate of "
                                  "the Habitat Suitability Model tool, "
                                  "reusable in Combine Habitat "
                                  "Factors.").format(label),
                         description=(
                             "Habitat factor reclassified onto the 0-100 "
                             "suitability scale with an ASCII remap file, "
                             "saved for reuse in the Combine Habitat "
                             "Factors tool."))
                if saved_paths is not None:
                    saved_paths.append(rp)
                del keep
        else:
            scores = a
            n_out = int((valid & ((a < 0) | (a > 100))).sum())
            if n_out:
                _warn(messages,
                      "{:,} cells of {} lie outside the 0-100 suitability "
                      "scale this tool expects (already-reclassified "
                      "factors). The combine runs anyway, but check the "
                      "input.".format(n_out, label))
        wgt = weight / 100.0
        if geometric:
            with np.errstate(invalid="ignore"):
                term = np.power(np.maximum(scores, 0.0), wgt,
                                dtype=np.float64)
            neg = valid & (scores < 0)
            n_neg = int(neg.sum())
            if n_neg:
                _warn(messages,
                      "{:,} cells of {} are NEGATIVE; a fractional power of "
                      "a negative number is undefined, so those cells "
                      "become NoData in the geometric model.".format(
                          n_neg, label))
                valid &= ~neg
            acc *= term
            del term
        else:
            acc += np.asarray(scores, dtype=np.float64) * wgt
        valid_all &= valid
        del a, scores, valid
    return acc, valid_all


def _weights_ok(weights):
    import arcpy

    for wt in weights:
        if wt <= 0:
            raise arcpy.ExecuteError(
                "Every factor weight must be a positive number (got "
                "{}).".format(wt))
    total = sum(weights)
    if abs(total - 100) > 1e-9:
        raise arcpy.ExecuteError(
            "Your factor weights sum to {:g}, not 100! Check your math and "
            "try again.".format(total))


# ---------------------------------------------------------------------------
# HSM 1 -- reclassify + combine
# ---------------------------------------------------------------------------
def run_hsm(factors, method, out_raster, save_reclassified=False,
            messages=None, saved_paths=None):
    """Build an HSM from raw factor rasters.

    ``factors``: ``[(raster, remap_file, weight)]`` -- any count >= 1;
    integer-ish weights summing to 100. Returns the written output path.
    ``saved_paths`` (optional list) collects the ``<base>_r`` reclassified
    factor rasters written when ``save_reclassified`` is on, so the dialog
    can hand them to a derived output parameter (Pro then adds them to the
    map alongside the model itself).
    """
    import arcpy

    if not factors:
        raise arcpy.ExecuteError("Supply at least one habitat factor.")
    weights = [float(f[2]) for f in factors]
    _weights_ok(weights)
    remaps = [parse_remap_source(f[1]) for f in factors]
    labels = [os.path.basename(str(f[0])) for f in factors]
    if len(factors) == 1:
        _warn(messages,
              "Only one habitat factor was supplied; the 'combined' model "
              "is simply that factor's reclassified scores.")

    out_raster = _mh.resolve_raster_output(out_raster, messages)
    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()
    save_dir = os.path.dirname(out_raster) if save_reclassified else None

    opened = []
    copies = []
    try:
        for f in factors:
            src, cp = _hs._open_source(str(f[0]), scratch, messages)
            opened.append(src)
            if cp:
                copies.append(cp)
        ref = opened[0]
        w, h = ref.RasterXSize, ref.RasterYSize
        if w < 1 or h < 1:
            raise arcpy.ExecuteError("The first factor raster is empty.")
        gt = ref.GetGeoTransform()
        proj = ref.GetProjection()

        _mh.begin_phases(
            [("Opening the factor rasters...", 1)]
            # Each saved reclassified factor consumes a SECOND phase
            # (_deliver -> _tt._deliver_output advances once internally);
            # without budgeting it the bar read "Combining..." by factor
            # 2 and parked at ~100% for the rest of the run.
            + sum(([("Reclassifying factor {} of {}: {}...".format(
                        i + 1, len(factors), lab), 3)]
                   + ([("Saving reclassified factor {}...".format(i + 1),
                        1)] if save_dir else [])
                   for i, lab in enumerate(labels)), [])
            + [("Combining habitat factors...", 2),
               ("Saving the output raster...", 2)])
        _say(messages,
             "Combining {} habitat factor{} using the {} model; weights "
             "are normalized to sum to 1 (w/100).".format(
                 len(factors), "" if len(factors) == 1 else "s", method))

        saved = [] if saved_paths is None else saved_paths
        acc, valid_all = _combine_core(
            gdal, opened, weights, labels, method, gt, proj, w, h, messages,
            remaps=remaps, save_dir=save_dir, saved_paths=saved,
            scratch=scratch)
        _mh.phase_advance()
        out = np.full((h, w), np.float32(_tt.NODATA_OUT))
        out[valid_all] = acc[valid_all].astype(np.float32)
        del acc
        _mh.phase_advance()
        _deliver(gdal, out, valid_all, gt, proj, w, h, out_raster, scratch,
                 messages,
                 summary=(
                     "Habitat suitability model (0-100) combined from {n} "
                     "factor{s} by weighted {m}: {f}.").format(
                     n=len(factors), s="" if len(factors) == 1 else "s",
                     m=method.lower(),
                     f="; ".join("{} (weight {:g}, remap {})".format(
                         lab, wt, os.path.basename(str(fac[1])))
                         for lab, wt, fac in zip(labels, weights, factors))),
                 description=(
                     "Habitat suitability model on the 0-100 scale (100 = "
                     "best habitat), built by reclassifying each habitat "
                     "factor with an ASCII remap file and combining by "
                     "weighted {} mean. A modernized port of the "
                     "CorridorDesigner Habitat Suitability Model "
                     "tool.").format(
                     "geometric" if method == GEOMETRIC else "additive"))
        del out
        _lnk.write(out_raster, _lnk.hsm_record(
            analysis_id=_lnk.new_analysis_id(),
            tool="Habitat Suitability Model",
            method=("geometric" if method == GEOMETRIC else "additive"),
            factors=[{"path": str(f[0]), "weight": wt,
                      "remap_file": str(f[1])}
                     for f, wt in zip(factors, weights)]), messages)
        if saved:
            _say(messages,
                 "Saved {} reclassified factor raster{}: {}".format(
                     len(saved), "" if len(saved) == 1 else "s",
                     "; ".join(saved)))
        _mh.end_phases()
        return out_raster
    finally:
        # Null EVERY dataset handle before deleting the scratch copies:
        # the locals ref/src still referenced the first/last factor's
        # dataset, so their copies stayed file-locked and Delete failed
        # silently (accumulating multi-hundred-MB scratch GeoTIFFs).
        ref = None
        src = None
        opened = None
        for cp in copies:
            try:
                arcpy.management.Delete(cp)
            except Exception:                                    # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# HSM 2 -- combine already-reclassified factors
# ---------------------------------------------------------------------------
def run_combine(factors, method, out_raster, messages=None):
    """Combine already-reclassified (0-100) factors: ``[(raster, weight)]``."""
    import arcpy

    if not factors:
        raise arcpy.ExecuteError("Supply at least one habitat factor.")
    weights = [float(f[1]) for f in factors]
    _weights_ok(weights)
    labels = [os.path.basename(str(f[0])) for f in factors]
    if len(factors) == 1:
        _warn(messages,
              "Only one factor was supplied; the 'combined' model is simply "
              "that factor scaled by its weight rule.")

    out_raster = _mh.resolve_raster_output(out_raster, messages)
    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()

    opened = []
    copies = []
    try:
        for f in factors:
            src, cp = _hs._open_source(str(f[0]), scratch, messages)
            opened.append(src)
            if cp:
                copies.append(cp)
        ref = opened[0]
        w, h = ref.RasterXSize, ref.RasterYSize
        if w < 1 or h < 1:
            raise arcpy.ExecuteError("The first factor raster is empty.")
        gt = ref.GetGeoTransform()
        proj = ref.GetProjection()

        _mh.begin_phases(
            [("Opening the factor rasters...", 1)]
            + [("Reading factor {} of {}: {}...".format(
                i + 1, len(factors), lab), 2)
               for i, lab in enumerate(labels)]
            + [("Combining habitat factors...", 2),
               ("Saving the output raster...", 2)])
        _say(messages,
             "Combining {} previously reclassified factor{} using the {} "
             "model.".format(len(factors),
                             "" if len(factors) == 1 else "s", method))

        acc, valid_all = _combine_core(
            gdal, opened, weights, labels, method, gt, proj, w, h, messages)
        _mh.phase_advance()
        out = np.full((h, w), np.float32(_tt.NODATA_OUT))
        out[valid_all] = acc[valid_all].astype(np.float32)
        del acc
        _mh.phase_advance()
        _deliver(gdal, out, valid_all, gt, proj, w, h, out_raster, scratch,
                 messages,
                 summary=(
                     "Habitat suitability model (0-100) combined from {n} "
                     "previously reclassified factor{s} by weighted {m}: "
                     "{f}.").format(
                     n=len(factors), s="" if len(factors) == 1 else "s",
                     m=method.lower(),
                     f="; ".join("{} (weight {:g})".format(lab, wt)
                                 for lab, wt in zip(labels, weights))),
                 description=(
                     "Habitat suitability model on the 0-100 scale (100 = "
                     "best habitat), built by combining habitat factors "
                     "already scored on 0-100 by weighted {} mean. A "
                     "modernized port of the CorridorDesigner Combine "
                     "Habitat Factors tool.").format(
                     "geometric" if method == GEOMETRIC else "additive"))
        del out
        _lnk.write(out_raster, _lnk.hsm_record(
            analysis_id=_lnk.new_analysis_id(),
            tool="Combine Habitat Factors",
            method=("geometric" if method == GEOMETRIC else "additive"),
            factors=[{"path": str(f[0]), "weight": wt}
                     for f, wt in zip(factors, weights)]), messages)
        _mh.end_phases()
        return out_raster
    finally:
        # Null EVERY dataset handle before deleting the scratch copies:
        # the locals ref/src still referenced the first/last factor's
        # dataset, so their copies stayed file-locked and Delete failed
        # silently (accumulating multi-hundred-MB scratch GeoTIFFs).
        ref = None
        src = None
        opened = None
        for cp in copies:
            try:
                arcpy.management.Delete(cp)
            except Exception:                                    # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# feature rasterization (shared by the two Modify tools)
# ---------------------------------------------------------------------------
def _read_feature_geoms(in_features, rast_sr, densify_step, messages):
    """SHAPE@ geometries from the ORIGINAL parameter value -- a map layer's
    active selection is honored (the standing per-input-selection rule).
    Geometries are projected onto the RASTER's spatial reference FIRST and
    only then true-curve-densified, so ``densify_step`` (half a cell) is in
    the raster's units -- the standing densify-AFTER-projecting rule; the
    other order densifies in the feature class's own units (degrees vs
    meters) and either adds no vertices at all or millions of them."""
    import arcpy

    geoms = []
    n_curved = 0
    with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as cur:
        for (g,) in cur:
            if g is None:
                continue
            gsr = g.spatialReference
            same = (gsr is None or rast_sr is None
                    or (gsr.factoryCode == rast_sr.factoryCode
                        and gsr.name == rast_sr.name))
            if not same:
                try:
                    g = g.projectAs(rast_sr)
                except Exception:                                # noqa: BLE001
                    pass
            if getattr(g, "hasCurves", False):
                try:
                    g = g.densify("DISTANCE", densify_step, densify_step)
                    n_curved += 1
                except Exception:                                # noqa: BLE001
                    pass
            geoms.append(g)
    if not geoms:
        raise arcpy.ExecuteError(
            "The feature input contains no usable features (empty dataset, "
            "or an active selection selecting nothing).")
    if n_curved:
        _say(messages,
             "{} true-curve feature{} densified before rasterizing.".format(
                 n_curved, " was" if n_curved == 1 else "s were"))
    return geoms


def _rasterize_mask(gdal, geoms, rast_sr, gt, proj, w, h, scratch,
                    all_touched=False):
    """Boolean (H, W) mask of ``geoms`` on the reference grid. Cell-center
    test by default (matching ExtractByMask); ``all_touched`` for lines and
    points so thin features are not missed."""
    import arcpy
    from osgeo import ogr

    sample_sr = geoms[0].spatialReference
    same = (sample_sr is None or rast_sr is None
            or (sample_sr.factoryCode == rast_sr.factoryCode
                and sample_sr.name == rast_sr.name))
    if not same:
        geoms = [g.projectAs(rast_sr) for g in geoms]

    shp = arcpy.CreateUniqueName("hsm_feat.shp", scratch)
    # The scratch features must stay in the RASTER's coordinate system --
    # an Output Coordinate System environment would make CopyFeatures
    # project them, and the rasterized mask would land in the wrong place.
    with _mh.neutral_grid_env():
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
        opts = ["ALL_TOUCHED=TRUE"] if all_touched else []
        gdal.RasterizeLayer(mds, [1], vec.GetLayer(0), burn_values=[1],
                            options=opts)
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


def _carry_record_forward(src_path, out_path, tool, detail, messages):
    """Stamp ``out_path`` with the source's record + a post_processing step
    (same model as Fill Corridor Holes: the output is the SAME analysis
    product, modified). A source with no record gets a fresh HSM record of
    method 'external' so the chain can still pick it up from here on."""
    rec = _lnk.read(src_path, None)
    if rec is None:
        rec = _lnk.hsm_record(
            analysis_id=_lnk.new_analysis_id(), tool=tool,
            method="external", source_raster=str(src_path))
    out_rec = _lnk.add_post_processing_step(rec, tool, detail)
    _lnk.write(out_path, out_rec, messages)


def _selection_note(in_features, messages):
    """Say and return the 'N of M features' note (standing rule: list the
    count selected of the count total)."""
    import arcpy

    try:
        path = arcpy.Describe(in_features).catalogPath
    except Exception:                                            # noqa: BLE001
        path = str(in_features)
    n_total, n_selected, has_sel = _mh.selection_info(in_features, path)
    if has_sel:
        note = "run on a selection of {} of {} features".format(
            n_selected, n_total)
        _say(messages, "The feature input has an active selection: "
             "{}.".format(note))
        return note
    return "all {} features used".format(n_total) if n_total else None


# ---------------------------------------------------------------------------
# Modify HSM 1 -- burn features at a fixed score
# ---------------------------------------------------------------------------
def run_reclassify_features(in_hsm, in_features, new_score, out_raster,
                            messages=None):
    """Assign ``new_score`` to every HSM cell whose center falls inside
    ``in_features`` (a polygon layer; selections honored)."""
    import arcpy

    new_score = float(new_score)
    if not 0 <= new_score <= 100:
        # The dialog blocks this; scripted callers get a warning (the tool
        # still runs -- matching the combine tools' out-of-range policy).
        _warn(messages,
              "The new score {:g} lies outside the 0-100 suitability scale "
              "-- downstream HSM tools expect 0-100.".format(new_score))
    out_raster = _mh.resolve_raster_output(out_raster, messages)
    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()

    src, in_copy = _hs._open_source(str(in_hsm), scratch, messages)
    try:
        w, h = src.RasterXSize, src.RasterYSize
        if w < 1 or h < 1:
            raise arcpy.ExecuteError("The input HSM raster is empty.")
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        _mh.begin_phases([("Reading the input HSM...", 2),
                          ("Rasterizing the features...", 2),
                          ("Applying the new score...", 1),
                          ("Saving the output raster...", 2)])
        band = src.GetRasterBand(1)
        a = np.asarray(band.ReadAsArray(0, 0, w, h), dtype=np.float32)
        nod = band.GetNoDataValue()
        valid = np.isfinite(a)
        if nod is not None:
            with np.errstate(invalid="ignore"):
                valid &= (a != np.float32(nod))
        with np.errstate(invalid="ignore"):
            valid &= (a != np.float32(_tt.NODATA_OUT))
        valid = _tt.mask_band_valid(gdal, band, valid)
        _mh.phase_advance()

        sel_note = _selection_note(in_features, messages)
        step = 0.5 * min(abs(gt[1]), abs(gt[5]))
        rast_sr = arcpy.Describe(str(in_hsm)).spatialReference \
            if proj else None
        geoms = _read_feature_geoms(in_features, rast_sr, step, messages)
        mask = _rasterize_mask(gdal, geoms, rast_sr, gt, proj, w, h, scratch)
        _mh.phase_advance()

        burn = mask & valid
        n_burn = int(burn.sum())
        if not n_burn:
            _warn(messages,
                  "No HSM cells fall inside the features -- the output "
                  "equals the input. Check that the features overlap the "
                  "raster (and any active selection).")
        else:
            _say(messages,
                 "{:,} cells reassigned the score {:g}.".format(
                     n_burn, new_score))
        out = np.where(valid, a, np.float32(_tt.NODATA_OUT)).astype(
            np.float32)
        out[burn] = np.float32(new_score)
        del a, mask, burn
        _mh.phase_advance()
        _deliver(gdal, out, valid, gt, proj, w, h, out_raster, scratch,
                 messages,
                 summary=(
                     "HSM with features reclassified: cells inside {} "
                     "reassigned the suitability score {:g}{}.").format(
                     str(in_features), new_score,
                     "; " + sel_note if sel_note else ""),
                 description=(
                     "Habitat suitability model with digitized features "
                     "burned in at a fixed suitability score -- for "
                     "influences the base factors could not capture "
                     "(workshop book section 2.6). A modernized port of "
                     "the CorridorDesigner Reclassify Features tool."))
        del out, valid
        detail = "cells inside {} set to {:g}{}".format(
            os.path.basename(str(in_features)), new_score,
            "; " + sel_note if sel_note else "")
        _carry_record_forward(str(in_hsm), out_raster,
                              "Reclassify Features in HSM", detail, messages)
        _mh.end_phases()
        return out_raster
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:                                    # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Modify HSM 2 -- distance-banded critical feature
# ---------------------------------------------------------------------------
def run_critical_feature(in_hsm, in_critical, bands, out_raster,
                         messages=None):
    """Scale the HSM by distance-band percentages from ``in_critical``.

    ``bands``: ``[(from, to, percent)]`` in METERS, ascending; each cell's
    HSM value is multiplied by percent/100 for the band its distance to the
    nearest critical-feature cell falls in. Distances are computed in meters
    regardless of the raster's coordinate system: projected linear units are
    converted via metersPerUnit, and a geographic raster uses its true
    spheroidal cell dimensions at the middle row (the distance transform
    needs one constant cell size per axis, so east-west size is the middle
    row's -- fine for typical study areas, warned about when the raster
    spans enough latitude to matter). Distances beyond the last band leave
    the HSM unchanged, with a warning (see module notes: the original
    silently multiplied by raw distance there).
    """
    import arcpy
    from scipy import ndimage

    out_raster = _mh.resolve_raster_output(out_raster, messages)
    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()

    bands = [(float(lo), float(hi), float(pc)) for lo, hi, pc in bands]
    if not bands:
        raise arcpy.ExecuteError("Supply at least one distance band.")
    for lo, hi, pc in bands:
        if hi < lo:
            raise arcpy.ExecuteError(
                "The distance band {:g} to {:g} runs backwards.".format(
                    lo, hi))
        if pc < 0:
            raise arcpy.ExecuteError(
                "The percentage for band {:g}-{:g} is negative.".format(
                    lo, hi))
        if pc > 100:
            # warn-and-run, matching run_reclassify_features' policy: the
            # dialog enforces 0-100, but scripted callers bypass it, and a
            # silent 800 (a typo for 80) poisons every 0-100 consumer.
            _warn(messages,
                  "The percentage for band {:g}-{:g} is {:g} -- above 100, "
                  "so cells there will EXCEED the 0-100 suitability "
                  "scale.".format(lo, hi, pc))
    for i in range(1, len(bands)):
        if bands[i][0] < bands[i - 1][0] or bands[i][1] < bands[i - 1][1]:
            raise arcpy.ExecuteError(
                "The distance bands must be in ascending order.")

    src, in_copy = _hs._open_source(str(in_hsm), scratch, messages)
    crit_ds = None
    crit_copy = None
    try:
        w, h = src.RasterXSize, src.RasterYSize
        if w < 1 or h < 1:
            raise arcpy.ExecuteError("The input HSM raster is empty.")
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        _mh.begin_phases([("Reading the input HSM...", 2),
                          ("Locating the critical feature...", 2),
                          ("Computing distances...", 3),
                          ("Applying the distance bands...", 1),
                          ("Saving the output raster...", 2)])
        band = src.GetRasterBand(1)
        a = np.asarray(band.ReadAsArray(0, 0, w, h), dtype=np.float32)
        nod = band.GetNoDataValue()
        valid = np.isfinite(a)
        if nod is not None:
            with np.errstate(invalid="ignore"):
                valid &= (a != np.float32(nod))
        with np.errstate(invalid="ignore"):
            valid &= (a != np.float32(_tt.NODATA_OUT))
        valid = _tt.mask_band_valid(gdal, band, valid)
        _mh.phase_advance()

        hsm_desc = arcpy.Describe(str(in_hsm))
        sr = hsm_desc.spatialReference if proj else None
        # Distances (and the user's bands) are METERS. The EDT below takes
        # one constant cell size per axis, so convert the grid's cell size
        # to meters first.
        cellw, cellh = abs(gt[1]), abs(gt[5])
        sr_type = getattr(sr, "type", "") if sr is not None else ""
        if sr_type == "Geographic":
            a_ax = float(getattr(sr, "semiMajorAxis", 0) or 6378137.0)
            b_ax = float(getattr(sr, "semiMinorAxis", 0)
                         or 6356752.31424518)
            d = _hs._geographic_row_distances(gt, h, a_ax, b_ax)
            mid = h // 2
            cellw_m = float(d["h"][mid])
            cellh_m = float((d["vA"][mid] + d["vB"][mid]) / 2.0)
            _say(messages,
                 "Geographic HSM: distances are measured in meters using "
                 "the spheroidal cell size at the raster's middle row "
                 "({:.2f} x {:.2f} m).".format(cellw_m, cellh_m))
            w_all = np.asarray(d["h"], dtype=np.float64)
            spread = (float(w_all.max() - w_all.min()) / cellw_m
                      if cellw_m > 0 else 0.0)
            if spread > 0.05:
                _warn(messages,
                      "East-west cell size varies {:.0f}% between the "
                      "raster's north and south edges, but distances use "
                      "the middle row's size everywhere -- band edges near "
                      "the top and bottom are off by up to about half "
                      "that. For a raster spanning this much latitude, "
                      "consider projected data.".format(spread * 100.0))
        elif sr_type == "Projected":
            mpu = float(getattr(sr, "metersPerUnit", 1.0) or 1.0)
            cellw_m, cellh_m = cellw * mpu, cellh * mpu
            if abs(mpu - 1.0) > 1e-9:
                _say(messages,
                     "Distance bands are in meters; the raster's linear "
                     "unit is {} ({:g} m), converted "
                     "automatically.".format(
                         getattr(sr, "linearUnitName", "unknown"), mpu))
        else:
            cellw_m, cellh_m = cellw, cellh
            _warn(messages,
                  "The HSM has no usable coordinate system, so cell sizes "
                  "cannot be converted -- band distances are compared in "
                  "raw map units, ASSUMED to be meters.")

        # The critical feature: a raster (data cells are the feature) or a
        # feature layer (rasterized; selections honored).
        is_raster = False
        sel_note = None
        try:
            dtype = arcpy.Describe(in_critical).dataType
            is_raster = dtype in ("RasterDataset", "RasterLayer",
                                  "RasterBand")
        except Exception:                                        # noqa: BLE001
            pass
        if is_raster:
            crit_ds, crit_copy = _hs._open_source(str(in_critical), scratch,
                                                  messages)
            # Presence decided on the SOURCE grid and MAX-aggregated onto
            # the HSM grid -- nearest sampling thinned fine linear masks
            # (see _critical_presence_on_grid).
            target = _critical_presence_on_grid(
                gdal, crit_ds, gt, proj, w, h,
                os.path.basename(str(in_critical)), scratch, messages)
        else:
            sel_note = _selection_note(in_critical, messages)
            step = 0.5 * min(abs(gt[1]), abs(gt[5]))
            geoms = _read_feature_geoms(in_critical, sr, step, messages)
            shape_type = getattr(geoms[0], "type", "polygon").lower()
            target = _rasterize_mask(
                gdal, geoms, sr, gt, proj, w, h, scratch,
                all_touched=(shape_type in ("polyline", "point",
                                            "multipoint")))
        if not target.any():
            raise arcpy.ExecuteError(
                "The critical feature does not overlap the HSM raster at "
                "all -- every cell's distance would be undefined.")
        _mh.phase_advance()

        dist = ndimage.distance_transform_edt(
            ~target, sampling=(cellh_m, cellw_m))
        del target
        _mh.phase_advance()

        pct = np.full((h, w), np.nan, dtype=np.float64)
        unassigned = np.ones((h, w), dtype=bool)
        for lo, hi, pc in bands:
            m = unassigned & (dist >= lo) & (dist <= hi)
            pct[m] = pc
            unassigned &= ~m
        # Diagnose the three distinct ways a cell can miss every band --
        # closer than the first band, in a gap between bands, or beyond the
        # last -- each with its own fix; lumping them under 'beyond' pointed
        # users at a final far-out band that would not help the other two.
        miss = unassigned & valid
        below = miss & (dist < bands[0][0])
        beyond = miss & (dist > bands[-1][1])
        gap = miss & ~below & ~beyond
        n_below, n_gap, n_beyond = (int(below.sum()), int(gap.sum()),
                                    int(beyond.sum()))
        if n_below:
            _warn(messages,
                  "{:,} cells are CLOSER than the first band's start "
                  "({:g}) -- including any cells inside the critical "
                  "feature itself -- and were left UNCHANGED (multiplier "
                  "100%). Start the first band at 0 to control "
                  "them.".format(n_below, bands[0][0]))
        if n_gap:
            _warn(messages,
                  "{:,} cells fall in GAPS between your distance bands and "
                  "were left UNCHANGED (multiplier 100%). Make each band "
                  "start where the previous one ends.".format(n_gap))
        if n_beyond:
            _warn(messages,
                  "{:,} cells lie beyond every distance band and were left "
                  "UNCHANGED (multiplier 100%). The original tool silently "
                  "multiplied such cells by their raw distance; add a final "
                  "band out to a very large distance to control this "
                  "explicitly (the workshop book's examples end with one, "
                  "e.g. '100 999999 : 0').".format(n_beyond))
        pct[unassigned] = 100.0
        del dist, unassigned, miss, below, beyond, gap

        out = np.full((h, w), np.float32(_tt.NODATA_OUT))
        out[valid] = (a[valid].astype(np.float64)
                      * pct[valid] / 100.0).astype(np.float32)
        del a, pct
        _mh.phase_advance()
        band_txt = "; ".join(
            "{:g}-{:g} m: {:g}%".format(lo, hi, pc) for lo, hi, pc in bands)
        _deliver(gdal, out, valid, gt, proj, w, h, out_raster, scratch,
                 messages,
                 summary=(
                     "HSM scaled by distance from the critical feature {} "
                     "(bands, meters: {}){}.").format(
                     str(in_critical), band_txt,
                     "; " + sel_note if sel_note else ""),
                 description=(
                     "Habitat suitability model multiplied by "
                     "distance-band percentages from a critical habitat "
                     "feature (workshop book section 2.6, e.g. a riparian "
                     "species scored 100% within 30 m of water, 0% beyond "
                     "100 m). A modernized port of the CorridorDesigner "
                     "Critical Feature tool."))
        del out, valid
        detail = "scaled by distance from {} (bands {}{})".format(
            os.path.basename(str(in_critical)), band_txt,
            "; " + sel_note if sel_note else "")
        _carry_record_forward(str(in_hsm), out_raster,
                              "Critical Habitat Feature", detail, messages)
        _mh.end_phases()
        return out_raster
    finally:
        src = None
        crit_ds = None
        for cp in (in_copy, crit_copy):
            if cp:
                try:
                    arcpy.management.Delete(cp)
                except Exception:                                # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# HSM 3 -- normalize an external HSM to 0-100
# ---------------------------------------------------------------------------
def run_normalize(in_raster, worst, best, out_raster, clamp=True,
                  messages=None):
    """Linear stretch: ``((x - worst) / (best - worst)) * 100``.

    ``worst`` may exceed ``best`` (a model where LOW values mean good
    habitat inverts naturally, as in the original). ``clamp`` (default on,
    a documented improvement) pins results to 0-100.
    """
    import arcpy

    worst = float(worst)
    best = float(best)
    if worst == best:
        raise arcpy.ExecuteError(
            "You set the values for worst and best habitat equal!")
    out_raster = _mh.resolve_raster_output(out_raster, messages)
    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()

    src, in_copy = _hs._open_source(str(in_raster), scratch, messages)
    try:
        w, h = src.RasterXSize, src.RasterYSize
        if w < 1 or h < 1:
            raise arcpy.ExecuteError("The input raster is empty.")
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        _mh.begin_phases([("Reading the input raster...", 2),
                          ("Stretching to 0-100...", 2),
                          ("Saving the output raster...", 2)])
        band = src.GetRasterBand(1)
        a = np.asarray(band.ReadAsArray(0, 0, w, h), dtype=np.float64)
        nod = band.GetNoDataValue()
        valid = np.isfinite(a)
        if nod is not None:
            with np.errstate(invalid="ignore"):
                valid &= (a != np.float64(nod))
        with np.errstate(invalid="ignore"):
            valid &= (a != np.float64(_tt.NODATA_OUT))
        valid = _tt.mask_band_valid(gdal, band, valid)
        _mh.phase_advance()

        stretched = (a - worst) / (best - worst) * 100.0
        n_out = int((valid & ((stretched < 0) | (stretched > 100))).sum())
        if n_out:
            if clamp:
                _say(messages,
                     "{:,} cells fell outside 0-100 (values beyond the "
                     "worst/best anchors) and were clamped.".format(n_out))
                np.clip(stretched, 0.0, 100.0, out=stretched)
            else:
                _warn(messages,
                      "{:,} cells fell outside 0-100 (values beyond the "
                      "worst/best anchors) and were written as-is; "
                      "downstream HSM tools expect 0-100.".format(n_out))
        out = np.full((h, w), np.float32(_tt.NODATA_OUT))
        out[valid] = stretched[valid].astype(np.float32)
        del a, stretched
        _mh.phase_advance()
        _deliver(gdal, out, valid, gt, proj, w, h, out_raster, scratch,
                 messages,
                 summary=(
                     "Existing HSM {} normalized onto the 0-100 suitability "
                     "scale (worst {:g} -> 0, best {:g} -> 100{}).").format(
                     str(in_raster), worst, best,
                     ", clamped" if clamp else ""),
                 description=(
                     "Externally built habitat suitability model linearly "
                     "stretched onto the CorridorDesigner 0-100 scale "
                     "(100 = best habitat) so it can feed the Modify HSM "
                     "and corridor tools. A modernized port of the "
                     "CorridorDesigner Normalize Existing HSM tool."))
        del out, valid
        # A fresh analysis id (Normalize CREATES a model), but record the
        # source's own id when it has one, so the Data Report's
        # regenerated-source staleness check can work.
        src_rec = _lnk.read(str(in_raster), None)
        _lnk.write(out_raster, _lnk.hsm_record(
            analysis_id=_lnk.new_analysis_id(),
            tool="Normalize Existing HSM", method="normalized",
            source_raster=str(in_raster),
            source_analysis_id=(src_rec or {}).get("analysis_id"),
            params={"worst": worst, "best": best, "clamp": bool(clamp)}),
            messages)
        _mh.end_phases()
        return out_raster
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:                                    # noqa: BLE001
                pass


def _patch_map_lyrx(out_fc, messages):
    """A .lyrx beside ``out_fc`` symbolizing the patches by Unique Values on
    Class_Name (fixed three-class palette; matched to Jeff's reference
    Patches_Black_Bear_HSI_v5.lyrx, 2026-08-15: fills only -- stroke width
    0 -- and the smallest class LABELED "Smaller than Breeding Patch" in
    the legend while its Class_Name VALUE stays "Too small"), built the
    same way the termini tool builds its layer file: save a real layer
    file from the output and replace its renderer. Returns the .lyrx
    path, or None on any failure."""
    import json
    import arcpy

    try:
        ws = os.path.dirname(str(out_fc))
        name = os.path.splitext(os.path.basename(str(out_fc)))[0]
        # The question is "is out_fc INSIDE a gdb container" (feature
        # datasets count: x.gdb\fds\fc), not "is the immediate parent a
        # workspace" -- a feature-dataset output made lyr_dir a path
        # inside the gdb, where no filesystem directory exists, and
        # SaveToLayerFile failed. Walk up to the container, place the
        # .lyrx beside it.
        lyr_dir = ws
        if _mh.in_gdb_container(ws):
            probe = ws
            while probe and not _mh.is_gdb_workspace(probe):
                parent = os.path.dirname(probe)
                if parent == probe:
                    break
                probe = parent
            if _mh.is_gdb_workspace(probe):
                lyr_dir = os.path.dirname(probe)
        out_lyrx = os.path.join(lyr_dir, name + "_symbology.lyrx")

        legend_labels = {1: "Smaller than Breeding Patch",
                         2: PATCH_CLASS_NAMES[2], 3: PATCH_CLASS_NAMES[3]}
        classes = []
        for cls in (3, 2, 1):                    # best habitat first
            value = PATCH_CLASS_NAMES[cls]
            (fr, fg, fb), (sr_, sg, sb) = _PATCH_COLORS[value]
            classes.append({
                "type": "CIMUniqueValueClass", "label": legend_labels[cls],
                "patch": "Default",
                "visible": True,
                "values": [{"type": "CIMUniqueValue",
                            "fieldValues": [value]}],
                "symbol": {
                    "type": "CIMSymbolReference",
                    "symbol": {
                        "type": "CIMPolygonSymbol",
                        "symbolLayers": [
                            {"type": "CIMSolidStroke", "enable": True,
                             "capStyle": "Round", "joinStyle": "Round",
                             "lineStyle3D": "Strip", "miterLimit": 4,
                             "width": 0, "height3D": 1,
                             "anchor3D": "Center",
                             "color": {"type": "CIMRGBColor",
                                       "values": [sr_, sg, sb, 100]}},
                            {"type": "CIMSolidFill", "enable": True,
                             "color": {"type": "CIMRGBColor",
                                       "values": [fr, fg, fb, 100]}},
                        ],
                        "angleAlignment": "Map"}}})
        renderer = {"type": "CIMUniqueValueRenderer",
                    "sampleSize": 10000,
                    "defaultSymbolPatch": "Default",
                    "fields": ["Class_Name"],
                    "groups": [{"type": "CIMUniqueValueGroup",
                                "heading": "Patch class",
                                "classes": classes}],
                    "isDefaultSymbolVisible": True,
                    "polygonSymbolColorTarget": "Fill"}

        tmp = "jenness_patchmap_tmp_layer"
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
            layer["featureTable"]["displayField"] = "Class_Name"
        with open(out_lyrx, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        return out_lyrx
    except Exception as e:                                       # noqa: BLE001
        _warn(messages, "Could not create the symbology layer file: "
                        "{}".format(e))
        return None


def run_patch_map(in_hsm, out_fc, radius, radius_units, threshold,
                  breeding_ha, population_ha, messages=None):
    """Create the habitat patches of ``in_hsm``.

    The CorridorDesigner PatchConfiguration port: the HSM is averaged over
    a circular moving window (mean of the data cells, the original's Focal
    Statistics MEAN with DATA), cells whose average meets ``threshold``
    become suitable habitat, suitable cells are grouped into patches
    (edge- OR corner-touching cells connect, the original's RegionGroup
    EIGHT), and each patch is classified by its GEODESIC area in hectares:
    below ``breeding_ha`` -> class 1 "Too small", at least ``breeding_ha``
    but below ``population_ha`` -> class 2 "Breeding patch", at least
    ``population_ha`` -> class 3 "Population patch". (The original counted
    planar pixels and assumed a meters projection; geodesic areas hold on
    any coordinate system, including geographic.) Writes one polygon per
    patch with PatchID/Class/Class_Name/Area_Ha and returns the symbology
    .lyrx path (or None).

    ``radius``/``radius_units``: circular window radius; units from
    hillshade.RADIUS_UNITS ("Cells" or linear units; a geographic HSM
    sizes the window per row on the spheroid). The original species files
    used e.g. 'Circle 200 Map' or a 3x3 rectangle (~ radius 1 cell).
    """
    import arcpy
    from osgeo import ogr
    from scipy import ndimage
    import land_facet_termini as _lft
    import tool_metadata as _tm

    radius = float(radius)
    threshold = float(threshold)
    breeding_ha = float(breeding_ha)
    population_ha = float(population_ha)
    if radius <= 0:
        raise arcpy.ExecuteError("The moving-window radius must be positive.")
    if radius_units not in _hs.RADIUS_UNITS:
        raise arcpy.ExecuteError(
            "Unknown radius unit: {}.".format(radius_units))
    if not 0 <= threshold <= 100:
        raise arcpy.ExecuteError(
            "The habitat quality threshold must be between 0 and 100 "
            "(the HSM suitability scale).")
    if breeding_ha <= 0:
        raise arcpy.ExecuteError(
            "The minimum breeding patch size must be positive.")
    if population_ha < breeding_ha:
        raise arcpy.ExecuteError(
            "The minimum population patch size ({:g} ha) cannot be smaller "
            "than the minimum breeding patch size ({:g} ha).".format(
                population_ha, breeding_ha))

    out_fc = str(out_fc).rstrip("\\/")
    # Folder outputs need a real format: extensionless becomes a shapefile
    # explicitly, so every later arcpy call resolves the same dataset (the
    # suite's standing folder-output rule).
    if (not _mh.in_gdb_container(out_fc)
            and not os.path.splitext(out_fc)[1]):
        out_fc += ".shp"
    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()

    src, in_copy = _hs._open_source(str(in_hsm), scratch, messages)
    try:
        w, h = src.RasterXSize, src.RasterYSize
        if w < 1 or h < 1:
            raise arcpy.ExecuteError("The input HSM raster is empty.")
        gt = src.GetGeoTransform()
        proj = src.GetProjection()
        _mh.begin_phases([("Reading the input HSM...", 2),
                          ("Averaging over the moving window...", 3),
                          ("Grouping suitable habitat into patches...", 2),
                          ("Classifying and writing the patches...", 2)])
        band = src.GetRasterBand(1)
        a = np.asarray(band.ReadAsArray(0, 0, w, h), dtype=np.float64)
        nod = band.GetNoDataValue()
        valid = np.isfinite(a)
        if nod is not None:
            with np.errstate(invalid="ignore"):
                valid &= (a != np.float64(nod))
        with np.errstate(invalid="ignore"):
            valid &= (a != np.float64(_tt.NODATA_OUT))
        valid = _tt.mask_band_valid(gdal, band, valid)
        rast_sr = arcpy.Describe(str(in_hsm)).spatialReference
        _mh.phase_advance()

        rx_rows, ry, note = _tt._radius_row_cells(
            gt, h, rast_sr, radius, radius_units)
        _say(messages,
             "Averaging the HSM over a circular window of {} (mean of the "
             "data cells, the original tool's Focal Statistics MEAN with "
             "DATA).".format(note))
        avg = _hs._blur_grid(a, valid, rx_rows, ry, "Average")
        del a
        _mh.phase_advance()

        with np.errstate(invalid="ignore"):
            suitable = valid & np.isfinite(avg) & (avg >= threshold)
        del avg
        n_suit = int(suitable.sum())
        if not n_suit:
            raise arcpy.ExecuteError(
                "No cells have an averaged suitability at or above {:g} -- "
                "there is no suitable habitat to group into patches. Lower "
                "the threshold or check the input model.".format(threshold))
        _say(messages,
             "{:,} of {:,} data cells ({:.1f}%) average at or above the "
             "quality threshold of {:g}.".format(
                 n_suit, int(valid.sum()),
                 100.0 * n_suit / max(1, int(valid.sum())), threshold))

        labels, n_patches = ndimage.label(suitable, structure=np.ones(
            (3, 3), dtype=bool))
        del suitable
        labels = labels.astype(np.int32, copy=False)

        geom, _geo = _tt._surface_row_geometry(gt, h, rast_sr)
        row_area = np.asarray(geom["pix_m2"], dtype=np.float64)
        areas_m2 = np.bincount(
            labels.ravel(),
            weights=np.broadcast_to(row_area[:, None], (h, w)).ravel(),
            minlength=n_patches + 1)
        area_ha = areas_m2 / 10000.0
        cls = np.ones(n_patches + 1, dtype=np.int32)
        cls[area_ha >= breeding_ha] = 2
        cls[area_ha >= population_ha] = 3
        cls[0] = 0
        _mh.phase_advance()

        for c in (3, 2, 1):
            sel = (cls[1:] == c)
            _say(messages, "{}: {:,} patch{} totaling {:,.1f} ha.".format(
                PATCH_CLASS_NAMES[c], int(sel.sum()),
                "" if int(sel.sum()) == 1 else "es",
                float(area_ha[1:][sel].sum())))
        if not (cls[1:] == 3).any():
            _warn(messages,
                  "NO patch reaches the population size of {:g} ha -- the "
                  "landscape holds no patch large enough for a population "
                  "under these settings.".format(population_ha))

        geoms = _lft._polygons_from_labels(gdal, ogr, labels, gt, proj,
                                           rast_sr)
        del labels
        if len(geoms) != n_patches:
            _warn(messages,
                  "{:,} patches were labeled but {:,} polygons came back "
                  "from polygonization -- the output may be missing "
                  "patches.".format(n_patches, len(geoms)))

        out_ws, out_name = os.path.split(out_fc)
        arcpy.management.CreateFeatureclass(out_ws, out_name, "POLYGON",
                                            spatial_reference=rast_sr)
        # one AddFields call, not one AddField per field (standing rule)
        arcpy.management.AddFields(out_fc, [
            [nm, tp, "", ln or "", "", ""]
            for nm, tp, ln in [("PatchID", "LONG", None),
                               ("Class", "SHORT", None),
                               ("Class_Name", "TEXT", 24),
                               ("Area_Ha", "DOUBLE", None)]])

        order = sorted(geoms.keys(),
                       key=lambda l: (-int(cls[l]), -float(area_ha[l])))
        with arcpy.da.InsertCursor(
                out_fc, ["SHAPE@", "PatchID", "Class", "Class_Name",
                         "Area_Ha"]) as cur:
            for i, lbl in enumerate(order, start=1):
                c = int(cls[lbl])
                cur.insertRow([geoms[lbl], i, c, PATCH_CLASS_NAMES[c],
                               float(area_ha[lbl])])
                if i % 200 == 0:
                    _mh.phase_step(i, len(order))
        _say(messages, "{:,} patch polygon{} written.".format(
            len(order), "" if len(order) == 1 else "s"))

        try:
            _tm.set_entity_and_fields(
                out_fc,
                entity=("One polygon per habitat patch: 8-connected groups "
                        "of cells whose window-averaged habitat "
                        "suitability meets the quality threshold, "
                        "classified by geodesic area."),
                fields=[
                    {"name": "PatchID", "description":
                        "Sequential patch number (largest class, then "
                        "largest area, first)."},
                    {"name": "Class", "description":
                        "Patch size class: 1 = too small to support "
                        "breeding, 2 = breeding patch, 3 = population "
                        "patch.",
                     "coded": [(1, "Too small (below the minimum breeding "
                                   "patch size)"),
                               (2, "Breeding patch (supports at least one "
                                   "breeding event)"),
                               (3, "Population patch (supports a "
                                   "population)")]},
                    {"name": "Class_Name", "description":
                        "The size class as text (Too small / Breeding "
                        "patch / Population patch)."},
                    {"name": "Area_Ha", "description":
                        "The patch's area in hectares (geodesic, summed "
                        "from true per-row cell areas -- the value the "
                        "classification used)."},
                ],
                messages=messages)
            from arcpy import metadata as md
            m = md.Metadata(out_fc)
            m.title = os.path.basename(out_fc)
            m.tags = ("habitat patch, patch configuration, breeding patch, "
                      "population patch, habitat suitability, HSM, "
                      "CorridorDesigner, Jenness Enterprises")
            m.summary = (
                "Created by Create Habitat Patches from {src}: suitability "
                "averaged over a circular window of {note}, cells averaging "
                ">= {thr:g} grouped into 8-connected patches, classified by "
                "geodesic area (breeding >= {b:g} ha, population >= "
                "{p:g} ha).").format(
                    src=str(in_hsm), note=note, thr=threshold,
                    b=breeding_ha, p=population_ha)
            m.description = (
                "Habitat patches: polygons of contiguous suitable "
                "habitat, classified by whether each patch is large enough "
                "to support breeding or a population. The habitat "
                "suitability model is averaged over a moving window so "
                "small inclusions and gaps do not fragment the patches, "
                "thresholded, grouped (edge- or corner-touching cells "
                "connect), and classified by geodesic area in hectares. A "
                "modernized port of the CorridorDesigner Patch "
                "Configuration tool. " + _CD_CITATION)
            m.credits = ("Tool by Jeff Jenness and Dan Majka. Method: "
                         + _CD_CITATION)
            m.save()
        except Exception as e:                                   # noqa: BLE001
            _warn(messages, "Could not write output metadata: {}".format(e))

        chain_rec = _lnk.read(str(in_hsm), None)
        analysis_id = ((chain_rec or {}).get("analysis_id")
                       or _lnk.new_analysis_id())
        classes_meta = []
        for c in (1, 2, 3):
            sel = (cls[1:] == c)
            classes_meta.append({
                "class": c, "name": PATCH_CLASS_NAMES[c],
                "n_patches": int(sel.sum()),
                "area_ha": round(float(area_ha[1:][sel].sum()), 2)})
        _lnk.write(out_fc, _lnk.patch_map_record(
            analysis_id=analysis_id,
            source_raster=str(in_hsm),
            source_analysis_id=(chain_rec or {}).get("analysis_id"),
            params={"radius": radius, "radius_units": radius_units,
                    "window": note, "threshold": threshold,
                    "breeding_ha": breeding_ha,
                    "population_ha": population_ha},
            classes=classes_meta), messages)

        out_lyrx = _patch_map_lyrx(out_fc, messages)
        _mh.end_phases()
        return out_lyrx
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:                                    # noqa: BLE001
                pass
