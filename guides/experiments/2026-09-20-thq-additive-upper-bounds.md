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
payloads are 4/6/8 B plus rate-matched 32/48 B controls (the latter use 32/48
stages), on top of the canonical 96 B THQ4 base. The corresponding selected
path records are 100/102/104/128/144 B per document. The production boundary
remains the frozen R4 candidate shell followed by canonical THQ4
interval-squared top-128.

The 4/6/8/32/48 B labels describe one persisted path only. The score oracle
retains `beam_width` paths, requires the exact FP32 document score at query
time, and therefore has no serving payload size. Its retained beam storage
and the shared FP32 codebook footprint are reported separately. The MSE-beam
variant also persists its single offline-selected path separately from the
research-only full beam artifact.

## Interpretation rules

The score oracle shows how closely the codebook family can approximate exact
per-document scores when the encoder sees `q`; it must not be reported as a
retrieval or production candidate. It is not a mathematical retrieval upper
bound, and a weak oracle row does not prove that additive representation
capacity is exhausted. If it is strong while greedy and MSE-beam rows are
weak, path selection or encoding is a plausible bottleneck. If the MSE beam is
strong, only then is a faithful conditional-codebook implementation justified.

The runner materializes FP32 codebooks, greedy paths, the offline MSE-selected
path, and the complete research beam beside the result. It reports global
codebook bytes, per-document record bytes, retained-beam bytes, and a complete
1M-document footprint for non-leaky variants. The independent audit replays
THQ top-128, additive decoding, final top-10 IDs, nDCG, and teacher overlap
from those artifacts and the source bundle. It rejects compact result JSON,
decoded INT8, and legacy Hamming payloads as document substitutes. Until the
1M FP32 documents, matching queries, qrels, and teacher IDs are available, the
gate remains `not executed`; no quality number is inferred from earlier
eight-query compact artifacts.

Implementation: `tools/agent-memory-bench/run-thq-additive-upper-bounds.py`.
