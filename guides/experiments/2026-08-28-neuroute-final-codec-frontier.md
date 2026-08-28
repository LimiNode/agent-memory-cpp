# NeuRoute final codec frontier

Date: 2026-08-28. Frozen protocol; measurements are intentionally absent.

## Question

Does symmetric per-document INT5 preserve the final ranking contribution of
FP32 inside the already frozen ADC256 top-64 pools, and which byte-equivalent
physical decoder is best for the compact scalar winner?

This study does not change routing, candidate generation, Hamming, ADC, or the
ordered top-64 pool. INT6/INT7/INT8 quality is frozen from the parent evidence;
only the missing INT5 quality row is newly measured.

## Matrix

The quality screen covers DE/FR/JA 25k and nested DE 1M, three frozen router
seeds, and the unchanged one-sided mean/per-dataset nDCG gates. The native
screen compares scalar BP128 and pinned `fast-pack/simdcomp` BP128 for 5-, 6-
and 7-bit codes, plus raw INT8. Every layout must reproduce the same decoded
integers and top-10 sequence before timing.

SIMDComp is pinned at `009c67807670d16f8984c0534aef0e630e5465a4`. It is an
optional x86/SSE2 benchmark adapter, not a core dependency or durable-format
commitment. Non-x86 builds retain the scalar self-test.

## Decision

The quantizer is the lowest-byte representation passing both frozen quality
limits. Among byte-equivalent layouts for that quantizer, the lowest maximum
native rank-top10 p95 wins. Timing separately reports decode ns/vector,
decode-and-dot per top-64 query, and full deterministic top-10 selection.

A quality winner licenses the separately frozen full-corpus storage study.

## Limitations

Native timing is warm and pool-local. It does not model random full-corpus
fetch, page-cache state, MDBX payload access, or end-to-end routing. Those are
reserved for the later stored-code protocol.
