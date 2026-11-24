# AMCE envelope resource qualification

- Date: 2026-11-22.
- Context: corrective working tree of Draft PR #690, based on
  `4ec7866f31e0d0b2e3d2ca1933b27c3e78f767ec`; integrated baseline
  `386e26fe9176d268722894cd9f48df33f3b7bf72`.
- Status: local GNU C++17 resource/latency experiment; not a release gate,
  semantic-admission proof, storage benchmark or production speed guarantee.
- Owner: [AMCE envelope contract, section 4.2](../conversation-event-contract.md#42-event-codec-v1-phase-2-review-contract).
- Runner: [`tools/amce-codec-bench.cpp`](../../tools/amce-codec-bench.cpp).

## Question and expected result

Does preparing each nested AMXR frame once, instead of serializing it in both
size-count and emission passes, reduce repeated work without obscuring its
memory cost? Do default and extended consumer profiles actually encode/decode
the exact 32 MiB frame ceiling? Does changing only the binary output container
establish a performance improvement?

Expected: bounded reuse reduces nested serialization calls but retains more
temporary bytes simultaneously. Both advertised profiles must pass exact-size
qualification. String/vector copy controls must remain separate from the full
codec comparison: the old closed grammar and corrected extensible grammar do
not have identical semantics or bytes, and codec differences cannot be
attributed to the container type alone.

## Setup and measurement boundaries

Host: Windows 11 Pro `10.0.26100`, Intel Xeon E5-2696 v3 at 2.30 GHz,
18 cores / 36 logical processors, 134,054,936 KiB visible physical memory.
GNU `15.2.0`, x86-64, strict C++17, `-O3 -DNDEBUG`; JSON/MDBX disabled in the
linked Release libraries. The runner links the GNU runtimes statically so
Windows DLL allocation boundaries cannot bypass or mismatch its global-new
probe. No new mandatory library dependency is introduced.

Each case runs in three fresh processes. Each operation has five warmups and
101 individual timed samples per process; quantiles use nearest rank. Fixture
construction, qualification round-trip, logging, sample storage and result
destruction are outside operation timing. Field validation is measured
separately. Exact size preflight is included in encoding, **not measured as an
independent phase**: there is no separate public size-preflight API, and the
experiment does not invent one or infer its duration by subtraction.
The corrected runner additionally measures framing-only header inspection;
that operation does not validate the event bodies or expose a permitted
metadata read. It is not a canonical read fast path.

The single-threaded global-new probe records allocation calls, cumulative
requested bytes, peak simultaneously live requested bytes, and live requested
bytes at return. The latter distinguishes the result's dynamically allocated
storage from temporaries; stack/SSO bytes are not counted. A second peak records
the actual malloc requests made by the probe, including its own headers and
alignment padding. Neither metric includes malloc-internal fragmentation,
arbitrary C allocations or allocator-reserved memory. These are **not RSS** or
unqualified native heap-size measurements. The probe adds overhead to timing.

Windows `PeakWorkingSetSize` is reported separately for the whole process,
including fixture construction, qualification, retained inputs/wire buffers,
warmups and instrumentation. It is not a per-operation allocation peak and
must not be subtracted from another process's high-water value.

The host was not CPU-pinned, isolated or power-state controlled; other work
could run concurrently. Values are local directional observations, not stable
cross-machine latency claims. Very short operations approach the host timer's
100 ns granularity.

## Source and binary bindings

The old source checkout is detached at the exact source HEAD above. The
old GNU library SHA-256 is
`affc9735436477c9c9b81fd0361d3740d0c8527b42526f426785c2cb5c1dcf1c`;
the old runner executable is
`9fc21889d07d237b186e0da52ef9b7ade85a8178d062b578e56ae052476e4e26`.

The qualified corrected library is an immutable copied archive, SHA-256
`32fe7e0bdec967ed77c573e8c2a70f26c9aacd1ae167ca78d51e2fc1ccd6b825`.
Its runner executable is
`084c7dc472d5e76fc559212f30c4d6699ba1c8a4aea7a2c680627136ef509526`.
The corrected runner source is
`57aa99e8702ae354ac34ae4dbf4be9ba93dccae5c030792bba89682156307625`.
Both closure executables were built from that same frozen runner source.
These bind measurements to actual binaries, not an assertion that an
uncommitted corrective tree already belongs to the baseline commit.

The corrected codec source SHA-256 is
`35149a1e4ef2483d203079e67bf4b61c4dd84f8095d4329ca149c56769972873`;
`Conversation.cpp` is
`284b01e3c0afcb35607b894587c7f485da9f69c38999b6a2d25059c99f3282b2`.
Earlier exploratory `new-*`, `final-*` and `qualified-*` campaigns remain
retained, but only the frozen `closure-*` outputs supply the tables below.
Both executables were built after the final header/cap fixture additions and
linked to immutable library copies; no runner edits followed their build.

## Reproduction

From the corrected feature worktree, the explicit GNU commands are:

```powershell
g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra -Wpedantic -static-libstdc++ -static-libgcc -static -DAGENT_MEMORY_AMCE_BENCH_LEGACY -I E:/_repoz/agent-memory-cpp/.worktrees/amce-premerge-bench/src tools/amce-codec-bench.cpp E:/_repoz/agent-memory-cpp/.build/amce-premerge-baseline/libagent_memory.a -lpsapi -o E:/_repoz/agent-memory-cpp/.build/amce-premerge-baseline/amce-final-old.exe
g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra -Wpedantic -static-libstdc++ -static-libgcc -static -I src tools/amce-codec-bench.cpp E:/_repoz/agent-memory-cpp/.artifacts/amce-envelope-2026-11-22/libagent_memory-qualified.a -lpsapi -o E:/_repoz/agent-memory-cpp/.build/amce-premerge-baseline/amce-final-new.exe
```

For each `r=1..3`, run each old case `minimal`, `text_1m`, `nested_amxr`,
`combined_phase1_max`, `materialization_1m`, and the same corrected cases plus
`frame_32m`, `extended_frame_32m` and `implementation_caps_32m`:

```powershell
E:/_repoz/agent-memory-cpp/.build/amce-premerge-baseline/amce-final-new.exe --case nested_amxr --samples 101 --warmups 5
```

Capture stdout as `closure-new-<case>-r<r>.jsonl`, and old executable output
as `closure-old-<case>-r<r>.jsonl` under
`E:/_repoz/agent-memory-cpp/.artifacts/amce-envelope-2026-11-22/`.
The configuration line names raw columns; each measurement retains every
timing/allocation sample. Raw outputs and copied libraries remain untracked,
not silently committed as large evidence dumps.

## Results and interpretation

The final source-frozen closure campaign completed **39 process outputs,
132 operation measurements and 13,332 timed samples**, all PASS. Each operation
has 101 samples/five warmups and six raw allocation columns. All three maximum
cases reached exactly **33,554,432 bytes**, including all implementation caps
(text 8 MiB, attachments/links 4096, extensions 1024/8 MiB, binding/locator 65536,
recipe 4096). Each grouped operation has three complete process repetitions;
allocation columns were checked for constancy over all 101 samples.

The manifest `closure-raw-sha256.json` binds 15 `closure-old-*` and 24
`closure-new-*` files by name, byte length and SHA-256. Its SHA-256 is
`88d47100e7ac032eddd24308b391ee493c9bf5582adcb54b6668da9bf131a5a6`.
Every file contains a PASS completion. Earlier exploratory campaigns are
retained separately and do not supply these closure tables.

The following ranges are **three per-process quantiles**, in milliseconds,
not pooled quantiles or confidence intervals:

| Fixture / operation | Codec | Median ms | p95 ms | p99 ms |
| --- | --- | --- | --- | --- |
| 1 MiB text / encode | Old | 0.795-0.839 | 0.910-1.228 | 0.952-1.300 |
| 1 MiB text / encode | Corrected | 0.695-0.721 | 0.876-0.936 | 0.921-0.947 |
| 1 MiB text / decode | Old | 0.826-0.858 | 0.913-0.955 | 0.938-1.155 |
| 1 MiB text / decode | Corrected | 0.762-0.793 | 0.952-0.971 | 0.980-0.997 |
| 258 maximal AMXR refs / encode | Old | 45.785-47.099 | 49.978-50.231 | 50.477-52.818 |
| 258 maximal AMXR refs / encode | Corrected | 41.576-43.070 | 44.768-45.517 | 46.007-47.371 |
| 258 maximal AMXR refs / decode | Old | 34.608-35.880 | 37.345-38.793 | 38.167-41.151 |
| 258 maximal AMXR refs / decode | Corrected | 29.341-30.947 | 31.739-32.989 | 32.839-33.643 |
| Combined Phase1 maxima / encode | Old | 48.974-50.362 | 52.752-53.452 | 53.453-55.936 |
| Combined Phase1 maxima / encode | Corrected | 44.699-46.046 | 47.750-48.093 | 48.144-52.978 |
| Combined Phase1 maxima / decode | Old | 38.037-39.317 | 40.366-42.821 | 41.590-46.106 |
| Combined Phase1 maxima / decode | Corrected | 30.716-31.956 | 34.734-35.376 | 36.070-39.510 |
| 32 MiB default opaque / encode | Corrected | 9.176-9.511 | 10.228-10.357 | 11.339-11.969 |
| 32 MiB default opaque / decode | Corrected | 9.267-9.561 | 9.870-10.518 | 10.972-11.523 |
| 32 MiB extended typed / encode | Corrected | 11.303-11.630 | 11.918-12.332 | 12.909-14.936 |
| 32 MiB extended typed / decode | Corrected | 12.457-12.720 | 12.963-13.381 | 13.575-14.222 |
| 32 MiB implementation caps / encode | Corrected | 15.944-16.123 | 16.613-17.470 | 17.083-20.112 |
| 32 MiB implementation caps / decode | Corrected | 16.140-16.348 | 16.811-17.046 | 17.078-17.826 |

Nested frames change from 16,924,574 to 16,924,594 wire bytes; combined maxima
from 21,186,977 to 21,186,997. They are the same domain inputs, **not the same
wire semantics**. Minimal frames change 67 to 87 bytes; corrected median encode
is 0.7-0.8 microseconds, old 0.6. A prior exploratory campaign showed slower
corrected text-only encoding; the closure run reverses that ordering. This
variation forbids a universal speedup claim.

Allocation counters are raw requested sizes, not rounded allocator sizes:

| Fixture / operation | Codec | New calls | Cumulative requested bytes | Peak live requested bytes | Live requested bytes at return |
| --- | --- | --- | --- | --- | --- |
| Nested refs / encode | Old | 1,289 | 50,769,466 | 16,990,112 | 16,924,575 |
| Nested refs / encode | Corrected | 1,041 | 33,893,675 | 33,849,524 | 16,924,594 |
| Combined maxima / encode | Old | 1,803 | 55,056,994 | 21,252,515 | 21,186,978 |
| Combined maxima / encode | Corrected | 1,555 | 38,181,203 | 38,111,927 | 21,186,997 |
| 32 MiB default opaque / encode | Corrected | 1 | 33,554,432 | 33,554,432 | 33,554,432 |
| 32 MiB default opaque / decode | Corrected | 1 | 33,554,353 | 33,554,353 | 33,554,353 |
| 32 MiB extended typed / encode | Corrected | 2 | 33,554,463 | 33,554,432 | 33,554,432 |
| 32 MiB extended typed / decode | Corrected | 31 | 33,555,022 | 33,555,022 | 33,554,991 |
| 32 MiB implementation caps / encode | Corrected | 12,273 | 34,057,435 | 33,554,432 | 33,554,432 |
| 32 MiB implementation caps / decode | Corrected | 12,283 | 35,309,690 | 34,806,779 | 34,806,687 |

Preparation changes 516 nested AMXR serializations to 258. Cumulative encoder
requests decrease, but **peak live requested bytes nearly double** for nested
references because prepared frames coexist with output. This is a bounded
time/allocation-volume versus peak-memory trade-off, not a free optimization.
Nested decode peaks at 17,254,990 requested bytes in both implementations;
combined decode at 21,524,800. The implementation-cap decoder retains 34,806,687
dynamic requested bytes and peaks at 34,806,779: logical/wire budgets are not
exact physical heap ceilings.

Source-level copy accounting: each nested AMXR is materialized once during
preparation and copied once into output. Unknown payload/extension bytes are
copied once into their owning decoded container and once into re-encoded output.
The Phase1 serializer does not copy its DTO. This counts traversed buffer
boundaries, not instrumented machine copy instructions.

Whole-process high-water working sets are separate from operation allocations:

| Fixture | Codec | Process peak working set bytes |
| --- | --- | --- |
| nested_amxr | Old | 75,882,496-75,886,592 |
| nested_amxr | Corrected | 92,798,976-92,807,168 |
| combined_phase1_max | Old | 93,523,968-93,528,064 |
| combined_phase1_max | Corrected | 110,444,544-110,448,640 |
| frame_32m | Corrected | 138,264,576-138,268,672 |
| extended_frame_32m | Corrected | 138,485,760-138,493,952 |
| implementation_caps_32m | Corrected | 140,861,440-140,869,632 |

These process figures include fixtures, qualification round-trips, retained
inputs/wire buffers and warmups; they are not codec-only charges.

The identical-byte 1MiB materialization control in the corrected executable:

| Container | Median us | p95 us | p99 us | New calls / requested bytes |
| --- | --- | --- | --- | --- |
| `std::string` | 285.000-312.400 | 319.000-367.500 | 350.500-396.300 | 1 / 1,048,577 |
| `std::vector<uint8_t>` | 282.500-315.000 | 317.800-357.400 | 323.000-374.600 | 1 / 1,048,576 |

Ranges overlap. The byte API is justified by semantics/ownership, not a measured
container-speed advantage. Implementation-cap header inspection reports zero
allocations and median/p95/p99 of 0.1 microseconds, at timer granularity. It
validates framing only, never metadata, payload meaning or permissions.

## Limits and next checks

These are allocation-instrumented full codec costs for different wire
semantics, not a same-format optimization proof. The isolated materialization
control uses identical 1 MiB bytes; it does not validate, encode or decode an
event. Default maximum qualification uses an unsupported opaque payload:
successful retention does not qualify its inner schema or semantic admission.
The extended maximum is a typed 4 MiB message with advisory extension bytes,
not proof that arbitrary combinations of independently maximal values fit.
The separate implementation-cap fixture covers all declared independent
field/count ceilings together, padding scope to the 32MiB aggregate boundary.

A separately measured no-retention preparation alternative could reduce peak
live memory at the expense of repeated AMXR serialization. Decide that tradeoff
from the recorded calls/bytes/latency, not from the number of passes alone.
Other compilers/allocators, CPU isolation, longer repeat campaigns and separate
malloc-reserved/commit measurements remain follow-ups. No journal throughput,
MDBX/zstd tuning, import behavior, production activation or storage maturity
claim follows from this experiment.

## 2026-11-23: same-envelope retained versus repeated AMXR

This follow-up closes the earlier comparison's format confound. Both variants
use the corrected envelope grammar, validation and resource budgets from
integration commit `ce92f0892a0c182e2989daf007cae8c760a8a431`, whose parents are
corrective HEAD `994b0d7c9ed4534a08586c3dd30c8cb0165e7b80` and integrated main
`aea7d89f657c04b7b6388353530d9418a6a6ad81`. The retained source is that commit;
the repeated source is an isolated working-tree patch, not falsely attributed
to that commit. Concurrent ToolResult schema review is outside these two
Phase-1 payload fixtures and is not qualified by this experiment.

Question: should the codec retain every prepared AMXR until emission, or
serialize one bounded AMXR temporary in each of the two passes? The expected
trade-off was lower peak memory at the cost of repeated serialization and
more cumulative allocation. No numerical performance threshold was
preregistered. Selection below considers the observed costs and implementation
state, not an invented production SLA.

### Matched setup and correctness control

The host/compiler/probe boundaries are those above: Windows 11, Xeon E5-2696 v3,
GNU 15.2, strict C++17, Release `-O3 -DNDEBUG`, JSON/MDBX OFF. Both static
libraries were built from separate detached worktrees at the same integration
commit; both executables use the same runner source and static GNU runtimes.
The sole production difference removes `ExternalFrames`, its vector/cursor,
and its emission-reuse branch. `Writer::external` instead calls the unchanged
bounded standalone AMXR serializer in preflight and emission, charging the same
blob and object budgets in each pass. The exact total is still checked before
reserving one output buffer, and the emitted size is checked afterward.

Each strategy/case has three fresh process repetitions, 101 timed samples and
five warmups per operation. All twelve processes ran sequentially; strategy
order alternated by case/repetition. The parent was asked to defer heavy local
builds during the campaign, but the host was not pinned, isolated or power-state
controlled. Per-process p95/p99 variation remains visible below. Operations
include field validation, complete encode including preflight, decode and
framing-only inspection. Fixture generation, qualification and destruction are
outside operation timing. Whole-process memory includes those phases and is
not an operation-only allocation counter.

An additive runner `--wire-output` option writes the qualification frame before
timing. Four separate one-sample proof processes emitted both cases; Windows
`fc /b` reported no differences for either pair. Exact wire controls:

| Fixture | Both wire sizes | Both SHA-256 values |
| --- | --- | --- |
| `nested_amxr` | 16,924,594 | `d29064cf3e1c9f7b00871047e0b79faef580931b6de2d780bac097326a1d166c` |
| `combined_phase1_max` | 21,186,997 | `af338b876678f8961b3502eba793ddbe27005606edc132d8afeec048474c67cb` |

Both variants also passed `agent_memory_conversation_codec_test`, including
the existing independent literals, malformed inputs, resource profiles and
exact-size implementation-cap fixtures. These controls establish matched
codec behavior for the exercised corpus, not exhaustive equivalence of every
possible input or allocation-failure point.

### Results

The campaign completed **12 measured process outputs, 48 operation measurements
and 4,848 timed samples**, all PASS. The four wire-proof processes are separate
and do not inflate these counts. Raw allocation columns were constant across
all 101 samples of every process, and source/library/runner/executable hashes
were checked unchanged before and after the campaign.

Ranges below are three per-process nearest-rank quantiles in milliseconds,
not pooled quantiles or confidence intervals:

| Fixture | Strategy | Encode median ms | p95 ms | p99 ms |
| --- | --- | --- | --- | --- |
| 258 maximal AMXR refs | Retained | 43.777-44.556 | 47.233-53.244 | 58.762-64.402 |
| 258 maximal AMXR refs | Repeated | 49.774-51.069 | 52.826-53.267 | 54.442-57.441 |
| Combined Phase 1 maxima | Retained | 45.853-47.032 | 48.108-49.780 | 50.911-55.353 |
| Combined Phase 1 maxima | Repeated | 52.496-53.139 | 56.239-57.278 | 58.511-71.757 |

| Fixture / strategy | Encode new calls | Cumulative requested bytes | Encode peak live requested bytes | Retained at return |
| --- | --- | --- | --- | --- |
| Nested / retained | 1,041 | 33,893,675 | 33,849,524 | 16,924,594 |
| Nested / repeated | 1,289 | 50,769,485 | 16,990,131 | 16,924,594 |
| Combined / retained | 1,555 | 38,181,203 | 38,111,927 | 21,186,997 |
| Combined / repeated | 1,803 | 55,057,013 | 21,252,534 | 21,186,997 |

| Fixture / strategy | Whole-process Windows peak working set bytes |
| --- | --- |
| Nested / retained | 92,819,456 |
| Nested / repeated | 75,931,648-75,935,744 |
| Combined / retained | 110,448,640-110,456,832 |
| Combined / repeated | 93,593,600-93,597,696 |

Repeated encoding has 516 nested AMXR serialization calls instead of 258.
Its paired median times are approximately 13-16% higher, while encode peak
live requested bytes fall approximately 50% for nested references and 44%
for combined maxima. Cumulative allocation increases: peak and volume are
different costs. Working-set values are absolute process observations, not
subtracted per-operation allocation measurements. None measures native
allocator-reserved memory.

### Decision

Prefer **repeated bounded serialization** for the embedded library default.
The measured additional 6-7 ms per large encode is accepted in exchange for
removing approximately 16.9 MB of simultaneous requested storage in these
reference-heavy cases and deleting the aggregate cache/cursor invariant.
Multiple concurrent encodes make the peak cost relevant; no concurrent
throughput or multiplied RSS measurement is claimed. No policy framework,
runtime strategy flag, standalone AMXR change or new mandatory dependency is
needed. A future latency-critical caller can motivate a separately measured
strategy only if a concrete requirement justifies the extra state.

The retained measurements remain valid historical observations; the earlier
old-format comparison is not silently replaced or presented as this control.
This experiment qualifies the memory-strategy mechanism at its frozen source;
final integration tests/CI must qualify the selected strategy together with
any later schema corrections.

### Source, binary and raw-output bindings

| Artifact | Retained SHA-256 | Repeated SHA-256 |
| --- | --- | --- |
| `conversation_codec.cpp` | `35149a1e4ef2483d203079e67bf4b61c4dd84f8095d4329ca149c56769972873` | `9a61d543bdc4108242cca70896f6c740ecbb4b06ad719b4f9c9ec78fee27fc3d` |
| `libagent_memory.a` | `f9cc41d560457a66a26df99b730eb94eef7a52c5f301f09e6c4c67c26f27e6ef` | `1d9136e112bbe1ce36866004eb0abcaf0a76a46b00356356d0b0892562bd003b` |
| Runner executable | `b09c7946d7d37cedfe1b0fbed2e7b48e16a9e1e266b9fe871eefe6a9f6e2feb1` | `839a28d70711ef92297696c2d35da89922217a97cb89ce7962e8e3299059d717` |

Both runner sources have SHA-256
`04c48b5e67ab0cdc41b85832ce71bbcdfb45ec6d75b903c86842c65a3a46cf78`;
the unchanged `Conversation.cpp` is
`284b01e3c0afcb35607b894587c7f485da9f69c38999b6a2d25059c99f3282b2`;
unchanged standalone `ExternalObjectRef.cpp` is
`6ae6a8163e9e12e7590910f1840180712409bf02dfad7ce097cb2f9419012580`.

Raw artifacts are under
`E:/_repoz/agent-memory-cpp/.artifacts/amce-690-final-review/`:
`same-wire-<strategy>-<case>-r<1..3>.jsonl`, `same-wire-summary.json`,
`same-wire-bindings.json`, the four `.bin` wire controls and proof logs.
The twelve-file manifest `same-wire-raw-sha256.json` has SHA-256
`4490d4cd6582e9d4111d24905568b0f489dc85bf6054f1b31205b09553685c4f`.
Generated raw outputs/binaries stay untracked. The exact minimal production
patch is retained as `same-wire-repeated-amxr.patch`, SHA-256
`774cd347f87eb8fe4c1df599af444928fcfe900e934e3dd4cbebeb644a0553ae`;
`same-wire-runner.patch` contains the identical wire-output control addition.

Reproduction builds use the corresponding detached worktree with CMake Ninja,
the options above and `--target agent_memory agent_memory_conversation_codec_test`.
Build outputs are respectively `.build/amce-690-memory-retained` and
`.build/amce-690-memory-repeated`. Link each runner as above with
`-static-libstdc++ -static-libgcc -static -lpsapi`, against its own library.
For each strategy/case run the resulting executable with
`--case nested_amxr --samples 101 --warmups 5` (or `combined_phase1_max`).
The proof invocation additionally passes `--wire-output <artifact.bin>` and
uses one sample/one warmup, followed by `fc /b` on the two outputs.
