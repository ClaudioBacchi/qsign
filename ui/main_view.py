"""Minimal Flet shell for Milestone 1."""

import asyncio
import base64
import inspect
import locale
import re
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.services.certificate_service import (
    CertificateInfo,
    CertificateService,
    CertificateServiceError,
)
from app.services.general_preferences_service import (
    ErpDocument,
    ErpUser,
    ErpUserSettings,
    ErpUsersResult,
    GeneralPreferencesService,
    SupabaseSettings,
)
from app.services.infinity_dms_client import (
    InfinityDmsClient,
    InfinityDmsCredentials,
)
from services.signature.signature_service import CapturedSignature
from services.templates.supabase_template_sync_service import (
    SupabaseTemplateSyncService,
    SupabaseTemplateSyncServiceError,
)

if TYPE_CHECKING:
    import flet as ft


def _checked(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _integer_or_default(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_erp_document_filename(value: str) -> str:
    name = Path(value).name.strip()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = name.strip(" .") or "documento.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


class AnchorOverlayViewModel(Protocol):
    """Presentation-only rectangle projected onto the rendered page."""

    left: float
    top: float
    width: float
    height: float
    label: str
    signature_content: bytes | None
    signature_media_type: str
    target_id: str | None


class MainView:
    """Build controls and expose presentation-only updates."""

    APP_TITLE = "qSign by Queen Srl - queensrl.net"
    _MOUSE_WHEEL_PAGE_THRESHOLD = 40.0

    def __init__(
        self,
        page: "ft.Page",
        certificate_service: CertificateService | None = None,
        general_preferences_service: GeneralPreferencesService | None = None,
        template_sync_service: SupabaseTemplateSyncService | None = None,
        infinity_dms_client: InfinityDmsClient | None = None,
        signed_history_directory: str | Path = Path("dist") / "signed",
        learned_template_directory: str | Path = "templates",
        app_config_path: str | Path = Path("config") / "app.yaml",
        erp_temp_base_directory: str | Path | None = None,
        erp_temp_session_id: str | None = None,
        on_general_preferences_saved: Callable[[SupabaseSettings], None] | None = None,
    ) -> None:
        import flet as ft
        import flet.canvas as cv

        self._ft = ft
        self._cv = cv
        self._page = page
        self._certificate_service = certificate_service
        self._general_preferences_service = general_preferences_service
        self._template_sync_service = template_sync_service
        self._infinity_dms_client = infinity_dms_client
        self._on_general_preferences_saved = on_general_preferences_saved
        self._signed_history_directory = Path(signed_history_directory)
        self._learned_template_directory = Path(learned_template_directory)
        self._app_config_path = Path(app_config_path)
        self._erp_temp_base_directory = (
            Path(erp_temp_base_directory)
            if erp_temp_base_directory is not None
            else self._default_erp_temp_base()
        )
        self._erp_temp_session_id = erp_temp_session_id or uuid.uuid4().hex
        self._erp_temp_session_root: Path | None = None
        self._on_open_document: Callable[[str], None] | None = None
        self._on_close: Callable[[], None] | None = None
        self._on_previous: Callable[[], None] | None = None
        self._on_next: Callable[[], None] | None = None
        self._on_zoom_in: Callable[[], None] | None = None
        self._on_zoom_out: Callable[[], None] | None = None
        self._on_save_signed_pdf: Callable[[], None] | None = None
        self._on_add_signature_box: Callable[[], None] | None = None
        self._on_signature_area_click: Callable[[str | None], None] | None = None
        self._on_manual_signature_rect: (
            Callable[[float, float, float, float, float, float], None] | None
        ) = None
        self._manual_signature_mode = False
        self._manual_drag_start: tuple[float, float] | None = None
        self._manual_draft_rect: tuple[float, float, float, float] | None = None
        self._mouse_wheel_page_delta = 0.0
        self._active_dialog: object | None = None
        self._admin_mode = False
        self._security_button: object | None = None
        self._erp_auto_refresh_stop = threading.Event()
        self._erp_auto_refresh_thread: threading.Thread | None = None
        self._erp_auto_refresh_lock = threading.Lock()
        self._erp_session_user_confirmed = False
        self._erp_download_lock = threading.Lock()
        self._closing = threading.Event()
        self._erp_user_generation_lock = threading.Lock()
        self._erp_user_generation = 0
        self._erp_generation_user_id = self._current_selected_erp_user_id()
        self._erp_documents_request_lock = threading.Lock()
        self._erp_documents_request_generation = 0
        self._erp_documents_active_generation = 0
        self._erp_documents_in_flight = False
        self._erp_documents_pending = False
        self._erp_temp_files: set[Path] = set()
        self._cleanup_orphaned_erp_temp_files()
        self._window_icon_configured = False
        self._signature_strokes: list[list[tuple[float, float]]] = []
        self._current_signature_stroke: list[tuple[float, float]] | None = None
        self._signature_preview = ft.Image(
            src="",
            width=420,
            height=180,
            fit=ft.BoxFit.CONTAIN,
        )
        self._signature_paint = ft.Paint(
            color=ft.Colors.BLACK,
            stroke_width=3,
            stroke_cap=ft.StrokeCap.ROUND,
            anti_alias=True,
            style=ft.PaintingStyle.STROKE,
        )
        self._signature_canvas = cv.Canvas(
            shapes=[],
            width=420,
            height=180,
        )
        self._file_picker = ft.FilePicker()
        self._pfx_file_picker = ft.FilePicker()
        self._document_name = ft.Text("Nessun documento")
        self._page_count = ft.Text("Pagina — / —")
        self._zoom = ft.Text("Zoom: 100%")
        self._active_user = ft.Text(self._active_user_status_text())
        self._document_status = ft.Text(self._certificate_status_text())
        self._viewer_placeholder = ft.GestureDetector(
            content=ft.Container(
                content=ft.Image(
                    src=self._image_data_uri("images/logo_qsign_grande.png"),
                    width=680,
                    fit=ft.BoxFit.CONTAIN,
                    semantics_label="QSign",
                ),
                padding=20,
            ),
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap=self._open_queen_site,
            tooltip="Apri queensrl.net",
        )
        self._home_view = ft.Container(
            content=self._viewer_placeholder,
            alignment=ft.Alignment(0, 0),
            expand=True,
            bgcolor=ft.Colors.WHITE,
        )
        self._pdf_image = ft.Image(
            src="data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "YAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
            visible=False,
            fit=ft.BoxFit.FILL,
            error_content=ft.Text(
                "Impossibile visualizzare la pagina PDF",
                color=ft.Colors.RED_700,
            ),
        )
        self._pdf_stack = ft.Stack(
            controls=[self._pdf_image],
            visible=False,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        self._manual_draft_overlay = self._build_manual_overlay(0, 0, 0, 0)
        self._manual_draft_overlay.visible = False
        self._signature_surface = ft.GestureDetector(
            content=self._pdf_stack,
            on_pan_start=self._start_manual_signature_drag,
            on_pan_update=self._update_manual_signature_drag,
            on_pan_end=self._finish_manual_signature_drag,
            on_scroll=self._handle_pdf_mouse_wheel,
        )
        self._horizontal_viewer = ft.Row(
            controls=[self._signature_surface],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.START,
            scroll=ft.ScrollMode.AUTO,
        )
        self._document_viewer = ft.ListView(
            controls=[self._horizontal_viewer],
            scroll=ft.ScrollMode.AUTO,
            padding=20,
            expand=True,
            visible=False,
        )
        self._viewer_layers = ft.Stack(
            controls=[self._home_view, self._document_viewer],
            expand=True,
            fit=ft.StackFit.EXPAND,
        )

    def bind_actions(
        self,
        on_open_document: Callable[[str], None],
        on_close: Callable[[], None],
        on_previous: Callable[[], None],
        on_next: Callable[[], None],
        on_zoom_in: Callable[[], None],
        on_zoom_out: Callable[[], None],
        on_save_signed_pdf: Callable[[], None] | None = None,
        on_manual_signature_rect: (
            Callable[[float, float, float, float, float, float], None] | None
        ) = None,
        on_add_signature_box: Callable[[], None] | None = None,
        on_signature_area_click: Callable[[str | None], None] | None = None,
    ) -> None:
        """Bind controller actions without exposing Flet outside the view."""
        self._on_open_document = on_open_document
        self._on_close = on_close
        self._on_previous = on_previous
        self._on_next = on_next
        self._on_zoom_in = on_zoom_in
        self._on_zoom_out = on_zoom_out
        self._on_save_signed_pdf = on_save_signed_pdf
        self._on_manual_signature_rect = on_manual_signature_rect
        self._on_add_signature_box = on_add_signature_box
        self._on_signature_area_click = on_signature_area_click

    def build(self) -> None:
        ft = self._ft
        self.prepare_window_shell()
        self._page.padding = 0
        self._page.services.append(self._file_picker)
        self._page.services.append(self._pfx_file_picker)
        toolbar = ft.Column(
            controls=[
                self._build_menu_bar(),
                self._build_icon_toolbar(),
            ],
            tight=True,
            spacing=4,
        )
        viewer = ft.Container(
            content=self._viewer_layers,
            expand=True,
            bgcolor=ft.Colors.GREY_200,
        )
        status_bar = ft.Container(
            content=ft.Row(
                controls=[
                    self._document_name,
                    self._page_count,
                    self._zoom,
                    self._active_user,
                    self._document_status,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=10,
        )
        self._page.add(
            ft.Column(
                controls=[
                    ft.Container(content=toolbar, padding=10),
                    viewer,
                    status_bar,
                ],
                expand=True,
                spacing=0,
            )
        )

    def start_erp_auto_refresh(self) -> None:
        if (
            self._erp_auto_refresh_thread is not None
            and self._erp_auto_refresh_thread.is_alive()
        ):
            return
        self._erp_auto_refresh_stop.clear()
        self._erp_auto_refresh_thread = threading.Thread(
            target=self._erp_auto_refresh_loop,
            name="qsign-erp-auto-refresh",
            daemon=True,
        )
        self._erp_auto_refresh_thread.start()

    def stop_background_tasks(self) -> None:
        self._closing.set()
        self._erp_auto_refresh_stop.set()
        thread = self._erp_auto_refresh_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)
        self._cleanup_erp_temp_files()

    def _cleanup_erp_temp_files(self) -> None:
        session_root = self._erp_temp_session_root
        for path in tuple(self._erp_temp_files):
            try:
                if self._is_own_erp_temp_path(path):
                    path.unlink(missing_ok=True)
            except OSError:
                continue
            finally:
                self._erp_temp_files.discard(path)
        if session_root is not None:
            try:
                session_root.rmdir()
            except OSError:
                pass

    def _cleanup_orphaned_erp_temp_files(self) -> None:
        self._cleanup_erp_temp_files()

    def _erp_auto_refresh_loop(self) -> None:
        while not self._erp_auto_refresh_stop.is_set():
            settings = (
                self._general_preferences_service.get_supabase_settings()
                if self._general_preferences_service is not None
                else SupabaseSettings()
            )
            wait_seconds = (
                settings.erp_refresh_interval_seconds
                if settings.list_erp_documents and settings.auto_refresh_erp_documents
                else 5
            )
            if self._erp_auto_refresh_stop.wait(wait_seconds):
                break
            self._refresh_erp_documents_if_auto_allowed()

    def _refresh_erp_documents_if_auto_allowed(self) -> bool:
        if self._general_preferences_service is None:
            return False
        settings = self._general_preferences_service.get_supabase_settings()
        if not settings.list_erp_documents or not settings.auto_refresh_erp_documents:
            return False
        if not self._erp_session_user_confirmed:
            return False
        if self._document_viewer.visible or not self._home_view.visible:
            return False
        erp_settings = self._general_preferences_service.get_erp_user_settings()
        if (
            not erp_settings.documents_url.strip()
            or not erp_settings.selected_user_id.strip()
        ):
            return False
        if not self._erp_auto_refresh_lock.acquire(blocking=False):
            return False

        def refresh() -> None:
            try:
                if not self._document_viewer.visible and self._home_view.visible:
                    self.refresh_erp_documents()
            finally:
                self._erp_auto_refresh_lock.release()

        self.run_ui_task(refresh)
        return True

    def prepare_window_shell(self) -> None:
        self._page.title = self.APP_TITLE
        self._configure_window_icon()

    def maximize_window(self) -> None:
        window = getattr(self._page, "window", None)
        if window is None or not hasattr(window, "maximized"):
            return
        window.maximized = True

    def activate_window(self) -> None:
        window = getattr(self._page, "window", None)
        if window is None:
            return
        if hasattr(window, "visible"):
            window.visible = True
        if hasattr(window, "minimized"):
            window.minimized = False
        if hasattr(window, "focused"):
            window.focused = True
        for method_name in ("to_front", "focus"):
            method = getattr(window, method_name, None)
            if callable(method):
                try:
                    result = method()
                except Exception:
                    continue
                self._await_if_needed(result)
        update = getattr(self._page, "update", None)
        if callable(update):
            update()

    def _await_if_needed(self, value: object) -> None:
        if not inspect.isawaitable(value):
            return

        async def await_value() -> None:
            await value

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            run_task = getattr(self._page, "run_task", None)
            if callable(run_task):
                run_task(await_value)
                return
            asyncio.run(await_value())
            return
        loop.create_task(await_value())

    def _build_menu_bar(self) -> object:
        ft = self._ft
        menu_style = ft.MenuStyle(
            bgcolor=ft.Colors.TRANSPARENT,
            elevation=0,
            padding=0,
        )
        menu_button_style = ft.ButtonStyle(
            bgcolor=ft.Colors.TRANSPARENT,
            elevation=0,
            padding=ft.Padding(left=8, top=6, right=8, bottom=6),
        )
        menu_item_width = 180
        return ft.MenuBar(
            style=menu_style,
            controls=[
                ft.SubmenuButton(
                    content=ft.Text("Documenti"),
                    style=menu_button_style,
                    controls=[
                        ft.MenuItemButton(
                            content=ft.Text("Apri"),
                            width=menu_item_width,
                            on_click=self._pick_pdf,
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Chiudi"),
                            width=menu_item_width,
                            on_click=lambda _: self._invoke(self._on_close),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Salva"),
                            width=menu_item_width,
                            on_click=lambda _: self._invoke(self._on_save_signed_pdf),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Storico"),
                            width=menu_item_width,
                            on_click=lambda _: self.show_signed_history(),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Template"),
                            width=menu_item_width,
                            on_click=lambda _: self.show_template_history(),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Aggiungi zona firma"),
                            width=menu_item_width,
                            on_click=lambda _: self._invoke(
                                self._on_add_signature_box
                            ),
                        ),
                    ],
                ),
                ft.SubmenuButton(
                    content=ft.Text("Preferenze"),
                    style=menu_button_style,
                    controls=[
                        ft.MenuItemButton(
                            content=ft.Text("Impostazioni"),
                            width=menu_item_width,
                            on_click=lambda _: self.show_general_preferences(),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Connessione ERP"),
                            width=menu_item_width,
                            on_click=lambda _: self.show_user_preferences(),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Certificato"),
                            width=menu_item_width,
                            on_click=lambda _: self.show_certificate_preferences(),
                        ),
                    ],
                ),
                ft.MenuItemButton(
                    content=ft.Text("Informazioni"),
                    style=menu_button_style,
                    on_click=lambda _: self.show_information(),
                ),
            ],
        )

    def _build_icon_toolbar(self) -> object:
        ft = self._ft
        self._security_button = ft.IconButton(
            icon=ft.Icons.LOCK_OPEN if self._admin_mode else ft.Icons.LOCK,
            tooltip=(
                "Modalità amministratore attiva"
                if self._admin_mode
                else "Sblocca impostazioni amministratore"
            ),
            on_click=lambda _: self.show_admin_unlock_dialog(),
        )
        return ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.FILE_OPEN,
                    tooltip="Apri",
                    on_click=self._pick_pdf,
                ),
                ft.IconButton(
                    icon=ft.Icons.SAVE,
                    tooltip="Salva",
                    on_click=lambda _: self._invoke(self._on_save_signed_pdf),
                ),
                ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    tooltip="Chiudi",
                    on_click=lambda _: self._invoke(self._on_close),
                ),
                ft.IconButton(
                    icon=ft.Icons.ARCHIVE,
                    tooltip="Storico",
                    on_click=lambda _: self.show_signed_history(),
                ),
                ft.IconButton(
                    icon=ft.Icons.ADD,
                    tooltip="Aggiungi zona firma",
                    on_click=lambda _: self._invoke(self._on_add_signature_box),
                ),
                ft.VerticalDivider(width=12),
                ft.IconButton(
                    icon=ft.Icons.CHEVRON_LEFT,
                    tooltip="Pagina precedente",
                    on_click=lambda _: self._invoke(self._on_previous),
                ),
                ft.IconButton(
                    icon=ft.Icons.CHEVRON_RIGHT,
                    tooltip="Pagina successiva",
                    on_click=lambda _: self._invoke(self._on_next),
                ),
                ft.VerticalDivider(width=12),
                ft.IconButton(
                    icon=ft.Icons.ZOOM_OUT,
                    tooltip="Zoom -",
                    on_click=lambda _: self._invoke(self._on_zoom_out),
                ),
                ft.IconButton(
                    icon=ft.Icons.ZOOM_IN,
                    tooltip="Zoom +",
                    on_click=lambda _: self._invoke(self._on_zoom_in),
                ),
                ft.Container(expand=True),
                self._security_button,
            ],
            spacing=2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    async def _pick_pdf(self, _: object) -> None:
        files = await self._file_picker.pick_files(
            dialog_title="Apri documento PDF",
            file_type=self._ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["pdf"],
            allow_multiple=False,
        )
        if files and files[0].path and self._on_open_document is not None:
            self._on_open_document(files[0].path)

    def display_document(
        self,
        filename: str,
        image_content: bytes,
        image_width: int,
        image_height: int,
        page_number: int,
        page_count: int,
        zoom: float,
        anchor_overlays: tuple[AnchorOverlayViewModel, ...] = (),
        anchor_count: int = 0,
        selected_anchor: object | None = None,
        workflow_status: str = "",
    ) -> None:
        """Display renderer output without knowing which engine produced it."""
        image_source = base64.b64encode(image_content).decode("ascii")
        self._pdf_image.src = f"data:image/png;base64,{image_source}"
        self._pdf_image.width = image_width
        self._pdf_image.height = image_height
        self._pdf_image.visible = True
        self._pdf_stack.width = image_width
        self._pdf_stack.height = image_height
        self._pdf_stack.visible = True
        self._signature_surface.width = image_width
        self._signature_surface.height = image_height
        self._pdf_stack.controls = [
            self._pdf_image,
            *self._build_anchor_overlay_controls(anchor_overlays),
            self._manual_draft_overlay,
        ]
        self._mouse_wheel_page_delta = 0.0
        self._viewer_placeholder.visible = False
        self._home_view.visible = False
        self._document_viewer.visible = True
        self._document_name.value = filename
        self._page_count.value = f"Pagina {page_number} / {page_count}"
        self._zoom.value = f"Zoom: {zoom:.0%}"
        self._document_status.value = workflow_status or self._anchor_status(
            anchor_count=anchor_count,
            selected_anchor=selected_anchor,
        )
        self._page.update()

    def show_status(self, message: str) -> None:
        self._document_status.value = f"Stato: {message}"
        self._page.update()

    def defer_signature_capture(self, callback: Callable[[], None]) -> None:
        async def delayed_capture() -> None:
            await asyncio.sleep(0.5)
            callback()

        run_task = getattr(self._page, "run_task", None)
        if callable(run_task):
            run_task(delayed_capture)
            return
        callback()

    def defer_viewer_refresh(self, callback: Callable[[], None]) -> None:
        async def delayed_refresh() -> None:
            await asyncio.sleep(0.2)
            callback()

        run_task = getattr(self._page, "run_task", None)
        if callable(run_task):
            run_task(delayed_refresh)
            return
        callback()

    def run_background_task(self, callback: Callable[[], None]) -> None:
        threading.Thread(target=callback, daemon=True).start()

    def run_ui_task(self, callback: Callable[[], None]) -> None:
        async def invoke() -> None:
            callback()

        run_task = getattr(self._page, "run_task", None)
        if callable(run_task):
            run_task(invoke)
            return
        callback()

    def show_certificate_status(self) -> None:
        self._document_status.value = self._certificate_status_text()
        self._page.update()

    def show_active_user_status(self) -> None:
        if self._general_preferences_service is not None:
            self._sync_erp_user_generation_from_settings(
                self._general_preferences_service.get_erp_user_settings()
            )
        self._active_user.value = self._active_user_status_text()
        self._page.update()

    def refresh_erp_documents(self) -> bool:
        if self._closing.is_set():
            return False
        if self._general_preferences_service is None:
            self._show_local_home_placeholder(update=False)
            return False
        settings = self._general_preferences_service.get_erp_user_settings()
        self._sync_erp_user_generation_from_settings(settings)
        if not settings.documents_url.strip() or not settings.selected_user_id.strip():
            self._show_local_home_placeholder(update=False)
            return False
        selected_user_id, user_generation = self._erp_user_context()
        settings_key = self._erp_documents_settings_key(settings)
        with self._erp_documents_request_lock:
            if self._erp_documents_in_flight:
                self._erp_documents_pending = True
                return True
            self._erp_documents_request_generation += 1
            request_generation = self._erp_documents_request_generation
            self._erp_documents_active_generation = request_generation
            self._erp_documents_in_flight = True
            self._erp_documents_pending = False
        self._show_erp_documents_loading()

        def load_documents() -> None:
            try:
                result = self._general_preferences_service.fetch_erp_documents(
                    settings
                )
            except Exception:
                result = ErpDocumentsResult(
                    False,
                    "Connessione ERP documenti fallita",
                    (),
                )
            self.run_ui_task(
                lambda: self._finish_erp_documents_refresh(
                    request_generation,
                    selected_user_id,
                    user_generation,
                    settings_key,
                    result,
                )
            )

        self.run_background_task(load_documents)
        return True

    def _show_erp_documents_loading(self) -> None:
        ft = self._ft
        self._home_view.content = ft.Container(
            padding=24,
            expand=True,
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text(
                                "Documenti ERP da firmare",
                                weight=ft.FontWeight.BOLD,
                                size=18,
                            ),
                            ft.Container(expand=True),
                            ft.OutlinedButton(
                                "Aggiorna",
                                disabled=True,
                                on_click=lambda _: self.refresh_erp_documents(),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=ft.Text("Caricamento documenti..."),
                        expand=True,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    ),
                    ft.Text(""),
                ],
                spacing=12,
                expand=True,
            ),
        )
        self._viewer_placeholder.visible = False
        if self._home_view.visible:
            self._page.update()

    def _finish_erp_documents_refresh(
        self,
        request_generation: int,
        expected_user_id: str,
        expected_user_generation: int,
        expected_settings_key: tuple[str, str, str, str, str, str],
        result: ErpDocumentsResult,
    ) -> None:
        start_pending = False
        with self._erp_documents_request_lock:
            if request_generation != self._erp_documents_active_generation:
                return
            if self._erp_documents_pending:
                self._erp_documents_in_flight = False
                self._erp_documents_pending = False
                start_pending = True
            else:
                self._erp_documents_in_flight = False
        if start_pending:
            self.refresh_erp_documents()
            return
        if self._closing.is_set():
            return
        if not self._erp_download_context_is_current(
            expected_user_id,
            expected_user_generation,
        ):
            self.refresh_erp_documents()
            return
        current_settings = self._general_preferences_service.get_erp_user_settings()
        if self._erp_documents_settings_key(current_settings) != expected_settings_key:
            self.refresh_erp_documents()
            return
        if result.success:
            self._show_erp_documents_home(result.documents)
        else:
            self._show_erp_documents_error(result.message)

    @staticmethod
    def _erp_documents_settings_key(
        settings: ErpUserSettings,
    ) -> tuple[str, str, str, str, str, str]:
        return (
            settings.documents_url.strip(),
            settings.basic_username.strip(),
            settings.basic_password,
            settings.selected_user_id.strip(),
            settings.selected_user_name.strip(),
            settings.company_id.strip(),
        )

    def show_admin_unlock_dialog(self) -> None:
        ft = self._ft
        if self._general_preferences_service is None:
            self.show_error("Preferenze generali non disponibili")
            return
        first_setup = not self._general_preferences_service.has_admin_password()
        admin_active = self._admin_mode
        password = ft.TextField(
            label=(
                "Nuova password amministratore"
                if first_setup
                else "Password amministratore"
            ),
            password=True,
            can_reveal_password=True,
            autofocus=not admin_active,
            width=360,
            visible=not admin_active,
        )
        confirm_password = ft.TextField(
            label="Conferma password amministratore",
            password=True,
            can_reveal_password=True,
            width=360,
            visible=first_setup and not admin_active,
        )
        result_text = ft.Text("")

        def logout(_: object) -> None:
            self._set_admin_mode(False)
            self._close_dialog()
            self.show_status("modalitÃ  operatore attiva")

        def unlock(_: object) -> None:
            if first_setup:
                if not password.value:
                    result_text.value = "Password amministratore obbligatoria"
                    self._update_control(result_text)
                    return
                if password.value != confirm_password.value:
                    result_text.value = "Le password non coincidono"
                    self._update_control(result_text)
                    return
                self._general_preferences_service.set_admin_password(password.value)
                self._set_admin_mode(True)
                self._close_dialog()
                self.show_status("modalità amministratore attiva")
                return
            if self._general_preferences_service.verify_admin_password(
                password.value or ""
            ):
                self._set_admin_mode(True)
                self._close_dialog()
                self.show_status("modalità amministratore attiva")
                return
            result_text.value = "Password amministratore non valida"
            self._update_control(result_text)

        dialog = ft.AlertDialog(
            title=ft.Text(
                "Imposta password amministratore"
                if first_setup
                else (
                    "Amministratore attivo"
                    if admin_active
                    else "Sblocca amministratore"
                )
            ),
            content=ft.Container(
                width=400,
                content=ft.Column(
                    controls=[
                        ft.Text(
                            (
                                "Prima configurazione: scegli una password "
                                "per proteggere le impostazioni avanzate."
                            )
                            if first_setup
                            else (
                                "Le impostazioni avanzate sono attualmente sbloccate."
                                if admin_active
                                else "Inserisci la password per modificare le impostazioni avanzate."
                            )
                        ),
                        password,
                        confirm_password,
                        result_text,
                    ],
                    tight=True,
                    spacing=10,
                ),
            ),
            actions=[
                ft.TextButton("Annulla", on_click=lambda _: self._close_dialog()),
                *(
                    [ft.FilledButton("Logout", on_click=logout)]
                    if admin_active
                    else [
                        ft.FilledButton(
                            "Imposta" if first_setup else "Sblocca",
                            on_click=unlock,
                        )
                    ]
                ),
            ],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def _set_admin_mode(self, enabled: bool) -> None:
        self._admin_mode = enabled
        if self._security_button is not None:
            self._security_button.icon = (
                self._ft.Icons.LOCK_OPEN if enabled else self._ft.Icons.LOCK
            )
            self._security_button.tooltip = (
                "Modalità amministratore attiva"
                if enabled
                else "Sblocca impostazioni amministratore"
            )
        self._page.update()

    def show_startup_user_confirmation(self) -> bool:
        if self._general_preferences_service is None:
            return False
        settings = self._general_preferences_service.get_erp_user_settings()
        self._sync_erp_user_generation_from_settings(settings)
        has_erp_connection = bool(
            settings.users_url.strip() or settings.documents_url.strip()
        )
        has_selected_user = bool(
            settings.selected_user_id.strip() or settings.selected_user_name.strip()
        )
        if not has_erp_connection and not has_selected_user:
            return False
        if settings.persistent_user and has_selected_user:
            self._general_preferences_service.log_erp_user_session_selection(
                settings,
                source="startup_persistent_user",
            )
            self._erp_session_user_confirmed = True
            self.show_active_user_status()
            if self._erp_document_list_enabled():
                self.refresh_erp_documents()
            return False
        ft = self._ft

        def confirm(_: object) -> None:
            self._general_preferences_service.log_erp_user_session_selection(
                settings,
                source="startup_confirmation",
            )
            self._erp_session_user_confirmed = True
            self._close_dialog()
            if self._erp_document_list_enabled():
                self.refresh_erp_documents()

        def replace(_: object) -> None:
            self._close_dialog()
            self.show_user_preferences()

        selected_user = self._selected_user_summary(
            settings.selected_user_id,
            settings.selected_user_name,
        )
        content_controls = [
            ft.Text("Utente selezionato per questa sessione di lavoro:"),
            ft.Text(selected_user, weight=ft.FontWeight.BOLD),
        ]
        actions = [
            ft.TextButton(
                "Sostituisci" if has_selected_user else "Seleziona utente",
                on_click=replace,
            ),
        ]
        if has_selected_user:
            actions.append(ft.FilledButton("Conferma", on_click=confirm))
        dialog = ft.AlertDialog(
            title=ft.Text("Utente operativo"),
            content=ft.Column(
                controls=content_controls,
                tight=True,
                spacing=8,
            ),
            actions=actions,
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)
        return True

    def _certificate_status_text(self) -> str:
        if self._certificate_service is None:
            return "Certificato: non disponibile"
        try:
            certificate = self._certificate_service.get_active_certificate()
        except CertificateServiceError:
            return "Certificato: errore"
        if certificate is None:
            return "Certificato: nessun certificato attivo"
        return f"Certificato: {certificate.name} attivo"

    def _active_user_status_text(self) -> str:
        if self._general_preferences_service is None:
            return ""
        settings = self._general_preferences_service.get_erp_user_settings()
        if not settings.selected_user_name:
            return ""
        return f"Utente: {settings.selected_user_name}"

    @staticmethod
    def _selected_user_summary(user_id: str, user_name: str) -> str:
        if not user_name:
            return "Nessun utente selezionato"
        if user_id:
            return f"{user_name} ({user_id})"
        return user_name

    def _erp_document_list_enabled(self) -> bool:
        if self._general_preferences_service is None:
            return False
        return self._general_preferences_service.get_supabase_settings().list_erp_documents

    def _show_local_home_placeholder(self, update: bool = True) -> None:
        self._home_view.content = self._viewer_placeholder
        self._viewer_placeholder.visible = True
        if update and self._home_view.visible:
            self._page.update()

    def _show_erp_documents_home(
        self,
        documents: tuple[ErpDocument, ...],
    ) -> None:
        ft = self._ft
        settings = (
            self._general_preferences_service.get_erp_user_settings()
            if self._general_preferences_service is not None
            else ErpUserSettings()
        )
        can_open_documents = self._erp_document_download_configured(settings)
        result_text = ft.Text("")
        rows = [
            ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(document.name)),
                    ft.DataCell(ft.Text(document.checkout_date)),
                    *(
                        [
                            ft.DataCell(
                                ft.OutlinedButton(
                                    "Apri",
                                    disabled=not self._erp_document_has_download_keys(
                                        document
                                    ),
                                    on_click=lambda _, item=document, status=result_text: self._open_erp_document(
                                        item,
                                        status,
                                    ),
                                )
                            )
                        ]
                        if can_open_documents
                        else []
                    ),
                ],
            )
            for document in documents
        ]
        content: object
        if rows:
            content = ft.ListView(
                controls=[
                    ft.DataTable(
                        columns=[
                            ft.DataColumn(ft.Text("Nome documento")),
                            ft.DataColumn(ft.Text("Data")),
                            *(
                                [ft.DataColumn(ft.Text("Azione"))]
                                if can_open_documents
                                else []
                            ),
                        ],
                        rows=rows,
                        column_spacing=24,
                    )
                ],
                expand=True,
                spacing=0,
                padding=0,
            )
        else:
            content = ft.Text("Nessun documento da firmare")
        self._home_view.content = ft.Container(
            padding=24,
            expand=True,
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text(
                                "Documenti ERP da firmare",
                                weight=ft.FontWeight.BOLD,
                                size=18,
                            ),
                            ft.Container(expand=True),
                            ft.OutlinedButton(
                                "Aggiorna",
                                on_click=lambda _: self.refresh_erp_documents(),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=content,
                        expand=True,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    ),
                    result_text,
                ],
                spacing=12,
                expand=True,
            ),
        )
        self._viewer_placeholder.visible = False
        if self._home_view.visible:
            self._page.update()

    def _erp_document_download_configured(self, settings: ErpUserSettings) -> bool:
        return bool(
            self._infinity_dms_client is not None
            and self._on_open_document is not None
            and settings.document_service_url.strip()
            and settings.company_id.strip()
            and settings.basic_username.strip()
            and settings.basic_password.strip()
        )

    @staticmethod
    def _erp_document_has_download_keys(document: ErpDocument) -> bool:
        return bool(document.document_id.strip() and document.auth_code.strip())

    def _open_erp_document(self, document: ErpDocument, status_text: object) -> None:
        if (
            self._general_preferences_service is None
            or self._infinity_dms_client is None
            or self._on_open_document is None
            or self._closing.is_set()
        ):
            return
        if not self._erp_download_lock.acquire(blocking=False):
            return
        settings = self._general_preferences_service.get_erp_user_settings()
        self._sync_erp_user_generation_from_settings(settings)
        selected_user_id, user_generation = self._erp_user_context()
        if self._closing.is_set():
            self._release_erp_download_lock()
            return
        if not self._erp_document_download_configured(settings):
            self._release_erp_download_lock()
            return
        if not self._erp_document_has_download_keys(document):
            self._release_erp_download_lock()
            self._set_erp_download_status(status_text, "Documento ERP non apribile")
            return
        self._set_erp_download_status(status_text, "Download in corso...")

        def download() -> None:
            if self._closing.is_set():
                self._release_erp_download_lock()
                return
            path: Path | None = None
            try:
                content = self._infinity_dms_client.download_document(
                    service_url=settings.document_service_url.strip(),
                    credentials=InfinityDmsCredentials(
                        username=settings.basic_username.strip(),
                        password=settings.basic_password,
                        company_id=settings.company_id.strip() or "SALAV",
                    ),
                    document_id=document.document_id,
                    auth_code=document.auth_code,
                )
                if self._closing.is_set():
                    self._release_erp_download_lock()
                    return
                path = self._save_erp_temp_pdf(document.name, content)
            except Exception:
                if self._closing.is_set():
                    self._discard_erp_temp_pdf(path)
                    self._release_erp_download_lock()
                    return
                self.run_ui_task(
                    lambda: self._finish_erp_document_download(
                        status_text,
                        None,
                        "Download documento ERP fallito",
                        selected_user_id,
                        user_generation,
                    )
                )
                return
            if self._closing.is_set():
                self._discard_erp_temp_pdf(path)
                self._release_erp_download_lock()
                return
            self.run_ui_task(
                lambda: self._finish_erp_document_download(
                    status_text,
                    path,
                    "",
                    selected_user_id,
                    user_generation,
                )
            )

        self.run_background_task(download)

    def _set_erp_download_status(self, status_text: object, message: str) -> None:
        status_text.value = message
        status_text.color = (
            self._ft.Colors.RED_700 if message == "Download in corso..." else None
        )
        self._update_control(status_text)

    def _finish_erp_document_download(
        self,
        status_text: object,
        path: Path | None,
        error_message: str,
        expected_user_id: str,
        expected_user_generation: int,
    ) -> None:
        try:
            if self._closing.is_set():
                self._discard_erp_temp_pdf(path)
                return
            if not self._erp_download_context_is_current(
                expected_user_id,
                expected_user_generation,
            ):
                self._discard_erp_temp_pdf(path)
                self._set_erp_download_status(
                    status_text,
                    "Utente ERP cambiato: seleziona nuovamente il documento",
                )
                return
            if path is None:
                self._set_erp_download_status(status_text, error_message)
                return
            self._set_erp_download_status(status_text, "")
            if self._on_open_document is not None:
                self._on_open_document(str(path))
        finally:
            self._release_erp_download_lock()

    def _save_erp_temp_pdf(self, document_name: str, content: bytes) -> Path:
        temp_root = self._erp_temp_root()
        temp_root.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_erp_document_filename(document_name)
        path = temp_root / f"{uuid.uuid4().hex}_{safe_name}"
        path.write_bytes(content)
        self._erp_temp_files.add(path)
        return path

    def _erp_temp_root(self) -> Path:
        if self._erp_temp_session_root is None:
            self._erp_temp_session_root = (
                self._erp_temp_base_directory / self._erp_temp_session_id
            )
        return self._erp_temp_session_root

    @staticmethod
    def _default_erp_temp_base() -> Path:
        return Path(tempfile.gettempdir()) / "qsign" / "erp_documents"

    def _is_own_erp_temp_path(self, path: Path) -> bool:
        session_root = self._erp_temp_session_root
        if session_root is None or session_root.is_symlink():
            return False
        return path.absolute().parent == session_root.absolute()

    def _discard_erp_temp_pdf(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            if self._is_own_erp_temp_path(path):
                path.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            self._erp_temp_files.discard(path)

    def _release_erp_download_lock(self) -> None:
        try:
            self._erp_download_lock.release()
        except RuntimeError:
            pass

    def _current_selected_erp_user_id(self) -> str:
        if self._general_preferences_service is None:
            return ""
        try:
            return (
                self._general_preferences_service.get_erp_user_settings()
                .selected_user_id
                .strip()
            )
        except Exception:
            return ""

    def _sync_erp_user_generation_from_settings(
        self, settings: ErpUserSettings
    ) -> None:
        selected_user_id = settings.selected_user_id.strip()
        with self._erp_user_generation_lock:
            if selected_user_id == self._erp_generation_user_id:
                return
            self._erp_generation_user_id = selected_user_id
            self._erp_user_generation += 1

    def _erp_user_context(self) -> tuple[str, int]:
        with self._erp_user_generation_lock:
            return self._erp_generation_user_id, self._erp_user_generation

    def _erp_download_context_is_current(
        self,
        expected_user_id: str,
        expected_user_generation: int,
    ) -> bool:
        if self._general_preferences_service is not None:
            self._sync_erp_user_generation_from_settings(
                self._general_preferences_service.get_erp_user_settings()
            )
        current_user_id, current_generation = self._erp_user_context()
        return (
            current_user_id == expected_user_id
            and current_generation == expected_user_generation
        )

    def _show_erp_documents_error(self, message: str) -> None:
        ft = self._ft
        self._home_view.content = ft.Container(
            padding=24,
            expand=True,
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Documenti ERP da firmare",
                        weight=ft.FontWeight.BOLD,
                        size=18,
                    ),
                    ft.Text(message or "Errore caricamento documenti ERP"),
                    ft.OutlinedButton(
                        "Riprova",
                        on_click=lambda _: self.refresh_erp_documents(),
                    ),
                ],
                spacing=12,
            ),
        )
        self._viewer_placeholder.visible = False
        if self._home_view.visible:
            self._page.update()

    def clear_document(self) -> None:
        self._pdf_image.visible = False
        self._pdf_stack.visible = False
        self._pdf_stack.controls = [self._pdf_image]
        self._manual_draft_overlay.visible = False
        self._manual_draft_rect = None
        self._manual_drag_start = None
        self._mouse_wheel_page_delta = 0.0
        self._home_view.visible = True
        self._document_viewer.visible = False
        self._document_name.value = "Nessun documento"
        self._page_count.value = "Pagina — / —"
        self._zoom.value = "Zoom: 100%"
        self._active_user.value = self._active_user_status_text()
        self.refresh_erp_documents()
        self.show_certificate_status()

    def show_error(self, message: str) -> None:
        self.show_status(f"errore — {message}")

    def set_manual_signature_mode(self, enabled: bool) -> None:
        self._manual_signature_mode = enabled
        self._manual_drag_start = None
        self._manual_draft_rect = None
        self._manual_draft_overlay.visible = False
        if enabled:
            self.show_status("documento sconosciuto: disegna il rettangolo firma")

    def ask_save_template(
        self,
        on_confirm: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        ft = self._ft

        def confirm(_: object) -> None:
            self._close_dialog()
            on_confirm()

        def cancel(_: object) -> None:
            self._close_dialog()
            on_cancel()

        dialog = ft.AlertDialog(
            title=ft.Text("Informazioni"),
            content=ft.Text("Vuoi salvare questo modello per i prossimi documenti?"),
            actions=[
                ft.TextButton("No", on_click=cancel),
                ft.TextButton("Sì", on_click=confirm),
            ],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def ask_discard_signed_document(
        self,
        on_confirm: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        ft = self._ft

        def confirm(_: object) -> None:
            self._close_dialog()
            on_confirm()

        def cancel(_: object) -> None:
            self._close_dialog()
            on_cancel()

        dialog = ft.AlertDialog(
            title=ft.Text("Documento firmato non salvato"),
            content=ft.Text(
                "La firma acquisita non è stata ancora salvata. "
                "Continuare senza salvare?"
            ),
            actions=[
                ft.TextButton("Torna al documento", on_click=cancel),
                ft.FilledButton("Continua senza salvare", on_click=confirm),
            ],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def open_signature_dialog(
        self,
        on_confirm: Callable[[CapturedSignature], None],
        on_clear: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        ft = self._ft
        self._signature_strokes = []
        self._current_signature_stroke = None
        self._signature_preview.src = self._signature_svg_data_uri()
        self._refresh_signature_canvas()

        def clear(_: object) -> None:
            self._signature_strokes = []
            self._current_signature_stroke = None
            self._signature_preview.src = self._signature_svg_data_uri()
            self._refresh_signature_canvas()
            self._page.update()
            self.show_status("firma cancellata")
            if on_clear is not None:
                on_clear()

        def cancel(_: object) -> None:
            self._close_dialog()
            self.show_status("firma annullata")
            if on_cancel is not None:
                on_cancel()

        def confirm(_: object) -> None:
            self._commit_current_signature_stroke()
            content = self._signature_svg().encode("utf-8")
            self._close_dialog()
            on_confirm(CapturedSignature(content=content, media_type="image/svg+xml"))

        signature_pad = ft.GestureDetector(
            content=ft.Container(
                content=self._signature_canvas,
                width=420,
                height=180,
                bgcolor=ft.Colors.WHITE,
                border=ft.Border(
                    left=ft.BorderSide(1, ft.Colors.GREY_500),
                    top=ft.BorderSide(1, ft.Colors.GREY_500),
                    right=ft.BorderSide(1, ft.Colors.GREY_500),
                    bottom=ft.BorderSide(1, ft.Colors.GREY_500),
                ),
            ),
            drag_interval=16,
            on_pan_down=self._start_signature_stroke,
            on_pan_update=self._update_signature_stroke,
            on_pan_end=self._finish_signature_stroke,
            on_tap_down=self._start_signature_stroke,
            on_tap_up=self._finish_signature_stroke,
        )
        dialog = ft.AlertDialog(
            title=ft.Text("Firma"),
            content=signature_pad,
            actions=[
                ft.TextButton("Cancella", on_click=clear),
                ft.TextButton("Annulla", on_click=cancel),
                ft.TextButton("Conferma", on_click=confirm),
            ],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def show_information(self) -> None:
        ft = self._ft
        dialog = ft.AlertDialog(
            content=ft.Container(
                width=620,
                content=ft.Column(
                    controls=[
                        ft.Container(
                            content=ft.Image(
                                src=self._image_data_uri(
                                    "images/logo_qsign_grande.png"
                                ),
                                width=420,
                                height=150,
                                fit=ft.BoxFit.CONTAIN,
                                semantics_label="QSign",
                            ),
                            alignment=ft.Alignment(0, 0),
                        ),
                        ft.Text(
                            f"Versione: {self._app_version()}",
                            weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Divider(),
                        ft.Row(
                            controls=[
                                ft.Text("Sito ufficiale:", weight=ft.FontWeight.BOLD),
                                ft.TextButton(
                                    "queensrl.net",
                                    on_click=lambda _: self._open_url(
                                        "https://queensrl.net"
                                    ),
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            controls=[
                                ft.Text("Supporto:", weight=ft.FontWeight.BOLD),
                                ft.TextButton(
                                    "assistenza@qss.it",
                                    on_click=lambda _: self._open_url(
                                        "mailto:assistenza@qss.it"
                                    ),
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            controls=[
                                ft.Text(
                                    "Diritto di Autore @ 2026 Queen Srl. "
                                    "Tutti i diritti riservati",
                                    size=12,
                                ),
                                ft.Image(
                                    src=self._image_data_uri(
                                        "images/logo_queen_25anni.png"
                                    ),
                                    width=170,
                                    height=55,
                                    fit=ft.BoxFit.CONTAIN,
                                    semantics_label="Queen Srl",
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                    spacing=12,
                ),
            ),
            actions=[ft.TextButton("Chiudi", on_click=lambda _: self._close_dialog())],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def show_signed_history(self) -> None:
        ft = self._ft
        self._close_dialog()
        documents = self._signed_history_documents()
        search = ft.TextField(
            label="Cerca documento",
            prefix_icon=ft.Icons.SEARCH,
            width=420,
        )
        sort_state = {"field": "created_at", "ascending": False}
        table_container = ft.Container(expand=True)

        def sort_label(field: str, label: str) -> str:
            if sort_state["field"] != field:
                return label
            return f"{label} {'ASC' if sort_state['ascending'] else 'DESC'}"

        def filtered_documents() -> list[Path]:
            query = (search.value or "").strip().lower()
            items = [
                path
                for path in documents
                if not query or query in path.name.lower()
            ]
            if sort_state["field"] == "name":
                key = lambda path: path.name.lower()
            else:
                key = lambda path: path.stat().st_ctime
            return sorted(
                items,
                key=key,
                reverse=not bool(sort_state["ascending"]),
            )

        def sort_by(field: str) -> None:
            if sort_state["field"] == field:
                sort_state["ascending"] = not bool(sort_state["ascending"])
            else:
                sort_state["field"] = field
                sort_state["ascending"] = field == "name"
            render_table()

        def render_table(_: object | None = None) -> None:
            rows = filtered_documents()
            if not documents:
                table_container.content = ft.Container(
                    content=ft.Text("Nessun documento firmato trovato"),
                    alignment=ft.Alignment(0, -1),
                    padding=12,
                )
            elif not rows:
                table_container.content = ft.Container(
                    content=ft.Text("Nessun documento corrisponde alla ricerca"),
                    alignment=ft.Alignment(0, -1),
                    padding=12,
                )
            else:
                table_container.content = ft.ListView(
                    controls=[
                        ft.DataTable(
                            columns=[
                                ft.DataColumn(
                                    ft.TextButton(
                                        sort_label("name", "Nome file"),
                                        on_click=lambda _: sort_by("name"),
                                    )
                                ),
                                ft.DataColumn(
                                    ft.TextButton(
                                        sort_label("created_at", "Creato il"),
                                        on_click=lambda _: sort_by("created_at"),
                                    )
                                ),
                                ft.DataColumn(ft.Text("Apri")),
                            ],
                            rows=[
                                ft.DataRow(
                                    cells=[
                                        ft.DataCell(
                                            ft.Container(
                                                content=ft.TextButton(
                                                    path.name,
                                                    on_click=lambda _, item=path: self._open_signed_file(
                                                        item
                                                    ),
                                                ),
                                                alignment=ft.Alignment(-1, 0),
                                                width=560,
                                            )
                                        ),
                                        ft.DataCell(
                                            ft.Container(
                                                content=ft.Text(
                                                    self._format_file_created_at(path),
                                                    text_align=ft.TextAlign.LEFT,
                                                ),
                                                width=150,
                                            )
                                        ),
                                        ft.DataCell(
                                            ft.IconButton(
                                                icon=ft.Icons.FOLDER_OPEN,
                                                tooltip="Apri documento firmato",
                                                on_click=lambda _, item=path: self._open_signed_file(
                                                    item
                                                ),
                                            )
                                        ),
                                    ]
                                )
                                for path in rows
                            ],
                            column_spacing=24,
                        )
                    ],
                    spacing=0,
                )
            self._update_control(table_container)

        search.on_change = render_table
        render_table()

        dialog = ft.AlertDialog(
            title=ft.Text("Storico documenti firmati"),
            content=ft.Container(
                width=840,
                height=520,
                content=ft.Column(
                    controls=[
                        search,
                        table_container,
                    ],
                    tight=True,
                    spacing=10,
                ),
            ),
            actions=[ft.TextButton("Chiudi", on_click=lambda _: self._close_dialog())],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def show_general_preferences(self) -> None:
        ft = self._ft
        if self._general_preferences_service is None:
            self.show_error("Preferenze generali non disponibili")
            return
        settings = self._general_preferences_service.get_supabase_settings()
        supabase_url = ft.TextField(
            label="URL progetto Supabase",
            value=settings.project_url,
        )
        supabase_password = ft.TextField(
            label="Password/API key Supabase",
            value=settings.password,
            password=True,
            can_reveal_password=True,
        )
        supabase_table = ft.TextField(
            label="Tabella template Supabase",
            value=settings.table_name,
        )
        auto_sync_templates = ft.Checkbox(
            label="Sincronizza automaticamente i template all'avvio",
            value=settings.auto_sync_templates_on_startup,
        )
        auto_save_signed_documents = ft.Checkbox(
            label="Salvataggio automatico",
            value=settings.auto_save_signed_documents,
        )
        list_erp_documents = ft.Checkbox(
            label="Elenco Documenti ERP",
            value=settings.list_erp_documents,
        )
        auto_refresh_erp_documents = ft.Checkbox(
            label="Aggiorna automaticamente documenti ERP",
            value=settings.auto_refresh_erp_documents,
        )
        erp_refresh_interval_seconds = ft.TextField(
            label="Intervallo aggiornamento documenti ERP (secondi)",
            value=str(settings.erp_refresh_interval_seconds),
            width=320,
        )

        def sync_erp_document_options(
            _: object | None = None,
            *,
            update: bool = True,
        ) -> None:
            enabled = _checked(list_erp_documents.value)
            if not enabled:
                auto_refresh_erp_documents.value = False
                erp_refresh_interval_seconds.value = "0"
            auto_refresh_erp_documents.disabled = not enabled
            erp_refresh_interval_seconds.disabled = not enabled
            if update:
                self._update_control(auto_refresh_erp_documents)
                self._update_control(erp_refresh_interval_seconds)

        list_erp_documents.on_change = sync_erp_document_options
        sync_erp_document_options(update=False)
        signature_capture_mode = ft.Dropdown(
            label="Metodo firma",
            value=(
                "wacom"
                if settings.signature_capture_mode == "wacom"
                else "mouse"
            ),
            options=[
                ft.dropdown.Option("mouse", "Mouse"),
                ft.dropdown.Option("wacom", "Wacom STU-430"),
            ],
            width=320,
        )
        local_erp_port = ft.TextField(
            label="Porta bridge ERP locale",
            value=str(settings.local_erp_port),
            width=320,
            input_filter=ft.NumbersOnlyInputFilter(),
        )
        show_signature_text = ft.Checkbox(
            label="Mostra testo nel riquadro firma",
            value=settings.show_signature_text,
        )
        operational_options = ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        auto_sync_templates,
                        auto_save_signed_documents,
                        show_signature_text,
                    ],
                    spacing=10,
                    expand=True,
                ),
                ft.Column(
                    controls=[
                        list_erp_documents,
                        auto_refresh_erp_documents,
                        erp_refresh_interval_seconds,
                    ],
                    spacing=10,
                    expand=True,
                ),
            ],
            spacing=24,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
        result_text = ft.Text("")
        result_panel = ft.Container(
            content=result_text,
            padding=ft.Padding(left=0, top=0, right=12, bottom=0),
            alignment=ft.Alignment(1, 0),
            expand=True,
        )

        def current_settings() -> SupabaseSettings:
            return SupabaseSettings(
                project_url=supabase_url.value or "",
                password=supabase_password.value or "",
                table_name=supabase_table.value or "SaluteLavoro",
                auto_sync_templates_on_startup=_checked(auto_sync_templates.value),
                auto_save_signed_documents=_checked(
                    auto_save_signed_documents.value
                ),
                list_erp_documents=_checked(list_erp_documents.value),
                auto_refresh_erp_documents=_checked(
                    list_erp_documents.value
                )
                and _checked(
                    auto_refresh_erp_documents.value
                ),
                erp_refresh_interval_seconds=_integer_or_default(
                    erp_refresh_interval_seconds.value,
                    60 if _checked(list_erp_documents.value) else 0,
                ),
                show_signature_text=_checked(show_signature_text.value),
                signature_capture_mode=(
                    "wacom"
                    if signature_capture_mode.value == "wacom"
                    else "mouse"
                ),
                local_erp_port=_integer_or_default(local_erp_port.value, 9091),
            )

        def set_result(message: str) -> None:
            result_text.value = message
            self._update_control(result_text)

        def save(_: object) -> None:
            updated_settings = current_settings()
            self._general_preferences_service.save_supabase_settings(updated_settings)
            if self._on_general_preferences_saved is not None:
                self._on_general_preferences_saved(updated_settings)
            if (
                not updated_settings.list_erp_documents
                and self._home_view.visible
                and not self._document_viewer.visible
            ):
                self._show_local_home_placeholder(update=False)
            if (
                updated_settings.list_erp_documents
                and self._home_view.visible
                and not self._document_viewer.visible
            ):
                self.refresh_erp_documents()
            set_result("Impostazioni salvate")

        def test(_: object) -> None:
            result = self._general_preferences_service.test_supabase_connection(
                current_settings()
            )
            set_result(result.message)

        def verify_table(_: object) -> None:
            result = self._general_preferences_service.test_supabase_template_table(
                current_settings()
            )
            if result.success and not result.exists:
                set_result(
                    f"{result.message}. Premi 'Crea tabella' per duplicarla da SaluteLavoro."
                )
                return
            set_result(result.message)

        def create_table(_: object) -> None:
            result = self._general_preferences_service.ensure_supabase_template_table(
                current_settings()
            )
            set_result(result.message)

        supabase_tab_content = ft.Container(
            padding=ft.Padding(left=0, top=14, right=0, bottom=0),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Container(content=supabase_url, expand=True),
                            ft.Container(content=supabase_password, expand=True),
                        ],
                        spacing=14,
                    ),
                    ft.Row(
                        controls=[
                            ft.Container(content=supabase_table, width=320),
                            ft.OutlinedButton("Test", on_click=test),
                            ft.OutlinedButton(
                                "Verifica tabella",
                                on_click=verify_table,
                            ),
                            ft.OutlinedButton(
                                "Crea tabella",
                                on_click=create_table,
                            ),
                        ],
                        wrap=True,
                        spacing=10,
                    ),
                ],
                spacing=14,
            ),
        )
        options_tab_content = ft.Container(
            padding=ft.Padding(left=0, top=18, right=0, bottom=0),
            content=operational_options,
        )
        signature_tab_content = ft.Container(
            padding=ft.Padding(left=0, top=18, right=0, bottom=0),
            content=ft.Column(
                controls=[
                    signature_capture_mode,
                    local_erp_port,
                ],
                spacing=12,
            ),
        )
        tab_labels = []
        tab_contents = []
        if self._admin_mode:
            tab_labels.append(ft.Tab(label="Supabase"))
            tab_contents.append(supabase_tab_content)
        else:
            tab_labels.append(ft.Tab(label="Opzioni"))
            tab_contents.append(options_tab_content)
        if self._admin_mode:
            tab_labels.append(ft.Tab(label="Opzioni"))
            tab_contents.append(options_tab_content)
        tab_labels.append(ft.Tab(label="Firma"))
        tab_contents.append(signature_tab_content)

        dialog = ft.AlertDialog(
            title=ft.Text("Impostazioni"),
            content=ft.Container(
                width=720,
                height=320,
                content=ft.Tabs(
                    content=ft.Column(
                        controls=[
                            ft.TabBar(tabs=tab_labels),
                            ft.TabBarView(
                                controls=tab_contents,
                                expand=True,
                            ),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    length=len(tab_labels),
                    selected_index=0,
                    animation_duration=150,
                    expand=True,
                ),
            ),
            actions=[
                result_panel,
                ft.FilledButton("Salva", on_click=save),
                ft.TextButton("Chiudi", on_click=lambda _: self._close_dialog()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def show_user_preferences(self) -> None:
        ft = self._ft
        if self._general_preferences_service is None:
            self.show_error("Preferenze utenti non disponibili")
            return
        settings = self._general_preferences_service.get_erp_user_settings()
        self._sync_erp_user_generation_from_settings(settings)
        users_url = ft.TextField(
            label="URL lista utenti ERP",
            value=settings.users_url,
            width=380,
        )
        documents_url = ft.TextField(
            label="URL query documenti ERP",
            value=settings.documents_url,
            width=380,
        )
        document_service_url = ft.TextField(
            label="URL servizio documentale SOAP",
            value=settings.document_service_url,
            width=380,
        )
        company_id = ft.TextField(
            label="Company ID",
            value=settings.company_id or "SALAV",
            width=180,
        )
        basic_username = ft.TextField(
            label="Utente Basic Auth",
            value=settings.basic_username,
            width=380,
        )
        basic_password = ft.TextField(
            label="Password Basic Auth",
            value=settings.basic_password,
            password=True,
            can_reveal_password=True,
            width=380,
        )
        selected_user_id = settings.selected_user_id
        selected_user_name = settings.selected_user_name
        persistent_user = ft.Checkbox(
            label="Utente Persistente",
            value=settings.persistent_user,
        )
        selected_summary = ft.Text(
            self._selected_user_summary(selected_user_id, selected_user_name)
        )
        users_list = ft.ListView(height=220, spacing=4)
        result_text = ft.Text("")
        users_request_lock = threading.Lock()
        users_request_generation = 0

        def current_settings() -> ErpUserSettings:
            return ErpUserSettings(
                users_url=users_url.value or "",
                documents_url=documents_url.value or "",
                document_service_url=document_service_url.value or "",
                company_id=company_id.value or "SALAV",
                basic_username=basic_username.value or "",
                basic_password=basic_password.value or "",
                selected_user_id=selected_user_id,
                selected_user_name=selected_user_name,
                persistent_user=_checked(persistent_user.value),
            )

        def set_result(message: str) -> None:
            result_text.value = message
            self._update_control(result_text)

        def next_users_request_generation() -> int:
            nonlocal users_request_generation
            with users_request_lock:
                users_request_generation += 1
                return users_request_generation

        def is_latest_users_request(request_generation: int) -> bool:
            with users_request_lock:
                return request_generation == users_request_generation

        def select_user(user: ErpUser) -> None:
            nonlocal selected_user_id, selected_user_name
            clear_selection = not user.user_id and user.display_name == "Nessun utente"
            selected_user_id = "" if clear_selection else user.user_id
            selected_user_name = "" if clear_selection else user.display_name
            self._erp_session_user_confirmed = not clear_selection
            selected_summary.value = self._selected_user_summary(
                selected_user_id, selected_user_name
            )
            try:
                updated_settings = current_settings()
                self._general_preferences_service.save_erp_user_settings(updated_settings)
            except Exception as error:
                set_result(str(error))
                return
            self._sync_erp_user_generation_from_settings(updated_settings)
            self._general_preferences_service.log_erp_user_session_selection(
                updated_settings,
                source="user_preferences_selection",
            )
            self.show_active_user_status()
            self._update_control(selected_summary)
            set_result(
                "Nessun utente selezionato"
                if clear_selection
                else f"Utente salvato: {user.display_name}"
            )
            self.refresh_erp_documents()

        def user_row(user: ErpUser) -> object:
            return ft.Row(
                controls=[
                    ft.Text(user.display_name, expand=True),
                    ft.Text(user.user_id, width=140),
                    ft.OutlinedButton(
                        "Seleziona",
                        on_click=lambda _, item=user: select_user(item),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

        def load_users(_: object) -> None:
            request_settings = current_settings()
            request_generation = next_users_request_generation()
            load_users_button.disabled = True
            test_users_button.disabled = True
            self._update_control(test_users_button)
            self._update_control(load_users_button)
            set_result("Caricamento utenti...")

            def fetch() -> None:
                try:
                    result = self._general_preferences_service.fetch_erp_users(
                        request_settings
                    )
                except Exception:
                    result = ErpUsersResult(
                        False,
                        "Caricamento utenti ERP fallito",
                        (),
                    )
                self.run_ui_task(
                    lambda: finish_load_users(request_generation, result)
                )

            self.run_background_task(fetch)

        def finish_load_users(
            request_generation: int,
            result: ErpUsersResult,
        ) -> None:
            if (
                self._closing.is_set()
                or self._active_dialog is not dialog
                or not is_latest_users_request(request_generation)
            ):
                return
            load_users_button.disabled = False
            test_users_button.disabled = False
            users_list.controls = [
                user_row(ErpUser("", "Nessun utente")),
                *[user_row(user) for user in result.users],
            ]
            self._update_control(test_users_button)
            self._update_control(load_users_button)
            self._update_control(users_list)
            set_result(result.message)

        def test_users(_: object) -> None:
            request_settings = current_settings()
            request_generation = next_users_request_generation()
            test_users_button.disabled = True
            load_users_button.disabled = True
            self._update_control(test_users_button)
            self._update_control(load_users_button)
            set_result("Verifica in corso...")

            def fetch() -> None:
                try:
                    result = self._general_preferences_service.fetch_erp_users(
                        request_settings
                    )
                except Exception:
                    result = ErpUsersResult(
                        False,
                        "Verifica utenti ERP fallita",
                        (),
                    )
                self.run_ui_task(
                    lambda: finish_test_users(request_generation, result)
                )

            self.run_background_task(fetch)

        def finish_test_users(
            request_generation: int,
            result: ErpUsersResult,
        ) -> None:
            if (
                self._closing.is_set()
                or self._active_dialog is not dialog
                or not is_latest_users_request(request_generation)
            ):
                return
            test_users_button.disabled = False
            load_users_button.disabled = False
            self._update_control(test_users_button)
            self._update_control(load_users_button)
            if result.success:
                set_result(
                    f"Connessione utenti riuscita: {len(result.users)} utenti disponibili"
                )
            else:
                set_result(result.message)

        def save_settings(message: str) -> None:
            try:
                self._general_preferences_service.save_erp_user_settings(
                    current_settings()
                )
            except Exception as error:
                set_result(str(error))
                return
            self.show_active_user_status()
            set_result(message)

        test_users_button = ft.OutlinedButton("Test", on_click=test_users)
        load_users_button = ft.OutlinedButton(
            "Carica utenti",
            on_click=load_users,
        )
        dialog = ft.AlertDialog(
            title=ft.Text("Connessione ERP"),
            content=ft.Container(
                width=920,
                height=520,
                content=ft.Row(
                    controls=[
                        ft.Container(
                            width=400,
                            content=ft.Column(
                                controls=[
                                    *(
                                        [
                                            users_url,
                                            documents_url,
                                            document_service_url,
                                            company_id,
                                            basic_username,
                                            basic_password,
                                        ]
                                        if self._admin_mode
                                        else [
                                            ft.Text(
                                                "Connessione configurata dall'amministratore"
                                            )
                                        ]
                                    ),
                                    ft.Row(
                                        controls=[
                                            *(
                                                [test_users_button]
                                                if self._admin_mode
                                                else []
                                            ),
                                            load_users_button,
                                        ],
                                        wrap=True,
                                    ),
                                    result_text,
                                ],
                                spacing=10,
                            ),
                        ),
                        ft.VerticalDivider(width=1),
                        ft.Container(
                            width=460,
                            content=ft.Column(
                                controls=[
                                    ft.Text(
                                        "Utente operativo",
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.Container(
                                        content=selected_summary,
                                        padding=10,
                                        border=ft.Border(
                                            left=ft.BorderSide(
                                                1,
                                                ft.Colors.GREY_500,
                                            ),
                                            top=ft.BorderSide(
                                                1,
                                                ft.Colors.GREY_500,
                                            ),
                                            right=ft.BorderSide(
                                                1,
                                                ft.Colors.GREY_500,
                                            ),
                                            bottom=ft.BorderSide(
                                                1,
                                                ft.Colors.GREY_500,
                                            ),
                                        ),
                                        width=440,
                                    ),
                                    persistent_user,
                                    ft.Divider(),
                                    ft.Text(
                                        "Utenti disponibili",
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    users_list,
                                ],
                                spacing=10,
                            ),
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    spacing=18,
                ),
            ),
            actions=[
                ft.FilledButton(
                    "Salva",
                    on_click=lambda _: save_settings("Impostazioni ERP salvate"),
                ),
                ft.TextButton("Chiudi", on_click=lambda _: self._close_dialog()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def show_template_history(self, result_message: str = "") -> None:
        ft = self._ft
        self._close_dialog()
        result_text = ft.Text(result_message)

        def set_result(message: str) -> None:
            result_text.value = message
            self._update_control(result_text)

        def refresh(_: object | None = None) -> None:
            self.show_template_history()

        def refresh_with_result(message: str) -> None:
            self.show_template_history(message)

        def sync_action(action: str) -> None:
            if self._template_sync_service is None:
                set_result("Sincronizzazione template non disponibile")
                return
            try:
                if action == "download":
                    result = self._template_sync_service.download_templates()
                    refresh_with_result(
                        f"Scaricati {result.downloaded}, invariati {result.skipped}"
                    )
                elif action == "upload":
                    result = self._template_sync_service.upload_templates()
                    set_result(f"Caricati {result.uploaded}")
                else:
                    result = self._template_sync_service.sync_templates()
                    refresh_with_result(
                        f"Caricati {result.uploaded}, "
                        f"scaricati {result.downloaded}, invariati {result.skipped}"
                    )
            except SupabaseTemplateSyncServiceError as error:
                set_result(str(error))

        templates = self._local_learned_templates()
        search = ft.TextField(
            label="Cerca template",
            prefix_icon=ft.Icons.SEARCH,
            width=420,
        )
        sort_state = {"field": "updated_at", "ascending": False}
        table_container = ft.Container(expand=True)

        def sort_label(field: str, label: str) -> str:
            if sort_state["field"] != field:
                return label
            return f"{label} {'ASC' if sort_state['ascending'] else 'DESC'}"

        def filtered_templates() -> list[Path]:
            query = (search.value or "").strip().lower()
            items = [
                path
                for path in templates
                if not query or query in path.name.lower()
            ]
            if sort_state["field"] == "template":
                key = lambda path: path.name.lower()
            else:
                key = lambda path: path.stat().st_mtime
            return sorted(
                items,
                key=key,
                reverse=not bool(sort_state["ascending"]),
            )

        def sort_by(field: str) -> None:
            if sort_state["field"] == field:
                sort_state["ascending"] = not bool(sort_state["ascending"])
            else:
                sort_state["field"] = field
                sort_state["ascending"] = field == "template"
            render_table()

        def render_table(_: object | None = None) -> None:
            rows = filtered_templates()
            if not templates:
                table_container.content = ft.Container(
                    content=ft.Text("Nessun template documento trovato"),
                    alignment=ft.Alignment(0, -1),
                    padding=12,
                )
            elif not rows:
                table_container.content = ft.Container(
                    content=ft.Text("Nessun template corrisponde alla ricerca"),
                    alignment=ft.Alignment(0, -1),
                    padding=12,
                )
            else:
                table_container.content = ft.ListView(
                    controls=[
                        ft.DataTable(
                            columns=[
                                ft.DataColumn(
                                    ft.TextButton(
                                        sort_label("template", "Template"),
                                        on_click=lambda _: sort_by("template"),
                                    )
                                ),
                                ft.DataColumn(
                                    ft.TextButton(
                                        sort_label("updated_at", "Aggiornato il"),
                                        on_click=lambda _: sort_by("updated_at"),
                                    )
                                ),
                            ],
                            rows=[
                                ft.DataRow(
                                    cells=[
                                        ft.DataCell(
                                            ft.Container(
                                                content=ft.Text(
                                                    path.name,
                                                    no_wrap=True,
                                                ),
                                                width=600,
                                            )
                                        ),
                                        ft.DataCell(
                                            ft.Container(
                                                content=ft.Text(
                                                    self._format_file_updated_at(path),
                                                    text_align=ft.TextAlign.LEFT,
                                                ),
                                                width=150,
                                            )
                                        ),
                                    ]
                                )
                                for path in rows
                            ],
                            column_spacing=24,
                        )
                    ],
                    spacing=0,
                )
            self._update_control(table_container)

        search.on_change = render_table
        render_table()

        dialog = ft.AlertDialog(
            title=ft.Text("Template Documenti"),
            content=ft.Container(
                width=840,
                height=520,
                content=ft.Column(
                    controls=[
                        search,
                        table_container,
                        ft.Row(
                            controls=[
                                ft.OutlinedButton("Aggiorna", on_click=refresh),
                                ft.OutlinedButton(
                                    "Scarica", on_click=lambda _: sync_action("download")
                                ),
                                ft.OutlinedButton(
                                    "Carica", on_click=lambda _: sync_action("upload")
                                ),
                                ft.FilledButton(
                                    "Sincronizza",
                                    on_click=lambda _: sync_action("sync"),
                                ),
                            ],
                            wrap=True,
                        ),
                        result_text,
                    ],
                    tight=True,
                    spacing=10,
                ),
            ),
            actions=[ft.TextButton("Chiudi", on_click=lambda _: self._close_dialog())],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def show_certificate_preferences(self) -> None:
        ft = self._ft
        if self._certificate_service is None:
            self.show_error("Gestione certificati non disponibile")
            return
        self._close_dialog()
        try:
            certificate = self._certificate_service.get_active_certificate()
        except CertificateServiceError as error:
            self.show_error(str(error))
            certificate = None
        signature_metadata = (
            self._certificate_service.get_signature_metadata()
            if certificate is not None
            else None
        )
        dialog = ft.AlertDialog(
            title=ft.Text("CERTIFICATO"),
            content=ft.Container(
                width=520,
                content=ft.Column(
                    controls=[
                        ft.Text("Certificato attivo", weight=ft.FontWeight.BOLD),
                        ft.Text(
                            certificate.name
                            if certificate is not None
                            else "<Nessun certificato selezionato>"
                        ),
                        ft.Text(
                            "Motivo firma: "
                            + (
                                signature_metadata.reason
                                if signature_metadata is not None
                                else "-"
                            )
                        ),
                        ft.Text(
                            "Luogo: "
                            + (
                                signature_metadata.location
                                if (
                                    signature_metadata is not None
                                    and signature_metadata.location
                                )
                                else "Non disponibile"
                            )
                        ),
                        ft.Text(
                            "Contatto firmatario: "
                            + (
                                signature_metadata.contact_info
                                if (
                                    signature_metadata is not None
                                    and signature_metadata.contact_info
                                )
                                else "Non disponibile"
                            )
                        ),
                        ft.Divider(),
                        ft.Row(
                            controls=[
                                *(
                                    [
                                        ft.OutlinedButton(
                                            "Genera",
                                            on_click=lambda _: self._show_generate_certificate_dialog(),
                                        ),
                                        ft.OutlinedButton(
                                            "Importa PFX",
                                            on_click=self._pick_pfx,
                                        ),
                                    ]
                                    if self._admin_mode
                                    else []
                                ),
                                ft.OutlinedButton(
                                    "Seleziona certificato",
                                    on_click=lambda _: self._show_select_certificate_dialog(),
                                ),
                                *(
                                    [
                                        ft.OutlinedButton(
                                            "Cancella",
                                            disabled=certificate is None,
                                            on_click=lambda _: self._confirm_delete_certificate(
                                                certificate
                                            ),
                                        ),
                                    ]
                                    if self._admin_mode
                                    else []
                                ),
                            ],
                            wrap=True,
                        ),
                        ft.Divider(),
                        ft.Text("Informazioni", weight=ft.FontWeight.BOLD),
                        *self._certificate_info_controls(certificate),
                    ],
                    tight=True,
                    spacing=10,
                ),
            ),
            actions=[ft.TextButton("Chiudi", on_click=lambda _: self._close_dialog())],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def _certificate_info_controls(
        self, certificate: CertificateInfo | None
    ) -> list[object]:
        ft = self._ft
        return [
            ft.Row(
                controls=[
                    ft.Text("Tipo", width=110, weight=ft.FontWeight.BOLD),
                    ft.Text(certificate.type if certificate is not None else "-"),
                ]
            ),
            ft.Row(
                controls=[
                    ft.Text("Valido fino", width=110, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        self._format_system_date(certificate.valid_until)
                        if certificate is not None
                        else "-"
                    ),
                ]
            ),
            ft.Row(
                controls=[
                    ft.Text("Thumbprint", width=110, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        certificate.thumbprint if certificate is not None else "-",
                        selectable=True,
                    ),
                ]
            ),
        ]

    def _show_generate_certificate_dialog(self) -> None:
        if self._certificate_service is None:
            self.show_error("Gestione certificati non disponibile")
            return
        ft = self._ft
        self._close_dialog()
        first_name = ft.TextField(label="Nome")
        last_name = ft.TextField(label="Cognome")
        organization = ft.TextField(label="Organizzazione")
        default_valid_until = date.today() + timedelta(days=365 * 3)
        selected_valid_until = {"value": default_valid_until}
        valid_until = ft.TextField(
            label="Valido fino",
            value=self._format_system_date(default_valid_until.isoformat()),
            read_only=True,
        )

        def pick_valid_until(_: object) -> None:
            def selected(event: object) -> None:
                value = getattr(getattr(event, "control", None), "value", None)
                if value is None:
                    value = getattr(date_picker, "value", None)
                if isinstance(value, datetime):
                    value = value.date()
                if isinstance(value, date):
                    selected_valid_until["value"] = value
                    valid_until.value = self._format_system_date(value.isoformat())
                    self._update_control(valid_until)

            date_picker = ft.DatePicker(
                value=selected_valid_until["value"],
                first_date=date.today() + timedelta(days=1),
                last_date=date.today() + timedelta(days=365 * 20),
                help_text="Scadenza certificato",
                cancel_text="Annulla",
                confirm_text="Conferma",
                on_change=selected,
            )
            self._page.show_dialog(date_picker)

        password = ft.TextField(
            label="Password PFX",
            password=True,
            can_reveal_password=True,
        )
        signature_metadata = self._certificate_service.get_signature_metadata()
        signature_reason = ft.TextField(
            label="Motivo firma",
            value=signature_metadata.reason,
        )
        signature_location = ft.TextField(
            label="Luogo",
            value=signature_metadata.location,
        )
        signature_contact = ft.TextField(
            label="Contatto firmatario",
            value=signature_metadata.contact_info,
        )

        def generate(_: object) -> None:
            try:
                self._certificate_service.generate_self_signed(
                    first_name.value or "",
                    last_name.value or "",
                    organization.value or "",
                    password.value or "",
                    selected_valid_until["value"].isoformat(),
                )
                self._certificate_service.set_signature_metadata(
                    reason=signature_reason.value or "",
                    location=signature_location.value or "",
                    contact_info=signature_contact.value or "",
                )
            except CertificateServiceError as error:
                self.show_error(str(error))
                return
            self._close_dialog()
            self.show_certificate_preferences()
            self.show_certificate_status()

        dialog = ft.AlertDialog(
            title=ft.Text("Genera certificato"),
            content=ft.Container(
                width=420,
                content=ft.Column(
                    controls=[
                        first_name,
                        last_name,
                        organization,
                        ft.Row(
                            controls=[
                                valid_until,
                                ft.IconButton(
                                    icon=ft.Icons.CALENDAR_MONTH,
                                    tooltip="Scegli scadenza",
                                    on_click=pick_valid_until,
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        password,
                        signature_reason,
                        signature_location,
                        signature_contact,
                    ],
                    tight=True,
                ),
            ),
            actions=[
                ft.TextButton("Annulla", on_click=lambda _: self._close_dialog()),
                ft.TextButton("Genera", on_click=generate),
            ],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    async def _pick_pfx(self, _: object) -> None:
        self._close_dialog()
        files = await self._pfx_file_picker.pick_files(
            dialog_title="Importa certificato PFX",
            file_type=self._ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["pfx"],
            allow_multiple=False,
        )
        if files and files[0].path:
            self._show_import_pfx_password_dialog(files[0].path)

    def _show_import_pfx_password_dialog(self, pfx_path: str) -> None:
        if self._certificate_service is None:
            self.show_error("Gestione certificati non disponibile")
            return
        ft = self._ft
        password = ft.TextField(
            label="Password PFX",
            password=True,
            can_reveal_password=True,
        )

        def import_pfx(_: object) -> None:
            try:
                self._certificate_service.import_pfx(pfx_path, password.value or "")
            except CertificateServiceError as error:
                self.show_error(str(error))
                return
            self._close_dialog()
            self.show_certificate_preferences()
            self.show_certificate_status()

        dialog = ft.AlertDialog(
            title=ft.Text("Importa PFX"),
            content=ft.Container(width=420, content=password),
            actions=[
                ft.TextButton("Annulla", on_click=lambda _: self._close_dialog()),
                ft.TextButton("Importa", on_click=import_pfx),
            ],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def _confirm_delete_certificate(
        self, certificate: CertificateInfo | None
    ) -> None:
        if certificate is None or self._certificate_service is None:
            return
        ft = self._ft
        self._close_dialog()

        def delete(_: object) -> None:
            try:
                self._certificate_service.delete_certificate(certificate.thumbprint)
            except CertificateServiceError as error:
                self.show_error(str(error))
                return
            self._close_dialog()
            self.show_certificate_preferences()
            self.show_certificate_status()

        dialog = ft.AlertDialog(
            title=ft.Text("Cancella certificato"),
            content=ft.Text(f"Cancellare il certificato {certificate.name}?"),
            actions=[
                ft.TextButton("Annulla", on_click=lambda _: self._close_dialog()),
                ft.TextButton("Cancella", on_click=delete),
            ],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def _show_select_certificate_dialog(self) -> None:
        if self._certificate_service is None:
            self.show_error("Gestione certificati non disponibile")
            return
        ft = self._ft
        self._close_dialog()
        try:
            certificates = self._certificate_service.list_certificates()
        except CertificateServiceError as error:
            self.show_error(str(error))
            return

        def select_certificate(certificate: CertificateInfo) -> None:
            try:
                self._certificate_service.set_active_certificate(certificate.thumbprint)
            except CertificateServiceError as error:
                self.show_error(str(error))
                return
            self._close_dialog()
            self.show_certificate_preferences()
            self.show_certificate_status()

        if certificates:
            controls = [
                ft.TextButton(
                    content=ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text(certificate.name),
                                ft.Text(
                                    f"Scadenza: {self._format_system_date(certificate.valid_until)}",
                                    size=12,
                                ),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.START,
                            tight=True,
                            spacing=2,
                        ),
                        alignment=ft.Alignment(-1, 0),
                        width=460,
                    ),
                    style=ft.ButtonStyle(
                        padding=ft.Padding(
                            left=0,
                            top=6,
                            right=0,
                            bottom=6,
                        ),
                        alignment=ft.Alignment(-1, 0),
                    ),
                    on_click=lambda _, cert=certificate: select_certificate(cert),
                )
                for certificate in certificates
            ]
        else:
            controls = [ft.Text("Nessun certificato nello Store Windows")]

        dialog = ft.AlertDialog(
            title=ft.Text("Seleziona certificato"),
            content=ft.Container(
                width=500,
                height=360,
                content=ft.ListView(controls=controls, spacing=8),
            ),
            actions=[ft.TextButton("Annulla", on_click=lambda _: self._close_dialog())],
        )
        self._active_dialog = dialog
        self._page.show_dialog(dialog)

    def _open_queen_site(self, _: object | None = None) -> None:
        self._open_url("https://queensrl.net")

    def _open_url(self, url: str) -> None:
        if hasattr(self._page, "run_task"):
            self._page.run_task(self._launch_url, url)
            return
        self._page.launch_url(url)

    async def _launch_queen_site(self) -> None:
        await self._launch_url("https://queensrl.net")

    async def _launch_url(self, url: str) -> None:
        result = self._page.launch_url(url)
        if inspect.isawaitable(result):
            await result

    def _app_version(self) -> str:
        config_path = self._app_config_path
        if not config_path.is_file() and not config_path.is_absolute():
            config_path = self._application_root() / config_path
        if not config_path.is_file():
            return "00.000.000"
        try:
            for line in config_path.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip() == "version":
                    return value.strip().strip("'\"") or "00.000.000"
        except OSError:
            return "00.000.000"
        return "00.000.000"

    def _signed_history_documents(self) -> list[Path]:
        if not self._signed_history_directory.is_dir():
            return []
        return sorted(
            (
                path
                for path in self._signed_history_directory.iterdir()
                if path.is_file() and path.suffix.lower() == ".pdf"
            ),
            key=lambda path: path.stat().st_ctime,
            reverse=True,
        )

    def _local_learned_templates(self) -> list[Path]:
        template_root = self._learned_template_directory
        if not template_root.is_dir():
            return []
        return sorted(
            (
                path
                for path in template_root.iterdir()
                if path.is_file()
                and path.name.startswith("learned_")
                and path.suffix.lower() == ".json"
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def _open_signed_file(self, path: Path) -> None:
        if hasattr(self._page, "run_task"):
            self._page.run_task(self._launch_signed_file, path)
            return
        self._page.launch_url(path.resolve().as_uri())

    async def _launch_signed_file(self, path: Path) -> None:
        result = self._page.launch_url(path.resolve().as_uri())
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _format_file_created_at(path: Path) -> str:
        created_at = datetime.fromtimestamp(path.stat().st_ctime)
        return created_at.strftime("%d/%m/%Y %H:%M:%S")

    @staticmethod
    def _format_file_updated_at(path: Path) -> str:
        updated_at = datetime.fromtimestamp(path.stat().st_mtime)
        return updated_at.strftime("%d/%m/%Y %H:%M:%S")

    def _configure_window_icon(self) -> None:
        if sys.platform != "win32":
            return
        if self._window_icon_configured:
            return
        icon_path = self._resource_path("icons/favicon.ico")
        if not icon_path.is_file():
            return
        self._window_icon_configured = True
        threading.Thread(
            target=self._apply_windows_window_icon,
            args=(str(icon_path), self._page.title),
            daemon=True,
        ).start()

    @classmethod
    def _application_root(cls) -> Path:
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root)
        return Path(__file__).resolve().parent.parent

    @classmethod
    def _resource_path(cls, relative_path: str) -> Path:
        return cls._application_root() / "resources" / relative_path

    @classmethod
    def _image_data_uri(cls, relative_path: str) -> str:
        image_path = cls._resource_path(relative_path)
        try:
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError:
            return ""
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _apply_windows_window_icon(icon_path: str, title: str | None) -> None:
        if not title:
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.LoadImageW.argtypes = [
                wintypes.HINSTANCE,
                wintypes.LPCWSTR,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.LoadImageW.restype = wintypes.HANDLE
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.FindWindowW.restype = wintypes.HWND
            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            user32.GetWindowTextLengthW.restype = ctypes.c_int
            user32.GetWindowTextW.argtypes = [
                wintypes.HWND,
                wintypes.LPWSTR,
                ctypes.c_int,
            ]
            user32.GetWindowTextW.restype = ctypes.c_int
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            user32.SendMessageW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.SendMessageW.restype = wintypes.LPARAM
            image_icon = 1
            load_from_file = 0x00000010
            load_default_size = 0x00000040
            wm_seticon = 0x0080
            icon_small = 0
            icon_big = 1
            gclp_hicon = -14
            gclp_hiconsm = -34
            set_class_long = (
                user32.SetClassLongPtrW
                if hasattr(user32, "SetClassLongPtrW")
                else user32.SetClassLongW
            )
            set_class_long.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPARAM]
            set_class_long.restype = wintypes.LPARAM
            big_icon = user32.LoadImageW(
                None, icon_path, image_icon, 0, 0, load_from_file | load_default_size
            )
            small_icon = user32.LoadImageW(
                None, icon_path, image_icon, 16, 16, load_from_file
            )
            if not big_icon and not small_icon:
                return
            for _ in range(120):
                hwnds = MainView._find_windows_windows_by_title(
                    user32, ctypes, wintypes, title
                )
                for hwnd in hwnds:
                    if big_icon:
                        user32.SendMessageW(hwnd, wm_seticon, icon_big, big_icon)
                        set_class_long(hwnd, gclp_hicon, big_icon)
                    if small_icon:
                        user32.SendMessageW(hwnd, wm_seticon, icon_small, small_icon)
                        set_class_long(hwnd, gclp_hiconsm, small_icon)
                time.sleep(0.25)
        except Exception:
            return

    @staticmethod
    def _find_windows_windows_by_title(
        user32: object, ctypes_module: object, wintypes_module: object, title: str
    ) -> tuple[int, ...]:
        matches: list[int] = []
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            matches.append(int(hwnd))

        enum_windows_proc = ctypes_module.WINFUNCTYPE(
            ctypes_module.c_bool,
            wintypes_module.HWND,
            wintypes_module.LPARAM,
        )

        def collect_matching_window(candidate: int, _: int) -> bool:
            if not user32.IsWindowVisible(candidate):
                return True
            length = user32.GetWindowTextLengthW(candidate)
            if length:
                buffer = ctypes_module.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(candidate, buffer, length + 1)
                if title in buffer.value and int(candidate) not in matches:
                    matches.append(int(candidate))
            return True

        user32.EnumWindows(enum_windows_proc(collect_matching_window), 0)
        return tuple(matches)

    def _build_anchor_overlay_controls(
        self, overlays: tuple[AnchorOverlayViewModel, ...]
    ) -> list[object]:
        ft = self._ft
        border_side = ft.BorderSide(2, ft.Colors.GREEN)
        return [
            self._build_anchor_overlay_control(overlay, border_side)
            for overlay in overlays
        ]

    def _build_anchor_overlay_control(
        self, overlay: AnchorOverlayViewModel, border_side: object
    ) -> object:
        ft = self._ft
        target_id = getattr(overlay, "target_id", None)
        is_signature_area = target_id is not None or overlay.label == "Zona firma"
        ignore_interactions = self._manual_signature_mode and not is_signature_area
        return ft.Container(
            left=overlay.left,
            top=overlay.top,
            width=overlay.width,
            height=overlay.height,
            border=ft.Border(
                left=border_side,
                top=border_side,
                right=border_side,
                bottom=border_side,
            ),
            bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.GREEN),
            tooltip=overlay.label,
            ignore_interactions=ignore_interactions,
            on_click=(
                None
                if ignore_interactions
                else self._signature_area_click_handler(target_id)
            ),
            content=self._signature_control_for_overlay(overlay),
        )

    def _signature_area_click_handler(
        self, target_id: str | None
    ) -> Callable[[object], None]:
        return lambda _: self._invoke_signature_area_click(target_id)

    def _invoke_signature_area_click(self, target_id: str | None) -> None:
        if self._on_signature_area_click is not None:
            self._on_signature_area_click(target_id)

    def _signature_control_for_overlay(
        self, overlay: AnchorOverlayViewModel
    ) -> object | None:
        content = getattr(overlay, "signature_content", None)
        if content is None:
            return None
        media_type = getattr(overlay, "signature_media_type", "image/svg+xml")
        if media_type == "image/svg+xml":
            canvas = self._signature_canvas_for_overlay(content, overlay)
            if canvas is not None:
                return canvas
        encoded = base64.b64encode(content).decode("ascii")
        return self._ft.Image(
            src=f"data:{media_type};base64,{encoded}",
            width=overlay.width,
            height=overlay.height,
            fit=self._ft.BoxFit.CONTAIN,
        )

    def _signature_canvas_for_overlay(
        self, content: bytes, overlay: AnchorOverlayViewModel
    ) -> object | None:
        strokes = self._signature_strokes_from_svg(content)
        if not strokes:
            return None
        scale_x = overlay.width / 420
        scale_y = overlay.height / 180
        paint = self._signature_paint.copy(
            stroke_width=max(1.5, min(scale_x, scale_y) * 3)
        )
        shapes = []
        for stroke in strokes:
            for start, end in zip(stroke, stroke[1:]):
                shapes.append(
                    self._cv.Line(
                        start[0] * scale_x,
                        start[1] * scale_y,
                        end[0] * scale_x,
                        end[1] * scale_y,
                        paint=paint,
                    )
                )
        return self._cv.Canvas(
            shapes=shapes,
            width=overlay.width,
            height=overlay.height,
        )

    @staticmethod
    def _signature_strokes_from_svg(content: bytes) -> list[list[tuple[float, float]]]:
        svg = content.decode("utf-8", errors="ignore")
        strokes: list[list[tuple[float, float]]] = []
        for match in re.finditer(r"<polyline\b[^>]*\bpoints='([^']+)'", svg):
            points: list[tuple[float, float]] = []
            for point in match.group(1).split():
                x_value, separator, y_value = point.partition(",")
                if not separator:
                    continue
                try:
                    points.append((float(x_value), float(y_value)))
                except ValueError:
                    continue
            if len(points) > 1:
                strokes.append(points)
        return strokes

    def _handle_pdf_mouse_wheel(self, event: object) -> None:
        if not self._document_viewer.visible or not self._pdf_stack.visible:
            self._mouse_wheel_page_delta = 0.0
            return

        delta_y = self._scroll_delta_y(event)
        if delta_y == 0:
            return

        self._mouse_wheel_page_delta += delta_y
        if abs(self._mouse_wheel_page_delta) < self._MOUSE_WHEEL_PAGE_THRESHOLD:
            return

        if self._mouse_wheel_page_delta > 0:
            self._invoke(self._on_next)
        else:
            self._invoke(self._on_previous)
        self._mouse_wheel_page_delta = 0.0

    @staticmethod
    def _scroll_delta_y(event: object) -> float:
        scroll_delta = getattr(event, "scroll_delta", None)
        if scroll_delta is not None:
            value = getattr(scroll_delta, "y", None)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0

        for name in ("scroll_delta_y", "delta_y", "dy"):
            value = getattr(event, name, None)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    def _start_manual_signature_drag(self, event: object) -> None:
        if not self._manual_signature_mode:
            return
        position = event.local_position
        self._manual_drag_start = (float(position.x), float(position.y))
        self._manual_draft_rect = None

    def _update_manual_signature_drag(self, event: object) -> None:
        if not self._manual_signature_mode or self._manual_drag_start is None:
            return
        position = event.local_position
        left, top, width, height = self._normalized_drag_rect(
            self._manual_drag_start,
            (float(position.x), float(position.y)),
        )
        self._manual_draft_rect = (left, top, width, height)
        self._manual_draft_overlay.left = left
        self._manual_draft_overlay.top = top
        self._manual_draft_overlay.width = width
        self._manual_draft_overlay.height = height
        self._manual_draft_overlay.visible = True
        self._update_control(self._manual_draft_overlay)

    def _finish_manual_signature_drag(self, event: object) -> None:
        if (
            not self._manual_signature_mode
            or self._manual_drag_start is None
            or self._on_manual_signature_rect is None
            or self._pdf_stack.width is None
            or self._pdf_stack.height is None
        ):
            return
        if self._manual_draft_rect is None:
            position = event.local_position
            self._manual_draft_rect = self._normalized_drag_rect(
                self._manual_drag_start,
                (float(position.x), float(position.y)),
            )
        left, top, width, height = self._manual_draft_rect
        self._manual_drag_start = None
        self._manual_draft_rect = None
        self._manual_draft_overlay.visible = False
        self._update_control(self._manual_draft_overlay)
        if width < 8 or height < 8:
            return
        self._on_manual_signature_rect(
            left,
            top,
            width,
            height,
            float(self._pdf_stack.width),
            float(self._pdf_stack.height),
        )

    def _build_manual_overlay(
        self, left: float, top: float, width: float, height: float
    ) -> object:
        ft = self._ft
        border_side = ft.BorderSide(2, ft.Colors.BLUE)
        return ft.Container(
            left=left,
            top=top,
            width=width,
            height=height,
            border=ft.Border(
                left=border_side,
                top=border_side,
                right=border_side,
                bottom=border_side,
            ),
            bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.BLUE),
            tooltip="Rettangolo firma manuale",
            ignore_interactions=True,
        )

    def _start_signature_stroke(self, event: object) -> None:
        position = self._event_position(event)
        self._current_signature_stroke = [(float(position.x), float(position.y))]

    def _update_signature_stroke(self, event: object) -> None:
        if self._current_signature_stroke is None:
            return
        position = self._event_position(event)
        next_point = (float(position.x), float(position.y))
        previous_point = self._current_signature_stroke[-1]
        self._current_signature_stroke.append(next_point)
        self._signature_preview.src = self._signature_svg_data_uri(
            include_current=True
        )
        self._append_signature_segment(previous_point, next_point)
        self._update_signature_canvas()

    def _finish_signature_stroke(self, event: object) -> None:
        if self._current_signature_stroke is None:
            return
        position = self._event_position(event)
        next_point = (float(position.x), float(position.y))
        previous_point = self._current_signature_stroke[-1]
        self._current_signature_stroke.append(next_point)
        self._append_signature_segment(previous_point, next_point)
        self._commit_current_signature_stroke()
        self._signature_preview.src = self._signature_svg_data_uri()
        self._update_signature_canvas()

    def _commit_current_signature_stroke(self) -> None:
        if self._current_signature_stroke is None:
            return
        if len(self._current_signature_stroke) > 1:
            self._signature_strokes.append(self._current_signature_stroke)
        self._current_signature_stroke = None

    def _refresh_signature_canvas(self, include_current: bool = False) -> None:
        self._signature_canvas.shapes = []
        strokes = list(self._signature_strokes)
        if include_current and self._current_signature_stroke:
            strokes.append(self._current_signature_stroke)
        for stroke in strokes:
            for previous_point, next_point in zip(stroke, stroke[1:]):
                self._append_signature_segment(previous_point, next_point)

    def _append_signature_segment(
        self, start: tuple[float, float], end: tuple[float, float]
    ) -> None:
        if start == end:
            return
        line = self._cv.Line(
            start[0],
            start[1],
            end[0],
            end[1],
            paint=self._signature_paint,
        )
        self._signature_canvas.shapes = [*self._signature_canvas.shapes, line]

    def _update_signature_canvas(self) -> None:
        self._update_control(self._signature_canvas)

    def _update_control(self, control: object) -> None:
        if hasattr(control, "update"):
            try:
                control.update()
                return
            except (AssertionError, RuntimeError):
                pass
        self._page.update()

    def _signature_svg_data_uri(self, include_current: bool = False) -> str:
        encoded = base64.b64encode(
            self._signature_svg(include_current=include_current).encode("utf-8")
        ).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"

    def _signature_svg(self, include_current: bool = False) -> str:
        strokes = list(self._signature_strokes)
        if include_current and self._current_signature_stroke:
            strokes.append(self._current_signature_stroke)
        paths = "\n".join(self._svg_polyline(stroke) for stroke in strokes if len(stroke) > 1)
        return (
            "<svg xmlns='http://www.w3.org/2000/svg' "
            "width='420' height='180' viewBox='0 0 420 180'>"
            f"{paths}</svg>"
        )

    @staticmethod
    def _svg_polyline(points: list[tuple[float, float]]) -> str:
        encoded_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        return (
            f"<polyline points='{encoded_points}' fill='none' "
            "stroke='black' stroke-width='3' stroke-linecap='round' "
            "stroke-linejoin='round'/>"
        )

    @staticmethod
    def _normalized_drag_rect(
        start: tuple[float, float], end: tuple[float, float]
    ) -> tuple[float, float, float, float]:
        left = min(start[0], end[0])
        top = min(start[1], end[1])
        return left, top, abs(end[0] - start[0]), abs(end[1] - start[1])

    def _close_dialog(self) -> None:
        dialog = self._active_dialog
        self._active_dialog = None
        if dialog is None:
            return
        if dialog is not None and hasattr(dialog, "open"):
            dialog.open = False
        if hasattr(self._page, "pop_dialog"):
            self._page.pop_dialog()
        elif hasattr(self._page, "close_dialog"):
            self._page.close_dialog()
        self._page.update()

    @staticmethod
    def _format_system_date(value: str) -> str:
        if not value:
            return "-"
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return value
        try:
            locale.setlocale(locale.LC_TIME, "")
        except locale.Error:
            pass
        return parsed.strftime("%x")

    @staticmethod
    def _event_position(event: object) -> object:
        position = getattr(event, "local_position", None)
        if position is not None:
            return position
        return event

    @staticmethod
    def _anchor_status(anchor_count: int, selected_anchor: object | None) -> str:
        if selected_anchor is None:
            return f"Stato: documento aperto | Anchor trovati: {anchor_count}"

        bounds = selected_anchor.bounds
        page = selected_anchor.page_index + 1
        return (
            f"Stato: Anchor trovati: {anchor_count} | "
            f"Pagina anchor: {page} | "
            f"Coord: {bounds.left:.1f},{bounds.top:.1f},"
            f"{bounds.right:.1f},{bounds.bottom:.1f}"
        )

    @staticmethod
    def _invoke(callback: Callable[[], None] | None) -> None:
        if callback is not None:
            callback()
