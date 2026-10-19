"""Dispatch training of a model family (CLI: ``bankverse train <family>``)."""

from __future__ import annotations

from typing import Any


def train_family(family: str, *, trained_by: str = "pipeline", quick: bool = False) -> dict[str, Any]:
    if family == "fraud":
        from bankverse.modules.fraud.train import train
    elif family == "aml":
        from bankverse.modules.aml.train import train
    elif family == "credit":
        from bankverse.modules.credit.train import train
    elif family == "customer":
        from bankverse.modules.customer.train import train
    elif family == "documents":
        from bankverse.modules.documents.train import train
    elif family == "copilot":
        from bankverse.modules.copilot.train import train
    elif family == "forecasting":
        from bankverse.modules.forecasting.train import train
    else:
        raise KeyError(f"Unknown model family {family!r}")
    return train(trained_by=trained_by, quick=quick)
