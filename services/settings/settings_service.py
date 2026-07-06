"""Typed settings sections without a persistence-format commitment."""

from dataclasses import dataclass, field

from services.logging.logging_service import LoggingService


@dataclass(slots=True)
class GeneralSettings:
    pass


@dataclass(slots=True)
class TransportSettings:
    pass


@dataclass(slots=True)
class PDFSettings:
    pass


@dataclass(slots=True)
class CertificateSettings:
    pass


@dataclass(slots=True)
class WacomSettings:
    pass


@dataclass(slots=True)
class LoggingSettings:
    pass


@dataclass(slots=True)
class Settings:
    general: GeneralSettings = field(default_factory=GeneralSettings)
    transport: TransportSettings = field(default_factory=TransportSettings)
    pdf: PDFSettings = field(default_factory=PDFSettings)
    certificates: CertificateSettings = field(default_factory=CertificateSettings)
    wacom: WacomSettings = field(default_factory=WacomSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)


class SettingsService:
    """Own typed settings while storage is intentionally undecided."""

    def __init__(self, logger: LoggingService) -> None:
        self._logger = logger
        self._settings = Settings()

    def load(self) -> Settings:
        self._logger.debug("Default settings loaded")
        return self._settings

