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
fraction of queries with full 10/10 survival.  It also reports `r50`, `r95`,
`r99`, maximum rank, query encode p50/p95/p99, exhaustive scan p50/p95/p99,
materialized bytes/document, and cache materialization time.

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

Result artifact: `tmp/cosine-lsh-locality-20260908/result.json`.

| code | bytes/doc | survival @256 | @1k | @5k | @10k | r50 | r95 | r99 | scan p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Gaussian-256 | 32 | .476 | .628 | .801 | .859 | 231 | 32,587 | 135,969 | 198 ms |
| Gaussian-512 | 64 | .733 | .851 | .934 | .963 | 29 | 5,930 | 30,389 | 387 ms |
| Rademacher-256 | 32 | .480 | .618 | .775 | .841 | 234 | 35,044 | 123,692 | 199 ms |
| Rademacher-512 | 64 | .750 | .864 | .946 | .968 | 23 | 4,478 | 23,807 | 398 ms |

The 512-bit codes are materially more local than their 256-bit counterparts,
but even the best row retains only .75 survival at a 256-document budget and
.968 at 10k.  This is weaker than the existing THQ4 full-scan locality at the
same compact-code budgets.  The result therefore does not justify an MIH or
selective LSH index yet; the next LSH-specific experiment must measure whether
multi-table collision lookup can recover this gap at lower bytes touched than
the exhaustive scan.
