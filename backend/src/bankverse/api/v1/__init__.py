"""Versioned API (v1). Each business module contributes its own router."""

from fastapi import APIRouter

from bankverse.api.v1 import auth, org, platform_admin

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(org.router)
api_router.include_router(platform_admin.router)
