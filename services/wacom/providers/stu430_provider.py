"""Wacom STU-430 signature provider."""

import threading

from services.signature.signature_service import CapturedSignature
from services.wacom.stu_sdk import WacomSTUSDK
from services.wacom.wacom_service import WacomProvider


class STU430Provider(WacomProvider):
    """Capture signatures from a Wacom STU-430 tablet."""

    def __init__(self, sdk: WacomSTUSDK | None = None) -> None:
        self._sdk = sdk or WacomSTUSDK()
        self._cancel_event = threading.Event()

    def connect(self) -> None:
        devices = [device for device in self._sdk.get_usb_devices() if device.is_wacom_stu]
        if not devices:
            raise RuntimeError("Nessuna tavoletta Wacom STU collegata")
        self._sdk.get_tablet_info(devices[0])

    def disconnect(self) -> None:
        return None

    def capture_signature(self) -> CapturedSignature:
        self._cancel_event.clear()
        return self._sdk.capture_signature(cancel_event=self._cancel_event)

    def cancel_signature_capture(self) -> None:
        self._cancel_event.set()
