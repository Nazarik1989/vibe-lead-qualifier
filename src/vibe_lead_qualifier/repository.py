"""SQLite persistence and transactionally enforced local-processing idempotency."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from vibe_lead_qualifier.models import BriefState

MessageHandler = Callable[[BriefState], tuple[BriefState, dict[str, Any]]]


class SQLiteRepository:
    """Connection-per-operation repository suitable for a compact webhook service."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS dialogs (
                    dialog_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS processed_events (
                    fingerprint TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    dialog_id TEXT,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    dialog_ref TEXT,
                    outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_processed_dialog
                    ON processed_events(dialog_id);
                CREATE INDEX IF NOT EXISTS idx_event_log_created
                    ON event_log(created_at);
                """
            )

    def ping(self) -> bool:
        try:
            with closing(self._connect()) as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def get_dialog(self, dialog_id: str) -> BriefState | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT state_json FROM dialogs WHERE dialog_id = ?",
                (dialog_id,),
            ).fetchone()
        if row is None:
            return None
        return BriefState.model_validate_json(row["state_json"])

    def process_message_once(
        self,
        *,
        fingerprint: str,
        event_type: str,
        dialog_id: str,
        handler: MessageHandler,
    ) -> tuple[dict[str, Any], bool]:
        """Update one dialog and cache its response in one write transaction."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cached = connection.execute(
                "SELECT response_json FROM processed_events WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if cached is not None:
                response = json.loads(cached["response_json"])
                self._write_event_log(
                    connection,
                    fingerprint=fingerprint,
                    event_type=event_type,
                    dialog_id=dialog_id,
                    outcome="duplicate",
                )
                connection.execute("COMMIT")
                return response, True

            row = connection.execute(
                "SELECT state_json FROM dialogs WHERE dialog_id = ?",
                (dialog_id,),
            ).fetchone()
            current = (
                BriefState.model_validate_json(row["state_json"])
                if row is not None
                else BriefState()
            )
            updated, response = handler(current)
            state_json = updated.model_dump_json()
            response_json = json.dumps(response, ensure_ascii=False, separators=(",", ":"))

            connection.execute(
                """
                INSERT INTO dialogs(dialog_id, state_json)
                VALUES (?, ?)
                ON CONFLICT(dialog_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (dialog_id, state_json),
            )
            connection.execute(
                """
                INSERT INTO processed_events(
                    fingerprint, event_type, dialog_id, response_json
                ) VALUES (?, ?, ?, ?)
                """,
                (fingerprint, event_type, dialog_id, response_json),
            )
            self._write_event_log(
                connection,
                fingerprint=fingerprint,
                event_type=event_type,
                dialog_id=dialog_id,
                outcome="processed",
            )
            connection.execute("COMMIT")
            return response, False
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def process_event_once(
        self,
        *,
        fingerprint: str,
        event_type: str,
        response: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cached = connection.execute(
                "SELECT response_json FROM processed_events WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if cached is not None:
                saved = json.loads(cached["response_json"])
                self._write_event_log(
                    connection,
                    fingerprint=fingerprint,
                    event_type=event_type,
                    dialog_id=None,
                    outcome="duplicate",
                )
                connection.execute("COMMIT")
                return saved, True

            response_json = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
            connection.execute(
                """
                INSERT INTO processed_events(fingerprint, event_type, response_json)
                VALUES (?, ?, ?)
                """,
                (fingerprint, event_type, response_json),
            )
            self._write_event_log(
                connection,
                fingerprint=fingerprint,
                event_type=event_type,
                dialog_id=None,
                outcome="processed",
            )
            connection.execute("COMMIT")
            return response, False
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @staticmethod
    def _write_event_log(
        connection: sqlite3.Connection,
        *,
        fingerprint: str,
        event_type: str,
        dialog_id: str | None,
        outcome: str,
    ) -> None:
        # The audit journal stores no message text or brief data. Even the external
        # dialog identifier is pseudonymized because it is not needed for support.
        dialog_ref = (
            hashlib.sha256(dialog_id.encode("utf-8")).hexdigest()[:16] if dialog_id else None
        )
        connection.execute(
            """
            INSERT INTO event_log(fingerprint, event_type, dialog_ref, outcome)
            VALUES (?, ?, ?, ?)
            """,
            (fingerprint, event_type, dialog_ref, outcome),
        )
