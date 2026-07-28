from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vibe_lead_qualifier.config import Settings  # noqa: E402
from vibe_lead_qualifier.main import create_app  # noqa: E402

TEST_API_TOKEN = "test-api-token"
TEST_WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        vibe_api_token=TEST_API_TOKEN,
        vibe_webhook_secret=TEST_WEBHOOK_SECRET,
        vibe_base_url="https://vibe.example.test",
        database_path=tmp_path / "qualifier.sqlite3",
        log_level="WARNING",
        http_timeout_seconds=1.0,
        enable_demo_endpoints=True,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
