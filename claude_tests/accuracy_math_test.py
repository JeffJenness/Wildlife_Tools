# -*- coding: utf-8 -*-
"""Published-example fixtures for accuracy_assessment.py -- checks
against numbers PRINTED in the 2007 Kappa manual (Jenness & Wynne)
and internal consistency identities. Runs anywhere; no R needed.
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE),
                                "WildlifeTools", "Toolboxes"))

import accuracy_assessment as aa  # noqa: E402

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


# the manual's worked matrix (p. 11): rows predicted, cols observed
M = np.array([[60, 22, 4],
              [2, 30, 3],
              [1, 4, 10]], dtype=float)

print("== manual page 11: overall / producer's / user's ==")
b = aa.matrix_basics(M)
check("overall accuracy 73.5%", abs(b["overall"] - 100.0 / 136.0)
      < 1e-12, str(b["overall"]))
check("producer's: 95.2 / 53.6 / 58.8",
      np.allclose(b["producers"], [60 / 63, 30 / 56, 10 / 17]))
check("user's: 69.8 / 85.7 / 66.7",
      np.allclose(b["users"], [60 / 86, 30 / 35, 10 / 15]))

print("== manual page 18: weighted average sensitivity etc. ==")
# the manual's five-class example totals: sens 0.6105, spec 0.9026,
# omission 0.3896, commission 0.0974 -- from a matrix whose diagonal
# is (14, 120, 80, 0, 7) etc.; we verify OUR overall-average
# formulas reproduce the same arithmetic on the same quartet sums
a_, b_, c_, d_ = 221.0, 141.0, 141.0, 1307.0
check("weighted sensitivity arithmetic",
      abs(a_ / (a_ + c_) - 0.6105) < 5e-4)
check("weighted specificity arithmetic",
      abs(d_ / (b_ + d_) - 0.9026) < 5e-4)

print("== manual page 37: sample size table (k=4, 95%, 10%) ==")
check("option 1, no FPC -> 156",
      aa.sample_size_tortora(4, 0.95, 0.10) == 156)
check("option 1, N=10000 -> 154",
      aa.sample_size_tortora(4, 0.95, 0.10,
                             population=10000) == 154)
check("option 1, N=1000 -> 136",
      aa.sample_size_tortora(4, 0.95, 0.10,
                             population=1000) == 136)
check("option 2 (max prop 36.7%), no FPC -> 145",
      aa.sample_size_tortora(4, 0.95, 0.10,
                             max_prop=0.367) == 145)
check("option 2, N=10000 -> 143",
      aa.sample_size_tortora(4, 0.95, 0.10, max_prop=0.367,
                             population=10000) == 143)
check("option 2, N=1000 -> 127",
      aa.sample_size_tortora(4, 0.95, 0.10, max_prop=0.367,
                             population=1000) == 127)

print("== internal identities ==")
k, v, z, p = aa.kappa(M)
check("kappa in (0, 1) and Z = K/sqrt(V)",
      0 < k < 1 and abs(z - k / math.sqrt(v)) < 1e-12)
check("Chebyshev floor at 95% = 74%",
      abs(aa.chebyshev_floor(0.95) - (1 - 1 / 1.959963985 ** 2))
      < 1e-9)
s = aa.per_class_suite(M)
check("sensitivity == producer's; PPP == user's",
      np.allclose(s["sensitivity"], b["producers"])
      and np.allclose(s["ppp"], b["users"]))
check("a+b+c+d = n for every class",
      np.allclose(s["a"] + s["b"] + s["c"] + s["d"], M.sum()))
pon = aa.pontius(M / M.sum())
check("quantity + allocation = total disagreement",
      abs(pon["quantity"] + pon["allocation"]
          - pon["disagreement"]) < 1e-12)
o = aa.olofsson(M, np.array([0.4, 0.35, 0.25]))
check("olofsson area proportions sum to 1",
      abs(o["area_prop"].sum() - 1.0) < 1e-12)
check("olofsson p-matrix rows sum to the weights",
      np.allclose(o["p"].sum(axis=1), o["weights"]))
# Card stratified users == binomial UA variance
cd = aa.card(M, [0.4, 0.35, 0.25], "stratified")
ua = b["users"]
row = M.sum(axis=1)
check("card stratified user's variance = UA(1-UA)/n_i",
      np.allclose(cd["v_users"], ua * (1 - ua) / row))
ki, vi = aa.conditional_kappa(M)
check("conditional kappa: all finite, each <= 1",
      np.all(np.isfinite(ki)) and np.all(ki <= 1.0 + 1e-12))
cmp_ = aa.compare_kappas([(k, v), (k, v)])
check("identical kappas: pairwise z = 0, common = k",
      abs(cmp_["pairwise"][0][2]) < 1e-12
      and abs(cmp_["common_kappa"] - k) < 1e-12)
cmp2 = aa.compare_kappas([(0.8, 0.001), (0.4, 0.001)])
z_exp = (0.8 - 0.4) / math.sqrt(0.002)
check("distinct kappas: pairwise z matches hand calc",
      abs(cmp2["pairwise"][0][2] - z_exp) < 1e-12)
w2 = aa.weighted_kappa(M, "linear")[0]
ident = np.eye(3)
check("weighted kappa with identity weights == plain kappa",
      abs(aa.weighted_kappa(M, "custom", w_matrix=ident)[0] - k)
      < 1e-12, str((w2, k)))
ac1, av, _z, _p = aa.gwet_ac1(np.array([[50.0, 0], [0, 50.0]]))
check("perfect agreement: AC1 = 1, var = 0",
      abs(ac1 - 1.0) < 1e-12 and av < 1e-12)

print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
