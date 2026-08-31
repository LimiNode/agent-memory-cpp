# NeuRoute nonlinear INT5 routing-kernel frontier

## Question

Can a specialized square-root-companded INT5 kernel remove the fully resident
latency penalty versus homogeneous INT8, and at what working-set limit does the
compact representation become faster?

## Frozen protocol

The DE-1M internal partition, three model seeds, K32 representatives,
top-1024 address shortlist, scorer, and complete Hamming/ADC/exact cascade are
unchanged. The matched matrix uses two trace repetitions, one warm-up batch,
two measured batches, resident and 256 MiB conditions, and 1, 8, and 16
workers. A separate crossover matrix uses three measured batches at 8 workers
for 128, 192, 256, 320, 384, 512, 768, and 1024 MiB plus resident operation.

The implementation frontier contains:

- the historical SIMDComp-unpack direct-square path as a replay control;
- the same 244-byte layout after removing scratch initialization, hoisting the
  document scale, computing signed squares in the integer domain, precomputing
  routing spans, and removing process telemetry from query timers;
- fused fixed-five-bit SSE and AVX2 extraction/dot paths that do not materialize
  a `uint32[384]` decode buffer;
- direct Q8 and Q16 integer signed-square dot sensitivity paths;
- a fused AVX2 Q8 sensitivity path.

The AVX2 physical control stores 256 dimensions in a SIMDComp AVX2 BP5 block,
128 dimensions in an SSE BP5 tail, and one FP32 amplitude. It remains exactly
244 bytes per representative. Three full mixed stores were materialized and
rehashed.

## Correctness and quality

The historical path reproduced all 228 parent query hashes and candidate
counts. Every new path retained mean nDCG@10 `0.649780` and the same final exact
top-10 on all 228 queries. Different FP32 reduction orders changed some routing
and candidate hashes: the selected fused AVX2 path retained 97.37% selected
address identity and 99.56% candidate identity versus optimized direct-square,
but 100% final exact identity.

Q8 and Q16 query quantization remain sensitivity evidence. They preserve the
observed final nDCG in this frozen matrix, but change scorer arithmetic and do
not qualify as exact implementation controls.

## Resident kernel result

Single-worker resident p95 values from the final matched run were:

| Kernel | Representative p95 ms | Full cascade p95 ms |
| --- | ---: | ---: |
| homogeneous INT8 | 2.444 | 10.523 |
| historical INT5 direct-square | 4.169 | 12.130 |
| optimized same-layout INT5 | 3.575 | 11.385 |
| fused SSE INT5 | 4.372 | 12.312 |
| fused AVX2 INT5 | 3.265 | 11.238 |
| fused AVX2 Q8 | 3.712 | 11.653 |
| direct Q8 integer | 3.353 | 11.393 |
| direct Q16 integer | 3.363 | 11.391 |

Removing implementation overhead reduced representative p95 by 14.2% versus
the historical path. The AVX2 fused layout reduced it by 21.7% and is the
fastest exact INT5 implementation. It is still 1.336x the INT8 representative
p95 and 1.068x the full-cascade p95, so it does not pass the preregistered 1.02
resident gate.

The fastest quality-eligible integer sensitivity path is direct Q8 at 1.372x
INT8 representative p95. It misses the preregistered 1.10 gate, so the AoSoA
follow-up is not opened. This is a hard stop for this branch, not evidence that
all batched or AoSoA formulations are intrinsically slow. The earlier result
only ruled out the tested scalar/grouped four-dimensional bit-sliced LUT.

## Memory crossover

The representative-stage winner changes once and monotonically across the
finite working-set caps:

| Working-set cap | INT8 rep p95 ms | INT5 rep p95 ms | INT5 / INT8 |
| --- | ---: | ---: | ---: |
| 128 MiB | 52.443 | 33.390 | 0.637 |
| 192 MiB | 35.886 | 15.378 | 0.429 |
| 256 MiB | 17.148 | 3.423 | 0.200 |
| 320 MiB | 2.957 | 3.430 | 1.160 |
| 384 MiB | 2.964 | 3.446 | 1.162 |
| 512 MiB | 2.912 | 3.419 | 1.174 |
| 768 MiB | 2.932 | 3.472 | 1.184 |
| 1 GiB | 3.011 | 3.420 | 1.136 |
| resident | 2.915 | 3.407 | 1.169 |

The measured representative-stage crossover is therefore bracketed between
256 and 320 MiB on this machine. At 256 MiB the full-cascade p95 is 34.077 ms
for INT8 and 12.516 ms for INT5, a 2.72x advantage for the compact path.

Full-cascade p95 is not monotone above 320 MiB because downstream variance is
larger than the small codec difference near parity. It is reported as a
secondary observation; the representative-stage curve is the causal storage
comparison. This single-machine Windows working-set sweep does not license an
automatic runtime selector.

## Decision

The dense routing policy remains explicit:

- resident mode: homogeneous INT8;
- compact or memory-pressure mode: nonlinear INT5 power 0.5 using optimized
  direct-square; fused AVX2 remains experimental because its page-fault ratio
  versus direct-square was 1.198, above the preregistered 1.05 pressure gate;
- no AoSoA materialization, because the registered direct-integer gate failed;
- no automatic RAM-based mode switching from this one-host crossover curve.

The result closes the previously identified implementation gaps: zeroed
scratch, scale placement, real AVX2 packing, fused extraction/dot, and direct
Q8/Q16 arithmetic were all tested. The resident gap fell materially but did
not disappear. Nonlinear INT5 remains a production-valid memory-pressure mode,
not a universal replacement for resident INT8.

## Reproduction and limitations

The compact result and raw native reports are under
`tmp/neuroute-r4-int5-kernel-frontier/` in the experiment worktree and are not
committed. The committed contract records the executable and parent artifact
hashes. The working-set-cap mechanism uses the Windows process working-set API;
Linux and Windows CI build the R4 harness and run native/self-test contracts,
but do not claim to reproduce the DE-1M timings.
