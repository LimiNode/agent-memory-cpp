# Data-dependent product locator

This calibration-only line tests whether local codebooks trained from the actual
ITQ-256 distribution can restore the routing quality that fixed mathematical
product centers did not provide. It is separate from the earlier static-product
experiment: codebooks and any bit decomposition are fit only from frozen train
data, never from the 648 evaluation queries or French confirmation data.

The planned treatments are deliberately progressive:

1. contiguous balanced ITQ-bit blocks with train-only local Hamming medoids;
2. a train-only bit permutation before the same local-medoids treatment;
3. local float k-means on frozen pre-ITQ projections, retaining the frozen
   ranking ITQ-256 code for the downstream cascade.

Each runs at controlled implicit-cell budgets of 4,096, 16,384, and 65,536,
rather than assuming a sparse `16 blocks x 8` product space is useful. Query
cells are expanded best-first by summed local Hamming cost, with a lexical cell
key as the deterministic tie break, until the requested candidate mass is
reached. The unchanged downstream treatment is Hamming@768, binary ADC@256,
and exact E5 rerank.

The 54-row 100k/1M plan reports candidate mass, E5 survival, reranked nDCG,
local probes, non-empty-cell traversal, and codebook/index bytes. A pragmatic
exploratory gate is predeclared: at 5% candidate mass, a treatment below 70%
E5 survival after ADC does not justify native trie or empty-cell traversal
engineering. Passing that gate would only justify a follow-up implementation
study; it is not a production-selection or confirmation claim.
