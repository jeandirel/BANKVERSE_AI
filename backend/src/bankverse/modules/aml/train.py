"""AML model training on IBM AMLSim (inductive protocol).

* Train graphs: 20K_cycle200 and 20K_fanin200 (80 % of nodes for fitting, 20 % for validation).
* Test graph: 20K_fanin200cycle200 — a *different* simulated graph never seen during training,
  mixing both typologies (inductive generalisation, no neighbourhood leakage between splits).
* Compared: interpretable rules, Isolation Forest, logistic regression, LightGBM (structural only /
  structural + amounts) and a GraphSAGE GNN (PyTorch Geometric) on structural features.
* The deployed model uses STRUCTURAL features only (amount behaviour of the simulator does not
  transfer to real banks — documented in the model card).
"""

from __future__ import annotations

import time
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import polars as pl
import torch
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from torch import nn
from torch_geometric.nn import SAGEConv

from bankverse.core.logging import get_logger
from bankverse.data import lake
from bankverse.db.session import control_session
from bankverse.ml import dl, drift, registry, tracking
from bankverse.ml.metrics import binary_report, precision_at_k
from bankverse.modules.aml.graph_features import ALL, STRUCTURAL, edge_index

log = get_logger(__name__)
MODEL_NAME = "aml_account_risk"
SEED = 11
TRAIN_SCENARIOS = ("20K_cycle200", "20K_fanin200")
TEST_SCENARIO = "20K_fanin200cycle200"
FEATURE_LABELS = {
    "in_deg_pct": "Nombre d'émetteurs distincts (rang)", "out_deg_pct": "Nombre de bénéficiaires distincts (rang)",
    "in_cnt_pct": "Volume de virements reçus (rang)", "out_cnt_pct": "Volume de virements émis (rang)",
    "deg_ratio": "Part des contreparties entrantes", "reciprocity": "Réciprocité des contreparties",
    "max_in_unique_w_pct": "Pic d'émetteurs distincts sur une fenêtre (rang)",
    "max_out_unique_w_pct": "Pic de bénéficiaires distincts sur une fenêtre (rang)",
    "in_burst_ratio": "Concentration temporelle des entrées", "out_burst_ratio": "Concentration temporelle des sorties",
    "pass_through_ratio": "Entrées suivies d'une sortie rapide", "scc_small": "Appartient à un anneau (composante fortement connexe réduite)",
    "scc_size_log": "Taille de la composante fortement connexe (log)", "pagerank_pct": "Centralité PageRank (rang)",
    "clustering": "Coefficient de clustering", "community_size_log": "Taille de la communauté Louvain (log)",
    "active_span_ratio": "Durée d'activité relative", "flow_balance_cnt": "Solde des flux (en nombre)",
    "in_amt_mean_pct": "Montant moyen reçu (rang)", "out_amt_mean_pct": "Montant moyen émis (rang)",
    "amt_cv": "Dispersion des montants reçus", "flow_balance_amt": "Solde des flux (en montant)",
    "max_in_amt_w_pct": "Pic de montants reçus sur une fenêtre (rang)",
}


def rule_score(df: pl.DataFrame) -> np.ndarray:
    """Interpretable baseline: fan-in burst, fan-out burst, ring membership, pass-through."""
    fan_in = (df["max_in_unique_w_pct"] >= 0.98).to_numpy()
    fan_out = (df["max_out_unique_w_pct"] >= 0.98).to_numpy()
    ring = (df["scc_small"] == 1).to_numpy()
    passthrough = ((df["pass_through_ratio"] >= 0.6) & (df["in_cnt_pct"] >= 0.5)).to_numpy()
    return (fan_in.astype(float) * 0.35 + fan_out * 0.25 + ring * 0.3 + passthrough * 0.25).clip(0, 1) + \
        1e-3 * df["max_in_unique_w_pct"].to_numpy()


class GraphSAGE(nn.Module):
    def __init__(self, dim: int, hidden: int = 64) -> None:
        super().__init__()
        self.c1 = SAGEConv(dim, hidden)
        self.c2 = SAGEConv(hidden, hidden)
        self.head = nn.Sequential(nn.ReLU(), nn.Dropout(0.2), nn.Linear(hidden + dim, 1))

    def forward(self, x: torch.Tensor, ei: torch.Tensor) -> torch.Tensor:
        h = torch.relu(self.c1(x, ei))
        h = self.c2(h, ei)
        return self.head(torch.cat([h, x], dim=1)).squeeze(-1)


def _graph_tensors(feats: pl.DataFrame, edges: pl.DataFrame, cols: list[str],
                   scaler: dl.Standardizer | None = None) -> tuple[torch.Tensor, torch.Tensor, np.ndarray]:
    order = feats.get_column("node").to_list()
    raw = feats.select(cols).to_numpy().astype(np.float32)
    x = torch.tensor(scaler.transform(raw) if scaler is not None else raw)
    ei = torch.tensor(edge_index(edges, order))
    y = feats.get_column("is_laundering").to_numpy().astype(np.float32)
    return x, ei, y


def train_gnn(graphs: list[dict[str, Any]], test: dict[str, Any], *, epochs: int, cols: list[str]) -> tuple[GraphSAGE, dict[str, Any]]:
    torch.manual_seed(SEED)
    model = GraphSAGE(len(cols))
    opt = torch.optim.Adam(model.parameters(), lr=3e-3, weight_decay=1e-4)
    pos = sum(float(g["y"][g["train_mask"]].sum()) for g in graphs)
    neg = sum(float((1 - g["y"][g["train_mask"]]).sum()) for g in graphs)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(neg / max(pos, 1)))
    best, best_state, history = -1.0, None, []
    for ep in range(epochs):
        model.train()
        for g in graphs:
            opt.zero_grad()
            out = model(g["x"], g["ei"])
            m = torch.from_numpy(g["train_mask"])
            loss = loss_fn(out[m], torch.from_numpy(g["y"])[m])
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            vy, vp = [], []
            for g in graphs:
                p = torch.sigmoid(model(g["x"], g["ei"])).numpy()
                vy.append(g["y"][g["val_mask"]])
                vp.append(p[g["val_mask"]])
            ap = float(average_precision_score(np.concatenate(vy), np.concatenate(vp)))
        history.append(round(ap, 4))
        if ap > best:
            best, best_state = ap, {k: v.clone() for k, v in model.state_dict().items()}
    assert best_state is not None
    model.load_state_dict(best_state)
    _ = test
    return model, {"val_pr_auc_history": history, "best_val_pr_auc": round(best, 4)}


def train(trained_by: str = "pipeline", quick: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    rng = np.random.default_rng(SEED)
    gold = lake.read_dataset("gold", "aml_node_features", lazy=False)
    edges = lake.read_dataset("silver", "amlsim_transactions", lazy=False)
    assert isinstance(gold, pl.DataFrame) and isinstance(edges, pl.DataFrame)
    tr = gold.filter(pl.col("scenario").is_in(TRAIN_SCENARIOS))
    te = gold.filter(pl.col("scenario") == TEST_SCENARIO)
    is_val = rng.random(tr.height) < 0.2
    fit, val = tr.filter(~pl.Series(is_val)), tr.filter(pl.Series(is_val))
    y_fit, y_val, y_te = (d.get_column("is_laundering").to_numpy() for d in (fit, val, te))
    results: dict[str, dict[str, Any]] = {}
    fitted: dict[str, Any] = {}

    def rec(name: str, family: str, features: str, p_val: np.ndarray, p_te: np.ndarray, secs: float, params: dict[str, Any]) -> None:
        k = int(round(0.02 * len(y_te)))
        results[name] = {"family": family, "features": features, "train_seconds": round(secs, 1), "params": params,
                         "val": binary_report(y_val, p_val), "test": binary_report(y_te, p_te),
                         "test_precision_at_2pct": precision_at_k(y_te, p_te, k),
                         "test_recall_at_2pct": round(float(y_te[np.argsort(-p_te)[:k]].sum() / max(y_te.sum(), 1)), 4)}
        log.info("aml_model_done", model=name, val_pr_auc=results[name]["val"]["pr_auc"], test_pr_auc=results[name]["test"]["pr_auc"])

    t = time.perf_counter()
    rec("rules", "interpretable rules", "structural", rule_score(val), rule_score(te), time.perf_counter() - t,
        {"rules": ["fan-in burst (top 2 %)", "fan-out burst (top 2 %)", "ring membership (small SCC)", "pass-through ≥ 60 %"]})

    Xs = {n: d.select(STRUCTURAL).to_numpy().astype(np.float32) for n, d in (("fit", fit), ("val", val), ("te", te))}
    Xa = {n: d.select(ALL).to_numpy().astype(np.float32) for n, d in (("fit", fit), ("val", val), ("te", te))}

    t = time.perf_counter()
    iso = IsolationForest(n_estimators=300, max_samples=512, random_state=SEED, n_jobs=2).fit(Xs["fit"])
    sv, st_ = -iso.score_samples(Xs["val"]), -iso.score_samples(Xs["te"])
    lo, hi = np.percentile(sv, [0.5, 99.5])
    rec("isolation_forest", "unsupervised anomaly", "structural", np.clip((sv - lo) / (hi - lo), 0, 1),
        np.clip((st_ - lo) / (hi - lo), 0, 1), time.perf_counter() - t, {"n_estimators": 300})

    t = time.perf_counter()
    scaler = dl.Standardizer().fit(Xs["fit"])
    lr = LogisticRegression(max_iter=1000, class_weight="balanced").fit(scaler.transform(Xs["fit"]), y_fit)
    rec("logistic_regression", "linear baseline", "structural", lr.predict_proba(scaler.transform(Xs["val"]))[:, 1],
        lr.predict_proba(scaler.transform(Xs["te"]))[:, 1], time.perf_counter() - t, {"class_weight": "balanced"})

    for name, X, cols in (("lightgbm_structural", Xs, STRUCTURAL), ("lightgbm_with_amounts", Xa, ALL)):
        t = time.perf_counter()
        m = lgb.LGBMClassifier(n_estimators=2000, learning_rate=0.03, num_leaves=31, min_child_samples=40, subsample=0.8,
                               subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=SEED, n_jobs=2, verbose=-1)
        m.fit(X["fit"], y_fit, eval_X=(X["val"],), eval_y=(y_val,), eval_metric="average_precision",
              callbacks=[lgb.early_stopping(100, verbose=False)])
        rec(name, "gradient boosting", "structural" if cols is STRUCTURAL else "structural + amounts",
            m.predict_proba(X["val"])[:, 1], m.predict_proba(X["te"])[:, 1], time.perf_counter() - t,
            {"best_iteration": int(m.best_iteration_), "learning_rate": 0.03, "num_leaves": 31})
        fitted[name] = m

    # GraphSAGE (inductive): full-batch training on each training graph
    t = time.perf_counter()
    graphs = []
    gnn_scaler = dl.Standardizer().fit(tr.select(STRUCTURAL).to_numpy().astype(np.float32))
    for sc in TRAIN_SCENARIOS:
        f = gold.filter(pl.col("scenario") == sc)
        x, ei, y = _graph_tensors(f, edges.filter(pl.col("scenario") == sc), STRUCTURAL, gnn_scaler)
        vmask = rng.random(len(y)) < 0.2
        graphs.append({"x": x, "ei": ei, "y": y, "train_mask": ~vmask, "val_mask": vmask})
    xt, eit, yt = _graph_tensors(te, edges.filter(pl.col("scenario") == TEST_SCENARIO), STRUCTURAL, gnn_scaler)
    gnn, hist = train_gnn(graphs, {"x": xt, "ei": eit, "y": yt}, epochs=60 if quick else 200, cols=STRUCTURAL)
    gnn.eval()
    with torch.no_grad():
        p_te_gnn = torch.sigmoid(gnn(xt, eit)).numpy()
        vy, vp = [], []
        for g in graphs:
            p = torch.sigmoid(gnn(g["x"], g["ei"])).numpy()
            vy.append(g["y"][g["val_mask"]])
            vp.append(p[g["val_mask"]])
    k = int(round(0.02 * len(yt)))
    results["graphsage"] = {
        "family": "graph neural network", "features": "structural + message passing", "train_seconds": round(time.perf_counter() - t, 1),
        "params": {**dl.describe(gnn), **hist, "epochs": 60 if quick else 200},
        "val": binary_report(np.concatenate(vy).astype(int), np.concatenate(vp)), "test": binary_report(yt.astype(int), p_te_gnn),
        "test_precision_at_2pct": precision_at_k(yt, p_te_gnn, k),
        "test_recall_at_2pct": round(float(yt[np.argsort(-p_te_gnn)[:k]].sum() / max(yt.sum(), 1)), 4),
        "note": "validation nodes differ from the tabular split (per-graph random masks)",
    }
    log.info("aml_model_done", model="graphsage", test_pr_auc=results["graphsage"]["test"]["pr_auc"])

    deployable = ["rules", "isolation_forest", "logistic_regression", "lightgbm_structural", "graphsage"]
    champion = max(deployable, key=lambda n: results[n]["val"]["pr_auc"])
    model = fitted.get("lightgbm_structural")  # interpretable companion (SHAP explanations) whatever the champion
    contrib = model.booster_.predict(Xs["te"][:4000], pred_contrib=True)[:, :-1]
    importance = sorted(({"feature": f, "label": FEATURE_LABELS.get(f, f), "mean_abs_shap": round(float(v), 5)}
                         for f, v in zip(STRUCTURAL, np.abs(contrib).mean(axis=0), strict=True)), key=lambda d: -d["mean_abs_shap"])
    comparison = sorted(({"model": n, "family": r["family"], "features": r["features"], "val_pr_auc": r["val"]["pr_auc"],
                          "test_pr_auc": r["test"]["pr_auc"], "test_roc_auc": r["test"]["roc_auc"],
                          "test_precision_at_2pct": r["test_precision_at_2pct"], "test_recall_at_2pct": r["test_recall_at_2pct"],
                          "train_seconds": r["train_seconds"]} for n, r in results.items()), key=lambda c: -c["test_pr_auc"])
    p_te_champ = p_te_gnn if champion == "graphsage" else model.predict_proba(Xs["te"])[:, 1]
    test_full = binary_report(y_te, p_te_champ, curves=True)
    ref = drift.reference_profile({f: fit[f].to_numpy() for f in STRUCTURAL}, {f: val[f].to_numpy() for f in STRUCTURAL})
    with control_session() as db:
        version = registry.next_version(db, MODEL_NAME)
    adir = registry.artifact_dir(MODEL_NAME, version)
    artifact = adir / "model.joblib"
    torch.save(gnn.state_dict(), adir / "graphsage_state.pt")
    joblib.dump({"kind": champion, "model": model, "features": STRUCTURAL, "feature_labels": FEATURE_LABELS,
                 "gnn_hidden": 64,
                 "window": 10, "pass_through_steps": 3, "graphsage_state": str(adir / "graphsage_state.pt"),
                 "gnn_scaler": gnn_scaler}, artifact, compress=3)
    metrics = {"champion": champion, "explainer": "lightgbm_structural (TreeSHAP companion model)",
               "selection": f"best validation PR-AUC among deployable structural models → {champion}",
               "test": test_full, "comparison": comparison, "global_importance": importance,
               "details": {k2: {kk: vv for kk, vv in v.items()} for k2, v in results.items()},
               "protocol": {"train_graphs": list(TRAIN_SCENARIOS), "test_graph": TEST_SCENARIO, "val_fraction": 0.2}}
    card = {
        "model": MODEL_NAME,
        "intended_use": "Prioritise accounts for AML investigation by combining interpretable typology rules and a "
                        "graph-feature model. An alert is NOT evidence of money laundering; decisions and any suspicious "
                        "activity report belong to the bank's AML officers.",
        "training_data": "IBM AMLSim published samples (Apache-2.0): three simulated 20,000-account graphs with injected "
                         "cycle and fan-in laundering typologies.",
        "protocol": metrics["protocol"],
        "features": "Structural & temporal graph features expressed as within-graph ranks/ratios (comparable across banks).",
        "champion": champion,
        "explanations": "Per-account factors come from an interpretable companion LightGBM model trained on the same "
                        "features (TreeSHAP); message-passing models have no exact feature attribution.",
        "excluded": "Amount-level features: in AMLSim laundering transfers are systematically smaller (~150 vs ~283) — a "
                    "simulator artefact that would not transfer; benchmarked separately (lightgbm_with_amounts).",
        "test_performance": {k2: test_full[k2] for k2 in ("pr_auc", "roc_auc", "gini")},
        "limitations": ["Simulated typologies only (cycle, fan-in); real laundering is more diverse and adaptive.",
                        "Scores are relative ranks within the analysed graph, not calibrated probabilities.",
                        "Requires the bank's own labelled investigations to validate before any pilot use."],
        "human_oversight": "Four-eyes decision on AML cases; alerts never trigger automatic account actions.",
    }
    with tracking.run("aml_account_risk", f"v{version}", tags={"module": "aml"}) as run_id:
        tracking.log_params({"train_graphs": ",".join(TRAIN_SCENARIOS), "test_graph": TEST_SCENARIO, "seed": SEED})
        for c in comparison:
            tracking.log_metrics({k2: v for k2, v in c.items() if isinstance(v, float)}, prefix=f"{c['model']}.")
        tracking.log_json(metrics, "metrics.json")
    gold_id = lake.current_version_id("aml_node_features", "gold")
    with control_session(owner=True) as db:
        mv = registry.register_version(
            db, name=MODEL_NAME, module="aml", task="account-level laundering risk ranking",
            description="AML account risk (graph features, LightGBM) + GraphSAGE challenger; trained on IBM AMLSim.",
            version=version, algorithm=f"{champion} (structural graph features)", artifact_path=artifact, metrics=metrics,
            params=results[champion]["params"], features=STRUCTURAL,
            dataset_versions=[str(gold_id)] if gold_id else [], reference_profile=ref, model_card=card,
            mlflow_run_id=run_id, trained_by=trained_by, training_seconds=time.perf_counter() - t0)
        mv_id = str(mv.id)
    return {"model": MODEL_NAME, "version": version, "model_version_id": mv_id, "comparison": comparison,
            "seconds": round(time.perf_counter() - t0, 1)}
