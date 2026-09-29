# Federated Retrieval Roadmap

Federated retrieval means searching multiple independent vector, lexical or
structured spaces and merging their candidates. It is optional retrieval
capability, not a reason to make an external service the source of truth.

## Space manifest

Every searchable space must publish a versioned manifest containing:

```text
space_id
scope_ids (the exact scopes admitted by the space; one scope is represented as
           a one-element set, and empty is valid only for an explicitly
           scope-neutral space)
projection_kind / embedding_purpose
embedding_model_id + immutable revision/digest
query/document role and input-template policy
input-template/preprocessing/tokenizer digest
dimension + metric + normalization
codec and index kind
index_generation
canonical/corpus frontier
index_configuration_digest
manifest_digest
```

Candidate results retain the manifest identity, exact source revision,
canonical `KnowledgeUnitRef` (or profile-equivalent logical identity), local
score, local rank and source provenance. A manifest is derived metadata; it
does not transfer canonical ownership from the configured storage backend.
For a federated request, the executor binds each space to the intersection of
the manifest's declared scope set and the `RetrievalPlan.scope_ids`; a legacy
single `scope_id` normalizes to a one-element set and never authorizes every
scope.

## Compatibility and fusion

1. **Same underlying space:** identical model revision, role/template,
   preprocessing, dimension, normalization and metric. A common exact reranker
   may compare candidates after the declared precision and tie policy.
2. **Heterogeneous generators, common representation:** codecs or indexes may
   differ, but candidates can be decoded into one canonical representation and
   reranked there. Decoded vectors are not automatically score-compatible just
   because their dimensions match; the representation and metric contract must
   match.
3. **Incompatible spaces:** different models, dimensions, purposes or
   semantics. Do not perform a shared vector rerank. Use an explicitly
   calibrated score fusion or rank-only/RRF policy, with its own evidence.

The safe exact path is:

```text
heterogeneous candidate generators
  -> union candidates
  -> canonical/exact representation
  -> one common reranker
```

Embedding translation is a separate research adapter. It needs a versioned
translation artifact and held-out quality evidence before changing a space's
compatibility class.

## Bounded fan-out, security and completion

The planned federated executor should select spaces by scope and capability,
then lower each route from the same logical `RetrievalPlan` with a bounded
fan-out budget. External routes receive only the existing
`ExternalCandidateConstraint` (eligible canonical units, pinned frontier and
policy fingerprint), never raw `RetrievalAccessContext`, grants, roles,
jurisdictions or policy claims. A backend unable to apply that exact
pre-ranking constraint is unavailable for a policy-aware route; canonical
hydration remains mandatory. `ExternalCandidateConstraint` may cross only a
same-trust-domain adapter boundary: an untrusted or cross-trust external
backend is unavailable for policy-aware federation. A future opaque
partition-handle protocol is deferred and requires a separate security design.

Do not collapse independent result axes:

```text
topology:   Local | Federated
completion: existing RetrievalCompletion (and per-space route completion)
fusion:     ExactCommonRerank | CalibratedScore | RRF
```

The result records each queried/skipped space and its route completion. A
timeout or unavailable provider is a provider/route execution status, not a
semantic `Unknown` result. Partial and required-route failures use the existing
`RetrievalCompletion` contract rather than a new status vocabulary.

Backpressure pauses producers. If a deadline or budget drops a route, the
declared `BudgetExhaustionAction` and completion value are recorded; fan-out is
never silently changed to trade away recall.

## Merge versus physical unification

If spaces share one model and storage format, physically rebuilding one index
may be cheaper and more reproducible than permanent fan-out. Keep federation
when isolation, ownership, update cadence or data residency requires it. Never
merge indexes from different models by concatenating bytes or pretending that
their cosine scores are comparable.

## Acceptance protocol

The minimum fixture has two compatible spaces, one incompatible space,
duplicate logical units, one stale generation and one unavailable source. It
must verify local quality, merged `nDCG@K`, candidate coverage, canonical
deduplication, stale filtering, fan-out count, timeout behavior, route
completion and rank-only fallback. Report quality separately for each local
source and the merged result; a merged score must not hide a failed source.

## Milestones

| Milestone | Dependency | Minimum evidence | Acceptance | Risk |
|---|---|---|---|---|
| F1 manifests and candidate envelope | artifact provenance + index generations | schema and compatibility tests | incompatible spaces are rejected from shared rerank | metadata drift |
| F2 bounded fan-out | `RetrievalPlan` and execution lowering | fake multi-space replay | deadlines, partial results and provenance are deterministic | tail amplification |
| F3 fusion calibration | qrels and matched query folds | RRF and calibrated-score comparison | no global claim without per-space metrics | overfitting |
| F4 physical merge decision | F1/F2 benchmark | rebuild-versus-fan-out report | choose only on matched quality, cost and lifecycle evidence | premature consolidation |

External vector stores, if used, are derived-index adapters and comparison
targets. The first-party embedded profile keeps canonical memory records,
revisions, tombstones and provenance in MDBX; a host may substitute another
canonical backend only through the full canonical-storage conformance contract
defined in `architecture.md`, not through a federation adapter alone.
