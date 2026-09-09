# NeuRoute R3 document-summary materialization audit

## Context

- Date: 2026-08-29
- PR: stacked on the matched R0/R1/R2 representation ladder
- Status: full DE-1M materialization and independent byte replay complete

## Question

Can the frozen 16-bit/K8 route expose a deterministic, teacher-blind summary
of the actual document distribution hidden behind each occupied address, so a
matched R3 reranker can test information that is absent from R0 and the
projected-prototype R1/R2 treatments?

## Frozen materialization

For each of the three DE-1M route seeds, every normalized document is assigned
to its maximum-cosine effective K8 prototype, with the lowest slot winning an
exact tie. Each nonempty local prototype group then records:

- document count;
- full-384D mean residual from the assigned prototype;
- full-384D population diagonal residual variance;
- a deterministic top centered-residual direction and Rayleigh eigenvalue;
- total residual energy.

The top direction uses four fixed groupwise covariance power iterations. It
starts at the positive coordinate of maximum diagonal variance, canonicalizes
the sign by the largest absolute component, and is exactly zero with zero
eigenvalue for groups with fewer than three documents or no residual energy.
The summaries use documents and the frozen partition only: no query, teacher,
configuration qrels, or internal qrels affects their bytes.

The uncompressed `.npy` arrays remain under `tmp/`. Git retains only the
contract, implementation, compact audit, artifact hashes, and replay evidence.

## Audit result

All `3,000,000` document assignments were present exactly once. Every summary
was finite and every undersized-group fallback matched the contract.

| Seed | Occupied addresses | Nonempty K8-local groups | Groups with fewer than 3 docs | Documents exactly on assigned prototype |
|---:|---:|---:|---:|---:|
| 2026082701 | 65,108 | 454,103 | 415,390 | 390,391 |
| 2026082702 | 65,039 | 446,073 | 408,992 | 382,709 |
| 2026082703 | 65,191 | 457,159 | 418,189 | 393,274 |

The occupancy-stratified audit is:

| Seed | Address occupancy | Addresses | Documents | Nonempty local groups | Undersized local groups | Zero-energy local groups |
|---:|:---:|---:|---:|---:|---:|---:|
| 2026082701 | <=8 | 23,026 | 117,666 | 117,462 | 117,461 | 117,278 |
| 2026082701 | 9--16 | 21,628 | 260,968 | 173,009 | 154,875 | 151,107 |
| 2026082701 | 17--32 | 14,817 | 332,592 | 118,536 | 103,680 | 103,262 |
| 2026082701 | >32 | 5,637 | 288,774 | 45,096 | 39,374 | 38,922 |
| 2026082702 | <=8 | 24,615 | 122,930 | 122,698 | 122,694 | 122,492 |
| 2026082702 | 9--16 | 20,447 | 245,808 | 163,559 | 146,577 | 142,855 |
| 2026082702 | 17--32 | 14,044 | 317,610 | 112,352 | 98,267 | 97,788 |
| 2026082702 | >32 | 5,933 | 313,652 | 47,464 | 41,454 | 41,030 |
| 2026082703 | <=8 | 22,921 | 119,295 | 119,014 | 119,009 | 118,773 |
| 2026082703 | 9--16 | 21,611 | 260,705 | 172,873 | 154,683 | 150,940 |
| 2026082703 | 17--32 | 15,065 | 339,735 | 120,520 | 105,420 | 104,914 |
| 2026082703 | >32 | 5,594 | 280,265 | 44,752 | 39,077 | 38,666 |

This audit corrects an important intuition before measuring R3. The current K8
construction is one normalized address centroid followed by up to seven
farthest-first document representatives. Therefore `effective K = occupancy`
for a small address does **not** mean that every slot is an actual document.
Nevertheless, roughly 90% of nonempty local groups are singletons and roughly
91% have fewer than three documents. Residual variance/direction is necessarily
sparse; count and residual mean are the denser R3 additions. That observation
does not invalidate R3, but it makes the additive R3a/R3b/R3c ladder essential:
it will distinguish a count effect from genuinely richer distribution evidence.

Result SHA-256 is
`2a6e2e527e1925b3d48619299a7cb5da8e6f9984c29566708e6e4eaa34bf3b9d`.
An independent second materialization reproduced the result and all 18 array
artifacts byte for byte. Evidence SHA-256 is
`d0e32fcc57af49f5297debb0e4f962c75036c8ad7cbfd407882c62e8829c9720`.

## Limitations

- Diagonal variance and one power-iterated direction are compact distribution
  summaries, not a sufficient statistic for every document configuration.
- The top direction is undefined for singleton/two-document local groups by
  contract; this is a deliberate deterministic fallback, not evidence that the
  parent address has no semantic variation.
- Exact K8 construction and materialization are offline research operations;
  this PR makes no retrieval-latency or production-storage claim.
- The audit licenses only the already frozen matched R3 ladder. It does not
  license stateful scheduling, native activation, or production selection.

## Next check

Keep the DE-1M route, exact top-1024 shortlist, 8,141 training queries, teacher,
optimizer, budgets, and cascade fixed. Compare R0 against additive R3a count,
R3b full residual-mean interactions, and R3c variance/top-direction
interactions with approximately matched trainable parameter counts.
