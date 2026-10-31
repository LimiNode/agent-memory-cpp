# Lexical Retrieval R3A: Data Acquisition and Judgment Readiness

Status: Draft acquisition result. This note does not report retrieval quality
and does not contain qrels.

## Concrete source acquired

R3A uses the public `LimiNode/agent-memory-cpp` repository itself as a bounded,
permitted technical corpus. The source is MIT licensed and is pinned to
`c71e275fd876f64f6dfb50dd76ed284b42fce9b1` (the merged R3 plan revision).
The deterministic preparation tool is
`tools/lexical-research/prepare_r3a_corpus.py`.

The selected roots are `guides/`, `src/`, `tests/`, and `tools/`. Selection is
based only on path, UTF-8 readability, non-empty content, and a one MiB file
size limit. It does not inspect queries or qrels. Generated build trees,
external dependencies, and files with unsupported extensions are excluded.
Line endings are normalized before hashing; document IDs are stable
`r3a:<relative-path>` values.

The acquisition run at the pinned source produced:

| Field | Value |
| --- | --- |
| selected documents | 1,889 |
| excluded files | 21 |
| corpus digest | `473398540b16e43b2fdb2a502b8b2e459e9cc5641f2393e080a825e39f9c4dfe` |
| digest definition | SHA-256 over ordered document ID, document digest, and byte count |
| qrels | none; `JUDGMENT_READINESS_ONLY` |

The compact manifest is committed at
`tools/lexical-research/fixtures/lexical_retrieval_r3a_manifest.json`.
The generated JSONL is intentionally not committed; it is reproducible with:

```powershell
py -3 tools/lexical-research/prepare_r3a_corpus.py `
  --root . `
  --source-commit c71e275fd876f64f6dfb50dd76ed284b42fce9b1 `
  --output .artifacts/lexical_retrieval_r3a_manifest.json
```

The command writes the manifest and a sibling JSONL corpus. A self-test covers
UTF-8 normalization, deterministic count, and the no-synthetic-qrels rule.

## What this source does and does not represent

The corpus contains real project C++, tests, retrieval/storage tools, and
English and Russian technical material where present in the repository. It is
a concrete technical evaluation source, not evidence that the repository is a
statistically representative sample of all agent-memory workloads. The
scenario quotas below are therefore acceptance criteria for query authoring,
not claims about the corpus before inspection.

If a quota cannot be filled without inventing or duplicating scenarios, R3A
must report the shortfall and remain readiness-only. A larger external corpus
may be added later only with its own license and provenance manifest.

## Bounded query and judgment pool

The initial pilot budget is deliberately bounded:

* 60 stable queries;
* 20 pooled documents per query;
* 1,200 query-document pairs;
* two independent judgments per pair (2,400 ratings maximum);
* adjudication for disagreements, capped at 600 additional ratings;
* a 3,000-rating hard ceiling for the pilot.

The working query quota is:

| Scenario | Queries |
| --- | ---: |
| exact C++ symbol/API lookup | 12 |
| Russian morphology | 12 |
| mixed RU/EN terminology | 12 |
| rare fact or identifier lookup | 8 |
| corrected versus stale statement | 8 |
| contradiction or hard negative | 4 |
| lexical no-answer control | 4 |
| **Total** | **60** |

These are quotas, not labels. The authoring report must identify the source
document(s) supporting each query and record any unfilled quota.

The pool is assembled before judging from a frozen, documented rule: a fixed
candidate slice, deterministic same-language samples, and declared hard
negatives. It must not use judgments to select documents. Unpooled documents
remain unjudged; they are never silently treated as grade zero. If an assessor
proposes an omitted relevant document, it is added through a recorded pool
revision and the affected query is re-adjudicated.

The relevance rubric is graded and frozen before annotation:

* `0`: not relevant, misleading, or contradicted for the query;
* `1`: related context but not answer-bearing;
* `2`: useful answer-bearing evidence;
* `3`: direct and authoritative answer-bearing evidence.

Judges receive opaque query/document IDs and the rubric, not backend names or
scores. Query authoring, judging, adjudication, and evaluation are separate
roles. Duplicate/conflicting judgments, unknown IDs, malformed grades, and
incomplete adjudication are validation failures. `unjudged`, `incomplete_qrels`,
and explicit `judged_zero_relevant` remain separate states.

## Cost and stop rules

At a planning rate of 30 seconds per rating, the 2,400 primary ratings are
approximately 20 reviewer-hours; the 600-rating adjudication ceiling adds up
to five hours. Query authoring and pool preparation are budgeted separately.
These are planning estimates, not measured performance claims.

Stop acquisition and report `JUDGMENT_READINESS_ONLY` if any of the following
occurs:

* license or source revision cannot be verified;
* the reproducible digest changes unexpectedly;
* fewer than 60 valid queries can be authored without leakage;
* a scenario quota cannot be filled from real source material;
* the 3,000-rating ceiling or two-judge requirement cannot be met.

No quality aggregate, backend ranking, or production recommendation may be
published while the readiness-only status remains.

## Next deliverable

The next Draft PR should add only the frozen query manifest, pool-construction
record, and independent qrels or a dated external blocker. It must reuse the
R1/R2 receipt and validator seams and must not introduce a new retrieval
framework, backend, production default, or MDBX schema change.
