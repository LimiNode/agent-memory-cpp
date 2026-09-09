# NeuRoute shortlist-generator bake-off

Date: 2026-09-02

## Question

Can a document-only ANN generator recover a sufficiently complete occupied-
address shortlist before exact local K8, avoiding both the data-starved learned
router and the global 450K-prototype scan?

Global FP32 K8 over all occupied addresses is used only as an offline teacher
and reference. It is not a production treatment. ANN rows remain comparison
controls until the later common bake-off against cheap 12/14/16-bit selectors;
that product decision admits at most 4,096 local-K8 addresses.

## Frozen comparison

The experiment retains the frozen DE-1M topology and complete R4 cascade. It
compares the failed fixed Top-M control from PR #268, exact address-centroid K1,
Faiss IVF and HNSW over the approximately 65K address centroids, and Faiss IVF
and HNSW over all approximately 450K K8 prototypes. Prototype hits are
deduplicated to occupied-address rows before exact local FP32 K8 selects the
final 1,024 addresses.

Faiss is an external benchmark dependency, not a library dependency. Indexes
train only on document-derived vectors. ANN parameter selection uses global-K8
coverage and then generator latency measured on configuration requests only;
locked-internal timing cannot select a parameter. The best parameter per
generator family is then compared at M=4096 and M=8192 through native R4. Only
the best two distinct configuration families open locked internal.

The native diagnostic timing excludes external shortlist generation. The
report therefore keeps Faiss query time separately and uses generator plus
local-K8 time for ordering quality-passing rows. Index build cost is outside
the request path and remains a limitation of this bounded bake-off.

Every generator materializes one ordered Top-8192 address list per request;
the M=4096 treatment is its deterministic prefix. Consequently the recorded
generator time is the conservative Top-8192 generation time for both native
budgets, while local-K8 timing reflects the actual 4096 or 8192 rows. This PR
does not claim a separately optimized M=4096 ANN query path.

## Result

The compact result has SHA-256
`f6320992c8d0ede976bab4c537b710ea931e215f531cc8be4f2865c9a77b7020`;
the independently validated evidence has SHA-256
`df36ef838988382ca2997ea2f4ee0ddc148a4bcd145bf757d3eeafb05f5fe0cc`.

At the product-boundary sensitivity point, M=4,096, configuration produced:

| Generator | Generator / local-K8 p95 | Final overlap | Candidate / Hamming / ADC overlap | Mean / worst-stratum nDCG loss | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| Fixed learned control | 4.30 / 15.82 ms | 0.9246 | 0.9782 / 0.9796 / 0.9655 | 0.02447 / 0.04708 | fail |
| Exact centroid K1 | 1.68 / 15.44 ms | 0.9654 | 0.9922 / 0.9927 / 0.9849 | 0.00886 / 0.02695 | fail |
| Address IVF | 0.97 / 15.44 ms | 0.9566 | 0.9899 / 0.9902 / 0.9803 | 0.00994 / 0.03065 | fail |
| Address HNSW | 141.32 / 15.76 ms | 0.9627 | 0.9918 / 0.9925 / 0.9839 | 0.00922 / 0.02882 | fail |
| **K8-prototype IVF** | **13.82 / 15.74 ms** | **0.9996** | **0.9998 / 0.9998 / 0.9998** | **0.00058 / 0.00173** | **quality pass** |
| K8-prototype HNSW | 12,371.83 / 15.58 ms | 0.9969 | 0.9989 / 0.9985 / 0.9977 | 0.00047 / 0.00170 | quality pass, unusable latency |

Configuration opened the two prototype families. On the reused confirmation
partition, prototype IVF at M=4,096 preserved final top-10 and nDCG exactly:

| Generator | Generator / local-K8 / native-total p95 | Final overlap | Candidate / Hamming / ADC overlap | Mean / worst-stratum nDCG loss |
| --- | ---: | ---: | ---: | ---: |
| K8-prototype IVF, M=4096 | 13.47 / 15.66 / 39.25 ms | 1.0000 | 0.9998 / 0.9997 / 0.9997 | 0 / 0 |
| K8-prototype HNSW, M=4096 | 12,453.79 / 16.19 / 40.15 ms | 0.9969 | 0.9983 / 0.9981 / 0.9973 | 0 / 0 |

The native total excludes the external generator. Thus the directional
end-to-end IVF estimate is about `13.47 + 39.25 = 52.72 ms`. Its M=4,096
generator time is conservative because the query produced Top-8,192 and then
used its prefix.

## Decision

Prototype IVF is the only useful ANN control: it removes the global exact K8
scan while preserving the frozen R4 result at M=4,096. Address-only ANN does
not recover the multimodal buckets, and prototype HNSW is computationally
unusable under the tested parameters.

The original experiment contract marks prototype IVF as an implementation
candidate. The stricter product policy introduced for the closing bake-off does
not select it yet: local K8 misses the 15 ms target (`15.66 ms` p95), generator
plus local K8 costs about 29.1 ms before the remaining cascade, and serialized
index footprint/cold behavior are not measured. It proceeds as a strong
quality control against the 12/14/16-bit learned selector frontier, not as the
production winner.

## Limitations

- Faiss build/query evidence is directional on one Windows host and one fixed
  thread count.
- Requests 76--151 are excluded from parameter and family selection here, but
  their outcomes were reported by prior PRs; this is a reused confirmation
  partition rather than a pristine never-observed holdout.
- Serialized index footprint and cold index-build latency are not selection
  criteria in this PR; #270 must measure the selected native representation.
- A successful external Faiss row licenses a native implementation experiment,
  not a Faiss dependency in `agent_memory` and not production deployment.

## Reproduction

Run the shortlist-generator runner under the ignored Faiss virtual environment
with the PR #268 result, frozen protocol/layout/K8 manifests, and safe native R4
executable. The ignored raw output is
`tmp/neuroute-shortlist-generator-bakeoff/result.json`.
