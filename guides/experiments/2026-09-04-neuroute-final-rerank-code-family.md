# NeuRoute final-rerank code-family bake-off

Date: 2026-09-04. Stacked after PR #290.

## Question

Which representation is best specifically for the final document-level
`ADC64 → top-10` stage? Earlier code-family experiments used document-flat,
K8-prototype, or residual-IVF geometry. Their winners cannot be assumed to be
optimal on a 64-document near-neighbour pool.

This experiment freezes all earlier work:

```text
R4 routing → postings → candidate documents → Hamming768 → ADC64
                                                        ↓ frozen
                                           codec under test → top10
```

It therefore does not attribute routing, K8, K32/R0, Hamming, or ADC errors to
the final codec.

## Setup

- DE 1M actual-R4 native reports;
- 76 configuration and 76 previously opened internal queries;
- three router seeds and the INT8 routing-storage arm: 456 ADC64 cases;
- one deterministic, label-free 16,384-document training sample;
- identical native ADC64 document IDs for every method;
- FP32 inner product on that same pool is the per-query reference;
- database encoding happens offline; reported p95 times cover portable codec
  scoring and deterministic top-10 selection. Sub-byte scalar and PQ4 timings
  do not include a physical packed-bit decoder and remain directional.

The runner is
`tools/agent-memory-bench/run-neuroute-final-rerank-code-family.py`; the frozen
matrix contract is
`tools/agent-memory-bench/neuroute-final-rerank-code-family.example.json`.
Raw local output is `tmp/final-rerank-code-family-v2.json` and is not committed.
Its SHA-256 is
`7fc92a8684f5a6cbae6ffd3aa604313c60ff4598faaab970e37c3cdf7897d5c3`.

## Results

Overall results across both partitions and all three seeds are:

| Method | Record B/doc | Top-10 overlap | nDCG loss vs FP32 | p05 / worst | Python p95 ms |
|---|---:|---:|---:|---:|---:|
| FP32 | 1536 | 1.0000 | .000000 | 1.0 / 1.0 | .0547 |
| FP16 | 768 | .9996 | .000000 | 1.0 / .9 | .1535 |
| INT12 power-.5 | 580 | 1.0000 | -.001124 | 1.0 / 1.0 | .2765 |
| INT12 linear | 580 | .9996 | -.000095 | 1.0 / .9 | .1130 |
| INT10 power-.5 | 484 | .9982 | -.002981 | 1.0 / .9 | .2735 |
| INT10 linear | 484 | .9969 | -.001196 | 1.0 / .9 | .1040 |
| INT8 linear | 388 | .9934 | -.001370 | .9 / .9 | .1112 |
| INT8 power-.5 | 388 | .9871 | -.002787 | .9 / .9 | .2701 |
| INT6 linear | 292 | .9673 | -.004789 | .9 / .8 | .1105 |
| INT6 power-.5 | 292 | .9618 | -.001812 | .9 / .8 | .2675 |
| INT5 linear | 244 | .9384 | .006326 | .8 / .7 | .1100 |
| INT5 power-.5 | 244 | .9184 | .008928 | .8 / .7 | .2627 |
| INT4 linear | 196 | .8651 | .013071 | .6 / .5 | .1061 |
| INT4 power-.5 | 196 | .8336 | .027000 | .6 / .5 | .2810 |
| ITQ384 ADC | 48 | .7520 | .063811 | .5 / .3 | .2775 |
| ITQ256 ADC | 32 | .7094 | .082392 | .5 / .3 | .2600 |
| RaBitQ384 | 52 | .7064 | .070214 | .4 / .2 | .2908 |
| BBQ384, FP16 scales | 64 | .7064 | .074002 | .4 / .2 | 1.0410 |
| ITQ384 Hamming | 48 | .6643 | .117956 | .4 / .2 | .2073 |
| ITQ208 ADC | 26 | .6605 | .078232 | .4 / .2 | .3454 |
| RaBitQ256 | 36 | .6559 | .092576 | .4 / .3 | .2827 |
| BBQ256, FP16 scales | 48 | .6544 | .095708 | .4 / .3 | 1.1270 |
| RaBitQ208 | 30 | .6309 | .122469 | .3 / .2 | .3333 |
| BBQ208, FP16 scales | 48 | .6294 | .124152 | .3 / .2 | 1.1952 |
| ITQ256 Hamming | 32 | .6105 | .126894 | .3 / .2 | .2113 |
| PQ4, 16-byte code | 16 | .5754 | .159126 | .3 / .1 | .7130 |
| ITQ208 Hamming | 26 | .5724 | .151977 | .3 / .1 | .1690 |
| OPQ8, 16-byte code | 16 | .5721 | .148416 | .3 / .1 | .6556 |
| PQ8, 16-byte code | 16 | .5689 | .148621 | .3 / .1 | .5303 |
| ITQ ternary128 ADC | 26 | .5526 | .132369 | .3 / .1 | .2234 |
| ITQ128 ADC | 16 | .5515 | .119178 | .3 / .1 | .2184 |
| OPQ4, 16-byte code | 16 | .5419 | .157876 | .3 / .1 | .8197 |
| BBQ128, FP16 scales | 32 | .5390 | .169001 | .2 / .1 | .9194 |
| RaBitQ128 | 20 | .5250 | .182209 | .2 / .1 | .2218 |
| ITQ quaternary104 ADC | 26 | .5237 | .133161 | .2 / .0 | .1972 |
| ITQ128 Hamming | 16 | .4640 | .225945 | .2 / .1 | .1727 |

`INT8 = 388 B/doc` here is not four bytes: it is 384 coordinates × 8 bits =
384 code bytes, plus one four-byte per-document symmetric scale. Likewise,
the other scalar rows report the complete packed 384D record plus its scale.

## Interpretation

The best final-stage family is scalar quantization, and its width/compander
choice differs from the K8 residual result:

- INT12 power-.5 reproduces the FP32 top-10 set on every measured case;
- INT10 remains almost exact at 484 bytes;
- linear INT8 is the smallest measured point above `.99` mean overlap;
- nonlinear power-.5 is worse than linear at 8 bits and below on this narrow
  near-neighbour pool;
- ITQ/RaBitQ/BBQ/PQ compress much harder, but their 16–64 byte points lose too
  much order information for direct final top-10 selection.

Negative nDCG loss means a quantized ordering happened to score slightly
higher against qrels than the FP32 ordering. It does not imply a better general
semantic model; overlap remains the fidelity measurement.

This validates the stage-specific premise: the compact binary families can be
excellent generators on broader document sets, yet scalar precision is more
valuable once only 64 very close documents remain.

## Limitations and next checks

- Both partitions were opened by earlier NeuRoute studies; this is comparative
  engineering evidence, not a new untouched production confirmation.
- Python timing is directional. Native packed/SIMD implementations, including
  physical sub-byte unpack, are needed before choosing by latency.
- The experiment isolates only `ADC64 → top10`. A follow-up should freeze and
  test `candidate pool → 768` and `Hamming768 → 64` independently with the same
  family matrix, then compose the stage-wise winners through the complete
  native cascade.
- PQ/OPQ, RaBitQ, and BBQ-like names retain the research-spec qualifications
  from PR #290; no vendor binary compatibility is claimed.
