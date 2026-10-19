"""Model lifecycle (platform-only, four-eyes, deploy, rollback), incidents, data uploads and dashboard scoping."""

from __future__ import annotations

import io
import uuid

import joblib
import pytest

from tests.conftest import login

pytestmark = pytest.mark.integration


def _register(name: str, version: int, trained_by: str) -> None:
    from bankverse.db.session import control_session
    from bankverse.ml import registry

    adir = registry.artifact_dir(name, version)
    path = adir / "model.joblib"
    joblib.dump({"kind": "dummy", "version": version}, path)
    with control_session(owner=True) as db:
        registry.register_version(db, name=name, module="fraud", task="test", description="test model", version=version, algorithm="dummy",
                                  artifact_path=path, metrics={"test": {"pr_auc": 0.5 + version / 100}}, params={}, features=["x"],
                                  dataset_versions=[], reference_profile={}, model_card={"intended_use": "tests"}, mlflow_run_id=None,
                                  trained_by=trained_by, training_seconds=1.0)


def test_model_lifecycle_is_platform_only_with_four_eyes(client, tenants):
    name = f"test_model_{uuid.uuid4().hex[:8]}"
    platform = tenants.org("platform", kind="platform")
    bank = tenants.org()
    trainer = tenants.user(platform, "data_scientist")
    reviewer = tenants.user(platform, "risk_manager")
    _register(name, 1, trainer["email"])
    _register(name, 2, reviewer["email"])
    hb = login(client, tenants.user(bank, "risk_manager"))
    r = client.post(f"/api/v1/models/{name}/versions/1/review", json={"decision": "approved", "comment": "looks good to me"}, headers=hb)
    assert r.status_code == 403 and r.json()["error"]["code"] == "platform_only"
    assert client.get(f"/api/v1/models/{name}").status_code == 200  # banks can read the evidence
    client.cookies.clear()
    hp = login(client, reviewer)
    own = client.post(f"/api/v1/models/{name}/versions/2/review", json={"decision": "approved", "comment": "my own model is great"}, headers=hp)
    assert own.status_code == 403 and own.json()["error"]["code"] == "four_eyes"
    ok = client.post(f"/api/v1/models/{name}/versions/1/review", json={"decision": "approved", "comment": "Evaluation evidence reviewed"}, headers=hp)
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert client.post(f"/api/v1/models/{name}/versions/2/deploy", json={"reason": "not approved"}, headers=hp).status_code == 409
    assert client.post(f"/api/v1/models/{name}/versions/1/deploy", json={"reason": "first release"}, headers=hp).json()["status"] == "production"
    assert client.post(f"/api/v1/models/{name}/rollback", json={"reason": "nothing to roll back"}, headers=hp).status_code == 409
    detail = client.get(f"/api/v1/models/{name}").json()
    assert detail["versions"][1]["approvals"][0]["reviewer"] == reviewer["email"]
    assert detail["deployments"][0]["active"] is True


def test_incidents_permissions_and_isolation(client, tenants):
    a, b = tenants.org(), tenants.org()
    h = login(client, tenants.user(a, "risk_manager"))
    inc = client.post("/api/v1/governance/incidents", json={"title": "Hausse des faux positifs", "category": "model_drift", "severity": "high"},
                      headers=h)
    assert inc.status_code == 201
    upd = client.patch(f"/api/v1/governance/incidents/{inc.json()['id']}", json={"status": "resolved", "resolution": "Seuil revu"}, headers=h)
    assert upd.json()["status"] == "resolved" and upd.json()["resolved_at"]
    client.cookies.clear()
    hf = login(client, tenants.user(a, "fraud_analyst"))
    assert client.post("/api/v1/governance/incidents", json={"title": "Test incident", "category": "other", "severity": "low"},
                       headers=hf).status_code == 403
    client.cookies.clear()
    login(client, tenants.user(b, "auditor"))
    assert client.get("/api/v1/governance/incidents").json() == []


def test_data_upload_profiling_and_isolation(client, tenants):
    a, b = tenants.org(), tenants.org()
    h = login(client, tenants.user(a, "data_scientist"))
    csv = b"ts,amount,merchant_name,mcc,card_ref\n2026-01-01 10:00:00,12.5,Shop,5411,c1\n2026-01-02 11:00:00,-3,Shop,5411,c2\n"
    r = client.post("/api/v1/data/uploads", files={"file": ("tx.csv", io.BytesIO(csv), "text/csv")}, data={"template": "card_transactions"}, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["rows"] == 2 and body["quality_status"] == "warn"
    assert any(c["check"] == "amount_non_negative" and c["status"] == "warn" for c in body["quality"])
    dup = client.post("/api/v1/data/uploads", files={"file": ("tx.csv", io.BytesIO(csv), "text/csv")}, data={"template": "card_transactions"}, headers=h)
    assert dup.status_code == 409
    bad = client.post("/api/v1/data/uploads", files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}, data={"template": "transfers"}, headers=h)
    assert bad.json()["quality_status"] == "fail"
    client.cookies.clear()
    hb = login(client, tenants.user(b, "data_scientist"))
    assert client.get(f"/api/v1/data/uploads/{body['id']}").status_code == 404
    assert client.get("/api/v1/data/uploads").json()["items"] == []
    client.cookies.clear()
    hf = login(client, tenants.user(a, "fraud_analyst"))
    assert client.post("/api/v1/data/uploads", files={"file": ("y.csv", io.BytesIO(b"a\n1\n"), "text/csv")}, headers=hf).status_code == 403


def test_dashboard_sections_follow_permissions(client, tenants):
    org = tenants.org()
    login(client, tenants.user(org, "fraud_analyst"))
    d = client.get("/api/v1/dashboard").json()
    assert "fraud" in d and "credit" not in d and "models" not in d
    client.cookies.clear()
    login(client, tenants.user(org, "credit_analyst"))
    d = client.get("/api/v1/dashboard").json()
    assert "credit" in d and "fraud" not in d and "documents" in d
    assert client.get("/api/v1/public/evidence").status_code == 200
