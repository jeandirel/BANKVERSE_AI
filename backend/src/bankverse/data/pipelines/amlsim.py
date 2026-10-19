"""IBM AMLSim samples: raw tgz → bronze (nodes, transactions) → silver (validated) → gold node features."""

from __future__ import annotations

import tarfile
from typing import Any

import polars as pl

from bankverse.data import lake, quality
from bankverse.data.runner import RunContext
from bankverse.modules.aml.graph_features import ALL, node_features

SOURCE = "ibm_amlsim_samples"
SCENARIOS = ("20K_cycle200", "20K_fanin200", "20K_fanin200cycle200")


def run(ctx: RunContext) -> dict[str, Any]:
    raw = lake.layer_dir("raw", SOURCE)
    nodes_parts, edge_parts = [], []
    with ctx.step("extract_and_bronze") as st:
        for sc in SCENARIOS:
            with tarfile.open(raw / f"{sc}.tgz") as tf:
                n = pl.read_csv(tf.extractfile(f"{sc}/nodes.csv").read())  # type: ignore[union-attr]
                t = pl.read_csv(tf.extractfile(f"{sc}/transactions.csv").read())  # type: ignore[union-attr]
            nodes_parts.append(n.with_columns(scenario=pl.lit(sc)))
            edge_parts.append(t.with_columns(scenario=pl.lit(sc)))
        nodes = pl.concat(nodes_parts)
        edges = pl.concat(edge_parts)
        b1 = lake.write_parquet(nodes, "bronze", "amlsim_nodes")
        b2 = lake.write_parquet(edges, "bronze", "amlsim_transactions")
        st["rows_out"] = nodes.height + edges.height
    raw_id = lake.current_version_id(SOURCE, "raw")
    bn = lake.register_version(name="amlsim_nodes", layer="bronze", path=b1, source_key=SOURCE, rows=nodes.height,
                               columns=nodes.width, schema=lake.schema_of(nodes), run_id=ctx.run_id,
                               upstream_ids=[raw_id] if raw_id else [], transform="untar + csv→parquet")
    be = lake.register_version(name="amlsim_transactions", layer="bronze", path=b2, source_key=SOURCE, rows=edges.height,
                               columns=edges.width, schema=lake.schema_of(edges), run_id=ctx.run_id,
                               upstream_ids=[raw_id] if raw_id else [], transform="untar + csv→parquet")
    with ctx.step("silver_validate") as st:
        nodes = nodes.rename({"nodeid": "node", "isFraud": "is_laundering", "init_balance": "init_balance",
                              "fraudStep": "fraud_step"}).with_columns(pl.col("node").cast(pl.Utf8))
        edges = edges.rename({"sourceNodeId": "src", "targetNodeId": "dst", "value": "amount", "time": "step"}).with_columns(
            pl.col("src").cast(pl.Utf8), pl.col("dst").cast(pl.Utf8))
        before = edges.height
        edges = edges.unique(maintain_order=True)
        checks = [
            quality.unique(nodes, ["scenario", "node"]),
            *quality.not_null(nodes, ["node", "is_laundering"]),
            *quality.not_null(edges, ["src", "dst", "amount", "step"]),
            quality.in_range(edges, "amount", 0, None),
            quality.in_range(edges, "step", 0, 1000),
            quality.allowed_values(nodes, "is_laundering", [0, 1]),
            quality.CheckResult("referential_integrity_edges", "pass" if edges.join(
                nodes.select("scenario", pl.col("node").alias("src")), on=["scenario", "src"], how="anti").height == 0 else "fail",
                observed=float(edges.join(nodes.select("scenario", pl.col("node").alias("src")), on=["scenario", "src"], how="anti").height),
                threshold=0),
        ]
        s1 = lake.write_parquet(nodes, "silver", "amlsim_nodes")
        s2 = lake.write_parquet(edges, "silver", "amlsim_transactions")
        st["rows_out"] = edges.height
        st["details"]["duplicate_edges_removed"] = before - edges.height
    vn = lake.register_version(name="amlsim_nodes", layer="silver", path=s1, source_key=SOURCE, rows=nodes.height,
                               columns=nodes.width, schema=lake.schema_of(nodes), prof=lake.profile(nodes), run_id=ctx.run_id,
                               upstream_ids=[bn], transform="rename/typing")
    ve = lake.register_version(name="amlsim_transactions", layer="silver", path=s2, source_key=SOURCE, rows=edges.height,
                               columns=edges.width, schema=lake.schema_of(edges), prof=lake.profile(edges), run_id=ctx.run_id,
                               upstream_ids=[be], transform="rename/typing, dedup")
    status = quality.persist(ve, checks)
    lake.set_quality_status(ve, status)
    lake.set_quality_status(vn, status)
    quality.enforce(checks)
    feats = []
    for sc in SCENARIOS:
        with ctx.step(f"graph_features_{sc}") as st:
            e = edges.filter(pl.col("scenario") == sc)
            nd = nodes.filter(pl.col("scenario") == sc)
            f = node_features(e, nd.get_column("node").to_list(), window=10, pass_through_steps=3)
            f = f.join(nd.select("node", "is_laundering", "init_balance"), on="node", how="left").with_columns(scenario=pl.lit(sc))
            feats.append(f.select(["scenario", "node", "is_laundering", "init_balance", "community_id", *ALL]))
            st["rows_out"] = f.height
    gold = pl.concat(feats)
    gcheck = [*quality.not_null(gold, ALL), quality.row_count(gold, 60_000)]
    g = lake.write_parquet(gold, "gold", "aml_node_features")
    vg = lake.register_version(name="aml_node_features", layer="gold", path=g, source_key=SOURCE, rows=gold.height,
                               columns=gold.width, schema=lake.schema_of(gold), prof=lake.profile(gold), run_id=ctx.run_id,
                               upstream_ids=[vn, ve], transform="bankverse.modules.aml.graph_features.node_features")
    lake.set_quality_status(vg, quality.persist(vg, gcheck))
    quality.enforce(gcheck)
    counts = gold.group_by("scenario").agg(n=pl.len(), laundering=pl.col("is_laundering").sum()).sort("scenario").to_dicts()
    return {"nodes": nodes.height, "transactions": edges.height, "gold_rows": gold.height, "scenarios": counts}
