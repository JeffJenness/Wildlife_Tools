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

"""Fill Corridor Holes (fill_corridor_holes.py). The polygons are rectangles
with rectangular holes, so every area below is known by arithmetic. The
holes are far smaller or far larger than each threshold, except where a
test is about the threshold itself.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" fill_corridor_holes_test.py
"""
import json
import math
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import fill_corridor_holes as F  # noqa: E402
import land_facet_linkage as L  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


def raises(name, fn, has):
    try:
        fn()
        check(name, False, "no error was raised")
    except arcpy.ExecuteError as e:
        check(name, has in str(e), str(e))
    except Exception as e:                                            # noqa: BLE001
        check(name, False, "wrong kind of error: %r" % e)


class Msgs:
    def __init__(self):
        self.lines, self.warnings = [], []

    def addMessage(self, s):
        self.lines.append(s)

    def addWarningMessage(self, s):
        self.warnings.append(s)


arcpy.env.overwriteOutput = True
work = tempfile.mkdtemp(prefix="fill_holes_test_")
gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
out_gdb = arcpy.management.CreateFileGDB(work, "out.gdb")[0]
UTM = 26912


def box(x, y, w, h):
    return [[x, y], [x, y + h], [x + w, y + h], [x + w, y], [x, y]]            # clockwise: an outer ring


def hole(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]            # counterclockwise: a hole


def poly(*rings, wkid=UTM):
    return arcpy.AsShape({"rings": list(rings), "spatialReference": {"wkid": wkid}}, True)


def n_rings(g):
    return len(json.loads(g.JSON)["rings"])


def near(a, b, tol=0.01):
    return abs(a - b) <= tol


# ---- 1. the units of the threshold
FOOT = 0.3048
for unit, m2 in (("Square Meters", 1.0), ("Hectares", 100.0 * 100.0), ("Square Kilometers", 1000.0 * 1000.0),
                 ("Square Feet", FOOT * FOOT), ("Acres", 43560.0 * FOOT * FOOT),
                 ("Square Miles", (5280.0 * FOOT) ** 2)):
    got = F.threshold_to_m2(2.5, unit)
    check("units: 2.5 %s in square meters" % unit, abs(got - 2.5 * m2) <= 1e-9 * 2.5 * m2, (got, 2.5 * m2))
check("units: the list offered is the list understood", sorted(F.UNIT_CHOICES) == sorted(F._UNIT_FACTORS_M2)
      and F.DEFAULT_UNITS == "Square Meters", F.UNIT_CHOICES)
raises("units: a unit that is not offered is an error", lambda: F.threshold_to_m2(1, "Square Yards"),
       "Unrecognized size unit 'Square Yards'")

# ---- 2. one polygon at a time
# 2,000 by 1,000 m, with a small hole (100 by 100 = 10,000 sq m) and a large one (400 by 400 = 160,000 sq m)
TWO = poly(box(500000, 3900000, 2000, 1000), hole(500100, 3900100, 100, 100), hole(501000, 3900300, 400, 400))
g, removed, total, geo = F._fill_small_holes(TWO, 50000.0)
check("one polygon: the small hole is filled and the large one kept", (removed, total) == (1, 2) and n_rings(g) == 2,
      (removed, total, n_rings(g)))
check("one polygon: its area grows by exactly the small hole (to 1,840,000 sq m)", near(g.area, 1840000.0), g.area)
check("one polygon: the large hole is where it was",
      sorted({(x, y) for x, y in json.loads(g.JSON)["rings"][1]})
      == [(501000, 3900300), (501000, 3900700), (501400, 3900300), (501400, 3900700)], json.loads(g.JSON)["rings"])
check("one polygon: the area was measured on the ellipsoid", geo is True)
check("one polygon: the coordinate system is kept", g.spatialReference.factoryCode == UTM)
check("one polygon: the original shape is not altered", near(TWO.area, 1830000.0) and n_rings(TWO) == 3)

g, removed, total, geo = F._fill_small_holes(TWO, 5000.0)
check("nothing small enough: nothing is removed, and the same shape comes back", removed == 0 and total == 2 and g is TWO,
      (removed, total))
g, removed, total, geo = F._fill_small_holes(TWO, 1e9)
check("everything small enough: both holes are filled (2,000,000 sq m)",
      (removed, total) == (2, 2) and n_rings(g) == 1 and near(g.area, 2000000.0), (removed, total, g.area))
NONE = poly(box(500000, 3900000, 2000, 1000))
g, removed, total, geo = F._fill_small_holes(NONE, 1e9)
check("no holes: nothing to do", (removed, total) == (0, 0) and g is NONE, (removed, total))

# the threshold itself: a hole is filled when it is SMALLER than the threshold, not when it equals it
SMALL = poly(hole(500100, 3900100, 100, 100))
exact = SMALL.getArea("GEODESIC", "SQUAREMETERS")
check("threshold: the hole's true area is close to its 10,000 sq m on the map", abs(exact - 10000.0) < 20.0, exact)
ONE = poly(box(500000, 3900000, 2000, 1000), hole(500100, 3900100, 100, 100))
check("threshold: a hole a little smaller than the threshold is filled",
      F._fill_small_holes(ONE, exact * 1.0001)[1] == 1)
check("threshold: a hole a little larger than the threshold is kept", F._fill_small_holes(ONE, exact * 0.9999)[1] == 0)
check("threshold: a hole of exactly the threshold is kept", F._fill_small_holes(ONE, exact)[1] == 0)

# two parts, each with a hole; and an island inside a hole
PARTS = poly(box(500000, 3900000, 1000, 1000), hole(500100, 3900100, 100, 100),
             box(503000, 3900000, 1000, 1000), hole(503100, 3900100, 500, 500))
g, removed, total, geo = F._fill_small_holes(PARTS, 50000.0)
check("two parts: only the small hole goes; both parts stay",
      (removed, total) == (1, 2) and g.partCount == 2 and n_rings(g) == 3 and near(g.area, 1750000.0),
      (removed, total, g.partCount, n_rings(g), g.area))
ISLAND = poly(box(500000, 3900000, 1000, 1000), hole(500100, 3900100, 800, 800), box(500400, 3900400, 200, 200))
g, removed, total, geo = F._fill_small_holes(ISLAND, 50000.0)
check("an island in a large hole: the hole and the island are kept (400,000 sq m)",
      removed == 0 and total == 1 and near(g.area, 1000000.0 - 640000.0 + 40000.0), (removed, total, g.area))
g, removed, total, geo = F._fill_small_holes(ISLAND, 1e9)
check("an island in a hole that is filled: no ground is counted twice (1,000,000 sq m at most)",
      removed == 1 and g.area <= 1000000.0 + 0.01, (removed, g.area, g.partCount))

# heights are kept on a polygon that is rebuilt
ZP = arcpy.Polygon(arcpy.Array([arcpy.Array(
    [arcpy.Point(500000, 3900000, 10), arcpy.Point(500000, 3901000, 20), arcpy.Point(502000, 3901000, 30),
     arcpy.Point(502000, 3900000, 40), arcpy.Point(500000, 3900000, 10), None,
     arcpy.Point(500100, 3900100, 5), arcpy.Point(500200, 3900100, 5), arcpy.Point(500200, 3900200, 5),
     arcpy.Point(500100, 3900200, 5), arcpy.Point(500100, 3900100, 5)])]), arcpy.SpatialReference(UTM), True)
check("setup: the test polygon has heights and a hole", n_rings(ZP) == 2 and ZP.getPart(0)[1].Z == 20.0,
      (n_rings(ZP), ZP.getPart(0)[1].Z))
g, removed, total, geo = F._fill_small_holes(ZP, 50000.0)
zs = sorted(pt.Z for pt in g.getPart(0) if pt is not None)
check("heights: kept when a hole is filled", removed == 1 and zs == [10.0, 10.0, 20.0, 30.0, 40.0], zs)

# a hole drawn as a true circle (as a geodatabase buffer is): radius 100 m, 31,416 sq m
CIRC = arcpy.AsShape({"curveRings": [box(500000, 3900000, 1000, 1000),
                                     [[500600, 3900500], {"a": [[500600, 3900500], [500500, 3900500], 0, 0]}]],
                      "spatialReference": {"wkid": UTM}}, True)
circle = math.pi * 100.0 ** 2
check("setup: the test polygon has a true curve", CIRC.hasCurves and abs(CIRC.area - (1e6 - circle)) < 1.0, CIRC.area)
# (The circle is turned into straight sides that stay within a twentieth of the step of it: see
# curve_densify_test.py. Here the step is 2 m, and the hole measures within 0.3 percent of the circle.)
g, removed, total, geo = F._fill_small_holes(CIRC, 20000.0)
check("circle: a circular hole larger than the threshold is kept, with its area nearly unchanged",
      (removed, total) == (0, 1) and abs(g.area - (1e6 - circle)) < 0.003 * circle, (removed, total, g.area))
check("circle: the hole is measured as a circle, not as the two ends of its arc (a threshold just under it keeps it)",
      F._fill_small_holes(CIRC, 31300.0)[1] == 0 and F._fill_small_holes(CIRC, 31500.0)[1] == 1,
      (F._fill_small_holes(CIRC, 31000.0)[1], F._fill_small_holes(CIRC, 31500.0)[1]))
g, removed, total, geo = F._fill_small_holes(CIRC, 40000.0)
check("circle: a circular hole smaller than the threshold is filled",
      (removed, total) == (1, 1) and near(g.area, 1000000.0), (removed, total, g.area))

# latitude and longitude: a hole 0.01 degrees on a side at 35 N is about 1,109 m by 913 m, about 1.013 sq km
GEO = poly(box(-112.0, 35.0, 0.1, 0.1), hole(-111.96, 35.04, 0.01, 0.01), wkid=4326)
g, removed, total, geo = F._fill_small_holes(GEO, 900000.0)
check("degrees: a hole of about 1.01 sq km is kept at a threshold of 0.9 sq km", (removed, total, geo) == (0, 1, True),
      (removed, total, geo))
g, removed, total, geo = F._fill_small_holes(GEO, 1100000.0)
check("degrees: and filled at a threshold of 1.1 sq km", (removed, total, geo) == (1, 1, True) and n_rings(g) == 1,
      (removed, total, geo))

# ---- 3. whole runs
def make(name, rows, where=gdb, wkid=UTM):
    fc = arcpy.management.CreateFeatureclass(where, name, "POLYGON", spatial_reference=arcpy.SpatialReference(wkid))[0]
    arcpy.management.AddField(fc, "Species", "TEXT", field_length=20)
    arcpy.management.AddField(fc, "Pctile", "DOUBLE")
    with arcpy.da.InsertCursor(fc, ["Species", "Pctile", "SHAPE@"]) as c:
        for r in rows:
            c.insertRow(r)
    return fc


def rows(fc):
    return sorted((r[0], r[1], r[2]) for r in arcpy.da.SearchCursor(fc, ["Species", "Pctile", "SHAPE@"]))


CORR = make("corridor", [("bear", 1.5, TWO),                                             # 1 of 2 holes to fill
                         ("lion", 5.0, poly(box(505000, 3900000, 1000, 1000))),          # no holes
                         ("deer", 10.0, poly(box(507000, 3900000, 1000, 1000), hole(507100, 3900100, 50, 50)))])
ID = "33333333-cccc-4ccc-8ccc-333333333333"
L.write(CORR, L.corridor_summary_record(ID, "termini", [{"label": "Black bear"}], [1.5, 5, 10]))
before = [(s, p, g.area) for s, p, g in rows(CORR)]

m = Msgs()
outs = F.run_fill_corridor_holes([CORR], 5, "Hectares", out_gdb, messages=m)
want = os.path.join(out_gdb, "corridor_holes_removed")
check("run: one output for the input, named for it", outs == [want] and arcpy.Exists(want), outs)
got = rows(want) if arcpy.Exists(want) else []
check("run: every feature and every attribute is still there",
      [(s, p) for s, p, _g in got] == [("bear", 1.5), ("deer", 10.0), ("lion", 5.0)], [(s, p) for s, p, _g in got])
areas = {s: g.area for s, _p, g in got}
check("run: holes under 5 hectares are filled, the 16 hectare hole is kept",
      near(areas.get("bear", 0), 1840000.0) and near(areas.get("deer", 0), 1000000.0)
      and near(areas.get("lion", 0), 1000000.0), areas)
check("run: the count of holes and features is reported",
      "corridor: 2 of 3 holes removed, across 2 of 3 features." in m.lines, m.lines)
check("run: nothing warned about", m.warnings == [], m.warnings)
check("run: the input is not changed", [(s, p, g.area) for s, p, g in rows(CORR)] == before)
rec = L.read(want) or {}
steps = rec.get("post_processing") or []
check("record: the output is still the same kind of product, of the same analysis",
      rec.get("record") == L.CORRIDOR and rec.get("analysis_id") == ID and rec.get("percentiles") == [1.5, 5, 10], rec)
check("record: with a note of what was done to it",
      len(steps) == 1 and steps[0].get("tool") == "Fill Corridor Holes"
      and steps[0].get("detail") == "holes smaller than 5 Hectares filled (2 of 3 holes removed, across 2 of 3 features)",
      steps)
check("record: the input's own record has no such note", "post_processing" not in (L.read(CORR) or {}), L.read(CORR))
summ = ""
try:
    summ = arcpy.metadata.Metadata(want).summary or ""
except Exception as e:                                                # noqa: BLE001
    summ = repr(e)
check("metadata: the summary names the tool, the source and what was done",
      summ.startswith("Created by Fill Corridor Holes from ") and "corridor: holes smaller than 5 Hectares filled" in summ,
      summ)

# a second pass over the output adds a second note and finds nothing more to fill
m = Msgs()
outs2 = F.run_fill_corridor_holes([want], 5, "Hectares", out_gdb, "_again", m)
rec2 = L.read(outs2[0]) or {}
check("second pass: nothing left to fill; the kept hole is counted",
      "corridor_holes_removed: 0 of 1 hole removed, across 0 of 3 features." in m.lines, m.lines)
check("second pass: named with the suffix that was asked for",
      os.path.basename(outs2[0]) == "corridor_holes_removed_again", outs2)
check("second pass: the record now has two notes",
      len(rec2.get("post_processing") or []) == 2 and rec2.get("analysis_id") == ID, rec2.get("post_processing"))

# an input with no record gets none; a threshold in other units
PLAIN = make("plain", [("elk", 1.0, TWO)])
m = Msgs()
outs = F.run_fill_corridor_holes([PLAIN], 0.2, "Square Kilometers", out_gdb, messages=m)
check("no record on the input: none on the output", L.read(outs[0]) is None, L.read(outs[0]))
check("0.2 sq km threshold: both holes (0.01 and 0.16 sq km) are filled",
      near(rows(outs[0])[0][2].area, 2000000.0) and "plain: 2 of 2 holes removed, across 1 of 1 feature." in m.lines,
      m.lines)
outs = F.run_fill_corridor_holes([PLAIN], 20, "Acres", out_gdb, "_acres", Msgs())      # 20 acres = 80,937 sq m
check("20 acre threshold: the 10,000 sq m hole is filled, the 160,000 sq m hole kept",
      near(rows(outs[0])[0][2].area, 1840000.0), rows(outs[0])[0][2].area)

# several inputs at once; one of them is not polygons
PTS = arcpy.management.CreateFeatureclass(gdb, "pts", "POINT", spatial_reference=arcpy.SpatialReference(UTM))[0]
m = Msgs()
outs = F.run_fill_corridor_holes([PLAIN, PTS, CORR], 5, "Hectares", out_gdb, "_multi", m)
check("several inputs: one output each, in the order given; the points are passed over with a warning",
      [os.path.basename(p) for p in outs] == ["plain_multi", "corridor_multi"]
      and any(w.startswith("pts: not a polygon feature class; skipped.") for w in m.warnings), (outs, m.warnings))
raises("only points: an error", lambda: F.run_fill_corridor_holes([PTS], 5, "Hectares", out_gdb, "_x", Msgs()),
       "No output was written")
raises("no inputs: an error", lambda: F.run_fill_corridor_holes([], 5, "Hectares", out_gdb), "No input feature classes")
raises("a threshold of zero: an error", lambda: F.run_fill_corridor_holes([PLAIN], 0, "Hectares", out_gdb, "_x"),
       "must be greater than zero")
raises("a negative threshold: an error", lambda: F.run_fill_corridor_holes([PLAIN], -1, "Hectares", out_gdb, "_x"),
       "must be greater than zero")
raises("no threshold: an error", lambda: F.run_fill_corridor_holes([PLAIN], None, "Hectares", out_gdb, "_x"),
       "must be greater than zero")
raises("a unit that is not offered: an error",
       lambda: F.run_fill_corridor_holes([PLAIN], 5, "Square Yards", out_gdb, "_x"), "Unrecognized size unit")
check("errors: none of those made an output", not arcpy.Exists(os.path.join(out_gdb, "plain_x")))

# a selection on the input: only the selected features are written
lyr = arcpy.management.MakeFeatureLayer(CORR, "fch_corr_lyr")[0]
arcpy.management.SelectLayerByAttribute(lyr, "NEW_SELECTION", "Species IN ('bear', 'lion')")
m = Msgs()
outs = F.run_fill_corridor_holes([lyr], 5, "Hectares", out_gdb, "_sel", m)
got = rows(outs[0])
check("selection: only the selected features are in the output", [s for s, _p, _g in got] == ["bear", "lion"],
      [s for s, _p, _g in got])
check("selection: the run says so, and counts only them",
      "corridor: 2 of 3 features selected -- processing only the selection." in m.lines
      and "corridor: 1 of 2 holes removed, across 1 of 2 features." in m.lines, m.lines)
steps = (L.read(outs[0]) or {}).get("post_processing") or [{}]
check("selection: the record's note says it was a selection",
      str(steps[-1].get("detail", "")).endswith("; run on a selection of 2 of 3 features"), steps[-1])
arcpy.management.Delete(lyr)

# a layer with a definition query: only the features the layer shows
q = arcpy.management.MakeFeatureLayer(CORR, "fch_corr_query", "Species <> 'bear'")[0]
m = Msgs()
outs = F.run_fill_corridor_holes([q], 5, "Hectares", out_gdb, "_query", m)
got = rows(outs[0])
check("definition query: only the features the layer shows are written", [s for s, _p, _g in got] == ["deer", "lion"],
      [s for s, _p, _g in got])
check("definition query: and only their holes are counted",
      "corridor: 1 of 1 hole removed, across 1 of 2 features." in m.lines, m.lines)
arcpy.management.Delete(q)

# a folder as the output place gives shapefiles; latitude and longitude input
folder = os.path.join(work, "shp")
os.makedirs(folder)
outs = F.run_fill_corridor_holes([PLAIN], 5, "Hectares", folder, messages=Msgs())
check("folder output: a shapefile, named for the input", outs == [os.path.join(folder, "plain_holes_removed.shp")]
      and os.path.isfile(outs[0]), outs)
check("folder output: holes filled there too (1,840,000 sq m)",
      near([r[0] for r in arcpy.da.SearchCursor(outs[0], ["SHAPE@"])][0].area, 1840000.0))
GEOFC = make("degrees", [("bear", 1.0, GEO)], wkid=4326)
m = Msgs()
outs = F.run_fill_corridor_holes([GEOFC], 1.1, "Square Kilometers", out_gdb, messages=m)
check("degrees: a 1.01 sq km hole is filled at 1.1 sq km, measured truly and without a warning",
      "degrees: 1 of 1 hole removed, across 1 of 1 feature." in m.lines and m.warnings == [], (m.lines, m.warnings))
outs = F.run_fill_corridor_holes([GEOFC], 0.9, "Square Kilometers", out_gdb, "_kept", m)
check("degrees: and kept at 0.9 sq km", "degrees: 0 of 1 hole removed, across 0 of 1 feature." in m.lines, m.lines)

# names
check("names: characters a dataset name cannot hold become underscores", F._mh_legalize("my corridor-5%") == "my_corridor_5",
      F._mh_legalize("my corridor-5%"))
check("names: a name that starts with a digit gets a letter in front", F._mh_legalize("2020_corridor") == "P2020_corridor",
      F._mh_legalize("2020_corridor"))
check("names: a name of nothing usable still gives a name", F._mh_legalize("---") == "poly", F._mh_legalize("---"))
check("names: a geodatabase takes the bare name, a folder gets .shp",
      F._resolve_fc_output(out_gdb, "x") == os.path.join(out_gdb, "x")
      and F._resolve_fc_output(out_gdb + "\\", "x") == os.path.join(out_gdb + "\\", "x")
      and F._resolve_fc_output(folder, "x") == os.path.join(folder, "x.shp")
      and F._resolve_fc_output(folder, "x.shp") == os.path.join(folder, "x.shp"))

print("\n%d passed, %d failed" % (passed, failed))
arcpy.management.ClearWorkspaceCache()
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
