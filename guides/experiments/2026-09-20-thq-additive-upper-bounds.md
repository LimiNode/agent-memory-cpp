# THQ additive-codec upper-bound suite

Date: 2026-09-20  
Status: `PLANNED`

## Purpose

The existing THQ-joint and RSLM-like rows are useful bounded controls, but they
do not close the class of additive or conditional learned codecs.  This gate
is a capacity diagnostic for AVQ/AAQ/QINCo-like ideas, not a claim to reproduce
any paper implementation.

The experiment deliberately separates three evidence levels:

1. `avq_like_greedy`: document-only global additive codebooks, greedily encoded;
2. `aaq_like_beam`: the same additive codebooks, encoded with a reconstruction
   beam, which is an optimistic encoder control;
3. `qinco_like_query_oracle`: the beam's stored reconstructions are selected
   with the query score.  This is **query-leaking** and is an upper bound on the
   tested codebook/path family, not a deployable scorer.

Each model uses 8-bit additive stages at 32/48/64 B side payloads (4/6/8
stages), on top of the canonical 96 B THQ4 base.  The production boundary is
the same frozen R4 candidate shell -> THQ4 interval-squared top-128.  Quality
is measured after the common candidate-local scorer and reported against the
same FP32 cosine teacher and qrels.

## Interpretation rules

The query-oracle row may show how much ranking quality is still present in the
codebook family when the encoder is allowed to see `q`.  It must not be used
as a production candidate.  If even this row is below the INT8/RSLM frontier,
the tested additive capacity is insufficient.  If it is strong while greedy
and beam rows are weak, the bottleneck is encoding/search, not representation
capacity.  If beam is strong, only then is a faithful conditional-codebook
implementation justified.

The runner is source-bound and fail-closed.  It rejects compact result JSON,
decoded INT8, and legacy Hamming payloads as document substitutes.  Until the
1M FP32 documents, matching queries, qrels, and teacher IDs are available, the
gate remains `not executed`; no upper-bound number is inferred from earlier
eight-query compact artifacts.

Implementation: `tools/agent-memory-bench/run-thq-additive-upper-bounds.py`.
