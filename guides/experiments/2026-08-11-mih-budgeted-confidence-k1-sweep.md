# 2026-08-11 MIH budgeted-confidence K1 sweep

## Question

Can a wider Hamming shortlist recover the E5-oracle documents already present
in a budgeted-confidence MIH union, without raising the union to the more
expensive 16k-candidate operating point?

## Frozen setup

The evaluation uses the held-out 22,607-document / 1,252-query MIRACL Russian
E5 root and the existing full 25,000-vector ITQ calibration root. Every row
uses 256-bit ITQ, 50 ITQ iterations, seeds 42--46, 32 equal 8-bit bands,
binary ADC K2=256, and the E5-oracle top-10 funnel.

Budgeted-confidence probing first probes every exact 8-bit bucket, then probes
one-bit buckets in increasing absolute query-projection margin. Its parameter
is a **soft candidate target**, not a hard candidate cap: the mandatory exact
bucket union is always retained. The observed five-seed exact-bucket floor is
about 3,150.5 unique candidates per query.

The predeclared matrix is:

```text
soft target:  8,192 / 12,288 / 16,384
Hamming K1:     512 / 768 / 1,024 / 1,536
ADC K2: 256
seeds: 42--46
```

This is 60 evaluator rows. For each target, seed, and K1 greater than 512,
the evidence includes a paired 10,000-replicate bootstrap against K1=512:
45 comparisons in total, with bootstrap seed `20260811`.

## Result

Values are five-seed means of E5-oracle top-10 survival after the stated
stage. The raw-union value is invariant within each target because K1 changes
only the second stage.

| Soft target | Actual union | K1 | Raw union | Hamming K1 | ADC K2=256 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 8,192 | 8,227.3 | 512 | .965112 | .959840 | .959521 |
| 8,192 | 8,227.3 | 768 | .965112 | .962412 | .961821 |
| 8,192 | 8,227.3 | 1,024 | .965112 | .963419 | .962428 |
| 8,192 | 8,227.3 | 1,536 | .965112 | .964409 | .963099 |
| 12,288 | 12,312.4 | 512 | .992939 | .984121 | .983738 |
| 12,288 | 12,312.4 | 768 | .992939 | .987780 | **.986789** |
| 12,288 | 12,312.4 | 1,024 | .992939 | .989808 | .988419 |
| 12,288 | 12,312.4 | 1,536 | .992939 | .991406 | .989425 |
| 16,384 | 16,084.3 | 512 | .998498 | .987987 | .987572 |
| 16,384 | 16,084.3 | 768 | .998498 | .992109 | .991102 |
| 16,384 | 16,084.3 | 1,024 | .998498 | .994505 | .992971 |
| 16,384 | 16,084.3 | 1,536 | .998498 | .996310 | .994201 |

At the 12,288 target, paired ADC-survival deltas versus K1=512 are +.003051
for K1=768, +.004681 for K1=1,024, and +.005687 for K1=1,536. The smallest
tested shortlist that exceeds the previously chosen `.985` operating threshold is
therefore **soft target 12,288 with Hamming K1=768 and ADC K2=256**.

## Interpretation

The earlier 12k result was not primarily a candidate-generation failure:
its raw union already contained `.992939` of the E5 oracle. Increasing K1
from 512 to 768 recovers enough of those candidates to cross the operating
threshold while keeping the union about 23% smaller than the 16k target.

The 8k target remains insufficient even at K1=1,536 because its raw union is
only `.965112`. Conversely, larger K1 values and the 16k target are useful
diagnostics but are not selected here: they cost a wider shortlist or a larger
candidate union without being required to meet the stated threshold.

This closes the K1 bottleneck question for the simple margin-order policy.
The next separate line keeps the selected cascade and frozen roots unchanged
while testing calibration-only weighted-Hamming and alternative probing
policies. Those policies must be compared against this selected 12,288/768/256
control rather than against the superseded 12,288/512 row.

## Evidence

The compact evidence bundle contains all 60 reports and NPZ contribution files,
45 paired bootstrap reports, the matrix contract, source snapshots, compact
manifest, and bundle manifest. Its archive SHA-256 is
`48daf37080d9c7683b25e9774ea087dc5eca5fa839c4620880abf4ef44ab8fc0`; its
internal bundle-root SHA-256 is
`c79f6fc4bafd12aeaf279c6a9ef68332e5cc144fb1b3ef690f661c728e30bea0`.

The reviewable draft-release asset is
[mih-budgeted-confidence-k1-evidence-v1.zip](https://github.com/LimiNode/agent-memory-cpp/releases/download/untagged-9e9c324501b443576a67/mih-budgeted-confidence-k1-evidence-v1.zip).
Its exact evidence-producing commit is `fa888cf7d1bfc456bf55e7b3dad45f53c8e947e2`;
the subsequent note-link commit does not change scientific code. The archive validator verifies
the complete row/comparison grid, contribution hashes and summaries, paired
bootstrap replay, common evaluator/runtime/calibration provenance, and POSIX
archive member names before packaging.
