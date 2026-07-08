"""Desktop entry point for QSign."""

from collections.abc import Callable
from pathlib import Path


def run() -> None:
    """Start the Flet desktop application."""
    import flet as ft

    from app.qsign_application import QSignApplication

    target: Callable[[ft.Page], None] = QSignApplication().main
    project_root = Path(__file__).resolve().parent.parent
    ft.run(main=target, assets_dir=str(project_root / "resources"))


if __name__ == "__main__":
    run()
