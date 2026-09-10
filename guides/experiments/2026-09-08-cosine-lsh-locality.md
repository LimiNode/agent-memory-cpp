# Cosine-LSH / SimHash locality study

## Purpose

This study isolates angular random-hyperplane locality on the frozen DE-1M
fixture.  It answers whether E5 nearest neighbours are naturally local under
an untrained cosine hash before introducing ITQ, THQ, MIH, or an inverted
index.  A packed exhaustive Hamming scan is used deliberately: this is a
representation/locality control, not a claim that the result is a production
LSH index.

## Protocol

`run-cosine-lsh-locality.py` memory-maps the frozen `1M x 384` float32
document matrix and 152 queries.  For each family it generates deterministic,
column-normalized random hyperplanes and stores little-endian sign bits:

* Gaussian hyperplanes (`N(0,1)`), 256 and 512 bits;
* Rademacher hyperplanes (`+1/-1`), 256 and 512 bits.

No PCA or ITQ fitting is performed.  Query/document signs are compared with a
packed byte popcount lookup.  Teacher IDs are the exact E5 top-10 document IDs
from the frozen evaluation manifest.  A neighbour's reported rank is the
lower-bound Hamming rank (`1 + number of documents at strictly smaller
distance`), which is deterministic in the presence of ties.

For each code the runner reports survival of all ten teacher documents at
budgets 256, 1k, 5k, and 10k, including mean, p05, worst-query, and the
fraction of queries with full 10/10 survival.  The exact-K result uses a
stable document-ID tie break: all documents below the boundary are retained,
then the smallest IDs from the boundary shell fill the budget.  Two controls
are reported as well: strict survival (only distances strictly below the
boundary) and tie-expanded survival (all documents at the boundary).  This
keeps ties from making a representation look better or worse by accident.
The runner also reports boundary-shell size, `r50`, `r95`, `r99`, maximum rank,
query encode p50/p95/p99, exhaustive scan p50/p95/p99, materialized
bytes/document, and cache materialization time.  The corrected replay uses
three independent seeds per family; seed dispersion is retained in the raw
artifact rather than collapsed into one lucky row.

Example invocation (paths are intentionally explicit):

```powershell
python tools/agent-memory-bench/run-cosine-lsh-locality.py `
  --vectors E:/.../document-vectors-f32le.bin --documents 1000000 `
  --queries E:/.../eval_queries.bin --query-count 152 `
  --teacher-ids E:/.../eval_teacher_ids.bin `
  --cache-dir E:/.../cosine-lsh-cache `
  --output E:/.../cosine-lsh-locality.json
```

The exhaustive scan is intentionally separate from classic LSH table lookup.
An LSH-index follow-up must add independent tables, band widths, multiprobe
ordering, posting/page accounting, and cold/warm measurements; those are not
silently inferred from this file.

## Interpretation guardrails

The angular collision formula `1 - theta/pi` is exact for uniformly random
spherical hyperplanes.  Gaussian planes are the direct experimental match;
Rademacher planes are a useful high-dimensional random-projection control, not
a literal test of that formula.  These rows must be compared with the existing
ITQ/raw-THQ locality rows using the same teacher IDs and budgets before any
MIH or physical selective index is designed.

## Frozen DE-1M result

The run used 1,000,000 documents and 152 queries from the native confirmation
fixture (`eval_teacher_ids.bin` contains exact E5 top-10 IDs).  Survival is
the mean fraction of those ten IDs present in the Hamming top-K set; rank
quantiles are over all 1,520 teacher documents.

Result artifact: `tmp/cosine-lsh-locality-v2/result.json` (three seeds per
family; raw artifact is retained outside Git).  SHA-256:
`14ce0cef8856a24e403912c158ffadb8ad316a20470d7b9998d7118490fe52db`.

| code | bytes/doc | survival @256 | @1k | @5k | @10k | r50 | r95 | r99 | scan p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Gaussian-256 (seed 20260908 control) | 32 | .476 | .628 | .801 | .859 | 231 | 32,587 | 135,969 | 198 ms |
| Gaussian-512 (3-seed mean ± sd) | 64 | .743 ± .017 | .852 ± .013 | .939 ± .012 | .963 ± .009 | 24 | 5,633 | 34,406 | — |
| Rademacher-256 (seed 20260908 control) | 32 | .480 | .618 | .775 | .841 | 234 | 35,044 | 123,692 | 199 ms |
| Rademacher-512 (3-seed mean ± sd) | 64 | .758 ± .010 | .867 ± .003 | .953 ± .006 | .974 ± .005 | 22 | 3,778 | 21,653 | — |

The 512-bit codes are materially more local than their 256-bit counterparts,
but even the best family retains only about .76 survival at a 256-document
budget and .974 at 10k.  At K=256 the Rademacher strict/tie-expanded means are
.744/.775 (seed-average), with a median boundary shell of about 88--92
documents; tie policy therefore changes the number modestly but does not
explain the gap to raw THQ4.  This is weaker than the existing THQ4 full-scan
locality at the same compact-code budgets.  The result therefore does not
justify an MIH or selective LSH index yet; the next LSH-specific experiment
must measure whether multi-table collision lookup can recover this gap at
lower bytes touched than the exhaustive scan.
