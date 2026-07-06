"""Minimal Flet shell for Milestone 1."""

import base64
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import flet as ft


class MainView:
    """Build controls and expose presentation-only updates."""

    def __init__(
        self,
        page: "ft.Page",
    ) -> None:
        import flet as ft

        self._ft = ft
        self._page = page
        self._on_open_document: Callable[[str], None] | None = None
        self._on_close: Callable[[], None] | None = None
        self._on_previous: Callable[[], None] | None = None
        self._on_next: Callable[[], None] | None = None
        self._on_zoom_in: Callable[[], None] | None = None
        self._on_zoom_out: Callable[[], None] | None = None
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
        self._horizontal_viewer = ft.Row(
            controls=[self._viewer_placeholder, self._pdf_image],
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
    ) -> None:
        """Bind controller actions without exposing Flet outside the view."""
        self._on_open_document = on_open_document
        self._on_close = on_close
        self._on_previous = on_previous
        self._on_next = on_next
        self._on_zoom_in = on_zoom_in
        self._on_zoom_out = on_zoom_out

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
    ) -> None:
        """Display renderer output without knowing which engine produced it."""
        ft = self._ft
        image_source = base64.b64encode(image_content).decode("ascii")
        self._pdf_image.src = f"data:image/png;base64,{image_source}"
        self._pdf_image.width = image_width
        self._pdf_image.height = image_height
        self._pdf_image.visible = True
        self._viewer_placeholder.visible = False
        self._document_name.value = filename
        self._page_count.value = f"Pagina {page_number} / {page_count}"
        self._zoom.value = f"Zoom: {zoom:.0%}"
        self._document_status.value = "Stato: documento aperto"
        self._page.update()

    def show_status(self, message: str) -> None:
        self._document_status.value = f"Stato: {message}"
        self._page.update()

    def clear_document(self) -> None:
        self._pdf_image.visible = False
        self._viewer_placeholder.visible = True
        self._document_name.value = "Nessun documento"
        self._page_count.value = "Pagina — / —"
        self._zoom.value = "Zoom: 100%"
        self.show_status("pronto")

    def show_error(self, message: str) -> None:
        self.show_status(f"errore — {message}")

    def show_information(self) -> None:
        ft = self._ft
        dialog = ft.AlertDialog(
            title=ft.Text("QSign"),
            content=ft.Text("Document Rendering — Milestone 2"),
        )
        self._page.show_dialog(dialog)

    @staticmethod
    def _invoke(callback: Callable[[], None] | None) -> None:
        if callback is not None:
            callback()
