"""Composition root for the current desktop shell."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from app.pdf_viewer_controller import PDFViewerController
from app.services.certificate_service import CertificateService
from app.services.general_preferences_service import GeneralPreferencesService
from app.services.infinity_dms_client import InfinityDmsClient
from services.anchors.anchor_detector import AnchorDetector
from services.logging.logging_service import LoggingService
from services.pdf.pdf_service import PDFService
from services.pdf.providers.pymupdf_renderer import (
    PyMuPDFDocumentBackend,
    PyMuPDFRenderer,
)
from services.pdf.providers.pymupdf_provider import PyMuPDFProvider
from services.pdf.providers.pymupdf_signature_writer import PyMuPDFSignatureWriter
from services.pdf.providers.pyhanko_digital_signature_writer import (
    PyHankoDigitalSignatureWriter,
)
from services.templates.template_repository import FilesystemTemplateRepository
from services.templates.supabase_template_sync_service import (
    SupabaseTemplateSyncService,
    SupabaseTemplateSyncServiceError,
)
from services.wacom.providers.stu430_provider import STU430Provider

if TYPE_CHECKING:
    import flet as ft


class QSignApplication:
    """Build the UI and inject application-level callbacks."""

    def __init__(self, logger: LoggingService | None = None) -> None:
        self._logger = logger or LoggingService.create("qsign")

    def main(self, page: "ft.Page") -> None:
        """Configure the QSign desktop window."""
        try:
            self._main(page)
        except Exception:
            self._logger.exception("QSign desktop shell failed during startup")
            raise

    def _main(self, page: "ft.Page") -> None:
        from ui.main_view import MainView

        self._logger.info("Starting QSign desktop shell")
        renderer = PyMuPDFRenderer(logger=self._logger)
        pdf_provider = PyMuPDFProvider(logger=self._logger)
        anchor_detector = AnchorDetector(logger=self._logger)
        template_repository = FilesystemTemplateRepository("templates")
        certificate_service = CertificateService()
        general_preferences_service = GeneralPreferencesService(logger=self._logger)
        infinity_dms_client = InfinityDmsClient(logger=self._logger)
        template_sync_service = SupabaseTemplateSyncService(
            preferences_service=general_preferences_service,
            template_root="templates",
            logger=self._logger,
        )
        self._logger.info("QSign services initialized")
        digital_signature_writer = PyHankoDigitalSignatureWriter(
            certificate_service=certificate_service,
            logger=self._logger,
            metadata_provider=certificate_service.get_signature_metadata,
            visible_text_provider=lambda: general_preferences_service.get_supabase_settings().show_signature_text,
        )
        signature_writer = PyMuPDFSignatureWriter(
            logger=self._logger,
            digital_signature_writer=digital_signature_writer,
        )
        pdf_service = PDFService(
            backend=PyMuPDFDocumentBackend(renderer),
            renderer=renderer,
            signature_writer=signature_writer,
            logger=self._logger,
        )
        view = MainView(
            page=page,
            certificate_service=certificate_service,
            general_preferences_service=general_preferences_service,
            template_sync_service=template_sync_service,
            infinity_dms_client=infinity_dms_client,
        )
        controller = PDFViewerController(
            pdf_service=pdf_service,
            view=view,
            logger=self._logger,
            pdf_provider=pdf_provider,
            anchor_detector=anchor_detector,
            template_repository=template_repository,
            general_preferences_service=general_preferences_service,
            signature_provider=self._create_signature_provider(),
        )
        view.bind_actions(
            on_open_document=controller.open_document,
            on_close=controller.close_document,
            on_previous=controller.previous_page,
            on_next=controller.next_page,
            on_zoom_in=controller.zoom_in,
            on_zoom_out=controller.zoom_out,
            on_save_signed_pdf=controller.save_signed_pdf,
            on_manual_signature_rect=controller.set_manual_signature_rectangle,
            on_add_signature_box=controller.add_signature_box,
            on_signature_area_click=controller.open_signature_dialog,
        )
        self._logger.info("QSign view initialized")
        view.prepare_window_shell()
        self._sync_templates_on_startup(
            general_preferences_service,
            template_sync_service,
        )
        self._logger.info("QSign building view")
        view.build()
        view.maximize_window()
        view.start_erp_auto_refresh()
        view.show_startup_user_confirmation()
        self._logger.info("QSign desktop shell ready")
        self._bind_shutdown(page, controller, view)

    def _create_signature_provider(self) -> STU430Provider | None:
        try:
            return STU430Provider()
        except Exception as error:
            self._logger.warning("Wacom STU provider unavailable", error=str(error))
            return None

    def _bind_shutdown(
        self,
        page: "ft.Page",
        controller: PDFViewerController,
        view: object | None = None,
    ) -> None:
        def shutdown(_: object | None = None) -> None:
            self._logger.info("QSign shutdown requested")
            stop_background_tasks = getattr(view, "stop_background_tasks", None)
            if callable(stop_background_tasks):
                stop_background_tasks()
            controller.shutdown()

        page.on_close = shutdown
        window = getattr(page, "window", None)
        if window is None:
            return
        if hasattr(window, "prevent_close"):
            window.prevent_close = True
        if hasattr(window, "on_event"):
            window.on_event = lambda event: self._handle_window_event(
                event,
                page,
                controller,
                view,
                shutdown,
            )
        update = getattr(page, "update", None)
        if callable(update):
            update()

    def _handle_window_event(
        self,
        event: object,
        page: "ft.Page",
        controller: PDFViewerController,
        view: object | None,
        shutdown: Callable[[object | None], None],
    ) -> None:
        event_type = getattr(event, "type", "")
        event_value = getattr(event_type, "value", event_type)
        if event_value != "close":
            return
        if self._window_close_needs_confirmation(controller, view):
            view.ask_discard_signed_document(
                lambda: self._shutdown_and_destroy(page, shutdown, event),
                lambda: None,
            )
            return
        self._shutdown_and_destroy(page, shutdown, event)

    def _window_close_needs_confirmation(
        self, controller: PDFViewerController, view: object | None
    ) -> bool:
        has_unsaved = getattr(controller, "has_unsaved_signed_document", None)
        ask_discard = getattr(view, "ask_discard_signed_document", None)
        return callable(has_unsaved) and bool(has_unsaved()) and callable(ask_discard)

    def _shutdown_and_destroy(
        self,
        page: "ft.Page",
        shutdown: Callable[[object | None], None],
        event: object,
    ) -> None:
        shutdown(event)
        window = getattr(page, "window", None)
        destroy = getattr(window, "destroy", None)
        if not callable(destroy):
            return
        run_task = getattr(page, "run_task", None)
        if callable(run_task):
            async def destroy_safely() -> None:
                try:
                    await destroy()
                except RuntimeError as error:
                    if str(error) == "Session closed":
                        self._logger.warning("Window destroy skipped: session closed")
                        return
                    raise

            run_task(destroy_safely)
            return
        self._logger.warning("Window destroy requested but page cannot run tasks")

    @staticmethod
    def _set_window_visible(page: "ft.Page", visible: bool) -> None:
        window = getattr(page, "window", None)
        if window is None or not hasattr(window, "visible"):
            return
        window.visible = visible
        update = getattr(page, "update", None)
        if callable(update):
            update()

    def _sync_templates_on_startup(
        self,
        general_preferences_service: GeneralPreferencesService,
        template_sync_service: SupabaseTemplateSyncService,
    ) -> None:
        settings = general_preferences_service.get_supabase_settings()
        if not settings.auto_sync_templates_on_startup:
            return
        try:
            result = template_sync_service.sync_templates()
        except SupabaseTemplateSyncServiceError as error:
            self._logger.warning("Startup template sync failed", error=str(error))
            return
        self._logger.info(
            "Startup template sync completed",
            uploaded=result.uploaded,
            downloaded=result.downloaded,
            skipped=result.skipped,
        )
