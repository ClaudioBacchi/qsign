"""FTP transport placeholder."""

from pathlib import Path

from services.transport.transport_service import TransportService


class FTPProvider(TransportService):
    """Reserved boundary for a future FTP transport."""

    def download_document(self, document_id: str, destination: Path) -> Path:
        raise NotImplementedError("FTP transport is planned for Milestone 3")

    def upload_document(self, document_id: str, source: Path) -> None:
        raise NotImplementedError("FTP transport is planned for Milestone 3")

