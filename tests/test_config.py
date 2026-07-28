from __future__ import annotations

from vibe_lead_qualifier.config import Settings


def test_settings_load_demo_flag_and_http_timeout_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DEMO_ENDPOINTS", "true")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "7.5")

    settings = Settings.from_env(env_file=None)

    assert settings.enable_demo_endpoints is True
    assert settings.http_timeout_seconds == 7.5
