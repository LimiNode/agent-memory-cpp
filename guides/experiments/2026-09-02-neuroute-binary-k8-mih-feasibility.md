# NeuRoute binary K8 MIH feasibility

Date: 2026-09-02  
Context: PR #275, stacked on #274

## Question

Does moving the already tested frozen prototype binary geometry into a classic
multi-index hashing backend avoid exhaustive prototype scanning at useful
teacher-address recall? This is a bounded mechanism audit, not a product
backend selection. It deliberately does not repeat the historical global
document-MIH parameter sweeps.

The audit uses all 152 frozen queries and three DE-1M seeds. It measures exact
Hamming distance from the frozen query code to every active K8 prototype,
aggregates the minimum over each address, and finds the smallest radius needed
for 95/97/99/99.5% recall of the FP32-K8 top-1024 addresses. It separately
reports prototype visits and unique-address union size. The classic sufficient
probe estimate is `m * sum(C(64,i), i<=floor(radius/m))`, using four 64-bit
subindexes for ITQ256 and six for the 384-bit orthogonal control.

## Result

Result SHA-256: `d29a6db6ca572c417fe49dce160d5ea9f6e969e48af3b23cb204e435fc1cd04d`  
Evidence SHA-256: `5948043f9df10399534cc8f919a84230788b01094fae006c4ecf0d91a4a24edf`

Seed-mean 99%-recall diagnostics are:

| Code | Mean radius | Mean classic probes | p95 prototype rows | p95 unique addresses | Mean achieved recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| ITQ256 | 114.83 | 1.60e19 | 76,901 | 38,526 | .9926 |
| Random-orthogonal Hamming384 | 80.06 | 2.05e14 | 66,417 | 49,180 | .9928 |

At 99.5%, ITQ256 reaches mean radius 115.93 with about 90K p95 prototype rows;
orthogonal384 reaches 81.07 with about 76K. Neither is remotely a small-radius
MIH workload. A BinaryFlat scan remains the honest physical denominator for
these frozen codes, but #274 already showed that their full-cascade quality is
insufficient.

## Decision

No fixed-code physical backend is licensed. Global prototype MIH is closed:
the prototype domain is smaller than the document domain, but the required
radius is still combinatorial and the candidate union remains very large.

This result does not test semantic-anchor center relocation. The query center
here remains the frozen query binary code. It also does not test a deliberately
learned hypercube. Those are distinct hypotheses: relocation needs matched
query-restricted and anchor-seeded controls, while learned prototype memory
needs a separate representation ceiling before any neural query router.

## Limitations

- Probe counts are analytic sufficient counts, not measured native postings.
- The audit measures teacher-address recall, not document-level qrels utility.
- ITQ training is prototype-only and the run is an offline diagnostic.
- No production policy or native dependency is added.

Raw result and evidence remain under `tmp/neuroute-binary-k8-mih/`.
