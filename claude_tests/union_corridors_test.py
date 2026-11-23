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

"""Union Corridors (union_corridors.py). The inputs are squares and other
shapes whose areas are known by arithmetic, so every expected area below is
worked out by hand and not taken from the tool.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" union_corridors_test.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import land_facet_linkage as L  # noqa: E402
import union_corridors as U  # noqa: E402

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
work = tempfile.mkdtemp(prefix="union_corridors_test_")
gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
WKID = 26912
SR = arcpy.SpatialReference(WKID)


def box(x, y, w, h):
    """Corners of a rectangle, clockwise (an outer ring)."""
    return [[x, y], [x, y + h], [x + w, y + h], [x + w, y], [x, y]]


def hole(x, y, w, h):
    """Corners of a rectangle, counterclockwise (a hole)."""
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]


def poly(*rings):
    return arcpy.AsShape({"rings": list(rings), "spatialReference": {"wkid": WKID}}, True)


def make(name, rows, where=gdb):
    """A polygon feature class of (species, percentile, shape) rows."""
    fc = arcpy.management.CreateFeatureclass(where, name, "POLYGON", spatial_reference=SR)[0]
    arcpy.management.AddField(fc, "Species", "TEXT", field_length=20)
    arcpy.management.AddField(fc, "Pctile", "LONG")
    with arcpy.da.InsertCursor(fc, ["Species", "Pctile", "SHAPE@"]) as c:
        for r in rows:
            c.insertRow(r)
    return fc


def shapes(fc):
    return [r[0] for r in arcpy.da.SearchCursor(fc, ["SHAPE@"])]


def n_rings(g):
    return len(json.loads(g.JSON)["rings"])


def near(a, b, tol=0.01):
    return abs(a - b) <= tol


A = make("a", [("bear", 5, poly(box(0, 0, 1000, 1000)))])                   # 1,000,000
B = make("b", [("lion", 5, poly(box(500, 0, 1000, 1000)))])                 # overlaps A by 500,000
C = make("c", [("deer", 5, poly(box(5000, 0, 1000, 1000)))])                # apart from both
D = make("d", [("elk", 5, poly(box(0, 3000, 1000, 1000), hole(400, 3400, 200, 200)))])   # 960,000, one hole

# ---- 1. two corridors that overlap
m = Msgs()
out = os.path.join(gdb, "u_ab")
ret = U.run_union_corridors([A, B], out, m)
g = shapes(out)
check("overlap: the output path is returned", ret == out, ret)
check("overlap: one feature", len(g) == 1, len(g))
check("overlap: the overlap is counted once (1,500,000 sq m)", len(g) == 1 and near(g[0].area, 1500000.0),
      [x.area for x in g])
check("overlap: one part, no holes", len(g) == 1 and g[0].partCount == 1 and n_rings(g[0]) == 1,
      [(x.partCount, n_rings(x)) for x in g])
names = [f.name.lower() for f in arcpy.ListFields(out)]
check("overlap: the inputs' attributes are not carried over", "species" not in names and "pctile" not in names
      and not any(n.startswith("fid_") for n in names), names)
check("overlap: the output keeps the inputs' coordinate system",
      arcpy.Describe(out).spatialReference.factoryCode == WKID, arcpy.Describe(out).spatialReference.name)
check("overlap: the run says what it wrote", m.lines and m.lines[-1] == "Union of 2 inputs written.", m.lines[-1:])
check("overlap: nothing warned about", m.warnings == [], m.warnings)
arcpy.env.workspace = gdb
left = [n for n in arcpy.ListFeatureClasses() if "scratch" in n.lower()]
check("overlap: no working dataset is left behind", left == [], left)

# ---- 2. corridors that do not touch, and three at once
out = os.path.join(gdb, "u_ac")
U.run_union_corridors([A, C], out, Msgs())
g = shapes(out)
check("apart: still one feature, of two parts, 2,000,000 sq m",
      len(g) == 1 and g[0].partCount == 2 and near(g[0].area, 2000000.0), [(x.partCount, x.area) for x in g])
out = os.path.join(gdb, "u_abc")
U.run_union_corridors([A, B, C], out, Msgs())
g = shapes(out)
check("three inputs: one feature, two parts, 2,500,000 sq m",
      len(g) == 1 and g[0].partCount == 2 and near(g[0].area, 2500000.0), [(x.partCount, x.area) for x in g])

# ---- 3. holes and gaps are not filled in
out = os.path.join(gdb, "u_dc")
U.run_union_corridors([D, C], out, Msgs())
g = shapes(out)
check("holes: a hole in one corridor stays a hole (960,000 + 1,000,000 sq m)",
      len(g) == 1 and near(g[0].area, 1960000.0) and n_rings(g[0]) == 3, [(x.area, n_rings(x)) for x in g])
# a C shape and a bar that closes it: the ground they enclose belongs to neither corridor
CSHAPE = make("cshape", [("bear", 5, poly([[0, 0], [0, 300], [300, 300], [300, 200], [100, 200], [100, 100],
                                           [300, 100], [300, 0], [0, 0]]))])         # 70,000
BAR = make("bar", [("lion", 5, poly(box(300, 0, 100, 300)))])                        # 30,000
out = os.path.join(gdb, "u_gap")
U.run_union_corridors([CSHAPE, BAR], out, Msgs())
g = shapes(out)
check("gaps: ground enclosed by two corridors, in neither, is not added (100,000 sq m, not 120,000)",
      len(g) == 1 and near(g[0].area, 100000.0) and n_rings(g[0]) == 2, [(x.area, n_rings(x)) for x in g])

# ---- 4. a selection on an input is honored; so is a layer's definition query
BANDS = make("bands", [("bear", 1, poly(box(0, 6000, 1000, 1000))),
                       ("bear", 5, poly(box(2000, 6000, 1000, 1000))),
                       ("bear", 10, poly(box(4000, 6000, 1000, 1000)))])
lyr = arcpy.management.MakeFeatureLayer(BANDS, "uc_bands_lyr")[0]
arcpy.management.SelectLayerByAttribute(lyr, "NEW_SELECTION", "Pctile = 5")
m = Msgs()
out = os.path.join(gdb, "u_sel")
U.run_union_corridors([lyr, C], out, m)
g = shapes(out)
check("selection: only the selected band is used (2,000,000 sq m)", len(g) == 1 and near(g[0].area, 2000000.0),
      [x.area for x in g])
check("selection: the selected band is the 5 percent one (it starts at x = 2000)",
      len(g) == 1 and near(g[0].extent.XMin, 2000.0) and near(g[0].extent.XMax, 6000.0),
      [(x.extent.XMin, x.extent.XMax) for x in g])
check("selection: the run says that only the selection was used",
      any("bands: 1 of 3 features selected" in s for s in m.lines), m.lines)
rec = L.read(out, L.UNION) or {}
src = rec.get("sources") or [{}, {}]
check("selection: the record keeps the counts of each input",
      len(src) == 2 and (src[0].get("n_total"), src[0].get("n_selected"), src[0].get("selected")) == (3, 1, True)
      and (src[1].get("n_total"), src[1].get("n_selected"), src[1].get("selected")) == (1, 1, False), src)
check("selection: the layer's selection is still there afterward",
      int(arcpy.management.GetCount(lyr)[0]) == 1, arcpy.management.GetCount(lyr)[0])
arcpy.management.Delete(lyr)

q = arcpy.management.MakeFeatureLayer(BANDS, "uc_bands_query", "Pctile <= 5")[0]
out = os.path.join(gdb, "u_query")
U.run_union_corridors([q, C], out, Msgs())
g = shapes(out)
check("definition query: only the features the layer shows are used (3,000,000 sq m)",
      len(g) == 1 and near(g[0].area, 3000000.0), [x.area for x in g])
src = (L.read(out, L.UNION) or {}).get("sources") or [{}]
check("definition query: the record counts the features the layer shows (2), not all the dataset's (3)",
      (src[0].get("n_total"), src[0].get("n_selected"), src[0].get("selected")) == (2, 2, False), src[0])
arcpy.management.SelectLayerByAttribute(q, "NEW_SELECTION", "Pctile = 1")
out = os.path.join(gdb, "u_query_sel")
mq = Msgs()
U.run_union_corridors([q, C], out, mq)
g = shapes(out)
check("definition query with a selection: the run says 1 of the 2 features the layer shows",
      any("bands: 1 of 2 features selected" in s for s in mq.lines), mq.lines)
check("definition query with a selection: only the selected feature is used (2,000,000 sq m)",
      len(g) == 1 and near(g[0].area, 2000000.0) and near(g[0].extent.XMin, 0.0), [x.area for x in g])
arcpy.management.Delete(q)

# ---- 5. inputs that cannot be used
PTS = arcpy.management.CreateFeatureclass(gdb, "pts", "POINT", spatial_reference=SR)[0]
with arcpy.da.InsertCursor(PTS, ["SHAPE@XY"]) as c:
    c.insertRow(((10, 10),))
m = Msgs()
out = os.path.join(gdb, "u_pts")
U.run_union_corridors([A, PTS, B], out, m)
g = shapes(out)
check("not polygons: a point input is passed over with a warning, and the rest are joined",
      len(g) == 1 and near(g[0].area, 1500000.0)
      and any(w.startswith("pts: not a polygon feature class; skipped.") for w in m.warnings), (m.warnings,))
check("not polygons: the record lists only the inputs that were used",
      [os.path.basename(s["path"]) for s in (L.read(out, L.UNION) or {}).get("sources", [])] == ["a", "b"],
      L.read(out, L.UNION))
raises("not polygons: nothing but points is an error", lambda: U.run_union_corridors([PTS], os.path.join(gdb, "u_x"), Msgs()),
       "No usable polygon inputs")
check("not polygons: no output is made in that case", not arcpy.Exists(os.path.join(gdb, "u_x")))
raises("no inputs: an empty list is an error", lambda: U.run_union_corridors([], os.path.join(gdb, "u_x"), Msgs()),
       "No input feature classes were supplied")
m = Msgs()
out = os.path.join(gdb, "u_one")
U.run_union_corridors([D], out, m)
g = shapes(out)
check("one input: it is written as it is, with a warning (960,000 sq m, its hole kept)",
      len(g) == 1 and near(g[0].area, 960000.0) and n_rings(g[0]) == 2
      and any(w.startswith("Only one usable input was supplied") for w in m.warnings), (m.warnings,))

# ---- 6. the linkage record, and the warning about unrelated analyses
check("label: no record, no label", U._source_label(None) is None and U._source_label({}) is None)
check("label: the category's label comes first",
      U._source_label({"category": {"label": "Black bear"}, "tool": "Least-Cost Corridor", "record": "corridor"})
      == "Black bear")
check("label: then the tool, then the kind of record",
      U._source_label({"category": {}, "tool": "Least-Cost Corridor", "record": "corridor"}) == "Least-Cost Corridor"
      and U._source_label({"record": "corridor"}) == "corridor")

ID1, ID2 = "11111111-aaaa-4aaa-8aaa-111111111111", "22222222-bbbb-4bbb-8bbb-222222222222"
S1 = make("s1", [("bear", 5, poly(box(0, 9000, 1000, 1000)))])
S2 = make("s2", [("bear", 10, poly(box(500, 9000, 1000, 1000)))])
S3 = make("s3", [("lion", 5, poly(box(1000, 9000, 1000, 1000)))])
L.write(S1, L.corridor_record(ID1, "termini", "cost1", {"category_id": 1, "label": "Black bear"}, "A", "B", [5]))
L.write(S2, L.corridor_record(ID1, "termini", "cost1", {"category_id": 1, "label": "Black bear"}, "A", "B", [10]))
L.write(S3, L.corridor_record(ID2, "termini", "cost2", {"category_id": 2, "label": "Mountain lion"}, "A", "B", [5]))

m = Msgs()
out = os.path.join(gdb, "u_same")
U.run_union_corridors([S1, S2], out, m)
rec = L.read(out, L.UNION) or {}
check("same analysis: no warning", m.warnings == [], m.warnings)
check("same analysis: the record says so",
      rec.get("mixed_analysis") is False and rec.get("source_analysis_ids") == [ID1]
      and rec.get("tool") == "Union Corridors", rec)
check("same analysis: the union has an id of its own", rec.get("analysis_id") not in (None, "", ID1, ID2), rec)
check("same analysis: each source keeps its kind, id and label",
      [(s.get("record_kind"), s.get("analysis_id"), s.get("label")) for s in rec.get("sources", [])]
      == [("corridor", ID1, "Black bear"), ("corridor", ID1, "Black bear")], rec.get("sources"))

m = Msgs()
out = os.path.join(gdb, "u_mixed")
U.run_union_corridors([S3, S1], out, m)
rec = L.read(out, L.UNION) or {}
mixed = [w for w in m.warnings if "DIFFERENT Land Facet analysis ids" in w]
check("different analyses: one warning that names each analysis and its inputs",
      len(mixed) == 1 and (ID1 + ": Black bear") in mixed[0] and (ID2 + ": Mountain lion") in mixed[0], m.warnings)
check("different analyses: the record says so, with the ids in order",
      rec.get("mixed_analysis") is True and rec.get("source_analysis_ids") == sorted([ID1, ID2]), rec)
desc = ""
try:
    desc = arcpy.metadata.Metadata(out).description or ""
except Exception as e:                                                # noqa: BLE001
    desc = repr(e)
check("different analyses: the output's own description warns of it too",
      "sources carried different Land Facet analysis ids" in desc, desc[-200:])

m = Msgs()
out = os.path.join(gdb, "u_part")
U.run_union_corridors([S1, A], out, m)
rec = L.read(out, L.UNION) or {}
check("an input with no record: said, and not treated as a different analysis",
      any(s.startswith("1 of 2 inputs carry no Land Facet linkage record") for s in m.lines) and m.warnings == []
      and rec.get("mixed_analysis") is False, (m.lines, m.warnings))
check("an input with no record: listed in the record with nothing known about it",
      [(s.get("record_kind"), s.get("analysis_id"), s.get("label")) for s in rec.get("sources", [])][1:]
      == [(None, None, None)], rec.get("sources"))
summ = ""
try:
    summ = arcpy.metadata.Metadata(out).summary or ""
except Exception as e:                                                # noqa: BLE001
    summ = repr(e)
check("metadata: the summary names the tool and the inputs", summ == "Created by Union Corridors from 2 inputs: s1, a.",
      summ)
m = Msgs()
U.run_union_corridors([A, B], os.path.join(gdb, "u_none"), m)
check("no input has a record: nothing is said about records",
      not any("linkage record" in s for s in m.lines + m.warnings), m.lines)

# ---- 7. a shapefile as the output, and running without a message window
folder = os.path.join(work, "shp")
os.makedirs(folder)
out = os.path.join(folder, "union_out.shp")
U.run_union_corridors([A, B], out, Msgs())
g = shapes(out) if arcpy.Exists(out) else []
check("shapefile output: written, 1,500,000 sq m", len(g) == 1 and near(g[0].area, 1500000.0), [x.area for x in g])
check("shapefile output: no working shapefile is left in the folder",
      sorted(f for f in os.listdir(folder) if "scratch" in f.lower()) == [], os.listdir(folder))
out = os.path.join(gdb, "u_quiet")
try:
    U.run_union_corridors([A, B], out)
    check("no message window: the run still works", near(shapes(out)[0].area, 1500000.0))
except Exception as e:                                                # noqa: BLE001
    check("no message window: the run still works", False, repr(e))

# ---- 8. the inputs are left as they were
check("inputs: unchanged by all of the above",
      [near(shapes(fc)[0].area, a) for fc, a in ((A, 1e6), (B, 1e6), (C, 1e6), (D, 960000.0))] == [True] * 4
      and int(arcpy.management.GetCount(BANDS)[0]) == 3
      and "Species" in [f.name for f in arcpy.ListFields(A)])

print("\n%d passed, %d failed" % (passed, failed))
arcpy.env.workspace = None
arcpy.management.ClearWorkspaceCache()
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
