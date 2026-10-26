# Learned sparse retrieval: source and feasibility audit

**Date:** 2026-10-26  
**Status:** `PROTOCOL_ONLY` — no benchmark result and no production recommendation  
**Base:** `main@1e2806d21bc36fa6baeca536aaccec0bf535502c`  
**Scope:** independent research PR; no C++ API, MDBX schema, or production dependency

## Decision boundary

The repository already has a usable BM25 lexical path. This note evaluates
whether learned document-term weights are worth a separate offline experiment;
it does not replace or delay that path. A learned model must remain frozen at
insertion time, emit derived postings, and be searchable without running a
neural model for every query. Training and inference belong to an external
research environment. No ONNX, PyTorch, model checkpoint, tokenizer package,
or generic ML runtime may be added to the required C++17 build by this PR.

The first deliverable is a source/licensing/feasibility audit. A positive
answer here does not imply that a model improves this project's corpus.

## Candidate matrix

The matrix separates algorithm, code, checkpoint, tokenizer, and data terms.
These are independent checks: an Apache/MIT repository does not automatically
grant commercial use of its downloaded model weights or training data.

| Candidate | Learned signal | Primary sources | Tokenizer / vocabulary | Languages | Code and model terms | Initial decision |
| --- | --- | --- | --- | --- | --- | --- |
| DeepCT | contextual reweighting of observed terms | [paper](https://doi.org/10.1145/3397271.3401204), [reference repository](https://github.com/AdeDZY/DeepCT) | model-specific BERT WordPiece; export must preserve the exact vocabulary and normalization | English in the published checkpoints | repository reports BSD-3-Clause; checkpoint/data terms require a separate receipt | feasibility screen only |
| uniCOIL | contextual weights for lexical subwords | [paper](https://arxiv.org/abs/2106.14807), [Pyserini](https://github.com/castorini/pyserini) | WordPiece vocabulary; query and document tokenization must be identical for matching terms | primarily English checkpoints | Pyserini is Apache-2.0; checkpoint and corpus terms remain separate | high-priority offline arm |
| DeepImpact | document-term impacts, optionally with expansion | [reference repository](https://github.com/AHNR0/DeepImpact), [PISA](https://github.com/pisa-engine/pisa) | model/tokenizer-specific; impact vocabulary must be versioned with the artifact | published evidence is primarily English | PISA is Apache-2.0; reference model terms are not assumed verified | high-priority impact arm |
| BGE-M3 sparse | multilingual sparse weights plus optional dense/multi-vector outputs | [paper](https://arxiv.org/abs/2402.03216), [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding) | model tokenizer and sparse vocabulary; preserve model fingerprint and tokenizer revision | multilingual, including Russian and English | repository is MIT; checkpoint/commercial terms must be checked before redistribution | multilingual feasibility arm |
| SPLADE v2 / SPLADE++ | sparse expansion and reweighting | [SPLADE](https://arxiv.org/abs/2107.05720), [repository](https://github.com/naver/splade) | masked-language-model WordPiece vocabulary; expansion may create terms absent from source text | mostly English evidence | repository/model terms report non-commercial/share-alike constraints for common releases | research-only control |

`SPLADE` is not a production candidate until its model terms and index growth
are explicitly accepted. No candidate is promoted because of published
leaderboard numbers; all claims require the project's frozen RU/EN/code qrels.

## Feasibility fields

Every candidate receipt must contain the following fields before any quality
comparison is accepted:

| Field | Required evidence | Failure condition |
| --- | --- | --- |
| `source_revision` | paper URL, repository commit/tag, checkpoint identifier | mutable `main` or unpinned download |
| `code_license` | SPDX or verified repository license | `NOASSERTION` or incompatible code copied into the project |
| `model_license` | license/terms for the exact checkpoint | repository license used as a proxy for weights |
| `tokenizer_revision` | tokenizer files, normalization, vocabulary hash | token IDs cannot be reproduced |
| `language_scope` | declared RU/EN/code coverage and known gaps | multilingual claim inferred from model name only |
| `encoding_mode` | document-side, query-side, or expansion behavior | inference cost mixed into search timing |
| `posting_schema` | `(term_id, impact, unit_id)` plus artifact identity | bare scores without source revision or unit identity |
| `document_cost` | cold model load, warm encoding, output bytes, peak RSS | only aggregate batch time reported |
| `query_cost` | preprocessing, optional query encoding, search separately | query model time hidden in posting traversal |
| `update_cost` | one-document insert, replacement, delete, and rebuild cost | full-corpus rebuild silently substituted for update |
| `offline_reproducibility` | frozen environment and deterministic output hash | network or mutable checkpoint required to replay |
| `deployment_eligibility` | AGPL/commercial compatibility decision | “research only” artifact used in production by accident |

The normal operating mode is **frozen model → inference at insertion →
persisted derived weights → cheap query lookup**. Re-training during insertion
is out of scope. If a candidate requires query-time neural inference, it is a
separate latency arm and cannot be compared as if it were an inverted-index
scorer.

## Artifact and posting contract

An offline artifact is valid only when it carries:

- `source_revision` and `projection_version` (or the declared M0 lexical
  document revision);
- `model_id`, `model_revision`, `tokenizer_revision`, and model/license receipt;
- canonical content hash and `ResourceId`/unit identity;
- `generation` and active/inactive state for stale-update checks;
- sorted sparse postings `(term_id, weight, unit_id)` with an explicit weight
  type (`float32` first; `int8` only after calibration);
- vocabulary and tokenizer hashes, posting count, logical bytes, and physical
  bytes;
- deterministic serialization hash.

The learned artifact is derived retrieval data. It must not mutate canonical
text, `KnowledgeUnit`, `ResourceManifest`, or the existing `flat-v2` schema in
this research stage. A future adapter may store a separate projection, but it
must be bound to the source revision and model fingerprint and must fail closed
on a stale generation.

## Matched bake-off plan

Phase 2 will reuse the project's lexical qrels shape (`query_id`, `doc_id`,
`grade`) and the existing research runner contract as the reference, without
making this PR depend on the still-Draft classical bake-off #534. The runner
must load one frozen corpus/query/qrels manifest and execute every arm over the
same records, filters, candidate depth, final `K`, and tie rule.

The frozen query set must include:

- exact C++ identifiers, class names, methods, paths, flags, and error codes;
- Russian technical queries, English queries, and mixed RU/EN queries;
- paraphrases and vocabulary-mismatch queries;
- rare terms and long documents;
- no-answer queries and stale/deleted-resource cases.

The minimum arms are:

1. exhaustive project BM25 (mandatory relevance and ordering oracle);
2. BM25+ and BM25L from the classical reference arm;
3. DeepImpact or DeepCT document-side impacts;
4. uniCOIL document/query weighting;
5. BGE-M3 sparse for RU/EN coverage;
6. SPLADE only if its model terms pass the license gate;
7. optional RRF fusion, reported separately from each lexical arm.

The benchmark must distinguish document encoding, query encoding, posting
traversal, and result materialization. A learned arm that changes tokenization
or expands terms must report its vocabulary and posting growth instead of
pretending that its IDs are comparable with BM25 token IDs.

## Metrics and acceptance gates

Required quality metrics:

- Recall@1/5/10/50, nDCG@10, and MRR;
- identifier/path/command exact-hit retention;
- per-slice RU, EN, mixed, paraphrase, and no-answer results;
- top-K identity parity against exhaustive BM25 for execution-only controls.

Required cost metrics:

- cold and warm model load;
- document encoding p50/p95 and bytes per document;
- query encoding and query search measured separately;
- one-document insert, replacement, delete visibility, and full rebuild cost;
- posting count, logical/physical bytes, average non-zero weights, and peak RSS.

Timing receipts must report `sample_count` as query-repeat observations, not
independent queries. `measured` means only that the observed sample
distribution was large enough for the protocol; it does not make a p99 claim
statistically stable. Fewer than 100 observations are directional, 100–999
descriptive, and at least 1000 may be labelled `measured`.

Quality gates:

1. no identifier or no-answer regression beyond the pre-registered tolerance;
2. positive semantic lift on a judged slice, not only a smoke fixture;
3. bounded update cost for one changed resource;
4. reproducible artifact hash and complete license receipt;
5. no stale generation or deleted-unit resurrection;
6. no mandatory ML dependency in the C++17 library.

Failure of any gate is a `NO-GO` for an adapter, while preserving the BM25
baseline as the default.

## Staged follow-up PRs

This PR is Phase 1 only. Follow-ups remain independently reviewable:

| Stage | Deliverable | Explicit exclusion |
| --- | --- | --- |
| PR2 | isolated read-only runner, frozen manifest, smoke fixture, timing separation | no production API or model dependency |
| PR3 | artifact validator and negative controls for model/tokenizer/content drift | no model bundled in C++ |
| PR4 | classical/code-aware bake-off and BM25 comparison | no learned promotion |
| PR5 | offline learned-sparse artifact adapters and quantization study | no MDBX production schema change |
| PR6 | decision record and optional adapter proposal | no production default without all gates |

The research outcome may be `NO-GO`. The first recommended candidate for a
real measurement is DeepImpact/uniCOIL because document-side weights preserve
cheap query execution; BGE-M3 is the multilingual control; SPLADE remains a
license and index-growth risk. These are experiment priorities, not results.

## Explicit non-goals

- no C++ neural inference;
- no ONNX/PyTorch/transformers dependency;
- no modification of BM25, `flat-v2`, `segmented-v1`, or publication
  coordinator behavior;
- no claim that learned weights improve quality before matched qrels;
- no production use of an artifact whose model or data license is unresolved;
- no merge or production default from this research PR.
