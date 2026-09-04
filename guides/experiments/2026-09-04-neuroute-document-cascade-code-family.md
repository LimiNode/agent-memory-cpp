# NeuRoute document-cascade code-family bake-off

Date: 2026-09-04. Stacked after PR #291.

## Question

Earlier work compared the final `ADC64 -> top10` rerank, but the actual R4
document cascade still had two untested decisions:

```text
candidate documents (~5k) -> 512 / 768 / 1024
frozen Hamming768          ->  32 /  64 /  128
```

This study applies the complete codec matrix to both stages, measures final
qrels quality after exact reranking, and then composes representative stage
winners. It tests whether the historical `Hamming768 -> ADC64` sequence is
still the right document-level cascade.

## Setup

- actual-R4 DE 1M native candidate pools;
- 76 configuration and 76 previously opened internal queries;
- three routing seeds, for 456 cases in total;
- 4,978.09 candidate documents per case on average (4,870 to 5,000);
- one deterministic, label-free 16,384-document training sample;
- identical input documents and queries for all 36 methods;
- FP32 over the complete candidate pool is the representation oracle;
- qrels nDCG@10, top-10 overlap, top-1 agreement, p05, worst-query, and
  maximum per-query nDCG loss are retained separately for configuration and
  internal replay;
- database encoding is offline and excluded from query timing.

The runner is
`tools/agent-memory-bench/run-neuroute-document-cascade-code-family.py` and its
contract is
`tools/agent-memory-bench/neuroute-document-cascade-code-family.example.json`.
The score cache is restartable and is bound to the ordered candidate-pool
hashes, training seed, and training count.

Raw output is `tmp/document-cascade-code-family-v5.json` (not committed),
SHA-256 `5a226c88a559cf10a579aef2476b5c302d612b2bef236c24390e852600412b7b`.

## Full isolated comparison

The table uses the central `~5k -> 768` and `historical Hamming768 -> 64`
points. `Stage ov.` is overlap with the FP32 top-K at that stage. `Exact10`
is top-10 overlap with the complete candidate-pool FP32 oracle after an exact
rerank of the selected documents. Positive delta nDCG means worse than that
oracle. The Hamming input has already lost some candidate-oracle documents, so
even FP32 in the second lane is capped at `0.9930` Exact10.

Python p95 includes portable scoring and all three K selections in the lane.
It is useful for comparing these research implementations, but it is not a
native packed/SIMD latency claim.

| Method | B/doc | ~5k->768 stage ov. | Exact10 | delta nDCG | H768->64 stage ov. | Exact10 | delta nDCG | Python p95 ms, stage1/stage2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BBQ128 FP16 scales | 32 | .4777 | .9871 | +.000822 | .4481 | .8395 | +.032250 | 5.33 / 1.74 |
| BBQ208 FP16 scales | 48 | .5536 | .9967 | -.000389 | .5335 | .9191 | +.006567 | 5.66 / 1.79 |
| BBQ256 FP16 scales | 48 | .5804 | .9993 | +.000000 | .5650 | .9408 | +.001133 | 5.65 / 1.85 |
| BBQ384 FP16 scales | 64 | .6451 | 1.0000 | +.000000 | .6388 | .9704 | +.002880 | 6.51 / 1.96 |
| FP16 | 768 | .9996 | 1.0000 | +.000000 | .9996 | .9930 | +.002156 | 10.76 / 2.08 |
| FP32 | 1536 | 1.0000 | 1.0000 | +.000000 | 1.0000 | .9930 | +.002156 | 3.08 / 1.57 |
| INT10 linear | 484 | .9980 | 1.0000 | +.000000 | .9974 | .9930 | +.002156 | 9.05 / 1.82 |
| INT10 power-.5 | 484 | .9975 | 1.0000 | +.000000 | .9961 | .9930 | +.002156 | 27.00 / 4.72 |
| INT12 linear | 580 | .9994 | 1.0000 | +.000000 | .9994 | .9930 | +.002156 | 9.22 / 1.89 |
| INT12 power-.5 | 580 | .9993 | 1.0000 | +.000000 | .9995 | .9930 | +.002156 | 26.33 / 4.67 |
| INT4 linear | 196 | .8721 | 1.0000 | +.000000 | .8565 | .9930 | +.002156 | 9.60 / 1.93 |
| INT4 power-.5 | 196 | .8399 | 1.0000 | +.000000 | .8205 | .9925 | +.002156 | 36.92 / 7.04 |
| INT5 linear | 244 | .9387 | 1.0000 | +.000000 | .9288 | .9930 | +.002156 | 11.54 / 2.35 |
| INT5 power-.5 | 244 | .9237 | 1.0000 | +.000000 | .9111 | .9930 | +.002156 | 31.39 / 5.94 |
| INT6 linear | 292 | .9700 | 1.0000 | +.000000 | .9650 | .9930 | +.002156 | 9.85 / 1.97 |
| INT6 power-.5 | 292 | .9624 | 1.0000 | +.000000 | .9537 | .9930 | +.002156 | 26.94 / 4.74 |
| INT8 linear | 388 | .9924 | 1.0000 | +.000000 | .9900 | .9930 | +.002156 | 9.06 / 1.81 |
| INT8 power-.5 | 388 | .9905 | 1.0000 | +.000000 | .9871 | .9930 | +.002156 | 25.97 / 4.65 |
| ITQ128 ADC | 16 | .5687 | .9910 | +.001289 | .4947 | .8485 | +.018550 | 2.65 / .70 |
| ITQ128 Hamming | 16 | .4916 | .9566 | +.007298 | .4064 | .7270 | +.044422 | 1.97 / .60 |
| ITQ208 ADC | 26 | .6230 | .9998 | -.000016 | .5995 | .9439 | +.012906 | 3.15 / .79 |
| ITQ208 Hamming | 26 | .5442 | .9919 | +.000302 | .5042 | .8794 | +.021756 | 2.55 / .73 |
| ITQ256 ADC | 32 | .6464 | 1.0000 | +.000000 | .6304 | .9664 | +.004621 | 3.38 / .85 |
| ITQ256 Hamming | 32 | .5649 | .9974 | -.000043 | .5359 | .9048 | +.011880 | 2.53 / .68 |
| ITQ384 ADC | 48 | .6823 | 1.0000 | +.000000 | .6889 | .9825 | +.001636 | 4.37 / 1.07 |
| ITQ384 Hamming | 48 | .6054 | .9987 | +.000000 | .5994 | .9526 | +.010327 | 3.03 / .80 |
| ITQ quaternary104 ADC | 26 | .5823 | .9906 | +.001387 | .4888 | .8322 | +.023041 | 8.12 / 1.35 |
| ITQ ternary128 ADC | 26 | .5997 | .9943 | -.000032 | .5305 | .8818 | +.014768 | 9.37 / 1.57 |
| OPQ4, 16-byte code | 16 | .5470 | .9908 | +.000811 | .4884 | .8478 | +.009072 | 7.23 / 2.24 |
| OPQ8, 16-byte code | 16 | .5814 | .9943 | +.000468 | .5172 | .8763 | +.010987 | 5.27 / 1.94 |
| PQ4, 16-byte code | 16 | .5298 | .9875 | +.000882 | .4852 | .8599 | +.010640 | 7.32 / 2.23 |
| PQ8, 16-byte code | 16 | .5523 | .9928 | -.000149 | .4829 | .8610 | +.020656 | 5.38 / 1.89 |
| RaBitQ128 | 20 | .4759 | .9877 | -.000394 | .4428 | .8362 | +.031287 | 2.68 / .74 |
| RaBitQ208 | 30 | .5521 | .9969 | -.000389 | .5325 | .9211 | +.006429 | 3.12 / .83 |
| RaBitQ256 | 36 | .5785 | .9991 | +.000000 | .5640 | .9410 | +.003140 | 3.47 / .89 |
| RaBitQ384 | 52 | .6438 | 1.0000 | +.000000 | .6380 | .9713 | +.004329 | 4.18 / 1.00 |

Every scalar payload is the complete packed 384-coordinate record plus one
FP32 per-document scale. In particular, INT8 is `384 + 4 = 388 B/doc`, not
four bytes. BBQ and RaBitQ retain their local research-spec qualifications;
these rows do not claim compatibility with vendor implementations.

## Composed document profiles

Unlike the isolated second lane, these profiles feed each stage from the
actual output of the previous one. Exact profiles retain the existing
1,536-byte E5 source vector and report the auxiliary code separately.
Non-exact profiles can discard that source vector after indexing.

| Profile | Config/internal overlap | p05/worst | nDCG@10 | delta nDCG | Max query loss | Read KiB/request | Auxiliary B/doc | Resident MiB/1M |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FP32 control: 512 -> 32 -> 10 | 1.0000 / 1.0000 | 1.0 / 1.0 | .639377 | +.000000 | .0000 | 8283.1 | 1536 | 1464.8 |
| INT4 linear: 512 -> 64 -> exact10 | 1.0000 / 1.0000 | 1.0 / 1.0 | .639377 | +.000000 | .0000 | 1146.8 | 196 | 1651.8 |
| ITQ208 ADC: 768 -> INT4: 64 -> exact10 | 1.0000 / .9996 | 1.0 / .9 | .639393 | -.000016 | .0000 | 369.4 | 222 | 1676.6 |
| INT12 power-.5: 512 -> 32 -> top10 | 1.0000 / .9987 | 1.0 / .9 | .640495 | -.001118 | .0000 | 3127.7 | 580 | 553.1 |
| ITQ208 ADC: 768 -> INT4: 64 -> INT10: top10 | .9943 / .9961 | 1.0 / .9 | .640588 | -.001212 | .0120 | 303.6 | 706 | 673.3 |
| INT8 linear: 512 -> 32 -> top10 | .9895 / .9930 | .9 / .9 | .641004 | -.001627 | .3691 | 2092.4 | 388 | 370.0 |
| ITQ208 ADC -> ITQ384 ADC -> INT8 | .9864 / .9917 | .9 / .8 | .641020 | -.001643 | .3691 | 210.9 | 462 | 440.6 |
| ITQ128 ADC -> OPQ8 -> INT8 | .9118 / .9360 | .7 / .4 | .635771 | +.003606 | .4307 | 142.3 | 420 | 400.5 |

The historical native `Hamming768 -> ADC64 -> exact10` profile has only
`.9316/.9596` configuration/internal overlap (`.9456` overall), p05/worst
`.8/.6`, nDCG `.635422`, mean loss `+.003955`, and maximum query loss `.3869`
against the same candidate-pool oracle. All four recommended profiles below
improve it materially.

## Interpretation

1. **A generator and a narrow reranker need different representations.** At
   `~5k -> 768`, ITQ208 ADC needs only 26 bytes and still preserves `.9998` of
   the final exact top-10. At `768 -> 64`, the same family is only `.9439`;
   scalar codes are much safer among close neighbours.
2. **ADC, not plain Hamming, is the important part of ITQ here.** At identical
   widths, every ITQ ADC point is materially better than Hamming. The old
   Hamming-first document cascade is no longer on the measured Pareto frontier.
3. **Low stage overlap can still be sufficient.** INT4 linear retains only
   `.8721` of the FP32 top-768, yet retains every FP32 top-10 document at that
   budget. Stage overlap remains a diagnostic, while exact final quality is the
   product gate.
4. **Linear scalar quantization wins below 8 bits on document stages.** The
   power-.5 transform is consistently slower in this Python reference and has
   lower stage overlap. Power-.5 remains useful at the 12-bit direct-final
   point; there is no universal compander winner.
5. **Mean nDCG alone is unsafe.** INT8's mean nDCG happens to improve, but its
   maximum query loss is `.3691`. The lossless INT4-filter-plus-exact profile
   and the INT12 no-source-vector profile have zero positive maximum loss on
   this matrix.
6. **RaBitQ/BBQ are valid but not document-stage winners here.** Their quality
   rises through 384 bits, but ITQ ADC is smaller or better at the useful
   generator points. This does not invalidate their K8/local-residual use.

## Recommended complete retrieval profiles

All profiles keep the product routing line established by #269: float IVF over
K8 prototypes, followed by local exact K8 and the frozen K32/R0 address logic.
They do **not** globally scan all 454k K8 prototypes. The document choices below
begin after address postings have produced the roughly 5k-document pool.

### Quality-first, zero measured document-stage loss

```text
float K8-prototype IVF -> local exact K8 -> K32/R0 -> postings
-> INT4-linear top512 -> INT4-linear top64 -> exact FP32 top10
```

One 196-byte auxiliary code serves both filtering stages. It reproduced the
candidate-pool FP32 top-10 exactly in all 456 cases and reduced logical query
reads from about 8.3 MiB to 1.15 MiB. It still retains the 1,536-byte FP32 E5
source vector for the final 64-document read.

### Bandwidth-first, near-exact with an exact final read

```text
float K8-prototype IVF -> local exact K8 -> K32/R0 -> postings
-> ITQ208 binary ADC top768 -> INT4-linear top64 -> exact FP32 top10
```

This reads about 369 KiB per request and adds 222 B/doc beside the source E5
vector. Overall overlap is `.99978`; p05 is `1.0`, worst is `.9`, and no case
has positive nDCG loss. It is preferred when memory bandwidth is more important
than one observed top-10 substitution in 456 cases.

### Source-vector-free, quality-oriented

```text
float K8-prototype IVF -> local exact K8 -> K32/R0 -> postings
-> INT12 power-.5 top512 -> INT12 power-.5 top32 -> INT12 power-.5 top10
```

The same 580-byte record serves all document stages, so the document vector
store is about 553 MiB per million documents and no FP32 document vector is
required. Overall overlap is `.9993`, p05 `1.0`, worst `.9`, with zero positive
maximum qrels loss on this fixture.

### Aggressive source-vector-free throughput profile

```text
float K8-prototype IVF -> local exact K8 -> K32/R0 -> postings
-> ITQ208 binary ADC top768 -> INT4-linear top64 -> INT10-linear top10
```

This reads about 304 KiB/request and stores 706 B/doc across three records.
It retains `.9952` overall overlap, p05 `1.0`, worst `.9`, and maximum query
loss `.0120`. It is a deliberate quality/complexity trade, not the default.

The single-INT8 and fully binary/PQ profiles are not recommended defaults:
their means look attractive, but their worst-query losses are too large.

## Relationship to K8 routing evidence

The codec winner is stage-specific. #269 showed that float prototype IVF can
preserve approximately `.9996` of the successful K8 routing result at
`M=4096`. #290 showed promising local-residual scalar results, but its Python
NPZ lane was not an authoritative native full-R4 replay. Therefore:

- use the proven float-IVF plus local-exact-K8 path for the upper part now;
- keep FP16/INT8 residual K8 storage as an opt-in follow-up until native
  prototype-to-address-to-R4 replay confirms it;
- do not revive a global K8 prototype scan as a product route;
- do not infer document-stage winners from K8-prototype results or vice versa.

The complete profiles above are component-validated recommendations. A final
native integration must still run the selected float-IVF router and selected
document profile in one process before claiming an end-to-end production p95.

## Limitations and next checks

- Both query partitions were opened by earlier studies. These results are
  comparative engineering evidence, not an untouched production confirmation.
- The candidate-pool FP32 oracle is stronger than the historical document
  cascade, but it is not a corpus-wide exact-search oracle.
- Python/NumPy p95 is directional. BLAS makes FP32 look artificially strong;
  scalar sub-byte decoding is not a native packed SIMD kernel. Native p95 and
  actual bytes loaded must decide between the first two profiles.
- PQ preparation now uses Faiss' native packed 16-byte encoder; the query
  scorer reads the physical nibble-packed PQ4 form. No new project dependency
  was added: Faiss remains an offline research-runner dependency.
- A new untouched dataset should confirm the chosen profile, especially its
  p05/worst-query behavior, before a production default changes.
