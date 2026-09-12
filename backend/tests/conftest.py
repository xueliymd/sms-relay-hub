import base64
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ADMIN_TOKEN", "neutral-admin-token-for-tests-123456")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "neutral-admin-password-for-tests-123456")
os.environ.setdefault("DATA_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"t" * 32).decode())
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.main import app
from app.services.store import store


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{(tmp_path / 'relay-test.db').as_posix()}"


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    store.configure(database_url, drop_existing=True)
    with TestClient(app) as test_client:
        yield test_client
    store.dispose()
