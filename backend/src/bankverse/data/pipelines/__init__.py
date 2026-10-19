"""Pipeline registry. Each pipeline is an ordered list of stages; each stage is a tracked run.

Stages are idempotent: re-running on identical inputs re-registers the same dataset fingerprints.
Without ``force``, a stage whose output already exists is skipped (resumable pipelines).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bankverse.data import lake
from bankverse.data.runner import RunContext, run_tracked

Stage = tuple[str, Callable[[RunContext], dict[str, Any]], tuple[str, str] | None, str]


def _registry() -> dict[str, list[Stage]]:
    from bankverse.data.pipelines import amlsim, banking77, bank_marketing, finqa, macro, tabformer, uci_credit

    return {
        "tabformer": [
            ("bronze", tabformer.bronze, ("bronze", "tabformer_transactions"), "polars"),
            ("silver", tabformer.silver, ("silver", "tabformer_transactions"), "polars"),
            ("gold_features", tabformer.gold_features, ("gold", "fraud_features_tabformer"), "polars"),
            ("spark_parity", tabformer.spark_daily_parity, ("gold", "card_daily_2019"), "spark"),
        ],
        "amlsim": [("all", amlsim.run, ("gold", "aml_node_features"), "polars")],
        "uci_credit": [("all", uci_credit.run, ("gold", "credit_features_uci"), "polars")],
        "bank_marketing": [("all", bank_marketing.run, ("gold", "bank_marketing"), "polars")],
        "banking77": [("all", banking77.run, ("silver", "banking77"), "polars")],
        "macro": [("all", macro.run, ("silver", "macro_monthly"), "polars")],
        "finqa": [("all", finqa.run, ("silver", "finqa_pages"), "python")],
    }


def available() -> list[str]:
    return list(_registry())


def run_pipeline(name: str, *, force: bool = False, triggered_by: str = "cli") -> dict[str, Any]:
    stages = _registry().get(name)
    if stages is None:
        raise KeyError(f"Unknown pipeline {name!r}. Available: {available()}")
    results: dict[str, Any] = {}
    for stage_name, fn, output, engine in stages:
        if not force and output is not None and lake.layer_dir(*output).exists():
            results[stage_name] = {"status": "skipped (output exists; use --force)"}
            continue
        results[stage_name] = run_tracked(f"{name}:{stage_name}", fn, engine=engine, triggered_by=triggered_by)
    return results
