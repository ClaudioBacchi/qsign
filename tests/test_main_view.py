"""Presentation tests for renderer output conversion."""

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.certificate_service import CertificateInfo, SignatureMetadata
from app.services.general_preferences_service import (
    SupabaseConnectionResult,
    SupabaseSettings,
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
            "images/logo_qsign_grande.png",
        )

        view._viewer_placeholder.on_tap(None)

        self.assertEqual(page.launched_urls, ["https://queensrl.net"])

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
            ["Generali", "Certificato"],
        )
        self.assertEqual(menu_bar.controls[1].controls[0].width, 180)
        self.assertEqual(menu_bar.controls[1].controls[1].width, 180)

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
            ],
        )

    def test_windows_title_bar_colors_use_colorref_format(self) -> None:
        self.assertEqual(MainView._windows_colorref_from_hex("1f3c98"), 0x00983C1F)
        self.assertEqual(MainView._windows_colorref_from_hex("#ffffff"), 0x00FFFFFF)

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
            table = page.dialog.content.content.controls[0]
            self.assertEqual(len(table.rows), 1)
            row = table.rows[0]
            name_button = row.cells[0].content
            self.assertEqual(name_button.content, "contratto_signed.pdf")
            self.assertRegex(row.cells[1].content.value, r"\d{2}/\d{2}/\d{4} ")
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
                page.dialog.content.content.value,
                "Nessun documento firmato trovato",
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
            table = page.dialog.content.content.controls[0].content.controls[0]
            self.assertEqual(len(table.rows), 1)
            self.assertEqual(table.rows[0].cells[0].content.value, "learned_privacy.json")
            self.assertRegex(table.rows[0].cells[1].content.value, r"\d{2}/\d{2}/\d{4} ")

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
                page.dialog.content.content.controls[0].content.value,
                "Nessun template documento trovato",
            )

            button_row = page.dialog.content.content.controls[1]
            button_row.controls[1].on_click(None)

            self.assertEqual(page.dialog.title.value, "Template Documenti")
            table = page.dialog.content.content.controls[0].content.controls[0]
            self.assertEqual(len(table.rows), 1)
            self.assertEqual(
                table.rows[0].cells[0].content.value,
                "learned_synced.json",
            )
            self.assertEqual(
                page.dialog.content.content.controls[2].value,
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
            self.assertEqual(controls[0].content.src, "images/logo_qsign_grande.png")
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
            self.assertEqual(footer.controls[1].src, "images/logo_queen_25anni.png")

            site_button.on_click(None)
            support_button.on_click(None)

            self.assertEqual(
                page.launched_urls,
                ["https://queensrl.net", "mailto:assistenza@qss.it"],
            )

    def test_certificate_child_dialog_replaces_preferences_dialog(self) -> None:
        page = FakePage()
        view = MainView(page, certificate_service=FakeCertificateService())

        view.show_certificate_preferences()
        page.dialog.content.content.controls[6].controls[0].on_click(None)

        self.assertEqual(page.dialog.title.value, "Genera certificato")
        self.assertEqual(page.pop_count, 1)

    def test_certificate_delete_asks_for_confirmation(self) -> None:
        page = FakePage()
        service = FakeCertificateService()
        view = MainView(page, certificate_service=service)

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

    def test_general_preferences_save_and_test_supabase_settings(self) -> None:
        page = FakePage()
        service = FakeGeneralPreferencesService()
        view = MainView(page, general_preferences_service=service)

        view.show_general_preferences()
        controls = page.dialog.content.content.controls
        controls[1].value = "https://demo.supabase.co"
        controls[2].value = "secret"
        controls[3].value = "SaluteLavoro"
        controls[4].value = True
        controls[5].controls[0].on_click(None)

        self.assertEqual(
            controls[6].value,
            "Connessione Supabase riuscita",
        )
        controls[5].controls[1].on_click(None)

        self.assertEqual(
            service.settings,
            SupabaseSettings(
                project_url="https://demo.supabase.co",
                password="secret",
                table_name="SaluteLavoro",
                auto_sync_templates_on_startup=True,
            ),
        )
        self.assertEqual(controls[6].value, "Impostazioni salvate")

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

    def test_generate_certificate_dialog_saves_signature_reason_and_updates_status(
        self,
    ) -> None:
        page = FakePage()
        service = FakeCertificateService()
        view = MainView(page, certificate_service=service)

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


if __name__ == "__main__":
    unittest.main()


def _event(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(local_position=SimpleNamespace(x=x, y=y))


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

    def get_supabase_settings(self) -> SupabaseSettings:
        return self.settings

    def save_supabase_settings(self, settings: SupabaseSettings) -> None:
        self.settings = settings

    def test_supabase_connection(
        self, settings: SupabaseSettings | None = None
    ) -> SupabaseConnectionResult:
        return SupabaseConnectionResult(True, "Connessione Supabase riuscita")


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
