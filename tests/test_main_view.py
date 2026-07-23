"""Presentation tests for renderer output conversion."""

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pymupdf

from app.services.certificate_service import CertificateInfo, SignatureMetadata
from app.services.general_preferences_service import (
    ErpDocument,
    ErpDocumentsResult,
    ErpUser,
    ErpUserSettings,
    ErpUsersResult,
    SupabaseConnectionResult,
    SupabaseSettings,
    SupabaseTableResult,
)
from models.document import Rectangle
from ui.main_view import MainView

VALID_PDF_BYTES = b""


class FakePage:
    def __init__(self) -> None:
        self.controls: list[object] = []
        self.services: list[object] = []
        self.updated = False
        self.launched_urls: list[str] = []
        self.pop_count = 0
        self.window = SimpleNamespace(maximized=False)

    def add(self, *controls: object) -> None:
        self.controls.extend(controls)

    def update(self) -> None:
        self.updated = True

    def show_dialog(self, control: object) -> None:
        self.dialog = control

    def pop_dialog(self) -> None:
        self.dialog_popped = True
        self.pop_count += 1

    def close_dialog(self) -> None:
        self.dialog_closed = True

    def launch_url(self, url: str) -> None:
        self.launched_urls.append(url)


class FakeAsyncLaunchPage(FakePage):
    def run_task(self, handler: object, *args: object, **kwargs: object) -> None:
        asyncio.run(handler(*args, **kwargs))

    async def launch_url(self, url: str) -> None:
        self.launched_urls.append(url)


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
        self.assertTrue(view._pdf_stack.visible)
        self.assertFalse(view._viewer_placeholder.visible)
        self.assertFalse(view._home_view.visible)
        self.assertTrue(view._document_viewer.visible)
        self.assertTrue(page.updated)

    def test_initial_placeholder_is_clickable_qsign_logo(self) -> None:
        page = FakePage()
        view = MainView(page)

        self.assertTrue(view._viewer_placeholder.visible)
        self.assertTrue(view._home_view.visible)
        self.assertFalse(view._document_viewer.visible)
        self.assertEqual(
            view._viewer_placeholder.content.content.src,
            view._image_data_uri("images/logo_qsign_grande.png"),
        )

        view._viewer_placeholder.on_tap(None)

        self.assertEqual(page.launched_urls, ["https://queensrl.net"])

    def test_erp_documents_are_not_loaded_without_documents_url(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(selected_user_id="20")
        view = MainView(page, general_preferences_service=service)

        shown = view.refresh_erp_documents()

        self.assertFalse(shown)
        self.assertEqual(service.erp_document_fetch_count, 0)
        self.assertIs(view._home_view.content, view._viewer_placeholder)
        self.assertTrue(view._viewer_placeholder.visible)

    def test_erp_documents_are_not_loaded_without_selected_erp_user(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
        )
        view = MainView(page, general_preferences_service=service)

        shown = view.refresh_erp_documents()

        self.assertFalse(shown)
        self.assertEqual(service.erp_document_fetch_count, 0)
        self.assertIs(view._home_view.content, view._viewer_placeholder)

    def test_erp_documents_are_not_loaded_when_document_list_is_disabled(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.settings = SupabaseSettings(list_erp_documents=False)
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="20",
        )
        service.erp_documents_result = ErpDocumentsResult(
            True,
            "Caricati 1 documenti",
            (ErpDocument("NOME_DOCUMENTO.pdf", "2026-07-16 09:29:57"),),
        )
        view = MainView(page, general_preferences_service=service)

        shown = view.refresh_erp_documents()

        self.assertFalse(shown)
        self.assertEqual(service.erp_document_fetch_count, 0)
        self.assertIs(view._home_view.content, view._viewer_placeholder)
        self.assertTrue(view._viewer_placeholder.visible)

    def test_erp_documents_grid_shows_safe_columns_only(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            basic_username="api-user",
            basic_password="api-secret",
            selected_user_id="20",
            selected_user_name="Romani",
        )
        service.erp_documents_result = ErpDocumentsResult(
            True,
            "Caricati 1 documenti",
            (ErpDocument("NOME_DOCUMENTO.pdf", "2026-07-16 09:29:57", "Visita"),),
        )
        view = MainView(page, general_preferences_service=service)

        shown = view.refresh_erp_documents()

        self.assertTrue(shown)
        self.assertEqual(service.erp_document_fetch_settings.selected_user_id, "20")
        content_column = view._home_view.content.content
        self.assertEqual(content_column.controls[0].controls[0].value, "Documenti ERP da firmare")
        table = content_column.controls[1].content.controls[0]
        self.assertTrue(content_column.controls[1].expand)
        self.assertTrue(content_column.controls[1].content.expand)
        self.assertEqual(
            [column.label.value for column in table.columns],
            ["Nome documento", "Data"],
        )
        self.assertEqual(table.rows[0].cells[0].content.value, "NOME_DOCUMENTO.pdf")
        self.assertEqual(table.rows[0].cells[1].content.value, "2026-07-16 09:29:57")
        self.assertEqual(len(table.rows[0].cells), 2)

    def test_erp_documents_refresh_runs_in_background_and_keeps_local_open_available(self) -> None:
        page = FakePage()
        service = _erp_download_service()
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view.build()
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        shown = view.refresh_erp_documents()

        self.assertTrue(shown)
        self.assertEqual(service.erp_document_fetch_count, 0)
        self.assertEqual(len(background_jobs), 1)
        self.assertEqual(_erp_documents_body(view).value, "Caricamento documenti...")
        self.assertTrue(_erp_refresh_button(view).disabled)
        toolbar = page.controls[0].controls[0].content
        self.assertEqual(toolbar.controls[1].controls[0].tooltip, "Apri")

        background_jobs[0]()
        self.assertEqual(service.erp_document_fetch_count, 1)
        ui_jobs[0]()

        self.assertFalse(_erp_refresh_button(view).disabled)

    def test_erp_documents_refresh_coalesces_pending_requests(self) -> None:
        page = FakePage()
        service = _erp_download_service()
        service.erp_document_results = [
            ErpDocumentsResult(
                True,
                "Caricati 1 documenti",
                (ErpDocument("OLD.pdf", "", "", "DOC-OLD", "AUTH-OLD", "20"),),
            ),
            ErpDocumentsResult(
                True,
                "Nessun documento da firmare",
                (),
            ),
        ]
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        view.refresh_erp_documents()
        view.refresh_erp_documents()
        view.refresh_erp_documents()

        self.assertEqual(len(background_jobs), 1)
        background_jobs[0]()
        ui_jobs[0]()

        self.assertEqual(len(background_jobs), 2)
        background_jobs[1]()
        ui_jobs[1]()

        self.assertEqual(service.erp_document_fetch_count, 2)
        self.assertEqual(_erp_documents_body(view).value, "Nessun documento da firmare")

    def test_erp_documents_result_after_shutdown_is_ignored(self) -> None:
        page = FakePage()
        service = _erp_download_service()
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        view.refresh_erp_documents()
        page.updated = False
        view.stop_background_tasks()
        background_jobs[0]()
        ui_jobs[0]()

        self.assertFalse(page.updated)

    def test_erp_documents_result_for_old_user_starts_one_current_refresh(self) -> None:
        page = FakePage()
        service = _erp_download_service()
        service.erp_document_results = [
            ErpDocumentsResult(
                True,
                "Caricati 1 documenti",
                (ErpDocument("A.pdf", "", "", "DOC-A", "AUTH-A", "20"),),
            ),
            ErpDocumentsResult(
                True,
                "Caricati 1 documenti",
                (ErpDocument("B.pdf", "", "", "DOC-B", "AUTH-B", "21"),),
            ),
        ]
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        view.refresh_erp_documents()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            document_service_url="https://erp.example.test/soap",
            company_id="SALAV",
            basic_username="api-user",
            basic_password="api-secret",
            selected_user_id="21",
            selected_user_name="Bianchi",
        )
        background_jobs[0]()
        ui_jobs[0]()

        self.assertEqual(len(background_jobs), 2)
        background_jobs[1]()
        ui_jobs[1]()
        table = view._home_view.content.content.controls[1].content.controls[0]
        self.assertEqual(table.rows[0].cells[0].content.value, "B.pdf")

    def test_erp_documents_error_restores_retry_button(self) -> None:
        page = FakePage()
        service = _erp_download_service()
        service.erp_documents_result = ErpDocumentsResult(
            False,
            "Connessione ERP documenti fallita",
            (),
        )
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        view.refresh_erp_documents()
        background_jobs[0]()
        ui_jobs[0]()

        error_column = view._home_view.content.content
        self.assertEqual(error_column.controls[1].value, "Connessione ERP documenti fallita")
        self.assertEqual(_button_label(error_column.controls[2]), "Riprova")

    def test_erp_document_open_downloads_temp_pdf_and_opens_viewer(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            document_service_url="https://erp.example.test/soap",
            company_id="SALAV",
            basic_username="api-user",
            basic_password="api-secret",
            selected_user_id="20",
            selected_user_name="Romani",
        )
        service.erp_documents_result = ErpDocumentsResult(
            True,
            "Caricati 1 documenti",
            (
                ErpDocument(
                    "../NOME_DOCUMENTO.pdf",
                    "2026-07-16 09:29:57",
                    "",
                    "DOC-1",
                    "AUTH-1",
                    "20",
                ),
            ),
        )
        client = FakeInfinityDmsClient(_valid_pdf_bytes())
        opened_paths: list[str] = []
        view = MainView(
            page,
            general_preferences_service=service,
            infinity_dms_client=client,
        )
        view.bind_actions(
            on_open_document=lambda path: opened_paths.append(path),
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )
        view.run_background_task = lambda callback: callback()
        view.run_ui_task = lambda callback: callback()

        view.refresh_erp_documents()
        table = view._home_view.content.content.controls[1].content.controls[0]
        _find_button(table, "Apri").on_click(None)

        self.assertEqual(len(opened_paths), 1)
        self.assertIsNone(view._home_view.content.content.controls[2].color)
        opened_path = Path(opened_paths[0])
        self.assertTrue(opened_path.is_file())
        self.assertEqual(opened_path.read_bytes(), _valid_pdf_bytes())
        self.assertNotIn("..", opened_path.name)
        self.assertEqual(client.calls[0]["document_id"], "DOC-1")
        self.assertEqual(client.calls[0]["auth_code"], "AUTH-1")
        view.stop_background_tasks()
        self.assertFalse(opened_path.exists())

    def test_erp_temp_files_are_isolated_by_view_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = MainView(
                FakePage(),
                erp_temp_base_directory=base,
                erp_temp_session_id="session-a",
            )
            second = MainView(
                FakePage(),
                erp_temp_base_directory=base,
                erp_temp_session_id="session-b",
            )

            first_path = first._save_erp_temp_pdf("same.pdf", b"%PDF-a")
            second_path = second._save_erp_temp_pdf("same.pdf", b"%PDF-b")

            self.assertNotEqual(first_path.parent, second_path.parent)
            self.assertEqual(first_path.parent.name, "session-a")
            self.assertEqual(second_path.parent.name, "session-b")
            self.assertTrue(first_path.exists())
            self.assertTrue(second_path.exists())

    def test_erp_temp_cleanup_does_not_delete_other_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = MainView(
                FakePage(),
                erp_temp_base_directory=base,
                erp_temp_session_id="session-a",
            )
            second = MainView(
                FakePage(),
                erp_temp_base_directory=base,
                erp_temp_session_id="session-b",
            )
            first_path = first._save_erp_temp_pdf("same.pdf", b"%PDF-a")
            second_path = second._save_erp_temp_pdf("same.pdf", b"%PDF-b")

            first.stop_background_tasks()

            self.assertFalse(first_path.exists())
            self.assertTrue(second_path.exists())

            second.stop_background_tasks()

            self.assertFalse(second_path.exists())

    def test_erp_temp_cleanup_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            view = MainView(
                FakePage(),
                erp_temp_base_directory=Path(directory),
                erp_temp_session_id="session-a",
            )
            path = view._save_erp_temp_pdf("same.pdf", b"%PDF-a")

            view.stop_background_tasks()
            view.stop_background_tasks()

            self.assertFalse(path.exists())

    def test_erp_document_open_error_keeps_grid_and_local_open_available(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            document_service_url="https://erp.example.test/soap",
            company_id="SALAV",
            basic_username="api-user",
            basic_password="api-secret",
            selected_user_id="20",
        )
        service.erp_documents_result = ErpDocumentsResult(
            True,
            "Caricati 1 documenti",
            (ErpDocument("NOME.pdf", "", "", "DOC-1", "AUTH-1", "20"),),
        )
        client = FakeInfinityDmsClient(error=RuntimeError("boom"))
        opened_paths: list[str] = []
        view = MainView(
            page,
            general_preferences_service=service,
            infinity_dms_client=client,
        )
        view.bind_actions(
            on_open_document=lambda path: opened_paths.append(path),
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )
        view.run_background_task = lambda callback: callback()
        view.run_ui_task = lambda callback: callback()
        view.build()

        view.refresh_erp_documents()
        toolbar = page.controls[0].controls[0].content
        table = view._home_view.content.content.controls[1].content.controls[0]
        _find_button(table, "Apri").on_click(None)

        self.assertEqual(opened_paths, [])
        self.assertTrue(view._home_view.visible)
        self.assertEqual(
            view._home_view.content.content.controls[2].value,
            "Download documento ERP fallito",
        )
        self.assertEqual(toolbar.controls[1].controls[0].tooltip, "Apri")
        self.assertEqual(view._erp_temp_files, set())

    def test_erp_document_double_click_starts_single_download(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            document_service_url="https://erp.example.test/soap",
            company_id="SALAV",
            basic_username="api-user",
            basic_password="api-secret",
            selected_user_id="20",
        )
        service.erp_documents_result = ErpDocumentsResult(
            True,
            "Caricati 1 documenti",
            (ErpDocument("NOME.pdf", "", "", "DOC-1", "AUTH-1", "20"),),
        )
        client = FakeInfinityDmsClient(_valid_pdf_bytes())
        background_jobs = []
        view = MainView(
            page,
            general_preferences_service=service,
            infinity_dms_client=client,
        )
        view.bind_actions(
            on_open_document=lambda _: None,
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )
        view.run_background_task = lambda callback: callback()
        view.run_ui_task = lambda callback: callback()

        view.refresh_erp_documents()
        view.run_background_task = lambda callback: background_jobs.append(callback)
        table = view._home_view.content.content.controls[1].content.controls[0]
        open_button = _find_button(table, "Apri")
        open_button.on_click(None)
        open_button.on_click(None)

        self.assertEqual(
            view._home_view.content.content.controls[2].color,
            view._ft.Colors.RED_700,
        )
        self.assertEqual(len(background_jobs), 1)
        self.assertEqual(client.calls, [])

    def test_erp_download_started_after_shutdown_does_not_call_transport_or_ui(self) -> None:
        page = FakePage()
        service = _erp_download_service()
        client = FakeInfinityDmsClient(_valid_pdf_bytes())
        background_jobs: list[object] = []
        opened_paths: list[str] = []
        view = MainView(
            page,
            general_preferences_service=service,
            infinity_dms_client=client,
        )
        view.bind_actions(
            on_open_document=lambda path: opened_paths.append(path),
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )
        view.run_background_task = lambda callback: callback()
        view.run_ui_task = lambda callback: callback()

        view.refresh_erp_documents()
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: self.fail("UI task should not be queued")

        table = view._home_view.content.content.controls[1].content.controls[0]
        _find_button(table, "Apri").on_click(None)
        view.stop_background_tasks()
        page.updated = False
        background_jobs[0]()

        self.assertEqual(client.calls, [])
        self.assertEqual(opened_paths, [])
        self.assertFalse(page.updated)
        self.assertTrue(view._erp_download_lock.acquire(blocking=False))
        view._erp_download_lock.release()

    def test_erp_download_queued_callback_after_shutdown_does_not_open_pdf(self) -> None:
        page = FakePage()
        service = _erp_download_service()
        client = FakeInfinityDmsClient(_valid_pdf_bytes())
        ui_jobs: list[object] = []
        opened_paths: list[str] = []
        view = MainView(
            page,
            general_preferences_service=service,
            infinity_dms_client=client,
        )
        view.bind_actions(
            on_open_document=lambda path: opened_paths.append(path),
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )
        view.run_background_task = lambda callback: callback()
        view.run_ui_task = lambda callback: callback()

        view.refresh_erp_documents()
        view.run_ui_task = lambda callback: ui_jobs.append(callback)
        table = view._home_view.content.content.controls[1].content.controls[0]
        _find_button(table, "Apri").on_click(None)
        temp_path = next(iter(view._erp_temp_files))
        view.stop_background_tasks()
        page.updated = False
        ui_jobs[0]()

        self.assertFalse(temp_path.exists())
        self.assertEqual(opened_paths, [])
        self.assertFalse(page.updated)
        self.assertTrue(view._erp_download_lock.acquire(blocking=False))
        view._erp_download_lock.release()

    def test_erp_download_user_change_before_callback_discards_download(self) -> None:
        page = FakePage()
        service = _erp_download_service()
        client = FakeInfinityDmsClient(_valid_pdf_bytes())
        ui_jobs: list[object] = []
        opened_paths: list[str] = []
        view = MainView(
            page,
            general_preferences_service=service,
            infinity_dms_client=client,
        )
        view.bind_actions(
            on_open_document=lambda path: opened_paths.append(path),
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )
        view.run_background_task = lambda callback: callback()
        view.run_ui_task = lambda callback: callback()

        view.refresh_erp_documents()
        view.run_ui_task = lambda callback: ui_jobs.append(callback)
        table = view._home_view.content.content.controls[1].content.controls[0]
        _find_button(table, "Apri").on_click(None)
        temp_path = next(iter(view._erp_temp_files))
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            document_service_url="https://erp.example.test/soap",
            company_id="SALAV",
            basic_username="api-user",
            basic_password="api-secret",
            selected_user_id="21",
            selected_user_name="Bianchi",
        )
        ui_jobs[0]()

        self.assertFalse(temp_path.exists())
        self.assertEqual(opened_paths, [])
        self.assertEqual(
            view._home_view.content.content.controls[2].value,
            "Utente ERP cambiato: seleziona nuovamente il documento",
        )

    def test_erp_download_same_user_before_callback_still_opens_pdf(self) -> None:
        page = FakePage()
        service = _erp_download_service()
        client = FakeInfinityDmsClient(_valid_pdf_bytes())
        ui_jobs: list[object] = []
        opened_paths: list[str] = []
        view = MainView(
            page,
            general_preferences_service=service,
            infinity_dms_client=client,
        )
        view.bind_actions(
            on_open_document=lambda path: opened_paths.append(path),
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )
        view.run_background_task = lambda callback: callback()
        view.run_ui_task = lambda callback: callback()

        view.refresh_erp_documents()
        view.run_ui_task = lambda callback: ui_jobs.append(callback)
        table = view._home_view.content.content.controls[1].content.controls[0]
        _find_button(table, "Apri").on_click(None)
        ui_jobs[0]()

        self.assertEqual(len(opened_paths), 1)
        self.assertTrue(Path(opened_paths[0]).exists())
        view.stop_background_tasks()

    def test_local_pdf_display_does_not_create_erp_temp_session(self) -> None:
        page = FakePage()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            view = MainView(page, erp_temp_base_directory=base)

            view.display_document(
                filename="sample.pdf",
                image_content=b"png",
                image_width=100,
                image_height=100,
                page_number=1,
                page_count=1,
                zoom=1.0,
            )

            self.assertEqual(list(base.iterdir()), [])

    def test_erp_document_incomplete_soap_configuration_hides_open_action(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            basic_username="api-user",
            basic_password="api-secret",
            selected_user_id="20",
        )
        service.erp_documents_result = ErpDocumentsResult(
            True,
            "Caricati 1 documenti",
            (ErpDocument("NOME.pdf", "", "", "DOC-1", "AUTH-1", "20"),),
        )
        client = FakeInfinityDmsClient(_valid_pdf_bytes())
        view = MainView(
            page,
            general_preferences_service=service,
            infinity_dms_client=client,
        )
        view.bind_actions(
            on_open_document=lambda _: None,
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )
        view.run_background_task = lambda callback: callback()
        view.run_ui_task = lambda callback: callback()

        view.refresh_erp_documents()
        table = view._home_view.content.content.controls[1].content.controls[0]

        self.assertEqual([column.label.value for column in table.columns], ["Nome documento", "Data"])
        self.assertEqual(client.calls, [])

    def test_empty_erp_document_list_shows_empty_state(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="20",
        )
        service.erp_documents_result = ErpDocumentsResult(
            True,
            "Nessun documento da firmare",
            (),
        )
        view = MainView(page, general_preferences_service=service)

        view.refresh_erp_documents()

        content_column = view._home_view.content.content
        self.assertEqual(
            content_column.controls[1].content.value,
            "Nessun documento da firmare",
        )

    def test_invalid_erp_document_schema_shows_error_state(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="20",
        )
        service.erp_documents_result = ErpDocumentsResult(
            False,
            "Risposta ERP documenti non valida",
            (),
        )
        view = MainView(page, general_preferences_service=service)

        view.refresh_erp_documents()

        error_column = view._home_view.content.content
        self.assertEqual(error_column.controls[1].value, "Risposta ERP documenti non valida")
        self.assertEqual(_button_label(error_column.controls[2]), "Riprova")

    def test_erp_document_error_keeps_pdf_open_command_available(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="20",
        )
        service.erp_documents_result = ErpDocumentsResult(
            False,
            "Connessione ERP documenti fallita",
            (),
        )
        view = MainView(page, general_preferences_service=service)
        view.run_background_task = lambda callback: callback()
        view.run_ui_task = lambda callback: callback()

        view.build()
        view.refresh_erp_documents()

        toolbar = page.controls[0].controls[0].content
        self.assertEqual(toolbar.controls[1].controls[0].tooltip, "Apri")
        error_column = view._home_view.content.content
        self.assertEqual(error_column.controls[1].value, "Connessione ERP documenti fallita")
        self.assertEqual(_button_label(error_column.controls[2]), "Riprova")

    def test_auto_refresh_erp_documents_runs_only_after_user_confirmation(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.settings = SupabaseSettings(
            list_erp_documents=True,
            auto_refresh_erp_documents=True,
            erp_refresh_interval_seconds=30,
        )
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="20",
            selected_user_name="Romani",
        )
        view = MainView(page, general_preferences_service=service)

        self.assertFalse(view._refresh_erp_documents_if_auto_allowed())
        self.assertEqual(service.erp_document_fetch_count, 0)

        view._erp_session_user_confirmed = True

        self.assertTrue(view._refresh_erp_documents_if_auto_allowed())
        self.assertEqual(service.erp_document_fetch_count, 1)

    def test_auto_refresh_erp_documents_is_skipped_while_pdf_is_open(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.settings = SupabaseSettings(
            list_erp_documents=True,
            auto_refresh_erp_documents=True,
            erp_refresh_interval_seconds=30,
        )
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="20",
            selected_user_name="Romani",
        )
        view = MainView(page, general_preferences_service=service)
        view._erp_session_user_confirmed = True

        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=100,
            image_height=100,
            page_number=1,
            page_count=1,
            zoom=1.0,
        )

        self.assertFalse(view._refresh_erp_documents_if_auto_allowed())
        self.assertEqual(service.erp_document_fetch_count, 0)

    def test_pdf_mouse_wheel_changes_page_when_document_is_visible(self) -> None:
        page = FakePage()
        view = MainView(page)
        calls: list[str] = []
        view.bind_actions(
            on_open_document=lambda _: None,
            on_close=lambda: None,
            on_previous=lambda: calls.append("previous"),
            on_next=lambda: calls.append("next"),
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )
        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=3,
            zoom=1.0,
        )

        view._signature_surface.on_scroll(
            SimpleNamespace(scroll_delta=SimpleNamespace(y=80))
        )
        view._signature_surface.on_scroll(
            SimpleNamespace(scroll_delta=SimpleNamespace(y=-80))
        )

        self.assertEqual(calls, ["next", "previous"])

    def test_pdf_mouse_wheel_is_ignored_without_visible_document(self) -> None:
        page = FakePage()
        view = MainView(page)
        calls: list[str] = []
        view.bind_actions(
            on_open_document=lambda _: None,
            on_close=lambda: None,
            on_previous=lambda: calls.append("previous"),
            on_next=lambda: calls.append("next"),
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
        )

        view._signature_surface.on_scroll(
            SimpleNamespace(scroll_delta=SimpleNamespace(y=80))
        )

        self.assertEqual(calls, [])

    def test_logo_click_uses_flet_task_for_async_launch_url(self) -> None:
        page = FakeAsyncLaunchPage()
        view = MainView(page)

        view._viewer_placeholder.on_tap(None)

        self.assertEqual(page.launched_urls, ["https://queensrl.net"])

    def test_clear_document_returns_to_centered_white_logo_home(self) -> None:
        page = FakePage()
        view = MainView(page)
        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=1,
            zoom=1.0,
        )

        view.clear_document()

        self.assertTrue(view._home_view.visible)
        self.assertTrue(view._viewer_placeholder.visible)
        self.assertFalse(view._document_viewer.visible)
        self.assertEqual(view._home_view.bgcolor, view._ft.Colors.WHITE)
        self.assertEqual(view._home_view.alignment, view._ft.Alignment(0, 0))
        self.assertEqual(view._viewer_layers.fit, view._ft.StackFit.EXPAND)

    def test_viewer_background_is_gray_behind_loaded_pdfs(self) -> None:
        page = FakePage()
        view = MainView(page)

        view.build()

        root_column = page.controls[0]
        viewer = root_column.controls[1]
        self.assertEqual(viewer.bgcolor, view._ft.Colors.GREY_200)

    def test_main_toolbar_has_text_menu_and_icon_shortcuts(self) -> None:
        page = FakePage()
        view = MainView(page)

        view.build()

        toolbar = page.controls[0].controls[0].content
        self.assertEqual(page.title, "qSign by Queen Srl - queensrl.net")
        menu_bar = toolbar.controls[0]
        self.assertEqual(menu_bar.style.bgcolor, view._ft.Colors.TRANSPARENT)
        self.assertEqual(menu_bar.style.elevation, 0)
        self.assertEqual(
            [control.content.value for control in menu_bar.controls],
            ["Documenti", "Preferenze", "Informazioni"],
        )
        self.assertEqual(menu_bar.controls[0].style.bgcolor, view._ft.Colors.TRANSPARENT)
        self.assertEqual(
            [control.content.value for control in menu_bar.controls[0].controls],
            ["Apri", "Chiudi", "Salva", "Storico", "Template", "Aggiungi zona firma"],
        )
        self.assertEqual(
            [control.width for control in menu_bar.controls[0].controls],
            [180, 180, 180, 180, 180, 180],
        )
        self.assertEqual(
            [control.content.value for control in menu_bar.controls[1].controls],
            ["Impostazioni", "Connessione ERP", "Certificato"],
        )

        view.maximize_window()

        self.assertTrue(page.window.maximized)
        self.assertEqual(menu_bar.controls[1].controls[0].width, 180)
        self.assertEqual(menu_bar.controls[1].controls[1].width, 180)
        self.assertEqual(menu_bar.controls[1].controls[2].width, 180)

        icon_toolbar = toolbar.controls[1]
        tooltips = [
            getattr(control, "tooltip", None)
            for control in icon_toolbar.controls
            if getattr(control, "tooltip", None)
        ]
        self.assertEqual(
            tooltips,
            [
                "Apri",
                "Salva",
                "Chiudi",
                "Storico",
                "Aggiungi zona firma",
                "Pagina precedente",
                "Pagina successiva",
                "Zoom -",
                "Zoom +",
                "Sblocca impostazioni amministratore",
            ],
        )

    def test_activate_window_restores_focuses_and_updates_window(self) -> None:
        page = FakePage()
        page.window.visible = False
        page.window.minimized = True
        page.window.focused = False
        page.window.to_front_count = 0
        page.window.focus_count = 0
        page.window.to_front = lambda: setattr(
            page.window,
            "to_front_count",
            page.window.to_front_count + 1,
        )
        page.window.focus = lambda: setattr(
            page.window,
            "focus_count",
            page.window.focus_count + 1,
        )
        view = MainView(page)

        view.activate_window()

        self.assertTrue(page.window.visible)
        self.assertFalse(page.window.minimized)
        self.assertTrue(page.window.focused)
        self.assertEqual(page.window.to_front_count, 1)
        self.assertEqual(page.window.focus_count, 1)
        self.assertTrue(page.updated)

    def test_activate_window_awaits_async_window_methods(self) -> None:
        page = FakeAsyncLaunchPage()
        page.window.visible = False
        page.window.minimized = True
        page.window.focused = False
        page.window.to_front_count = 0
        page.window.focus_count = 0

        async def to_front() -> None:
            page.window.to_front_count += 1

        async def focus() -> None:
            page.window.focus_count += 1

        page.window.to_front = to_front
        page.window.focus = focus
        view = MainView(page)

        view.activate_window()

        self.assertTrue(page.window.visible)
        self.assertFalse(page.window.minimized)
        self.assertTrue(page.window.focused)
        self.assertEqual(page.window.to_front_count, 1)
        self.assertEqual(page.window.focus_count, 1)
        self.assertTrue(page.updated)

    def test_signed_history_lists_signed_documents_and_opens_selected_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_dir = Path(directory)
            signed_pdf = signed_dir / "contratto_signed.pdf"
            ignored_txt = signed_dir / "note.txt"
            signed_pdf.write_bytes(b"%PDF")
            ignored_txt.write_text("ignore", encoding="utf-8")
            page = FakePage()
            view = MainView(page, signed_history_directory=signed_dir)

            view.show_signed_history()

            self.assertEqual(page.dialog.title.value, "Storico documenti firmati")
            table_container = page.dialog.content.content.controls[1]
            table = table_container.content.controls[0]
            self.assertEqual(len(table.rows), 1)
            row = table.rows[0]
            name_button = row.cells[0].content.content
            self.assertEqual(name_button.content, "contratto_signed.pdf")
            self.assertRegex(
                row.cells[1].content.content.value,
                r"\d{2}/\d{2}/\d{4} ",
            )
            open_button = row.cells[2].content
            self.assertEqual(open_button.tooltip, "Apri documento firmato")

            name_button.on_click(None)

            self.assertEqual(page.launched_urls, [signed_pdf.resolve().as_uri()])

    def test_signed_history_shows_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            page = FakePage()
            view = MainView(page, signed_history_directory=directory)

            view.show_signed_history()

            self.assertEqual(
                page.dialog.content.content.controls[1].content.content.value,
                "Nessun documento firmato trovato",
            )

    def test_signed_history_filters_and_sorts_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_dir = Path(directory)
            alpha = signed_dir / "alpha_signed.pdf"
            beta = signed_dir / "beta_signed.pdf"
            alpha.write_bytes(b"%PDF")
            beta.write_bytes(b"%PDF")
            page = FakePage()
            view = MainView(page, signed_history_directory=signed_dir)

            view.show_signed_history()
            controls = page.dialog.content.content.controls
            search = controls[0]
            table_container = controls[1]

            search.value = "beta"
            search.on_change(None)

            table = table_container.content.controls[0]
            self.assertEqual(len(table.rows), 1)
            self.assertEqual(
                table.rows[0].cells[0].content.content.content,
                "beta_signed.pdf",
            )

            search.value = ""
            search.on_change(None)
            table = table_container.content.controls[0]
            table.columns[0].label.on_click(None)

            table = table_container.content.controls[0]
            self.assertEqual(
                [row.cells[0].content.content.content for row in table.rows],
                ["alpha_signed.pdf", "beta_signed.pdf"],
            )

    def test_signed_history_delete_is_only_available_for_admin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_dir = Path(directory)
            (signed_dir / "contratto_signed.pdf").write_bytes(b"%PDF")
            page = FakePage()
            view = MainView(page, signed_history_directory=signed_dir)

            view.show_signed_history()

            self.assertEqual(
                [_button_label(button) for button in page.dialog.actions],
                ["Chiudi"],
            )

            view._set_admin_mode(True)
            view.show_signed_history()

            self.assertEqual(
                [_button_label(button) for button in page.dialog.actions],
                ["Elimina", "Chiudi"],
            )

    def test_signed_history_delete_removes_signed_files_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_dir = Path(directory)
            signed_pdf = signed_dir / "contratto_signed.pdf"
            second_pdf = signed_dir / "visita_signed.pdf"
            ignored_txt = signed_dir / "note.txt"
            signed_pdf.write_bytes(b"%PDF")
            second_pdf.write_bytes(b"%PDF")
            ignored_txt.write_text("ignore", encoding="utf-8")
            page = FakePage()
            view = MainView(page, signed_history_directory=signed_dir)
            view._set_admin_mode(True)

            view.show_signed_history()
            page.dialog.actions[0].on_click(None)

            self.assertEqual(page.dialog.title.value, "Elimina storico")

            page.dialog.actions[1].on_click(None)

            self.assertFalse(signed_pdf.exists())
            self.assertFalse(second_pdf.exists())
            self.assertTrue(ignored_txt.exists())
            self.assertEqual(
                page.dialog.content.content.controls[1].content.content.value,
                "Nessun documento firmato trovato",
            )
            self.assertEqual(
                view._document_status.value,
                "Stato: eliminati 2 documenti dallo storico",
            )

    def test_template_history_lists_learned_templates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            learned = template_dir / "learned_privacy.json"
            ignored = template_dir / "manual.json"
            learned.write_text('{"template_id":"learned_privacy"}', encoding="utf-8")
            ignored.write_text("{}", encoding="utf-8")
            page = FakePage()
            view = MainView(page, learned_template_directory=template_dir)

            view.show_template_history()

            self.assertEqual(page.dialog.title.value, "Template Documenti")
            table_container = page.dialog.content.content.controls[1]
            table = table_container.content.controls[0]
            self.assertEqual(len(table.rows), 1)
            self.assertEqual(
                table.rows[0].cells[0].content.content.value,
                "learned_privacy.json",
            )
            self.assertRegex(
                table.rows[0].cells[1].content.content.value,
                r"\d{2}/\d{2}/\d{4} ",
            )

    def test_template_history_filters_and_sorts_learned_templates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            alpha = template_dir / "learned_alpha.json"
            beta = template_dir / "learned_beta.json"
            alpha.write_text("{}", encoding="utf-8")
            beta.write_text("{}", encoding="utf-8")
            page = FakePage()
            view = MainView(page, learned_template_directory=template_dir)

            view.show_template_history()
            controls = page.dialog.content.content.controls
            search = controls[0]
            table_container = controls[1]

            search.value = "beta"
            search.on_change(None)

            table = table_container.content.controls[0]
            self.assertEqual(len(table.rows), 1)
            self.assertEqual(
                table.rows[0].cells[0].content.content.value,
                "learned_beta.json",
            )

            search.value = ""
            search.on_change(None)
            table = table_container.content.controls[0]
            table.columns[0].label.on_click(None)

            table = table_container.content.controls[0]
            self.assertEqual(
                [row.cells[0].content.content.value for row in table.rows],
                ["learned_alpha.json", "learned_beta.json"],
            )

    def test_template_history_delete_is_only_available_for_admin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            (template_dir / "learned_privacy.json").write_text(
                "{}", encoding="utf-8"
            )
            page = FakePage()
            view = MainView(page, learned_template_directory=template_dir)

            view.show_template_history()

            button_row = page.dialog.content.content.controls[2]
            self.assertEqual(
                [_button_label(button) for button in button_row.controls],
                ["Aggiorna", "Scarica", "Carica", "Sincronizza"],
            )

            view._set_admin_mode(True)
            view.show_template_history()

            button_row = page.dialog.content.content.controls[2]
            self.assertEqual(
                [_button_label(button) for button in button_row.controls],
                ["Elimina", "Aggiorna", "Scarica", "Carica", "Sincronizza"],
            )

    def test_template_history_delete_removes_learned_templates_after_confirmation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            learned_alpha = template_dir / "learned_alpha.json"
            learned_beta = template_dir / "learned_beta.json"
            manual = template_dir / "manual.json"
            learned_alpha.write_text("{}", encoding="utf-8")
            learned_beta.write_text("{}", encoding="utf-8")
            manual.write_text("{}", encoding="utf-8")
            page = FakePage()
            view = MainView(page, learned_template_directory=template_dir)
            view._set_admin_mode(True)

            view.show_template_history()
            button_row = page.dialog.content.content.controls[2]
            button_row.controls[0].on_click(None)

            self.assertEqual(page.dialog.title.value, "Elimina template")

            page.dialog.actions[1].on_click(None)

            self.assertFalse(learned_alpha.exists())
            self.assertFalse(learned_beta.exists())
            self.assertTrue(manual.exists())
            self.assertEqual(
                page.dialog.content.content.controls[1].content.content.value,
                "Nessun template documento trovato",
            )
            self.assertEqual(
                page.dialog.content.content.controls[3].value,
                "Eliminati 2 template",
            )

    def test_template_history_delete_uses_sync_service_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            learned_alpha = template_dir / "learned_alpha.json"
            learned_beta = template_dir / "learned_beta.json"
            learned_alpha.write_text("{}", encoding="utf-8")
            learned_beta.write_text("{}", encoding="utf-8")
            page = FakePage()
            sync_service = FakeTemplateSyncService(template_dir)
            view = MainView(
                page,
                template_sync_service=sync_service,
                learned_template_directory=template_dir,
            )
            view._set_admin_mode(True)

            view.show_template_history()
            page.dialog.content.content.controls[2].controls[0].on_click(None)
            page.dialog.actions[1].on_click(None)

            self.assertEqual(sync_service.delete_count, 1)
            self.assertFalse(learned_alpha.exists())
            self.assertFalse(learned_beta.exists())
            self.assertEqual(
                page.dialog.content.content.controls[3].value,
                "Eliminati 2 template (Supabase: 2)",
            )

    def test_template_history_delete_stays_available_for_remote_only_cleanup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            page = FakePage()
            sync_service = FakeTemplateSyncService(template_dir)
            sync_service.remote_delete_count = 14
            view = MainView(
                page,
                template_sync_service=sync_service,
                learned_template_directory=template_dir,
            )
            view._set_admin_mode(True)

            view.show_template_history()
            delete_button = page.dialog.content.content.controls[2].controls[0]

            self.assertFalse(delete_button.disabled)

            delete_button.on_click(None)
            page.dialog.actions[1].on_click(None)

            self.assertEqual(sync_service.delete_count, 1)
            self.assertEqual(
                page.dialog.content.content.controls[3].value,
                "Eliminati 14 template da Supabase",
            )

    def test_template_download_refreshes_template_grid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            page = FakePage()
            sync_service = FakeTemplateSyncService(template_dir)
            view = MainView(
                page,
                template_sync_service=sync_service,
                learned_template_directory=template_dir,
            )

            view.show_template_history()
            self.assertEqual(
                page.dialog.content.content.controls[1].content.content.value,
                "Nessun template documento trovato",
            )

            button_row = page.dialog.content.content.controls[2]
            button_row.controls[1].on_click(None)

            self.assertEqual(page.dialog.title.value, "Template Documenti")
            table = page.dialog.content.content.controls[1].content.controls[0]
            self.assertEqual(len(table.rows), 1)
            self.assertEqual(
                table.rows[0].cells[0].content.content.value,
                "learned_synced.json",
            )
            self.assertEqual(
                page.dialog.content.content.controls[3].value,
                "Scaricati 1, invariati 0",
            )

    def test_anchor_overlay_is_rendered_above_pdf_image(self) -> None:
        page = FakePage()
        view = MainView(page)

        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=1,
            zoom=1.0,
            anchor_overlays=(
                SimpleNamespace(
                    left=10,
                    top=20,
                    width=30,
                    height=40,
                    label="Firma",
                ),
            ),
            anchor_count=1,
            selected_anchor=SimpleNamespace(
                page_index=0,
                bounds=Rectangle(10, 20, 40, 60),
            ),
        )

        self.assertEqual(view._pdf_stack.width, 200)
        self.assertEqual(view._pdf_stack.height, 300)
        self.assertEqual(len(view._pdf_stack.controls), 3)
        overlay = view._pdf_stack.controls[1]
        self.assertEqual(overlay.left, 10)
        self.assertEqual(overlay.top, 20)
        self.assertEqual(overlay.width, 30)
        self.assertEqual(overlay.height, 40)
        self.assertIn("Anchor trovati: 1", view._document_status.value)
        self.assertIn("Coord: 10.0,20.0,40.0,60.0", view._document_status.value)

    def test_manual_signature_drag_updates_draft_overlay_without_rebuilding_stack(self) -> None:
        page = FakePage()
        view = MainView(page)
        view.set_manual_signature_mode(True)

        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=1,
            zoom=1.0,
        )
        controls_before_drag = tuple(view._pdf_stack.controls)

        view._start_manual_signature_drag(_event(20, 30))
        view._update_manual_signature_drag(_event(80, 90))

        self.assertEqual(tuple(view._pdf_stack.controls), controls_before_drag)
        self.assertTrue(view._manual_draft_overlay.visible)
        self.assertEqual(view._manual_draft_overlay.left, 20)
        self.assertEqual(view._manual_draft_overlay.top, 30)
        self.assertEqual(view._manual_draft_overlay.width, 60)
        self.assertEqual(view._manual_draft_overlay.height, 60)

    def test_manual_signature_finish_uses_last_visible_draft_rectangle(self) -> None:
        page = FakePage()
        view = MainView(page)
        captured = []
        view.bind_actions(
            on_open_document=lambda _: None,
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
            on_manual_signature_rect=lambda *args: captured.append(args),
        )
        view.set_manual_signature_mode(True)
        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=1,
            zoom=1.0,
        )

        view._start_manual_signature_drag(_event(20, 30))
        view._update_manual_signature_drag(_event(120, 90))
        view._finish_manual_signature_drag(_event(21, 31))

        self.assertEqual(captured[0][:4], (20, 30, 100, 60))
        self.assertFalse(view._manual_draft_overlay.visible)

    def test_manual_signature_finish_ignores_tiny_accidental_drag(self) -> None:
        page = FakePage()
        view = MainView(page)
        captured = []
        view.bind_actions(
            on_open_document=lambda _: None,
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
            on_manual_signature_rect=lambda *args: captured.append(args),
        )
        view.set_manual_signature_mode(True)
        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=1,
            zoom=1.0,
        )

        view._start_manual_signature_drag(_event(20, 30))
        view._update_manual_signature_drag(_event(23, 34))
        view._finish_manual_signature_drag(_event(23, 34))

        self.assertEqual(captured, [])
        self.assertFalse(view._manual_draft_overlay.visible)

    def test_anchor_overlay_does_not_capture_input_during_manual_correction(self) -> None:
        page = FakePage()
        view = MainView(page)
        view.set_manual_signature_mode(True)

        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=1,
            zoom=1.0,
            anchor_overlays=(
                SimpleNamespace(
                    left=10,
                    top=20,
                    width=30,
                    height=40,
                    label="Firma suggerita",
                ),
            ),
        )

        overlay = view._pdf_stack.controls[1]
        self.assertTrue(overlay.ignore_interactions)
        self.assertIsNone(overlay.on_click)

    def test_signature_area_stays_clickable_during_manual_correction(self) -> None:
        page = FakePage()
        view = MainView(page)
        clicked = []
        view.bind_actions(
            on_open_document=lambda _: None,
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
            on_signature_area_click=lambda target_id: clicked.append(target_id),
        )
        view.set_manual_signature_mode(True)

        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=1,
            zoom=1.0,
            anchor_overlays=(
                SimpleNamespace(
                    left=10,
                    top=20,
                    width=80,
                    height=40,
                    label="Zona firma",
                    signature_content=None,
                    signature_media_type="image/svg+xml",
                ),
            ),
        )

        overlay = view._pdf_stack.controls[1]
        self.assertFalse(overlay.ignore_interactions)
        self.assertIsNotNone(overlay.on_click)
        overlay.on_click(None)
        self.assertEqual(clicked, [None])

    def test_multi_signature_area_stays_clickable_during_manual_correction(self) -> None:
        page = FakePage()
        view = MainView(page)
        clicked = []
        view.bind_actions(
            on_open_document=lambda _: None,
            on_close=lambda: None,
            on_previous=lambda: None,
            on_next=lambda: None,
            on_zoom_in=lambda: None,
            on_zoom_out=lambda: None,
            on_signature_area_click=lambda target_id: clicked.append(target_id),
        )
        view.set_manual_signature_mode(True)

        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=1,
            zoom=1.0,
            anchor_overlays=(
                SimpleNamespace(
                    left=10,
                    top=20,
                    width=80,
                    height=40,
                    label="Zona firma 2",
                    signature_content=None,
                    signature_media_type="image/svg+xml",
                    target_id="manual-signature-2",
                ),
            ),
        )

        overlay = view._pdf_stack.controls[1]
        self.assertFalse(overlay.ignore_interactions)
        self.assertIsNotNone(overlay.on_click)
        overlay.on_click(None)
        self.assertEqual(clicked, ["manual-signature-2"])

    def test_signature_overlay_is_rendered_inside_signature_rectangle(self) -> None:
        page = FakePage()
        view = MainView(page)
        signature = (
            b"<svg xmlns='http://www.w3.org/2000/svg' "
            b"width='420' height='180' viewBox='0 0 420 180'>"
            b"<polyline points='10.0,20.0 80.0,60.0' fill='none' "
            b"stroke='black' stroke-width='3' stroke-linecap='round' "
            b"stroke-linejoin='round'/></svg>"
        )

        view.display_document(
            filename="sample.pdf",
            image_content=b"png",
            image_width=200,
            image_height=300,
            page_number=1,
            page_count=1,
            zoom=1.0,
            anchor_overlays=(
                SimpleNamespace(
                    left=10,
                    top=20,
                    width=80,
                    height=40,
                    label="Zona firma",
                    signature_content=signature,
                    signature_media_type="image/svg+xml",
                ),
            ),
        )

        overlay = view._pdf_stack.controls[1]
        self.assertIsNotNone(overlay.content)
        self.assertEqual(overlay.content.width, 80)
        self.assertEqual(overlay.content.height, 40)
        self.assertGreater(len(overlay.content.shapes), 0)

    def test_signature_dialog_captures_clear_and_confirms_svg_signature(self) -> None:
        page = FakePage()
        view = MainView(page)
        captured = []

        view.open_signature_dialog(captured.append)

        self.assertEqual(page.dialog.title.value, "Firma")
        view._start_signature_stroke(_event(10, 20))
        view._update_signature_stroke(_event(40, 50))
        view._finish_signature_stroke(_event(80, 60))
        self.assertIn("polyline", view._signature_svg())
        self.assertGreater(len(view._signature_canvas.shapes), 0)

        page.dialog.actions[0].on_click(None)
        self.assertNotIn("polyline", view._signature_svg())
        self.assertEqual(len(view._signature_canvas.shapes), 0)

        view._start_signature_stroke(_event(15, 25))
        view._finish_signature_stroke(_event(70, 80))
        page.dialog.actions[2].on_click(None)

        self.assertEqual(captured[0].media_type, "image/svg+xml")
        self.assertIn(b"polyline", captured[0].content)
        self.assertTrue(page.dialog_popped)

    def test_signature_dialog_confirms_current_stroke_even_without_pan_end(self) -> None:
        page = FakePage()
        view = MainView(page)
        captured = []

        view.open_signature_dialog(captured.append)
        view._start_signature_stroke(_event(10, 20))
        view._update_signature_stroke(_event(40, 50))
        page.dialog.actions[2].on_click(None)

        self.assertEqual(captured[0].media_type, "image/svg+xml")
        self.assertIn(b"polyline", captured[0].content)

    def test_signature_dialog_uses_requested_canvas_as_svg_viewbox(self) -> None:
        page = FakePage()
        view = MainView(page)
        captured = []

        view.open_signature_dialog(
            captured.append,
            canvas_width=420,
            canvas_height=120,
        )
        view._start_signature_stroke(_event(10, 20))
        view._finish_signature_stroke(_event(70, 80))
        page.dialog.actions[2].on_click(None)

        self.assertIn(b"viewBox='0 0 420 120'", captured[0].content)
        self.assertEqual(view._signature_canvas.height, 120)

    def test_information_uses_the_current_flet_dialog_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_config = Path(directory) / "app.yaml"
            app_config.write_text('version: "01.001.001"', encoding="utf-8")
            page = FakePage()
            view = MainView(page, app_config_path=app_config)

            view.show_information()

            self.assertIsNone(page.dialog.title)
            controls = page.dialog.content.content.controls
            self.assertTrue(controls[0].content.src.startswith("data:image/png;base64,"))
            self.assertEqual(controls[1].value, "Versione: 01.001.001")
            site_button = controls[3].controls[1]
            support_button = controls[4].controls[1]
            footer = controls[5]
            self.assertEqual(site_button.content, "queensrl.net")
            self.assertEqual(support_button.content, "assistenza@qss.it")
            self.assertEqual(
                footer.controls[0].value,
                "Diritto di Autore @ 2026 Queen Srl. Tutti i diritti riservati",
            )
            self.assertTrue(footer.controls[1].src.startswith("data:image/png;base64,"))

            site_button.on_click(None)
            support_button.on_click(None)

            self.assertEqual(
                page.launched_urls,
                ["https://queensrl.net", "mailto:assistenza@qss.it"],
            )

    def test_certificate_child_dialog_replaces_preferences_dialog(self) -> None:
        page = FakePage()
        view = MainView(page, certificate_service=FakeCertificateService())
        view._admin_mode = True

        view.show_certificate_preferences()
        page.dialog.content.content.controls[6].controls[0].on_click(None)

        self.assertEqual(page.dialog.title.value, "Genera certificato")
        self.assertEqual(page.pop_count, 1)

    def test_certificate_delete_asks_for_confirmation(self) -> None:
        page = FakePage()
        service = FakeCertificateService()
        view = MainView(page, certificate_service=service)
        view._admin_mode = True

        view.show_certificate_preferences()
        page.dialog.content.content.controls[6].controls[3].on_click(None)

        self.assertEqual(page.dialog.title.value, "Cancella certificato")
        page.dialog.actions[1].on_click(None)
        self.assertEqual(service.deleted_thumbprints, ["AABB"])

    def test_certificate_preferences_show_signature_reason_summary(self) -> None:
        page = FakePage()
        service = FakeCertificateService()
        view = MainView(page, certificate_service=service)

        view.show_certificate_preferences()
        labels = [
            getattr(control, "value", "")
            for control in page.dialog.content.content.controls
        ]

        self.assertIn("Motivo firma: SorveglianzaSanitaria", labels)
        self.assertIn("Luogo: Forli", labels)
        self.assertIn("Contatto firmatario: privacy@example.test", labels)

    def test_certificate_status_bar_shows_active_certificate(self) -> None:
        page = FakePage()
        view = MainView(page, certificate_service=FakeCertificateService())

        self.assertEqual(
            view._document_status.value,
            "Certificato: Claudio Bacchi attivo",
        )

    def test_status_bar_shows_selected_erp_user_when_configured(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            selected_user_id="42",
            selected_user_name="Mario Rossi",
        )

        view = MainView(page, general_preferences_service=service)

        self.assertEqual(view._active_user.value, "Utente: Mario Rossi")

    def test_general_preferences_save_and_test_supabase_settings(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True

        view.show_general_preferences()
        supabase_tab, options_tab, signature_tab = _dialog_tab_contents(page.dialog)
        _find_control(supabase_tab, label="URL progetto Supabase").value = (
            "https://demo.supabase.co"
        )
        _find_control(supabase_tab, label="Password/API key Supabase").value = "secret"
        _find_control(supabase_tab, label="Tabella template Supabase").value = (
            "SaluteLavoro"
        )
        _find_control(
            options_tab,
            label="Sincronizza automaticamente i template all'avvio",
        ).value = True
        _find_control(options_tab, label="Salvataggio automatico").value = True
        _find_control(options_tab, label="Elenco Documenti ERP").value = True
        _find_control(
            options_tab,
            label="Aggiorna automaticamente documenti ERP",
        ).value = True
        _find_control(
            options_tab,
            label="Intervallo aggiornamento documenti ERP (secondi)",
        ).value = "45"
        _find_control(options_tab, label="Mostra testo nel riquadro firma").value = True
        _find_control(signature_tab, label="Metodo firma").value = "wacom"
        _find_control(signature_tab, label="Porta bridge ERP locale").value = "55123"
        self.assertEqual(
            [
                control.label
                for control in signature_tab.content.controls
                if hasattr(control, "label")
            ],
            [
                "Metodo firma",
                "Porta bridge ERP locale",
            ],
        )
        self.assertEqual(len(options_tab.content.controls), 2)
        self.assertEqual(
            [
                control.label
                for column in options_tab.content.controls
                for control in column.controls
                if hasattr(control, "label")
            ],
            [
                "Sincronizza automaticamente i template all'avvio",
                "Salvataggio automatico",
                "Mostra testo nel riquadro firma",
                "Elenco Documenti ERP",
                "Aggiorna automaticamente documenti ERP",
                "Intervallo aggiornamento documenti ERP (secondi)",
            ],
        )
        _find_button(supabase_tab, "Test").on_click(None)

        self.assertEqual(
            _general_preferences_result(page.dialog).content.value,
            "Connessione Supabase riuscita",
        )
        page.dialog.actions[1].on_click(None)

        self.assertEqual(
            service.settings,
            SupabaseSettings(
                project_url="https://demo.supabase.co",
                password="secret",
                table_name="SaluteLavoro",
                auto_sync_templates_on_startup=True,
                auto_save_signed_documents=True,
                list_erp_documents=True,
                auto_refresh_erp_documents=True,
                erp_refresh_interval_seconds=45,
                show_signature_text=True,
                signature_capture_mode="wacom",
                local_erp_port=55123,
            ),
        )
        self.assertEqual(
            _general_preferences_result(page.dialog).content.value,
            "Impostazioni salvate",
        )

    def test_general_preferences_save_disabled_signature_text(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.settings = SupabaseSettings(
            project_url="https://demo.supabase.co",
            password="secret",
            table_name="Meddoc",
            auto_sync_templates_on_startup=True,
            auto_save_signed_documents=True,
            show_signature_text=True,
            signature_capture_mode="wacom",
        )
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True

        view.show_general_preferences()
        options_tab = _dialog_tab_contents(page.dialog)[1]
        _find_control(
            options_tab,
            label="Sincronizza automaticamente i template all'avvio",
        ).value = "false"
        _find_control(options_tab, label="Salvataggio automatico").value = "false"
        _find_control(
            options_tab,
            label="Mostra testo nel riquadro firma",
        ).value = "false"
        page.dialog.actions[1].on_click(None)

        self.assertFalse(service.settings.auto_sync_templates_on_startup)
        self.assertFalse(service.settings.auto_save_signed_documents)
        self.assertFalse(service.settings.show_signature_text)
        self.assertEqual(service.settings.signature_capture_mode, "wacom")

    def test_general_preferences_disabling_erp_document_list_restores_logo_home(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.settings = SupabaseSettings(list_erp_documents=True)
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="20",
        )
        service.erp_documents_result = ErpDocumentsResult(
            True,
            "Caricati 1 documenti",
            (ErpDocument("NOME_DOCUMENTO.pdf", "2026-07-16 09:29:57"),),
        )
        view = MainView(page, general_preferences_service=service)
        view.refresh_erp_documents()
        self.assertIsNot(view._home_view.content, view._viewer_placeholder)

        view.show_general_preferences()
        options_tab = _dialog_tab_contents(page.dialog)[0]
        list_erp_documents = _find_control(
            options_tab,
            label="Elenco Documenti ERP",
        )
        list_erp_documents.value = False
        list_erp_documents.on_change(None)
        page.dialog.actions[1].on_click(None)

        self.assertFalse(service.settings.list_erp_documents)
        self.assertIs(view._home_view.content, view._viewer_placeholder)
        self.assertTrue(view._viewer_placeholder.visible)

    def test_general_preferences_enabling_erp_document_list_loads_documents_without_auto_refresh(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.settings = SupabaseSettings(
            list_erp_documents=False,
            auto_refresh_erp_documents=False,
        )
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="20",
        )
        service.erp_documents_result = ErpDocumentsResult(
            True,
            "Caricati 1 documenti",
            (ErpDocument("NOME_DOCUMENTO.pdf", "2026-07-16 09:29:57"),),
        )
        view = MainView(page, general_preferences_service=service)

        view.show_general_preferences()
        options_tab = _dialog_tab_contents(page.dialog)[0]
        _find_control(options_tab, label="Elenco Documenti ERP").value = True
        page.dialog.actions[1].on_click(None)

        self.assertTrue(service.settings.list_erp_documents)
        self.assertFalse(service.settings.auto_refresh_erp_documents)
        self.assertEqual(service.erp_document_fetch_count, 1)
        self.assertIsNot(view._home_view.content, view._viewer_placeholder)

    def test_general_preferences_save_notifies_listener_port_change(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        saved_settings: list[SupabaseSettings] = []
        view = MainView(
            page,
            general_preferences_service=service,
            on_general_preferences_saved=saved_settings.append,
        )

        view.show_general_preferences()
        _, signature_tab = _dialog_tab_contents(page.dialog)
        _find_control(signature_tab, label="Porta bridge ERP locale").value = "55123"
        page.dialog.actions[1].on_click(None)

        self.assertEqual(service.settings.local_erp_port, 55123)
        self.assertEqual(saved_settings[0].local_erp_port, 55123)

    def test_general_preferences_checks_and_creates_supabase_table(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True

        view.show_general_preferences()
        supabase_tab = _dialog_tab_contents(page.dialog)[0]
        _find_control(supabase_tab, label="URL progetto Supabase").value = (
            "https://demo.supabase.co"
        )
        _find_control(supabase_tab, label="Password/API key Supabase").value = "secret"
        _find_control(supabase_tab, label="Tabella template Supabase").value = "Meddoc"
        _find_button(supabase_tab, "Verifica tabella").on_click(None)

        self.assertEqual(
            _general_preferences_result(page.dialog).content.value,
            (
                "Tabella template Supabase 'Meddoc' non trovata. "
                "Premi 'Crea tabella' per duplicarla da SaluteLavoro."
            ),
        )

        _find_button(supabase_tab, "Crea tabella").on_click(None)

        self.assertEqual(service.created_table_settings.table_name, "Meddoc")
        self.assertEqual(
            _general_preferences_result(page.dialog).content.value,
            "Tabella template Supabase 'Meddoc' pronta",
        )

    def test_general_preferences_hides_supabase_settings_for_operator(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.settings = SupabaseSettings(
            project_url="https://demo.supabase.co",
            password="secret",
            table_name="Meddoc",
            auto_sync_templates_on_startup=False,
            auto_save_signed_documents=False,
        )
        view = MainView(page, general_preferences_service=service)

        view.show_general_preferences()
        options_tab, signature_tab = _dialog_tab_contents(page.dialog)

        with self.assertRaises(AssertionError):
            _find_control(page.dialog.content, label="URL progetto Supabase")
        with self.assertRaises(AssertionError):
            _find_control(page.dialog.content, label="Password/API key Supabase")
        with self.assertRaises(AssertionError):
            _find_control(page.dialog.content, label="Tabella template Supabase")
        self.assertEqual(
            [
                label
                for label in [_button_label(button) for button in page.dialog.actions]
                if label
            ],
            ["Salva", "Chiudi"],
        )

        _find_control(
            options_tab,
            label="Sincronizza automaticamente i template all'avvio",
        ).value = True
        _find_control(options_tab, label="Salvataggio automatico").value = True
        _find_control(signature_tab, label="Metodo firma").value = "wacom"
        page.dialog.actions[1].on_click(None)

        self.assertEqual(
            service.settings,
            SupabaseSettings(
                project_url="https://demo.supabase.co",
                password="secret",
                table_name="Meddoc",
                auto_sync_templates_on_startup=True,
                auto_save_signed_documents=True,
                signature_capture_mode="wacom",
            ),
        )

    def test_user_preferences_load_select_and_save_erp_user(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True

        view.show_user_preferences()
        layout = page.dialog.content.content.controls
        connection_controls = layout[0].content.controls
        users_controls = layout[2].content.controls
        connection_controls[0].value = "https://erp.example.test/users"
        connection_controls[1].value = "https://erp.example.test/documents"
        connection_controls[2].value = "https://erp.example.test/soap"
        connection_controls[3].value = "SALAV"
        connection_controls[4].value = "api-user"
        connection_controls[5].value = "api-secret"
        connection_controls[6].controls[1].on_click(None)

        users_list = users_controls[5]
        self.assertEqual(connection_controls[7].value, "Caricati 1 utenti")
        users_controls[2].value = True
        users_list.controls[1].controls[2].on_click(None)
        self.assertEqual(users_controls[1].content.value, "Mario Rossi (42)")
        self.assertEqual(service.erp_document_fetch_count, 1)

        self.assertEqual(
            service.erp_settings,
            ErpUserSettings(
                users_url="https://erp.example.test/users",
                documents_url="https://erp.example.test/documents",
                document_service_url="https://erp.example.test/soap",
                company_id="SALAV",
                basic_username="api-user",
                basic_password="api-secret",
                selected_user_id="42",
                selected_user_name="Mario Rossi",
                persistent_user=True,
            ),
        )
        self.assertEqual(view._active_user.value, "Utente: Mario Rossi")
        self.assertEqual(connection_controls[7].value, "Utente salvato: Mario Rossi")
        self.assertEqual(
            service.session_user_logs,
            [
                (
                    ErpUserSettings(
                        users_url="https://erp.example.test/users",
                        documents_url="https://erp.example.test/documents",
                        document_service_url="https://erp.example.test/soap",
                        company_id="SALAV",
                        basic_username="api-user",
                        basic_password="api-secret",
                        selected_user_id="42",
                        selected_user_name="Mario Rossi",
                        persistent_user=True,
                    ),
                    "user_preferences_selection",
                )
            ],
        )

        reloaded_view = MainView(page, general_preferences_service=service)

        self.assertEqual(reloaded_view._active_user.value, "Utente: Mario Rossi")

    def test_user_preferences_tests_erp_user_connection_without_loading_grid(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True

        view.show_user_preferences()
        layout = page.dialog.content.content.controls
        connection_controls = layout[0].content.controls
        users_controls = layout[2].content.controls
        connection_controls[0].value = "https://erp.example.test/users"
        connection_controls[1].value = "https://erp.example.test/documents"
        connection_controls[4].value = "api-user"
        connection_controls[5].value = "api-secret"
        connection_controls[6].controls[0].on_click(None)

        self.assertEqual(
            connection_controls[7].value,
            "Connessione utenti riuscita: 1 utenti disponibili",
        )
        self.assertEqual(users_controls[5].controls, [])

    def test_user_preferences_load_users_runs_in_background(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            users_url="https://erp.example.test/users",
            basic_username="api-user",
            basic_password="api-secret",
        )
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        view.show_user_preferences()
        connection_controls = page.dialog.content.content.controls[0].content.controls
        users_controls = page.dialog.content.content.controls[2].content.controls
        test_button = connection_controls[6].controls[0]
        load_button = connection_controls[6].controls[1]
        load_button.on_click(None)

        self.assertEqual(service.erp_user_fetch_count, 0)
        self.assertTrue(load_button.disabled)
        self.assertTrue(test_button.disabled)
        self.assertEqual(connection_controls[7].value, "Caricamento utenti...")

        background_jobs[0]()
        ui_jobs[0]()

        self.assertFalse(load_button.disabled)
        self.assertFalse(test_button.disabled)
        self.assertEqual(len(users_controls[5].controls), 2)
        self.assertEqual(connection_controls[7].value, "Caricati 1 utenti")

    def test_user_preferences_test_users_runs_in_background(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            users_url="https://erp.example.test/users",
            basic_username="api-user",
            basic_password="api-secret",
        )
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        view.show_user_preferences()
        connection_controls = page.dialog.content.content.controls[0].content.controls
        users_controls = page.dialog.content.content.controls[2].content.controls
        test_button = connection_controls[6].controls[0]
        load_button = connection_controls[6].controls[1]
        test_button.on_click(None)

        self.assertEqual(service.erp_user_fetch_count, 0)
        self.assertTrue(test_button.disabled)
        self.assertTrue(load_button.disabled)
        self.assertEqual(connection_controls[7].value, "Verifica in corso...")

        background_jobs[0]()
        ui_jobs[0]()

        self.assertFalse(test_button.disabled)
        self.assertFalse(load_button.disabled)
        self.assertEqual(
            connection_controls[7].value,
            "Connessione utenti riuscita: 1 utenti disponibili",
        )
        self.assertEqual(users_controls[5].controls, [])

    def test_user_preferences_result_after_close_is_ignored(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        view.show_user_preferences()
        connection_controls = page.dialog.content.content.controls[0].content.controls
        connection_controls[6].controls[1].on_click(None)
        page.dialog.actions[1].on_click(None)
        page.updated = False
        background_jobs[0]()
        ui_jobs[0]()

        self.assertFalse(page.updated)

    def test_user_preferences_stale_users_result_is_ignored(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_user_results = [
            ErpUsersResult(True, "Caricati 1 utenti", (ErpUser("1", "Old"),)),
            ErpUsersResult(True, "Caricati 1 utenti", (ErpUser("2", "New"),)),
        ]
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        view.show_user_preferences()
        connection_controls = page.dialog.content.content.controls[0].content.controls
        users_controls = page.dialog.content.content.controls[2].content.controls
        load_button = connection_controls[6].controls[1]
        load_button.on_click(None)
        load_button.on_click(None)

        background_jobs[0]()
        ui_jobs[0]()
        background_jobs[1]()
        ui_jobs[1]()

        self.assertEqual(len(users_controls[5].controls), 2)
        self.assertEqual(users_controls[5].controls[1].controls[0].value, "New")

    def test_user_preferences_worker_exception_is_controlled(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_users_error = RuntimeError("api-secret should not leak")
        background_jobs: list[object] = []
        ui_jobs: list[object] = []
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True
        view.run_background_task = lambda callback: background_jobs.append(callback)
        view.run_ui_task = lambda callback: ui_jobs.append(callback)

        view.show_user_preferences()
        connection_controls = page.dialog.content.content.controls[0].content.controls
        connection_controls[6].controls[1].on_click(None)
        background_jobs[0]()
        ui_jobs[0]()

        self.assertEqual(connection_controls[7].value, "Caricamento utenti ERP fallito")
        self.assertNotIn("api-secret", connection_controls[7].value)

    def test_user_preferences_hides_api_settings_for_operator(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            users_url="https://erp.example.test/users",
            basic_username="api-user",
            basic_password="api-secret",
        )
        view = MainView(page, general_preferences_service=service)

        view.show_user_preferences()
        layout = page.dialog.content.content.controls
        connection_controls = layout[0].content.controls
        users_controls = layout[2].content.controls

        labels = [getattr(control, "label", "") for control in connection_controls]
        self.assertNotIn("URL lista utenti ERP", labels)
        self.assertNotIn("URL servizio documentale SOAP", labels)
        self.assertNotIn("Company ID", labels)
        self.assertNotIn("Utente Basic Auth", labels)
        self.assertNotIn("Password Basic Auth", labels)
        self.assertEqual(
            [_button_label(button) for button in connection_controls[1].controls],
            ["Carica utenti"],
        )
        self.assertEqual(
            [_button_label(button) for button in page.dialog.actions],
            ["Salva", "Chiudi"],
        )

        connection_controls[1].controls[0].on_click(None)

        self.assertEqual(connection_controls[2].value, "Caricati 1 utenti")
        self.assertEqual(len(users_controls[5].controls), 2)

    def test_user_preferences_operator_can_save_persistent_user_flag(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            users_url="https://erp.example.test/users",
            documents_url="https://erp.example.test/documents",
            selected_user_id="42",
            selected_user_name="Mario Rossi",
        )
        view = MainView(page, general_preferences_service=service)

        view.show_user_preferences()
        users_controls = page.dialog.content.content.controls[2].content.controls
        users_controls[2].value = True
        page.dialog.actions[0].on_click(None)

        self.assertTrue(service.erp_settings.persistent_user)
        self.assertEqual(page.dialog.content.content.controls[0].content.controls[2].value, "Impostazioni ERP salvate")

        view.show_user_preferences()
        users_controls = page.dialog.content.content.controls[2].content.controls
        self.assertTrue(users_controls[2].value)

    def test_user_preferences_can_clear_selected_erp_user(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            users_url="https://erp.example.test/users",
            documents_url="https://erp.example.test/documents",
            basic_username="api-user",
            basic_password="api-secret",
            selected_user_id="42",
            selected_user_name="Mario Rossi",
        )
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True

        view.show_user_preferences()
        layout = page.dialog.content.content.controls
        connection_controls = layout[0].content.controls
        users_controls = layout[2].content.controls
        connection_controls[6].controls[1].on_click(None)
        users_list = users_controls[5]

        users_list.controls[0].controls[2].on_click(None)

        self.assertEqual(
            service.erp_settings,
            ErpUserSettings(
                users_url="https://erp.example.test/users",
                documents_url="https://erp.example.test/documents",
                company_id="SALAV",
                basic_username="api-user",
                basic_password="api-secret",
            ),
        )
        self.assertEqual(users_controls[1].content.value, "Nessun utente selezionato")
        self.assertEqual(view._active_user.value, "")
        self.assertEqual(connection_controls[7].value, "Nessun utente selezionato")
        self.assertEqual(service.erp_document_fetch_count, 0)

    def test_startup_user_confirmation_is_skipped_without_selected_user(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        view = MainView(page, general_preferences_service=service)

        shown = view.show_startup_user_confirmation()

        self.assertFalse(shown)
        self.assertFalse(hasattr(page, "dialog"))

    def test_startup_user_confirmation_is_shown_with_erp_url_without_selected_user(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            users_url="https://erp.example.test/users",
        )
        view = MainView(page, general_preferences_service=service)

        shown = view.show_startup_user_confirmation()

        self.assertTrue(shown)
        self.assertEqual(page.dialog.title.value, "Utente operativo")
        self.assertEqual(page.dialog.content.controls[1].value, "Nessun utente selezionato")
        self.assertEqual([_button_label(button) for button in page.dialog.actions], ["Seleziona utente"])

    def test_startup_user_confirmation_can_be_confirmed(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            selected_user_id="3",
            selected_user_name="Ghinassi",
        )
        view = MainView(page, general_preferences_service=service)

        shown = view.show_startup_user_confirmation()

        self.assertTrue(shown)
        self.assertEqual(page.dialog.title.value, "Utente operativo")
        self.assertEqual(page.dialog.content.controls[1].value, "Ghinassi (3)")

        page.dialog.actions[1].on_click(None)

        self.assertEqual(page.pop_count, 1)
        self.assertEqual(service.erp_document_fetch_count, 0)
        self.assertEqual(
            service.session_user_logs,
            [
                (
                    ErpUserSettings(
                        selected_user_id="3",
                        selected_user_name="Ghinassi",
                    ),
                    "startup_confirmation",
                )
            ],
        )

    def test_startup_user_confirmation_refreshes_documents_after_confirm(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="3",
            selected_user_name="Ghinassi",
        )
        service.settings = SupabaseSettings(list_erp_documents=True)
        view = MainView(page, general_preferences_service=service)

        view.show_startup_user_confirmation()

        self.assertEqual(service.erp_document_fetch_count, 0)

        page.dialog.actions[1].on_click(None)

        self.assertEqual(service.erp_document_fetch_count, 1)
        self.assertEqual(service.erp_document_fetch_settings.selected_user_id, "3")

    def test_startup_user_confirmation_keeps_logo_when_erp_document_list_is_disabled(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.settings = SupabaseSettings(list_erp_documents=False)
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="3",
            selected_user_name="Ghinassi",
        )
        view = MainView(page, general_preferences_service=service)

        view.show_startup_user_confirmation()
        page.dialog.actions[1].on_click(None)

        self.assertEqual(service.erp_document_fetch_count, 0)
        self.assertIs(view._home_view.content, view._viewer_placeholder)
        self.assertTrue(view._viewer_placeholder.visible)

    def test_startup_user_persistent_skips_confirmation_and_loads_documents(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            documents_url="https://erp.example.test/documents",
            selected_user_id="3",
            selected_user_name="Ghinassi",
            persistent_user=True,
        )
        service.settings = SupabaseSettings(list_erp_documents=True)
        view = MainView(page, general_preferences_service=service)

        shown = view.show_startup_user_confirmation()

        self.assertFalse(shown)
        self.assertFalse(hasattr(page, "dialog"))
        self.assertEqual(view._active_user.value, "Utente: Ghinassi")
        self.assertEqual(service.erp_document_fetch_count, 1)
        self.assertEqual(
            service.session_user_logs,
            [(service.erp_settings, "startup_persistent_user")],
        )

    def test_startup_user_confirmation_can_open_user_preferences(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.erp_settings = ErpUserSettings(
            selected_user_id="3",
            selected_user_name="Ghinassi",
        )
        view = MainView(page, general_preferences_service=service)

        view.show_startup_user_confirmation()
        page.dialog.actions[0].on_click(None)

        self.assertEqual(page.pop_count, 1)
        self.assertEqual(page.dialog.title.value, "Connessione ERP")

    def test_certificate_preferences_do_not_show_global_signature_reason_editor(
        self,
    ) -> None:
        page = FakePage()
        service = FakeCertificateService()
        view = MainView(page, certificate_service=service)

        view.show_certificate_preferences()

        labels = [
            getattr(control, "value", "")
            for control in page.dialog.content.content.controls
        ]
        self.assertNotIn("Metadati firma", labels)

    def test_certificate_preferences_only_allows_selection_for_operator(self) -> None:
        page = FakePage()
        service = FakeCertificateService()
        view = MainView(page, certificate_service=service)

        view.show_certificate_preferences()

        action_row = page.dialog.content.content.controls[6]
        self.assertEqual(
            [_button_label(button) for button in action_row.controls],
            ["Seleziona certificato"],
        )

    def test_select_certificate_list_aligns_items_to_left(self) -> None:
        page = FakePage()
        service = FakeCertificateService()
        view = MainView(page, certificate_service=service)

        view.show_certificate_preferences()
        page.dialog.content.content.controls[6].controls[0].on_click(None)

        certificate_button = page.dialog.content.content.controls[0]
        certificate_content = certificate_button.content
        certificate_column = certificate_content.content

        self.assertEqual(certificate_content.width, 460)
        self.assertEqual(certificate_content.alignment.x, -1)
        self.assertEqual(certificate_column.horizontal_alignment, view._ft.CrossAxisAlignment.START)

    def test_generate_certificate_dialog_saves_signature_reason_and_updates_status(
        self,
    ) -> None:
        page = FakePage()
        service = FakeCertificateService()
        view = MainView(page, certificate_service=service)
        view._admin_mode = True

        view.show_certificate_preferences()
        page.dialog.content.content.controls[6].controls[0].on_click(None)
        generate_controls = page.dialog.content.content.controls
        generate_controls[0].value = "Mario"
        generate_controls[1].value = "Rossi"
        generate_controls[4].value = "secret"
        generate_controls[5].value = "Privacy"
        generate_controls[6].value = "Cesena"
        generate_controls[7].value = "contatto@example.test"
        page.dialog.actions[1].on_click(None)

        self.assertEqual(service.generated_names, [("Mario", "Rossi")])
        self.assertEqual(
            service.signature_metadata,
            SignatureMetadata(
                reason="Privacy",
                location="Cesena",
                contact_info="contatto@example.test",
            ),
        )
        self.assertEqual(
            view._document_status.value,
            "Certificato: Claudio Bacchi attivo",
        )

    def test_admin_unlock_first_setup_saves_password_and_enables_admin_mode(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        view = MainView(page, general_preferences_service=service)
        view.build()

        view.show_admin_unlock_dialog()
        controls = page.dialog.content.content.controls
        self.assertTrue(controls[1].autofocus)
        controls[1].value = "admin-secret"
        controls[2].value = "admin-secret"
        page.dialog.actions[1].on_click(None)

        self.assertTrue(view._admin_mode)
        self.assertEqual(service.admin_password, "admin-secret")
        self.assertEqual(view._security_button.icon, view._ft.Icons.LOCK_OPEN)

    def test_admin_unlock_rejects_invalid_existing_password(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.admin_password = "admin-secret"
        view = MainView(page, general_preferences_service=service)

        view.show_admin_unlock_dialog()
        controls = page.dialog.content.content.controls
        self.assertTrue(controls[1].autofocus)
        controls[1].value = "wrong"
        page.dialog.actions[1].on_click(None)

        self.assertFalse(view._admin_mode)
        self.assertEqual(controls[3].value, "Password amministratore non valida")

    def test_admin_unlock_dialog_logs_out_active_administrator(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        service.admin_password = "admin-secret"
        view = MainView(page, general_preferences_service=service)
        view.build()
        view._set_admin_mode(True)

        view.show_admin_unlock_dialog()

        self.assertEqual(page.dialog.title.value, "Amministratore attivo")
        controls = page.dialog.content.content.controls
        self.assertFalse(controls[1].visible)
        self.assertEqual(_button_label(page.dialog.actions[1]), "Logout")

        page.dialog.actions[1].on_click(None)

        self.assertFalse(view._admin_mode)
        self.assertEqual(view._security_button.icon, view._ft.Icons.LOCK)
        self.assertEqual(view._document_status.value, "Stato: modalitÃ  operatore attiva")


if __name__ == "__main__":
    unittest.main()


def _event(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(local_position=SimpleNamespace(x=x, y=y))


def _button_label(button: object) -> str:
    content = getattr(button, "content", "")
    return str(getattr(content, "value", content))


def _dialog_tab_contents(dialog: object) -> list[object]:
    tabs = dialog.content.content
    return tabs.content.controls[1].controls


def _find_control(root: object, *, label: str) -> object:
    for control in _walk_controls(root):
        if getattr(control, "label", None) == label:
            return control
    raise AssertionError(f"Control with label {label!r} not found")


def _find_button(root: object, label: str) -> object:
    for control in _walk_controls(root):
        if _button_label(control) == label:
            return control
    raise AssertionError(f"Button {label!r} not found")


def _erp_refresh_button(view: MainView) -> object:
    return view._home_view.content.content.controls[0].controls[2]


def _erp_documents_body(view: MainView) -> object:
    return view._home_view.content.content.controls[1].content


def _valid_pdf_bytes() -> bytes:
    global VALID_PDF_BYTES
    if VALID_PDF_BYTES:
        return VALID_PDF_BYTES
    document = pymupdf.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), "qSign ERP PDF")
        VALID_PDF_BYTES = document.tobytes()
        return VALID_PDF_BYTES
    finally:
        document.close()


def _erp_download_service() -> "FakeGeneralPreferencesService":
    service = FakeGeneralPreferencesService()
    service.erp_settings = ErpUserSettings(
        documents_url="https://erp.example.test/documents",
        document_service_url="https://erp.example.test/soap",
        company_id="SALAV",
        basic_username="api-user",
        basic_password="api-secret",
        selected_user_id="20",
        selected_user_name="Romani",
    )
    service.erp_documents_result = ErpDocumentsResult(
        True,
        "Caricati 1 documenti",
        (
            ErpDocument(
                "NOME.pdf",
                "2026-07-16 09:29:57",
                "",
                "DOC-1",
                "AUTH-1",
                "20",
            ),
        ),
    )
    return service


def _general_preferences_result(dialog: object) -> object:
    return dialog.actions[0]


def _walk_controls(root: object) -> list[object]:
    controls = [root]
    found: list[object] = []
    while controls:
        control = controls.pop(0)
        found.append(control)
        for attr in ("content", "controls", "tabs", "rows", "cells"):
            children = getattr(control, attr, None)
            if children is None or isinstance(children, str):
                continue
            if isinstance(children, list):
                controls.extend(children)
            else:
                controls.append(children)
    return found


class FakeCertificateService:
    def __init__(self) -> None:
        self.deleted_thumbprints: list[str] = []
        self.signature_metadata = SignatureMetadata(
            reason="SorveglianzaSanitaria",
            location="Forli",
            contact_info="privacy@example.test",
        )
        self.generated_names: list[tuple[str, str]] = []

    def get_active_certificate(self) -> CertificateInfo:
        return CertificateInfo(
            name="Claudio Bacchi",
            type="Store Windows - chiave privata",
            valid_until="2029-07-08",
            thumbprint="AABB",
        )

    def list_certificates(self) -> tuple[CertificateInfo, ...]:
        return (self.get_active_certificate(),)

    def delete_certificate(self, thumbprint: str) -> None:
        self.deleted_thumbprints.append(thumbprint)

    def get_signature_reason(self) -> str:
        return self.signature_metadata.reason

    def set_signature_reason(self, reason: str) -> None:
        self.signature_metadata = SignatureMetadata(
            reason=reason,
            location=self.signature_metadata.location,
            contact_info=self.signature_metadata.contact_info,
        )

    def get_signature_metadata(self) -> SignatureMetadata:
        return self.signature_metadata

    def set_signature_metadata(
        self,
        reason: str,
        location: str = "",
        contact_info: str = "",
    ) -> None:
        self.signature_metadata = SignatureMetadata(
            reason=reason,
            location=location,
            contact_info=contact_info,
        )

    def generate_self_signed(
        self,
        first_name: str,
        last_name: str,
        organization: str,
        pfx_password: str,
        valid_until: str | None = None,
    ) -> CertificateInfo:
        self.generated_names.append((first_name, last_name))
        return self.get_active_certificate()


class FakeGeneralPreferencesService:
    def __init__(self) -> None:
        self.settings = SupabaseSettings(list_erp_documents=True)
        self.erp_settings = ErpUserSettings()
        self.erp_documents_result = ErpDocumentsResult(True, "Nessun documento da firmare", ())
        self.erp_document_results: list[ErpDocumentsResult] = []
        self.erp_users_result = ErpUsersResult(
            True,
            "Caricati 1 utenti",
            (ErpUser("42", "Mario Rossi"),),
        )
        self.erp_user_results: list[ErpUsersResult] = []
        self.erp_users_error: Exception | None = None
        self.erp_documents_error: Exception | None = None
        self.erp_document_fetch_count = 0
        self.erp_document_fetch_settings = ErpUserSettings()
        self.erp_user_fetch_count = 0
        self.erp_user_fetch_settings = ErpUserSettings()
        self.created_table_settings = SupabaseSettings()
        self.admin_password = ""
        self.session_user_logs: list[tuple[ErpUserSettings, str]] = []

    def get_supabase_settings(self) -> SupabaseSettings:
        return self.settings

    def save_supabase_settings(self, settings: SupabaseSettings) -> None:
        self.settings = settings

    def test_supabase_connection(
        self, settings: SupabaseSettings | None = None
    ) -> SupabaseConnectionResult:
        return SupabaseConnectionResult(True, "Connessione Supabase riuscita")

    def test_supabase_template_table(
        self, settings: SupabaseSettings | None = None
    ) -> SupabaseTableResult:
        table_name = (settings or self.settings).table_name
        if table_name == "Meddoc":
            return SupabaseTableResult(
                True,
                False,
                "Tabella template Supabase 'Meddoc' non trovata",
            )
        return SupabaseTableResult(
            True,
            True,
            f"Tabella template Supabase '{table_name}' disponibile",
        )

    def ensure_supabase_template_table(
        self,
        settings: SupabaseSettings | None = None,
    ) -> SupabaseTableResult:
        self.created_table_settings = settings or self.settings
        return SupabaseTableResult(
            True,
            True,
            (
                "Tabella template Supabase "
                f"'{self.created_table_settings.table_name}' pronta"
            ),
        )

    def has_admin_password(self) -> bool:
        return bool(self.admin_password)

    def set_admin_password(self, password: str) -> None:
        self.admin_password = password

    def verify_admin_password(self, password: str) -> bool:
        return bool(self.admin_password) and password == self.admin_password

    def log_erp_user_session_selection(
        self,
        settings: ErpUserSettings | None = None,
        source: str = "manual",
    ) -> None:
        self.session_user_logs.append((settings or self.erp_settings, source))

    def get_erp_user_settings(self) -> ErpUserSettings:
        return self.erp_settings

    def save_erp_user_settings(self, settings: ErpUserSettings) -> None:
        self.erp_settings = settings

    def fetch_erp_users(
        self, settings: ErpUserSettings | None = None
    ) -> ErpUsersResult:
        self.erp_user_fetch_count += 1
        self.erp_user_fetch_settings = settings or self.erp_settings
        if self.erp_users_error is not None:
            raise self.erp_users_error
        if self.erp_user_results:
            return self.erp_user_results.pop(0)
        return self.erp_users_result

    def fetch_erp_documents(
        self, settings: ErpUserSettings | None = None
    ) -> ErpDocumentsResult:
        self.erp_document_fetch_count += 1
        self.erp_document_fetch_settings = settings or self.erp_settings
        if self.erp_documents_error is not None:
            raise self.erp_documents_error
        if self.erp_document_results:
            return self.erp_document_results.pop(0)
        return self.erp_documents_result


class FakeInfinityDmsClient:
    def __init__(self, content: bytes | None = None, error: Exception | None = None) -> None:
        self.content = content if content is not None else _valid_pdf_bytes()
        self.error = error
        self.calls: list[dict[str, object]] = []

    def download_document(
        self,
        *,
        service_url: str,
        credentials: object,
        document_id: str,
        auth_code: str,
    ) -> bytes:
        self.calls.append(
            {
                "service_url": service_url,
                "credentials": credentials,
                "document_id": document_id,
                "auth_code": auth_code,
            }
        )
        if self.error is not None:
            raise self.error
        return self.content


class FakeTemplateSyncResult:
    def __init__(
        self,
        uploaded: int = 0,
        downloaded: int = 0,
        skipped: int = 0,
        deleted: int = 0,
        remote_deleted: int = 0,
        remote_remaining: int = 0,
    ) -> None:
        self.uploaded = uploaded
        self.downloaded = downloaded
        self.skipped = skipped
        self.deleted = deleted
        self.remote_deleted = remote_deleted
        self.remote_remaining = remote_remaining


class FakeTemplateSyncService:
    def __init__(self, template_dir: Path) -> None:
        self._template_dir = template_dir
        self.delete_count = 0
        self.remote_delete_count = 0

    def download_templates(self) -> FakeTemplateSyncResult:
        (self._template_dir / "learned_synced.json").write_text(
            '{"template_id":"learned_synced"}',
            encoding="utf-8",
        )
        return FakeTemplateSyncResult(downloaded=1)

    def upload_templates(self) -> FakeTemplateSyncResult:
        return FakeTemplateSyncResult(uploaded=1)

    def sync_templates(self) -> FakeTemplateSyncResult:
        self.download_templates()
        return FakeTemplateSyncResult(uploaded=1, downloaded=1)

    def delete_templates(self) -> FakeTemplateSyncResult:
        self.delete_count += 1
        deleted = 0
        for path in self._template_dir.glob("learned_*.json"):
            if path.is_file():
                path.unlink()
                deleted += 1
        return FakeTemplateSyncResult(
            deleted=deleted,
            remote_deleted=self.remote_delete_count or deleted,
        )
