"""Presentation tests for renderer output conversion."""

import base64
import unittest

from ui.main_view import MainView


class FakePage:
    def __init__(self) -> None:
        self.controls: list[object] = []
        self.services: list[object] = []
        self.updated = False

    def add(self, *controls: object) -> None:
        self.controls.extend(controls)

    def update(self) -> None:
        self.updated = True

    def show_dialog(self, control: object) -> None:
        self.dialog = control


class MainViewTests(unittest.TestCase):
    def test_png_is_sent_to_flet_as_an_explicit_data_uri(self) -> None:
        page = FakePage()
        view = MainView(page)
        png = b"\x89PNG\r\n\x1a\nsample"

        view.display_document(
            filename="sample.pdf",
            image_content=png,
            image_width=595,
            image_height=842,
            page_number=1,
            page_count=2,
            zoom=1.0,
        )

        image = view._pdf_image
        expected = base64.b64encode(png).decode("ascii")
        self.assertEqual(image.src, f"data:image/png;base64,{expected}")
        self.assertEqual(image.width, 595)
        self.assertEqual(image.height, 842)
        self.assertTrue(image.visible)
        self.assertFalse(view._viewer_placeholder.visible)
        self.assertTrue(page.updated)

    def test_information_uses_the_current_flet_dialog_api(self) -> None:
        page = FakePage()
        view = MainView(page)

        view.show_information()

        self.assertEqual(page.dialog.title.value, "QSign")


if __name__ == "__main__":
    unittest.main()
