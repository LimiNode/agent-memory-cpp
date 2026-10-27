# Learned-sparse weighted-postings smoke experiment

**Date:** 2026-10-26
**Status:** `DIRECTIONAL_SMOKE_ONLY` — no model promotion and no production recommendation
**Base:** `main@1e2806d21bc36fa6baeca536aaccec0bf535502c`
**Scope:** read-only research runner, frozen fixture, and CI self-test; no C++ API, MDBX schema, or required ML dependency

## Question and boundary

Can a small, frozen weighted-postings artifact be validated and searched with
the same deterministic lifecycle controls that a later learned-sparse artifact
would need? This experiment tests the artifact contract and measurement
plumbing. It does **not** test whether a learned model improves retrieval.

The fixture is deliberately a toy hand-authored artifact (`toy-document-impact-v1`),
not a trained model, checkpoint, or claim about BGE-M3, DeepImpact, uniCOIL, or
SPLADE. The production C++17 build remains free of ONNX, PyTorch,
Transformers, tokenizer, and other ML dependencies.

## Reproduction

From the repository root, with Python 3.11+:

```text
python tools/learned-sparse/run_weighted_postings_experiment.py \
  --manifest tools/learned-sparse/fixtures/learned_sparse_smoke_v1.json \
  --self-test

python tools/learned-sparse/run_weighted_postings_experiment.py \
  --manifest tools/learned-sparse/fixtures/learned_sparse_smoke_v1.json \
  --repeats 5 \
  --output build/learned-sparse-receipt.json
```

The first command is the CI gate. It validates the frozen manifest, checks the
positive ranking cases, and requires every intentional mutation to fail closed.
The second command writes a local receipt; generated `build/` output is not
committed.

## Frozen inputs

| Field | Value |
| --- | --- |
| Fixture | `tools/learned-sparse/fixtures/learned_sparse_smoke_v1.json` |
| Fixture SHA-256 | `53d2aa1a9535044db9d9c9e3c914c8053c3a96b039e4825af4ceec95b8a79ddc` |
| Source revision | `corpus-revision-2026-10-26` |
| Tokenizer | `unicode_word_v1`, revision `unicode-word-v1` |
| Artifact | `toy-document-impact-v1`, revision `fixture-1`, mode `document_only_impact` |
| Model license | `fixture-only`; no checkpoint is distributed or implied |
| Corpus / queries | 3 active documents / 3 queries (2 judged, 1 no-answer) |
| Weighted postings | 11 |

The fixture preserves model/tokenizer/source revisions, vocabulary token IDs,
active document identity, non-negative finite weights, and deterministic
postings ordering. It intentionally contains no expansion terms and is not a
proxy for a trained sparse model. The smoke self-test independently pins the
fixture digest in the runner; the digest is an integrity check, not proof of
model provenance or licensing.

## Result

The run completed locally on the stated fixture:

| Check | Result |
| --- | --- |
| Positive weighted ranking | `PASS` (`q_code → d1`, `q_ru → d3`, `q_none → []`) |
| Negative controls | `11` rejected |
| Weighted Recall@1 / Recall@5 / Recall@10 | `1.0 / 1.0 / 1.0` |
| Weighted nDCG@10 / no-answer accuracy | `1.0 / 1.0` |
| BM25 Recall@1 / Recall@5 / Recall@10 | `1.0 / 1.0 / 1.0` |
| BM25 nDCG@10 / no-answer accuracy | `1.0 / 1.0` |
| Manifest SHA-256 | `53d2aa1a9535044db9d9c9e3c914c8053c3a96b039e4825af4ceec95b8a79ddc` |

The eleven controls cover negative weights, duplicate postings, stale source
revision, incompatible tokenizer revision, wrong vocabulary token ID, postings
for an inactive/deleted document, duplicate qrels, cross-document posting
injection, a qrel on a no-answer query, a non-finite weight, and an unexpected
positive result for the no-answer query. A non-zero validator/oracle failure is
required for each mutation while the positive fixture must continue to pass.

## Timing semantics

The runner builds the weighted inverted index once before the measured loop.
Each query repeat reports separate posting traversal/scoring, ranking and
materialization, and total-query samples. BM25 and weighted quality use the
same corpus, qrels, limit and deterministic tie rule. With three queries and
five repeats there are **15 query-repeat observations**, not 15 independent
queries. The local total-query median was approximately `0.01 ms` on this
machine. It is labelled `directional`; the sample is too small to support a
stable p95/p99 or a statistically general latency claim. The runner records
`sample_count_semantics=query_repeat_observations_not_independent_queries` and
does not call this result a production benchmark.

## Interpretation and limitations

The result demonstrates that a compact derived artifact can be checked for
identity and stale-data hazards before search, and that the runner has a
repeatable quality/timing receipt. It does not demonstrate semantic lift,
multilingual model quality, update cost, memory footprint, checkpoint
licensing, or production readiness. The fixture is too small and too easy for
those conclusions; the perfect scores are expected smoke-test values.

The next learned-sparse experiment must use a frozen real corpus and qrels,
pin the exact model/checkpoint/tokenizer revisions and licenses, separate
document encoding from query encoding and posting traversal, and report update
and storage costs. A failed gate keeps BM25 as the default and yields `NO-GO`
for an adapter.
