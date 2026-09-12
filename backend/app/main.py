import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.relay import router as relay_router
from app.api.updates import router as updates_router
from app.core.errors import RelayError, relay_error_handler
from app.services.store import store
from app.workers import retention_loop


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    store.ensure_admin_account()
    retention_task = asyncio.create_task(retention_loop())
    try:
        yield
    finally:
        retention_task.cancel()
        with suppress(asyncio.CancelledError):
            await retention_task


app = FastAPI(title="SMS Relay Hub", version="1.0.0", lifespan=lifespan)
app.add_exception_handler(RelayError, relay_error_handler)
app.include_router(health_router)
app.include_router(relay_router)
app.include_router(updates_router)

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.is_dir():
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def admin_ui() -> FileResponse:
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
        )
