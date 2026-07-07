"""Template repository abstractions and filesystem implementation."""

from abc import ABC, abstractmethod
from pathlib import Path

from models.template import Template


class TemplateRepositoryError(RuntimeError):
    """Raised when templates cannot be loaded or parsed."""


class TemplateRepository(ABC):
    """Storage-neutral boundary for QSign templates."""

    @abstractmethod
    def list_templates(self) -> tuple[Template, ...]:
        """Return all templates available in the repository."""

    @abstractmethod
    def get_template(self, template_id: str) -> Template:
        """Return one template by stable identifier."""


class FilesystemTemplateRepository(TemplateRepository):
    """Load immutable template definitions from JSON files."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def list_templates(self) -> tuple[Template, ...]:
        from services.templates.template_loader import load_template_file

        if not self._root.exists():
            raise TemplateRepositoryError(f"Template directory does not exist: {self._root}")
        if not self._root.is_dir():
            raise TemplateRepositoryError(f"Template path is not a directory: {self._root}")

        templates = [
            load_template_file(path)
            for path in sorted(self._root.glob("*.json"), key=lambda item: item.name)
        ]
        return tuple(templates)

    def get_template(self, template_id: str) -> Template:
        for template in self.list_templates():
            if template.template_id == template_id:
                return template
        raise TemplateRepositoryError(f"Template not found: {template_id}")
