"""Tests for document navigation state without loading Flet."""

import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from app.services.erp_document_context import ErpSignedDocumentUploadContext
from app.services.general_preferences_service import ErpUserSettings, SupabaseSettings
from app.pdf_viewer_controller import AnchorOverlay, PDFViewerController
from models.document import Document, Metadata, Page, Rectangle, TextBlock, Word
from models.pdf_document import PDFDocument
from models.pdf_document import PageSize
from models.template import (
    AnchorRule,
    PlacementRule,
    RecognitionRule,
    Template,
    TemplateSettings,
    TemplateState,
)
from services.anchors.anchor_detector import AnchorDetector
from services.anchors.anchor_models import AnchorMatch
from services.logging.logging_service import LoggingService
from services.pdf.pdf_renderer import RenderedPage
from services.pdf.pdf_service import PDFService
from services.signature.signature_service import CapturedSignature


class FakeViewer:
    def __init__(self) -> None:
        self.pages: list[
            tuple[int, int, float, bytes, tuple[AnchorOverlay, ...], int]
        ] = []
        self.cleared = False
        self.errors: list[str] = []
        self.statuses: list[str] = []
        self.flow_events: list[tuple[str, str]] = []
        self.manual_mode = False
        self.save_callback = None
        self.cancel_save_callback = None
        self.open_signature_dialog_called = False
        self.signature_dialog_canvas_size: tuple[float | None, float | None] | None = None
        self.defer_signature_capture_count = 0
        self.defer_viewer_refresh_count = 0
        self.discard_callback = None
        self.cancel_discard_callback = None

    def display_document(
        self,
        filename: str,
        image_content: bytes,
        image_width: int,
        image_height: int,
        page_number: int,
        page_count: int,
        zoom: float,
        anchor_overlays: tuple[AnchorOverlay, ...] = (),
        anchor_count: int = 0,
        selected_anchor: AnchorMatch | None = None,
        workflow_status: str = "",
    ) -> None:
        self.pages.append(
            (
                page_number,
                page_count,
                zoom,
                image_content,
                anchor_overlays,
                anchor_count,
            )
        )

    def clear_document(self) -> None:
        self.cleared = True

    def show_error(self, message: str) -> None:
        self.errors.append(message)

    def show_status(self, message: str) -> None:
        self.statuses.append(message)

    def show_document_flow_downloaded(self, document_name: str) -> None:
        self.flow_events.append(("Scaricato", document_name))

    def show_document_flow_signed(self, document_name: str) -> None:
        self.flow_events.append(("Firmato", document_name))

    def show_document_flow_uploaded(self, document_name: str) -> None:
        self.flow_events.append(("Caricato", document_name))

    def show_document_flow_upload_failed(self, document_name: str) -> None:
        self.flow_events.append(("Errore invio", document_name))

    def set_manual_signature_mode(self, enabled: bool) -> None:
        self.manual_mode = enabled

    def ask_save_template(self, on_confirm, on_cancel) -> None:
        self.save_callback = on_confirm
        self.cancel_save_callback = on_cancel

    def ask_discard_signed_document(self, on_confirm, on_cancel) -> None:
        self.discard_callback = on_confirm
        self.cancel_discard_callback = on_cancel

    def open_signature_dialog(
        self,
        on_confirm,
        on_clear,
        on_cancel,
        *,
        canvas_width=None,
        canvas_height=None,
    ) -> None:
        self.open_signature_dialog_called = True
        self.signature_dialog_canvas_size = (canvas_width, canvas_height)
        on_confirm(
            CapturedSignature(
                content=b"<svg><polyline points='1,1 2,2'/></svg>",
                media_type="image/svg+xml",
            )
        )

    def defer_signature_capture(self, callback) -> None:
        self.defer_signature_capture_count += 1
        callback()

    def defer_viewer_refresh(self, callback) -> None:
        self.defer_viewer_refresh_count += 1
        callback()

    def run_background_task(self, callback) -> None:
        callback()

    def run_ui_task(self, callback) -> None:
        callback()


class DeferredBackgroundViewer(FakeViewer):
    def __init__(self) -> None:
        super().__init__()
        self.background_tasks = []
        self.ui_tasks = []

    def run_background_task(self, callback) -> None:
        self.background_tasks.append(callback)

    def run_ui_task(self, callback) -> None:
        self.ui_tasks.append(callback)


class FakeSignatureProvider:
    def __init__(
        self,
        signature: CapturedSignature,
        on_capture=None,
    ) -> None:
        self.signature = signature
        self.capture_count = 0
        self.on_capture = on_capture

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def capture_signature(self) -> CapturedSignature:
        self.capture_count += 1
        if self.on_capture is not None:
            self.on_capture()
        return self.signature


class CancellableSignatureProvider(FakeSignatureProvider):
    def __init__(self, signature: CapturedSignature) -> None:
        super().__init__(signature)
        self.cancel_count = 0
        self.cancelled = False

    def cancel_signature_capture(self) -> None:
        self.cancel_count += 1
        self.cancelled = True

    def capture_signature(self) -> CapturedSignature:
        self.capture_count += 1
        if self.cancelled:
            raise RuntimeError("Firma annullata")
        return self.signature


class FailingSignatureProvider:
    def __init__(self) -> None:
        self.capture_count = 0

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def capture_signature(self) -> CapturedSignature:
        self.capture_count += 1
        raise RuntimeError("tablet unavailable")


class FakeTemplateSyncResult:
    def __init__(self, uploaded: int = 0, conflicts: tuple[object, ...] = ()) -> None:
        self.uploaded = uploaded
        self.conflicts = conflicts


class FakeTemplateSyncConflict:
    def __init__(self, template_id: str) -> None:
        self.template_id = template_id


class FakeTemplateSyncService:
    def __init__(self, result: FakeTemplateSyncResult | None = None) -> None:
        self.result = result or FakeTemplateSyncResult(uploaded=1)
        self.uploaded_paths: list[Path] = []

    def upload_template(self, path: Path) -> FakeTemplateSyncResult:
        self.uploaded_paths.append(path)
        return self.result


class PDFViewerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = PDFDocument(
            path=Path("sample.pdf"),
            filename="sample.pdf",
            page_count=2,
            page_sizes=(PageSize(200, 200), PageSize(200, 200)),
            loaded=True,
        )
        self.service = MagicMock(spec=PDFService)
        self.service.open_document.return_value = self.document
        self.service.current_document = self.document
        self.service.render_page.side_effect = (
            lambda page, zoom: RenderedPage(
                content=f"{page}:{zoom}".encode(),
                width=int(200 * zoom),
                height=int(200 * zoom),
                media_type="image/png",
            )
        )
        self.view = FakeViewer()
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller"),
        )

    def test_change_page_renders_the_next_page(self) -> None:
        self.controller.open_document("sample.pdf")

        self.controller.next_page()

        self.assertEqual(self.controller.state.page_index, 1)
        self.assertEqual(self.view.pages[-1][0], 2)
        self.service.render_page.assert_called_with(1, 1.0)

    def test_previous_page_does_not_move_before_first_page(self) -> None:
        self.controller.open_document("sample.pdf")

        self.controller.previous_page()

        self.assertEqual(self.controller.state.page_index, 0)
        self.assertEqual(self.service.render_page.call_count, 1)

    def test_zoom_changes_render_scale(self) -> None:
        self.controller.open_document("sample.pdf")

        self.controller.zoom_in()

        self.assertEqual(self.controller.state.zoom, 1.25)
        self.service.render_page.assert_called_with(0, 1.25)

    def test_open_document_analyzes_pdf_and_displays_anchor_overlay(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.anchors"),
            pdf_provider=FakePDFProvider(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.anchor_detector")
            ),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(self.view.pages[-1][5], 1)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 22.5)
        self.assertEqual(overlays[0].top, 158)
        self.assertEqual(overlays[0].width, 110)
        self.assertEqual(overlays[0].height, 40)
        self.assertEqual(overlays[0].label, "Zona firma")
        self.assertTrue(self.view.manual_mode)

    def test_zoom_scales_anchor_overlay_with_rendered_image(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.zoom_anchors"),
            pdf_provider=FakePDFProvider(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.zoom_detector")
            ),
        )

        self.controller.open_document("sample.pdf")
        self.controller.zoom_in()

        overlays = self.view.pages[-1][4]
        self.assertEqual(self.controller.state.zoom, 1.25)
        self.assertEqual(overlays[0].left, 28.125)
        self.assertEqual(overlays[0].top, 197.5)
        self.assertEqual(overlays[0].width, 137.5)
        self.assertEqual(overlays[0].height, 50)

    def test_change_page_hides_anchor_from_other_page(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.page_anchors"),
            pdf_provider=FakePDFProvider(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.page_detector")
            ),
        )

        self.controller.open_document("sample.pdf")
        self.controller.next_page()

        self.assertEqual(self.view.pages[-1][0], 2)
        self.assertEqual(self.view.pages[-1][4], ())

    def test_demo_anchor_ignores_signed_text_and_uses_worker_acknowledgement(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.worker_ack"),
            pdf_provider=FakePDFProviderWorkerAcknowledgement(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.worker_ack_detector")
            ),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(self.controller.state.page_index, 1)
        self.assertEqual(self.view.pages[-1][0], 2)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 55)
        self.assertEqual(overlays[0].top, 90)
        self.assertEqual(overlays[0].width, 145)
        self.assertEqual(overlays[0].height, 45)

    def test_open_document_focuses_detected_signature_page(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.focus_signature"),
            pdf_provider=FakePDFProviderWorkerAcknowledgement(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.focus_signature_detector")
            ),
        )

        self.controller.open_document("sample.pdf")

        self.assertEqual(self.controller.state.page_index, 1)
        self.service.render_page.assert_called_with(1, 1.0)
        self.assertEqual(self.view.pages[-1][0], 2)

    def test_repeated_anchor_uses_page_with_clear_signature_space(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.repeated_worker_ack"),
            pdf_provider=FakePDFProviderRepeatedWorkerAcknowledgement(),
            anchor_detector=AnchorDetector(
                LoggingService.create(
                    "qsign.tests.controller.repeated_worker_ack_detector"
                )
            ),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(self.controller.state.page_index, 1)
        self.service.render_page.assert_called_with(1, 1.0)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].top, 90)

    def test_unknown_document_enables_manual_signature_rectangle_and_saves_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.manual"),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.manual_detector")
                ),
                template_root=directory,
            )

            self.controller.open_document("sample.pdf")

            self.assertTrue(self.view.manual_mode)
            self.controller.set_manual_signature_rectangle(
                left=20,
                top=30,
                width=80,
                height=40,
                image_width=200,
                image_height=200,
            )

            self.assertFalse(self.view.manual_mode)
            self.assertIsNotNone(self.view.save_callback)
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            self.assertEqual(len(saved_templates), 1)
            self.assertIn(
                "manual_signature_flow",
                saved_templates[0].read_text(encoding="utf-8"),
            )
            self.assertIn('"page_index": 0', saved_templates[0].read_text(encoding="utf-8"))

    def test_saving_manual_template_updates_stable_learned_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.stable_save"),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.stable_save_detector")
                ),
                template_root=directory,
            )

            self.controller.open_document("sample.pdf")
            self.controller.set_manual_signature_rectangle(
                left=20,
                top=30,
                width=80,
                height=40,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()
            self.controller.set_manual_signature_rectangle(
                left=25,
                top=35,
                width=90,
                height=45,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            self.assertEqual(len(saved_templates), 1)
            content = saved_templates[0].read_text(encoding="utf-8")
            self.assertIn('"x_offset": 25.0', content)
            self.assertIn('"y_offset": 35.0', content)
            self.assertIn('"width": 90.0', content)
            self.assertIn('"height": 45.0', content)

    def test_saving_manual_template_uploads_it_to_supabase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sync_service = FakeTemplateSyncService()
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.template_upload"),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.template_upload_detector")
                ),
                template_sync_service=sync_service,
                template_root=directory,
            )

            self.controller.open_document("sample.pdf")
            self.controller.set_manual_signature_rectangle(
                left=20,
                top=30,
                width=80,
                height=40,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            self.assertEqual(len(sync_service.uploaded_paths), 1)
            self.assertTrue(sync_service.uploaded_paths[0].name.startswith("learned_"))
            self.assertEqual(
                self.view.statuses[-1],
                f"Template sincronizzato su Supabase: {sync_service.uploaded_paths[0].name}",
            )

    def test_saving_manual_template_reports_supabase_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sync_service = FakeTemplateSyncService(
                FakeTemplateSyncResult(
                    conflicts=(FakeTemplateSyncConflict("learned_sample.json"),)
                )
            )
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.template_conflict"),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.template_conflict_detector")
                ),
                template_sync_service=sync_service,
                template_root=directory,
            )

            self.controller.open_document("sample.pdf")
            self.controller.set_manual_signature_rectangle(
                left=20,
                top=30,
                width=80,
                height=40,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            self.assertEqual(
                self.view.errors[-1],
                "Conflitto template Supabase: learned_sample.json. "
                "Upload automatico saltato.",
            )

    def test_manual_template_saves_structural_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.structural_save"),
                pdf_provider=FakePDFProviderScreeningDocument(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.structural_save_detector")
                ),
                template_root=directory,
            )

            self.controller.open_document("screening.pdf")
            self.controller.set_manual_signature_rectangle(
                left=20,
                top=30,
                width=80,
                height=40,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            content = saved_templates[0].read_text(encoding="utf-8")
            self.assertIn('"rule_id": "manual-structural-signature"', content)
            self.assertIn("prevenzione oncologica", content)
            self.assertIn("screening", saved_templates[0].name)

    def test_legacy_manual_template_without_structural_signature_requires_filename_match(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.legacy_header_only"),
            pdf_provider=FakePDFProviderHeaderLikeDocument(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.legacy_header_only_detector")
            ),
            template_repository=FakeLegacyHeaderOnlyTemplateRepository(),
        )
        self.view.open_signature_dialog_called = False

        self.controller.open_document("different.pdf")

        self.assertTrue(self.view.manual_mode)
        self.assertEqual(self.view.pages[-1][4], ())

    def test_structural_template_tolerates_partial_signature_match(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.partial_structural"),
            pdf_provider=FakePDFProviderPartialStructuralDocument(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.partial_structural_detector")
            ),
            template_repository=FakePartialStructuralTemplateRepository(),
        )

        self.controller.open_document("portal.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 33)
        self.assertEqual(overlays[0].top, 44)

    def test_manual_signature_rectangle_saves_current_page_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.manual_page"),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.manual_page_detector")
                ),
                template_root=directory,
            )

            self.controller.open_document("sample.pdf")
            self.controller.state.page_index = 1
            self.controller.set_manual_signature_rectangle(
                left=20,
                top=30,
                width=80,
                height=40,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            self.assertEqual(len(saved_templates), 1)
            self.assertIn('"page_index": 1', saved_templates[0].read_text(encoding="utf-8"))

    def test_manual_template_saves_relative_anchor_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.relative_save"),
                pdf_provider=FakePDFProvider(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.relative_save_detector")
                ),
                template_root=directory,
            )

            self.controller.open_document("sample.pdf")
            self.controller.set_manual_signature_rectangle(
                left=60,
                top=158,
                width=80,
                height=30,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            content = saved_templates[0].read_text(encoding="utf-8")
            self.assertIn('"anchor_rules": [', content)
            self.assertIn("\"expression\": \"In fede L'interessato\"", content)
            self.assertIn('"placement_id": "relative-signature"', content)
            self.assertIn('"placement_id": "manual-signature"', content)

    def test_manual_correction_saves_selected_anchor_with_corrected_box(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create(
                    "qsign.tests.controller.corrected_relative_save"
                ),
                pdf_provider=FakePDFProviderWorkerAcknowledgement(),
                anchor_detector=AnchorDetector(
                    LoggingService.create(
                        "qsign.tests.controller.corrected_relative_save_detector"
                    )
                ),
                template_root=directory,
            )

            self.controller.open_document("sample.pdf")
            self.controller.set_manual_signature_rectangle(
                left=40,
                top=120,
                width=160,
                height=60,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            content = saved_templates[0].read_text(encoding="utf-8")
            self.assertIn('"expression": "Il lavoratore per presa visione"', content)
            self.assertIn('"x_offset": -40.0', content)
            self.assertIn('"y_offset": 50.0', content)
            self.assertIn('"width": 160.0', content)
            self.assertIn('"height": 60.0', content)
            self.assertIn('"page_index": 1', content)

    def test_relative_template_without_fixed_page_follows_best_anchor_occurrence(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.relative_no_page"),
            pdf_provider=FakePDFProviderRepeatedWorkerAcknowledgement(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.relative_no_page_detector")
            ),
            template_repository=FakeRelativeTemplateWithoutFixedPageRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(self.controller.state.page_index, 1)
        self.assertEqual(self.view.pages[-1][0], 2)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 40)
        self.assertEqual(overlays[0].top, 120)

    def test_recognized_template_prepares_signature_rectangle(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.template"),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.template_detector")
            ),
            template_repository=FakeTemplateRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 20)
        self.assertEqual(overlays[0].top, 30)
        self.assertEqual(overlays[0].width, 80)
        self.assertEqual(overlays[0].height, 40)
        self.assertTrue(self.view.manual_mode)

    def test_cancelling_manual_correction_restores_recognized_template_box(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create(
                "qsign.tests.controller.cancel_manual_correction"
            ),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create(
                    "qsign.tests.controller.cancel_manual_correction_detector"
                )
            ),
            template_repository=FakeTemplateRepository(),
        )

        self.controller.open_document("sample.pdf")
        self.controller.set_manual_signature_rectangle(
            left=70,
            top=80,
            width=90,
            height=50,
            image_width=200,
            image_height=200,
        )

        self.assertIsNotNone(self.view.cancel_save_callback)
        self.view.cancel_save_callback()

        overlays = self.view.pages[-1][4]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 20)
        self.assertEqual(overlays[0].top, 30)
        self.assertEqual(overlays[0].width, 80)
        self.assertEqual(overlays[0].height, 40)
        self.assertTrue(self.view.manual_mode)

    def test_save_signed_pdf_requires_captured_signature(self) -> None:
        self.controller.open_document("sample.pdf")
        self.controller.set_manual_signature_rectangle(
            left=20,
            top=30,
            width=80,
            height=40,
            image_width=200,
            image_height=200,
        )

        self.controller.save_signed_pdf()

        self.assertEqual(self.view.errors[-1], "Nessuna firma acquisita")

    def test_auto_save_failure_keeps_signature_visible_and_unsaved(self) -> None:
        self.service.save_signed_preview.side_effect = RuntimeError(
            "firma digitale non completata"
        )
        controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.save_failure"),
            general_preferences_service=FakeGeneralPreferencesService(
                auto_save_signed_documents=True
            ),
        )
        controller.open_document("sample.pdf")
        controller.set_manual_signature_rectangle(20, 30, 80, 40, 200, 200)
        signature = CapturedSignature(
            content=b"<svg><polyline points='1,1 2,2'/></svg>",
            media_type="image/svg+xml",
        )

        controller.apply_mouse_signature(signature)

        self.assertEqual(self.view.errors[-1], "firma digitale non completata")
        self.assertTrue(controller.has_unsaved_signed_document())
        self.assertFalse(self.view.cleared)
        overlay = self.view.pages[-1][4][0]
        self.assertEqual(overlay.signature_content, signature.content)

    def test_open_signature_dialog_uses_wacom_provider_when_available(self) -> None:
        signature = CapturedSignature(
            content=b"<svg><polyline points='3,3 4,4'/></svg>",
            media_type="image/svg+xml",
        )
        provider = FakeSignatureProvider(signature)
        controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.wacom"),
            general_preferences_service=FakeGeneralPreferencesService(
                signature_capture_mode="wacom"
            ),
            signature_provider=provider,
        )
        controller.open_document("sample.pdf")
        controller.set_manual_signature_rectangle(10, 10, 50, 30, 200, 200)
        self.view.save_callback()

        controller.open_signature_dialog()

        self.assertFalse(self.view.open_signature_dialog_called)
        self.assertEqual(provider.capture_count, 1)
        saved_overlay = self.view.pages[-1][4][0]
        self.assertEqual(saved_overlay.signature_content, signature.content)
        self.assertEqual(self.view.defer_viewer_refresh_count, 1)

    def test_open_signature_dialog_reports_wacom_error_without_mouse_fallback(self) -> None:
        provider = FailingSignatureProvider()
        view = DeferredBackgroundViewer()
        controller = PDFViewerController(
            pdf_service=self.service,
            view=view,
            logger=LoggingService.create("qsign.tests.controller.wacom_fallback"),
            general_preferences_service=FakeGeneralPreferencesService(
                signature_capture_mode="wacom"
            ),
            signature_provider=provider,
        )
        controller.open_document("sample.pdf")
        controller.set_manual_signature_rectangle(10, 10, 50, 30, 200, 200)

        controller.open_signature_dialog()
        view.background_tasks[0]()

        self.assertFalse(view.open_signature_dialog_called)
        self.assertEqual(provider.capture_count, 1)
        self.assertEqual(view.errors, [])
        view.ui_tasks[0]()
        self.assertEqual(
            view.errors[-1],
            "Firma Wacom non disponibile: tablet unavailable",
        )
        saved_overlay = view.pages[-1][4][0]
        self.assertIsNone(saved_overlay.signature_content)
        self.service.save_signed_preview.assert_not_called()

    def test_open_signature_dialog_reports_missing_wacom_provider_without_mouse_fallback(self) -> None:
        controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.wacom_missing"),
            general_preferences_service=FakeGeneralPreferencesService(
                signature_capture_mode="wacom"
            ),
            signature_provider=None,
        )
        controller.open_document("sample.pdf")
        controller.set_manual_signature_rectangle(10, 10, 50, 30, 200, 200)
        self.view.save_callback()

        controller.open_signature_dialog()

        self.assertFalse(self.view.open_signature_dialog_called)
        self.assertEqual(self.view.errors[-1], "Firma Wacom non disponibile")
        saved_overlay = self.view.pages[-1][4][0]
        self.assertIsNone(saved_overlay.signature_content)

    def test_open_document_starts_wacom_when_signature_box_is_ready(self) -> None:
        signature = CapturedSignature(
            content=b"<svg><polyline points='3,3 4,4'/></svg>",
            media_type="image/svg+xml",
        )
        provider = FakeSignatureProvider(signature)
        controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.wacom_auto"),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.wacom_auto_detector")
            ),
            template_repository=FakeTemplateRepository(),
            general_preferences_service=FakeGeneralPreferencesService(
                signature_capture_mode="wacom"
            ),
            signature_provider=provider,
        )

        controller.open_document("sample.pdf")

        self.assertEqual(self.view.defer_signature_capture_count, 1)
        self.assertEqual(provider.capture_count, 1)
        self.assertFalse(self.view.open_signature_dialog_called)
        saved_overlay = self.view.pages[-1][4][0]
        self.assertEqual(saved_overlay.signature_content, signature.content)

    def test_manual_unknown_document_save_starts_wacom_when_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signature = CapturedSignature(
                content=b"<svg><polyline points='3,3 4,4'/></svg>",
                media_type="image/svg+xml",
            )
            provider = FakeSignatureProvider(signature)
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.wacom_manual_save"),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create(
                        "qsign.tests.controller.wacom_manual_save_detector"
                    )
                ),
                general_preferences_service=FakeGeneralPreferencesService(
                    signature_capture_mode="wacom"
                ),
                signature_provider=provider,
                template_root=directory,
            )

            controller.open_document("sample.pdf")
            controller.set_manual_signature_rectangle(10, 10, 50, 30, 200, 200)

            self.assertEqual(provider.capture_count, 0)

            self.view.save_callback()

            self.assertEqual(provider.capture_count, 1)
            saved_overlay = self.view.pages[-1][4][0]
            self.assertEqual(saved_overlay.signature_content, signature.content)

    def test_manual_correction_of_recognized_box_refreshes_wacom_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signature = CapturedSignature(
                content=b"<svg><polyline points='3,3 4,4'/></svg>",
                media_type="image/svg+xml",
            )
            view = DeferredBackgroundViewer()
            provider = FakeSignatureProvider(signature)
            controller = PDFViewerController(
                pdf_service=self.service,
                view=view,
                logger=LoggingService.create(
                    "qsign.tests.controller.wacom_manual_correction"
                ),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create(
                        "qsign.tests.controller.wacom_manual_correction_detector"
                    )
                ),
                template_repository=FakeTemplateRepository(),
                general_preferences_service=FakeGeneralPreferencesService(
                    signature_capture_mode="wacom"
                ),
                signature_provider=provider,
                template_root=directory,
            )

            controller.open_document("sample.pdf")
            self.assertEqual(len(view.background_tasks), 1)

            controller.set_manual_signature_rectangle(70, 80, 90, 50, 200, 200)
            view.background_tasks[0]()

            self.assertIsNone(view.pages[-1][4][0].signature_content)

            view.save_callback()
            view.background_tasks[1]()
            view.ui_tasks[0]()

            saved_overlay = view.pages[-1][4][0]
            self.assertEqual(saved_overlay.left, 70)
            self.assertEqual(saved_overlay.top, 80)
            self.assertEqual(saved_overlay.width, 90)
            self.assertEqual(saved_overlay.height, 50)
            self.assertEqual(saved_overlay.signature_content, signature.content)

    def test_manual_unknown_document_without_template_save_starts_wacom_when_selected(self) -> None:
        signature = CapturedSignature(
            content=b"<svg><polyline points='3,3 4,4'/></svg>",
            media_type="image/svg+xml",
        )
        provider = FakeSignatureProvider(signature)
        controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.wacom_manual_no_save"),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create(
                    "qsign.tests.controller.wacom_manual_no_save_detector"
                )
            ),
            general_preferences_service=FakeGeneralPreferencesService(
                signature_capture_mode="wacom"
            ),
            signature_provider=provider,
        )

        controller.open_document("sample.pdf")
        controller.set_manual_signature_rectangle(10, 10, 50, 30, 200, 200)
        self.view.cancel_save_callback()

        self.assertEqual(provider.capture_count, 1)
        saved_overlay = self.view.pages[-1][4][0]
        self.assertEqual(saved_overlay.signature_content, signature.content)

    def test_open_document_renders_pdf_before_deferred_wacom_capture(self) -> None:
        signature = CapturedSignature(
            content=b"<svg><polyline points='3,3 4,4'/></svg>",
            media_type="image/svg+xml",
        )
        pages_before_capture: list[int] = []
        provider = FakeSignatureProvider(
            signature,
            on_capture=lambda: pages_before_capture.append(len(self.view.pages)),
        )
        controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.wacom_render_first"),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create(
                    "qsign.tests.controller.wacom_render_first_detector"
                )
            ),
            template_repository=FakeTemplateRepository(),
            general_preferences_service=FakeGeneralPreferencesService(
                signature_capture_mode="wacom"
            ),
            signature_provider=provider,
        )

        controller.open_document("sample.pdf")

        self.assertGreaterEqual(pages_before_capture[0], 1)

    def test_close_document_cancels_pending_wacom_capture(self) -> None:
        signature = CapturedSignature(
            content=b"<svg><polyline points='3,3 4,4'/></svg>",
            media_type="image/svg+xml",
        )
        view = DeferredBackgroundViewer()
        provider = CancellableSignatureProvider(signature)
        controller = PDFViewerController(
            pdf_service=self.service,
            view=view,
            logger=LoggingService.create("qsign.tests.controller.wacom_cancel"),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.wacom_cancel_detector")
            ),
            template_repository=FakeTemplateRepository(),
            general_preferences_service=FakeGeneralPreferencesService(
                signature_capture_mode="wacom"
            ),
            signature_provider=provider,
        )

        controller.open_document("sample.pdf")
        controller.close_document()
        view.background_tasks[0]()

        self.assertEqual(provider.cancel_count, 1)
        self.assertEqual(provider.capture_count, 1)
        self.assertTrue(view.cleared)
        self.assertEqual(controller.state.page_count, 0)
        self.assertEqual(view.errors, [])
        self.service.save_signed_preview.assert_not_called()

    def test_completed_wacom_capture_refreshes_viewer_on_ui_task(self) -> None:
        signature = CapturedSignature(
            content=b"<svg><polyline points='3,3 4,4'/></svg>",
            media_type="image/svg+xml",
        )
        view = DeferredBackgroundViewer()
        provider = FakeSignatureProvider(signature)
        controller = PDFViewerController(
            pdf_service=self.service,
            view=view,
            logger=LoggingService.create("qsign.tests.controller.wacom_ui_refresh"),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create(
                    "qsign.tests.controller.wacom_ui_refresh_detector"
                )
            ),
            template_repository=FakeTemplateRepository(),
            general_preferences_service=FakeGeneralPreferencesService(
                signature_capture_mode="wacom"
            ),
            signature_provider=provider,
        )

        controller.open_document("sample.pdf")
        view.background_tasks[0]()

        self.assertEqual(len(view.ui_tasks), 1)
        self.assertIsNone(view.pages[-1][4][0].signature_content)

        view.ui_tasks[0]()

        self.assertEqual(view.pages[-1][4][0].signature_content, signature.content)
        self.assertEqual(view.defer_viewer_refresh_count, 1)
        self.service.save_signed_preview.assert_not_called()

    def test_open_signature_dialog_uses_mouse_when_preference_is_mouse(self) -> None:
        signature = CapturedSignature(
            content=b"<svg><polyline points='3,3 4,4'/></svg>",
            media_type="image/svg+xml",
        )
        controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.mouse_preference"),
            general_preferences_service=FakeGeneralPreferencesService(
                signature_capture_mode="mouse"
            ),
            signature_provider=FakeSignatureProvider(signature),
        )
        controller.open_document("sample.pdf")
        controller.set_manual_signature_rectangle(10, 10, 50, 30, 200, 200)
        self.view.save_callback()

        controller.open_signature_dialog()

        self.assertTrue(self.view.open_signature_dialog_called)
        saved_overlay = self.view.pages[-1][4][0]
        self.assertEqual(
            saved_overlay.signature_content,
            b"<svg><polyline points='1,1 2,2'/></svg>",
        )

    def test_mouse_signature_dialog_matches_signature_rectangle_aspect(self) -> None:
        controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.mouse_aspect"),
        )
        controller.open_document("sample.pdf")
        controller.set_manual_signature_rectangle(10, 10, 140, 40, 200, 200)
        self.view.save_callback()

        controller.open_signature_dialog()

        self.assertEqual(self.view.signature_dialog_canvas_size, (420.0, 120.0))

    def test_save_signed_pdf_uses_current_signature_rectangle(self) -> None:
        self.service.save_signed_preview.return_value = Path("dist/signed/sample_signed.pdf")
        self.controller.open_document("sample.pdf")
        self.controller.set_manual_signature_rectangle(
            left=20,
            top=30,
            width=80,
            height=40,
            image_width=200,
            image_height=200,
        )
        signature = CapturedSignature(
            content=b"<svg><polyline points='1,1 2,2'/></svg>",
            media_type="image/svg+xml",
        )
        self.controller.apply_mouse_signature(signature)

        self.controller.save_signed_pdf()

        saved_signature, area = self.service.save_signed_preview.call_args.args
        self.assertEqual(saved_signature, signature)
        self.assertEqual(area.page_index, 0)
        self.assertEqual(area.x, 20)
        self.assertEqual(area.y, 30)
        self.assertEqual(area.width, 80)
        self.assertEqual(area.height, 40)
        self.service.close_document.assert_called_once()
        self.assertTrue(self.view.cleared)
        self.assertIn("PDF firmato salvato", self.view.statuses[-1])

    def test_save_signed_pdf_uploads_signed_file_to_erp_when_context_is_available(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_path = Path(directory) / "sample_signed.pdf"
            signed_path.write_bytes(b"%PDF-signed-content")
            self.service.save_signed_preview.return_value = signed_path
            dms_client = FakeInfinityDmsClient()
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.erp_upload"),
                general_preferences_service=FakeGeneralPreferencesService(
                    erp_settings=ErpUserSettings(
                        document_service_url="https://erp.example.test/soap",
                        company_id="SALAV",
                        basic_username="api-user",
                        basic_password="api-secret",
                    )
                ),
                infinity_dms_client=dms_client,
            )
            controller.open_document(
                "sample.pdf",
                ErpSignedDocumentUploadContext(
                    document_id="DOC-1",
                    logical_dir="//Dipendenti/Idoneita/",
                    logical_name="sample_signed.pdf",
                ),
            )
            controller.set_manual_signature_rectangle(20, 30, 80, 40, 200, 200)
            controller.apply_mouse_signature(
                CapturedSignature(
                    content=b"<svg><polyline points='1,1 2,2'/></svg>",
                    media_type="image/svg+xml",
                )
            )

            controller.save_signed_pdf()

            self.assertEqual(len(dms_client.uploads), 1)
            upload = dms_client.uploads[0]
            self.assertEqual(upload["service_url"], "https://erp.example.test/soap")
            self.assertEqual(upload["credentials"].username, "api-user")
            self.assertEqual(upload["credentials"].password, "api-secret")
            self.assertEqual(upload["credentials"].company_id, "SALAV")
            self.assertEqual(upload["content"], b"%PDF-signed-content")
            self.assertEqual(upload["logical_dir"], "//Dipendenti/Idoneita/")
            self.assertEqual(upload["logical_name"], "sample_signed.pdf")
            self.assertIn("PDF firmato inviato all'ERP", self.view.statuses)
            self.assertIn(("Firmato", "sample_signed.pdf"), self.view.flow_events)
            self.assertIn(("Caricato", "sample_signed.pdf"), self.view.flow_events)
            self.assertFalse(
                PDFViewerController._erp_upload_sidecar(signed_path).exists()
            )

    def test_erp_signed_upload_retries_transient_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_path = Path(directory) / "sample_signed.pdf"
            signed_path.write_bytes(b"%PDF-signed-content")
            self.service.save_signed_preview.return_value = signed_path
            dms_client = FakeInfinityDmsClient(
                [RuntimeError("rete temporaneamente non disponibile"), "0"]
            )
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.erp_retry"),
                general_preferences_service=FakeGeneralPreferencesService(
                    erp_settings=ErpUserSettings(
                        document_service_url="https://erp.example.test/soap",
                        company_id="SALAV",
                        basic_username="api-user",
                        basic_password="api-secret",
                    )
                ),
                infinity_dms_client=dms_client,
            )
            controller.open_document(
                "sample.pdf",
                ErpSignedDocumentUploadContext(
                    document_id="DOC-1",
                    logical_dir="//Dipendenti/Idoneita/",
                    logical_name="sample_signed.pdf",
                ),
            )
            controller.set_manual_signature_rectangle(20, 30, 80, 40, 200, 200)
            controller.apply_mouse_signature(
                CapturedSignature(
                    content=b"<svg><polyline points='1,1 2,2'/></svg>",
                    media_type="image/svg+xml",
                )
            )

            controller.save_signed_pdf()

            self.assertEqual(len(dms_client.uploads), 2)
            self.assertEqual(self.view.statuses[-1], "PDF firmato inviato all'ERP")
            self.assertIn(("Caricato", "sample_signed.pdf"), self.view.flow_events)
            self.assertEqual(self.view.errors, [])

    def test_document_flow_keeps_original_erp_name_for_unique_signed_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_path = (
                Path(directory)
                / "89579d0be4304ebb9f91f08d81a611ed_privacy_TEST_4_.pdf"
            )
            signed_path.write_bytes(b"%PDF-signed-content")
            self.service.save_signed_preview.return_value = signed_path
            dms_client = FakeInfinityDmsClient()
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.erp_flow_name"),
                general_preferences_service=FakeGeneralPreferencesService(
                    erp_settings=ErpUserSettings(
                        document_service_url="https://erp.example.test/soap",
                        company_id="SALAV",
                        basic_username="api-user",
                        basic_password="api-secret",
                    )
                ),
                infinity_dms_client=dms_client,
            )
            controller.open_document(
                "89579d0be4304ebb9f91f08d81a611ed_privacy_TEST_4_.pdf",
                ErpSignedDocumentUploadContext(
                    document_id="DOC-1",
                    logical_dir="//Dipendenti/Privacy/",
                    logical_name="privacy_TEST(4).pdf",
                ),
            )
            controller.set_manual_signature_rectangle(20, 30, 80, 40, 200, 200)
            controller.apply_mouse_signature(
                CapturedSignature(
                    content=b"<svg><polyline points='1,1 2,2'/></svg>",
                    media_type="image/svg+xml",
                )
            )

            controller.save_signed_pdf()

            self.assertIn(("Firmato", "privacy_TEST(4).pdf"), self.view.flow_events)
            self.assertIn(("Caricato", "privacy_TEST(4).pdf"), self.view.flow_events)
            self.assertNotIn(("Firmato", signed_path.name), self.view.flow_events)

    def test_erp_signed_upload_reports_failure_after_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_path = Path(directory) / "sample_signed.pdf"
            signed_path.write_bytes(b"%PDF-signed-content")
            self.service.save_signed_preview.return_value = signed_path
            dms_client = FakeInfinityDmsClient(
                [
                    RuntimeError("errore 1"),
                    RuntimeError("errore 2"),
                    RuntimeError("errore 3"),
                ]
            )
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.erp_retry_failed"),
                general_preferences_service=FakeGeneralPreferencesService(
                    erp_settings=ErpUserSettings(
                        document_service_url="https://erp.example.test/soap",
                        company_id="SALAV",
                        basic_username="api-user",
                        basic_password="api-secret",
                    )
                ),
                infinity_dms_client=dms_client,
            )
            controller.open_document(
                "sample.pdf",
                ErpSignedDocumentUploadContext(
                    document_id="DOC-1",
                    logical_dir="//Dipendenti/Idoneita/",
                    logical_name="sample_signed.pdf",
                ),
            )
            controller.set_manual_signature_rectangle(20, 30, 80, 40, 200, 200)
            controller.apply_mouse_signature(
                CapturedSignature(
                    content=b"<svg><polyline points='1,1 2,2'/></svg>",
                    media_type="image/svg+xml",
                )
            )

            controller.save_signed_pdf()

            self.assertEqual(len(dms_client.uploads), 3)
            self.assertTrue(signed_path.exists())
            self.assertTrue(
                PDFViewerController._erp_upload_sidecar(signed_path).exists()
            )
            self.assertIn(
                "invio documento firmato all'ERP fallito dopo 3 tentativi",
                self.view.errors[-1],
            )
            self.assertIn(("Errore invio", "sample_signed.pdf"), self.view.flow_events)
            self.assertNotIn(("Caricato", "sample_signed.pdf"), self.view.flow_events)

    def test_erp_signed_upload_retries_non_zero_copy_file_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_path = Path(directory) / "sample_signed.pdf"
            signed_path.write_bytes(b"%PDF-signed-content")
            self.service.save_signed_preview.return_value = signed_path
            dms_client = FakeInfinityDmsClient(["1", "2", "0"])
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.erp_non_zero"),
                general_preferences_service=FakeGeneralPreferencesService(
                    erp_settings=ErpUserSettings(
                        document_service_url="https://erp.example.test/soap",
                        company_id="SALAV",
                        basic_username="api-user",
                        basic_password="api-secret",
                    )
                ),
                infinity_dms_client=dms_client,
            )
            controller.open_document(
                "sample.pdf",
                ErpSignedDocumentUploadContext(
                    document_id="DOC-1",
                    logical_dir="//Dipendenti/Idoneita/",
                    logical_name="sample_signed.pdf",
                ),
            )
            controller.set_manual_signature_rectangle(20, 30, 80, 40, 200, 200)
            controller.apply_mouse_signature(
                CapturedSignature(
                    content=b"<svg><polyline points='1,1 2,2'/></svg>",
                    media_type="image/svg+xml",
                )
            )

            controller.save_signed_pdf()

            self.assertEqual(len(dms_client.uploads), 3)
            self.assertEqual(self.view.statuses[-1], "PDF firmato inviato all'ERP")
            self.assertIn(("Caricato", "sample_signed.pdf"), self.view.flow_events)
            self.assertFalse(
                PDFViewerController._erp_upload_sidecar(signed_path).exists()
            )

    def test_retry_pending_erp_uploads_sends_saved_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signed_path = Path(directory) / "sample_signed.pdf"
            signed_path.write_bytes(b"%PDF-signed-content")
            sidecar = PDFViewerController._erp_upload_sidecar(signed_path)
            sidecar.write_text(
                json.dumps(
                    {
                        "signed_path": str(signed_path),
                        "document_id": "DOC-1",
                        "logical_dir": "//Dipendenti/Idoneita/",
                        "logical_name": "sample_signed.pdf",
                    }
                ),
                encoding="utf-8",
            )
            dms_client = FakeInfinityDmsClient()
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.erp_pending"),
                general_preferences_service=FakeGeneralPreferencesService(
                    erp_settings=ErpUserSettings(
                        document_service_url="https://erp.example.test/soap",
                        company_id="SALAV",
                        basic_username="api-user",
                        basic_password="api-secret",
                    )
                ),
                infinity_dms_client=dms_client,
            )

            controller.retry_pending_erp_uploads(directory)

            self.assertEqual(len(dms_client.uploads), 1)
            self.assertEqual(dms_client.uploads[0]["content"], b"%PDF-signed-content")
            self.assertFalse(sidecar.exists())

    def test_unsigned_save_prompt_blocks_page_change_after_signature(self) -> None:
        self.controller.open_document("sample.pdf")
        self.controller.set_manual_signature_rectangle(
            left=20,
            top=30,
            width=80,
            height=40,
            image_width=200,
            image_height=200,
        )
        self.controller.apply_mouse_signature(
            CapturedSignature(
                content=b"<svg><polyline points='1,1 2,2'/></svg>",
                media_type="image/svg+xml",
            )
        )
        calls_before = self.service.render_page.call_count

        self.controller.next_page()

        self.assertEqual(self.controller.state.page_index, 0)
        self.assertIsNotNone(self.view.discard_callback)
        self.assertEqual(self.service.render_page.call_count, calls_before)

        self.view.discard_callback()

        self.assertEqual(self.controller.state.page_index, 1)
        self.assertTrue(self.controller.has_unsaved_signed_document())

    def test_close_document_requires_confirmation_after_unsaved_signature(self) -> None:
        self.controller.open_document("sample.pdf")
        self.controller.set_manual_signature_rectangle(20, 30, 80, 40, 200, 200)
        self.controller.apply_mouse_signature(
            CapturedSignature(
                content=b"<svg><polyline points='1,1 2,2'/></svg>",
                media_type="image/svg+xml",
            )
        )

        self.controller.close_document()

        self.service.close_document.assert_not_called()
        self.assertFalse(self.view.cleared)
        self.assertIsNotNone(self.view.discard_callback)

        self.view.discard_callback()

        self.service.close_document.assert_called_once()
        self.assertTrue(self.view.cleared)
        self.assertFalse(self.controller.has_unsaved_signed_document())

    def test_added_signature_box_is_saved_and_signed_only_after_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first_signature = CapturedSignature(
                content=b"<svg><polyline points='1,1 2,2'/></svg>",
                media_type="image/svg+xml",
            )
            second_signature = CapturedSignature(
                content=b"<svg><polyline points='3,3 4,4'/></svg>",
                media_type="image/svg+xml",
            )
            provider = FakeSignatureProvider(first_signature)
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.multi_box"),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.multi_box_detector")
                ),
                general_preferences_service=FakeGeneralPreferencesService(
                    signature_capture_mode="wacom"
                ),
                signature_provider=provider,
                template_root=directory,
            )

            controller.open_document("sample.pdf")
            controller.set_manual_signature_rectangle(20, 30, 80, 40, 200, 200)
            self.view.save_callback()
            self.assertEqual(provider.capture_count, 1)

            provider.signature = second_signature
            controller.add_signature_box()
            self.assertIsNotNone(self.view.discard_callback)
            self.view.discard_callback()
            controller.set_manual_signature_rectangle(110, 120, 50, 30, 200, 200)
            self.view.save_callback()

            self.assertEqual(provider.capture_count, 1)
            overlays = self.view.pages[-1][4]
            self.assertEqual([overlay.label for overlay in overlays], ["Zona firma 1", "Zona firma 2"])
            self.assertEqual(overlays[0].signature_content, first_signature.content)
            self.assertIsNone(overlays[1].signature_content)
            saved_templates = list(Path(directory).glob("learned_*.json"))
            self.assertEqual(len(saved_templates), 1)
            content = saved_templates[0].read_text(encoding="utf-8")
            self.assertIn('"placement_id": "manual-signature"', content)
            self.assertIn('"placement_id": "manual-signature-2"', content)

            controller.open_signature_dialog(overlays[1].target_id)

            self.assertEqual(provider.capture_count, 2)
            overlays = self.view.pages[-1][4]
            self.assertEqual(overlays[1].signature_content, second_signature.content)

            self.service.save_signed_previews.return_value = Path(
                "dist/signed/sample_signed.pdf"
            )
            controller.save_signed_pdf()

            signatures = self.service.save_signed_previews.call_args.args[0]
            self.assertEqual(len(signatures), 2)
            self.assertEqual(signatures[0][0], first_signature)
            self.assertEqual(signatures[0][1].x, 20)
            self.assertEqual(signatures[1][0], second_signature)
            self.assertEqual(signatures[1][1].x, 110)
            self.service.save_signed_preview.assert_not_called()

    def test_confirmed_mouse_signature_auto_saves_when_preference_is_enabled(self) -> None:
        self.service.save_signed_preview.return_value = Path(
            "dist/signed/sample_signed.pdf"
        )
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.auto_save"),
            general_preferences_service=FakeGeneralPreferencesService(
                auto_save_signed_documents=True
            ),
        )
        self.controller.open_document("sample.pdf")
        self.controller.set_manual_signature_rectangle(
            left=20,
            top=30,
            width=80,
            height=40,
            image_width=200,
            image_height=200,
        )

        signature = CapturedSignature(
            content=b"<svg><polyline points='1,1 2,2'/></svg>",
            media_type="image/svg+xml",
        )
        self.controller.apply_mouse_signature(signature)

        saved_signature, area = self.service.save_signed_preview.call_args.args
        self.assertEqual(saved_signature, signature)
        self.assertEqual(area.page_index, 0)
        self.service.close_document.assert_called_once()
        self.assertTrue(self.view.cleared)
        self.assertIn("PDF firmato salvato", self.view.statuses[-1])

    def test_learned_manual_template_prefers_current_anchor_over_fixed_page(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.learned_dynamic"),
            pdf_provider=FakePDFProviderWorkerAcknowledgement(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.learned_dynamic_detector")
            ),
            template_repository=FakeManualFixedTemplateRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(self.controller.state.page_index, 1)
        self.assertEqual(self.view.pages[-1][0], 2)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].top, 90)
        self.assertTrue(self.view.manual_mode)

    def test_learned_template_with_failed_relative_anchor_uses_saved_manual_box(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.learned_bad_relative"),
            pdf_provider=FakePDFProviderWorkerAcknowledgement(),
            anchor_detector=AnchorDetector(
                LoggingService.create(
                    "qsign.tests.controller.learned_bad_relative_detector"
                )
            ),
            template_repository=FakeBadRelativeLearnedTemplateRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(self.controller.state.page_index, 1)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 40)
        self.assertEqual(overlays[0].top, 120)
        self.assertEqual(overlays[0].width, 160)
        self.assertEqual(overlays[0].height, 60)

    def test_legacy_learned_template_is_recognized_with_deterministic_token_coverage(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.learned"),
            pdf_provider=FakePDFProviderLearnedDocument(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.learned_detector")
            ),
            template_repository=FakeLearnedTemplateRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertTrue(self.view.manual_mode)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 40)
        self.assertEqual(overlays[0].top, 50)

    def test_latest_learned_template_wins_when_scores_are_equal(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.latest_learned"),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.latest_learned_detector")
            ),
            template_repository=FakeMultipleLearnedTemplateRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 70)
        self.assertEqual(overlays[0].top, 80)

    def test_learned_template_wins_over_base_template_when_scores_are_equal(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.learned_over_base"),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.learned_over_base_detector")
            ),
            template_repository=FakeBaseAndLearnedTemplateRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual([overlay.label for overlay in overlays], ["Zona firma 1", "Zona firma 2"])
        self.assertEqual(overlays[0].left, 20)
        self.assertEqual(overlays[1].left, 110)

    def test_updating_recognized_template_saves_added_signature_box(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.update_template_box"),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create(
                        "qsign.tests.controller.update_template_box_detector"
                    )
                ),
                template_repository=FakeTemplateRepository(),
                template_root=directory,
            )

            controller.open_document("sample.pdf")
            self.assertEqual(len(self.view.pages[-1][4]), 1)

            controller.add_signature_box()
            controller.set_manual_signature_rectangle(110, 120, 50, 30, 200, 200)
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            self.assertEqual(len(saved_templates), 1)
            content = saved_templates[0].read_text(encoding="utf-8")
            self.assertIn('"placement_id": "manual-signature"', content)
            self.assertIn('"placement_id": "manual-signature-2"', content)
            self.assertIn('"x_offset": 20', content)
            self.assertIn('"x_offset": 110.0', content)

    def test_updating_learned_template_keeps_recognized_template_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.update_learned_name"),
                pdf_provider=FakePDFProviderWithoutAnchors(),
                anchor_detector=AnchorDetector(
                    LoggingService.create(
                        "qsign.tests.controller.update_learned_name_detector"
                    )
                ),
                template_repository=FakeRecognizedLearnedTemplateRepository(),
                template_root=directory,
            )

            controller.open_document("sample_signed_reopened.pdf")
            controller.add_signature_box()
            controller.set_manual_signature_rectangle(110, 120, 50, 30, 200, 200)
            self.view.save_callback()

            original_template = Path(directory) / "learned_existing_model.json"
            derived_template = Path(directory) / "learned_documento_speciale.json"
            self.assertTrue(original_template.exists())
            self.assertFalse(derived_template.exists())
            content = original_template.read_text(encoding="utf-8")
            self.assertIn('"placement_id": "manual-signature"', content)
            self.assertIn('"placement_id": "manual-signature-2"', content)

    def test_learned_manual_boxes_are_used_even_when_demo_anchor_matches(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.learned_manual_boxes"),
            pdf_provider=FakePDFProvider(),
            anchor_detector=AnchorDetector(
                LoggingService.create(
                    "qsign.tests.controller.learned_manual_boxes_detector"
                )
            ),
            template_repository=FakeLearnedManualBoxesWithDemoAnchorRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual([overlay.label for overlay in overlays], ["Zona firma 1", "Zona firma 2"])
        self.assertEqual(overlays[0].left, 20)
        self.assertEqual(overlays[1].left, 110)

    def test_learned_filename_stem_must_match_exactly_after_normalization(self) -> None:
        self.document = PDFDocument(
            path=Path("sample(1).pdf"),
            filename="sample(1).pdf",
            page_count=1,
            page_sizes=(PageSize(200, 200),),
            loaded=True,
        )
        self.service.open_document.return_value = self.document
        self.service.current_document = self.document
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.filename_specific"),
            pdf_provider=FakePDFProviderWithoutAnchors(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.filename_specific_detector")
            ),
            template_repository=FakeFilenameSpecificLearnedTemplateRepository(),
        )

        self.controller.open_document("sample(1).pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 70)
        self.assertEqual(overlays[0].top, 80)

    def test_scanned_learned_template_matches_same_filename_document_type(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.filename_type"),
            pdf_provider=FakePDFProviderScannedDocument(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.filename_type_detector")
            ),
            template_repository=FakeScannedNominaTemplateRepository(),
        )

        self.controller.open_document(
            "3G S.A.S. di Geminiano Radicchi & C._stampa_nomina_000000000075163.pdf"
        )

        overlays = self.view.pages[-1][4]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 265.5)
        self.assertEqual(overlays[0].top, 740.0)
        self.assertEqual(overlays[0].width, 263.0)
        self.assertEqual(overlays[0].height, 78.0)

    def test_scanned_manual_template_uses_generic_filename_document_type_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.scanned_save"),
                pdf_provider=FakePDFProviderScannedDocument(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.scanned_save_detector")
                ),
                template_root=directory,
            )

            self.controller.open_document(
                "3 EFFE SRL UNIPERSONALE_stampa_nomina_000000000003502.pdf"
            )
            self.controller.set_manual_signature_rectangle(
                left=20,
                top=30,
                width=80,
                height=40,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            self.assertEqual(
                [path.name for path in saved_templates],
                ["learned_stampa_nomina.json"],
            )

    def test_scanned_manual_template_without_stable_filename_saves_visual_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.visual_save"),
                pdf_provider=FakePDFProviderVisualScannedDocument(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.visual_save_detector")
                ),
                template_root=directory,
            )

            self.controller.open_document("851013_beuk8pvxri.pdf")
            self.controller.set_manual_signature_rectangle(
                left=20,
                top=30,
                width=80,
                height=40,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            self.assertEqual(
                [path.name for path in saved_templates],
                ["learned_visual_000000003f840040.json"],
            )
            content = saved_templates[0].read_text(encoding="utf-8")
            self.assertIn('"rule_id": "manual-visual-signature"', content)

    def test_scanned_learned_template_matches_same_visual_signature(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.visual_match"),
            pdf_provider=FakePDFProviderVisualScannedDocument(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.visual_match_detector")
            ),
            template_repository=FakeVisualScannedTemplateRepository(),
        )

        self.controller.open_document("851020_wjfecpztvu.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 174.5)
        self.assertEqual(overlays[0].top, 715.0)

    def test_relative_template_places_signature_from_detected_anchor(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.relative"),
            pdf_provider=FakePDFProvider(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.relative_detector")
            ),
            template_repository=FakeRelativeTemplateRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 60)
        self.assertEqual(overlays[0].top, 140)

    def test_relative_template_uses_anchor_on_the_learned_page(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.relative_page"),
            pdf_provider=FakePDFProviderRepeatedAnchor(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.relative_page_detector")
            ),
            template_repository=FakeRelativeTemplateOnSecondPageRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(self.controller.state.page_index, 1)
        self.assertEqual(self.view.pages[-1][0], 2)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 60)
        self.assertEqual(overlays[0].top, 140)

    def test_manual_template_ignores_weak_relative_anchor_and_uses_fallback(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.weak_relative"),
            pdf_provider=FakePDFProviderWeakAnchorDocument(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.weak_relative_detector")
            ),
            template_repository=FakeWeakRelativeTemplateRepository(),
        )

        self.controller.open_document("sample.pdf")

        overlays = self.view.pages[-1][4]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].left, 20)
        self.assertEqual(overlays[0].top, 30)

    def test_manual_template_does_not_save_weak_header_as_relative_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.controller = PDFViewerController(
                pdf_service=self.service,
                view=self.view,
                logger=LoggingService.create("qsign.tests.controller.weak_save"),
                pdf_provider=FakePDFProviderWeakAnchorDocument(),
                anchor_detector=AnchorDetector(
                    LoggingService.create("qsign.tests.controller.weak_save_detector")
                ),
                template_root=directory,
            )

            self.controller.open_document("sample.pdf")
            self.controller.set_manual_signature_rectangle(
                left=20,
                top=80,
                width=80,
                height=40,
                image_width=200,
                image_height=200,
            )
            self.view.save_callback()

            saved_templates = list(Path(directory).glob("learned_*.json"))
            content = saved_templates[0].read_text(encoding="utf-8")
            self.assertIn('"anchor_rules": []', content)
            self.assertNotIn('"placement_id": "relative-signature"', content)
            self.assertIn('"placement_id": "manual-signature"', content)

    def test_confirmed_mouse_signature_is_displayed_in_viewer_and_hidden_on_other_page(self) -> None:
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller.signature"),
            pdf_provider=FakePDFProvider(),
            anchor_detector=AnchorDetector(
                LoggingService.create("qsign.tests.controller.signature_detector")
            ),
        )

        self.controller.open_document("sample.pdf")
        self.controller.apply_mouse_signature(
            CapturedSignature(
                content=b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
                media_type="image/svg+xml",
            )
        )

        overlays = self.view.pages[-1][4]
        self.assertEqual(overlays[0].signature_content, b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")

        self.controller.next_page()
        self.view.discard_callback()

        self.assertEqual(self.view.pages[-1][4], ())


class FakeGeneralPreferencesService:
    def __init__(
        self,
        auto_save_signed_documents: bool = False,
        signature_capture_mode: str = "mouse",
        erp_settings: ErpUserSettings | None = None,
    ) -> None:
        self._auto_save_signed_documents = auto_save_signed_documents
        self._signature_capture_mode = signature_capture_mode
        self._erp_settings = erp_settings or ErpUserSettings()

    def get_supabase_settings(self) -> SupabaseSettings:
        return SupabaseSettings(
            auto_save_signed_documents=self._auto_save_signed_documents,
            signature_capture_mode=self._signature_capture_mode,
        )

    def get_erp_user_settings(self) -> ErpUserSettings:
        return self._erp_settings


class FakeInfinityDmsClient:
    def __init__(
        self,
        results: list[str | Exception] | None = None,
    ) -> None:
        self.uploads: list[dict[str, object]] = []
        self.results = list(results or ["0"])

    def upload_document(self, **kwargs: object) -> str:
        self.uploads.append(kwargs)
        result = self.results.pop(0) if self.results else "0"
        if isinstance(result, Exception):
            raise result
        return result


class FakePDFProvider:
    def load_document(self, path: str | Path) -> Document:
        body_signature_word = Word(
            text="Firma",
            bounds=Rectangle(10, 20, 45, 30),
            block_index=0,
            line_index=0,
            word_index=0,
        )
        in_word = Word(
            text="In",
            bounds=Rectangle(50, 120, 60, 130),
            block_index=0,
            line_index=1,
            word_index=0,
        )
        fede_word = Word(
            text="fede",
            bounds=Rectangle(65, 120, 85, 130),
            block_index=0,
            line_index=1,
            word_index=1,
        )
        interested_word = Word(
            text="L'interessato",
            bounds=Rectangle(55, 140, 105, 150),
            block_index=0,
            line_index=2,
            word_index=0,
        )
        words = (
            body_signature_word,
            in_word,
            fede_word,
            interested_word,
        )
        return Document(
            source_path=Path(path),
            page_count=2,
            pages=(
                Page(
                    index=0,
                    width=200,
                    height=200,
                    text_blocks=(
                        TextBlock(
                            text="Firma\nIn fede\nL'interessato",
                            bounds=Rectangle(10, 20, 105, 150),
                            words=words,
                            block_index=0,
                        ),
                    ),
                ),
                Page(index=1, width=200, height=200),
            ),
            metadata=Metadata(),
        )


class FakePDFProviderWithoutAnchors:
    def load_document(self, path: str | Path) -> Document:
        words = (
            Word(
                text="Documento",
                bounds=Rectangle(10, 20, 70, 30),
                block_index=0,
                line_index=0,
                word_index=0,
            ),
            Word(
                text="Speciale",
                bounds=Rectangle(75, 20, 130, 30),
                block_index=0,
                line_index=0,
                word_index=1,
            ),
        )
        return Document(
            source_path=Path(path),
            page_count=1,
            pages=(
                Page(
                    index=0,
                    width=200,
                    height=200,
                    text_blocks=(
                        TextBlock(
                            text="Documento Speciale",
                            bounds=Rectangle(10, 20, 130, 30),
                            words=words,
                            block_index=0,
                        ),
                    ),
                ),
            ),
            metadata=Metadata(),
        )


class FakePDFProviderScannedDocument:
    def load_document(self, path: str | Path) -> Document:
        return Document(
            source_path=Path(path),
            page_count=1,
            pages=(Page(index=0, width=595, height=842),),
            metadata=Metadata(),
        )


class FakePDFProviderVisualScannedDocument:
    def load_document(self, path: str | Path) -> Document:
        return Document(
            source_path=Path(path),
            page_count=1,
            pages=(Page(index=0, width=595, height=842),),
            metadata=Metadata(
                {
                    "qsign_visual_signature": (
                        "000000003f8400403f80fe003801ff4c3ff40040013ffb801ffa00001ffffff81ffffff81ffffc001ffffff81ffffff81ffffff81fe100001ffdfff81ffffff81ffbfff81ffffff80f0000001ffffff81fffc0001ffe00001ff000001ffffff81ffffff81ffffff81fffffc01ffe00000c0003e0001c0b700000000000000000"
                    )
                }
            ),
        )


class FakePDFProviderHeaderLikeDocument:
    def load_document(self, path: str | Path) -> Document:
        words = _words_from_text(
            (
                "Salute",
                "Lavoro",
                "Società",
                "Coopera",
                "Ambulatori",
                "Via",
                "Meucci",
                "Documento",
                "Speciale",
            ),
            top=20,
        )
        return Document(
            source_path=Path(path),
            page_count=1,
            pages=(
                Page(
                    index=0,
                    width=200,
                    height=200,
                    text_blocks=(
                        TextBlock(
                            text=" ".join(word.text for word in words),
                            bounds=Rectangle(10, 20, 170, 30),
                            words=words,
                            block_index=0,
                        ),
                    ),
                ),
            ),
            metadata=Metadata(),
        )


class FakePDFProviderScreeningDocument:
    def load_document(self, path: str | Path) -> Document:
        words = _words_from_text(
            (
                "Salute",
                "Lavoro",
                "Società",
                "Coopera",
                "Ambulatori",
                "Via",
                "Meucci",
                "Informativa",
                "prevenzione",
                "oncologica",
                "programmi",
                "screening",
                "diagnosi",
                "precoce",
                "patologie",
                "tumorali",
            ),
            top=20,
        )
        return Document(
            source_path=Path(path),
            page_count=1,
            pages=(
                Page(
                    index=0,
                    width=200,
                    height=200,
                    text_blocks=(
                        TextBlock(
                            text=" ".join(word.text for word in words),
                            bounds=Rectangle(10, 20, 190, 30),
                            words=words,
                            block_index=0,
                        ),
                    ),
                ),
            ),
            metadata=Metadata(),
        )


class FakePDFProviderPartialStructuralDocument:
    def load_document(self, path: str | Path) -> Document:
        words = _words_from_text(
            (
                "Salute",
                "Lavoro",
                "Società",
                "Coopera",
                "Ambulatori",
                "modalit",
                "consultazione",
                "idoneit",
                "referti",
                "data__________",
                "corre",
                "visualizzazione",
                "necessario",
            ),
            top=20,
        )
        return Document(
            source_path=Path(path),
            page_count=1,
            pages=(
                Page(
                    index=0,
                    width=200,
                    height=200,
                    text_blocks=(
                        TextBlock(
                            text=" ".join(word.text for word in words),
                            bounds=Rectangle(10, 20, 190, 30),
                            words=words,
                            block_index=0,
                        ),
                    ),
                ),
            ),
            metadata=Metadata(),
        )


class FakePDFProviderWorkerAcknowledgement:
    def load_document(self, path: str | Path) -> Document:
        page_one_words = (
            Word(
                text="Documento",
                bounds=Rectangle(10, 20, 55, 30),
                block_index=0,
                line_index=0,
                word_index=0,
            ),
            Word(
                text="firmato",
                bounds=Rectangle(60, 20, 95, 30),
                block_index=0,
                line_index=0,
                word_index=1,
            ),
            Word(
                text="digitalmente",
                bounds=Rectangle(100, 20, 160, 30),
                block_index=0,
                line_index=0,
                word_index=2,
            ),
            Word(
                text="Accertamenti",
                bounds=Rectangle(30, 70, 95, 80),
                block_index=1,
                line_index=0,
                word_index=0,
            ),
            Word(
                text="sanitari",
                bounds=Rectangle(100, 70, 140, 80),
                block_index=1,
                line_index=0,
                word_index=1,
            ),
        )
        page_two_words = (
            Word(
                text="Il",
                bounds=Rectangle(80, 70, 90, 80),
                block_index=0,
                line_index=0,
                word_index=0,
            ),
            Word(
                text="lavoratore",
                bounds=Rectangle(95, 70, 145, 80),
                block_index=0,
                line_index=0,
                word_index=1,
            ),
            Word(
                text="per",
                bounds=Rectangle(150, 70, 165, 80),
                block_index=0,
                line_index=0,
                word_index=2,
            ),
            Word(
                text="presa",
                bounds=Rectangle(170, 70, 195, 80),
                block_index=0,
                line_index=0,
                word_index=3,
            ),
            Word(
                text="visione",
                bounds=Rectangle(200, 70, 235, 80),
                block_index=0,
                line_index=0,
                word_index=4,
            ),
        )
        return Document(
            source_path=Path(path),
            page_count=2,
            pages=(
                Page(
                    index=0,
                    width=300,
                    height=300,
                    text_blocks=(
                        TextBlock(
                            text="Documento firmato digitalmente\nAccertamenti sanitari",
                            bounds=Rectangle(10, 20, 160, 80),
                            words=page_one_words,
                            block_index=0,
                        ),
                    ),
                ),
                Page(
                    index=1,
                    width=300,
                    height=300,
                    text_blocks=(
                        TextBlock(
                            text="Il lavoratore per presa visione",
                            bounds=Rectangle(80, 70, 235, 80),
                            words=page_two_words,
                            block_index=0,
                        ),
                    ),
                ),
            ),
            metadata=Metadata(),
        )


class FakePDFProviderRepeatedWorkerAcknowledgement:
    def load_document(self, path: str | Path) -> Document:
        return Document(
            source_path=Path(path),
            page_count=2,
            pages=(
                _worker_acknowledgement_page(
                    index=0,
                    extra_words=(
                        Word(
                            text="contenuto",
                            bounds=Rectangle(70, 95, 130, 105),
                            block_index=1,
                            line_index=0,
                            word_index=0,
                        ),
                    ),
                ),
                _worker_acknowledgement_page(index=1),
            ),
            metadata=Metadata(),
        )


class FakePDFProviderRepeatedAnchor:
    def load_document(self, path: str | Path) -> Document:
        return Document(
            source_path=Path(path),
            page_count=2,
            pages=(
                _page_with_words(0, ("In", "fede"), top=120),
                _page_with_words(1, ("In", "fede"), top=120),
            ),
            metadata=Metadata(),
        )


class FakePDFProviderWeakAnchorDocument:
    def load_document(self, path: str | Path) -> Document:
        words = (
            Word(
                text="Documento",
                bounds=Rectangle(10, 20, 70, 30),
                block_index=0,
                line_index=0,
                word_index=0,
            ),
            Word(
                text="Speciale",
                bounds=Rectangle(75, 20, 130, 30),
                block_index=0,
                line_index=0,
                word_index=1,
            ),
            Word(
                text="P.",
                bounds=Rectangle(20, 50, 30, 60),
                block_index=1,
                line_index=0,
                word_index=0,
            ),
            Word(
                text="IVA",
                bounds=Rectangle(35, 50, 55, 60),
                block_index=1,
                line_index=0,
                word_index=1,
            ),
            Word(
                text="03455980403",
                bounds=Rectangle(60, 50, 120, 60),
                block_index=1,
                line_index=0,
                word_index=2,
            ),
        )
        return Document(
            source_path=Path(path),
            page_count=1,
            pages=(
                Page(
                    index=0,
                    width=200,
                    height=200,
                    text_blocks=(
                        TextBlock(
                            text="Documento Speciale\nP. IVA 03455980403",
                            bounds=Rectangle(10, 20, 130, 60),
                            words=words,
                            block_index=0,
                        ),
                    ),
                ),
            ),
            metadata=Metadata(),
        )


class FakePDFProviderLearnedDocument:
    def load_document(self, path: str | Path) -> Document:
        texts = (
            "Documento",
            "Speciale",
            "Ambulatori",
            "Salute",
            "Lavoro",
            "Dipendenti",
            "Strumentali",
            "Profilo",
            "Logout",
        )
        words = tuple(
            Word(
                text=text,
                bounds=Rectangle(10 + index * 10, 20, 20 + index * 10, 30),
                block_index=0,
                line_index=0,
                word_index=index,
            )
            for index, text in enumerate(texts)
        )
        return Document(
            source_path=Path(path),
            page_count=1,
            pages=(
                Page(
                    index=0,
                    width=200,
                    height=200,
                    text_blocks=(
                        TextBlock(
                            text=" ".join(texts),
                            bounds=Rectangle(10, 20, 130, 30),
                            words=words,
                            block_index=0,
                        ),
                    ),
                ),
            ),
            metadata=Metadata(),
        )


class FakeTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="recognized-demo",
                code="RECOGNIZED_DEMO",
                name="Recognized demo",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-special",
                        rule_type="literal",
                        expression="Documento Speciale",
                        required=True,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=40,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeRecognizedLearnedTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_existing_model",
                code="LEARNED_EXISTING_MODEL",
                name="Existing learned model",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-special",
                        rule_type="literal",
                        expression="Documento Speciale",
                        required=True,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=40,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeLearnedTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned-demo",
                code="LEARNED_DEMO",
                name="Learned demo",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="manual-recognition-phrase",
                        rule_type="literal",
                        expression=(
                            "Documento Speciale Ambulatori Salute Lavoro "
                            "Dipendenti Strumentali Profilo Logout "
                            "Variabile Assente Mancante Diversa"
                        ),
                        required=True,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=40,
                        y_offset=50,
                        width=70,
                        height=30,
                    ),
                ),
                settings=TemplateSettings(recognition_threshold=80),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeMultipleLearnedTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_sample_1783437000",
                code="LEARNED_SAMPLE_OLD",
                name="Learned sample old",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-special",
                        rule_type="literal",
                        expression="Documento Speciale",
                        required=True,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=40,
                    ),
                ),
            ),
            Template(
                template_id="learned_sample_1783438000",
                code="LEARNED_SAMPLE_NEW",
                name="Learned sample new",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-special",
                        rule_type="literal",
                        expression="Documento Speciale",
                        required=True,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=70,
                        y_offset=80,
                        width=90,
                        height=50,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return next(
            template
            for template in self.list_templates()
            if template.template_id == template_id
        )


class FakeBaseAndLearnedTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        base_template = FakeTemplateRepository().list_templates()[0]
        learned_template = Template(
            template_id="learned_documento_speciale",
            code="LEARNED_DOCUMENTO_SPECIALE",
            name="Learned documento speciale",
            document_type="manual_signature_flow",
            version="0.1.0",
            state=TemplateState.DRAFT,
            recognition_rules=(
                RecognitionRule(
                    rule_id="contains-special",
                    rule_type="literal",
                    expression="Documento Speciale",
                    required=True,
                ),
            ),
            placement_rules=(
                PlacementRule(
                    placement_id="manual-signature",
                    role="signer",
                    anchor_id="manual",
                    side="manual",
                    alignment="manual",
                    x_offset=20,
                    y_offset=30,
                    width=80,
                    height=40,
                ),
                PlacementRule(
                    placement_id="manual-signature-2",
                    role="signer",
                    anchor_id="manual",
                    side="manual",
                    alignment="manual",
                    x_offset=110,
                    y_offset=120,
                    width=50,
                    height=30,
                ),
            ),
        )
        return (base_template, learned_template)

    def get_template(self, template_id: str) -> Template:
        return next(
            template
            for template in self.list_templates()
            if template.template_id == template_id
        )


class FakeLearnedManualBoxesWithDemoAnchorRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_in_fede",
                code="LEARNED_IN_FEDE",
                name="Learned in fede",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-in-fede",
                        rule_type="literal",
                        expression="In fede",
                        required=True,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=40,
                    ),
                    PlacementRule(
                        placement_id="manual-signature-2",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=110,
                        y_offset=120,
                        width=50,
                        height=30,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeFilenameSpecificLearnedTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_sample_1783439000",
                code="LEARNED_SAMPLE_BASE",
                name="Learned sample base",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="manual-filename-stem",
                        rule_type="literal",
                        expression="sample",
                        required=False,
                        weight=10,
                    ),
                    RecognitionRule(
                        rule_id="contains-special",
                        rule_type="literal",
                        expression="Documento Speciale",
                        required=True,
                        weight=1,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=40,
                    ),
                ),
            ),
            Template(
                template_id="learned_sample_1_1783438000",
                code="LEARNED_SAMPLE_SUFFIX",
                name="Learned sample suffix",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="manual-filename-stem",
                        rule_type="literal",
                        expression="sample(1)",
                        required=False,
                        weight=10,
                    ),
                    RecognitionRule(
                        rule_id="contains-special",
                        rule_type="literal",
                        expression="Documento Speciale",
                        required=True,
                        weight=1,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=70,
                        y_offset=80,
                        width=90,
                        height=50,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return next(
            template
            for template in self.list_templates()
            if template.template_id == template_id
        )


class FakeScannedNominaTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_effe_unipersonale_stampa_nomina_000000000003502",
                code="LEARNED_EFFE_UNIPERSONALE_STAMPA_NOMINA_000000000003502",
                name="Learned scanned nomina",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="manual-filename-stem",
                        rule_type="literal",
                        expression=(
                            "3 EFFE SRL UNIPERSONALE_stampa_nomina_000000000003502"
                        ),
                        required=False,
                        weight=0.25,
                    ),
                    RecognitionRule(
                        rule_id="manual-recognition-phrase",
                        rule_type="literal",
                        expression=(
                            "3 EFFE SRL UNIPERSONALE_stampa_nomina_000000000003502"
                        ),
                        required=False,
                        weight=6.0,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=265.5,
                        y_offset=740.0,
                        width=263.0,
                        height=78.0,
                        page_index=0,
                    ),
                ),
                settings=TemplateSettings(recognition_threshold=80),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeVisualScannedTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_visual_000000003f840040",
                code="LEARNED_VISUAL_000000003F840040",
                name="Learned visual scanned",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="manual-filename-stem",
                        rule_type="literal",
                        expression="851013_beuk8pvxri",
                        required=False,
                        weight=0.25,
                    ),
                    RecognitionRule(
                        rule_id="manual-recognition-phrase",
                        rule_type="literal",
                        expression="851013_beuk8pvxri",
                        required=False,
                        weight=6.0,
                    ),
                    RecognitionRule(
                        rule_id="manual-visual-signature",
                        rule_type="visual_hash",
                        expression=(
                            "000000003f8400403f80fe003801ff4c3ff40040013ffb801ffa00001ffffff81ffffff81ffffc001ffffff81ffffff81ffffff81fe100001ffdfff81ffffff81ffbfff81ffffff80f0000001ffffff81fffc0001ffe00001ff000001ffffff81ffffff81ffffff81fffffc01ffe00000c0003e0001c0b700000000000000000"
                        ),
                        required=True,
                        weight=30.0,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=174.5,
                        y_offset=715.0,
                        width=250.0,
                        height=81.0,
                        page_index=0,
                    ),
                ),
                settings=TemplateSettings(recognition_threshold=80),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeLegacyHeaderOnlyTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_header_only",
                code="LEARNED_HEADER_ONLY",
                name="Learned header only",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="manual-filename-stem",
                        rule_type="literal",
                        expression="original",
                        required=False,
                        weight=0.25,
                    ),
                    RecognitionRule(
                        rule_id="manual-recognition-phrase",
                        rule_type="literal",
                        expression=(
                            "Salute Lavoro Società Coopera Ambulatori Via Meucci "
                            "Documento Speciale"
                        ),
                        required=False,
                        weight=6.0,
                    ),
                    RecognitionRule(
                        rule_id="manual-keyword-1",
                        rule_type="literal",
                        expression="salute",
                        required=False,
                        weight=1.25,
                    ),
                    RecognitionRule(
                        rule_id="manual-keyword-2",
                        rule_type="literal",
                        expression="lavoro",
                        required=False,
                        weight=1.25,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=40,
                    ),
                ),
                settings=TemplateSettings(recognition_threshold=80),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakePartialStructuralTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_partial_structural",
                code="LEARNED_PARTIAL_STRUCTURAL",
                name="Learned partial structural",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="manual-filename-stem",
                        rule_type="literal",
                        expression="portal",
                        required=False,
                        weight=0.25,
                    ),
                    RecognitionRule(
                        rule_id="manual-recognition-phrase",
                        rule_type="literal",
                        expression="Salute Lavoro Società Coopera Ambulatori",
                        required=False,
                        weight=6.0,
                    ),
                    RecognitionRule(
                        rule_id="manual-structural-signature",
                        rule_type="literal",
                        expression=(
                            "modalit consultazione idoneit referti data__________ corre "
                            "visualizzazione necessario collegarsi indirizzo saluteelavoro "
                            "tablet inserire campo username primo"
                        ),
                        required=True,
                        weight=10.0,
                    ),
                    *(
                        RecognitionRule(
                            rule_id=f"manual-keyword-{index}",
                            rule_type="literal",
                            expression=expression,
                            required=False,
                            weight=1.25,
                        )
                        for index, expression in enumerate(
                            (
                                "modalit",
                                "consultazione",
                                "idoneit",
                                "referti",
                                "data__________",
                                "corre",
                                "visualizzazione",
                                "necessario",
                            ),
                            start=1,
                        )
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=33,
                        y_offset=44,
                        width=88,
                        height=55,
                    ),
                ),
                settings=TemplateSettings(recognition_threshold=80),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeManualFixedTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="manual-fixed-demo",
                code="MANUAL_FIXED_DEMO",
                name="Manual fixed demo",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-worker-ack",
                        rule_type="literal",
                        expression="Il lavoratore per presa visione",
                        required=True,
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=40,
                        page_index=0,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeBadRelativeLearnedTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_sample_1783439000",
                code="LEARNED_SAMPLE_BAD_RELATIVE",
                name="Learned sample bad relative",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-worker-ack",
                        rule_type="literal",
                        expression="Il lavoratore per presa visione",
                        required=True,
                    ),
                ),
                anchor_rules=(
                    AnchorRule(
                        anchor_id="manual-learned-anchor",
                        name="Anchor appreso",
                        search_type="text",
                        expression=(
                            "e-mail: info@saluteelavoro.eu "
                            "Il lavoratore per presa visione"
                        ),
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="relative-signature",
                        role="signer",
                        anchor_id="manual-learned-anchor",
                        side="relative",
                        alignment="manual",
                        x_offset=-20,
                        y_offset=50,
                        width=160,
                        height=60,
                        page_index=1,
                    ),
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=40,
                        y_offset=120,
                        width=160,
                        height=60,
                        page_index=1,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeRelativeTemplateWithoutFixedPageRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="learned_worker_ack",
                code="LEARNED_WORKER_ACK",
                name="Learned worker acknowledgement",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-worker-ack",
                        rule_type="literal",
                        expression="Il lavoratore per presa visione",
                        required=True,
                    ),
                ),
                anchor_rules=(
                    AnchorRule(
                        anchor_id="manual-learned-anchor",
                        name="Anchor appreso",
                        search_type="text",
                        expression="Il lavoratore per presa visione",
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="relative-signature",
                        role="signer",
                        anchor_id="manual-learned-anchor",
                        side="relative",
                        alignment="manual",
                        x_offset=20,
                        y_offset=50,
                        width=160,
                        height=60,
                    ),
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=40,
                        page_index=0,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeRelativeTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="relative-demo",
                code="RELATIVE_DEMO",
                name="Relative demo",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-signature-text",
                        rule_type="literal",
                        expression="In fede",
                        required=True,
                    ),
                ),
                anchor_rules=(
                    AnchorRule(
                        anchor_id="manual-learned-anchor",
                        name="Anchor appreso",
                        search_type="text",
                        expression="In fede",
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="relative-signature",
                        role="signer",
                        anchor_id="manual-learned-anchor",
                        side="relative",
                        alignment="manual",
                        x_offset=10,
                        y_offset=20,
                        width=80,
                        height=30,
                    ),
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=30,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeRelativeTemplateOnSecondPageRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="relative-page-demo",
                code="RELATIVE_PAGE_DEMO",
                name="Relative page demo",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-signature-text",
                        rule_type="literal",
                        expression="In fede",
                        required=True,
                    ),
                ),
                anchor_rules=(
                    AnchorRule(
                        anchor_id="manual-learned-anchor",
                        name="Anchor appreso",
                        search_type="text",
                        expression="In fede",
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="relative-signature",
                        role="signer",
                        anchor_id="manual-learned-anchor",
                        side="relative",
                        alignment="manual",
                        x_offset=10,
                        y_offset=20,
                        width=80,
                        height=30,
                        page_index=1,
                    ),
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=30,
                        page_index=1,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


class FakeWeakRelativeTemplateRepository:
    def list_templates(self) -> tuple[Template, ...]:
        return (
            Template(
                template_id="weak-relative-demo",
                code="WEAK_RELATIVE_DEMO",
                name="Weak relative demo",
                document_type="manual_signature_flow",
                version="0.1.0",
                state=TemplateState.DRAFT,
                recognition_rules=(
                    RecognitionRule(
                        rule_id="contains-special",
                        rule_type="literal",
                        expression="Documento Speciale",
                        required=True,
                    ),
                ),
                anchor_rules=(
                    AnchorRule(
                        anchor_id="manual-learned-anchor",
                        name="Anchor appreso",
                        search_type="text",
                        expression="P. IVA 03455980403",
                    ),
                ),
                placement_rules=(
                    PlacementRule(
                        placement_id="relative-signature",
                        role="signer",
                        anchor_id="manual-learned-anchor",
                        side="relative",
                        alignment="manual",
                        x_offset=10,
                        y_offset=20,
                        width=80,
                        height=30,
                    ),
                    PlacementRule(
                        placement_id="manual-signature",
                        role="signer",
                        anchor_id="manual",
                        side="manual",
                        alignment="manual",
                        x_offset=20,
                        y_offset=30,
                        width=80,
                        height=40,
                    ),
                ),
            ),
        )

    def get_template(self, template_id: str) -> Template:
        return self.list_templates()[0]


def _page_with_words(index: int, texts: tuple[str, ...], top: float) -> Page:
    words = _words_from_text(texts, top=top, left=50)
    return Page(
        index=index,
        width=200,
        height=200,
        text_blocks=(
            TextBlock(
                text=" ".join(texts),
                bounds=Rectangle(50, top, 105, top + 10),
                words=words,
                block_index=0,
            ),
        ),
    )


def _words_from_text(
    texts: tuple[str, ...], top: float, left: float = 10
) -> tuple[Word, ...]:
    current_left = left
    words = []
    for word_index, text in enumerate(texts):
        width = max(10, len(text) * 5)
        words.append(
            Word(
                text=text,
                bounds=Rectangle(current_left, top, current_left + width, top + 10),
                block_index=0,
                line_index=0,
                word_index=word_index,
            )
        )
        current_left += width + 5
    return tuple(words)


def _worker_acknowledgement_page(
    index: int, extra_words: tuple[Word, ...] = ()
) -> Page:
    anchor_words = (
        Word(
            text="Il",
            bounds=Rectangle(20, 70, 30, 80),
            block_index=0,
            line_index=0,
            word_index=0,
        ),
        Word(
            text="lavoratore",
            bounds=Rectangle(35, 70, 85, 80),
            block_index=0,
            line_index=0,
            word_index=1,
        ),
        Word(
            text="per",
            bounds=Rectangle(90, 70, 105, 80),
            block_index=0,
            line_index=0,
            word_index=2,
        ),
        Word(
            text="presa",
            bounds=Rectangle(110, 70, 135, 80),
            block_index=0,
            line_index=0,
            word_index=3,
        ),
        Word(
            text="visione",
            bounds=Rectangle(140, 70, 175, 80),
            block_index=0,
            line_index=0,
            word_index=4,
        ),
    )
    words = (*anchor_words, *extra_words)
    return Page(
        index=index,
        width=200,
        height=200,
        text_blocks=(
            TextBlock(
                text="Il lavoratore per presa visione",
                bounds=Rectangle(20, 70, 175, 80),
                words=words,
                block_index=0,
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
