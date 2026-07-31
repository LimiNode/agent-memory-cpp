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
