"""Abstract transport service."""

from abc import ABC, abstractmethod
from pathlib import Path

from services.logging.logging_service import LoggingService


class TransportService(ABC):
    """Port for downloading and uploading documents."""

    def __init__(self, logger: LoggingService) -> None:
        self._logger = logger

    @abstractmethod
    def download_document(self, document_id: str, destination: Path) -> Path:
        """Download a document and return its local path."""

    @abstractmethod
    def upload_document(self, document_id: str, source: Path) -> None:
        """Upload a local document."""

