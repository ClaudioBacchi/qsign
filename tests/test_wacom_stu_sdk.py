import unittest

from services.wacom.stu_sdk import (
    STUPenPoint,
    STUTabletInfo,
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

    def _point_at_screen(self, x: int, y: int) -> STUPenPoint:
        return STUPenPoint(
            round(x * self.info.tablet_max_x / self.info.screen_width),
            round(y * self.info.tablet_max_y / self.info.screen_height),
            600,
            True,
        )


if __name__ == "__main__":
    unittest.main()
