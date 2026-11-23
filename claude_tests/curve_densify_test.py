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

"""True curves (arcs and circles, as a geodatabase buffer makes them) in the
toolbox. Three things are checked:

  1. the one helper every engine uses, wildlife_geom.densify_curves, follows
     a true circle as closely as it says, at any radius;
  2. no engine turns curves into straight pieces any other way (a search of
     the engines' text: a guard for tools yet to be written);
  3. the two line topography tools follow a curved line in latitude and
     longitude along the curve, not along the straight line between its ends.

The circles' true sizes are known from their radii, so nothing here is taken
from the toolbox.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" curve_densify_test.py
"""
import glob
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLBOXES = os.path.join(HERE, "..", "WildlifeTools", "Toolboxes")
sys.path.insert(0, TOOLBOXES)
import arcpy  # noqa: E402
import line_surface_tools as LST  # noqa: E402
import wildlife_geom as WG  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


CX, CY = 500000.0, 3900000.0


def circle(r, wkid=26912):
    return arcpy.AsShape({"curveRings": [[[CX + r, CY], {"a": [[CX + r, CY], [CX, CY], 0, 1]}]],
                          "spatialReference": {"wkid": wkid}}, True)


def measure(g, r):
    """(vertices, farthest a side's middle lies inside the circle, farthest a vertex lies from it, percent of
    the circle's area that is missing)"""
    rings = json.loads(g.JSON)["rings"]
    inside = off = 0.0
    for ring in rings:
        for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:]):
            inside = max(inside, r - math.hypot((x0 + x1) / 2 - CX, (y0 + y1) / 2 - CY))
            off = max(off, abs(r - math.hypot(x0 - CX, y0 - CY)))
    return sum(len(x) for x in rings) - len(rings), inside, off, 100.0 * (math.pi * r * r - abs(g.area)) / (math.pi * r * r)


# ---- 1. the helper
check("the helper allows a twentieth of the step", WG.CURVE_DEVIATION == 0.05, WG.CURVE_DEVIATION)
for r in (100.0, 1000.0, 10000.0, 100000.0):
    c = circle(r)
    check("setup: a true circle of radius %g m" % r, c.hasCurves and abs(c.area - math.pi * r * r) < 1e-6 * r * r)
    for step in (30.0, 5.0):
        g = WG.densify_curves(c, step)
        n, inside, off, short = measure(g, r)
        check("circle of %g m, step %g: no true curves are left" % (r, step), not g.hasCurves and n >= 36, (g.hasCurves, n))
        check("circle of %g m, step %g: every vertex is on the circle" % (r, step), off < 1e-3, off)
        check("circle of %g m, step %g: no side is farther inside the circle than a twentieth of the step (%.2f m)"
              % (r, step, 0.05 * step), inside <= 0.05 * step * 1.001 + 1e-6, inside)
        # a circle cut into chords each within s of it is short in area by at most 4/3 of s / r
        check("circle of %g m, step %g: its area is short by no more than that allows" % (r, step),
              0.0 <= short <= 100.0 * (4.0 / 3.0) * max(0.05 * step, r * (1 - math.cos(math.pi / 36))) / r + 1e-9, short)
old = circle(10000.0).densify("DISTANCE", 30.0, 30.0)
check("for the record: the call the toolbox used before 2026-11-23 left a 10 km circle some 29 m inside itself",
      20.0 < measure(old, 10000.0)[1] < 30.5, measure(old, 10000.0))
# straight sides: cut into pieces no longer than the step, as before
half = arcpy.AsShape({"curveRings": [[[0, 0], [0, 1000], [1000, 1000], [1000, 0], {"a": [[0, 0], [500, 0], 0, 1]}]],
                      "spatialReference": {"wkid": 26912}}, True)
ring = json.loads(WG.densify_curves(half, 30.0).JSON)["rings"][0]
straight = [math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:])
            if (x0 == x1 and x0 in (0, 1000)) or (y0 == y1 and y0 == 1000)]
check("straight sides are cut into pieces no longer than the step", straight and max(straight) <= 30.0 + 1e-6
      and len(straight) >= 3 * 33, (len(straight), max(straight) if straight else None))
check("a shape with no curves comes back with the same outline",
      abs(WG.densify_curves(arcpy.AsShape({"rings": [[[0, 0], [0, 100], [100, 100], [100, 0], [0, 0]]],
                                           "spatialReference": {"wkid": 26912}}, True), 30.0).area - 10000.0) < 1e-6)
check("a step of zero does not stop it", WG.densify_curves(circle(100.0), 0.0).pointCount >= 36)
# in degrees: the step is in the shape's own units
cg = arcpy.AsShape({"curveRings": [[[-112.0 + 0.5, 35.0], {"a": [[-112.0 + 0.5, 35.0], [-112.0, 35.0], 0, 1]}]],
                    "spatialReference": {"wkid": 4326}}, True)
g = WG.densify_curves(cg, 30.0 / 111320.0)
rings = json.loads(g.JSON)["rings"]
worst = max(0.5 - math.hypot((x0 + x1) / 2 + 112.0, (y0 + y1) / 2 - 35.0)
            for rg in rings for (x0, y0), (x1, y1) in zip(rg[:-1], rg[1:]))
check("in degrees: a circle of half a degree, step 30 m in degrees, is followed to within a twentieth of that",
      0.0 <= worst <= 0.05 * 30.0 / 111320.0 * 1.001, worst)

# ---- 2. no engine densifies curves in any other way
direct, geodesic = [], []
for p in sorted(glob.glob(os.path.join(TOOLBOXES, "*.py")) + glob.glob(os.path.join(TOOLBOXES, "*.pyt"))):
    name = os.path.basename(p)
    text = open(p, encoding="utf-8", errors="replace").read()
    for n, line in enumerate(text.split("\n"), 1):
        if re.search(r"\.densify\s*\(", line) and name != "wildlife_geom.py":
            direct.append("%s:%d" % (name, n))
        if re.search(r"densify\s*\(\s*[\"']GEODESIC", line) and name != "wildlife_geom.py":   # (its own warning)
            geodesic.append("%s:%d" % (name, n))
check("no engine calls a geometry's own densify: all go through wildlife_geom.densify_curves", direct == [], direct)
check("no engine densifies with the GEODESIC method, which replaces an arc by the line between its ends",
      geodesic == [], geodesic)
uses = sum(len(re.findall(r"\bdensify_curves\s*\(", open(p, encoding="utf-8", errors="replace").read()))
           for p in glob.glob(os.path.join(TOOLBOXES, "*.py")) if not p.endswith("wildlife_geom.py"))
check("the helper is in use across the toolbox (27 calls when this was written)", uses >= 27, uses)

# ---- 3. the line topography tools, on a curved line in latitude and longitude
arc = arcpy.AsShape({"curvePaths": [[[-112.0, 35.0], {"a": [[-111.98, 35.0], [-111.99, 35.0], 0, 1]}]],
                     "spatialReference": {"wkid": 4326}}, True)                # half a circle of radius 0.01 degree
true_m = arc.getLength("GEODESIC", "METERS")
chord_m = arcpy.AsShape({"paths": [[[-112.0, 35.0], [-111.98, 35.0]]],
                         "spatialReference": {"wkid": 4326}}, True).getLength("GEODESIC", "METERS")
check("setup: the curved line is some 3.18 km long, and its ends 1.83 km apart",
      arc.hasCurves and 3150.0 < true_m < 3220.0 and 1800.0 < chord_m < 1850.0, (true_m, chord_m))
for interval in (30.0, 10.0):
    got = LST._densify_if_curved(arc, interval)
    check("curved line in degrees, sampled every %g m: followed along the curve (its length within 0.1 percent), "
          "not along the line between its ends" % interval,
          not got.hasCurves and abs(got.getLength("GEODESIC", "METERS") - true_m) < 0.001 * true_m,
          (got.getLength("GEODESIC", "METERS"), true_m, chord_m))
got = LST._densify_if_curved(arc)
check("curved line in degrees, no sampling interval: followed along the curve (within 0.1 percent)",
      abs(got.getLength("GEODESIC", "METERS") - true_m) < 0.001 * true_m, got.getLength("GEODESIC", "METERS"))
arc_utm = arcpy.AsShape({"curvePaths": [[[CX, CY], {"a": [[CX + 2000, CY], [CX + 1000, CY], 0, 1]}]],
                         "spatialReference": {"wkid": 26912}}, True)           # half a circle of radius 1 km
got = LST._densify_if_curved(arc_utm, 30.0)
check("curved line in a projection: followed along the curve (%.1f m, within 0.1 percent)" % (math.pi * 1000.0),
      abs(got.length - math.pi * 1000.0) < 0.001 * math.pi * 1000.0, got.length)
plain = arcpy.AsShape({"paths": [[[CX, CY], [CX + 2000, CY]]], "spatialReference": {"wkid": 26912}}, True)
check("a line with no curves is handed back untouched", LST._densify_if_curved(plain, 30.0) is plain)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
