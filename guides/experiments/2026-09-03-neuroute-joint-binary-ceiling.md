# Joint document/prototype binary ceiling

Date: 2026-09-03. This is a first low-cost learned-binary screening after the
repaired #278 geometry decomposition. It is a research baseline, not a claim
that all supervised hashing methods have been exhausted.

## Question and setup

Can a shared binary projection trained from frozen R4 prototype/document
positive pairs make semantic prototypes locally close to their member
documents? Training uses up to 200,000 deterministic representative positive
pairs per seed. The projection uses the lowest-variance directions of the
positive pair differences, with per-bit median thresholds for balance. Query
evaluation is held out by the frozen configuration/internal halves. Widths are
256, 384, and 512 bits; native MIH and production selection are disabled.

## Three-seed result

| width | positive-pair mean Hamming | positive-pair p95 | semantic-oracle target r95 | configuration r95 | internal r95 | mean bit entropy |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| frozen ITQ256 control | 92.0 | 117.0 | (from #278) 73.75 | — | — | — |
| supervised 256 | 109.1 | 137.3 | 103.0 | 103.7 | 101.0 | 0.9955 |
| supervised 384 | 161.9 | 201.0 | 150.7 | 153.3 | 148.3 | 0.9950 |
| supervised 512 | 213.3 | 265.3 | 197.3 | 200.7 | 193.4 | 0.9941 |

The proposed projection is consistently worse than the frozen ITQ code at
256 bits and degrades further at larger widths. Excellent bit entropy does
not compensate for poor pairwise locality. This particular linear
low-variance objective therefore provides no positive geometry signal and does
not justify native MIH or another selector round.

## Interpretation and limitations

The negative result is specific to this deterministic positive-pair linear
baseline. It does not rule out nonlinear supervised hashing, metric-learning
objectives, or a representation trained directly against final retrieval
utility. Absolute radii also grow with code width, so future comparisons must
include normalized radius and actual MIH probe/posting work. The next product
decision remains conditional: either test a stronger, explicitly Hamming-aware
joint objective, or close the MIH branch and return to the cheap 12/14/16-bit
address selector plus local K8 path.
