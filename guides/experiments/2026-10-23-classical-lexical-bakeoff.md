# Classical lexical scoring bake-off (PR4)

Date: 2026-10-23
Scope: `research/lexical-classical-bakeoff` on the post-merge `main` (`3be74a26`)

## Question

On a frozen, small mixed RU/EN/code fixture, do BM25+ or BM25L change ranking
behaviour relative to the existing BM25 control without changing production
lexical defaults or storage contracts?

## Setup

Run the dependency-free harness and self-test:

```text
py -3 tools/lexical-research/run_classical_bakeoff.py \
  tools/lexical-research/fixtures/classical_bakeoff_v1.json --self-test
py -3 tools/lexical-research/run_classical_bakeoff.py \
  tools/lexical-research/fixtures/classical_bakeoff_v1.json --repeats 5 \
  --output classical-bakeoff.receipt.json
```

The fixture freezes RU/EN text, C++ identifiers, headings, short and long
chunks, a negative query, and explicit `unjudged`/`incomplete_qrels` cases.
Resource and chunk judgments are aggregated independently. Ties use ascending
`doc_id`.

Formulas are explicit in the receipt: BM25 uses `k1=1.5`, `b=0.75`, and
`log1p_okapi` IDF; BM25+ adds `delta=1` after length normalization; BM25L uses
the normalized term frequency and `delta=1` in its lower-bounded term factor.
The golden vectors are independently specified rather than copied from a run.

## Storage compatibility audit

| Area | Status | Boundary |
| --- | --- | --- |
| Offline corpus text and in-memory scoring | implemented | research harness only |
| Resource/chunk relevance slices | implemented | qrels metadata, no DB writes |
| Projection-aware field scoring | contract-only | requires production `SearchProjection` |
| BM25F | contract-only/deferred | no honest field projection path in this PR |
| MDBX postings, DBI/schema, canonical bytes, `ChunkPayload`, `SegmentSet`, compression | missing from this PR | intentionally unchanged |

## Interpretation and limitations

This PR establishes a reproducible control comparison, not a quality claim
about a production corpus. A small frozen sample cannot support statistical
significance or superiority claims. `sample_count` is the number of repeated
observations of the fixture queries; it is not the number of independent
queries. `descriptive` timing reports the observed sample distribution only and
does not imply stable p99. Python tokenization is a diagnostic heuristic and
does not provide exact C++ byte spans. Losing algorithms remain in the receipt.

## Follow-up

Use the same fixture and qrel schema when a real field projection contract is
available; only then add BM25F. Larger corpora and independent repeated query
sets are required before making latency or quality decisions.
