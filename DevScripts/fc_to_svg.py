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

"""Regenerate WildlifeTools/Cartography/CompassRose.svg AND CompassRoseDot.svg
from the authoritative compass-rose feature classes.

CompassRoseDot.svg is the same artwork plus the "north dot" circle from
Compass_Rose_North_Circle (black fill, white outline per
Compass_Rose_North_Circle.lyrx), which extends the canvas upward; the script
prints the relative focal coordinates of the compass center (feature-class
origin 0,0) that the add-in's CompassArt constants must match.

The geometry source of record is the pair of feature classes built from the VBA
MakeNorthArrow port (see VBA_Sources/MyGeneralOperations_with_Compass.bas):

    Pro_Test_Project/Pro Test Tools.gdb/Compass_Rose_Poly
    Pro_Test_Project/Pro Test Tools.gdb/Compass_Rose_Line

Each feature carries Name, SymClass, FillColor, OutlineColor (named keys into
PALETTE below), OutlineWidth, and DrawOrder. To recolor the compass, edit those
attributes (or PALETTE) and rerun this script, then rebuild the add-in so the
new SVG deploys.

Output-format notes (kept bit-compatible with the originally shipped SVG):
  * 1000 SVG units per page unit, y flipped, coordinates written with %.2f.
  * A 20-unit margin on every side (viewBox = extent * 1000 + 40).
  * Polygons: fill-rule="evenodd" (required for the outer ring donut); rings
    keep their duplicate closing vertex AND end with "Z". Lines: round
    linecaps, no fill.
  * stroke-width = OutlineWidth * 7.5; OutlineWidth <= 0 -> stroke="none".

Run with the ArcGIS Pro Python environment (needs osgeo/OGR, not arcpy):
    "C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" fc_to_svg.py [out.svg]
"""
import os
import sys

from osgeo import ogr

ogr.UseExceptions()

HERE = os.path.dirname(os.path.abspath(__file__))
GDB = os.path.normpath(os.path.join(
    HERE, "..", "..", "Pro_Test_Project", "Pro Test Tools.gdb"))
DEFAULT_OUT = os.path.normpath(os.path.join(
    HERE, "..", "WildlifeTools", "Cartography", "CompassRose.svg"))

POLY_FC = "Compass_Rose_Poly"
LINE_FC = "Compass_Rose_Line"
DOT_FC = "Compass_Rose_North_Circle"    # extra "north dot" for CompassRoseDot.svg

# Named colors used by the FillColor / OutlineColor attribute fields.
PALETTE = {
    "black":  "#000000",
    "white":  "#FFFFFF",
    "gray1":  "#414141",   # dark gray (north wedge)
    "gray3":  "#BEBEBE",   # light gray (center circle)
    "vlgray": "#E6E6E6",   # very light gray (cardinal arrowheads)
    "turq":   "#00CED1",   # south wedge
    "yellow": "#FFFF00",   # west wedge
    "ganado": "#7D1B28",   # Ganado red (inner circle)
}

SCALE = 1000.0           # SVG units per page unit
MARGIN = 20.0            # SVG units per side
STROKE_FACTOR = 7.5      # OutlineWidth -> stroke-width
PX_FACTOR = 0.32         # viewBox units -> width/height pixel attributes


def fmt(v):
    return f"{v:.2f}"


def read_rows():
    """All features from both FCs as dicts, sorted by DrawOrder."""
    ds = ogr.Open(GDB)
    rows = []
    for fc, kind in ((POLY_FC, "poly"), (LINE_FC, "line")):
        lyr = ds.GetLayerByName(fc)
        for f in lyr:
            rows.append({
                "kind": kind,
                "order": f.GetField("DrawOrder"),
                "name": f.GetField("Name"),
                "fill": f.GetField("FillColor"),
                "outline": f.GetField("OutlineColor"),
                "width": f.GetField("OutlineWidth"),
                "geom": f.GetGeometryRef().Clone(),
            })
    rows.sort(key=lambda r: r["order"])
    return rows


def north_dot_row(order):
    """The north-dot circle as a synthetic row, drawn on top. Symbology per
    Compass_Rose_North_Circle.lyrx: solid black fill, white outline of width 1.
    The GDB stores the circle as true arcs, so linearize it (2-degree step)."""
    ds = ogr.Open(GDB)
    lyr = ds.GetLayerByName(DOT_FC)
    f = lyr.GetNextFeature()
    return {
        "kind": "poly",
        "order": order,
        "name": "North_Dot",
        "fill": "black",
        "outline": "white",
        "width": 1.0,
        "geom": f.GetGeometryRef().GetLinearGeometry(2.0),
    }


def union_extent(rows):
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for r in rows:
        gxmin, gxmax, gymin, gymax = r["geom"].GetEnvelope()
        xmin, xmax = min(xmin, gxmin), max(xmax, gxmax)
        ymin, ymax = min(ymin, gymin), max(ymax, gymax)
    return xmin, ymin, xmax, ymax


def line_parts(geom):
    """Linestring point lists for LINESTRING or MULTILINESTRING."""
    if geom.GetGeometryName() == "LINESTRING":
        return [geom.GetPoints()]
    return [geom.GetGeometryRef(i).GetPoints() for i in range(geom.GetGeometryCount())]


def polygon_rings(geom):
    """Ring point lists for POLYGON or MULTIPOLYGON."""
    rings = []
    if geom.GetGeometryName() == "POLYGON":
        polys = [geom]
    else:
        polys = [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())]
    for poly in polys:
        for i in range(poly.GetGeometryCount()):
            rings.append(poly.GetGeometryRef(i).GetPoints())
    return rings


def build_svg(rows):
    xmin, ymin, xmax, ymax = union_extent(rows)
    x0 = xmin - MARGIN / SCALE
    y1 = ymax + MARGIN / SCALE
    vb_w = (xmax - xmin) * SCALE + 2.0 * MARGIN
    vb_h = (ymax - ymin) * SCALE + 2.0 * MARGIN
    px_w = round(vb_w * PX_FACTOR)
    px_h = round(vb_h * PX_FACTOR)

    def tx(x):
        return (x - x0) * SCALE

    def ty(y):
        return (y1 - y) * SCALE

    def path_data(point_lists, close):
        subpaths = []
        for pts in point_lists:
            # NOTE: rings keep their duplicate closing vertex (matches shipped file).
            coords = [f"M {fmt(tx(pts[0][0]))} {fmt(ty(pts[0][1]))}"]
            coords += [f"L {fmt(tx(x))} {fmt(ty(y))}" for x, y, *_ in pts[1:]]
            subpaths.append(" ".join(coords) + (" Z" if close else ""))
        return " ".join(subpaths)

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w}" height="{px_h}" '
        f'viewBox="0 0 {fmt(vb_w)} {fmt(vb_h)}">',
        "<title>Jennessent Compass Rose</title>",
    ]
    for r in rows:
        sw = fmt((r["width"] or 0.0) * STROKE_FACTOR)
        if r["kind"] == "poly":
            d = path_data(polygon_rings(r["geom"]), close=True)
            stroke = "none" if (r["width"] or 0.0) <= 0 else PALETTE[r["outline"]]
            out.append(f'<path d="{d}" fill="{PALETTE[r["fill"]]}" fill-rule="evenodd" '
                       f'stroke="{stroke}" stroke-width="{sw}"/>')
        else:
            # Lines keep their stroke color in FillColor; OutlineColor is unused.
            d = path_data(line_parts(r["geom"]), close=False)
            out.append(f'<path d="{d}" fill="none" stroke="{PALETTE[r["fill"]]}" '
                       f'stroke-width="{sw}" stroke-linecap="round"/>')
    out.append("</svg>")
    metrics = {"x0": x0, "y1": y1, "vb_w": vb_w, "vb_h": vb_h}
    return "\n".join(out), metrics


def write_svg(rows, out_path):
    svg, m = build_svg(rows)
    with open(out_path, "w", newline="\n") as f:
        f.write(svg)
    # Relative focal coordinates of the compass center (FC origin 0,0) within the
    # SVG canvas: x from the left, y from the BOTTOM (the add-in's convention).
    fx = (0.0 - m["x0"]) * SCALE / m["vb_w"]
    fy = 1.0 - (m["y1"] - 0.0) * SCALE / m["vb_h"]
    aspect = m["vb_w"] / m["vb_h"]
    print(f"Wrote {out_path} ({len(svg):,} chars)")
    print(f"  viewBox {fmt(m['vb_w'])} x {fmt(m['vb_h'])}   aspect(w/h) = {aspect:.6f}")
    print(f"  focal of FC origin (0,0): fx = {fx:.6f}, fy(from bottom) = {fy:.6f}")


if __name__ == "__main__":
    if len(sys.argv) > 1:                       # explicit output -> classic art only
        write_svg(read_rows(), sys.argv[1])
    else:
        rows = read_rows()
        write_svg(rows, DEFAULT_OUT)
        rows.append(north_dot_row(rows[-1]["order"] + 1))
        write_svg(rows, DEFAULT_OUT.replace("CompassRose.svg", "CompassRoseDot.svg"))
