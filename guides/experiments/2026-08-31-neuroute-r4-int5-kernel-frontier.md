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
workers. A separate crossover matrix uses the production compact kernel chosen
by the matched concurrency and pressure gates, then runs three measured batches
at 8 workers for 128, 192, 256, 320, 384, 512, 768, and 1024 MiB plus resident
operation.

The implementation frontier contains:

- the historical SIMDComp-unpack direct-square path as a replay control;
- the same 244-byte layout after removing scratch initialization, hoisting the
  document scale, computing signed squares in the integer domain, precomputing
  routing spans, reusing per-thread maximum buffers, preparing only the query
  data needed by each treatment, and removing process telemetry from query
  timers;
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
| homogeneous INT8 | 2.415 | 10.502 |
| historical INT5 direct-square | 4.102 | 11.938 |
| optimized same-layout INT5 | 3.564 | 11.500 |
| fused SSE INT5 | 4.266 | 12.209 |
| fused AVX2 INT5 | 3.279 | 11.228 |
| fused AVX2 Q8 | 3.687 | 11.791 |
| direct Q8 integer | 3.485 | 11.486 |
| direct Q16 integer | 3.348 | 11.398 |

Removing implementation overhead reduced representative p95 by 13.1% versus
the historical path. The AVX2 fused layout reduced it by 20.1% and is the
fastest exact INT5 implementation. It is still 1.358x the INT8 representative
p95 and 1.069x the full-cascade p95, so it does not pass the preregistered 1.02
resident gate.

The fastest quality-eligible integer sensitivity path is direct Q16 at 1.386x
INT8 representative p95. It misses the preregistered 1.10 gate, so the AoSoA
follow-up is not opened. This is a hard stop for this branch, not evidence that
all batched or AoSoA formulations are intrinsically slow. The earlier result
only ruled out the tested scalar/grouped four-dimensional bit-sliced LUT.

## Memory crossover

The representative-stage winner changes once and monotonically across the
finite working-set caps:

| Working-set cap | INT8 rep p95 ms | INT5 rep p95 ms | INT5 / INT8 |
| --- | ---: | ---: | ---: |
| 128 MiB | 57.093 | 34.464 | 0.604 |
| 192 MiB | 35.729 | 13.968 | 0.391 |
| 256 MiB | 18.311 | 3.468 | 0.189 |
| 320 MiB | 2.982 | 3.409 | 1.143 |
| 384 MiB | 3.021 | 3.458 | 1.145 |
| 512 MiB | 2.972 | 3.458 | 1.163 |
| 768 MiB | 2.950 | 3.525 | 1.195 |
| 1 GiB | 2.989 | 3.447 | 1.153 |
| resident | 3.013 | 3.447 | 1.144 |

The production-kernel crossover is bracketed between 256 and 320 MiB on this
machine. At 256 MiB the full-cascade p95 is `36.711 ms` for INT8 and
`12.679 ms` for INT5, a 2.90x advantage for the compact path. Both the
representative and full-cascade winner sequences change once and monotonically.
The representative-stage curve remains the primary causal storage comparison.
This single-machine Windows working-set sweep does not license an automatic
runtime selector.

## Decision

The dense routing policy remains explicit:

- resident mode: homogeneous INT8;
- compact or memory-pressure mode: nonlinear INT5 power 0.5 using optimized
  fused AVX2; its pressure p95 ratio versus direct-square is `0.965`, within
  the preregistered `1.05` latency cap, and its page-fault ratio is `1.022`,
  within the separate `1.10` fault cap;
- no AoSoA materialization, because the registered direct-integer gate failed;
- no automatic RAM-based mode switching from this one-host crossover curve.

The result closes the previously identified implementation gaps: zeroed
scratch, scale placement, real AVX2 packing, fused extraction/dot, and direct
Q8/Q16 arithmetic were all tested. The pressure decision repeated in a second
fresh matched run (`1.055` page-fault ratio), also below the `1.10` cap. The
resident gap fell materially but did not disappear. Nonlinear INT5 remains a
production-valid memory-pressure mode, not a universal replacement for
resident INT8.

## Reproduction and limitations

The compact result and raw native reports are under
`tmp/neuroute-r4-int5-kernel-frontier/` in the experiment worktree and are not
committed. The committed contract records the executable and parent artifact
hashes. The working-set-cap mechanism uses the Windows process working-set API;
Linux and Windows CI build the R4 harness and run native/self-test contracts,
but do not claim to reproduce the DE-1M timings.
