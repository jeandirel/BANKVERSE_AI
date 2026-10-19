"""Invariants of RBAC, drift computation and the agents' tool registry."""

from __future__ import annotations

import numpy as np

from bankverse.auth.rbac import ROLE_PERMISSIONS, Role
from bankverse.ml import drift
from bankverse.modules.agents import graph
from bankverse.modules.agents import tools as T


def test_least_privilege_matrix() -> None:
    auditor = ROLE_PERMISSIONS[Role.AUDITOR]
    assert not any(p.endswith((".write", ".decide", ".manage", ".assess", ".triage", ".upload", ".approve", ".deploy", ".train")) for p in auditor)
    assert "credit.assess" not in ROLE_PERMISSIONS[Role.FRAUD_ANALYST]
    assert "models.approve" not in ROLE_PERMISSIONS[Role.DATA_SCIENTIST]  # trainers cannot approve
    assert "agents.approve" not in ROLE_PERMISSIONS[Role.FRAUD_ANALYST]
    assert "platform.admin" not in ROLE_PERMISSIONS[Role.BANK_ORG_ADMIN]


def test_psi_detects_shift() -> None:
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, 5000)
    prof = drift.reference_profile({"x": ref}, {"x": ref})
    stable = drift.drift_report(prof, {"x": rng.normal(0, 1, 2000)})
    shifted = drift.drift_report(prof, {"x": rng.normal(1.5, 1, 2000)})
    assert stable["status"] == "stable" and shifted["status"] == "significant"
    assert drift.drift_report(prof, {"x": rng.normal(0, 1, 50)})["status"] == "insufficient_data"


def test_agent_tools_never_include_money_movement() -> None:
    forbidden = ("transfer", "virement", "payment", "block_card", "decide", "declare_sar", "approve_credit")
    assert not any(any(f in name for f in forbidden) for name in T.TOOLS)
    assert {t.kind for t in T.TOOLS.values()} == {"read", "action"}
    assert len(graph.AGENTS) == 8
    for spec in graph.AGENTS.values():
        assert spec.permission  # every agent requires an explicit permission
