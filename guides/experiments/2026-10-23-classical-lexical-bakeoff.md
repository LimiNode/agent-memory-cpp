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
chunks, a negative query, a zero-relevant query, and explicit
`unjudged`/`incomplete_qrels` cases. Resource and chunk arms build separate
indexes with separate `N`, `DF`, `avgdl`, and top-k; a mixed-unit pool remains
only as a labelled diagnostic arm. Qrel levels are checked against the
document unit. Ties use ascending `doc_id`.

Formulas are explicit in the receipt: BM25 uses `k1=1.5`, `b=0.75`, and
`log1p_okapi` IDF; BM25+ adds `delta=1` after length normalization; BM25L uses
the normalized term frequency and `delta=1` in its lower-bounded term factor.
The golden vectors are independently specified rather than copied from a run.
They include a length-normalization competition in which BM25/BM25+ rank
`a > b`, while BM25L ranks `b > a`; this proves the fixture can detect a
scorer-ranking difference rather than merely execute three formulas.

## Architecture boundary

The bake-off follows the repository rule:

`Canonical Storage ↔ SearchProjection ↔ IndexStore ↔ Retrieval`

Canonical content, revisions, blocks, and source identity are authoritative
knowledge data. Search projections and lexical statistics/postings are
versioned derived structures. BM25, BM25+, BM25L, BM25F, WAND, ANN, and future
sparse algorithms may share compatible TF/DF/position inputs, but none may
rewrite canonical bytes or make a scoring formula part of the canonical model.
An index publication must identify the projection/tokenizer/statistics epoch;
readers either use an exact published snapshot or return an explicit partial /
unavailable outcome while a derived index catches up.

## Storage compatibility audit

| Area | Status | Boundary |
| --- | --- | --- |
| Offline corpus text and in-memory scoring | implemented | research harness only |
| Resource/chunk relevance slices | implemented | qrels metadata, no DB writes |
| Projection-aware field scoring | contract-only | requires production `SearchProjection` |
| BM25F | contract-only/deferred | no honest field projection path in this PR |
| `SearchProjection` type and durable `Original` publication | contract-only | existing production contract, not implemented by this harness |
| BM25F-ready field materialization and retrieval lifecycle | missing from this PR | deferred until projection indexing exists |
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
