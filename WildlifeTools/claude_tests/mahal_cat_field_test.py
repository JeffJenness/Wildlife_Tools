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

"""Categorical-source field+value redesign test (Jeff, 2026-09-08).

Verifies, headlessly:
 1. resolve_category_values maps a RAT field + attribute value to ALL matching
    cell values (the Landfire EVT_LF = "Tree" scenario), and falls back to the
    literal numeric cell value when no field is given (classic / script mode).
 2. run_covariance_from_datasets with cat_field produces the same mean and
    covariance as a NumPy reference over the union mask of the matching values.
 3. The classic single-value path still matches its own NumPy reference.
 4. If the real Landfire EVT raster is present, EVT_LF = "Tree" resolves to a
    plausible set of codes (smoke test only; no statistics run on it).

Run with Pro's python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" mahal_cat_field_test.py
"""
import os
import sys
import tempfile

import numpy as np
import arcpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "Toolboxes"))
import mahalanobis  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("  {} {}{}".format("PASS" if ok else "FAIL", label,
                             " -- " + detail if detail else ""))
    if not ok:
        FAILURES.append(label)


class Msgs(object):
    def addMessage(self, m):
        print("    [msg] {}".format(m))

    def addWarningMessage(self, m):
        print("    [warn] {}".format(m))


def build_test_data(gdb):
    """A 40x40 categorical raster with RAT (VegName: 5 classes over values 1-5,
    'Tree' covering values 2 and 5) plus two continuous predictor rasters."""
    rng = np.random.RandomState(42)
    sr = arcpy.SpatialReference(26912)  # NAD83 / UTM 12N
    ll = arcpy.Point(500000, 3800000)

    cat = rng.randint(1, 6, size=(40, 40)).astype(np.int32)
    cat_path = os.path.join(gdb, "veg")
    arcpy.NumPyArrayToRaster(cat, ll, 30, 30).save(cat_path)
    arcpy.management.DefineProjection(cat_path, sr)
    arcpy.management.BuildRasterAttributeTable(cat_path)
    arcpy.management.AddField(cat_path, "VegName", "TEXT", field_length=40)
    names = {1: "Water", 2: "Aspen Forest", 3: "Shrub", 4: "Grass", 5: "Conifer Forest"}
    life = {1: "Water", 2: "Tree", 3: "Shrub", 4: "Herb", 5: "Tree"}
    arcpy.management.AddField(cat_path, "LifeForm", "TEXT", field_length=20)
    with arcpy.da.UpdateCursor(cat_path, ["Value", "VegName", "LifeForm"]) as cur:
        for row in cur:
            row[1] = names[row[0]]
            row[2] = life[row[0]]
            cur.updateRow(row)

    elev = (2000 + 10 * cat + rng.normal(0, 25, size=(40, 40))).astype(np.float32)
    slope = (5 + 2 * cat + rng.normal(0, 3, size=(40, 40))).astype(np.float32)
    elev_path = os.path.join(gdb, "elev")
    slope_path = os.path.join(gdb, "slope")
    arcpy.NumPyArrayToRaster(elev, ll, 30, 30).save(elev_path)
    arcpy.NumPyArrayToRaster(slope, ll, 30, 30).save(slope_path)
    arcpy.management.DefineProjection(elev_path, sr)
    arcpy.management.DefineProjection(slope_path, sr)
    return cat, elev, slope, cat_path, elev_path, slope_path


def np_stats(elev, slope, mask):
    X = np.vstack([elev[mask].astype(float), slope[mask].astype(float)])
    mean = X.mean(axis=1)
    cov = np.cov(X, ddof=1)
    return mean, cov, mask.sum()


def read_stats_tables(gdb, base):
    mean_t = os.path.join(gdb, base + "_mean")
    cov_t = os.path.join(gdb, base + "_cov")
    mean = [r[0] for r in arcpy.da.SearchCursor(mean_t, ["Mean"])]
    cols = [f.name for f in arcpy.ListFields(cov_t) if f.name.startswith("Col")]
    cov = np.array([list(r) for r in arcpy.da.SearchCursor(cov_t, cols)])
    return np.array(mean), cov, mean_t, cov_t


def main():
    scratch = tempfile.mkdtemp(prefix="mahal_cat_")
    gdb = os.path.join(scratch, "t.gdb")
    arcpy.management.CreateFileGDB(scratch, "t.gdb")
    arcpy.env.overwriteOutput = True
    cat, elev, slope, cat_path, elev_path, slope_path = build_test_data(gdb)
    msgs = Msgs()

    print("1. resolve_category_values")
    vals, label = mahalanobis.resolve_category_values(
        cat_path, "LifeForm", "Tree", msgs)
    check("Tree -> values {2, 5}", sorted(vals) == [2.0, 5.0], str(vals))
    check("label mentions field and count",
          label is not None and "LifeForm" in label and "2 raster values" in label,
          str(label))
    vals1, label1 = mahalanobis.resolve_category_values(
        cat_path, "VegName", "Aspen Forest", msgs)
    check("Aspen Forest -> value {2}", vals1 == [2.0], str(vals1))
    vals2, label2 = mahalanobis.resolve_category_values(cat_path, None, "3", None)
    check("no field -> literal passthrough", vals2 == ["3"] and label2 is None,
          str((vals2, label2)))
    try:
        mahalanobis.resolve_category_values(cat_path, "LifeForm", "Cactus", None)
        check("unknown value raises", False)
    except arcpy.ExecuteError as e:
        check("unknown value raises", "No rows" in str(e), str(e)[:60])

    print("2. covariance via field ('Tree' = union of values 2 and 5)")
    mahalanobis.run_covariance_from_datasets(
        var_rows=[(elev_path, None), (slope_path, None)],
        sample_method=mahalanobis.SAMPLE_EXACT,
        stats_source=mahalanobis.SRC_CATEGORICAL,
        out_tables={"mean": os.path.join(gdb, "tree_mean"),
                    "cov": os.path.join(gdb, "tree_cov")},
        inversion=mahalanobis.INV_STANDARD,
        cat_raster=cat_path, cat_field="LifeForm", cat_value="Tree",
        messages=msgs)
    got_mean, got_cov, mt, ct = read_stats_tables(gdb, "tree")
    want_mean, want_cov, n = np_stats(elev, slope, (cat == 2) | (cat == 5))
    check("mean matches numpy union mask (n={})".format(n),
          np.allclose(got_mean, want_mean, rtol=1e-6),
          "{} vs {}".format(got_mean, want_mean))
    check("cov matches numpy union mask",
          np.allclose(got_cov, want_cov, rtol=1e-6))

    print("3. classic literal cell value (no field)")
    mahalanobis.run_covariance_from_datasets(
        var_rows=[(elev_path, None), (slope_path, None)],
        sample_method=mahalanobis.SAMPLE_EXACT,
        stats_source=mahalanobis.SRC_CATEGORICAL,
        out_tables={"mean": os.path.join(gdb, "v3_mean"),
                    "cov": os.path.join(gdb, "v3_cov")},
        inversion=mahalanobis.INV_STANDARD,
        cat_raster=cat_path, cat_field=None, cat_value="3",
        messages=msgs)
    got_mean3, got_cov3, _, _ = read_stats_tables(gdb, "v3")
    want_mean3, want_cov3, n3 = np_stats(elev, slope, cat == 3)
    check("literal value 3 mean matches (n={})".format(n3),
          np.allclose(got_mean3, want_mean3, rtol=1e-6))
    check("literal value 3 cov matches",
          np.allclose(got_cov3, want_cov3, rtol=1e-6))

    print("3a. semicolon list of literal cell values (script form, no field)")
    # '2;5' with no field must equal the field-driven 'Tree' run exactly.
    vals_semi, _ = mahalanobis.resolve_category_values(cat_path, None, "2; 5", None)
    check("'2; 5' parses to two values", vals_semi == ["2", "5"], str(vals_semi))
    mahalanobis.run_covariance_from_datasets(
        var_rows=[(elev_path, None), (slope_path, None)],
        sample_method=mahalanobis.SAMPLE_EXACT,
        stats_source=mahalanobis.SRC_CATEGORICAL,
        out_tables={"mean": os.path.join(gdb, "semi_mean"),
                    "cov": os.path.join(gdb, "semi_cov")},
        inversion=mahalanobis.INV_STANDARD,
        cat_raster=cat_path, cat_field=None, cat_value="2;5",
        messages=msgs)
    got_mean_s, got_cov_s, _, _ = read_stats_tables(gdb, "semi")
    check("'2;5' stats identical to field-driven Tree stats",
          np.allclose(got_mean_s, got_mean, rtol=0) and
          np.allclose(got_cov_s, got_cov, rtol=0))

    print("3b. categorical raster in a DIFFERENT coordinate system (Landfire bug)")
    # The 2026-09-08 live failure: a categorical raster in another CS (Landfire
    # in CONUS Albers vs a UTM analysis grid) matched 0 cells because the
    # category warp had no dstSRS. Reproject the veg raster to Albers and the
    # union-mask statistics must still come out (nearest-neighbour reprojection
    # can flip a few boundary cells, so compare loosely).
    veg_albers = os.path.join(gdb, "veg_albers")
    arcpy.management.ProjectRaster(cat_path, veg_albers,
                                   arcpy.SpatialReference(5070), "NEAREST", "30")
    mahalanobis.run_covariance_from_datasets(
        var_rows=[(elev_path, None), (slope_path, None)],
        sample_method=mahalanobis.SAMPLE_EXACT,
        stats_source=mahalanobis.SRC_CATEGORICAL,
        out_tables={"mean": os.path.join(gdb, "alb_mean"),
                    "cov": os.path.join(gdb, "alb_cov")},
        inversion=mahalanobis.INV_STANDARD,
        cat_raster=veg_albers, cat_field="LifeForm", cat_value="Tree",
        messages=msgs)
    got_mean_a, got_cov_a, _, _ = read_stats_tables(gdb, "alb")
    check("mixed-CS mean close to same-CS mean",
          np.allclose(got_mean_a, want_mean, rtol=0.02),
          "{} vs {}".format(got_mean_a, want_mean))
    check("mixed-CS cov finite and same sign structure",
          np.all(np.isfinite(got_cov_a)) and got_cov_a[0, 0] > 0)

    print("4. Landfire smoke test (optional)")
    lf = r"D:\GIS_Data\Landfire\LF_230\LF_230_EVT.gdb\LC22_EVT_230"
    if arcpy.Exists(lf):
        vals_lf, label_lf = mahalanobis.resolve_category_values(
            lf, "EVT_LF", "Tree", msgs)
        check("EVT_LF='Tree' resolves to many codes", len(vals_lf) > 10,
              "{} codes".format(len(vals_lf)))
    else:
        print("    (Landfire raster not found; skipped)")

    print()
    if FAILURES:
        print("FAILED: {}".format(FAILURES))
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
