# Corpus-specific asymmetric K8 prototype map

Date: 2026-09-04. This study follows PR #284, which closed the symmetric
shared `384 -> 96 -> 64 -> B` neural metric. It tests whether the fixed K8
prototype corpus can instead own independent binary codes while a small query
encoder learns to enter that index-specific Hamming space.

## Question and protocol

For every frozen K8 prototype `p_j`, the index stores a learned code `C_j`.
The query network `g_theta(E5(q))` emits only the query code. Prototype codes
are trained from the first 4,070 queries; the remaining 4,071 queries are
held out and use the same learned codebook. The 454,322-prototype corpus is
never replaced or rescanned with FP32 K8 at runtime.

The real source is the PR #284 8,141-query materialization and its corrected
FP32 top-1,024 teacher cache. Three deterministic seeds (285, 286, 287) were
run at 32, 64, and 128 bits. Each query samples rank-spread positives from
teacher ranks `[0, 1, 7, 31, 127, 255, 511, 1023]`, fixed teacher negatives,
eight corpus-random negatives, and one Hamming hard-negative round. Quality
uses deterministic `(distance, prototype_id)` ordering and reports mean,
p05, and worst-query teacher recall.

The harness also includes a free-query/free-prototype lookup mode as a
representational diagnostic. Its initial pairwise sampling objective is not a
global top-1,024 oracle; therefore it is reported as an optimization control,
not as a proof of the Hamming space's absolute capacity.

## Held-out prototype geometry

Mean internal teacher-prototype recall at shortlist 4,096 was:

| method | 32 bits | 64 bits | 128 bits |
| --- | ---: | ---: | ---: |
| asymmetric query encoder + free prototype codes, mean over 3 seeds | 0.22213 (±0.0300) | 0.29563 (±0.0039) | **0.32840 (±0.0005)** |
| corrected PR #284 symmetric best cell | 0.25039 | — | 0.17592 |

The asymmetric 128-bit cells were `0.32774`, `0.32856`, and `0.32891` for
seeds 285–287. The gain over the corrected PR #284 128-bit result is real and
stable across seeds, but it is still only a prototype-level diagnostic. The
free lookup control remained near random (`0.011–0.014` recall@4,096) under
the sampled pair objective, showing that this particular optimization does
not constitute a useful global ceiling.

## Address utility and cascade gate

The best reproducible cell (128 bits, seed 287; pre-hard-negative artifact,
whose held-out prototype recall is 0.29995) was replayed against the frozen
semantic-anchor posting map. Selected prototype IDs were deduplicated to
documents and reranked exactly within that candidate set. This is the address
utility gate before native local-K8/R4 integration.

| prototype budget | mean prototype recall | mean candidate docs | target survival / final top10 overlap |
| ---: | ---: | ---: | ---: |
| 1,024 | 0.04748 | 19,784 | 0.03684 |
| 2,048 | 0.09000 | 38,313 | 0.07368 |
| 4,096 | 0.16176 | 74,146 | 0.15461 |
| 8,192 | 0.26101 | 139,314 | 0.26118 |

At every budget p05 and worst-query final top-10 overlap were zero. Prototype
recall and document utility track one another here; neither approaches the
`0.99` final-overlap gate used by the native cascade studies. The full native
K32/Hamming/ADC/R4 cascade is therefore not licensed, and global K8 scanning
over all addresses remains outside the product line.

## Interpretation and decision

The result validates the central architectural distinction: freeing the
database-side prototype codes materially improves held-out prototype ranking,
especially at 128 bits. It does not validate a production selector. The
many-to-many prototype-to-document postings turn a moderate prototype gain
into poor address survival, with severe worst-query failures.

This study stops before native local-K8 and full R4 replay. No 192/256-bit
extension is justified by the address gate. The corpus-specific asymmetric
idea remains scientifically open, but a next attempt must change the training
objective (for example, address/document-utility-weighted listwise learning,
better coverage of teacher ranks, or an alternating discrete code optimizer)
before another width sweep. Any successor must first pass address utility;
only then should it be integrated into local K8 and the complete R4 cascade.

## Reproduction and limitations

Raw reports stay under `tmp/` and are not committed. The main frontier reports
are `tmp/asymmetric-results/seed-{285,286,287}.json`; the cascade reports are
`tmp/asymmetric-cascade-{1024,2048,4096,8192}.json`. The latter have SHA-256:

* 1024: `dcc12195291dc003a9d0c5dbc672ca569db69b99a30f60eed6a3414fac500e01`;
* 2048: `56f6a0808ee40ac6d38ef027f4a92750c54ec2c7b5f4e326db18d21d62968bf1`;
* 4096: `7709d7ac6ef0b96ba0facf12302d6de50f7599353536da62c36f74696d5dce76`;
* 8192: `616fb46dfed5fc815cf50fe9e338800e5b96251142d59bceb64358df695373e9`.

The asymmetric frontier used one fixed train/held-out split and three seeds;
the lookup control was intentionally diagnostic rather than a complete
global optimization. Cascade replay used 152 frozen semantic-anchor queries,
the existing prototype postings, and exact document rerank; it was not a
native R4 latency measurement. Exhaustive Hamming scans are offline quality
ceilings only.
