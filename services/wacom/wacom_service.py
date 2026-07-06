"""Device-neutral Wacom service and provider contract."""

from abc import ABC, abstractmethod

from services.logging.logging_service import LoggingService
from services.signature.signature_service import CapturedSignature


class WacomProvider(ABC):
    """Port implemented by each supported signature device."""

    @abstractmethod
    def connect(self) -> None:
        """Connect to the device."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the device."""

    @abstractmethod
    def capture_signature(self) -> CapturedSignature:
        """Capture a signature using a provider-specific SDK."""


class WacomService:
    """Facade that isolates workflows from a concrete Wacom model."""

    def __init__(
        self, provider: WacomProvider, logger: LoggingService
    ) -> None:
        self._provider = provider
        self._logger = logger

    def connect(self) -> None:
        self._provider.connect()
        self._logger.info("Signature device connected")

    def disconnect(self) -> None:
        self._provider.disconnect()
        self._logger.info("Signature device disconnected")

    def capture_signature(self) -> CapturedSignature:
        signature = self._provider.capture_signature()
        self._logger.info("Signature captured")
        return signature

