from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from vibe_lead_qualifier import cli
from vibe_lead_qualifier.config import Settings


@pytest.mark.asyncio
async def test_cli_passes_configured_http_timeout_to_vibe_client(monkeypatch) -> None:
    captured: dict[str, object] = {}
    settings = Settings(
        vibe_api_token="test-api-token",
        vibe_webhook_secret=None,
        vibe_base_url="https://vibe.example.test",
        database_path=Path("unused.sqlite3"),
        log_level="WARNING",
        http_timeout_seconds=7.5,
    )

    class FakeVibeClient:
        def __init__(self, token: str, base_url: str, *, timeout: float) -> None:
            captured.update(token=token, base_url=base_url, timeout=timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_me(self) -> dict[str, int]:
            return {"id": 1}

    monkeypatch.setattr(cli, "_load_vibe_settings", lambda: settings)
    monkeypatch.setattr(cli, "VibeClient", FakeVibeClient)

    result = await cli.run(argparse.Namespace(command="check-token"))

    assert result == cli.EXIT_OK
    assert captured == {
        "token": "test-api-token",
        "base_url": "https://vibe.example.test",
        "timeout": 7.5,
    }
