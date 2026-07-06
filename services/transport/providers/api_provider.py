"""HTTP API transport placeholder."""

from pathlib import Path

from services.transport.transport_service import TransportService


class APIProvider(TransportService):
    """Reserved boundary for a future HTTP API transport."""

    def download_document(self, document_id: str, destination: Path) -> Path:
        raise NotImplementedError("HTTP API transport is planned for Milestone 3")

    def upload_document(self, document_id: str, source: Path) -> None:
        raise NotImplementedError("HTTP API transport is planned for Milestone 3")

