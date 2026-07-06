# QSign — Foundation Architecture

## Scope

Milestone 1 establishes contracts, dependency direction, and a desktop shell.
It deliberately contains no real PDF parsing or rendering, document transport,
signature-device SDK integration, certificate-store access, PAdES signing, or
client workflow.

## Dependency direction

```text
UI → Workflow → Services → Providers
```

Domain models do not depend on any layer. Composition belongs in `app`; only
that package may select concrete providers. Services receive collaborators
through constructors and never import the UI.

## Decisions

### PDF libraries remain behind ports

`PDFService` owns the document lifecycle. Structural inspection and persistence
are delegated to `PDFDocumentBackend`; rendering and signature operations have
separate contracts. This avoids binding domain state to objects from a future
PDF library and lets unit tests use a deterministic fake.

The default backend is intentionally unavailable. It fails explicitly rather
than presenting placeholder bytes as valid PDF processing.

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
the application composition layer. Future controllers can invoke workflows
without placing document logic in UI event handlers.

## Extension rules

1. Put technology-specific code in a provider.
2. Expose provider-neutral models at service boundaries.
3. Inject implementations at the composition root.
4. Do not import `ui` from `services`, `providers`, or `models`.
5. Add contract tests for every new provider and unit tests for orchestration.
6. Keep unsupported milestone behavior explicit with `NotImplementedError`.

