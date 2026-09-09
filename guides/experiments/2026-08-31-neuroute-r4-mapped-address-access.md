# NeuRoute R4 mapped address-block access

## Context

- Date: 2026-08-31
- PR: stacked on the batched learned scorer
- Status: full DE-1M warm and fresh-process measurement complete

## Question

How much of the remaining R4 latency comes from 1,024 stream seeks, read calls,
and staging copies rather than from touching the selected INT8 bytes themselves?

## Frozen protocol

The address-major INT8 file, FF32 prefixes, fused scalar dot, batched scorer,
requests, and outputs remain frozen. Four matched treatments compare current
`seek/read` staging, memory-mapped staging copies, direct mapped shortlist-order
scoring, and direct mapped physical-offset-order scoring.

Warm-page-cache measurement uses one untimed pass and three deterministically
shuffled measured passes. Fifteen fixed requests per seed are also executed as
fresh processes with the OS page cache uncontrolled; this is not cold-disk
evidence. Every paired score hash must remain identical.

## Expected result

Mapping should remove system read calls. Direct scoring should additionally
remove the approximately 7.2 MB query-local staging copy. Physical-offset order
may improve page and cache traversal, but could lose locality against the query
feature/scorer order; it is therefore measured rather than assumed.

## Actual result

All 5,472 warm and 180 fresh-process samples preserve identical score hashes.
Memory mapping plus direct scoring reduces warm total p95 from `42.52 ms` to
about `16.2 ms`; removing system read calls is the dominant gain.

Warm-page-cache results:

| Access | Access/fetch p95 ms | Dot p95 ms | Total p50/p95 ms | System reads/query |
|---|---:|---:|---:|---:|
| `seek/read` staging | 26.920 | 9.287 | 38.252 / 42.515 | 1,024 |
| mmap + staging copy | 3.464 | 9.265 | 17.459 / 19.145 | 0 |
| mmap direct shortlist order | 0.039 | 9.751 | 14.887 / 16.234 | 0 |
| mmap direct offset order | 0.105 | 9.624 | **14.856 / 16.174** | 0 |

The mapped direct `fetch` field covers span preparation only; mapped-page
touches occur during dot and therefore remain included in total. The staging
control shows that replacing 1,024 stream operations already saves roughly
`23 ms p95`; removing the 7.2 MB staging copy saves another roughly `3 ms`.

Fresh-process first-request results, with the shared OS page cache uncontrolled:

| Access | Total p50/p95 ms |
|---|---:|
| `seek/read` staging | 40.803 / 44.797 |
| mmap + staging copy | 23.175 / 25.498 |
| mmap direct shortlist order | **21.036 / 22.411** |
| mmap direct offset order | 20.892 / 22.850 |

Offset order is the preregistered warm-p95 winner by only `0.06 ms`; shortlist
order has the slightly better fresh-process p95. The robust architectural result
is direct memory-mapped access. Physical ordering remains a directional tie and
should not be treated as a production requirement from this host alone.

## Limitations and next checks

The benchmark uses flat materialized files, not MDBX transactions, memory
pressure, or concurrent queries. Compression and the full cascade remain
separate follow-ups.

## Evidence

```text
result SHA-256:      012166ecbb200f481b528b86e53506cd43047659fcaa5abdf6b5fc3d74aa793f
warm report SHA-256: a60b71320b208bdb893eba23d4d93f6a30dee3be647268a8b13bcabb4e0ca072
evidence SHA-256:    e69515ac53e1d96d93108c376f315e6aa362f4fe36618817401a66d68a45a3d0
```

The evidence writer recomputes warm and fresh-process summaries, revalidates
all paired hashes, reruns the native self-test, and independently replays one
fresh-process request for every access treatment.
