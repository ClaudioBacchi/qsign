"""Tests for persistent document flow logging."""

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from services.logging.document_flow_log_service import DocumentFlowLogService


class DocumentFlowLogServiceTests(unittest.TestCase):
    def test_append_and_read_events_for_selected_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qsign_documenti.log"
            service = DocumentFlowLogService(path)

            service.append_event(
                document="ieri.pdf",
                status="Scaricato",
                timestamp=datetime(2026, 7, 22, 9, 0, 0),
            )
            service.append_event(
                document="oggi.pdf",
                status="Caricato",
                timestamp=datetime(2026, 7, 23, 10, 30, 0),
            )

            events = service.events_for_day(datetime(2026, 7, 23).date())

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].document, "oggi.pdf")
            self.assertEqual(events[0].status, "Caricato")
            self.assertEqual(events[0].timestamp, datetime(2026, 7, 23, 10, 30, 0))

    def test_invalid_lines_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qsign_documenti.log"
            path.write_text(
                "riga non json\n"
                "{\"timestamp\":\"2026-07-23T11:00:00\","
                "\"document\":\"demo.pdf\",\"status\":\"Firmato\"}\n",
                encoding="utf-8",
            )
            service = DocumentFlowLogService(path)

            events = service.events_for_day(datetime(2026, 7, 23).date())

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].document, "demo.pdf")


if __name__ == "__main__":
    unittest.main()
