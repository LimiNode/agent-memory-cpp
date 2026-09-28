# Federated Retrieval Roadmap

Federated retrieval means searching multiple independent vector or lexical
spaces and merging their candidates. It is an optional retrieval capability,
not a reason to make an external service the source of truth.

## Space manifest

Every searchable space must publish a manifest containing:

```text
space_id
scope_id
embedding_model_id + immutable revision/digest
dimension
metric and normalization
preprocessing/tokenizer hash
codec and index kind
index_generation
source_revision watermark
```

The manifest is versioned derived metadata. Candidate results must include the
same fields plus `record_id`, record revision, local score, local rank and
source provenance.

## Compatibility classes

1. **Same space:** identical model revision, dimension, preprocessing,
   normalization and metric. Scores may be compared after the declared tie and
   precision policy.
2. **Calibratable spaces:** different index/codec representations with a
   common decoded representation. Decode and rerank in that representation;
   never compare raw compressed scores.
3. **Incompatible spaces:** different models, dimensions or semantics. Do not
   perform a shared vector rerank. Use local ranking plus RRF or a separately
   calibrated rank/score fusion policy.

Embedding translation is a separate research adapter. It must produce a
versioned translation artifact and its own quality evidence before it can move
an incompatible space into class 1 or 2.

## Fan-out and merge

The planned federated executor should:

- select spaces by scope, capability and a bounded routing budget;
- execute local plans with independent deadlines and cancellation;
- preserve local top-N even when one space is unavailable;
- deduplicate by canonical `(record_id, revision)`;
- use calibrated score fusion only for compatible spaces;
- use RRF/rank-only fallback for incompatible spaces;
- return per-candidate provenance and an explicit partial/timeout status.

Federation is not global exact search. A result must state whether it is local,
merged, partial, or rank-only, and which spaces were queried or skipped.

## Merge versus physical unification

If spaces share one model and storage format, physically rebuilding one index
may be cheaper and more reproducible than permanent fan-out. Keep federation
when isolation, ownership, update cadence or data residency requires it. Never
merge indexes from different embedding models by concatenating bytes or
pretending that their cosine scores are comparable.

## Acceptance protocol

The minimum federated fixture has two compatible spaces, one incompatible
space, duplicate records, one stale generation and one unavailable source. It
must verify local quality, merged `nDCG@K`, candidate coverage, duplicate
resolution, stale filtering, fan-out count, timeout behavior and rank-only
fallback. Report quality separately for each local source and for the merged
result; a merged score must not hide a failed source.

## Milestones

| Milestone | Dependency | Minimum evidence | Acceptance | Risk |
|---|---|---|---|---|
| F1 manifests and candidate envelope | artifact provenance + index generations | schema and compatibility tests | incompatible spaces are rejected from shared rerank | metadata drift |
| F2 bounded fan-out | retrieval execution plan | fake multi-space replay | deadlines, partial results and provenance are deterministic | tail amplification |
| F3 fusion calibration | qrels and matched query folds | RRF and calibrated-score comparison | no global claim without per-space metrics | overfitting |
| F4 physical merge decision | F1/F2 benchmark | rebuild-versus-fan-out report | choose only on matched quality, cost and lifecycle evidence | premature consolidation |

External vector stores, if used, are adapters and comparison targets. MDBX
remains the canonical owner of memory records, revisions, tombstones and
provenance.
