"""Tests for QSign application composition helpers."""

import unittest
from types import SimpleNamespace

from app.main import _prepare_flet_window
from app.qsign_application import QSignApplication


class QSignApplicationTests(unittest.TestCase):
    def test_prepare_flet_window_sets_title_without_maximizing_early(self) -> None:
        page = FakePage()

        _prepare_flet_window(page)

        self.assertEqual(page.title, "qSign by Queen Srl - queensrl.net")
        self.assertFalse(page.window.maximized)

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

    def test_window_close_asks_confirmation_before_shutdown(self) -> None:
        page = FakePage()
        controller = FakeController()
        view = FakeView()
        app = QSignApplication()

        app._bind_shutdown(page, controller, view)
        page.window.on_event(SimpleNamespace(type="close"))

        self.assertTrue(page.window.prevent_close)
        self.assertEqual(controller.shutdown_count, 0)
        self.assertEqual(page.window.destroy_count, 0)
        self.assertIsNotNone(view.close_callback)

        view.close_callback()

        self.assertEqual(controller.shutdown_count, 1)
        self.assertEqual(page.destroy_task_count, 1)
        self.assertEqual(page.window.destroy_count, 1)

    def test_bind_shutdown_ignores_session_closed_during_window_destroy(self) -> None:
        page = FakePage()
        page.window.destroy_error = RuntimeError("Session closed")
        controller = FakeController()
        app = QSignApplication()

        app._bind_shutdown(page, controller)
        page.window.on_event(SimpleNamespace(type="close"))

        self.assertEqual(controller.shutdown_count, 1)
        self.assertEqual(page.destroy_task_count, 1)
        self.assertEqual(page.window.destroy_count, 1)

    def test_window_close_asks_confirmation_when_signed_document_is_unsaved(self) -> None:
        page = FakePage()
        controller = FakeController()
        controller.has_unsaved = True
        view = FakeView()
        app = QSignApplication()

        app._bind_shutdown(page, controller, view)
        page.window.on_event(SimpleNamespace(type="close"))

        self.assertEqual(controller.shutdown_count, 0)
        self.assertEqual(page.window.destroy_count, 0)
        self.assertIsNotNone(view.discard_callback)
        self.assertIsNone(view.close_callback)

        view.discard_callback()

        self.assertEqual(controller.shutdown_count, 1)
        self.assertEqual(page.window.destroy_count, 1)


class FakeWindow:
    def __init__(self) -> None:
        self.visible = True
        self.maximized = False
        self.prevent_close = False
        self.on_event = None
        self.destroy_count = 0
        self.destroy_error: Exception | None = None

    async def destroy(self) -> None:
        self.destroy_count += 1
        if self.destroy_error is not None:
            raise self.destroy_error


class FakePage:
    def __init__(self) -> None:
        self.window = FakeWindow()
        self.update_count = 0
        self.destroy_task_count = 0

    def update(self) -> None:
        self.update_count += 1

    def run_task(self, handler: object) -> None:
        self.destroy_task_count += 1
        coroutine = handler()
        try:
            coroutine.send(None)
        except StopIteration:
            return


class FakeController:
    def __init__(self) -> None:
        self.shutdown_count = 0
        self.has_unsaved = False

    def shutdown(self) -> None:
        self.shutdown_count += 1

    def has_unsaved_signed_document(self) -> bool:
        return self.has_unsaved


class FakeView:
    def __init__(self) -> None:
        self.discard_callback = None
        self.cancel_discard_callback = None
        self.close_callback = None
        self.cancel_close_callback = None

    def ask_discard_signed_document(self, on_confirm, on_cancel) -> None:
        self.discard_callback = on_confirm
        self.cancel_discard_callback = on_cancel

    def ask_close_application(self, on_confirm, on_cancel) -> None:
        self.close_callback = on_confirm
        self.cancel_close_callback = on_cancel


if __name__ == "__main__":
    unittest.main()
