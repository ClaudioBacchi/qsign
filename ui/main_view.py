"""Minimal Flet shell for Milestone 1."""

import base64
import inspect
import locale
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.services.certificate_service import (
    CertificateInfo,
    CertificateService,
    CertificateServiceError,
)
from services.signature.signature_service import CapturedSignature

if TYPE_CHECKING:
    import flet as ft


class AnchorOverlayViewModel(Protocol):
    """Presentation-only rectangle projected onto the rendered page."""

    left: float
    top: float
    width: float
    height: float
    label: str
    signature_content: bytes | None
    signature_media_type: str


class MainView:
    """Build controls and expose presentation-only updates."""

    def __init__(
        self,
        page: "ft.Page",
        certificate_service: CertificateService | None = None,
    ) -> None:
        import flet as ft
        import flet.canvas as cv

        self._ft = ft
        self._cv = cv
        self._page = page
        self._certificate_service = certificate_service
        self._on_open_document: Callable[[str], None] | None = None
        self._on_close: Callable[[], None] | None = None
        self._on_previous: Callable[[], None] | None = None
        self._on_next: Callable[[], None] | None = None
        self._on_zoom_in: Callable[[], None] | None = None
        self._on_zoom_out: Callable[[], None] | None = None
        self._on_save_signed_pdf: Callable[[], None] | None = None
        self._on_signature_area_click: Callable[[], None] | None = None
        self._on_manual_signature_rect: (
            Callable[[float, float, float, float, float, float], None] | None
        ) = None
        self._manual_signature_mode = False
        self._manual_drag_start: tuple[float, float] | None = None
        self._manual_draft_rect: tuple[float, float, float, float] | None = None
        self._active_dialog: object | None = None
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
        self._document_status = ft.Text(self._certificate_status_text())
        self._viewer_placeholder = ft.GestureDetector(
            content=ft.Container(
                content=ft.Image(
                    src="images/logo_qsign_grande.png",
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
        on_signature_area_click: Callable[[], None] | None = None,
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
        self._on_signature_area_click = on_signature_area_click

    def build(self) -> None:
        ft = self._ft
        self._page.title = "QSign"
        self._page.padding = 0
        self._page.services.append(self._file_picker)
        self._page.services.append(self._pfx_file_picker)
        self._configure_window_icon()
        toolbar = ft.Row(
            controls=[
                ft.ElevatedButton("Apri PDF", on_click=self._pick_pdf),
                ft.OutlinedButton(
                    "Chiudi PDF", on_click=lambda _: self._invoke(self._on_close)
                ),
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
                ft.OutlinedButton(
                    "Salva PDF firmato",
                    on_click=lambda _: self._invoke(self._on_save_signed_pdf),
                ),
                ft.TextButton(
                    "Certificato",
                    on_click=lambda _: self.show_certificate_preferences(),
                ),
                ft.TextButton("Informazioni", on_click=lambda _: self.show_information()),
            ]
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

    def show_certificate_status(self) -> None:
        self._document_status.value = self._certificate_status_text()
        self._page.update()

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

    def clear_document(self) -> None:
        self._pdf_image.visible = False
        self._pdf_stack.visible = False
        self._pdf_stack.controls = [self._pdf_image]
        self._manual_draft_overlay.visible = False
        self._manual_draft_rect = None
        self._manual_drag_start = None
        self._home_view.visible = True
        self._viewer_placeholder.visible = True
        self._document_viewer.visible = False
        self._document_name.value = "Nessun documento"
        self._page_count.value = "Pagina — / —"
        self._zoom.value = "Zoom: 100%"
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

    def ask_save_template(self, on_confirm: Callable[[], None]) -> None:
        ft = self._ft

        def confirm(_: object) -> None:
            self._close_dialog()
            on_confirm()

        def cancel(_: object) -> None:
            self._close_dialog()
            self.show_status("modello non salvato")

        dialog = ft.AlertDialog(
            title=ft.Text("QSign"),
            content=ft.Text("Vuoi salvare questo modello per i prossimi documenti?"),
            actions=[
                ft.TextButton("No", on_click=cancel),
                ft.TextButton("Sì", on_click=confirm),
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
            title=ft.Text("QSign"),
            content=ft.Text("Document Rendering — Milestone 2"),
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
                                ft.OutlinedButton(
                                    "Genera",
                                    on_click=lambda _: self._show_generate_certificate_dialog(),
                                ),
                                ft.OutlinedButton("Importa PFX", on_click=self._pick_pfx),
                                ft.OutlinedButton(
                                    "Seleziona certificato",
                                    on_click=lambda _: self._show_select_certificate_dialog(),
                                ),
                                ft.OutlinedButton(
                                    "Cancella",
                                    disabled=certificate is None,
                                    on_click=lambda _: self._confirm_delete_certificate(
                                        certificate
                                    ),
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
                    content=ft.Column(
                        controls=[
                            ft.Text(certificate.name),
                            ft.Text(
                                f"Scadenza: {self._format_system_date(certificate.valid_until)}",
                                size=12,
                            ),
                        ],
                        tight=True,
                        spacing=2,
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
        if hasattr(self._page, "run_task"):
            self._page.run_task(self._launch_queen_site)
            return
        self._page.launch_url("https://queensrl.net")

    async def _launch_queen_site(self) -> None:
        result = self._page.launch_url("https://queensrl.net")
        if inspect.isawaitable(result):
            await result

    def _configure_window_icon(self) -> None:
        if sys.platform != "win32":
            return
        if self._window_icon_configured:
            return
        icon_path = (
            Path(__file__).resolve().parent.parent
            / "resources"
            / "icons"
            / "favicon.ico"
        )
        if not icon_path.is_file():
            return
        self._window_icon_configured = True
        threading.Thread(
            target=self._apply_windows_window_icon,
            args=(str(icon_path), self._page.title),
            daemon=True,
        ).start()

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
            ft.Container(
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
                ignore_interactions=(
                    self._manual_signature_mode and overlay.label != "Zona firma"
                ),
                on_click=(
                    None
                    if self._manual_signature_mode and overlay.label != "Zona firma"
                    else lambda _: self._invoke(self._on_signature_area_click)
                ),
                content=self._signature_control_for_overlay(overlay),
            )
            for overlay in overlays
        ]

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
