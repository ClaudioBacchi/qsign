# QSign Document Intelligence Engine

## Purpose

The Document Intelligence Engine is the deterministic core that turns an
opened, text-based PDF into a documented preparation plan:

- recognized document type and template version;
- evidence explaining the recognition;
- resolved textual anchors;
- anchor-relative signature rectangles;
- neutral workflow preparation data.

It does not sign, modify, upload, or display documents. It does not communicate
with Wacom devices, certificate stores, PAdES providers, transports, or Flet.

## Design principles

1. **Deterministic:** identical document content, templates, and settings always
   produce the same result.
2. **Explainable:** every decision includes rule-level evidence and rejection
   reasons.
3. **Fail closed:** unknown, ambiguous, or geometrically invalid results never
   become automatic signature placements.
4. **Offline:** no network call is required or allowed by the engine.
5. **Provider-neutral:** PDF-library types never cross the document-analysis
   boundary.
6. **Configuration-driven:** new document types are added through templates,
   not conditional logic in the engine.
7. **Read-only:** intelligence analysis never changes the source PDF.

AI, machine learning, OCR, fuzzy semantic matching, and probabilistic inference
are explicitly excluded.

## Processing pipeline

The pipeline is strictly ordered:

**PDF document → text/layout extraction → template loading → recognition →
anchor resolution → placement → workflow preparation**

Each stage consumes immutable, provider-neutral input and returns either a
successful result or a typed failure with diagnostics. A later stage does not
run when an earlier stage is unresolved.

## Required provider-neutral document view

The intelligence components require a read-only `DocumentTextMap` concept. It
is produced by a future document-analysis adapter and contains:

- stable document identifier and optional content fingerprint;
- page count and canonical page geometries;
- ordered text blocks, lines, spans, and words;
- bounding rectangle for every geometric text unit;
- page index and reading order;
- document metadata already available through the PDF boundary;
- indication that no usable text layer exists.

The map contains no PyMuPDF objects. A future PyMuPDF adapter may create it, but
that choice remains outside the intelligence contracts.

Scanned image-only documents return `NO_TEXT_LAYER`. The engine does not attempt
OCR and does not guess their type.

## Components

### Document Intelligence Engine

**Responsibility**

Orchestrate one complete analysis, enforce stage ordering, collect diagnostics,
and produce the final preparation result.

**Input**

- document identity and `DocumentTextMap`;
- active template snapshot;
- engine settings;
- correlation identifier for logging.

**Output**

A `DocumentIntelligenceResult` with status, selected template reference,
recognition trace, resolved anchors, placements, workflow preparation, and
diagnostics.

**Dependencies**

- Template Engine;
- Recognition Engine;
- Anchor Engine;
- Placement Engine;
- injected logging and clock abstractions where audit timestamps are needed.

**Conceptual interface**

One side-effect-free analysis operation. It never activates templates or writes
to their repository during document processing.

### Recognition Engine

**Responsibility**

Evaluate active templates against normalized document features, calculate
scores, reject invalid candidates, and select one template only when the result
is sufficiently strong and unambiguous.

**Input**

- `DocumentTextMap`;
- validated template snapshot;
- recognition settings.

**Output**

`RecognitionResult`: `MATCHED`, `UNKNOWN`, or `AMBIGUOUS`, including ranked
candidates and rule-level evidence.

**Dependencies**

Only deterministic normalization and matcher evaluators. It has no repository,
UI, workflow, or device dependency.

### Template Engine

**Responsibility**

Load, validate, version, activate, import, and export template definitions. It
provides an immutable active snapshot to analysis runs.

**Input**

- repository operations;
- template definitions from the future Template Designer;
- schema and validation rules.

**Output**

- validated template versions;
- active template snapshot;
- validation reports;
- import/export packages.

**Dependencies**

An injected `TemplateRepository` boundary. Storage format and engine behavior
remain independent.

### Anchor Engine

**Responsibility**

Locate configured textual references in the recognized document, preserve
their geometry, disambiguate multiple occurrences, and return exactly resolved
anchors or explicit failures.

**Input**

- `DocumentTextMap`;
- selected template version;
- anchor definitions and resolution settings.

**Output**

`AnchorResolutionResult` values containing page, matched text, bounding
rectangle, match evidence, and selection trace.

**Dependencies**

Deterministic text normalization and geometric text data only.

### Placement Engine

**Responsibility**

Transform each resolved anchor and anchor-relative placement rule into a
validated signature rectangle in QSign canonical page coordinates.

**Input**

- resolved anchors;
- signature-area definitions;
- page geometries;
- placement settings.

**Output**

Validated `SignaturePlacement` values or explicit placement failures.

**Dependencies**

Geometry primitives only. It is independent from Wacom, PAdES, UI, transport,
certificates, and the concrete PDF library.

## Final result model

The final result contains these conceptual sections:

| Section | Meaning |
|---|---|
| Status | `READY`, `UNKNOWN_DOCUMENT`, `AMBIGUOUS_DOCUMENT`, `NO_TEXT_LAYER`, `ANCHOR_UNRESOLVED`, `PLACEMENT_INVALID`, or `ANALYSIS_ERROR` |
| Document reference | Stable identity or fingerprint of the analyzed input |
| Template reference | Template identifier and immutable version |
| Recognition | Total score, runner-up score, threshold, ambiguity margin, and evidence |
| Anchors | Resolved occurrence and geometry for every required anchor |
| Placements | Page and canonical rectangle for every signature role |
| Workflow preparation | Neutral ordered actions and roles proposed by the template |
| Diagnostics | Machine-readable codes plus human-readable explanations |

Workflow preparation is a plan, not workflow execution. It may describe actions
such as “capture signature for role Patient” and “apply signature to placement
PatientSignature”, but it does not choose a device, certificate, signing
provider, or transport.

## Orchestration rules

1. Create one immutable snapshot of templates and settings for the analysis.
2. Reject a document with no usable text layer.
3. Normalize text once and reuse the result throughout recognition and anchor
   resolution.
4. Run recognition and stop on `UNKNOWN` or `AMBIGUOUS`.
5. Resolve every required anchor from the selected template.
6. Stop if a required anchor is missing or ambiguous.
7. Calculate all placements and validate every rectangle.
8. Stop if any required placement is invalid.
9. Build the neutral workflow preparation from the exact template version.
10. Return the full evidence trail.

The engine never falls back silently to coordinates, another template, or a
“best effort” anchor.

## Performance model

The design targets linear work over document text and the number of active
rules:

- text normalization is performed once per document;
- normalized page text and token geometry are reusable;
- literal terms may be indexed per page;
- regular expressions are compiled when a template snapshot is loaded;
- templates with failed mandatory preconditions are removed before weighted
  scoring;
- immutable templates allow safe caching by template version and checksum.

Caching is an optimization only. It must not alter ordering or results.

## Diagnostics and auditability

Every run should be reproducible from:

- document fingerprint;
- template identifier, version, and checksum;
- engine-settings version;
- matcher evidence and scores;
- selected anchor occurrences;
- calculated rectangles;
- engine version.

Document clinical content must not be copied into general logs. Diagnostics
should prefer rule identifiers, page numbers, hashes, and short redacted
excerpts explicitly marked safe by policy.

## Extension rules

- Add matcher behavior through a matcher evaluator registered by type.
- Add persistence through another `TemplateRepository`.
- Add document extraction through a provider implementing the document-analysis
  boundary.
- Add placement strategies through explicit strategy identifiers in the
  template schema.
- Reject unknown matcher or placement types during template validation.

No extension may add dependencies from intelligence components to UI, signing,
devices, transport, or client-specific workflows.

