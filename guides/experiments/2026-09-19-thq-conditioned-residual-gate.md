# THQ-pattern-conditioned residual gate

Date: 2026-09-19  
Branch: `research/thq-conditioned-residual-wave`  
Status: `EXECUTED`

## Protocol

This gate tests whether the existing THQ4 coordinate pattern can condition a
small residual codebook. For each coordinate and THQ4 level, a document-only
Lloyd-Max codebook is fit at 2 and 3 bits. Symbols are persisted for the
463,258 unique documents in the canonical R4 candidate shell. Query scoring
uses the persisted THQ4 base plus decoded residual; analytic direct scoring is
checked against explicit reconstruction.

The same four shuffled folds and 152-query shell are used as in the ADC and
persistable RSLM gates. No query scores or qrels enter fitting.

## Result

| arm | side code | THQ4+side | nDCG@10 | candidate-FP32 overlap | teacher overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| THQ-conditioned 2-bit | 96 B | 192 B | `.645813` | `.923684` | `.919737` |
| THQ-conditioned 3-bit | 144 B | 240 B | `.652309` | `.948684` | `.944737` |

Direct/reconstructive top-10 orders agree for all 152 queries in both arms;
the maximum absolute score error is below `3.0e-7`.

The conditional codebooks do not beat the stable ADC48 control (`.647401`) at
2 bits and do not beat direct INT8 (`.656991`) or persistable RSLM4 (`.659320`)
at 3 bits. This is a bounded negative for this document-only conditional
scalar construction. It does not disprove joint conditional vector codecs or
retrieval-aware training.

## Evidence

Raw result SHA-256:  
`4d1bec192f8cf3c19d57f53ee88b8adeeae1bd0d0df32576cedb4837a6e0c762`

Independent source-replay audit SHA-256:  
`b0b60c72e9a9b35f10c035f6bb9eee8ed2070048e8888fc96dc0517802ac2561`

The audit independently checks source bindings, persisted ID/symbol/codebook
hashes, row cardinality and fold membership, THQ top-128 and candidate FP32
top-10 sequences, nDCG and teacher overlap.

## Decision

The simple THQ-pattern-conditioned scalar residual is not a production
candidate. The next useful conditional experiment is a joint vector codebook or
score-aware conditional basis, not another scalar bit sweep. Faithful RSLM
mechanics and independent RaBitQ/TurboQuant/NEQ controls remain separate gates.
