"""Persistent operational log for document workflow events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DocumentFlowEvent:
    document: str
    status: str
    timestamp: datetime


class DocumentFlowLogService:
    """Append and read qSign document flow events from a JSON-lines log."""

    DEFAULT_LOG_PATH = Path("logs") / "qsign_documenti.log"

    def __init__(self, path: str | Path = DEFAULT_LOG_PATH) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def append_event(
        self,
        *,
        document: str,
        status: str,
        timestamp: datetime | None = None,
    ) -> None:
        event_timestamp = timestamp or datetime.now()
        payload = {
            "timestamp": event_timestamp.isoformat(timespec="seconds"),
            "document": document,
            "status": status,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")

    def events_for_day(self, day: date | None = None) -> tuple[DocumentFlowEvent, ...]:
        selected_day = day or datetime.now().date()
        if not self._path.is_file():
            return ()
        events: list[DocumentFlowEvent] = []
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    event = self._parse_event(line)
                    if event is not None and event.timestamp.date() == selected_day:
                        events.append(event)
        except OSError:
            return ()
        return tuple(events)

    @staticmethod
    def _parse_event(line: str) -> DocumentFlowEvent | None:
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        try:
            timestamp = datetime.fromisoformat(str(payload.get("timestamp") or ""))
        except ValueError:
            return None
        document = str(payload.get("document") or "").strip()
        status = str(payload.get("status") or "").strip()
        if not document or not status:
            return None
        return DocumentFlowEvent(
            document=document,
            status=status,
            timestamp=timestamp,
        )
