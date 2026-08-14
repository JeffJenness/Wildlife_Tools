# -*- coding: utf-8 -*-
"""Land Facet Mahalanobis -- one cost surface per land facet.

The third step of the Land Facet corridor analysis (clustering, then density,
then this). For each land facet it measures how far every cell on the landscape
departs from that facet's prototypical conditions, in Mahalanobis units, and
writes that as a resistance surface for least-cost corridor modelling
(Land_Facet_Tools.pdf p.11-12, p.30 steps 3(c)(iii)-(v)).

TWO THINGS THAT DEFINE THE METHOD, both easy to get subtly wrong:

The WILDLAND BLOCKS are training data, and they restrict the SAMPLE, not the
output. The mean vector and covariance matrix for a facet come only from that
facet's cells INSIDE the blocks -- those are the places known to support a
functioning community. The resulting surface is then evaluated over the WHOLE
analysis area, including everything outside the blocks, because that is the
ground a corridor has to cross.

DENSITY's characteristic value is 100 PERCENT, NOT ITS MEAN. Every other
variable uses the sample mean of the facet's cells; the density variable instead
uses 1.0, because the prototypical cell of a facet is one whose entire
neighborhood is that same facet (manual p.11 fn.4, p.12, and the workflow at
p.30 step (iv), which had the user hand-edit the mean after the fact). Only the
MEAN is replaced -- the covariance still comes from the real observed densities.
This tool applies the override automatically from the density raster's linkage
record, so it can no longer be forgotten.

OUTPUT SHAPE differs deliberately from the density tool. Density is an
intermediate this toolbox consumes itself, so it is one multi-band raster.
These surfaces are terminal products handed to CorridorDesigner or Cost
Distance, which expect a SINGLE-BAND cost surface -- so each facet gets its own
raster. Follow the consumer, not the family resemblance.

The numerics are not reimplemented here: this orchestrates the validated
routines in ``mahalanobis.py`` (align_predictors, stats_from_categorical,
invert_covariance, compute_mahalanobis, save_optional_tables), which are shared
with the general-purpose Mahalanobis tools.
"""
import os

import numpy as np

import mahalanobis as _mh
import topographic_tools as _tt
import land_facet_linkage as _lnk

OUT_TYPES = [_mh.OUT_D2, _mh.OUT_D, _mh.OUT_PVALUE]
INVERSIONS = [_mh.INV_STANDARD, _mh.INV_PSEUDO]

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org. Land Facet Corridor Designer: Beier, P., and "
    "B. Brost. 2010, Conservation Biology 24:701-710; Brost, B. M., and P. "
    "Beier. 2012, Ecological Applications 22:87-103; Jenness, J., B. Brost, "
    "and P. Beier. 2013, http://www.jennessent.com/arcgis/land_facets.htm. "
    "Mahalanobis, P.C. 1936, Proc. National Institute of Sciences of India "
    "2:49-55.")


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def safe_name(text, maxlen=48):
    """A dataset-name-safe token: letters, digits and underscores, not
    leading with a digit (geodatabase rule)."""
    out = []
    for ch in str(text):
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    s = "".join(out).strip("_")
    while "__" in s:
        s = s.replace("__", "_")
    if not s:
        s = "facet"
    if s[0].isdigit():
        s = "F" + s
    return s[:maxlen]


# ---------------------------------------------------------------------------
# density / facet raster pairing
# ---------------------------------------------------------------------------
def check_density_pairing(frec, drec, present_ids, messages):
    """Decide whether a density raster may be used with this facet raster.

    Returns ``{facet_id: band}``. Graduated rather than a single warn-or-stop,
    because the three failure modes are not equally dangerous:

    * same analysis id                  -> silent, they are the same run
    * different id, same facets/labels  -> warn, probably a re-run
    * different facet id sets           -> ERROR

    The last case is refused rather than warned about because the damage is
    invisible. A wrong band means the surface is built from ANOTHER facet's
    density, and since density's ideal is 100% that does not look like noise --
    it systematically pulls the corridor toward ground where the wrong facet is
    abundant, and the output looks perfectly plausible.
    """
    import arcpy

    if drec is None:
        raise arcpy.ExecuteError(
            "The density raster carries no land facet linkage record, so there "
            "is no way to tell which band holds which facet. Regenerate it with "
            "the Land Facet Density tool, or leave the density raster empty to "
            "run without it.")

    bands = {b.get("facet_id"): b.get("band") for b in (drec.get("bands") or [])
             if b.get("facet_id") is not None and b.get("band")}
    if not bands:
        raise arcpy.ExecuteError(
            "The density raster's linkage record lists no bands.")

    missing = sorted(i for i in present_ids if i not in bands)
    if missing:
        raise arcpy.ExecuteError(
            "The density raster has no band for land facet{} {}. It was built "
            "from a different set of facets than the land facet raster you "
            "chose, so its bands cannot be matched to these facets. Rebuild the "
            "density raster from THIS land facet raster.".format(
                "" if len(missing) == 1 else "s",
                ", ".join(str(m) for m in missing)))

    src_id = drec.get("source_analysis_id")
    own_id = (frec or {}).get("analysis_id")
    if own_id and src_id and src_id == own_id:
        _say(messages, "The density raster matches this land facet raster "
                       "(analysis {}).".format(own_id))
    else:
        _warn(messages,
              "The density raster does not record the same analysis id as the "
              "land facet raster, but it covers every facet present, so it is "
              "being used. Check that it really was built from this land facet "
              "raster and not an earlier version of it.")
    return bands


# ---------------------------------------------------------------------------
# sample mask: the facets, restricted to the wildland blocks
# ---------------------------------------------------------------------------
def masked_facet_raster(facet_path, blocks, scratch, messages):
    """Write a copy of the facet raster with everything outside the Wildland
    Blocks set to NoData, for use as the statistics category raster.

    Only the SAMPLE is restricted this way; the cost surfaces are still computed
    over the full extent of the variable rasters.
    """
    import arcpy
    from osgeo import gdal, ogr

    gdal.UseExceptions()
    src, tmp = _mh._gdal_source_path(facet_path, scratch)
    ds = gdal.Open(src)
    try:
        band = ds.GetRasterBand(1)
        w, h = ds.RasterXSize, ds.RasterYSize
        gt, proj = ds.GetGeoTransform(), ds.GetProjection()
        arr = band.ReadAsArray()
        nod = band.GetNoDataValue()
        vmask = _mh.band_validity_mask(band)   # capture while ds is open
    finally:
        ds = None

    # Copy the blocks to a scratch shapefile so any selection the user has made
    # is honoured and GDAL gets a source it can always open.
    shp = arcpy.CreateUniqueName("lf_blocks.shp", scratch)
    arcpy.management.CopyFeatures(blocks, shp)

    mask_path = arcpy.CreateUniqueName("lf_blockmask.tif", scratch)
    drv = gdal.GetDriverByName("GTiff")
    mds = drv.Create(mask_path, w, h, 1, gdal.GDT_Byte,
                     options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    mds.SetGeoTransform(gt)
    try:
        mds.SetProjection(proj)
    except Exception:
        pass
    vec = ogr.Open(shp)
    try:
        gdal.RasterizeLayer(mds, [1], vec.GetLayer(0), burn_values=[1])
    finally:
        vec = None
    mds.FlushCache()
    inside = mds.GetRasterBand(1).ReadAsArray().astype(bool)
    mds = None

    n_in = int(inside.sum())
    if n_in == 0:
        raise arcpy.ExecuteError(
            "The Wildland Blocks do not overlap the land facet raster, so there "
            "are no cells to build the statistics from. Check that both use the "
            "same area and coordinate system.")

    # float32 is exact for facet ids (small integers) and halves both the
    # in-memory arrays and the scratch file against float64; the masking is done
    # in place rather than through a chain of np.where copies. A nodata value a
    # float32 cannot hold exactly (e.g. the Float64 1.79e308) becomes inf and is
    # caught by the finite test.
    NOD = -9999.0
    out = arr.astype(np.float32)
    bad = ~np.isfinite(out)
    if vmask is not None:
        bad |= ~vmask
    if nod is not None:
        with np.errstate(invalid="ignore"):
            bad |= (out == np.float32(nod))
    out[~inside] = np.float32(NOD)
    out[bad] = np.float32(NOD)
    del arr, bad, inside

    out_path = arcpy.CreateUniqueName("lf_facets_in_blocks.tif", scratch)
    ods = drv.Create(out_path, w, h, 1, gdal.GDT_Float32,
                     options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    ods.SetGeoTransform(gt)
    try:
        ods.SetProjection(proj)
    except Exception:
        pass
    ob = ods.GetRasterBand(1)
    ob.SetNoDataValue(NOD)
    ob.WriteArray(out)
    ob.FlushCache()
    ods.FlushCache()
    ods = None

    _say(messages, "Wildland Blocks cover {:,} of {:,} cells; statistics come "
                   "from the facet cells inside them.".format(n_in, w * h))
    # The blocks shapefile and the burn mask have served their purpose; only the
    # masked facet raster needs to outlive this function (its caller deletes it
    # once every facet's statistics have been sampled).
    cleanup = [shp, mask_path]
    if tmp:
        cleanup.append(tmp)
    for junk in cleanup:
        try:
            arcpy.management.Delete(junk)
        except Exception:
            pass
    return out_path


def _reset_phase_bar(label):
    """Re-establish the shared whole-run step progressor (0.._PHASE_RESOLUTION).

    Nested work installs progressors of its own -- ``compute_mahalanobis`` steps
    over row strips, and GP calls such as CopyRaster reset the bar too -- which
    leaves the phase bar reporting positions against somebody else's range. Call
    this at the start of each facet so the meter goes back to meaning "one
    increment per cost surface".
    """
    try:
        import arcpy
        arcpy.SetProgressor("step", label, 0, _mh._PHASE_RESOLUTION, 1)
        # SetProgressor rewinds the bar to zero; jump straight back to the
        # current phase's start so the meter never appears to LOSE progress at
        # the top of each facet.
        if _mh._phase_bounds and 0 <= _mh._phase_i < len(_mh._phase_bounds):
            _, start, _ = _mh._phase_bounds[_mh._phase_i]
            arcpy.SetProgressorPosition(int(start * _mh._PHASE_RESOLUTION))
    except Exception:
        pass


class _InnerProgress(object):
    """Redirect a nested routine's progressor calls into the current phase.

    ``mahalanobis.compute_mahalanobis`` drives its own step progressor over row
    strips and labels it "Computing Mahalanobis values...". Left alone that
    replaces the per-facet meter with a bar that restarts on every facet under a
    label that never says which facet is running. Inside this context manager
    those calls instead advance the CURRENT phase's slice of the shared bar, and
    the label is kept as ours, so the meter still steps once per cost surface
    while showing real sub-progress within each one.

    The three arcpy entry points are restored on exit, and every redirect is a
    no-op if no phase run is active, so nothing here can leave the progressor in
    a strange state.
    """

    def __init__(self, label):
        self.label = label
        self.total = None
        self._saved = None

    def __enter__(self):
        try:
            import arcpy
        except Exception:                                        # noqa: BLE001
            return self
        self._arcpy = arcpy
        self._saved = (arcpy.SetProgressor, arcpy.SetProgressorPosition,
                       arcpy.SetProgressorLabel)
        orig_position = self._saved[1]

        def set_progressor(ptype=None, message=None, min_range=0,
                           max_range=100, step_value=1):
            # Remember the nested range; do NOT install its progressor.
            self.total = max_range or None

        def set_position(position=None):
            # Map the nested position into this phase's slice of our bar. Call
            # the ORIGINAL arcpy function -- routing through phase_step would
            # re-enter this patched function.
            if not self.total or position is None:
                return
            try:
                if not _mh._phase_bounds:
                    return
                _, start, end = _mh._phase_bounds[_mh._phase_i]
                frac = start + (end - start) * (
                    min(float(position), float(self.total)) / float(self.total))
                orig_position(int(frac * _mh._PHASE_RESOLUTION))
            except Exception:                                    # noqa: BLE001
                pass

        def set_label(message=None):
            pass            # keep the facet label we set

        arcpy.SetProgressor = set_progressor
        arcpy.SetProgressorPosition = set_position
        arcpy.SetProgressorLabel = set_label
        return self

    def __exit__(self, *exc):
        if self._saved:
            (self._arcpy.SetProgressor,
             self._arcpy.SetProgressorPosition,
             self._arcpy.SetProgressorLabel) = self._saved
        return False


def _table_path(workspace, name):
    """Where an optional statistics table goes. In a geodatabase a bare name is
    right; in a FOLDER the name needs the .dbf extension or the table is created
    as something arcpy cannot then insert rows into -- the same
    give-folder-output-a-real-format problem that resolve_raster_output solves
    for rasters."""
    # maxlen well above the raster name's: with the default 48 a long facet
    # label truncated the "_Mean"/"_Cov" suffixes clean off, collapsing both
    # tables onto the SAME path so the covariance silently overwrote the mean.
    # is_gdb_workspace handles the typed-trailing-separator case suite-wide.
    if _mh.is_gdb_workspace(workspace):
        return os.path.join(workspace, safe_name(name, maxlen=120))
    return os.path.join(workspace, safe_name(name, maxlen=120) + ".dbf")


def _scaled_vrt(gdal, vrt, factor):
    """A VRT whose values are ``factor`` times the source's.

    Used to bring the density bands back from their stored integers to 0-1
    proportions. Mahalanobis distance is invariant under a linear rescaling of a
    variable, so this changes no result -- it keeps the ideal a literal 1.0,
    keeps the exported mean/covariance tables in units a reader recognises, and
    stops one variable's covariance entries being 10^8 times the others'. The
    aligned VRTs already carry NaN for NoData, so nothing invalid is scaled into
    a valid-looking number.
    """
    return gdal.Translate(
        "", vrt, format="VRT", outputType=gdal.GDT_Float64,
        scaleParams=[[0.0, 1.0 / factor, 0.0, 1.0]])


# ---------------------------------------------------------------------------
def run_facet_mahalanobis(in_raster, blocks, variables, out_workspace,
                          out_prefix=None, density_raster=None,
                          facet_values=None, out_type=None,
                          inversion=None, sample_method=None,
                          save_tables=False, messages=None):
    """Build one Mahalanobis cost surface per land facet. Returns the list of
    written raster paths."""
    import arcpy

    out_type = out_type or _mh.OUT_D2
    inversion = inversion or _mh.INV_STANDARD
    sample_method = sample_method or _mh.SAMPLE_EXACT
    if out_type not in OUT_TYPES:
        raise arcpy.ExecuteError("Unknown output type: {}".format(out_type))
    if not variables:
        raise arcpy.ExecuteError("At least one variable raster is required.")

    scratch = arcpy.env.scratchFolder
    gdal = _mh._gdal()

    frec = _lnk.read(in_raster, _lnk.FACETS)
    if frec:
        _say(messages, "Read the land facet linkage record: analysis {}.".format(
            frec.get("analysis_id")))
    else:
        _warn(messages,
              "The land facet raster carries no linkage record, so facets are "
              "taken from its distinct cell values and labelled generically.")

    # ---- which facets ------------------------------------------------------
    entries = []
    if frec:
        for f in frec.get("facets") or []:
            if f.get("facet_id") is not None:
                entries.append(dict(f))
    if not entries:
        src, tmp = _mh._gdal_source_path(in_raster, scratch)
        ds = gdal.Open(src)
        try:
            b = ds.GetRasterBand(1)
            a = b.ReadAsArray()
            nod = b.GetNoDataValue()
            vmask = _mh.band_validity_mask(b)   # capture while ds is open
        finally:
            ds = None
        a = np.asarray(a, dtype=np.float64)
        keep = np.isfinite(a) & (a != _tt.NODATA_OUT)
        if nod is not None:
            with np.errstate(invalid="ignore"):
                keep &= (a != nod)
        if vmask is not None:
            keep &= vmask
        for v in np.unique(a[keep]):
            entries.append({"facet_id": int(v) if float(v).is_integer() else float(v),
                            "category_label": None, "cluster": None,
                            "category_value": None})
        if tmp:
            try:
                arcpy.management.Delete(tmp)
            except Exception:
                pass
    if facet_values:
        want = set()
        for v in facet_values:
            try:
                want.add(int(v))
            except (TypeError, ValueError):
                want.add(v)
        entries = [e for e in entries if e["facet_id"] in want]
    if not entries:
        raise arcpy.ExecuteError("No land facets were selected to process.")
    ids = [e["facet_id"] for e in entries]
    _say(messages, "{} land facet{} to process.".format(
        len(entries), "" if len(entries) == 1 else "s"))

    # ---- density pairing ---------------------------------------------------
    drec = None
    band_of = {}
    scale_factor = None
    if density_raster:
        drec = _lnk.read(density_raster, _lnk.DENSITY)
        band_of = check_density_pairing(frec, drec, ids, messages)
        scale_factor = drec.get("scale_factor")
        ideal = drec.get("ideal_value", _lnk.IDEAL_DENSITY)
        _say(messages,
             "Density will be used as a variable; its characteristic value is "
             "{} (100%), not its mean, per the Land Facet method.".format(ideal))
    else:
        ideal = None
        _warn(messages,
              "No density raster was supplied. The cost surfaces will describe "
              "how closely each cell resembles the facet's typical values, but "
              "will NOT prefer the interiors of facet patches over scattered "
              "isolated cells, which is what the density variable contributes "
              "in the published method.")

    # ---- variables: shared, then every density band ------------------------
    resolved = _mh.resolve_variables([(v, None) for v in variables])
    n_shared = len(resolved)
    if density_raster:
        # Resolve the density raster separately rather than re-resolving the
        # whole list: resolve_variables Describes every dataset, and the shared
        # ones were just done.
        resolved = resolved + _mh.resolve_variables([(density_raster, None)])
    _say(messages, "{} variable{} ({} shared{}).".format(
        len(resolved), "" if len(resolved) == 1 else "s", n_shared,
        ", plus one density band per facet" if density_raster else ""))

    # One progress increment per cost surface, each labelled with the facet it is
    # working on, plus the two shared setup steps in front. Declared here because
    # the facet count is not known until the record has been read.
    labels = [_lnk.facet_label(e) for e in entries]
    _mh.begin_phases(
        [("Aligning the variable rasters...", 3),
         ("Restricting the sample to the Wildland Blocks...", 2)]
        + [("Facet {} of {}: {}".format(i, len(entries), lb), 6)
           for i, lb in enumerate(labels, start=1)])

    vrts, geo = _mh.align_predictors(resolved, sample_method, scratch, messages)

    # Density bands come back as stored integers; put them in 0-1 proportions.
    # The scaled VRT only REFERENCES its source, so the source datasets have to
    # outlive it. Assigning straight over vrts[i] would drop the last Python
    # reference to the warped VRT and let GDAL free a dataset the scaled one
    # still points at.
    _vrt_keepalive = []
    if density_raster and scale_factor:
        for i in range(n_shared, len(vrts)):
            source_vrt = vrts[i]
            _vrt_keepalive.append(source_vrt)
            vrts[i] = _scaled_vrt(gdal, source_vrt, scale_factor)

    # Band b of the density raster is resolved variable n_shared + (b-1).
    def _vrt_index_for_band(b):
        return n_shared + int(b) - 1

    # ---- statistics category raster ---------------------------------------
    _mh.phase_advance()
    cat_path = masked_facet_raster(in_raster, blocks, scratch, messages)

    # ---- one surface per facet --------------------------------------------
    analysis_id = (frec or {}).get("analysis_id") or _lnk.new_analysis_id()
    prefix = safe_name(out_prefix or "Mahal")
    written, failures = [], []
    used_names = set()

    try:
        for i, entry in enumerate(entries, start=1):
            fid = entry["facet_id"]
            label = _lnk.facet_label(entry)
            step_label = "Facet {} of {}: {}".format(i, len(entries), label)
            _mh.phase_advance(step_label)
            # A previous facet's delivery (CopyRaster) may have taken the bar over.
            _reset_phase_bar(step_label)
            _say(messages, "")
            _say(messages, "--- {} (facet {}) ---".format(label, fid))

            idx = list(range(n_shared))
            dens_pos = None
            if density_raster:
                dens_pos = len(idx)
                idx.append(_vrt_index_for_band(band_of[fid]))
            sub_vrts = [vrts[j] for j in idx]
            sub_vars = [resolved[j] for j in idx]

            try:
                mean, cov, corr, n_used = _mh.stats_from_categorical(
                    sub_vrts, geo, cat_path, fid, sample_method, scratch,
                    messages)
            except Exception as e:                                # noqa: BLE001
                failures.append((label, "statistics: {}".format(e)))
                _warn(messages, "Skipped {}: {}".format(label, e))
                continue

            if n_used is not None and n_used < len(sub_vars) + 1:
                failures.append((label, "only {} sample cells".format(n_used)))
                _warn(messages,
                      "Skipped {}: only {} of its cells fall inside the Wildland "
                      "Blocks, which is too few to estimate a covariance matrix "
                      "for {} variables.".format(label, n_used, len(sub_vars)))
                continue

            if dens_pos is not None:
                observed = float(mean[dens_pos])
                if observed > 1.0 + 1e-6:
                    # A proportion cannot exceed 1, so the sample must contain
                    # cells whose NoData marker is being read as data -- the
                    # known case is a multi-band density raster in a file
                    # geodatabase, which may not preserve the NoData
                    # declaration, so the stored 65535 sentinel arrives as a
                    # value (scaled here to 6.55).
                    _warn(messages,
                          "The sampled density mean for {} is {:.3f}, above the "
                          "highest possible proportion (1.0). The density "
                          "raster has probably lost its NoData declaration (a "
                          "file geodatabase may not preserve it on a multi-band "
                          "raster), so NoData cells are being read as data. "
                          "Rebuild the density raster as a GeoTIFF before "
                          "trusting this facet's surface.".format(
                              label, observed))
                mean = np.asarray(mean, dtype=np.float64).copy()
                mean[dens_pos] = float(ideal)
                _say(messages,
                     "Density mean {:.4f} replaced by the ideal {:.4f}; the "
                     "covariance still uses the observed values.".format(
                         observed, float(ideal)))

            try:
                inv_cov = _mh.invert_covariance(cov, inversion, messages)
            except Exception as e:                                # noqa: BLE001
                failures.append((label, "covariance: {}".format(e)))
                _warn(messages, "Skipped {}: {}".format(label, e))
                continue

            name = "{}_{}".format(prefix, safe_name(label))
            if name.lower() in used_names:
                # Long labels can truncate to the same safe token (two class
                # names differing only past the cutoff); the facet id is unique
                # by construction, so appending it restores distinct names
                # instead of letting a later surface overwrite an earlier one.
                name = "{}_F{}".format(name, fid)
            used_names.add(name.lower())
            out_path = _mh.resolve_raster_output(
                os.path.join(out_workspace, name), messages)

            tables = {}
            if save_tables:
                opts = {"mean": _table_path(out_workspace, name + "_Mean"),
                        "cov": _table_path(out_workspace, name + "_Cov")}
                try:
                    _mh.save_optional_tables(
                        opts, mean, cov, inv_cov, corr, sub_vars,
                        {"source": "Land facet cells inside the Wildland Blocks",
                         "n": (int(n_used) if n_used is not None else None)},
                        messages)
                    tables = opts
                except Exception as e:                            # noqa: BLE001
                    _warn(messages, "Could not save the statistics tables for "
                                    "{}: {}".format(label, e))

            try:
                with _InnerProgress(step_label):
                    scratch_tif, out_stats = _mh.compute_mahalanobis(
                        sub_vrts, geo, mean, inv_cov, out_type, scratch,
                        messages)
            except Exception as e:                                # noqa: BLE001
                failures.append((label, "surface: {}".format(e)))
                _warn(messages, "Skipped {}: {}".format(label, e))
                continue

            dens_note = ("" if dens_pos is None else
                         " Density (band {} of {}) was included with a "
                         "characteristic value of {} rather than its mean.".format(
                             band_of[fid], os.path.basename(str(density_raster)),
                             ideal))
            try:
                _tt._deliver_output(
                    scratch_tif, out_path, out_stats, messages,
                    title=os.path.basename(str(out_path)),
                    summary=(
                        "Created by Land Facet Mahalanobis from {src}: the {ot} "
                        "of every cell from the prototypical conditions of "
                        "{lab}. The mean vector and covariance matrix were "
                        "built from that facet's {n:,} cells inside {blk}; the "
                        "surface covers the whole analysis area.{dn}"
                    ).format(src=str(in_raster), ot=out_type, lab=label,
                             n=(n_used or 0), blk=os.path.basename(str(blocks)),
                             dn=dens_note),
                    description=(
                        "Mahalanobis distance cost (resistance) surface for one "
                        "land facet: how far each cell departs, in multivariate "
                        "standard deviations, from the conditions typical of "
                        "that facet inside the Wildland Blocks. Low values mark "
                        "ground most like the facet, so it is used directly as "
                        "the resistance surface in least-cost corridor "
                        "modelling. One raster per facet. {cite}"
                    ).format(cite=_CD_CITATION),
                    tags=("Mahalanobis distance, land facet, cost surface, "
                          "resistance, corridor, connectivity, least-cost, "
                          "Corridor Designer, Land Facet, Jenness Enterprises"),
                    citation=_CD_CITATION)
            except Exception as e:                                # noqa: BLE001
                # A delivery failure (destination locked, disk full, exists
                # with overwrite off) is a per-facet problem; the remaining
                # facets must still get their surfaces.
                failures.append((label, "saving: {}".format(e)))
                _warn(messages,
                      "Skipped {}: the surface was computed but could not be "
                      "saved to {} ({}).".format(label, out_path, e))
                continue

            _lnk.write(out_path, _lnk.mahalanobis_record(
                analysis_id=analysis_id, facet=entry,
                source_facet_raster=in_raster,
                source_analysis_id=(frec or {}).get("analysis_id"),
                source_density_raster=density_raster,
                variables=[{"order": j, "name": v["name"]}
                           for j, v in enumerate(sub_vars)],
                out_type=out_type, n_sample=(int(n_used) if n_used else None),
                density_band=(band_of.get(fid) if density_raster else None),
                density_ideal=(float(ideal) if dens_pos is not None else None),
                blocks=str(blocks), tables=tables), messages)

            written.append(out_path)
            _say(messages, "Wrote {}".format(out_path))
    finally:
        # The masked facet raster is a full-grid scratch file; do not leave it
        # behind, and park the progress bar even if a facet aborted the run.
        try:
            arcpy.management.Delete(cat_path)
        except Exception:
            pass
        _mh.end_phases()

    _say(messages, "")
    _say(messages, "{} of {} cost surface{} written.".format(
        len(written), len(entries), "" if len(entries) == 1 else "s"))
    if failures:
        _warn(messages, "{} facet{} skipped:".format(
            len(failures), "" if len(failures) == 1 else "s"))
        for lab, why in failures:
            _warn(messages, "    {} -- {}".format(lab, why))
    return written
