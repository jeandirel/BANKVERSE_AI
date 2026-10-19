"""Declarative data-quality checks executed on every silver/gold dataset.

Each check returns a :class:`CheckResult`; results are persisted in ``platform.quality_results``
and a ``fail`` on a *blocking* check stops the pipeline (no silent propagation of bad data).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from bankverse.db.models.platform import QualityResult
from bankverse.db.session import control_session


@dataclass
class CheckResult:
    name: str
    status: str  # pass | warn | fail
    column: str | None = None
    observed: float | None = None
    threshold: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    blocking: bool = True


class QualityError(Exception):
    def __init__(self, failures: list[CheckResult]) -> None:
        super().__init__("; ".join(f"{f.name}({f.column}) observed={f.observed}" for f in failures))
        self.failures = failures


def row_count(df: pl.DataFrame, minimum: int, *, expected: int | None = None) -> CheckResult:
    n = df.height
    if expected is not None:
        return CheckResult("row_count_matches_source", "pass" if n == expected else "fail", observed=n,
                           threshold=expected, details={"expected": expected})
    return CheckResult("row_count_min", "pass" if n >= minimum else "fail", observed=n, threshold=minimum)


def not_null(df: pl.DataFrame, cols: Sequence[str], max_rate: float = 0.0, *, blocking: bool = True) -> list[CheckResult]:
    out = []
    for c in cols:
        rate = df.get_column(c).null_count() / max(df.height, 1)
        status = "pass" if rate <= max_rate else ("fail" if blocking else "warn")
        out.append(CheckResult("null_rate", status, column=c, observed=round(rate, 6), threshold=max_rate,
                               blocking=blocking))
    return out


def unique(df: pl.DataFrame, cols: Sequence[str]) -> CheckResult:
    dup = df.height - df.select(list(cols)).unique().height
    return CheckResult("unique_key", "pass" if dup == 0 else "fail", column=",".join(cols), observed=dup, threshold=0)


def in_range(df: pl.DataFrame, col: str, lo: float | None, hi: float | None, *, max_rate: float = 0.0,
             blocking: bool = True) -> CheckResult:
    s = df.get_column(col)
    mask = pl.Series([False] * df.height)
    if lo is not None:
        mask = mask | (s < lo).fill_null(False)
    if hi is not None:
        mask = mask | (s > hi).fill_null(False)
    rate = float(mask.sum()) / max(df.height, 1)
    status = "pass" if rate <= max_rate else ("fail" if blocking else "warn")
    return CheckResult("value_range", status, column=col, observed=round(rate, 6), threshold=max_rate,
                       details={"lo": lo, "hi": hi}, blocking=blocking)


def allowed_values(df: pl.DataFrame, col: str, allowed: Sequence[Any], *, blocking: bool = True) -> CheckResult:
    bad = df.filter(~pl.col(col).is_in(list(allowed)) & pl.col(col).is_not_null())
    vals = bad.get_column(col).unique().head(10).to_list()
    status = "pass" if bad.height == 0 else ("fail" if blocking else "warn")
    return CheckResult("allowed_values", status, column=col, observed=bad.height, threshold=0,
                       details={"unexpected": [str(v) for v in vals]}, blocking=blocking)


def class_count(df: pl.DataFrame, col: str, value: Any, expected: int) -> CheckResult:
    n = df.filter(pl.col(col) == value).height
    return CheckResult("class_count_matches_source", "pass" if n == expected else "fail", column=col, observed=n,
                       threshold=expected)


def outlier_rate(df: pl.DataFrame, col: str, *, z: float = 6.0, warn_above: float = 0.01) -> CheckResult:
    """Robust outlier rate (|x - median| / (1.4826 * MAD) > z). Informational (non-blocking)."""
    s = df.get_column(col).drop_nulls().cast(pl.Float64)
    if s.len() == 0:
        return CheckResult("outlier_rate", "pass", column=col, observed=0.0, blocking=False)
    med = s.median()
    mad = (s - med).abs().median() or 0.0
    if mad == 0:
        return CheckResult("outlier_rate", "pass", column=col, observed=0.0, blocking=False, details={"mad": 0})
    rate = float(((s - med).abs() / (1.4826 * mad) > z).mean())
    return CheckResult("outlier_rate", "warn" if rate > warn_above else "pass", column=col, observed=round(rate, 6),
                       threshold=warn_above, blocking=False, details={"z": z})


def persist(version_id: uuid.UUID, results: list[CheckResult]) -> str:
    with control_session(owner=True) as db:
        for r in results:
            db.add(QualityResult(dataset_version_id=version_id, check_name=r.name, column=r.column,
                                 status=r.status, observed=r.observed, threshold=r.threshold, details=r.details))
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warn" for r in results):
        return "warn"
    return "pass"


def enforce(results: list[CheckResult]) -> None:
    failures = [r for r in results if r.status == "fail" and r.blocking]
    if failures:
        raise QualityError(failures)
