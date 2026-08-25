# Shared learned semantic-address encoder

Date: 2026-08-26. This is the representation-learning follow-up after the
fixed PCA/median address-ranking experiment did not improve the symmetric
control. It is inspired by the shared-encoder principle of NeuRoute, while
remaining scale-aware for the 25k corpus.

## Hypothesis

A shared encoder trained from the document distribution can create a local
continuous geometry whose median-balanced binary addresses route queries better
than a query-only head attempting to reproduce a fixed PCA partition.

## Protocol

- train only from the 25,000 document embeddings, not query relevance labels;
- `384 -> 96 -> 64 -> L` ReLU encoder with a local cosine-geometry objective
  over each deterministic document batch and a small variance anti-collapse
  term;
- median threshold each learned document dimension, then use identical learned
  encoder for queries;
- test `L = 8, 10, 12, 14`, rather than copying a 20-bit billion-scale setting
  into a 25k collection where it would tend toward singleton buckets;
- select probes and document replication only on the v1 configuration split;
- route directly to postings, then use unchanged ITQ/ADC/exact-E5 cascade;
- do not use centroid scoring or a centroid refinement stage in the headline.

The 162-query v1 internal partition remains an exploratory locked comparison,
not a new external confirmation set.

## es-25k result

The shared encoder completed the full predeclared grid. Each bit width selected
`replication=4` and `16` probes on the configuration partition under the 10%
ceiling. Internal results were poor:

| Learned bits | Occupied addresses | Candidate fraction | E5 top-10 survival after ADC | nDCG@10 |
| ---: | ---: | ---: | ---: | ---: |
| 8 | 54 | 8.86% | 21.79% | 0.2723 |
| 10 | 112 | 9.38% | 25.99% | 0.3377 |
| 12 | 208 | 9.46% | 29.20% | 0.3246 |
| 14 | 421 | 9.40% | 30.12% | 0.3511 |

Median thresholding balances individual output dimensions but did not prevent
strong inter-bit correlation: the 8-bit encoder occupied only 54 of 256
addresses. The small document-only local-cosine objective therefore failed to
produce a useful binary routing geometry, despite its low training loss.

This closes **this constrained shared-encoder v1**, not NeuRoute-style learned
routing generally. It does not reproduce NeuRoute's selected-pair mask,
anti-collapse condition, larger training regime, logit-guided probing or
bucket-local centroid stage. Its negative result is nevertheless informative:
neither changing only query-side ranking nor this lightweight document-only
representation objective currently beats the symmetric PCA control. Any later
NeuRoute-faithful study must first predeclare a materially stronger pair-mining
and balance/diversity protocol rather than retune this implementation.
