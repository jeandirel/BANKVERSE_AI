"""BANKING77 customer queries: csv → bronze → silver (official train/test split preserved)."""

from __future__ import annotations

import json
from typing import Any

import polars as pl

from bankverse.data import lake, quality
from bankverse.data.registry import SOURCES_BY_KEY
from bankverse.data.runner import RunContext

SOURCE = "banking77"


def run(ctx: RunContext) -> dict[str, Any]:
    raw = lake.layer_dir("raw", SOURCE)
    with ctx.step("bronze") as st:
        tr = pl.read_csv(raw / "train.csv").with_columns(split=pl.lit("train"))
        te = pl.read_csv(raw / "test.csv").with_columns(split=pl.lit("test"))
        df = pl.concat([tr, te])
        cats = json.loads((raw / "categories.json").read_text())
        b = lake.write_parquet(df, "bronze", "banking77")
        st["rows_out"] = df.height
    raw_id = lake.current_version_id(SOURCE, "raw")
    vb = lake.register_version(name="banking77", layer="bronze", path=b, source_key=SOURCE, rows=df.height, columns=df.width,
                               schema=lake.schema_of(df), run_id=ctx.run_id, upstream_ids=[raw_id] if raw_id else [], transform="csv→parquet")
    exp = SOURCES_BY_KEY[SOURCE].expected
    with ctx.step("silver") as st:
        s = df.with_columns(text=pl.col("text").str.strip_chars()).filter(pl.col("text").str.len_chars() > 0)
        checks = [quality.CheckResult("train_rows", "pass" if tr.height == exp["train_rows"] else "fail", observed=tr.height, threshold=exp["train_rows"]),
                  quality.CheckResult("test_rows", "pass" if te.height == exp["test_rows"] else "fail", observed=te.height, threshold=exp["test_rows"]),
                  quality.CheckResult("labels", "pass" if s.get_column("category").n_unique() == exp["labels"] == len(cats) else "fail",
                                      observed=s.get_column("category").n_unique(), threshold=exp["labels"]),
                  *quality.not_null(s, ["text", "category"]), quality.allowed_values(s, "category", cats)]
        sp = lake.write_parquet(s, "silver", "banking77")
        st["rows_out"] = s.height
    vs = lake.register_version(name="banking77", layer="silver", path=sp, source_key=SOURCE, rows=s.height, columns=s.width,
                               schema=lake.schema_of(s), prof=lake.profile(s), run_id=ctx.run_id, upstream_ids=[vb], transform="trim, validate labels")
    lake.set_quality_status(vs, quality.persist(vs, checks))
    quality.enforce(checks)
    return {"train": tr.height, "test": te.height, "labels": len(cats)}
