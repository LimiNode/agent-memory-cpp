# NeuRoute final-rerank implementation ceiling

## Question

Does a fused implementation of the selected 244-byte uniform INT5/SIMDComp
final-document codec materially reduce complete R4 retrieval latency compared
with a full decode buffer?

## Frozen boundary

The DE-1M internal partition, three model seeds, R4 K32 routing, learned
address scorer, 5,000-document boundary, Hamming top768, ADC top64, tie rules,
and selected uniform per-document INT5 codec remain unchanged. The physical
store contains 1,000,000 records of 244 bytes and is bound by SHA-256
`a49da89c1d79815af718fb3a41d8d2fb3e9644e98f48ac5b4323cf561b5bbbbb`.

This experiment corrects an implementation gap in the earlier full-cascade
harness: its top64-to-top10 stage still read FP32 document vectors. The new
matrix executes the real physical INT5 store and retains FP32 only as a
descriptive reference.

The treatments are:

- FP32 pairwise scoring;
- INT5 random gather, full 384-value SIMDComp decode buffer, dot, and top10;
- the same INT5 random gather followed by three 128-value unpack-and-dot
  blocks, avoiding the full decoded-document buffer.

Both production routing modes, homogeneous INT8 and nonlinear power-0.5 INT5,
are crossed with all three final treatments at 1, 8, and 16 workers. Each of
the 54 native invocations uses two trace repetitions, one warm-up batch, and
two measured batches.

## Correctness

The decode-buffer and fused-block INT5 paths agreed on routing scores, selected
addresses, candidate documents, Hamming top768, ADC top64, and final top10 for
all 5,472 paired query executions. Every process revalidated the 244 MB store
hash before measurement. No codec, quantization parameter, candidate rule, or
quality selection changed.

## Result

Across both routing modes and all three seeds at one worker:

| INT5 final implementation | Final-stage mean ms | Full-cascade mean ms |
| --- | ---: | ---: |
| full decode buffer | 0.05835 | 10.0433 |
| fused 128-value blocks | 0.04843 | 10.0734 |

The fused treatment reduces the final stage by 17.0%, but the stage is only
about 0.5% of the complete request. The measured full-cascade change is
-0.30% rather than a gain. That small opposite-sign movement is consistent
with whole-query noise and does not open the preregistered 5% continuation
gate.

The final decomposition includes physical gather, unpack, dot, top10, total
final stage, and complete retrieval p50/p95/p99 for every native report.

## Decision

Use the fused-block path as the compact implementation: it preserves the
selected codec's ranking identity and removes the unnecessary 384-value
decoded buffer. Stop further optimization of top64-to-top10. Even a substantial
additional microkernel improvement cannot materially move the current R4
end-to-end latency budget.

This closes the final-rerank implementation ceiling. It does not claim that
uniform INT5 is identical to FP32, and it does not reopen the already completed
codec-quality selection.

Raw native reports, the byte-replayed compact result, and evidence are under
`tmp/neuroute-final-rerank-ceiling/` in the experiment worktree and are not
committed.
