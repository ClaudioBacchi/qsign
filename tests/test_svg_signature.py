"""Tests for SVG signature geometry normalization."""

import unittest

from services.signature.svg_signature import (
    fit_svg_signature_strokes,
    parse_svg_signature,
)


class SvgSignatureTests(unittest.TestCase):
    def test_signature_strokes_fit_target_without_non_uniform_distortion(self) -> None:
        geometry = parse_svg_signature(
            (
                b"<svg xmlns='http://www.w3.org/2000/svg' "
                b"width='420' height='180' viewBox='0 0 420 180'>"
                b"<polyline points='60,20 160,40'/>"
                b"<polyline points='200,100 360,160'/></svg>"
            )
        )

        strokes, scale = fit_svg_signature_strokes(
            geometry,
            target_width=120,
            target_height=50,
        )

        all_points = [point for stroke in strokes for point in stroke]
        width = max(x for x, _ in all_points) - min(x for x, _ in all_points)
        height = max(y for _, y in all_points) - min(y for _, y in all_points)
        self.assertAlmostEqual(scale, 50 / 152)
        self.assertLessEqual(width, 120)
        self.assertLessEqual(height, 50)
        self.assertAlmostEqual(width / height, 300 / 140)

    def test_signature_parser_accepts_double_quoted_points(self) -> None:
        geometry = parse_svg_signature(
            b'<svg viewBox="0 0 500 200"><polyline points="1,2 3,4"/></svg>'
        )

        self.assertEqual(geometry.viewbox_width, 500)
        self.assertEqual(geometry.viewbox_height, 200)
        self.assertEqual(geometry.strokes, (((1.0, 2.0), (3.0, 4.0)),))


if __name__ == "__main__":
    unittest.main()

