# -*- coding: utf-8 -*-
"""Estimated Shortest Path through Points -- the arcpy-free routing
core.

An open-path / round-trip traveling-salesperson HEURISTIC (hence
"Estimated"): nearest-neighbor and (small-n) cheapest-insertion
constructions plus randomized restarts, each polished by
best-improvement 2-opt (segment reversal) and Or-opt (segments of
1-3 points relocated, forward or reversed).  All moves are evaluated
by O(1) deltas on one precomputed distance matrix, with inner scans
vectorized in numpy, and applied only when STRICTLY improving --
a zero-gain move accepted as "improving" loops forever (hard-won
first-draft bug, see the survey_routes figure script).  Deterministic
for a given input and seed; typically lands within a few percent of
the true optimum.

Free endpoints use the classic dummy-node trick: a node at zero
distance from everything pins down nothing, so the polish is free to
end the path wherever it likes; the dummy is cut off afterwards.
A round trip is solved as a path from the anchor to a clone of the
anchor, which optimizes the cycle with the same machinery."""

import numpy as np

_EPS = 1e-9


def dist_matrix(pts):
    """Full Euclidean distance matrix for (N, 2) coordinates.
    Built axis-by-axis (no (N, N, 2) intermediate) so the peak
    allocation is ~2 matrices, not 3-4 -- at the 8,000-stop ceiling
    that is the difference between ~1 GB and ~2.5 GB."""
    p = np.asarray(pts, dtype=np.float64)
    dx = p[:, None, 0] - p[None, :, 0]
    dy = p[:, None, 1] - p[None, :, 1]
    return np.hypot(dx, dy, out=dx)


def _normalized(D):
    """Scale the matrix so its largest distance is 1.  The solver
    only ever COMPARES lengths (the reported route length is
    recomputed from raw coordinates), and normalizing keeps float
    rounding safely below the strictly-improving threshold _EPS at
    any coordinate magnitude -- at ~1e7-unit coordinates an absolute
    1e-9 threshold sits BELOW the rounding noise of the move deltas,
    and zero-gain moves cycle forever (found in review)."""
    m = float(D.max())
    return (D / m, m) if m > 0 else (D, 1.0)


def route_len(D, order):
    o = np.asarray(order)
    return float(D[o[:-1], o[1:]].sum())


# ---------------------------------------------------------------------------
# constructions (order[0] and order[-1] are the fixed endpoints)
# ---------------------------------------------------------------------------

def nearest_neighbor(D, n):
    """Greedy chain from node 0 through the middles to node n-1."""
    if n <= 2:
        return list(range(n))
    remaining = np.ones(n, dtype=bool)
    remaining[0] = remaining[n - 1] = False
    order = [0]
    cur = 0
    for _ in range(n - 2):
        d = np.where(remaining, D[cur], np.inf)
        nxt = int(d.argmin())
        order.append(nxt)
        remaining[nxt] = False
        cur = nxt
    return order + [n - 1]


def cheapest_insertion(D, n):
    """Grow the path by always inserting the globally cheapest
    (point, slot) pair.  O(n^3) with vectorized steps -- callers gate
    it to modest n."""
    if n <= 2:
        return list(range(n))
    order = [0, n - 1]
    todo = list(range(1, n - 1))
    while todo:
        o = np.asarray(order)
        p, q = o[:-1], o[1:]
        base = D[p, q]
        tv = np.asarray(todo)
        add = D[np.ix_(tv, p)] + D[np.ix_(tv, q)] - base[None, :]
        flat = int(add.argmin())
        vi, k = divmod(flat, add.shape[1])
        order.insert(k + 1, todo.pop(vi))
    return order


# ---------------------------------------------------------------------------
# polish
# ---------------------------------------------------------------------------

def _two_opt_pass(D, order):
    """One best-improvement 2-opt sweep; endpoints fixed.  Returns
    True if any reversal was applied."""
    n = len(order)
    improved = False
    for i in range(1, n - 2):
        a, b = order[i - 1], order[i]
        js = np.arange(i + 1, n - 1)
        c = order[js]
        d = order[js + 1]
        delta = D[a, c] + D[b, d] - D[a, b] - D[c, d]
        k = int(delta.argmin())
        if delta[k] < -_EPS:
            j = int(js[k])
            order[i:j + 1] = order[i:j + 1][::-1].copy()
            improved = True
    return improved


def _or_opt_pass(D, order):
    """One Or-opt sweep: relocate segments of 3, 2, then 1
    consecutive points (forward or reversed) to their best
    strictly-improving slot.  Longest-first is the classical order,
    and Babin, Deneault & Laporte (2007) measured it clearly better
    than shortest-first on planar instances."""
    improved = False
    for seg_len in (3, 2, 1):
        i = 1
        while i < len(order) - seg_len:
            n = len(order)
            s0, s1 = order[i], order[i + seg_len - 1]
            a, b = order[i - 1], order[i + seg_len]
            gain = D[a, s0] + D[s1, b] - D[a, b]
            if gain > _EPS:
                ks = np.concatenate([np.arange(1, i),
                                     np.arange(i + seg_len + 1, n)])
                if len(ks):
                    p = order[ks - 1]
                    q = order[ks]
                    fwd = D[p, s0] + D[s1, q] - D[p, q]
                    rev = D[p, s1] + D[s0, q] - D[p, q]
                    cost = np.minimum(fwd, rev)
                    k = int(cost.argmin())
                    if gain - cost[k] > _EPS:
                        kk = int(ks[k])
                        seg = list(order[i:i + seg_len])
                        if rev[k] < fwd[k]:
                            seg = seg[::-1]
                        rest = list(order[:i]) + \
                            list(order[i + seg_len:])
                        new_k = kk - seg_len if kk >= i + seg_len \
                            else kk
                        order[:] = np.asarray(
                            rest[:new_k] + seg + rest[new_k:])
                        improved = True
                        continue        # re-examine slot i
            i += 1
    return improved


def polish(D, order, max_sweeps=500):
    """2-opt + Or-opt sweeps until a full pass finds nothing.  The
    sweep cap is pure insurance against float-noise cycling -- with
    the distance matrix normalized (see ``plan_route``) it is never
    reached in practice."""
    order = np.asarray(order)
    for _ in range(max_sweeps):
        any_move = _two_opt_pass(D, order)
        any_move = _or_opt_pass(D, order) or any_move
        if not any_move:
            break
    return list(int(v) for v in order)


def solve_path(D, n, attempts, rng):
    """Best (length, order) over the constructions + polish.  The
    endpoints (nodes 0 and n-1) stay fixed."""
    cands = [nearest_neighbor(D, n)]
    if n <= 600:
        cands.append(cheapest_insertion(D, n))
    middles = np.arange(1, n - 1)
    # randomized restarts add little beyond good constructions at
    # large n but cost a full polish each -- gate them down
    if n > 2500:
        attempts = min(int(attempts or 0), 1)
    for _ in range(max(0, int(attempts))):
        cands.append([0] + [int(v) for v in rng.permutation(middles)]
                     + [n - 1])
    best_len, best_order = None, None
    for c in cands:
        o = polish(D, c)
        ln = route_len(D, o)
        if best_len is None or ln < best_len - _EPS:
            best_len, best_order = ln, o
    return best_len, best_order


# ---------------------------------------------------------------------------
# public entry
# ---------------------------------------------------------------------------

def plan_route(stops, start=None, end=None, round_trip=False,
               attempts=3, seed=0):
    """Order ``stops`` ((N, 2) array) into a short route.

    start / end : optional (2,) coordinates OUTSIDE the stop list
        (the truck, the trailhead).  Omitted ends are chosen freely
        by the solver (dummy-node trick).
    round_trip : close the loop back to the start (or, with no start
        given, back to wherever the loop begins); ``end`` is ignored.

    Returns dict:
        stop_order : visit order as indices into ``stops``
        path_xy    : (M, 2) full route vertices, including any real
                     start/end and the closing leg of a round trip
        length     : total route length in coordinate units
    """
    stops = np.asarray(stops, dtype=np.float64).reshape(-1, 2)
    n_stops = len(stops)
    if n_stops < 1:
        raise ValueError("No stops to route through.")
    rng = np.random.default_rng(seed)

    if round_trip:
        if start is not None:
            anchor = np.asarray(start, dtype=np.float64).reshape(2)
            nodes = np.vstack([anchor, stops, anchor])
            node_to_stop = {i + 1: i for i in range(n_stops)}
            lead = []                      # anchor is not a stop
        else:
            nodes = np.vstack([stops[0], stops[1:], stops[0]])
            node_to_stop = {i: i for i in range(1, n_stops)}
            lead = [0]                     # first stop anchors loop
        D, _ = _normalized(dist_matrix(nodes))
        _, order = solve_path(D, len(nodes), attempts, rng)
        stop_order = lead + [node_to_stop[o] for o in order[1:-1]]
        path = nodes[np.asarray(order)]
    else:
        free_s, free_e = start is None, end is None
        node0 = np.zeros(2) if free_s else \
            np.asarray(start, dtype=np.float64).reshape(2)
        node_n = np.zeros(2) if free_e else \
            np.asarray(end, dtype=np.float64).reshape(2)
        nodes = np.vstack([node0, stops, node_n])
        D, _ = _normalized(dist_matrix(nodes))
        if free_s:
            D[0, :] = 0.0
            D[:, 0] = 0.0
        if free_e:
            D[-1, :] = 0.0
            D[:, -1] = 0.0
        _, order = solve_path(D, len(nodes), attempts, rng)
        stop_order = [o - 1 for o in order[1:-1]]
        path_idx = ([] if free_s else [0]) + list(order[1:-1]) + \
            ([] if free_e else [len(nodes) - 1])
        path = nodes[np.asarray(path_idx)]

    seg = np.diff(path, axis=0)
    length = float(np.hypot(seg[:, 0], seg[:, 1]).sum())
    return {"stop_order": np.asarray(stop_order, dtype=np.int64),
            "path_xy": path, "length": length}
