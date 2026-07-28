"""Deterministic brief extraction with a replaceable interface."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol

from vibe_lead_qualifier.models import BriefPatch, BriefState


class BriefExtractor(Protocol):
    """Boundary that can later be implemented by a schema-guided LLM extractor."""

    def extract(
        self,
        text: str,
        current: BriefState,
        context: Mapping[str, Any] | None = None,
    ) -> BriefPatch: ...


_NEXT_LABEL = (
    r"(?=\s*(?:[;\n]|[.!?]\s+(?=[А-ЯЁA-Z])|,\s*(?:имя|клиент|услуга|задача|"
    r"бюджет|срок|дедлайн|контакт|телефон|почта|комментарий)\s*[:=\-—])|$)"
)


def _label_value(text: str, labels: str) -> str | None:
    pattern = rf"(?:^|[;\n])\s*(?:{labels})\s*[:=\-—]\s*(.+?){_NEXT_LABEL}"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        # Also support compact one-line briefs: ``Имя: Иван, бюджет: 50000``.
        pattern = rf"(?:^|[,;]\s*)(?:{labels})\s*[:=\-—]\s*(.+?){_NEXT_LABEL}"
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return _clean_value(match.group(1)) if match else None


def _clean_value(value: str) -> str:
    return value.strip(" \t\r\n,;.!—-")


def _parse_budget(text: str) -> int | None:
    labelled = _label_value(text, r"бюджет")
    source = labelled if labelled else text
    patterns = [
        r"(?:бюджет(?:ом)?|готов(?:ы|а)?\s+потратить)\s*(?:около|до|примерно)?\s*"
        r"[:=\-—]?\s*(\d[\d\s.,]*?)\s*(млн|миллион\w*|тыс\.?|тысяч\w*|к|k|₽|руб\w*)",
        r"(?<!\d)(\d[\d\s.,]*?)\s*(млн|миллион\w*|тыс\.?|тысяч\w*|к|k|₽|руб\w*)",
    ]
    if labelled:
        patterns.insert(
            0,
            r"^\s*(\d[\d\s.,]*)(?:\s*(млн|миллион\w*|тыс\.?|тысяч\w*|к|k|₽|руб\w*))?",
        )

    for pattern in patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE)
        if not match:
            continue
        raw_number = re.sub(r"\s+", "", match.group(1))
        suffix = (match.group(2) or "").lower()
        has_scale_suffix = suffix.startswith(("млн", "миллион", "тыс", "тысяч")) or suffix in {
            "к",
            "k",
        }
        looks_like_grouped_integer = re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw_number)
        if looks_like_grouped_integer and not has_scale_suffix:
            normalized = raw_number.replace(",", "").replace(".", "")
        elif "," in raw_number or "." in raw_number:
            normalized = raw_number.replace(",", ".")
            if normalized.count(".") > 1:
                normalized = normalized.replace(".", "")
        else:
            normalized = raw_number
        try:
            amount = float(normalized)
        except ValueError:
            continue
        if suffix.startswith(("млн", "миллион")):
            amount *= 1_000_000
        elif suffix.startswith(("тыс", "тысяч")) or suffix in {"к", "k"}:
            amount *= 1_000
        return round(amount)
    return None


def _find_contact(text: str) -> str | None:
    email = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, re.IGNORECASE)
    if email:
        return email.group(0)

    phone = re.search(r"(?<!\d)(?:\+7|8)[\s\-(]*(?:\d[\s\-()]*){10}(?!\d)", text)
    if phone:
        return re.sub(r"\s+", " ", phone.group(0)).strip()

    telegram = re.search(r"(?<![\w.])@[A-Z0-9_]{5,32}\b", text, re.IGNORECASE)
    if telegram:
        return telegram.group(0)

    labelled = _label_value(text, r"контакт|телефон|почта|e-?mail|telegram|телеграм")
    return labelled


class RuleBasedBriefExtractor:
    """Small, explainable Russian-language extractor for the MVP."""

    def extract(
        self,
        text: str,
        current: BriefState,
        context: Mapping[str, Any] | None = None,
    ) -> BriefPatch:
        del current  # The deterministic rules only need it through the stable interface.
        context = context or {}

        name = _label_value(text, r"имя|клиент")
        if not name:
            match = re.search(
                r"(?:меня\s+зовут|мо[её]\s+имя)\s+"
                r"([А-ЯЁA-Z][А-ЯЁа-яёA-Za-z-]*(?:\s+[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z-]*)?)",
                text,
                re.IGNORECASE,
            )
            name = _clean_value(match.group(1)) if match else None
        if not name:
            context_name = context.get("user_name")
            if isinstance(context_name, str) and context_name.strip():
                name = _clean_value(context_name)

        service = _label_value(text, r"услуга|задача")
        if not service:
            match = re.search(
                r"(?:мне\s+)?(?:нужен|нужна|нужно|нужны|хочу|интересует)\s+(.+?)"
                r"(?=[.;\n]|,\s*(?:бюджет|срок|дедлайн|контакт|телефон|почта)\b|$)",
                text,
                re.IGNORECASE,
            )
            service = _clean_value(match.group(1)) if match else None

        deadline = _label_value(text, r"срок|дедлайн")
        if not deadline:
            match = re.search(
                r"(?:срок|дедлайн)\s*(?:[:=\-—]\s*)?(.+?)"
                r"(?=[.;\n]|,\s*(?:бюджет|контакт|телефон|почта)\b|$)",
                text,
                re.IGNORECASE,
            )
            deadline = _clean_value(match.group(1)) if match else None
        if not deadline:
            match = re.search(
                r"\b(?:до|к)\s+(?:концу\s+\w+|\d{1,2}\s+[а-яё]+(?:\s+\d{4})?)",
                text,
                re.IGNORECASE,
            )
            deadline = _clean_value(match.group(0)) if match else None
        if not deadline:
            match = re.search(
                r"\b(?:за|в\s+течение)\s+\d+\s+"
                r"(?:дн(?:я|ей)|день|недел(?:ю|и|ь)|месяц(?:а|ев)?)\b",
                text,
                re.IGNORECASE,
            )
            deadline = _clean_value(match.group(0)) if match else None

        return BriefPatch(
            name=name,
            service=service,
            budget=_parse_budget(text),
            deadline=deadline,
            contact=_find_contact(text),
            comment=_label_value(text, r"комментарий|пожелания"),
        )
