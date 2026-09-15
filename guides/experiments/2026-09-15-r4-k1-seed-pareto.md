# K1→K16 seed-count/A Pareto gate (2026-09-15)

This diagnostic reuses the frozen AoSoA-32 K1→K16 address orders from #407.
It evaluates every non-empty subset of the three seeds at `A=8192` and
`A=16384`, with a common 5k unique-document budget over all 152 queries. It
does not materialize new vectors or use teacher IDs for routing. For each
teacher it records whether its address entered the A-prefix, its K16 rank, and
the first candidate-entry rank (or miss).

| Seed streams | A | Mean recall | p05 | Min |
|---|---:|---:|---:|---:|
| 1 (best single) | 8192 | .916 | .70 | .50 |
| 2 (best pair) | 8192 | .983 | .90 | .80 |
| 3 | 8192 | .993 | .90 | .90 |
| 1 (best single) | 16384 | .950 | .80 | .60 |
| 2 (best pair) | 16384 | .991 | .90 | .90 |
| 3 | 16384 | .997 | 1.00 | .90 |

The exact best pair is seed `2026082701 + 2026082702` at A=16384 (`.9901`
mean); the third stream raises this to `.9974`. At A=8192 the full three-seed
arm remains `.9928`, while the best pair is `.9829`. This confirms that seed
diversity, rather than simply increasing A, is the dominant low-budget quality
lever. Single-seed operation is not a viable default.

Independent audit: `semantic_r4_k1_seed_pareto_audit_v1`, 2,128 rows, PASS.
The raw receipt is retained outside Git and SHA-bound to the frozen manifests
and native order files.
