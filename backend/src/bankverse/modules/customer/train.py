"""Customer intelligence models.

1. ``customer_message_topics`` — intent/topic classifier for customer messages, trained on BANKING77
   (official train/test split, 77 intents grouped into 11 business topics) + an LSA text embedder
   (TF-IDF → truncated SVD, 256 dims) used for semantic search in pgvector.
2. ``marketing_conversion`` — term-deposit subscription propensity on UCI Bank Marketing. The call
   ``duration`` is excluded (known only after the call → target leakage, as documented by the
   dataset authors). Chronological split (rows are ordered May 2008 → Nov 2010).
"""

from __future__ import annotations

import time
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import polars as pl
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import Normalizer
from sklearn.svm import LinearSVC

from bankverse.data import lake
from bankverse.db.session import control_session
from bankverse.ml import registry, tracking
from bankverse.ml.metrics import binary_report

TOPIC_MODEL = "customer_message_topics"
CONV_MODEL = "marketing_conversion"
SEED = 77

TOPIC_GROUPS: dict[str, list[str]] = {
    "card_lost_or_stolen": ["lost_or_stolen_card", "lost_or_stolen_phone", "compromised_card", "card_swallowed"],
    "card_payment_issue": ["declined_card_payment", "card_not_working", "card_payment_not_recognised", "card_payment_wrong_exchange_rate",
                           "reverted_card_payment?", "pending_card_payment", "card_payment_fee_charged", "contactless_not_working",
                           "virtual_card_not_working", "declined_transfer"],
    "unrecognised_transaction": ["direct_debit_payment_not_recognised", "cash_withdrawal_not_recognised", "transaction_charged_twice",
                                 "extra_charge_on_statement"],
    "transfer_issue": ["transfer_not_received_by_recipient", "pending_transfer", "failed_transfer", "transfer_timing", "beneficiary_not_allowed",
                       "cancel_transfer", "transfer_into_account", "transfer_fee_charged", "receiving_money", "balance_not_updated_after_bank_transfer"],
    "top_up": ["top_up_failed", "top_up_reverted", "pending_top_up", "top_up_limits", "top_up_by_card_charge", "top_up_by_bank_transfer_charge",
               "top_up_by_cash_or_cheque", "automatic_top_up", "verify_top_up", "balance_not_updated_after_cheque_or_cash_deposit"],
    "cash_atm": ["pending_cash_withdrawal", "wrong_amount_of_cash_received", "declined_cash_withdrawal", "cash_withdrawal_charge", "atm_support",
                 "wrong_exchange_rate_for_cash_withdrawal"],
    "fees_and_exchange": ["exchange_rate", "exchange_charge", "exchange_via_app", "fiat_currency_support", "supported_cards_and_currencies",
                          "card_payment_fee_charged"],
    "refund": ["request_refund", "Refund_not_showing_up"],
    "pin_and_security": ["change_pin", "pin_blocked", "passcode_forgotten", "get_physical_card", "activate_my_card", "card_acceptance"],
    "identity_and_account": ["verify_my_identity", "unable_to_verify_identity", "why_verify_identity", "verify_source_of_funds",
                             "edit_personal_details", "terminate_account", "age_limit", "country_support", "visa_or_mastercard",
                             "get_disposable_virtual_card", "getting_virtual_card", "getting_spare_card", "order_physical_card",
                             "card_about_to_expire", "card_linking", "card_delivery_estimate", "card_arrival", "disposable_card_limits",
                             "apple_pay_or_google_pay", "top_up_limits"],
    "other": [],
}
COMPLAINT_INTENTS = {
    "declined_card_payment", "card_not_working", "card_payment_not_recognised", "card_payment_wrong_exchange_rate", "transaction_charged_twice",
    "extra_charge_on_statement", "transfer_not_received_by_recipient", "failed_transfer", "top_up_failed", "top_up_reverted",
    "wrong_amount_of_cash_received", "declined_cash_withdrawal", "cash_withdrawal_not_recognised", "direct_debit_payment_not_recognised",
    "Refund_not_showing_up", "balance_not_updated_after_bank_transfer", "balance_not_updated_after_cheque_or_cash_deposit",
    "card_swallowed", "pin_blocked", "unable_to_verify_identity", "wrong_exchange_rate_for_cash_withdrawal", "contactless_not_working",
    "virtual_card_not_working", "declined_transfer", "card_payment_fee_charged", "cash_withdrawal_charge", "transfer_fee_charged",
    "compromised_card", "lost_or_stolen_card",
}


def intent_group(intent: str) -> str:
    for g, members in TOPIC_GROUPS.items():
        if intent in members:
            return g
    return "other"


def train_topics(trained_by: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    df = lake.read_dataset("silver", "banking77", lazy=False)
    assert isinstance(df, pl.DataFrame)
    tr, te = df.filter(pl.col("split") == "train"), df.filter(pl.col("split") == "test")
    xtr, ytr = tr.get_column("text").to_list(), tr.get_column("category").to_list()
    xte, yte = te.get_column("text").to_list(), te.get_column("category").to_list()
    feats = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True, strip_accents="unicode", lowercase=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, lowercase=True)),
    ])
    results = {}
    candidates = {
        "tfidf_logreg": Pipeline([("f", feats), ("clf", LogisticRegression(C=20, max_iter=3000))]),
        "tfidf_linearsvc": Pipeline([("f", FeatureUnion(feats.transformer_list)), ("clf", LinearSVC(C=0.6))]),
    }
    fitted = {}
    for name, pipe in candidates.items():
        t = time.perf_counter()
        pipe.fit(xtr, ytr)
        pred = pipe.predict(xte)
        results[name] = {"accuracy": round(float(accuracy_score(yte, pred)), 4), "macro_f1": round(float(f1_score(yte, pred, average="macro")), 4),
                         "train_seconds": round(time.perf_counter() - t, 1)}
        fitted[name] = pipe
    best = max(results, key=lambda n: results[n]["macro_f1"])
    model = fitted["tfidf_logreg"]  # probabilistic (confidence scores needed in the UI); SVC reported for comparison
    pred = model.predict(xte)
    group_true = [intent_group(y) for y in yte]
    group_pred = [intent_group(y) for y in pred]
    report = classification_report(yte, pred, output_dict=True, zero_division=0)
    worst = sorted(((k, v["f1-score"]) for k, v in report.items() if isinstance(v, dict) and k not in ("macro avg", "weighted avg")),
                   key=lambda kv: kv[1])[:10]
    embedder = Pipeline([("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, strip_accents="unicode")),
                         ("svd", TruncatedSVD(n_components=256, random_state=SEED)), ("norm", Normalizer())])
    embedder.fit(xtr)
    metrics = {"intent": {"deployed": "tfidf_logreg", "best_macro_f1": best, "comparison": results},
               "topic_group": {"accuracy": round(float(accuracy_score(group_true, group_pred)), 4),
                               "macro_f1": round(float(f1_score(group_true, group_pred, average="macro")), 4)},
               "worst_intents": [{"intent": k, "f1": round(v, 3)} for k, v in worst],
               "data": {"train": len(xtr), "test": len(xte), "labels": len(set(ytr))}}
    card = {"model": TOPIC_MODEL, "intended_use": "Route and analyse customer messages by topic; flag likely complaints for faster handling.",
            "training_data": "BANKING77 (PolyAI, CC BY 4.0) — English online-banking queries.",
            "limitations": ["English only: French messages require a French labelled corpus (not available in this environment).",
                            "Complaint flag derived from intent semantics, not from labelled complaints.",
                            "Embeddings are LSA (TF-IDF + SVD) trained locally; multilingual neural embeddings need model weights not reachable here."]}
    with control_session() as db:
        version = registry.next_version(db, TOPIC_MODEL)
    adir = registry.artifact_dir(TOPIC_MODEL, version)
    artifact = adir / "model.joblib"
    joblib.dump({"classifier": model, "embedder": embedder, "groups": TOPIC_GROUPS, "complaint_intents": sorted(COMPLAINT_INTENTS)}, artifact, compress=3)
    with tracking.run("customer_message_topics", f"v{version}", tags={"module": "customer"}) as run_id:
        tracking.log_metrics(metrics)
    gid = lake.current_version_id("banking77", "silver")
    with control_session(owner=True) as db:
        registry.register_version(db, name=TOPIC_MODEL, module="customer", task="text classification (77 intents → 11 topics)",
                                  description="Customer message topic classifier + LSA embedder (BANKING77).", version=version,
                                  algorithm="tfidf (word+char) + logistic regression", artifact_path=artifact, metrics=metrics,
                                  params={"C": 20}, features=["text"], dataset_versions=[str(gid)] if gid else [], reference_profile={},
                                  model_card=card, mlflow_run_id=run_id, trained_by=trained_by,
                                  training_seconds=time.perf_counter() - t0, risk_tier="limited")
    return {"model": TOPIC_MODEL, "version": version, **metrics["intent"]["comparison"]["tfidf_logreg"],
            "group_accuracy": metrics["topic_group"]["accuracy"]}


# Calendar (month, weekday) and macro indicators identify the period rather than the customer: under a
# chronological split they do not transfer (base rate jumps from ~3 % to ~46 % at the end of the data), so the
# deployed model uses customer and campaign-history attributes only. Variants are reported in the metrics.
CONV_CAT = ["job", "marital", "education", "default", "housing", "loan", "contact", "poutcome"]
CONV_NUM = ["age", "campaign", "pdays", "previous", "previously_contacted"]
MACRO_AND_CALENDAR = ["month", "day_of_week", "emp_var_rate", "cons_price_idx", "cons_conf_idx", "euribor3m", "nr_employed"]


def train_conversion(trained_by: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    df = lake.read_dataset("gold", "bank_marketing", lazy=False)
    assert isinstance(df, pl.DataFrame)
    pdf = df.to_pandas()
    pdf["pdays"] = pdf["pdays"].fillna(-1)
    n = len(pdf)
    tr, va, te = pdf.iloc[: int(0.7 * n)], pdf.iloc[int(0.7 * n): int(0.85 * n)], pdf.iloc[int(0.85 * n):]

    def prep(d: pd.DataFrame) -> pd.DataFrame:
        X = d[CONV_CAT + CONV_NUM].copy()
        for c in CONV_CAT:
            X[c] = pd.Categorical(X[c], categories=sorted(pdf[c].unique()))
        return X

    Xtr, Xva, Xte = prep(tr), prep(va), prep(te)
    ytr, yva, yte = tr["subscribed"].to_numpy(), va["subscribed"].to_numpy(), te["subscribed"].to_numpy()
    lg = lgb.LGBMClassifier(n_estimators=2000, learning_rate=0.02, num_leaves=15, min_child_samples=50, subsample=0.8, subsample_freq=1,
                            colsample_bytree=0.8, random_state=SEED, n_jobs=2, verbose=-1)
    lg.fit(Xtr, ytr, eval_X=(Xva,), eval_y=(yva,), eval_metric="auc", callbacks=[lgb.early_stopping(150, verbose=False)])
    oh = pd.get_dummies(pd.concat([Xtr, Xva, Xte]), columns=CONV_CAT).astype(float)
    mu, sd = oh.iloc[: len(Xtr)].mean(), oh.iloc[: len(Xtr)].std().replace(0, 1)
    oh = (oh - mu) / sd
    lr = LogisticRegression(max_iter=3000, C=0.3).fit(oh.iloc[: len(Xtr)], ytr)
    p_lr_te = lr.predict_proba(oh.iloc[len(Xtr) + len(Xva):])[:, 1]
    p_lg_te = lg.predict_proba(Xte)[:, 1]
    p_lg_va = lg.predict_proba(Xva)[:, 1]
    p_lr_va = lr.predict_proba(oh.iloc[len(Xtr): len(Xtr) + len(Xva)])[:, 1]

    def lift(y: np.ndarray, p: np.ndarray) -> list[dict[str, float]]:
        order = np.argsort(-p)
        base = y.mean()
        out = []
        for dec in range(1, 11):
            k = int(len(y) * dec / 10)
            out.append({"decile": dec, "conversion": round(float(y[order[:k]].mean()), 4), "lift": round(float(y[order[:k]].mean() / base), 3)})
        return out

    # Reference evaluations (reported, not deployed): random stratified split with all features (optimistic, ignores drift)
    from sklearn.model_selection import train_test_split

    full_cat = [*CONV_CAT, "month", "day_of_week"]
    full_num = [*CONV_NUM, "emp_var_rate", "cons_price_idx", "cons_conf_idx", "euribor3m", "nr_employed"]
    r_tr, r_te = train_test_split(pdf, test_size=0.2, stratify=pdf["subscribed"], random_state=SEED)

    def prep_full(d: pd.DataFrame) -> pd.DataFrame:
        X = d[full_cat + full_num].copy()
        for c in full_cat:
            X[c] = pd.Categorical(X[c], categories=sorted(pdf[c].unique()))
        return X

    ref_model = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.03, num_leaves=15, min_child_samples=50, random_state=SEED,
                                   n_jobs=2, verbose=-1).fit(prep_full(r_tr), r_tr["subscribed"])
    from sklearn.metrics import roc_auc_score

    reference_random_split_auc = round(float(roc_auc_score(r_te["subscribed"], ref_model.predict_proba(prep_full(r_te))[:, 1])), 4)
    contrib = lg.booster_.predict(Xte, pred_contrib=True)[:, :-1]
    imp = sorted(({"feature": f, "mean_abs_shap": round(float(v), 5)} for f, v in zip(CONV_CAT + CONV_NUM, np.abs(contrib).mean(axis=0), strict=True)),
                 key=lambda d: -d["mean_abs_shap"])
    analysis = {}
    for col in ("month", "contact", "poutcome", "job"):
        g = df.group_by(col).agg(contacts=pl.len(), conversion=pl.col("subscribed").mean()).sort("conversion", descending=True)
        analysis[col] = [{"value": r[col], "contacts": r["contacts"], "conversion": round(r["conversion"], 4)} for r in g.to_dicts()]
    camp = (df.with_columns(bucket=pl.when(pl.col("campaign") >= 6).then(pl.lit("6+")).otherwise(pl.col("campaign").cast(pl.Utf8)))
            .group_by("bucket").agg(contacts=pl.len(), conversion=pl.col("subscribed").mean()).sort("bucket"))
    analysis["campaign_contacts"] = [{"value": r["bucket"], "contacts": r["contacts"], "conversion": round(r["conversion"], 4)} for r in camp.to_dicts()]
    metrics = {
        "split": {"train": len(tr), "val": len(va), "test": len(te), "chronological": True,
                  "base_rate": {"train": round(float(ytr.mean()), 4), "val": round(float(yva.mean()), 4), "test": round(float(yte.mean()), 4)}},
        "comparison": [
            {"model": "lightgbm", "val": binary_report(yva, p_lg_va), "test": binary_report(yte, p_lg_te)},
            {"model": "logistic_regression", "val": binary_report(yva, p_lr_va), "test": binary_report(yte, p_lr_te)},
        ],
        "lift_test": lift(yte, p_lg_te), "global_importance": imp, "campaign_analysis": analysis,
        "excluded": {"duration": "known only after the call (target leakage)",
                     "calendar_and_macro": "period identifiers that do not transfer across regimes (see split.base_rate)"},
        "reference_random_split_auc_all_features": reference_random_split_auc,
    }
    card = {"model": CONV_MODEL, "intended_use": "Prioritise outbound term-deposit campaigns (marketing analytics).",
            "training_data": "UCI Bank Marketing (Portuguese bank, 2008-2010, CC BY 4.0).",
            "limitations": ["Strong macro-economic shift across the period (base rate varies by split).",
                            "Campaign context (Portugal 2008-2010) differs from the DEMO bank; used for campaign analytics only."]}
    with control_session() as db:
        version = registry.next_version(db, CONV_MODEL)
    adir = registry.artifact_dir(CONV_MODEL, version)
    artifact = adir / "model.joblib"
    joblib.dump({"model": lg, "categorical": CONV_CAT, "numeric": CONV_NUM}, artifact, compress=3)
    with tracking.run("marketing_conversion", f"v{version}", tags={"module": "customer"}) as run_id:
        tracking.log_metrics({"lightgbm_test": metrics["comparison"][0]["test"], "logreg_test": metrics["comparison"][1]["test"]})
    gid = lake.current_version_id("bank_marketing", "gold")
    with control_session(owner=True) as db:
        registry.register_version(db, name=CONV_MODEL, module="customer", task="propensity (term deposit subscription)",
                                  description="Campaign conversion propensity (UCI Bank Marketing, duration excluded).", version=version,
                                  algorithm="lightgbm", artifact_path=artifact, metrics=metrics, params={"num_leaves": 15},
                                  features=CONV_CAT + CONV_NUM, dataset_versions=[str(gid)] if gid else [], reference_profile={},
                                  model_card=card, mlflow_run_id=run_id, trained_by=trained_by,
                                  training_seconds=time.perf_counter() - t0, risk_tier="limited")
    return {"model": CONV_MODEL, "version": version, "test_auc_lgbm": metrics["comparison"][0]["test"]["roc_auc"],
            "test_auc_logreg": metrics["comparison"][1]["test"]["roc_auc"], "lift_top_decile": metrics["lift_test"][0]["lift"]}


def train(trained_by: str = "pipeline", quick: bool = False) -> dict[str, Any]:
    return {"topics": train_topics(trained_by), "conversion": train_conversion(trained_by)}
