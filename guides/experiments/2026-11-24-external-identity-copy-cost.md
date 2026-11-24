# External identity copy cost: portable hardening

- Date: 2026-11-24. Context: foundation Draft #696 (`ccc4a3e6`, test refinement
  `3d7b868d`) and corrective #694/#695 stack. The implementation is identical
  in those ordinary cherry-picks; final publication heads are in PR receipts.
- Hypothesis: direct active-alternative construction and recursive component
  copies preserve owned values without extra allocations or serialize/decode
  staging. Latency may differ; this is not a speedup proposal.
- Owner/rationale: [foundation audit](../audits/2026-11-24-external-identity-copy.md).

## Setup and reproduction

Intel Xeon E5-2696 v3 2.30 GHz, Windows host / Ubuntu WSL2 kernel
6.18.33.2-microsoft-standard-WSL2. GNU g++ 15.2.0-16ubuntu1, libstdc++ headers
15, runtime libstdc++6 16-20260322-1ubuntu1. C++17 static library built in CMake
Release, JSON/MDBX OFF; standalone harness `-O3`, no LTO or sanitizers.

From the Phase 3B source root (adjust source prefix to the worktree):

```sh
g++ -std=c++17 -O3 -I src \
    tools/agent-memory-bench/external_identity_copy_benchmark.cpp \
    /var/tmp/am-copy-phase3b-release/libagent_memory.a -o /var/tmp/am-copy-cost
/usr/bin/time -v /var/tmp/am-copy-cost
```

The committed harness compares successful ordinary and named copies; ordinary
copies are never fault-injected. Scalar fixture: 48-byte instance, 160-byte
string object key, no parent. Composite fixture: parent and object each have
three named signed/unsigned/string components, 48-byte component names and a
160-byte string value. Each case first counts intercepted successful allocations
and consumes its result, then times five batches of 10,000 copies, including
destruction. Fixtures are created outside timing; a volatile checksum consumes
results. There is no explicit warmup beyond the initial allocation-count copy.

## Observed result

After the concurrent builds completed, one process reported:

| Copy | Intercepted allocations | Median ns/copy | Min | Max |
| --- | ---: | ---: | ---: | ---: |
| Scalar ordinary | 2 | 52.1 | 50.8 | 58.5 |
| Scalar explicit | 2 | 55.7 | 55.3 | 67.3 |
| Composite ordinary | 11 | 229.5 | 218.4 | 235.5 |
| Composite explicit | 11 | 268.4 | 257.1 | 293.5 |

Process peak RSS was 4,120 KiB, including all fixtures, harness and runtime;
this is not per-copy incremental memory. An earlier run during builds reported
scalar 50.7/56.6 and composite 242.2/263.6 ns (ordinary/explicit), with the same
2/11 allocation counts. These are directional local measurements, not a stable
latency regression threshold, throughput SLA or maximum-key screen.

The Windows GNU optimized probe intercepted 0/0 scalar and 2/2 composite calls,
less than the corresponding Linux profile, so those counters cannot be used as
total heap-allocation claims. Its times were also measured during builds and
are not used for acceptance. Compiler/runtime-visible interception is distinct
from proof that no underlying allocation occurred.

## Interpretation and limits

The tested Linux profile needs no extra owned allocation for explicit copying.
The named path is modestly slower in this tiny screen; safety, unchanged value
semantics and normal exception propagation justify it, not faster execution.
There is no new binary staging buffer or alternate identity representation.
The same resource count does not prove universal identical RSS or latency.

Qualification of actual injected exceptions is separate in the linked audit
and [Phase 3B report](../audits/2026-11-24-conversation-source-import-qualification.md).
This benchmark does not inject malloc/OS faults, prove caller-side implicit
copies safe, exercise maximum-size identities or establish another STL as fixed.
Before setting a production latency budget, use isolated repeated processes,+randomized order, representative key-size distributions and allocation-byte /
peak-memory instrumentation. No cache or broader value-type rewrite is justified
by this bounded cost screen. Raw output is retained in the tool transcript;
this compact table and committed harness are the durable reproducible summary.
