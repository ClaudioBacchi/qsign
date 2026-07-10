"""Minimal ctypes access to the Wacom STU SDK."""

from __future__ import annotations

import ctypes
import time
import threading
from dataclasses import dataclass
from pathlib import Path

from services.signature.signature_service import CapturedSignature


_DEFAULT_DLL_PATHS = (
    Path(r"C:\Program Files (x86)\Wacom STU SDK\C\bin\x64\wgssSTU.dll"),
    Path(r"C:\Program Files (x86)\Wacom STU SDK\COM\bin\x64\wgssSTU.dll"),
)

_WACOM_VENDOR_ID = 0x056A
_PRODUCT_NAMES = {
    0x00A1: "STU-500",
    0x00A2: "STU-300",
    0x00A3: "STU-520A",
    0x00A4: "STU-430",
    0x00A5: "STU-530",
    0x00A6: "STU-430V",
    0x00A8: "STU-540",
}


class WacomSTUSDKError(RuntimeError):
    """Raised when the Wacom STU SDK cannot be loaded or queried."""


@dataclass(frozen=True, slots=True)
class STUPenPoint:
    """Single decoded pen sample in tablet coordinates."""

    x: int
    y: int
    pressure: int
    touching: bool


@dataclass(frozen=True, slots=True)
class STUUsbDevice:
    """USB device entry returned by the Wacom STU SDK."""

    vendor_id: int
    product_id: int
    device_version: int
    file_name: str
    bulk_file_name: str

    @property
    def model_name(self) -> str:
        return _PRODUCT_NAMES.get(self.product_id, "Unknown STU device")

    @property
    def is_wacom_stu(self) -> bool:
        return self.vendor_id == _WACOM_VENDOR_ID and self.product_id in _PRODUCT_NAMES


@dataclass(frozen=True, slots=True)
class STUTabletInfo:
    """Basic information read from a connected STU tablet."""

    model_name: str
    firmware_major: int
    firmware_minor: int
    tablet_max_x: int
    tablet_max_y: int
    tablet_max_pressure: int
    screen_width: int
    screen_height: int
    max_report_rate: int
    resolution: int
    encoding_flag: int


@dataclass(frozen=True, slots=True)
class _ScreenRect:
    left: int
    top: int
    right: int
    bottom: int

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


@dataclass(frozen=True, slots=True)
class _TabletLayout:
    signature_area: _ScreenRect
    clear_button: _ScreenRect
    submit_button: _ScreenRect


class _UsbDeviceBase(ctypes.Structure):
    _fields_ = (
        ("idVendor", ctypes.c_uint16),
        ("idProduct", ctypes.c_uint16),
        ("bcdDevice", ctypes.c_uint16),
    )


class _UsbDevice(ctypes.Structure):
    _fields_ = (
        ("usbDevice", _UsbDeviceBase),
        ("fileName", ctypes.c_char_p),
        ("bulkFileName", ctypes.c_char_p),
    )


class _Information(ctypes.Structure):
    _fields_ = (
        ("modelNameNullTerminated", ctypes.c_char * 10),
        ("firmwareMajorVersion", ctypes.c_uint8),
        ("firmwareMinorVersion", ctypes.c_uint8),
        ("secureIc", ctypes.c_uint8),
        ("secureIcVersion", ctypes.c_uint8 * 4),
    )


class _Capability(ctypes.Structure):
    _fields_ = (
        ("tabletMaxX", ctypes.c_uint16),
        ("tabletMaxY", ctypes.c_uint16),
        ("tabletMaxPressure", ctypes.c_uint16),
        ("screenWidth", ctypes.c_uint16),
        ("screenHeight", ctypes.c_uint16),
        ("maxReportRate", ctypes.c_uint8),
        ("resolution", ctypes.c_uint16),
        ("encodingFlag", ctypes.c_uint8),
    )


class _Rectangle(ctypes.Structure):
    _fields_ = (
        ("upperLeftXpixel", ctypes.c_uint16),
        ("upperLeftYpixel", ctypes.c_uint16),
        ("lowerRightXpixel", ctypes.c_uint16),
        ("lowerRightYpixel", ctypes.c_uint16),
    )


class _PenData(ctypes.Structure):
    _fields_ = (
        ("rdy", ctypes.c_uint8),
        ("sw", ctypes.c_uint8),
        ("pressure", ctypes.c_uint16),
        ("x", ctypes.c_uint16),
        ("y", ctypes.c_uint16),
    )


class _PenDataOption(ctypes.Structure):
    _fields_ = (
        ("rdy", ctypes.c_uint8),
        ("sw", ctypes.c_uint8),
        ("pressure", ctypes.c_uint16),
        ("x", ctypes.c_uint16),
        ("y", ctypes.c_uint16),
        ("option", ctypes.c_uint16),
    )


class _PenDataTimeCountSequence(ctypes.Structure):
    _fields_ = (
        ("rdy", ctypes.c_uint8),
        ("sw", ctypes.c_uint8),
        ("pressure", ctypes.c_uint16),
        ("x", ctypes.c_uint16),
        ("y", ctypes.c_uint16),
        ("timeCount", ctypes.c_uint16),
        ("sequence", ctypes.c_uint16),
    )


_PEN_CALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(_PenData)
)
_PEN_OPTION_CALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(_PenDataOption)
)
_PEN_TIME_SEQUENCE_CALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(_PenDataTimeCountSequence),
)
_IGNORED_REPORT_CALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p
)
_DECRYPT_CALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8)
)


class _ReportHandlerFunctionTable(ctypes.Structure):
    _fields_ = (
        ("onPenData", ctypes.c_void_p),
        ("onPenDataOption", ctypes.c_void_p),
        ("onPenDataEncrypted", ctypes.c_void_p),
        ("onPenDataEncryptedOption", ctypes.c_void_p),
        ("onDevicePublicKey", ctypes.c_void_p),
        ("decrypt", ctypes.c_void_p),
        ("onPenDataTimeCountSequence", ctypes.c_void_p),
        ("onPenDataTimeCountSequenceEncrypted", ctypes.c_void_p),
        ("onEncryptionStatus", ctypes.c_void_p),
        ("onEventData", ctypes.c_void_p),
        ("onEventDataPinPad", ctypes.c_void_p),
        ("onEventDataKeyPad", ctypes.c_void_p),
        ("onEventDataSignature", ctypes.c_void_p),
        ("onEventDataEncrypted", ctypes.c_void_p),
        ("onEventDataPinPadEncrypted", ctypes.c_void_p),
        ("onEventDataKeyPadEncrypted", ctypes.c_void_p),
        ("onEventDataSignatureEncrypted", ctypes.c_void_p),
    )


class _PenReportCollector:
    def __init__(self) -> None:
        self.points: list[STUPenPoint] = []
        self.raw_reports = 0
        self._on_pen_data = _PEN_CALLBACK(self._handle_pen_data)
        self._on_pen_data_option = _PEN_OPTION_CALLBACK(self._handle_pen_data_option)
        self._on_pen_data_time_sequence = _PEN_TIME_SEQUENCE_CALLBACK(
            self._handle_pen_data_time_sequence
        )
        self._ignored = _IGNORED_REPORT_CALLBACK(self._handle_ignored_report)
        self._decrypt = _DECRYPT_CALLBACK(self._handle_decrypt)
        ignored = ctypes.cast(self._ignored, ctypes.c_void_p).value
        self.table = _ReportHandlerFunctionTable(
            ctypes.cast(self._on_pen_data, ctypes.c_void_p).value,
            ctypes.cast(self._on_pen_data_option, ctypes.c_void_p).value,
            ignored,
            ignored,
            ignored,
            ctypes.cast(self._decrypt, ctypes.c_void_p).value,
            ctypes.cast(self._on_pen_data_time_sequence, ctypes.c_void_p).value,
            ignored,
            ignored,
            ignored,
            ignored,
            ignored,
            ignored,
            ignored,
            ignored,
            ignored,
            ignored,
        )

    def _append(self, x: int, y: int, pressure: int, sw: int) -> None:
        self.points.append(
            STUPenPoint(
                x=int(x),
                y=int(y),
                pressure=int(pressure),
                touching=bool(sw),
            )
        )

    def _handle_pen_data(
        self, _: ctypes.c_void_p, __: int, pen_data: ctypes.POINTER(_PenData)
    ) -> int:
        value = pen_data.contents
        self._append(value.x, value.y, value.pressure, value.sw)
        return 0

    def _handle_pen_data_option(
        self, _: ctypes.c_void_p, __: int, pen_data: ctypes.POINTER(_PenDataOption)
    ) -> int:
        value = pen_data.contents
        self._append(value.x, value.y, value.pressure, value.sw)
        return 0

    def _handle_pen_data_time_sequence(
        self,
        _: ctypes.c_void_p,
        __: int,
        pen_data: ctypes.POINTER(_PenDataTimeCountSequence),
    ) -> int:
        value = pen_data.contents
        self._append(value.x, value.y, value.pressure, value.sw)
        return 0

    @staticmethod
    def _handle_ignored_report(_: ctypes.c_void_p, __: int, ___: ctypes.c_void_p) -> int:
        return 0

    @staticmethod
    def _handle_decrypt(_: ctypes.c_void_p, __: ctypes.POINTER(ctypes.c_uint8)) -> int:
        return 1


class WacomSTUSDK:
    """Thin loader for the native Wacom STU SDK DLL."""

    def __init__(self, dll_path: Path | None = None) -> None:
        self.dll_path = dll_path or self._default_dll_path()
        try:
            self._dll = ctypes.WinDLL(str(self.dll_path))
        except OSError as error:
            raise WacomSTUSDKError(
                f"Unable to load Wacom STU SDK DLL: {self.dll_path}"
            ) from error
        self._configure_functions()

    @staticmethod
    def _default_dll_path() -> Path:
        for path in _DEFAULT_DLL_PATHS:
            if path.exists():
                return path
        raise WacomSTUSDKError(
            "Wacom STU SDK DLL not found. Expected wgssSTU.dll under "
            r"C:\Program Files (x86)\Wacom STU SDK."
        )

    def _configure_functions(self) -> None:
        self._dll.WacomGSS_getUsbDevices.argtypes = (
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.POINTER(_UsbDevice)),
        )
        self._dll.WacomGSS_getUsbDevices.restype = ctypes.c_int
        self._dll.WacomGSS_free.argtypes = (ctypes.c_void_p,)
        self._dll.WacomGSS_free.restype = ctypes.c_int
        self._dll.WacomGSS_UsbInterface_create_2.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._dll.WacomGSS_UsbInterface_create_2.restype = ctypes.c_int
        self._dll.WacomGSS_Interface_disconnect.argtypes = (ctypes.c_void_p,)
        self._dll.WacomGSS_Interface_disconnect.restype = ctypes.c_int
        self._dll.WacomGSS_Interface_free.argtypes = (ctypes.c_void_p,)
        self._dll.WacomGSS_Interface_free.restype = ctypes.c_int
        self._dll.WacomGSS_Protocol_getInformation.argtypes = (
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.POINTER(_Information)),
        )
        self._dll.WacomGSS_Protocol_getInformation.restype = ctypes.c_int
        self._dll.WacomGSS_Protocol_getCapability.argtypes = (
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.POINTER(_Capability)),
        )
        self._dll.WacomGSS_Protocol_getCapability.restype = ctypes.c_int
        self._dll.WacomGSS_Protocol_setClearScreen.argtypes = (ctypes.c_void_p,)
        self._dll.WacomGSS_Protocol_setClearScreen.restype = ctypes.c_int
        self._dll.WacomGSS_Protocol_setInkingMode.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint8,
        )
        self._dll.WacomGSS_Protocol_setInkingMode.restype = ctypes.c_int
        self._dll.WacomGSS_Protocol_setPenDataOptionMode.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint8,
        )
        self._dll.WacomGSS_Protocol_setPenDataOptionMode.restype = ctypes.c_int
        self._dll.WacomGSS_Interface_interfaceQueue.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._dll.WacomGSS_Interface_interfaceQueue.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_create_1.argtypes = (
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._dll.WacomGSS_Tablet_create_1.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_attach.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        self._dll.WacomGSS_Tablet_attach.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_disconnect.argtypes = (ctypes.c_void_p,)
        self._dll.WacomGSS_Tablet_disconnect.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_free.argtypes = (ctypes.c_void_p,)
        self._dll.WacomGSS_Tablet_free.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_interfaceQueue.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._dll.WacomGSS_Tablet_interfaceQueue.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_getInformation.argtypes = (
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.POINTER(_Information)),
        )
        self._dll.WacomGSS_Tablet_getInformation.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_getCapability.argtypes = (
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.POINTER(_Capability)),
        )
        self._dll.WacomGSS_Tablet_getCapability.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_setClearScreen.argtypes = (ctypes.c_void_p,)
        self._dll.WacomGSS_Tablet_setClearScreen.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_setInkingMode.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint8,
        )
        self._dll.WacomGSS_Tablet_setInkingMode.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_setPenDataOptionMode.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint8,
        )
        self._dll.WacomGSS_Tablet_setPenDataOptionMode.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_writeImage.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint8,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
        )
        self._dll.WacomGSS_Tablet_writeImage.restype = ctypes.c_int
        self._dll.WacomGSS_Tablet_setHandwritingDisplayArea.argtypes = (
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(_Rectangle),
        )
        self._dll.WacomGSS_Tablet_setHandwritingDisplayArea.restype = ctypes.c_int
        self._dll.WacomGSS_InterfaceQueue_try_getReport.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_int),
        )
        self._dll.WacomGSS_InterfaceQueue_try_getReport.restype = ctypes.c_int
        self._dll.WacomGSS_InterfaceQueue_free.argtypes = (ctypes.c_void_p,)
        self._dll.WacomGSS_InterfaceQueue_free.restype = ctypes.c_int
        self._dll.WacomGSS_ReportHandler_handleReport.argtypes = (
            ctypes.c_size_t,
            ctypes.POINTER(_ReportHandlerFunctionTable),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            ctypes.POINTER(ctypes.c_int),
        )
        self._dll.WacomGSS_ReportHandler_handleReport.restype = ctypes.c_int

    def get_usb_devices(self) -> list[STUUsbDevice]:
        count = ctypes.c_size_t()
        devices = ctypes.POINTER(_UsbDevice)()
        result = self._dll.WacomGSS_getUsbDevices(
            ctypes.sizeof(_UsbDevice), ctypes.byref(count), ctypes.byref(devices)
        )
        if result != 0:
            raise WacomSTUSDKError(f"getUsbDevices failed with code {result}")
        try:
            return [self._device_from_native(devices[index]) for index in range(count.value)]
        finally:
            if devices:
                self._dll.WacomGSS_free(devices)

    def get_tablet_info(self, device: STUUsbDevice) -> STUTabletInfo:
        interface = self._open_usb_interface(device)
        information = ctypes.POINTER(_Information)()
        capability = ctypes.POINTER(_Capability)()
        try:
            result = self._dll.WacomGSS_Protocol_getInformation(
                interface, ctypes.sizeof(_Information), ctypes.byref(information)
            )
            if result != 0:
                raise WacomSTUSDKError(
                    f"Protocol_getInformation failed with code {result}"
                )
            result = self._dll.WacomGSS_Protocol_getCapability(
                interface, ctypes.sizeof(_Capability), ctypes.byref(capability)
            )
            if result != 0:
                raise WacomSTUSDKError(f"Protocol_getCapability failed with code {result}")
            return self._tablet_info_from_native(information.contents, capability.contents)
        finally:
            if information:
                self._dll.WacomGSS_free(information)
            if capability:
                self._dll.WacomGSS_free(capability)
            self._dll.WacomGSS_Interface_disconnect(interface)
            self._dll.WacomGSS_Interface_free(interface)

    def capture_signature(
        self,
        device: STUUsbDevice | None = None,
        max_seconds: float = 60.0,
        quiet_seconds: float = 1.2,
        cancel_event: threading.Event | None = None,
    ) -> CapturedSignature:
        del quiet_seconds
        selected_device = device or self._first_stu_device()
        interface = self._open_usb_interface(selected_device)
        tablet = self._create_tablet(interface)
        queue = ctypes.c_void_p()
        try:
            result = self._dll.WacomGSS_Tablet_interfaceQueue(
                tablet, ctypes.byref(queue)
            )
            if result != 0:
                raise WacomSTUSDKError(
                    f"Tablet_interfaceQueue failed with code {result}"
                )
            info = self._read_attached_tablet_info(tablet)
            self._call("Tablet_setPenDataOptionMode", tablet, 0)
            layout = _tablet_layout(info)
            self._prepare_signature_screen(tablet, info, layout)
            return self._capture_signature_from_tablet_buttons(
                tablet, queue, info, layout, max_seconds, cancel_event
            )
        finally:
            self._dll.WacomGSS_Tablet_setInkingMode(tablet, 0)
            self._dll.WacomGSS_Tablet_setClearScreen(tablet)
            if queue:
                self._dll.WacomGSS_InterfaceQueue_free(queue)
            self._dll.WacomGSS_Tablet_disconnect(tablet)
            self._dll.WacomGSS_Tablet_free(tablet)

    def _prepare_signature_screen(
        self, tablet: ctypes.c_void_p, info: STUTabletInfo, layout: _TabletLayout
    ) -> None:
        self._call("Tablet_setInkingMode", tablet, 0)
        self._call("Tablet_setClearScreen", tablet)
        image = _signature_screen_image(info, layout)
        image_data = (ctypes.c_uint8 * len(image)).from_buffer_copy(image)
        result = self._dll.WacomGSS_Tablet_writeImage(
            tablet, 0, image_data, ctypes.c_size_t(len(image))
        )
        if result != 0:
            raise WacomSTUSDKError(f"Tablet_writeImage failed with code {result}")
        self._set_handwriting_display_area(tablet, layout.signature_area)
        self._call("Tablet_setInkingMode", tablet, 1)

    def _set_handwriting_display_area(
        self, tablet: ctypes.c_void_p, area: _ScreenRect
    ) -> None:
        rectangle = _Rectangle(area.left, area.top, area.right, area.bottom)
        result = self._dll.WacomGSS_Tablet_setHandwritingDisplayArea(
            tablet, ctypes.sizeof(_Rectangle), ctypes.byref(rectangle)
        )
        if result != 0:
            raise WacomSTUSDKError(
                f"Tablet_setHandwritingDisplayArea failed with code {result}"
            )

    def _capture_signature_from_tablet_buttons(
        self,
        tablet: ctypes.c_void_p,
        queue: ctypes.c_void_p,
        info: STUTabletInfo,
        layout: _TabletLayout,
        max_seconds: float,
        cancel_event: threading.Event | None,
    ) -> CapturedSignature:
        collector = _PenReportCollector()
        deadline = time.monotonic() + max_seconds
        strokes: list[list[STUPenPoint]] = []
        current_stroke: list[STUPenPoint] = []
        previous_touching = False
        processed_points = 0

        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise WacomSTUSDKError("Firma annullata")
            received_report = self._read_report_into_collector(queue, collector)
            if not received_report:
                time.sleep(0.01)
                continue

            for point in collector.points[processed_points:]:
                screen_x, screen_y = _pen_point_to_screen(point, info)
                touch_started = point.touching and not previous_touching
                previous_touching = point.touching

                if touch_started and layout.clear_button.contains(screen_x, screen_y):
                    strokes.clear()
                    current_stroke.clear()
                    self._prepare_signature_screen(tablet, info, layout)
                    continue

                if touch_started and layout.submit_button.contains(screen_x, screen_y):
                    if current_stroke:
                        strokes.append(current_stroke)
                        current_stroke = []
                    signed_strokes = [
                        stroke for stroke in strokes if _touching_count(stroke) >= 2
                    ]
                    if signed_strokes:
                        svg = _signature_svg_from_strokes(
                            signed_strokes, info, layout.signature_area
                        )
                        return CapturedSignature(
                            content=svg.encode("utf-8"),
                            media_type="image/svg+xml",
                        )
                    continue

                if point.touching and layout.signature_area.contains(screen_x, screen_y):
                    current_stroke.append(point)
                    continue

                if not point.touching and current_stroke:
                    if _touching_count(current_stroke) >= 2:
                        strokes.append(current_stroke)
                    current_stroke = []

            processed_points = len(collector.points)

        raise WacomSTUSDKError(
            "Firma non inviata dalla tavoletta entro il tempo disponibile "
            f"(report grezzi: {collector.raw_reports}, punti decodificati: "
            f"{len(collector.points)})"
        )

    def _first_stu_device(self) -> STUUsbDevice:
        for device in self.get_usb_devices():
            if device.is_wacom_stu:
                return device
        raise WacomSTUSDKError("Nessuna tavoletta Wacom STU collegata")

    def _read_tablet_info(self, interface: ctypes.c_void_p) -> STUTabletInfo:
        information = ctypes.POINTER(_Information)()
        capability = ctypes.POINTER(_Capability)()
        try:
            result = self._dll.WacomGSS_Protocol_getInformation(
                interface, ctypes.sizeof(_Information), ctypes.byref(information)
            )
            if result != 0:
                raise WacomSTUSDKError(
                    f"Protocol_getInformation failed with code {result}"
                )
            result = self._dll.WacomGSS_Protocol_getCapability(
                interface, ctypes.sizeof(_Capability), ctypes.byref(capability)
            )
            if result != 0:
                raise WacomSTUSDKError(f"Protocol_getCapability failed with code {result}")
            return self._tablet_info_from_native(information.contents, capability.contents)
        finally:
            if information:
                self._dll.WacomGSS_free(information)
            if capability:
                self._dll.WacomGSS_free(capability)

    def _read_attached_tablet_info(self, tablet: ctypes.c_void_p) -> STUTabletInfo:
        information = ctypes.POINTER(_Information)()
        capability = ctypes.POINTER(_Capability)()
        try:
            result = self._dll.WacomGSS_Tablet_getInformation(
                tablet, ctypes.sizeof(_Information), ctypes.byref(information)
            )
            if result != 0:
                raise WacomSTUSDKError(
                    f"Tablet_getInformation failed with code {result}"
                )
            result = self._dll.WacomGSS_Tablet_getCapability(
                tablet, ctypes.sizeof(_Capability), ctypes.byref(capability)
            )
            if result != 0:
                raise WacomSTUSDKError(f"Tablet_getCapability failed with code {result}")
            return self._tablet_info_from_native(information.contents, capability.contents)
        finally:
            if information:
                self._dll.WacomGSS_free(information)
            if capability:
                self._dll.WacomGSS_free(capability)

    def _create_tablet(self, interface: ctypes.c_void_p) -> ctypes.c_void_p:
        tablet = ctypes.c_void_p()
        result = self._dll.WacomGSS_Tablet_create_1(ctypes.byref(tablet))
        if result != 0:
            raise WacomSTUSDKError(f"Tablet_create_1 failed with code {result}")
        result = self._dll.WacomGSS_Tablet_attach(tablet, interface)
        if result != 0:
            self._dll.WacomGSS_Tablet_free(tablet)
            raise WacomSTUSDKError(f"Tablet_attach failed with code {result}")
        return tablet

    def _collect_pen_points(
        self, queue: ctypes.c_void_p, max_seconds: float, quiet_seconds: float
    ) -> tuple[list[STUPenPoint], int]:
        collector = _PenReportCollector()
        deadline = time.monotonic() + max_seconds
        last_touch = 0.0
        seen_touch = False
        while time.monotonic() < deadline:
            report = ctypes.POINTER(ctypes.c_uint8)()
            length = ctypes.c_size_t()
            received = ctypes.c_int()
            result = self._dll.WacomGSS_InterfaceQueue_try_getReport(
                queue, ctypes.byref(report), ctypes.byref(length), ctypes.byref(received)
            )
            if result != 0:
                raise WacomSTUSDKError(
                    f"InterfaceQueue_try_getReport failed with code {result}"
                )
            try:
                if received.value:
                    collector.raw_reports += 1
                    self._handle_report(collector, report, length.value)
                    if collector.points and collector.points[-1].touching:
                        seen_touch = True
                        last_touch = time.monotonic()
                elif seen_touch and time.monotonic() - last_touch >= quiet_seconds:
                    break
            finally:
                if report:
                    self._dll.WacomGSS_free(report)
            time.sleep(0.01)
        return collector.points, collector.raw_reports

    def _read_report_into_collector(
        self, queue: ctypes.c_void_p, collector: _PenReportCollector
    ) -> bool:
        report = ctypes.POINTER(ctypes.c_uint8)()
        length = ctypes.c_size_t()
        received = ctypes.c_int()
        result = self._dll.WacomGSS_InterfaceQueue_try_getReport(
            queue, ctypes.byref(report), ctypes.byref(length), ctypes.byref(received)
        )
        if result != 0:
            raise WacomSTUSDKError(
                f"InterfaceQueue_try_getReport failed with code {result}"
            )
        try:
            if received.value:
                collector.raw_reports += 1
                self._handle_report(collector, report, length.value)
                return True
            return False
        finally:
            if report:
                self._dll.WacomGSS_free(report)

    def _handle_report(
        self,
        collector: _PenReportCollector,
        report: ctypes.POINTER(ctypes.c_uint8),
        length: int,
    ) -> None:
        report_pointer = ctypes.POINTER(ctypes.c_uint8)()
        decoded = ctypes.c_int()
        result = self._dll.WacomGSS_ReportHandler_handleReport(
            ctypes.sizeof(_ReportHandlerFunctionTable),
            ctypes.byref(collector.table),
            None,
            report,
            length,
            ctypes.byref(report_pointer),
            ctypes.byref(decoded),
        )
        if result != 0:
            raise WacomSTUSDKError(f"ReportHandler_handleReport failed with code {result}")

    def _call(self, function_name: str, *args: object) -> None:
        function = getattr(self._dll, f"WacomGSS_{function_name}")
        result = function(*args)
        if result != 0:
            raise WacomSTUSDKError(f"{function_name} failed with code {result}")

    def _open_usb_interface(self, device: STUUsbDevice) -> ctypes.c_void_p:
        interface = ctypes.c_void_p()
        result = self._dll.WacomGSS_UsbInterface_create_2(
            device.file_name.encode("utf-8"),
            device.bulk_file_name.encode("utf-8") if device.bulk_file_name else None,
            1,
            ctypes.byref(interface),
        )
        if result != 0:
            raise WacomSTUSDKError(f"UsbInterface_create_2 failed with code {result}")
        return interface

    @staticmethod
    def _decode(value: bytes | None) -> str:
        if not value:
            return ""
        return value.decode("utf-8", errors="replace")

    @classmethod
    def _device_from_native(cls, device: _UsbDevice) -> STUUsbDevice:
        return STUUsbDevice(
            vendor_id=int(device.usbDevice.idVendor),
            product_id=int(device.usbDevice.idProduct),
            device_version=int(device.usbDevice.bcdDevice),
            file_name=cls._decode(device.fileName),
            bulk_file_name=cls._decode(device.bulkFileName),
        )

    @classmethod
    def _tablet_info_from_native(
        cls, information: _Information, capability: _Capability
    ) -> STUTabletInfo:
        return STUTabletInfo(
            model_name=cls._decode(information.modelNameNullTerminated),
            firmware_major=int(information.firmwareMajorVersion),
            firmware_minor=int(information.firmwareMinorVersion),
            tablet_max_x=int(capability.tabletMaxX),
            tablet_max_y=int(capability.tabletMaxY),
            tablet_max_pressure=int(capability.tabletMaxPressure),
            screen_width=int(capability.screenWidth),
            screen_height=int(capability.screenHeight),
            max_report_rate=int(capability.maxReportRate),
            resolution=int(capability.resolution),
            encoding_flag=int(capability.encodingFlag),
        )


def _signature_svg_from_pen_points(
    points: list[STUPenPoint], info: STUTabletInfo
) -> str:
    strokes: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    scale_x = 420.0 / max(1, info.tablet_max_x)
    scale_y = 180.0 / max(1, info.tablet_max_y)
    for point in points:
        if point.touching:
            current.append((point.x * scale_x, point.y * scale_y))
        elif current:
            if len(current) > 1:
                strokes.append(current)
            current = []
    if len(current) > 1:
        strokes.append(current)
    paths = "\n".join(_svg_polyline(stroke) for stroke in strokes)
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' "
        "width='420' height='180' viewBox='0 0 420 180'>"
        f"{paths}</svg>"
    )


def _signature_svg_from_strokes(
    strokes: list[list[STUPenPoint]], info: STUTabletInfo, area: _ScreenRect
) -> str:
    svg_strokes: list[list[tuple[float, float]]] = []
    width = max(1, area.right - area.left)
    height = max(1, area.bottom - area.top)
    for stroke in strokes:
        svg_stroke: list[tuple[float, float]] = []
        for point in stroke:
            if not point.touching:
                continue
            screen_x, screen_y = _pen_point_to_screen(point, info)
            x = max(0.0, min(420.0, (screen_x - area.left) * 420.0 / width))
            y = max(0.0, min(180.0, (screen_y - area.top) * 180.0 / height))
            svg_stroke.append((x, y))
        if len(svg_stroke) > 1:
            svg_strokes.append(svg_stroke)
    paths = "\n".join(_svg_polyline(stroke) for stroke in svg_strokes)
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' "
        "width='420' height='180' viewBox='0 0 420 180'>"
        f"{paths}</svg>"
    )


def _svg_polyline(points: list[tuple[float, float]]) -> str:
    encoded_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return (
        f"<polyline points='{encoded_points}' fill='none' "
        "stroke='black' stroke-width='3' stroke-linecap='round' "
        "stroke-linejoin='round'/>"
    )


def _tablet_layout(info: STUTabletInfo) -> _TabletLayout:
    width = max(1, info.screen_width)
    height = max(1, info.screen_height)
    margin = max(6, width // 40)
    button_top = max(height - 42, height * 3 // 4)
    button_bottom = height - margin
    gap = max(8, width // 32)
    button_width = (width - margin * 2 - gap) // 2
    return _TabletLayout(
        signature_area=_ScreenRect(margin, 26, width - margin, button_top - 10),
        clear_button=_ScreenRect(margin, button_top, margin + button_width, button_bottom),
        submit_button=_ScreenRect(
            width - margin - button_width, button_top, width - margin, button_bottom
        ),
    )


def _pen_point_to_screen(point: STUPenPoint, info: STUTabletInfo) -> tuple[float, float]:
    return (
        point.x * max(1, info.screen_width) / max(1, info.tablet_max_x),
        point.y * max(1, info.screen_height) / max(1, info.tablet_max_y),
    )


def _touching_count(points: list[STUPenPoint]) -> int:
    return sum(1 for point in points if point.touching)


def _signature_screen_image(info: STUTabletInfo, layout: _TabletLayout) -> bytes:
    width = max(1, info.screen_width)
    height = max(1, info.screen_height)
    pixels = [[False for _ in range(width)] for _ in range(height)]
    _draw_text(pixels, 12, 8, "FIRMA QUI - qSign", scale=2, uppercase=False)
    _draw_rect(pixels, layout.signature_area)
    _draw_button(pixels, layout.clear_button, "PULISCI")
    _draw_button(pixels, layout.submit_button, "INVIA")
    return _pack_monochrome(pixels, width, height)


def _draw_button(pixels: list[list[bool]], rect: _ScreenRect, label: str) -> None:
    _draw_rect(pixels, rect)
    _draw_rect(
        pixels,
        _ScreenRect(rect.left + 2, rect.top + 2, rect.right - 2, rect.bottom - 2),
    )
    scale = 2 if rect.right - rect.left >= 90 else 1
    text_width = len(label) * 6 * scale - scale
    text_height = 7 * scale
    x = rect.left + max(2, (rect.right - rect.left - text_width) // 2)
    y = rect.top + max(2, (rect.bottom - rect.top - text_height) // 2)
    _draw_text(pixels, x, y, label, scale)


def _draw_rect(pixels: list[list[bool]], rect: _ScreenRect) -> None:
    _draw_line(pixels, rect.left, rect.top, rect.right, rect.top)
    _draw_line(pixels, rect.left, rect.bottom, rect.right, rect.bottom)
    _draw_line(pixels, rect.left, rect.top, rect.left, rect.bottom)
    _draw_line(pixels, rect.right, rect.top, rect.right, rect.bottom)


def _draw_line(
    pixels: list[list[bool]], x1: int, y1: int, x2: int, y2: int
) -> None:
    if y1 == y2:
        for x in range(min(x1, x2), max(x1, x2) + 1):
            _set_pixel(pixels, x, y1)
        return
    if x1 == x2:
        for y in range(min(y1, y2), max(y1, y2) + 1):
            _set_pixel(pixels, x1, y)


def _draw_text(
    pixels: list[list[bool]],
    x: int,
    y: int,
    text: str,
    scale: int = 1,
    uppercase: bool = True,
) -> None:
    cursor = x
    for char in text.upper() if uppercase else text:
        glyph = _FONT_5X7.get(char, _FONT_5X7[" "])
        for row_index, row in enumerate(glyph):
            for col_index, value in enumerate(row):
                if value != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        _set_pixel(
                            pixels,
                            cursor + col_index * scale + dx,
                            y + row_index * scale + dy,
                        )
        cursor += 6 * scale


def _set_pixel(pixels: list[list[bool]], x: int, y: int) -> None:
    if 0 <= y < len(pixels) and 0 <= x < len(pixels[y]):
        pixels[y][x] = True


def _pack_monochrome(
    pixels: list[list[bool]], screen_width: int, screen_height: int
) -> bytes:
    aligned_width = (screen_width + 7) & ~7
    row_bytes = aligned_width // 8
    data = bytearray([0xFF] * row_bytes * screen_height)
    for y in range(screen_height):
        for x in range(screen_width):
            if pixels[y][x]:
                index = y * row_bytes + x // 8
                data[index] &= ~(1 << (7 - x % 8))
    return bytes(data)


_FONT_5X7 = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "01010", "01010", "00100"),
    "g": ("00000", "01110", "10001", "10001", "01111", "00001", "11110"),
    "i": ("00100", "00000", "01100", "00100", "00100", "00100", "01110"),
    "n": ("00000", "00000", "11110", "10001", "10001", "10001", "10001"),
    "q": ("00000", "01110", "10001", "10001", "01111", "00001", "00001"),
}
