"""Daily card aggregates computed with PySpark (portable to AWS Glue 5 / EMR Serverless) and a
reference Polars implementation used for parity testing.

Run standalone on Glue/EMR::

    spark-submit card_daily.py s3://<lake>/silver/tabformer_transactions 2019 s3://<lake>/gold/card_daily_2019
"""

from __future__ import annotations

import sys

import polars as pl


def spark_card_daily(silver_path: str, year: int) -> pl.DataFrame:
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    spark = (
        SparkSession.builder.appName("bankverse-card-daily")
        .master("local[2]")
        .config("spark.driver.memory", "2g")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )
    try:
        df = spark.read.parquet(silver_path).where(F.col("year") == year)
        agg = (
            df.groupBy("card_key", F.to_date("ts").alias("day"))
            .agg(
                F.count(F.lit(1)).alias("n_tx"),
                F.sum(F.abs("amount")).alias("amount_abs_sum"),
                F.sum(F.col("is_fraud").cast("int")).alias("n_fraud"),
                F.countDistinct("merchant_id").alias("n_merchants"),
            )
        )
        rows = agg.toPandas()
    finally:
        spark.stop()
    return pl.from_pandas(rows).with_columns(
        pl.col("day").cast(pl.Date), pl.col("n_tx").cast(pl.Int64), pl.col("n_fraud").cast(pl.Int64),
        pl.col("n_merchants").cast(pl.Int64),
    ).sort(["card_key", "day"])


def polars_card_daily(silver_path: str, year: int) -> pl.DataFrame:
    lf = pl.scan_parquet(f"{silver_path}/**/*.parquet", hive_partitioning=True).filter(pl.col("year") == year)
    return (
        lf.group_by("card_key", pl.col("ts").dt.date().alias("day"))
        .agg(
            n_tx=pl.len().cast(pl.Int64),
            amount_abs_sum=pl.col("amount").abs().sum(),
            n_fraud=pl.col("is_fraud").cast(pl.Int64).sum(),
            n_merchants=pl.col("merchant_id").n_unique().cast(pl.Int64),
        )
        .sort(["card_key", "day"])
        .collect()
    )


if __name__ == "__main__":  # pragma: no cover - entry point for spark-submit
    src, yr, dst = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    spark_card_daily(src, yr).write_parquet(dst)
