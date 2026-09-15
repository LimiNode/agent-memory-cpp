Exit code: 0
Wall time: 0.6 seconds
Output:
# K1→K16 seed-count/A quality frontier (2026-09-15)

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

This is a quality frontier over `(seed_count, A)`, not a runtime Pareto curve:
native representative/address work and latency are not measured here.

Independent audit: `semantic_r4_k1_seed_pareto_audit_v2`, 2,128 rows, PASS.
The compact execution receipt is committed as
`2026-09-15-r4-k1-seed-pareto.receipt.json`.
The executed evidence is retained outside Git and SHA-bound to the frozen
manifests and native order files:

```text
raw output SHA-256:    3f383e218c95ee5a1352585a460d1b25c40fa3a3de2622a7175a3b5e275c1626
receipt SHA-256:        c427fb862e3c3b31b8cce6a0687f4468fc065d00091bea9cbb11fbc9c1233faa
runner SHA-256:         949d1a51ad540665211657bc28202e6d5b96d6420b44736a86e4ccf5bf293c14
native receipt SHA-256: 4bc286df8bd5c3f2674eef18c8beb59d218ce34530d9b9a86aa611e507e0e360
r4 layout SHA-256:      95886a3b62eb0c2fc9182b721e94a252097395edc34d7604f7d11872bb5c039c
```

The receipt is a compact execution record; the multi-megabyte raw output and
native order payloads remain in the external artifact workspace.
