"""Presentation tests for renderer output conversion."""

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.certificate_service import CertificateInfo, SignatureMetadata
from app.services.general_preferences_service import (
    ErpUser,
    ErpUserSettings,
    ErpUsersResult,
    SupabaseConnectionResult,
    SupabaseSettings,
    SupabaseTableResult,
)
from models.document import Rectangle
from ui.main_view import MainView


class FakePage:
    def __init__(self) -> None:
        self.controls: list[object] = []
        self.services: list[object] = []
        self.updated = False
        self.launched_urls: list[str] = []
        self.pop_count = 0

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
        self.assertEqual(page.title, "QSign by Queen Srl - queensrl.net")
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
            ["Apri", "Chiudi", "Salva", "Storico", "Template"],
        )
        self.assertEqual(
            [control.width for control in menu_bar.controls[0].controls],
            [180, 180, 180, 180, 180],
        )
        self.assertEqual(
            [control.content.value for control in menu_bar.controls[1].controls],
            ["Generali", "Utenti", "Certificato"],
        )
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
                "Pagina precedente",
                "Pagina successiva",
                "Zoom -",
                "Zoom +",
                "Sblocca impostazioni amministratore",
            ],
        )

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
            on_signature_area_click=lambda: clicked.append(True),
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
        self.assertEqual(clicked, [True])

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
        controls = page.dialog.content.content.controls
        controls[1].value = "https://demo.supabase.co"
        controls[2].value = "secret"
        controls[3].value = "SaluteLavoro"
        controls[4].value = True
        controls[5].value = True
        controls[7].value = "wacom"
        controls[8].value = True
        controls[9].controls[0].on_click(None)

        self.assertEqual(
            controls[10].value,
            "Connessione Supabase riuscita",
        )
        controls[9].controls[3].on_click(None)

        self.assertEqual(
            service.settings,
            SupabaseSettings(
                project_url="https://demo.supabase.co",
                password="secret",
                table_name="SaluteLavoro",
                auto_sync_templates_on_startup=True,
                auto_save_signed_documents=True,
                show_signature_text=True,
                signature_capture_mode="wacom",
            ),
        )
        self.assertEqual(controls[10].value, "Impostazioni salvate")

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
        controls = page.dialog.content.content.controls
        controls[4].value = "false"
        controls[5].value = "false"
        controls[8].value = "false"
        controls[9].controls[3].on_click(None)

        self.assertFalse(service.settings.auto_sync_templates_on_startup)
        self.assertFalse(service.settings.auto_save_signed_documents)
        self.assertFalse(service.settings.show_signature_text)
        self.assertEqual(service.settings.signature_capture_mode, "wacom")

    def test_general_preferences_checks_and_creates_supabase_table(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        view = MainView(page, general_preferences_service=service)
        view._admin_mode = True

        view.show_general_preferences()
        controls = page.dialog.content.content.controls
        controls[1].value = "https://demo.supabase.co"
        controls[2].value = "secret"
        controls[3].value = "Meddoc"
        controls[9].controls[1].on_click(None)

        self.assertEqual(
            controls[10].value,
            (
                "Tabella template Supabase 'Meddoc' non trovata. "
                "Premi 'Crea tabella' per duplicarla da SaluteLavoro."
            ),
        )

        controls[9].controls[2].on_click(None)

        self.assertEqual(service.created_table_settings.table_name, "Meddoc")
        self.assertEqual(
            controls[10].value,
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
        controls = page.dialog.content.content.controls

        labels = [getattr(control, "label", "") for control in controls]
        self.assertNotIn("URL progetto Supabase", labels)
        self.assertNotIn("Password/API key Supabase", labels)
        self.assertNotIn("Tabella template Supabase", labels)
        self.assertEqual([_button_label(button) for button in controls[5].controls], ["Salva"])

        controls[1].value = True
        controls[2].value = True
        controls[4].value = "wacom"
        controls[5].controls[0].on_click(None)

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
        connection_controls[1].value = "https://erp.example.test/users"
        connection_controls[2].value = "api-user"
        connection_controls[3].value = "api-secret"
        connection_controls[4].controls[1].on_click(None)

        users_list = users_controls[4]
        self.assertEqual(connection_controls[5].value, "Caricati 1 utenti")
        users_list.controls[0].controls[2].on_click(None)
        self.assertEqual(users_controls[1].content.value, "Mario Rossi (42)")

        self.assertEqual(
            service.erp_settings,
            ErpUserSettings(
                users_url="https://erp.example.test/users",
                basic_username="api-user",
                basic_password="api-secret",
                selected_user_id="42",
                selected_user_name="Mario Rossi",
            ),
        )
        self.assertEqual(view._active_user.value, "Utente: Mario Rossi")
        self.assertEqual(connection_controls[5].value, "Utente salvato: Mario Rossi")
        self.assertEqual(
            service.session_user_logs,
            [
                (
                    ErpUserSettings(
                        users_url="https://erp.example.test/users",
                        basic_username="api-user",
                        basic_password="api-secret",
                        selected_user_id="42",
                        selected_user_name="Mario Rossi",
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
        connection_controls[1].value = "https://erp.example.test/users"
        connection_controls[2].value = "api-user"
        connection_controls[3].value = "api-secret"
        connection_controls[4].controls[0].on_click(None)

        self.assertEqual(
            connection_controls[5].value,
            "Connessione utenti riuscita: 1 utenti disponibili",
        )
        self.assertEqual(users_controls[4].controls, [])

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
        self.assertNotIn("Utente Basic Auth", labels)
        self.assertNotIn("Password Basic Auth", labels)
        self.assertEqual(
            [_button_label(button) for button in connection_controls[2].controls],
            ["Carica utenti"],
        )

        connection_controls[2].controls[0].on_click(None)

        self.assertEqual(connection_controls[3].value, "Caricati 1 utenti")
        self.assertEqual(len(users_controls[4].controls), 1)

    def test_startup_user_confirmation_is_skipped_without_selected_user(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        view = MainView(page, general_preferences_service=service)

        shown = view.show_startup_user_confirmation()

        self.assertFalse(shown)
        self.assertFalse(hasattr(page, "dialog"))

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
        self.assertEqual(page.dialog.title.value, "Utenti")

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
        self.settings = SupabaseSettings()
        self.erp_settings = ErpUserSettings()
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
        return ErpUsersResult(
            True,
            "Caricati 1 utenti",
            (ErpUser("42", "Mario Rossi"),),
        )


class FakeTemplateSyncResult:
    def __init__(
        self,
        uploaded: int = 0,
        downloaded: int = 0,
        skipped: int = 0,
    ) -> None:
        self.uploaded = uploaded
        self.downloaded = downloaded
        self.skipped = skipped


class FakeTemplateSyncService:
    def __init__(self, template_dir: Path) -> None:
        self._template_dir = template_dir

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
