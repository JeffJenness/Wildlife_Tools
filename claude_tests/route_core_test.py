# -*- coding: utf-8 -*-
"""Headless tests of the Estimated Shortest Path core (route_core):
known optima, brute-force comparisons, free ends, round trips,
determinism, degenerate inputs, and a larger-n smoke."""
import itertools
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))),
    "WildlifeTools", "Toolboxes"))
import route_core as R

ok = 0


def check(cond, label):
    global ok
    assert cond, label
    ok += 1
    print("  ok:", label)


# 1. collinear points, fixed ends -> optimum is the straight sweep
mid = np.array([[x, 0.0] for x in (3, 7, 1, 5, 2, 8, 6, 4)])
res = R.plan_route(mid, start=(0.0, 0.0), end=(9.0, 0.0))
check(abs(res["length"] - 9.0) < 1e-9, "collinear fixed ends = 9.0")
check(list(mid[res["stop_order"]][:, 0]) ==
      sorted(mid[:, 0]), "collinear order is the sweep")

# 2. free both ends on collinear points -> length = span, extremes end
res = R.plan_route(mid)
check(abs(res["length"] - 7.0) < 1e-9, "free ends = span 7.0")
ends = {res["path_xy"][0][0], res["path_xy"][-1][0]}
check(ends == {1.0, 8.0}, "free ends land on the extremes")

# 3. free end only (start fixed off to the left)
res = R.plan_route(mid, start=(-2.0, 0.0))
check(abs(res["length"] - 10.0) < 1e-9, "fixed start, free end = 10")

# 4. round trip on a square = perimeter
sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
res = R.plan_route(sq, round_trip=True)
check(abs(res["length"] - 40.0) < 1e-9, "round trip square = 40")
check(np.allclose(res["path_xy"][0], res["path_xy"][-1]),
      "round trip closes the loop")
res = R.plan_route(sq, start=(5.0, -5.0), round_trip=True)
best = 40.0 - 10.0 + 2 * np.hypot(5, 5)      # replace one side
check(abs(res["length"] - best) < 1e-9, "round trip w/ start optimal")
check(np.allclose(res["path_xy"][0], [5, -5]) and
      np.allclose(res["path_xy"][-1], [5, -5]),
      "round trip starts and ends at the start point")

# 5. brute force, open path fixed ends: 20 random configs, 7 middles
rng = np.random.default_rng(7)
worst = 1.0
for rep in range(20):
    pts = rng.uniform(0, 100, size=(9, 2))
    start, end, middles = pts[0], pts[1], pts[2:]
    best = np.inf
    for perm in itertools.permutations(range(7)):
        seq = np.vstack([start, middles[list(perm)], end])
        d = np.diff(seq, axis=0)
        best = min(best, float(np.hypot(d[:, 0], d[:, 1]).sum()))
    res = R.plan_route(middles, start=start, end=end)
    check(res["length"] <= best * 1.0001 + 1e-9,
          "brute force rep {}: heuristic {:.2f} vs opt {:.2f}".format(
              rep, res["length"], best))
    worst = max(worst, res["length"] / best)
print("  open-path worst ratio vs optimum over 20 reps: "
      "{:.4f}".format(worst))

# 6. brute force, round trip: 10 configs, 8 stops
worst = 1.0
for rep in range(10):
    pts = rng.uniform(0, 100, size=(8, 2))
    best = np.inf
    for perm in itertools.permutations(range(1, 8)):
        seq = np.vstack([pts[0], pts[list(perm)], pts[0]])
        d = np.diff(seq, axis=0)
        best = min(best, float(np.hypot(d[:, 0], d[:, 1]).sum()))
    res = R.plan_route(pts, round_trip=True)
    check(res["length"] <= best * 1.02 + 1e-9,
          "round-trip rep {}: heuristic {:.2f} vs opt {:.2f}".format(
              rep, res["length"], best))
    worst = max(worst, res["length"] / best)
print("  round-trip worst ratio vs optimum over 10 reps: "
      "{:.4f}".format(worst))

# 7. determinism
pts = np.random.default_rng(11).uniform(0, 1000, size=(60, 2))
a = R.plan_route(pts, start=(0, 0), attempts=4)
b = R.plan_route(pts, start=(0, 0), attempts=4)
check(a["length"] == b["length"] and
      (a["stop_order"] == b["stop_order"]).all(), "deterministic")

# 8. degenerate inputs terminate
same = np.zeros((25, 2))
res = R.plan_route(same, start=(1.0, 0.0))
check(abs(res["length"] - 1.0) < 1e-9, "all-coincident stops ok")
res = R.plan_route(np.array([[5.0, 5.0]]))
check(res["length"] == 0.0 and list(res["stop_order"]) == [0],
      "single stop ok")

# 9. larger-n smoke: 800 stops, must beat raw nearest-neighbor
pts = np.random.default_rng(3).uniform(0, 10000, size=(800, 2))
t0 = time.time()
res = R.plan_route(pts, start=(0.0, 0.0), end=(10000.0, 10000.0),
                   attempts=1)
dt = time.time() - t0
nodes = np.vstack([[0, 0], pts, [10000, 10000]])
D = R.dist_matrix(nodes)
nn_len = R.route_len(D, R.nearest_neighbor(D, len(nodes)))
check(res["length"] < nn_len,
      "800 stops: polished {:.0f} beats raw NN {:.0f} ({:.1f}s)"
      .format(res["length"], nn_len, dt))
check((np.sort(res["stop_order"]) == np.arange(800)).all(),
      "800 stops: every stop visited exactly once")

print("ALL OK ({} checks)".format(ok))
