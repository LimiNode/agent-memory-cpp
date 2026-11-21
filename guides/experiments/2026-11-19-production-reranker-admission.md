# Production-shaped reranker admission qualification

## Purpose and boundary

This experiment is a separate R5.2 qualification follow-up to PR #633. It
does not modify production defaults, public APIs, ABI, persisted formats, or
the implementation merged by PR #625. The executable is a research harness
that exercises generated, source-bound copies of the actual
`HybridRetrievalEngine::retrieve()` procedure with a fixture index and the same
deterministic reranker. Production source and headers remain byte-identical.

The three arms are `linear_scan`, `sorted_identity_index`, and
`hash_identity_index`. Each arm executes the same sequence:

1. construct the fixture and the engine;
2. apply the same mandatory `scope=documents` metadata filter;
3. call `HybridRetrievalEngine::retrieve()`;
4. run exactly one admission check, including identity and protected-metadata
   checks; and
5. run the unchanged production response materialization.

`generate-production-reranker-variants.py` first checks the LF-normalized
SHA-256 of both production source and header. It creates three uninstrumented
renamed class copies and three separately instrumented copies under the build
directory. The uninstrumented linear procedure is byte-identical after
undoing the class rename; the other two replace only `admit_reranked_hits()`.
All generated copies are compiled into one executable so the arm order can be
balanced within each repeat. The self-test also compares the generated linear
arm with the unchanged production class linked from the static library.

The sorted and hash indexes are constructed inside each measured retrieval,
so their construction cost is included in the end-to-end samples. The linear
arm has no auxiliary index. Sorted/hash do not run an additional linear gate;
their parity and fail-closed controls are mandatory, not inferred from such a
gate. This is an experimental replacement inside generated sources only.

The primary timer and allocation counters surround only `engine.retrieve()`.
Fixture/index/engine construction and the diagnostic return-value copy are
excluded. The fixture index's `last_query = query` observation happens inside
`search()` and is included equally in all arms; it is instrumentation overhead,
not a production index cost.
The same fixture is prepared immediately before each call, so these are warm
synthetic queries, not independent cold requests. A separately named
`setup_and_retrieve_timing` retains the original wider scope; its time must be
at least the nested query time. Break-even uses only the primary query samples.

## Frozen fixture and measurements

Candidate counts are `10`, `50`, `100`, `500`, `1000`, and `2000`. The first
three cases cover the small regime; the latter three cover the high-candidate
regime and expose a possible break-even. Metadata widths are `2`, `8`, and
`32` additional metadata keys (plus `scope` and `owner`); every candidate count
is crossed with every width to avoid confounding size with metadata cost.
Each case uses two warmups and twelve repeated samples, with all six arm-order
permutations used twice after warmup. `sample_count`
describes repeated executions of one fixture, not independent requests.

The receipt records raw samples, summary statistics, allocation counts and
bytes, parity, production-path coverage, materialization coverage, and the
bounded break-even definition. A lower mean on this fixture is not a
production latency claim and does not select an implementation.

## Negative controls

Every arm must fail closed through the same retrieval path for:

- foreign chunk;
- foreign resource;
- mismatched section;
- protected metadata mutation;
- duplicate identity;
- NaN, positive infinity, and negative infinity scores;
- untrusted result tier and enrichment level; and
- oversized output.

The executable self-test checks the empty response and proves that the
indexed gate was actually called. Positive checks compare order, all lexical
fields and scores, object IDs, metadata, and enrichment after materialization.
The fixture includes equal chunk IDs with distinct tiers and a filtered-out
record before valid candidates.

## Reproduction

```text
python -m pip install -r tools/requirements-tools.txt
cmake -S . -B build-reranker-r52 \
  -DCMAKE_BUILD_TYPE=Release \
  -DAGENT_MEMORY_BUILD_BENCHMARKS=ON \
  -DAGENT_MEMORY_BUILD_TESTS=ON \
  -DAGENT_MEMORY_BUILD_RESEARCH_TESTS=ON \
  -DAGENT_MEMORY_PRODUCTION_RERANKER_EXPECTED_PR_HEAD_SHA=<PR_HEAD_SHA>
cmake --build build-reranker-r52 --config Release \
  --target agent-memory-production-reranker-admission
build-reranker-r52/tools/agent-memory-bench/agent-memory-production-reranker-admission \
  build-reranker-r52/production-reranker-admission.json
python tools/agent-memory-bench/validate-production-reranker-admission.py --self-test
```

The CI job additionally validates source/fixture digests, all raw timing
summaries, balanced execution orders, and the computed first observed
mean-based crossover per metadata width. It validates the receipt and its SHA-256 sidecar against
the exact PR HEAD and uploads the artifact. `QUALIFICATION_ONLY` is the only
supported interpretation of this experiment until a separately reviewed
production design exists.

The validator requires the compiler/version, C++17, Release configuration,
platform, processor and pointer width. Each CI arm additionally supplies its
expected compiler and platform; missing/fake environment and cross-platform
mislabeling are negative controls, not accepted provenance.

Both Ubuntu/GNU and Windows/MSVC compile and execute the actual new target in
a required matrix lane. Generic core-library Windows checks are not counted
as execution evidence for this benchmark. Production defaults remain
`KEEP CURRENT`; any candidate crossover is `CONDITIONAL` on the recorded
synthetic candidate count/metadata regime, not authorization for activation.

## Executed local decision experiment

The clean-source run at `df17946cb6cbad36f0da25caa6823dc67d3f41d7`
used actual MSVC 19.44.35228.0, Release/C++17, on Windows AMD64 and an
Intel Xeon E5-2696 v3. Three focused CTest passed, followed by all 18 cases
(648 retained query samples), source/generated-transform validation and the
raw-receipt SHA-256 sidecar check. The compact
[receipt](2026-11-19-r5-reranker-admission.receipt.json) and adjacent `.sha256`
preserve this tested source; later documentation commits do not relabel it.
Full raw data remain outside Git and in fresh per-platform CI artifacts.

Primary `retrieve()` p50 in milliseconds, including per-query lookup
construction and materialization but excluding fixture preparation:

| Candidates | Extra metadata keys | Linear | Sorted | Hash |
| ---: | ---: | ---: | ---: | ---: |
| 10 | 2 | 0.0340 | 0.0393 | 0.0357 |
| 100 | 2 | 0.5745 | 0.5828 | 0.5297 |
| 500 | 2 | 4.0972 | 3.0347 | 2.6965 |
| 2000 | 2 | 44.2335 | 13.7050 | 11.4774 |
| 2000 | 32 | 102.9341 | 75.3033 | 71.9109 |

First tested candidate count with mean time no greater than linear:

| Extra metadata keys | Sorted | Hash |
| ---: | ---: | ---: |
| 2 | 500 | 50 |
| 8 | 500 | 100 |
| 32 | 100 | 100 |

These are observed crossovers, not stable thresholds. For example, metadata
copying reduces the relative benefit at 32 keys; shared-host load affects
small differences. The fixture does not establish the production distribution
of candidate counts or metadata widths. **CONDITIONAL** applies only to
continuing indexed-gate qualification in the large-candidate regime;
**KEEP CURRENT** applies to production defaults. No implementation is adopted.

The allocation series counts ordinary C++ `new`/`new[]` requests inside the
primary scope. It is not peak memory or RSS and does not cover direct C or
over-aligned allocation APIs. No stable p99 or independent-request inference
is made from twelve repeated synthetic samples.

## R5 Closure — direct stages and adversarial request shapes (2026-11-20)

The original eighteen-case matrix and twelve uninstrumented repeats remain the
primary matched timing scope. Six extra positive cases use identity-sorted
trusted input at metadata width eight, across the same six candidate counts.
This does not mean the reranker returns sorted output: it reverses the trusted
input in both matrices. All inputs remain explicitly `SYNTHETIC`.

Three additional class copies measure the same retrieval with direct stage
timers: index search, trusted-pool record copy, reranker, whole admission,
lookup construction, validation/identity lookup, trusted-result record copy,
and response materialization. The two record-copy stages include metadata but
are not isolated metadata-container microbenchmarks. The admission timer
contains the construction/validation/trusted-copy timers; these are not added
again when comparing with the full instrumented retrieval timer. Query
preparation, control flow, temporary destruction and timer overhead are not
claimed to be an exhaustive stage partition.

Each instrumented case has one warmup and three retained samples. Per-record
clocks deliberately add overhead; this is diagnostic evidence only and is
never used to select a crossover. A validator checks direct timer nesting,
stage call counts, all six generated-source hashes and the separation from
the uninstrumented primary series. Removing only the declared observation
insertions restores the source-bound copies exactly; production files and
public interfaces remain unchanged. All six renamed classes link alongside
the actual production class in one executable.

The negative matrix now has thirteen controls per arm in both instrumented
and uninstrumented retrieval. It includes all-repeated output identities and
a late duplicate, in addition to the original controls. Thirty-six separately
profiled negative workloads cover 10/100/2000 candidates, widths 2/32, three
arms and both duplicate shapes. All-repeated output rejects at candidate two;
it is not presented as worst-N admission work. Late-duplicate output contains
N-1 valid records followed by a duplicate and must perform N validations and
N-1 trusted copies before returning an empty response.

No real or representative request-frequency log was supplied. Graph discovery
and the checked benchmark/docs sources provide fixture counts, not production
frequencies. The receipt records
`NOT_EXECUTED: REPRESENTATIVE_REQUEST_DISTRIBUTION_UNAVAILABLE`; chosen counts
10/50/100/500/1000/2000 must not be interpreted as a workload distribution.

The allocation counter still observes ordinary `new`/`new[]` requests only.
It has no size accounting at deallocation, so peak live/retained workspace and
process peak RSS remain `NOT_MEASURED`, explicitly distinct from allocated
totals. Adding a dispatcher is `DEFER`: synthetic crossover alone does not
justify additional production policy, branches or state. Production defaults
remain `KEEP CURRENT`.

Receipt schema v2 adds these observations without rewriting historical v1
receipt bytes or SHA-256. Existing dated local numbers above remain evidence
for their original tested source, not results of this corrective.

Configure also records whether the source worktree is clean. Development
builds may still execute, but a dirty-source receipt is rejected as exact-source
evidence. The source manifest includes the target's CMake definitions as well
as the generator, profiler, runner, validator and production seams.

### Executed closure check

The clean Windows/MSVC 19.44.35228.0 Release/C++17 run at
`9c8cd6a4e27496c7494fa715e5a428ab926c9682` passed 3/3 focused CTest,
the complete 18-case primary matrix, six sorted-input cases and 36 measured
duplicate-rejection scenarios. The source/generated hashes, compiler/platform
and sidecar validator passed. Raw receipt SHA-256:
`adbba41dbc5e804056cbea3fd7a00247e42972861d027ab43b9943b199bcbddb`.
This is a local synthetic execution; fresh Linux/Windows CI receipts are
separate source-bound artifacts and are not relabeled as this run.

N=2000, two extra metadata keys; milliseconds, p50:

| Arm | Primary retrieve | Instrumented retrieve | Lookup build | Validation/lookup | Trusted result copy | Response materialization |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Linear | 42.0080 | 45.5461 | 0 | 34.2157 | 2.5376 | 3.2713 |
| Sorted | 12.7589 | 13.3906 | 1.0522 | 1.7710 | 2.0518 | 3.0198 |
| Hash | 11.5729 | 12.9319 | 0.4712 | 0.9605 | 2.2433 | 3.0690 |

The table does not subtract timings or sum independent medians. Instrumented
timers include their own overhead, and stage nesting is checked sample by
sample. The new first observed mean crossovers (sorted/hash) are 500/50 for
width two, 50/50 for width eight and 50/100 for width thirty-two. Their change
from earlier recorded runs reinforces the decision not to turn a synthetic
crossover into a production dispatch threshold.

There are 864 retained uninstrumented samples, 216 positive profile samples
and 108 negative profile samples. They are repeated fixed-fixture executions,
not 1188 independent real requests. All-repeated output fails after two
validations; the late-duplicate path performs N validations and N-1 trusted
copies as claimed. The validator preserves its earlier controls and adds
fifteen closure mutations, including dirty-source evidence, missing profiled
sources, stage nesting/depth, false memory/frequency claims and activation.
