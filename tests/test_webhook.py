from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import TEST_API_TOKEN, TEST_WEBHOOK_SECRET
from fastapi.testclient import TestClient

from vibe_lead_qualifier import __version__
from vibe_lead_qualifier.config import Settings
from vibe_lead_qualifier.main import create_app
from vibe_lead_qualifier.security import make_signature
from vibe_lead_qualifier.service import QUESTIONS


def _body(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _signed_post(
    client: TestClient,
    payload: Any,
    *,
    secret: str = TEST_WEBHOOK_SECRET,
):
    raw_body = _body(payload)
    return client.post(
        "/webhooks/vibe",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Signature": make_signature(raw_body, secret),
        },
    )


def _complete_message(
    *,
    dialog_id: str = "dialog-complete",
    message_id: str = "message-complete",
) -> dict[str, Any]:
    return {
        "event": "agent.message",
        "message_id": message_id,
        "text": (
            "Имя: Анна; Услуга: лендинг; Бюджет: 150 000 руб.; "
            "Срок: до 15 августа; Контакт: anna@example.com; "
            "Комментарий: позвонить утром."
        ),
        "context": {"dialog_id": dialog_id},
    }


def test_health_reports_version_and_database_status(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_demo_routes_are_not_registered_by_default(tmp_path: Path) -> None:
    settings = Settings(
        vibe_api_token=TEST_API_TOKEN,
        vibe_webhook_secret=TEST_WEBHOOK_SECRET,
        vibe_base_url="https://vibe.example.test",
        database_path=tmp_path / "demo-disabled.sqlite3",
        log_level="WARNING",
    )
    application = create_app(settings)

    assert settings.enable_demo_endpoints is False
    assert "/demo/messages" not in application.openapi()["paths"]
    assert "/demo/dialogs/{dialog_id}" not in application.openapi()["paths"]

    with TestClient(application) as default_client:
        post_response = default_client.post(
            "/demo/messages",
            json={"dialog_id": "disabled", "message_id": "message-1", "text": "Привет"},
        )
        get_response = default_client.get("/demo/dialogs/disabled")

    assert post_response.status_code == 404
    assert get_response.status_code == 404


def test_demo_routes_work_when_explicitly_enabled(client: TestClient) -> None:
    assert "/demo/messages" in client.app.openapi()["paths"]
    assert "/demo/dialogs/{dialog_id}" in client.app.openapi()["paths"]

    message = client.post(
        "/demo/messages",
        json={
            "dialog_id": "enabled-demo",
            "message_id": "enabled-message-1",
            "text": "Имя: Анна; Услуга: лендинг",
        },
    )
    dialog = client.get("/demo/dialogs/enabled-demo")

    assert message.status_code == 200
    assert message.json() == {"reply": QUESTIONS["budget"]}
    assert dialog.status_code == 200
    assert dialog.json()["brief"]["name"] == "Анна"
    assert dialog.json()["brief"]["service"] == "лендинг"


def test_webhook_accepts_valid_hmac_of_exact_raw_body(client: TestClient) -> None:
    raw_body = b'{  "event" : "webhook.test", "event_id": "raw-1" }\n'
    response = client.post(
        "/webhooks/vibe",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Signature": make_signature(raw_body, TEST_WEBHOOK_SECRET),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/json"},
        {
            "Content-Type": "application/json",
            "X-Vibe-Signature": "0" * 64,
        },
        {
            "Content-Type": "application/json",
            "X-Vibe-Signature": "not-a-sha256-signature",
        },
    ],
    ids=["missing", "wrong-digest", "malformed"],
)
def test_webhook_rejects_missing_or_invalid_hmac(
    client: TestClient,
    headers: dict[str, str],
) -> None:
    response = client.post(
        "/webhooks/vibe",
        content=b'{"event":"webhook.test"}',
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid webhook signature"}


def test_webhook_signature_is_bound_to_raw_bytes(client: TestClient) -> None:
    compact = b'{"event":"webhook.test"}'
    differently_formatted = b'{ "event": "webhook.test" }'
    response = client.post(
        "/webhooks/vibe",
        content=differently_formatted,
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Signature": make_signature(compact, TEST_WEBHOOK_SECRET),
        },
    )

    assert response.status_code == 401


def test_signature_is_checked_before_json_parsing(client: TestClient) -> None:
    invalid_json = b'{"event":'

    invalid_signature = client.post(
        "/webhooks/vibe",
        content=invalid_json,
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Signature": "0" * 64,
        },
    )
    valid_signature = client.post(
        "/webhooks/vibe",
        content=invalid_json,
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Signature": make_signature(invalid_json, TEST_WEBHOOK_SECRET),
        },
    )

    assert invalid_signature.status_code == 401
    assert invalid_signature.json() == {"detail": "invalid webhook signature"}
    assert valid_signature.status_code == 400
    assert valid_signature.json() == {"detail": "invalid JSON body"}


def test_webhook_returns_503_when_verification_is_not_configured(tmp_path: Path) -> None:
    settings = Settings(
        vibe_api_token=TEST_API_TOKEN,
        vibe_webhook_secret=None,
        vibe_base_url="https://vibe.example.test",
        database_path=tmp_path / "unconfigured.sqlite3",
        log_level="WARNING",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/webhooks/vibe",
            content=b'{"event":"webhook.test"}',
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "webhook verification is not configured"}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"event": "webhook.test", "event_id": "test-1"}, {"status": "ok"}),
        (
            {"event": "generation.complete", "generation_id": "gen-1"},
            {"status": "ok", "event": "generation.complete"},
        ),
        (
            {"event": "generation.error", "generation_id": "gen-2"},
            {"status": "ok", "event": "generation.error"},
        ),
    ],
)
def test_documented_passive_events_are_safe(
    client: TestClient,
    payload: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    response = _signed_post(client, payload)

    assert response.status_code == 200
    assert response.json() == expected


def test_unknown_event_is_acknowledged_and_ignored(client: TestClient) -> None:
    response = _signed_post(
        client,
        {"event": "future.event", "event_id": "future-1", "data": {"anything": True}},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "event": "future.event",
        "ignored": True,
    }


def test_incomplete_brief_returns_exactly_one_concrete_question(client: TestClient) -> None:
    response = _signed_post(
        client,
        {
            "event": "agent.message",
            "message_id": "question-1",
            "text": "Здравствуйте!",
            "context": {"dialog_id": "dialog-question"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"reply": QUESTIONS["name"]}
    assert response.json()["reply"].count("?") == 1


def test_multiple_messages_merge_into_one_dialog(client: TestClient) -> None:
    first = _signed_post(
        client,
        {
            "event": "agent.message",
            "message_id": "merge-1",
            "text": "Имя: Анна; Услуга: лендинг",
            "context": {"dialog_id": "dialog-merge"},
        },
    )
    second = _signed_post(
        client,
        {
            "event": "agent.message",
            "message_id": "merge-2",
            "text": "Бюджет: 150 000 руб.; Срок: до 15 августа",
            "context": {"dialog_id": "dialog-merge"},
        },
    )
    dialog = client.get("/demo/dialogs/dialog-merge")

    assert first.json() == {"reply": QUESTIONS["budget"]}
    assert second.json() == {"reply": QUESTIONS["contact"]}
    assert dialog.status_code == 200
    assert dialog.json() == {
        "dialog_id": "dialog-merge",
        "brief": {
            "name": "Анна",
            "service": "лендинг",
            "budget": 150000,
            "deadline": "до 15 августа",
            "contact": None,
            "comment": None,
            "deal_created": False,
        },
        "missing_fields": ["contact"],
        "ready": False,
    }


def test_full_brief_emits_one_correct_crm_deal_action(client: TestClient) -> None:
    response = _signed_post(client, _complete_message())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"].startswith("Спасибо, Анна!")
    assert payload["actions"] == [
        {
            "method": "crm.deal.add",
            "params": {
                "fields": {
                    "TITLE": "Заявка: лендинг — Анна",
                    "OPPORTUNITY": 150000,
                    "CURRENCY_ID": "RUB",
                    "CATEGORY_ID": 0,
                    "COMMENTS": (
                        "Клиент: Анна\n"
                        "Задача: лендинг\n"
                        "Срок: до 15 августа\n"
                        "Контакт: anna@example.com\n"
                        "Комментарий: позвонить утром"
                    ),
                }
            },
        }
    ]


def test_duplicate_event_reuses_saved_response_without_reprocessing_local_state_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "persistent.sqlite3"
    settings = Settings(
        vibe_api_token=TEST_API_TOKEN,
        vibe_webhook_secret=TEST_WEBHOOK_SECRET,
        vibe_base_url="https://vibe.example.test",
        database_path=database_path,
        log_level="WARNING",
    )
    payload = _complete_message(dialog_id="persistent-dialog", message_id="persistent-message")

    with TestClient(create_app(settings)) as first_client:
        first = _signed_post(first_client, payload)
        assert first.status_code == 200

    with TestClient(create_app(settings)) as second_client:
        duplicate = _signed_post(second_client, payload)
        assert duplicate.status_code == 200

    assert duplicate.json() == first.json()
    assert len(duplicate.json()["actions"]) == 1

    with sqlite3.connect(database_path) as connection:
        processed_count = connection.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0]
        event_outcomes = [
            row[0]
            for row in connection.execute("SELECT outcome FROM event_log ORDER BY id").fetchall()
        ]
        dialog_rows = connection.execute("SELECT state_json FROM dialogs").fetchall()

    assert processed_count == 1
    assert event_outcomes == ["processed", "duplicate"]
    assert len(dialog_rows) == 1
    assert json.loads(dialog_rows[0][0])["deal_created"] is True


def test_new_message_after_completed_dialog_does_not_create_another_action(
    client: TestClient,
) -> None:
    completed = _signed_post(
        client,
        _complete_message(dialog_id="completed-dialog", message_id="completed-1"),
    )
    later = _signed_post(
        client,
        {
            "event": "agent.message",
            "message_id": "completed-2",
            "text": "Комментарий: дополнительная информация",
            "context": {"dialog_id": "completed-dialog"},
        },
    )
    dialog = client.get("/demo/dialogs/completed-dialog")

    assert len(completed.json()["actions"]) == 1
    assert later.status_code == 200
    assert set(later.json()) == {"reply"}
    assert "уже собран" in later.json()["reply"]
    assert dialog.json()["brief"]["deal_created"] is True


@pytest.mark.parametrize(
    ("payload", "dialog_id"),
    [
        (
            {
                "event": "agent.message",
                "message": {
                    "id": "nested-1",
                    "text": "Имя: Олег",
                    "context": {"dialog_id": "nested-dialog"},
                },
            },
            "nested-dialog",
        ),
        (
            {
                "event": "agent.message",
                "message_id": "flat-1",
                "dialog_id": "flat-dialog",
                "text": "Имя: Олег",
            },
            "flat-dialog",
        ),
    ],
    ids=["nested-message-envelope", "flat-payload"],
)
def test_nested_and_flat_agent_message_payloads_are_supported(
    client: TestClient,
    payload: dict[str, Any],
    dialog_id: str,
) -> None:
    response = _signed_post(client, payload)
    dialog = client.get(f"/demo/dialogs/{dialog_id}")

    assert response.status_code == 200
    assert response.json() == {"reply": QUESTIONS["service"]}
    assert dialog.status_code == 200
    assert dialog.json()["brief"]["name"] == "Олег"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"event": "agent.message", "message_id": "missing-dialog", "text": "Привет"},
        {
            "event": "agent.message",
            "message_id": "missing-text",
            "context": {"dialog_id": "validation-dialog"},
        },
    ],
    ids=["missing-event", "missing-dialog", "missing-text"],
)
def test_missing_required_event_fields_return_422(
    client: TestClient,
    payload: dict[str, Any],
) -> None:
    response = _signed_post(client, payload)

    assert response.status_code == 422
    assert isinstance(response.json().get("detail"), str)


def test_webhook_body_must_be_a_json_object(client: TestClient) -> None:
    response = _signed_post(client, ["webhook.test"])

    assert response.status_code == 422
    assert response.json() == {"detail": "webhook body must be a JSON object"}


def test_dialog_get_returns_state_and_missing_dialog_returns_404(client: TestClient) -> None:
    created = _signed_post(
        client,
        {
            "event": "agent.message",
            "message_id": "get-dialog-1",
            "text": "Имя: Мария; Услуга: аудит",
            "context": {"dialog_id": "get-dialog"},
        },
    )

    existing = client.get("/demo/dialogs/get-dialog")
    missing = client.get("/demo/dialogs/does-not-exist")

    assert created.status_code == 200
    assert existing.status_code == 200
    assert existing.json()["dialog_id"] == "get-dialog"
    assert existing.json()["brief"]["service"] == "аудит"
    assert existing.json()["missing_fields"] == ["budget", "deadline", "contact"]
    assert existing.json()["ready"] is False
    assert missing.status_code == 404
    assert missing.json() == {"detail": "dialog not found"}
