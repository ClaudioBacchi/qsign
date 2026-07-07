# QSign Anchor and Placement Engines

## Purpose

The Anchor Engine locates semantic textual references using deterministic text
and geometry rules. The Placement Engine derives signature rectangles from
those resolved anchors.

The two stages are separate because finding “In fede” is a text-resolution
problem, while placing a valid rectangle below it is a geometry problem.

## Canonical coordinate system

All intelligence geometry uses one QSign coordinate system:

- units are PDF points;
- origin is the top-left of the visible CropBox;
- X increases to the right;
- Y increases downward;
- page rotation is normalized before intelligence processing;
- page indices are zero-based internally;
- rectangles are represented by left, top, right, and bottom edges.

The future document-analysis provider converts library-specific coordinates to
this system. A future PDF-writing or PAdES provider converts canonical
placements to its own coordinate system. This prevents PyMuPDF, UI, and signing
conventions from leaking into placement rules.

## Anchor Engine

### Responsibility

- search configured anchor text;
- map normalized matches back to original text geometry;
- collect all valid occurrences;
- apply page, context, and occurrence rules;
- select one occurrence or report missing/ambiguous;
- provide a complete selection trace.

### Input

- `DocumentTextMap`;
- selected immutable template version;
- one anchor definition;
- anchor-resolution settings.

### Output

A resolved anchor contains:

- anchor and template identifiers;
- page index;
- matched original text or a redacted diagnostic form;
- union bounding rectangle;
- contributing text-token identifiers;
- exactness, context, and scope evidence;
- occurrence index and total candidate count.

Failure results distinguish `NOT_FOUND`, `CONTEXT_MISMATCH`,
`OCCURRENCE_MISMATCH`, and `AMBIGUOUS`.

### Dependencies

Text normalization, normalized-to-original token mapping, and canonical
geometry. The Anchor Engine has no dependency on rendering images, Flet, Wacom,
PAdES, workflow execution, certificates, or transport.

## Anchor resolution flow

1. Select pages allowed by the anchor scope.
2. Search normalized text using literal or approved regex matching.
3. Map every match to original geometric tokens.
4. Merge token rectangles into one anchor rectangle.
5. Apply required context-before and context-after rules.
6. Check expected occurrence constraints.
7. Apply the configured occurrence policy.
8. Return one occurrence or an explicit failure.

Reading order comes from `DocumentTextMap`. Cross-line phrase matching is
allowed when token order is continuous after whitespace normalization.
Cross-page phrase matching is not allowed.

## Occurrence policies

| Policy | Behavior |
|---|---|
| Unique | Exactly one valid occurrence is required |
| First | First occurrence in page and reading order |
| Last | Last occurrence in page and reading order |
| Nth | One-based configured occurrence after filtering |
| Context-ranked | Highest deterministic context score; ties are ambiguous |

“First” and “last” are safe only when the template author has validated the
document family. The Template Designer should prefer `Unique` plus context.

## Context disambiguation

Context uses explicit rules such as:

- phrase occurring within N text tokens before or after;
- phrase occurring on the same line or text block;
- anchor restricted to the last page;
- anchor following another named anchor in reading order.

Context scores use declared weights only when `Context-ranked` is selected.
Equal top scores remain ambiguous.

## Placement Engine

### Responsibility

- select an anchor reference point;
- apply alignment, side, offsets, width, and height;
- validate page containment and margins;
- try only explicitly ordered fallback rules;
- return canonical rectangles and evidence.

### Input

- resolved anchor;
- signature-area definition;
- canonical page geometry;
- placement settings and protected zones if configured.

### Output

A signature placement contains:

- area and signer-role identifiers;
- anchor-resolution reference;
- page index;
- canonical signature rectangle;
- placement strategy and fallback index;
- validation evidence.

Failures distinguish `OUT_OF_BOUNDS`, `MARGIN_VIOLATION`, `INVALID_SIZE`,
`PROTECTED_ZONE_COLLISION`, and `NO_VALID_FALLBACK`.

### Dependencies

Canonical geometry primitives only.

## Placement calculation

Each rule declares:

- side relative to the anchor: above, below, left, or right;
- horizontal or vertical alignment: start, center, or end;
- X and Y offsets from the aligned anchor reference point;
- signature width and height in PDF points;
- minimum page margins;
- optional ordered fallback rules.

For example, “below, start aligned, X offset 0, Y offset 8” places the
signature rectangle eight points below the anchor and aligns its left edge with
the anchor's left edge. The stored values are relative; the final absolute page
rectangle exists only in the analysis output.

## Validation rules

1. Width and height must be positive and within environment limits.
2. The anchor rectangle must belong to the target page.
3. The calculated rectangle must remain inside the visible CropBox.
4. Configured minimum margins must be respected.
5. The rectangle must not overlap an explicitly protected zone.
6. All numeric values must be finite.

The engine does not silently clamp, resize, move to another page, or choose a
different anchor. If the primary placement fails, only template-defined
fallbacks may be evaluated in their declared order.

## Multiple signature areas

A template may declare several areas for distinct neutral roles. Each area is
calculated independently but the final result is accepted only when:

- every required area is valid;
- required areas do not overlap unless explicitly allowed;
- every workflow action references an existing placement;
- their deterministic order is defined by the template.

Optional areas may fail without blocking readiness only when the template says
so, and their failure remains in diagnostics.

## Independence from later milestones

- Wacom produces signature capture data; it does not calculate placement.
- PAdES consumes a validated placement; it does not recognize anchors.
- UI displays or confirms a placement; it does not own placement rules.
- Transport moves documents; it does not inspect template geometry.
- Workflow consumes neutral roles and placements; it does not alter them.

This boundary allows placement to be tested in console tools and automated
tests without a signature device or desktop window.

## Future validation fixtures

Placement testing should cover:

- anchors split across text spans or lines;
- repeated anchors with context disambiguation;
- different page sizes and CropBoxes;
- rotated pages normalized by the adapter;
- anchors near every page edge;
- primary placement failure with valid and invalid fallbacks;
- multiple signature areas and collision rules;
- exact expected rectangles as golden values.

