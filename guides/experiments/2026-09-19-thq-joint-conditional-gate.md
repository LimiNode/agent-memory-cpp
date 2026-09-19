# Joint THQ-pattern-conditioned vector gate

Date: 2026-09-19  
Branch: `research/thq-joint-conditional-wave`  
Status: `EXECUTED`

## Protocol

This gate tests a joint 3D residual codebook for each THQ4 pattern. The 384
coordinates are partitioned into 128 three-dimensional blocks; each block has
up to 64 THQ4 level patterns and a document-only Lloyd codebook with 2 or 3
bits per block. Sparse patterns fall back to the block-global codebook. Symbols
are bit-packed; the packed streams and codebooks are persisted for the 463,258
unique documents in the canonical R4 candidate shell.

The same four shuffled folds and 152-query shell are used as in the ADC,
RSLM and scalar-conditioned gates. Direct analytic cosine scoring is checked
against explicit reconstruction; no query scores or qrels enter fitting.

## Result

| arm | side code | THQ4+side | nDCG@10 | candidate-FP32 overlap | teacher overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| THQ-joint 2-bit / 128×3D | 32 B | 128 B | `.655113` | `.892105` | `.888816` |
| THQ-joint 3-bit / 128×3D | 48 B | 144 B | `.655159` | `.915789` | `.911842` |

Direct/reconstructive top-10 orders agree for all 152 queries in both arms;
maximum absolute score error is below `3.0e-7`.

This is materially better than the scalar THQ-conditioned controls
(`.645813/.652309`) and clears the stable ADC48 reference (`.647401`) at both
rates. It remains slightly below direct INT8 (`.656991`) and below persistable
RSLM4 (`.659320`), while candidate-FP32 overlap is lower than those controls.
The gain therefore supports joint conditional structure as a useful direction,
but does not yet justify production selection.

The packed side streams are exactly 32/48 B per document. The global codebooks
are 393,216/786,432 B; candidate-union totals including THQ4 and the codebook
are 59,690,240/67,495,584 B, and one-million-document extrapolations are
128,393,216/144,786,432 B. These figures describe the candidate-union
reference and its extrapolation, not a full production materialization. The
candidate-union ID mapping adds 1,853,032 B, giving complete physical
footprints of 61,543,272/69,348,616 B. The one-million-row figures assume row
index equals document ID; shared THQ thresholds and centroids are excluded.

## Evidence

Raw result SHA-256:  
`985d8d0ac90d3fd8c82e0da400503ea59fa9689b4a0dd0a0be480fe5efa8b87e`

Independent source-replay audit SHA-256:  
`226639e007a8fad55eab5349c9ffe9ccea30c70bc97039fd80469a124e15e8ca`

The audit checks source bindings, persisted ID/packed-symbol/codebook hashes,
exact packed sizes, family cardinality and fold membership, independently
recomputed THQ top-128 and candidate FP32 top-10 sequences, and a codec
decode/re-score whose top-10 must exactly match every result row, followed by
nDCG and teacher overlap.

## Decision

Unlike scalar conditioning, the joint 3D conditional codebook is worth a
follow-up. The next bounded comparison should use stronger joint codebooks or
score-aware conditional bases at the same 32/48 B side budgets, with held-out
query/domain confirmation before any native work.
