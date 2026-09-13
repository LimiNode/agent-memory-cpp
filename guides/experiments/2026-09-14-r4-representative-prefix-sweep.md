# R4 representative-prefix work sweep (2026-09-14)

## Question

The representative-max route clears the quality gate but scores 0.88--0.90M
representatives per seed.  Can a shorter per-address representative prefix
preserve recall while materially reducing query-side work?

## Protocol

For each address, only the first `K={1,4,8,16,32}` frozen representatives are
eligible for the max query-cosine score.  The runner builds all five orders in
one full score pass per seed, then evaluates single-seed routes and a global
three-seed score fusion under the same unique-document budgets.  The receipt
records exact representative vectors scored per query, posting entries,
duplication, and teacher survival.  No teacher IDs participate in route order.

## Results

Three-seed representative-prefix fusion:

| prefix K | vectors scored/query | @5k | @10k | @20k | @50k | @100k |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 195,338 | .4224 | .5066 | .6072 | .7355 | .8408 |
| 4 | 750,911 | .7513 | .7914 | .8447 | .9158 | .9533 |
| 8 | 1,358,099 | .9204 | .9414 | .9586 | .9796 | .9862 |
| 16 | 2,104,812 | .9908 | .9954 | .9954 | .9974 | .9980 |
| 32 | 2,666,557 | .9993 | .9993 | .9993 | .9993 | 1.0000 |

At K=16, the route already clears `.99 @ 5k` and reaches `.9974 @ 50k`, but
still scores 2.10M vectors per query (2.1× the 1M corpus).  K=8 is materially
cheaper but remains below the `.99 @ 50k` gate.  Thus the quality/work frontier
has a sharp transition between K=8 and K=16; there is no low-work prefix that
inherits the full K=32 result.

## Decision

The representative-max route is a valid quality control, and K=16 is the
current candidate for a physical/THQ cascade experiment.  It is not yet a
deployable ANN index: query-side scoring remains a dense 2.1M-vector pass and
the sweep does not measure native SIMD, compressed representatives, pages, or
latency.  The next experiment should test packed/quantized representative
scoring and progressive THQ-ADC on the K=16 candidate stream, with the exact
score pass retained as a control.

## Limitations and provenance

This is a logical route-generation sweep only; there is no MDBX/page, cold/warm
I/O, or production claim.  Frozen THQ and R4 manifest SHAs are
`f58e074e481dc910ca7b12b35bc27dc51097640704fb2b0c749018bcf2edd57b` and
`95886a3b62eb0c2fc9182b721e94a252097395edc34d7604f7d11872bb5c039c`.
The external raw bundle SHA-256 is
`c11b785e528fbaa081e28374b3a41df55454e77d6fea78083153cfc9b7464c57`.
