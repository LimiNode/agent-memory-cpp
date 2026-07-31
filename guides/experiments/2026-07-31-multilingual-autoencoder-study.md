# Multilingual Autoencoder Binary Encoder Study

## 2026-07-31 — Protocol established

### Status

Planned. This note records the agreed experiment before external datasets,
embeddings, training checkpoints, or benchmark figures are created. Later PRs
append measured results here rather than replacing this protocol.

### Question

Can a document-only trained autoencoder produce binary codes that preserve
multilingual E5 retrieval quality better than zero-training binary baselines,
without concealing regression in Russian or other individual languages?

### Dataset and split plan

- CI regression fixture: BEIR SciFact.
- Primary study: MIRACL with per-language Wikipedia corpora, queries, and
  qrels. It evaluates monolingual retrieval per language.
- Initial languages: `ru,en,de,fr,es,ar,zh,ja`.
- Training receives only E5 `passage: ` document embeddings; E5 `query: `
  embeddings and qrels are evaluation-only.
- Held-out run: training document IDs do not overlap evaluation document IDs.
  Evaluation corpora retain all qrels-referenced documents plus deterministic
  distractors.
- Checkpoints are selected using document-only validation reconstruction/code
  health, or a predeclared budget, never qrels.

### Expected result

At equal training-vector count, a balanced multilingual mixture should retain
quality across more languages than a RU-only encoder. It might still reduce
Russian quality relative to RU-only; this is a hypothesis to measure, not an
assumption.

### Planned matrix

| Regime | Training documents |
| --- | ---: |
| RU-only | 100k Russian |
| RU+EN | 50k Russian + 50k English |
| Eight-language balanced | 12.5k per selected language |

The primary run follows with 25k documents per selected language and may expand
to 50k only after the smaller run is stable.

### Measurements

For each language and bit budget (64/128/256), record exact E5, binary-only,
and binary-candidate-filter plus exact-rerank quality:

- nDCG@10, Recall@10, Recall@100, and MRR;
- exact-top-k and qrels candidate coverage;
- `relative_retention = compressed_metric / original_e5_metric`;
- macro average, minimum-language score, and maximum relative degradation;
- training/materialization/build/query-encoding/candidate-search/rerank timing
  and memory.

### Threats to validity

- MIRACL does not measure cross-language retrieval.
- A balanced Wikipedia sample is not automatically representative of a
  production memory corpus.
- Local timing results need repeated runs, warm-up, environment details, and
  preserved raw artifacts before they become stable performance evidence.
- A corpus-adaptive run can be useful operationally, but is transductive and
  must not be presented as held-out generalization.

### Follow-up ladder

1. Implement the external MIRACL preparation and manifest validator.
2. Implement offline autoencoder training and versioned artifact export.
3. Load the artifact for dependency-free C++ encoder inference.
4. Run this MIRACL matrix and record results.
5. Evaluate the frozen artifact on Mr. TyDi without retraining.
6. If cross-language retrieval is a product goal, evaluate XOR-Retrieve.

See [multilingual autoencoder evaluation](../multilingual-autoencoder-evaluation.md)
for the normative contract and source links.

## 2026-07-31 — First RU development pilot (128-bit linear AE)

### Status

Completed as an end-to-end pipeline check, not as a quality claim or a member
of the planned equal-budget matrix. The pilot is useful negative evidence: a
small document-only linear autoencoder did not preserve held-out E5 retrieval.

### Setup

- MIRACL Russian dev, qrels-closed evaluation corpus: 13,607 documents, 1,252
  queries, and 13,100 judgments.
- Document-only training source: 2,500 selected E5 `passage: ` embeddings;
  2,358 were training rows and 142 were the deterministic validation subset.
- E5: `intfloat/multilingual-e5-small` at revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`, CPU float32, batch size 32,
  eight recorded CPU threads.
- Linear `tanh_sign_ste_v1` autoencoder, 128 bits, seed 42, 20 epochs,
  batch size 256, learning rate `0.001`, quantization weight `0.1`, and
  balance weight `0.01`.
- Artifact SHA-256:
  `d8fb1a44a0250f5cb898ab79520ea64e9d05fe334993b9ecd828b71e3265577c`.
  Materialization manifest SHA-256:
  `9e93b16351a95cf3da7f240f0cdc6f9bdf7a2f77e3c29516b68758ff5ae14245`.

The C++ evaluator compared original-float E5 with Hamming candidate selection
followed by exact cosine reranking. It separately ranked decoder-reconstructed
document vectors against the original float query. `oracle_k` was 10.

### Results

| Candidate limit | Exact top-10 candidate coverage | Reranked top-10 agreement | Reranked nDCG@10 |
| ---: | ---: | ---: | ---: |
| 128 | 0.0088 | 0.0088 | 0.0108 |
| 512 | 0.0408 | 0.0408 | 0.0445 |
| 2,048 | 0.1574 | 0.1574 | 0.1724 |
| 13,607 (full corpus) | 1.0000 | 1.0000 | 0.8027 |

The original-float E5 oracle achieved nDCG@10 `0.8027`, Recall@10 `0.9350`,
and MRR `0.8144`. Decoder approximation was also ineffective: nDCG@10
`0.00077` and exact-top-10 agreement `0.00096`.

### Interpretation

The full-corpus row recovers the original E5 result, so the exact reranker and
qrels evaluation path agree with the oracle. The loss occurs before reranking:
the 128-bit code trained on only 2,358 document vectors almost never admits
the original nearest neighbours. Increasing the candidate limit helps, but
only by sacrificing most of the candidate-stage reduction.

This result does **not** reject autoencoder binarization in general. It rejects
this undertrained 2.5k-document linear artifact as a useful RU candidate
filter, and it rejects its decoder as a compact replacement for retained float
vectors.

### Evaluation hardening discovered by the pilot

The first run exposed two contract defects that were fixed before recording
the table:

- the C++ materialized-artifact loader now accepts standard four-field TREC
  qrels (`query Q0 document grade`) emitted by the MIRACL preparer, in addition
  to the older three-column form;
- exact-agreement accounting now distinguishes whole candidate-pool coverage
  from top-K agreement, so decoder agreement cannot be spuriously reported as
  `1.0` merely because its full ranking contains every document.

### Limitations and next checks

- The training set is far below the planned RU-only 100k and primary 25k-per-
  language regimes; it is a development pilot only.
- This run uses a single artifact and seed, one bit budget, and no repeated
  timing protocol.
- The prepared development split predates the bounded-query smoke extension;
  its recorded manifest remains the provenance for this result, but future
  comparable runs must use the current preparer and source-replay validator.

Next, materialize the current 25k RU pilot under its recorded CPU recipe, then
run 64/128/256-bit artifacts before moving to equal-budget RU+EN and
eight-language ablations. Keep original float vectors for exact reranking; the
decoder mode is currently an explicitly failed compact-storage baseline.
