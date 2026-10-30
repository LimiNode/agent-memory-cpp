# Lexical Retrieval Qualification R1

Status: design and minimal reproducible harness only. This note is not a
production activation decision and contains no benchmark claim.

## Context and question

Phase A integrated the existing BM25, flat-v2/seg-v2 qualification, learned
sparse weighted-postings smoke, and BGE-M3 pilot evidence. R1 defines one
comparable evaluation contract for candidate retrieval without changing the
BM25 default, MDBX layout, publication coordinator, or retrieval APIs.

The question is: can candidate quality, canonical hydration, provenance
eligibility, and lifecycle cost be measured separately on the same frozen
corpus and query split? Ranking algorithms and physical index backends are
kept distinct: flat-v2 and segmented-v2 use the same Okapi BM25 scorer in this
protocol and are not counted as independent ranking algorithms.

## Frozen inputs

The committed fixture and manifest are:

* `tools/lexical-research/fixtures/lexical_retrieval_qualification_r1.json`
* `tools/lexical-research/fixtures/lexical_retrieval_qualification_r1_manifest.json`

The fixture is a small, repository-owned UTF-8 corpus containing C++ symbols
and API names, English and Russian text, morphology variants, rare facts,
corrections, contradictory revisions, hard negatives, and no-answer queries.
It declares train/dev/test query membership, corpus/qrels/query/tokenizer
revisions, a source frontier, and a committed-fixture-only access policy.
Synthetic or mini-corpus results must not be described as representative
quality evidence.

Run the fail-closed protocol and BM25 smoke with:

```powershell
py -3 tools/lexical-research/validate_lexical_retrieval_qualification.py --self-test
py -3 tools/lexical-research/validate_lexical_retrieval_qualification.py --run-bm25
```

The validator reuses `run_lexical_protocol.py`; it does not introduce a second
benchmark framework. Its mutation controls reject a split scorer identity,
duplicate qrels, and removal of the no-answer control. Incomplete and
unjudged qrels remain visible in the report but cannot silently enter positive
quality aggregates.

## Baseline matrix

| Backend / algorithm | Status | Index/build and lifecycle evidence | Qualification limitation |
| --- | --- | --- | --- |
| BM25 | production baseline | Existing C++ lexical index; measure build, update, reopen, and hydration with the established lifecycle tests | Current default; no implementation change in R1 |
| flat-v2 | experimental index backend | Reuse segmented qualification harness and the same corpus/query policy | Same BM25 scoring as segmented-v2; not a new ranker |
| segmented-v2 | research-only index backend | Reuse segmented qualification harness; report update/reopen/decode/storage costs | `PERFORMANCE_INCONCLUSIVE`; activation remains blocked |
| learned-sparse weighted postings | research-only ranker smoke | Reuse the frozen artifact and lifecycle checks from #560 | `DIRECTIONAL_SMOKE_ONLY`; not trained-model evidence |
| BGE-M3 sparse pilot | research-only ranker pilot | Reuse the artifact inventory and pilot receipt from #562 | `INSUFFICIENT_EVIDENCE` without independent qrels and a larger corpus |

R1 does not implement adapters for the rows that do not already have an
evaluation tool. Their receipts must use the metric and eligibility vocabulary
below before they can be compared.

## Measurement contract

Candidate generation is reported separately from canonical hydration. For each
backend and query, a run records ranked candidate IDs, scores, source revision,
whether the canonical record hydrated, and whether the candidate was eligible
at the frozen source frontier. A stale revision is an ineligible candidate, not
a relevant hit. Duplicate candidates are invalid input.

Quality gates are:

* candidate Recall@K, nDCG@K, and MRR over complete positive judgments;
* hydrated Recall@K after eligibility and canonical hydration;
* provenance correctness (source revision and chunk/resource identity);
* no-answer accuracy, including false-positive retrieval;
* qrels coverage with `judged`, `judged_zero_relevant`, `no_answer`,
  `unjudged`, and `incomplete_qrels` kept as separate states.

Cost gates are:

* p50/p95/p99 query latency;
* index build, update, reopen, and decode costs;
* index bytes and decoded bytes.

Timing must distinguish data generation, index/build materialization, query
encoding or preprocessing, candidate search, exact rerank, and hydration. The
observation unit is one query repeat. `sample_count` is the number of observed
query-repeat samples, not the number of independent queries. A `measured`
sample distribution does not imply a statistically stable p99; the current
Python smoke is explicitly directional and is not directly comparable to C++
production latency.

## Reproducibility and split policy

Every future receipt must include the manifest digest, source frontier, backend
revision, tokenizer revision, scorer revision, query split, qrels revision,
candidate depth, eligibility policy, and environment. Build/update/reopen
measurements must not be folded into query latency. No tuning against the test
split is allowed; a future independent qrels release is required before any
quality claim beyond this fixture smoke.

## Interpretation and next checks

This PR establishes the contract and proves that the frozen fixture and BM25
control are reproducible. It does not rank the five baselines, prove a quality
win, or authorize production activation. The next experiment may add at most
two targeted measurements after a separate review: (1) C++ BM25 versus
flat-v2/seg-v2 on the same fixture with hydration and lifecycle costs, and (2)
an independently judged, larger multilingual qrels set for the sparse pilots.
Optimization should follow measured loss (tokenization, candidate
enumeration, layout, hydration, or eligibility), not precede it.
