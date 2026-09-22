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

"""R cross-validation for accuracy_assessment.py: the same fixture
matrices are fed to the R reference packages (fmsb, psych, caret,
yardstick, mapaccuracy, diffeR, irrCAC) and every shared statistic
is compared. Run with ArcGIS Pro's python; requires R 4.x with the
packages installed (dev machine only -- users never need R).
"""
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE),
                                "WildlifeTools", "Toolboxes"))

import accuracy_assessment as aa  # noqa: E402

RSCRIPT = r"C:\Program Files\R\R-4.5.1\bin\Rscript.exe"

PASS = 0
FAIL = 0


def check(name, ours, theirs, tol=1e-9, rel=False):
    global PASS, FAIL
    ours = float(ours)
    theirs = float(theirs)
    if rel:
        ok = abs(ours - theirs) <= tol * max(abs(theirs), 1e-12)
    else:
        ok = abs(ours - theirs) <= tol
    if ok:
        PASS += 1
        print("  PASS  {}  ({:.10g} vs R {:.10g})".format(
            name, ours, theirs))
    else:
        FAIL += 1
        print("  FAIL  {}  ours {:.12g}  R {:.12g}".format(
            name, ours, theirs))


# fixtures ------------------------------------------------------------------
# the 2007 manual's worked matrix (rows = predicted, cols = observed)
MANUAL = np.array([[60, 22, 4],
                   [2, 30, 3],
                   [1, 4, 10]], dtype=float)
# an Olofsson-style stratified design (4 map strata + mapped areas)
OLO = np.array([[66, 0, 5, 4],
                [0, 55, 8, 12],
                [1, 0, 153, 11],
                [2, 1, 9, 313]], dtype=float)
OLO_AREAS = np.array([200000.0, 150000.0, 3100000.0, 6450000.0])
# ordinal 4x4 for weighted kappa
ORD = np.array([[20, 5, 1, 0],
                [4, 18, 6, 1],
                [1, 5, 15, 4],
                [0, 2, 5, 13]], dtype=float)
# imbalanced 2x2 for AC1 (kappa's prevalence-paradox territory)
TWO = np.array([[80, 5],
                [10, 5]], dtype=float)


def r_matrix(m):
    vals = ",".join(str(int(v)) for v in np.asarray(m).ravel())
    return ("matrix(c({}), nrow={}, byrow=TRUE)"
            .format(vals, m.shape[0]))


R_SCRIPT = """
options(digits=15)
suppressMessages({{
  library(fmsb); library(psych); library(caret)
  library(yardstick); library(mapaccuracy); library(diffeR)
  library(irrCAC)
}})
emit <- function(name, v) cat(sprintf("%s %.15g\\n", name, v))

manual <- {manual}
olo <- {olo}
olo_areas <- c({olo_areas})
ord <- {ord}
two <- {two}

expand_pairs <- function(m) {{
  k <- nrow(m); pred <- c(); obs <- c()
  for (i in 1:k) for (j in 1:k) if (m[i, j] > 0) {{
    pred <- c(pred, rep(i, m[i, j])); obs <- c(obs, rep(j, m[i, j]))
  }}
  list(pred=pred, obs=obs)
}}

# ---- Cohen's kappa + variance (fmsb implements Fleiss-Cohen-
# Everitt; note fmsb wants the table oriented the same way ours is)
kt <- Kappa.test(as.table(manual))
emit("kappa_manual", kt$Result$estimate)
# fmsb reports the CI; back out the SE it used
emit("kappa_manual_se",
     (kt$Result$conf.int[2] - kt$Result$estimate) / qnorm(0.975))

# psych: unweighted + weighted variance cross-check
ck <- cohen.kappa(manual)
emit("kappa_psych", ck$kappa)
emit("kappa_psych_var", ck$var.kappa)

# ---- weighted kappa from raw pairs via yardstick (unambiguous
# linear/quadratic conventions)
pr <- expand_pairs(ord)
df <- data.frame(truth=factor(pr$obs, levels=1:4),
                 estimate=factor(pr$pred, levels=1:4))
emit("wkappa_linear",
     kap(df, truth, estimate, weighting="linear")$.estimate)
emit("wkappa_quadratic",
     kap(df, truth, estimate, weighting="quadratic")$.estimate)

# ---- caret per-class suite on the manual matrix
cm <- confusionMatrix(as.table(manual))
emit("caret_accuracy", cm$overall["Accuracy"])
emit("caret_kappa", cm$overall["Kappa"])
emit("caret_acc_lo", cm$overall["AccuracyLower"])
emit("caret_acc_hi", cm$overall["AccuracyUpper"])
emit("caret_nir", cm$overall["AccuracyNull"])
emit("caret_acc_p", cm$overall["AccuracyPValue"])
emit("caret_mcnemar_p", cm$overall["McnemarPValue"])
for (i in 1:3) {{
  emit(sprintf("caret_sens_%d", i), cm$byClass[i, "Sensitivity"])
  emit(sprintf("caret_spec_%d", i), cm$byClass[i, "Specificity"])
  emit(sprintf("caret_ppv_%d", i), cm$byClass[i, "Pos Pred Value"])
  emit(sprintf("caret_npv_%d", i), cm$byClass[i, "Neg Pred Value"])
  emit(sprintf("caret_f1_%d", i), cm$byClass[i, "F1"])
  emit(sprintf("caret_prev_%d", i), cm$byClass[i, "Prevalence"])
  emit(sprintf("caret_drate_%d", i),
       cm$byClass[i, "Detection Rate"])
  emit(sprintf("caret_dprev_%d", i),
       cm$byClass[i, "Detection Prevalence"])
  emit(sprintf("caret_balacc_%d", i),
       cm$byClass[i, "Balanced Accuracy"])
}}
# 2x2 case exercises the continuity-corrected McNemar path
m2 <- matrix(c(60,22,4,30), nrow=2, byrow=TRUE,
             dimnames=list(c("1","2"), c("1","2")))
cm2 <- confusionMatrix(as.table(m2))
emit("caret2_mcnemar_p", cm2$overall["McnemarPValue"])
emit("caret2_acc_lo", cm2$overall["AccuracyLower"])
emit("caret2_acc_hi", cm2$overall["AccuracyUpper"])
# NOTE: caret's Sensitivity is 'of reference class j, how many
# predicted j' = producer's accuracy -- same as ours.

# ---- Olofsson et al. 2014 via mapaccuracy
po <- expand_pairs(olo)
names(olo_areas) <- as.character(1:4)
oo <- mapaccuracy::olofsson(r=as.character(po$obs),
                            m=as.character(po$pred),
                            Nh=olo_areas)
emit("olo_oa", oo$OA)
emit("olo_se_oa", oo$SEoa)
for (i in 1:4) {{
  emit(sprintf("olo_ua_%d", i), oo$UA[as.character(i)])
  emit(sprintf("olo_se_ua_%d", i), oo$SEua[as.character(i)])
  emit(sprintf("olo_pa_%d", i), oo$PA[as.character(i)])
  emit(sprintf("olo_se_pa_%d", i), oo$SEpa[as.character(i)])
  emit(sprintf("olo_area_%d", i), oo$area[as.character(i)])
  emit(sprintf("olo_se_area_%d", i), oo$SEa[as.character(i)])
}}

# ---- Pontius & Millones via diffeR (population matrix = counts)
ct <- as.table(manual)
emit("pontius_qty", overallQtyD(ct) / sum(manual))
emit("pontius_alloc", overallAllocD(ct) / sum(manual))
emit("pontius_diff", overallDiff(ct) / sum(manual))

# ---- Gwet AC1 via irrCAC (author's own package)
pt <- expand_pairs(two)
ratings <- data.frame(r1=pt$pred, r2=pt$obs)
g <- gwet.ac1.raw(ratings)
emit("ac1", g$est$coeff.val)
emit("ac1_se", g$est$coeff.se)
"""


def main():
    r_code = R_SCRIPT.format(
        manual=r_matrix(MANUAL), olo=r_matrix(OLO),
        olo_areas=",".join(str(v) for v in OLO_AREAS),
        ord=r_matrix(ORD), two=r_matrix(TWO))
    rfile = os.path.join(tempfile.mkdtemp(prefix="acc_r_"),
                         "validate.R")
    with open(rfile, "w") as f:
        f.write(r_code)
    out = subprocess.run([RSCRIPT, rfile], capture_output=True,
                         text=True, timeout=600)
    if out.returncode != 0:
        print("R FAILED:")
        print(out.stdout[-3000:])
        print(out.stderr[-3000:])
        sys.exit(2)
    rv = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                rv[parts[0]] = float(parts[1])
            except ValueError:
                pass

    print("== Cohen's kappa + variance ==")
    k, v, z, p = aa.kappa(MANUAL)
    check("kappa (fmsb)", k, rv["kappa_manual"])
    check("kappa (psych)", k, rv["kappa_psych"])
    # variance oracle is psych (documented Fleiss-Cohen-Everitt
    # 1969, the manual's theta formula); fmsb's CI instead uses the
    # null-hypothesis variance, a known difference -- its ESTIMATE
    # is checked above, its SE deliberately not.
    check("kappa var (psych)", v, rv["kappa_psych_var"],
          tol=1e-9, rel=True)

    print("== weighted kappa ==")
    kwl, _vl, _z, _p = aa.weighted_kappa(ORD, "linear")
    kwq, _vq, _z, _p = aa.weighted_kappa(ORD, "quadratic")
    check("weighted kappa linear (yardstick)", kwl,
          rv["wkappa_linear"])
    check("weighted kappa quadratic (yardstick)", kwq,
          rv["wkappa_quadratic"])

    print("== per-class suite (caret) ==")
    b = aa.matrix_basics(MANUAL)
    s = aa.per_class_suite(MANUAL)
    check("overall accuracy", b["overall"], rv["caret_accuracy"])
    lo, hi = aa.exact_binom_ci(int(round(np.trace(MANUAL))),
                               int(MANUAL.sum()))
    check("accuracy CI lower (Clopper-Pearson)", lo,
          rv["caret_acc_lo"])
    check("accuracy CI upper (Clopper-Pearson)", hi,
          rv["caret_acc_hi"])
    nir, nir_p = aa.nir_test(MANUAL)
    check("NIR (no-information rate)", nir, rv["caret_nir"])
    check("accuracy > NIR p-value", nir_p, rv["caret_acc_p"],
          tol=1e-9, rel=True)
    check("McNemar-Bowker p (3x3)", aa.mcnemar_test(MANUAL)[2],
          rv["caret_mcnemar_p"], tol=1e-9, rel=True)
    for i in range(3):
        check("sensitivity class {}".format(i + 1),
              s["sensitivity"][i], rv["caret_sens_%d" % (i + 1)])
        check("specificity class {}".format(i + 1),
              s["specificity"][i], rv["caret_spec_%d" % (i + 1)])
        check("PPV class {}".format(i + 1),
              s["ppp"][i], rv["caret_ppv_%d" % (i + 1)])
        check("NPV class {}".format(i + 1),
              s["npp"][i], rv["caret_npv_%d" % (i + 1)])
        check("F1 class {}".format(i + 1),
              s["f1"][i], rv["caret_f1_%d" % (i + 1)])
        check("prevalence class {}".format(i + 1),
              s["prevalence"][i], rv["caret_prev_%d" % (i + 1)])
        check("detection rate class {}".format(i + 1),
              s["detection_rate"][i],
              rv["caret_drate_%d" % (i + 1)])
        check("detection prevalence class {}".format(i + 1),
              s["detection_prevalence"][i],
              rv["caret_dprev_%d" % (i + 1)])
        check("balanced accuracy class {}".format(i + 1),
              s["balanced_accuracy"][i],
              rv["caret_balacc_%d" % (i + 1)])
    M2 = np.array([[60.0, 22.0], [4.0, 30.0]])
    check("McNemar p (2x2, continuity-corrected)",
          aa.mcnemar_test(M2)[2], rv["caret2_mcnemar_p"],
          tol=1e-9, rel=True)
    lo2, hi2 = aa.exact_binom_ci(90, 116)
    check("accuracy CI lower (2x2)", lo2, rv["caret2_acc_lo"])
    check("accuracy CI upper (2x2)", hi2, rv["caret2_acc_hi"])

    print("== Olofsson et al. 2014 (mapaccuracy) ==")
    o = aa.olofsson(OLO, OLO_AREAS)
    check("overall accuracy", o["overall"], rv["olo_oa"])
    check("SE overall", o["se_overall"], rv["olo_se_oa"],
          tol=1e-6, rel=True)
    for i in range(4):
        check("UA class {}".format(i + 1), o["users"][i],
              rv["olo_ua_%d" % (i + 1)])
        check("SE UA class {}".format(i + 1), o["se_users"][i],
              rv["olo_se_ua_%d" % (i + 1)], tol=1e-6, rel=True)
        check("PA class {}".format(i + 1), o["producers"][i],
              rv["olo_pa_%d" % (i + 1)])
        check("SE PA class {}".format(i + 1), o["se_producers"][i],
              rv["olo_se_pa_%d" % (i + 1)], tol=1e-6, rel=True)
        # mapaccuracy returns area PROPORTIONS; ours are absolute
        check("area proportion class {}".format(i + 1),
              o["area_prop"][i],
              rv["olo_area_%d" % (i + 1)], tol=1e-9, rel=True)
        check("SE area prop class {}".format(i + 1),
              o["se_area_prop"][i],
              rv["olo_se_area_%d" % (i + 1)], tol=1e-6, rel=True)

    print("== Pontius & Millones (diffeR) ==")
    pon = aa.pontius(MANUAL / MANUAL.sum())
    check("quantity disagreement", pon["quantity"],
          rv["pontius_qty"])
    check("allocation disagreement", pon["allocation"],
          rv["pontius_alloc"])
    check("total disagreement", pon["disagreement"],
          rv["pontius_diff"])

    print("== Gwet AC1 (irrCAC) ==")
    ac1, var, _z, _p = aa.gwet_ac1(TWO)
    check("AC1", ac1, rv["ac1"])
    # irrCAC rounds its reported SE to 5 decimals
    check("AC1 SE", var ** 0.5, rv["ac1_se"], tol=5e-6)

    print()
    print("{} passed, {} failed".format(PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
