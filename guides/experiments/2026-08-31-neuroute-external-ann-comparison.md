# NeuRoute R4 versus external ANN baselines

## Context

- Date: 2026-08-31.
- PR context: #262, stacked on #261.
- Dataset: frozen DE-1M E5, 1,000,000 normalized 384-dimensional
  document vectors and 76 internal evaluation queries.
- Scope: full NeuRoute R4, Faiss exact Flat, Faiss float IVF/HNSW, Faiss
  binary Flat/IVF/HNSW, and the historical native MIH control.
- Explicitly excluded: ScaNN, DiskANN, BM25, WAND, and BMW.

The comparison was intended to measure the complete retriever, including the
query-dependent K8 coarse shortlist. During implementation it became clear
that the earlier approximately 10--12 ms native R4 result started after that
shortlist had already been loaded from a persisted protocol. It therefore
measured only:

```text
K32 representative scoring -> R0 scorer -> candidates
-> Hamming768 -> ADC64 -> final top10
```

The #262 timer instead starts before a physical full scan of the persisted K8
prototype store and includes the runtime top-1024 address selection and all 22
coarse features.

## Question

How does the complete frozen R4 cascade compare with established external ANN
baselines on quality, single-query latency, batch throughput, build cost, and
the major retrieval payload? Does either the INT8 or nonlinear-INT5 routing
store dominate across all of those axes?

## Setup

The frozen K8 geometry is one centroid plus seven farthest-first document
prototypes per occupied 16-bit address. The materializer reconstructed the
active address-major FP32 records and verified their dense-prototype SHA-256
against the earlier evidence for all three seeds. The physical K8 stores were
685.6--702.7 MB.

The complete R4 cascade was:

```text
physical K8 FP32 scan -> top1024 addresses
-> K32 FF actual-document representatives -> learned R0/max scorer
-> approximately 5000 candidates -> Hamming768 -> ADC64
-> symmetric per-document INT8 final top10
```

The routing representation remained an explicit treatment:

- homogeneous symmetric per-document INT8;
- mixed nonlinear INT5 power `.5` representatives with INT8 remainder.

The final document codec was corrected to physical document-major INT8 after
the actual R4 ADC64 pools exposed a transfer failure in the previously selected
uniform INT5 final codec. This final-codec decision is separate from the
user-selected routing representation.

Faiss float ANN points generated 5,000 candidates and used an exact FP32
downstream top10. Faiss binary ANN points used the same Hamming768, ADC64, and
exact FP32 top10 semantics. Artifact accounting includes the downstream FP32
document store when the harness requires it. The R4 payload includes K8,
routing records and mappings, final INT8 records, binary codes, and document
rank metadata.

Raw artifacts remain under
`tmp/neuroute-external-ann-comparison/` and are intentionally not committed.
The replay chain is:

- `materialize-neuroute-k8-coarse.py`;
- `evaluate-neuroute-external-baseline.py`;
- `analyze-neuroute-r4-final-codec-transfer.py`;
- `summarize-neuroute-external-comparison.py`;
- `write-neuroute-external-comparison-evidence.py`.

## Results

The table reports p95 for one worker, mean quality over the three R4 seeds
where applicable, complete major payload, and eight-worker throughput.

| Treatment | p95 ms | Candidate recall | Final overlap | nDCG@10 | Payload MiB | QPS, w8 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| R4 routing INT8 | 73.44 | 0.9035 | 0.8763 | 0.6516 | 1437.5 | 100.8 |
| R4 routing nonlinear INT5 | 81.56 | 0.9026 | 0.8750 | 0.6507 | 1315.9 | 93.6 |
| Faiss exact Flat | 145.86 | 1.0000 | 1.0000 | 0.6610 | 1464.8 | 41.8 |
| Faiss float IVF, nprobe 128 | 15.48 | 0.9132 | 0.9132 | 0.6599 | 2943.3 | 316.4 |
| Faiss float IVF, nprobe 512 | 30.44 | 0.9737 | 0.9737 | 0.6685 | 2943.3 | 176.4 |
| Faiss float HNSW, ef 512 | 21.01 | 0.9974 | 0.9974 | 0.6611 | 3189.2 | 266.4 |
| Faiss binary Flat | 8.42 | 0.9316 | 0.9013 | 0.6431 | 1495.4 | 523.4 |
| Faiss binary IVF, nprobe 512 | 6.97 | 0.8961 | 0.8355 | 0.6233 | 1503.1 | 596.6 |
| Faiss binary HNSW, ef 512 | 16.88 | 0.9776 | 0.9013 | 0.6457 | 1754.9 | 338.7 |
| Historical MIH, m19/r56 | 25.74 | 0.9263 | 0.8974 | 0.6432 | 1539.0 | not measured |

For routing INT8, the K8 coarse stage alone had p95 62.65 ms. The old
post-shortlist cascade remained p95 11.11 ms. For nonlinear INT5 those values
were 69.15 and 12.80 ms. The exact K8 scan therefore dominates the complete R4
latency and reverses any interpretation based only on the old approximately
10 ms partial timer.

### Final-codec transfer correction

The same 228 actual R4 `seed x query` ADC64 pools were replayed against FP32,
physical INT8, and physical uniform INT5:

| Final codec | Mean nDCG@10 | Loss versus FP32 | Mean top10 overlap | Changed top10 queries |
| --- | ---: | ---: | ---: | ---: |
| FP32 | 0.650652 | 0 | 1.0000 | 0 |
| symmetric per-document INT8 | 0.651576 | -0.000924 | 0.9961 | 9 / 228 |
| uniform INT5 SIMDComp | 0.632126 | 0.018526 | 0.9456 | 110 / 228 |

INT8 passed the corrective per-seed aggregate loss cap of 0.003; its worst
seed mean loss was 0.001669. Uniform INT5 failed on every seed with mean loss
between 0.01749 and 0.01925. The INT8 correction was selected after observing
the failure, so it is explicitly post-hoc and still needs independent held-out
revalidation. The maximum single-query INT8 loss was 0.17724 despite 0.9
top10 overlap for that query; aggregate quality must not hide that tail.

## Interpretation

There is no universal winner.

- Mature Faiss float ANN is substantially faster and reaches higher quality on
  this host, at roughly twice the complete R4 payload.
- Faiss binary Flat/IVF is much faster and compact at the index level, but its
  complete harness payload includes the FP32 final store and its quality is
  lower than R4 at the stronger measured points.
- R4 remains on the artifact-bytes/nDCG Pareto frontier, but not on the
  p95-latency/nDCG frontier.
- R4 routing INT8 is the faster resident configuration in this run.
  Nonlinear INT5 remains the smaller explicit configuration and retains its
  previously measured advantage under memory pressure. No automatic RAM-based
  runtime switching is licensed.
- The current exact K8 coarse scan is the dominant implementation and
  algorithmic bottleneck. Optimizing the approximately 10--12 ms downstream
  cascade cannot make the full design latency-competitive by itself.

## Limitations and threats to validity

- Faiss and its downstream cascade are Python-orchestrated; R4 is native C++.
  Cross-runtime latency is directional rather than a clean kernel-only result.
- Faiss build memory records a legacy post-build RSS delta, not a sampled peak.
- R4 K8 uses a full FP32 prototype scan. This result does not estimate a future
  approximate or compressed K8 implementation.
- Timing is from one Windows host. The harness has warm-up and repeated query
  samples, but host-level interference remains possible.
- The 76-query partition is internal. The corrective final INT8 decision is
  not a replacement for an independently selected held-out evaluation.
- Historical MIH latency comes from the original 305-query parent run; only
  quality was replayed on the common 76-query partition.

## Decision and follow-up

For the measured dense branch:

- preserve user configuration of routing INT8 versus nonlinear INT5 power
  `.5`;
- use symmetric per-document INT8 for the full-R4 final stage;
- retain uniform final INT5 as a negative transfer result, not a production
  default;
- keep portable C++17 as the default build and AVX2 as explicit CMake opt-in;
- stop treating the downstream-cascade implementation ceiling as the full
  retriever ceiling;
- close the current dense research batch with #263 interpretation, while
  recording approximate/compressed K8 as the only material dense latency
  reopening condition.

## #263 dense-policy closure

#263 binds the replayed #259--#262 evidence without adding another workload or
silently broadening the claim. The closed policy for the current DE-1M R4
design is:

| Concern | Policy |
| --- | --- |
| Routing storage | User explicitly selects `int8` or `nonlinear_int5_power_half` |
| Automatic memory selector | Disabled; not licensed by the measured crossover alone |
| Default execution | Portable C++17 |
| Optional execution | SSE2 when available; AVX2 only after explicit CMake opt-in |
| Persisted format | Independent of the selected execution kernel |
| Final document codec | Symmetric per-document INT8 |
| Uniform final INT5 | Negative actual-pool transfer control |
| Current K8 | Persisted FP32 full scan, included in the complete timer |
| Dense status | Closed for the current exact-K8 design |

“Closed” means that the measured branch has a recorded algorithm, physical
format, execution policy, full timer, and external comparison. It does not mean
that R4 is a universal winner or that the post-hoc final INT8 correction has
independent held-out licensing.

The dense branch reopens only for one of four explicit reasons:

1. an independent held-out evaluation invalidates final INT8;
2. approximate or compressed K8 is proposed with a full-cascade quality gate;
3. a new workload or hardware target changes the Pareto frontier;
4. ScaNN or DiskANN is explicitly brought into scope.

Lexical BM25/WAND/BMW work remains deferred and was not started by #263.

## 2026-09-01 K8 and implementation closure replay

The full NeuRoute rows were rerun with the current native executable after the
exact full-scan and K1/K2-prefilter K8 frontier plus implementation audit.
External Faiss and
historical MIH reports are byte-identical reused controls; ScaNN and DiskANN
remain excluded. The selected K8 policy is the exact physical FP32 K8 fallback
because neither exact compressed K8 nor the K1/K2 approximate frontier met the
registered quality and 15 ms conditions.

This fallback does not close prototype ANN or specialized compressed-K8
kernels: K4-to-K8 prefiltering, HNSW/IVF/binary prototype indexes and fused
fixed-width decoders remain unmeasured.

| Engine / policy | Mean nDCG@10 | Top10 overlap | p95 ms, w1 | Mean major payload |
| --- | ---: | ---: | ---: | ---: |
| NeuRoute R4 / INT8 routing | .651576 | .8763 | 81.863 | 1.507 GB |
| NeuRoute R4 / nonlinear INT5 routing | .650710 | .8750 | 84.753 | 1.380 GB |
| Faiss binary flat | .643086 | .9013 | 8.423 | 1.568 GB |
| Historical MIH m19/r56 | .643241 | - | 25.737 | see raw report |
| Faiss float IVF, nprobe 128 | .659919 | - | 15.483 | 3.086 GB class |
| Faiss float IVF, nprobe 512 | .668508 | .9737 | 30.445 | 3.086 GB |
| Faiss exact flat | .661003 | 1.0000 | 145.862 | 1.536 GB |

NeuRoute remains faster than Faiss exact flat and retains a smaller compact
INT5 payload, but the coarse stage dominates: p95 is `71.936 ms` for the INT8
mode and `72.683 ms` for the INT5 mode, while the post-shortlist cascade is
`10.329/12.332 ms`. The full request does not meet the intended latency target
and is not on the p95-latency-versus-nDCG Pareto frontier. It remains on the
artifact-bytes-versus-nDCG frontier under the harness accounting.

The cross-runtime comparison remains directional: Faiss rows are Python
orchestrated, NeuRoute is native C++, build peak RSS was not measured, and all
timings are from one Windows host. The result supports an engineering policy,
not a universal ANN ranking.

Replayable local artifacts:

- `tmp/neuroute-external-ann-comparison/result-k8-closure.json`, SHA-256
  `d970530dca2f60f5d50ac01451f26750a65208ea95c2440ced2cd5e3abc3b627`;
- `tmp/neuroute-external-ann-comparison/evidence-k8-closure.json`, SHA-256
  `20493f82531aeef06bdc5090d6d0a5c823e11c3f85b59ef808ec513f3c7e4d65`.
