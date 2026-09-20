# Native THQ4 interval² kernel bakeoff

Date: 2026-09-20  
Status: `EXECUTED`

## Contract

The canonical THQ4 payload remains 96 bytes/document: four 2-bit ordinal
levels per byte. The benchmark compares alternative scorers on exactly this
payload and uses scalar coordinate interval² as the correctness reference:

1. coordinate FP32 LUT (reference);
2. two-coordinate 16-entry pair LUT in FP32;
3. pair LUT quantized to uint8 with one scale per pair;
4. the same uint8 tables evaluated by AVX2 `PSHUFB` over a 32-document
   pair-transposed block layout.

The pair identity is exact: two adjacent 2-bit levels form one 4-bit index,
so 384 coordinate lookups become 192 pair-table lookups without changing the
score. The uint8 arms are approximations and are therefore measured for
top-128 set overlap rather than being declared exact.

## Full 152-query result

The canonical 1M-document THQ4 codes and all 152 queries were scanned once.
The exact pair LUT had zero score mismatches against the coordinate reference.
The AVX2 output had zero absolute error against its scalar uint8 counterpart.

| kernel | p50/query | p95/query | correctness |
| --- | ---: | ---: | --- |
| coordinate FP32 | 580.27 ms | 593.58 ms | reference |
| pair LUT FP32 | 200.77 ms | 206.03 ms | exact |
| pair LUT uint8 | 218.42 ms | 221.72 ms | 0.997533 mean top-128 set overlap |
| pair LUT uint8 + AVX2 PSHUFB | 159.67 ms | 167.15 ms | 0.997533 mean top-128 set overlap |

The AVX2 arm is therefore about 3.6x faster than the scalar coordinate
reference and about 1.26x faster than the scalar pair LUT on this host. The
scalar uint8 arm is slower than FP32 pair lookup; quantization only pays after
SIMD evaluation. All 152 queries had at least one ordered top-128 difference
in the uint8 arms, despite the high set overlap. No production quality claim
is made from this filter-only benchmark.

The result and source hashes are recorded in
`2026-09-20-thq-fastscan-kernel-bakeoff-result.json`. The benchmark source is
`tools/agent-memory-bench/thq_fastscan_kernel_benchmark.cpp`; CMake exposes an
opt-in `agent-memory-thq-fastscan-kernel-benchmark` target under
`AGENT_MEMORY_NEUROUTE_ENABLE_AVX2`.

## Decision

Keep canonical interval² semantics. The next native candidate is the AVX2
pair-transposed kernel, but it must still be tested inside the complete R4
candidate cascade and against page/cache costs. The uint8 quantization policy
needs a top-128 quality gate before it can replace FP32 pair tables.
