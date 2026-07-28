"""FastAPI entry point."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from vibe_lead_qualifier import __version__
from vibe_lead_qualifier.config import Settings
from vibe_lead_qualifier.models import DemoMessage, DialogView
from vibe_lead_qualifier.repository import SQLiteRepository
from vibe_lead_qualifier.security import verify_signature
from vibe_lead_qualifier.service import InvalidEvent, LeadQualifierService


def create_app(
    settings: Settings | None = None,
    repository: SQLiteRepository | None = None,
) -> FastAPI:
    runtime_settings = settings or Settings.from_env()
    runtime_repository = repository or SQLiteRepository(runtime_settings.database_path)
    service = LeadQualifierService(runtime_repository)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=runtime_settings.log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        runtime_repository.initialize()
        application.state.settings = runtime_settings
        application.state.repository = runtime_repository
        application.state.qualifier = service
        yield

    application = FastAPI(
        title="Vibe Lead Qualifier",
        version=__version__,
        debug=False,
        lifespan=lifespan,
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        if not runtime_repository.ping():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="database unavailable",
            )
        return {"status": "ok", "version": __version__}

    @application.post("/webhooks/vibe")
    async def vibe_webhook(
        request: Request,
        x_vibe_signature: str | None = Header(default=None),
    ) -> JSONResponse:
        # Body bytes must be captured and authenticated before any JSON parsing.
        raw_body = await request.body()
        webhook_secret = runtime_settings.vibe_webhook_secret
        if not webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="webhook verification is not configured",
            )
        if not x_vibe_signature or not verify_signature(
            raw_body,
            webhook_secret,
            x_vibe_signature,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid webhook signature",
            )

        try:
            payload: Any = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid JSON body",
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=422,
                detail="webhook body must be a JSON object",
            )

        try:
            response = service.process_event(payload)
        except InvalidEvent as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc
        return JSONResponse(response)

    if runtime_settings.enable_demo_endpoints:

        @application.post("/demo/messages")
        def demo_message(message: DemoMessage) -> JSONResponse:
            """Unsigned local-only demonstration of qualification logic."""

            payload = {
                "event": "agent.message",
                "message_id": message.message_id,
                "text": message.text,
                "channel": message.channel,
                "context": {**message.context, "dialog_id": message.dialog_id},
                "attachments": [item.model_dump(exclude_none=True) for item in message.attachments],
            }
            try:
                response = service.process_event(payload, source="demo")
            except InvalidEvent as exc:
                raise HTTPException(
                    status_code=422,
                    detail=str(exc),
                ) from exc
            return JSONResponse(response)

        @application.get("/demo/dialogs/{dialog_id}", response_model=DialogView)
        def demo_dialog(dialog_id: str) -> DialogView:
            dialog = service.get_dialog(dialog_id)
            if dialog is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="dialog not found",
                )
            return dialog

    return application


app = create_app()
