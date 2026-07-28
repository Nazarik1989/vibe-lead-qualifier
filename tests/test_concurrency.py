from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from vibe_lead_qualifier.repository import SQLiteRepository
from vibe_lead_qualifier.service import LeadQualifierService


def test_concurrent_complete_messages_emit_only_one_crm_action(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "concurrent.sqlite3")
    repository.initialize()
    service = LeadQualifierService(repository)

    def process(message_id: str) -> dict[str, Any]:
        return service.process_event(
            {
                "event": "agent.message",
                "message_id": message_id,
                "text": (
                    "Имя: Иван; услуга: сайт; бюджет: 50000; срок: 2 недели; контакт: @ivan_test"
                ),
                "context": {"dialog_id": "concurrent-dialog"},
            }
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(process, ("message-1", "message-2")))

    assert sum("actions" in response for response in responses) == 1
    state = repository.get_dialog("concurrent-dialog")
    assert state is not None
    assert state.deal_created is True
