import asyncio
import logging

from app.core.config import settings
from app.services.store import store

logger = logging.getLogger(__name__)


def run_retention_once() -> int:
    return store.run_retention(settings.retention_days)


async def retention_loop() -> None:
    while True:
        try:
            run_retention_once()
        except Exception:
            # The next scheduled run retries; no message bodies or credentials are logged here.
            logger.exception("Retention run failed")
        # Keep OTP expiry cleanup responsive even when message redaction runs hourly.
        await asyncio.sleep(min(settings.retention_interval_seconds, 60))
