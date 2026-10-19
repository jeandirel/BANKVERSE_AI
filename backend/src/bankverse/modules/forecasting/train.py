"""Forecasting study: rolling-origin backtests of every model on every reference series.

Registered as ``macro_forecasting`` (one version = one reproducible study). The leaderboard drives the
default model proposed in the Forecasting Studio; forecasts themselves are re-fitted on demand on the
latest data (see api/v1/forecasting.py).
"""

from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd
from sqlalchemy import select

from bankverse.core.config import get_settings
from bankverse.core.logging import get_logger
from bankverse.data import lake
from bankverse.db.models.platform import RefObservation
from bankverse.db.session import control_session
from bankverse.ml import registry, tracking
from bankverse.modules.forecasting import engine

log = get_logger(__name__)
MODEL_NAME = "macro_forecasting"


def load_series(key: str) -> pd.Series:
    with control_session() as db:
        rows = db.execute(select(RefObservation.obs_date, RefObservation.value).where(RefObservation.series_key == key)
                          .order_by(RefObservation.obs_date)).all()
    s = pd.Series([r[1] for r in rows], index=pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows]), name=key)
    return s.asfreq("MS").interpolate(limit=2)


def train(trained_by: str = "pipeline", quick: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    keys = ["eurusd", "euribor_3m", "us10y", "us_cpi", "vix", "brent", "gold", "case_shiller"]
    study: dict[str, Any] = {}
    for key in keys:
        s = load_series(key)
        seasonal = engine.seasonal_strength(engine.transform(key, s.to_numpy(dtype=float))) > 0.3
        models = [m for m in engine.MODELS if m != "sarima" or seasonal]
        bt = engine.backtest(key, s, models, h=12, n_origins=4 if quick else 8, step=3)
        bt["seasonal_strength"] = round(engine.seasonal_strength(engine.transform(key, s.to_numpy(dtype=float))), 3)
        bt["n_obs"] = len(s)
        study[key] = bt
        log.info("forecast_backtest_done", series=key, best=bt["best_by_mase"],
                 mase={m: v.get("mase") for m, v in bt["models"].items()})
    leaderboard = []
    for key, bt in study.items():
        for m, v in bt["models"].items():
            if v.get("status") != "failed":
                leaderboard.append({"series": key, "model": m, "mase": v["mase"], "mae": v["mae"], "coverage80": v["coverage80"]})
    wins: dict[str, int] = {}
    for bt in study.values():
        if bt["best_by_mase"]:
            wins[bt["best_by_mase"]] = wins.get(bt["best_by_mase"], 0) + 1
    naive_beaten = {k: bool(bt["best_by_mase"] and bt["best_by_mase"] != "naive") for k, bt in study.items()}
    metrics = {"study": study, "leaderboard": leaderboard, "wins_by_model": wins, "naive_beaten": naive_beaten,
               "protocol": {"horizon_months": 12, "rolling_origins": 4 if quick else 8, "origin_step_months": 3,
                            "metrics": ["MAE", "RMSE", "MASE", "sMAPE", "interval coverage"]}}
    card = {"model": MODEL_NAME, "intended_use": "Exploratory macro-financial forecasting and scenario analysis for risk teams.",
            "data": "Public monthly series (datahub.io mirrors of FRED/Federal Reserve, EMMI Euribor, BLS, CBOE, EIA, World Bank, S&P).",
            "limitations": ["Forecasts are statistical projections with uncertainty intervals, not financial certainties or advice.",
                            "Univariate models ignore structural breaks and policy decisions; scenario analysis is conditional on user assumptions.",
                            "Prophet and TFT not used (toolchain unavailable / not justified for ~300-450 monthly points)."]}
    with control_session() as db:
        version = registry.next_version(db, MODEL_NAME)
    adir = registry.artifact_dir(MODEL_NAME, version)
    artifact = adir / "study.json"
    artifact.write_text(json.dumps(metrics, default=str))
    with tracking.run("macro_forecasting", f"v{version}", tags={"module": "forecasting"}) as run_id:
        for row in leaderboard:
            tracking.log_metrics({f"{row['series']}.{row['model']}.mase": row["mase"]})
        tracking.log_file(artifact)
    gid = lake.current_version_id("macro_monthly", "silver")
    with control_session(owner=True) as db:
        registry.register_version(db, name=MODEL_NAME, module="forecasting", task="monthly time-series forecasting (12 months)",
                                  description="Rolling-origin benchmark of 8 forecasting methods on 8 macro-financial series.",
                                  version=version, algorithm="model selection by backtest MASE", artifact_path=artifact,
                                  metrics=metrics, params={"horizon": 12}, features=keys, dataset_versions=[str(gid)] if gid else [],
                                  reference_profile={}, model_card=card, mlflow_run_id=run_id, trained_by=trained_by,
                                  training_seconds=time.perf_counter() - t0, risk_tier="limited")
    _ = get_settings
    return {"model": MODEL_NAME, "version": version, "best_by_series": {k: v["best_by_mase"] for k, v in study.items()},
            "wins": wins, "seconds": round(time.perf_counter() - t0, 1)}
