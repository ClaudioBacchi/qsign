"""Minimal Flet shell for Milestone 1."""

import base64
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

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
    ) -> None:
        import flet as ft
        import flet.canvas as cv

        self._ft = ft
        self._cv = cv
        self._page = page
        self._on_open_document: Callable[[str], None] | None = None
        self._on_close: Callable[[], None] | None = None
        self._on_previous: Callable[[], None] | None = None
        self._on_next: Callable[[], None] | None = None
        self._on_zoom_in: Callable[[], None] | None = None
        self._on_zoom_out: Callable[[], None] | None = None
        self._on_signature_area_click: Callable[[], None] | None = None
        self._on_manual_signature_rect: (
            Callable[[float, float, float, float, float, float], None] | None
        ) = None
        self._manual_signature_mode = False
        self._manual_drag_start: tuple[float, float] | None = None
        self._manual_draft_rect: tuple[float, float, float, float] | None = None
        self._active_dialog: object | None = None
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
        self._document_name = ft.Text("Nessun documento")
        self._page_count = ft.Text("Pagina — / —")
        self._zoom = ft.Text("Zoom: 100%")
        self._document_status = ft.Text("Stato: pronto")
        self._viewer_placeholder = ft.Text("Visualizzazione PDF", size=24)
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
            controls=[self._viewer_placeholder, self._signature_surface],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.START,
            scroll=ft.ScrollMode.AUTO,
        )
        self._document_viewer = ft.ListView(
            controls=[self._horizontal_viewer],
            scroll=ft.ScrollMode.AUTO,
            padding=20,
            expand=True,
        )

    def bind_actions(
        self,
        on_open_document: Callable[[str], None],
        on_close: Callable[[], None],
        on_previous: Callable[[], None],
        on_next: Callable[[], None],
        on_zoom_in: Callable[[], None],
        on_zoom_out: Callable[[], None],
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
        self._on_manual_signature_rect = on_manual_signature_rect
        self._on_signature_area_click = on_signature_area_click

    def build(self) -> None:
        ft = self._ft
        self._page.title = "QSign"
        self._page.padding = 0
        self._page.services.append(self._file_picker)
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
                ft.TextButton("Informazioni", on_click=lambda _: self.show_information()),
            ]
        )
        viewer = ft.Container(
            content=self._document_viewer,
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

    def clear_document(self) -> None:
        self._pdf_image.visible = False
        self._pdf_stack.visible = False
        self._pdf_stack.controls = [self._pdf_image]
        self._manual_draft_overlay.visible = False
        self._manual_draft_rect = None
        self._manual_drag_start = None
        self._viewer_placeholder.visible = True
        self._document_name.value = "Nessun documento"
        self._page_count.value = "Pagina — / —"
        self._zoom.value = "Zoom: 100%"
        self.show_status("pronto")

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
        if dialog is not None and hasattr(dialog, "open"):
            dialog.open = False
        if hasattr(self._page, "pop_dialog"):
            self._page.pop_dialog()
        elif hasattr(self._page, "close_dialog"):
            self._page.close_dialog()
        self._page.update()

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
