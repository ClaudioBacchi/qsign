"""Tests for QSign application composition helpers."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from app.services.certificate_service import CertificateInfo
from app.services.general_preferences_service import SupabaseSettings
from app.main import (
    _prepare_flet_runtime_metadata,
    _prepare_flet_window,
    _prepare_qsign_flet_runtime,
    _read_app_version,
    _safe_runtime_directory_name,
    _version_tuple,
    _windows_icon_resources,
    _windows_version_resource,
)
from app.qsign_application import QSignApplication


class QSignApplicationTests(unittest.TestCase):
    def test_prepare_flet_window_sets_title_without_maximizing_early(self) -> None:
        page = FakePage()

        _prepare_flet_window(page)

        self.assertEqual(page.title, "qSign by Queen Srl - queensrl.net")
        self.assertFalse(page.window.maximized)

    def test_read_app_version_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "app.yaml").write_text(
                'name: QSign\nversion: "1.2.3"\n',
                encoding="utf-8",
            )

            self.assertEqual(_read_app_version(root), "1.2.3")

    def test_windows_version_resource_contains_qsign_metadata(self) -> None:
        resource = _windows_version_resource(
            version="1.2.3",
            strings={
                "FileDescription": "QSign",
                "ProductName": "QSign",
                "CompanyName": "Queen Srl",
            },
        )

        self.assertEqual(_version_tuple("1.2.3"), (1, 2, 3, 0))
        self.assertIn("QSign".encode("utf-16le"), resource)
        self.assertIn("Queen Srl".encode("utf-16le"), resource)

    def test_windows_icon_resources_convert_ico_to_executable_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            icon_path = Path(temp_dir) / "icon.ico"
            image = b"\x89PNG\r\n\x1a\nfake"
            icon_path.write_bytes(
                b"\x00\x00\x01\x00\x01\x00"
                + b"\x10\x10\x00\x00\x01\x00 \x00"
                + len(image).to_bytes(4, "little")
                + (22).to_bytes(4, "little")
                + image
            )

            resources = _windows_icon_resources(icon_path)

        self.assertIsNotNone(resources)
        assert resources is not None
        self.assertEqual(resources.icons, [(1, image)])
        self.assertEqual(resources.group[:6], b"\x00\x00\x01\x00\x01\x00")
        self.assertEqual(resources.group[-2:], b"\x01\x00")

    @unittest.skipUnless(sys.platform == "win32", "Windows-only Flet runtime setup")
    def test_prepare_flet_runtime_metadata_sets_qsign_flet_view_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "cache"
            flet_dir = cache_dir / "flet"
            flet_dir.mkdir(parents=True)
            flet_exe = flet_dir / "flet.exe"
            flet_exe.write_bytes(b"fake exe")
            icon_path = Path(temp_dir) / "resources" / "icons" / "favicon.ico"
            icon_path.parent.mkdir(parents=True)
            icon_path.write_bytes(b"icon")
            fake_flet_desktop = Mock()
            fake_flet_desktop.ensure_client_cached.return_value = str(cache_dir)

            with (
                patch.dict(sys.modules, {"flet_desktop": fake_flet_desktop}),
                patch.dict(os.environ, {}, clear=False),
                patch(
                    "app.main._qsign_flet_runtime_root",
                    return_value=Path(temp_dir) / "qsign-runtime",
                ),
                patch("app.main._set_windows_executable_metadata") as set_metadata,
            ):
                _prepare_flet_runtime_metadata(Path(temp_dir))
                runtime_name = _safe_runtime_directory_name(cache_dir.name, "0.0.0")
                expected_flet_dir = (
                    Path(temp_dir) / "qsign-runtime" / runtime_name / "flet"
                )
                self.assertEqual(os.environ["FLET_VIEW_PATH"], str(expected_flet_dir))

        set_metadata.assert_called_once_with(
            expected_flet_dir / "flet.exe", version="0.0.0", icon_path=icon_path
        )

    def test_prepare_qsign_flet_runtime_copies_source_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_flet_dir = root / "flet-cache" / "flet"
            source_flet_dir.mkdir(parents=True)
            (source_flet_dir / "flet.exe").write_bytes(b"runtime")
            (source_flet_dir / "icudtl.dat").write_bytes(b"data")

            with patch(
                "app.main._qsign_flet_runtime_root",
                return_value=root / "qsign-runtime",
            ):
                copied_flet_dir = _prepare_qsign_flet_runtime(
                    root / "flet-cache", "1.2.3"
                )
                copied_again = _prepare_qsign_flet_runtime(
                    root / "flet-cache", "1.2.3"
                )

            self.assertEqual(copied_flet_dir, copied_again)
            self.assertNotEqual(copied_flet_dir, source_flet_dir)
            self.assertEqual((copied_flet_dir / "flet.exe").read_bytes(), b"runtime")
            self.assertEqual((copied_flet_dir / "icudtl.dat").read_bytes(), b"data")

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

    def test_verify_signature_setup_reports_mouse_certificate_ready(self) -> None:
        app = QSignApplication()
        certificate_service = FakeCertificateService()

        result = app._verify_signature_setup(
            certificate_service,
            None,
            SupabaseSettings(signature_capture_mode="mouse"),
        )

        self.assertEqual(
            result,
            "Verifica firma OK - OK certificato: Mario Rossi | "
            "Wacom: non richiesto, metodo firma Mouse",
        )

    def test_verify_signature_setup_reports_wacom_failure(self) -> None:
        app = QSignApplication()

        result = app._verify_signature_setup(
            FakeCertificateService(),
            FakeWacomProvider(error=RuntimeError("Nessuna tavoletta")),
            SupabaseSettings(signature_capture_mode="wacom"),
        )

        self.assertEqual(
            result,
            "Verifica firma: OK certificato: Mario Rossi | "
            "Wacom: non disponibile (Nessuna tavoletta)",
        )


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


class FakeCertificateService:
    def get_active_certificate(self) -> CertificateInfo:
        return CertificateInfo(
            name="Mario Rossi",
            type="Store Windows - chiave privata",
            valid_until="2029-01-01",
            thumbprint="AABB",
        )


class FakeWacomProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.disconnect_count = 0

    def connect(self) -> None:
        if self.error is not None:
            raise self.error

    def disconnect(self) -> None:
        self.disconnect_count += 1

    def capture_signature(self) -> object:
        raise AssertionError("diagnostic must not capture signatures")


if __name__ == "__main__":
    unittest.main()
