"""Versioned API (v1). Each business module contributes its own router."""

from fastapi import APIRouter

from bankverse.api.v1 import (
    agents,
    aml,
    auth,
    cases,
    copilot,
    credit,
    customers,
    dashboard,
    data,
    documents,
    forecasting,
    fraud,
    governance,
    org,
    platform_admin,
    public,
    search,
)

api_router = APIRouter()
for r in (auth.router, org.router, platform_admin.router, fraud.router, aml.router, credit.router, customers.router,
          forecasting.router, documents.router, copilot.router, agents.router, governance.router, data.router, dashboard.router, public.router, cases.router, search.router):
    api_router.include_router(r)
