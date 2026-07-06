"""Minimal Flet shell for Milestone 1."""

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import flet as ft


class MainView:
    """Build controls and expose presentation-only updates."""

    def __init__(
        self,
        page: "ft.Page",
        on_open: Callable[[], None],
        on_close: Callable[[], None],
        on_information: Callable[[], None],
    ) -> None:
        import flet as ft

        self._ft = ft
        self._page = page
        self._on_open = on_open
        self._on_close = on_close
        self._on_information = on_information
        self._document_name = ft.Text("Nessun documento")
        self._page_count = ft.Text("Pagine: —")
        self._document_status = ft.Text("Stato: pronto")

    def build(self) -> None:
        ft = self._ft
        self._page.title = "QSign"
        self._page.padding = 0
        toolbar = ft.Row(
            controls=[
                ft.ElevatedButton("Apri PDF", on_click=lambda _: self._on_open()),
                ft.OutlinedButton("Chiudi PDF", on_click=lambda _: self._on_close()),
                ft.TextButton(
                    "Informazioni", on_click=lambda _: self._on_information()
                ),
            ]
        )
        viewer = ft.Container(
            content=ft.Text("Visualizzazione PDF", size=24),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )
        status_bar = ft.Container(
            content=ft.Row(
                controls=[
                    self._document_name,
                    self._page_count,
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

    def show_status(self, message: str) -> None:
        self._document_status.value = f"Stato: {message}"
        self._page.update()

    def clear_document(self) -> None:
        self._document_name.value = "Nessun documento"
        self._page_count.value = "Pagine: —"
        self.show_status("pronto")

    def show_information(self) -> None:
        ft = self._ft
        dialog = ft.AlertDialog(
            title=ft.Text("QSign"),
            content=ft.Text("Foundation Architecture — Milestone 1"),
        )
        self._page.open(dialog)
