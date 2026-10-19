"""Fraud model training & comparison on IBM TabFormer (temporal split, leakage-safe features).

Protocol
--------
* Splits are chronological: train 2013-2017, validation 2018, test 2019 (never used for any choice).
* Class imbalance (~0.12 % fraud): training negatives are down-sampled to 10 % with inverse-probability
  weights (=10) so that predicted probabilities stay on the original scale; validation and test sets
  are complete (no sampling) for tabular models.
* Model selection (champion, hyper-parameters, calibration, decision threshold) uses validation only.
* Sequence models (LSTM, Transformer) are evaluated on a weighted subsample (all frauds + 150k
  negatives, weight = N_neg/150k); tabular models are also reported on that same subsample so the
  comparison is apples-to-apples.
* The decision threshold minimises expected operational cost on validation:
  cost = Σ missed fraud amounts + review_cost × number of alerts.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd
import polars as pl
import xgboost as xgb
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

from bankverse.core.logging import get_logger
from bankverse.data import lake
from bankverse.db.session import control_session
from bankverse.ml import dl, drift, registry, tracking
from bankverse.ml.metrics import (
    binary_report,
    cost_at_threshold,
    cost_optimal_threshold,
    precision_at_k,
    threshold_metrics,
)
from bankverse.modules.fraud.features import (
    CATEGORICAL_FEATURES,
    FEATURE_LABELS_FR,
    FEATURES,
    NUMERIC_FEATURES,
)

log = get_logger(__name__)
MODEL_NAME = "fraud_card_transactions"
SEED = 42
NEG_RATE = 0.10
REVIEW_COST = 5.0
SEQ_FEATURES = ["log_amount", "is_refund", "hour", "is_night", "is_online", "is_swipe", "is_chip", "is_foreign",
                "has_error", "err_bad_pin", "err_insufficient_balance", "mins_since_last_tx", "merchant_new_30d",
                "mcc_new_30d", "amount_z_30d", "n_tx_24h"]
SEQ_LEN = 16


# ------------------------------------------------------------------------------------------------
# Data loading
# ------------------------------------------------------------------------------------------------
def _scan(split: str) -> pl.LazyFrame:
    return pl.scan_parquet(str(lake.layer_dir("gold", "fraud_features_tabformer") / f"split={split}" / "*.parquet"))


def load_split(split: str, neg_rate: float | None = None) -> pd.DataFrame:
    lf = _scan(split).select(["card_key", "ts", "is_fraud", *FEATURES])
    if neg_rate is not None:
        keep = (pl.struct("card_key", "ts", "amount").hash(seed=SEED) % 10_000) < int(neg_rate * 10_000)
        lf = lf.filter(pl.col("is_fraud") | keep)
    df = lf.collect()
    df = df.with_columns([pl.col(c).cast(pl.Float32) for c in NUMERIC_FEATURES])
    pdf = df.to_pandas()
    pdf["weight"] = np.where(pdf["is_fraud"], 1.0, 1.0 / neg_rate if neg_rate else 1.0).astype(np.float32)
    return pdf


class Preprocessor:
    """Tree models: raw numerics + MCC as pandas category. Linear/NN models: standardised numerics +
    one-hot of the most frequent MCC codes."""

    def __init__(self, top_mcc: int = 30, smoothing: float = 200.0) -> None:
        self.top_mcc = top_mcc
        self.smoothing = smoothing
        self.mcc_categories: list[int] = []
        self.top_codes: list[int] = []
        self.mcc_rate: dict[int, float] = {}
        self.prior = 0.0
        self.scaler = dl.Standardizer()

    def fit(self, df: pd.DataFrame) -> Preprocessor:
        self.mcc_categories = sorted(int(x) for x in df["mcc"].unique())
        self.top_codes = [int(x) for x in df["mcc"].value_counts().index[: self.top_mcc]]
        # Smoothed (m-estimate) fraud rate per MCC, computed on the *training* split only (weighted).
        w = df["weight"].to_numpy()
        y = df["is_fraud"].to_numpy().astype(float)
        self.prior = float((w * y).sum() / w.sum())
        g = pd.DataFrame({"mcc": df["mcc"].astype(int), "wy": w * y, "w": w}).groupby("mcc").sum()
        self.mcc_rate = {int(k): float((r.wy + self.prior * self.smoothing) / (r.w + self.smoothing))
                         for k, r in g.iterrows()}
        self.scaler.fit(self._dense_raw(df))
        return self

    def mcc_te(self, mcc: np.ndarray) -> np.ndarray:
        return np.array([self.mcc_rate.get(int(m), self.prior) for m in mcc], dtype=np.float32)

    def tree(self, df: pd.DataFrame) -> pd.DataFrame:
        X = df[FEATURES].copy()
        X["mcc"] = pd.Categorical(X["mcc"].astype(int), categories=self.mcc_categories)
        return X

    def _dense_raw(self, df: pd.DataFrame) -> np.ndarray:
        num = df[NUMERIC_FEATURES].to_numpy(np.float32, copy=True)
        # heavy-tailed magnitudes → signed log transform for linear/NN models
        for name in ("amount", "sum_amt_24h", "mean_amt_30d", "std_amt_30d", "max_amt_30d", "mins_since_last_tx",
                     "amount_ratio_30d"):
            j = NUMERIC_FEATURES.index(name)
            num[:, j] = np.sign(num[:, j]) * np.log1p(np.abs(num[:, j]))
        mcc = df["mcc"].to_numpy()
        onehot = np.stack([(mcc == c) for c in self.top_codes], axis=1).astype(np.float32)
        te = np.log(self.mcc_te(mcc) + 1e-6)[:, None]
        return np.hstack([num, te, onehot])

    def dense(self, df: pd.DataFrame) -> np.ndarray:
        return self.scaler.transform(self._dense_raw(df))

    def compact(self, df: pd.DataFrame) -> np.ndarray:
        """Numeric features + smoothed MCC fraud rate, no one-hot (for bagged trees: random feature
        subsets would otherwise be dominated by near-empty one-hot columns)."""
        return np.hstack([df[NUMERIC_FEATURES].to_numpy(np.float32, copy=True),
                          self.mcc_te(df["mcc"].to_numpy())[:, None]])

    @property
    def compact_names(self) -> list[str]:
        return [*NUMERIC_FEATURES, "mcc_fraud_rate"]

    @property
    def dense_names(self) -> list[str]:
        return [*NUMERIC_FEATURES, "mcc_fraud_rate_log", *[f"mcc_{c}" for c in self.top_codes]]


# ------------------------------------------------------------------------------------------------
# Model trainers
# ------------------------------------------------------------------------------------------------
def _es_subsample(y: np.ndarray, rng: np.random.Generator, rate: float = 0.15) -> tuple[np.ndarray, np.ndarray]:
    """Indices + weights of an early-stopping subsample: all positives + ``rate`` of negatives (weighted)."""
    keep = (y == 1) | (rng.random(len(y)) < rate)
    idx = np.where(keep)[0]
    w = np.where(y[idx] == 1, 1.0, 1.0 / rate).astype(np.float32)
    return idx, w


def _tune_lightgbm(Xtr: pd.DataFrame, ytr: np.ndarray, wtr: np.ndarray, Xva: pd.DataFrame, yva: np.ndarray,
                   n_trials: int) -> tuple[lgb.LGBMClassifier, dict[str, Any], list[dict[str, Any]]]:
    trials_log: list[dict[str, Any]] = []
    es_idx, es_w = _es_subsample(yva, np.random.default_rng(SEED + 1))
    Xes, yes = Xva.iloc[es_idx], yva[es_idx]

    def make(params: dict[str, Any]) -> lgb.LGBMClassifier:
        return lgb.LGBMClassifier(objective="binary", n_estimators=1200, learning_rate=params["learning_rate"],
                                  num_leaves=params["num_leaves"], min_child_samples=params["min_child_samples"],
                                  subsample=params["subsample"], subsample_freq=1,
                                  colsample_bytree=params["colsample_bytree"], reg_lambda=params["reg_lambda"],
                                  random_state=SEED, n_jobs=2, verbose=-1)

    def objective(trial: optuna.Trial) -> float:
        params = {
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.15, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 127, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 20, 400, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
        }
        m = make(params)
        m.fit(Xtr, ytr, sample_weight=wtr, eval_X=(Xes,), eval_y=(yes,), eval_sample_weight=[es_w],
              eval_metric="average_precision", callbacks=[lgb.early_stopping(50, verbose=False)])
        ap = average_precision_score(yes, m.predict_proba(Xes)[:, 1], sample_weight=es_w)
        trials_log.append({**params, "val_pr_auc": round(float(ap), 5), "best_iteration": int(m.best_iteration_)})
        trial.set_user_attr("best_iteration", int(m.best_iteration_))
        return ap

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=SEED))
    study.enqueue_trial({"learning_rate": 0.05, "num_leaves": 63, "min_child_samples": 100, "subsample": 0.8,
                         "colsample_bytree": 0.8, "reg_lambda": 1.0})
    study.optimize(objective, n_trials=n_trials)
    best = dict(study.best_params)
    best_iter = study.best_trial.user_attrs["best_iteration"]
    final = make(best)
    final.set_params(n_estimators=max(best_iter, 50))
    final.fit(Xtr, ytr, sample_weight=wtr)
    best["n_estimators"] = int(max(best_iter, 50))
    return final, best, trials_log


def _sequence_data(split: str, n_neg: int, rng: np.random.Generator, seq_scaler: dl.Standardizer | None,
                   pre: Preprocessor) -> tuple[np.ndarray, np.ndarray, np.ndarray, dl.Standardizer, pl.DataFrame]:
    """All frauds + ``n_neg`` sampled negatives of a split, as (n, SEQ_LEN, F) windows of the card history."""
    lf = _scan(split)
    if split == "train":  # bounded memory: the two most recent training years are enough for sequence models
        lf = lf.filter(pl.col("ts") >= pl.datetime(2016, 1, 1))
    df = (lf.select(["card_key", "ts", "is_fraud", "mcc", *[pl.col(c).cast(pl.Float32) for c in SEQ_FEATURES]])
          .sort(["card_key", "ts"]).collect())
    vals = np.hstack([df.select(SEQ_FEATURES).to_numpy().astype(np.float32, copy=False),
                      np.log(pre.mcc_te(df.get_column("mcc").to_numpy()) + 1e-6)[:, None]])
    vals[:, SEQ_FEATURES.index("mins_since_last_tx")] = np.log1p(vals[:, SEQ_FEATURES.index("mins_since_last_tx")])
    if seq_scaler is None:
        seq_scaler = dl.Standardizer().fit(vals[rng.choice(len(vals), min(len(vals), 500_000), replace=False)])
    vals = seq_scaler.transform(vals)
    card = df.get_column("card_key").to_numpy()
    new_group = np.r_[True, card[1:] != card[:-1]]
    group_first = np.where(new_group)[0]
    starts = group_first[np.cumsum(new_group) - 1]
    y_all = df.get_column("is_fraud").to_numpy().astype(int)
    pos = np.where(y_all == 1)[0]
    neg_all = np.where(y_all == 0)[0]
    neg = rng.choice(neg_all, size=min(n_neg, len(neg_all)), replace=False)
    targets = np.sort(np.r_[pos, neg])
    X = dl.build_sequences(vals, starts, targets, SEQ_LEN)
    y = y_all[targets]
    w = np.where(y == 1, 1.0, len(neg_all) / len(neg)).astype(np.float32)
    keys = df.select(["card_key", "ts"])[targets]
    return X, y, w, seq_scaler, keys


# ------------------------------------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------------------------------------
def train(trained_by: str = "pipeline", quick: bool = False) -> dict[str, Any]:
    import os

    smoke = os.environ.get("BANKVERSE_TRAIN_SMOKE") == "1"  # code-path check only: tiny data, nothing registered
    t_start = time.perf_counter()
    rng = np.random.default_rng(SEED)
    log.info("fraud_training_load", smoke=smoke)
    tr = load_split("train", 0.01 if smoke else NEG_RATE)
    va = load_split("val", 0.05 if smoke else None)
    te = load_split("test", 0.05 if smoke else None)
    if smoke:
        quick = True
    y_tr, w_tr = tr["is_fraud"].to_numpy().astype(int), tr["weight"].to_numpy()
    y_va, y_te = va["is_fraud"].to_numpy().astype(int), te["is_fraud"].to_numpy().astype(int)
    pre = Preprocessor().fit(tr)
    Xtr_t, Xva_t, Xte_t = pre.tree(tr), pre.tree(va), pre.tree(te)
    Xtr_d, Xva_d, Xte_d = pre.dense(tr), pre.dense(va), pre.dense(te)
    amount_va, amount_te = va["amount"].to_numpy(), te["amount"].to_numpy()
    data_summary = {
        "train": {"rows_used": len(tr), "frauds": int(y_tr.sum()), "negative_sampling_rate": NEG_RATE,
                  "effective_rows": float(w_tr.sum())},
        "val": {"rows": len(va), "frauds": int(y_va.sum())},
        "test": {"rows": len(te), "frauds": int(y_te.sum())},
    }
    results: dict[str, dict[str, Any]] = {}
    preds_va: dict[str, np.ndarray] = {}
    preds_te: dict[str, np.ndarray] = {}
    fitted: dict[str, Any] = {}

    def record(name: str, family: str, p_va: np.ndarray, p_te: np.ndarray, seconds: float, params: dict[str, Any],
               supervised: bool = True) -> None:
        preds_va[name], preds_te[name] = p_va, p_te
        results[name] = {"family": family, "supervised": supervised, "train_seconds": round(seconds, 1),
                         "params": params, "val": binary_report(y_va, p_va), "test": binary_report(y_te, p_te)}
        log.info("fraud_model_done", model=name, val_pr_auc=results[name]["val"]["pr_auc"],
                 test_pr_auc=results[name]["test"]["pr_auc"], seconds=round(seconds, 1))

    # 1. Logistic regression (baseline)
    t = time.perf_counter()
    lr = LogisticRegression(C=1.0, max_iter=600, solver="lbfgs")
    lr.fit(Xtr_d, y_tr, sample_weight=w_tr)
    record("logistic_regression", "linear baseline", lr.predict_proba(Xva_d)[:, 1], lr.predict_proba(Xte_d)[:, 1],
           time.perf_counter() - t, {"C": 1.0, "inputs": "standardised + smoothed MCC fraud rate + one-hot top-30 MCC"})
    fitted["logistic_regression"] = lr

    # 2. Random forest
    t = time.perf_counter()
    rf = RandomForestClassifier(n_estimators=20 if smoke else (60 if quick else 120), max_depth=14, min_samples_leaf=20,
                                max_features="sqrt", n_jobs=2, random_state=SEED)
    Xtr_c, Xva_c, Xte_c = pre.compact(tr), pre.compact(va), pre.compact(te)
    rf.fit(Xtr_c, y_tr, sample_weight=w_tr)
    record("random_forest", "bagging trees", rf.predict_proba(Xva_c)[:, 1], rf.predict_proba(Xte_c)[:, 1],
           time.perf_counter() - t, {"n_estimators": rf.n_estimators, "max_depth": 14, "min_samples_leaf": 20,
                                     "inputs": "numeric features + smoothed MCC fraud rate"})
    fitted["random_forest"] = rf

    # 3. XGBoost
    t = time.perf_counter()
    xg = xgb.XGBClassifier(n_estimators=1200, learning_rate=0.05, max_depth=6, subsample=0.8, colsample_bytree=0.8,
                           tree_method="hist", enable_categorical=True, eval_metric="aucpr", early_stopping_rounds=60,
                           n_jobs=2, random_state=SEED, max_cat_to_onehot=1)
    es_idx, es_w = _es_subsample(y_va, np.random.default_rng(SEED + 1))
    xg.fit(Xtr_t, y_tr, sample_weight=w_tr, eval_set=[(Xva_t.iloc[es_idx], y_va[es_idx])],
           sample_weight_eval_set=[es_w], verbose=False)
    record("xgboost", "gradient boosting", xg.predict_proba(Xva_t)[:, 1], xg.predict_proba(Xte_t)[:, 1],
           time.perf_counter() - t, {"learning_rate": 0.05, "max_depth": 6, "best_iteration": int(xg.best_iteration)})
    fitted["xgboost"] = xg

    # 4. LightGBM with Optuna tuning (validation PR-AUC)
    t = time.perf_counter()
    lgbm, best_params, trials = _tune_lightgbm(Xtr_t, y_tr, w_tr, Xva_t, y_va, n_trials=1 if smoke else (3 if quick else 6))
    record("lightgbm", "gradient boosting (tuned)", lgbm.predict_proba(Xva_t)[:, 1], lgbm.predict_proba(Xte_t)[:, 1],
           time.perf_counter() - t, {**best_params, "optuna_trials": len(trials)})
    results["lightgbm"]["tuning_trials"] = trials
    fitted["lightgbm"] = lgbm

    # 5. Isolation Forest (unsupervised, fitted on non-fraud training rows)
    t = time.perf_counter()
    normal = Xtr_d[y_tr == 0]
    iso = IsolationForest(n_estimators=200, max_samples=512, random_state=SEED, n_jobs=2)
    iso.fit(normal[rng.choice(len(normal), min(len(normal), 200_000), replace=False)])
    s_va, s_te = -iso.score_samples(Xva_d), -iso.score_samples(Xte_d)
    lo, hi = np.percentile(s_va, [0.1, 99.9])
    record("isolation_forest", "unsupervised anomaly", np.clip((s_va - lo) / (hi - lo), 0, 1),
           np.clip((s_te - lo) / (hi - lo), 0, 1), time.perf_counter() - t,
           {"n_estimators": 200, "max_samples": 512, "note": "scores rescaled to [0,1]; not probabilities"},
           supervised=False)

    # 6. Autoencoder (unsupervised reconstruction error)
    t = time.perf_counter()
    ae = dl.AutoEncoder(Xtr_d.shape[1])
    dl.fit_autoencoder(ae, normal[rng.choice(len(normal), min(len(normal), 300_000), replace=False)],
                       epochs=3 if quick else 6)
    e_va, e_te = dl.reconstruction_error(ae, Xva_d), dl.reconstruction_error(ae, Xte_d)
    lo, hi = np.percentile(e_va, [0.1, 99.9])
    record("autoencoder", "deep unsupervised", np.clip((e_va - lo) / (hi - lo), 0, 1),
           np.clip((e_te - lo) / (hi - lo), 0, 1), time.perf_counter() - t,
           {**dl.describe(ae), "note": "reconstruction error rescaled to [0,1]"}, supervised=False)

    # Free training matrices before the memory-hungry sequence stage (drift reference computed first).
    ref = drift.reference_profile({f: tr[f].to_numpy() for f in NUMERIC_FEATURES},
                                  {f: va[f].to_numpy() for f in NUMERIC_FEATURES})
    del Xtr_d, Xtr_c, Xtr_t, normal
    import gc

    gc.collect()

    # 7-8. Sequence models (LSTM, Transformer) over the card's last 16 transactions
    seq_results: dict[str, Any] = {}
    t = time.perf_counter()
    Xs_tr, ys_tr, ws_tr, seq_scaler, _ = _sequence_data("train", 40_000 if quick else 90_000, rng, None, pre)
    Xs_va, ys_va, ws_va, _, keys_va = _sequence_data("val", 150_000, rng, seq_scaler, pre)
    Xs_te, ys_te, ws_te, _, keys_te = _sequence_data("test", 150_000, rng, seq_scaler, pre)
    prep_seconds = time.perf_counter() - t
    pos_weight = float((ys_tr == 0).sum() / max((ys_tr == 1).sum(), 1))
    n_step = Xs_tr.shape[2]
    for name, net in (("lstm", dl.LSTMClassifier(n_step)),
                      ("transformer", dl.TransformerClassifier(n_step, max_len=SEQ_LEN))):
        t = time.perf_counter()
        fit = dl.fit_classifier(net, Xs_tr, ys_tr, epochs=3 if quick else 8, batch=256, lr=1e-3,
                                pos_weight=pos_weight)
        p_sva, p_ste = dl.predict_proba(net, Xs_va), dl.predict_proba(net, Xs_te)
        seq_results[name] = {
            "family": "deep sequence", "supervised": True, "train_seconds": round(fit.seconds + prep_seconds, 1),
            "params": {**dl.describe(net), "seq_len": SEQ_LEN, "epochs": fit.epochs, "train_sequences": len(ys_tr),
                       "loss_history": [round(x, 5) for x in fit.history],
                       "note": "trained with pos_weight → scores are ranking scores, not calibrated probabilities"},
            "val_subsample": binary_report(ys_va, p_sva, w=ws_va),
            "test_subsample": binary_report(ys_te, p_ste, w=ws_te),
        }
        log.info("fraud_model_done", model=name, test_pr_auc=seq_results[name]["test_subsample"]["pr_auc"])

    # Tabular models on the same weighted subsample (apples-to-apples with sequence models)
    def subsample_report(name: str, frame: pd.DataFrame, preds: np.ndarray, keys: pl.DataFrame, ys: np.ndarray,
                         ws: np.ndarray) -> dict[str, Any]:
        # duplicates (same card & minute) are possible: align through a join keeping order of keys
        lookup = pl.from_pandas(frame[["card_key", "ts"]]).with_columns(p=pl.Series(preds)).unique(
            ["card_key", "ts"], keep="first")
        aligned = keys.join(lookup, on=["card_key", "ts"], how="left", maintain_order="left").get_column("p").to_numpy()
        return binary_report(ys, np.nan_to_num(aligned), w=ws)

    for name in list(results):
        results[name]["test_subsample"] = subsample_report(name, te, preds_te[name], keys_te, ys_te, ws_te)
    results.update(seq_results)

    # Champion selection on validation PR-AUC among supervised tabular models
    candidates = ["logistic_regression", "random_forest", "xgboost", "lightgbm"]
    champion = max(candidates, key=lambda n: results[n]["val"]["pr_auc"])
    p_va_raw, p_te_raw = preds_va[champion], preds_te[champion]
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(p_va_raw, y_va)
    p_va, p_te = calibrator.predict(p_va_raw), calibrator.predict(p_te_raw)
    thr, val_cost = cost_optimal_threshold(y_va, p_va, amount_va, REVIEW_COST)
    test_cost = cost_at_threshold(y_te, p_te, amount_te, REVIEW_COST, thr)
    final_val = binary_report(y_va, p_va, threshold=thr, curves=True)
    final_test = binary_report(y_te, p_te, threshold=thr, curves=True)
    final_test["uncalibrated"] = binary_report(y_te, p_te_raw)
    days_test = max((te["ts"].max() - te["ts"].min()).days, 1)
    operational = {
        "review_cost": REVIEW_COST,
        "threshold": round(float(thr), 6),
        "validation_cost": val_cost,
        "test_cost": test_cost,
        "alerts_per_day_test": round(float((p_te >= thr).sum()) / days_test, 2),
        "precision_at_100": precision_at_k(y_te, p_te, 100),
        "precision_at_1000": precision_at_k(y_te, p_te, 1000),
        "alternative_thresholds": [threshold_metrics(y_te, p_te, t_) for t_ in (0.1, 0.3, 0.5, 0.7)],
    }
    # False-positive analysis & temporal stability on test
    te_pl = pl.from_pandas(te[["ts", "is_fraud", "is_online", "is_foreign", "mcc", "has_error", "amount"]]).with_columns(
        p=pl.Series(p_te), alert=pl.Series(p_te >= thr))
    fp = te_pl.filter(pl.col("alert") & ~pl.col("is_fraud"))
    fp_analysis = {
        "false_positives": fp.height,
        "share_online": round(float(fp.get_column("is_online").mean() or 0), 4),
        "share_foreign": round(float(fp.get_column("is_foreign").mean() or 0), 4),
        "share_with_auth_error": round(float(fp.get_column("has_error").mean() or 0), 4),
        "top_mcc": fp.group_by("mcc").len().sort("len", descending=True).head(8).to_dicts(),
        "median_amount": round(float(fp.get_column("amount").median() or 0), 2),
    }
    quarterly = []
    for q in range(1, 5):
        part = te_pl.filter(pl.col("ts").dt.quarter() == q)
        yq, pq = part.get_column("is_fraud").to_numpy().astype(int), part.get_column("p").to_numpy()
        if yq.sum() > 0:
            quarterly.append({"quarter": f"2019-Q{q}", "n": len(yq), "frauds": int(yq.sum()),
                              "pr_auc": round(float(average_precision_score(yq, pq)), 4)})

    # Explainability: native TreeSHAP contributions on a test sample
    sample_idx = rng.choice(len(Xte_t), size=min(5000, len(Xte_t)), replace=False)
    model_obj = fitted[champion]
    if champion == "lightgbm":
        contrib = model_obj.booster_.predict(Xte_t.iloc[sample_idx], pred_contrib=True)[:, :-1]
        names = FEATURES
        importance_note = "LightGBM TreeSHAP"
    elif champion == "xgboost":
        contrib = model_obj.get_booster().predict(xgb.DMatrix(Xte_t.iloc[sample_idx], enable_categorical=True),
                                                  pred_contribs=True)[:, :-1]
        names = FEATURES
        importance_note = "XGBoost TreeSHAP"
    else:
        import shap

        explainer = shap.TreeExplainer(model_obj) if champion == "random_forest" else None
        if explainer is not None:
            contrib = np.asarray(explainer.shap_values(Xte_c[sample_idx[:1000]]))[..., 1]
            names = pre.compact_names
        else:
            contrib = Xte_d[sample_idx] * lr.coef_[0]
            names = pre.dense_names
        importance_note = "dense model"
    importance = sorted(({"feature": n, "label": FEATURE_LABELS_FR.get(n, n),
                          "mean_abs_shap": round(float(v), 5)} for n, v in zip(names, np.abs(contrib).mean(axis=0),
                                                                                strict=False)),
                        key=lambda d: -d["mean_abs_shap"])

    # Drift reference profile: score distribution added to the feature bins computed earlier
    ref["__score__"] = {"edges": [float(x) for x in np.unique(np.quantile(p_va, np.linspace(0, 1, 11)))],
                        "proportions": []}
    ref["__score__"]["proportions"] = drift.proportions(p_va, ref["__score__"]["edges"]).tolist()

    comparison = []
    for name, r in results.items():
        comparison.append({
            "model": name, "family": r["family"], "supervised": r["supervised"],
            "val_pr_auc": r.get("val", {}).get("pr_auc"), "test_pr_auc": r.get("test", {}).get("pr_auc"),
            "test_roc_auc": r.get("test", {}).get("roc_auc"),
            "test_subsample_pr_auc": r["test_subsample"]["pr_auc"],
            "test_subsample_roc_auc": r["test_subsample"]["roc_auc"], "train_seconds": r["train_seconds"],
        })
    comparison.sort(key=lambda c: -(c["test_subsample_pr_auc"] or 0))

    if smoke:
        return {"smoke": True, "champion": champion, "comparison": comparison, "threshold": float(thr),
                "importance_top": importance[:5]}
    # Persist artifact + register
    with control_session() as db:
        version = registry.next_version(db, MODEL_NAME)
    adir = registry.artifact_dir(MODEL_NAME, version)
    payload = {
        "kind": champion, "model": model_obj, "calibrator": calibrator, "features": FEATURES,
        "numeric_features": NUMERIC_FEATURES, "categorical_features": CATEGORICAL_FEATURES,
        "mcc_categories": pre.mcc_categories, "preprocessor": pre,
        "threshold": float(thr), "review_cost": REVIEW_COST, "feature_labels": FEATURE_LABELS_FR,
        "bands": {"critical": max(float(thr) * 4, 0.6), "high": max(float(thr) * 2, float(thr) + 1e-6),
                  "medium": float(thr)},
    }
    artifact = adir / "model.joblib"
    joblib.dump(payload, artifact, compress=3)
    metrics = {
        "champion": champion, "val": {k: v for k, v in final_val.items()}, "test": final_test,
        "operational": operational, "comparison": comparison, "quarterly_test": quarterly,
        "false_positive_analysis": fp_analysis, "global_importance": importance[:25], "data": data_summary,
        "details": {k: {kk: vv for kk, vv in v.items() if kk != "tuning_trials"} for k, v in results.items()},
        "lightgbm_tuning": results["lightgbm"].get("tuning_trials", []),
    }
    card = model_card(champion, metrics, data_summary)
    report_path = write_report(metrics, card, version)
    gold_id = lake.current_version_id("fraud_features_tabformer", "gold")
    with tracking.run("fraud_card_transactions", f"v{version}-{champion}",
                      tags={"module": "fraud", "champion": champion}) as run_id:
        tracking.log_params({"champion": champion, "neg_rate": NEG_RATE, "review_cost": REVIEW_COST,
                             "threshold": round(float(thr), 6), "seed": SEED, **{f"lgbm_{k}": v for k, v in best_params.items()}})
        for c in comparison:
            tracking.log_metrics({k: v for k, v in c.items() if isinstance(v, float)}, prefix=f"{c['model']}.")
        tracking.log_metrics({"pr_auc": final_test["pr_auc"], "roc_auc": final_test["roc_auc"],
                              "brier": final_test["brier"], "ece": final_test["ece"]}, prefix="champion.test.")
        tracking.log_json(metrics, "metrics.json")
        tracking.log_json(card, "model_card.json")
        tracking.log_file(report_path)
    total_seconds = time.perf_counter() - t_start
    with control_session(owner=True) as db:
        mv = registry.register_version(
            db, name=MODEL_NAME, module="fraud", task="binary classification (card transaction fraud)",
            description="Card-transaction fraud scoring trained on IBM TabFormer with past-only behavioural features.",
            version=version, algorithm=f"{champion} + isotonic calibration", artifact_path=artifact, metrics=metrics,
            params={"champion_params": results[champion]["params"], "neg_rate": NEG_RATE, "seed": SEED},
            features=FEATURES, dataset_versions=[str(gold_id)] if gold_id else [], reference_profile=ref,
            model_card=card, mlflow_run_id=run_id, trained_by=trained_by, training_seconds=total_seconds,
            champion_candidate=True,
        )
        mv_id = str(mv.id)
    return {"model": MODEL_NAME, "version": version, "model_version_id": mv_id, "champion": champion,
            "test_pr_auc": final_test["pr_auc"], "test_roc_auc": final_test["roc_auc"], "threshold": round(float(thr), 4),
            "comparison": comparison, "report": str(report_path), "seconds": round(total_seconds, 1)}


def model_card(champion: str, m: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    t = m["test"]
    return {
        "model": MODEL_NAME,
        "intended_use": "Decision support for fraud analysts: prioritise card transactions for review. "
                        "Alerts are reviewed by a human; the model never blocks a card or a payment by itself.",
        "out_of_scope": ["Automatic blocking of cards or payments without human review",
                         "Use on populations or payment types absent from the training data (e.g. SEPA transfers)",
                         "Any decision with legal effect on a customer"],
        "training_data": "IBM TabFormer synthetic card transactions (Apache-2.0), 2013-2017 for training, 2018 "
                         "validation, 2019 test. Synthetic data: performance on real portfolios must be "
                         "re-established on the bank's own labelled data before any pilot.",
        "data_summary": data,
        "features": "37 behavioural features computed only from the card's strictly earlier transactions "
                    "(velocity, amount deviation, novelty of merchant/MCC/region, authorisation errors, channel). "
                    "No identifiers, no demographic attributes, no calendar year.",
        "leakage_controls": ["Chronological split; test year never used for selection",
                             "Past-only rolling windows (closed='left') and shift-based novelty flags",
                             "Same feature code used in training and online serving",
                             "Entity identifiers excluded from features"],
        "champion": champion,
        "selection_rule": "Highest validation PR-AUC among supervised tabular models; isotonic calibration and "
                          "cost-optimal threshold fitted on validation.",
        "test_performance": {k: t[k] for k in ("pr_auc", "roc_auc", "gini", "brier", "ece")},
        "operating_point": m["operational"],
        "limitations": [
            "Synthetic training data (simulated fraud patterns); real-world transfer is unproven.",
            "Fraud prevalence varies strongly by year in TabFormer (episodic fraud waves) — monitor drift.",
            "Cost model is simplified (review cost constant, recovered amount = full amount when alerted).",
            "Deep sequence models were trained on sub-samples with a bounded CPU budget.",
        ],
        "fairness": "No demographic attributes are used or available in TabFormer; disparate impact by customer "
                    "group cannot be assessed on this dataset and must be evaluated on pilot data.",
        "monitoring": "PSI drift on inputs and score distribution; analyst feedback (confirmed fraud / false "
                      "positive) tracked on alerts.",
        "regulatory_notes": "Fraud detection support tool; under the EU AI Act, systems used for detecting "
                            "financial fraud are excluded from the high-risk credit-scoring category, but the "
                            "platform applies the same governance (approval, logging, human oversight).",
    }


def write_report(m: dict[str, Any], card: dict[str, Any], version: int) -> Path:
    from bankverse.core.config import get_settings

    out = get_settings().artifacts_dir / "reports"
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# Fraud model report — {MODEL_NAME} v{version}", "",
             f"Champion: **{m['champion']}** (+ isotonic calibration). Data: IBM TabFormer, chronological split.", "",
             "## Model comparison", "",
             "| Model | Family | Val PR-AUC | Test PR-AUC | Test ROC-AUC | Test PR-AUC (weighted subsample) | Train s |",
             "|---|---|---|---|---|---|---|"]
    for c in m["comparison"]:
        lines.append(f"| {c['model']} | {c['family']} | {c['val_pr_auc'] if c['val_pr_auc'] is not None else '—'} | "
                     f"{c['test_pr_auc'] if c['test_pr_auc'] is not None else '—'} | "
                     f"{c['test_roc_auc'] if c['test_roc_auc'] is not None else '—'} | {c['test_subsample_pr_auc']} | "
                     f"{c['train_seconds']} |")
    t = m["test"]
    op = m["operational"]
    lines += ["", "## Champion on the full 2019 test set", "",
              f"- PR-AUC {t['pr_auc']} · ROC-AUC {t['roc_auc']} · Gini {t['gini']} · Brier {t['brier']} · ECE {t['ece']}",
              f"- Cost-optimal threshold (validation): {op['threshold']} with review cost {op['review_cost']}",
              f"- At threshold: precision {t['at_threshold']['precision']}, recall {t['at_threshold']['recall']}, "
              f"F1 {t['at_threshold']['f1']}, alerts/day {op['alerts_per_day_test']}",
              f"- Confusion: {t['at_threshold']['confusion']}",
              f"- Expected cost on test: {op['test_cost']}",
              f"- Precision@100 {op['precision_at_100']} · Precision@1000 {op['precision_at_1000']}", "",
              "## Temporal stability (2019 quarters)", ""]
    lines += [f"- {q['quarter']}: PR-AUC {q['pr_auc']} ({q['frauds']} frauds / {q['n']})" for q in m["quarterly_test"]]
    lines += ["", "## Top features (mean |SHAP|)", ""]
    lines += [f"- {i['label']} (`{i['feature']}`): {i['mean_abs_shap']}" for i in m["global_importance"][:12]]
    lines += ["", "## Limitations", ""] + [f"- {x}" for x in card["limitations"]]
    path = out / f"fraud_model_v{version}.md"
    path.write_text("\n".join(lines) + "\n")
    return path
