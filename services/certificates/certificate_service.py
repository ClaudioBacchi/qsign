"""Certificate operations reserved for Milestone 5."""

from dataclasses import dataclass
from pathlib import Path

from services.logging.logging_service import LoggingService


@dataclass(frozen=True, slots=True)
class Certificate:
    """Provider-neutral certificate descriptor."""

    identifier: str
    display_name: str


class CertificateService:
    """Facade for future replaceable certificate providers."""

    def __init__(self, logger: LoggingService) -> None:
        self._logger = logger

    def list_certificates(self) -> tuple[Certificate, ...]:
        raise NotImplementedError("Certificate discovery is planned for Milestone 5")

    def get_default_certificate(self) -> Certificate | None:
        raise NotImplementedError("Certificate selection is planned for Milestone 5")

    def sign_pdf(
        self, document_path: Path, certificate: Certificate
    ) -> Path:
        raise NotImplementedError("PAdES signing is planned for Milestone 5")

