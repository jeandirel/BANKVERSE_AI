"""Time-series forecasting engine (monthly series).

Models: naive, seasonal naive, moving average, exponential smoothing (ETS, damped trend), ARIMA
(order chosen by AIC on a small grid, differencing chosen with ADF/KPSS), SARIMA (only when STL
seasonal strength justifies it), SARIMAX (exogenous scenario driver), gradient boosting on lags
(XGBoost, recursive) and a small LSTM (PyTorch, recursive). Prophet and Temporal Fusion Transformer
are intentionally not used: Prophet's Stan toolchain is not installed in this environment and the
series (300–450 monthly points, univariate) do not justify a TFT.

Evaluation is out-of-sample only, with rolling origins (no shuffling). Forecasts are reported with
prediction intervals and are never presented as certainties.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.stattools import adfuller, ccf, kpss

warnings.filterwarnings("ignore")

LOG_SERIES = {"brent", "gold", "us_cpi", "case_shiller", "vix", "eurusd"}
Z80, Z95 = 1.2816, 1.96


@dataclass
class Forecast:
    mean: np.ndarray
    lo80: np.ndarray | None = None
    hi80: np.ndarray | None = None
    lo95: np.ndarray | None = None
    hi95: np.ndarray | None = None
    info: dict[str, Any] | None = None


def _rw_intervals(y: np.ndarray, mean: np.ndarray, h: int, scale: float | None = None) -> Forecast:
    sigma = scale if scale is not None else float(np.std(np.diff(y), ddof=1))
    s = sigma * np.sqrt(np.arange(1, h + 1))
    return Forecast(mean, mean - Z80 * s, mean + Z80 * s, mean - Z95 * s, mean + Z95 * s)


def naive(y: np.ndarray, h: int) -> Forecast:
    return _rw_intervals(y, np.repeat(y[-1], h), h)


def seasonal_naive(y: np.ndarray, h: int, m: int = 12) -> Forecast:
    mean = np.array([y[-m + (i % m)] for i in range(h)])
    sigma = float(np.std(y[m:] - y[:-m], ddof=1))
    s = sigma * np.sqrt(np.floor(np.arange(h) / m) + 1)
    return Forecast(mean, mean - Z80 * s, mean + Z80 * s, mean - Z95 * s, mean + Z95 * s)


def moving_average(y: np.ndarray, h: int, w: int = 12) -> Forecast:
    mean = np.repeat(float(np.mean(y[-w:])), h)
    resid = y[w:] - np.convolve(y, np.ones(w) / w, mode="valid")[:-1]
    sigma = float(np.std(resid, ddof=1))
    s = sigma * np.sqrt(1 + np.arange(h) / w)
    return Forecast(mean, mean - Z80 * s, mean + Z80 * s, mean - Z95 * s, mean + Z95 * s, {"window": w})


def ets(y: np.ndarray, h: int) -> Forecast:
    # statsmodels 0.15 requires an indexed series for prediction intervals
    model = ETSModel(pd.Series(y), error="add", trend="add", damped_trend=True).fit(disp=False)
    pred = model.get_prediction(start=len(y), end=len(y) + h - 1)
    f80 = pred.summary_frame(alpha=0.2)
    f95 = pred.summary_frame(alpha=0.05)
    return Forecast(f80["mean"].to_numpy(), f80["pi_lower"].to_numpy(), f80["pi_upper"].to_numpy(),
                    f95["pi_lower"].to_numpy(), f95["pi_upper"].to_numpy(), {"aic": round(float(model.aic), 2)})


def choose_d(y: np.ndarray) -> int:
    try:
        adf_p = adfuller(y, autolag="AIC")[1]
        kpss_p = kpss(y, regression="c", nlags="auto")[1]
    except Exception:
        return 1
    return 0 if (adf_p < 0.05 and kpss_p > 0.05) else 1


def arima(y: np.ndarray, h: int, seasonal: bool = False) -> Forecast:
    d = choose_d(y)
    best: tuple[float, Any, tuple[int, int, int]] | None = None
    for p in range(0, 3):
        for q in range(0, 3):
            try:
                if seasonal:
                    m = SARIMAX(y, order=(p, d, q), seasonal_order=(1, 0, 1, 12), trend="n").fit(disp=False)
                else:
                    m = ARIMA(y, order=(p, d, q), trend="t" if d == 1 else "c").fit()
            except Exception:  # noqa: S112 - non-converging orders are skipped (order search)  # nosec B112
                continue
            if best is None or m.aic < best[0]:
                best = (m.aic, m, (p, d, q))
    assert best is not None
    _, model, order = best
    fc = model.get_forecast(h)
    ci80, ci95 = fc.conf_int(alpha=0.2), fc.conf_int(alpha=0.05)
    ci80, ci95 = np.asarray(ci80), np.asarray(ci95)
    return Forecast(np.asarray(fc.predicted_mean), ci80[:, 0], ci80[:, 1], ci95[:, 0], ci95[:, 1],
                    {"order": order, "seasonal_order": (1, 0, 1, 12) if seasonal else None, "aic": round(float(best[0]), 2)})


def sarimax_scenario(y: np.ndarray, x: np.ndarray, x_future: np.ndarray, h: int) -> Forecast:
    d = choose_d(y)
    model = SARIMAX(y, exog=x, order=(1, d, 1), trend="n").fit(disp=False)
    fc = model.get_forecast(h, exog=x_future.reshape(-1, 1))
    ci80, ci95 = np.asarray(fc.conf_int(alpha=0.2)), np.asarray(fc.conf_int(alpha=0.05))
    beta = float(np.asarray(model.params)[0])
    return Forecast(np.asarray(fc.predicted_mean), ci80[:, 0], ci80[:, 1], ci95[:, 0], ci95[:, 1],
                    {"order": (1, d, 1), "exog_coefficient": round(beta, 4), "aic": round(float(model.aic), 2)})


def _lag_matrix(y: np.ndarray, months: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray]:
    X, t = [], []
    for i in range(lags, len(y)):
        X.append(np.r_[y[i - lags:i][::-1], np.sin(2 * np.pi * months[i] / 12), np.cos(2 * np.pi * months[i] / 12)])
        t.append(y[i])
    return np.array(X), np.array(t)


def xgboost_lags(y: np.ndarray, h: int, months: np.ndarray, lags: int = 12) -> Forecast:
    import xgboost as xgb

    dy = np.diff(y)  # model differences (stationarity) then integrate
    X, t = _lag_matrix(dy, months[1:], lags)
    m = xgb.XGBRegressor(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.9, n_jobs=2, random_state=7).fit(X, t)
    resid_sd = float(np.std(t - m.predict(X), ddof=1))
    hist = list(dy)
    out = []
    last_month = int(months[-1])
    for k in range(h):
        mo = (last_month + k) % 12 + 1
        feat = np.r_[np.array(hist[-lags:])[::-1], np.sin(2 * np.pi * mo / 12), np.cos(2 * np.pi * mo / 12)]
        step = float(m.predict(feat[None, :])[0])
        hist.append(step)
        out.append(step)
    mean = y[-1] + np.cumsum(out)
    return _rw_intervals(y, mean, h, scale=resid_sd)


def lstm(y: np.ndarray, h: int, window: int = 24, epochs: int = 60, seed: int = 7) -> Forecast:
    import torch
    from torch import nn

    torch.manual_seed(seed)
    dy = np.diff(y)
    mu, sd = float(dy.mean()), float(dy.std() + 1e-9)
    z = (dy - mu) / sd
    X = np.array([z[i - window:i] for i in range(window, len(z))], dtype=np.float32)[..., None]
    t = np.array([z[i] for i in range(window, len(z))], dtype=np.float32)

    class Net(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.lstm = nn.LSTM(1, 24, batch_first=True)
            self.out = nn.Linear(24, 1)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            o, _ = self.lstm(x)
            return self.out(o[:, -1]).squeeze(-1)

    net = Net()
    opt = torch.optim.Adam(net.parameters(), lr=5e-3)
    Xt, tt = torch.from_numpy(X), torch.from_numpy(t)
    for _ in range(epochs):
        opt.zero_grad()
        loss = ((net(Xt) - tt) ** 2).mean()
        loss.backward()
        opt.step()
    net.eval()
    with torch.no_grad():
        resid_sd = float((net(Xt) - tt).std()) * sd
        hist = list(z)
        out = []
        for _ in range(h):
            x = torch.tensor(np.array(hist[-window:], dtype=np.float32)[None, :, None])
            step = float(net(x)[0])
            hist.append(step)
            out.append(step * sd + mu)
    mean = y[-1] + np.cumsum(out)
    return _rw_intervals(y, mean, h, scale=resid_sd)


def seasonal_strength(y: np.ndarray) -> float:
    if len(y) < 36:
        return 0.0
    res = STL(y, period=12, robust=True).fit()
    var_r = np.var(res.resid)
    return float(max(0.0, 1 - var_r / max(np.var(res.resid + res.seasonal), 1e-12)))


MODELS = ("naive", "seasonal_naive", "moving_average", "ets", "arima", "sarima", "xgboost_lags", "lstm")


def run_model(name: str, y: np.ndarray, h: int, months: np.ndarray) -> Forecast:
    if name == "naive":
        return naive(y, h)
    if name == "seasonal_naive":
        return seasonal_naive(y, h)
    if name == "moving_average":
        return moving_average(y, h)
    if name == "ets":
        return ets(y, h)
    if name == "arima":
        return arima(y, h)
    if name == "sarima":
        return arima(y, h, seasonal=True)
    if name == "xgboost_lags":
        return xgboost_lags(y, h, months)
    if name == "lstm":
        return lstm(y, h)
    raise KeyError(name)


def transform(key: str, v: np.ndarray) -> np.ndarray:
    return np.log(v) if key in LOG_SERIES else v


def inverse(key: str, v: np.ndarray | None) -> np.ndarray | None:
    if v is None:
        return None
    return np.exp(v) if key in LOG_SERIES else v


def backtest(key: str, s: pd.Series, models: list[str], h: int = 12, n_origins: int = 8, step: int = 3,
             min_train: int = 120) -> dict[str, Any]:
    """Rolling-origin evaluation on the original scale. Returns per-model metrics and per-origin errors."""
    values = s.to_numpy(dtype=float)
    months = s.index.month.to_numpy()
    n = len(values)
    origins = [n - h - step * i for i in range(n_origins) if n - h - step * i >= min_train][::-1]
    res: dict[str, Any] = {}
    for name in models:
        errs, abs_pct, sm, cov80, cov95 = [], [], [], [], []
        scales = []
        failed = 0
        for o in origins:
            train, test = values[:o], values[o:o + h]
            try:
                f = run_model(name, transform(key, train), h, months[:o])
            except Exception:
                failed += 1
                continue
            mean = inverse(key, f.mean)
            assert mean is not None
            e = test - mean
            errs.append(e)
            scales.append(float(np.mean(np.abs(np.diff(train)))))
            sm.append(np.mean(2 * np.abs(e) / (np.abs(test) + np.abs(mean) + 1e-9)))
            abs_pct.append(np.abs(e))
            if f.lo80 is not None:
                lo80, hi80, lo95, hi95 = (inverse(key, f.lo80), inverse(key, f.hi80), inverse(key, f.lo95), inverse(key, f.hi95))
                cov80.append(np.mean((test >= lo80) & (test <= hi80)))  # type: ignore[operator]
                cov95.append(np.mean((test >= lo95) & (test <= hi95)))  # type: ignore[operator]
        if not errs:
            res[name] = {"status": "failed", "failed_origins": failed}
            continue
        E = np.vstack(errs)
        res[name] = {
            "mae": round(float(np.mean(np.abs(E))), 5), "rmse": round(float(np.sqrt(np.mean(E ** 2))), 5),
            "mase": round(float(np.mean(np.abs(E)) / np.mean(scales)), 4), "smape": round(float(np.mean(sm)), 4),
            "coverage80": round(float(np.mean(cov80)), 3) if cov80 else None,
            "coverage95": round(float(np.mean(cov95)), 3) if cov95 else None,
            "mae_by_horizon": [round(float(x), 5) for x in np.mean(np.abs(E), axis=0)],
            "abs_error_q80_by_horizon": [round(float(x), 5) for x in np.quantile(np.abs(E), 0.8, axis=0)],
            "abs_error_q95_by_horizon": [round(float(x), 5) for x in np.quantile(np.abs(E), 0.95, axis=0)],
            "origins": len(errs), "failed_origins": failed,
        }
    ok = {k: v for k, v in res.items() if v.get("status") != "failed"}
    best = min(ok, key=lambda k: ok[k]["mase"]) if ok else None
    return {"horizon": h, "origins": [str(s.index[o].date()) for o in origins], "models": res, "best_by_mase": best}


def diagnostics(key: str, s: pd.Series) -> dict[str, Any]:
    y = transform(key, s.to_numpy(dtype=float))
    out: dict[str, Any] = {"transform": "log" if key in LOG_SERIES else "level"}
    for label, series in (("level", y), ("first_difference", np.diff(y))):
        try:
            adf = adfuller(series, autolag="AIC")
            kp = kpss(series, regression="c", nlags="auto")
            out[label] = {"adf_stat": round(float(adf[0]), 3), "adf_pvalue": round(float(adf[1]), 4),
                          "kpss_stat": round(float(kp[0]), 3), "kpss_pvalue": round(float(kp[1]), 4),
                          "stationary": bool(adf[1] < 0.05 and kp[1] > 0.05)}
        except Exception as exc:
            out[label] = {"error": type(exc).__name__}
    stl = STL(y, period=12, robust=True).fit()
    resid = stl.resid
    med = np.median(resid)
    mad = np.median(np.abs(resid - med)) * 1.4826 + 1e-12
    z = (resid - med) / mad
    anomalies = [{"date": str(s.index[i].date()), "value": round(float(s.iloc[i]), 4), "robust_z": round(float(z[i]), 2)}
                 for i in np.where(np.abs(z) > 3.5)[0]]
    out["stl"] = {"seasonal_strength": round(seasonal_strength(y), 3),
                  "trend": [round(float(v), 5) for v in inverse(key, stl.trend)],  # type: ignore[union-attr]
                  "seasonal": [round(float(v), 5) for v in stl.seasonal]}
    out["anomalies"] = anomalies[-30:]
    return out


def cross_correlation(a: pd.Series, b: pd.Series, max_lag: int = 12) -> list[dict[str, float]]:
    df = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    da, dbb = np.diff(df["a"].to_numpy()), np.diff(df["b"].to_numpy())
    out = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            c = np.corrcoef(da[lag:], dbb[: len(dbb) - lag])[0, 1] if lag < len(da) else np.nan
        else:
            c = np.corrcoef(da[:lag], dbb[-lag:])[0, 1]
        out.append({"lag": lag, "corr": round(float(c), 4)})
    _ = ccf
    return out
