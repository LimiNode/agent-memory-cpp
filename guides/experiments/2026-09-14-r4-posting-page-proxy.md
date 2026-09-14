# R4 posting and THQ logical page proxy (2026-09-14)

## Question

After the corrected full native gate, how much page-shaped work does the
K16 route generate for postings and packed THQ document codes under the same
global candidate budgets?

## Protocol and scope

The experiment consumes the production AoSoA-32 K1 order stream from #407 at
`A=16,384` for all three R4 seeds and all 152 queries. It fuses whole postings
until unique-document budgets of 5k/10k/20k/50k, then counts distinct 4-KiB
ranges in namespaced posting files and in the frozen 144-byte THQ4 document
code file. A THQ record uses the inclusive byte interval `[id*144,id*144+143]`;
there are about 28.44 records per page, not 3.5. The receipt also records page
runs/transitions and exact-top-256 pages.

This is deliberately not an MDBX benchmark: it does not create an MDBX
database, control OS cache eviction, observe physical media reads, or report
service latency.  Page counts are file-range arithmetic only.

## Results

| Unique budget | Candidate docs (mean) | Posting pages (mean) | THQ code pages (mean) | Combined logical pages (mean) |
|---:|---:|---:|---:|---:|
| 5k | 5,014 | 189.6 | 4,691.0 | 4,880.6 |
| 10k | 10,017 | 331.3 | 8,686.2 | 9,017.4 |
| 20k | 20,013 | 555.0 | 15,094.1 | 15,649.1 |
| 50k | 50,012 | 1,020.6 | 26,320.9 | 27,341.5 |

The document-code gather still dominates. At 5k the exact-top-256 useful input
is 36,864 bytes, while its scattered document pages have mean amplification
26.90x. Page-run mean is 256.9 and forward contiguous runs average 1.02 pages,
so distinct-page counts do not imply sequential I/O. The result supports a
fused contiguous payload or candidate-friendly secondary representation.

## Evidence

The receipt contains 608 rows (152 queries × four budgets), binds the corrected
full-native receipt and both frozen manifests, and records
`mdbx_pages_measured=false`.  Independent audit:
`semantic_r4_posting_page_proxy_audit_v1`, 608 rows, four summaries, PASS.

Raw and receipt payloads are retained outside Git under
`E:\_repoz\agent-memory-workspaces\r4-posting-page-proxy` and are SHA-bound in
the receipt.

## Next gate

Materialize the same posting and document-code records in MDBX with explicit
page-entry/key-size settings, then compare actual file bytes, page counts, warm
read latency, and transaction overhead against this logical proxy.  Do not
interpret this proxy as that measurement.
