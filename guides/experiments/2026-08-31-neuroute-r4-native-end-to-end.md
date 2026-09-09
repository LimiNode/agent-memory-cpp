# NeuRoute R4 native end-to-end benchmark

## 2026-08-31 — stacked draft PR after #249

### Question

Do the independently measured R4 optimizations compose across the complete
frozen DE-1M cascade, and does the faster AVX2 representative reduction change
retrieval quality?

The compared paths were:

1. `seek/read -> INT8-to-FP32 decode -> scalar representative dot -> scalar R0 scorer`;
2. `raw INT8 mmap-direct -> fused scalar dot -> batched AVX2 R0 scorer`;
3. the second path with the faster AVX2 tree-reduction representative dot.

Every path then executed the same strict `.005` address-prefix boundary,
Hamming top-768, binary ADC top-64, and exact E5 top-10 cascade. The strict
path was expected to be bit-identical to the native baseline. The AVX2 path
was preregistered as a quality-gated sensitivity treatment because #246 found
small absolute score differences.

### Setup

- Dataset: frozen DE-1M, 1,000,000 documents and 76 internal queries.
- Seeds: `2026082701`, `2026082702`, and `2026082703`.
- Frozen router: 16-bit partition, K8 coarse shortlist, 1,024 addresses,
  FF32 actual-document basis, learned R0 plus normalized K32 max-cosine.
- Physical representative format: raw address-major INT8, 388 bytes/record.
- Candidate and cascade limits: `5000 -> 768 -> 64 -> 10`.
- Timing: one warm-up pass and three measured passes; fresh-process samples
  used 15 deterministically selected queries per seed and treatment with OS
  page-cache state uncontrolled.
- Concurrency: 1, 2, 4, and 8 workers, three measured 76-query batches per
  seed/treatment/worker count.
- Host: Windows 10 build 26100, Intel Xeon E5-2696 v3, 18 cores / 36 logical
  processors, Visual Studio 17 2022 Release build with AVX2 enabled for the
  benchmark translation unit.
- Hamming backend: POPCNT.

Protocol and commands:

```text
tools/agent-memory-bench/neuroute-r4-native-end-to-end.example.json
py -3 tools/agent-memory-bench/materialize-neuroute-r4-native-end-to-end.py ...
py -3 tools/agent-memory-bench/run-neuroute-r4-native-end-to-end.py ...
py -3 tools/agent-memory-bench/write-neuroute-r4-native-end-to-end-evidence.py ...
```

Raw local artifacts are under `tmp/neuroute-r4-native-end-to-end/` and are not
committed. Their stable identities are:

- materialization manifest: `87616ce4290b07c0096490e06a03dcde0c5e90bce7f3fb754e779bb1ee7493f9`;
- warm report: `d20d3006d7fe668cfdab76c5cad8bd118e5f58e6d9eea491d75453bde7d6dfaf`;
- result: `da6d824c1025836abf180231c9abc473927c0d06057300c3820ce2f0e25b0ba5`;
- evidence: `fbfdd3e14babf9fb7fdfad40b36e80bd290aaa472f48895a1e34cf13d57e8fec`.

### Results

Warm per-query latency across all seeds and measured passes:

| Path | p50, ms | p95, ms | p95 speedup | mean nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| baseline seek/decode/scalar | 56.588 | 63.008 | 1.00x | 0.650652 |
| strict mmap/fused/batched | 16.042 | 17.452 | 3.61x | 0.650652 |
| fast AVX2 sensitivity | 9.787 | 10.423 | 6.05x | 0.650652 |

The p95 stage decomposition shows where the gain came from:

| Stage | baseline, ms | strict, ms | fast AVX2, ms |
| --- | ---: | ---: | ---: |
| representative fetch | 19.078 | 0.0001 | 0.0001 |
| representative decode | 18.265 | 0 | 0 |
| representative dot/max | 10.802 | 9.397 | 2.392 |
| learned address scorer | 13.015 | 6.675 | 6.589 |
| Hamming top-768 | 0.564 | 0.546 | 0.522 |
| binary ADC top-64 | 0.486 | 0.512 | 0.520 |
| exact E5 top-10 | 0.077 | 0.074 | 0.073 |

The stage percentiles are marginal distributions and therefore do not sum to
the total p95.

Concurrency scaled without a material strict-path latency collapse:

| Path | 1-worker p50 qps | 1-worker p95 latency, ms | 8-worker p50 qps | 8-worker p95 latency, ms |
| --- | ---: | ---: | ---: | ---: |
| baseline | 18.2 | 60.46 | 100.9 | 92.50 |
| strict | 62.4 | 17.39 | 453.7 | 18.21 |
| fast AVX2 | 102.5 | 10.40 | 759.5 | 11.02 |

Fresh-process first-query p95 was 68.77 ms for the baseline, 31.86 ms for the
strict path, and 25.93 ms for the fast AVX2 path. These are not cold-disk
numbers; process launch plus input/model setup dominated the external launch
measure and OS page-cache state was intentionally uncontrolled.

### Determinism and parent replay

The baseline and strict native paths had identical score, selected-address,
candidate, Hamming, ADC, and exact-result hashes for every paired sample.
Candidate counts and final nDCG exactly replayed the frozen INT8 parent for all
228 seed/query pairs.

The exact selected-address sequence matched the NumPy INT8 parent for 199 of
228 pairs (`87.28%`). The other 29 pairs had the same candidate count and final
nDCG. This is recorded as a diagnostic rather than hidden behind an exact
parent-order claim: the native float32 accumulation order is not the NumPy
`einsum` accumulation order. It does not invalidate the native baseline versus
strict comparison because those two native paths are bit-identical.

Despite the maximum absolute scorer-error gate failure in isolated #246, the
fast AVX2 path had zero mean and every-seed nDCG loss in this complete cascade.
It therefore passes the preregistered quality gates in this study, but remains
a sensitivity treatment until review.

### Interpretation

The three optimization directions compose. The old ~66.7 ms representative
stage was primarily a physical-access and decode problem, not a downstream
Hamming/ADC/exact bottleneck. Mmap-direct access removes the per-address system
reads, fused INT8 scoring removes the full float workspace, and the batched
scorer halves the remaining model cost. The AVX2 tree reduction removes most of
the last representative-dot cost and, in this frozen matrix, does not change
the final ranking quality.

The earlier #249 lossless SIMDComp experiment remains negative: fixed-width
BP128 saved no bytes, adaptive FOR/zigzag grew the store after offsets, and all
variants slowed the warm path. Raw INT8 is therefore the selected physical
format for this end-to-end benchmark.

### Limitations and next checks

- Timing is directional local evidence from one Windows/AVX2 host.
- Fresh-process measurements do not control the OS page cache and are not
  cold-disk measurements.
- The 1/2/4/8-worker test uses independent queries and read-only shared maps;
  it does not include a production request queue or admission control.
- The NumPy/native selected-address order discrepancy should be retained in
  paper wording even though final nDCG replayed exactly.
- Production selection remains forbidden until review and an independent host
  replay; no PR in this stack is merged.
