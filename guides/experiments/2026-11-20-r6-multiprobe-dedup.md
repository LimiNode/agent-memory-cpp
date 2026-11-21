# R6 production-shaped MultiProbe deduplication

Date: 2026-11-20. Status: **PREREGISTERED, NOT_EXECUTED**.
This research continuation does not change the production index, API, defaults,
dependencies, persisted schema or storage workstream. No merge is authorized.

## Question and source boundary

Can a query-local sparse visited structure remove the O(N) initialization of
`seen(N)` without changing the complete bounded MultiProbe search contract?
This is not a new MIH algorithm or a container-only microbenchmark. Existing
MIH generation-array deduplicators are prior art, not a contribution here.

The source baseline is `main@0a43df571d4e2d5ad29de2c2cd3b00006637e248`.
Before freezing this experiment, the fetched `main` advanced to
`cf054590b6a24a616bc23b03aa534fd6befbcefb`. The MultiProbe header/source
and Hamming distance implementation are unchanged between those revisions.
Keep the declared baseline rather than attributing this experiment to the
newer, unmeasured repository revision.
The following bytes were read and SHA-256 checked before implementation:

| Source | SHA-256 |
| --- | --- |
| `src/agent_memory/index/MultiProbeHammingIndex.cpp` | `3a87d4f0fae3aebe3715dece107fb0ea92b6058286d53e2e0debe0221d018b46` |
| `src/agent_memory/index/MultiProbeHammingIndex.hpp` | `5457b8973ac4d9da22b50d466a8d0e2ce6bad6254098ae16cef0cd921e5093f5` |

Graph discovery returned no matching MultiProbe nodes, so the source/header
and maintained `hamming_hot_path.cpp` control were read directly. The current
`search()` delegates to `search_with_diagnostics()`. That implementation marks
a position seen **before** metadata filtering, probes all tables for a radius
before considering early stopping, and ranks the accepted union by full
Hamming distance followed by `ChunkId`. `erase()` swaps the last record into
the removed position. None of these semantics may change.

K8 geometry/memberships remain unavailable; recovered alternate IVF bytes do
not satisfy the required disjoint quality protocol. Their qualification is
retained in `.artifacts/r6-batch2-input-qualification/qualification.md` under
the canonical checkout. Neither blocked experiment is replaced by invented
data. This independent fallback uses explicitly synthetic binary signatures.

## Four fixed arms

1. Actual `agent_memory::MultiProbeHammingIndex`, linked from `agent_memory`.
2. A generated private **unchanged-dense** copy as a source-equivalence control.
3. The same private copy with per-query `std::unordered_set<std::size_t>`.
4. The same private copy with a compact query-local open-address visited set.

No shared mutable state, retained reader workspace, cached generations or
post-timing choice of another algorithm is permitted. Generation-array controls
are omitted from this first matrix because they change the reader-workspace
contract and retain O(N) memory; this omission is not a negative result.

Generate copies only into the build directory from the pinned production
header/source. The generator must reject an unexpected file digest, missing
or repeated class/body/dedup sentinel, or unexpected substitution count. It
may rename the class and matching constructor/qualified-method tokens, reuse
existing value types from the original header, and substitute only the visited
declaration and first-visit test/update. Retain the generated source and its
digest. A reverse-transform check must recover the original class/body outside
those named substitutions. No copied scoring, probing or update implementation
is maintained by hand and no production file is edited.
Compile every generated class in its own translation unit with the same
optimization and non-LTO call boundary as the actual production baseline.
Keep one profile's four indexes resident at a time, not all profile indexes.

Both alternatives return `true` exactly once for each first-seen position.
The sparse set starts query-local and charges reserve, node allocation, growth
and destruction. The compact set stores positions with `size_t::max()` as an
empty sentinel; indexed positions must exclude that value. Capacity starts at
16, remains a power of two, doubles before exceeding one-half occupancy, and
checks arithmetic overflow. Growth rehashes into a new local buffer before
swapping; a failed allocation leaves no persistent index mutation or partial
result. Use a fixed integer hash, not a query-tuned policy. Every rehash and
buffer release belongs to the measured call. No O(N) preallocation is hidden
outside the alternatives' timers.

## Frozen synthetic matrix

There are 24 profiles: widths `{128, 256}` x N `{4096, 262144}` x the three
workloads below x metadata filter `{none, cohort == "0"}`. Each stored record
has `cohort = decimal(record_ordinal % 16)`. Keep eight queries per profile and
`limit=10`, `candidate_multiplier=64`, `minimum_candidate_count=128`,
`table_count=8`. The target remains exactly the production clamped target;
it is a stopping target, not a hard candidate cap.

| Workload | bits/table | Maximum radius | Signature/query generation |
| --- | ---: | ---: | --- |
| Sparse uniform | 12 | 0 | Uniform raw engine words; independent query words |
| Broad uniform | 4 | 1 | Uniform raw engine words; independent query words |
| Projected-key skew | 12 | 1 | Seven of every eight records share zero projected bits; other bits remain uniform; queries share those projected bits |

Use `std::mt19937_64` raw outputs, not implementation-dependent distribution
adapters: corpus seed `20261122`, query seed `20261123`. Apply the production
projection positions when clearing skewed projected bits. IDs are stable
zero-padded ordinal strings. Reset both engines per signature width, before
each profile, so both N values share a corpus prefix and filter-only profiles
share identical signatures/queries. Skew modifies exactly ordinals whose
`ordinal % 8 != 7`; in particular `cohort == "0"` records are all in the skewed
majority. This correlation is intentional and reported, not an independent
filter/cluster experiment. Hash a canonical little-endian encoding of all
signature words, IDs, metadata, options and queries; require GNU/MSVC fixture
digests to agree. These profiles exercise sparse, broad and skewed discovery,
not a representative request-frequency distribution or held-out retrieval set.
Do not tune the matrix, thresholds or hash after observing timings.

## Semantic evidence and negative controls

Before timing, compare all full result IDs, ordered Hamming distances, copied
metadata and exposed `candidate_count`, `probed_bucket_count`, and
`visited_posting_count` against the actual production class.

The production API does **not** expose discovered candidate identities. A
separate intrusive traced dense copy must first match actual production
results/counters. Its trace then binds each alternative's ordered bucket
lookups, visited postings, first-seen positions before filtering, accepted
positions, full distances and radius stop. Do not describe this as a directly
observed production trace. Three independently derived literal small-fixture
goldens check projection, radius enumeration, metadata selection, Hamming
distances and distance/ID tie order. The timed path contains no trace
construction or trace hashing.
Hashing required by sparse/compact deduplication remains inside the stopwatch.

Correctness fixtures also cover empty index, zero limit bypassing invalid
identity validation, rejected nonzero invalid identity/width, radii 0/1/2,
ties, no accepted candidates, all candidates, repeated posting discovery,
same-key and changed-key replacements, non-last erase/swap, reinsertion,
clear/rebuild, and existing deterministic insertion fault points. Force
allocation failure in a separate diagnostic run: reject the query and verify
the original index remains searchable; a subsequent normal search must pass.
Exercise four concurrent independent const readers, never concurrent writers.
All visited storage is call-local, so stale/reused workspace is disallowed by
construction; repeated searches after lifecycle changes must confirm it.
Reopen/crash recovery are `NOT_APPLICABLE` for this in-memory index, not passed.

The independent auditor must reject duplicate or missing candidate evidence,
an altered distance, changed probe/stop trace, wrong tie order, stale
position evidence after erase/swap, missing samples, false capacity/work
accounting and input/source/binary digest mismatches. Include positive controls
alongside every mutation family. A validator self-test is not a native oracle
execution or a performance run.

## Measurement, memory and stopping rules

Measure the complete uninstrumented `search()` call, including local visited
initialization, probing, filtering, full Hamming scoring, selection, copied
result metadata and local scratch destruction. Returned-result destruction is
outside the stopwatch equally for every arm. Build/generation/input preparation
are outside search timing and reported separately. The dense-copy control must
remain visible in all tables; any code-generation cost discrepancy must be
discussed before attributing a difference to deduplication.

Use one warmup per query/arm, then five repeats. Randomize the four arms within
each matched query/repeat trial using `std::mt19937` seed `20261124` and raw
modulo Fisher-Yates. Retain 960 matched trials / 3840 rows and 768 warmup
executions per compiler. Record first search separately before warmup; do not
mix it into steady-state quantiles. Report nearest-rank p50/p95 descriptively:
repeats are not independent queries and eight synthetic queries do not
establish stable tails or production latency.

Run intrusive work/trace/allocation diagnostics separately. Account for bucket
lookups, raw postings, unique positions before filters, accepted candidates,
Hamming evaluations, duplicates, visited capacity, allocation requests and
growth. Distinguish vector/bucket payload, allocator-requested bytes and process
peak RSS. Use independent per-arm source-derived minima and actual allocation
observations where available; matching two reports is not proof of correct
memory accounting. Unavailable peak-live allocation or RSS measurements remain
`NOT_MEASURED`. No Pareto claim may depend on treating an unavailable memory
metric as zero. Four full indexes must be accounted for in process memory, not
attributed to one query's workspace.

First validate GNU and MSVC Release C++17 correctness and source generation.
The targets are opt-in with `AGENT_MEMORY_BUILD_BENCHMARKS=ON` and
`AGENT_MEMORY_BUILD_MULTIPROBE_DEDUP_RESEARCH=ON`; the latter defaults to OFF
so pinned historical sources do not constrain unrelated benchmark consumers.
The producer checks effective Release optimization parity and absence of LTO;
compile each variant separately, not through a unity build.
Freeze one measured commit before running either compiler. Retain immutable
packages under canonical `.artifacts/r6-multiprobe-dedup/` with executable,
generated source, producer, compiler commands, fixture/source identities,
raw rows, diagnostics, audit and SHA-256 receipt. Builds belong under canonical
`.build/`, not inside the worktree. Do not overlap builds or measurements with
the ADC corrective timings. Public redistribution and stable evidence
publication require their own checks; a local package is not public replay.

Any semantic mismatch stops that arm as `REJECTED`. With valid parity, require
at least 10% full-search p50 improvement, no descriptive p95 regression and no
measured memory-budget regression in the claimed profiles on both compilers.
The comparison must beat both actual production and the unchanged-dense copy.
Otherwise keep `KEEP_CURRENT`; do not widen the search for a positive result.
A favorable synthetic tradeoff is at most `CONDITIONAL`, never production
qualification. Report dense/skew regressions and sparse wins together rather
than averaging them into an adoption claim. K8/IVF remain `NOT_EXECUTED` until
their actual prerequisites are satisfied.

## Actual result and next gate

The bounded implementation and local correctness checks are complete on the
uncommitted research source with C++ SHA-256
`c0fd5f26ff2f072f31079bef461ec5d6321472da358b08167a4a36c43425b4bc`.
This is not a measured commit attribution. GNU 15.2 and MSVC 19.44.35228.0
both pass 121 normal native controls, three literal goldens, 4096 compact-set
positions, and four concurrent readers per shared index. Additional allocation
failure/trace controls pass 309 cases on GNU and 334 on MSVC; different standard
library allocation counts explain the different failure-point sweep lengths.
The controls include widths 128/256, radii 0/1/2 and metadata filtering.
Focused CTest passes 4/4 on each compiler: normal native, diagnostic native,
source-generator and Python-auditor controls. The generator rejects four source
corruptions; the independent validator rejects 52 corruptions. PowerShell syntax
and actual GNU/MSVC Release optimization-parity checks pass.

The diagnostic observer uses an out-of-band, fixed 1,048,576-entry pointer/size
table allocated with `calloc` before observations. Its 16 MiB x64 bookkeeping
is excluded from query allocation payload, not silently counted as zero process
memory. It is released after observations. Allocation pointers remain ordinary
`malloc`/`free` pointers across the shared-runtime boundary; no prefix header
is added. Aligned allocation and process RSS remain outside the observation
scope. This observer and trace code are absent from the normal timing target.

No full matrix, generated corpus replay, performance run, measured decision,
commit or PR publication has executed yet for this research line. Next:
independent code review, one clean frozen source, then matched GNU/MSVC
execution after the ADC timings finish. Update the
[existing algorithm registry](2026-11-18-algorithm-research-sweep.md)
with the resulting limited decision; do not create a second registry.
