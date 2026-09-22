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

"""Driver-level tests for accuracy_assessment.py: field mode,
raster extraction with the circle-majority option, analysis-field
comparisons, weighted (Olofsson) mode, output tables, and the two
companion drivers. Run with ArcGIS Pro's python.
"""
import math
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE),
                                "WildlifeTools", "Toolboxes"))

import accuracy_assessment as aa  # noqa: E402
import arcpy  # noqa: E402

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  {}  {}".format(name, detail))


class Msgs:
    def __init__(self):
        self.warns = []

    def addMessage(self, t):
        pass

    def addWarningMessage(self, t):
        self.warns.append(str(t))


TMP = tempfile.mkdtemp(prefix="acc_drv_")
arcpy.env.overwriteOutput = True
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
X0, Y0 = 500000.0, 3800000.0

# the manual's matrix as paired field values on points
M = np.array([[60, 22, 4], [2, 30, 3], [1, 4, 10]], dtype=float)
fc = arcpy.management.CreateFeatureclass(
    GDB, "samples", "POINT", spatial_reference=SR).getOutput(0)
arcpy.management.AddField(fc, "PredCls", "LONG")
arcpy.management.AddField(fc, "ObsCls", "LONG")
arcpy.management.AddField(fc, "Observer", "TEXT",
                          field_length=12)
with arcpy.da.InsertCursor(
        fc, ["SHAPE@", "PredCls", "ObsCls", "Observer"]) as ins:
    n = 0
    for i in range(3):
        for j in range(3):
            for _ in range(int(M[i, j])):
                ins.insertRow([arcpy.PointGeometry(
                    arcpy.Point(X0 + (n % 20) * 30.0,
                                Y0 + (n // 20) * 30.0), SR),
                    i + 1, j + 1, "A"])
                n += 1

print("== field mode: manual matrix reproduced ==")
msgs = Msgs()
rep = os.path.join(TMP, "report.txt")
mt = os.path.join(GDB, "matrix_t")
ct = os.path.join(GDB, "class_t")
lines, _res = aa.run_classification_accuracy(
    fc, predicted_field="PredCls", observed_field="ObsCls",
    out_report=rep, out_matrix_table=mt, out_class_table=ct,
    messages=msgs)
txt = "\n".join(lines)
k_exp, v_exp, _z, _p = aa.kappa(M)
check("report contains the exact kappa",
      "{:.4f}".format(k_exp) in txt, "{:.4f}".format(k_exp))
check("report contains overall accuracy 0.7353",
      "0.7353" in txt)
check("report ends with the R snippet",
      "psych::cohen.kappa" in txt
      and "irrCAC::gwet.ac1.raw" in txt)
check("report file written",
      os.path.exists(rep) and os.path.getsize(rep) > 500)
rows = {(r[0], r[1], r[2]): r[3] for r in arcpy.da.SearchCursor(
    mt, ["Analysis", "Predicted", "Observed", "Count"])}
check("matrix table: 9 cells, correct counts",
      len(rows) == 9 and rows[("", "1", "2")] == 22
      and rows[("", "3", "3")] == 10, str(len(rows)))
cls = {r[0]: r for r in arcpy.da.SearchCursor(
    ct, ["Class", "UserAcc", "ProdAcc", "F1", "N_Pred"])}
check("class table: UA/PA/F1 for class 1",
      abs(cls["1"][1] - 60.0 / 86.0) < 1e-9
      and abs(cls["1"][2] - 60.0 / 63.0) < 1e-9
      and abs(cls["1"][3] - 0.8053691275) < 1e-9
      and cls["1"][4] == 86, str(cls.get("1")))

print("== memory-workspace output tables (Pro pane default) ==")
lines, _res = aa.run_classification_accuracy(
    fc, predicted_field="PredCls", observed_field="ObsCls",
    out_matrix_table=r"memory\acc_mt_mem",
    out_class_table=r"memory\acc_ct_mem", messages=Msgs())
n_mem = int(arcpy.management.GetCount(r"memory\acc_mt_mem")
            .getOutput(0))
cls_mem = {r[0] for r in arcpy.da.SearchCursor(
    r"memory\acc_ct_mem", ["Class"])}
check("memory matrix table created with 9 cells", n_mem == 9,
      str(n_mem))
check("memory class table has the 3 classes",
      cls_mem == {"1", "2", "3"}, str(cls_mem))
arcpy.management.Delete(r"memory\acc_mt_mem")
arcpy.management.Delete(r"memory\acc_ct_mem")

print("== analysis field: identical groups compare as equal ==")
# duplicate every record under observer B
with arcpy.da.InsertCursor(
        fc, ["SHAPE@", "PredCls", "ObsCls", "Observer"]) as ins:
    n = 0
    for i in range(3):
        for j in range(3):
            for _ in range(int(M[i, j])):
                ins.insertRow([arcpy.PointGeometry(
                    arcpy.Point(X0 + (n % 20) * 30.0,
                                Y0 + 3000 + (n // 20) * 30.0),
                    SR), i + 1, j + 1, "B"])
                n += 1
lines, _res = aa.run_classification_accuracy(
    fc, predicted_field="PredCls", observed_field="ObsCls",
    analysis_field="Observer", messages=Msgs())
txt = "\n".join(lines)
check("comparison block present with Z = 0.000",
      "A vs B: Z = 0.000" in txt, txt[-400:])
check("R snippet parallels the multi-analysis run",
      'ms[["A"]]' in txt and 'ms[["B"]]' in txt
      and "Fleiss common kappa" in txt)
check("common kappa equals the shared kappa",
      "Common Kappa (Fleiss): {:.4f}".format(k_exp) in txt)

print("== raster extraction + circle majority ==")
# 20x20 raster: left half class 1, right half class 2, one class-3
# cell at (row 10, col 10) -- a point there reads 3 plainly but the
# 2-cell-radius majority around it is class 2
band = np.ones((20, 20), dtype=np.int32)
band[:, 10:] = 2
band[10, 10] = 3
ras_path = os.path.join(GDB, "cls")
ras = arcpy.NumPyArrayToRaster(band, arcpy.Point(X0, Y0),
                               10.0, 10.0)
ras.save(ras_path)
arcpy.management.DefineProjection(ras_path, SR)
pfc = arcpy.management.CreateFeatureclass(
    GDB, "pnts", "POINT", spatial_reference=SR).getOutput(0)
arcpy.management.AddField(pfc, "ObsCls", "LONG")
# cell (r=10, c=10) center: x = X0 + 10*10 + 5, y = ymax - 10*10 - 5
ymax = Y0 + 20 * 10.0
with arcpy.da.InsertCursor(pfc, ["SHAPE@", "ObsCls"]) as ins:
    ins.insertRow([arcpy.PointGeometry(
        arcpy.Point(X0 + 105.0, ymax - 105.0), SR), 3])
    ins.insertRow([arcpy.PointGeometry(
        arcpy.Point(X0 + 25.0, ymax - 25.0), SR), 1])
    ins.insertRow([arcpy.PointGeometry(
        arcpy.Point(X0 + 175.0, ymax - 25.0), SR), 2])
rows = aa._read_point_rows(pfc, ["ObsCls"], None)
xy = [(r[0], r[1]) for r in rows]
plain = aa._extract_from_raster(xy, ras_path, None, None)
check("plain extraction reads the odd cell as 3",
      plain == [3, 1, 2], str(plain))
maj = aa._extract_from_raster(xy, ras_path, 25.0, None)
check("25 m circle majority reads it as 2 (locational "
      "uncertainty)", maj[0] == 2 and maj[1] == 1
      and maj[2] == 2, str(maj))

print("== raster VAT class-name field (the S_value case) ==")
arcpy.management.BuildRasterAttributeTable(ras_path, "Overwrite")
arcpy.management.AddField(ras_path, "ClsName", "TEXT",
                          field_length=24)
names = {1: "pine oak", 2: "oak juniper", 3: "mixed conifer"}
with arcpy.da.UpdateCursor(ras_path, ["Value", "ClsName"]) as cur:
    for row in cur:
        row[1] = names[row[0]]
        cur.updateRow(row)
arcpy.management.AddField(pfc, "ObsName", "TEXT",
                          field_length=24)
with arcpy.da.UpdateCursor(pfc, ["ObsCls", "ObsName"]) as cur:
    for row in cur:
        row[1] = names[row[0]]
        cur.updateRow(row)
lines, res = aa.run_classification_accuracy(
    pfc, predicted_layer=ras_path,
    predicted_class_field="ClsName", observed_field="ObsName",
    messages=Msgs())
labels = [str(v) for v in res[0]["labels"]]
check("VAT field maps cell codes to class names",
      "pine oak" in labels and "mixed conifer" in labels
      and abs(res[0]["basics"]["overall"] - 1.0) < 1e-12,
      str((labels, res[0]["basics"]["overall"])))

print("== auto total-area measured from the raster ==")
lines, res = aa.run_classification_accuracy(
    pfc, predicted_layer=ras_path, observed_field="ObsCls",
    weight_by_map=True, messages=Msgs())
check("area measured: 400 cells x 100 m2 = 4 ha",
      res[0].get("area_total_m2") is not None
      and abs(res[0]["area_total_m2"] - 40000.0) < 1e-6,
      str(res[0].get("area_total_m2")))
check("report area table is in hectares (Area_ha column)",
      "Area_ha" in "\n".join(lines))

print("== weighted mode: Olofsson block + area table ==")
lines, _res = aa.run_classification_accuracy(
    fc, predicted_field="PredCls", observed_field="ObsCls",
    weight_by_map=True,
    map_props_override=[("1", 0.5), ("2", 0.3), ("3", 0.2)],
    map_area_ha=1000.0, out_class_table=os.path.join(GDB, "cw"),
    messages=Msgs())
txt = "\n".join(lines)
check("Olofsson block present", "Olofsson et al. 2014" in txt)
o = aa.olofsson(M, np.array([0.5, 0.3, 0.2]))
check("area-weighted OA matches the direct call",
      "{:.4f}".format(o["overall"]) in txt)
areas = {r[0]: (r[1], r[2]) for r in arcpy.da.SearchCursor(
    os.path.join(GDB, "cw"), ["Class", "AreaProp", "Area_ha"])}
check("class table: error-adjusted area in ha",
      abs(areas["1"][0] - o["area_prop"][0]) < 1e-9
      and abs(areas["1"][1] - o["area_prop"][0] * 1000.0) < 1e-6,
      str(areas.get("1")))
check("R snippet includes mapaccuracy call when weighted",
      "mapaccuracy::olofsson" in txt)

print("== companion drivers ==")
lines, ck, pe = aa.run_compare_analyses(
    [("Model A", 0.8, 0.001), ("Model B", 0.4, 0.001)],
    messages=Msgs())
check("compare: derived common kappa and P returned",
      abs(ck - 0.6) < 1e-9 and 0.0 < pe < 1.0,
      str((ck, pe)))
txt = "\n".join(lines)
z_exp = (0.8 - 0.4) / math.sqrt(0.002)
check("compare: pairwise z matches hand calc",
      "Z = {:.3f}".format(z_exp) in txt, txt)
lines, n_rec = aa.run_sample_size(
    4, 0.95, 0.10, method=aa.SIZE_WORST, population=10000,
    messages=Msgs())
txt = "\n".join(lines)
check("sample size worst case: manual's 156 then FPC 154",
      "n = 156" in txt and "n = 154" in txt and n_rec == 154,
      txt)
lines, n_rec = aa.run_sample_size(
    4, 0.95, 0.10, method=aa.SIZE_MAXPROP, max_prop=0.367,
    population=10000, messages=Msgs())
txt = "\n".join(lines)
check("sample size max-prop: manual's 145 then FPC 143",
      "n = 145" in txt and "n = 143" in txt and n_rec == 143,
      txt)
lines, n_rec = aa.run_sample_size(
    method=aa.SIZE_TARGETSE, target_se=0.01,
    class_table=[("a", 0.4, 0.8), ("b", 0.6, 0.9)],
    messages=Msgs())
check("sample size target-SE returns the Olofsson n",
      n_rec == aa.sample_size_olofsson(0.01, [0.8, 0.9],
                                       [0.4, 0.6]),
      str(n_rec))

try:
    arcpy.management.Delete(GDB)
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass
print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
