# QSign Recognition Engine

## Purpose

The Recognition Engine identifies one document template using deterministic
rules and a transparent weighted score. It does not infer meaning and does not
select a “closest” template when evidence is insufficient.

## Inputs

- provider-neutral `DocumentTextMap`;
- immutable active template snapshot;
- recognition settings snapshot;
- precomputed normalized document views.

## Outputs

The result is one of:

- `MATCHED`: exactly one candidate satisfies threshold and ambiguity policy;
- `UNKNOWN`: no candidate satisfies mandatory rules and threshold;
- `AMBIGUOUS`: at least two candidates are too close to select safely.

The output includes every candidate considered, gate outcomes, matcher scores,
total score, rank, rejection reasons, and the exact settings used.

## Text normalization

Normalization is explicit and profile-driven. A default profile may:

1. apply Unicode normalization;
2. normalize line endings and repeated whitespace;
3. compare using Unicode-aware case folding;
4. optionally normalize punctuation variants;
5. preserve a mapping from normalized characters to original text tokens and
   geometry.

Accent removal, punctuation removal, and numeric normalization are disabled
unless explicitly configured. They can create false positives and therefore
must never happen implicitly.

The original text and geometry remain untouched.

## Supported deterministic matcher families

### Literal text

Checks for one exact normalized phrase, with optional whole-word boundaries,
page scope, and occurrence limits.

### Term group

Supports explicit logic:

- all terms must be present;
- at least N of M terms must be present;
- any term may be present.

Each group is one matcher with one weight, preventing accidental score
inflation from repeated synonyms.

### Regular expression

Evaluates an approved expression against a bounded text scope. Expressions are
compiled when templates load, not per document. Template validation enforces
length and complexity limits to prevent pathological execution time.

### Page count

Checks exact count or an inclusive range. It is useful as supporting evidence,
but normally should not identify a document alone.

### Metadata

Checks stable metadata fields when available. Metadata is supporting evidence
because producers often omit or rewrite it.

### Structural text rule

Checks deterministic relationships such as two phrases occurring on the same
page or in a defined order. The first implementation should add this only when
literal and grouped rules cannot express a real requirement.

Layout fingerprints, pixel comparisons, edit distance, fuzzy matching, and
statistical classifiers are excluded from the initial design.

## Score model

Recognition has two phases.

### Phase 1: gates

- every required matcher must succeed;
- every exclusion matcher must fail;
- unsupported or invalid rules reject the template during loading, before
  document analysis.

A candidate that fails a gate is rejected and receives no weighted rank.

### Phase 2: weighted evidence

Each evidence matcher produces a deterministic value between zero and one.
Binary rules produce zero or one. Explicit N-of-M groups may produce the
configured matched fraction.

The candidate percentage is the sum of each value multiplied by its weight,
divided by the sum of all applicable evidence weights, multiplied by 100.

Repeated text does not increase a score unless occurrence count is explicitly
part of that matcher. Missing metadata removes only a matcher marked
“not applicable”; otherwise it is a normal failed rule. This behavior is fixed
in the template and visible in evidence.

## Selection policy

1. Reject candidates that fail gates.
2. Calculate scores for remaining candidates.
3. Sort by score descending.
4. Reject the result as `UNKNOWN` when the highest score is below its template
   threshold or the environment minimum.
5. Compare the highest score with the runner-up.
6. Return `AMBIGUOUS` when their difference is smaller than the configured
   ambiguity margin.
7. Return `MATCHED` otherwise.

Priority is used only as a deterministic ordering key for diagnostics or an
explicitly approved exact tie policy. The safe default for an exact tie is
`AMBIGUOUS`, not priority-based automatic recognition.

Template code and version provide the final stable sort order so diagnostic
output is reproducible.

## Example evaluation

A “Privacy consent” template could require the literal phrase “Informativa sul
trattamento dei dati”, exclude “revoca del consenso”, and assign weighted
evidence to a controller name, a legal-reference regex, and a grouped set of
consent phrases.

The evidence trace states which phrases matched and their page numbers. It does
not expose full clinical document text in general logs.

## Ambiguity handling

Ambiguity is a valid business result. It must:

- prevent anchor resolution and automatic placement;
- identify the top candidates and score difference;
- expose which rules failed to distinguish them;
- allow the future Template Designer to test and refine templates;
- never be resolved by filename, template creation order, or hidden heuristics.

## Failure and edge cases

| Condition | Result |
|---|---|
| No text layer | `NO_TEXT_LAYER` before recognition |
| Empty active template set | `UNKNOWN` with configuration diagnostic |
| All candidates fail required rules | `UNKNOWN` |
| Highest score below threshold | `UNKNOWN` |
| Top candidates inside ambiguity margin | `AMBIGUOUS` |
| Invalid template rule | Template excluded at snapshot validation |
| Regex execution limit exceeded | Candidate rejected with explicit diagnostic |

## Performance

- Normalize each page once.
- Precompile regular expressions per template snapshot.
- Evaluate cheap required literals before expensive expressions.
- Stop evaluating a candidate after a failed mandatory gate.
- Index normalized literals when the active template count justifies it.
- Cache only by document fingerprint, template-snapshot checksum, and settings
  checksum.

Performance optimization must not short-circuit evidence needed for the final
audit trace.

## Test strategy for the future implementation

Every published template should have:

- positive fixtures that must match;
- near-negative fixtures from similar document families;
- documents that must remain unknown;
- deliberate ambiguity fixtures;
- whitespace, case, punctuation, and page-scope variants;
- score and evidence golden results;
- performance limits on representative document sets.

