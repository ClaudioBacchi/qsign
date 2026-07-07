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

    def _demo_only_repository(self, directory: str) -> FilesystemTemplateRepository:
        path = Path(directory)
        self._write_demo_template(path)
        return FilesystemTemplateRepository(path)

    @staticmethod
    def _write_demo_template(path: Path) -> None:
        (path / "qsign_sample_privacy.json").write_text(
            """{
  "template_id": "qsign-demo-privacy",
  "code": "QSIGN_DEMO_PRIVACY",
  "name": "QSign demo privacy",
  "document_type": "privacy",
  "version": "0.1.0",
  "state": "draft",
  "recognition_rules": [
    {
      "rule_id": "privacy-title",
      "rule_type": "literal",
      "expression": "TRATTAMENTO DATI PERSONALI",
      "scope": "document"
    }
  ],
  "anchor_rules": [
    {
      "anchor_id": "main-signature-anchor",
      "name": "Firma interessato",
      "search_type": "text",
      "expression": "In fede",
      "scope": "document"
    }
  ],
  "placement_rules": [
    {
      "placement_id": "main-signature",
      "role": "signer",
      "anchor_id": "main-signature-anchor",
      "side": "below",
      "alignment": "center",
      "x_offset": 0,
      "y_offset": 8,
      "width": 120,
      "height": 45
    }
  ]
}""",
            encoding="utf-8",
        )

    def test_repository_loads_demo_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._demo_only_repository(directory)

            templates = repository.list_templates()

        self.assertEqual(len(templates), 1)
        template = templates[0]
        self.assertEqual(template.template_id, "qsign-demo-privacy")
        self.assertEqual(template.state, TemplateState.DRAFT)
        self.assertEqual(template.recognition_rules[0].scope, RuleScope.DOCUMENT)
        self.assertEqual(template.anchor_rules[0].anchor_id, "main-signature-anchor")
        self.assertEqual(template.placement_rules[0].anchor_id, "main-signature-anchor")
        self.assertTrue(template.checksum)

    def test_repository_loads_manual_placement_page_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "manual.json").write_text(
                """{
  "template_id": "manual-page",
  "code": "MANUAL_PAGE",
  "name": "Manual page",
  "document_type": "manual_signature_flow",
  "version": "0.1.0",
  "state": "draft",
  "recognition_rules": [
    {
      "rule_id": "manual-recognition-phrase",
      "rule_type": "literal",
      "expression": "Documento Speciale"
    }
  ],
  "placement_rules": [
    {
      "placement_id": "manual-signature",
      "role": "signer",
      "anchor_id": "manual",
      "side": "manual",
      "alignment": "manual",
      "x_offset": 10,
      "y_offset": 20,
      "width": 30,
      "height": 40,
      "page_index": 1
    }
  ]
}""",
                encoding="utf-8",
            )
            repository = FilesystemTemplateRepository(path)

            template = repository.list_templates()[0]

            self.assertEqual(template.placement_rules[0].page_index, 1)

    def test_get_template_returns_matching_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._demo_only_repository(directory)

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
