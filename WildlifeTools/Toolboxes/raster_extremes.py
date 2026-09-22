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

"""Find Raster Extreme Values -- peaks and low points of a DEM, as points.

Finds the REGIONAL extremes of a surface and writes one point feature per
extreme. The two kinds are perfect mirrors of one another:

    a LOW POINT is a cell (or a connected flat of equal cells) with no lower
    way out -- a D8 sink;
    a PEAK is exactly the same thing on the INVERTED surface -- a cell (or
    flat) with no higher neighbor, i.e. a summit.

One implementation therefore serves both, run once on the DEM and once on its
negation. (An earlier flow-accumulation-based definition of peaks was
deliberately dropped: every cell with no upslope contributor has accumulation
zero, which describes whole ridgelines and divide crests -- often a quarter of
the raster -- not summits.)

PLATEAUS: connected cells of EQUAL extreme value are grouped into one region
and yield ONE point (at the region cell nearest its centroid), so a mesa top
or a flat-bottomed basin -- ubiquitous in integer DEMs -- produces a single
feature rather than a cloud. A flat BENCH on a hillside (equal cells whose rim
leads downhill) is correctly rejected: a region only qualifies if every known
neighbor is strictly on the wrong side of it.

PROMINENCE, nearly free: the priority-flood depression fill already in this
toolbox (shared with the TWI tool; Barnes et al. 2014) gives, at the bottom of
every closed depression, the depth to its spill point. Run on the INVERTED
DEM, that same number at a summit is its TOPOGRAPHIC PROMINENCE -- the drop
from the summit to the key saddle separating it from higher ground, the
standard measure of whether a bump is a mountain. Both are written to the
output (one Prominence field: depression depth for low points, saddle
prominence for peaks) and drive the optional minimum-prominence filter.
Extremes whose saddle path leaves the raster are measured against the raster
edge (the standard convention for a bounded map), and edge-draining low
points have depth 0.

EDGE FLAG: every point records whether its region touches the raster edge or
NoData -- where neighbors are unknown, so the extreme (and its prominence) may
be an artifact of truncation. The field lets users screen those out.

The output honors the Output Coordinates geoprocessing environment (most tools
in this suite deliberately ignore environments; this one is explicitly asked
to respect it): geometry AND the X/Y attribute fields are written in the
output feature class's actual coordinate system -- the DEM's, unless the
environment overrides it.

No Spatial Analyst required. Citation: Barnes, R., C. Lehman, and D. Mulla.
2014. Priority-flood: an optimal depression-filling and watershed-labeling
algorithm for digital elevation models. Computers & Geosciences 62:117-127.
https://doi.org/10.1016/j.cageo.2013.04.024
"""
import os

import numpy as np

import mahalanobis as _mh
import hillshade as _hs
import topographic_tools as _tt
import tool_metadata as _tm

EXTREME_TYPES = ["Both (peaks and low points)", "Peaks (high points) only",
                 "Low points only"]

# Unique-Values symbology template (blue Peak, red Low Point, on "Type"),
# captured from Jeff's hand-made sample; its renderer is grafted onto a layer
# file saved from each run's real output.
LAYER_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "LayerTemplates", "RasterExtremes_Points.lyrx")

TYPE_PEAK = "Peak"
TYPE_LOW = "Low Point"

_CITATION = (
    "Barnes, R., C. Lehman, and D. Mulla. 2014. Priority-flood: an optimal "
    "depression-filling and watershed-labeling algorithm for digital "
    "elevation models. Computers & Geosciences 62:117-127. "
    "https://doi.org/10.1016/j.cageo.2013.04.024")


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def find_extremes(a, valid, messages=None):
    """Regional minima of ``a`` (masked by ``valid``), plateau-grouped.

    Returns a list of dicts: {row, col, value, prominence, edge} -- one per
    extreme region, the (row, col) being the region cell nearest the region's
    centroid. Run with ``-a`` for maxima (value/prominence are then negated by
    the caller). Pure numpy/scipy; independently testable.
    """
    from scipy import ndimage

    H, W = a.shape
    af = np.where(valid, a.astype(np.float64), np.inf)

    # Cells whose neighborhood is not fully known: on the array border, or
    # 8-adjacent to NoData. Regions touching these get edge=True.
    interior = ndimage.binary_erosion(valid, structure=np.ones((3, 3), bool),
                                      border_value=0)
    edgezone = valid & ~interior

    # Priority-flood fill: depth > 0 marks every cell of a closed depression.
    filled, _order = _tt._twi_priority_flood(af, valid, messages)
    depth = np.where(valid, filled - af, 0.0)

    results = []

    def _emit(region_mask, value, prom):
        # One point per region: the region cell nearest the region centroid
        # (a bare centroid can fall outside an L-shaped flat).
        rr, cc = np.nonzero(region_mask)
        cy, cx = rr.mean(), cc.mean()
        k = int(np.argmin((rr - cy) ** 2 + (cc - cx) ** 2))
        results.append({
            "row": int(rr[k]), "col": int(cc[k]),
            "value": float(value), "prominence": float(prom),
            "edge": bool(np.any(edgezone[region_mask])),
        })

    # ---- bottoms of closed depressions ------------------------------------
    dep_lab, n_dep = ndimage.label(depth > 0, structure=np.ones((3, 3), int))
    if n_dep:
        idx = np.arange(1, n_dep + 1)
        dep_min = ndimage.minimum(af, labels=dep_lab, index=idx)
        # All cells at each depression's lowest value, then re-labelled so two
        # separate equal-elevation pits inside ONE depression each get a point.
        min_of = np.zeros(n_dep + 1, np.float64)
        min_of[1:] = dep_min
        bottoms = (dep_lab > 0) & (af == min_of[dep_lab])
        bot_lab, n_bot = ndimage.label(bottoms, structure=np.ones((3, 3), int))
        for sl, bi in zip(ndimage.find_objects(bot_lab), range(1, n_bot + 1)):
            if sl is None:
                continue
            m = np.zeros((H, W), bool)
            m[sl] = bot_lab[sl] == bi
            r0, c0 = np.argwhere(m)[0]
            # Depth at the bottom = fill level minus bottom value.
            _emit(m, af[r0, c0], float(filled[r0, c0] - af[r0, c0]))

    # ---- zero-depth extremes (edge-draining) ------------------------------
    # Candidates with no strictly-lower known neighbor but nothing to fill:
    # their plateau reaches the raster edge / NoData. A bench (flat whose rim
    # leads DOWN) also shows up here and is rejected by the rim test: every
    # known neighbor outside the region must be STRICTLY higher -- an EQUAL
    # neighbor outside the region means the flat continues into cells that do
    # have a lower way out, i.e. the flat drains.
    minf3 = ndimage.minimum_filter(af, size=3, mode="constant", cval=np.inf)
    cand = valid & (af == minf3) & ~(depth > 0)
    zc_lab, n_zc = ndimage.label(cand, structure=np.ones((3, 3), int))
    grow = np.ones((3, 3), bool)
    for sl, zi in zip(ndimage.find_objects(zc_lab), range(1, n_zc + 1)):
        if sl is None:
            continue
        sl2 = (slice(max(0, sl[0].start - 1), min(H, sl[0].stop + 1)),
               slice(max(0, sl[1].start - 1), min(W, sl[1].stop + 1)))
        blob = zc_lab[sl2] == zi
        val = float(af[sl2][blob][0])
        rim = ndimage.binary_dilation(blob, structure=grow) & ~blob \
            & valid[sl2]
        if rim.any() and not np.all(af[sl2][rim] > val):
            continue                          # a bench: the flat drains
        m = np.zeros((H, W), bool)
        m[sl2] = blob
        _emit(m, val, 0.0)

    return results


def write_layer_file(out_fc, messages=None):
    """A .lyrx beside ``out_fc`` carrying the Unique-Values-on-Type symbology
    (blue Peak, red Low Point) from the shipped template, with the data
    connection captured from the REAL output (SaveToLayerFile handles every
    workspace type). Returns the .lyrx path, or None on any failure -- set it
    as the tool's output-parameter ``.symbology`` so the layer draws symbolized
    the moment it lands in the map. Same proven pattern as the LoCoH and
    Cartography layer files."""
    import json
    import arcpy

    try:
        if not os.path.exists(LAYER_TEMPLATE):
            _warn(messages, "Layer template not found; skipping symbology.")
            return None
        ws = os.path.dirname(str(out_fc))
        name = os.path.splitext(os.path.basename(str(out_fc)))[0]
        lyr_dir = _mh.lyrx_dir_for(ws)
        if lyr_dir is None:
            return None
        out_lyrx = os.path.join(lyr_dir, name + "_symbology.lyrx")

        tmp = "jenness_extremes_tmp_layer"
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass
        arcpy.management.MakeFeatureLayer(out_fc, tmp)
        try:
            if os.path.exists(out_lyrx):      # SaveToLayerFile refuses to overwrite
                os.remove(out_lyrx)
            arcpy.management.SaveToLayerFile(tmp, out_lyrx, "ABSOLUTE")
        finally:
            try:
                arcpy.management.Delete(tmp)
            except Exception:
                pass

        with open(out_lyrx, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        with open(LAYER_TEMPLATE, "r", encoding="utf-8") as fh:
            tpl = json.load(fh)
        tpl_layer = tpl["layerDefinitions"][0]
        layer = doc["layerDefinitions"][0]
        layer["renderer"] = tpl_layer["renderer"]
        layer["name"] = name
        if "featureTable" in layer:
            layer["featureTable"]["displayField"] = "Type"
        with open(out_lyrx, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        return _mh.ensure_label_class(out_lyrx, messages)
    except Exception as e:                                       # noqa: BLE001
        _warn(messages, "Could not create the symbology layer file: "
                        "{}".format(e))
        return None


def run_find_extremes(in_raster, out_fc, extreme_type=None,
                      min_prominence=0.0, window=1, messages=None):
    """Find the peaks and/or low points of ``in_raster`` and write them to the
    point feature class ``out_fc``. Returns the symbology .lyrx path (or
    None); set it on the tool's output parameter."""
    import arcpy
    from scipy import ndimage

    extreme_type = extreme_type or EXTREME_TYPES[0]
    if extreme_type not in EXTREME_TYPES:
        raise arcpy.ExecuteError("Unknown extreme type: " + str(extreme_type))
    want_peaks = "Low points" not in extreme_type
    want_lows = "Peaks" not in extreme_type
    min_prominence = float(min_prominence or 0.0)
    window = max(1, int(window or 1))
    scratch = arcpy.env.scratchFolder

    phases = [("Reading the DEM...", 2)]
    if want_lows:
        phases.append(("Finding low points (priority-flood)...", 4))
    if want_peaks:
        phases.append(("Finding peaks (priority-flood, inverted)...", 4))
    phases.append(("Writing the points...", 2))
    _mh.begin_phases(phases)

    src, in_copy = _hs._open_source(in_raster, scratch, messages)
    try:
        band = src.GetRasterBand(1)
        W, H = src.RasterXSize, src.RasterYSize
        if W < 3 or H < 3:
            raise arcpy.ExecuteError("The DEM must be at least 3 x 3 cells.")
        gt = src.GetGeoTransform()
        z = band.ReadAsArray(0, 0, W, H).astype(np.float64)
        nod = band.GetNoDataValue()
        vmask = _mh.band_validity_mask(band)   # capture while src is open
    finally:
        src = None
        if in_copy:
            try:
                arcpy.management.Delete(in_copy)
            except Exception:
                pass

    valid = np.isfinite(z)
    if nod is not None:
        with np.errstate(invalid="ignore"):
            valid &= (z != nod)
    if vmask is not None:
        valid &= vmask
    if not valid.any():
        raise arcpy.ExecuteError("The DEM has no valid (non-NoData) cells.")

    dem_sr = arcpy.Describe(in_raster).spatialReference
    try:
        cat = arcpy.Describe(in_raster).catalogPath
    except Exception:
        cat = str(in_raster)
    src_name = os.path.basename(cat)
    src_ws = os.path.dirname(cat)

    # ---- find ---------------------------------------------------------------
    points = []                       # (type, result-dict) in DEM sign space
    if want_lows:
        _mh.phase_advance()
        lows = find_extremes(z, valid, messages)
        for r in lows:
            points.append((TYPE_LOW, r))
        _say(messages, "{:,} low point{} found.".format(
            len(lows), "" if len(lows) == 1 else "s"))
    if want_peaks:
        _mh.phase_advance()
        highs = find_extremes(np.where(valid, -z, np.inf), valid, messages)
        for r in highs:
            r["value"] = -r["value"]          # back to real elevations
            points.append((TYPE_PEAK, r))
        _say(messages, "{:,} peak{} found.".format(
            len(highs), "" if len(highs) == 1 else "s"))

    # ---- optional filters ---------------------------------------------------
    if min_prominence > 0:
        n0 = len(points)
        points = [(t, r) for t, r in points
                  if r["prominence"] >= min_prominence]
        if n0 != len(points):
            _say(messages,
                 "{:,} of {:,} extremes kept at minimum prominence/depth "
                 "{:g} (edge-draining low points have depth 0 and are "
                 "removed by any positive threshold).".format(
                     len(points), n0, min_prominence))

    if window > 1:
        size = 2 * window + 1
        zi = np.where(valid, z, np.inf)
        zx = np.where(valid, z, -np.inf)
        minw = ndimage.minimum_filter(zi, size=size, mode="constant",
                                      cval=np.inf)
        maxw = ndimage.maximum_filter(zx, size=size, mode="constant",
                                      cval=-np.inf)
        n0 = len(points)
        points = [(t, r) for t, r in points
                  if (t == TYPE_LOW and z[r["row"], r["col"]]
                      == minw[r["row"], r["col"]])
                  or (t == TYPE_PEAK and z[r["row"], r["col"]]
                      == maxw[r["row"], r["col"]])]
        _say(messages,
             "{:,} of {:,} extremes kept as the most extreme within {} "
             "cells.".format(len(points), n0, window))

    # Most prominent first, peaks before lows -- a friendly attribute order.
    points.sort(key=lambda tr: (tr[0] != TYPE_PEAK, -tr[1]["prominence"]))

    # ---- write --------------------------------------------------------------
    _mh.phase_advance("Writing the points...")
    out_sr = arcpy.env.outputCoordinateSystem or dem_sr
    reproject = (out_sr is not None and dem_sr is not None
                 and out_sr.factoryCode != dem_sr.factoryCode)
    if reproject:
        _say(messages,
             "The Output Coordinates environment is set: the points (and the "
             "X / Y fields) are written in {}.".format(out_sr.name))

    out_ws, out_name = os.path.split(str(out_fc))
    arcpy.management.CreateFeatureclass(out_ws, out_name, "POINT",
                                        spatial_reference=_mh.clean_sr(out_sr))
    fields = [("Type", "TEXT", 12), ("SrcRaster", "TEXT", 128),
              ("SrcPath", "TEXT", 254), ("Elevation", "DOUBLE", None),
              ("Prominence", "DOUBLE", None), ("EdgeOfData", "SHORT", None),
              ("X", "DOUBLE", None), ("Y", "DOUBLE", None)]
    # one AddFields call, not one AddField per field (standing rule)
    arcpy.management.AddFields(out_fc, [
        [nm, tp, "", ln or "", "", ""] for nm, tp, ln in fields])

    n_edge = 0
    with arcpy.da.InsertCursor(
            out_fc, ["SHAPE@", "Type", "SrcRaster", "SrcPath", "Elevation",
                     "Prominence", "EdgeOfData", "X", "Y"]) as cur:
        for i, (typ, r) in enumerate(points):
            # Cell-centre coordinates in the DEM's grid...
            col, row = r["col"], r["row"]
            x = gt[0] + (col + 0.5) * gt[1] + (row + 0.5) * gt[2]
            y = gt[3] + (col + 0.5) * gt[4] + (row + 0.5) * gt[5]
            pg = arcpy.PointGeometry(arcpy.Point(x, y), dem_sr)
            # ...projected so the geometry AND the X/Y fields agree with the
            # output feature class's coordinate system.
            if reproject:
                pg = pg.projectAs(out_sr)
            pt = pg.centroid
            if r["edge"]:
                n_edge += 1
            cur.insertRow([pg, typ, src_name, src_ws, r["value"],
                           r["prominence"], 1 if r["edge"] else 0,
                           float(pt.X), float(pt.Y)])
            if (i + 1) % 200 == 0:
                _mh.phase_step(i + 1, max(1, len(points)))

    _say(messages, "{:,} point{} written{}.".format(
        len(points), "" if len(points) == 1 else "s",
        " ({:,} on the edge of the data, flagged EdgeOfData = 1)".format(
            n_edge) if n_edge else ""))

    # ---- metadata (standing rules: dataset + field level) -------------------
    try:
        _tm.set_entity_and_fields(
            out_fc,
            entity=("One point per regional extreme of the source raster: "
                    "peaks (summits) and/or low points (sinks), plateau-"
                    "grouped so a flat top or flat-bottomed basin yields a "
                    "single point."),
            fields=[
                {"name": "Type", "description":
                    "Which kind of extreme this point is.",
                 "coded": [(TYPE_PEAK, "A summit: no known neighbor is "
                            "higher (a sink of the inverted surface)."),
                           (TYPE_LOW, "A sink: no known neighbor is lower "
                            "(no outflow direction).")]},
                {"name": "SrcRaster", "description":
                    "Name of the source raster the extremes were found on."},
                {"name": "SrcPath", "description":
                    "Workspace path of the source raster."},
                {"name": "Elevation", "description":
                    "The extreme cell's value, in the source raster's own "
                    "units."},
                {"name": "Prominence", "description":
                    "For a peak: its topographic prominence -- the drop from "
                    "the summit to the key saddle separating it from higher "
                    "ground (measured to the raster edge when the saddle "
                    "path leaves the map). For a low point: the depth of its "
                    "depression below the spill point (0 for edge-draining "
                    "low points). Source-raster units."},
                {"name": "EdgeOfData", "description":
                    "Whether this extreme's region touches the raster edge "
                    "or NoData, where neighbors are unknown -- the extreme "
                    "and its prominence may be artifacts of truncation.",
                 "coded": [(1, "On the edge of the data."),
                           (0, "Interior: all neighbors known.")]},
                {"name": "X", "description":
                    "X-coordinate of the point, in the output feature "
                    "class's coordinate system."},
                {"name": "Y", "description":
                    "Y-coordinate of the point, in the output feature "
                    "class's coordinate system."},
            ],
            messages=messages)
        from arcpy import metadata as md
        m = md.Metadata(out_fc)
        m.title = os.path.basename(str(out_fc))
        m.tags = ("peaks, summits, low points, sinks, pits, extremes, "
                  "prominence, DEM, topography, Jenness Enterprises")
        m.summary = (
            "Created by Find Raster Extreme Values from {src}: {what}, "
            "minimum prominence/depth {mp:g}, dominance window {w} cell{ws}. "
            "Coordinates are in {sr}{env}.").format(
                src=str(in_raster),
                what=extreme_type.lower(),
                mp=min_prominence, w=window, ws="" if window == 1 else "s",
                sr=(out_sr.name if out_sr is not None else "the DEM's "
                    "coordinate system"),
                env=(" (set by the Output Coordinates environment)"
                     if reproject else ""))
        m.description = (
            "Regional extremes of a raster surface as points. A low point is "
            "a cell or connected flat with no lower known neighbor (a sink); "
            "a peak is the same on the inverted surface (a summit). Flats "
            "are grouped to one point each; benches (flats that drain) are "
            "excluded. Prominence comes from priority-flood depression "
            "depth -- on the inverted surface this is the summit's "
            "topographic prominence. " + _CITATION)
        m.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                     "(http://www.jennessent.com). Method: " + _CITATION)
        m.save()
    except Exception as e:
        _warn(messages, "Could not write output metadata: {}".format(e))

    out_lyrx = write_layer_file(out_fc, messages)
    _mh.end_phases()
    return out_lyrx
