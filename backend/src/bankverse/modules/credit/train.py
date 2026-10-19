"""Credit default (PD) models on UCI Default of Credit Card Clients.

Protocol: stratified split 60/20/20 (train/validation/test, seeded). The dataset has no application
date, so temporal stability cannot be measured; the PSI between train and test score distributions is
reported as a population-stability proxy. Calibration (Platt or isotonic, chosen on validation) is
fitted on validation; the test set is used once for reporting.

Fairness: the policy model excludes sex, marital status, age and education. On the test set, the
platform reports per-group AUC, calibration (predicted vs observed default rate), and the gap in
acceptance rates / true-positive rates at the reference cut-off. A comparison model *including* the
protected attributes quantifies their (small) predictive contribution.
"""

from __future__ import annotations

import time
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import polars as pl
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import train_test_split

from bankverse.core.logging import get_logger
from bankverse.data import lake
from bankverse.db.session import control_session
from bankverse.ml import dl, drift, registry, tracking
from bankverse.ml.metrics import binary_report, ks_statistic
from bankverse.modules.credit.features import FEATURE_LABELS, FEATURES, GRADE_BOUNDS, PROTECTED, risk_grade

log = get_logger(__name__)
MODEL_NAME = "credit_default_pd"
SEED = 2005
REFERENCE_CUTOFF = 0.35  # grade E boundary: applications at or above are flagged for in-depth human review


class Platt:
    def __init__(self) -> None:
        self.lr = LogisticRegression()

    def fit(self, p: np.ndarray, y: np.ndarray) -> Platt:
        self.lr.fit(np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))[:, None], y)
        return self

    def predict(self, p: np.ndarray) -> np.ndarray:
        z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))[:, None]
        return self.lr.predict_proba(z)[:, 1]


class TorchMLP:
    """Small MLP (PyTorch) wrapped with a scikit-like interface."""

    def __init__(self, dim: int) -> None:
        self.scaler = dl.Standardizer()
        self.net = dl.MLP(dim, (64, 32), dropout=0.15)

    def fit(self, X: np.ndarray, y: np.ndarray) -> TorchMLP:
        self.scaler.fit(X)
        dl.fit_classifier(self.net, self.scaler.transform(X), y, epochs=40, batch=256, lr=1e-3, seed=SEED)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        p = dl.predict_proba(self.net, self.scaler.transform(X))
        return np.c_[1 - p, p]


def fairness_report(df: pl.DataFrame, p: np.ndarray, cutoff: float) -> dict[str, Any]:
    y = df.get_column("default").to_numpy()
    groups = {
        "sex": {1: "male", 2: "female"},
        "marriage": {1: "married", 2: "single", 3: "other"},
        "education": {1: "graduate school", 2: "university", 3: "high school", 4: "other"},
    }
    out: dict[str, Any] = {}
    ages = df.get_column("age").to_numpy()
    age_band = np.where(ages < 30, "<30", np.where(ages < 45, "30-44", np.where(ages < 60, "45-59", "60+")))
    cols = {**{k: (df.get_column(k).to_numpy(), v) for k, v in groups.items()}, "age_band": (age_band, None)}
    for attr, (values, names) in cols.items():
        rows = []
        for g in np.unique(values):
            m = values == g
            if m.sum() < 30:
                continue
            flagged = p[m] >= cutoff
            pos = y[m] == 1
            rows.append({
                "group": names.get(int(g), str(g)) if names else str(g), "n": int(m.sum()),
                "observed_default_rate": round(float(y[m].mean()), 4), "mean_predicted_pd": round(float(p[m].mean()), 4),
                "auc": round(float(roc_auc_score(y[m], p[m])), 4) if 0 < y[m].sum() < m.sum() else None,
                "review_rate_at_cutoff": round(float(flagged.mean()), 4),
                "tpr_at_cutoff": round(float(flagged[pos].mean()), 4) if pos.any() else None,
                "fpr_at_cutoff": round(float(flagged[~pos].mean()), 4) if (~pos).any() else None,
            })
        rates = [r["review_rate_at_cutoff"] for r in rows]
        tprs = [r["tpr_at_cutoff"] for r in rows if r["tpr_at_cutoff"] is not None]
        out[attr] = {"groups": rows,
                     "review_rate_gap": round(max(rates) - min(rates), 4) if rates else None,
                     "equal_opportunity_gap": round(max(tprs) - min(tprs), 4) if tprs else None,
                     "calibration_gap_max": round(max(abs(r["observed_default_rate"] - r["mean_predicted_pd"]) for r in rows), 4)}
    return out


def train(trained_by: str = "pipeline", quick: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    df = lake.read_dataset("gold", "credit_features_uci", lazy=False)
    assert isinstance(df, pl.DataFrame)
    idx = np.arange(df.height)
    y_all = df.get_column("default").to_numpy()
    tr_i, tmp_i = train_test_split(idx, test_size=0.4, stratify=y_all, random_state=SEED)
    va_i, te_i = train_test_split(tmp_i, test_size=0.5, stratify=y_all[tmp_i], random_state=SEED)
    tr, va, te = df[tr_i], df[va_i], df[te_i]
    X = {k: d.select(FEATURES).to_numpy().astype(np.float32) for k, d in (("tr", tr), ("va", va), ("te", te))}
    y = {k: d.get_column("default").to_numpy() for k, d in (("tr", tr), ("va", va), ("te", te))}
    results: dict[str, Any] = {}
    fitted: dict[str, Any] = {}

    def evaluate(name: str, model: Any, family: str, secs: float, params: dict[str, Any]) -> None:
        p_va, p_te = model.predict_proba(X["va"])[:, 1], model.predict_proba(X["te"])[:, 1]
        # calibration choice on validation (Brier), applied to test
        iso = IsotonicRegression(out_of_bounds="clip").fit(p_va, y["va"])
        platt = Platt().fit(p_va, y["va"])
        cands = {"none": p_va, "isotonic": iso.predict(p_va), "platt": platt.predict(p_va)}
        best_cal = min(cands, key=lambda k: brier_score_loss(y["va"], cands[k]))
        cal = {"none": None, "isotonic": iso, "platt": platt}[best_cal]
        p_te_cal = p_te if cal is None else cal.predict(p_te)
        p_va_cal = cands[best_cal]
        rep = binary_report(y["te"], p_te_cal, curves=True)
        rep["ks"] = ks_statistic(y["te"], p_te_cal)
        rep["uncalibrated_brier"] = round(float(brier_score_loss(y["te"], p_te)), 5)
        results[name] = {"family": family, "train_seconds": round(secs, 1), "params": params, "calibration": best_cal,
                         "val": binary_report(y["va"], p_va_cal), "test": rep,
                         "psi_train_vs_test": round(drift.psi(drift.proportions(model.predict_proba(X["tr"])[:, 1],
                                                                                 np.quantile(p_va, np.linspace(0, 1, 11))),
                                                              drift.proportions(p_te, np.quantile(p_va, np.linspace(0, 1, 11)))), 4)}
        fitted[name] = (model, cal)
        log.info("credit_model_done", model=name, val_auc=results[name]["val"]["roc_auc"], test_auc=rep["roc_auc"],
                 test_brier=rep["brier"], calibration=best_cal)

    t = time.perf_counter()
    scaler = dl.Standardizer().fit(X["tr"])

    class ScaledLR:
        def __init__(self) -> None:
            self.m = LogisticRegression(C=0.5, max_iter=2000)

        def fit(self, X_: np.ndarray, y_: np.ndarray) -> ScaledLR:
            self.m.fit(scaler.transform(X_), y_)
            return self

        def predict_proba(self, X_: np.ndarray) -> np.ndarray:
            return self.m.predict_proba(scaler.transform(X_))

    lr = ScaledLR().fit(X["tr"], y["tr"])
    evaluate("logistic_regression", lr, "linear (scorecard-like)", time.perf_counter() - t, {"C": 0.5})
    t = time.perf_counter()
    rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=20, max_features="sqrt", n_jobs=2, random_state=SEED)
    rf.fit(X["tr"], y["tr"])
    evaluate("random_forest", rf, "bagging trees", time.perf_counter() - t, {"n_estimators": 300, "min_samples_leaf": 20})
    t = time.perf_counter()
    xg = xgb.XGBClassifier(n_estimators=1500, learning_rate=0.03, max_depth=4, subsample=0.8, colsample_bytree=0.8,
                           min_child_weight=5, eval_metric="auc", early_stopping_rounds=100, n_jobs=2, random_state=SEED)
    xg.fit(X["tr"], y["tr"], eval_set=[(X["va"], y["va"])], verbose=False)
    evaluate("xgboost", xg, "gradient boosting", time.perf_counter() - t, {"max_depth": 4, "best_iteration": int(xg.best_iteration)})
    t = time.perf_counter()
    lg = lgb.LGBMClassifier(n_estimators=2000, learning_rate=0.02, num_leaves=15, min_child_samples=60, subsample=0.8,
                            subsample_freq=1, colsample_bytree=0.8, reg_lambda=2.0, random_state=SEED, n_jobs=2, verbose=-1)
    lg.fit(X["tr"], y["tr"], eval_X=(X["va"],), eval_y=(y["va"],), eval_metric="auc", callbacks=[lgb.early_stopping(150, verbose=False)])
    evaluate("lightgbm", lg, "gradient boosting", time.perf_counter() - t, {"num_leaves": 15, "best_iteration": int(lg.best_iteration_)})
    t = time.perf_counter()
    mlp = TorchMLP(len(FEATURES)).fit(X["tr"], y["tr"])
    evaluate("mlp", mlp, "neural network (PyTorch)", time.perf_counter() - t, {"hidden": [64, 32], "epochs": 40})
    t = time.perf_counter()
    cal_lr = CalibratedClassifierCV(FrozenEstimator(lr.m), method="sigmoid")
    _ = cal_lr  # sklearn-native calibration is equivalent to the Platt wrapper used above (kept for reference)

    champion = max(results, key=lambda n: results[n]["val"]["roc_auc"])
    model, cal = fitted[champion]
    p_te = model.predict_proba(X["te"])[:, 1]
    p_te = p_te if cal is None else cal.predict(p_te)
    fairness = fairness_report(te, p_te, REFERENCE_CUTOFF)
    # Contribution of protected attributes (comparison model only — never deployed)
    with_prot = [*FEATURES, *PROTECTED]
    lg_p = lgb.LGBMClassifier(**{**lg.get_params(), "n_estimators": max(int(lg.best_iteration_), 50)})
    lg_p.fit(tr.select(with_prot).to_numpy(), y["tr"])
    auc_with = float(roc_auc_score(y["te"], lg_p.predict_proba(te.select(with_prot).to_numpy())[:, 1]))
    grades = [{"grade": g, "pd_range": GRADE_BOUNDS[g], "n": int(sum(1 for v in p_te if risk_grade(v) == g)),
               "observed_default_rate": round(float(y["te"][[risk_grade(v) == g for v in p_te]].mean()), 4)
               if any(risk_grade(v) == g for v in p_te) else None} for g in GRADE_BOUNDS]
    importance: list[dict[str, Any]] = []
    if champion in ("lightgbm", "xgboost"):
        contrib = (model.booster_.predict(X["te"], pred_contrib=True) if champion == "lightgbm"
                   else model.get_booster().predict(xgb.DMatrix(X["te"]), pred_contribs=True))[:, :-1]
        importance = sorted(({"feature": f, "label": FEATURE_LABELS[f], "mean_abs_shap": round(float(v), 5)}
                             for f, v in zip(FEATURES, np.abs(contrib).mean(axis=0), strict=True)), key=lambda d: -d["mean_abs_shap"])
    comparison = sorted(({"model": n, "family": r["family"], "calibration": r["calibration"], "val_auc": r["val"]["roc_auc"],
                          "test_auc": r["test"]["roc_auc"], "test_gini": r["test"]["gini"], "test_pr_auc": r["test"]["pr_auc"],
                          "test_brier": r["test"]["brier"], "test_ece": r["test"]["ece"], "test_ks": r["test"]["ks"],
                          "psi_train_vs_test": r["psi_train_vs_test"], "train_seconds": r["train_seconds"]}
                         for n, r in results.items()), key=lambda c: -c["test_auc"])
    ref = drift.reference_profile({f: tr[f].to_numpy() for f in FEATURES}, {f: va[f].to_numpy() for f in FEATURES})
    metrics = {"champion": champion, "test": results[champion]["test"], "comparison": comparison, "fairness": fairness,
               "protected_attributes_ablation": {"test_auc_policy_model": results["lightgbm"]["test"]["roc_auc"],
                                                 "test_auc_with_protected": round(auc_with, 4)},
               "grades_test": grades, "global_importance": importance, "reference_cutoff": REFERENCE_CUTOFF,
               "split": {"train": len(tr_i), "val": len(va_i), "test": len(te_i), "stratified": True, "seed": SEED}}
    card = {
        "model": MODEL_NAME,
        "intended_use": "Estimate the probability of default (next month) of a credit-card holder to support a credit "
                        "analyst. The platform never approves or declines credit automatically.",
        "training_data": "UCI Default of Credit Card Clients (Taiwan, 2005; CC BY 4.0), 30,000 clients, 22.1 % defaults.",
        "features": "Repayment behaviour only (6-month payment status, utilisation, repayment ratios, limit).",
        "excluded_attributes": PROTECTED,
        "horizon": "1 month (the only horizon available in the data) — multi-horizon PD is not estimable from this dataset.",
        "calibration": results[champion]["calibration"],
        "test_performance": {k: results[champion]["test"][k] for k in ("roc_auc", "gini", "pr_auc", "brier", "ece", "ks")},
        "fairness_summary": {k: {kk: v[kk] for kk in ("review_rate_gap", "equal_opportunity_gap", "calibration_gap_max")}
                             for k, v in fairness.items()},
        "limitations": ["2005 Taiwanese portfolio — population, currency (NT$) and macro context differ from EU banks.",
                        "No application date: temporal stability not measurable (PSI train/test reported as a proxy).",
                        "EUR applications are converted with a fixed documented factor (1 EUR = 40 NT$).",
                        "Under the EU AI Act, creditworthiness evaluation of natural persons is a high-risk use case: "
                        "a production deployment requires the full conformity process; this prototype is decision support only."],
    }
    with control_session() as db:
        version = registry.next_version(db, MODEL_NAME)
    adir = registry.artifact_dir(MODEL_NAME, version)
    artifact = adir / "model.joblib"
    joblib.dump({"kind": champion, "model": model, "calibrator": cal, "features": FEATURES, "feature_labels": FEATURE_LABELS,
                 "reference_cutoff": REFERENCE_CUTOFF}, artifact, compress=3)
    with tracking.run("credit_default_pd", f"v{version}-{champion}", tags={"module": "credit"}) as run_id:
        tracking.log_params({"champion": champion, "seed": SEED})
        for c in comparison:
            tracking.log_metrics({k: v for k, v in c.items() if isinstance(v, float)}, prefix=f"{c['model']}.")
        tracking.log_json(metrics, "metrics.json")
        tracking.log_json(card, "model_card.json")
    gold_id = lake.current_version_id("credit_features_uci", "gold")
    with control_session(owner=True) as db:
        mv = registry.register_version(
            db, name=MODEL_NAME, module="credit", task="probability of default (1 month)",
            description="Credit-card PD model (behavioural features only) with calibration and fairness audit.",
            version=version, algorithm=f"{champion} + {results[champion]['calibration']} calibration", artifact_path=artifact,
            metrics=metrics, params=results[champion]["params"], features=FEATURES,
            dataset_versions=[str(gold_id)] if gold_id else [], reference_profile=ref, model_card=card, mlflow_run_id=run_id,
            trained_by=trained_by, training_seconds=time.perf_counter() - t0)
        mv_id = str(mv.id)
    return {"model": MODEL_NAME, "version": version, "model_version_id": mv_id, "champion": champion, "comparison": comparison,
            "protected_ablation": metrics["protected_attributes_ablation"], "seconds": round(time.perf_counter() - t0, 1)}
