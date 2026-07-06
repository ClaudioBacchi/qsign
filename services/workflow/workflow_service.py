"""Top-level workflow contract for QSign use cases."""

from pathlib import Path

from models.pdf_document import PDFDocument
from services.logging.logging_service import LoggingService
from services.signature.signature_service import CapturedSignature


class WorkflowService:
    """Stable application-facing facade; behavior arrives in Milestone 6."""

    def __init__(self, logger: LoggingService) -> None:
        self._logger = logger

    def load_document(self, source: str) -> PDFDocument:
        raise NotImplementedError("Client workflows are planned for Milestone 6")

    def request_signature(self) -> CapturedSignature:
        raise NotImplementedError("Client workflows are planned for Milestone 6")

    def apply_signature(self, signature: CapturedSignature) -> None:
        raise NotImplementedError("Client workflows are planned for Milestone 6")

    def save_document(self, destination: Path | None = None) -> PDFDocument:
        raise NotImplementedError("Client workflows are planned for Milestone 6")

    def upload_document(self) -> None:
        raise NotImplementedError("Client workflows are planned for Milestone 6")

    def close_document(self) -> None:
        raise NotImplementedError("Client workflows are planned for Milestone 6")

