# QSign — Foundation Architecture

## Scope

Milestone 1 established contracts, dependency direction, and a desktop shell.
Milestone 2 adds only PDF opening and page rendering. Document editing,
transport, signature-device SDK integration, certificate-store access, PAdES
signing, annotations, and client workflows remain excluded.

## Dependency direction

```text
UI → Workflow → Services → Providers
```

Domain models do not depend on any layer. Composition belongs in `app`; only
that package may select concrete providers. Services receive collaborators
through constructors and never import the UI.

## Decisions

### PyMuPDF remains behind the rendering port

`PDFService` owns the document lifecycle. Structural inspection and persistence
are delegated to backend-neutral ports; rendering and signature operations have
separate contracts. `PyMuPDFRenderer` implements `PDFRenderer` and is selected
only in the application composition root. `PyMuPDFDocumentBackend` adapts that
same rendering session to the pre-existing document-inspection port. Neither
the UI, domain models, nor `PDFService` imports PyMuPDF.

The renderer owns one open document and a bounded LRU cache keyed by zero-based
page index and zoom. Opening or closing a document clears the cache. Pixmaps and
page handles are released immediately after PNG creation; the document is
closed deterministically through `PDFService.close_document`.

The persistence backend remains intentionally unavailable because PDF editing
and saving are outside Milestone 2.

### Providers implement stable service contracts

Transport providers implement `TransportService`. Wacom models implement
`WacomProvider` and are consumed through `WacomService`. New providers can
therefore be added by implementing a contract and changing composition, without
editing workflows.

Certificate provider contracts will be split out when their real capabilities
are known; inventing a Windows, smart-card, token, or remote-signature API now
would prematurely constrain that design.

### Logging is injected

Services receive `LoggingService` and do not call the Python logging module
directly. Handler, format, destination, and telemetry decisions remain
centralized.

### Settings are typed but storage-neutral

The six required sections exist as typed dataclasses. No JSON, TOML, registry,
database, or environment-variable format is selected in this milestone.

### UI contains presentation behavior only

`MainView` creates and updates Flet controls. Actions are callbacks supplied by
`PDFViewerController` owns navigation and zoom state and invokes `PDFService`.
The UI receives only PNG bytes and primitive display values; it knows neither
PyMuPDF nor renderer objects.

## Extension rules

1. Put technology-specific code in a provider.
2. Expose provider-neutral models at service boundaries.
3. Inject implementations at the composition root.
4. Do not import `ui` from `services`, `providers`, or `models`.
5. Add contract tests for every new provider and unit tests for orchestration.
6. Keep unsupported milestone behavior explicit with `NotImplementedError`.
