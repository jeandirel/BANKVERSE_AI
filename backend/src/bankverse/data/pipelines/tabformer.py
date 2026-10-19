"""IBM TabFormer card transactions: raw → bronze → silver → gold (fraud features) + Spark parity job."""

from __future__ import annotations

import tarfile
from typing import Any

import polars as pl

from bankverse.data import lake, quality
from bankverse.data.runner import RunContext
from bankverse.modules.fraud.features import FEATURES, compute_features

SOURCE = "ibm_tabformer_cc"
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR",
    "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
SPLITS = {"train": (2013, 2017), "val": (2018, 2018), "test": (2019, 2019)}
N_CHUNKS = 8


def bronze(ctx: RunContext) -> dict[str, Any]:
    raw = lake.layer_dir("raw", SOURCE)
    csv = raw / "card_transaction.v1.csv"
    with ctx.step("extract_archive") as st:
        if not csv.exists():
            with tarfile.open(raw / "transactions.tgz") as tf:
                member = tf.getmember("card_transaction.v1.csv")
                tf.extract(member, raw, filter="data")
        st["details"]["csv_bytes"] = csv.stat().st_size
    with ctx.step("csv_to_parquet") as st:
        lf = pl.scan_csv(csv, schema_overrides={"Merchant Name": pl.Utf8, "Zip": pl.Utf8, "Time": pl.Utf8,
                                                "Amount": pl.Utf8, "Errors?": pl.Utf8})
        out = lake.layer_dir("bronze", "tabformer_transactions")
        out.mkdir(parents=True, exist_ok=True)
        lf.sink_parquet(out / "part-0.parquet", compression="zstd")
        n = pl.scan_parquet(out / "part-0.parquet").select(pl.len()).collect().item()
        st["rows_out"] = n
    checks = [quality.CheckResult("row_count_matches_source", "pass" if n == 24386900 else "fail", observed=n,
                                  threshold=24386900)]
    raw_id = lake.current_version_id(SOURCE, "raw")
    vid = lake.register_version(name="tabformer_transactions", layer="bronze", path=out, source_key=SOURCE, rows=n,
                                columns=15, schema=lake.schema_of(pl.scan_parquet(out / "part-0.parquet")),
                                run_id=ctx.run_id, upstream_ids=[raw_id] if raw_id else [],
                                transform="csv→parquet (typed, unmodified)")
    lake.set_quality_status(vid, quality.persist(vid, checks))
    quality.enforce(checks)
    return {"bronze_rows": n}


def _clean(lf: pl.LazyFrame) -> pl.LazyFrame:
    state = pl.col("Merchant State")
    return lf.select(
        user_id=pl.col("User").cast(pl.Int32),
        card_key=pl.format("u{}c{}", pl.col("User"), pl.col("Card")),
        ts=pl.datetime(pl.col("Year"), pl.col("Month"), pl.col("Day"),
                       pl.col("Time").str.slice(0, 2).cast(pl.Int32), pl.col("Time").str.slice(3, 2).cast(pl.Int32)),
        amount=pl.col("Amount").str.replace("$", "", literal=True).cast(pl.Float64),
        use_chip=pl.col("Use Chip"),
        merchant_id=pl.col("Merchant Name"),
        merchant_city=pl.col("Merchant City"),
        merchant_state=state,
        merchant_zip=pl.col("Zip").str.replace(r"\.0$", ""),
        merchant_country=pl.when(pl.col("Merchant City") == "ONLINE").then(pl.lit("ONLINE"))
        .when(state.is_null() | state.is_in(list(US_STATES))).then(pl.lit("US"))
        .otherwise(state),
        mcc=pl.col("MCC").cast(pl.Int32),
        errors=pl.col("Errors?").str.strip_chars_end(","),
        is_fraud=(pl.col("Is Fraud?") == "Yes"),
    )


def silver(ctx: RunContext) -> dict[str, Any]:
    """Processed year by year (bounded memory); exact duplicates can only occur within a year."""
    src = lake.layer_dir("bronze", "tabformer_transactions")
    bronze_lf = pl.scan_parquet(src / "*.parquet")
    years = sorted(bronze_lf.select(pl.col("Year").unique()).collect().to_series().to_list())
    target = lake.layer_dir("silver", "tabformer_transactions")
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        import shutil

        shutil.rmtree(tmp)
    totals = {"rows_in": 0, "rows_out": 0, "fraud": 0}
    checks: list[quality.CheckResult] = []
    sample_parts: list[pl.DataFrame] = []
    for y in years:
        with ctx.step(f"clean_year_{y}") as st:
            df = _clean(bronze_lf.filter(pl.col("Year") == y)).collect()
            before = df.height
            df = df.unique(maintain_order=True)
            checks += [
                *quality.not_null(df, ["card_key", "ts", "amount", "use_chip", "merchant_id", "mcc"]),
                quality.allowed_values(df, "use_chip", ["Swipe Transaction", "Chip Transaction", "Online Transaction"]),
                quality.in_range(df, "amount", -1e5, 1e5),
                quality.in_range(df, "mcc", 1000, 9999),
            ]
            (tmp / f"year={y}").mkdir(parents=True, exist_ok=True)
            df.write_parquet(tmp / f"year={y}" / "part-0.parquet", compression="zstd", statistics=True)
            totals["rows_in"] += before
            totals["rows_out"] += df.height
            totals["fraud"] += int(df.get_column("is_fraud").sum())
            sample_parts.append(df.sample(min(df.height, 10_000), seed=y))
            st["rows_in"], st["rows_out"] = before, df.height
            st["details"]["duplicates_removed"] = before - df.height
    import shutil

    if target.exists():
        shutil.rmtree(target)
    tmp.rename(target)
    sample = pl.concat(sample_parts)
    checks.append(quality.CheckResult("row_count_matches_bronze", "pass" if totals["rows_in"] == 24386900 else "fail",
                                      observed=totals["rows_in"], threshold=24386900))
    checks.append(quality.outlier_rate(sample, "amount"))
    # Collapse per-year results into one result per (check, column) — keep the worst observation.
    merged: dict[tuple[str, str | None], quality.CheckResult] = {}
    order = {"pass": 0, "warn": 1, "fail": 2}
    for c in checks:
        k = (c.name, c.column)
        if k not in merged or order[c.status] > order[merged[k].status] or (c.observed or 0) > (merged[k].observed or 0):
            merged[k] = c
    checks = list(merged.values())
    up = lake.current_version_id("tabformer_transactions", "bronze")
    vid = lake.register_version(name="tabformer_transactions", layer="silver", path=target, source_key=SOURCE,
                                rows=totals["rows_out"], columns=sample.width, schema=lake.schema_of(sample),
                                prof=lake.profile(sample), run_id=ctx.run_id, upstream_ids=[up] if up else [],
                                transform="parse amounts/timestamps, derive card key & country, exact dedup")
    lake.set_quality_status(vid, quality.persist(vid, checks))
    quality.enforce(checks)
    return {"silver_rows": totals["rows_out"], "fraud": totals["fraud"],
            "duplicates_removed": totals["rows_in"] - totals["rows_out"]}


def gold_features(ctx: RunContext) -> dict[str, Any]:
    """Features are computed per user chunk (cards never cross chunks, so windows are exact) and
    streamed to disk per split — bounded memory regardless of dataset size."""
    import shutil

    src = lake.layer_dir("silver", "tabformer_transactions")
    base = pl.scan_parquet(str(src / "**" / "*.parquet"), hive_partitioning=True).filter(
        (pl.col("ts") >= pl.datetime(2012, 12, 1)) & (pl.col("ts") < pl.datetime(2020, 1, 1))
    )
    target = lake.layer_dir("gold", "fraud_features_tabformer")
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    checks: list[quality.CheckResult] = []
    total = 0
    stats: dict[str, dict[str, int]] = {}
    schema: list[dict[str, str]] = []
    for k in range(N_CHUNKS):
        with ctx.step(f"features_chunk_{k}") as st:
            chunk = base.filter(pl.col("user_id") % N_CHUNKS == k)
            feats = compute_features(chunk, home_country="US").with_columns(
                split=pl.when(pl.col("ts").dt.year().is_between(*SPLITS["train"])).then(pl.lit("train"))
                .when(pl.col("ts").dt.year().is_between(*SPLITS["val"])).then(pl.lit("val"))
                .when(pl.col("ts").dt.year().is_between(*SPLITS["test"])).then(pl.lit("test"))
                .otherwise(pl.lit("burn_in"))
            ).filter(pl.col("split") != "burn_in")
            cols = ["card_key", "user_id", "ts", "merchant_id", "is_fraud", "split", *FEATURES]
            out = feats.select(cols).collect()
            schema = lake.schema_of(out)
            checks += [*quality.not_null(out, FEATURES),
                       quality.in_range(out, "mins_since_last_tx", 0, 30 * 24 * 60),
                       quality.in_range(out, "online_share_30d", 0, 1)]
            for split_name, part in out.partition_by("split", as_dict=True).items():
                name = split_name[0] if isinstance(split_name, tuple) else split_name
                d = tmp / f"split={name}"
                d.mkdir(parents=True, exist_ok=True)
                part.drop("split").write_parquet(d / f"part-{k}.parquet", compression="zstd", statistics=True)
                agg = stats.setdefault(name, {"n": 0, "fraud": 0})
                agg["n"] += part.height
                agg["fraud"] += int(part.get_column("is_fraud").sum())
            total += out.height
            st["rows_out"] = out.height
            del out
    if target.exists():
        shutil.rmtree(target)
    tmp.rename(target)
    checks.append(quality.CheckResult("row_count_min", "pass" if total >= 8_000_000 else "fail", observed=total,
                                      threshold=8_000_000))
    merged: dict[tuple[str, str | None], quality.CheckResult] = {}
    for c in checks:
        k2 = (c.name, c.column)
        if k2 not in merged or c.status == "fail" or (c.observed or 0) > (merged[k2].observed or 0):
            merged[k2] = c
    checks = list(merged.values())
    split_stats = [{"split": k, **v} for k, v in sorted(stats.items())]
    up = lake.current_version_id("tabformer_transactions", "silver")
    vid = lake.register_version(name="fraud_features_tabformer", layer="gold", path=target, source_key=SOURCE,
                                rows=total, columns=len(schema), schema=schema, prof={"splits": split_stats},
                                run_id=ctx.run_id, upstream_ids=[up] if up else [],
                                transform="bankverse.modules.fraud.features.compute_features (past-only windows)")
    lake.set_quality_status(vid, quality.persist(vid, checks))
    quality.enforce(checks)
    return {"gold_rows": total, "splits": split_stats}


def spark_daily_parity(ctx: RunContext) -> dict[str, Any]:
    """Distributed aggregation with PySpark (same code runs on AWS Glue / EMR) and parity check
    against the Polars implementation on the same silver data (2019)."""
    from bankverse.data.spark_jobs.card_daily import polars_card_daily, spark_card_daily

    src = lake.layer_dir("silver", "tabformer_transactions")
    with ctx.step("spark_aggregate") as st:
        spark_df = spark_card_daily(str(src), year=2019)
        st["rows_out"] = spark_df.height
    with ctx.step("polars_aggregate") as st:
        pol_df = polars_card_daily(str(src), year=2019)
        st["rows_out"] = pol_df.height
    with ctx.step("parity_check") as st:
        key = ["card_key", "day"]
        joined = spark_df.join(pol_df, on=key, how="full", suffix="_pl", coalesce=True)
        mismatches = joined.filter(
            (pl.col("n_tx") != pl.col("n_tx_pl")) | (pl.col("n_fraud") != pl.col("n_fraud_pl"))
            | ((pl.col("amount_abs_sum") - pl.col("amount_abs_sum_pl")).abs() > 1e-6)
            | pl.col("n_tx").is_null() | pl.col("n_tx_pl").is_null()
        ).height
        st["details"] = {"rows": joined.height, "mismatches": mismatches}
        if mismatches:
            raise RuntimeError(f"Spark/Polars parity failed: {mismatches} mismatching rows")
    out = lake.write_parquet(spark_df, "gold", "card_daily_2019")
    up = lake.current_version_id("tabformer_transactions", "silver")
    lake.register_version(name="card_daily_2019", layer="gold", path=out, source_key=SOURCE, rows=spark_df.height,
                          columns=spark_df.width, schema=lake.schema_of(spark_df), run_id=ctx.run_id,
                          upstream_ids=[up] if up else [], transform="PySpark daily card aggregation (parity-checked)")
    return {"spark_rows": spark_df.height, "parity_mismatches": 0}
