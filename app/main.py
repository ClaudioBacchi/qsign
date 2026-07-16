"""Desktop entry point for QSign."""

from collections.abc import Callable
from pathlib import Path
import sys
import threading

APP_TITLE = "qSign by Queen Srl - queensrl.net"


def run() -> None:
    """Start the Flet desktop application."""
    import flet as ft

    from app.qsign_application import QSignApplication

    target: Callable[[ft.Page], None] = QSignApplication().main
    project_root = Path(__file__).resolve().parent.parent
    ft.run(
        main=target,
        before_main=_prepare_flet_window,
        assets_dir=str(project_root / "resources"),
    )


def _prepare_flet_window(page: object) -> None:
    setattr(page, "title", APP_TITLE)
    if sys.platform != "win32":
        return
    icon_path = (
        Path(__file__).resolve().parent.parent
        / "resources"
        / "icons"
        / "favicon.ico"
    )
    if not icon_path.is_file():
        return
    from ui.main_view import MainView

    threading.Thread(
        target=MainView._apply_windows_window_icon,
        args=(str(icon_path), APP_TITLE),
        daemon=True,
    ).start()


if __name__ == "__main__":
    run()
