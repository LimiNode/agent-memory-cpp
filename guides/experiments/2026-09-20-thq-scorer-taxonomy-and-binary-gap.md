# THQ scorer taxonomy and the remaining binary comparison

Date: 2026-09-20  
Context: `main` after #432 (`379e4244`); corrective review of the #431/#432
evidence chain.

## Question

Which claims about THQ4 and binary codes are already established, and which
comparison is still absent before choosing a filter or score codec?

## Canonical scorer contract

The production-shaped THQ4 representation stores four interval levels per
coordinate (two bits per coordinate, 96 B for 384 dimensions).  Its score is
the interval-squared ADC

```text
sum_d LUT[d][level_d]
```

where the query-side LUT contains the squared distance from the query
coordinate to each learned interval.  It is not `popcount(query_code XOR
document_code)`.

The native `document_codec_native_benchmark.cpp` contains a separate historical
Gaussian-threshold packed Hamming control.  Its method IDs already carry the
`gaussian_threshold_control` qualifier; the accompanying notes now state the
same qualification explicitly.  Results from that control must not be cited
as the canonical THQ4 interval-squared scorer.

## Evidence already checked

* Canonical THQ4 interval-squared candidate retention and independent top-128
  parity were checked in the native full-corpus gate (#419 lineage).
* THQ4 top-128 followed by FP32 reranking was checked on the frozen R4 shell;
  the candidate-FP32 top-10 ceiling is a membership result, not evidence that
  THQ4 reconstructs a vector.
* Scalar ADC, RSLM-like, scalar-conditioned, and joint-conditioned residual
  codecs were replayed from persisted packed artifacts.  The current
  committed audits are source-replay `PASS` artifacts with hashes and row
  counts; their physical-footprint receipts include the candidate-ID mapping.
* Earlier binary/Hamming, ITQ, and local RaBitQ/BBQ-like studies exist, but
  they use different document lanes, prototype/local-IVF partitions, budgets,
  or final-stage contracts.  They are valid bounded controls and are not a
  matched replacement comparison for the current R4 shell.
* The standalone references are deliberately named `RaBitQ-RR-1` and
  `BBQ-block-1 (BBQ-like)`.  They are not claims of compatibility with an
  official vendor implementation.

The cheap packed-codec round-trip and binary-reference parity tests are now
run locally; the packed THQ contract and Python compilation are also enforced
by CI.  These checks establish representation correctness, not retrieval
quality or serving latency.

## Still not checked

The following single matched bake-off remains open.  The three methods are
**alternative filter arms**, not a sequential THQ4 -> RaBitQ -> BBQ cascade:

```text
                         ┌→ THQ4 interval² → top-K ─┐
frozen R4 candidate set ├→ RaBitQ-RR-1   → top-K ─┼→ same FP32 oracle rerank
                         └→ BBQ-block-1    → top-K ─┘
```

The pinned codec configurations are:

```text
THQ4:
  representation = canonical 384D ordinal, 4 interval levels, 96 B/document
  scorer = interval-squared ADC

RaBitQ-RR-1:
  bits = 384 (full-dimensional one-bit estimator)
  metric = ip
  seed = 20260920
  gain = ||rotated_centered_document||² / L1(rotated_centered_document)

BBQ-block-1 (BBQ-like):
  bits = 384
  blocks = 8 (48 bits/block)
  scale_storage = fp16
  metric = ip
  seed = 20260920
```

`K` is the number of candidates emitted by each alternative filter arm.  For
`K = 32, 64, 128, 256, 512`, the gate must report, on the same 152-query
candidate shell:

* top-10 survival/overlap, qrels nDCG, p05 **per-query nDCG@10**, and
  worst-query nDCG@10;
* filter-only latency and bytes touched;
* filter plus the same **FP32 oracle rerank** cost, including the number of
  documents sent downstream;
* packed/native warm-up and repeated p50/p95/p99 measurements;
* provenance for rotation, scales/corrections, query encoding, tie policy, and
  the exact candidate stream.

Without this gate, statements such as “binary is faster” are only statements
about a primitive per-document operation.  They do not establish a cheaper
R4 cascade, because a weaker binary filter may require a larger `K` and a more
expensive final rerank.

## Decision

No production codec selection is licensed by the current evidence.  The next
research PR should implement the matched gate above, retain the old controls as
separate lanes, and publish one source-replay audit per codec family.  A
binary method should advance only if its complete-cascade cost is lower at the
same required top-10 survival, not merely if its inner loop is faster.
