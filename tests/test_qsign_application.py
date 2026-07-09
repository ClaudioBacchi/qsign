"""Tests for QSign application composition helpers."""

import unittest

from app.qsign_application import QSignApplication


class QSignApplicationTests(unittest.TestCase):
    def test_set_window_visible_updates_flet_window_when_available(self) -> None:
        page = FakePage()

        QSignApplication._set_window_visible(page, False)

        self.assertFalse(page.window.visible)
        self.assertEqual(page.update_count, 1)

        QSignApplication._set_window_visible(page, True)

        self.assertTrue(page.window.visible)
        self.assertEqual(page.update_count, 2)

    def test_set_window_visible_ignores_pages_without_window(self) -> None:
        page = object()

        QSignApplication._set_window_visible(page, False)


class FakeWindow:
    def __init__(self) -> None:
        self.visible = True


class FakePage:
    def __init__(self) -> None:
        self.window = FakeWindow()
        self.update_count = 0

    def update(self) -> None:
        self.update_count += 1


if __name__ == "__main__":
    unittest.main()
