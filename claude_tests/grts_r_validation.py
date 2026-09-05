# -*- coding: utf-8 -*-
"""Permanent R cross-validation of the GRTS engine against spsurvey
(the reference implementation, Dumelle et al. 2023) -- the kappa
suite's R-validation discipline applied to survey design.

100 draws x 50 sites from the Pinalenos polygon per implementation,
every sample scored with ONE common yardstick (our balance_metrics
over a fixed test-point set), distributions compared. The expected
signature (established 2026-09-03): spsurvey approximates a polygon
frame with pt_density x n random candidate points, so its balance
converges MONOTONICALLY toward our exact continuous placement as
pt_density grows:

    pt_density=10   Var(v) ~ 0.138
    pt_density=1000 Var(v) ~ 0.128
    ours (exact)    Var(v) ~ 0.121
    simple random   Var(v) ~ 0.331   (context)

Needs R with spsurvey installed (skips gracefully if absent).
Slow (~10 min): the price of a real head-to-head."""
import csv
import os
import subprocess
import sys
import tempfile

import numpy as np
from scipy import stats

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))),
    "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: F401
import grts_core as G
import grts_sampling as GS

import glob
_r = sorted(glob.glob(r"C:\Program Files\R\R-*\bin\Rscript.exe"))
RSCRIPT = _r[-1] if _r else ""
FRAME_FC = (r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife"
            r"\Pro_Test_Project\Pro Test Tools.gdb\Pinalenos")
N, RUNS = 50, 100

if not RSCRIPT:
    print("SKIP: Rscript not found under C:\\Program Files\\R")
    sys.exit(0)
chk = subprocess.run([RSCRIPT, "-e", "packageVersion('spsurvey')"],
                     capture_output=True, text=True)
if chk.returncode != 0:
    print("SKIP: spsurvey not installed in R")
    sys.exit(0)

tmp = tempfile.mkdtemp(prefix="grts_rval_")
shp = os.path.join(tmp, "frame.shp")
arcpy.env.overwriteOutput = True
arcpy.management.CopyFeatures(FRAME_FC, shp)

# ---- common yardstick -------------------------------------------------
sr = arcpy.Describe(shp).spatialReference
rings = GS._read_polygons(shp, sr, None)[0][None]
measure = G.ArealMeasure(rings)
test_pts = GS._test_points_areal(measure,
                                 np.random.default_rng(123456),
                                 n_test=6000)


def score_runs(list_of_xy):
    vs = []
    for xy in list_of_xy:
        v, _p = G.balance_metrics(np.asarray(xy), test_pts)
        vs.append(v)
    return np.asarray(vs)


# ---- ours -------------------------------------------------------------
ours = score_runs([G.grts_areal(rings, N,
                                np.random.default_rng(s))["xy"][:N]
                   for s in range(1, RUNS + 1)])

# ---- spsurvey at two densities ---------------------------------------
R_TEMPLATE = """suppressMessages({{ library(sf); library(spsurvey) }})
frame <- st_read("{shp}", quiet = TRUE)
out <- NULL
for (i in 1:{runs}) {{
  set.seed(i)
  s <- grts(frame, n_base = {n}, pt_density = {dens})
  xy <- st_coordinates(s$sites_base)
  out <- rbind(out, data.frame(run = i, x = xy[, 1], y = xy[, 2]))
}}
write.csv(out, "{csv}", row.names = FALSE)
"""


def spsurvey_runs(dens):
    csvp = os.path.join(tmp, "r_{}.csv".format(dens))
    rp = os.path.join(tmp, "r_{}.R".format(dens))
    with open(rp, "w", encoding="ascii") as f:
        f.write(R_TEMPLATE.format(shp=shp.replace("\\", "/"),
                                  runs=RUNS, n=N, dens=dens,
                                  csv=csvp.replace("\\", "/")))
    r = subprocess.run([RSCRIPT, rp], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    runs = {}
    with open(csvp, newline="") as f:
        for row in csv.DictReader(f):
            runs.setdefault(int(row["run"]), []).append(
                (float(row["x"]), float(row["y"])))
    assert len(runs) == RUNS
    return score_runs([runs[i] for i in sorted(runs)])


sp10 = spsurvey_runs(10)
sp1000 = spsurvey_runs(1000)

# ---- verdicts ---------------------------------------------------------
m_ours, m10, m1000 = ours.mean(), sp10.mean(), sp1000.mean()
print("Var(v) means: ours {:.4f} | spsurvey d10 {:.4f} | "
      "d1000 {:.4f}".format(m_ours, m10, m1000))

# 1. same league: both implementations near each other, far from SRS
assert abs(m_ours - m1000) < 0.02, (m_ours, m1000)
# 2. monotone convergence toward the exact continuous limit
assert m10 > m1000 > m_ours - 0.005, (m10, m1000, m_ours)
# 3. both crush simple random (~0.33 on this frame/yardstick)
assert m_ours < 0.2 and m1000 < 0.2
ks = stats.ks_2samp(ours, sp1000)
print("ours vs spsurvey(d1000): KS D = {:.3f}, p = {:.3f}".format(
    ks.statistic, ks.pvalue))
print("ALL OK: designs agree; spsurvey converges toward the exact "
      "continuous placement as its frame approximation densifies")
