"""Tests for document navigation state without loading Flet."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

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
        self.manual_mode = False
        self.save_callback = None

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

    def set_manual_signature_mode(self, enabled: bool) -> None:
        self.manual_mode = enabled

    def ask_save_template(self, on_confirm) -> None:
        self.save_callback = on_confirm


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

        self.assertEqual(self.view.pages[-1][4], ())


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
    words = tuple(
        Word(
            text=text,
            bounds=Rectangle(50 + word_index * 15, top, 60 + word_index * 20, top + 10),
            block_index=0,
            line_index=0,
            word_index=word_index,
        )
        for word_index, text in enumerate(texts)
    )
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
