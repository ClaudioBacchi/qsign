import ctypes
import unittest
from unittest.mock import MagicMock

from services.wacom.stu_sdk import (
    STUPenPoint,
    STUTabletInfo,
    STUUsbDevice,
    WacomSTUSDK,
    WacomSTUSDKError,
    _draw_text,
    _pack_monochrome,
    _pen_point_to_screen,
    _signature_screen_image,
    _signature_svg_from_strokes,
    _tablet_layout,
)


class WacomSTUSDKHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.info = STUTabletInfo(
            model_name="STU-430",
            firmware_major=1,
            firmware_minor=2,
            tablet_max_x=9600,
            tablet_max_y=6000,
            tablet_max_pressure=1023,
            screen_width=320,
            screen_height=200,
            max_report_rate=200,
            resolution=2540,
            encoding_flag=0,
        )

    def test_tablet_layout_contains_signature_area_and_buttons(self) -> None:
        layout = _tablet_layout(self.info)

        self.assertTrue(layout.signature_area.contains(160, 80))
        self.assertTrue(layout.clear_button.contains(40, 175))
        self.assertTrue(layout.submit_button.contains(280, 175))
        self.assertFalse(layout.signature_area.contains(280, 175))

    def test_signature_screen_image_is_packed_as_monochrome(self) -> None:
        image = _signature_screen_image(self.info, _tablet_layout(self.info))

        self.assertEqual(len(image), 320 // 8 * 200)
        self.assertLess(min(image), 0xFF)

    def test_draw_text_can_preserve_qsign_mixed_case(self) -> None:
        pixels = [[False for _ in range(120)] for _ in range(12)]

        _draw_text(pixels, 0, 0, "FIRMA QUI - qSign", uppercase=False)

        self.assertTrue(any(pixels[3][x] for x in range(60, 65)))
        self.assertTrue(any(pixels[y][72] for y in range(1, 7)))

    def test_pack_monochrome_clears_black_pixels_msb_first(self) -> None:
        pixels = [[False for _ in range(8)]]
        pixels[0][0] = True
        pixels[0][7] = True

        self.assertEqual(_pack_monochrome(pixels, 8, 1), bytes([0x7E]))

    def test_signature_svg_is_normalized_to_signature_area(self) -> None:
        layout = _tablet_layout(self.info)
        first = self._point_at_screen(
            layout.signature_area.left, layout.signature_area.top
        )
        second = self._point_at_screen(
            layout.signature_area.right, layout.signature_area.bottom
        )

        svg = _signature_svg_from_strokes([[first, second]], self.info, layout.signature_area)

        self.assertIn("0.0,0.0", svg)
        self.assertIn("420.0,180.0", svg)

    def test_pen_point_to_screen_uses_tablet_capability_ratio(self) -> None:
        x, y = _pen_point_to_screen(STUPenPoint(4800, 3000, 1, True), self.info)

        self.assertEqual((x, y), (160, 100))

    def test_capture_signature_does_not_double_release_attached_usb_interface(self) -> None:
        sdk = WacomSTUSDK.__new__(WacomSTUSDK)
        sdk._dll = MagicMock()
        interface = ctypes.c_void_p(11)
        tablet = ctypes.c_void_p(22)
        device = STUUsbDevice(
            vendor_id=0x056A,
            product_id=0x00A4,
            device_version=1,
            file_name="usb",
            bulk_file_name="bulk",
        )
        sdk._first_stu_device = MagicMock(return_value=device)
        sdk._open_usb_interface = MagicMock(return_value=interface)
        sdk._create_tablet = MagicMock(return_value=tablet)
        sdk._read_attached_tablet_info = MagicMock(return_value=self.info)
        sdk._call = MagicMock()
        sdk._prepare_signature_screen = MagicMock()
        sdk._capture_signature_from_tablet_buttons = MagicMock(
            side_effect=WacomSTUSDKError("Firma annullata")
        )
        sdk._dll.WacomGSS_Tablet_interfaceQueue.return_value = 0

        with self.assertRaises(WacomSTUSDKError):
            sdk.capture_signature()

        sdk._dll.WacomGSS_Tablet_disconnect.assert_called_once_with(tablet)
        sdk._dll.WacomGSS_Tablet_free.assert_called_once_with(tablet)
        sdk._dll.WacomGSS_Interface_disconnect.assert_not_called()
        sdk._dll.WacomGSS_Interface_free.assert_not_called()

    def test_capture_signature_releases_usb_interface_when_tablet_create_fails(
        self,
    ) -> None:
        sdk = WacomSTUSDK.__new__(WacomSTUSDK)
        sdk._dll = MagicMock()
        interface = ctypes.c_void_p(11)
        device = STUUsbDevice(
            vendor_id=0x056A,
            product_id=0x00A4,
            device_version=1,
            file_name="usb",
            bulk_file_name="bulk",
        )
        sdk._first_stu_device = MagicMock(return_value=device)
        sdk._open_usb_interface = MagicMock(return_value=interface)
        sdk._create_tablet = MagicMock(
            side_effect=WacomSTUSDKError("Tablet_attach failed with code 7")
        )

        with self.assertRaises(WacomSTUSDKError):
            sdk.capture_signature()

        sdk._dll.WacomGSS_Interface_disconnect.assert_called_once_with(interface)
        sdk._dll.WacomGSS_Interface_free.assert_called_once_with(interface)

    def _point_at_screen(self, x: int, y: int) -> STUPenPoint:
        return STUPenPoint(
            round(x * self.info.tablet_max_x / self.info.screen_width),
            round(y * self.info.tablet_max_y / self.info.screen_height),
            600,
            True,
        )


if __name__ == "__main__":
    unittest.main()
