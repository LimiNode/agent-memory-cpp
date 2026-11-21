# BGE-M3 sparse pilot protocol (2026-10-27)

**Status:** `INSUFFICIENT_EVIDENCE`

**Scope:** a read-only pilot protocol for measuring BGE-M3 lexical weights
against a frozen retrieval corpus. This document does not activate a production
writer, add ML dependencies to the C++ library, or claim a model result.

## Evidence ledger

The local cache contains exactly one file:

| Artifact | Revision | Size | SHA-256 | Loaded by this pilot |
| --- | --- | ---: | --- | --- |
| `pytorch_model.bin` | `5617a9f61b028005a4858fdac845db406aefb181` | `2,271,145,830` bytes | `B5E0CE3470ABF5EF3831AA1BD5553B486803E83251590AB7FF35A117CF6AAD38` | No |

The Hugging Face repository is `BAAI/bge-m3`, and the revision API identifies
the commit as `5617a9f61b028005a4858fdac845db406aefb181`. The repository tree at
that revision contains `config.json`, `tokenizer.json`,
`sentencepiece.bpe.model`, `tokenizer_config.json`, `special_tokens_map.json`,
`sparse_linear.pt`, and other files in addition to the checkpoint. None of
those companion files is present in the local cache, so no tokenizer or model
configuration was loaded. The model card declares `license: mit`; the revision
API itself reports no normalized license field, so licensing is recorded as
model-card metadata rather than inferred from the local file.

No frozen English/Russian/code corpus, query set, or qrels is available in this
worktree. Consequently there is no valid BGE-M3 ranking, Recall, nDCG,
no-answer, or latency result to report.

## Required pilot inputs

Before running a real pilot, freeze and hash every input below at one model
revision:

1. `pytorch_model.bin` (or an explicitly selected supported format);
2. the exact tokenizer files and tokenizer configuration;
3. model configuration and sparse-head configuration, including `sparse_linear.pt`;
4. license and source-revision metadata;
5. a corpus containing English, Russian, and code documents;
6. judged queries with explicit qrels, including at least one no-answer query;
7. a BM25/tokenizer baseline and a deterministic tie-breaking rule.

The receipt must list each file, byte size, SHA-256, source URL, and revision.
Missing or mismatched artifacts are a hard `INSUFFICIENT_EVIDENCE` outcome, not
a reason to substitute a different tokenizer, synthetic qrels, or a toy score.

## Execution gates

The pilot runner should:

- load the model only after all hashes and revision identities match;
- export actual BGE-M3 token IDs and sparse weights for documents and queries;
- build the weighted index once outside measured query repeats;
- report traversal/scoring, ranking/materialization, and total-query timings
  separately;
- compare weighted sparse retrieval with the pinned BM25 baseline using the
  same corpus, qrels, cutoff, and tie rule;
- run positive and mutation controls for token identity, tokenizer revision,
  missing postings, duplicate postings, stale source, and unexpected
  no-answer hits;
- keep generated receipts and model outputs out of version control.

`sample_count` must mean query-repeat observations, not independent queries.
Small samples are `directional`; they do not establish stable p95/p99 claims.

## Boundary

This protocol is an experiment only. It does not change C++ storage schemas,
enable a learned-sparse production writer, or establish semantic superiority
over the existing lexical index. A future implementation must pass the
artifact, parity, quality, and mutation gates before any production design is
considered.
