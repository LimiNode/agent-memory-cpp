# Embedding Model Evaluation

Status: `Normative` for encoder comparisons; this guide defines an evaluation
contract, not a default model choice.

## Model identity is more than dimension

Two vectors are comparable only when their complete embedding contract matches.
Dimension alone is never an identity: `384D` MiniLM, Nomic, and E5 vectors are
different coordinate spaces and must not be mixed.

An embedding representation identity binds at least:

```text
provider/model name
model revision or immutable artifact digest
tokenizer identity and revision
pooling mode
normalization mode
document input policy
output dimension and Matryoshka projection
```

The identity is persisted with vectors, query receipts, and derived ANN/codec
manifests. A missing or incompatible field fails closed. Changing any field
creates a successor projection and requires a fresh quality evaluation.

Keep the following contracts separate:

```text
EmbeddingRepresentationIdentity
    model/provider revision, tokenizer, pooling, normalization,
    document input policy, output dimension/projection

QueryEncodingPolicy
    query prefix/instruction, query template, query-side tokenizer policy

RetrievalScoringIdentity
    similarity metric, score normalization, ANN parameters
```

Changing a scoring metric requires a fresh retrieval evaluation and may require
an index rebuild, but does not by itself require re-embedding unchanged
vectors. Changing only a query instruction does not automatically invalidate
the persisted document projection, although it does require a new query-policy
receipt and evaluation. A metric or query policy is part of the comparison
contract even when it is not part of the document-vector identity.

## Chunking is part of the retrieval model

Chunking is not an invisible preprocessing detail. A reproducible evaluation
records:

```text
chunker identity and revision
tokenizer identity
maximum model tokens
structural split policy
overlap and boundary policy
truncation/error policy
```

Word counts are not tokenizer-token counts. If a model accepts 256 wordpieces,
feeding a 512-word chunk and silently truncating it is a different measured
condition, not an equivalent baseline. Benchmarks either use a common
tokenizer-bounded chunk set or report each model's best-native chunking as a
separate lane.

## Two-stage evaluation gate

### E0: encoder-only exact gate

Every candidate first runs without ANN, quantization, or learned codec fitting:

```text
canonical text
  -> candidate encoder
  -> exact FP32 corpus/query vectors
  -> exact retrieval
  -> the same qrels and query order
```

Required measurements are nDCG@10, MRR, and relevance/candidate Recall@128
against qrels or another declared relevance target,
query encoding p50/p95, corpus encoding throughput, peak RAM/VRAM, model and
tokenizer identity, and a chunk-length sensitivity sweep. RU/DE/EN or other
language slices are reported separately when the corpus supports them.

This E0 recall is not ANN overlap. `RelevanceRecall@128` (or an explicitly
named `CandidateRecall@128`) measures whether relevant resources are present in
the returned set. E1 additionally reports `ANNRecall@128`: overlap between an
approximate top-128 result and the exact FP32 top-128 from the same embedding
space. Exact E0 retrieval must not report ANN recall against itself.

The baseline is `intfloat/multilingual-e5-small`. `all-MiniLM-L6-v2` and
`nomic-embed-text-v1.5` are controls/candidates, not selected backends.
Potential modern candidates such as `google/embeddinggemma-300m`,
`snowflake-arctic-embed-m-v2.0`, `Qwen/Qwen3-Embedding-0.6B`, and
`Alibaba-NLP/gte-multilingual-base` remain survey candidates until E0 evidence
exists. External model-card scores are context, not project results.

### E1: space-specific ANN/codec gate

Only an E0 candidate with an explicit acceptance decision proceeds:

```text
new embedding identity
  -> refit route/prototype/THQ parameters
  -> refit every codec in that space
  -> regenerate fresh routed quality and serving evidence
```

Codec, route, or qrels results from E5 must never be reused for MiniLM, Nomic,
Arctic, Gemma, Qwen, or another space. Each space receives its own source
hashes, model manifest, exact oracle, packed payloads, and quality receipt.

## Native versus matched-size comparisons

Report both views when models expose a supported projection:

1. **Native quality:** each model at its documented native output dimension.
2. **Matched storage:** dimensions selected from documented Matryoshka or
   elastic-output contracts, with equal bytes/vector and the same downstream
   evaluation.

Never compare a native 1024D model to a 256D model and call the difference a
codec result. Encoder quality, vector footprint, and codec quality are separate
axes in the report.

When native chunking differs, chunk-level qrels cannot simply be reused. A
cross-chunking comparison either uses canonical resource/document-level qrels,
or records a deterministic projection of each judgment onto the candidate's
chunk universe with a separate mapping receipt. The fixed document universe in
the current DE-1M work is a special case; scientific-RAG evaluations must make
this mapping explicit.

## Reproducibility and acceptance

An E0/E1 receipt binds corpus, chunks, query set, qrels, model/tokenizer
artifacts, build/runtime, hardware, batch size, precision, thread policy,
warmup/repeat contract, and raw result hashes. Missing source material is
`PENDING_SOURCE_REPLAY`, never an inferred score. A model is not promoted from
candidate to default on advertised leaderboard numbers alone.
