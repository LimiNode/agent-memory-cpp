# C1 and numeric flat storage comparison

Date: 2026-11-20. Research PR #663; correctness prerequisite #662.
Status: full-size comparison completed and replayed; directional local evidence,
not isolated production performance qualification. No activation recommendation.

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

## Completed bounded comparison

The follow-up at exact source
`a7a71807c00571034ccdd9587259e612171123c7` completed within its 3600-second
external bound: 210 unique raw observations, two backends, three corpus sizes,
five recorded repeats and seven workloads, plus the unrecorded warmup pair.
The runner returned 0; the raw matrix, logical result parity and every declared
p50/p95 passed replay. The receipt preserves the measured source, clean-tree
state and executable hash; later dependency/CI synchronization does not relabel
the measurement as a run at a newer HEAD. The source and external trees were
unchanged by that synchronization.

Host: Intel Xeon E5-2696 v3 at 2.30 GHz, 18 cores/36 logical processors,
approximately 128 GiB RAM, Windows 11 Pro AMD64 build 26100. Build: MinGW
GNU 15.2.0, Ninja, C++17 Release, `-O3 -DNDEBUG`, JSON/MDBX ON. The fixture
has no embeddings, query filters, shared writers or production retrieval path.

Single-block edit wall time (milliseconds, linear-interpolated quantiles):

| Blocks | C1 p50 | C1 p95 | Flat p50 | Flat p95 |
| --- | --- | --- | --- | --- |
| 64 | 43.8110 | 53.2055 | 6.8181 | 39.3316 |
| 1024 | 132.1323 | 195.1390 | 177.8489 | 214.5054 |
| 16384 | 26045.6859 | 27870.2962 | 25817.5955 | 38361.6185 |

The counter observations are deterministic across the five repeats:

| Blocks | C1 written value bytes | Flat written value bytes | Flat canonical value reads | Flat new block versions |
| --- | --- | --- | --- | --- |
| 64 | 10079 | 6610 | 68 | 1 |
| 1024 | 166464 | 101608 | 1028 | 1 |
| 16384 | 2800573 | 1687466 | 16388 | 1 |

C1 performs four canonical writes; flat performs three (payload, structure,
head). Flat records three new payload versions for the three subsequent edits.
These bytes exclude mapping-internal DBI operations and physical page writes;
the structure is still rewritten. At 16384 blocks, point lookup reads two
canonical values in either adapter: C1 reads 2664396 bytes and flat 1687667.
Flat outline reads one structure value (1687268 bytes), not zero-cost metadata.
The full edit reads O(V) payloads; the measurements do not support an O(1)
operation or a general latency advantage for flat.

This was not a quiescent machine: native MSVC builds ran concurrently, and a
separate build exhausted E: free space before the benchmark completed. The
benchmark uses `temp_directory_path()` for its databases; all operations and
raw/receipt writes completed, and all evidence checks passed. Nevertheless, CPU/scheduling
and storage-pressure effects were not isolated. Five local repeats and their
interpolated p95 describe this run only; they are not production tail-latency,
throughput, page-I/O or stable speedup evidence. Earlier timeout results remain
valid unsuccessful observations, not superseded successful runs.

### Compact raw retention

This research line explicitly retains exactly two generated evidence files at
`.artifacts/canonical-comparison-v2/`, with a combined 128 KiB budget. The
completed TSV and receipt total 69355 bytes. No database, executable, failed-run
directory or large report is committed. Keep this measured pair immutable;
superseding evidence must use a new name and preserve its source binding.
Future archival/release publication requires separate authorization.
The benchmark CTest profile replays this full retained pair as well as its
generated smoke pair; changing an aggregate or raw observation cannot silently
leave the published evidence outside the contract checks. Exact-path Git
attributes preserve the measured bytes, including the receipt's CRLF endings.

- [Raw TSV](../../.artifacts/canonical-comparison-v2/balanced-5-60min.tsv),
  SHA-256 `8ff5103a866ff105f41d0c46aa192e3abbcfcd44439d22fcb3d96b69dbe464d1`.
- [Receipt](../../.artifacts/canonical-comparison-v2/balanced-5-60min.receipt.json),
  SHA-256 `24c204346406f4f779b4eb1dc282b91b5dc97f87477643c858633032f4773c12`.

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

The completed retained pair replays with the same validator command, using
`.artifacts/canonical-comparison-v2/balanced-5-60min.tsv` and the adjacent
receipt as inputs. It used `--repeats 5 --timeout 3600` instead of the shorter
bound shown above. Completion closes the bounded matrix/replay gate, not
production performance qualification. A quiescent repeat, realistic workloads
and a separately reviewed domain-editor optimization remain follow-up work.
Readiness for review also requires current exact-head CI and dependency checks;
neither successful replay nor earlier timeouts establish production suitability.
