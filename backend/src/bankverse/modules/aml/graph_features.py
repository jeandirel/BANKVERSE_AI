"""Account-level graph & temporal features for AML (shared by AMLSim training and bank scoring).

Two feature groups:
* STRUCTURAL — degrees, counterparty concentration, temporal bursts of distinct counterparties,
  pass-through timing, reciprocity, strongly-connected-component size, PageRank, clustering and
  Louvain community size. Expressed as within-graph percentile ranks or ratios, so they are
  comparable between graphs of different size/scale (simulator vs. bank).
* AMOUNT — amount levels and dispersion (within-graph percentiles). In IBM AMLSim, laundering
  transfers are systematically smaller than normal ones (simulator artefact); these features are
  therefore benchmarked separately and excluded from the deployed model.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import numpy as np
import polars as pl

STRUCTURAL = [
    "in_deg_pct", "out_deg_pct", "in_cnt_pct", "out_cnt_pct", "deg_ratio", "reciprocity",
    "max_in_unique_w_pct", "max_out_unique_w_pct", "in_burst_ratio", "out_burst_ratio", "pass_through_ratio",
    "scc_small", "scc_size_log", "pagerank_pct", "clustering", "community_size_log", "active_span_ratio",
    "flow_balance_cnt",
]
AMOUNT = ["in_amt_mean_pct", "out_amt_mean_pct", "amt_cv", "flow_balance_amt", "max_in_amt_w_pct"]
ALL = STRUCTURAL + AMOUNT


def _pct(s: pl.Expr) -> pl.Expr:
    return (s.rank("average") / s.count()).fill_null(0.0)


def node_features(edges: pl.DataFrame, nodes: list[Any] | None = None, *, window: int = 10,
                  pass_through_steps: int = 3, seed: int = 7) -> pl.DataFrame:
    """edges: columns src, dst, amount (float), step (int, e.g. day index). Returns one row per node."""
    e = edges.select(pl.col("src").cast(pl.Utf8), pl.col("dst").cast(pl.Utf8), pl.col("amount").cast(pl.Float64),
                     pl.col("step").cast(pl.Int64))
    node_ids = pl.concat([e.select(pl.col("src").alias("node")), e.select(pl.col("dst").alias("node"))]).unique()
    if nodes is not None:
        node_ids = pl.concat([node_ids, pl.DataFrame({"node": [str(n) for n in nodes]})]).unique()

    out_agg = e.group_by("src").agg(out_cnt=pl.len(), out_deg=pl.col("dst").n_unique(), out_amt_mean=pl.col("amount").mean(),
                                    out_amt_sum=pl.col("amount").sum(), out_first=pl.col("step").min(), out_last=pl.col("step").max()
                                    ).rename({"src": "node"})
    in_agg = e.group_by("dst").agg(in_cnt=pl.len(), in_deg=pl.col("src").n_unique(), in_amt_mean=pl.col("amount").mean(),
                                   in_amt_sum=pl.col("amount").sum(), in_amt_std=pl.col("amount").std(),
                                   in_first=pl.col("step").min(), in_last=pl.col("step").max()).rename({"dst": "node"})
    # Temporal bursts: distinct counterparties within tumbling windows of `window` steps.
    win_in = (e.with_columns(w=pl.col("step") // window).group_by(["dst", "w"])
              .agg(u=pl.col("src").n_unique(), c=pl.len(), a=pl.col("amount").sum())
              .group_by("dst").agg(max_in_unique_w=pl.col("u").max(), max_in_cnt_w=pl.col("c").max(),
                                    max_in_amt_w=pl.col("a").max()).rename({"dst": "node"}))
    win_out = (e.with_columns(w=pl.col("step") // window).group_by(["src", "w"])
               .agg(u=pl.col("dst").n_unique(), c=pl.len())
               .group_by("src").agg(max_out_unique_w=pl.col("u").max(), max_out_cnt_w=pl.col("c").max()).rename({"src": "node"}))
    # Pass-through: share of incoming transfers followed by an outgoing transfer within k steps.
    ins = e.select(node=pl.col("dst"), t_in=pl.col("step")).sort(["node", "t_in"])
    outs = e.select(node=pl.col("src"), t_out=pl.col("step")).sort(["node", "t_out"])
    pt = (ins.join_asof(outs, left_on="t_in", right_on="t_out", by="node", strategy="forward")
          .with_columns(hit=((pl.col("t_out") - pl.col("t_in")) <= pass_through_steps).fill_null(False))
          .group_by("node").agg(pass_through_ratio=pl.col("hit").mean()))
    # Reciprocity: counterparties seen in both directions.
    pairs_out = e.select(node=pl.col("src"), cp=pl.col("dst")).unique()
    pairs_in = e.select(node=pl.col("dst"), cp=pl.col("src")).unique()
    recip = (pairs_out.join(pairs_in, on=["node", "cp"], how="inner").group_by("node").agg(n_recip=pl.len()))

    df = (node_ids.join(out_agg, on="node", how="left").join(in_agg, on="node", how="left")
          .join(win_in, on="node", how="left").join(win_out, on="node", how="left").join(pt, on="node", how="left")
          .join(recip, on="node", how="left"))
    zero_cols = ["out_cnt", "out_deg", "in_cnt", "in_deg", "out_amt_sum", "in_amt_sum", "max_in_unique_w", "max_in_cnt_w",
                 "max_in_amt_w", "max_out_unique_w", "max_out_cnt_w", "n_recip", "pass_through_ratio"]
    # Counts come back as unsigned integers: cast to signed before any subtraction (avoids wrap-around).
    df = df.with_columns([pl.col(c).fill_null(0).cast(pl.Float64) for c in zero_cols])

    # Graph-level structure (networkx)
    G = nx.DiGraph()
    G.add_nodes_from(df.get_column("node").to_list())
    agg_edges = e.group_by(["src", "dst"]).agg(w=pl.col("amount").sum(), n=pl.len())
    G.add_weighted_edges_from(agg_edges.select(["src", "dst", "n"]).iter_rows())
    scc_size: dict[str, int] = {}
    for comp in nx.strongly_connected_components(G):
        size = len(comp)
        for n in comp:
            scc_size[n] = size
    pr = nx.pagerank(G, alpha=0.85, weight="weight", max_iter=200, tol=1e-8)
    U = G.to_undirected()
    clust = nx.clustering(U)
    comms = nx.community.louvain_communities(U, weight="weight", seed=seed, resolution=1.0)
    comm_size: dict[str, int] = {}
    comm_id: dict[str, int] = {}
    for i, c in enumerate(comms):
        for n in c:
            comm_size[n] = len(c)
            comm_id[n] = i
    ids = df.get_column("node").to_list()
    span_total = max(int(e.get_column("step").max() or 1) - int(e.get_column("step").min() or 0), 1)
    df = df.with_columns(
        scc_size=pl.Series([scc_size.get(n, 1) for n in ids]),
        pagerank=pl.Series([pr.get(n, 0.0) for n in ids]),
        clustering=pl.Series([float(clust.get(n, 0.0)) for n in ids]),
        community_size=pl.Series([comm_size.get(n, 1) for n in ids]),
        community_id=pl.Series([comm_id.get(n, -1) for n in ids]),
        first=pl.min_horizontal("out_first", "in_first"), last=pl.max_horizontal("out_last", "in_last"),
    )
    giant = max(scc_size.values()) if scc_size else 1
    tot_cnt = pl.col("in_cnt") + pl.col("out_cnt")
    df = df.with_columns(
        in_deg_pct=_pct(pl.col("in_deg")), out_deg_pct=_pct(pl.col("out_deg")),
        in_cnt_pct=_pct(pl.col("in_cnt")), out_cnt_pct=_pct(pl.col("out_cnt")),
        deg_ratio=(pl.col("in_deg") / (pl.col("in_deg") + pl.col("out_deg")).clip(1, None)),
        reciprocity=(pl.col("n_recip") / (pl.col("in_deg") + pl.col("out_deg")).clip(1, None)),
        max_in_unique_w_pct=_pct(pl.col("max_in_unique_w")), max_out_unique_w_pct=_pct(pl.col("max_out_unique_w")),
        in_burst_ratio=(pl.col("max_in_cnt_w") / pl.col("in_cnt").clip(1, None)),
        out_burst_ratio=(pl.col("max_out_cnt_w") / pl.col("out_cnt").clip(1, None)),
        scc_small=((pl.col("scc_size") >= 2) & (pl.col("scc_size") <= 30) & (pl.col("scc_size") < giant)).cast(pl.Int8),
        scc_size_log=pl.col("scc_size").log1p(),
        pagerank_pct=_pct(pl.col("pagerank")),
        community_size_log=pl.col("community_size").log1p(),
        active_span_ratio=((pl.col("last") - pl.col("first")).fill_null(0) / span_total).clip(0, 1),
        flow_balance_cnt=((pl.col("out_cnt") - pl.col("in_cnt")) / tot_cnt.clip(1, None)),
        in_amt_mean_pct=_pct(pl.col("in_amt_mean").fill_null(0)), out_amt_mean_pct=_pct(pl.col("out_amt_mean").fill_null(0)),
        amt_cv=(pl.col("in_amt_std").fill_null(0) / pl.col("in_amt_mean").fill_null(0).clip(1e-6, None)).clip(0, 10),
        flow_balance_amt=((pl.col("out_amt_sum") - pl.col("in_amt_sum")) /
                          (pl.col("out_amt_sum") + pl.col("in_amt_sum")).clip(1e-6, None)),
        max_in_amt_w_pct=_pct(pl.col("max_in_amt_w")),
    )
    return df


def edge_index(edges: pl.DataFrame, node_order: list[str]) -> np.ndarray:
    """Edge index (2, E) over `node_order` for PyTorch Geometric, both directions, de-duplicated."""
    idx = {n: i for i, n in enumerate(node_order)}
    pairs = edges.select(pl.col("src").cast(pl.Utf8), pl.col("dst").cast(pl.Utf8)).unique()
    s = np.array([idx[x] for x in pairs.get_column("src").to_list()], dtype=np.int64)
    d = np.array([idx[x] for x in pairs.get_column("dst").to_list()], dtype=np.int64)
    ei = np.vstack([np.r_[s, d], np.r_[d, s]])
    return np.unique(ei, axis=1)
