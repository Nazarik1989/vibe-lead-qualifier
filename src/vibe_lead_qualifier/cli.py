"""Command-line utilities for checking the integration and running a local demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel

from vibe_lead_qualifier.config import Settings
from vibe_lead_qualifier.vibe_client import (
    GenerationEstimateRequest,
    VibeAPIError,
    VibeClient,
    VibeTimeoutError,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_TIMEOUT = 3

_SENSITIVE_KEY_PARTS = ("token", "secret", "authorization")


def redact_secrets(value: Any) -> Any:
    """Recursively replace values stored under secret-looking JSON keys."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list | tuple):
        return [redact_secrets(item) for item in value]
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(redact_secrets(value), ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibe-lead",
        description="Проверка Vibe Agent API и локальная демонстрация квалификатора.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("check-token", help="Проверить VIBE_API_TOKEN через /api/agent/me.")

    webhook_test = commands.add_parser(
        "webhook-self-test",
        help="Отправить официальное подписанное событие webhook.test.",
    )
    webhook_test.add_argument("url", help="Публичный HTTPS URL обработчика webhook.")

    register = commands.add_parser(
        "register-webhook",
        help="Включить push webhook или отключить его.",
    )
    register_group = register.add_mutually_exclusive_group(required=True)
    register_group.add_argument("url", nargs="?", help="Публичный HTTPS URL обработчика.")
    register_group.add_argument(
        "--disable",
        action="store_true",
        help="Отключить push webhook, передав url=null.",
    )

    estimate = commands.add_parser(
        "estimate",
        help="Бесплатно проверить запрос и оценить стоимость без генерации.",
    )
    estimate.add_argument("--type", dest="generation_type", required=True)
    estimate.add_argument("--model", required=True)
    estimate.add_argument("--prompt", required=True)
    estimate.add_argument(
        "--strict",
        action="store_true",
        help="Отклонять неизвестные и несовместимые параметры.",
    )

    demo = commands.add_parser(
        "demo-message",
        help="Отправить сообщение в локальный unsigned demo endpoint.",
    )
    demo.add_argument("--base-url", required=True, help="URL локального сервиса, например :8000.")
    demo.add_argument("--dialog-id", required=True)
    demo.add_argument("--message-id", required=True)
    demo.add_argument("--text", required=True)

    return parser


def _demo_endpoint(base_url: str) -> str:
    raw_url = base_url.strip().rstrip("/")
    parts = urlsplit(raw_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("--base-url должен быть абсолютным HTTP(S) URL.")
    if parts.query or parts.fragment:
        raise ValueError("--base-url не должен содержать query-параметры или fragment.")
    path = parts.path.rstrip("/")
    if not path.endswith("/demo/messages"):
        path = f"{path}/demo/messages"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _load_vibe_settings() -> Settings:
    settings = Settings.from_env()
    if not settings.vibe_api_token:
        raise ValueError(
            "VIBE_API_TOKEN не задан. Добавьте его в локальный .env, не отправляя в чат."
        )
    return settings


async def _run_vibe_command(args: argparse.Namespace) -> int:
    settings = _load_vibe_settings()
    async with VibeClient(
        settings.vibe_api_token or "",
        settings.vibe_base_url,
        timeout=settings.http_timeout_seconds,
    ) as client:
        if args.command == "check-token":
            response = await client.get_me()
            print("Токен действителен. Секретные поля скрыты.")
            _print_json(response)
            return EXIT_OK

        if args.command == "webhook-self-test":
            response = await client.webhook_test(args.url)
            _print_json(response)
            if response.delivered:
                print("Webhook self-test успешно доставлен.")
                return EXIT_OK
            print("Webhook self-test не доставлен; проверьте URL и логи listener.", file=sys.stderr)
            return EXIT_FAILURE

        if args.command == "register-webhook":
            webhook_url = None if args.disable else args.url
            response = await client.set_webhook_url(webhook_url)
            _print_json(response)
            if webhook_url is None:
                print("Push webhook отключён.")
            else:
                print("Push webhook зарегистрирован.")
            return EXIT_OK

        if args.command == "estimate":
            request = GenerationEstimateRequest(
                type=args.generation_type,
                model=args.model,
                prompt=args.prompt,
                strict=args.strict,
            )
            response = await client.estimate_generation(request)
            _print_json(response)
            if response.valid:
                print("Запрос валиден; генерация не запускалась и деньги не списывались.")
                return EXIT_OK
            print("Запрос не прошёл dry-run; генерация не запускалась.", file=sys.stderr)
            return EXIT_FAILURE

    raise RuntimeError(f"Неизвестная команда: {args.command}")


async def _run_demo_command(args: argparse.Namespace) -> int:
    endpoint = _demo_endpoint(args.base_url)
    payload = {
        "dialog_id": args.dialog_id,
        "message_id": args.message_id,
        "text": args.text,
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(endpoint, json=payload)
    except httpx.TimeoutException:
        print("Локальный demo endpoint не ответил вовремя.", file=sys.stderr)
        return EXIT_TIMEOUT
    except httpx.RequestError:
        print("Не удалось подключиться к локальному demo endpoint.", file=sys.stderr)
        return EXIT_FAILURE

    try:
        response_payload = response.json()
    except ValueError:
        response_payload = None

    if response.status_code >= 400:
        print(f"Demo endpoint вернул HTTP {response.status_code}.", file=sys.stderr)
        if response_payload is not None:
            _print_json(response_payload)
        return EXIT_FAILURE
    if response_payload is None:
        print("Demo endpoint вернул ответ не в формате JSON.", file=sys.stderr)
        return EXIT_FAILURE

    _print_json(response_payload)
    return EXIT_OK


async def run(args: argparse.Namespace) -> int:
    """Execute parsed arguments and convert expected failures to process codes."""

    try:
        if args.command == "demo-message":
            return await _run_demo_command(args)
        return await _run_vibe_command(args)
    except VibeTimeoutError as exc:
        print(f"Таймаут: {exc}", file=sys.stderr)
        return EXIT_TIMEOUT
    except VibeAPIError as exc:
        print(f"Ошибка Vibe API: {exc}", file=sys.stderr)
        if exc.retry_after is not None:
            print(f"Повторите запрос не раньше чем через {exc.retry_after} с.", file=sys.stderr)
        if exc.details is not None:
            _print_json({"details": exc.details})
        return EXIT_FAILURE
    except (OSError, ValueError) as exc:
        print(f"Ошибка настроек или аргументов: {exc}", file=sys.stderr)
        return EXIT_USAGE


def main(argv: Sequence[str] | None = None) -> int:
    """Synchronous console-script entry point."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Операция прервана пользователем.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main", "redact_secrets", "run"]
