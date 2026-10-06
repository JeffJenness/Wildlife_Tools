# -*- coding: utf-8 -*-
"""Run with Pro's Python:  propy select_random_messages_test.py

Smoke test of select_random.run_select_random after the 2026-10-06 text
changes: the small-population warning, the 'select' wording, the renamed
option. Calls the ENGINE directly (no ImportToolbox, so no sidecar churn)."""
import sys

import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "WildlifeTools", "Toolboxes"))
import arcpy
import select_random as SR


class Msgs:
    def __init__(self):
        self.info, self.warn = [], []

    def addMessage(self, t):
        self.info.append(t)

    def addWarningMessage(self, t):
        self.warn.append(t)


def make_layer(n, name):
    fc = arcpy.management.CreateFeatureclass(
        "memory", name, "POINT", spatial_reference=arcpy.SpatialReference(26912)).getOutput(0)
    with arcpy.da.InsertCursor(fc, ["SHAPE@XY"]) as cur:
        for i in range(n):
            cur.insertRow([(400000 + 100 * i, 3900000 + 37 * (i % 7))])
    return arcpy.management.MakeFeatureLayer(fc, name + "_lyr").getOutput(0)


fails = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        fails.append(name)


OPT = SR.SAMPLE_METHODS[2]
check("option name", OPT == "Sample size calculated from a confidence level and margin of error", OPT)

# ---- tiny population: the calculated sample is all of it, and the tool says so
lyr = make_layer(15, "srr_tiny")
m = Msgs()
n_now, n_s = SR.run_select_random(lyr, sample_method=OPT, confidence=95, margin=5,
                                  selection_method="New selection", random_seed=7, messages=m)
check("tiny: all 15 selected", (n_now, n_s) == (15, 15), (n_now, n_s))
check("tiny: warning present", any("so small that the calculated sample is all of it" in w and "15 records" in w
                                  for w in m.warn), m.warn)
check("tiny: seed message says selection",
      any("this selection is exactly reproducible" in t for t in m.info), m.info)
check("no message says draw", not any("draw" in t.lower() for t in m.info + m.warn), m.info + m.warn)
check("calculated-size message", any(t.startswith("Calculated sample size: 95% confidence") for t in m.info), m.info)

# ---- a population large enough: a real sample, and NO warning
lyr2 = make_layer(142, "srr_mid")
m2 = Msgs()
n_now, n_s = SR.run_select_random(lyr2, sample_method=OPT, confidence=95, margin=5,
                                  selection_method="New selection", random_seed=7, messages=m2)
check("142 -> 104", (n_now, n_s) == (104, 104), (n_now, n_s))
check("142: no small-population warning", not m2.warn, m2.warn)

# ---- the old option name still runs (saved models and scripts)
m3 = Msgs()
n_now, n_s = SR.run_select_random(
    lyr2, sample_method="Auto Calculate (from confidence level and margin of error)",
    confidence=95, margin=10, selection_method="New selection", random_seed=7, messages=m3)
check("old option name: 142 at 10% -> 58", n_s == 58, n_s)

# ---- same seed, same selection
ids = []
for _ in range(2):
    SR.run_select_random(lyr2, sample_method="Fixed number of records", n_records=20,
                         selection_method="New selection", random_seed=42, messages=Msgs())
    ids.append(sorted(int(v) for v in arcpy.Describe(lyr2).FIDSet.split(";")))
check("same seed, same selection", ids[0] == ids[1] and len(ids[0]) == 20)

# ---- Add draws only from the unselected; the messages say 'selects'
m4 = Msgs()
SR.run_select_random(lyr2, sample_method="Fixed number of records", n_records=10,
                     selection_method="Add to the current selection", random_seed=1, messages=m4)
check("add: 30 selected, none repeated", len(arcpy.Describe(lyr2).FIDSet.split(";")) == 30)
check("add: message says selects", any("method selects only from" in t for t in m4.info), m4.info)

print("\nSMOKE: %d failed" % len(fails))
sys.exit(1 if fails else 0)
