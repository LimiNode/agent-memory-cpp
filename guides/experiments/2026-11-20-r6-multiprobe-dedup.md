# R6 production-shaped MultiProbe deduplication

Preregistered: 2026-11-20. Executed: 2026-11-21. Status: **EXECUTED**.
Research decision: **CONDITIONAL** in two synthetic profiles; production
default decision: **KEEP_CURRENT**.
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

## Execution, correctness and source attribution

Draft PR: [#679](https://github.com/LimiNode/agent-memory-cpp/pull/679).
Both compilers measured the same clean commit
`fdc21558678e2985d260824f7929d89ceb3bd276`, C++ SHA-256
`c0fd5f26ff2f072f31079bef461ec5d6321472da358b08167a4a36c43425b4bc`.
The later documentation/CI correction is not a new measured source. Production
source, generated variants, scoring, matrix and receipts remain unchanged.
GNU 15.2.0 and MSVC 19.44.35228.0 each completed exactly 3840 samples,
960 matched trials, 768 warmups and 768 separate diagnostic rows. All full
results, metadata and exposed counters match production; all three private
candidate/probe/distance traces match in memory. Cross-compiler fixture hashes
and all 192 production reference cases agree. No rerun or rescue arm was used.

Each compiler passes 121 normal native controls, three literal goldens,
4096 compact-set positions and four concurrent readers per shared index.
Diagnostic allocation/fault/trace controls pass 309 cases on GNU and 334 on
MSVC; standard-library allocation counts account for the different failure-point
sweep lengths. Controls cover widths 128/256, radii 0/1/2 and metadata filters.
Focused CTest is 4/4 on each compiler: normal native, diagnostic native,
source-generator and auditor controls. The generator rejects four corruptions;
the auditor rejects 52. PowerShell syntax and effective GNU/MSVC Release
optimization/no-LTO/separate-translation-unit checks pass.

The diagnostic observer uses an out-of-band, fixed 1,048,576-entry pointer/size
table allocated with `calloc` before observations. Its 16 MiB x64 bookkeeping
is excluded from query allocation payload, not silently counted as zero process
memory. It is released after observations. Allocation pointers remain ordinary
`malloc`/`free` pointers across the shared-runtime boundary; no prefix header
is added. Aligned allocation and process RSS remain outside the observation
scope. This observer and trace code are absent from the normal timing target.

The original Windows R4 CI configure rejected CRLF checkout bytes before
compilation (`production cpp digest changed`). The final CI correction sets
`core.autocrlf=false` only on the ephemeral Windows runner before checkout.
It does not normalize or weaken the generator's byte-exact hash gate. An
additional in-memory check accepts LF/LF and rejects CPP-only, HPP-only and
both-file CRLF conversion. These checks do not alter the measured generator.

## Full-search result

Only the compact set in profiles 18 and 19 passes the preregistered latency
and ordinary-allocation-payload gates against **both** controls on **both**
compilers. These are width 256, N=262144, sparse uniform radius-0 discovery.
Times below are descriptive milliseconds; filter means `cohort == "0"`.

| Compiler / filter | Production p50 | Dense-copy p50 | Sparse-set p50 | Compact-set p50 | Production / compact p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| GNU / none | 0.0632 | 0.0633 | 0.0779 | 0.0434 | 0.1038 / 0.0588 |
| GNU / cohort | 0.1264 | 0.1263 | 0.1343 | 0.0970 | 0.1694 / 0.1255 |
| MSVC / none | 0.0697 | 0.0711 | 0.0762 | 0.0468 | 0.1566 / 0.0768 |
| MSVC / cohort | 0.1353 | 0.1333 | 0.1404 | 0.1088 | 0.1675 / 0.1284 |

That is 31.3%/32.9% lower p50 without a filter and 23.3%/19.6% lower with
the filter on GNU/MSVC respectively. The width-128 large unfiltered case
passes only on MSVC, not GNU, so it is not a cross-compiler win. No small-N
case passes both compilers. The unordered set passes no profile.

All profiles remain visible below. Each entry is compact/production
`p50 / p95`; lower is better. The decision additionally checks the dense copy
and all eight per-query allocation peaks, not just these rounded ratios.

| Width / N | Workload / filter | GNU ratio | MSVC ratio | Cross-compiler decision |
| --- | --- | ---: | ---: | --- |
| 128 / 4096 | sparse / none | 1.024 / 1.020 | 1.077 / 1.000 | KEEP_CURRENT |
| 128 / 4096 | sparse / cohort | 0.964 / 1.091 | 1.032 / 1.077 | KEEP_CURRENT |
| 128 / 4096 | broad / none | 2.842 / 3.350 | 2.367 / 2.365 | KEEP_CURRENT |
| 128 / 4096 | broad / cohort | 1.406 / 1.355 | 1.138 / 1.178 | KEEP_CURRENT |
| 128 / 4096 | skew / none | 3.372 / 3.329 | 3.190 / 3.120 | KEEP_CURRENT |
| 128 / 4096 | skew / cohort | 2.241 / 2.111 | 2.017 / 1.538 | KEEP_CURRENT |
| 128 / 262144 | sparse / none | 0.979 / 0.847 | 0.854 / 0.887 | KEEP_CURRENT |
| 128 / 262144 | sparse / cohort | 0.984 / 0.969 | 0.959 / 0.987 | KEEP_CURRENT |
| 128 / 262144 | broad / none | 2.596 / 2.535 | 2.390 / 2.273 | KEEP_CURRENT |
| 128 / 262144 | broad / cohort | 1.197 / 1.175 | 1.130 / 1.126 | KEEP_CURRENT |
| 128 / 262144 | skew / none | 4.177 / 3.841 | 4.385 / 5.019 | KEEP_CURRENT |
| 128 / 262144 | skew / cohort | 2.990 / 2.978 | 2.367 / 2.994 | KEEP_CURRENT |
| 256 / 4096 | sparse / none | 0.939 / 0.977 | 0.978 / 0.891 | KEEP_CURRENT |
| 256 / 4096 | sparse / cohort | 0.952 / 1.107 | 1.000 / 1.000 | KEEP_CURRENT |
| 256 / 4096 | broad / none | 2.591 / 2.154 | 2.246 / 2.530 | KEEP_CURRENT |
| 256 / 4096 | broad / cohort | 1.599 / 1.293 | 1.142 / 1.136 | KEEP_CURRENT |
| 256 / 4096 | skew / none | 3.177 / 3.081 | 3.045 / 3.905 | KEEP_CURRENT |
| 256 / 4096 | skew / cohort | 2.338 / 2.092 | 1.887 / 1.777 | KEEP_CURRENT |
| 256 / 262144 | sparse / none | 0.687 / 0.566 | 0.671 / 0.490 | CONDITIONAL |
| 256 / 262144 | sparse / cohort | 0.767 / 0.741 | 0.804 / 0.767 | CONDITIONAL |
| 256 / 262144 | broad / none | 2.237 / 2.275 | 1.866 / 1.484 | KEEP_CURRENT |
| 256 / 262144 | broad / cohort | 1.205 / 1.202 | 1.130 / 1.269 | KEEP_CURRENT |
| 256 / 262144 | skew / none | 3.771 / 3.799 | 4.475 / 4.940 | KEEP_CURRENT |
| 256 / 262144 | skew / cohort | 2.939 / 3.025 | 2.404 / 2.491 | KEEP_CURRENT |

The dense copy is an essential control, not a presumed identical timer.
Its p50 differs by as much as 15.1% from production in one small filtered
skew case; in the two passing profiles the discrepancy is at most 2.1%.
Therefore no result is selected against the more favorable control alone.
Across all profiles, unordered-set p50 ratios are 1.06–8.34 on GNU and
1.04–16.37 on MSVC; compact ratios are 0.687–4.18 and 0.671–4.48.

## Physical work, allocation cost and limits

In the two passing profiles each query visits 497–536 postings in eight
bucket lookups and encounters 494–536 distinct positions. Without filtering,
all those positions receive full Hamming scoring; with filtering only 26–45
are scored. The first-seen-before-filter contract is unchanged. Removing the
262144-byte dense initialization is useful here without changing candidate
work. These observations are not a measured isolated initialization timer.

At width 256 / large N, broad unfiltered search instead visits
130745–132135 postings and scores 105471–106138 positions. Projected skew
visits 1835081 postings, encounters 229449 positions and scores all of them
without filtering or 16384 with filtering. All arms perform the same work;
hashing/growth/probing adds cost rather than reducing Hamming evaluations.
At that skew, compact p50 is 3.77x/4.48x slower unfiltered on GNU/MSVC.

| Passing profile / compiler | Production = dense peak requested-live bytes | Compact peak requested-live bytes | Maximum allocation requests, production / compact |
| --- | ---: | ---: | ---: |
| none / GNU | 277680 | 31920 | 14 / 21 |
| cohort / GNU | 269824 | 29696 | 14 / 21 |
| none / MSVC | 278533 | 32773 | 34 / 41 |
| cohort / MSVC | 270638 | 29813 | 34 / 41 |

The compact arm uses fewer requested bytes, not fewer allocation calls.
For large filtered skew it instead peaks at 6373376/6379053 bytes on
GNU/MSVC versus production 689968/658197 bytes, so memory also regresses.
These are separate ordinary-new payload observations, not allocator overhead,
aligned allocation or process RSS. Four full indexes plus the synthetic input
are resident per profile; they are outside the query allocation window and
are not treated as zero process memory. Process RSS and process CPU time are
`NOT_MEASURED`; no whole-process memory Pareto claim is made.

Sequential index builds are outside search timing. Across profiles, production
builds range 6.92–1157.58 ms on GNU and 13.15–1862.60 ms on MSVC; compact
builds range 6.41–1200.94 ms and 11.62–1881.22 ms. Build order is not randomized,
so these are retained setup costs, not a build-speed claim. First-search timings
are retained separately for every arm/profile, never pooled into steady-state
quantiles. The machine is Windows 10.0.26100, Xeon E5-2696 v3 @ 2.30 GHz,
18 physical / 36 logical cores, 137272254464 bytes reported RAM. GNU uses
`-O3`; MSVC uses Release `MaxSpeed|AnySuitable||`; both are verified non-LTO.
Timing/generation/build/preflight took 107.47 seconds on GNU and 209.10 seconds
on MSVC in their separately recorded execution windows. These elapsed windows
are not pure search time or process CPU measurements.

## Retention, qualification and decision

Immutable local packages are under canonical `.artifacts/r6-multiprobe-dedup/`.
Each retains both executables, source/generated bytes, build provenance,
native controls, 48 fixture files, raw timing/diagnostics and manifest sidecar.
The fixture files alone occupy 424134144 bytes per package. No external
dataset, fitting, download, dependency change or evidence release was used.

| Package | Manifest SHA-256 | Measured executable SHA-256 |
| --- | --- | --- |
| `fdc21558-gnu-v1` | `81ccf97f57f5e9ea8cc61d0696999bbbe211cf33c74c7714fff90c94bab2e7c1` | `fe48cf9f5a6c9289f190d1e760b1745b42eb2986a42ea03cb8851812faa6a3fe` |
| `fdc21558-msvc-v1` | `52081910df6d0ac4835187c6090b7fbadc14b3ca3888cfa1465887b6e3287e36` | `9cbe1d678946bf4444f9669238acbf10f92bc14eb880434b9c4f71bd43395c1c` |

Replay the retained auditor with `--directory <package> --repository <checkout>`
`--expected-source fdc21558678e2985d260824f7929d89ceb3bd276`
`--verify-fixtures --mutation-self-test`. This checks every retained size/hash,
source blob/tree, compiler ID/version, generated byte and synthetic record,
then exercises 52 rejection controls. Local retention and audit are not public
artifact publication or independent held-out quality replication.

Owner and independent retained-auditor replays both pass on both complete
packages, including full fixture verification and all 52 rejection controls.
The independent reviewer also recomputed all 96 compiler/profile/alternative
decision gates separately from the auditor and confirmed the same outcomes.
The review found no research blocker; it does not authorize production use.

The input qualification matrix remains at canonical
`.artifacts/r6-batch2-input-qualification/qualification.json`, SHA-256
`5b28c851770d38703ea9abbdd3cbb88490ac09f284d74687741da4475713bf3f`.
K8 is `MISSING` for required normalized prototypes and complete memberships;
alternate IVF4096 is `AVAILABLE` for historical mechanics but its training on
base/evaluation documents is `INVALID` for the requested disjoint quality
protocol. Original IVF256 model/binding is incomplete. Both requested quality
experiments remain `NOT_EXECUTED`, with no invented synthetic replacement.

The bounded fallback is complete: compact dedup has a real full-search
**CONDITIONAL** benefit in two synthetic profiles, with significant broad/skew
losses elsewhere. Unordered-set replacement is **KEEP_CURRENT** throughout.
Keep production unchanged. Before any implementation proposal, obtain a
representative query/candidate/filter distribution and a frozen applied-quality
corpus, then preregister a separate product-path validation; do not choose a
dispatch threshold from these exposed synthetic timings. Keep this PR Draft,
request independent review, and update only the
[existing registry](2026-11-18-algorithm-research-sweep.md).
