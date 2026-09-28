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

PROMINENCE: cells are merged in ascending order of value (union-find over the
eight-connected grid). Each extreme region seeds its own component; when two
components meet, the one whose extreme is less extreme is absorbed and its
extreme is measured to the level at which they met -- the saddle. For a low
point that is the depth below the spill point of ITS OWN basin; on the
INVERTED DEM the same number at a summit is its TOPOGRAPHIC PROMINENCE -- the
drop from the summit to the key saddle separating it from higher ground, the
standard measure of whether a bump is a mountain. Every extreme gets its own
number, including a subsidiary summit inside a larger massif and a small pit
inside a larger bowl. (An earlier version read prominence off the fill
depth, which only exists at the bottom of each MERGED depression, and so
silently dropped every nested extreme -- on the Grand Canyon test DEM more
than a third of the summits. Fixed 2026-09-23.) Both kinds are written to the
output (one Prominence field: depression depth for low points, saddle
prominence for peaks) and drive the optional minimum-prominence filter.
Extremes whose saddle path leaves the raster are measured against the raster
edge or NoData: the prominence written is the drop the extreme's region had
already made when it first touched the outside, a LOWER BOUND on the true
value (an extreme lying on the edge itself, or beside NoData, has made no drop
and gets 0). Of two EQUAL extremes in one basin, one is measured to the
saddle between them and the other carries the basin's full depth.

EDGE FLAG (EdgeOfData = 1): the extreme's prominence/depth was measured
against the raster edge or NoData rather than against real terrain -- either
the extreme itself lies on the edge or beside NoData, or its region reached
the outside before meeting a higher (deeper) extreme. Such a value is a lower
bound; the field lets users screen those points out.

The output honors the Output Coordinates geoprocessing environment (most tools
in this suite deliberately ignore environments; this one is explicitly asked
to respect it): geometry AND the X/Y attribute fields are written in the
output feature class's actual coordinate system -- the DEM's, unless the
environment overrides it.

No Spatial Analyst required. Sink definition after O'Callaghan and Mark
(1984), Computer Vision, Graphics, and Image Processing 28:323-344.
"""
import os

import numpy as np

import clip_tools as _ct
import mahalanobis as _mh
import hillshade as _hs
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

_METHOD = (
    "Extremes are regional minima/maxima of the eight-connected grid "
    "(plateaus grouped, benches excluded); prominence is computed by merging "
    "cells in elevation order, each extreme measured to the saddle at which "
    "it joins a more extreme component or the raster edge. Sink definition "
    "after O'Callaghan and Mark (1984), Computer Vision, Graphics, and Image "
    "Processing 28:323-344.")


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


def find_extremes(a, valid, messages=None):
    """Regional minima of ``a`` (masked by ``valid``), plateau-grouped, each
    with its prominence.

    Returns a list of dicts: {row, col, value, prominence, edge} -- one per
    extreme region, the (row, col) being the region cell nearest the region's
    centroid. Run with ``-a`` for maxima (the caller negates ``value``;
    prominence is the same number either way). Pure numpy/scipy;
    independently testable.

    Prominence: cells are merged in ascending order of value (union-find over
    the eight-connected grid, with everything outside the valid area as one
    pre-merged component of value -inf). Each extreme region seeds a
    component; when two components meet at level v the one with the higher
    extreme is absorbed and its extreme's prominence is v minus its value.
    ``edge`` is True when the absorbing component held no REAL extreme at or
    below the absorbed one -- it won only because it contains the outside --
    so the prominence is the drop to the raster edge or NoData, a lower bound
    (0 when the extreme itself lies on the edge or beside NoData).
    """
    from scipy import ndimage

    H, W = a.shape
    af = np.where(valid, a.astype(np.float64), np.inf)
    eight = np.ones((3, 3), bool)

    # Cells whose neighborhood is not fully known: on the array border, or
    # 8-adjacent to NoData. Regions touching these get edge=True.
    interior = ndimage.binary_erosion(valid, structure=eight, border_value=0)
    edgezone = valid & ~interior

    # ---- 1. the extreme regions -------------------------------------------
    # Candidates: valid cells with no strictly lower known neighbor. Connected
    # candidates are necessarily equal-valued, so each candidate region is a
    # flat (or a single cell).
    minf3 = ndimage.minimum_filter(af, size=3, mode="constant", cval=np.inf)
    cand = valid & (af == minf3)
    lab, n_lab = ndimage.label(cand, structure=eight.astype(int))
    if n_lab == 0:
        return []
    # Bench test: a candidate region that touches an EQUAL valid cell which is
    # not itself a candidate continues into cells that have a lower way out,
    # i.e. the flat drains, and it is not an extreme.
    padf = np.pad(af, 1, constant_values=np.inf)
    padc = np.pad(cand, 1, constant_values=False)
    padv = np.pad(valid, 1, constant_values=False)
    drains = np.zeros(n_lab + 1, bool)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            sl = (slice(1 + dr, 1 + dr + H), slice(1 + dc, 1 + dc + W))
            hit = cand & padv[sl] & ~padc[sl] & (padf[sl] == af)
            if hit.any():
                drains[np.unique(lab[hit])] = True
    drains[0] = True
    keep_ids = np.flatnonzero(~drains)
    m = len(keep_ids)
    if m == 0:
        return []
    remap = np.zeros(n_lab + 1, np.int64)
    remap[keep_ids] = np.arange(1, m + 1)
    region = remap[lab]                      # 0 = not an extreme cell
    idx = np.arange(1, m + 1)
    reg_val = ndimage.minimum(af, labels=region, index=idx)
    reg_edge = ndimage.maximum(edgezone.astype(np.int8), labels=region,
                               index=idx) > 0

    # ---- 2. prominence: merge cells in ascending order ---------------------
    Wp = W + 2
    zl = padf.ravel().tolist()
    rl = np.pad(region, 1, constant_values=0).ravel().tolist()
    N = len(zl)
    parent = list(range(N))
    bid = [0] * N                     # bottom region id at a root (0 = none)
    bval = [float("inf")] * N         # that region's value (inf = none)
    rbest = [float("inf")] * N        # lowest REAL extreme in the component
    done = bytearray(N)
    OUT = 0                           # padded corner: the outside component
    for o in np.flatnonzero(~padv.ravel()).tolist():
        parent[o] = OUT
        done[o] = 1
    bid[OUT] = -1
    bval[OUT] = float("-inf")
    prom = [0.0] * (m + 1)
    edge_hit = [False] * (m + 1)      # measured against the outside
    order = np.flatnonzero(padv.ravel())
    order = order[np.argsort(padf.ravel()[order], kind="stable")].tolist()
    deltas = (-Wp - 1, -Wp, -Wp + 1, -1, 1, Wp - 1, Wp, Wp + 1)
    total = len(order)
    step = max(1, total // 40)
    for k, i in enumerate(order):
        done[i] = 1
        r = rl[i]
        if r:
            bid[i] = r
            bval[i] = zl[i]
            rbest[i] = zl[i]
        v = zl[i]
        for d in deltas:
            n = i + d
            if not done[n]:
                continue
            x = i
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            y = n
            while parent[y] != y:
                parent[y] = parent[parent[y]]
                y = parent[y]
            if x == y:
                continue
            # The component with the LOWER extreme survives; the other's
            # extreme (if it has one) is measured to this level, the saddle.
            if bval[y] < bval[x]:
                x, y = y, x
            if bid[y] > 0:
                prom[bid[y]] = v - bval[y]
                # x holds no real extreme at or below y's: x survived only
                # because it contains the outside, so this prominence is the
                # drop to the raster edge / NoData -- a lower bound.
                edge_hit[bid[y]] = rbest[x] > bval[y]
            if rbest[y] < rbest[x]:
                rbest[x] = rbest[y]
            parent[y] = x
        if messages is not None and k % step == 0:
            _mh.phase_step(k, total)

    # ---- 3. one point per region: the cell nearest the region centroid -----
    rr, cc = np.nonzero(region)
    reg = region[rr, cc]
    cy = ndimage.mean(rr.astype(np.float64), labels=reg, index=idx)
    cx = ndimage.mean(cc.astype(np.float64), labels=reg, index=idx)
    d2 = (rr - cy[reg - 1]) ** 2 + (cc - cx[reg - 1]) ** 2
    o = np.lexsort((d2, reg))
    first = np.searchsorted(reg[o], idx)
    sel = o[first]
    results = []
    for jx, s in enumerate(sel):
        results.append({
            "row": int(rr[s]), "col": int(cc[s]),
            "value": float(reg_val[jx]), "prominence": float(prom[jx + 1]),
            "edge": bool(reg_edge[jx]) or bool(edge_hit[jx + 1]),
        })
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


def disk_footprint(radius):
    """Boolean circular footprint for the dominance filter: every cell whose
    center lies within ``radius`` cells of the central cell's center (a
    circle, not a square, so a diagonal neighbor counts the same as a
    cardinal one at the same distance)."""
    radius = float(radius)
    r = int(np.ceil(radius))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) <= radius * radius + 1e-9


def dominance_filter(z, valid, points, radius):
    """Keep only the ``points`` (``(type, record)`` pairs) that are the most
    extreme valid cell within a circle of ``radius`` cells of themselves --
    peaks against the maximum, low points against the minimum."""
    from scipy import ndimage
    fp = disk_footprint(radius)
    zi = np.where(valid, z, np.inf)
    zx = np.where(valid, z, -np.inf)
    minw = ndimage.minimum_filter(zi, footprint=fp, mode="constant",
                                  cval=np.inf)
    maxw = ndimage.maximum_filter(zx, footprint=fp, mode="constant",
                                  cval=-np.inf)
    return [(t, r) for t, r in points
            if (t == TYPE_LOW and z[r["row"], r["col"]]
                == minw[r["row"], r["col"]])
            or (t == TYPE_PEAK and z[r["row"], r["col"]]
                == maxw[r["row"], r["col"]])]


def run_find_extremes(in_raster, out_fc, extreme_type=None,
                      min_prominence=0.0, window=1.5, messages=None):
    """Find the peaks and/or low points of ``in_raster`` and write them to the
    point feature class ``out_fc``. Returns the symbology .lyrx path (or
    None); set it on the tool's output parameter."""
    import arcpy

    extreme_type = extreme_type or EXTREME_TYPES[0]
    if extreme_type not in EXTREME_TYPES:
        raise arcpy.ExecuteError("Unknown extreme type: " + str(extreme_type))
    want_peaks = "Low points" not in extreme_type
    want_lows = "Peaks" not in extreme_type
    min_prominence = float(min_prominence or 0.0)
    # Radius in cells. Anything under 2 reaches at most the eight immediate
    # neighbors (1.5 = all eight, exactly 1 = the four cardinal ones), which
    # every extreme dominates by definition -- so the filter is skipped.
    window = max(1.0, float(window or 1.5))
    scratch = arcpy.env.scratchFolder

    phases = [("Reading the DEM...", 2)]
    if want_lows:
        phases.append(("Finding low points...", 4))
    if want_peaks:
        phases.append(("Finding peaks (inverted surface)...", 4))
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
                 "{:g} (extremes lying on the edge of the data or beside "
                 "NoData have prominence 0 and are removed by any positive "
                 "threshold).".format(
                     len(points), n0, min_prominence))

    if window >= 2:
        n0 = len(points)
        points = dominance_filter(z, valid, points, window)
        _say(messages,
             "{:,} of {:,} extremes kept as the most extreme cell within a "
             "circle of radius {:g} cells.".format(len(points), n0, window))

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
              ("SrcPath", "TEXT", 254), ("CellValue", "DOUBLE", None),
              ("Prominence", "DOUBLE", None), ("EdgeOfData", "SHORT", None),
              ("X", "DOUBLE", None), ("Y", "DOUBLE", None)]
    # one AddFields call, not one AddField per field (standing rule)
    arcpy.management.AddFields(out_fc, [
        [nm, tp, "", ln or "", "", ""] for nm, tp, ln in fields])

    n_edge = 0
    with arcpy.da.InsertCursor(
            out_fc, ["SHAPE@", "Type", "SrcRaster", "SrcPath", "CellValue",
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
                pg = _ct.project_geometry(pg, out_sr)
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
        " ({:,} measured against the edge of the data or NoData, a lower "
        "bound on their prominence, flagged EdgeOfData = 1)".format(
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
                {"name": "CellValue", "description":
                    "The extreme cell's value, in the source raster's own "
                    "units."},
                {"name": "Prominence", "description":
                    "For a peak: its topographic prominence -- the drop from "
                    "the summit to the key saddle separating it from higher "
                    "ground. For a low point: the depth of its depression "
                    "below the spill point. When the saddle path leaves the "
                    "map (EdgeOfData = 1) the value is the drop to the raster "
                    "edge or NoData, a lower bound on the true prominence; an "
                    "extreme lying on the edge itself gets 0. Source-raster "
                    "units."},
                {"name": "EdgeOfData", "description":
                    "1 when the prominence/depth was measured against the "
                    "raster edge or NoData rather than real terrain: the "
                    "extreme lies on the edge or beside NoData, or its region "
                    "reached the outside before meeting higher (deeper) "
                    "ground. The Prominence is then a lower bound.",
                 "coded": [(1, "Measured against the edge of the data "
                              "(prominence is a lower bound)."),
                           (0, "Interior: measured against real terrain.")]},
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
            "minimum prominence/depth {mp:g}, dominance radius {w:g} "
            "cells (circular). "
            "Coordinates are in {sr}{env}.").format(
                src=str(in_raster),
                what=extreme_type.lower(),
                mp=min_prominence, w=window,
                sr=(out_sr.name if out_sr is not None else "the DEM's "
                    "coordinate system"),
                env=(" (set by the Output Coordinates environment)"
                     if reproject else ""))
        m.description = (
            "Regional extremes of a raster surface as points. A low point is "
            "a cell or connected flat with no lower known neighbor (a sink); "
            "a peak is the same on the inverted surface (a summit). Flats "
            "are grouped to one point each; benches (flats that drain) are "
            "excluded. " + _METHOD)
        m.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                     "(http://www.jennessent.com). " + _METHOD)
        m.save()
    except Exception as e:
        _warn(messages, "Could not write output metadata: {}".format(e))

    out_lyrx = write_layer_file(out_fc, messages)
    _mh.end_phases()
    return out_lyrx
