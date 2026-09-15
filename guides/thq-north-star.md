# THQ retrieval North Star

This note is normative for the THQ/R4 research line. A result is not a
production result unless it satisfies the invariants below and reports the
complete persistent index footprint.

## Production invariants

1. Per-document FP32 E5 vectors are not stored in the production index.
2. Exact FP32 E5 is an offline teacher/reference only.
3. Packed THQ4 ordinal (96 bytes/document at 384 dimensions) is the current
   prefilter candidate. It is not a canonical production representation until
   the native total-footprint/latency gate proves that THQ is better than a
   direct shared scalar table. The 144-byte thermometer remains a research
   control, not a production default.
4. qrels nDCG@10 is the primary quality gate. Exact-teacher overlap is a
   diagnostic, not the product objective.
5. No query-specific candidate slab or pre-materialized query result is part
   of the persistent index.
6. The persistent index exists before a query arrives.
7. Total footprint includes every routing table, posting list, representative
   sidecar, THQ table, reranker table, metadata and storage overhead.
8. Native C++ end-to-end measurements are authoritative for latency and
   physical work.
9. Physical accounting reports posting entries touched, duplicate reads,
   candidate overshoot, logical payload bytes/pages and (when available)
   physical MDBX/OS pages.
10. The same document code is not stored once per seed unless a measured
    latency/quality gain justifies its additional footprint.

The direct shared INT8 path and the THQ4-prefilter path therefore remain equal
decision arms until native evidence resolves the trade-off:

```text
direct: shared INT8 -> top-10
cascade: THQ4 interval-squared -> top-128 -> shared INT8 -> top-10
```

The current 96-byte THQ4 choice is a research hypothesis, not permission to
add a second persistent representation without measured bandwidth, page and
end-to-end latency benefit.

## Required architecture comparisons

The next decisive gates compare persistent architectures, not query-specific
materializations:

```text
A: (seed,address) -> posting doc_ids -> one canonical doc_id -> packed THQ
B: (seed,address) -> posting-local duplicated THQ payload
```

The K16 comparison follows the same rule:

```text
A: address -> INT8 representative vectors
B: address -> representative doc_ids -> one shared compact code table
```

Every receipt must include a complete footprint table with disk bytes, mmap
bytes, resident bytes, posting/duplicate reads, and the quality result. The
candidate files produced by #411/#412 remain ephemeral layout controls; they
are not evidence of a persistent index.

## Quality gate semantics

The authoritative quality comparison is:

```text
direct packed THQ top-10 over the full corpus
THQ -> FP32 top-10 on the routed candidate set
offline exact E5 teacher top-10 over the full corpus
```

For all 152 queries, report qrels nDCG@10 for each output, candidate
survival separately, and teacher overlap only as a diagnostic. A lower teacher
overlap is acceptable only when product qrels quality remains acceptable.
