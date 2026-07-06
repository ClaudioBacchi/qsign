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
