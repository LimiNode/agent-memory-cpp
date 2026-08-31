# NeuRoute nonlinear INT5 routing-kernel frontier

## Question

Can a specialized square-root-companded INT5 kernel remove the fully resident
latency penalty versus homogeneous INT8 while retaining the already licensed
quality and the compact-mode pressure advantage?

## Matrix

The frozen DE-1M internal partition, three model seeds, K32 representatives,
top-1024 address shortlist, scorer, and complete Hamming/ADC/exact cascade are
unchanged. Five paths were measured under resident and 256 MiB working-set
conditions with 1, 8, and 16 workers:

- homogeneous INT8 resident control;
- current SIMDComp unpack plus signed-square AVX2 dot;
- SIMDComp unpack plus a 16-entry `vpshufb(m^2)` lookup;
- a physical sign-plus-four-magnitude-plane INT5 layout with FP32 query LUTs;
- the same bit-sliced layout with per-query INT8 sensitivity arithmetic.

The bit-sliced physical representation stores 32 codes as five `uint32`
planes, so it remains exactly 20 bytes per 32 dimensions and 244 bytes per
representative. Three full mixed stores were materialized and rehashed; their
sizes are 259,608,160, 261,826,624, and 258,581,008 bytes, exactly matching the
corresponding vector-major mixed stores.

## Correctness and quality

The direct-square control reproduced all 228 parent query hashes and candidate
counts. Every INT5 kernel produced the same mean nDCG@10 (`0.649780`) and the
same final exact top-10 on all 228 queries. The implementation-level paths did
not produce identical intermediate FP32 score bytes:

- `vpshufb`: 96.93% selected-address identity, 100% candidates/final top-10;
- bit-sliced FP32: 86.40% selected-address identity, 99.56% candidates,
  100% final top-10;
- bit-sliced Q8 sensitivity: 0% selected-address identity, 36.40% candidate
  identity, 100% final top-10.

Thus the Q8 result is useful sensitivity evidence, not an exact implementation
control. Its downstream stability does not license changing scorer arithmetic
without a separate cross-dataset quality protocol.

## Resident results

Single-worker resident p95 values were:

| Kernel | Representative p95 ms | Full cascade p95 ms |
| --- | ---: | ---: |
| homogeneous INT8 | 2.395 | 10.428 |
| INT5 direct square | 4.167 | 12.132 |
| INT5 `vpshufb` | 6.011 | 13.991 |
| INT5 bit-sliced FP32 LUT | 23.039 | 30.918 |
| INT5 bit-sliced Q8 LUT | 23.231 | 31.212 |

The direct-square kernel remains the fastest exact INT5 path. Its full-cascade
p95 ratio versus INT8 is `1.1634`, so it fails the preregistered `1.02` resident
gate. `vpshufb` loses because SIMDComp unpack already dominates and the byte
packing/lookup sequence adds work. The ten nonlinear bit terms required by the
T-MAC-like square decomposition make the bit-sliced LUT paths compute-bound;
the zero footprint penalty does not imply a latency win.

## Pressure and concurrency

The selected direct-square path passes its concurrency and pressure controls.
At 8 workers under the 256 MiB cap its p95 was 13.590 ms versus 32.507 ms for
homogeneous INT8, with 633.4 versus 390.1 queries/s at p50. This confirms the
existing compact-mode result; it does not rescue the resident gate.

## Decision

Close the routing kernel branch with an explicit two-mode policy:

- resident mode: homogeneous INT8;
- compact or memory-pressure mode: nonlinear INT5 power 0.5 direct-square.

The tested specialized kernels do not justify further bit-sliced or AoSoA
materialization work: neither alternative exact family approached the current
direct-square control. The physical size reduction remains a production-valid
speed/memory trade-off rather than a universal INT8 replacement.
