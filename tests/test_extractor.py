from __future__ import annotations

import pytest

from vibe_lead_qualifier.extractor import RuleBasedBriefExtractor
from vibe_lead_qualifier.models import BriefState


def test_labelled_multiline_brief_supports_common_separators_and_normalization() -> None:
    extractor = RuleBasedBriefExtractor()
    text = """
        Имя — Анна Петрова;
        Задача = разработка чат-бота;
        Бюджет: 2.500.000 ₽;
        Дедлайн - до 1 декабря 2026;
        Контакт: anna.pet@example.com;
        Пожелания: интеграция с CRM.
    """

    patch = extractor.extract(text, BriefState())

    assert patch.model_dump() == {
        "name": "Анна Петрова",
        "service": "разработка чат-бота",
        "budget": 2500000,
        "deadline": "до 1 декабря 2026",
        "contact": "anna.pet@example.com",
        "comment": "интеграция с CRM",
    }


def test_compact_one_line_brief_is_deterministic() -> None:
    extractor = RuleBasedBriefExtractor()
    text = (
        "Имя: Илья, услуга: настройка рекламы, бюджет: 80 тыс., "
        "срок: к концу августа, контакт: @ilya_test"
    )

    first = extractor.extract(text, BriefState())
    second = extractor.extract(text, BriefState(name="Другое имя"))

    assert first == second
    assert first.model_dump() == {
        "name": "Илья",
        "service": "настройка рекламы",
        "budget": 80000,
        "deadline": "к концу августа",
        "contact": "@ilya_test",
        "comment": None,
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Бюджет: 50 000", 50000),
        ("Бюджет: 1,5 млн", 1500000),
        ("Готов потратить около 75 тыс.", 75000),
        ("Готов потратить 250k", 250000),
        ("Стоимость задачи — 120 000 руб.", 120000),
        ("Бюджет: 100.000 руб.", 100000),
        ("Бюджет: 150,000", 150000),
    ],
)
def test_budget_formats_are_normalized_to_integer_rubles(text: str, expected: int) -> None:
    patch = RuleBasedBriefExtractor().extract(text, BriefState())

    assert patch.budget == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Пишите на Lead.Test+tag@example.com", "Lead.Test+tag@example.com"),
        ("Телефон: +7 (999) 123-45-67", "+7 (999) 123-45-67"),
        ("Telegram: @lead_owner", "@lead_owner"),
        ("Контакт: предпочитаю звонок через секретаря", "предпочитаю звонок через секретаря"),
    ],
)
def test_contact_formats_are_extracted_without_guessing(text: str, expected: str) -> None:
    patch = RuleBasedBriefExtractor().extract(text, BriefState())

    assert patch.contact == expected


def test_contact_precedence_is_email_then_phone_then_telegram() -> None:
    text = "@lead_owner, +7 (999) 123-45-67, lead@example.com"

    patch = RuleBasedBriefExtractor().extract(text, BriefState())

    assert patch.contact == "lead@example.com"


def test_natural_language_and_context_name_are_supported() -> None:
    extractor = RuleBasedBriefExtractor()

    natural = extractor.extract(
        "Меня зовут Иван Петров. Мне нужен лендинг, бюджет 90 тыс., до 10 сентября.",
        BriefState(),
    )
    from_context = extractor.extract(
        "Нужен аудит сайта.",
        BriefState(),
        {"user_name": "  Мария  "},
    )

    assert natural.name == "Иван Петров"
    assert natural.service == "лендинг"
    assert natural.budget == 90000
    assert natural.deadline == "до 10 сентября"
    assert from_context.name == "Мария"
    assert from_context.service == "аудит сайта"
