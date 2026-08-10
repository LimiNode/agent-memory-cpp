# 2026-08-10 MIH MDBX versus CSR posting-storage benchmark

## Question

Can the 16 x 16-bit MIH candidate generator from the shared-code cascade use
an MDBX `KeyValueTable` posting layout without losing the practical advantage
of its bounded-radius candidate search, or is a dense in-memory CSR directory
a materially better hot-path representation?

This is a posting-storage and candidate-union experiment. It does not measure
end-to-end retrieval quality or production latency.

## Setup

The input is the held-out ITQ-256 codebook used by the MIH cascade experiment:
22,607 documents, 1,252 queries, seed 42, and 50 ITQ iterations. The input
manifest binds the calibration/evaluation materialization manifest
`cd1987fdef63f5f6b4fd595d312648ea58f85aa502ed982958ebf02e99290e86`,
the calibration-ID hash
`96d118f976ff8d2ba208b89e1ec33e861e43a46a9e5cf88ca4eef35f5e06dc6b`,
and the packed document/query code payload hashes.

The benchmark builds the same logical postings in two forms:

- a static direct-address CSR directory with `16 * (2^16 + 1)` offsets and
  contiguous `uint32_t` document positions;
- an MDBX `KeyValueTable<string, string>` where each non-empty `(band, bucket)`
  has one metadata record and deterministic bounded posting pages.

For each of 128 deterministically selected queries, it runs five warm passes
at the predeclared global MIH radii 48, 56, and 64. Before accepting a timing,
the harness requires the two backends to agree exactly on bucket probes,
visited postings, unique candidates, and a candidate-union checksum. It also
has a CTest self-test for the 2,752 / 7,232 / 12,972 probe counts, damaged page
rejection, and synthetic CSR/MDBX candidate equality.

The reported warm scope is exactly bucket metadata lookup, posting-page lookup,
decode, and candidate deduplication. It excludes query encoding, full-Hamming
ranking, binary ADC, exact reranking, cold-cache I/O, and OS-cache eviction.

## Result

The final 256-entry-page run used GNU 15.2.0, C++17, Release, MinGW Makefiles,
Windows AMD64, and a 64-bit process. CSR logical storage was 9,835,584 bytes;
the MDBX file was 33,554,432 bytes. Build time was 13.47 ms for CSR and
27,617.92 ms for the one-transaction MDBX bulk build.

| Global radius | Metadata lookups/query | Posting page reads/query | Candidates/query | CSR warm median (ms/128) | MDBX warm median (ms/128) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 48 | 2,752 | 945 | 1,239 | 11.31 | 340.59 |
| 56 | 7,232 | 2,405 | 2,978 | 22.56 | 844.20 |
| 64 | 12,972 | 4,233 | 4,926 | 39.85 | 1,494.51 |

The page-size control did not materially change the result. At page sizes 64,
256, and 1024, each non-empty bucket on this corpus fitted in one posting page,
so the number of posting-page reads was unchanged. MDBX medians respectively
spanned 336.42/324.79/341.61 ms at radius 48, 806.24/813.35/823.21 ms at radius
56, and 1,459.68/1,438.86/1,435.18 ms at radius 64. These small differences are
local-run variation, not a page-size effect.

## Interpretation

For this deliberately direct layout, thousands of individual B-tree metadata
lookups dominate the warm candidate-union path. CSR is roughly 30x--38x faster
in these local medians while also using less logical storage. This is evidence
against a naive one-key-per-probed-bucket MDBX hot path, not evidence that MDBX
cannot participate in a production MIH design.

MDBX remains appropriate for durable source records and can still support a
different candidate layout. The measured bulk builder also holds an in-memory
bucket map before committing, so it is not a streaming-ingestion benchmark.

## Limitations and follow-up

The raw input, MDBX files, and JSON reports remain under `tmp/` and are not
committed. This is one local warm-cache environment, without process-wide
memory high-water measurement, cache eviction, or concurrent readers.

The next practical challenger is not larger posting pages: it is fewer durable
lookups through a band-local directory, batched bucket access, or grouping of
nearby bucket keys into a bounded durable page. That layout must preserve the
same bounded-radius and exact-union contract, then be compared inside the full
MIH -> Hamming -> ADC/OPQ -> exact-E5 cascade.
