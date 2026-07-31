# MIRACL AE Study Preparation

`prepare-miracl-ae-study.py` creates a deterministic, document-only training
split plus a held-out retrieval evaluation split for the multilingual binary
autoencoder study. It is an offline research utility; it is not linked into the
C++ library and never runs from CI against external data.

The preparer intentionally accepts locally materialized MIRACL files. This
keeps network access and model dependencies outside the repository build, while
the generated manifest records the source identities and every relevant file
hash.

## Source acquisition

Use immutable Hugging Face revisions in the study config. Download only the
languages used by a run. For a Russian-only run, for example:

```powershell
hf download miracl/miracl-corpus --repo-type dataset --revision <corpus-commit> `
  --include "miracl-corpus-v1.0-ru/*" --local-dir data/miracl-input
hf download miracl/miracl --repo-type dataset --revision <judgments-commit> `
  --include "miracl-v1.0-ru/topics/topics.miracl-v1.0-ru-dev.tsv" `
  --include "miracl-v1.0-ru/qrels/qrels.miracl-v1.0-ru-dev.tsv" `
  --local-dir data/miracl-input
```

Repeat the language-specific include patterns for a balanced multilingual run.
Do not download all language directories merely because the eventual product
will be multilingual. The research matrix begins with equal per-language
budgets so its ablations are interpretable.

The preparer supports a corpus glob because each MIRACL language corpus is
published as multiple `docs-*.jsonl.gz` files. Its example configuration is
[`../tools/agent-memory-bench/miracl-ae-study.example.json`](../tools/agent-memory-bench/miracl-ae-study.example.json).

## Preparation and validation

```powershell
python tools/agent-memory-bench/prepare-miracl-ae-study.py `
  --config tools/agent-memory-bench/miracl-ae-study.example.json `
  --input-root data/miracl-input `
  --output-root data/miracl-ae-8lang

python tools/agent-memory-bench/validate-miracl-ae-study.py `
  --prepared-root data/miracl-ae-8lang `
  --config tools/agent-memory-bench/miracl-ae-study.example.json `
  --source-root data/miracl-input
```

The source root is scanned once to locate qrels documents, twice more for
stable-hash train and distractor selection, and once to calculate provenance
hashes. That streaming scan is necessary
to sample fairly from each complete selected language corpus, but it never
stores the corpus in memory. It is therefore practical for large language
partitions, subject to the I/O time of reading those partitions.

The outputs are:

- `train-documents.jsonl`: balanced, document-only AE training records;
- `evaluation-documents.jsonl`, `evaluation-queries.tsv`, and
  `evaluation-qrels.tsv`: held-out retrieval evaluation records;
- `manifest.json`: config, source, output, split-policy, and embedding
  provenance.

No document ID may appear in both output splits. All qrels-referenced documents
are retained in evaluation, including documents with non-positive labels, so
the qrels file remains closed over the evaluation corpus. Queries and qrels are
evaluation-only and never enter the AE training split.

The current validator verifies output hashes, counts, split disjointness, qrels
closure, input configuration identity, and source-file hashes. It does not
claim that the source materialization itself is hermetic: pin the two Hugging
Face revisions and preserve the generated manifest with any benchmark report.

When `--config` and `--source-root` are supplied, the validator additionally
loads the local preparer, verifies its exact identity and source hash, and
replays the `balanced_stable_hash` selections from the source shards. A merely
balanced but differently selected train/evaluation split is rejected. The
prepared manifest records both the configured `evaluation_qrels_split` and
SHA-256 digests of the qrels-excluded and final evaluation document-ID sets.
