"""Platform administration (BANKVERSE operators): tenants lifecycle, platform health, audit chain."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require_platform_admin
from bankverse.core.errors import NotFound, ValidationFailed
from bankverse.db.models.control import Membership, Organization, SecurityEvent, User
from bankverse.services import audit, provisioning, usage

router = APIRouter(prefix="/platform", tags=["platform-admin"])


def _org_view(db: Session, org: Organization) -> dict[str, Any]:
    members = db.execute(select(func.count()).select_from(Membership).where(Membership.org_id == org.id)).scalar_one()
    return {
        "id": str(org.id), "slug": org.slug, "name": org.name, "kind": org.kind, "status": org.status,
        "country": org.country, "plan": org.plan, "isolation_mode": org.isolation_mode,
        "data_region": org.data_region, "created_at": org.created_at, "members": members,
        "usage": usage.usage_for(db, org.id),
    }


@router.get("/organizations")
def list_orgs(_: Principal = Depends(require_platform_admin), db: Session = Depends(control_db)) -> list[dict[str, Any]]:
    orgs = db.execute(select(Organization).order_by(Organization.created_at)).scalars().all()
    return [_org_view(db, o) for o in orgs]


class OrgCreateIn(BaseModel):
    slug: str = Field(min_length=3, max_length=63)
    name: str = Field(min_length=2, max_length=200)
    country: str = Field(default="FR", min_length=2, max_length=2)
    isolation_mode: str = Field(default="shared", pattern="^(shared|dedicated)$")
    dedicated_db_secret: str | None = None
    admin_email: EmailStr
    modules: list[str] | None = None


@router.post("/organizations")
def create_org(body: OrgCreateIn, request: Request, principal: Principal = Depends(require_platform_admin),
               db: Session = Depends(control_db)) -> dict[str, Any]:
    org = provisioning.create_organization(
        db, slug=body.slug, name=body.name, country=body.country.upper(), isolation_mode=body.isolation_mode,
        dedicated_db_secret=body.dedicated_db_secret, modules=body.modules,
    )
    token = provisioning.invite_admin(db, org, body.admin_email, principal.user_id)
    audit.record("platform.org_created", org_id=org.id, actor_user_id=principal.user_id, actor_label=principal.label,
                 ip=client_ip(request), resource_type="organization", resource_id=str(org.id),
                 details={"slug": org.slug, "isolation_mode": org.isolation_mode}, session=db)
    return {"organization": _org_view(db, org), "admin_invite_path": f"/invite/{token}"}


class OrgActionIn(BaseModel):
    action: str = Field(pattern="^(suspend|reactivate|deprovision|purge)$")
    confirm_slug: str | None = None
    reason: str = Field(min_length=3, max_length=500)


@router.post("/organizations/{org_id}/lifecycle")
def lifecycle(org_id: uuid.UUID, body: OrgActionIn, request: Request,
              principal: Principal = Depends(require_platform_admin), db: Session = Depends(control_db)) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    if org is None:
        raise NotFound("Organisation not found")
    if org.id == principal.org_id:
        raise ValidationFailed("You cannot change the lifecycle of your own organisation")
    if body.action in ("deprovision", "purge") and body.confirm_slug != org.slug:
        raise ValidationFailed("Destructive action: confirm_slug must equal the organisation slug")
    result: dict[str, Any] = {"status": "ok"}
    if body.action == "suspend":
        provisioning.suspend(db, org)
    elif body.action == "reactivate":
        provisioning.reactivate(db, org)
    elif body.action == "deprovision":
        provisioning.deprovision(db, org)
    else:
        db.commit()
        result["deleted_rows"] = provisioning.purge_tenant_data(org)
    audit.record(f"platform.org_{body.action}", org_id=org.id, actor_user_id=principal.user_id,
                 actor_label=principal.label, ip=client_ip(request), resource_type="organization",
                 resource_id=str(org.id), details={"reason": body.reason, **({k: v for k, v in result.items() if k != 'status'})},
                 session=db)
    result["organization"] = _org_view(db, org)
    return result


@router.get("/audit/verify")
def verify_audit(_: Principal = Depends(require_platform_admin), db: Session = Depends(control_db)) -> dict[str, Any]:
    return audit.verify_chain(db)


@router.get("/security-events")
def platform_security_events(_: Principal = Depends(require_platform_admin),
                             db: Session = Depends(control_db)) -> list[dict[str, Any]]:
    rows = db.execute(select(SecurityEvent).order_by(SecurityEvent.created_at.desc()).limit(300)).scalars()
    return [{"id": str(e.id), "kind": e.kind, "severity": e.severity, "org_id": str(e.org_id) if e.org_id else None,
             "user_id": str(e.user_id) if e.user_id else None, "ip": e.ip, "created_at": e.created_at,
             "details": e.details} for e in rows]


@router.get("/users")
def platform_users(_: Principal = Depends(require_platform_admin), db: Session = Depends(control_db)) -> list[dict[str, Any]]:
    rows = db.execute(select(User).order_by(User.created_at)).scalars()
    return [{"id": str(u.id), "email": u.email, "full_name": u.full_name, "status": u.status,
             "mfa_enabled": u.mfa_enabled, "is_platform_admin": u.is_platform_admin,
             "last_login_at": u.last_login_at, "locked": u.locked_until is not None} for u in rows]
