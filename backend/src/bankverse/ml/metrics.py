"""Evaluation utilities shared by every supervised model (binary classification)."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)


def _r(x: float) -> float:
    return float(round(float(x), 6))


def calibration_bins(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None, n_bins: int = 10,
                     strategy: str = "quantile") -> list[dict[str, float]]:
    w = np.ones_like(p) if w is None else w
    if strategy == "quantile":
        edges = np.unique(np.quantile(p, np.linspace(0, 1, n_bins + 1)))
    else:
        edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    out = []
    for b in range(len(edges) - 1):
        m = idx == b
        if not m.any():
            continue
        ww = w[m]
        out.append({"bin": b, "p_mean": _r(np.average(p[m], weights=ww)), "y_rate": _r(np.average(y[m], weights=ww)),
                    "weight": _r(ww.sum())})
    return out


def expected_calibration_error(bins: list[dict[str, float]]) -> float:
    total = sum(b["weight"] for b in bins) or 1.0
    return _r(sum(abs(b["p_mean"] - b["y_rate"]) * b["weight"] for b in bins) / total)


def ks_statistic(y: np.ndarray, p: np.ndarray) -> float:
    fpr, tpr, _ = roc_curve(y, p)
    return _r(np.max(tpr - fpr))


def pr_curve_points(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None, n: int = 60) -> list[dict[str, float]]:
    prec, rec, thr = precision_recall_curve(y, p, sample_weight=w)
    if len(thr) == 0:
        return []
    sel = np.unique(np.linspace(0, len(thr) - 1, n).astype(int))
    return [{"threshold": _r(thr[i]), "precision": _r(prec[i]), "recall": _r(rec[i])} for i in sel]


def roc_curve_points(y: np.ndarray, p: np.ndarray, n: int = 60) -> list[dict[str, float]]:
    fpr, tpr, thr = roc_curve(y, p)
    sel = np.unique(np.linspace(0, len(thr) - 1, n).astype(int))
    return [{"fpr": _r(fpr[i]), "tpr": _r(tpr[i])} for i in sel]


def threshold_metrics(y: np.ndarray, p: np.ndarray, thr: float, w: np.ndarray | None = None) -> dict[str, Any]:
    pred = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1], sample_weight=w).ravel()
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"threshold": _r(thr), "precision": _r(precision), "recall": _r(recall), "f1": _r(f1),
            "confusion": {"tn": _r(tn), "fp": _r(fp), "fn": _r(fn), "tp": _r(tp)},
            "alert_rate": _r((tp + fp) / max(tn + fp + fn + tp, 1))}


def cost_optimal_threshold(y: np.ndarray, p: np.ndarray, amount: np.ndarray, review_cost: float,
                           w: np.ndarray | None = None) -> tuple[float, dict[str, float]]:
    """Threshold minimising expected cost = Σ_FN |amount| + review_cost × (#alerts).

    Business assumptions (documented in the model card): an alert costs one analyst review; a missed
    fraud costs the transaction amount; a reviewed fraud is assumed blocked/recovered."""
    w = np.ones_like(p) if w is None else w
    order = np.argsort(-p)
    p_s, y_s, a_s, w_s = p[order], y[order], np.abs(amount[order]), w[order]
    fraud_loss_total = float(np.sum(y_s * a_s * w_s))
    cum_alerts = np.cumsum(w_s)
    cum_caught = np.cumsum(y_s * a_s * w_s)
    costs = (fraud_loss_total - cum_caught) + review_cost * cum_alerts
    # Only consider thresholds at distinct score values.
    distinct = np.r_[p_s[1:] != p_s[:-1], True]
    cand = np.where(distinct)[0]
    best = cand[np.argmin(costs[cand])]
    best_cost = float(costs[best])
    if fraud_loss_total < best_cost:  # alerting nothing is cheaper
        return 1.01, {"cost": _r(fraud_loss_total), "no_model_cost": _r(fraud_loss_total), "savings": 0.0}
    return float(p_s[best]), {"cost": _r(best_cost), "no_model_cost": _r(fraud_loss_total),
                              "savings": _r(fraud_loss_total - best_cost)}


def cost_at_threshold(y: np.ndarray, p: np.ndarray, amount: np.ndarray, review_cost: float, thr: float,
                      w: np.ndarray | None = None) -> dict[str, float]:
    w = np.ones_like(p) if w is None else w
    alert = p >= thr
    missed = float(np.sum(np.abs(amount) * w * ((y == 1) & ~alert)))
    reviews = float(np.sum(w * alert)) * review_cost
    total_fraud = float(np.sum(np.abs(amount) * w * (y == 1)))
    return {"cost": _r(missed + reviews), "no_model_cost": _r(total_fraud), "savings": _r(total_fraud - missed - reviews),
            "missed_fraud_amount": _r(missed), "review_cost_total": _r(reviews)}


def binary_report(y: np.ndarray, p: np.ndarray, *, w: np.ndarray | None = None, threshold: float | None = None,
                  curves: bool = False) -> dict[str, Any]:
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    rep: dict[str, Any] = {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "pr_auc": _r(average_precision_score(y, p, sample_weight=w)),
        "roc_auc": _r(roc_auc_score(y, p, sample_weight=w)),
        "brier": _r(brier_score_loss(y, np.clip(p, 0, 1), sample_weight=w)),
    }
    rep["gini"] = _r(2 * rep["roc_auc"] - 1)
    bins = calibration_bins(y, np.clip(p, 0, 1), w)
    rep["ece"] = expected_calibration_error(bins)
    if threshold is not None:
        rep["at_threshold"] = threshold_metrics(y, p, threshold, w)
    if curves:
        rep["calibration"] = bins
        rep["pr_curve"] = pr_curve_points(y, p, w)
        rep["roc_curve"] = roc_curve_points(y, p)
    return rep


def precision_at_k(y: np.ndarray, p: np.ndarray, k: int) -> float:
    top = np.argsort(-p)[:k]
    return _r(np.mean(y[top]))
