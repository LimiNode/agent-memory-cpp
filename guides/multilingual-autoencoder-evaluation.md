# Multilingual Autoencoder Evaluation

## Status

Planned research and tooling ladder. This guide defines the data, split, and
reporting contract before the project trains an autoencoder binary encoder on
an external multilingual corpus. It does not report a completed experiment or
promote an encoder to a production default.

The scope is compression of already generated dense embeddings. The
autoencoder does not tokenize raw text and does not train a text embedding
model.

## Questions

The experiment sequence must establish:

1. Whether document-only autoencoder training preserves E5 retrieval quality
   better than zero-training binary baselines.
2. Whether adding English, then linguistically diverse languages, improves or
   degrades Russian retrieval at an equal training-vector budget.
3. Whether one global encoder preserves each language instead of only a
   micro-average dominated by large corpora.
4. Whether the learned code generalizes beyond MIRACL Wikipedia passages.
5. Whether cross-language retrieval survives compression, separately from
   monolingual retrieval.

## Dataset roles

| Dataset | Role | Training use | Evaluation claim |
| --- | --- | --- | --- |
| BEIR SciFact | Small CI smoke fixture | None | Tooling and regression safety only |
| MIRACL | Primary multilingual study | Document embeddings only | Per-language monolingual retrieval preservation |
| Mr. TyDi | External generalization check | Never for the MIRACL-trained artifact | Transfer beyond MIRACL Wikipedia passages |
| XOR-Retrieve | Cross-lingual follow-up | Never for the evaluated artifact | Cross-language retrieval preservation |
| mMARCO | Later scale and stress corpus | Optional, clearly labelled translated data | Scale, not natural-language generalization |
| SWIM-IR | Later supervised-retriever research | Not needed for the autoencoder objective | Query-passage supervision, not reconstruction |
| MMTEB subset | Final broader evaluation matrix | None | Broader task coverage |

MIRACL is the primary source because it defines separate corpora, queries, and
qrels per language. Its official task is multilingual *monolingual* retrieval:
a Russian query is evaluated against Russian documents, not against all
languages in one shared corpus. It cannot establish cross-language quality.

## External-data manifest

Raw corpora, generated embeddings, checkpoints, and full benchmark reports are
external artifacts. They are not committed to this repository by default. A
future preparation tool emits a compact, versioned manifest and the experiment
note records its hash.

Every manifest must contain at least:

```json
{
  "schema_version": 1,
  "dataset": {
    "corpus": {
      "id": "miracl/miracl-corpus",
      "revision": "<caller-pinned immutable revision>"
    },
    "judgments": {
      "id": "miracl/miracl",
      "revision": "<caller-pinned immutable revision>"
    }
  },
  "languages": ["ru", "en", "de", "fr", "es", "ar", "zh", "ja"],
  "sampling": {
    "strategy": "balanced_stable_hash",
    "seed": 42,
    "train_documents_per_language": 25000,
    "evaluation_distractors_per_language": 10000,
    "evaluation_queries_per_language": 0
  },
  "split": {
    "policy": "held_out_document_ids",
    "evaluation_qrels_split": "dev",
    "qrels_excluded_document_ids_sha256": "<sha256 of sorted language-prefixed qrels doc IDs>",
    "evaluation_document_ids_sha256": "<sha256 of sorted final evaluation doc IDs>"
  },
  "embedding": {
    "model_id": "intfloat/multilingual-e5-small",
    "model_revision": "<resolved immutable revision>",
    "document_prefix": "passage: ",
    "query_prefix": "query: ",
    "normalized": true
  }
}
```

The caller supplies immutable source revisions when materializing the selected
language files. The preparation tool verifies source-file hashes, qrels closure,
and the requested languages in that materialization; it deliberately does not
perform remote revision resolution. A configuration called `all-18` is invalid
unless the caller materializes every requested language from the declared
revisions.

`evaluation_queries_per_language = 0` retains every dev query and is mandatory
for a comparable MIRACL quality result. A positive value creates a deterministic
qrels-closed pipeline smoke only; its selected query-ID digest is part of the
prepared manifest and its metrics must not be compared with the full-dev study.

## Split and leakage policy

For the primary held-out study:

- Training receives only document embeddings and stable document IDs.
- Queries, qrels, relevance grades, and query-derived metadata are forbidden
  from training, hyperparameter selection, and early stopping.
- Each evaluation corpus contains all qrels-referenced documents for selected
  dev queries plus deterministic distractors.
- `train_document_ids ∩ evaluation_document_ids = ∅`.
- Checkpoint selection uses a document-only validation split and reconstruction
  or binary-code-health metrics, or a predeclared training budget. Retrieval
  qrels remain final-evaluation-only.

A separate **corpus-adaptive** run may train on serving-index documents. It is
useful for deployment planning, but must be labelled `transductive`; it is not
evidence of held-out document generalization.

## Qrels-supervised training split

Query--qrel supervision is a separate experiment from the document-only
ladder. It uses the official MIRACL `train` topics and qrels, while the MIRACL
`dev` corpus, queries, and qrels remain held out for the final report.

- An external JSONL exclusion list may remove every held-out dev document ID
  from the supervised corpus. Its file SHA-256, canonical ID-set SHA-256,
  count, and observed-in-corpus count are part of the prepared manifest.
  Preparation fails if an ID is malformed, belongs to an unconfigured language,
  is duplicated, or is absent from the source corpus.
- After exclusions, a query remains only if it has at least one `grade > 0`
  qrel. The manifest records the dropped-query count and canonical ID hash.
  An authoritative configuration fixes both the expected count and ID-set hash
  so a changed exclusion set cannot silently alter the supervised task.
- The 25k document-only split remains the source of per-bit median calibration.
  Supervised queries, their qrels, and the held-out dev data never participate
  in that calibration.

For the first RU train-to-dev study, query-held-out validation is a
deterministic 80/20 split made before hard-negative mining. Documents may be
shared by the two query partitions; this tests generalization to unseen
queries, not to unseen passages. Frozen E5 top-k hard negatives are mined
separately for train and validation queries, exclude every positive qrel, and
must be persisted or canonically hashed in the trained artifact.

Supervised checkpoint selection uses hard binary codes on that fixed validation
query partition. For the first RU experiment, the candidate budget is fixed at
`K = 512` for every epoch and every baseline. The policy is lexicographic:
hard-code health gates must pass, then maximize positive-qrels coverage at
`K = 512`, then maximize reranked nDCG@10; ties prefer lower occupancy
deviation and then the earlier epoch. Document-only reconstruction and
soft-code proxy losses must not select a qrels-supervised checkpoint. The
held-out MIRACL dev split is used once only after this policy and all training
hyperparameters are fixed.

The supervised-train and held-out-dev E5 roots are intentionally different
materializations. Evaluation reports retain both the artifact's training-root
and prepared-study digests and the evaluated-root digests. They must not
require equality as a provenance shortcut: equality would make a genuine
held-out evaluation impossible.

## Training matrix

Every ablation row uses the same total document count, architecture, optimizer,
bit budgets, and training seeds.

| Regime | Documents | Purpose |
| --- | ---: | --- |
| RU-only | 100k Russian | Target-language baseline |
| RU+EN | 50k Russian + 50k English | Test whether English regularizes or shifts the code space |
| Eight-language balanced | 12.5k each of `ru,en,de,fr,es,ar,zh,ja` | Test multilingual diversity at equal budget |

The same held-out evaluation languages run for every regime. This exposes both
target-language preservation and out-of-distribution language loss.

The primary multilingual run uses `ru,en,de,fr,es,ar,zh,ja` with 25k documents
per language first. Repeat at 50k only if the smaller run is stable and has a
useful quality/bit-rate frontier. Sampling is balanced by language, not by
Wikipedia corpus size.

Only after the eight-language study is interpretable may the project compare
all available MIRACL languages. That production-scale manifest must state each
language weight; a raw proportional corpus union is not an accepted recipe.

## Evaluation and reporting

For every language and bit budget, compare:

1. Exact retrieval over original E5 vectors.
2. Binary-only retrieval.
3. Binary candidate filtering followed by exact rerank over original E5 vectors.
4. Approximate-vector retrieval, only when a decoder artifact is exported.

Report nDCG@10, Recall@10, Recall@100, MRR, candidate-stage exact-top-k and
qrels coverage, reranked quality, and
`relative_retention = compressed_metric / original_e5_metric`. Aggregate with
macro average, minimum language score, and maximum relative degradation.

Also record encoder-training time, materialization/build time, query encoding,
candidate search, rerank time, and memory footprint. Every result identifies
the encoder artifact hash, data-manifest hash, model revision, bit budget,
candidate limit, and seeds.

## Production interpretation

Multilingual support does not require one naive encoder trained on every
available passage.

- A language-routed monolingual deployment may use distinct code spaces for
  index shards. Their signatures are incompatible and require separate encoder
  identities.
- A cross-language deployment requires one global, balanced multilingual code
  space and the XOR-Retrieve evaluation gate.
- A hybrid deployment may retain both a global code space and language-aware
  shards.

Every trained artifact includes its dataset-manifest hash, language mixture,
split policy, training configuration, and model revision in its identity.

## Implementation ladder

1. Add a MIRACL preparation and manifest validator; external data stays out of
   CI.
2. Add a document-only autoencoder trainer and versioned artifact export,
   including optional decoder weights.
3. Add dependency-free C++ artifact loading and encoder inference.
4. Run the equal-budget MIRACL ablation at 64/128/256 bits.
5. Run the eight-language study and append measured results to an experiment
   note.
6. Run the frozen artifact on Mr. TyDi without retraining.
7. Run XOR-Retrieve only when cross-language retrieval is a product capability.
8. Evaluate an all-language balanced production-scale mixture.

## Sources

- [MIRACL project](https://github.com/project-miracl/miracl)
- [Mr. TyDi project](https://github.com/castorini/mr.tydi)
- [XOR QA paper](https://aclanthology.org/2021.naacl-main.46/)
- [mMARCO dataset](https://huggingface.co/datasets/unicamp-dl/mmarco)
- [SWIM-IR project](https://github.com/google-research-datasets/swim-ir)
- [MMTEB paper](https://arxiv.org/abs/2502.13595)
