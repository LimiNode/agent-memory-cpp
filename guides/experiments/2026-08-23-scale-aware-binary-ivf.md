# Scale-aware BinaryIVF calibration

This external calibration-only experiment tests whether the 25k BinaryIVF
quality/candidate frontier transfers to Spanish 100k and 1M frozen
materializations. It uses Faiss 1.13.2 outside the C++ library, serializes and
reloads every trained index, and records full Hamming/ADC shortlists, exact E5
oracle cache identity, per-query quality contributions, source hashes, and a
deterministic evidence archive.

`nlist` is scale-specific: 1024/4096 at 100k and 4096/16384 at 1M. The standard
ITQ-256 Hamming@768 and binary-ADC@256 cascade is retained. E5 quality uses the
shared shortlist evaluator and its exact Flat E5 oracle cache.

This is not a native latency comparison with MIH/HNSW or a confirmation study.
French data and production selection remain forbidden.

## Earlier exploratory result

The values below are retained as a directional observation from the original
runner. They are not evidence-grade: that runner did not retain per-query
shortlists, ADC ordering, contributions, oracle cache, or a replayable source
archive. They must not be used for algorithm selection.

| scale | BinaryIVF treatment | candidates | E5 survival after ADC | external Faiss p50 / p95 (ms) |
| --- | --- | ---: | ---: | ---: |
| 25k | nlist 4096, nprobe 205 | 5.66% | 92.38% | 0.546 / 1.039 |
| 100k | nlist 4096, nprobe 205 | 5.30% | 87.10% | 0.698 / 1.183 |
| 1M | nlist 16384, nprobe 819 | 5.20% | 87.08% | 2.600 / 3.264 |

At about 10% candidate mass, the exploratory 100k and 1M runs reported 91.57%
and 90.56% respectively with the larger codebook. These figures motivate a
fresh v2 replay; they do not establish transfer by themselves. The timings are
external Python/Faiss measurements, not a direct native MIH/HNSW comparison.

## Evidence-grade rerun

The v2 runner writes a config, serialized index, Hamming+ADC shortlist, quality
report, per-query contribution, and exact E5 oracle cache for every matrix row.
`write-scale-aware-binary-ivf-evidence.py` validates the frozen manifests,
serialized index metadata, quality/contribution bindings, evaluator-source
hashes, oracle cache identity, row summary, and deterministic ZIP membership.
Only a successful fresh v2 rerun and archive may replace the exploratory table.

## 2026-08-24 — v2 replay result

The complete predeclared 12-row 100k/1M matrix completed and the fail-closed
packager accepted every row. The local, unpublish-ed review archive is
`tmp/scale-aware-binary-ivf-v2-evidence.zip`, SHA-256
`43d3686eb5d03238952c616b805022fc65c4f2a61f14ad2d96396a794609ec35`.
It has not been uploaded as a GitHub Release or otherwise published.

| scale | treatment | actual candidates | E5 survival after ADC | reranked nDCG@10 | external Faiss p50 / p95 (ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| 100k | nlist 1024, nprobe 51 | 4.91% | 81.67% | .7077 | .278 / .677 |
| 100k | nlist 4096, nprobe 205 | 5.30% | 87.10% | .7457 | .390 / .971 |
| 100k | nlist 4096, nprobe 410 | 10.30% | 91.57% | .7614 | .533 / 1.321 |
| 100k | nlist 4096, nprobe 1024 | 24.89% | 95.74% | .7794 | .860 / 2.200 |
| 1M | nlist 4096, nprobe 205 | 5.01% | 83.12% | .6496 | .671 / 1.583 |
| 1M | nlist 16384, nprobe 819 | 5.20% | 87.08% | .6602 | 1.196 / 2.956 |
| 1M | nlist 16384, nprobe 1638 | 10.22% | 90.56% | .6783 | 1.900 / 4.666 |
| 1M | nlist 16384, nprobe 4096 | 25.07% | 93.21% | .6892 | 3.919 / 9.813 |

The retained representative points show that data-dependent binary lists remain
strongly enriched relative to random filtering, but 100k/1M require more
candidate mass than the 25k exploratory point to approach the stricter 90%
survival target. This is calibration-only evidence, not a backend selection or
a native latency claim. The separate float semantic IVF control therefore tests
whether the remaining loss comes from binary routing rather than the semantic
coarse partition itself.
