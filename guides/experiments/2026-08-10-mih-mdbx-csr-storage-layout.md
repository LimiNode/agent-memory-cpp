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
at the predeclared global MIH radii 48, 56, and 64. The report preserves the
seed and the complete ordered query-position list. Before timing, the harness
compares the complete CSR and MDBX candidate `seen` vectors for every selected
query/radius pair; the aggregate count/checksum retained in timing rows is only
a diagnostic. It also has a CTest self-test for the 2,752 / 7,232 / 12,972
probe counts, damaged page rejection, packed-payload SHA rejection, and
synthetic CSR/MDBX candidate equality.

The reported warm scope is bucket metadata lookup, posting-page lookup, decode,
candidate deduplication, and one MDBX read-transaction lifecycle per query. It
excludes query encoding, full-Hamming ranking, binary ADC, exact reranking,
cold-cache I/O, and OS-cache eviction.

## Result

The final 256-entry-page run used GNU 15.2.0, C++17, Release, MinGW Makefiles,
Windows AMD64, and a 64-bit process. It pinned libmdbx
`fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f` and mdbx-containers
`e9e9f2fd5139f7fb386afd458fcdd8e20d7ec6e3`. CSR logical arrays occupied
9,835,584 bytes; the resulting MDBX file footprint was 33,554,432 bytes.
These are not equal-definition storage measurements. Build time was 13.04 ms
for CSR and 27,223.66 ms for the one-transaction MDBX bulk build.

| Global radius | Metadata lookups/query | Posting page reads/query | Candidates/query | CSR warm median (ms/128) | MDBX warm median (ms/128) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 48 | 2,752 | 945 | 1,239 | 9.14 | 287.27 |
| 56 | 7,232 | 2,405 | 2,978 | 18.83 | 735.01 |
| 64 | 12,972 | 4,233 | 4,926 | 32.80 | 1,381.14 |

The empty read-transaction control was 0.14, 0.08, and 0.08 ms respectively
for the same 128 queries. It is negligible relative to the full MDBX path, so
the measured gap is not explained by transaction lifecycle.

The page-size control did not materially change the result. At page sizes 64,
256, and 1024, each non-empty bucket on this corpus fitted in one posting page,
so the number of posting-page reads was unchanged. MDBX medians respectively
spanned 327.95/287.27/284.59 ms at radius 48, 741.53/735.01/749.88 ms at radius
56, and 1,252.76/1,381.14/1,281.13 ms at radius 64. These local-run differences are
local-run variation, not a page-size effect.

## Interpretation

For this deliberately direct layout, the result is consistent with repeated
per-bucket MDBX lookups dominating the warm candidate-union path; the empty
transaction control rules out transaction lifecycle as the explanation. CSR is
roughly 29x--42x faster in these local medians. This is evidence against a naive
one-key-per-probed-bucket MDBX hot path, not evidence that MDBX cannot
participate in a production MIH design.

MDBX remains appropriate for durable source records and can still support a
different candidate layout. The measured bulk builder also holds an in-memory
bucket map before committing, so it is not a streaming-ingestion benchmark.

## Limitations and follow-up

The raw MDBX files remain under `tmp/` and are not committed. A compact public
evidence archive retains the packed inputs, configs, reports, source snapshots,
and dependency manifest. The public asset is
[mih-mdbx-csr-storage-evidence-v2.zip](https://github.com/LimiNode/agent-memory-cpp/releases/download/evidence/mih-mdbx-csr-v2/mih-mdbx-csr-storage-evidence-v2.zip).
Its ZIP SHA-256 is
`90df1f5f49e4899764a7386be31a26cf15d8b44a3d17c766aacf7ff9ebabc888` and
its internal bundle-root SHA-256 is
`96cc4f917707c02e2a128fe6270d48451055bf21f207d81b64d0811929ba516a`.
This is one local warm-cache environment, without process-wide memory
high-water measurement, cache eviction, or concurrent readers.

The next practical challenger is not larger posting pages: it is a durable
block-CSR or whole-band CSR layout whose direct bucket addressing occurs inside
one fetched MDBX value. It must preserve the same bounded-radius and
exact-union contract, then be compared inside the full MIH -> Hamming ->
ADC/OPQ -> exact-E5 cascade.
