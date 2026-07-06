"""Composition root for the current desktop shell."""

from typing import TYPE_CHECKING

from services.logging.logging_service import LoggingService

if TYPE_CHECKING:
    import flet as ft


class QSignApplication:
    """Build the UI and inject application-level callbacks."""

    def __init__(self, logger: LoggingService | None = None) -> None:
        self._logger = logger or LoggingService.create("qsign")

    def main(self, page: "ft.Page") -> None:
        """Configure the QSign desktop window."""
        from ui.main_view import MainView

        self._logger.info("Starting QSign desktop shell")
        view = MainView(
            page=page,
            on_open=lambda: view.show_status("Apertura PDF prevista in una milestone successiva"),
            on_close=lambda: view.clear_document(),
            on_information=lambda: view.show_information(),
        )
        view.build()

