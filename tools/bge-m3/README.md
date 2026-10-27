# BGE-M3 sparse pilot runner

`run_bge_m3_sparse_pilot.py` is an optional, read-only Python pilot. It is not
part of the required C++ build and does not write an index or activate a
production backend.

The runner first verifies every BGE-M3 artifact against the pinned revision
`5617a9f61b028005a4858fdac845db406aefb181`. It then requires frozen corpus,
query and independent qrels JSON files. Missing artifacts or evaluation data
produce a receipt with `decision=INSUFFICIENT_EVIDENCE`.

```powershell
python tools/bge-m3/run_bge_m3_sparse_pilot.py `
  --artifact-dir <directory-containing-all-bge-m3-files> `
  --corpus <corpus.json> `
  --queries <queries.json> `
  --qrels <qrels.json> `
  --output build/bge-m3-pilot-receipt.json
```

The artifact directory must contain the exact checkpoint, tokenizer,
configuration and `sparse_linear.pt` files. The runner uses the official
`FlagEmbedding.BGEM3FlagModel` sparse interface, retains query and document
weights separately, verifies the local dot-product posting scorer against
`compute_lexical_matching_score`, and reports BGE-M3 sparse and BM25 arms on
the same data. A cutoff below 50 reports `Recall@50: null` rather than
fabricating a result.

Corpus, query and qrels records require revisions and deterministic IDs. Each
document/query declares `en`, `ru` or `code`; judged queries require qrels and
no-answer queries must have none. Do not create qrels from BGE-M3 predictions.

The model cache, receipts and any corpus files are intentionally excluded from
Git. Install optional ML dependencies only in an isolated research environment.
