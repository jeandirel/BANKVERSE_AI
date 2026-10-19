"""Interpretable AML typology rules on bank transfers. Each hit carries the transfers that justify it,
so that an analyst can verify the evidence directly (no black box)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import polars as pl

EEA = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT",
       "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO"}


@dataclass
class RuleHit:
    rule: str
    typology: str
    weight: float
    detail: str
    transfer_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuleConfig:
    fan_window_days: int = 7
    fan_min_counterparties: int = 5
    fan_min_total: float = 20_000
    structuring_low: float = 8_500
    structuring_high: float = 9_999.99
    structuring_window_days: int = 10
    structuring_min_count: int = 3
    pass_through_hours: int = 72
    pass_through_min_in: float = 15_000
    pass_through_ratio: float = 0.85
    cycle_max_len: int = 6
    cycle_hop_days: int = 7
    cycle_max_decay: float = 0.15
    cross_border_min: float = 50_000


def _windows(rows: list[dict[str, Any]], days: int) -> list[list[dict[str, Any]]]:
    rows = sorted(rows, key=lambda r: r["ts"])
    out, j = [], 0
    for i in range(len(rows)):
        while rows[i]["ts"] - rows[j]["ts"] > timedelta(days=days):
            j += 1
        out.append(rows[j:i + 1])
    return out


def evaluate(transfers: pl.DataFrame, cfg: RuleConfig = RuleConfig()) -> dict[str, list[RuleHit]]:
    """transfers columns: id, src_account_id, dst_account_id, counterparty_iban, counterparty_country, ts, amount,
    channel, direction. Returns rule hits per internal account id."""
    hits: dict[str, list[RuleHit]] = defaultdict(list)
    rows = transfers.to_dicts()
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["dst_account_id"]:
            incoming[str(r["dst_account_id"])].append(r)
        if r["src_account_id"]:
            outgoing[str(r["src_account_id"])].append(r)

    def cp_in(r: dict[str, Any]) -> str:
        return str(r["src_account_id"] or r["counterparty_iban"] or f"cash:{r['id']}")

    def cp_out(r: dict[str, Any]) -> str:
        return str(r["dst_account_id"] or r["counterparty_iban"] or "unknown")

    for acc, ins in incoming.items():
        best: list[dict[str, Any]] = []
        for w in _windows([r for r in ins if r["channel"] != "cash"], cfg.fan_window_days):
            if (len({cp_in(r) for r in w}) >= cfg.fan_min_counterparties and sum(r["amount"] for r in w) >= cfg.fan_min_total
                    and len(w) > len(best)):
                best = w
        if best:
            hits[acc].append(RuleHit("AML-R01 fan-in", "fan_in", 0.35,
                                     f"{len({cp_in(r) for r in best})} émetteurs distincts en {cfg.fan_window_days} j, "
                                     f"total {sum(r['amount'] for r in best):,.0f} €", [str(r["id"]) for r in best]))
        cand = [r for r in ins if cfg.structuring_low <= r["amount"] <= cfg.structuring_high]
        bestw: list[dict[str, Any]] = []
        for w in _windows(cand, cfg.structuring_window_days):
            if len(w) >= cfg.structuring_min_count and len(w) > len(bestw):
                bestw = w
        if bestw:
            hits[acc].append(RuleHit("AML-R03 fractionnement", "structuring", 0.35,
                                     f"{len(bestw)} dépôts/virements entre {cfg.structuring_low:,.0f} et 10 000 € en "
                                     f"{cfg.structuring_window_days} j", [str(r["id"]) for r in bestw]))
        # pass-through: large inflow then most of it leaves quickly
        outs = sorted(outgoing.get(acc, []), key=lambda r: r["ts"])
        for r_in in sorted(ins, key=lambda r: r["ts"]):
            if r_in["amount"] < cfg.pass_through_min_in:
                continue
            after = [o for o in outs if r_in["ts"] <= o["ts"] <= r_in["ts"] + timedelta(hours=cfg.pass_through_hours)
                     and cp_out(o) != cp_in(r_in)]
            total = sum(o["amount"] for o in after)
            if after and total >= cfg.pass_through_ratio * r_in["amount"]:
                hits[acc].append(RuleHit("AML-R04 transit rapide", "pass_through", 0.3,
                                         f"{r_in['amount']:,.0f} € reçus puis {total:,.0f} € réémis en moins de "
                                         f"{cfg.pass_through_hours} h", [str(r_in["id"]), *[str(o["id"]) for o in after]]))
                break

    for acc, outs in outgoing.items():
        best = []
        for w in _windows([r for r in outs if r["channel"] != "cash"], cfg.fan_window_days):
            if (len({cp_out(r) for r in w}) >= cfg.fan_min_counterparties and sum(r["amount"] for r in w) >= cfg.fan_min_total
                    and len(w) > len(best)):
                best = w
        if best:
            hits[acc].append(RuleHit("AML-R02 fan-out", "fan_out", 0.3,
                                     f"{len({cp_out(r) for r in best})} bénéficiaires distincts en {cfg.fan_window_days} j, "
                                     f"total {sum(r['amount'] for r in best):,.0f} €", [str(r["id"]) for r in best]))
        xb = [r for r in outs if r["channel"] == "swift" and (r["counterparty_country"] or "FR") not in EEA
              and r["amount"] >= cfg.cross_border_min]
        if xb:
            hits[acc].append(RuleHit("AML-R06 transfert hors EEE", "cross_border", 0.15,
                                     f"{len(xb)} virement(s) ≥ {cfg.cross_border_min:,.0f} € hors EEE",
                                     [str(r["id"]) for r in xb]))

    # Cycles among internal accounts: A→B→…→A with time-ordered hops and limited amount decay
    internal = [r for r in rows if r["src_account_id"] and r["dst_account_id"]]
    by_src: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in internal:
        by_src[str(r["src_account_id"])].append(r)
    found: set[frozenset[str]] = set()

    def dfs(start: str, node: str, path: list[dict[str, Any]], visited: set[str]) -> None:
        if len(path) > cfg.cycle_max_len:
            return
        for r in by_src.get(node, []):
            if path:
                prev = path[-1]
                if not (prev["ts"] <= r["ts"] <= prev["ts"] + timedelta(days=cfg.cycle_hop_days)):
                    continue
                if r["amount"] < prev["amount"] * (1 - cfg.cycle_max_decay) or r["amount"] > prev["amount"] * 1.02:
                    continue
            nxt = str(r["dst_account_id"])
            if nxt == start and len(path) >= 2:
                cyc = [*path, r]
                key = frozenset(str(x["id"]) for x in cyc)
                if key not in found:
                    found.add(key)
                    members = {str(x["src_account_id"]) for x in cyc}
                    for m in members:
                        hits[m].append(RuleHit("AML-R05 circularité", "cycle", 0.4,
                                               f"Circuit de {len(cyc)} comptes, {cyc[0]['amount']:,.0f} € → "
                                               f"{cyc[-1]['amount']:,.0f} €", [str(x["id"]) for x in cyc]))
                continue
            if nxt not in visited:
                dfs(start, nxt, [*path, r], visited | {nxt})

    for start in list(by_src):
        dfs(start, start, [], {start})
    return hits


def combine(rule_hits: list[RuleHit]) -> float:
    """Noisy-OR combination of rule weights (distinct typologies)."""
    best: dict[str, float] = {}
    for h in rule_hits:
        best[h.typology] = max(best.get(h.typology, 0.0), h.weight)
    p = 1.0
    for w in best.values():
        p *= 1 - w
    return round(1 - p, 4)
