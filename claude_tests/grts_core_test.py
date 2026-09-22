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

"""Headless verification of grts_core: inclusion frequencies vs the
design's promised probabilities (the make-or-break test for any
sampling algorithm), reverse-hierarchical structure vs Stevens &
Olsen's Table 1, legacy/certainty guarantees, infinite-frame
correctness, and spatial balance superiority over pure random."""
import sys
import time

import numpy as np

sys.path.insert(0, r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife"
                   r"\Wildlife_Tools\WildlifeTools\Toolboxes")
import grts_core as G

t0 = time.time()
rng0 = np.random.default_rng(7)

# --- 1. RHO matches Stevens & Olsen Table 1 exactly -------------------
rho16 = G.reverse_hierarchical_order(16)
expect = [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15]
assert list(rho16) == expect, list(rho16)
print("1. reverse hierarchical order reproduces Table 1")

# --- 2. finite, equal probability: inclusion frequencies --------------
N, n, R = 200, 20, 3000
pts = rng0.uniform(0, 1000, size=(N, 2))
count = np.zeros(N)
for r in range(R):
    res = G.grts_finite(pts, n, np.random.default_rng(1000 + r))
    count[res["index"]] += 1
freq = count / R
target = n / N
se = np.sqrt(target * (1 - target) / R)
worst = np.abs(freq - target).max()
assert worst < 6 * se, "worst dev {:.4f} vs 6*SE {:.4f}".format(
    worst, 6 * se)
assert np.abs(freq.mean() - target) < 1e-9   # totals always exact
print("2. equal-prob inclusion: worst |freq-0.1| = {:.4f} "
      "(6*SE = {:.4f}) over {} reps".format(worst, 6 * se, R))

# --- 3. finite, PPS weights 1/2/4 -------------------------------------
w = np.ones(N)
w[:40] = 4.0
w[40:100] = 2.0
pi_target = G._inclusion_probs(n, w)
count = np.zeros(N)
for r in range(R):
    res = G.grts_finite(pts, n, np.random.default_rng(5000 + r),
                        weights=w)
    count[res["index"]] += 1
freq = count / R
dev = np.abs(freq - pi_target)
se_i = np.sqrt(pi_target * (1 - pi_target) / R)
assert (dev < 6 * se_i + 1e-12).all(), \
    "PPS worst z = {:.2f}".format((dev / se_i).max())
print("3. PPS inclusion matches targets (max z = {:.2f})".format(
    (dev / np.maximum(se_i, 1e-12)).max()))

# --- 4. certainty unit + legacy sites ---------------------------------
w2 = np.ones(N)
w2[7] = 1e6
seen7 = all(7 in G.grts_finite(
    pts, n, np.random.default_rng(60 + r), weights=w2)["index"]
    for r in range(50))
assert seen7, "certainty unit missed"
legacy = np.zeros(N, dtype=bool)
legacy[[3, 11, 19]] = True
for r in range(50):
    res = G.grts_finite(pts, n, np.random.default_rng(300 + r),
                        legacy=legacy)
    got = set(res["index"][res["use"] == "Legacy"])
    assert got == {3, 11, 19}, got
    assert (res["use"] != "Over").sum() == n
print("4. certainty + legacy guarantees hold")

# --- 5. areal frame: containment, holes, uniformity, balance ----------
outer = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
hole = np.array([[4, 4], [4, 6], [6, 6], [6, 4]], float)  # already CW
rings = [outer, hole]
inhole = 0
cell_counts = np.zeros((2, 2))
for r in range(200):
    res = G.grts_areal(rings, 32, np.random.default_rng(9000 + r))
    xy = res["xy"]
    assert len(xy) == 32
    assert (xy >= -1e-9).all() and (xy <= 10 + 1e-9).all()
    inhole += int(((xy[:, 0] > 4) & (xy[:, 0] < 6)
                   & (xy[:, 1] > 4) & (xy[:, 1] < 6)).sum())
    cell_counts[0, 0] += ((xy[:, 0] < 5) & (xy[:, 1] < 5)).sum()
    cell_counts[0, 1] += ((xy[:, 0] >= 5) & (xy[:, 1] < 5)).sum()
    cell_counts[1, 0] += ((xy[:, 0] < 5) & (xy[:, 1] >= 5)).sum()
    cell_counts[1, 1] += ((xy[:, 0] >= 5) & (xy[:, 1] >= 5)).sum()
assert inhole == 0, "{} points landed in the hole".format(inhole)
share = cell_counts / cell_counts.sum()
# each quadrant holds 24 of the 96 units of area -> expect 0.25 each
assert np.abs(share - 0.25).max() < 0.02, share
print("5. areal: hole avoided; quadrant shares {} (expect ~.25)"
      .format(np.round(share.ravel(), 3)))

# --- 6. linear frame: length-proportional + on-line -------------------
segs = np.array([[0, 0, 100, 0],       # length 100
                 [0, 50, 300, 50]])    # length 300
on_short = 0
tot = 0
for r in range(300):
    res = G.grts_linear(segs, 8, np.random.default_rng(400 + r))
    xy = res["xy"]
    on0 = np.abs(xy[:, 1] - 0) < 1e-6
    on1 = np.abs(xy[:, 1] - 50) < 1e-6
    assert (on0 | on1).all(), "point off the lines"
    on_short += int(on0.sum())
    tot += len(xy)
frac = on_short / tot
assert abs(frac - 0.25) < 0.03, frac
print("6. linear: all points on-line; short-line share {:.3f} "
      "(expect 0.25)".format(frac))

# --- 7. GRTS beats IRS on spatial balance (areal) ---------------------
test_pts = np.random.default_rng(1).uniform(0, 10, size=(4000, 2))
grts_var = []
irs_var = []
for r in range(40):
    res = G.grts_areal([outer], 50, np.random.default_rng(70 + r))
    v, _ = G.balance_metrics(res["xy"][:50], test_pts)
    grts_var.append(v)
    xy_irs = np.random.default_rng(170 + r).uniform(
        0, 10, size=(50, 2))
    v, _ = G.balance_metrics(xy_irs, test_pts)
    irs_var.append(v)
gm, im = np.mean(grts_var), np.mean(irs_var)
assert gm < 0.6 * im, (gm, im)
print("7. balance: Var(v) GRTS {:.3f} vs IRS {:.3f} "
      "(ratio {:.2f})".format(gm, im, gm / im))

# --- 8. RHO prefix balance: first 16 of 64 beat a random 16 -----------
pref_var = []
rand_var = []
for r in range(40):
    res = G.grts_areal([outer], 64, np.random.default_rng(800 + r))
    v, _ = G.balance_metrics(res["xy"][:16], test_pts)
    pref_var.append(v)
    pick = np.random.default_rng(900 + r).choice(64, 16,
                                                 replace=False)
    v, _ = G.balance_metrics(res["xy"][pick], test_pts)
    rand_var.append(v)
pm, rm = np.mean(pref_var), np.mean(rand_var)
assert pm < rm, (pm, rm)
print("8. RHO prefix: first-16 Var(v) {:.3f} < random-16 {:.3f}"
      .format(pm, rm))

# --- 9. reproducibility ------------------------------------------------
a = G.grts_finite(pts, n, np.random.default_rng(42))
b = G.grts_finite(pts, n, np.random.default_rng(42))
assert (a["index"] == b["index"]).all()
c = G.grts_areal(rings, 32, np.random.default_rng(42))
d = G.grts_areal(rings, 32, np.random.default_rng(42))
assert np.allclose(c["xy"], d["xy"])
print("9. seed-reproducible (finite + areal)")

# --- 10. design weights follow the BASE design even with n_over -------
# (regression for the 2026-09-04 review finding: prob was computed
# for the full base+over draw, so 1/prob over the base sample summed
# to N*n/(n+n_over) instead of N -- the Horvitz-Thompson invariant --
# and disagreed with spsurvey's ip = n_base/N)
pts10 = np.random.default_rng(3).uniform(0, 100, size=(100, 2))
r10 = G.grts_finite(pts10, 10, np.random.default_rng(5), n_over=10)
base10 = r10["prob"][np.asarray(r10["use"]) != "Over"]
assert np.allclose(r10["prob"], 0.1), r10["prob"][:5]
assert abs(float((1.0 / base10).sum()) - 100.0) < 1e-9
# PPS: base-design pi proportional to weight (no certainties here),
# and the HT estimate of the total weight is exact for equal draws
w10 = np.linspace(1, 3, 100)
r10b = G.grts_finite(pts10, 10, np.random.default_rng(6),
                     n_over=5, weights=w10)
pi_expect = 10.0 * w10 / w10.sum()
assert np.allclose(r10b["prob"], pi_expect[r10b["index"]])
print("10. Design_Wgt = base-design 1/pi (HT invariant, spsurvey ip)")

print("ALL OK in {:.1f} s".format(time.time() - t0))
