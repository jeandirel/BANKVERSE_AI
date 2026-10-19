"""UCI Default of Credit Card Clients: xls → bronze → silver (verified against published facts) → gold."""

from __future__ import annotations

from typing import Any

import pandas as pd
import polars as pl

from bankverse.data import lake, quality
from bankverse.data.registry import SOURCES_BY_KEY
from bankverse.data.runner import RunContext
from bankverse.modules.credit.features import FEATURES, PROTECTED, build

SOURCE = "uci_credit_default"


def run(ctx: RunContext) -> dict[str, Any]:
    raw = lake.layer_dir("raw", SOURCE) / "default_of_credit_card_clients.xls"
    with ctx.step("xls_to_bronze") as st:
        df = pl.from_pandas(pd.read_excel(raw, header=1))
        b = lake.write_parquet(df, "bronze", "uci_credit")
        st["rows_out"] = df.height
    raw_id = lake.current_version_id(SOURCE, "raw")
    vb = lake.register_version(name="uci_credit", layer="bronze", path=b, source_key=SOURCE, rows=df.height, columns=df.width,
                               schema=lake.schema_of(df), run_id=ctx.run_id, upstream_ids=[raw_id] if raw_id else [],
                               transform="xls→parquet")
    exp = SOURCES_BY_KEY[SOURCE].expected
    with ctx.step("silver_clean") as st:
        s = df.rename({c: c.lower() for c in df.columns}).rename({"default payment next month": "default"})
        # Undocumented codes are mapped to 'other' per UCI documentation (education 0/5/6 → 4; marriage 0 → 3)
        s = s.with_columns(
            education=pl.when(pl.col("education").is_in([0, 5, 6])).then(4).otherwise(pl.col("education")),
            marriage=pl.when(pl.col("marriage") == 0).then(3).otherwise(pl.col("marriage")),
        )
        checks = [
            quality.row_count(s, 0, expected=exp["rows"]),
            quality.class_count(s, "default", 1, exp["positives"]),
            quality.unique(s, ["id"]),
            *quality.not_null(s, s.columns),
            quality.in_range(s, "limit_bal", 1, 1e7),
            quality.in_range(s, "age", 18, 100),
            quality.allowed_values(s, "sex", [1, 2]),
            quality.allowed_values(s, "pay_0", list(range(-2, 9))),
            quality.outlier_rate(s, "bill_amt1"),
        ]
        sp = lake.write_parquet(s, "silver", "uci_credit")
        st["rows_out"] = s.height
    vs = lake.register_version(name="uci_credit", layer="silver", path=sp, source_key=SOURCE, rows=s.height, columns=s.width,
                               schema=lake.schema_of(s), prof=lake.profile(s), run_id=ctx.run_id, upstream_ids=[vb],
                               transform="lower-case, undocumented codes mapped to 'other'")
    lake.set_quality_status(vs, quality.persist(vs, checks))
    quality.enforce(checks)
    with ctx.step("gold_features") as st:
        g = build(s).select(["id", "default", *PROTECTED, *FEATURES]).collect()
        gp = lake.write_parquet(g, "gold", "credit_features_uci")
        st["rows_out"] = g.height
    gchecks = [*quality.not_null(g, FEATURES)]
    vg = lake.register_version(name="credit_features_uci", layer="gold", path=gp, source_key=SOURCE, rows=g.height,
                               columns=g.width, schema=lake.schema_of(g), prof=lake.profile(g), run_id=ctx.run_id,
                               upstream_ids=[vs], transform="bankverse.modules.credit.features.build")
    lake.set_quality_status(vg, quality.persist(vg, gchecks))
    quality.enforce(gchecks)
    return {"rows": g.height, "defaults": int(g.get_column("default").sum()), "facts_verified": True}
