"""Typed asynchronous client for the VibeMarketolog Agent API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Self
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_VIBE_BASE_URL = "https://lk.vibemarketolog.ru"
DEFAULT_TIMEOUT = httpx.Timeout(35.0, connect=10.0)


class VibeAPIError(Exception):
    """An HTTP, transport, or response-contract error returned by Vibe API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: Any = None,
        request_id: str | None = None,
        retry_after: str | int | float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details
        self.request_id = request_id
        self.retry_after = retry_after

    def __str__(self) -> str:
        context: list[str] = []
        if self.status_code is not None:
            context.append(f"HTTP {self.status_code}")
        if self.code:
            context.append(self.code)
        if self.request_id:
            context.append(f"request_id={self.request_id}")
        suffix = f" ({', '.join(context)})" if context else ""
        return f"{self.message}{suffix}"


class VibeTimeoutError(Exception):
    """The Vibe API did not respond before the configured client timeout."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"Vibe API не ответил вовремя при запросе {operation}.")


class VibeModel(BaseModel):
    """Forward-compatible base for public API response models."""

    model_config = ConfigDict(extra="allow")


class MeResponse(VibeModel):
    """Token metadata, limits, balances, and optional Yandex OAuth state."""

    id: int | str | None = None
    token_id: int | str | None = None
    user_id: int | str | None = None
    name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    balance: float | dict[str, Any] | None = None
    balances: dict[str, Any] | None = None
    daily_spend_limit: float | None = None
    daily_spend: float | dict[str, Any] | None = None
    webhook_url: str | None = None
    yandex_oauth: dict[str, Any] | bool | None = None


class WebhookTestResponse(VibeModel):
    delivered: bool
    http_status: int | None = None
    response_time_ms: int | float | None = None
    error: str | dict[str, Any] | None = None
    signature_sent: str | None = None
    secret_formula: str | None = None
    verify: str | None = None
    headers_sent: list[str] = Field(default_factory=list)
    payload_sent: dict[str, Any] | None = None


class WebhookURLResponse(VibeModel):
    status: str | None = None
    ok: bool | None = None
    url: str | None = None


class EstimateBalance(VibeModel):
    current: float | None = None
    after: float | None = None


class EstimateDailySpend(VibeModel):
    limit: float | None = None
    today: float | None = None
    within_limit: bool | None = None


class EstimateValidation(VibeModel):
    media: dict[str, Any] = Field(default_factory=dict)
    required_missing: list[str] = Field(default_factory=list)


class EstimateResponse(VibeModel):
    valid: bool
    dry_run: bool
    model: str | None = None
    type: str | None = None
    estimated_cost_rub: float | None = None
    balance: EstimateBalance | dict[str, Any] | None = None
    daily_spend: EstimateDailySpend | dict[str, Any] | None = None
    validation: EstimateValidation | dict[str, Any] | None = None
    rejected: list[str] = Field(default_factory=list)
    valid_params: list[str] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)


class GenerationEstimateRequest(VibeModel):
    """The common `/generate` body accepted by the free estimate endpoint."""

    type: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=20_000)
    strict: bool = False


class InboxAttachment(VibeModel):
    type: str | None = None
    url: str | None = None
    mime: str | None = None
    name: str | None = None


class InboxMessage(VibeModel):
    id: int | str
    text: str
    agent_id: str | None = None
    channel: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    attachments: list[InboxAttachment] = Field(default_factory=list)
    created_at: str | None = None


class InboxResponse(VibeModel):
    messages: list[InboxMessage] = Field(default_factory=list)


class CRMAction(VibeModel):
    method: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class ReplyResponse(VibeModel):
    status: str | None = None
    late: bool | None = None


def normalize_agent_base_url(base_url: str) -> str:
    """Return an absolute base ending in exactly `/api/agent`."""

    raw_url = base_url.strip().rstrip("/")
    parts = urlsplit(raw_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("VIBE_BASE_URL должен быть абсолютным HTTP(S) URL.")
    if parts.query or parts.fragment:
        raise ValueError("VIBE_BASE_URL не должен содержать query-параметры или fragment.")

    path = parts.path.rstrip("/")
    if not path.endswith("/api/agent"):
        path = f"{path}/api/agent"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class VibeClient:
    """Small, retry-free async client for the Agent API endpoints used by the service."""

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_VIBE_BASE_URL,
        *,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        clean_token = token.strip()
        if not clean_token:
            raise ValueError("VIBE_API_TOKEN не задан.")

        self._token = clean_token
        self.base_url = normalize_agent_base_url(base_url)
        self._timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close only a client created by this wrapper; injected clients remain caller-owned."""

        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def get_me(self) -> MeResponse:
        return await self._request("GET", "/me", MeResponse)

    async def webhook_test(self, callback_url: str) -> WebhookTestResponse:
        return await self._request(
            "POST",
            "/webhook-test",
            WebhookTestResponse,
            json_body={"callback_url": callback_url},
        )

    async def set_webhook_url(self, url: str | None) -> WebhookURLResponse:
        return await self._request(
            "POST",
            "/webhook-url",
            WebhookURLResponse,
            json_body={"url": url},
            allow_empty_response=True,
        )

    async def estimate_generation(
        self,
        request: GenerationEstimateRequest | Mapping[str, Any],
    ) -> EstimateResponse:
        request_model = (
            request
            if isinstance(request, GenerationEstimateRequest)
            else GenerationEstimateRequest.model_validate(request)
        )
        return await self._request(
            "POST",
            "/generate/estimate",
            EstimateResponse,
            json_body=request_model.model_dump(exclude_defaults=True),
        )

    async def estimate(
        self,
        generation_type: str,
        model: str,
        prompt: str,
        *,
        strict: bool = False,
        parameters: Mapping[str, Any] | None = None,
    ) -> EstimateResponse:
        payload: dict[str, Any] = dict(parameters or {})
        payload.update(
            {"type": generation_type, "model": model, "prompt": prompt, "strict": strict}
        )
        return await self.estimate_generation(payload)

    async def get_inbox(self, *, wait: int = 20, limit: int = 10) -> InboxResponse:
        return await self._request(
            "GET",
            "/inbox",
            InboxResponse,
            params={"wait": wait, "limit": limit},
        )

    async def reply_to_message(
        self,
        message_id: int | str,
        reply: str,
        actions: Sequence[CRMAction | Mapping[str, Any]] | None = None,
    ) -> ReplyResponse:
        body: dict[str, Any] = {"reply": reply}
        if actions is not None:
            body["actions"] = [
                action.model_dump() if isinstance(action, CRMAction) else dict(action)
                for action in actions
            ]
        encoded_id = quote(str(message_id), safe="")
        return await self._request(
            "POST",
            f"/inbox/{encoded_id}/reply",
            ReplyResponse,
            json_body=body,
            allow_empty_response=True,
        )

    async def _request[ResponseT: VibeModel](
        self,
        method: str,
        path: str,
        response_model: type[ResponseT],
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        allow_empty_response: bool = False,
    ) -> ResponseT:
        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        try:
            response = await self._client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise VibeTimeoutError(f"{method} {path}") from exc
        except httpx.RequestError as exc:
            raise VibeAPIError(
                "Не удалось подключиться к Vibe API.",
                code="transport_error",
            ) from exc

        if response.status_code >= 400:
            raise self._make_http_error(response)

        if allow_empty_response and not response.content.strip():
            payload: Any = {}
        else:
            try:
                payload = response.json()
            except ValueError as exc:
                raise VibeAPIError(
                    "Vibe API вернул ответ не в формате JSON.",
                    status_code=response.status_code,
                    code="invalid_response",
                ) from exc

        try:
            return response_model.model_validate(payload)
        except ValidationError as exc:
            raise VibeAPIError(
                "Ответ Vibe API не соответствует ожидаемому контракту.",
                status_code=response.status_code,
                code="invalid_response",
                details={"validation_error_count": exc.error_count()},
            ) from exc

    def _make_http_error(self, response: httpx.Response) -> VibeAPIError:
        payload: dict[str, Any] = {}
        try:
            raw_payload = response.json()
            if isinstance(raw_payload, dict):
                payload = self._remove_token(raw_payload)
        except ValueError:
            pass

        raw_message = payload.get("message")
        if not isinstance(raw_message, str):
            raw_detail = payload.get("detail")
            raw_message = raw_detail if isinstance(raw_detail, str) else None
        message = raw_message or f"Vibe API вернул ошибку HTTP {response.status_code}."

        code = payload.get("error")
        if not isinstance(code, str):
            code = payload.get("code") if isinstance(payload.get("code"), str) else None
        request_id = payload.get("request_id")
        if not isinstance(request_id, str):
            request_id = response.headers.get("X-Request-Id")
        retry_after: str | int | float | None = payload.get("retry_after")
        if retry_after is None:
            retry_after = response.headers.get("Retry-After")

        return VibeAPIError(
            message,
            status_code=response.status_code,
            code=code,
            details=payload.get("details"),
            request_id=request_id,
            retry_after=retry_after,
        )

    def _remove_token(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._remove_token(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._remove_token(item) for item in value]
        if isinstance(value, str):
            return value.replace(self._token, "[REDACTED]")
        return value


__all__ = [
    "CRMAction",
    "DEFAULT_VIBE_BASE_URL",
    "EstimateResponse",
    "GenerationEstimateRequest",
    "InboxAttachment",
    "InboxMessage",
    "InboxResponse",
    "MeResponse",
    "ReplyResponse",
    "VibeAPIError",
    "VibeClient",
    "VibeTimeoutError",
    "WebhookTestResponse",
    "WebhookURLResponse",
    "normalize_agent_base_url",
]
