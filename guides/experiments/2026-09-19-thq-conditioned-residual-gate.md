# THQ-pattern-conditioned residual gate

Date: 2026-09-19  
Branch: `research/thq-conditioned-residual-wave`  
Status: `EXECUTED`

## Protocol

This gate tests whether the existing THQ4 coordinate pattern can condition a
small residual codebook. For each coordinate and THQ4 level, a document-only
Lloyd-Max codebook is fit at 2 and 3 bits. Symbols are bit-packed and persisted
for the 463,258 unique documents in the canonical R4 candidate shell. Query
scoring reopens and unpacks the persisted stream, then checks analytic direct
scoring against explicit reconstruction.

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

The packed side streams are exactly 96/144 B per document. The global
codebooks are 24,576/49,152 B; candidate-union totals including THQ4 and the
codebook are 88,970,112/111,231,072 B, and one-million-document
extrapolations are 192,024,576/240,049,152 B. These figures describe the
candidate-union reference and its extrapolation, not a full production
materialization.

## Evidence

Raw result SHA-256:  
`cc972068c6564c01fad068b54aa748913c725ff64562e814a9783912a8de5ed1`

Independent source-replay audit SHA-256:  
`4b6aee0c518fe56a06fb93e26dc19157cc17ac4620d6cc027b0bad6826a9b69e`

The audit independently checks source bindings, persisted ID/packed-symbol/codebook
hashes, exact packed sizes, row cardinality and fold membership, THQ top-128
and candidate FP32 top-10 sequences, and a codec decode/re-score whose top-10
must exactly match every result row, followed by nDCG and teacher overlap.

## Decision

The simple THQ-pattern-conditioned scalar residual is not a production
candidate. The next useful conditional experiment is a joint vector codebook or
score-aware conditional basis, not another scalar bit sweep. Faithful RSLM
mechanics and independent RaBitQ/TurboQuant/NEQ controls remain separate gates.
