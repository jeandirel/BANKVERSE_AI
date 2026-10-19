"""Versioned API (v1). Each business module contributes its own router."""

from fastapi import APIRouter

from bankverse.api.v1 import (
    aml,
    auth,
    cases,
    credit,
    customers,
    documents,
    forecasting,
    fraud,
    org,
    platform_admin,
    search,
)

api_router = APIRouter()
for r in (auth.router, org.router, platform_admin.router, fraud.router, aml.router, credit.router, customers.router,
          forecasting.router, documents.router, cases.router, search.router):
    api_router.include_router(r)
