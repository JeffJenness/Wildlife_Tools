# -*- coding: utf-8 -*-
"""Classification Accuracy (Kappa) -- accuracy assessment of
categorical maps and models, ported and modernized from Jenness and
Wynne's ArcView 3.x "Cohen's Kappa and Classification Table Metrics"
extension (kappa_stats.avx; Jenness, J. and J. J. Wynne, 2007,
Jenness Enterprises; methods primarily after Congalton and Green
1999, "Assessing the Accuracy of Remotely Sensed Data").

The classic core, kept intact:
- The error matrix from any pairing of categorical values.
- Cohen's Kappa with its delta-method variance (the Fleiss, Cohen
  and Everitt theta-1..4 formula), Z test against chance, and
  confidence interval -- with the honest Chebyshev worst-case note
  when normality is in doubt.
- Per-class user's/producer's accuracies, and the Card (1982)
  map-proportion-weighted estimates with variances under simple or
  stratified random sampling.
- The per-class diagnostic suite from the collapsed 2x2 confusion
  matrices: sensitivity, specificity, omission and commission error,
  positive and negative predictive power -- plus weighted overall
  averages, and the 2x2 presence/absence guidance.
- Comparisons: per-analysis Z, pairwise Z between analyses, and the
  Fleiss (1981) common-kappa chi-square test.
- Congalton-Green / Tortora (1978) sample-size estimation with the
  finite population correction and per-class precisions.

The modern additions:
- WEIGHTED kappa (Cohen 1968) for ordinal classes, with linear,
  quadratic or custom weights and its delta-method variance.
- Per-class CONDITIONAL kappa with variances (Congalton and Green).
- The Olofsson et al. (2013, 2014) good-practice estimators:
  the error matrix in area proportions, stratified-estimator
  overall/user's/producer's accuracies with standard errors, and
  ERROR-ADJUSTED AREA estimates per class with confidence intervals
  (DOI 10.1016/j.rse.2014.02.015; 10.1016/j.rse.2012.10.031).
- Pontius and Millones (2011) QUANTITY and ALLOCATION disagreement
  (DOI 10.1080/01431161.2011.552923).
- Gwet's AC1 chance-corrected agreement (Gwet 2008,
  DOI 10.1348/000711006X126600), robust to the prevalence paradox
  that afflicts kappa in imbalanced 2x2 presence/absence designs.
- Per-class F1 (the Dice coefficient; harmonic mean of user's and
  producer's accuracy) and macro-F1.

Every statistic in this module operates on a plain (k, k) numpy
count matrix (rows = predicted/map, columns = observed/reference),
so the whole core is unit-testable without arcpy and cross-checkable
against R (psych, vcd, fmsb, caret, yardstick, mapaccuracy, diffeR,
irrCAC).
"""
import math
import os

import numpy as np


# ---------------------------------------------------------------------------
#  basics
# ---------------------------------------------------------------------------
def matrix_basics(n):
    """Totals and raw accuracies from a (k, k) count matrix with
    rows = predicted (map) and columns = observed (reference).
    Returns a dict: n_total, row_tot, col_tot, diag, overall,
    users (per predicted class i), producers (per observed class j).
    Zero-margin classes yield NaN accuracies (reported, not
    crashed)."""
    n = np.asarray(n, dtype=np.float64)
    row = n.sum(axis=1)
    col = n.sum(axis=0)
    tot = float(n.sum())
    diag = np.diag(n).copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        users = np.where(row > 0, diag / row, np.nan)
        producers = np.where(col > 0, diag / col, np.nan)
    overall = float(diag.sum() / tot) if tot > 0 else float("nan")
    return {"n_total": tot, "row_tot": row, "col_tot": col,
            "diag": diag, "overall": overall, "users": users,
            "producers": producers}


def kappa(n):
    """Cohen's Kappa with the delta-method variance of Fleiss,
    Cohen and Everitt (1969) as presented by Congalton and Green
    (1999:50) -- the theta-1..4 formula of the original extension.
    Returns (kappa, variance, z, p_two_sided)."""
    n = np.asarray(n, dtype=np.float64)
    tot = float(n.sum())
    if tot <= 0:
        return float("nan"), float("nan"), float("nan"), float("nan")
    row = n.sum(axis=1)
    col = n.sum(axis=0)
    diag = np.diag(n)
    t1 = float(diag.sum() / tot)
    t2 = float((row * col).sum() / (tot * tot))
    if 1.0 - t2 <= 0:
        return float("nan"), float("nan"), float("nan"), float("nan")
    k_hat = (t1 - t2) / (1.0 - t2)
    t3 = float((diag * (row + col)).sum() / (tot * tot))
    # theta-4 (Fleiss, Cohen & Everitt 1969; manual p. 9): for cell
    # (i, j) the weight is (n_j+ + n_+i) -- the SWAPPED margins (row
    # total of the observed class, column total of the predicted
    # class). Using the unswapped pair inflates the variance on
    # asymmetric matrices (caught by the fmsb/psych cross-check).
    swapped = row[np.newaxis, :] + col[:, np.newaxis]
    t4 = float((n * swapped ** 2).sum() / (tot ** 3))
    one = (t1 * (1.0 - t1)) / ((1.0 - t2) ** 2)
    two = (2.0 * (1.0 - t1) * (2.0 * t1 * t2 - t3)) \
        / ((1.0 - t2) ** 3)
    three = ((1.0 - t1) ** 2 * (t4 - 4.0 * t2 * t2)) \
        / ((1.0 - t2) ** 4)
    var = (one + two + three) / tot
    var = max(var, 0.0)
    z = k_hat / math.sqrt(var) if var > 0 else float("inf")
    p = 2.0 * _norm_sf(abs(z)) if math.isfinite(z) else 0.0
    return float(k_hat), float(var), float(z), float(p)


def weighted_kappa(n, weights="linear", w_matrix=None):
    """Cohen's (1968) weighted kappa on AGREEMENT weights (w_ii = 1,
    off-diagonal in [0, 1)); ``weights`` is "linear", "quadratic"
    or "custom" with ``w_matrix`` supplied. Class ORDER is taken as
    ordinal rank order. Variance per Fleiss, Cohen and Everitt
    (1969). Returns (kappa_w, variance, z, p)."""
    n = np.asarray(n, dtype=np.float64)
    k = n.shape[0]
    tot = float(n.sum())
    if tot <= 0 or k < 2:
        return float("nan"), float("nan"), float("nan"), float("nan")
    if w_matrix is not None:
        w = np.asarray(w_matrix, dtype=np.float64)
    else:
        i = np.arange(k, dtype=np.float64)
        d = np.abs(i[:, None] - i[None, :])
        if weights == "quadratic":
            w = 1.0 - (d / (k - 1.0)) ** 2
        else:
            w = 1.0 - d / (k - 1.0)
    p = n / tot
    r = p.sum(axis=1)                    # predicted marginals
    c = p.sum(axis=0)                    # observed marginals
    po_w = float((w * p).sum())
    pe_w = float((w * np.outer(r, c)).sum())
    if 1.0 - pe_w <= 0:
        return float("nan"), float("nan"), float("nan"), float("nan")
    kw = (po_w - pe_w) / (1.0 - pe_w)
    # delta-method variance (Fleiss, Cohen & Everitt 1969)
    w_i = (w * c[None, :]).sum(axis=1)   # E[w | predicted row i]
    w_j = (w * r[:, None]).sum(axis=0)   # E[w | observed col j]
    term = (p * (w - (w_i[:, None] + w_j[None, :])
                 * (1.0 - kw)) ** 2).sum()
    var = (term - (kw - pe_w * (1.0 - kw)) ** 2) \
        / (tot * (1.0 - pe_w) ** 2)
    var = max(float(var), 0.0)
    z = kw / math.sqrt(var) if var > 0 else float("inf")
    pval = 2.0 * _norm_sf(abs(z)) if math.isfinite(z) else 0.0
    return float(kw), var, float(z), float(pval)


def conditional_kappa(n):
    """Per-class conditional kappa (the user's-perspective
    per-category kappa of Congalton and Green 1999:106) with its
    delta-method variance. Returns (kappa_i, var_i) arrays over the
    predicted classes i."""
    n = np.asarray(n, dtype=np.float64)
    tot = float(n.sum())
    row = n.sum(axis=1)
    col = n.sum(axis=0)
    diag = np.diag(n)
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = tot * row - row * col
        ki = np.where(np.abs(denom) > 0,
                      (tot * diag - row * col) / denom, np.nan)
        # delta-method variance (Bishop, Fienberg & Holland 1975;
        # Congalton & Green):
        # var(K_i) = (p_i. - p_ii) / [ n (p_i.(1-p_.i))^3 ] *
        #   [ (p_i. - p_ii)(p_i. p_.i - p_ii)
        #     + p_ii (1 - p_i. - p_.i + p_ii) ]
        # (an earlier form here dropped the leading (p_i. - p_ii)
        # factor and used base^2 -- ~3x-inflated SEs and a nonzero
        # variance at perfect agreement; found in review, verified
        # against Monte Carlo)
        pi_ = row / tot
        pj_ = col / tot
        pii = diag / tot
        vnum = (pi_ - pii) * (pi_ * pj_ - pii) \
            + pii * (1.0 - pi_ - pj_ + pii)
        base = pi_ * (1.0 - pj_)
        vi = np.where(base > 0,
                      (pi_ - pii) * vnum / (tot * base ** 3), np.nan)
    return ki, np.maximum(vi, 0.0)


# ---------------------------------------------------------------------------
#  proportion-weighted estimators (Card 1982 / Olofsson et al. 2014)
# ---------------------------------------------------------------------------
def proportion_matrix(n, map_props):
    """The error matrix restated in map-area proportions:
    p_ij = W_i * n_ij / n_i+ (rows = map classes with strata weights
    W_i summing to 1). Rows with no samples yield zero rows and a
    flag. Returns (p, empty_rows_mask)."""
    n = np.asarray(n, dtype=np.float64)
    w = np.asarray(map_props, dtype=np.float64)
    row = n.sum(axis=1)
    empty = row <= 0
    with np.errstate(divide="ignore", invalid="ignore"):
        p = w[:, None] * n / np.where(row > 0, row, 1.0)[:, None]
    p[empty, :] = 0.0
    return p, empty


def olofsson(n, map_areas, conf=0.95):
    """The Olofsson et al. (2014) good-practice estimators from a
    (k, k) count matrix (rows = map class strata) and the mapped
    AREA of each class (any unit; also accepts proportions).
    Returns a dict with: p (proportion matrix), overall (accuracy),
    se_overall, users, se_users, producers, se_producers,
    area_prop (estimated true proportion of each class),
    se_area_prop, area (error-adjusted areas in the input unit),
    se_area, ci_area (half-widths), z (the normal quantile used).
    Formulas: Olofsson et al. 2014, eqs. 1-10
    (DOI 10.1016/j.rse.2014.02.015)."""
    n = np.asarray(n, dtype=np.float64)
    areas = np.asarray(map_areas, dtype=np.float64)
    a_tot = float(areas.sum())
    w = areas / a_tot if a_tot > 0 else areas
    row = n.sum(axis=1)
    p, empty = proportion_matrix(n, w)
    zq = _norm_ppf(0.5 + conf / 2.0)

    overall = float(np.trace(p))
    # users' accuracy of map class i: p_ii / p_i+
    p_row = p.sum(axis=1)
    p_col = p.sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        users = np.where(p_row > 0, np.diag(p) / p_row, np.nan)
        producers = np.where(p_col > 0, np.diag(p) / p_col, np.nan)
        # variances (Olofsson 2014 eqs. 5-7)
        vu = np.where(row > 1,
                      users * (1.0 - users) / (row - 1.0), np.nan)
        se_users = np.sqrt(vu)
        v_over = float(np.nansum(
            w * w * np.where(row > 1, users * (1.0 - users)
                             / (row - 1.0), 0.0)))
        se_overall = math.sqrt(max(v_over, 0.0))
        # producer's accuracy variance (eq. 7)
        k = n.shape[0]
        se_prod = np.full(k, np.nan)
        nhat_j = np.zeros(k)
        for j in range(k):
            # estimated N_.j (in area-proportion terms)
            nhat_j[j] = float(p_col[j])
        for j in range(k):
            if p_col[j] <= 0 or row[j] <= 0:
                continue
            pa = producers[j]
            ua = users[j]
            t_a = 0.0
            if row[j] > 1 and math.isfinite(ua):
                t_a = (w[j] ** 2) * ((1.0 - pa) ** 2) \
                    * ua * (1.0 - ua) / (row[j] - 1.0)
            t_b = 0.0
            for i in range(k):
                if i == j or row[i] <= 1:
                    continue
                pij = n[i, j] / row[i]
                t_b += (w[i] ** 2) * pij * (1.0 - pij) \
                    / (row[i] - 1.0)
            t_b *= pa ** 2
            v = (t_a + t_b) / (p_col[j] ** 2) \
                if p_col[j] > 0 else float("nan")
            se_prod[j] = math.sqrt(max(v, 0.0))
    # error-adjusted area proportions and areas (eqs. 9-10)
    area_prop = p_col.copy()
    v_ap = np.zeros(k)
    for j in range(k):
        s = 0.0
        for i in range(k):
            if row[i] <= 1:
                continue
            pij = n[i, j] / row[i]
            s += (w[i] ** 2) * pij * (1.0 - pij) / (row[i] - 1.0)
        v_ap[j] = s
    se_area_prop = np.sqrt(v_ap)
    area = area_prop * a_tot
    se_area = se_area_prop * a_tot
    return {"p": p, "weights": w, "overall": overall,
            "se_overall": se_overall, "users": users,
            "se_users": se_users, "producers": producers,
            "se_producers": se_prod, "area_prop": area_prop,
            "se_area_prop": se_area_prop, "area": area,
            "se_area": se_area, "ci_area": zq * se_area,
            "z": zq, "empty_strata": empty}


def card(n, map_props, design="stratified"):
    """Card (1982) map-proportion-weighted overall / producer's /
    user's accuracies with variances under simple or stratified
    random sampling -- the original extension's weighted analysis
    (manual pp. 27-28). Returns a dict: p_hat (cell probabilities),
    overall, v_overall, users, v_users, producers, v_producers."""
    n = np.asarray(n, dtype=np.float64)
    pi_ = np.asarray(map_props, dtype=np.float64)
    tot = float(n.sum())
    row = n.sum(axis=1)
    simple = str(design).lower().startswith("simple")
    with np.errstate(divide="ignore", invalid="ignore"):
        p_hat = pi_[:, None] * n / np.where(row > 0, row,
                                            1.0)[:, None]
        p_col = p_hat.sum(axis=0)
        pii = np.diag(p_hat)
        overall = float(pii.sum())
        # overall variance (Card 1982)
        if simple:
            v_overall = float(np.nansum(
                pii * (pi_ - pii) / np.where(pi_ > 0, pi_, 1.0))
                / tot)
        else:
            v_overall = float(np.nansum(
                np.where(row > 0,
                         pii * (pi_ - pii)
                         / np.where(pi_ > 0, pi_, 1.0)
                         * tot / np.where(row > 0, row, 1.0),
                         0.0)) / tot)
        # user's accuracy: theta_ii = p_ii / pi_i
        users = np.where(pi_ > 0, pii / pi_, np.nan)
        if simple:
            # Card 1982: v = p_ii (pi_i - p_ii) / (n pi_i^3)
            v_users = np.where(pi_ > 0,
                               pii * (pi_ - pii)
                               / (tot * pi_ ** 3), np.nan)
        else:
            v_users = np.where(
                (pi_ > 0) & (row > 0),
                users * (1.0 - users) / np.where(row > 0, row, 1.0),
                np.nan)
        # producer's accuracy: theta_jj = p_jj / p_.j
        producers = np.where(p_col > 0, pii / p_col, np.nan)
        k = n.shape[0]
        v_prod = np.full(k, np.nan)
        for j in range(k):
            if p_col[j] <= 0:
                continue
            s = 0.0
            for i in range(k):
                if i == j:
                    continue
                ni = row[i]
                if ni <= 0:
                    continue
                pij = p_hat[i, j]
                nn = tot if simple else ni
                s += pij * (pi_[i] - pij) / (pi_[i] * nn) \
                    if pi_[i] > 0 else 0.0
            jj = p_hat[j, j]
            nn = tot if simple else row[j]
            if nn > 0 and pi_[j] > 0:
                t1 = jj * (pi_[j] - jj) / (pi_[j] * nn)
            else:
                t1 = 0.0
            v_prod[j] = (jj ** 2 * s
                         + (p_col[j] - jj) ** 2 * t1) \
                / (p_col[j] ** 4)
    return {"p_hat": p_hat, "overall": overall,
            "v_overall": max(v_overall, 0.0), "users": users,
            "v_users": np.maximum(v_users, 0.0),
            "producers": producers,
            "v_producers": np.maximum(v_prod, 0.0)}


# ---------------------------------------------------------------------------
#  disagreement, agreement alternatives, per-class suite
# ---------------------------------------------------------------------------
def pontius(p):
    """Pontius and Millones (2011) quantity and allocation
    disagreement from a PROPORTION matrix (use proportion_matrix or
    n / n.sum()). Returns a dict: quantity (per class and total),
    allocation (per class and total), disagreement (total)."""
    p = np.asarray(p, dtype=np.float64)
    r = p.sum(axis=1)
    c = p.sum(axis=0)
    d = np.diag(p)
    q_j = np.abs(r - c)
    a_j = 2.0 * np.minimum(r - d, c - d)
    quantity = float(q_j.sum() / 2.0)
    allocation = float(a_j.sum() / 2.0)
    return {"quantity_j": q_j, "allocation_j": a_j,
            "quantity": quantity, "allocation": allocation,
            "disagreement": float(1.0 - d.sum())}


def gwet_ac1(n):
    """Gwet's AC1 (2008) first-order agreement coefficient with its
    delta-method variance -- robust to the prevalence paradox.
    Returns (ac1, variance, z, p)."""
    n = np.asarray(n, dtype=np.float64)
    tot = float(n.sum())
    k = n.shape[0]
    if tot <= 0 or k < 2:
        return float("nan"), float("nan"), float("nan"), float("nan")
    p = n / tot
    r = p.sum(axis=1)
    c = p.sum(axis=0)
    po = float(np.trace(p))
    piq = (r + c) / 2.0
    pe = float((piq * (1.0 - piq)).sum() / (k - 1.0))
    if 1.0 - pe <= 0:
        return float("nan"), float("nan"), float("nan"), float("nan")
    ac1 = (po - pe) / (1.0 - pe)
    # variance: Gwet's exact subject-level linearization for two
    # raters (ported from irrCAC::gwet.ac1.raw): for a subject in
    # cell (i, j),
    #   pa_ij   = 1 if i == j else 0
    #   pe_ij   = [(1 - pi_i) + (1 - pi_j)] / (2 (k - 1))
    #   x_ij    = (pa_ij - pe)/(1 - pe)
    #             - 2 (1 - AC1) (pe_ij - pe)/(1 - pe)
    #   var     = (1/(n-1)) sum_ij p_ij (x_ij - AC1)^2
    ii, jj = np.meshgrid(np.arange(k), np.arange(k), indexing="ij")
    agree = (ii == jj).astype(np.float64)
    pe_ij = ((1.0 - piq[ii]) + (1.0 - piq[jj])) / (2.0 * (k - 1.0))
    x_ij = (agree - pe) / (1.0 - pe) \
        - 2.0 * (1.0 - ac1) * (pe_ij - pe) / (1.0 - pe)
    var = float((p * (x_ij - ac1) ** 2).sum()) / (tot - 1.0) \
        if tot > 1 else float("nan")
    var = max(var, 0.0)
    z = ac1 / math.sqrt(var) if var > 0 else float("inf")
    pval = 2.0 * _norm_sf(abs(z)) if math.isfinite(z) else 0.0
    return float(ac1), var, float(z), float(pval)


def per_class_suite(n):
    """The per-class 2x2 diagnostics of the original extension --
    a, b, c, d per class, sensitivity (= producer's), specificity,
    false positive rate (commission), false negative rate
    (omission), positive predictive power (= user's), negative
    predictive power -- plus F1 per class, macro-F1, and the
    weighted overall averages from the collapsed confusion matrix
    (manual pp. 15-18). Returns a dict of arrays + scalars."""
    n = np.asarray(n, dtype=np.float64)
    tot = float(n.sum())
    row = n.sum(axis=1)
    col = n.sum(axis=0)
    a = np.diag(n).astype(np.float64)
    b = row - a                          # predicted X, observed not-X
    c = col - a                          # observed X, predicted not-X
    d = tot - row - col + a
    with np.errstate(divide="ignore", invalid="ignore"):
        sens = np.where(a + c > 0, a / (a + c), np.nan)
        spec = np.where(b + d > 0, d / (b + d), np.nan)
        ppp = np.where(a + b > 0, a / (a + b), np.nan)
        npp = np.where(c + d > 0, d / (c + d), np.nan)
        f1 = np.where((ppp + sens) > 0,
                      2.0 * ppp * sens / (ppp + sens), np.nan)
        # caret's four companions
        prevalence = (a + c) / tot if tot > 0 else a * np.nan
        detection_rate = a / tot if tot > 0 else a * np.nan
        detection_prev = (a + b) / tot if tot > 0 else a * np.nan
        balanced_acc = (sens + spec) / 2.0
    macro_f1 = float(np.nanmean(f1)) if len(f1) else float("nan")
    A, B, C, D = a.sum(), b.sum(), c.sum(), d.sum()
    overall = {
        "sensitivity": A / (A + C) if A + C > 0 else float("nan"),
        "specificity": D / (B + D) if B + D > 0 else float("nan"),
        "omission": C / (A + C) if A + C > 0 else float("nan"),
        "commission": B / (B + D) if B + D > 0 else float("nan"),
        "ppp": A / (A + B) if A + B > 0 else float("nan"),
        "npp": D / (C + D) if C + D > 0 else float("nan"),
    }
    return {"a": a, "b": b, "c": c, "d": d, "sensitivity": sens,
            "specificity": spec, "fpr": 1.0 - spec,
            "fnr": 1.0 - sens, "ppp": ppp, "npp": npp, "f1": f1,
            "macro_f1": macro_f1, "overall": overall,
            "prevalence": prevalence,
            "detection_rate": detection_rate,
            "detection_prevalence": detection_prev,
            "balanced_accuracy": balanced_acc}


# ---------------------------------------------------------------------------
#  comparisons and sample size
# ---------------------------------------------------------------------------
def compare_kappas(pairs):
    """Pairwise Z tests and the Fleiss (1981:222) common-kappa
    chi-square test for a list of (kappa, variance) pairs.
    Returns a dict: z_single (per analysis, vs 0), pairwise
    [(i, j, z, p)], common_kappa, chi2, df, p_equal."""
    ks = np.array([p[0] for p in pairs], dtype=np.float64)
    vs = np.array([p[1] for p in pairs], dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        # a zero-variance kappa of exactly 0 differs from 0 by
        # nothing: z = 0, not inf
        z_single = np.where(vs > 0, ks / np.sqrt(vs),
                            np.where(ks == 0, 0.0, np.inf))
    p_single = np.array([2.0 * _norm_sf(abs(z))
                         if math.isfinite(z)
                         else (float("nan") if math.isnan(z)
                               else 0.0)
                         for z in z_single])
    pw = []
    g = len(pairs)
    for i in range(g):
        for j in range(i + 1, g):
            sv = vs[i] + vs[j]
            if sv > 0:
                z = (ks[i] - ks[j]) / math.sqrt(sv)
            elif ks[i] == ks[j]:
                # two zero-variance analyses with IDENTICAL kappas
                # are not "significantly different" (found in
                # review: perfect-agreement pairs reported P = 0)
                z = 0.0
            else:
                z = float("inf")
            p = 2.0 * _norm_sf(abs(z)) if math.isfinite(z) \
                else (float("nan") if math.isnan(z) else 0.0)
            pw.append((i, j, float(z), float(p)))
    common = chi2 = p_eq = float("nan")
    if g >= 2 and (vs > 0).all():
        wsum = float((1.0 / vs).sum())
        common = float((ks / vs).sum() / wsum)
        chi2 = float((((ks - common) ** 2) / vs).sum())
        p_eq = _chi2_sf(chi2, g - 1)
    return {"z_single": z_single, "p_single": p_single,
            "pairwise": pw, "common_kappa": common, "chi2": chi2,
            "df": g - 1, "p_equal": p_eq}


def sample_size_tortora(n_classes, conf=0.95, precision=0.05,
                        max_prop=None, props=None, precisions=None,
                        population=None):
    """Congalton-Green / Tortora (1978) multinomial sample size:
    the chi-square critical value carries a Bonferroni share of
    alpha per class, B = chi2(1 - alpha/k, 1).
    Option 1: worst case (no proportions known) -- pass only
    n_classes; the worst class proportion 0.5 is assumed.
    Option 2: pass max_prop, the class proportion closest to 0.5.
    Option 3: pass props and per-class precisions (arrays); the
    binding class governs.
    ``population`` applies the finite population correction
    n' = n / (1 + (n - 1)/N). Returns the required n (int).
    (Reproduces the 2007 manual's published table: k = 4, 95%
    confidence, 10% precision -> 156 / 145; population 10,000 ->
    154 / 143.)"""
    alpha = 1.0 - conf
    k = int(n_classes)
    b_crit = _chi2_ppf(1.0 - alpha / k, 1)
    if props is not None and precisions is not None:
        props = np.asarray(props, dtype=np.float64)
        precisions = np.asarray(precisions, dtype=np.float64)
        if (precisions <= 0).any():
            raise ValueError(
                "Every per-class precision must be greater than 0 "
                "(a precision of 0 would require an infinite "
                "sample).")
        if ((props <= 0) | (props >= 1)).any():
            raise ValueError(
                "Every class proportion must lie strictly between "
                "0 and 1 (enter 0.25 or 25% -- not 0 or 150).")
        ns = b_crit * props * (1.0 - props) / precisions ** 2
        n = float(np.max(ns))
    else:
        p = 0.5 if max_prop is None else float(max_prop)
        p = min(max(p, 1e-9), 1.0 - 1e-9)
        n = b_crit * p * (1.0 - p) / precision ** 2
    if population:
        big_n = float(population)
        n = n / (1.0 + (n - 1.0) / big_n)
    return int(math.ceil(n))


def sample_size_olofsson(target_se, users_conj, map_props):
    """Olofsson et al. (2014) eq. 13: overall n for a target
    standard error of overall accuracy, from conjectured user's
    accuracies per stratum and the map proportions."""
    u = np.asarray(users_conj, dtype=np.float64)
    w = np.asarray(map_props, dtype=np.float64)
    s_i = np.sqrt(u * (1.0 - u))
    n = (float((w * s_i).sum()) / float(target_se)) ** 2
    return int(math.ceil(n))


def exact_binom_ci(x, n, conf=0.95):
    """Clopper-Pearson exact binomial confidence interval for a
    proportion (caret's accuracy CI). Returns (lo, hi)."""
    from scipy.stats import beta
    x, n = int(round(x)), int(round(n))
    if n <= 0:
        return float("nan"), float("nan")
    a = (1.0 - conf) / 2.0
    lo = 0.0 if x == 0 else float(beta.ppf(a, x, n - x + 1))
    hi = 1.0 if x == n else float(beta.ppf(1.0 - a, x + 1, n - x))
    return lo, hi


def nir_test(n):
    """caret's No Information Rate block: NIR = the largest
    reference-class proportion, and the one-sided exact binomial
    P(accuracy > NIR). Returns (nir, p_value)."""
    from scipy.stats import binom
    n = np.asarray(n, dtype=np.float64)
    tot = float(n.sum())
    if tot <= 0:
        return float("nan"), float("nan")
    nir = float(n.sum(axis=0).max() / tot)
    x = int(round(float(np.trace(n))))
    p = float(binom.sf(x - 1, int(round(tot)), nir))
    return nir, p


def mcnemar_test(n):
    """McNemar's test of marginal symmetry: for 2x2 the classic
    continuity-corrected form; for k x k the McNemar-Bowker test
    (both matching R's mcnemar.test defaults, which caret calls).
    Returns (chi2, df, p) -- chi2 is NaN when any off-diagonal
    pair sums to zero (R's behavior)."""
    n = np.asarray(n, dtype=np.float64)
    k = n.shape[0]
    if k == 2:
        b, c = n[0, 1], n[1, 0]
        if b + c <= 0:
            return float("nan"), 1, float("nan")
        chi2 = (abs(b - c) - 1.0) ** 2 / (b + c)
        return float(chi2), 1, _chi2_sf(float(chi2), 1)
    chi2 = 0.0
    df = k * (k - 1) // 2
    for i in range(k):
        for j in range(i + 1, k):
            s = n[i, j] + n[j, i]
            if s <= 0:
                return float("nan"), df, float("nan")
            chi2 += (n[i, j] - n[j, i]) ** 2 / s
    return float(chi2), df, _chi2_sf(float(chi2), df)


def chebyshev_floor(conf):
    """The worst-case coverage of a +-z interval when normality
    fails (Chebyshev): 1 - 1/z^2."""
    z = _norm_ppf(0.5 + conf / 2.0)
    return max(0.0, 1.0 - 1.0 / (z * z))


# ---------------------------------------------------------------------------
#  bootstrap
# ---------------------------------------------------------------------------
def bootstrap_ci(n, stat_fn, reps, conf=0.95, seed=None):
    """Percentile bootstrap CI for any scalar statistic of the count
    matrix: samples (cells) are resampled with replacement.
    Returns (lo, hi) or (nan, nan) when reps < 2."""
    n = np.asarray(n, dtype=np.float64)
    tot = int(round(float(n.sum())))
    if reps < 2 or tot < 2:
        return float("nan"), float("nan")
    k = n.shape[0]
    flat_p = (n / n.sum()).ravel()
    rng = np.random.default_rng(seed)
    vals = np.empty(int(reps))
    for r in range(int(reps)):
        counts = rng.multinomial(tot, flat_p).reshape(k, k)
        vals[r] = stat_fn(counts.astype(np.float64))
    vals = vals[np.isfinite(vals)]
    if len(vals) < 2:
        return float("nan"), float("nan")
    a = (1.0 - conf) / 2.0
    return (float(np.quantile(vals, a)),
            float(np.quantile(vals, 1.0 - a)))


# ---------------------------------------------------------------------------
#  distribution helpers (scipy when present; exact fallbacks)
# ---------------------------------------------------------------------------
def _norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _norm_ppf(q):
    from scipy.stats import norm
    return float(norm.ppf(q))


def _chi2_sf(x, df):
    from scipy.stats import chi2
    return float(chi2.sf(x, df))


def _chi2_ppf(q, df):
    from scipy.stats import chi2
    return float(chi2.ppf(q, df))


# ---------------------------------------------------------------------------
#  arcpy driver layer
# ---------------------------------------------------------------------------
def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


_LIN_M = {"Meters": 1.0, "Kilometers": 1000.0, "Feet": 0.3048,
          "Miles": 1609.344}
LINEAR_UNITS = ["Meters", "Kilometers", "Feet", "Miles"]
DESIGN_SIMPLE = "Simple random"
DESIGN_STRAT = "Stratified random (by map class)"
DESIGNS = [DESIGN_SIMPLE, DESIGN_STRAT]
WEIGHTING_NONE = "None (nominal classes)"
WEIGHTING_LINEAR = "Linear (ordinal classes)"
WEIGHTING_QUAD = "Quadratic (ordinal classes)"
WEIGHTINGS = [WEIGHTING_NONE, WEIGHTING_LINEAR, WEIGHTING_QUAD]


def _read_point_rows(in_features, fields, messages, want_xy=True):
    """[(x, y, field values...)] honoring an active selection."""
    import arcpy
    import mahalanobis as _mh

    n_total, n_sel, has_sel = _mh.selection_info(in_features,
                                                 str(in_features))
    if has_sel:
        _say(messages, "The sample layer has an active selection: "
                       "{} of {} records are used.".format(n_sel,
                                                           n_total))
    if want_xy:
        cols = ["SHAPE@X", "SHAPE@Y"] + (fields or [])
    else:
        # field/field mode never uses coordinates -- and demanding
        # SHAPE@ crashed the advertised standalone-TABLE input
        # (found in review)
        cols = list(fields or [])
    rows = []
    try:
        cur = arcpy.da.SearchCursor(in_features, cols)
    except RuntimeError:
        if want_xy:
            raise arcpy.ExecuteError(
                "The sample input has no geometry, so classes "
                "cannot be extracted from a classified layer. Give "
                "the predicted and observed values as FIELDS, or "
                "use a point feature class.")
        raise
    with cur:
        for row in cur:
            rows.append(row if want_xy
                        else (None, None) + tuple(row))
    return rows


def _extract_from_raster(points_xy, raster, radius_cs, messages):
    """Class values for each (x, y) from a classified raster -- the
    cell value, or the MAJORITY value in a circle of ``radius_cs``
    (CS units), the original extension's locational-uncertainty
    option. Majority ties break in favor of the value at the point
    itself, then the smallest value. None = NoData or outside."""
    import arcpy

    r = arcpy.Raster(str(raster))
    ext = r.extent
    cw, ch = r.meanCellWidth, r.meanCellHeight
    ncol, nrow = r.width, r.height
    nod = r.noDataValue
    rad_c = int(math.ceil(radius_cs / cw)) if radius_cs else 0
    rad_r = int(math.ceil(radius_cs / ch)) if radius_cs else 0

    # read ONLY the cell window covering the sample points (plus the
    # majority-circle margin) -- reading the whole classification to
    # score a handful of points meant multi-GB allocations on large
    # rasters (found in review)
    px = np.asarray([p[0] for p in points_xy], dtype=np.float64)
    py_ = np.asarray([p[1] for p in points_xy], dtype=np.float64)
    if len(px) == 0:
        return []
    c_lo = max(0, math.floor((px.min() - ext.XMin) / cw) - rad_c)
    c_hi = min(ncol, math.floor((px.max() - ext.XMin) / cw)
               + rad_c + 1)
    r_lo = max(0, math.floor((ext.YMax - py_.max()) / ch) - rad_r)
    r_hi = min(nrow, math.floor((ext.YMax - py_.min()) / ch)
               + rad_r + 1)
    if c_hi <= c_lo or r_hi <= r_lo:
        return [None] * len(points_xy)      # all outside the raster
    band = arcpy.RasterToNumPyArray(
        r, arcpy.Point(ext.XMin + c_lo * cw,
                       ext.YMax - r_hi * ch),
        c_hi - c_lo, r_hi - r_lo)
    if band.ndim == 3:
        band = band[0]
    out = []

    def py(v):
        return v.item() if hasattr(v, "item") else v

    for xy in points_xy:
        x, y = float(xy[0]), float(xy[1])
        # floor, not int(): int() truncates toward zero, so points
        # up to one full cell WEST of XMin / NORTH of YMax mapped to
        # index 0 and silently scored with the edge cell instead of
        # dropping as outside (found in review)
        c = math.floor((x - ext.XMin) / cw)
        rr = math.floor((ext.YMax - y) / ch)
        if not (0 <= c < ncol and 0 <= rr < nrow):
            out.append(None)
            continue
        v_at = band[rr - r_lo, c - c_lo]
        if radius_cs:
            c0, c1 = max(c_lo, c - rad_c), min(c_hi, c + rad_c + 1)
            r0, r1 = max(r_lo, rr - rad_r), min(r_hi, rr + rad_r + 1)
            win = band[r0 - r_lo:r1 - r_lo, c0 - c_lo:c1 - c_lo]
            cc = ext.XMin + (np.arange(c0, c1) + 0.5) * cw
            rc = ext.YMax - (np.arange(r0, r1) + 0.5) * ch
            dx = cc[None, :] - x
            dy = rc[:, None] - y
            m = (dx * dx + dy * dy) <= radius_cs * radius_cs
            vals = win[m]
            if nod is not None:
                vals = vals[vals != nod]
            if len(vals) == 0:
                out.append(None)
                continue
            uq, ct = np.unique(vals, return_counts=True)
            tied = uq[ct == ct.max()]
            out.append(py(v_at) if v_at in tied else py(tied.min()))
        else:
            out.append(None if (nod is not None and v_at == nod)
                       else py(v_at))
    return out


def _extract_from_polygons(points_xy, layer, class_field, radius_cs,
                           messages):
    """Class values from a polygon layer's ``class_field`` at each
    point; with a radius, the majority among 37 hex-packed sample
    locations in the circle (a sampled approximation of the
    majority-AREA rule; ties as in the raster case)."""
    import arcpy
    import patch_analysis as _pa

    polys = []
    with arcpy.da.SearchCursor(layer,
                               ["SHAPE@", class_field]) as cur:
        for g, v in cur:
            if g is None:
                continue
            gw = g
            if getattr(gw, "hasCurves", False):
                d = max(gw.length / 500.0, 1e-9)
                try:
                    gw = gw.densify("DISTANCE", d, d)
                except Exception:                                # noqa: BLE001
                    pass
            rings = _pa.rings_of(gw)
            if not rings:
                continue
            e = _pa.edges_of(rings)
            polys.append((e, _pa.y_bands(e), v,
                          (gw.extent.XMin, gw.extent.YMin,
                           gw.extent.XMax, gw.extent.YMax)))

    def value_at(px, py):
        for e, bands, v, (x0, y0, x1, y1) in polys:
            if not (x0 <= px <= x1 and y0 <= py <= y1):
                continue
            if _pa.points_inside(np.array([[px, py]]), e,
                                 bands=bands)[0]:
                return v
        return None

    offs = [(0.0, 0.0)]
    for ring_r, cnt in ((0.5, 6), (0.8, 12), (1.0, 18)):
        for t in range(cnt):
            a = 2.0 * math.pi * t / cnt
            offs.append((ring_r * math.cos(a),
                         ring_r * math.sin(a)))
    out = []
    for xy in points_xy:
        x, y = float(xy[0]), float(xy[1])
        if not radius_cs:
            out.append(value_at(x, y))
            continue
        votes = {}
        for ox, oy in offs:
            v = value_at(x + ox * radius_cs, y + oy * radius_cs)
            if v is not None:
                votes[v] = votes.get(v, 0) + 1
        if not votes:
            out.append(None)
            continue
        best = max(votes.values())
        tied = sorted([v for v, c in votes.items() if c == best],
                      key=lambda t_: (0, float(t_))
                      if _is_number(t_) else (1, str(t_)))
        v_at = value_at(x, y)
        out.append(v_at if v_at in tied else tied[0])
    return out


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _canon_label(v):
    """Canonical class label: numerically-equal values merge (a
    raster's integer 1, a float field's 1.0, and a text field's "1"
    are the SAME class -- without this they formed duplicate classes
    with an empty diagonal and kappa silently collapsed; found in
    review).  NaN canonicalizes to None (dropped like None)."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if _is_number(v):
        f = float(v)
        if math.isnan(f):
            return None
        return int(f) if f == int(f) else f
    return v


def build_matrix(pred, obs):
    """(matrix, class labels, n_dropped) from paired
    predicted/observed values; None/NaN pairs are dropped. Labels
    are canonicalized so numerically-equal codes merge. Class
    order: numeric when every label is numeric, else text."""
    pairs = [(_canon_label(p), _canon_label(o))
             for p, o in zip(pred, obs)]
    pairs = [(p, o) for p, o in pairs
             if p is not None and o is not None]
    dropped = len(pred) - len(pairs)
    vals = sorted({v for pr, ob in pairs for v in (pr, ob)},
                  key=lambda v: (0, float(v), "")
                  if _is_number(v) else (1, 0.0, str(v)))
    idx = {v: i for i, v in enumerate(vals)}
    k = len(vals)
    m = np.zeros((k, k), dtype=np.float64)
    for pr, ob in pairs:
        m[idx[pr], idx[ob]] += 1.0
    return m, vals, dropped


_AUTHALIC_R = 6371007.1810824    # sphere of equal area, WGS84


def map_proportions_from_raster(raster, labels, messages,
                                vat_field=None):
    """Map class proportions from a classified raster's cell counts
    (geographic rasters: rows weighted by cos(latitude) so
    high-latitude rows do not overcount area). With ``vat_field``
    the proportions come from the raster attribute table's Count
    summed per field value -- the path used when classes are
    reported through a VAT field such as a class-name field.

    Returns ``(props, area_m2)``; ``area_m2`` is the total mapped
    area of the ANALYZED classes (NoData and unanalyzed values
    excluded). On a geographic raster read through its attribute
    table the counts carry no latitude, so the area is ESTIMATED
    from a representative cell at the raster's central latitude
    (exact per-row weighting needs the grid itself, which the
    attribute-table path deliberately never reads)."""
    import arcpy

    if vat_field:
        counts = {}
        try:
            with arcpy.da.SearchCursor(
                    raster, ["Count", vat_field]) as cur:
                for c, lab in cur:
                    counts[lab] = counts.get(lab, 0.0) + float(c)
        except Exception:                                        # noqa: BLE001
            return None, None
        d0 = arcpy.Describe(raster)
        sr0 = d0.spatialReference
        geographic = sr0 is not None and sr0.type == "Geographic"
        tot = sum(counts.get(lb, 0.0) for lb in labels)
        if tot <= 0:
            return None, None
        area = None
        if geographic:
            try:
                ext = d0.extent
                lat_c = math.radians(
                    (float(ext.YMin) + float(ext.YMax)) / 2.0)
                area = tot * (math.radians(abs(d0.meanCellWidth))
                              * _AUTHALIC_R * math.cos(lat_c)
                              * math.radians(abs(d0.meanCellHeight))
                              * _AUTHALIC_R)
            except Exception:                                    # noqa: BLE001
                area = None
            _warn(messages,
                  "Geographic raster read through its attribute "
                  "table: cell areas shrink with latitude but the "
                  "table's counts carry no latitude, so the map "
                  "proportions use raw cell counts and the total "
                  "area is ESTIMATED from the cell area at the "
                  "raster's central latitude. Type "
                  "latitude-corrected proportions or a total map "
                  "area in the overrides to refine either.")
        else:
            try:
                mpu = float(sr0.metersPerUnit)
                area = tot * abs(d0.meanCellWidth
                                 * d0.meanCellHeight) * mpu * mpu
            except Exception:                                    # noqa: BLE001
                area = None
        return np.array([counts.get(lb, 0.0) / tot
                         for lb in labels]), area

    r = arcpy.Raster(str(raster))
    sr = r.spatialReference
    geographic = sr is not None and sr.type == "Geographic"
    ext = r.extent
    ncol, nrow = r.width, r.height
    cw, chh = r.meanCellWidth, r.meanCellHeight
    lvs = {}
    for lb in labels:
        try:
            lvs[lb] = float(lb)
        except (TypeError, ValueError):
            return None, None
    if geographic:
        cell_m2 = (math.radians(abs(cw)) * _AUTHALIC_R
                   * math.radians(abs(chh)) * _AUTHALIC_R)
    else:
        try:
            mpu = float(sr.metersPerUnit)
        except Exception:                                        # noqa: BLE001
            mpu = 1.0
        cell_m2 = abs(cw * chh) * mpu * mpu
    # stream the raster in row blocks -- reading the WHOLE grid was
    # a multi-GB single allocation on large classifications (found
    # in review)
    counts = {lb: 0.0 for lb in labels}
    block = max(1, int(4_000_000 // max(ncol, 1)))
    for r0 in range(0, nrow, block):
        nr = min(block, nrow - r0)
        arr = arcpy.RasterToNumPyArray(
            r, arcpy.Point(ext.XMin, ext.YMax - (r0 + nr) * chh),
            ncol, nr)
        if arr.ndim == 3:
            arr = arr[0]
        if geographic:
            lat = ext.YMax - (np.arange(r0, r0 + nr) + 0.5) * chh
            wrow = np.cos(np.radians(np.clip(lat, -89.9, 89.9)))
        else:
            wrow = np.ones(nr)
        for lb, lv in lvs.items():
            counts[lb] += float(((arr == lv)
                                 * wrow[:, None]).sum())
    tot = sum(counts.values())
    if tot <= 0:
        return None, None
    return (np.array([counts.get(lb, 0.0) / tot
                      for lb in labels]), tot * cell_m2)


def map_proportions_from_polygons(layer, class_field, labels,
                                  messages):
    """Map class proportions from a polygon feature class: geodesic
    areas summed per class value. Returns ``(props, area_m2)`` with
    the total limited to the ANALYZED classes, so the proportions
    and the area describe the same ground."""
    import arcpy

    areas = {}
    with arcpy.da.SearchCursor(layer,
                               ["SHAPE@", class_field]) as cur:
        for g, v in cur:
            if g is None:
                continue
            try:
                a = abs(g.getArea("GEODESIC", "SQUAREMETERS"))
            except Exception:                                    # noqa: BLE001
                a = abs(g.area)
            areas[v] = areas.get(v, 0.0) + a
    tot = sum(areas.get(lb, 0.0) for lb in labels)
    if tot <= 0:
        return None, None
    return (np.array([areas.get(lb, 0.0) / tot
                      for lb in labels]), tot)


# ---------------------------------------------------------------------------
#  report
# ---------------------------------------------------------------------------
def _fmt(v, nd=4):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "--"
    return "{:.{}f}".format(float(v), nd)


def _matrix_lines(m, labels):
    """The error matrix as aligned text (rows = predicted,
    columns = observed) with margins."""
    lab = [str(v) for v in labels]
    w = max(9, max(len(s) for s in lab) + 1)
    head = " " * (w + 2) + "".join(s.rjust(w) for s in lab) \
        + "Total".rjust(w)
    lines = ["Error matrix (rows = predicted/map, columns = "
             "observed/reference):", head]
    for i, s in enumerate(lab):
        cells = "".join("{:d}".format(int(m[i, j])).rjust(w)
                        for j in range(len(lab)))
        lines.append("  " + s.rjust(w) + cells
                     + "{:d}".format(int(m[i].sum())).rjust(w))
    tot = "".join("{:d}".format(int(m[:, j].sum())).rjust(w)
                  for j in range(len(lab)))
    lines.append("  " + "Total".rjust(w) + tot
                 + "{:d}".format(int(m.sum())).rjust(w))
    return lines


def r_snippet(m, labels, map_props=None, area_total=None,
              analyses=None):
    """A ready-to-run R validation snippet with the user's own
    matrix (and strata weights) baked in -- psych for kappa and its
    variance, caret for the per-class suite, diffeR for the Pontius
    disagreements, irrCAC for AC1, and mapaccuracy for the Olofsson
    estimators when map proportions are present. With ``analyses``
    (a list of ``(name, matrix)`` sharing ``labels``) the snippet
    loops over every analysis and finishes with the pairwise Z
    tests and the Fleiss common-kappa test, paralleling the whole
    multi-analysis report."""
    lab = ",".join('"{}"'.format(v) for v in labels)
    L = []
    L.append("# R cross-check of this analysis (install.packages")
    L.append("#  once: psych, caret, diffeR, irrCAC, mapaccuracy)")
    if not analyses or len(analyses) < 2:
        vals = ",".join(str(int(v))
                        for v in np.asarray(m).ravel())
        L.append("m <- matrix(c({}), nrow={}, byrow=TRUE,".format(
            vals, len(labels)))
        L.append("            dimnames=list(c({}), c({})))".format(
            lab, lab))
        L.append("psych::cohen.kappa(m)"
                 "        # kappa + FCE variance")
        L.append("caret::confusionMatrix(as.table(m))"
                 "  # per-class suite")
        L.append("diffeR::overallQtyD(as.table(m)) / sum(m)"
                 "   # quantity")
        L.append("diffeR::overallAllocD(as.table(m)) / sum(m)"
                 " # allocation")
        L.append("labs <- rownames(m)")
        L.append("pairs <- expand.grid(i=1:nrow(m), j=1:ncol(m))")
        L.append("pred <- rep(labs[pairs$i], "
                 "m[cbind(pairs$i, pairs$j)])")
        L.append("obs  <- rep(labs[pairs$j], "
                 "m[cbind(pairs$i, pairs$j)])")
        L.append("irrCAC::gwet.ac1.raw(data.frame(pred, obs))$est")
        if map_props is not None:
            wts = ",".join("{:.10g}".format(float(v))
                           for v in np.asarray(map_props))
            at = area_total if area_total else 1.0
            L.append("Nh <- c({}) * {:.10g}".format(
                wts, float(at)))
            L.append("names(Nh) <- labs")
            L.append("mapaccuracy::olofsson(r=obs, m=pred, Nh=Nh)"
                     "  # error-adjusted areas")
        return L

    L.append("labs <- c({})".format(lab))
    L.append("ms <- list()")
    for name, mi in analyses:
        vals = ",".join(str(int(v))
                        for v in np.asarray(mi).ravel())
        safe = str(name).replace('"', "'")
        L.append('ms[["{}"]] <- matrix(c({}), nrow={}, '
                 "byrow=TRUE,".format(safe, vals, len(labels)))
        L.append("            dimnames=list(labs, labs))")
    if map_props is not None:
        wts = ",".join("{:.10g}".format(float(v))
                       for v in np.asarray(map_props))
        at = area_total if area_total else 1.0
        L.append("Nh <- c({}) * {:.10g}".format(wts, float(at)))
        L.append("names(Nh) <- labs")
    L.append("for (nm in names(ms)) {")
    L.append('  cat("\\n==== Analysis", nm, "====\\n")')
    L.append("  m <- ms[[nm]]")
    L.append("  print(psych::cohen.kappa(m))"
             "     # kappa + FCE variance")
    L.append("  print(caret::confusionMatrix(as.table(m)))"
             "  # per-class suite")
    L.append("  print(diffeR::overallQtyD(as.table(m)) / sum(m))"
             "   # quantity")
    L.append("  print(diffeR::overallAllocD(as.table(m)) / sum(m))"
             " # allocation")
    L.append("  pairs <- expand.grid(i=1:nrow(m), j=1:ncol(m))")
    L.append("  pred <- rep(labs[pairs$i], "
             "m[cbind(pairs$i, pairs$j)])")
    L.append("  obs  <- rep(labs[pairs$j], "
             "m[cbind(pairs$i, pairs$j)])")
    L.append("  print(irrCAC::gwet.ac1.raw("
             "data.frame(pred, obs))$est)")
    if map_props is not None:
        L.append("  print(mapaccuracy::olofsson(r=obs, m=pred, "
                 "Nh=Nh))  # error-adjusted areas")
    L.append("}")
    L.append("# compare the analyses: pairwise Z tests and the "
             "Fleiss common kappa")
    L.append("ks <- sapply(ms, function(m) "
             "psych::cohen.kappa(m)$kappa)")
    L.append("vs <- sapply(ms, function(m) "
             "psych::cohen.kappa(m)$var.kappa)")
    L.append("for (i in 1:(length(ks) - 1)) "
             "for (j in (i + 1):length(ks)) {")
    L.append("  z <- (ks[i] - ks[j]) / sqrt(vs[i] + vs[j])")
    L.append('  cat(names(ms)[i], "vs", names(ms)[j], ": Z =",')
    L.append("      round(z, 3), \"  P =\", "
             "signif(2 * pnorm(-abs(z)), 5), \"\\n\")")
    L.append("}")
    L.append("w <- 1 / vs")
    L.append("kc <- sum(w * ks) / sum(w)")
    L.append("chi <- sum(w * (ks - kc)^2)")
    L.append('cat("Common kappa (Fleiss):", round(kc, 4),')
    L.append('    "  chi-square", round(chi, 3), "on", '
             "length(ks) - 1, \"df   P =\",")
    L.append("    signif(pchisq(chi, length(ks) - 1, "
             "lower.tail=FALSE), 5), \"\\n\")")
    return L


def analyze_matrix(m, labels, conf=0.95, weighting=None,
                   map_props=None, area_total=None,
                   design=DESIGN_STRAT, boot_reps=0, seed=None):
    """Every statistic for one error matrix, as a dict of blocks.
    ``map_props`` (proportions summing to 1) unlocks the Card and
    Olofsson weighted blocks; ``area_total`` (e.g. square meters or
    hectares) scales the error-adjusted areas."""
    res = {"m": m, "labels": labels, "conf": conf}
    res["basics"] = matrix_basics(m)
    res["kappa"] = kappa(m)
    res["conditional"] = conditional_kappa(m)
    res["suite"] = per_class_suite(m)
    res["pontius"] = pontius(m / m.sum()) if m.sum() > 0 else None
    res["ac1"] = gwet_ac1(m)
    res["chebyshev"] = chebyshev_floor(conf)
    zq = _norm_ppf(0.5 + conf / 2.0)
    res["zq"] = zq
    if weighting in (WEIGHTING_LINEAR, WEIGHTING_QUAD):
        res["wkappa"] = weighted_kappa(
            m, "quadratic" if weighting == WEIGHTING_QUAD
            else "linear")
    else:
        res["wkappa"] = None
    if map_props is not None:
        res["card"] = card(m, map_props, design)
        areas = np.asarray(map_props) * (area_total or 1.0)
        res["olofsson"] = olofsson(m, areas, conf)
        # explicit flag: real areas were given, so Area_ha columns
        # are meaningful (the old sum-not-in-(0,1) heuristic printed
        # garbage hectares whenever normalized proportions missed
        # 1.0 in binary; found in review)
        res["olofsson"]["has_area"] = area_total is not None \
            and float(area_total) > 0
        res["pontius_w"] = pontius(res["olofsson"]["p"])
    else:
        res["card"] = res["olofsson"] = res["pontius_w"] = None
    if boot_reps and int(boot_reps) >= 2:
        res["boot_kappa"] = bootstrap_ci(
            m, lambda mm: kappa(mm)[0], int(boot_reps), conf, seed)
        res["boot_oa"] = bootstrap_ci(
            m, lambda mm: matrix_basics(mm)["overall"],
            int(boot_reps), conf, seed)
    else:
        res["boot_kappa"] = res["boot_oa"] = None
    return res


def report_lines(res, title=""):
    """The formatted text report for one analyze_matrix result --
    the modern rendition of the original extension's report
    window."""
    m = res["m"]
    labels = [str(v) for v in res["labels"]]
    conf = res["conf"]
    zq = res["zq"]
    L = []
    if title:
        L += [title, "=" * len(title)]
    L += _matrix_lines(m, res["labels"])
    b = res["basics"]
    k, v, z, p = res["kappa"]
    L.append("")
    L.append("Overall statistics ({}% confidence):".format(
        int(round(conf * 100))))
    oa_lo, oa_hi = exact_binom_ci(np.trace(m), m.sum(), conf)
    L.append("  Overall accuracy: {}   ({} of {})   {}% CI "
             "(exact binomial): {} to {}".format(
                 _fmt(b["overall"]), int(np.trace(m)),
                 int(m.sum()), int(round(conf * 100)),
                 _fmt(oa_lo), _fmt(oa_hi)))
    nir, nir_p = nir_test(m)
    L.append("  No-information rate (largest reference class): "
             "{}   P(accuracy > NIR) = {}".format(
                 _fmt(nir), _fmt(nir_p, 5)
                 if nir_p >= 1e-5 else "< 0.00001"))
    mc_chi, mc_df, mc_p = mcnemar_test(m)
    L.append("  McNemar{} marginal-symmetry test: chi-square {} "
             "on {} df   P = {}".format(
                 "" if len(labels) == 2 else "-Bowker",
                 _fmt(mc_chi, 3), mc_df,
                 _fmt(mc_p, 5) if (math.isfinite(mc_p)
                                   and mc_p >= 1e-5)
                 else ("< 0.00001" if math.isfinite(mc_p)
                       else "--")))
    ci = zq * math.sqrt(v) if math.isfinite(v) else float("nan")
    L.append("  Cohen's Kappa: {}   variance {}   Z {}   "
             "P {}".format(_fmt(k), _fmt(v, 6), _fmt(z, 3),
                           _fmt(p, 5)))
    L.append("    {}% CI: {} to {}   (worst-case coverage if "
             "non-normal, Chebyshev: {}%)".format(
                 int(round(conf * 100)), _fmt(k - ci),
                 _fmt(k + ci),
                 int(round(res["chebyshev"] * 100))))
    if res["wkappa"] is not None:
        kw, vw, zw, pw = res["wkappa"]
        L.append("  Weighted Kappa (ordinal): {}   variance {}   "
                 "Z {}   P {}".format(_fmt(kw), _fmt(vw, 6),
                                      _fmt(zw, 3), _fmt(pw, 5)))
    ac1, av, azs, ap = res["ac1"]
    L.append("  Gwet's AC1: {}   SE {}   (robust to the kappa "
             "prevalence paradox)".format(_fmt(ac1),
                                          _fmt(math.sqrt(av), 4)
                                          if av >= 0 else "--"))
    if res["pontius"] is not None:
        po = res["pontius"]
        L.append("  Pontius-Millones disagreement (sample "
                 "proportions): quantity {}   allocation {}   "
                 "total {}".format(_fmt(po["quantity"]),
                                   _fmt(po["allocation"]),
                                   _fmt(po["disagreement"])))
    if res["boot_kappa"] is not None:
        L.append("  Bootstrap {}% CIs: kappa {} to {};   overall "
                 "accuracy {} to {}".format(
                     int(round(conf * 100)),
                     _fmt(res["boot_kappa"][0]),
                     _fmt(res["boot_kappa"][1]),
                     _fmt(res["boot_oa"][0]),
                     _fmt(res["boot_oa"][1])))
    # per-class table
    s = res["suite"]
    ki, vi = res["conditional"]
    L.append("")
    L.append("Per-class statistics:")
    hdr = ("  {:<14s}{:>7s}{:>7s}{:>9s}{:>9s}{:>9s}{:>9s}{:>9s}"
           "{:>9s}{:>9s}{:>9s}{:>9s}{:>9s}").format(
        "Class", "nPred", "nObs", "UserAcc", "ProdAcc", "CondK",
        "Sens", "Spec", "PPP", "NPP", "F1", "Preval", "BalAcc")
    L.append(hdr)
    for i, lb in enumerate(labels):
        L.append(("  {:<14s}{:>7d}{:>7d}{:>9s}{:>9s}{:>9s}{:>9s}"
                  "{:>9s}{:>9s}{:>9s}{:>9s}{:>9s}{:>9s}").format(
            lb[:14], int(b["row_tot"][i]), int(b["col_tot"][i]),
            _fmt(b["users"][i]), _fmt(b["producers"][i]),
            _fmt(ki[i]), _fmt(s["sensitivity"][i]),
            _fmt(s["specificity"][i]), _fmt(s["ppp"][i]),
            _fmt(s["npp"][i]), _fmt(s["f1"][i]),
            _fmt(s["prevalence"][i]),
            _fmt(s["balanced_accuracy"][i])))
    L.append("  Macro-F1: {}".format(_fmt(s["macro_f1"])))
    ov = s["overall"]
    L.append("  Weighted averages: sensitivity {}  specificity {}"
             "  omission {}  commission {}".format(
                 _fmt(ov["sensitivity"]), _fmt(ov["specificity"]),
                 _fmt(ov["omission"]), _fmt(ov["commission"])))
    if len(labels) == 2:
        L.append("  NOTE (2x2 presence/absence): the presence "
                 "class's row above carries the model's "
                 "sensitivity/specificity story; weighted-average "
                 "sensitivity and specificity are equal by "
                 "construction (manual p. 18).")
    if res["olofsson"] is not None:
        o = res["olofsson"]
        L.append("")
        L.append("Area-weighted estimates (Olofsson et al. 2014; "
                 "map proportions as strata weights):")
        L.append("  Overall accuracy: {}   SE {}   {}% CI {} to "
                 "{}".format(
                     _fmt(o["overall"]), _fmt(o["se_overall"]),
                     int(round(conf * 100)),
                     _fmt(o["overall"] - zq * o["se_overall"]),
                     _fmt(o["overall"] + zq * o["se_overall"])))
        has_area = o.get("has_area",
                         float(np.asarray(o["area"]).sum())
                         not in (0.0, 1.0))
        hdr = ("  {:<14s}{:>9s}{:>9s}{:>9s}{:>9s}{:>13s}{:>13s}"
               ).format("Class", "UserAcc", "SE", "ProdAcc",
                        "SE", "AreaProp", "SE")
        if has_area:
            hdr += "{:>14s}{:>12s}".format("Area_ha", "SE")
        L.append(hdr)
        for i, lb in enumerate(labels):
            row = ("  {:<14s}{:>9s}{:>9s}{:>9s}{:>9s}{:>13s}"
                   "{:>13s}").format(
                lb[:14], _fmt(o["users"][i]),
                _fmt(o["se_users"][i]), _fmt(o["producers"][i]),
                _fmt(o["se_producers"][i]),
                _fmt(o["area_prop"][i], 6),
                _fmt(o["se_area_prop"][i], 6))
            if has_area:
                row += "{:>14s}{:>12s}".format(
                    _fmt(o["area"][i] / 10000.0, 1),
                    _fmt(o["se_area"][i] / 10000.0, 1))
            L.append(row)
        if res["pontius_w"] is not None:
            pw = res["pontius_w"]
            L.append("  Pontius-Millones (area-weighted): quantity "
                     "{}   allocation {}   total {}".format(
                         _fmt(pw["quantity"]),
                         _fmt(pw["allocation"]),
                         _fmt(pw["disagreement"])))
    return L


# ---------------------------------------------------------------------------
#  tool drivers
# ---------------------------------------------------------------------------
def run_classification_accuracy(
        in_features, predicted_field=None, predicted_layer=None,
        predicted_class_field=None, observed_field=None,
        observed_layer=None, observed_class_field=None,
        analysis_field=None, circle_radius=None,
        linear_units="Meters", weight_by_map=False,
        design=DESIGN_STRAT, map_area_ha=None,
        map_props_override=None, weighting=WEIGHTING_NONE,
        conf=0.95, boot_reps=0, random_seed=None, out_report=None,
        out_matrix_table=None, out_class_table=None,
        messages=None):
    """The main Classification Accuracy analysis. Predicted and
    observed values each come from a FIELD of ``in_features`` or
    are extracted from a classified LAYER (raster, or polygons with
    ``*_class_field``); ``analysis_field`` splits the samples into
    several analyses that are compared automatically. Returns the
    list of report lines (also emitted to ``messages`` and
    ``out_report``)."""
    import arcpy
    import mahalanobis as _mh

    if (predicted_field is None) == (predicted_layer is None):
        raise arcpy.ExecuteError(
            "Give the predicted (map) values as EITHER a field or "
            "a classified layer.")
    if (observed_field is None) == (observed_layer is None):
        raise arcpy.ExecuteError(
            "Give the observed (reference) values as EITHER a "
            "field or a classified layer.")
    if linear_units not in _LIN_M:
        raise arcpy.ExecuteError(
            "Unknown distance unit: {}".format(linear_units))
    conf = float(conf)
    if not 0.5 < conf < 1.0:
        raise arcpy.ExecuteError(
            "Give a confidence level between 50 and 100 percent "
            "(exclusive).")

    _mh.begin_phases([
        ("Reading the samples...", 2),
        ("Building the error matrices...", 3),
        ("Writing the report...", 1),
    ])
    fields = []
    for f in (predicted_field, observed_field, analysis_field):
        if f:
            fields.append(f)
    rows = _read_point_rows(
        in_features, fields, messages,
        want_xy=(predicted_layer is not None
                 or observed_layer is not None))
    if not rows:
        raise arcpy.ExecuteError(
            "The sample input contains no records (empty dataset, "
            "or an active selection selecting nothing).")
    fi = {f: 2 + i for i, f in enumerate(fields)}
    radius_m = float(circle_radius) * _LIN_M[linear_units] \
        if circle_radius not in (None, "", 0) else None
    desc = arcpy.Describe(in_features)
    in_sr = getattr(desc, "spatialReference", None)

    def extracted(layer, class_field, what):
        import random_points as _rp
        lsr = arcpy.Describe(layer).spatialReference
        try:
            _t, _s, _hs = _mh.selection_info(layer, str(layer))
            if _hs:
                _warn(messages,
                      "The {} classification layer has an ACTIVE "
                      "SELECTION ({} of {} features): classes "
                      "extract, and map proportions compute, from "
                      "the selected features only.".format(
                          what, _s, _t))
        except Exception:                                # noqa: BLE001
            pass
        xy = np.array([[r[0], r[1]] for r in rows],
                      dtype=np.float64)
        if in_sr is not None and lsr is not None \
                and not _same_sr(in_sr, lsr):
            # resolve the datum shift explicitly -- a bare projectAs
            # silently skips it (NAD27 samples on a NAD83 map land
            # ~100 m off; found in review)
            xy = _rp._project_xy(
                xy, in_sr, lsr,
                transformation=_rp._geo_transformation(in_sr, lsr))
        rad_cs = None
        if radius_m:
            if lsr is not None and lsr.type == "Geographic":
                rad_cs = radius_m / 111320.0
            else:
                try:
                    mpu = float(getattr(lsr, "metersPerUnit", 1.0)
                                or 1.0)
                except Exception:                                # noqa: BLE001
                    mpu = 1.0
                rad_cs = radius_m / mpu
        d = arcpy.Describe(layer)
        if getattr(d, "shapeType", "") == "Polygon":
            if not class_field:
                raise arcpy.ExecuteError(
                    "Give the class field of the {} polygon "
                    "layer.".format(what))
            return _extract_from_polygons(xy, layer, class_field,
                                          rad_cs, messages)
        vals = _extract_from_raster(xy, layer, rad_cs, messages)
        if class_field:
            # the original extension's "Data Field" column: report
            # the raster's classes via a raster-attribute-table
            # field (e.g. a class-name field) instead of the raw
            # cell Value -- essential when the reference field
            # holds names while cells hold codes
            lut = {}
            try:
                with arcpy.da.SearchCursor(
                        layer, ["Value", class_field]) as cur:
                    for v, lab in cur:
                        lut[v] = lab
            except Exception:                                    # noqa: BLE001
                raise arcpy.ExecuteError(
                    "The {} raster has no attribute-table field "
                    "'{}' (build the raster attribute table, or "
                    "leave the class field blank to use the cell "
                    "values).".format(what, class_field))
            missing = sorted({v for v in vals
                              if v is not None and v not in lut})
            if missing:
                _warn(messages,
                      "{} raster: {} cell value{} not in the "
                      "attribute table ({}) -- kept as raw "
                      "values.".format(
                          what, len(missing),
                          "" if len(missing) == 1 else "s",
                          ", ".join(str(m) for m in missing[:6])))
            vals = [lut.get(v, v) if v is not None else None
                    for v in vals]
        return vals

    if predicted_field:
        pred_all = [r[fi[predicted_field]] for r in rows]
    else:
        pred_all = extracted(predicted_layer,
                             predicted_class_field, "predicted")
    if observed_field:
        obs_all = [r[fi[observed_field]] for r in rows]
    else:
        obs_all = extracted(observed_layer, observed_class_field,
                            "observed")
    if radius_m and predicted_field and observed_field:
        _warn(messages,
              "The circle radius only applies to values extracted "
              "from a layer; both sources are fields, so it was "
              "ignored.")
    _mh.phase_advance()

    groups = {}
    order = []
    if analysis_field:
        for i, r in enumerate(rows):
            key = r[fi[analysis_field]]
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(i)
    else:
        groups[None] = list(range(len(rows)))
        order = [None]

    all_lines = []
    results = []
    _area_said = set()
    for key in order:
        idx = groups[key]
        pred = [pred_all[i] for i in idx]
        obs = [obs_all[i] for i in idx]
        m, labels, dropped = build_matrix(pred, obs)
        title = "Analysis: {}".format(key) if analysis_field \
            else "Classification accuracy analysis"
        if len(labels) < 2:
            _warn(messages,
                  "{}: fewer than two classes among the paired "
                  "samples -- skipped.".format(title))
            continue
        if dropped:
            _warn(messages,
                  "{}: {} sample{} dropped (NoData, outside the "
                  "layer, or null values).".format(
                      title, dropped, "" if dropped == 1 else "s"))
        low = {}
        for lb in labels:
            low.setdefault(str(lb).casefold(), []).append(lb)
        cased = [g for g in low.values() if len(g) > 1]
        if cased:
            _warn(messages,
                  "{}: class labels differing only by case are "
                  "treated as DIFFERENT classes ({}) -- check the "
                  "field values if that is not intended.".format(
                      title,
                      " vs ".join(repr(str(v)) for v in cased[0])))
        if len(labels) > 1 and float(np.trace(m)) == 0.0:
            _warn(messages,
                  "{}: the predicted and observed values NEVER "
                  "agree (an empty matrix diagonal -- kappa will "
                  "be near zero). Check that both sources use the "
                  "same class codes or names.".format(title))
        props = None
        area_total_m2 = None
        if weight_by_map:
            if map_props_override:
                lut = {str(lb): float(v)
                       for lb, v in map_props_override}
                labset = {str(lb) for lb in labels}
                extra = sorted(k2 for k2 in lut
                               if k2 not in labset)
                zeroed = sorted(str(lb) for lb in labels
                                if str(lb) not in lut)
                if extra or zeroed:
                    _warn(messages,
                          "{}: the map-proportion override does "
                          "not fully match the matrix classes"
                          "{}{} -- unmatched classes get weight 0, "
                          "which biases the area-weighted "
                          "estimators. Label matching is exact "
                          "(case and spelling).".format(
                              title,
                              " (override rows with no matching "
                              "class: {})".format(
                                  ", ".join(extra[:6]))
                              if extra else "",
                              " (classes given weight 0: {})"
                              .format(", ".join(zeroed[:6]))
                              if zeroed else ""))
                vals = np.array([lut.get(str(lb), 0.0)
                                 for lb in labels])
                if vals.sum() > 0:
                    props = vals / vals.sum()
            elif predicted_layer is not None:
                d = arcpy.Describe(predicted_layer)
                if getattr(d, "shapeType", "") == "Polygon":
                    props, area_total_m2 = \
                        map_proportions_from_polygons(
                            predicted_layer, predicted_class_field,
                            labels, messages)
                else:
                    props, area_total_m2 = \
                        map_proportions_from_raster(
                            predicted_layer, labels, messages,
                            vat_field=predicted_class_field)
                if (area_total_m2 is not None
                        and not map_area_ha
                        and not _area_said):
                    _area_said.add(True)
                    _say(messages,
                         "Total mapped area of the analyzed "
                         "classes, measured from the "
                         "classification dataset: {:,.1f} ha".
                         format(area_total_m2 / 10000.0))
            if props is None:
                b0 = matrix_basics(m)
                props = b0["row_tot"] / b0["n_total"]
                _warn(messages,
                      "Map proportions were estimated from the "
                      "SAMPLE's predicted-class proportions "
                      "(no classified layer or override given) -- "
                      "fine for proportional sampling, biased "
                      "otherwise.")
        if map_area_ha not in (None, "", 0):
            area_total_m2 = float(map_area_ha) * 10000.0
        res = analyze_matrix(
            m, labels, conf=conf, weighting=weighting,
            map_props=props,
            area_total=area_total_m2, design=design,
            boot_reps=boot_reps or 0, seed=random_seed)
        res["title"] = title
        res["key"] = key
        res["area_total_m2"] = area_total_m2
        o = res.get("olofsson")
        if o is not None and props is not None:
            es = np.asarray(o.get("empty_strata", []), dtype=bool)
            if es.any():
                wlost = float(np.asarray(props)[es].sum())
                _warn(messages,
                      "{}: {} mapped class(es) have NO predicted "
                      "samples ({}), carrying {:.1%} of the map "
                      "weight -- the area-weighted (Card/Olofsson) "
                      "estimates treat that weight as zero "
                      "accuracy and are biased low. Add samples "
                      "for those classes or exclude them from the "
                      "weights.".format(
                          title, int(es.sum()),
                          ", ".join(str(labels[i])
                                    for i in np.flatnonzero(es)[:6]),
                          wlost))
        results.append(res)
        all_lines += report_lines(res, title) + [""]
    if not results:
        raise arcpy.ExecuteError("No analysis could be built.")

    if len(results) >= 2:
        pairs = [(r["kappa"][0], r["kappa"][1]) for r in results]
        cmpres = compare_kappas(pairs)
        all_lines.append("Comparisons between analyses:")
        for (i, j, z, p) in cmpres["pairwise"]:
            all_lines.append(
                "  {} vs {}: Z = {}   P = {}{}".format(
                    results[i]["key"], results[j]["key"],
                    _fmt(z, 3), _fmt(p, 5),
                    "   (significantly different)"
                    if p < 1.0 - conf else ""))
        all_lines.append(
            "  Common Kappa (Fleiss): {}   chi-square {} on {} "
            "df   P(equal) = {}".format(
                _fmt(cmpres["common_kappa"]),
                _fmt(cmpres["chi2"], 3), cmpres["df"],
                _fmt(cmpres["p_equal"], 5)))
        all_lines.append("")

    r0 = results[0]
    all_lines += r_snippet(
        r0["m"], r0["labels"],
        map_props=(r0["olofsson"]["weights"]
                   if r0["olofsson"] is not None else None),
        area_total=(float(map_area_ha) if map_area_ha
                    else (r0["area_total_m2"] / 10000.0
                          if r0.get("area_total_m2") else None)),
        analyses=([(("" if r["key"] is None else str(r["key"])),
                    r["m"]) for r in results]
                  if len(results) > 1 and all(
                      list(r["labels"]) == list(r0["labels"])
                      for r in results[1:]) else None))
    _mh.phase_advance()

    for line in all_lines:
        _say(messages, line)
    if out_report:
        out_report = str(out_report)
        if not os.path.splitext(out_report)[1]:
            out_report += ".txt"
        with open(out_report, "w", encoding="utf-8") as f:
            f.write("\n".join(all_lines) + "\n")
        _say(messages, "Report written to {}".format(out_report))
    if out_matrix_table:
        _write_matrix_table(out_matrix_table, results, messages)
    if out_class_table:
        _write_class_table(out_class_table, results, conf,
                           messages)
    _mh.end_phases()
    return all_lines, results


def _same_sr(a, b):
    try:
        return a.factoryCode == b.factoryCode \
            and a.factoryCode not in (0, None)
    except Exception:                                            # noqa: BLE001
        return False


def _make_table(path):
    import arcpy
    import os as _os

    p = str(path).replace("/", "\\").rstrip("\\")
    folder, name = _os.path.split(p)
    low = folder.lower()
    in_container = (low.endswith((".gdb", ".sde"))
                    or any(e + "\\" in low + "\\"
                           for e in (".gdb", ".sde"))
                    or low.split("\\", 1)[0] in ("memory",
                                                 "in_memory"))
    is_dbf = name.lower().endswith(".dbf") or not in_container
    if is_dbf and not name.lower().endswith(".dbf"):
        name += ".dbf"
    return arcpy.management.CreateTable(folder,
                                        name).getOutput(0), is_dbf


def _write_matrix_table(path, results, messages):
    import arcpy

    tbl, is_dbf = _make_table(path)
    arcpy.management.AddFields(tbl, [
        ["Analysis", "TEXT", "Analysis", 64, "", ""],
        ["Predicted", "TEXT", "Predicted (map) class", 64, "", ""],
        ["Observed", "TEXT", "Observed (reference) class",
         64, "", ""],
        ["Count", "LONG", "Sample count", "", "", ""]])
    with arcpy.da.InsertCursor(
            tbl, ["Analysis", "Predicted", "Observed",
                  "Count"]) as ins:
        for res in results:
            key = "" if res["key"] is None else str(res["key"])
            m = res["m"]
            for i, pl in enumerate(res["labels"]):
                for j, ol in enumerate(res["labels"]):
                    ins.insertRow((key, str(pl), str(ol),
                                   int(m[i, j])))
    _say(messages, "Error-matrix table written to {}".format(tbl))


def _write_class_table(path, results, conf, messages):
    import arcpy

    tbl, is_dbf = _make_table(path)
    nul = -999.0 if is_dbf else None

    def nz(v):
        if v is None or (isinstance(v, float)
                         and not math.isfinite(v)):
            return nul
        return float(v)

    arcpy.management.AddFields(tbl, [
        ["Analysis", "TEXT", "Analysis", 64, "", ""],
        ["Class", "TEXT", "Class", 64, "", ""],
        ["N_Pred", "LONG", "Samples predicted as this class",
         "", "", ""],
        ["N_Obs", "LONG", "Samples observed as this class",
         "", "", ""],
        ["UserAcc", "DOUBLE", "User's accuracy", "", "", ""],
        ["ProdAcc", "DOUBLE", "Producer's accuracy", "", "", ""],
        ["CondKappa", "DOUBLE", "Conditional kappa", "", "", ""],
        ["CondK_SE", "DOUBLE", "Conditional kappa SE", "", "", ""],
        ["Sens", "DOUBLE", "Sensitivity", "", "", ""],
        ["Spec", "DOUBLE", "Specificity", "", "", ""],
        ["PPP", "DOUBLE", "Positive predictive power", "", "", ""],
        ["NPP", "DOUBLE", "Negative predictive power", "", "", ""],
        ["F1", "DOUBLE", "F1 (Dice)", "", "", ""],
        ["Preval", "DOUBLE", "Prevalence (reference proportion)",
         "", "", ""],
        ["DetRate", "DOUBLE", "Detection rate", "", "", ""],
        ["DetPrev", "DOUBLE", "Detection prevalence",
         "", "", ""],
        ["BalAcc", "DOUBLE", "Balanced accuracy", "", "", ""],
        ["UA_W", "DOUBLE", "Area-weighted user's accuracy",
         "", "", ""],
        ["UA_W_SE", "DOUBLE", "Its standard error", "", "", ""],
        ["PA_W", "DOUBLE", "Area-weighted producer's accuracy",
         "", "", ""],
        ["PA_W_SE", "DOUBLE", "Its standard error", "", "", ""],
        ["AreaProp", "DOUBLE",
         "Error-adjusted area proportion (Olofsson)", "", "", ""],
        ["AreaP_SE", "DOUBLE", "Its standard error", "", "", ""],
        ["Area_ha", "DOUBLE", "Error-adjusted area (ha)",
         "", "", ""],
        ["Area_SE", "DOUBLE", "Its standard error (ha)",
         "", "", ""]])
    cols = ["Analysis", "Class", "N_Pred", "N_Obs", "UserAcc",
            "ProdAcc", "CondKappa", "CondK_SE", "Sens", "Spec",
            "PPP", "NPP", "F1", "Preval", "DetRate", "DetPrev",
            "BalAcc", "UA_W", "UA_W_SE", "PA_W",
            "PA_W_SE", "AreaProp", "AreaP_SE", "Area_ha",
            "Area_SE"]
    with arcpy.da.InsertCursor(tbl, cols) as ins:
        for res in results:
            key = "" if res["key"] is None else str(res["key"])
            b = res["basics"]
            s = res["suite"]
            ki, vi = res["conditional"]
            o = res["olofsson"]
            for i, lb in enumerate(res["labels"]):
                if o is not None:
                    ua_w, ua_se = o["users"][i], o["se_users"][i]
                    pa_w, pa_se = o["producers"][i], \
                        o["se_producers"][i]
                    ap, ap_se = o["area_prop"][i], \
                        o["se_area_prop"][i]
                    has_area = o.get(
                        "has_area",
                        float(np.asarray(o["area"]).sum())
                        not in (0.0, 1.0))
                    a_ha = o["area"][i] / 10000.0 if has_area \
                        else None
                    a_se = o["se_area"][i] / 10000.0 if has_area \
                        else None
                else:
                    ua_w = ua_se = pa_w = pa_se = None
                    ap = ap_se = a_ha = a_se = None
                ins.insertRow((
                    key, str(lb), int(b["row_tot"][i]),
                    int(b["col_tot"][i]), nz(b["users"][i]),
                    nz(b["producers"][i]), nz(ki[i]),
                    nz(math.sqrt(vi[i])
                       if np.isfinite(vi[i]) else None),
                    nz(s["sensitivity"][i]),
                    nz(s["specificity"][i]), nz(s["ppp"][i]),
                    nz(s["npp"][i]), nz(s["f1"][i]),
                    nz(s["prevalence"][i]),
                    nz(s["detection_rate"][i]),
                    nz(s["detection_prevalence"][i]),
                    nz(s["balanced_accuracy"][i]), nz(ua_w),
                    nz(ua_se), nz(pa_w), nz(pa_se), nz(ap),
                    nz(ap_se), nz(a_ha), nz(a_se)))
    _say(messages, "Per-class table written to {}".format(tbl))


def run_compare_analyses(entries, out_report=None, conf=0.95,
                         messages=None):
    """The Compare Kappa Analyses tool: ``entries`` is a list of
    (label, kappa, variance). Emits the pairwise Z tests and the
    Fleiss common-kappa chi-square, per the original extension."""
    import arcpy

    if len(entries) < 2:
        raise arcpy.ExecuteError(
            "Give at least two analyses to compare.")
    conf = float(conf)
    labels = [str(e[0]) for e in entries]
    pairs = [(float(e[1]), float(e[2])) for e in entries]
    cmpres = compare_kappas(pairs)
    L = ["Kappa comparison ({} analyses):".format(len(entries)),
         ""]
    L.append("  {:<24s}{:>10s}{:>12s}{:>9s}{:>10s}".format(
        "Analysis", "Kappa", "Variance", "Z", "P"))
    for i, (k, v) in enumerate(pairs):
        L.append("  {:<24s}{:>10s}{:>12s}{:>9s}{:>10s}".format(
            labels[i][:24], _fmt(k), _fmt(v, 6),
            _fmt(cmpres["z_single"][i], 3),
            _fmt(cmpres["p_single"][i], 5)))
    L.append("")
    L.append("Pairwise comparisons (H0: equal kappas):")
    for (i, j, z, p) in cmpres["pairwise"]:
        L.append("  {} vs {}: Z = {}   P = {}{}".format(
            labels[i], labels[j], _fmt(z, 3), _fmt(p, 5),
            "   (significantly different)" if p < 1.0 - conf
            else ""))
    L.append("")
    L.append("Common Kappa (Fleiss 1981): {}   chi-square {} on "
             "{} df   P(all equal) = {}".format(
                 _fmt(cmpres["common_kappa"]),
                 _fmt(cmpres["chi2"], 3), cmpres["df"],
                 _fmt(cmpres["p_equal"], 5)))
    for line in L:
        _say(messages, line)
    if out_report:
        out_report = str(out_report)
        if not os.path.splitext(out_report)[1]:
            out_report += ".txt"
        with open(out_report, "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
    return L, cmpres["common_kappa"], cmpres["p_equal"]


SIZE_WORST = "worst"
SIZE_MAXPROP = "maxprop"
SIZE_PERCLASS = "perclass"
SIZE_TARGETSE = "target_se"


def run_sample_size(n_classes=None, conf=0.95, precision=0.10,
                    method=SIZE_WORST, max_prop=None,
                    class_table=None, population=None,
                    target_se=None, out_report=None,
                    messages=None):
    """The Estimate Sample Size tool. ``method`` picks ONE
    estimator: the Congalton-Green / Tortora multinomial sizes --
    SIZE_WORST (no proportions known), SIZE_MAXPROP (largest class
    proportion known), SIZE_PERCLASS (per-class proportions and
    precisions from ``class_table``) -- or SIZE_TARGETSE, the
    Olofsson et al. (2014) size for a target standard error of
    overall accuracy from conjectured user's accuracies.
    ``class_table`` is a list of (label, proportion,
    precision_or_conjectured_UA); for the per-class methods the
    class count comes from the table. Returns
    ``(lines, n_recommended)`` -- the recommended n carries the
    finite population correction when ``population`` is given."""
    import arcpy

    method = str(method or SIZE_WORST)
    props = third = None
    if class_table:
        props = [float(r[1]) for r in class_table]
        third = [float(r[2]) for r in class_table]
    if method in (SIZE_PERCLASS, SIZE_TARGETSE):
        if not class_table:
            raise arcpy.ExecuteError(
                "This method needs the per-class table (one row "
                "per class).")
        n_classes = len(class_table)
    n_classes = int(n_classes or 0)
    if n_classes < 2:
        raise arcpy.ExecuteError("Give at least two classes.")

    big_n = (float(population)
             if population not in (None, "", 0) else None)
    if method == SIZE_TARGETSE:
        if target_se in (None, "", 0):
            raise arcpy.ExecuteError(
                "This method needs the target standard error.")
        L = ["Accuracy-assessment sample size ({} classes):"
             .format(n_classes)]
        n_rec = sample_size_olofsson(float(target_se), third,
                                     props)
        L.append("  Target standard error of overall accuracy "
                 "{:.3g}% ({:.4f} as a proportion), from the "
                 "conjectured user's accuracies (Olofsson et al. "
                 "2014, eq. 13): n = {}".format(
                     float(target_se) * 100.0, float(target_se),
                     n_rec))
        L.append("  Olofsson et al. also recommend allocating "
                 "50-100 samples to rare but important classes "
                 "and distributing the rest proportionally.")
    else:
        L = ["Accuracy-assessment sample size "
             "({} classes, {:.0f}% confidence):".format(
                 n_classes, conf * 100)]
        if method == SIZE_MAXPROP:
            if max_prop in (None, "", 0):
                raise arcpy.ExecuteError(
                    "This method needs the largest class "
                    "proportion.")
            n_rec = sample_size_tortora(
                n_classes, conf, precision,
                max_prop=float(max_prop))
            L.append("  Largest class proportion {:.1f}%, "
                     "precision {:.0f}%: n = {}".format(
                         float(max_prop) * 100, precision * 100,
                         n_rec))
        elif method == SIZE_PERCLASS:
            n_rec = sample_size_tortora(n_classes, conf,
                                        props=props,
                                        precisions=third)
            L.append("  Per-class proportions and precisions "
                     "(the most demanding class governs): "
                     "n = {}".format(n_rec))
        else:
            n_rec = sample_size_tortora(n_classes, conf,
                                        precision)
            L.append("  No class proportions known -- the worst "
                     "case (a class at exactly 50%) is assumed, "
                     "precision {:.0f}%: n = {}".format(
                         precision * 100, n_rec))
        if big_n is not None:
            kw = {"precision": precision}
            if method == SIZE_MAXPROP:
                kw["max_prop"] = float(max_prop)
            elif method == SIZE_PERCLASS:
                kw = {"props": props, "precisions": third}
            n_rec = sample_size_tortora(n_classes, conf,
                                        population=big_n, **kw)
            L.append("  With the finite population correction "
                     "(N = {:,.0f}): n = {}".format(big_n, n_rec))
    L.append("")
    L.append("Recommended sample size: {}".format(n_rec))
    L.append("Congalton and Green recommend a minimum of ~50 "
             "samples per class (75-100 with many classes), and "
             "never leaving a class unsampled. See the Random "
             "Point Generator and Select Random Records tools for "
             "drawing the design.")
    fmt = lambda v: "{:.10g}".format(float(v))
    L.append("")
    L.append("# R cross-check of this estimate (copy-paste "
             "into R):")
    if method == SIZE_TARGETSE:
        L.append("W <- c({})  # class proportions".format(
            ",".join(fmt(p) for p in props)))
        L.append("U <- c({})  # conjectured user's "
                 "accuracies".format(
                     ",".join(fmt(u) for u in third)))
        L.append("ceiling((sum(W * sqrt(U * (1 - U))) / {})^2)"
                 "  # -> {}".format(fmt(target_se), n_rec))
    else:
        L.append("B <- qchisq(1 - {} / {}, df = 1)"
                 "  # Bonferroni share of alpha".format(
                     fmt(1.0 - conf), n_classes))
        if method == SIZE_MAXPROP:
            p_txt = fmt(max_prop)
            L.append("n <- B * {p} * (1 - {p}) / {b}^2".format(
                p=p_txt, b=fmt(precision)))
        elif method == SIZE_PERCLASS:
            L.append("P <- c({})  # class proportions".format(
                ",".join(fmt(p) for p in props)))
            L.append("bi <- c({})  # required precisions".format(
                ",".join(fmt(b) for b in third)))
            L.append("n <- max(B * P * (1 - P) / bi^2)")
        else:
            L.append("n <- B * 0.25 / {}^2  # worst case, a "
                     "class at 50%".format(fmt(precision)))
        if big_n is not None:
            L.append("n <- n / (1 + (n - 1) / {})  # finite "
                     "population correction".format(fmt(big_n)))
        L.append("ceiling(n)  # -> {}".format(n_rec))
    for line in L:
        _say(messages, line)
    if out_report:
        out_report = str(out_report)
        if not os.path.splitext(out_report)[1]:
            out_report += ".txt"
        with open(out_report, "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
    return L, n_rec
