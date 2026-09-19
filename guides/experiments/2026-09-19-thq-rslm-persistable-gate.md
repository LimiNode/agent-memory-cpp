# Persistable RSLM2/3/4 reference gate

Date: 2026-09-19  
Branch: `research/rslm-codec-wave`  
Status: `EXECUTED`

## Protocol

This is the first gate of the post-ADC score-codec wave. It materializes a
document-only randomized block-FWHT residual representation with independent
per-coordinate Lloyd-Max codebooks at 2, 3 and 4 bits. Symbols are persisted
for the 463,258 unique documents in the canonical R4 candidate shell, together
with the exact document-ID mapping and codebooks. Query scoring uses the
persisted THQ4 base and an analytic cosine numerator/norm; reconstructive
decoding is retained as an independent parity oracle.

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

The RSLM4 quality reproduces the previous bounded RSLM-like control (`.659320`)
while now proving persistable candidate symbols and direct/reconstructive
parity. RSLM2 and RSLM3 do not improve the stable ADC48 result (`.647401`) or
the direct INT8 control (`.656991`) on this shell; RSLM4 remains a useful
quality/storage reference, not a production selection.

## Evidence

Raw result SHA-256:  
`1c901886246f4be240071ef17040873776204bae9089750ff01cb68d2cdec8c3`

Independent source-replay audit SHA-256:  
`f2ae7875d06ae5c22785a73160975cd5c9e2d326054e1c74a20bbb9287ca0cb2`

The audit verifies source/result bindings, persisted ID and symbol hashes,
family cardinality, fold membership, independently recomputed THQ top-128 and
candidate FP32 top-10 sequences, nDCG and teacher overlap. Large symbol
artifacts remain external research inputs and are bound by SHA in the receipt.

## Next gate

Complete the faithful RSLM mechanics (two-pass randomized FWHT, scaling and
joint 2D/4D constructions), then compare against this persistable reference.
Only after that comparison should the THQ-pattern-conditioned residual gate be
promoted to the next primary experiment.
