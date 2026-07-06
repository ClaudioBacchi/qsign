"""Signature orchestration placeholder."""

from dataclasses import dataclass

from services.logging.logging_service import LoggingService


@dataclass(frozen=True, slots=True)
class CapturedSignature:
    """Device-neutral signature payload."""

    content: bytes
    media_type: str


class SignatureService:
    """Coordinates signature capture and application in a future milestone."""

    def __init__(self, logger: LoggingService) -> None:
        self._logger = logger

    def request_signature(self) -> CapturedSignature:
        raise NotImplementedError("Signature capture is planned for Milestone 4")

    def apply_signature(self, signature: CapturedSignature) -> None:
        raise NotImplementedError("Signature application is planned for Milestone 6")

