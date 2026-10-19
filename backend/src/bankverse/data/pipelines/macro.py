"""Macro-financial monthly series → silver (standardised to month start) → platform.ref_series / ref_observations."""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from bankverse.data import lake, quality
from bankverse.data.runner import RunContext
from bankverse.db.models.platform import RefObservation, RefSeries
from bankverse.db.session import control_session

SOURCE = "datahub_macro"

SERIES: dict[str, dict[str, Any]] = {
    "eurusd": {"file": "exchange_rates_monthly.csv", "name": "EUR/USD (USD per EUR)", "upstream": "FRED (Board of Governors H.10) via datahub.io",
               "unit": "USD", "date": "Date", "value": "Exchange rate", "filter": ("Country", "Euro"), "invert": True,
               "description": "Monthly average; datahub publishes EUR per USD, inverted to the usual USD per EUR quote."},
    "euribor_3m": {"file": "euribor_3m_monthly.csv", "name": "Euribor 3 mois", "upstream": "EMMI / ECB via datahub.io", "unit": "%",
                   "date": "date", "value": "rate"},
    "us10y": {"file": "us10y_monthly.csv", "name": "US Treasury 10Y yield", "upstream": "Federal Reserve H.15 via datahub.io", "unit": "%",
              "date": "Date", "value": "Rate"},
    "us_cpi": {"file": "cpi_us_monthly.csv", "name": "US CPI (all items, NSA)", "upstream": "BLS via datahub.io", "unit": "index 1982-84=100",
               "date": "Date", "value": "Index"},
    "vix": {"file": "vix_monthly.csv", "name": "CBOE VIX (monthly close)", "upstream": "CBOE via datahub.io", "unit": "points", "date": "Date",
            "value": "Close"},
    "brent": {"file": "brent_monthly.csv", "name": "Brent crude oil spot", "upstream": "EIA via datahub.io", "unit": "USD/bbl", "date": "Date",
              "value": "Price"},
    "gold": {"file": "gold_monthly.csv", "name": "Gold price", "upstream": "World Bank / NMA via datahub.io", "unit": "USD/oz", "date": "Date",
             "value": "Price"},
    "case_shiller": {"file": "case_shiller_national.csv", "name": "S&P CoreLogic Case-Shiller US National (SA)",
                     "upstream": "S&P CoreLogic via datahub.io", "unit": "index Jan 2000=100", "date": "Date", "value": "National-US-SA"},
}
START = date(1990, 1, 1)


def run(ctx: RunContext) -> dict[str, Any]:
    raw = lake.layer_dir("raw", SOURCE)
    frames = []
    for key, cfg in SERIES.items():
        with ctx.step(f"standardise_{key}") as st:
            df = pl.read_csv(raw / cfg["file"], infer_schema_length=10000)
            if "filter" in cfg:
                df = df.filter(pl.col(cfg["filter"][0]) == cfg["filter"][1])
            d = df.get_column(cfg["date"]).cast(pl.Utf8)
            out = pl.DataFrame({"raw_date": d, "value": df.get_column(cfg["value"]).cast(pl.Float64)}).with_columns(
                obs_date=pl.when(pl.col("raw_date").str.len_chars() == 7).then(pl.col("raw_date") + "-01").otherwise(pl.col("raw_date"))
                .str.to_date("%Y-%m-%d").dt.truncate("1mo"))
            if cfg.get("invert"):
                out = out.with_columns(value=1.0 / pl.col("value"))
            out = (out.drop_nulls("value").filter(pl.col("obs_date") >= START).group_by("obs_date").agg(pl.col("value").mean())
                   .sort("obs_date").with_columns(series_key=pl.lit(key)))
            frames.append(out.select("series_key", "obs_date", "value"))
            st["rows_out"] = out.height
    df = pl.concat(frames)
    gaps = []
    for key in SERIES:
        s = df.filter(pl.col("series_key") == key).get_column("obs_date")
        expected = (s.max().year - s.min().year) * 12 + s.max().month - s.min().month + 1  # type: ignore[union-attr]
        gaps.append(quality.CheckResult("monthly_completeness", "pass" if s.len() == expected else "warn", column=key,
                                        observed=float(expected - s.len()), threshold=0, blocking=False))
    checks = [quality.unique(df, ["series_key", "obs_date"]), *quality.not_null(df, ["value"]), *gaps]
    p = lake.write_parquet(df, "silver", "macro_monthly")
    up = lake.current_version_id(SOURCE, "raw")
    vid = lake.register_version(name="macro_monthly", layer="silver", path=p, source_key=SOURCE, rows=df.height, columns=df.width,
                                schema=lake.schema_of(df), prof={"series": {k: int(df.filter(pl.col("series_key") == k).height) for k in SERIES}},
                                run_id=ctx.run_id, upstream_ids=[up] if up else [], transform="month-start standardisation, EUR/USD inversion")
    lake.set_quality_status(vid, quality.persist(vid, checks))
    quality.enforce(checks)
    with ctx.step("load_reference_tables") as st, control_session(owner=True) as db:
        for key, cfg in SERIES.items():
            s = df.filter(pl.col("series_key") == key)
            values = {"key": key, "name": cfg["name"], "source_key": SOURCE, "upstream": cfg["upstream"], "frequency": "M",
                      "unit": cfg["unit"], "description": cfg.get("description", ""), "first_date": s.get_column("obs_date").min(),
                      "last_date": s.get_column("obs_date").max(), "n_obs": s.height, "dataset_version_id": vid}
            stmt = insert(RefSeries).values(**values)
            db.execute(stmt.on_conflict_do_update(index_elements=[RefSeries.key], set_={k: stmt.excluded[k] for k in values if k != "key"}))
            db.execute(delete(RefObservation).where(RefObservation.series_key == key))
            db.execute(insert(RefObservation), [{"series_key": key, "obs_date": r[0], "value": r[1]} for r in s.select("obs_date", "value").iter_rows()])
        st["rows_out"] = df.height
    return {"series": len(SERIES), "observations": df.height}
