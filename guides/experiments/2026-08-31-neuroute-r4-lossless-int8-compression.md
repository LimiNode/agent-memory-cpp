# NeuRoute R4 lossless INT8 compression

## Context

- Date: 2026-08-31
- PR: stacked on direct mapped address access
- Status: full DE-1M materialization and latency measurement complete

## Question

Can the quality-safe raw INT8 representative store be compressed losslessly
with pinned SIMDComp BP128, and what serving latency does unpack add to the
selected mapped direct path?

## Frozen protocol

The full three-seed address-major corpus is physically materialized for four
treatments: raw INT8, fixed eight-bit BP128, adaptive per-128D frame-of-reference
BP128, and adaptive centered-zigzag BP128. Fixed BP128 has the same 384-byte
payload and isolates codec overhead. Adaptive records store three bit widths;
FOR additionally stores three minima. Variable-length stores include one u64
byte offset per occupied address row.

All formats are lossless relative to the same INT8 codes and scale. They use
direct memory mapping, physical address order, and the frozen batched scorer.
Every paired score hash must match. Measurement includes 5,472 warm samples and
180 fresh-process first requests with the OS page cache uncontrolled.

## Expected result

Fixed BP128 should not reduce footprint. Adaptive blocks may save space if a
substantial fraction of 128D code ranges fit in seven or fewer bits. Any gain
must be reported together with unpack-and-dot and total latency deltas; the PR
does not mandate selection of a compressed format.

## Actual result

Lossless SIMDComp does not compress these per-vector INT8 codes. Across each
one-million-document seed, only 97 of three million FOR blocks and 38 of three
million zigzag blocks fit in seven bits; every other block requires all eight.
Headers and address offsets therefore make both adaptive layouts larger.

Three-seed physical footprint:

| Format | Payload bytes | Sidecar bytes | Total ratio vs raw |
|---|---:|---:|---:|
| Raw INT8 | 1,164,000,000 | 0 | 1.0000 |
| SIMDComp fixed8 | 1,164,000,000 | 0 | 1.0000 |
| SIMDComp adaptive FOR | 1,181,995,344 | 1,562,704 | **1.0168** |
| SIMDComp adaptive zigzag | 1,172,998,176 | 1,562,704 | **1.0091** |

All 5,472 warm and 180 fresh-process samples preserve identical learned score
hashes. Unpack is nevertheless slower than direct raw-byte scoring:

| Format | Warm dot p95 ms | Warm total p50/p95 ms | Fresh-process total p50/p95 ms |
|---|---:|---:|---:|
| Raw INT8 | 9.760 | **14.809 / 16.130** | **21.745 / 23.905** |
| SIMDComp fixed8 | 11.721 | 16.660 / 18.269 | 23.651 / 26.598 |
| SIMDComp adaptive FOR | 12.187 | 17.060 / 18.738 | 24.146 / 26.436 |
| SIMDComp adaptive zigzag | 13.330 | 18.077 / 19.882 | 25.333 / 28.064 |

Fixed8 isolates the codec cost: it saves no bytes and adds `2.14 ms` or 13.3%
to warm total p95. Adaptive FOR adds 1.68% physical space and 16.2% latency;
zigzag adds 0.91% space and 23.3% latency. Raw INT8 remains the serving format.
The earlier INT5 result does not transfer because INT5 is a genuinely narrower
representation, whereas lossless per-vector INT8 blocks already occupy their
full eight-bit range.

## Limitations and next checks

The adaptive layout is optimized for sequential representatives at the start of
an address block. Random document access elsewhere in the corpus and MDBX page
integration remain outside this experiment. Results are single-host and
single-threaded.

## Evidence

```text
materialization SHA-256: a6405d5c0d877cdf8f9be4d34babad7c7e4b5cef328184c0e79cc4c0a81474bc
result SHA-256:          0a23baa73b5236d779839f212e5fcb53238643d0dc09e4fd96b0ee0c582ed17f
warm report SHA-256:     e3c150f45b742c6e8dfeaa49ad42a2d6416ff0ff4c9aebdf4df7c509cf4587f4
evidence SHA-256:        b5298bc6ba381934c617749a9eccc4e185d741618ab84f06b581760d17ad017e
```

The evidence writer rehashes 18 physical files / 3.52 GB, recomputes all warm
and fresh-process summaries, revalidates paired score identity, and reruns the
native SIMDComp round-trip self-test.
