import pytest

from app.core.config import settings
from app.services.store import store
from app.workers import run_retention_once


def test_retention_worker_uses_configured_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[int] = []

    def run_retention(days: int) -> int:
        observed.append(days)
        return 3

    monkeypatch.setattr(store, "run_retention", run_retention)

    assert run_retention_once() == 3
    assert observed == [settings.retention_days]
