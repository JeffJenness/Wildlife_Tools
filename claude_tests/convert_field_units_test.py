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
"""Unit Converter catalog (Python side) and the Convert Field Units engine:
known conversions, user units read from a temporary file, and real field
conversions on a geodatabase table, a dBASE table, a table view with a
selection, an integer field and in place. Calls the ENGINE directly (no
ImportToolbox, so no sidecar churn). Run with ArcGIS Pro's python from the
repo root:
  "C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" claude_tests/convert_field_units_test.py
"""
import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import unit_converter as uc  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        fails.append(name)


def rel(a, b, tol):
    return abs(a - b) <= tol * max(abs(a), abs(b)) or abs(a - b) < 1e-300


class Msgs:
    def __init__(self):
        self.lines = []

    def addMessage(self, t):
        self.lines.append(t)

    def addWarningMessage(self, t):
        self.lines.append("W " + t)

    def addErrorMessage(self, t):
        self.lines.append("E " + t)


# ------------------------------------------------------------ the catalog
cat = uc.load_catalog(user=False)
C = cat.convert
check("1,214 units in 23 classes", len(cat.units) == 1214 and len(cat.classes) == 23, (len(cat.units), len(cat.classes)))
check("every unit has a source in the citation list", all(u.sources and all(s in cat.citations for s in u.sources) for u in cat.units))
check("international watt and joule are both 1.000165", cat.find("watts (Int)").factor == 1.000165 and cat.find("joules (Int)").factor == 1.000165)
check("no watts are labeled (IT) any more", not any("watt" in u.name and "(IT)" in u.name for u in cat.units))
check("a knot is exactly 1.852 km/h", C(1, "knots", "kilometers/hr") == 1.852)
check("a horsepower is exactly 550 foot-pounds-force per second", rel(C(1, "horsepower (mech)", "ft-lbs / sec"), 550, 1e-14))
check("ArcGIS names find units: FeetUS, Foot_US, SquareFeetUS, AcresUS",
      cat.find("FeetUS").name == "feet (US Survey)" and cat.find("Foot_US").name == "feet (US Survey)"
      and cat.find("SquareFeetUS").name == "sq feet (Survey)" and cat.find("AcresUS").name == "acres (US survey)")
check("a US survey acre is 43,560 survey sq ft", rel(C(1, "acres (US survey)", "sq feet (Survey)"), 43560, 1e-14))
check("arcpy agrees: AcresUS", rel(cat.find("AcresUS").factor, arcpy.ArealUnitConversionFactor("AcresUS", "SquareMeters"), 1e-12))
check("arcpy agrees: MilesUS, NauticalMilesUS, YardsUS",
      all(rel(cat.find(n).factor, arcpy.LinearUnitConversionFactor(n, "Meters"), 1e-12) for n in ("MilesUS", "NauticalMilesUS", "YardsUS")))
check("Clarke's foot from EPSG 9005", cat.find("Clarke's feet").factor == 0.3047972654)
check("acres: description credited to the original program, value to NIST", cat.find("acres").desc_sources == ["oed_acre", "jenness_uc"] and cat.find("acres").sources == ["nist_hb44"])
check("survey units carry the retirement note", "retired" in cat.find("feet (US Survey)").definition and "retired" in cat.find("acres (US survey)").definition)
C = cat.convert
check("1 inch = 25.4 mm", rel(C(1, "inches", "millimeters"), 25.4, 1e-14))
check("1 acre = 0.40468564224 ha", rel(C(1, "acres", "hectares"), 0.40468564224, 1e-14))
check("212 F = 100 C", rel(C(212, "degrees Fahrenheit", "degrees Celsius"), 100, 1e-14))
check("100% slope = 45 degrees", rel(C(100, "% (Percent) Slope", "circ. degrees"), 45, 1e-14))
check("the old name % (slope) still finds the unit", cat.find("% (slope)").name == "% (Percent) Slope")
check("1 m diameter = pi/4 sq m", rel(C(1, "meters diameter", "sq m (CA)"), math.pi / 4, 1e-14))
check("negative area -> no diameter", math.isnan(C(-1, "sq m (CA)", "meters diameter")))
try:
    C(1, "feet", "liters")
    check("between classes refused", False)
except ValueError:
    check("between classes refused", True)
bad = 0
for u in cat.units:
    b = cat.find(dict(cat.classes)[u.cls])
    for x in (1.0, 37.25):
        back = C(C(x, u, b), b, u)
        if not rel(back, x, 1e-11) and abs(back - x) > 1e-12:
            bad += 1
check("round trip through every unit", bad == 0, bad)

# user units from a file, in the same format the window writes
tmpd = tempfile.mkdtemp(prefix="uc_test_")
upath = os.path.join(tmpd, "my_units.json")
with open(upath, "w", encoding="utf-8") as f:
    json.dump({"schemaVersion": 1,
               "classes": [{"name": "Test Money", "base": "test dollars", "baseSingular": "test dollar"}],
               "units": [{"name": "test furlongs", "singular": "test furlong", "class": "Length",
                          "definedAs": {"unit": "test rods", "factor": 40}},
                         {"name": "test rods", "singular": "test rod", "class": "Length",
                          "definedAs": {"unit": "feet", "factor": 16.5}},
                         {"name": "test cents", "class": "Test Money", "definedAs": {"unit": "test dollars", "factor": 0.01}},
                         {"name": "broken", "class": "Length", "definedAs": {"unit": "no such unit", "factor": 2}}],
               "saved": []}, f)
ucat = uc.load_catalog(user=upath)
check("user units load, in any order", ucat.find("test furlongs") is not None and rel(ucat.convert(1, "test furlongs", "feet"), 660, 1e-14))
check("user class loads", "Test Money" in ucat.class_names() and rel(ucat.convert(250, "test cents", "test dollars"), 2.5, 1e-14))
check("a unit with an unknown reference is skipped with a warning", ucat.find("broken") is None and any("broken" in w for w in ucat.warnings), ucat.warnings)

# ------------------------------------------------------------ the field tool
work = tempfile.mkdtemp(prefix="cfu_test_")
gdb = arcpy.management.CreateFileGDB(work, "t.gdb").getOutput(0)


def make_table(path_dir, name, field_type="DOUBLE", values=(1.0, 2.5, None, 100.0)):
    t = arcpy.management.CreateTable(path_dir, name).getOutput(0)
    arcpy.management.AddField(t, "LenIn", field_type)
    with arcpy.da.InsertCursor(t, ["LenIn"]) as cur:
        for v in values:
            cur.insertRow([v])
    return t


def column(t, fld):
    with arcpy.da.SearchCursor(t, [fld]) as cur:
        return [r[0] for r in cur]


# geodatabase table: a new field, nulls stay null
t1 = make_table(gdb, "inches")
m = Msgs()
n, skipped = uc.run_convert_field(t1, "LenIn", "inches", "millimeters", "LenMM", m)
vals = column(t1, "LenMM")
check("gdb: 3 converted, 1 skipped", (n, skipped) == (3, 1), (n, skipped))
check("gdb: values in mm", rel(vals[0], 25.4, 1e-14) and rel(vals[1], 63.5, 1e-14) and rel(vals[3], 2540, 1e-14), vals)
check("gdb: a null stays null", vals[2] is None, vals)
alias = [f.aliasName for f in arcpy.ListFields(t1) if f.name == "LenMM"][0]
check("gdb: the new field's alias names the conversion", "millimeters" in alias and "inches" in alias, alias)
import xml.etree.ElementTree as ET  # noqa: E402
from arcpy import metadata as md_mod  # noqa: E402


def field_doc(table, field):
    root = ET.fromstring(md_mod.Metadata(arcpy.Describe(table).catalogPath).xml)
    for a in root.iter("attr"):
        lab = a.find("attrlabl")
        if lab is not None and (lab.text or "").lower() == field.lower():
            return a
    return None


a = field_doc(t1, "LenMM")
check("gdb: the field is described in the metadata", a is not None)
if a is not None:
    d = a.findtext("attrdef") or ""
    check("metadata: says what it holds and which tool", "LenIn" in d and "inches" in d and "millimeters" in d and "Convert Field Units" in d, d)
    check("metadata: defines the unit with its factor", "1 millimeter = 0.001 meter" in d, d)
    check("metadata: units of measure", a.findtext("attrdomv/rdom/attrunit") == "millimeters", a.findtext("attrdomv/rdom/attrunit"))
    check("metadata: the range of the converted values", a.findtext("attrdomv/rdom/rdommin") == "25.4" and a.findtext("attrdomv/rdom/rdommax") == "2540",
          (a.findtext("attrdomv/rdom/rdommin"), a.findtext("attrdomv/rdom/rdommax")))
    check("metadata: the source of the definitions", "Unit Converter" in (a.findtext("attrdefs") or ""))

# suggested name when the output field is left empty
n, _ = uc.run_convert_field(t1, "LenIn", "inches", "feet", "", Msgs())
check("an empty output name gets a suggested field", "LenIn_feet" in [f.name for f in arcpy.ListFields(t1)], [f.name for f in arcpy.ListFields(t1)])

# in place
t2 = make_table(gdb, "inplace")
uc.run_convert_field(t2, "LenIn", "feet", "meters", "LenIn", Msgs())
vals = column(t2, "LenIn")
check("in place: feet replaced by meters", rel(vals[0], 0.3048, 1e-14) and rel(vals[3], 30.48, 1e-14), vals)
a = field_doc(t2, "LenIn")
check("in place: the metadata says the field held feet and now holds meters",
      a is not None and "held values in feet" in (a.findtext("attrdef") or "") and a.findtext("attrdomv/rdom/attrunit") == "meters")

# an integer field: rounded, with a warning
t3 = make_table(gdb, "ints", "LONG", (1, 3, 10))
m = Msgs()
uc.run_convert_field(t3, "LenIn", "feet", "inches", "LenIn", m)
check("integer field in place: rounded", column(t3, "LenIn") == [12, 36, 120], column(t3, "LenIn"))
check("integer field: warned", any("integer" in x for x in m.lines), m.lines)

# a dBASE table: no nulls, short names. A dBASE field cannot hold an empty
# value, so the record with no converted value is a negative circle area,
# which has no diameter.
dbf_dir = tempfile.mkdtemp(prefix="cfu_dbf_")
t4 = make_table(dbf_dir, "areas.dbf", "DOUBLE", (math.pi / 4, 2.0, -1.0))
m = Msgs()
n, skipped = uc.run_convert_field(t4, "LenIn", "sq m (CA)", "meters diameter", "", m)
names = [f.name for f in arcpy.ListFields(t4)]
newf = [x for x in names if x.lower().startswith("lenin_")]
check("dbf: suggested name fits 10 characters", newf and len(newf[0]) <= 10, names)
vals = column(t4, newf[0]) if newf else []
check("dbf: a circle of pi/4 sq m is 1 m across", vals and rel(vals[0], 1.0, 1e-12), vals)
check("dbf: 2 converted, 1 skipped", (n, skipped) == (2, 1), (n, skipped))
a = field_doc(t4, newf[0]) if newf else None
check("dbf: the field is described in the metadata too", a is not None and "meters diameter" in (a.findtext("attrdef") or ""))
check("dbf: the record with no converted value gets -999, and the message says so",
      vals and vals[2] == uc.NULL_STANDIN and any("-999" in x for x in m.lines), (vals, m.lines))

# a table view with a selection: only the selected records
t5 = make_table(gdb, "selected", "DOUBLE", (1.0, 2.0, 3.0, 4.0))
view = arcpy.management.MakeTableView(t5, "sel_view").getOutput(0)
arcpy.management.SelectLayerByAttribute(view, "NEW_SELECTION", "OBJECTID IN (2, 4)")
uc.run_convert_field(view, "LenIn", "kilometers", "meters", "LenM", Msgs())
vals = column(t5, "LenM")
check("table view: only the 2 selected records converted", vals[0] is None and vals[2] is None and rel(vals[1], 2000, 1e-14) and rel(vals[3], 4000, 1e-14), vals)

# refusals
for args, words in (((t1, "LenIn", "feet", "liters", "X"), "same measurement class"),
                    ((t1, "LenIn", "nope", "feet", "X"), "Unknown unit"),
                    ((t1, "Nope", "feet", "meters", "X"), "not in the table")):
    try:
        uc.run_convert_field(*args, messages=Msgs())
        check("refused: " + words, False)
    except arcpy.ExecuteError as e:
        check("refused: " + words, words in str(e), str(e))

print("\nCONVERT FIELD UNITS: %d failed" % len(fails))
sys.exit(1 if fails else 0)
