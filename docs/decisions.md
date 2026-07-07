# QSign architectural decisions

## ADR-002 — PyMuPDF as the PDF rendering engine

**Status:** Accepted for Milestone 2  
**Release:** `v0.2-document-rendering`

### Context

QSign needs fast desktop PDF rendering now and will later need reliable page
coordinates for graphical signature placement. The Foundation Architecture
requires the selected technology to remain replaceable and invisible to Flet,
workflows, domain models, console applications, Windows services, and tests.

### Decision

Use only the `pymupdf` package as the concrete implementation of
`PDFRenderer`. `PyMuPDFDocumentBackend` adapts the renderer to the Foundation
inspection port. Instantiate both in the application composition root and pass
them to `PDFService` through dependency injection.

Rendered pages cross the boundary as `RenderedPage`, containing PNG bytes and
dimensions. The UI receives only those bytes and primitive navigation values.

### Rationale

- PyMuPDF provides fast raster rendering with a compact API.
- It supports Python 3.14 through its stable ABI distribution.
- Its page geometry model is suitable for future coordinate-based work.
- It allows explicit document lifecycle management and in-memory PNG output.
- Keeping it behind `PDFRenderer` prevents vendor types from entering QSign's
  service or presentation contracts.

### Consequences

- `pymupdf` is a runtime dependency beginning with v0.2.
- PyMuPDF is dual-licensed under AGPL and commercial terms. Before proprietary
  distribution, Queen must verify AGPL compliance or obtain the appropriate
  commercial license; see the
  [official licensing documentation](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).
- One renderer instance owns one open document.
- Rendered pages are cached internally by page index and zoom in a bounded LRU.
- Page, pixmap, cache, and document resources have explicit release points.
- Rotation, thumbnails, and annotation rendering remain placeholders.
- PDF modification, signature insertion, and persistence remain unsupported.

## ADR-003 — Early introduction of development and release tooling

**Status:** Accepted for Milestone 2.1

**Release:** `v0.2.1-development-infrastructure`

### Context

QSign is intended to become a long-lived commercial product. Reproducible
development startup, explicit dependency installation, version metadata, and a
stable release preparation entry point are needed before packaging technology
is selected.

### Decision

Keep development and release tooling at the repository root. Provide separate
normal and debug launchers, a pinned `requirements.txt`, machine-readable
`version.json`, and a PowerShell release preparation script.

The release script creates only the required directory structure and validation
steps. PyInstaller packaging, executable signing, and documentation copying are
recorded as commented placeholders and are not implemented.

### Consequences

- Developers use the same virtual environment and startup entry point.
- Release preparation is repeatable without selecting a packager prematurely.
- Build, distribution, release, IDE, cache, and runtime-log output stays out of
  version control.
- Future packaging work has a documented insertion point without affecting the
  application architecture.

## ADR-004 — Deterministic Document Intelligence architecture

**Status:** Accepted as design; not implemented

**Release:** Documentation milestone, no runtime release

### Context

QSign must recognize document types, locate signature points, and prepare
workflow data offline without requiring a physician to search through the PDF.
The result affects later signing activity, so hidden heuristics and
unexplainable approximations are unacceptable.

### Decision

Design Document Intelligence as five separate provider-neutral components:
Document Intelligence, Template, Recognition, Anchor, and Placement Engines.

Recognition uses mandatory and exclusion gates followed by normalized weighted
evidence. A result is accepted only above threshold and outside the configured
ambiguity margin. Anchor resolution operates on textual references and their
geometry. Signature rectangles are calculated from anchor-relative placement
rules in one canonical coordinate system.

The engine returns evidence and neutral workflow preparation. It does not
execute workflows or depend on Wacom, PAdES, certificates, Flet, transport, or
client integrations.

AI, machine learning, OCR, fuzzy semantic matching, image classification, and
absolute signature coordinates in templates are excluded.

### Consequences

- Every automatic result can be reproduced and explained rule by rule.
- Unknown documents, close template scores, ambiguous anchors, and invalid
  rectangles stop automatic preparation.
- Scanned documents without a text layer cannot be recognized until a separate
  future decision explicitly introduces an allowed extraction strategy.
- New document families are added with validated templates rather than
  application conditionals.
- A document-analysis adapter must provide ordered text and canonical geometry
  without exposing concrete PDF-library objects.

## ADR-005 — Template persistence boundary and planned SQLite repository

**Status:** Accepted as design; database not implemented

**Release:** Documentation milestone, no runtime release

### Context

The future Template Designer needs transactional draft publication, immutable
version history, activation state, audit metadata, and reliable offline
operation. Templates must also remain portable and testable independently from
their storage.

### Decision

Define a storage-neutral `TemplateRepository` boundary. Plan SQLite as the
default local production repository because it is offline, transactional,
single-file, and serverless. Use a canonical versioned JSON package for import,
export, review, and portability.

No database, schema, migration, or persistence implementation is created in
this milestone.

### Consequences

- Intelligence analysis consumes immutable template snapshots and performs no
  storage queries during a run.
- The Template Designer never manipulates tables directly.
- Published versions are immutable and referenced by version and checksum.
- SQLite can be replaced without changing recognition, anchor, or placement
  behavior.
- Schema design, migration, backup, recovery, locking, and encryption policy
  remain mandatory work for the future implementation milestone.
