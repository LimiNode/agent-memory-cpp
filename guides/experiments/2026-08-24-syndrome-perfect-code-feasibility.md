# Syndrome and perfect-code locator feasibility

Date: 2026-08-24. Context: a mathematical control following the static
binary-product locator diagnostic.

## Question

Can a generic linear-code syndrome decoder supply a 256-bit coarse locator
with a few thousand nearest-center cells, and do classical perfect codes evade
that constraint?

## Method

This is not a corpus benchmark. `diagnose-syndrome-perfect-codes.py` performs
exact integer Hamming-ball calculations from the pinned contract. It checks
three perfect-code controls through the sphere identity

```text
2^k * sum(i = 0..R) C(n, i) = 2^n.
```

It separately computes the sphere-covering lower bound for 256-bit spaces and
the syndrome/center-count duality of a generic linear `[n, k]` code.

## Result

The known small perfect-code controls satisfy their expected identities:

| code | centers | correction radius | syndrome bits |
| --- | ---: | ---: | ---: |
| Hamming `[7,4,3]` | 16 | 1 | 3 |
| Hamming `[15,11,3]` | 2,048 | 1 | 4 |
| Golay `[23,12,7]` | 4,096 | 3 | 11 |

They demonstrate that a syndrome can identify a nearest center for special
codes. They do not provide a convenient coarse 256-bit partition. For a
generic 256-bit linear code with 4,096 centers, `k = 12`; its syndrome has 244
bits and a direct syndrome-to-coset-leader table has `2^244` entries. Reversing
the choice to a 12-bit syndrome gives `2^244` centers rather than a few
thousand cells.

Even ignoring decoding, the full-cube sphere-covering lower bound is broad:

| desired centers | minimum possible covering radius |
| ---: | ---: |
| 256 | 107 |
| 1,024 | 103 |
| 4,096 | 100 |
| 16,384 | 97 |
| 65,536 | 95 |

These are lower bounds, not claims that a code attaining them exists.

## Interpretation

This closes **generic table-based syndrome decoding** as the desired 256-bit
coarse locator. It does not claim that every structured decoder is impossible:
a special family can have a fast nearest-codeword algorithm. Nor does the
full-cube covering argument measure the observed ITQ document manifold, so it
cannot by itself predict retrieval quality.

The practical implication is narrower. Perfect Hamming/Golay codes are useful
mathematical demonstrations, but their local center counts are already too
fine for the intended inverted lists. The next independent structured control
is full-length RM(1,8)/Hadamard: it has 512 centers and a fast Walsh-Hadamard
nearest-center transform, so it can be evaluated on the frozen retrieval
protocol rather than ruled out by this generic syndrome argument.
