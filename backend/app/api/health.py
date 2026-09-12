from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.store import store

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=None)
def health() -> JSONResponse:
    try:
        store.check_database()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(status_code=200, content={"status": "ok"})
