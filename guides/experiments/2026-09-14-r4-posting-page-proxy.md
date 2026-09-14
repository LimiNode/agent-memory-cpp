# R4 posting and THQ logical page proxy (2026-09-14)

## Question

After the corrected full native gate, how much page-shaped work does the
K16 route generate for postings and packed THQ document codes under the same
global candidate budgets?

## Protocol and scope

The experiment reuses the corrected frozen native K16 address orders for three
R4 seeds and all 152 queries.  It fuses whole postings until unique-document
budgets of 5k/10k/20k/50k, then counts distinct 4-KiB ranges in the contiguous
`physical-to-document.i32le` posting payload and in the frozen 144-byte THQ4
document-code file.  The two files are counted separately and their sum is
reported as a logical payload-page proxy.

This is deliberately not an MDBX benchmark: it does not create an MDBX
database, control OS cache eviction, observe physical media reads, or report
service latency.  Page counts are file-range arithmetic only.

## Results

| Unique budget | Candidate docs (mean) | Posting pages (mean) | THQ code pages (mean) | Combined logical pages (mean) |
|---:|---:|---:|---:|---:|
| 5k | 5,013 | 200.3 | 4,576.8 | 4,777.1 |
| 10k | 10,014 | 329.5 | 8,506.4 | 8,835.9 |
| 20k | 20,015 | 502.8 | 14,870.0 | 15,372.8 |
| 50k | 50,012 | 756.0 | 26,149.0 | 26,905.0 |

The posting payload itself is compact and highly sequential: about 756 logical
pages at the 50k budget.  The document-code gather dominates the proxy because
144-byte records spread roughly 3.5 documents across each 4-KiB page and the
candidate IDs are not physically reordered for this experiment.  This supports
the architectural separation already indicated by the AoSoA page control:
postings can remain contiguous, while document/THQ payload needs either a
candidate-friendly secondary representation or a page-aware physical reorder.

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
