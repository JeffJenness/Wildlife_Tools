# -*- coding: utf-8 -*-
"""Clip Data to Analysis Area -- batch-clip rasters AND feature classes.

A modernized port of the CorridorDesigner ClipAnalysisLayers tool
(``util_clipLayers.py``, Dan Majka, 2007; these ArcGIS Pro versions are by
Jeff Jenness and Dan Majka). The original looped Clip_analysis over feature
classes and ExtractByMask over rasters (Spatial Analyst), warning and
continuing on each failure. This version keeps the one-tool-clips-everything
shape and the per-dataset warn-and-continue resilience, and adds:

* NO Spatial Analyst: rasters clip through Data Management's Clip with
  ClippingGeometry.
* Output format follows the workspace: a FOLDER gets shapefiles and
  GeoTIFFs, a GEODATABASE gets native feature classes and rasters.
* An optional OUTPUT COORDINATE SYSTEM, with geographic transformations
  chosen per input datum -- the dialog suggests transformations the same
  way the Project tool does (arcpy.ListTransformations, extent-aware), and
  accepts SEVERAL because different inputs may need different datum paths
  (WGS84 sources and NAD27 sources both headed to NAD83, say). At run time
  each dataset gets the first user-chosen transformation that is valid for
  its own datum pair, falling back to Esri's best suggestion.

Unlike the suite's grid-native analysis tools, this is a data-preparation
tool built ON arcpy geoprocessing (Clip, Project, ProjectRaster), so the
standard geoprocessing environments apply in the ordinary way.
"""
import os

import arcpy

import mahalanobis as _mh

_CD_CITATION = (
    "CorridorDesigner: Majka, D., J. Jenness, and P. Beier. 2007. "
    "http://corridordesign.org. Original ClipAnalysisLayers tool by Dan "
    "Majka; ArcGIS Pro version by Jeff Jenness and Dan Majka.")


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def _describe(ds):
    """(describe, catalog_path, display_name) for a dataset or layer.

    A MULTIVALUE parameter's ``.values`` are GP value wrappers, not real
    Layer objects, and Describe sometimes rejects them ("input value is
    not valid type") -- their text (a layer name inside Pro, or a path)
    usually resolves, so fall back to that (Jeff's first Pro run,
    2026-08-15)."""
    try:
        d = arcpy.Describe(ds)
    except Exception:                                            # noqa: BLE001
        d = arcpy.Describe(str(ds))
    path = getattr(d, "catalogPath", None) or str(ds)
    name = getattr(d, "name", None) or os.path.basename(path)
    return d, path, name


def _input_candidates(ds, name, path):
    """The forms of one input to TRY as a geoprocessing tool argument, in
    order: the original value (keeps a layer's selection), the display
    NAME (also selection-aware -- it re-resolves to the map layer inside
    Pro), and the catalog path (always openable; selection lost). The
    same GP value wrapper that Describe tolerates can still be rejected
    by a tool ("Cannot open GPLYR_{...}", "Invalid value type for
    parameter in_raster"), so the callers walk this list."""
    cands = [ds]
    for alt in (name, path):
        # Dedupe only among the STRING alternatives: the original object
        # stays first even when its str() equals one of them, because an
        # object can fail as a tool argument while its text succeeds.
        if isinstance(alt, str) and alt and not any(
                isinstance(c, str) and c == alt for c in cands):
            cands.append(alt)
    return cands


def _first_that_works(cands, call, name, messages):
    """Run ``call(candidate)`` with each candidate until one succeeds;
    re-raise the last failure if none does."""
    last = None
    for i, cand in enumerate(cands):
        try:
            call(cand)
            if i > 0:
                _say(messages,
                     "  ({}: the layer handle was not usable as a tool "
                     "input; re-resolved as {!r}.)".format(name, str(cand)))
            return
        except arcpy.ExecuteError as e:
            last = e
        except Exception as e:                                   # noqa: BLE001
            last = e
    raise last


def _is_raster(desc):
    return getattr(desc, "dataType", "") in (
        "RasterDataset", "RasterLayer", "RasterBand", "MosaicDataset",
        "MosaicLayer")


def _same_cs(sr_a, sr_b):
    """True when two spatial references describe the same coordinate
    system (factory code match when both have one, else full-string
    equality)."""
    if sr_a is None or sr_b is None:
        return False
    try:
        if (sr_a.factoryCode and sr_b.factoryCode
                and sr_a.factoryCode == sr_b.factoryCode):
            return True
        return sr_a.exportToString() == sr_b.exportToString()
    except Exception:                                            # noqa: BLE001
        return False


def _same_datum(sr_a, sr_b):
    """True when no geographic transformation is needed between the two."""
    try:
        return sr_a.GCS.datumName == sr_b.GCS.datumName
    except Exception:                                            # noqa: BLE001
        return False


def transformation_for(in_sr, out_sr, chosen, extent=None):
    """The geographic transformation to use for one dataset: the first
    USER-CHOSEN transformation valid for this datum pair, else Esri's own
    best suggestion (first of ListTransformations, extent-aware), else
    None (same datum, or nothing applicable). Returns (name_or_None,
    was_fallback)."""
    if _same_datum(in_sr, out_sr):
        return None, False
    try:
        cands = arcpy.ListTransformations(in_sr, out_sr, extent) or []
    except Exception:                                            # noqa: BLE001
        cands = []
    for t in (chosen or []):
        if t in cands:
            return t, False
    return (cands[0], True) if cands else (None, False)


def _out_name(base, workspace, is_folder, raster, prefix="", suffix=""):
    """(target_path, safe_name) for one output, honoring the workspace
    kind (the standing folder-format rule: shapefiles and GeoTIFFs in a
    folder, native datasets in a geodatabase) and the optional name
    prefix/suffix. Existence is NOT handled here -- see ``_produce``."""
    base = "{}{}{}".format(prefix or "", base, suffix or "")
    safe = arcpy.ValidateTableName(base, workspace)
    if is_folder:
        safe = safe + (".tif" if raster else ".shp")
    return os.path.join(workspace, safe), safe


def _produce(target, workspace, safe_name, make, messages):
    """Produce one output via ``make(destination)`` WITHOUT ever deleting
    an existing dataset before its replacement exists.

    The tool's first field run deleted a pre-existing dataset at the
    target name (overwrite on) and THEN crashed before writing anything
    -- the user's data was simply gone (Jeff, 2026-08-15). Rule: an
    existing target is left untouched while the new dataset is produced
    under a UNIQUE name in the same workspace; only after success is the
    old one deleted and the new one renamed into place. With overwrite
    OFF the unique name is simply kept. Returns the path actually
    written."""
    if not arcpy.Exists(target):
        make(target)
        return target
    alt = arcpy.CreateUniqueName(safe_name, workspace)
    make(alt)
    if not arcpy.env.overwriteOutput:
        _say(messages,
             "  ({} already existed; wrote {} instead -- enable "
             "overwrite-outputs to replace.)".format(
                 os.path.basename(target), os.path.basename(alt)))
        return alt
    try:
        arcpy.management.Delete(target)
        arcpy.management.Rename(alt, os.path.basename(target))
        return target
    except Exception as e:                                       # noqa: BLE001
        _warn(messages,
              "Could not replace the existing {} ({}); the new output "
              "was kept as {}.".format(os.path.basename(target), e,
                                       os.path.basename(alt)))
        return alt


def _stamp(out_path, src_name, area_name, cs_note, raster):
    """Light provenance metadata on one clipped output."""
    try:
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = (
            "Created by Clip Data to Analysis Area: {} clipped to the "
            "analysis area {}{}.".format(src_name, area_name, cs_note))
        md.description = (
            "{} clipped to the analysis area boundary by the Clip Data to "
            "Analysis Area tool, which batch-clips rasters and feature "
            "classes together and writes formats matching the output "
            "workspace. {}".format(
                "Raster" if raster else "Feature class", _CD_CITATION))
        md.credits = ("Tool by Jeff Jenness and Dan Majka. Method: "
                      + _CD_CITATION)
        md.save()
    except Exception:                                            # noqa: BLE001
        pass


def run_clip_data(datasets, area, workspace, out_cs=None,
                  transformations=None, prefix="", suffix="",
                  messages=None):
    """Clip every dataset in ``datasets`` to ``area`` into ``workspace``.

    ``datasets``: list of rasters / feature classes / layers (layer
    selections are honored by the underlying Clip tools). ``area``: polygon
    features (a layer's selection is honored). ``out_cs``: optional target
    ``arcpy.SpatialReference``; ``transformations``: optional list of
    geographic transformation names, matched per dataset (see
    ``transformation_for``). ``prefix``/``suffix``: optional text wrapped
    around every output name (e.g. suffix "_clip" so outputs never shadow
    same-named originals in the destination). Returns
    ``(feature_outputs, raster_outputs)``.
    """
    if not datasets:
        raise arcpy.ExecuteError("Supply at least one dataset to clip.")
    workspace = str(workspace).rstrip("\\/")
    if not arcpy.Exists(workspace):
        raise arcpy.ExecuteError(
            "The output workspace does not exist: {}".format(workspace))
    is_folder = not _mh.is_gdb_workspace(workspace)
    if is_folder and not os.path.isdir(workspace):
        raise arcpy.ExecuteError(
            "The output workspace must be a folder or a geodatabase: "
            "{}".format(workspace))
    _say(messages,
         "Output workspace is a {}: writing {}.".format(
             "folder" if is_folder else "geodatabase",
             "shapefiles and GeoTIFFs" if is_folder
             else "geodatabase feature classes and rasters"))
    if out_cs is not None:
        _say(messages,
             "Outputs will be projected to: {}. Rasters that need "
             "projecting are resampled with NEAREST NEIGHBOR; use Esri's "
             "Project Raster first if a raster needs a different "
             "method.".format(out_cs.name))

    area_desc, area_path, area_name = _describe(area)
    scratch = arcpy.env.scratchGDB

    # The same dataset listed twice -- as a map layer and again from disk,
    # say -- is almost always a mistake; compare resolved catalog paths.
    seen = {}
    for ds in datasets:
        try:
            _d0, p0, n0 = _describe(ds)
        except Exception:                                        # noqa: BLE001
            continue
        k = str(p0).lower()
        if k in seen:
            _warn(messages,
                  "{} appears MORE THAN ONCE in the dataset list (also "
                  "listed as {}) -- the same data will be clipped more "
                  "than once.".format(n0, seen[k]))
        else:
            seen[k] = n0

    def _label(d):
        try:
            return _describe(d)[2]
        except Exception:                                        # noqa: BLE001
            return str(d)

    _mh.begin_phases(
        [("Clipping {} of {}: {}...".format(i + 1, len(datasets),
                                            _label(d)), 2)
         for i, d in enumerate(datasets)])

    fc_out, ras_out, details = [], [], []
    produced = set()          # outputs written THIS run (casefolded paths)
    n_fail = 0
    for ds in datasets:
        _mh.phase_advance()
        try:
            d, path, name = _describe(ds)
            raster = _is_raster(d)
            # A MAP LAYER input remembers its layer name so the caller can
            # copy the original layer's symbology onto the clipped copy; a
            # dataset browsed from disk has no symbology to copy.
            src_is_layer = getattr(d, "dataType", "").endswith("Layer")
            base = os.path.splitext(os.path.basename(name))[0]
            in_sr = getattr(d, "spatialReference", None)
            project = (out_cs is not None and in_sr is not None
                       and getattr(in_sr, "name", "Unknown") != "Unknown"
                       and not _same_cs(in_sr, out_cs))
            if (out_cs is not None and (in_sr is None
                    or getattr(in_sr, "name", "Unknown") == "Unknown")):
                _warn(messages,
                      "{}: no coordinate system recorded, so it cannot be "
                      "projected -- clipped only.".format(name))

            tr, fallback = (None, False)
            if project:
                tr, fallback = transformation_for(
                    in_sr, out_cs, transformations,
                    getattr(d, "extent", None))
                if fallback:
                    _say(messages,
                         "{}: none of the chosen transformations fits its "
                         "datum ({}); using Esri's suggestion: {}.".format(
                             name, in_sr.GCS.datumName, tr))

            target, safe = _out_name(base, workspace, is_folder, raster,
                                     prefix, suffix)
            if target.lower() in produced:
                # A within-run name collision must never replace an output
                # this same run just wrote (two same-named inputs would
                # otherwise leave only the LAST one).
                _warn(messages,
                      "{}: the output name {} was already written earlier "
                      "in this run -- writing a numbered copy so the "
                      "earlier output survives.".format(
                          name, os.path.basename(target)))
                target = arcpy.CreateUniqueName(safe, workspace)
            cands = _input_candidates(ds, name, path)
            if raster:
                def make(dest, _c=cands, _tr=tr, _n=name):
                    step1 = (arcpy.CreateUniqueName(
                        "clip_tmp.tif", arcpy.env.scratchFolder)
                        if project else dest)
                    _first_that_works(
                        _c,
                        lambda c: arcpy.management.Clip(
                            c, "#", step1, in_template_dataset=area,
                            clipping_geometry="ClippingGeometry",
                            maintain_clipping_extent="NO_MAINTAIN_EXTENT"),
                        _n, messages)
                    if project:
                        # Always NEAREST (Jeff, 2026-08-15): one honest,
                        # never-inventing-values method for every raster
                        # beats per-raster resampling choices; users who
                        # want bilinear/cubic for a surface should Project
                        # Raster first, then clip the projected copy.
                        arcpy.management.ProjectRaster(
                            step1, dest,
                            out_cs,
                            resampling_type="NEAREST",
                            geographic_transform=(_tr or ""))
                        try:
                            arcpy.management.Delete(step1)
                        except Exception:                        # noqa: BLE001
                            pass

                target = _produce(target, workspace, safe, make, messages)
                produced.add(target.lower())
                ras_out.append(target)
                details.append({"path": target, "raster": True,
                                "sym_source": name if src_is_layer
                                else None})
            else:
                def make(dest, _c=cands, _tr=tr, _n=name):
                    step1 = (arcpy.CreateUniqueName("clip_tmp", scratch)
                             if project else dest)
                    _first_that_works(
                        _c,
                        lambda c: arcpy.analysis.Clip(c, area, step1),
                        _n, messages)
                    if project:
                        arcpy.management.Project(
                            step1, dest, out_cs,
                            transform_method=(_tr or ""))
                        try:
                            arcpy.management.Delete(step1)
                        except Exception:                        # noqa: BLE001
                            pass

                target = _produce(target, workspace, safe, make, messages)
                produced.add(target.lower())
                n = int(arcpy.management.GetCount(target)[0])
                if n == 0:
                    _warn(messages,
                          "{}: no features fall inside the analysis area "
                          "(the clipped output is empty).".format(name))
                fc_out.append(target)
                details.append({"path": target, "raster": False,
                                "sym_source": name if src_is_layer
                                else None})

            cs_note = ""
            if project:
                cs_note = ", projected to {}{}".format(
                    out_cs.name,
                    " (transformation {})".format(tr) if tr else "")
            _stamp(target, name, area_name, cs_note, raster)
            _say(messages, "  {} -> {}{}".format(
                name, target, cs_note))
        except arcpy.ExecuteError:
            n_fail += 1
            # The failure handler must NEVER raise itself -- one broken
            # input warns, the batch continues (Jeff's first Pro run died
            # here when Describe rejected the same wrapper the tool did).
            try:
                label = _describe(ds)[2]
            except Exception:                                    # noqa: BLE001
                label = str(ds)
            _warn(messages,
                  "Could not clip {}: {}".format(
                      label, arcpy.GetMessages(2) or "see messages above"))
        except Exception as e:                                   # noqa: BLE001
            n_fail += 1
            _warn(messages, "Could not clip {}: {}".format(str(ds), e))

    _say(messages,
         "Clipped {} dataset{} ({} feature, {} raster){}.".format(
             len(fc_out) + len(ras_out),
             "" if len(fc_out) + len(ras_out) == 1 else "s",
             len(fc_out), len(ras_out),
             "; {} FAILED (see warnings)".format(n_fail) if n_fail else ""))
    _mh.end_phases()
    return fc_out, ras_out, details


def add_outputs_to_map(details, messages=None):
    """Add each clipped output to the active map (execute-time only --
    arcpy.mp's CURRENT project is reliable in execute, unlike in dialog
    validation), copying the ORIGINAL layer's symbology onto outputs whose
    input came in as a map layer; disk-browsed inputs get Pro's default
    symbology. Silently does nothing outside Pro."""
    try:
        m = arcpy.mp.ArcGISProject("CURRENT").activeMap
    except Exception:                                            # noqa: BLE001
        m = None
    if m is None:
        return
    n_added = n_styled = 0
    for row in details:
        try:
            lyr = m.addDataFromPath(row["path"])
            n_added += 1
        except Exception as e:                                   # noqa: BLE001
            _warn(messages, "Could not add {} to the map: {}".format(
                os.path.basename(str(row["path"])), e))
            continue
        src = row.get("sym_source")
        if src:
            # Copy the source layer's renderer/colorizer via CIM.
            # ApplySymbologyFromLayer against an arcpy.mp layer reports
            # success without reliably updating the map layer (Jeff's Pro
            # run: "5 styled" yet default symbology everywhere); setting
            # the CIM definition directly is deterministic.
            try:
                src_lyr = next(l for l in m.listLayers()
                               if l.name == src)
                cim_s = src_lyr.getDefinition("V3")
                cim_n = lyr.getDefinition("V3")
                copied = False
                for attr in ("renderer", "colorizer"):
                    val = getattr(cim_s, attr, None)
                    if val is not None and hasattr(cim_n, attr):
                        setattr(cim_n, attr, val)
                        copied = True
                if copied:
                    lyr.setDefinition(cim_n)
                    n_styled += 1
            except Exception:                                    # noqa: BLE001
                _say(messages,
                     "  ({}: could not copy the symbology of layer "
                     "{!r}; default symbology used.)".format(
                         os.path.basename(str(row["path"])), src))
    if n_added:
        _say(messages,
             "Added {} clipped layer{} to the map{}.".format(
                 n_added, "" if n_added == 1 else "s",
                 " ({} styled like their source layers)".format(n_styled)
                 if n_styled else ""))
