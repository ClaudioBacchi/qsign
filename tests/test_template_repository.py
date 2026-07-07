"""Tests for filesystem template loading."""

import tempfile
import unittest
from pathlib import Path

from models.template import RuleScope, TemplateState
from services.templates.template_repository import (
    FilesystemTemplateRepository,
    TemplateRepositoryError,
)


class FilesystemTemplateRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.templates_dir = Path(__file__).parents[1] / "templates"

    def test_repository_loads_demo_template(self) -> None:
        repository = FilesystemTemplateRepository(self.templates_dir)

        templates = repository.list_templates()

        self.assertEqual(len(templates), 1)
        template = templates[0]
        self.assertEqual(template.template_id, "qsign-demo-privacy")
        self.assertEqual(template.state, TemplateState.DRAFT)
        self.assertEqual(template.recognition_rules[0].scope, RuleScope.DOCUMENT)
        self.assertEqual(template.anchor_rules[0].anchor_id, "main-signature-anchor")
        self.assertEqual(template.placement_rules[0].anchor_id, "main-signature-anchor")
        self.assertTrue(template.checksum)

    def test_get_template_returns_matching_template(self) -> None:
        repository = FilesystemTemplateRepository(self.templates_dir)

        template = repository.get_template("qsign-demo-privacy")

        self.assertEqual(template.code, "QSIGN_DEMO_PRIVACY")

    def test_get_template_raises_for_unknown_id(self) -> None:
        repository = FilesystemTemplateRepository(self.templates_dir)

        with self.assertRaises(TemplateRepositoryError):
            repository.get_template("unknown")

    def test_repository_rejects_missing_directory(self) -> None:
        repository = FilesystemTemplateRepository(self.templates_dir / "missing")

        with self.assertRaises(TemplateRepositoryError):
            repository.list_templates()

    def test_repository_rejects_invalid_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "broken.json").write_text("{}", encoding="utf-8")
            repository = FilesystemTemplateRepository(path)

            with self.assertRaises(TemplateRepositoryError):
                repository.list_templates()


if __name__ == "__main__":
    unittest.main()
