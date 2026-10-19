"""UCI Bank Marketing (bank-additional-full): csv → bronze → silver (facts verified) → gold."""

from __future__ import annotations

from typing import Any

import polars as pl

from bankverse.data import lake, quality
from bankverse.data.registry import SOURCES_BY_KEY
from bankverse.data.runner import RunContext

SOURCE = "uci_bank_marketing"


def run(ctx: RunContext) -> dict[str, Any]:
    raw = lake.layer_dir("raw", SOURCE) / "bank-additional-full.csv"
    with ctx.step("csv_to_bronze") as st:
        sep = ";" if raw.read_text(encoding="utf-8")[:200].count(";") > 3 else ","
        df = pl.read_csv(raw, separator=sep, quote_char='"', infer_schema_length=10000)
        b = lake.write_parquet(df, "bronze", "bank_marketing")
        st["rows_out"] = df.height
        st["details"]["separator"] = sep
    raw_id = lake.current_version_id(SOURCE, "raw")
    vb = lake.register_version(name="bank_marketing", layer="bronze", path=b, source_key=SOURCE, rows=df.height, columns=df.width,
                               schema=lake.schema_of(df), run_id=ctx.run_id, upstream_ids=[raw_id] if raw_id else [], transform="csv→parquet")
    exp = SOURCES_BY_KEY[SOURCE].expected
    with ctx.step("silver") as st:
        s = df.rename({c: c.replace(".", "_") for c in df.columns}).with_columns(
            subscribed=(pl.col("y") == "yes").cast(pl.Int8),
            previously_contacted=(pl.col("pdays") != 999).cast(pl.Int8),
            pdays=pl.when(pl.col("pdays") == 999).then(None).otherwise(pl.col("pdays")),
        ).drop("y")
        dups = s.height - s.unique().height
        checks = [quality.row_count(s, 0, expected=exp["rows"]), quality.class_count(s, "subscribed", 1, exp["positives"]),
                  *quality.not_null(s, ["age", "job", "marital", "education", "contact", "month", "campaign", "subscribed"]),
                  quality.in_range(s, "age", 17, 100), quality.in_range(s, "campaign", 1, 100),
                  quality.CheckResult("exact_duplicates", "warn" if dups else "pass", observed=float(dups), threshold=0, blocking=False,
                                      details={"note": "kept: identical rows are plausible distinct contacts in this dataset"})]
        sp = lake.write_parquet(s, "silver", "bank_marketing")
        gp = lake.write_parquet(s, "gold", "bank_marketing")
        st["rows_out"] = s.height
    vs = lake.register_version(name="bank_marketing", layer="silver", path=sp, source_key=SOURCE, rows=s.height, columns=s.width,
                               schema=lake.schema_of(s), prof=lake.profile(s), run_id=ctx.run_id, upstream_ids=[vb],
                               transform="normalise names, pdays=999→null, target encoding")
    lake.set_quality_status(vs, quality.persist(vs, checks))
    quality.enforce(checks)
    vg = lake.register_version(name="bank_marketing", layer="gold", path=gp, source_key=SOURCE, rows=s.height, columns=s.width,
                               schema=lake.schema_of(s), run_id=ctx.run_id, upstream_ids=[vs], transform="identity (model-ready)")
    lake.set_quality_status(vg, "pass")
    return {"rows": s.height, "subscribed": int(s.get_column("subscribed").sum()), "exact_duplicates": dups}
