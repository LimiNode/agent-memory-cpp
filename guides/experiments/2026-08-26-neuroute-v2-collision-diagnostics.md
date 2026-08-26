# NeuRoute-inspired v2 collision diagnostics

Date: 2026-08-26. This post-hoc diagnostic uses only the already permitted
configuration-selection queries and frozen v2 models. It never reads the v2
internal-evaluation contributions and cannot alter the reported v2 result.

## Question

Did the 256-probe limit conceal a viable 10% frontier, or are the learned
addresses semantically impure even after their collapse was removed?

## Result

The three full 12-bit seeds have a smooth but weak configuration frontier:

| Probes | Candidate fraction | ADC E5 top-10 survival | nDCG@10 |
| ---: | ---: | ---: | ---: |
| 64 | 1.52% | 10.39% | .1614 |
| 128 | 3.04% | 17.14% | .2352 |
| 256 | 6.11% | 26.81% | .3443 |
| 512 | 10.00% | 36.54% | .4378 |
| 1024 | 10.00% | 36.54% | .4378 |

Thus a 512-probe run reaches the hard ceiling but still remains far below the
PCA control's internal 66.36% survival. It does not justify a retroactive
confirmation rerun on the already observed v2 internal split.

Collision evidence identifies the mechanism. At 12 bits, only about 0.11% of
each document's E5 top-10 neighbours share its learned address; their learned
Hamming distance has p50/p95 `5/8`. The PCA control has corresponding values
14.65% and `2/4`. Mean E5 cosine within an address is also lower for v2
(`~0.754`) than PCA (`0.781`). The v2 cube is well occupied but its local
neighbourhoods do not preserve E5 semantic neighbourhoods.

## Consequence

The next representation experiment must change the loss, not merely increase
the number of probes or code bits. A fresh-corpus protocol may test periodic
latent-neighbour false-positive mining while retaining v2's fixed diversity
terms. It must use new calibration and confirmation queries, because all
Spanish v2 query partitions are now observed.
