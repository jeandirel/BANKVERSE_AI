"""Population Stability Index (PSI) based drift monitoring.

At training time a *reference profile* is stored with the model version: quantile bin edges per
feature (from training data) and the reference bin proportions (validation data). At monitoring
time the same bins are applied to recent inference inputs. Conventional reading:
PSI < 0.1 stable · 0.1–0.25 moderate shift · > 0.25 significant shift (investigate / retrain).
"""

from __future__ import annotations

from typing import Any

import numpy as np

EPS = 1e-4


def reference_profile(train: dict[str, np.ndarray], reference: dict[str, np.ndarray], n_bins: int = 10) -> dict[str, Any]:
    prof: dict[str, Any] = {}
    for name, values in train.items():
        v = np.asarray(values, dtype=float)
        v = v[np.isfinite(v)]
        if v.size == 0:
            continue
        edges = np.unique(np.quantile(v, np.linspace(0, 1, n_bins + 1)))
        if len(edges) < 2:
            edges = np.array([v.min() - 0.5, v.max() + 0.5])
        ref = np.asarray(reference.get(name, values), dtype=float)
        prof[name] = {"edges": [float(e) for e in edges], "proportions": proportions(ref, edges).tolist()}
    return prof


def proportions(values: np.ndarray, edges: list[float] | np.ndarray) -> np.ndarray:
    e = np.asarray(edges, dtype=float)
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    idx = np.clip(np.searchsorted(e, v, side="right") - 1, 0, len(e) - 2)
    counts = np.bincount(idx, minlength=len(e) - 1).astype(float)
    total = counts.sum()
    return counts / total if total else counts


def psi(expected: np.ndarray, actual: np.ndarray) -> float:
    e = np.clip(np.asarray(expected, dtype=float), EPS, None)
    a = np.clip(np.asarray(actual, dtype=float), EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def drift_report(profile: dict[str, Any], current: dict[str, np.ndarray], min_samples: int = 200) -> dict[str, Any]:
    features = []
    for name, ref in profile.items():
        if name not in current:
            continue
        cur = np.asarray(current[name], dtype=float)
        if cur.size < min_samples:
            continue
        actual = proportions(cur, ref["edges"])
        value = psi(np.asarray(ref["proportions"]), actual)
        features.append({"feature": name, "psi": round(value, 4),
                         "status": "stable" if value < 0.1 else "moderate" if value < 0.25 else "significant",
                         "reference": [round(x, 4) for x in ref["proportions"]],
                         "current": [round(float(x), 4) for x in actual]})
    features.sort(key=lambda f: -f["psi"])
    worst = features[0]["psi"] if features else None
    return {"features": features, "max_psi": worst, "n_features": len(features),
            "status": "insufficient_data" if not features else
            ("stable" if worst < 0.1 else "moderate" if worst < 0.25 else "significant")}
