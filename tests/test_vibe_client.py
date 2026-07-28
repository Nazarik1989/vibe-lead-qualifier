from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from vibe_lead_qualifier.vibe_client import (
    CRMAction,
    VibeAPIError,
    VibeClient,
    VibeTimeoutError,
    normalize_agent_base_url,
)

TEST_API_TOKEN = "test-api-token"


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://vibe.example.test", "https://vibe.example.test/api/agent"),
        ("https://vibe.example.test/", "https://vibe.example.test/api/agent"),
        (
            "https://vibe.example.test/api/agent",
            "https://vibe.example.test/api/agent",
        ),
        (
            "https://vibe.example.test/api/agent/",
            "https://vibe.example.test/api/agent",
        ),
        (
            "https://vibe.example.test/custom",
            "https://vibe.example.test/custom/api/agent",
        ),
    ],
)
def test_base_url_is_normalized_to_agent_api_root(base_url: str, expected: str) -> None:
    assert normalize_agent_base_url(base_url) == expected


@pytest.mark.parametrize(
    "base_url",
    [
        "vibe.example.test",
        "ftp://vibe.example.test",
        "https://vibe.example.test?token=unsafe",
        "https://vibe.example.test/#fragment",
    ],
)
def test_invalid_base_url_is_rejected(base_url: str) -> None:
    with pytest.raises(ValueError):
        normalize_agent_base_url(base_url)


@pytest.mark.asyncio
async def test_client_uses_exact_methods_paths_bodies_and_authentication() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "method": request.method,
                "raw_path": request.url.raw_path.decode("ascii"),
                "query": dict(request.url.params),
                "body": json.loads(request.content) if request.content else None,
                "authorization": request.headers.get("Authorization"),
                "accept": request.headers.get("Accept"),
            }
        )
        path = request.url.path
        if path.endswith("/me"):
            return httpx.Response(200, json={"id": 7, "scopes": ["agent"]})
        if path.endswith("/webhook-test"):
            return httpx.Response(200, json={"delivered": True, "http_status": 200})
        if path.endswith("/webhook-url"):
            return httpx.Response(200, json={"status": "ok", "url": None})
        if path.endswith("/generate/estimate"):
            return httpx.Response(
                200,
                json={
                    "valid": True,
                    "dry_run": True,
                    "estimated_cost_rub": 0.25,
                },
            )
        if path.endswith("/inbox"):
            return httpx.Response(
                200,
                json={"messages": [{"id": "inbox-1", "text": "Привет"}]},
            )
        if path.endswith("/reply"):
            return httpx.Response(200, json={"status": "ok", "late": False})
        raise AssertionError(f"Unexpected mock path: {path}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, trust_env=False) as http_client:
        client = VibeClient(
            TEST_API_TOKEN,
            "https://vibe.example.test/api/agent/",
            timeout=1.25,
            client=http_client,
        )
        me = await client.get_me()
        webhook = await client.webhook_test("https://callback.example.test/vibe")
        registered = await client.set_webhook_url(None)
        estimate = await client.estimate(
            "text",
            "test-model",
            "Короткий prompt",
            strict=True,
            parameters={"temperature": 0.2},
        )
        inbox = await client.get_inbox(wait=7, limit=3)
        reply = await client.reply_to_message(
            "msg/42",
            "Спасибо",
            actions=[
                CRMAction(
                    method="crm.deal.add",
                    params={"fields": {"TITLE": "Тестовая сделка"}},
                )
            ],
        )

    assert me.id == 7
    assert webhook.delivered is True
    assert registered.status == "ok"
    assert estimate.valid is True and estimate.dry_run is True
    assert inbox.messages[0].id == "inbox-1"
    assert reply.status == "ok"
    assert requests == [
        {
            "method": "GET",
            "raw_path": "/api/agent/me",
            "query": {},
            "body": None,
            "authorization": "Bearer test-api-token",
            "accept": "application/json",
        },
        {
            "method": "POST",
            "raw_path": "/api/agent/webhook-test",
            "query": {},
            "body": {"callback_url": "https://callback.example.test/vibe"},
            "authorization": "Bearer test-api-token",
            "accept": "application/json",
        },
        {
            "method": "POST",
            "raw_path": "/api/agent/webhook-url",
            "query": {},
            "body": {"url": None},
            "authorization": "Bearer test-api-token",
            "accept": "application/json",
        },
        {
            "method": "POST",
            "raw_path": "/api/agent/generate/estimate",
            "query": {},
            "body": {
                "type": "text",
                "model": "test-model",
                "prompt": "Короткий prompt",
                "strict": True,
                "temperature": 0.2,
            },
            "authorization": "Bearer test-api-token",
            "accept": "application/json",
        },
        {
            "method": "GET",
            "raw_path": "/api/agent/inbox?wait=7&limit=3",
            "query": {"wait": "7", "limit": "3"},
            "body": None,
            "authorization": "Bearer test-api-token",
            "accept": "application/json",
        },
        {
            "method": "POST",
            "raw_path": "/api/agent/inbox/msg%2F42/reply",
            "query": {},
            "body": {
                "reply": "Спасибо",
                "actions": [
                    {
                        "method": "crm.deal.add",
                        "params": {"fields": {"TITLE": "Тестовая сделка"}},
                    }
                ],
            },
            "authorization": "Bearer test-api-token",
            "accept": "application/json",
        },
    ]


@pytest.mark.asyncio
async def test_estimate_is_mocked_and_only_calls_free_dry_run_endpoint() -> None:
    called_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called_paths.append(request.url.path)
        assert request.method == "POST"
        assert request.url.path == "/api/agent/generate/estimate"
        return httpx.Response(
            200,
            json={
                "valid": True,
                "dry_run": True,
                "balance": {"current": 100.0, "after": 100.0},
                "estimated_cost_rub": 1.0,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = VibeClient(TEST_API_TOKEN, client=http_client)
        result = await client.estimate("text", "test-model", "Без реальной генерации")

    assert result.valid is True
    assert result.dry_run is True
    assert result.balance.current == result.balance.after == 100.0
    assert called_paths == ["/api/agent/generate/estimate"]


@pytest.mark.asyncio
async def test_timeout_is_converted_to_domain_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("mock timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = VibeClient(TEST_API_TOKEN, client=http_client)
        with pytest.raises(VibeTimeoutError) as caught:
            await client.get_me()

    assert caught.value.operation == "GET /me"
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)
    assert TEST_API_TOKEN not in str(caught.value)


@pytest.mark.asyncio
async def test_json_4xx_error_fields_are_parsed_and_token_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "message": f"quota rejected for {TEST_API_TOKEN}",
                "error": "rate_limit",
                "details": {"provided": TEST_API_TOKEN},
                "request_id": "request-json",
                "retry_after": 4,
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = VibeClient(TEST_API_TOKEN, client=http_client)
        with pytest.raises(VibeAPIError) as caught:
            await client.get_me()

    error = caught.value
    assert error.status_code == 429
    assert error.code == "rate_limit"
    assert error.message == "quota rejected for [REDACTED]"
    assert error.details == {"provided": "[REDACTED]"}
    assert error.request_id == "request-json"
    assert error.retry_after == 4
    assert TEST_API_TOKEN not in str(error)


@pytest.mark.asyncio
async def test_non_json_5xx_error_uses_status_and_response_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            text="upstream unavailable",
            headers={"X-Request-Id": "request-header", "Retry-After": "12"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = VibeClient(TEST_API_TOKEN, client=http_client)
        with pytest.raises(VibeAPIError) as caught:
            await client.get_me()

    error = caught.value
    assert error.status_code == 503
    assert error.code is None
    assert error.details is None
    assert error.request_id == "request-header"
    assert error.retry_after == "12"
    assert "HTTP 503" in error.message


@pytest.mark.asyncio
async def test_success_response_must_be_valid_json_and_match_contract() -> None:
    responses = iter(
        [
            httpx.Response(200, text="not-json"),
            httpx.Response(200, json={"unexpected": True}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        response.request = request
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = VibeClient(TEST_API_TOKEN, client=http_client)
        with pytest.raises(VibeAPIError, match="не в формате JSON") as invalid_json:
            await client.webhook_test("https://callback.example.test")
        with pytest.raises(VibeAPIError) as invalid_contract:
            await client.webhook_test("https://callback.example.test")

    assert invalid_json.value.code == "invalid_response"
    assert invalid_contract.value.code == "invalid_response"
    assert invalid_contract.value.details == {"validation_error_count": 1}


@pytest.mark.asyncio
async def test_endpoints_without_published_success_schema_accept_empty_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = VibeClient(TEST_API_TOKEN, client=http_client)
        webhook = await client.set_webhook_url("https://callback.example.test")
        reply = await client.reply_to_message(42, "Принято")

    assert webhook.status is None
    assert reply.status is None


@pytest.mark.asyncio
async def test_inbox_values_are_forwarded_without_undocumented_local_ranges() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"messages": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = VibeClient(TEST_API_TOKEN, client=http_client)
        result = await client.get_inbox(wait=21, limit=0)

    assert result.messages == []
    assert len(requests) == 1
    assert requests[0].url.path == "/api/agent/inbox"
    assert dict(requests[0].url.params) == {"wait": "21", "limit": "0"}
