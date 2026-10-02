# Fresh canonical qrels reference (2026-10-03)

This note records the first post-freeze quality measurement.  It is a
reference oracle, not a codec selection or product decision.

## Setup

The frozen serving study (`9bd438f310f70316853bd638db55282b2a39fe35`) was
already merged before opening the canonical DE-1M evaluation bundle.  The run
uses all 305 canonical queries and the untouched 3,144-row qrels file.  It does
not reuse the historical 152-query candidate stream.  The implementation is
`tools/agent-memory-bench/evaluate-fresh-qrels.py`; it uses a memory-mapped
FP32 corpus, exact dot-product ranking, and deterministic score-descending
then document-ID-ascending ordering.  Numeric IDs use numeric ordering; the
canonical DE-1M IDs are strings and therefore use lexical ordering.

Command (the source bundle is intentionally external to Git):

```text
python tools/agent-memory-bench/evaluate-fresh-qrels.py exact \
  --evaluation-root <canonical-de-1m>/e5 \
  --output tmp/fresh-qrels/exact.result.json \
  --raw tmp/fresh-qrels/exact.raw.jsonl
```

## Result

| measure | value |
| --- | ---: |
| corpus rows | 1,000,000 |
| queries | 305 |
| qrels rows | 3,144 |
| mean nDCG@10 | 0.6723946081 |
| p05 nDCG@10 | 0.0000000000 |
| mean MRR | 0.6938813429 |
| p05 MRR | 0.0833333333 |

The exact result and per-query raw rows are retained in the local evidence
workspace and are not committed as a large generated dump.  Their compact
provenance is:

```json
{
  "materialization_manifest_sha256": "b89bd21966532fd3e220dd9fb2ee279ecf1ec36e9ca8f6bc3e32d0e6652d705a",
  "document_vectors_sha256": "d4f67ebe91faa159eaaeb7884281ad0d0057c27cdb67c4007f260c6442636007",
  "document_ids_sha256": "4496b62e6243a90d1b64cc97c7eb12239b397f71ddcf743ad18fa82b3dcf832c",
  "query_vectors_sha256": "fa6c467e01bbe8a8e725d75fd5ac84c90008235be921c4a4d360d756d0d0d7b2",
  "qrels_sha256": "5b3d22a491558ae0955a7a968c4fec9974ddc463cb5f83fc67334cbfc87ab071"
}
```

## Interpretation and limits

This establishes the untouched exact quality ceiling for the fresh query set.
It does not measure route loss, THQ loss, packed-codec loss, latency, or MDBX
behavior.  Routed exports must be regenerated for all 305 queries and passed
to the `candidates` subcommand before those decomposition rows are reported.
The qrels are canonical DE-1M judgments, not a claim that a product winner has
been selected.
