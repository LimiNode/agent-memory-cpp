# Exact representative top-R oracle on the K16 topology (2026-09-14)

## Question

How much of the K16 three-seed route frontier can be retained if an ideal
representative-layer search returns the globally best FP32 representative hits,
instead of exhaustively scanning every representative?

This is an exact upper bound.  It is deliberately teacher-free during route
construction, but it assumes an oracle that can enumerate global representative
inner products in sorted order.  It is not an ANN implementation or a native
latency claim.

## Protocol

- frozen DE-1M: 1,000,000 documents, 152 queries, 384 dimensions;
- three frozen semantic R4 seeds and their physical postings;
- clipped `K=16` representatives per address, the same topology as the full
  native K16 gate;
- exact FP32 dot products for every clipped representative, retaining global
  top `R = 128, 256, 512, 1024, 2048, 4096, 8192` hits;
- parent addresses are emitted on first representative hit, then whole posting
  lists are read under a global unique-document budget of 5k/10k/20k/50k;
- duplicate documents across seeds are removed from the candidate count;
- teacher IDs are used only for evaluation.

If a top-R prefix cannot reach a requested budget, the row is retained with its
actual candidate count and is not silently extrapolated.

## Results

Mean candidate recall over 152 queries:

| representative hits R | 5k | 10k | 20k | 50k |
|---:|---:|---:|---:|---:|
| 128 | .989474* | .989474* | .989474* | .989474* |
| 256 | .990789* | .990789* | .990789* | .990789* |
| 512 | .990789 | .993421* | .993421* | .993421* |
| 1024 | .990789 | .995395 | .995395* | .995395* |
| 2048 | .990789 | .995395 | .995395 | .996053* |
| 4096 | .990789 | .995395 | .995395 | .997368* |
| 8192 | .990789 | .995395 | .995395 | .997368 |

An asterisk means that the top-R prefix exhausted before reaching that
requested unique-candidate budget for at least some queries; the row reports
the achieved candidate set, not a full-budget measurement.  For example, R=128
produces only about 2,197 mean candidates, and R=256 about 4,330.  R=1024 has
about 956 parent addresses in its prefix and reaches only about 16,216 mean
candidates at the nominal 20k/50k rows.

The first non-exhausted upper-bound points are therefore:

- R=512: `.990789` at 5k (the 10k prefix is already exhausted);
- R=1024: `.990789` at 5k and `.995395` at 10k;
- R=2048: `.995395` at 20k (the 50k prefix is exhausted);
- R=8192: `.997368` at 50k.

These values match the full K16 route frontier at the corresponding budgets,
while exposing a much smaller representative prefix.  The result is an
upper-bound statement: it proves that the existing topology does not require a
2.1M-representative exhaustive order to express this quality frontier, but it
does not yet provide a way to find those top representatives cheaply.

## Interpretation and next gate

The bottleneck is now sharply localized:

```text
K16 topology and postings: sufficient
exact global top-R representative oracle: sufficient at R≈1k–4k
available non-oracle representative search: not yet measured
```

The next justified experiment is an accelerator control on the representative
layer, not a new posting topology:

1. build an ANN/quantized representative index and measure its recall against
   the exact top-R oracle;
2. alternatively, test hierarchical K1/centroid coarse selection followed by
   K16 address refinement;
3. feed the selected addresses into the already validated postings → THQ →
   exact cascade.

Until that control exists, the top-R numbers must not be presented as serving
latency or production feasibility.

## Provenance and audit

Authoritative receipt: `EXECUTED`, SHA-256
`132522ca40dcacfb63e09be405615a2dd05f754617b5ee714d16eaa10f7cf21a`.

Raw output: 4,256 rows, SHA-256
`3c72e05fbb744726afe23e849d5fc3621503c93a3ce7722e1f21b3ee316489f6`.

Independent audit: `PASS` (4,256 rows and 28 summary cells), audit SHA-256
`966c9db6869efe2a60016e32005925174725ebf0ddf6042b07e2d61b36b3f9d7`.

Bound inputs:

- frozen THQ manifest: `f58e074e481dc910ca7bb12b35bc27dc51097640704fb2b0c749018bcf2edd57`;
- R4 layout manifest: `95886a3b62eb0c2fc9182b721e94a252097395edc34d7604f7d11872bb5c039c`;
- R4 codec manifest: `1566688756f1922c9f3cce83c46c9623d2231f221bbe496b12ae978c0cdad8db`;
- oracle runner source SHA: `7837bf90224298a1b519e21c922ebd2292843cebfd3c35ed6b4d250bdffe5a80`;
- audit source SHA: `c492ba4c275ef70ac68fa65f59e03e35534b6bc60e7bb4a009451adde43b67f2`.

The authoritative receipt/raw/audit are retained outside Git under
`E:\_repoz\agent-memory-workspaces\r4-representative-top-r-oracle-raw`.
