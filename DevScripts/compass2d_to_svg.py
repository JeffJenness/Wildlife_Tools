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

"""Generate the Compass_2d artwork SVGs for the WildlifeTools add-in:

    WildlifeTools/Cartography/Compass2.svg     "Compass graphic 2" / "North arrow 2"
    WildlifeTools/Cartography/Compass2Dot.svg  "Compass graphic 2 - dot" / "North arrow 2 - dot"

Compass2.svg is the Compass_2d polygon feature class symbolized per the
unique-value renderer in Pro_Test_Project/Compass_2d.lyrx. Compass2Dot.svg is
the same artwork plus the Sample_North arrow drawn on top, symbolized per
Pro_Test_Project/Compass_2d_North.lyrx — a white outline over a BUFFERED
DISCRETE GRADIENT fill (fromColor at the polygon edge stepping to toColor
inward across gradientSize% of the max inward distance). SVG has no buffered
gradient, so the bands are built geometrically as nested negative buffers,
painted largest-first. The arrow extends the canvas upward, so the script
prints the relative focal coordinates of the compass center that the add-in's
CompassArt constants must match.

Both .lyrx files are parsed at run time — re-symbolize in Pro, re-save the
.lyrx, rerun this script, rebuild the add-in to restyle the artwork.

Sources of record (all under Pro_Test_Project/):
    Pro Test Tools.gdb/Compass_2d      (copied from Default.gdb)
    Pro Test Tools.gdb/Sample_North
    Compass_2d.lyrx                    (CIMUniqueValueRenderer on ID)
    Compass_2d_North.lyrx              (CIMSimpleRenderer, gradient fill)

Output conventions match fc_to_svg.py (1000 units per page unit, y flipped,
%.2f coords, 20-unit margin, fill-rule="evenodd", stroke-width = points * 7.5),
except raw coordinates are first normalized so each artwork's long side is
2.0 page units — the FCs are digitized in UTM meters, and this keeps stroke
weights comparable to the classic compass art.

Run with the ArcGIS Pro Python environment (needs osgeo/OGR):
    "C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" compass2d_to_svg.py
"""
import json
import os

from osgeo import ogr

ogr.UseExceptions()

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.normpath(os.path.join(HERE, "..", "..", "Pro_Test_Project"))
GDB = os.path.join(PROJ, "Pro Test Tools.gdb")
LYRX_COMPASS = os.path.join(PROJ, "Compass_2d.lyrx")
LYRX_NORTH = os.path.join(PROJ, "Compass_2d_North.lyrx")
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "WildlifeTools", "Cartography"))

COMPASS_FC = "Compass_2d"
NORTH_FC = "Sample_North"
ID_FIELD = "ID"

NORM_LONG_SIDE = 2.0     # page units the artwork's long side is normalized to
SCALE = 1000.0           # SVG units per page unit
MARGIN = 20.0            # SVG units per side
STROKE_FACTOR = 7.5      # stroke points -> SVG stroke-width
PX_FACTOR = 0.32         # viewBox units -> width/height pixel attributes


def fmt(v):
    return f"{v:.2f}"


def rgb(color):
    """CIMRGBColor {values:[r,g,b,alpha]} -> #RRGGBB."""
    r, g, b = (int(round(c)) for c in color["values"][:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def lerp_rgb(c_from, c_to, t):
    """#RRGGBB linearly interpolated between two CIMRGBColors at t in [0,1]."""
    vals = [round(f + t * (to - f))
            for f, to in zip(c_from["values"][:3], c_to["values"][:3])]
    return "#{:02X}{:02X}{:02X}".format(*(int(v) for v in vals))


def read_compass_classes():
    """ID value -> {fill, stroke, width(points)} from the unique-value renderer."""
    with open(LYRX_COMPASS, encoding="utf-8") as f:
        doc = json.load(f)
    renderer = doc["layerDefinitions"][0]["renderer"]
    if renderer["type"] != "CIMUniqueValueRenderer":
        raise SystemExit(f"Expected CIMUniqueValueRenderer, got {renderer['type']}")
    classes = {}
    for group in renderer["groups"]:
        for cls in group["classes"]:
            fill, stroke, width = "none", "none", 0.0
            for lyr in cls["symbol"]["symbol"]["symbolLayers"]:
                if lyr["type"] == "CIMSolidFill" and lyr.get("enable"):
                    fill = rgb(lyr["color"])
                elif lyr["type"] == "CIMSolidStroke" and lyr.get("enable"):
                    stroke = rgb(lyr["color"])
                    width = lyr.get("width", 1.0)
            for uv in cls["values"]:
                classes[uv["fieldValues"][0]] = {
                    "fill": fill, "stroke": stroke, "width": width}
    return classes


def compass_rows():
    """Compass_2d features in OBJECTID order with their lyrx symbology."""
    classes = read_compass_classes()
    ds = ogr.Open(GDB)
    lyr = ds.GetLayerByName(COMPASS_FC)
    rows = []
    for f in lyr:
        key = f.GetField(ID_FIELD)
        if key not in classes:
            raise SystemExit(f"No lyrx symbol class for {ID_FIELD} = {key!r}")
        rows.append({"fid": f.GetFID(), "name": key,
                     **classes[key],
                     "geom": f.GetGeometryRef().GetLinearGeometry(2.0)})
    rows.sort(key=lambda r: r["fid"])
    return rows


def max_inward_distance(geom):
    """Largest d for which geom.Buffer(-d) is non-empty (bisection)."""
    xmin, xmax, ymin, ymax = geom.GetEnvelope()
    lo, hi = 0.0, max(xmax - xmin, ymax - ymin)
    for _ in range(60):
        mid = (lo + hi) / 2.0
        try:
            empty = geom.Buffer(-mid).IsEmpty()
        except Exception:
            empty = True
        if empty:
            hi = mid
        else:
            lo = mid
    return lo


def north_rows():
    """The Sample_North arrow as synthetic rows: discrete buffered-gradient
    bands (nested negative buffers painted largest-first, band color sampled at
    the band's starting fraction of the ramp), the toColor core, then the
    outline stroke on top."""
    with open(LYRX_NORTH, encoding="utf-8") as f:
        doc = json.load(f)
    renderer = doc["layerDefinitions"][0]["renderer"]
    if renderer["type"] != "CIMSimpleRenderer":
        raise SystemExit(f"Expected CIMSimpleRenderer, got {renderer['type']}")
    stroke, stroke_w, grad = "none", 0.0, None
    for lyr in renderer["symbol"]["symbol"]["symbolLayers"]:
        if lyr["type"] == "CIMSolidStroke" and lyr.get("enable"):
            stroke = rgb(lyr["color"])
            stroke_w = lyr.get("width", 1.0)
        elif lyr["type"] == "CIMGradientFill" and lyr.get("enable"):
            grad = lyr
    if grad is None:
        raise SystemExit("Expected a CIMGradientFill layer in " + LYRX_NORTH)

    c_from = grad["colorRamp"]["fromColor"]
    c_to = grad["colorRamp"]["toColor"]
    size_frac = grad.get("gradientSize", 100) / 100.0     # of max inward distance
    interval_frac = grad.get("interval", 25) / 100.0      # of the gradient span

    ds = ogr.Open(GDB)
    lyr = ds.GetLayerByName(NORTH_FC)
    feat = lyr.GetNextFeature()             # keep alive: geom ref dies with the feature
    geom = feat.GetGeometryRef().Clone().GetLinearGeometry(2.0)

    span = size_frac * max_inward_distance(geom)
    rows = []
    n_bands = max(1, round(1.0 / interval_frac))
    for k in range(n_bands):
        band = geom.Buffer(-span * k * interval_frac) if k else geom.Clone()
        if band.IsEmpty():
            break
        rows.append({"name": f"North_band{k}", "kind": "poly",
                     "fill": lerp_rgb(c_from, c_to, k * interval_frac),
                     "stroke": "none", "width": 0.0, "geom": band})
    core = geom.Buffer(-span)
    if not core.IsEmpty():
        rows.append({"name": "North_core", "kind": "poly", "fill": rgb(c_to),
                     "stroke": "none", "width": 0.0, "geom": core})
    rows.append({"name": "North_outline", "kind": "poly", "fill": "none",
                 "stroke": stroke, "width": stroke_w, "geom": geom.Clone()})
    return rows


def union_extent(rows):
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for r in rows:
        gxmin, gxmax, gymin, gymax = r["geom"].GetEnvelope()
        xmin, xmax = min(xmin, gxmin), max(xmax, gxmax)
        ymin, ymax = min(ymin, gymin), max(ymax, gymax)
    return xmin, ymin, xmax, ymax


def polygon_rings(geom):
    """Ring point lists for POLYGON or MULTIPOLYGON."""
    rings = []
    polys = ([geom] if geom.GetGeometryName() == "POLYGON"
             else [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())])
    for poly in polys:
        for i in range(poly.GetGeometryCount()):
            rings.append(poly.GetGeometryRef(i).GetPoints())
    return rings


def build_svg(rows, title, anchor):
    """SVG text + the anchor point's relative focal coords within the canvas.
    anchor = (x, y) in raw FC coordinates (the compass center)."""
    xmin, ymin, xmax, ymax = union_extent(rows)
    cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
    norm = NORM_LONG_SIDE / max(xmax - xmin, ymax - ymin)

    nxmin, nxmax = (xmin - cx) * norm, (xmax - cx) * norm
    nymin, nymax = (ymin - cy) * norm, (ymax - cy) * norm
    x0 = nxmin - MARGIN / SCALE
    y1 = nymax + MARGIN / SCALE
    vb_w = (nxmax - nxmin) * SCALE + 2.0 * MARGIN
    vb_h = (nymax - nymin) * SCALE + 2.0 * MARGIN
    px_w = round(vb_w * PX_FACTOR)
    px_h = round(vb_h * PX_FACTOR)

    def tx(x):
        return ((x - cx) * norm - x0) * SCALE

    def ty(y):
        return (y1 - (y - cy) * norm) * SCALE

    def path_data(rings):
        subpaths = []
        for pts in rings:
            coords = [f"M {fmt(tx(pts[0][0]))} {fmt(ty(pts[0][1]))}"]
            coords += [f"L {fmt(tx(x))} {fmt(ty(y))}" for x, y, *_ in pts[1:]]
            subpaths.append(" ".join(coords) + " Z")
        return " ".join(subpaths)

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w}" height="{px_h}" '
        f'viewBox="0 0 {fmt(vb_w)} {fmt(vb_h)}">',
        f"<title>{title}</title>",
    ]
    for r in rows:
        d = path_data(polygon_rings(r["geom"]))
        sw = fmt(r["width"] * STROKE_FACTOR)
        out.append(f'<path d="{d}" fill="{r["fill"]}" fill-rule="evenodd" '
                   f'stroke="{r["stroke"]}" stroke-width="{sw}"/>')
    out.append("</svg>")

    fx = tx(anchor[0]) / vb_w
    fy = 1.0 - ty(anchor[1]) / vb_h            # from the BOTTOM (add-in convention)
    return "\n".join(out), {"vb_w": vb_w, "vb_h": vb_h, "fx": fx, "fy": fy}


def write_svg(rows, title, anchor, out_path):
    svg, m = build_svg(rows, title, anchor)
    with open(out_path, "w", newline="\n") as f:
        f.write(svg)
    print(f"Wrote {out_path} ({len(svg):,} chars)")
    print(f"  viewBox {fmt(m['vb_w'])} x {fmt(m['vb_h'])}   "
          f"aspect(w/h) = {m['vb_w'] / m['vb_h']:.6f}")
    print(f"  focal of compass center: fx = {m['fx']:.6f}, "
          f"fy(from bottom) = {m['fy']:.6f}")


if __name__ == "__main__":
    compass = compass_rows()
    xmin, ymin, xmax, ymax = union_extent(compass)
    center = ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)   # compass center = focal
    write_svg(compass, "Jennessent Compass 2", center,
              os.path.join(OUT_DIR, "Compass2.svg"))
    write_svg(compass + north_rows(), "Jennessent Compass 2 with North Arrow",
              center, os.path.join(OUT_DIR, "Compass2Dot.svg"))
