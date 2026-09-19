# Persistable RSLM2/3/4 reference gate

Date: 2026-09-19  
Branch: `research/rslm-codec-wave`  
Status: `EXECUTED`

## Protocol

This is the first gate of the post-ADC score-codec wave. It materializes a
document-only randomized block-FWHT residual representation with independent
per-coordinate Lloyd-Max codebooks at 2, 3 and 4 bits. Symbols are bit-packed
and persisted for the 463,258 unique documents in the canonical R4 candidate
shell, together with the exact document-ID mapping and codebooks. Query
scoring reopens and unpacks those persisted streams; the analytic cosine
numerator/norm is checked against explicit reconstruction.

The protocol uses the same four shuffled folds and 152-query candidate shell
as the corrected ADC gates. No query scores or qrels are used to fit the
codebooks. This is a local persistable RSLM reference, not a claim to reproduce
every paper-specific two-pass/Gaussian/Ue7m9 RSLM detail; that faithful oracle
remains the next refinement of Gate A.

## Result

| arm | side code | THQ4+side | nDCG@10 | candidate-FP32 overlap | teacher overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| RSLM2 | 96 B | 192 B | `.653267` | `.938158` | `.932895` |
| RSLM3 | 144 B | 240 B | `.654028` | `.971053` | `.965789` |
| RSLM4 | 192 B | 288 B | `.659320` | `.976974` | `.969737` |

The direct scorer and reconstructive oracle agree on all 152 query top-10
orders for every arm. Maximum absolute score error is below `1.56e-7`.

RSLM2 and RSLM3 improve the stable ADC48 result (`.647401`) but remain below
the direct INT8 control (`.656991`) on this shell; RSLM4 exceeds both. The
result is still a local FWHT/Lloyd-Max reference, not a production selection.

The packed side streams are exactly 96/144/192 B per document. The global
codebooks are 6,144/12,288/24,576 B. For the candidate union, the complete
THQ4-plus-side totals including the codebook are 88,951,680/111,194,208/
133,442,880 B; the corresponding one-million-document extrapolations are
192,006,144/240,012,288/288,024,576 B. These are candidate-union and
extrapolated accounting figures, not a full production materialization.

## Evidence

Raw result SHA-256:  
`41b64c222c07a97bc235ed925556398c7746a35fe5fed386a01c068e35ac69a3`

Independent source-replay audit SHA-256:  
`a265229aa0913698f499ef20f1ce7c48b6648935e3b277bedd426098c2e94cbd`

The audit verifies source/result bindings, persisted ID and packed-symbol/codebook
hashes, exact packed sizes, family cardinality, fold membership, independently
recomputed THQ top-128 and candidate FP32 top-10 sequences, and an independent
decode/re-score whose top-10 must exactly match every result row. It also
recomputes nDCG and teacher overlap. Large symbol artifacts remain external
research inputs and are bound by SHA in the receipt.

## Next gate

Complete the faithful RSLM mechanics (two-pass randomized FWHT, scaling and
joint 2D/4D constructions), then compare against this persistable reference.
Only after that comparison should the THQ-pattern-conditioned residual gate be
promoted to the next primary experiment.
