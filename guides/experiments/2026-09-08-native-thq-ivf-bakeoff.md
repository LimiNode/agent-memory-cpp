# Native flat THQ versus E5-IVF → THQ bake-off

Date: 2026-09-08.  Research branch: `agent/pca12-routing-followups`.

## Question

Does a data-adaptive E5 IVF gate make THQ practical at a smaller document
read budget, compared with a flat THQ scan?  The same frozen DE-1M payload was
used for both routes.  The planned third row, the improved prototype-IVF →
local K8/K32/R0 cascade, remains a separate historical/native contract and is
not silently substituted by the older global-K8 result.

## Reproducible setup

The E5 IVF materialization is produced by
`tools/agent-memory-bench/materialize-native-thq-ivf.py` with `nlist=4096`,
`train_limit=100000`, `niter=15`, seed `20260908`, spherical inner-product
assignment, and stable document-id ordering inside postings.  The native
runner is `agent-memory-native-thq-ivf-bakeoff`; it performs centroid scoring,
posting traversal, THQ4 Hamming top-256, and three final reranks (FP32,
packed INT10, packed INT12).  Flat THQ uses the existing
`agent-memory-native-thq-full-scan` runner.

The native E5-IVF run used one measured pass over all 152 queries.  Timings
are process-local resident-file timings, not MDBX timings.  Logical bytes
include the selected THQ4 postings (144 bytes/code), posting ids (4 bytes),
and the 4096×384 float centroid scan; they are not claims about physical page
reads.

## Results

### E5-IVF → THQ4 → final rerank (native, 152 queries)

| candidate budget | mean candidates | route survival | THQ@256 survival | nDCG FP32 | nDCG INT10 | nDCG INT12 | p50 ms | p95 ms | p99 ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20k | 19,659 | .834 | .833 | .6390 | .6410 | .6390 | 33.7 | 36.5 | 38.0 |
| 50k | 49,708 | .914 | .913 | .6458 | .6477 | .6458 | 75.0 | 83.7 | 85.1 |
| 100k | 99,719 | .958 | .957 | .6496 | .6514 | .6495 | 142.9 | 158.5 | 160.3 |
| 200k | 199,723 | .980 | .979 | .6521 | .6539 | .6520 | 273.9 | 296.1 | 301.9 |
| 400k | 399,763 | .991 | .991 | .6563 | .6582 | .6563 | 539.1 | 581.3 | 596.5 |

The INT10/INT12 values are final-code reranks over the same THQ shortlist;
they are not FP32-free claims for the flat route until the corresponding flat
final-code rows are compared.  On this corpus INT10 is slightly above FP32 in
mean qrels nDCG due to tie/order effects, while INT12 is effectively equal.

### Flat THQ4 control (native, 152 queries)

| K | payload | mean nDCG | mean top-10 overlap | p95 scan | p95 total | p99 total |
|---:|---:|---:|---:|---:|---:|---:|
| 128 | 144 B/doc | .6541 | .9954 | 19.72 ms | 22.51 ms | 23.21 ms |
| 256 | 144 B/doc | .6540 | .9993 | 19.72 ms | 29.50 ms | 30.22 ms |
| 512 | 144 B/doc | .6540 | 1.0000 | 19.72 ms | 40.25 ms | 41.51 ms |
| 1024 | 144 B/doc | .6540 | 1.0000 | 19.72 ms | 59.86 ms | 61.43 ms |

Flat THQ4 reads 144 MB of routing payload per query.  At K=256 it is faster
than E5-IVF at every tested IVF budget while reaching the same qrels ceiling
as the 100k–200k IVF rows.  E5-IVF therefore does not win on this machine at
these unoptimized native settings; its value is the reduction in document
payload touched, not lower latency in this first implementation.

### Post-#269 prototype-IVF/R4 reference (not yet apples-to-apples)

The frozen post-#269 confirmation remains the quality-oriented reference:

| route | quality | generator p95 | native tail p95 | qualification |
|---|---:|---:|---:|---|
| K8-prototype IVF → local K8 → K32/R0 → old compact tail | 1.0000 overlap, zero nDCG loss | 13.47 ms (external Faiss) | 39.25 ms | generator excluded from native total |

This row is intentionally not merged with the native flat/IVF numbers: its
generator was external, its downstream payload/layout differs, and its tail
was Hamming/ADC rather than THQ3/4 → INT10/12.  It is the next quality path to
replay under the new contract, not a completed product latency claim.

## Interpretation

The hypothesis is only partially supported.  E5-space partitioning preserves
the expected quality curve (about .639 at 20k and .650 at 100k), and is much
better motivated than a THQ-space partition.  However, a native centroid scan
plus scalar THQ traversal costs more than the sequential flat THQ scan until
the deployment makes the 144 MB sequential read itself the limiting resource.
The natural next optimization is fused centroid/list scoring and real page
accounting, not increasing `nprobe` or adding another codec.

The modernized R4 row remains important: PR #269's prototype-IVF → local K8 →
K32/R0 path had near-exact quality, but its published native total excluded
the external Faiss generator.  It must be rerun with the THQ3/4 → INT10/12
tail before being compared numerically with this table.

## Limitations and follow-up

This run is resident-file native, not an MDBX open/transaction/page-cache
measurement.  No claim is made about cold page faults, MDBX overflow pages,
concurrency, or update cost.  The IVF centroids were trained on a 100k prefix,
and one seed/corpus was used.  The runner currently reports logical bytes;
the next PR should materialize postings in MDBX, report unique pages and raw
posting visits, and add the improved prototype-IVF/R4 row with the same final
rerank contract.  The raw artifacts are outside Git:

* `tmp/native-thq-ivf-bakeoff-v1/manifest.json`
* `tmp/native-thq-ivf-bakeoff-v1/full-result.json`
* `tmp/thq-full-scan-v2/native-bakeoff-result.json`
