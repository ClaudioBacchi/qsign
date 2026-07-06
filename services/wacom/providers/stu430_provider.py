"""Wacom STU-430 provider placeholder."""

from services.signature.signature_service import CapturedSignature
from services.wacom.wacom_service import WacomProvider


class STU430Provider(WacomProvider):
    """Reserved integration point for the Wacom STU SDK."""

    def connect(self) -> None:
        raise NotImplementedError("Wacom STU-430 support is planned for Milestone 4")

    def disconnect(self) -> None:
        raise NotImplementedError("Wacom STU-430 support is planned for Milestone 4")

    def capture_signature(self) -> CapturedSignature:
        raise NotImplementedError("Wacom STU-430 support is planned for Milestone 4")

