# NeuRoute final codec frontier

Date: 2026-08-28. Frozen protocol and completed measurement.

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

## Results

INT5 passed both frozen quality limits and became the smallest eligible final
representation at 244 bytes per document. The fixed-pool mean loss versus FP32
was `-0.001303`; negative loss means that the quantized perturbation produced a
slightly higher mean nDCG, not that INT5 is generally a better semantic model.

| Dataset | FP32 nDCG@10 | INT5 nDCG@10 | INT5 loss |
| --- | ---: | ---: | ---: |
| DE 25k | .632164 | .635747 | -.003583 |
| FR 25k | .617412 | .611406 | +.006007 |
| JA 25k | .687926 | .687244 | +.000682 |
| DE 1M | .578523 | .586841 | -.008318 |

The maximum per-dataset positive loss was `.006007`, below the frozen `.0075`
limit. INT5 therefore reduces the selected INT6 storage from 292 to 244 bytes
per document, or about 48 MB per million documents before container overhead.

All 84 native rows replayed identical decoded integers and ranked top-10
sequences. SIMDComp was decisively faster for the selected INT5 representation:

| INT5 physical layout | Max decode p95, ns/vector | Max decode+dot p95, ms/query | Max rank-top10 p95, ms/query |
| --- | ---: | ---: | ---: |
| Scalar BP128 | 913.486 | .087473 | .088624 |
| SIMDComp BP128 | 68.176 | .033888 | .035607 |

For context, raw INT8 reached `.045418 ms/query` maximum rank-top10 p95 in the
same run. The decision is therefore `int5_document` with `simdcomp_bp128` on
x86/SSE2 and the exact scalar decoder as the portable fallback. This is a
benchmark adapter decision, not a durable-format or core-dependency decision.

## Evidence

```text
quality result SHA-256:       6bd85bc64231ac036a68b337f9e2f95ab364e316176364310e57c6b14f0eb363
native materialization SHA:   e6c78173d5b9111c520589ccd1b0056f56bb66ca4eb792f1878d96d152fbe374
native report SHA-256:        ba31af12f65119b2b7fe180662d327477b76f1b616ec84726f0738d007740f26
fail-closed evidence SHA-256: 47c8138a28143c8f924a127d6bfec8f7eddc5291976bfa8c165e4dee7eb96379
```

The evidence writer regenerated the complete quality result byte-for-byte,
required a real SIMDComp treatment, replayed all native top-10 sequences with
the current Release executable, and selected the layout only after those
checks passed.

## Limitations

Native timing is warm and pool-local. It does not model random full-corpus
fetch, page-cache state, MDBX payload access, or end-to-end routing. Those are
reserved for the later stored-code protocol.
