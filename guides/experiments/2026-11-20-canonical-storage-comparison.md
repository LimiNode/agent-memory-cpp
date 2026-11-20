# C1 and numeric flat storage comparison

Date: 2026-11-20. Research PR #663; correctness prerequisite #662.
Status: executable comparison implemented; full-size performance qualification
not obtained. No production activation recommendation.

## Question and method

Compare legacy `MdbxCanonicalContentStore` (C1) and experimental
`MdbxCanonicalFlatStorage` on identical logical documents and operations.
The v2 harness measures point lookup, outline, selected leaf subtree, one
text edit, three subsequent edits, historical full render and close/reopen.
It uses an unrecorded warmup pair and alternates backend order. Quantiles use
linear interpolation at `q * (n - 1)`; all declared p50/p95 fields must replay
from the complete, unique, correctly hashed raw matrix.

The current fixture is `balanced_binary_preorder_v1`, with fanout at most two,
at 64, 1024 and 16384 blocks. Headings and paragraphs have deterministic IDs
and text; both backends receive the same initial revision and edit sequence.
This is not a wide-sibling, shared-hot or representative production corpus.

Counters observe actual canonical adapter table calls and value bytes,
new block-version writes, codec timings, mapping calls/time, process CPU and
wall time. Mapping-internal DBI operations are not canonical value counters.
`environment_bytes` is `std::filesystem::file_size`: apparent/logical data-file
length, not live payload, allocated disk blocks, page I/O or physical write
amplification. The receipt's legacy provenance label
`allocated_file_size_not_live_payload_bytes` refers only to that file length.
Allocations, page/disk I/O and codec-size estimates are not measured.

## Observations and unsuccessful runs

- Source `d187ce98a39b71aa231d35f78176fa0b80d12ded` used one wide sibling
  list. The first flat 16384-block warmup was stopped after over 12 minutes
  of process CPU. No complete raw matrix or successful receipt was produced.
- Source `eca96eeb6fbbc34c2ee3c527d8772c13e4868e2d` used the balanced
  fixture and nine recorded repeats. The diagnostic run was stopped when
  its observed large-corpus cost threatened the external runtime bound.
- Source `62c3dde2ddae93dc280f4379be9c188083313983`, balanced fixture,
  five recorded repeats, reached the 1800-second external timeout before
  completing the 16384-block matrix. The launch runner recorded `FAIL`, not
  completed evidence. An earlier five-repeat setup was explicitly stopped
  to remove uncertainty about concurrent rebuild/launch provenance.

Those runs originally retained samples only at full completion. Consequently
there are no published p50/p95 numbers from them: process progress is not an
aggregate observation. The follow-up harness now flushes completed raw sample
groups, but a partial matrix still cannot pass the validator or receive a
completed receipt. Failed runs retain their diagnostic fixture directories;
they are generated local artifacts, not golden fixtures or upgrade evidence.

The shared domain edit/validation path contains repeated linear searches.
Source inspection is consistent with substantial superlinear cost, including
wide-sibling cost in `common_sibling_ordinal`; this is not a profiler-confirmed
allocation of the observed runtime. Neither changing the fixture shape nor
bounded block payload writes establishes bounded whole-operation latency.

## Reproduction and remaining gate

Use a clean committed source, configure and build the Release benchmark from
that same commit, and run:

```sh
python tools/run-mdbx-canonical-comparison.py \
  --executable BUILD/bin/agent_memory_mdbx_canonical_flat_benchmark \
  --raw .artifacts/canonical-comparison/raw.tsv \
  --receipt .artifacts/canonical-comparison/receipt.json \
  --repeats 5 --timeout 1800
```

Windows adds `.exe`. Output paths must be fresh. The runner verifies source
and binary stability and the configure-time source stamp, then replays the
receipt. This is procedural provenance, not a cryptographic reproducible-build
proof. Runner controls cover stale/dirty source, binary changes, timeout,
worker/validator failure, malformed receipts and artifact ownership.

A complete matched run, preserved raw observations and independently replayed
aggregates remain required for the performance gate. A larger external time
budget or a separately reviewed domain-editor optimization may make it
feasible. Keep #663 experimental/Draft until the chosen evidence is obtained;
do not infer storage correctness failure or production suitability from the
timed-out synthetic comparison alone.
