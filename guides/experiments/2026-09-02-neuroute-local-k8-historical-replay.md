# NeuRoute historical-router to local-K8 replay

Date: 2026-09-02

## Question

Can an address router reduce the dominant global FP32 K8 scan to exact K8 over
at most 8,192 shortlisted occupied addresses while preserving the frozen full
R4 cascade?

## Provenance boundary

The serialized PR #203/#217/#223 router checkpoints are no longer available:
their GitHub Actions artifacts expired and no corresponding evidence release
was published. This experiment therefore does **not** claim a byte-identical
replay of those models. It reconstructs the documented historical recipes on
the current frozen DE-1M topology and labels them accordingly.

The configuration half contains requests 0--75. Only those query vectors and
their global FP32-K8 top-1024 rows train the two bit-router reconstructions.
Requests 76--151 remain locked until configuration selection. Centroid K1,
posting-mass, and stable-random controls require no query training.

## Frozen cascade and native diagnostic hook

For every seed, router, and address budget, the benchmark:

1. materializes a deterministic ordered list of occupied address rows;
2. scores the physical FP32 K8 records only within that list;
3. selects the best 1,024 addresses by exact local K8;
4. runs unchanged INT8 K32/R0, posting materialization, Hamming768, ADC64, and
   exact final reranking.

The C++ hook accepts a hash-bound external shortlist manifest solely for this
diagnostic replay. It verifies shape, row range, uniqueness, file size, and
payload SHA-256 before execution. Checkpoints bind the complete protocol chain,
authoritative qrels receipt, native executable, shortlist bytes, and Python
source bytes.

Budgets are 2,048, 4,096, 8,192, and 16,384. Only a locked-internal pass at or
below 8,192 can license #270 native integration. The 16,384 row is a sensitivity
control.

## Result

No reconstructed router passed the registered locked-internal gate. The compact
result has SHA-256
`22891e6fedc19576bb497dc956c501a81c782ed69f285e764bf7dae207eac6e6`;
the independently validated evidence has SHA-256
`a67ecf2f00556e843202d78854c65592221f8646f0cbe9344c0657000344e370`.

The configuration rows at the maximum eligible budget were:

| Router, M=8192 | Final top-10 overlap | Candidate retention | Hamming overlap | ADC overlap | Mean / worst-stratum nDCG loss | Local K8 p95 | Total p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Hard-Hamming reconstruction | 0.8434 | 0.8761 | 0.8926 | 0.8738 | 0.0688 / 0.0941 | 27.35 ms | 50.71 ms |
| Occupied-bit logit reconstruction | 0.9522 | 0.9727 | 0.9751 | 0.9653 | 0.0116 / 0.0263 | 28.73 ms | 52.00 ms |
| Centroid K1 | 0.9842 | 0.9960 | 0.9963 | 0.9927 | 0.0012 / 0.0022 | 31.02 ms | 54.62 ms |
| Posting mass | 0.2570 | 0.5962 | 0.5884 | 0.4087 | 0.3907 / 0.4280 | 31.84 ms | 60.52 ms |
| Stable random | 0.1263 | 0.1262 | 0.1273 | 0.1333 | 0.4952 / 0.5158 | 28.45 ms | 50.57 ms |

Configuration selected the closest eligible point and the 16,384-address
sensitivity row for each router. On locked internal, the two informative K1
rows were:

| Router | Final top-10 overlap | Candidate retention | Hamming overlap | ADC overlap | Mean / worst-stratum nDCG loss | Local K8 p95 | Total p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Centroid K1, M=8192 | 0.9807 | 0.9961 | 0.9965 | 0.9914 | 0.0140 / 0.0265 | 32.18 ms | 56.02 ms |
| Centroid K1, M=16384 | 0.9912 | 0.9983 | 0.9986 | 0.9962 | 0.0041 / 0.0062 | 61.20 ms | 84.40 ms |

Thus local exact K8 preserves downstream candidate/Hamming/ADC sets once the
shortlist contains the useful region, but K1 misses enough final-boundary
addresses at M=8192 to violate both overlap and nDCG gates. Doubling M nearly
repairs overlap but still fails the preregistered nDCG caps and is far above the
15 ms local-scan target.

## Decision

The historical-recipe reconstruction is negative. It does not license native
integration or a production router. The fixed Top-M learned-router frontier is
activated to target the gap between K1's strong stage retention and its weaker
final-boundary fidelity. This result does not claim that the unavailable PR
#223 checkpoint would fail the same replay.

## Limitations

- The historical learned checkpoints are unavailable, so a negative result
  applies to these recipe reconstructions, not to the exact historical model.
- Training has only 76 German configuration queries, far fewer than the 8,141
  multilingual queries used by PR #223.
- The current 12-bit and 16-bit partitions were trained independently. No
  hierarchical 12-to-16 claim is made here.
- Local-K8 timing includes the diagnostic shortlist consumption and exact
  gather/score work, but not training or a future in-process learned router.

## Reproduction

The ignored raw result is
`tmp/neuroute-local-k8-historical-replay/result.json`. Run the corresponding
runner with the configuration protocol, layout manifest, current K8 manifest,
and safe native executable, then write the compact evidence with
`write-neuroute-local-k8-historical-replay-evidence.py`.
