"""Credit-card default features (UCI 'Default of Credit Card Clients' schema).

The *policy model* uses repayment behaviour only. Protected or sensitive attributes (sex, marital
status, age) and education (a potential proxy) are excluded from the model and kept for fairness
auditing only.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

MONTHS = 6
PAY = ["pay_0", "pay_2", "pay_3", "pay_4", "pay_5", "pay_6"]
BILL = [f"bill_amt{i}" for i in range(1, 7)]
PAID = [f"pay_amt{i}" for i in range(1, 7)]
PROTECTED = ["sex", "marriage", "age", "education"]
NTD_PER_EUR = 40.0  # documented normalisation factor for applications expressed in EUR (≈ 2005 exchange rate)

FEATURES = [
    "limit_bal_log", *PAY, "max_delay", "n_months_delayed", "n_months_paid_duly", "delay_trend",
    "util_last", "util_mean", "util_max", "util_trend", "pay_ratio_last", "pay_ratio_mean", "n_zero_payments",
    "bill_log_last", "paid_log_mean", "bill_growth",
]
FEATURE_LABELS = {
    "limit_bal_log": "Plafond accordé (log)", "pay_0": "Statut de paiement M-1", "pay_2": "Statut de paiement M-2",
    "pay_3": "Statut de paiement M-3", "pay_4": "Statut de paiement M-4", "pay_5": "Statut de paiement M-5",
    "pay_6": "Statut de paiement M-6", "max_delay": "Retard maximal (mois)", "n_months_delayed": "Mois en retard",
    "n_months_paid_duly": "Mois payés à échéance", "delay_trend": "Tendance des retards",
    "util_last": "Utilisation du plafond (dernier mois)", "util_mean": "Utilisation moyenne", "util_max": "Utilisation maximale",
    "util_trend": "Tendance d'utilisation", "pay_ratio_last": "Taux de remboursement (dernier mois)",
    "pay_ratio_mean": "Taux de remboursement moyen", "n_zero_payments": "Mois sans paiement",
    "bill_log_last": "Encours dernier relevé (log)", "paid_log_mean": "Paiement moyen (log)", "bill_growth": "Croissance de l'encours",
}


def build(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    """Input columns: limit_bal, pay_0.., bill_amt1.., pay_amt1.. (NT$ units)."""
    lf = frame.lazy() if isinstance(frame, pl.DataFrame) else frame
    lim = pl.col("limit_bal").clip(1, None)
    pays = [pl.col(c) for c in PAY]
    bills = [pl.col(c) for c in BILL]
    paids = [pl.col(c) for c in PAID]
    util = [(b / lim).clip(-1, 5) for b in bills]
    ratios = [(paids[i] / bills[i + 1].clip(1, None)).clip(0, 5) if i + 1 < MONTHS else (paids[i] / bills[i].clip(1, None)).clip(0, 5)
              for i in range(MONTHS)]
    return lf.with_columns(
        limit_bal_log=pl.col("limit_bal").clip(1, None).log(),
        max_delay=pl.max_horizontal(pays).clip(0, None),
        n_months_delayed=pl.sum_horizontal([(p >= 1).cast(pl.Int32) for p in pays]),
        n_months_paid_duly=pl.sum_horizontal([(p == -1).cast(pl.Int32) for p in pays]),
        delay_trend=(pl.mean_horizontal(pays[:3]) - pl.mean_horizontal(pays[3:])),
        util_last=util[0], util_mean=pl.mean_horizontal(util), util_max=pl.max_horizontal(util),
        util_trend=(pl.mean_horizontal(util[:3]) - pl.mean_horizontal(util[3:])),
        pay_ratio_last=ratios[0], pay_ratio_mean=pl.mean_horizontal(ratios),
        n_zero_payments=pl.sum_horizontal([(p <= 0).cast(pl.Int32) for p in paids]),
        bill_log_last=pl.col("bill_amt1").clip(0, None).log1p(),
        paid_log_mean=pl.mean_horizontal(paids).clip(0, None).log1p(),
        bill_growth=((pl.col("bill_amt1") - pl.col("bill_amt6")) / lim).clip(-5, 5),
    )


def from_application(features: dict[str, Any]) -> pl.DataFrame:
    """Converts a DEMO/pilot application (EUR amounts, most-recent month first) to the UCI schema."""
    f = NTD_PER_EUR
    row: dict[str, Any] = {"limit_bal": float(features["limit_bal"]) * f}
    for i, c in enumerate(PAY):
        row[c] = int(features["pay_status"][i])
    for i in range(MONTHS):
        row[BILL[i]] = float(features["bill_amt"][i]) * f
        row[PAID[i]] = float(features["pay_amt"][i]) * f
    return pl.DataFrame([row])


def risk_grade(pd_: float) -> str:
    for grade, upper in (("A", 0.05), ("B", 0.10), ("C", 0.20), ("D", 0.35)):
        if pd_ < upper:
            return grade
    return "E"


GRADE_BOUNDS = {"A": "< 5 %", "B": "5–10 %", "C": "10–20 %", "D": "20–35 %", "E": "≥ 35 %"}


def to_matrix(df: pl.DataFrame) -> np.ndarray:
    return df.select(FEATURES).to_numpy().astype(np.float32)
