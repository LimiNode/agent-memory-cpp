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

## es-25k median-centered v2 result

The original v1 runner did not apply its stated median threshold before
building addresses. Its values are therefore superseded, rather than being
silently compared with the corrected result. Version 2 recomputed each
full-document output median after training, persists the threshold with its
model and result, subtracts it for both documents and queries, and reran the
same frozen es-25k grid without changing the contract or selection split.

The corrected encoder completed the full predeclared grid. It selected 16
probes for every width; the 8-bit row selected `replication=2`, while the
other widths selected `replication=4`. Internal results remain poor:

| Learned bits | Occupied addresses | Candidate fraction | E5 top-10 survival after ADC | nDCG@10 |
| ---: | ---: | ---: | ---: | ---: |
| 8 | 256 | 9.58% | 34.69% | 0.3747 |
| 10 | 1,024 | 4.97% | 24.14% | 0.3191 |
| 12 | 4,093 | 1.31% | 15.74% | 0.2269 |
| 14 | 15,980 | 0.38% | 6.36% | 0.1027 |

Median-centering removes the previous occupied-address collapse: 8-bit now
occupies all 256 addresses, and higher widths are likewise well populated.
That fixes the implementation defect, but it does not make the lightweight
document-only local-cosine objective competitive with the symmetric PCA
control. The best corrected row reaches only 34.69% E5 survival and `.3747`
nDCG@10 at the permitted candidate mass.

This closes **this constrained shared-encoder v2**, not NeuRoute-style learned
routing generally. It does not reproduce NeuRoute's selected-pair mask,
anti-collapse condition, larger training regime, logit-guided probing or
bucket-local centroid stage. Its negative result is nevertheless informative:
neither changing only query-side ranking nor this lightweight document-only
representation objective currently beats the symmetric PCA control. Any later
NeuRoute-faithful study must first predeclare a materially stronger pair-mining
and balance/diversity protocol rather than retune this implementation.
