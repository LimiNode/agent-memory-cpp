# Structural SegmentSet (experimental)

`build_structural_segment_set()` is a dependency-free Markdown structure pass.
It canonicalizes CRLF/CR to LF, validates UTF-8, records UTF-8 byte ranges,
and emits typed rows for headings, paragraphs, list items, fenced code, tables,
and image placeholders. Paragraph rows retain sentence ranges relative to the
row text.

The recipe is versioned and the identity digest includes the normalized source,
recipe descriptor, and explicit segment budgets. A source revision is therefore
not a SegmentSet, and changing the chunk budget intentionally creates a new
SegmentSet identity. This slice does not claim Markdown/CommonMark
completeness or semantic quality; it is deterministic structural infrastructure
for later ingestion experiments.

The implementation enforces source, segment-count, and per-segment byte budgets.
Ranges address the normalized UTF-8 representation, not the pre-normalized
host byte stream. It does not publish to storage, activate retrieval, or infer
quality from the resulting boundaries.
