# THQ additive-codec capacity diagnostic

Date: 2026-09-20  
Status: `IMPLEMENTED; FULL REPLAY BLOCKED`

## Purpose

The existing THQ-joint and RSLM-like rows are bounded controls, not evidence
that additive or conditional learned codecs have been exhausted. This gate is
a diagnostic relevant to AVQ/AAQ/QINCo directions, not a reproduction of any
paper implementation.

The experiment separates three neutral local references:

1. `additive_greedy`: document-only global additive codebooks, greedily encoded;
2. `additive_mse_beam`: the same codebooks with a reconstruction-MSE beam;
3. `additive_fp32_score_oracle`: a query-leaking member of that beam chosen by
   minimum absolute error to the exact FP32 cosine score for the same document.

The third row measures per-document score-approximation capacity. It is not a
retrieval upper bound: it can improve the score of an irrelevant document and
therefore is not a deployable scorer.

Each stage has 256 codewords, so one stage stores one byte. The tested side
payloads are therefore 4/6/8 B (4/6/8 stages), and total THQ4 sizes are
100/102/104 B. The production boundary remains the frozen R4 candidate shell
followed by canonical THQ4 interval-squared top-128.

## Interpretation rules

The score oracle shows how closely the codebook family can approximate exact
per-document scores when the encoder sees `q`; it must not be reported as a
retrieval or production result. If even this row is below the INT8/RSLM
frontier, the tested additive capacity is insufficient. If it is strong while
greedy and MSE-beam rows are weak, the bottleneck is encoding/search rather
than representation capacity. If the MSE beam is strong, only then is a
faithful conditional-codebook implementation justified.

The runner materializes FP32 codebooks and greedy/beam code streams beside the
result. The independent audit replays THQ top-128, additive decoding, final
top-10 IDs, nDCG, and teacher overlap from those artifacts and the source
bundle. It rejects compact result JSON, decoded INT8, and legacy Hamming
payloads as document substitutes. Until the 1M FP32 documents, matching
queries, qrels, and teacher IDs are available, the gate remains `not executed`;
no quality number is inferred from earlier eight-query compact artifacts.

Implementation: `tools/agent-memory-bench/run-thq-additive-upper-bounds.py`.
