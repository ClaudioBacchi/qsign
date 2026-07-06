"""Desktop entry point for QSign."""

from collections.abc import Callable


def run() -> None:
    """Start the Flet desktop application."""
    import flet as ft

    from app.qsign_application import QSignApplication

    target: Callable[[ft.Page], None] = QSignApplication().main
    ft.app(target=target)


if __name__ == "__main__":
    run()

