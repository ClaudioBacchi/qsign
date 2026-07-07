# QSign Template Engine

## Purpose

The Template Engine manages configuration that describes how QSign recognizes a
document and prepares its signature placements. Templates contain no executable
code and no provider-specific object.

The engine owns validation, versioning, activation, and retrieval. Recognition
and placement receive immutable template snapshots and never query storage
during an analysis.

## Template aggregate

### Template

| Field | Purpose |
|---|---|
| Template ID | Stable identity independent from name and version |
| Code | Human-readable unique code used in diagnostics |
| Name and description | Information shown to administrators |
| Document type | Stable business classification emitted after recognition |
| Version | Immutable monotonically increasing template version |
| State | `DRAFT`, `PUBLISHED`, `RETIRED`, or `DISABLED` |
| Priority | Deterministic tie-breaker, never a substitute for evidence |
| Validity interval | Optional start and end dates for controlled activation |
| Matchers | Recognition rules and weights |
| Anchors | Textual references and occurrence-selection rules |
| Signature areas | Anchor-relative placement definitions |
| Workflow preparation | Neutral roles and ordered action identifiers |
| Settings | Template-level overrides within allowed global limits |
| Schema version | Version of the template data model |
| Checksum | Integrity and cache identity of the canonical definition |
| Audit metadata | Creator, creation time, publisher, and publication time |

A published version is immutable. Editing creates a new draft version.

### Matcher

| Field | Purpose |
|---|---|
| Matcher ID | Stable identifier used in evidence |
| Type | Literal text, term group, regular expression, page count, or metadata |
| Scope | Whole document, selected pages, first page, last page, or page range |
| Expression | Type-specific deterministic condition |
| Normalization profile | Named text-normalization configuration |
| Required | Failure rejects the candidate before scoring |
| Exclusion | Success rejects the candidate before scoring |
| Weight | Positive contribution to normalized candidate score |
| Minimum occurrences | Optional deterministic occurrence constraint |
| Maximum occurrences | Optional deterministic upper constraint |

Required and exclusion rules are gates. Weighted rules provide evidence only
after all gates pass.

### Anchor

| Field | Purpose |
|---|---|
| Anchor ID | Stable identity referenced by signature areas |
| Name | Administrative label |
| Search type | Literal text or explicitly approved regular expression |
| Expression | Text to locate, such as “In fede” |
| Scope | Allowed page or page range |
| Normalization profile | Deterministic comparison behavior |
| Context before/after | Optional neighboring text conditions |
| Occurrence policy | First, last, Nth, unique, or context-ranked occurrence |
| Required | Whether unresolved placement blocks readiness |
| Expected occurrences | Optional validation and disambiguation constraint |

An anchor identifies document content and geometry. It never stores a fixed
page rectangle as its primary location.

### Signature Area

| Field | Purpose |
|---|---|
| Area ID | Stable placement identity |
| Role | Neutral signer role, not a person or device |
| Anchor ID | Anchor from which placement is calculated |
| Placement side | Above, below, left, right, or aligned overlay if permitted |
| Horizontal alignment | Start, center, or end relative to the anchor rectangle |
| X and Y offsets | Distances from the selected anchor reference point |
| Width and height | Signature rectangle dimensions in canonical PDF points |
| Page-margin rules | Minimum allowed distance from page boundaries |
| Fallbacks | Optional ordered anchor-relative alternatives |
| Required | Whether invalid placement blocks workflow readiness |

Offsets are relative to resolved anchor geometry. Absolute document coordinates
are not part of the template model.

### Settings

Settings are layered in this order:

1. engine defaults;
2. environment policy;
3. template overrides explicitly allowed by policy.

The effective settings include normalization profile, recognition threshold,
ambiguity margin, maximum regex complexity, allowed page scopes, placement
margins, and diagnostic redaction policy.

A template cannot lower safety-critical limits below environment policy.

## Validation

Validation occurs before a template can be published or activated:

- required identifiers, names, and document type are present;
- matcher and placement types are supported;
- weights are positive and the total evidence weight is non-zero;
- recognition thresholds and ambiguity margins are in valid ranges;
- regular expressions compile and satisfy complexity policy;
- anchor references are valid and required anchors are reachable;
- signature dimensions and offsets are physically plausible;
- each workflow placement reference exists;
- schema version is supported;
- published identity/version pairs are unique;
- checksum matches the canonical definition.

Warnings may identify weak templates, but errors prevent publication.

## Lifecycle

1. Create a draft.
2. Validate structure.
3. Test against positive and negative sample documents.
4. Review recognition scores, ambiguity, anchors, and placements.
5. Publish an immutable version.
6. Activate the version for a controlled scope.
7. Retire or disable it without deleting its audit history.

Analysis records always reference the exact published version and checksum.

## Repository boundary

The conceptual `TemplateRepository` offers:

- list active published templates as one consistent snapshot;
- retrieve a template and its versions;
- save a draft;
- publish a validated draft transactionally;
- activate, disable, or retire a published version;
- export and import a portable template package;
- retrieve audit metadata.

The interface exposes template concepts, not tables, SQL, files, or database
connections.

## Local database evaluation

| Option | Strengths | Limits |
|---|---|---|
| Individual JSON files | Simple, readable, easy to diff and test | Weak transactional updates, indexing, audit, and concurrent Designer operations |
| SQLite | Offline, transactional, single-file deployment, indexed metadata, mature backup behavior | Requires schema migrations and recovery policy |
| Custom binary store | Compact | Unnecessary complexity, poor transparency, custom tooling |

### Decision

Use SQLite as the planned production repository for the future Template
Designer, behind `TemplateRepository`. SQLite is available offline, supports
atomic publication and version history, and does not require a server.

No database is created in this design milestone.

For portability and review, import/export uses a canonical versioned JSON
template package. SQLite remains an implementation detail rather than the
template interchange format.

## Conceptual persistence model

The future repository needs four logical groups:

- template identity and administrative metadata;
- immutable versioned definitions and checksums;
- activation state and validity scope;
- audit events for draft, validation, publication, activation, and retirement.

The detailed SQL schema, migration tool, backup format, locking policy, and
encryption-at-rest policy are deferred to the database implementation
milestone.

## Compatibility

- Every definition carries a schema version.
- Readers reject unsupported future schema versions.
- Migrations create a new canonical definition and never mutate historical
  published evidence.
- Export packages contain template version, schema version, checksum, and
  required engine compatibility range.
- Unknown fields are not silently interpreted.

