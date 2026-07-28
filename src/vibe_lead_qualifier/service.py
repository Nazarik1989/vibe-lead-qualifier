"""Lead qualification use case and event normalization."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from vibe_lead_qualifier.extractor import BriefExtractor, RuleBasedBriefExtractor
from vibe_lead_qualifier.models import BriefPatch, BriefState, DialogView
from vibe_lead_qualifier.repository import SQLiteRepository

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("name", "service", "budget", "deadline", "contact")
QUESTIONS = {
    "name": "Как вас зовут?",
    "service": "Какую услугу или задачу нужно выполнить?",
    "budget": "Какой бюджет вы планируете на эту задачу?",
    "deadline": "К какому сроку нужен результат?",
    "contact": "Как с вами лучше связаться: телефон, e-mail или Telegram?",
}
SUPPORTED_PASSIVE_EVENTS = {"webhook.test", "generation.complete", "generation.error"}


class InvalidEvent(ValueError):
    """A signed payload cannot be normalized into the documented event fields."""


class LeadQualifierService:
    def __init__(
        self,
        repository: SQLiteRepository,
        extractor: BriefExtractor | None = None,
    ) -> None:
        self.repository = repository
        self.extractor = extractor or RuleBasedBriefExtractor()

    def process_event(
        self,
        payload: Mapping[str, Any],
        *,
        source: str = "vibe",
    ) -> dict[str, Any]:
        event_type = payload.get("event")
        if not isinstance(event_type, str) or not event_type:
            raise InvalidEvent("В подписанном payload отсутствует строковое поле event")

        if event_type == "agent.message":
            response, duplicate = self._process_agent_message(payload, source=source)
        else:
            response = self._passive_response(event_type)
            fingerprint = self._fingerprint(payload, event_type=event_type, source=source)
            response, duplicate = self.repository.process_event_once(
                fingerprint=fingerprint,
                event_type=event_type,
                response=response,
            )

        logger.info(
            "event processed: type=%s duplicate=%s",
            event_type,
            duplicate,
        )
        return response

    def get_dialog(self, dialog_id: str) -> DialogView | None:
        state = self.repository.get_dialog(dialog_id)
        if state is None:
            return None
        missing = self.missing_fields(state)
        return DialogView(
            dialog_id=dialog_id,
            brief=state,
            missing_fields=missing,
            ready=not missing,
        )

    @staticmethod
    def missing_fields(state: BriefState) -> list[str]:
        return [field for field in REQUIRED_FIELDS if getattr(state, field) is None]

    def _process_agent_message(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
    ) -> tuple[dict[str, Any], bool]:
        message = payload.get("message")
        message_data = message if isinstance(message, Mapping) else payload
        context_value = message_data.get("context")
        context = context_value if isinstance(context_value, Mapping) else {}

        dialog_id = message_data.get("dialog_id")
        if dialog_id is None:
            dialog_id = context.get("dialog_id")
        text = message_data.get("text")
        if not isinstance(dialog_id, (str, int)) or not str(dialog_id).strip():
            raise InvalidEvent("В agent.message отсутствует context.dialog_id")
        if not isinstance(text, str) or not text.strip():
            raise InvalidEvent("В agent.message отсутствует непустое поле text")

        normalized_dialog_id = str(dialog_id)
        fingerprint = self._fingerprint(
            payload,
            event_type="agent.message",
            source=source,
            message_data=message_data,
            dialog_id=normalized_dialog_id,
        )

        def handler(current: BriefState) -> tuple[BriefState, dict[str, Any]]:
            patch = self.extractor.extract(text, current, context)
            updated = self._merge(current, patch)
            missing = self.missing_fields(updated)
            if missing:
                return updated, {"reply": QUESTIONS[missing[0]]}

            if updated.deal_created:
                return updated, {
                    "reply": "Бриф уже собран и передан в CRM. Менеджер свяжется с вами.",
                }

            updated.deal_created = True
            action = self._build_deal_action(updated)
            reply = (
                f"Спасибо, {updated.name}! Бриф собран и передан менеджеру. "
                "Мы свяжемся с вами по указанному контакту."
            )
            return updated, {"reply": reply, "actions": [action]}

        return self.repository.process_message_once(
            fingerprint=fingerprint,
            event_type="agent.message",
            dialog_id=normalized_dialog_id,
            handler=handler,
        )

    @staticmethod
    def _merge(current: BriefState, patch: BriefPatch) -> BriefState:
        values = current.model_dump()
        for field, value in patch.model_dump(exclude_none=True).items():
            if field == "comment" and values.get("comment") and value != values["comment"]:
                values[field] = f"{values['comment']} | {value}"
            else:
                values[field] = value
        return BriefState.model_validate(values)

    @staticmethod
    def _build_deal_action(state: BriefState) -> dict[str, Any]:
        title = f"Заявка: {state.service} — {state.name}"[:255]
        comments = [
            f"Клиент: {state.name}",
            f"Задача: {state.service}",
            f"Срок: {state.deadline}",
            f"Контакт: {state.contact}",
        ]
        if state.comment:
            comments.append(f"Комментарий: {state.comment}")
        return {
            "method": "crm.deal.add",
            "params": {
                "fields": {
                    "TITLE": title,
                    "OPPORTUNITY": state.budget,
                    "CURRENCY_ID": "RUB",
                    "CATEGORY_ID": 0,
                    "COMMENTS": "\n".join(comments),
                }
            },
        }

    @staticmethod
    def _passive_response(event_type: str) -> dict[str, Any]:
        if event_type == "webhook.test":
            return {"status": "ok"}
        if event_type in {"generation.complete", "generation.error"}:
            return {"status": "ok", "event": event_type}
        return {"status": "ok", "event": event_type, "ignored": True}

    @staticmethod
    def _fingerprint(
        payload: Mapping[str, Any],
        *,
        event_type: str,
        source: str,
        message_data: Mapping[str, Any] | None = None,
        dialog_id: str | None = None,
    ) -> str:
        explicit_id = payload.get("event_id")
        if explicit_id is None:
            explicit_id = payload.get("delivery_id")
        if explicit_id is not None:
            material = f"{source}|{event_type}|event|{explicit_id}"
        elif event_type == "agent.message" and message_data is not None:
            message_id = message_data.get("message_id")
            if message_id is None:
                message_id = message_data.get("id")
            if message_id is not None:
                material = f"{source}|{event_type}|{dialog_id}|{message_id}"
            else:
                material = LeadQualifierService._canonical_material(source, payload)
        elif payload.get("generation_id") is not None:
            attempt = payload.get("attempt", 1)
            material = f"{source}|{event_type}|{payload['generation_id']}|{attempt}"
        else:
            material = LeadQualifierService._canonical_material(source, payload)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_material(source: str, payload: Mapping[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"{source}|body|{canonical}"
