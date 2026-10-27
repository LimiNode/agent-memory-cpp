# BGE-M3 sparse pilot runner

`run_bge_m3_sparse_pilot.py` is an optional, read-only Python pilot. It is not
part of the C++ runtime and does not write an index or activate a production
backend.

The runner verifies every BGE-M3 artifact against revision
`5617a9f61b028005a4858fdac845db406aefb181`, then requires frozen corpus,
query and independently judged qrels files plus a decision policy committed
before the evaluation. The policy carries a registration manifest containing
the source revision, path and digest of its policy body; a Boolean flag alone
is not accepted as preregistration evidence. Missing evidence produces
`INSUFFICIENT_EVIDENCE`.
Artifact, dependency, inference and scorer failures retain a distinct
`execution_status` and return a non-zero exit code.

```powershell
python tools/bge-m3/run_bge_m3_sparse_pilot.py `
  --artifact-dir <directory-containing-all-bge-m3-files> `
  --corpus <corpus.json> `
  --queries <queries.json> `
  --qrels <qrels.json> `
  --decision-policy tools/bge-m3/decision-policy.example.json `
  --output build/bge-m3-pilot-receipt.json
```

The optional inference environment is pinned in `requirements-pilot.txt`.
Routine CI does not install it or download model files. It runs the pure
dependency-free contract suite:

```powershell
python tools/bge-m3/run_bge_m3_sparse_pilot.py --self-test
```

The runner uses the official `FlagEmbedding.BGEM3FlagModel` sparse interface,
retains query and document weights separately, validates output cardinality,
tokenizer token-ID range and weights, and checks the common posting scorer
against `compute_lexical_matching_score`. A cutoff below 50 reports
`Recall@50: null`; a cutoff below 10 also reports `nDCG@10: null` and cannot
produce a GO/NO-GO decision.

Corpus, query and qrels records require revisions and deterministic IDs. Every
document/query declares `en`, `ru` or `code`; all three slices need a positive
judged query, no-answer queries have no qrels, and qrels declare independent
human-judgment provenance. Qrels must not be derived from BGE-M3 predictions.

BM25 document statistics are built once. Timings separate artifact
verification, model loading, document/query encoding, sparse and BM25 index
construction, posting traversal, ranking, and BM25 query execution.
`post_encoding_retrieval_p50_ms` excludes query encoding. End-to-end per-query
latency remains unavailable while encoding is measured only as one batch.

`no_answer_nonempty_hit_rate_diagnostic` is not abstention accuracy: no
calibrated threshold is registered. The only research decisions are
`GO_TO_LARGER_EVALUATION`, `NO_GO`, and `INSUFFICIENT_EVIDENCE`, derived from
the preregistered policy after a complete qualified evaluation. Receipt
admission revalidates the complete quality schema, frozen input evidence,
verified/loaded artifacts and the policy-derived decision. Completed
`INSUFFICIENT_EVIDENCE` is valid when the replayed cohort or cutoff is
insufficient; it is not the same as a failed execution. A completed receipt
also carries a replay bundle of the frozen corpus identities, queries, qrels
and ranked hits. Admission reloads those files, checks their SHA-256 values and
recomputes both arms' metrics before accepting any decision. An authored empty
quality object, forged verdict or metric aggregate is rejected. Sparse products
and accumulated scores must remain finite, malformed qrels are reported as
controlled evidence failures rather than uncaught type errors, and post-model
errors produce a non-zero diagnostic failure receipt when publication succeeds.

Model caches, receipts, generated embeddings and evaluation data stay outside
Git. Install ML dependencies only in an isolated research environment.
