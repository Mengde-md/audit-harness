from fastapi import APIRouter

from app.api.v1.routes.audits import router as audits_router

router = APIRouter(prefix="/api/v1")
router.include_router(audits_router)

