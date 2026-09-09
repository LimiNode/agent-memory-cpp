# NeuRoute R4 actual-document representative materialization

## Context

- Date: 2026-08-30
- PR: stacked on the document-level replication-topology diagnostic
- Status: full DE-1M materialization and independent byte replay complete

## Question

Can the frozen single-assignment 16-bit route expose compact, deterministic
sets of actual documents per occupied address so a later study can separate
full-resolution document interaction from the current K8 centroid/prototype
geometry and R3 moment summaries?

## Frozen protocol

The DE-1M corpus, three 16-bit route seeds, single document assignment, and
authoritative E5 bytes are unchanged. No query, qrel, teacher target, or learned
model participates in representative selection.

For every occupied address, the first representative is the actual document
with maximum cosine to the normalized address centroid. Remaining documents
are selected by deterministic farthest-first maximin cosine, with the lowest
global document position breaking exact ties. The materialized K32 set has
effective size `min(posting_count, 32)`; K8 and K16 are strict prefixes. Only
global document positions are stored. The existing K8 centroid-plus-seven
farthest-first construction is rebuilt and hashed as a control.

## Audit result

| Seed | Occupied addresses | Actual K8 positions | Actual K16 positions | Actual K32 positions |
|---:|---:|---:|---:|---:|
| 2026082701 | 65,108 | 454,322 | 705,898 | 891,610 |
| 2026082702 | 65,039 | 446,322 | 688,370 | 876,204 |
| 2026082703 | 65,191 | 457,455 | 710,544 | 898,743 |

Every active representative is unique within its address, belongs to that
address's frozen posting list, and has a valid global document position. Every
inactive slot is `-1`, and every effective count equals
`min(posting_count, 32)`. There is no document replication and no change to
candidate accounting.

Result SHA-256 is
`ee25eee2ee26fc334e3367b5ad7b27e11be4a6a90a47b049d2a35752e5c60d71`.
An independent second corpus-wide materialization reproduced the canonical
result and all twelve array artifacts byte for byte. Evidence SHA-256 is
`e3c4111bac603a639d0170660cf352d5075a23408b6c93ea682cfb7f926a0aa3`.

## Interpretation

The materialization establishes a clean R4 substrate. The next comparison can
hold the partition, shortlist, posting mass, and cascade fixed while varying
only whether address scoring sees current K8 geometry or exact interactions
with actual K8/K16/K32 documents. Because representative vectors are resolved
from the authoritative corpus payload on demand, the stored research artifact
remains compact and does not duplicate multi-gigabyte float arrays.

## Limitations

- Selection is teacher-blind. It does not claim that maximin representatives
  are optimal for the held-out query distribution.
- Exact K32 selection is an offline diagnostic operation and has no latency or
  native-storage claim.
- This PR materializes and audits representatives only. It makes no quality
  claim until the matched interaction ladder is measured.
- Current K8 contains an address centroid in slot zero; it is not an all-document
  K8 set and remains a distinct control.

## Next check

Keep the exact top-1024 K8 coarse shortlist fixed. Train matched address scorers
over R0 plus exact full-384D query interactions with current K8 and actual
K8/K16/K32 representatives. Compare max, top-two, smooth log-sum-exp, and a
small learned set-pooling treatment at strict `.003`, `.004`, and `.005` unique
candidate fractions. Teacher-trained representative selection remains outside
that matched interaction PR.
