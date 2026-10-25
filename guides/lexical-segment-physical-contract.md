# Lexical segment physical contract

This document defines the first dependency-free physical row contract for the
segmented lexical index. It is an encoding contract only; it does not publish
rows to MDBX and does not provide cross-store or manifest linearizability.

## Identity

Each row is addressed by the complete tuple:

```text
(scope_id, projection_kind, statistics_epoch, statistics_generation,
 token_id, segment_id, posting_layout_version)
```

The row also carries a SHA-256 membership witness for the complete ordered
posting partition. The witness is not an authority or a fencing token. A
storage reader must compare it with an independently trusted witness before
admitting a complete partition.

`flat-v2` values are a different physical format and must not be decoded as
segment rows. This contract uses encoding version `1` and posting layout
version `1`; a future physical format requires an explicit version gate and a
new decoder decision.

## Encoding

The uncompressed row is deterministic and little-endian. It contains a magic
prefix, encoding/layout versions, the physical identity, the 32-byte witness,
the posting count, every posting identity and revision field, and a trailing
SHA-256 checksum over the row body with a domain-separated prefix.

The decoder rejects unknown encoding or layout versions, truncation, malformed
lengths, trailing bytes, checksum changes, invalid posting identities, and all
configured bounds violations. It never returns a partially decoded row.

The bounded profile applies limits to segment count, postings per segment,
total postings, row bytes, and bounded UTF-8 identity strings. The codec is
intentionally uncompressed: compression, varints, block-max metadata, and
SIMD codecs belong to a later experiment.

## Canonical packing

The logical writer uses contiguous segment IDs starting at zero. For layout
version `1`, every segment except the final segment is full and the final
segment contains the remaining one through `max_postings_per_segment`
postings. A reader must reject a differently packed but otherwise equivalent
set of postings as a layout mismatch.

## Scope boundary

This slice supplies the physical key/value contract, bounded encode/decode,
membership witness, and corruption fixtures. It does not add a DBI, reinterpret
the existing `lexical_posting_segments` `flat-v2` payload, implement an MDBX
writer, or claim atomic lexical publication. Those concerns are separate
stacked PRs.
