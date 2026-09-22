# -*- coding: utf-8 -*-
# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Scholarship Fund notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Union Corridors -- combine every input corridor into one seamless shape.

A modernized port of the original CorridorDesigner Python toolbox's
"Create union of all corridors" tool (``cor_unionAndDissolveCorridors.py`` /
GP name ``UnionCorridors``): geometrically unions the inputs together
(``arcpy.analysis.Union``, keeping every overlap distinct) then dissolves
the result into ONE combined multipart polygon with no dissolve field --
this DELIBERATELY drops per-input attributes, matching the original and
the tool's own documented purpose (the CorridorDesigner workshop book, ch.
4.1: "The union of single-species corridors constitutes a preliminary
linkage design... this union of corridors is the most obvious way to
fulfill our goal of 'no species left behind'"). You don't care which
species' corridor covers a given patch of ground -- only whether ANY
corridor does.

NOT LAND-FACET-SPECIFIC, same as every other tool in this Corridor Designer
family -- works on any set of polygon feature classes, corridor output or
otherwise.

PER-INPUT SELECTION, RESPECTED (Jeff's exact request, 2026-08-09). A
corridor feature class typically holds MANY percentile-width bands as
separate features in one output (the ``Percentile`` field) -- to union
just the 5% bands across several species, select ``Percentile = 5`` on
each input's layer in the map BEFORE running this tool; an input with no
active selection contributes every one of its features, same as always.
This only works for inputs picked FROM THE MAP -- a dataset added via the
GP dialog's Browse button is a bare catalog path with no concept of a
selection at all (selection state lives on the map layer, not the
dataset). ``arcpy.analysis.Union`` (and the rest of the GP framework) is
handed the ORIGINAL parameter value for each input, never a resolved
catalog path, specifically so this keeps working -- resolving early would
silently discard every selection, exactly the class of bug found and
fixed in Fill Corridor Holes's own engine the same day.

WARNS WHEN SOURCES COME FROM DIFFERENT ANALYSES. Every input's OWN Land
Facet linkage record (if it has one) is read and compared by
``analysis_id`` -- the id shared by every dataset that descended from the
SAME land facet clustering run or the same species-HSM corridor lineage,
inherited unchanged through every downstream step including Fill Corridor
Holes. More than one distinct id among the inputs means the union is
combining genuinely UNRELATED analyses (different species, different
clustering runs) -- not necessarily wrong, but worth a clear warning
rather than a silent merge. The finding is also stamped on the output's
own linkage record (``union_record``'s ``mixed_analysis`` /
``source_analysis_ids``) so the Corridor Analysis Data Report can re-surface it
later without re-reading every source.

Needs no Spatial Analyst (the original checked for the extension anyway,
even though neither Union nor Dissolve ever used it).
"""
import os

import arcpy

import mahalanobis as _mh
import land_facet_linkage as _lnk


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def _source_label(rec):
    """A short, human-readable label for one source's linkage record, for
    run messages and the stored record alike -- reuses whatever the
    record's own kind considers its label, falling back to the tool name
    or the record kind itself."""
    if not rec:
        return None
    cat = rec.get("category") or {}
    if isinstance(cat, dict) and cat.get("label"):
        return cat["label"]
    return rec.get("tool") or rec.get("record")


def run_union_corridors(in_features_list, out_features, messages=None):
    """Union every input polygon feature class together (respecting any
    active per-input selection) and dissolve the result into one combined
    multipart polygon feature class -- matching the original CorridorDesigner
    "Create union of all corridors" tool. Returns ``out_features``."""
    if not in_features_list:
        raise arcpy.ExecuteError("No input feature classes were supplied.")

    resolved = []
    for item in in_features_list:
        try:
            path = arcpy.Describe(item).catalogPath
        except Exception:                                          # noqa: BLE001
            path = str(item)
        resolved.append((item, path))

    sources = []
    usable_items = []
    for item, path in resolved:
        desc = arcpy.Describe(item)
        if desc.shapeType != "Polygon":
            _warn(messages, "{}: not a polygon feature class; "
                            "skipped.".format(os.path.basename(path)))
            continue

        n_total, n_selected, has_selection = _mh.selection_info(item, path)
        # pin the selection NOW: arcpy.analysis.Union consumes the
        # inputs much later, and a live layer's selection can be
        # dropped by unrelated arcpy calls in between (standing rule,
        # see mahalanobis.pin_selection)
        pin = _mh.pin_selection(item)
        if has_selection:
            _say(messages, "{}: {:,} of {:,} feature{} selected -- "
                           "processing only the selection.".format(
                               os.path.basename(path), n_selected, n_total,
                               "" if n_total == 1 else "s"))

        rec = _lnk.read(path, None)
        sources.append({
            "path": path,
            "record_kind": (rec or {}).get("record"),
            "analysis_id": (rec or {}).get("analysis_id"),
            "label": _source_label(rec),
            "n_total": n_total,
            "n_selected": n_selected,
            "selected": bool(has_selection),
        })
        usable_items.append((item, path, pin))

    if not usable_items:
        raise arcpy.ExecuteError(
            "No usable polygon inputs -- every input was skipped (see "
            "messages above).")
    if len(usable_items) == 1:
        _warn(messages, "Only one usable input was supplied -- the output "
                        "will just be that input's selected (or all) "
                        "features, unioned with nothing.")

    distinct_ids = {s["analysis_id"] for s in sources if s["analysis_id"]}
    mixed_analysis = len(distinct_ids) > 1
    if mixed_analysis:
        by_id = {}
        for s in sources:
            if s["analysis_id"]:
                by_id.setdefault(s["analysis_id"], []).append(
                    s["label"] or os.path.basename(s["path"]))
        detail = "; ".join(
            "{}: {}".format(aid, ", ".join(labels))
            for aid, labels in by_id.items())
        _warn(messages, "These inputs carry DIFFERENT Land Facet analysis "
                        "ids -- this union combines genuinely unrelated "
                        "analyses (different species, different clustering "
                        "runs), not just different categories within one "
                        "analysis: {}.".format(detail))
    n_no_record = sum(1 for s in sources if not s["record_kind"])
    if 0 < n_no_record < len(sources):
        _say(messages, "{:,} of {:,} input{} carry no Land Facet linkage "
                       "record at all (not necessarily a problem -- a "
                       "hand-digitized or non-Jenness polygon layer has "
                       "none by design).".format(
                           n_no_record, len(sources),
                           "" if len(sources) == 1 else "s"))

    _mh.begin_phases([("Union", 3), ("Dissolve", 1)])
    _mh.phase_advance()
    out_ws, out_name = os.path.split(out_features)
    # Keep any format extension (.shp) at the END of the scratch name -- an
    # embedded mid-name '.shp' is an invalid folder dataset name and made
    # every shapefile destination fail before producing anything.
    base, ext = os.path.splitext(out_name)
    scratch = os.path.join(out_ws, "_" + base + "_union_scratch" + ext)
    if arcpy.Exists(scratch):
        arcpy.management.Delete(scratch)
    # feed Union the PINNED selections (explicit OID scratch layers),
    # never the live layers whose selections may have evaporated
    feed = []
    pin_layers = []
    for k, (item, path, pin) in enumerate(usable_items):
        if pin:
            lyr = _mh.pinned_layer(path, pin, "uc_pin_{}".format(k))
            pin_layers.append(lyr)
            feed.append(lyr)
        else:
            feed.append(item)
    try:
        arcpy.analysis.Union(feed, scratch, "ALL", "", "GAPS")
        _mh.phase_advance()
        arcpy.management.Dissolve(scratch, out_features, "", "", "MULTI_PART")
    finally:
        for lyr in pin_layers:
            try:
                arcpy.management.Delete(lyr)
            except Exception:                                      # noqa: BLE001
                pass
        if arcpy.Exists(scratch):
            try:
                arcpy.management.Delete(scratch)
            except Exception:                                      # noqa: BLE001
                pass
    _mh.end_phases()

    _lnk.write(out_features, _lnk.union_record(
        sources=sources, mixed_analysis=mixed_analysis,
        source_analysis_ids=sorted(distinct_ids)), messages)

    try:
        from arcpy import metadata as md
        m = md.Metadata(out_features)
        m.title = os.path.basename(out_features)
        m.summary = "Created by Union Corridors from {:,} input{}: {}.".format(
            len(sources), "" if len(sources) == 1 else "s",
            ", ".join(os.path.basename(s["path"]) for s in sources))
        m.description = (
            "The geometric union of every input's selected (or all) "
            "features, dissolved into one combined multipart polygon -- "
            "per-input attributes are not preserved, by design (the point "
            "is a single combined shape: is this ground part of ANY "
            "corridor). A modernized port of the CorridorDesigner "
            "\"Create union of all corridors\" tool."
            + (" WARNING: sources carried different Land Facet analysis "
               "ids -- see the Corridor Analysis Data Report."
               if mixed_analysis else ""))
        m.save()
    except Exception as e:                                          # noqa: BLE001
        _warn(messages, "Could not write output metadata: {}".format(e))

    _say(messages, "Union of {:,} input{} written.".format(
        len(sources), "" if len(sources) == 1 else "s"))
    return out_features
