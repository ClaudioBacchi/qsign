"""Presentation tests for renderer output conversion."""

import base64
import unittest
from types import SimpleNamespace

from app.services.certificate_service import CertificateInfo
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
        page = FakePage()
        view = MainView(page)

        view.show_information()

        self.assertEqual(page.dialog.title.value, "QSign")

    def test_certificate_child_dialog_replaces_preferences_dialog(self) -> None:
        page = FakePage()
        view = MainView(page, certificate_service=FakeCertificateService())

        view.show_certificate_preferences()
        page.dialog.content.content.controls[3].controls[0].on_click(None)

        self.assertEqual(page.dialog.title.value, "Genera certificato")
        self.assertEqual(page.pop_count, 1)

    def test_certificate_delete_asks_for_confirmation(self) -> None:
        page = FakePage()
        service = FakeCertificateService()
        view = MainView(page, certificate_service=service)

        view.show_certificate_preferences()
        page.dialog.content.content.controls[3].controls[3].on_click(None)

        self.assertEqual(page.dialog.title.value, "Cancella certificato")
        page.dialog.actions[1].on_click(None)
        self.assertEqual(service.deleted_thumbprints, ["AABB"])


if __name__ == "__main__":
    unittest.main()


def _event(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(local_position=SimpleNamespace(x=x, y=y))


class FakeCertificateService:
    def __init__(self) -> None:
        self.deleted_thumbprints: list[str] = []

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
