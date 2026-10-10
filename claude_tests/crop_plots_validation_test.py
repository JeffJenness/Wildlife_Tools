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

"""Dialog validation of Add Random Numbers without the Pro UI: the field
value list, the typed-new-name trick, and the integer-field refusals.
Drives updateParameters / updateMessages the way the GP pane does.
usage: python crop_plots_validation_test.py <pyt path> <scratch folder>
       (the scratch folder's crop_test.gdb comes from crop_plots_test.py)
"""
import importlib.machinery
import importlib.util
import os
import sys

import arcpy

pyt, scratch = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.dirname(pyt))
loader = importlib.machinery.SourceFileLoader("jenness_pyt", pyt)
spec = importlib.util.spec_from_loader("jenness_pyt", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
import crop_plots as cp  # noqa: E402

plots = os.path.join(scratch, "crop_test.gdb", "plots")
fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def error_of(p):
    """The parameter's error text, or '' (arcpy.Parameter outside the framework)."""
    try:
        if p.hasError():
            return p.message or "error"
    except Exception:
        pass
    return ""


tool = mod.AddRandomNumbers()
params = tool.getParameterInfo()
P = {p.name: p for p in params}


def validate():
    """One validation pass as the GP pane runs it: messages cleared first."""
    for p in params:
        try:
            p.clearMessage()
        except Exception:
            pass
    tool.updateParameters(params)
    tool.updateMessages(params)
check(P["field"].filter.type == "ValueList" and "Treatment" in list(P["field"].filter.list),
      "field is a value list seeded with Treatment")

# the input's numeric fields drive the list ------------------------------------
P["in_table"].value = plots
validate()
lst = list(P["field"].filter.list)
numeric = cp.numeric_field_names(plots)
int_field = next(f.name for f in arcpy.ListFields(plots) if f.type in ("SmallInteger", "Integer") and f.editable)
dbl_field = next(f.name for f in arcpy.ListFields(plots) if f.type == "Double" and f.editable)
check(all(n in lst for n in numeric) and int_field in lst and dbl_field in lst,
      "list holds the writable numeric fields (%s)" % ", ".join(numeric))
check(not any(f.name in lst for f in arcpy.ListFields(plots) if f.type in ("OID", "Geometry")),
      "ObjectID / geometry fields left out")
check("Shape_Length" not in lst and "Shape_Area" not in lst, "read-only shape measures left out")
check("Treatment" in lst, "the default new name stays a member while it is the value")

# a typed new name joins the list ----------------------------------------------
P["field"].value = "MyNewField"
validate()
check("MyNewField" in list(P["field"].filter.list), "a typed new field name becomes a list member")
validate()
check(not error_of(P["field"]), "new name: no error (warning only)")

# decimals into an integer field are refused -----------------------------------
P["field"].value = int_field
P["mode"].value = "Random decimals between 0 and 1"
validate()
check("integer field" in error_of(P["field"]), "decimal mode into %s (integer) refused" % int_field)
P["mode"].value = "Random decimals from a normal distribution (mean, standard deviation)"
P["mean"].value = 1.0
P["sd"].value = 1.0
validate()
check("integer field" in error_of(P["field"]), "normal mode into an integer field refused")
P["mode"].value = "Constant value"
P["constant"].value = 2.5
validate()
check("decimal part" in error_of(P["field"]), "constant 2.5 into an integer field refused")
P["constant"].value = 3.0
validate()
check(not error_of(P["field"]), "constant 3 into an integer field allowed")
P["mode"].value = "Random permutation of 1 to N (each record unique)"
validate()
check(not error_of(P["field"]), "permutation into an integer field allowed")

# a double field takes everything -----------------------------------------------
P["field"].value = dbl_field
P["mode"].value = "Random decimals between 0 and 1"
validate()
check(not error_of(P["field"]), "decimal mode into %s (double) allowed" % dbl_field)

# the case field follows the mode ----------------------------------------------
P["mode"].value = "Random permutation of 1 to N (repeated for replicates)"
P["case_field"].value = None
validate()
check(P["case_field"].enabled and (P["case_field"].valueAsText or "") == "Rep",
      "repeated mode enables the case field and suggests Rep")
P["mode"].value = "Constant value"
validate()
check(not P["case_field"].enabled, "other modes disable the case field")

print("\nFAILURES:", len(fails))
for f in fails:
    print("  -", f)
