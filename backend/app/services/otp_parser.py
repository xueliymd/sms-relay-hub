import re
from datetime import datetime, timedelta, timezone
from typing import cast

from app.core.config import settings

KEYWORDS = re.compile(r"验证码|校验码|动态码|verification|access\s+code|otp|code", re.I)


def extract_code(body: str) -> str | None:
    if not KEYWORDS.search(body):
        return None
    candidates = cast(list[str], re.findall(r"(?<!\d)\d{4,8}(?!\d)", body))
    for candidate in candidates:
        if re.search(rf"(?:订单|金额)\s*\D{{0,3}}{re.escape(candidate)}", body):
            continue
        return candidate
    return None


def code_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=settings.otp_ttl_seconds)
