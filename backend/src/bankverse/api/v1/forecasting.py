"""Financial Forecasting Studio API."""

from __future__ import annotations

import time
import uuid
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require, tenant_db
from bankverse.core.errors import NotFound, ValidationFailed
from bankverse.db.models.platform import MlModel, ModelVersion, RefSeries
from bankverse.db.models.tenant import ForecastRun
from bankverse.modules.forecasting import engine
from bankverse.modules.forecasting.train import MODEL_NAME, load_series
from bankverse.services import audit, usage

router = APIRouter(prefix="/forecasting", tags=["forecasting"])
DISCLAIMER = ("Projections statistiques assorties d'intervalles d'incertitude : elles ne constituent ni une certitude ni un "
              "conseil financier.")


@router.get("/series")
def series(p: Principal = Depends(require("forecast.read")), cdb: Session = Depends(control_db)) -> list[dict[str, Any]]:
    out = []
    for s in cdb.execute(select(RefSeries).order_by(RefSeries.name)).scalars():
        ser = load_series(s.key)
        out.append({"key": s.key, "name": s.name, "unit": s.unit, "upstream": s.upstream, "frequency": s.frequency,
                    "first_date": s.first_date, "last_date": s.last_date, "n_obs": s.n_obs, "last_value": round(float(ser.iloc[-1]), 4),
                    "change_12m": round(float(ser.iloc[-1] - ser.iloc[-13]), 4) if len(ser) > 13 else None,
                    "description": s.description, "source_key": s.source_key})
    return out


@lru_cache(maxsize=32)
def _diagnostics_cached(key: str, last: str) -> dict[str, Any]:
    s = load_series(key)
    return engine.diagnostics(key, s.loc["2000-01-01":])


@router.get("/series/{key}")
def series_detail(key: str, p: Principal = Depends(require("forecast.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    meta = cdb.get(RefSeries, key)
    if meta is None:
        raise NotFound("Unknown series")
    s = load_series(key)
    s2000 = s.loc["2000-01-01":]
    diag = _diagnostics_cached(key, str(meta.last_date))
    return {"key": key, "name": meta.name, "unit": meta.unit, "upstream": meta.upstream, "description": meta.description,
            "observations": [{"date": str(d.date()), "value": round(float(v), 5)} for d, v in s2000.items()],
            "diagnostics": diag, "disclaimer": DISCLAIMER}


def _study(cdb: Session) -> tuple[int, dict[str, Any]] | None:
    m = cdb.execute(select(MlModel).where(MlModel.name == MODEL_NAME)).scalar_one_or_none()
    if m is None:
        return None
    mv = cdb.execute(select(ModelVersion).where(ModelVersion.model_id == m.id).order_by(ModelVersion.version.desc()).limit(1)).scalar_one()
    return mv.version, mv.metrics


@router.get("/backtest/{key}")
def backtest(key: str, p: Principal = Depends(require("forecast.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    st = _study(cdb)
    if st is None or key not in st[1]["study"]:
        raise NotFound("No backtest study available for this series")
    return {"study_version": st[0], "protocol": st[1]["protocol"], **st[1]["study"][key]}


@router.get("/leaderboard")
def leaderboard(p: Principal = Depends(require("forecast.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    st = _study(cdb)
    if st is None:
        raise NotFound("No forecasting study available")
    return {"study_version": st[0], "wins_by_model": st[1]["wins_by_model"], "naive_beaten": st[1]["naive_beaten"],
            "best_by_series": {k: v["best_by_mase"] for k, v in st[1]["study"].items()}, "protocol": st[1]["protocol"]}


class Scenario(BaseModel):
    driver: str
    shock: float = Field(ge=-1000, le=1000, description="Absolute change applied to the driver over the horizon (driver units)")


class RunIn(BaseModel):
    series: str
    horizon: int = Field(default=12, ge=1, le=24)
    models: list[str] = Field(default_factory=lambda: ["naive", "ets", "arima"])
    scenario: Scenario | None = None
    save: bool = True


@router.post("/run")
def run(body: RunIn, request: Request, p: Principal = Depends(require("forecast.run")), db: Session = Depends(tenant_db),
        cdb: Session = Depends(control_db)) -> dict[str, Any]:
    if cdb.get(RefSeries, body.series) is None:
        raise NotFound("Unknown series")
    bad = [m for m in body.models if m not in engine.MODELS]
    if bad or not body.models:
        raise ValidationFailed(f"Unknown models: {bad}. Available: {list(engine.MODELS)}")
    usage.consume(p.org_id, "forecast_runs")
    t0 = time.perf_counter()
    s = load_series(body.series).loc["1999-01-01":]
    y = engine.transform(body.series, s.to_numpy(dtype=float))
    months = s.index.month.to_numpy()
    future = pd.date_range(s.index[-1] + pd.offsets.MonthBegin(1), periods=body.horizon, freq="MS")
    out: dict[str, Any] = {}
    for m in body.models:
        try:
            f = engine.run_model(m, y, body.horizon, months)
        except Exception as exc:
            out[m] = {"status": "failed", "error": type(exc).__name__}
            continue
        out[m] = {"status": "ok", "info": f.info or {}, "points": [
            {"date": str(d.date()), "mean": round(float(engine.inverse(body.series, f.mean)[i]), 5),  # type: ignore[index]
             "lo80": _r(engine.inverse(body.series, f.lo80), i), "hi80": _r(engine.inverse(body.series, f.hi80), i),
             "lo95": _r(engine.inverse(body.series, f.lo95), i), "hi95": _r(engine.inverse(body.series, f.hi95), i)}
            for i, d in enumerate(future)]}
    scenario_out = None
    if body.scenario:
        if cdb.get(RefSeries, body.scenario.driver) is None or body.scenario.driver == body.series:
            raise ValidationFailed("Invalid scenario driver")
        x = load_series(body.scenario.driver)
        df = pd.concat([s.rename("y"), x.rename("x")], axis=1).dropna()
        yy = engine.transform(body.series, df["y"].to_numpy(dtype=float))
        xx = df["x"].to_numpy(dtype=float)
        base_path = np.repeat(xx[-1], body.horizon)
        shock_path = xx[-1] + np.linspace(body.scenario.shock / body.horizon, body.scenario.shock, body.horizon)
        fb = engine.sarimax_scenario(yy, xx, base_path, body.horizon)
        fs = engine.sarimax_scenario(yy, xx, shock_path, body.horizon)
        scenario_out = {"driver": body.scenario.driver, "shock": body.scenario.shock, "model": fs.info,
                        "baseline": [round(float(v), 5) for v in engine.inverse(body.series, fb.mean)],  # type: ignore[union-attr]
                        "shocked": [round(float(v), 5) for v in engine.inverse(body.series, fs.mean)],  # type: ignore[union-attr]
                        "shocked_lo80": [round(float(v), 5) for v in engine.inverse(body.series, fs.lo80)],  # type: ignore[union-attr]
                        "shocked_hi80": [round(float(v), 5) for v in engine.inverse(body.series, fs.hi80)],  # type: ignore[union-attr]
                        "dates": [str(d.date()) for d in future],
                        "note": "Scénario conditionnel : hypothèse de trajectoire du facteur, pas une prévision du facteur."}
    result = {"series": body.series, "horizon": body.horizon, "forecasts": out, "scenario": scenario_out,
              "history": [{"date": str(d.date()), "value": round(float(v), 5)} for d, v in s.loc["2015-01-01":].items()],
              "disclaimer": DISCLAIMER, "duration_ms": int((time.perf_counter() - t0) * 1000)}
    if body.save:
        fr = ForecastRun(org_id=p.org_id, series_key=body.series, created_by=p.actor_id, horizon=body.horizon, models=body.models,
                         scenario=body.scenario.model_dump() if body.scenario else {}, result=result, duration_ms=result["duration_ms"])
        db.add(fr)
        db.flush()
        result["run_id"] = str(fr.id)
        audit.record("forecast.run", org_id=p.org_id, actor_user_id=p.user_id, actor_label=p.label, ip=client_ip(request),
                     resource_type="forecast_run", resource_id=str(fr.id), details={"series": body.series, "models": body.models})
    return result


def _r(arr: np.ndarray | None, i: int) -> float | None:
    return None if arr is None else round(float(arr[i]), 5)


@router.get("/runs")
def runs(p: Principal = Depends(require("forecast.read")), db: Session = Depends(tenant_db)) -> list[dict[str, Any]]:
    rows = db.execute(select(ForecastRun).order_by(ForecastRun.created_at.desc()).limit(50)).scalars()
    return [{"id": str(r.id), "series": r.series_key, "horizon": r.horizon, "models": r.models, "scenario": r.scenario,
             "created_at": r.created_at, "duration_ms": r.duration_ms} for r in rows]


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, p: Principal = Depends(require("forecast.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    r = db.execute(select(ForecastRun).where(ForecastRun.id == run_id, ForecastRun.org_id == p.org_id)).scalar_one_or_none()
    if r is None:
        raise NotFound("Run not found")
    return r.result | {"run_id": str(r.id), "created_at": r.created_at}


@router.get("/cross-correlation")
def cross_corr(a: str, b: str, max_lag: int = Query(default=12, ge=1, le=24), p: Principal = Depends(require("forecast.read")),
               cdb: Session = Depends(control_db)) -> dict[str, Any]:
    for k in (a, b):
        if cdb.get(RefSeries, k) is None:
            raise NotFound(f"Unknown series {k}")
    sa, sb = load_series(a).loc["2000-01-01":], load_series(b).loc["2000-01-01":]
    return {"a": a, "b": b, "transform": "first differences", "lags": engine.cross_correlation(sa, sb, max_lag),
            "note": "lag > 0 : la variation de A suit celle de B de « lag » mois."}
