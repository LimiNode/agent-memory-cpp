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
