"""Presentation controller for PDF viewing use cases."""

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from app.services.erp_document_context import ErpSignedDocumentUploadContext
from app.services.general_preferences_service import GeneralPreferencesService
from app.services.infinity_dms_client import InfinityDmsCredentials, InfinityDmsClient
from models.document import Document, Rectangle
from models.template import AnchorRule, PlacementRule, RecognitionRule, Template
from services.logging.logging_service import LoggingService
from services.anchors.anchor_detector import AnchorDetector
from services.anchors.anchor_models import (
    AnchorMatch,
    AnchorSearchOptions,
    AnchorSearchRule,
)
from services.pdf.pdf_service import PDFService
from services.pdf.pdf_provider import PdfProvider
from services.pdf.pdf_signature import SignatureArea
from services.signature.signature_service import CapturedSignature
from services.templates.template_repository import TemplateRepository
from services.wacom.wacom_service import WacomProvider


_DEMO_ANCHOR_RULES: tuple[tuple[str, ...], ...] = (
    ("Il lavoratore per presa visione",),
    ("Lavoratore per presa visione",),
    ("Per presa visione",),
    ("In fede L'interessato", "In fede L’interessato"),
    ("L'interessato", "L’interessato"),
    ("In fede",),
    ("Firma Cliente",),
)

_COMMON_MANUAL_TEMPLATE_TOKENS: frozenset[str] = frozenset(
    {
        "salute",
        "lavoro",
        "societ",
        "coopera",
        "cooperativa",
        "ambulatori",
        "meucci",
        "47122",
        "47035",
        "forl",
        "forli",
        "0543",
        "798337",
        "cari",
        "gambe",
        "gambettola",
        "ghinassi",
        "ennio",
        "massimo",
    }
)
_VISUAL_SIGNATURE_METADATA_KEY = "qsign_visual_signature"


class PDFViewerView(Protocol):
    """UI operations required by the controller."""

    def display_document(
        self,
        filename: str,
        image_content: bytes,
        image_width: int,
        image_height: int,
        page_number: int,
        page_count: int,
        zoom: float,
        anchor_overlays: tuple["AnchorOverlay", ...] = (),
        anchor_count: int = 0,
        selected_anchor: AnchorMatch | None = None,
        workflow_status: str = "",
    ) -> None: ...

    def set_manual_signature_mode(self, enabled: bool) -> None: ...

    def ask_save_template(
        self,
        on_confirm: "SaveTemplateCallback",
        on_cancel: "SaveTemplateCallback",
    ) -> None: ...

    def ask_discard_signed_document(
        self,
        on_confirm: "SaveTemplateCallback",
        on_cancel: "SaveTemplateCallback",
    ) -> None: ...

    def open_signature_dialog(
        self,
        on_confirm: "SignatureConfirmCallback",
        on_clear: "SignatureEventCallback",
        on_cancel: "SignatureEventCallback",
        *,
        canvas_width: float | None = None,
        canvas_height: float | None = None,
    ) -> None: ...

    def clear_document(self) -> None: ...

    def show_error(self, message: str) -> None: ...

    def show_status(self, message: str) -> None: ...

    def defer_signature_capture(self, callback: Callable[[], None]) -> None: ...

    def defer_viewer_refresh(self, callback: Callable[[], None]) -> None: ...

    def run_background_task(self, callback: Callable[[], None]) -> None: ...

    def run_ui_task(self, callback: Callable[[], None]) -> None: ...


@dataclass(slots=True)
class PDFViewerState:
    """Navigation state independent from Flet controls."""

    page_index: int = 0
    page_count: int = 0
    zoom: float = 1.0


@dataclass(frozen=True, slots=True)
class AnchorOverlay:
    """Presentation rectangle for an anchor on the rendered page image."""

    left: float
    top: float
    width: float
    height: float
    label: str
    signature_content: bytes | None = None
    signature_media_type: str = "image/svg+xml"
    target_id: str | None = None


class SaveTemplateCallback(Protocol):
    def __call__(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SignatureTarget:
    target_id: str
    rectangle: Rectangle
    page_index: int
    anchor_match: AnchorMatch | None = None
    signature: CapturedSignature | None = None
    role: str = "signer"


@dataclass(frozen=True, slots=True)
class SignatureRectangleSnapshot:
    rectangle: Rectangle | None
    page_index: int | None
    anchor_match: AnchorMatch | None
    workflow_status: str
    targets: tuple[SignatureTarget, ...] = ()
    selected_target_id: str | None = None


class SignatureConfirmCallback(Protocol):
    def __call__(self, signature: CapturedSignature) -> None: ...


class SignatureEventCallback(Protocol):
    def __call__(self) -> None: ...


class PDFViewerController:
    """Translate viewer actions into PDF service calls."""

    _ZOOM_STEP = 0.25
    _MINIMUM_ZOOM = 0.25
    _MAXIMUM_ZOOM = 4.0
    _DEMO_SIGNATURE_WIDTH = 110.0
    _DEMO_SIGNATURE_HEIGHT = 40.0
    _DEMO_SIGNATURE_TOP_GAP = 8.0
    _WORKER_ACK_SIGNATURE_WIDTH = 145.0
    _WORKER_ACK_SIGNATURE_HEIGHT = 45.0
    _WORKER_ACK_SIGNATURE_LEFT_GAP = 18.0
    _WORKER_ACK_SIGNATURE_TOP_GAP = 10.0

    def __init__(
        self,
        pdf_service: PDFService,
        view: PDFViewerView,
        logger: LoggingService,
        pdf_provider: PdfProvider | None = None,
        anchor_detector: AnchorDetector | None = None,
        template_repository: TemplateRepository | None = None,
        general_preferences_service: GeneralPreferencesService | None = None,
        infinity_dms_client: InfinityDmsClient | None = None,
        signature_provider: WacomProvider | None = None,
        template_root: str | Path = "templates",
    ) -> None:
        self._pdf_service = pdf_service
        self._view = view
        self._logger = logger
        self._pdf_provider = pdf_provider
        self._anchor_detector = anchor_detector
        self._template_repository = template_repository
        self._general_preferences_service = general_preferences_service
        self._infinity_dms_client = infinity_dms_client
        self._signature_provider = signature_provider
        self._template_root = Path(template_root)
        self._canonical_document: Document | None = None
        self._anchor_matches: tuple[AnchorMatch, ...] = ()
        self._signature_anchor_match: AnchorMatch | None = None
        self._signature_rectangle: Rectangle | None = None
        self._signature_page_index: int | None = None
        self._captured_signature: CapturedSignature | None = None
        self._signature_targets: tuple[SignatureTarget, ...] = ()
        self._selected_signature_target_id: str | None = None
        self._add_signature_box_mode = False
        self._recognized_template: Template | None = None
        self._pending_manual_rectangle_restore: SignatureRectangleSnapshot | None = None
        self._erp_upload_context: ErpSignedDocumentUploadContext | None = None
        self._workflow_status = "Apri un PDF"
        self._has_unsaved_signature = False
        self._wacom_capture_cancel: threading.Event | None = None
        self._wacom_capture_generation = 0
        self.state = PDFViewerState()

    def open_document(
        self,
        path: str,
        erp_upload_context: ErpSignedDocumentUploadContext | None = None,
    ) -> None:
        if self._confirm_unsaved_signature(
            lambda: self._open_document_now(path, erp_upload_context)
        ):
            return
        self._open_document_now(path, erp_upload_context)

    def _open_document_now(
        self,
        path: str,
        erp_upload_context: ErpSignedDocumentUploadContext | None = None,
    ) -> None:
        try:
            self._has_unsaved_signature = False
            self._erp_upload_context = erp_upload_context
            document = self._pdf_service.open_document(Path(path))
            self.state = PDFViewerState(page_count=document.page_count)
            self._analyze_document(Path(path))
            self._focus_signature_page_if_available()
            self._render_current_page()
            self._open_wacom_signature_when_ready()
        except Exception as error:
            self._logger.exception("Unable to open PDF", path=path)
            self.state = PDFViewerState()
            self._canonical_document = None
            self._anchor_matches = ()
            self._signature_anchor_match = None
            self._signature_rectangle = None
            self._signature_page_index = None
            self._captured_signature = None
            self._signature_targets = ()
            self._selected_signature_target_id = None
            self._add_signature_box_mode = False
            self._recognized_template = None
            self._pending_manual_rectangle_restore = None
            self._erp_upload_context = None
            self._has_unsaved_signature = False
            self._workflow_status = "Documento non aperto"
            self._view.clear_document()
            self._view.show_error(str(error))

    def close_document(self) -> None:
        if self._confirm_unsaved_signature(self._close_document_now):
            return
        self._close_document_now()

    def _close_document_now(self) -> None:
        self._cancel_wacom_capture()
        try:
            if self._pdf_service.current_document is not None:
                self._pdf_service.close_document()
        finally:
            self.state = PDFViewerState()
            self._canonical_document = None
            self._anchor_matches = ()
            self._signature_anchor_match = None
            self._signature_rectangle = None
            self._signature_page_index = None
            self._captured_signature = None
            self._signature_targets = ()
            self._selected_signature_target_id = None
            self._add_signature_box_mode = False
            self._recognized_template = None
            self._pending_manual_rectangle_restore = None
            self._erp_upload_context = None
            self._has_unsaved_signature = False
            self._workflow_status = "Apri un PDF"
            self._view.set_manual_signature_mode(False)
            self._view.clear_document()

    def shutdown(self) -> None:
        """Release document resources without updating a closing window."""
        self._cancel_wacom_capture()
        if self._pdf_service.current_document is not None:
            self._pdf_service.close_document()
        self.state = PDFViewerState()
        self._canonical_document = None
        self._anchor_matches = ()
        self._signature_anchor_match = None
        self._signature_rectangle = None
        self._signature_page_index = None
        self._captured_signature = None
        self._signature_targets = ()
        self._selected_signature_target_id = None
        self._add_signature_box_mode = False
        self._recognized_template = None
        self._pending_manual_rectangle_restore = None
        self._erp_upload_context = None
        self._has_unsaved_signature = False
        self._workflow_status = "Apri un PDF"

    def previous_page(self) -> None:
        if self._confirm_unsaved_signature(self._previous_page_now):
            return
        self._previous_page_now()

    def _previous_page_now(self) -> None:
        if self.state.page_index > 0:
            self.state.page_index -= 1
            self._render_current_page()

    def next_page(self) -> None:
        if self._confirm_unsaved_signature(self._next_page_now):
            return
        self._next_page_now()

    def _next_page_now(self) -> None:
        if self.state.page_index + 1 < self.state.page_count:
            self.state.page_index += 1
            self._render_current_page()

    def zoom_in(self) -> None:
        if self._confirm_unsaved_signature(self._zoom_in_now):
            return
        self._zoom_in_now()

    def _zoom_in_now(self) -> None:
        new_zoom = min(
            self._MAXIMUM_ZOOM, self.state.zoom + self._ZOOM_STEP
        )
        self._set_zoom(new_zoom)

    def zoom_out(self) -> None:
        if self._confirm_unsaved_signature(self._zoom_out_now):
            return
        self._zoom_out_now()

    def _zoom_out_now(self) -> None:
        new_zoom = max(
            self._MINIMUM_ZOOM, self.state.zoom - self._ZOOM_STEP
        )
        self._set_zoom(new_zoom)

    def has_unsaved_signed_document(self) -> bool:
        return self._has_unsaved_signature

    def _confirm_unsaved_signature(self, on_confirm: Callable[[], None]) -> bool:
        if not self._has_unsaved_signature:
            return False
        self._view.ask_discard_signed_document(
            on_confirm,
            lambda: self._view.show_status("salvataggio richiesto prima di continuare"),
        )
        return True

    def _set_zoom(self, zoom: float) -> None:
        if self.state.page_count and zoom != self.state.zoom:
            self.state.zoom = zoom
            self._render_current_page()

    def _focus_signature_page_if_available(self) -> None:
        if (
            self._signature_page_index is not None
            and 0 <= self._signature_page_index < self.state.page_count
        ):
            self.state.page_index = self._signature_page_index

    def _sync_selected_signature_target(self) -> None:
        target = self._selected_signature_target()
        if target is None:
            self._signature_rectangle = None
            self._signature_page_index = None
            self._signature_anchor_match = None
            self._captured_signature = None
            return
        self._signature_rectangle = target.rectangle
        self._signature_page_index = target.page_index
        self._signature_anchor_match = target.anchor_match
        self._captured_signature = target.signature

    def _set_signature_targets(
        self,
        targets: tuple[SignatureTarget, ...],
        selected_target_id: str | None = None,
    ) -> None:
        self._signature_targets = targets
        if selected_target_id is None and targets:
            selected_target_id = targets[0].target_id
        self._selected_signature_target_id = selected_target_id
        self._sync_selected_signature_target()

    def _selected_signature_target(self) -> SignatureTarget | None:
        if not self._signature_targets:
            return None
        selected_id = self._selected_signature_target_id
        if selected_id is not None:
            for target in self._signature_targets:
                if target.target_id == selected_id:
                    return target
        return self._signature_targets[0]

    def _select_signature_target(self, target_id: str | None) -> bool:
        if target_id is None:
            return False
        for target in self._signature_targets:
            if target.target_id == target_id:
                self._selected_signature_target_id = target_id
                self._sync_selected_signature_target()
                return True
        return False

    def _target_id_for_index(self, index: int) -> str:
        return f"signature-{index + 1}"

    def _next_signature_target_id(self) -> str:
        used = {target.target_id for target in self._signature_targets}
        index = 0
        while self._target_id_for_index(index) in used:
            index += 1
        return self._target_id_for_index(index)

    def _target_display_index(self, target: SignatureTarget) -> int:
        try:
            return self._signature_targets.index(target) + 1
        except ValueError:
            return 1

    def _replace_selected_target_signature(
        self, signature: CapturedSignature | None
    ) -> None:
        selected = self._selected_signature_target()
        if selected is None:
            return
        self._replace_target_signature(selected.target_id, signature)

    def _replace_target_signature(
        self, target_id: str, signature: CapturedSignature | None
    ) -> bool:
        if not any(target.target_id == target_id for target in self._signature_targets):
            return False
        self._signature_targets = tuple(
            SignatureTarget(
                target_id=target.target_id,
                rectangle=target.rectangle,
                page_index=target.page_index,
                anchor_match=target.anchor_match,
                signature=(
                    signature
                    if target.target_id == target_id
                    else target.signature
                ),
                role=target.role,
            )
            for target in self._signature_targets
        )
        self._selected_signature_target_id = target_id
        self._sync_selected_signature_target()
        return True

    def _render_current_page(self) -> None:
        document = self._pdf_service.current_document
        if document is None or self.state.page_count == 0:
            return
        try:
            rendered = self._pdf_service.render_page(
                self.state.page_index, self.state.zoom
            )
            self._view.display_document(
                filename=document.filename,
                image_content=rendered.content,
                image_width=rendered.width,
                image_height=rendered.height,
                page_number=self.state.page_index + 1,
                page_count=self.state.page_count,
                zoom=self.state.zoom,
                anchor_overlays=self._overlays_for_current_page(
                    rendered.width, rendered.height
                ),
                anchor_count=len(self._anchor_matches),
                selected_anchor=self._first_anchor_on_current_page(),
                workflow_status=self._workflow_status,
            )
        except Exception as error:
            self._logger.exception(
                "Unable to render PDF page",
                page=self.state.page_index,
                zoom=self.state.zoom,
            )
            self._view.show_error(str(error))

    def _analyze_document(self, path: Path) -> None:
        self._canonical_document = None
        self._anchor_matches = ()
        self._signature_anchor_match = None
        self._signature_rectangle = None
        self._signature_page_index = None
        self._captured_signature = None
        self._signature_targets = ()
        self._selected_signature_target_id = None
        self._add_signature_box_mode = False
        self._recognized_template = None
        self._pending_manual_rectangle_restore = None
        self._has_unsaved_signature = False
        self._workflow_status = "Analisi documento..."
        self._view.set_manual_signature_mode(False)
        if self._pdf_provider is None or self._anchor_detector is None:
            self._workflow_status = "Analisi non disponibile"
            return

        self._logger.info("PDF anchor analysis started", path=str(path))
        canonical_document = self._pdf_provider.load_document(path)
        self._canonical_document = canonical_document
        recognized_template = self._recognize_template(canonical_document)
        if recognized_template is not None:
            self._recognized_template = recognized_template
            if self._apply_template_anchor(canonical_document, recognized_template):
                is_manual_template = (
                    recognized_template.document_type == "manual_signature_flow"
                )
                self._workflow_status = (
                    f"Documento riconosciuto: {recognized_template.code} | "
                    + (
                        "Box firma appreso: puoi ridisegnarlo se non è corretto"
                        if is_manual_template
                        else "Documento pronto alla firma"
                    )
                )
                self._view.set_manual_signature_mode(is_manual_template)
                self._logger.info(
                    "PDF prepared from recognized template",
                    template=recognized_template.code,
                )
                return

        self._recognized_template = None

        demo_anchor = self._find_demo_signature_anchor(canonical_document)
        if demo_anchor is not None:
            matches, selected_match, expressions = demo_anchor
            self._anchor_matches = matches
            self._signature_anchor_match = selected_match
            signature_rectangle = self._signature_from_anchor(selected_match)
            self._set_signature_targets(
                (
                    SignatureTarget(
                        target_id="signature-1",
                        rectangle=signature_rectangle,
                        page_index=selected_match.page_index,
                        anchor_match=selected_match,
                    ),
                )
            )
            self._workflow_status = (
                "Template non riconosciuto | "
                "Anchor trovato | Zona firma suggerita: puoi ridisegnare il box"
            )
            self._view.set_manual_signature_mode(True)
            self._logger.info(
                "PDF anchors found",
                expressions=expressions,
                matches=len(matches),
                page=selected_match.page_index,
            )
            return

        self._workflow_status = (
            "Documento sconosciuto: disegna il rettangolo firma sul PDF"
        )
        self._view.set_manual_signature_mode(True)
        self._logger.info(
            "PDF anchor analysis completed without matches",
            path=str(path),
        )

    def _find_demo_signature_anchor(
        self, document: Document
    ) -> tuple[tuple[AnchorMatch, ...], AnchorMatch, tuple[str, ...]] | None:
        if self._anchor_detector is None:
            return None
        for expressions in _DEMO_ANCHOR_RULES:
            result = self._anchor_detector.find(
                document,
                AnchorSearchRule(
                    rule_id="demo-anchor",
                    expressions=expressions,
                    options=AnchorSearchOptions(
                        case_sensitive=False,
                        normalize_whitespace=True,
                    ),
                ),
            )
            if result.matches:
                return (
                    result.matches,
                    self._best_signature_anchor_match(document, result.matches),
                    expressions,
                )
        return None

    def set_manual_signature_rectangle(
        self,
        left: float,
        top: float,
        width: float,
        height: float,
        image_width: float,
        image_height: float,
    ) -> None:
        document = self._pdf_service.current_document
        if document is None or not document.page_sizes:
            return
        if width < 8 or height < 8:
            self._view.show_error("Rettangolo firma troppo piccolo")
            return

        self._cancel_wacom_capture()
        page_size = document.page_sizes[self.state.page_index]
        scale_x = page_size.width / image_width
        scale_y = page_size.height / image_height
        self._pending_manual_rectangle_restore = SignatureRectangleSnapshot(
            rectangle=self._signature_rectangle,
            page_index=self._signature_page_index,
            anchor_match=self._signature_anchor_match,
            workflow_status=self._workflow_status,
            targets=self._signature_targets,
            selected_target_id=self._selected_signature_target_id,
        )
        rectangle = Rectangle(
            left * scale_x,
            top * scale_y,
            (left + width) * scale_x,
            (top + height) * scale_y,
        )
        anchor_match = self._signature_anchor_match
        if (
            anchor_match is not None
            and anchor_match.page_index != self.state.page_index
        ):
            anchor_match = None
        if self._add_signature_box_mode and self._signature_targets:
            target = SignatureTarget(
                target_id=self._next_signature_target_id(),
                rectangle=rectangle,
                page_index=self.state.page_index,
                anchor_match=anchor_match,
            )
            self._set_signature_targets(
                (*self._signature_targets, target),
                selected_target_id=target.target_id,
            )
            self._workflow_status = (
                f"Zona firma {len(self._signature_targets)} aggiunta"
            )
        else:
            target = SignatureTarget(
                target_id="signature-1",
                rectangle=rectangle,
                page_index=self.state.page_index,
                anchor_match=anchor_match,
            )
            self._set_signature_targets((target,), selected_target_id=target.target_id)
            self._workflow_status = "Rettangolo firma manuale pronto alla firma"
        self._add_signature_box_mode = False
        self._view.set_manual_signature_mode(False)
        self._logger.info(
            "Manual signature rectangle selected",
            page=self._signature_page_index,
            left=round(self._signature_rectangle.left, 2),
            top=round(self._signature_rectangle.top, 2),
            right=round(self._signature_rectangle.right, 2),
            bottom=round(self._signature_rectangle.bottom, 2),
        )
        self._render_current_page()
        self._view.ask_save_template(
            self.save_manual_template,
            self.cancel_manual_template_change,
        )

    def add_signature_box(self) -> None:
        if self._confirm_unsaved_signature(self._add_signature_box_now):
            return
        self._add_signature_box_now()

    def _add_signature_box_now(self) -> None:
        if self._pdf_service.current_document is None:
            self._view.show_error("Apri prima un PDF")
            return
        self._cancel_wacom_capture()
        self._add_signature_box_mode = True
        self._workflow_status = "Disegna la nuova zona firma sul PDF"
        self._view.set_manual_signature_mode(True)
        self._render_current_page()

    def open_signature_dialog(self, target_id: str | None = None) -> None:
        if target_id is not None and not self._select_signature_target(target_id):
            self._view.show_error("Zona firma non disponibile")
            return
        if target_id is None and len(self._signature_targets) > 1:
            self._view.show_status("seleziona una zona firma sul PDF")
            return
        if self._signature_rectangle is None:
            self._view.show_error("Nessun rettangolo firma disponibile")
            return
        if self._wacom_signature_selected():
            self._capture_wacom_signature()
            return
        self._logger.info("Opening mouse signature dialog")
        canvas_width, canvas_height = self._mouse_signature_canvas_size()
        self._view.open_signature_dialog(
            self.apply_mouse_signature,
            self.log_mouse_signature_clear,
            self.log_mouse_signature_cancel,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )

    def _open_wacom_signature_when_ready(self) -> None:
        if not self._wacom_signature_selected():
            return
        if self._signature_rectangle is None:
            return
        if len(self._signature_targets) > 1:
            self._workflow_status = "Seleziona la zona firma da compilare"
            self._render_current_page()
            return
        self._view.defer_signature_capture(self._capture_wacom_signature)

    def _capture_wacom_signature(self) -> None:
        if self._signature_provider is None:
            self._view.show_error("Firma Wacom non disponibile")
            return
        target = self._selected_signature_target()
        if target is None:
            self._view.show_error("Nessun rettangolo firma disponibile")
            return
        target_id = target.target_id
        self._wacom_capture_generation += 1
        generation = self._wacom_capture_generation
        cancel_event = threading.Event()
        self._wacom_capture_cancel = cancel_event
        self._view.show_status("firma Wacom: firma sulla tavoletta")

        def capture() -> None:
            try:
                signature = self._signature_provider.capture_signature()
            except Exception as error:
                if cancel_event.is_set() or generation != self._wacom_capture_generation:
                    self._logger.info("Wacom signature capture cancelled")
                    return
                error_message = str(error)
                self._logger.warning(
                    "Wacom signature capture unavailable",
                    error=error_message,
                )
                self._view.run_ui_task(
                    lambda: self._view.show_error(
                        f"Firma Wacom non disponibile: {error_message}"
                    )
                )
                return
            if cancel_event.is_set() or generation != self._wacom_capture_generation:
                self._logger.info("Ignoring cancelled Wacom signature capture")
                return
            self._view.run_ui_task(
                lambda: self.apply_wacom_signature(signature, target_id)
            )

        self._view.run_background_task(capture)

    def _cancel_wacom_capture(self) -> None:
        self._wacom_capture_generation += 1
        if self._wacom_capture_cancel is not None:
            self._wacom_capture_cancel.set()
        cancel = getattr(self._signature_provider, "cancel_signature_capture", None)
        if callable(cancel):
            try:
                cancel()
            except Exception as error:
                self._logger.warning(
                    "Unable to cancel Wacom signature capture",
                    error=str(error),
                )

    def log_mouse_signature_clear(self) -> None:
        self._logger.info("Mouse signature cleared")

    def log_mouse_signature_cancel(self) -> None:
        self._logger.info("Mouse signature cancelled")

    def _mouse_signature_canvas_size(self) -> tuple[float, float]:
        rectangle = self._signature_rectangle
        width = 420.0
        if rectangle is None or rectangle.width <= 0 or rectangle.height <= 0:
            return width, 180.0
        height = width / (rectangle.width / rectangle.height)
        return width, max(90.0, min(220.0, height))

    def apply_mouse_signature(self, signature: CapturedSignature) -> None:
        target = self._selected_signature_target()
        self._apply_signature_to_target(
            signature,
            target.target_id if target is not None else None,
            refresh_again=False,
        )

    def apply_wacom_signature(
        self, signature: CapturedSignature, target_id: str | None = None
    ) -> None:
        self._apply_signature_to_target(signature, target_id, refresh_again=True)

    def _apply_signature_to_target(
        self,
        signature: CapturedSignature,
        target_id: str | None,
        refresh_again: bool,
    ) -> None:
        if target_id is None:
            self._replace_selected_target_signature(signature)
        elif not self._replace_target_signature(target_id, signature):
            self._view.show_error("Zona firma non disponibile")
            return
        if self._signature_page_index is not None:
            self.state.page_index = self._signature_page_index
        signed_count = sum(
            1 for target in self._signature_targets if target.signature is not None
        )
        if len(self._signature_targets) > 1:
            self._workflow_status = (
                f"Firma acquisita ({signed_count}/{len(self._signature_targets)})"
            )
        else:
            self._workflow_status = "Firma acquisita e posizionata nel viewer"
        self._logger.info(
            "Mouse signature confirmed",
            bytes=len(signature.content),
            media_type=signature.media_type,
        )
        if self._auto_save_signed_documents_enabled():
            self.save_signed_pdf()
            return
        self._has_unsaved_signature = True
        self._render_current_page()
        if refresh_again:
            self._view.defer_viewer_refresh(self._render_current_page)

    def save_signed_pdf(self) -> None:
        if self._pdf_service.current_document is None:
            self._view.show_error("Nessun PDF aperto")
            return
        if not self._signature_targets:
            self._view.show_error("Nessun rettangolo firma disponibile")
            return
        signed_targets = tuple(
            target for target in self._signature_targets if target.signature is not None
        )
        if not signed_targets:
            self._view.show_error("Nessuna firma acquisita")
            return

        signatures = tuple(
            (
                target.signature,
                SignatureArea(
                    page_index=target.page_index,
                    x=target.rectangle.left,
                    y=target.rectangle.top,
                    width=target.rectangle.width,
                    height=target.rectangle.height,
                ),
            )
            for target in signed_targets
            if target.signature is not None
        )
        try:
            if len(signatures) == 1:
                destination = self._pdf_service.save_signed_preview(
                    signatures[0][0],
                    signatures[0][1],
                )
            else:
                destination = self._pdf_service.save_signed_previews(signatures)
        except Exception as error:
            self._logger.exception("Unable to save signed PDF preview")
            self._view.show_error(str(error))
            return

        erp_upload_context = self._erp_upload_context
        self._workflow_status = f"PDF firmato salvato: {destination}"
        self._has_unsaved_signature = False
        self._logger.info("Signed PDF saved", destination=str(destination))
        if self._pdf_service.current_document is not None:
            self._pdf_service.close_document()
        self.state = PDFViewerState()
        self._canonical_document = None
        self._anchor_matches = ()
        self._signature_anchor_match = None
        self._signature_rectangle = None
        self._signature_page_index = None
        self._captured_signature = None
        self._signature_targets = ()
        self._selected_signature_target_id = None
        self._add_signature_box_mode = False
        self._recognized_template = None
        self._pending_manual_rectangle_restore = None
        self._erp_upload_context = None
        self._view.set_manual_signature_mode(False)
        self._view.clear_document()
        self._view.show_status(f"PDF firmato salvato: {destination}")
        self._queue_signed_pdf_erp_upload(Path(destination), erp_upload_context)

    def _queue_signed_pdf_erp_upload(
        self,
        destination: Path,
        context: ErpSignedDocumentUploadContext | None,
    ) -> None:
        if context is None:
            return
        if self._general_preferences_service is None or self._infinity_dms_client is None:
            self._logger.warning(
                "ERP signed document upload skipped",
                document_id=context.document_id,
                reason="services_not_configured",
            )
            return
        if not context.logical_dir.strip() or not context.logical_name.strip():
            self._logger.warning(
                "ERP signed document upload skipped",
                document_id=context.document_id,
                logical_dir_configured=bool(context.logical_dir.strip()),
                logical_name_configured=bool(context.logical_name.strip()),
            )
            return

        def upload() -> None:
            self._upload_signed_pdf_to_erp(destination, context)

        self._view.run_background_task(upload)

    def _upload_signed_pdf_to_erp(
        self,
        destination: Path,
        context: ErpSignedDocumentUploadContext,
    ) -> None:
        try:
            settings = self._general_preferences_service.get_erp_user_settings()
            service_url = settings.document_service_url.strip()
            username = settings.basic_username.strip()
            password = settings.basic_password
            company = settings.company_id.strip() or "SALAV"
            if not service_url:
                raise RuntimeError("servizio documentale ERP non configurato")
            if not username or not password:
                raise RuntimeError("credenziali documentali ERP non configurate")
            self._logger.info(
                "ERP signed document upload started",
                document_id=context.document_id,
                logical_dir_configured=bool(context.logical_dir.strip()),
                logical_name_configured=bool(context.logical_name.strip()),
            )
            result = self._infinity_dms_client.upload_document(
                service_url=service_url,
                credentials=InfinityDmsCredentials(
                    username=username,
                    password=password,
                    company_id=company,
                ),
                content=destination.read_bytes(),
                logical_dir=context.logical_dir,
                logical_name=context.logical_name,
            )
        except Exception as error:
            self._logger.exception(
                "ERP signed document upload failed",
                document_id=context.document_id,
            )
            self._view.run_ui_task(
                lambda: self._view.show_error(
                    f"invio documento firmato all'ERP fallito: {error}"
                )
            )
            return
        self._logger.info(
            "ERP signed document uploaded",
            document_id=context.document_id,
            result_present=bool(str(result).strip()),
        )
        self._view.run_ui_task(
            lambda: self._view.show_status("PDF firmato inviato all'ERP")
        )

    def _auto_save_signed_documents_enabled(self) -> bool:
        if self._general_preferences_service is None:
            return False
        return (
            self._general_preferences_service.get_supabase_settings()
            .auto_save_signed_documents
        )

    def _wacom_signature_selected(self) -> bool:
        if self._general_preferences_service is None:
            return False
        return (
            self._general_preferences_service.get_supabase_settings()
            .signature_capture_mode
            == "wacom"
        )

    def save_manual_template(self) -> None:
        if self._canonical_document is None or not self._signature_targets:
            self._view.show_error("Nessun modello manuale da salvare")
            return

        self._template_root.mkdir(exist_ok=True)
        template_path = self._template_root / self._manual_template_filename(
            self._canonical_document
        )
        recognition_rules = self._manual_recognition_rules(self._canonical_document)
        anchor_rule, placement_rules = self._manual_template_placement_rules(
            self._canonical_document
        )
        payload = {
            "schema_version": "1.0",
            "template_id": template_path.stem,
            "code": template_path.stem.upper(),
            "name": f"Template provvisorio {template_path.stem}",
            "description": "Template creato dal primo flusso manuale QSign.",
            "document_type": "manual_signature_flow",
            "version": "0.1.0",
            "state": "draft",
            "priority": 5,
            "document_rules": [],
            "recognition_rules": recognition_rules,
            "anchor_rules": [anchor_rule] if anchor_rule is not None else [],
            "placement_rules": placement_rules,
            "settings": {
                "recognition_threshold": 80,
                "ambiguity_margin": 5,
                "normalization_profile": "default",
            },
        }
        template_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._workflow_status = f"Modello salvato: {template_path.name}"
        self._pending_manual_rectangle_restore = None
        self._logger.info("Manual template saved", path=str(template_path))
        self._render_current_page()
        if len(self._signature_targets) == 1:
            self._open_wacom_signature_when_ready()
        else:
            self._workflow_status = "Seleziona la zona firma da compilare"
            self._render_current_page()

    def cancel_manual_template_change(self) -> None:
        snapshot = self._pending_manual_rectangle_restore
        self._pending_manual_rectangle_restore = None
        if snapshot is None or snapshot.rectangle is None:
            self._view.show_status("modello non salvato")
            self._open_wacom_signature_when_ready()
            return

        if snapshot.targets:
            self._set_signature_targets(
                snapshot.targets,
                selected_target_id=snapshot.selected_target_id,
            )
        else:
            self._signature_rectangle = snapshot.rectangle
            self._signature_page_index = snapshot.page_index
            self._signature_anchor_match = snapshot.anchor_match
        self._workflow_status = snapshot.workflow_status
        self._view.set_manual_signature_mode(True)
        if self._signature_page_index is not None:
            self.state.page_index = self._signature_page_index
        self._logger.info(
            "Manual signature rectangle change cancelled",
            page=self._signature_page_index,
        )
        self._render_current_page()

    def _overlays_for_current_page(
        self, image_width: int, image_height: int
    ) -> tuple[AnchorOverlay, ...]:
        document = self._pdf_service.current_document
        if document is None or not document.page_sizes:
            return ()
        if not 0 <= self.state.page_index < len(document.page_sizes):
            return ()

        page_size = document.page_sizes[self.state.page_index]
        scale_x = image_width / page_size.width
        scale_y = image_height / page_size.height
        if self._signature_targets:
            return tuple(
                self._signature_target_overlay(target, scale_x, scale_y)
                for target in self._signature_targets
                if target.page_index == self.state.page_index
            )
        if self._signature_rectangle is not None:
            return ()
        return tuple(
            self._anchor_overlay_from_match(match, scale_x, scale_y)
            for match in self._anchor_matches
            if match.page_index == self.state.page_index
        )

    def _signature_target_overlay(
        self, target: SignatureTarget, scale_x: float, scale_y: float
    ) -> AnchorOverlay:
        index = self._target_display_index(target)
        label = (
            "Zona firma"
            if len(self._signature_targets) == 1
            else f"Zona firma {index}"
        )
        return AnchorOverlay(
            left=target.rectangle.left * scale_x,
            top=target.rectangle.top * scale_y,
            width=target.rectangle.width * scale_x,
            height=target.rectangle.height * scale_y,
            label=label,
            signature_content=(
                target.signature.content if target.signature is not None else None
            ),
            signature_media_type=(
                target.signature.media_type
                if target.signature is not None
                else "image/svg+xml"
            ),
            target_id=target.target_id,
        )

    def _anchor_overlay_from_match(
        self, match: AnchorMatch, scale_x: float, scale_y: float
    ) -> AnchorOverlay:
        rectangle = self._signature_from_anchor(match)
        return AnchorOverlay(
            left=rectangle.left * scale_x,
            top=rectangle.top * scale_y,
            width=rectangle.width * scale_x,
            height=rectangle.height * scale_y,
            label=f"Zona firma: {match.text}",
        )

    def _signature_from_anchor(self, match: AnchorMatch) -> Rectangle:
        document = self._pdf_service.current_document
        if document is None or not document.page_sizes:
            return Rectangle(0, 0, self._DEMO_SIGNATURE_WIDTH, self._DEMO_SIGNATURE_HEIGHT)
        page_width = document.page_sizes[match.page_index].width
        page_height = document.page_sizes[match.page_index].height
        width = self._demo_signature_width(match)
        height = self._demo_signature_height(match)
        left = self._demo_signature_left(match, page_width, width)
        top = self._demo_signature_top(match, page_height, height)
        return Rectangle(
            left,
            top,
            left + width,
            top + height,
        )

    def _best_signature_anchor_match(
        self, document: Document, matches: tuple[AnchorMatch, ...]
    ) -> AnchorMatch:
        return max(
            matches,
            key=lambda match: (
                self._signature_anchor_candidate_score(document, match),
                match.page_index,
                match.bounds.top,
                match.bounds.left,
            ),
        )

    def _signature_anchor_candidate_score(
        self, document: Document, match: AnchorMatch
    ) -> float:
        if not 0 <= match.page_index < len(document.pages):
            return -10000.0
        rectangle = self._signature_from_anchor(match)
        page = document.pages[match.page_index]
        intersecting_words = sum(
            1
            for word in page.words
            if _rectangles_intersect(rectangle, word.bounds)
        )
        nearby_words = sum(
            1
            for word in page.words
            if _rectangles_intersect(
                _expanded_rectangle(rectangle, horizontal=8.0, vertical=8.0),
                word.bounds,
            )
        )
        score = 1000.0
        score -= intersecting_words * 250.0
        score -= max(0, nearby_words - intersecting_words) * 35.0
        score += max(0.0, rectangle.top - match.bounds.bottom)
        return score

    @staticmethod
    def _signature_from_template_anchor(
        match: AnchorMatch, placement: "PlacementRule"
    ) -> Rectangle:
        left = match.bounds.left + placement.x_offset
        top = match.bounds.top + placement.y_offset
        return Rectangle(
            left,
            top,
            left + placement.width,
            top + placement.height,
        )

    def _demo_signature_left(
        self, match: AnchorMatch, page_width: float, width: float
    ) -> float:
        if self._is_worker_acknowledgement_anchor(match):
            left = match.bounds.left + self._WORKER_ACK_SIGNATURE_LEFT_GAP
            return max(0.0, min(left, page_width - width))
        anchor_center = (match.bounds.left + match.bounds.right) / 2
        left = anchor_center - (width / 2)
        return max(0.0, min(left, page_width - width))

    def _demo_signature_top(
        self, match: AnchorMatch, page_height: float, height: float
    ) -> float:
        gap = (
            self._WORKER_ACK_SIGNATURE_TOP_GAP
            if self._is_worker_acknowledgement_anchor(match)
            else self._DEMO_SIGNATURE_TOP_GAP
        )
        top = match.bounds.bottom + gap
        return max(0.0, min(top, page_height - height))

    def _demo_signature_width(self, match: AnchorMatch) -> float:
        if self._is_worker_acknowledgement_anchor(match):
            return self._WORKER_ACK_SIGNATURE_WIDTH
        return self._DEMO_SIGNATURE_WIDTH

    def _demo_signature_height(self, match: AnchorMatch) -> float:
        if self._is_worker_acknowledgement_anchor(match):
            return self._WORKER_ACK_SIGNATURE_HEIGHT
        return self._DEMO_SIGNATURE_HEIGHT

    @staticmethod
    def _is_worker_acknowledgement_anchor(match: AnchorMatch) -> bool:
        return "presa visione" in _normalize(match.text)

    def _first_anchor_on_current_page(self) -> AnchorMatch | None:
        return next(
            (
                match
                for match in self._anchor_matches
                if match.page_index == self.state.page_index
            ),
            None,
        )

    def _recognize_template(self, document: Document) -> Template | None:
        if self._template_repository is None:
            return None
        best_template: Template | None = None
        best_score = 0.0
        for template in self._template_repository.list_templates():
            score = self._template_score(template, document)
            if score > best_score or (
                score == best_score
                and best_template is not None
                and _template_rank(template) > _template_rank(best_template)
            ):
                best_template = template
                best_score = score
        if (
            best_template is not None
            and best_score >= self._effective_recognition_threshold(best_template)
        ):
            self._logger.info(
                "Template recognized",
                template=best_template.code,
                score=best_score,
            )
            return best_template
        return None

    @staticmethod
    def _effective_recognition_threshold(template: Template) -> float:
        if (
            template.document_type == "manual_signature_flow"
            and _has_structural_recognition_rule(template)
        ):
            return min(template.settings.recognition_threshold, 75.0)
        if (
            template.document_type == "manual_signature_flow"
            and len(template.recognition_rules) == 1
            and template.recognition_rules[0].rule_id == "manual-recognition-phrase"
        ):
            return min(template.settings.recognition_threshold, 60.0)
        if (
            template.document_type == "manual_signature_flow"
            and _has_visual_recognition_rule(template)
        ):
            return min(template.settings.recognition_threshold, 70.0)
        return template.settings.recognition_threshold

    def _template_score(self, template: Template, document: Document) -> float:
        rules = template.recognition_rules
        if not rules:
            return 0.0
        document_text = _normalized_document_text(document)
        score = 0.0
        total_weight = 0.0
        filename_score: float | None = None
        has_structural_rule = False
        for rule in rules:
            expression = _normalize(rule.expression)
            if (
                template.document_type == "manual_signature_flow"
                and rule.rule_id == "manual-filename-stem"
            ):
                match_score = _filename_stem_match_score(
                    rule.expression, document.source_path.stem
                )
                filename_score = match_score
            elif (
                template.document_type == "manual_signature_flow"
                and rule.rule_id == "manual-recognition-phrase"
                and _looks_like_filename_fallback(rule.expression)
            ):
                match_score = _filename_stem_match_score(
                    rule.expression, document.source_path.stem
                )
            elif (
                template.document_type == "manual_signature_flow"
                and rule.rule_id == "manual-visual-signature"
            ):
                match_score = _visual_signature_match_score(
                    rule.expression, _visual_signature(document)
                )
            else:
                match_score = _literal_match_score(expression, document_text)
            if (
                template.document_type == "manual_signature_flow"
                and rule.rule_id.startswith("manual-structural-")
            ):
                has_structural_rule = True
            matched = match_score > 0
            if rule.required and not matched:
                return 0.0
            if rule.exclusion and matched:
                return 0.0
            total_weight += rule.weight
            if matched:
                score += rule.weight * match_score
        final_score = (score / total_weight) * 100 if total_weight else 0.0
        if (
            template.document_type == "manual_signature_flow"
            and not has_structural_rule
            and filename_score == 0.0
        ):
            return min(final_score, 75.0)
        return final_score

    def _apply_template_anchor(self, document: Document, template: Template) -> bool:
        targets: list[SignatureTarget] = []
        for anchor_rule in template.anchor_rules:
            if (
                template.document_type == "manual_signature_flow"
                and not _is_supported_relative_anchor_text(anchor_rule.expression)
            ):
                continue
            result = self._anchor_detector.find(
                document,
                self._search_rule_from_template_anchor(anchor_rule),
            )
            if result.matches:
                for placement in template.placement_rules:
                    if placement.anchor_id != anchor_rule.anchor_id:
                        continue
                    if placement.side == "manual":
                        continue
                    match = self._template_anchor_match_for_placement(
                        document, template, result.matches, placement
                    )
                    if match is None:
                        continue
                    targets.append(
                        SignatureTarget(
                            target_id=placement.placement_id,
                            rectangle=self._signature_from_template_anchor(
                                match, placement
                            ),
                            page_index=match.page_index,
                            anchor_match=match,
                            role=placement.role,
                        )
                    )
                    self._anchor_matches = result.matches
        if targets:
            self._set_signature_targets(tuple(targets))
            return True

        if (
            template.document_type == "manual_signature_flow"
            and template.anchor_rules
            and self._apply_manual_fallback_placement(template)
        ):
            return True

        if (
            template.document_type == "manual_signature_flow"
            and _manual_placement_count(template) > 1
            and self._apply_manual_fallback_placement(template)
        ):
            return True

        if template.document_type == "manual_signature_flow":
            demo_anchor = self._find_demo_signature_anchor(document)
            if demo_anchor is not None:
                matches, selected_match, _ = demo_anchor
                self._anchor_matches = matches
                self._signature_anchor_match = selected_match
                self._set_signature_targets(
                    (
                        SignatureTarget(
                            target_id="signature-1",
                            rectangle=self._signature_from_anchor(selected_match),
                            page_index=selected_match.page_index,
                            anchor_match=selected_match,
                        ),
                    )
                )
                return True

        return self._apply_manual_fallback_placement(template)

    def _apply_manual_fallback_placement(self, template: Template) -> bool:
        targets: list[SignatureTarget] = []
        for placement in template.placement_rules:
            if placement.side == "manual":
                targets.append(
                    SignatureTarget(
                        target_id=placement.placement_id,
                        rectangle=Rectangle(
                            placement.x_offset,
                            placement.y_offset,
                            placement.x_offset + placement.width,
                            placement.y_offset + placement.height,
                        ),
                        page_index=placement.page_index or 0,
                        role=placement.role,
                    )
                )
        if targets:
            self._anchor_matches = ()
            self._signature_anchor_match = None
            self._set_signature_targets(tuple(targets))
            return True
        return False

    def _template_anchor_match_for_placement(
        self,
        document: Document,
        template: Template,
        matches: tuple[AnchorMatch, ...],
        placement: PlacementRule,
    ) -> AnchorMatch | None:
        if placement.page_index is not None:
            return next(
                (match for match in matches if match.page_index == placement.page_index),
                None,
            )
        if template.document_type == "manual_signature_flow":
            return self._best_signature_anchor_match(document, matches)
        return matches[0] if matches else None

    @staticmethod
    def _search_rule_from_template_anchor(anchor_rule: AnchorRule) -> AnchorSearchRule:
        return AnchorSearchRule(
            rule_id=anchor_rule.anchor_id,
            expressions=(anchor_rule.expression,),
            options=AnchorSearchOptions(
                case_sensitive=False,
                normalize_whitespace=True,
            ),
        )

    def _manual_template_filename(self, document: Document) -> str:
        return f"learned_{_manual_template_key(document)}.json"

    def _manual_template_placement_rules(
        self, document: Document
    ) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
        if len(self._signature_targets) > 1:
            return None, [
                self._manual_fallback_placement_rule_for_target(target, index)
                for index, target in enumerate(self._signature_targets)
            ]
        fallback = self._manual_fallback_placement_rule()
        anchor = self._manual_selected_anchor_reference()
        if anchor is None:
            anchor = self._manual_anchor_candidate(document)
        if anchor is None or self._signature_rectangle is None:
            return None, [fallback]

        anchor_text, anchor_bounds = anchor
        if not _is_supported_relative_anchor_text(anchor_text):
            return None, [fallback]
        relative_placement = {
            "placement_id": "relative-signature",
            "role": "signer",
            "anchor_id": "manual-learned-anchor",
            "side": "relative",
            "alignment": "manual",
            "x_offset": self._signature_rectangle.left - anchor_bounds.left,
            "y_offset": self._signature_rectangle.top - anchor_bounds.top,
            "width": self._signature_rectangle.width,
            "height": self._signature_rectangle.height,
            "page_index": None,
            "required": True,
        }
        anchor_rule = {
            "anchor_id": "manual-learned-anchor",
            "name": "Anchor appreso manualmente",
            "search_type": "text",
            "expression": anchor_text,
            "scope": "document",
            "occurrence_policy": "first",
            "required": False,
        }
        return anchor_rule, [relative_placement, fallback]

    def _manual_selected_anchor_reference(self) -> tuple[str, Rectangle] | None:
        if (
            self._signature_anchor_match is None
            or self._signature_page_index is None
            or self._signature_anchor_match.page_index != self._signature_page_index
        ):
            return None

        text = self._signature_anchor_match.expression or self._signature_anchor_match.text
        if not _is_supported_relative_anchor_text(text):
            return None
        return text, self._signature_anchor_match.bounds

    def _manual_fallback_placement_rule(self) -> dict[str, object]:
        assert self._signature_rectangle is not None
        target = self._selected_signature_target()
        if target is not None:
            try:
                index = self._signature_targets.index(target)
            except ValueError:
                index = 0
            return self._manual_fallback_placement_rule_for_target(target, index)
        return {
            "placement_id": "manual-signature",
            "role": "signer",
            "anchor_id": "manual",
            "side": "manual",
            "alignment": "manual",
            "x_offset": self._signature_rectangle.left,
            "y_offset": self._signature_rectangle.top,
            "width": self._signature_rectangle.width,
            "height": self._signature_rectangle.height,
            "page_index": self._signature_page_index,
            "required": True,
        }

    def _manual_fallback_placement_rule_for_target(
        self, target: SignatureTarget, index: int
    ) -> dict[str, object]:
        return {
            "placement_id": (
                "manual-signature" if index == 0 else f"manual-signature-{index + 1}"
            ),
            "role": target.role,
            "anchor_id": "manual",
            "side": "manual",
            "alignment": "manual",
            "x_offset": target.rectangle.left,
            "y_offset": target.rectangle.top,
            "width": target.rectangle.width,
            "height": target.rectangle.height,
            "page_index": target.page_index,
            "required": True,
        }

    def _manual_anchor_candidate(
        self, document: Document
    ) -> tuple[str, Rectangle] | None:
        if self._signature_rectangle is None or self._signature_page_index is None:
            return None
        if not 0 <= self._signature_page_index < len(document.pages):
            return None
        page = document.pages[self._signature_page_index]
        lines = _page_text_lines(page)
        if not lines:
            return None

        candidates: list[tuple[float, str, Rectangle]] = []
        for index, (text, bounds) in enumerate(lines):
            if bounds.bottom > self._signature_rectangle.top + 2:
                continue
            distance = self._signature_rectangle.top - bounds.bottom
            if distance > 180:
                continue
            if not _is_horizontally_near(bounds, self._signature_rectangle):
                continue

            combined_text = text
            combined_bounds = bounds
            if index > 0:
                previous_text, previous_bounds = lines[index - 1]
                previous_distance = bounds.top - previous_bounds.bottom
                if 0 <= previous_distance <= 35 and _is_horizontally_near(
                    previous_bounds, self._signature_rectangle
                ):
                    combined_text = f"{previous_text} {text}"
                    combined_bounds = _union_rect(previous_bounds, bounds)

            score = _anchor_candidate_score(combined_text, distance)
            candidates.append((score, combined_text, combined_bounds))

        if not candidates:
            return None
        _, text, bounds = max(candidates, key=lambda item: item[0])
        return text, bounds

    @staticmethod
    def _manual_recognition_rules(document: Document) -> list[dict[str, object]]:
        filename_stem = document.source_path.stem
        phrase = _recognition_phrase(document)
        rules: list[dict[str, object]] = [
            {
                "rule_id": "manual-filename-stem",
                "rule_type": "literal",
                "expression": filename_stem,
                "scope": "document",
                "required": False,
                "weight": 0.25,
            },
            {
                "rule_id": "manual-recognition-phrase",
                "rule_type": "literal",
                "expression": phrase,
                "scope": "document",
                "required": False,
                "weight": 6.0,
            },
        ]
        structural_signature = " ".join(_structural_tokens(document)[:16])
        if structural_signature:
            rules.append(
                {
                    "rule_id": "manual-structural-signature",
                    "rule_type": "literal",
                    "expression": structural_signature,
                    "scope": "document",
                    "required": True,
                    "weight": 10.0,
                }
            )
        visual_signature = _visual_signature(document)
        if visual_signature:
            rules.append(
                {
                    "rule_id": "manual-visual-signature",
                    "rule_type": "visual_hash",
                    "expression": visual_signature,
                    "scope": "document",
                    "required": True,
                    "weight": 30.0,
                }
            )
        for index, token in enumerate(_structural_tokens(document)[:8], start=1):
            rules.append(
                {
                    "rule_id": f"manual-keyword-{index}",
                    "rule_type": "literal",
                    "expression": token,
                    "scope": "document",
                    "required": False,
                    "weight": 1.25,
                }
            )
        return rules


def _normalized_document_text(document: Document) -> str:
    parts = [document.source_path.stem]
    parts.extend(
        word.text
        for page in document.pages
        for word in page.words
    )
    return _normalize(" ".join(parts))


def _recognition_phrase(document: Document) -> str:
    text = " ".join(
        word.text
        for page in document.pages
        for word in page.words[:24]
    )
    return " ".join(text.split())[:160] or document.source_path.stem


def _manual_template_key(document: Document) -> str:
    tokens = _structural_tokens(document)[:8]
    if not tokens:
        filename_type_key = _filename_document_type_key(document.source_path.stem)
        if filename_type_key:
            tokens = (filename_type_key,)
        elif _visual_signature(document):
            tokens = (f"visual_{_visual_signature(document)[:16]}",)
        else:
            tokens = _distinctive_tokens(document.source_path.stem)[:8]
    value = "_".join(tokens) or document.source_path.stem or "manual"
    return re.sub(r"[^a-z0-9_]+", "_", value.casefold()).strip("_") or "manual"


def _structural_tokens(document: Document) -> tuple[str, ...]:
    text = " ".join(
        word.text
        for page in document.pages
        for word in page.words
    )
    return tuple(
        token
        for token in _distinctive_tokens(text)
        if token not in _COMMON_MANUAL_TEMPLATE_TOKENS
        and not token.isdigit()
        and len(token) > 4
        and not re.fullmatch(r"_+", token)
    )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _template_rank(template: Template) -> tuple[int, int, int, str]:
    return (
        template.priority,
        1 if _is_learned_template(template) else 0,
        _template_timestamp(template.template_id),
        template.template_id,
    )


def _is_learned_template(template: Template) -> bool:
    return (
        template.document_type == "manual_signature_flow"
        and template.template_id.startswith("learned_")
    )


def _template_timestamp(template_id: str) -> int:
    match = re.search(r"_(\d{10,})$", template_id)
    return int(match.group(1)) if match else 0


def _has_structural_recognition_rule(template: Template) -> bool:
    return any(
        rule.rule_id.startswith("manual-structural-")
        for rule in template.recognition_rules
    )


def _has_visual_recognition_rule(template: Template) -> bool:
    return any(
        rule.rule_id == "manual-visual-signature"
        for rule in template.recognition_rules
    )


def _manual_placement_count(template: Template) -> int:
    return sum(1 for placement in template.placement_rules if placement.side == "manual")


def _visual_signature(document: Document) -> str:
    return document.metadata.values.get(_VISUAL_SIGNATURE_METADATA_KEY, "").strip()


def _visual_signature_match_score(
    template_signature: str, document_signature: str
) -> float:
    template_clean = re.sub(r"[^0-9a-f]", "", template_signature.casefold())
    document_clean = re.sub(r"[^0-9a-f]", "", document_signature.casefold())
    if not template_clean or len(template_clean) != len(document_clean):
        return 0.0
    try:
        difference = int(template_clean, 16) ^ int(document_clean, 16)
    except ValueError:
        return 0.0
    total_bits = len(template_clean) * 4
    return 1.0 - (difference.bit_count() / total_bits)


def _literal_match_score(expression: str, document_text: str) -> float:
    if not expression:
        return 0.0
    if expression in document_text:
        return 1.0
    tokens = _distinctive_tokens(expression)
    if len(tokens) < 8:
        return 0.0
    document_tokens = set(document_text.split())
    matched_tokens = sum(1 for token in tokens if token in document_tokens)
    return matched_tokens / len(tokens)


def _filename_stem_match_score(template_stem: str, document_stem: str) -> float:
    template_normalized = _normalize_filename_stem(template_stem)
    document_normalized = _normalize_filename_stem(document_stem)
    if template_normalized == document_normalized:
        return 1.0
    template_type_key = _filename_document_type_key(template_stem)
    document_type_key = _filename_document_type_key(document_stem)
    if template_type_key and template_type_key == document_type_key:
        return 1.0
    return 0.0


def _normalize_filename_stem(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _looks_like_filename_fallback(value: str) -> bool:
    normalized = _normalize_filename_stem(value)
    return bool(
        re.search(r"(?:^|_)stampa_[a-z0-9_]+_\d{6,}$", normalized)
        or re.search(r"_\d{6,}$", normalized)
    )


def _filename_document_type_key(value: str) -> str:
    normalized = _normalize_filename_stem(value)
    match = re.search(r"(?:^|_)stampa_([a-z0-9_]+?)_\d{6,}$", normalized)
    if match:
        document_type = re.sub(r"_+", "_", match.group(1)).strip("_")
        if document_type:
            return f"stampa_{document_type}"
    return ""


def _distinctive_tokens(value: str) -> tuple[str, ...]:
    ignored = {
        "con",
        "del",
        "dei",
        "della",
        "delle",
        "gli",
        "per",
        "che",
        "una",
        "uno",
        "alla",
        "alle",
        "sul",
        "sulla",
    }
    tokens = []
    seen = set()
    for token in re.findall(r"[a-zA-Z0-9_]{4,}", value.casefold()):
        if token in ignored or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tuple(tokens)


def _page_text_lines(page: object) -> list[tuple[str, Rectangle]]:
    grouped: dict[tuple[int, int], list[Word]] = {}
    for word in page.words:
        grouped.setdefault((word.block_index, word.line_index), []).append(word)

    lines: list[tuple[str, Rectangle]] = []
    for words in grouped.values():
        sorted_words = sorted(words, key=lambda item: item.bounds.left)
        text = " ".join(word.text for word in sorted_words).strip()
        if not text:
            continue
        bounds = sorted_words[0].bounds
        for word in sorted_words[1:]:
            bounds = _union_rect(bounds, word.bounds)
        lines.append((text, bounds))
    return sorted(lines, key=lambda item: (item[1].top, item[1].left))


def _union_rect(first: Rectangle, second: Rectangle) -> Rectangle:
    return Rectangle(
        min(first.left, second.left),
        min(first.top, second.top),
        max(first.right, second.right),
        max(first.bottom, second.bottom),
    )


def _expanded_rectangle(
    rectangle: Rectangle, horizontal: float, vertical: float
) -> Rectangle:
    return Rectangle(
        rectangle.left - horizontal,
        rectangle.top - vertical,
        rectangle.right + horizontal,
        rectangle.bottom + vertical,
    )


def _rectangles_intersect(first: Rectangle, second: Rectangle) -> bool:
    return (
        first.left < second.right
        and first.right > second.left
        and first.top < second.bottom
        and first.bottom > second.top
    )


def _is_horizontally_near(anchor: Rectangle, target: Rectangle) -> bool:
    expanded_left = target.left - 120
    expanded_right = target.right + 120
    anchor_center = (anchor.left + anchor.right) / 2
    return expanded_left <= anchor_center <= expanded_right


def _anchor_candidate_score(text: str, distance: float) -> float:
    normalized = _normalize(text)
    if not _is_supported_relative_anchor_text(normalized):
        return -1000.0
    score = max(0.0, 180.0 - distance)
    if "in fede" in normalized:
        score += 220.0
    if "interessato" in normalized:
        score += 180.0
    if _contains_supported_signature_phrase(normalized):
        score += 160.0
    if "presa visione" in normalized:
        score += 200.0
    if "lavoratore" in normalized:
        score += 120.0
    if _looks_like_person_name(text):
        score -= 120.0
    return score


def _is_supported_relative_anchor_text(text: str) -> bool:
    normalized = _normalize(text)
    return (
        "in fede" in normalized
        or "interessato" in normalized
        or "presa visione" in normalized
        or _contains_supported_signature_phrase(normalized)
    )


def _contains_supported_signature_phrase(normalized: str) -> bool:
    return (
        "firma cliente" in normalized
        or "firma del cliente" in normalized
        or "firma dell interessato" in normalized
        or "firma dell'interessato" in normalized
        or "firma lavoratore" in normalized
        or "firma del lavoratore" in normalized
    )


def _looks_like_person_name(text: str) -> bool:
    tokens = [token for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}", text)]
    if len(tokens) < 2:
        return False
    uppercase_tokens = [token for token in tokens if token.upper() == token]
    return len(uppercase_tokens) == len(tokens)
