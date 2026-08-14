# Query-aware Hamming-target MIH control

## 2026-08-13 - pre-execution contract

The previous MIH-aware controls improved a document-to-document calibration
proxy but did not move the held-out query-to-document retrieval frontier. This
experiment changes only the supervision relation: a shared 256-bit projection
starts from full ITQ and learns from MIRACL **train** queries and their relevant
passages. It retains a full-ITQ anchor, train-document median thresholds, and
document code-health constraints.

The train materialization excludes every held-out dev document and is marked
`retrieval_training_only`. For each seed, the control and treatment start from
the same saved full-ITQ document anchor `W0`; the treatment alone refines it
with train queries and qrels. Checkpoint selection uses a deterministic
validation subset of train queries. Its gate is a checkpoint-ranking diagnostic
(not seed eligibility): it prefers a checkpoint with either a strict ADC-K2
survival improvement, or simultaneous strict raw-union and Hamming-K1 survival
improvements, while candidate and posting work remain within 5% of `W0`.
Every predeclared seed remains in the held-out matrix even if its selected
checkpoint did not pass that diagnostic. Held-out dev stays untouched until the
separately committed runner executes this exact contract.

The held-out pipeline is fixed to `16x16-r56 → Hamming K1=768 → binary ADC
K2=256 → exact E5`. Five paired seeds compare the ITQ control with the
query-aware artifact. The primary outcome is paired E5-oracle ADC-K2 survival;
candidate and posting means are mandatory work context. A 10,000-replicate
paired bootstrap will describe the fixed held-out comparison only and cannot
select another radius, partition, objective, or training setting.

The protocol is recorded in
`tools/agent-memory-bench/mih-query-aware-hamming-target.example.json`.

## Result

The original v1 control used an independently fitted ITQ calibration root, so
it was a useful pipeline signal but did not isolate refinement from an anchor
change. The v2 replay below replaces it with the matched saved-`W0` control.

The five predeclared seeds all completed under the same shared-`W` training
procedure. Checkpoint selection used only their deterministic train-query
validation split; the held-out dev matrix was then run once, with no change to
the radius, bands, artifact, or training setting.

Across all 6,260 paired held-out query observations, query-aware training
improved the fixed cascade at modest additional work:

| Measure | Query-aware − ITQ |
| --- | ---: |
| Raw-union E5-oracle survival | +0.001022 |
| Hamming K1=768 E5-oracle survival | +0.000958 |
| ADC K2=256 E5-oracle survival | +0.001006 |
| Final exact candidate coverage | +0.001006 |
| Reranked nDCG@10 | -0.000109 |
| Candidates/query | +5.59 |
| Posting visits/query | +7.39 |

ADC-K2 deltas by seed are `-0.000080`, `+0.000080`, `+0.001038`,
`+0.001677`, and `+0.002316`. The selected checkpoint gate diagnostics are
`true`, `false`, `true`, `true`, and `true` for seeds 52--56 respectively;
the false diagnostic for seed 53 did not remove that predeclared seed. Each
seed-level 10,000-replicate query bootstrap remains individually broad. The
matched-anchor replay therefore supports a small retrieval-aligned survival
signal, but neither an nDCG improvement nor a completed production
optimization. Its main diagnostic implication remains that query-to-passage
supervision is more relevant than document-only proxy learning.

## Evidence

The draft evidence archive is `mih-query-aware-hamming-target-evidence-v2.zip`.
It contains the predeclared contract, all ten reports and per-query
contributions, five matched ITQ anchors, five selected artifacts and histories,
five paired bootstraps, and source snapshots extracted by `git show` from the
exact evidence source commit. Archive SHA-256:
`b02bec1dbd5407db573be97de4e9c01d405d8d2ce36abde755509fcac04f48a5`.
Internal bundle root:
`0e4764baaad912e5657a004c6f406728e94b1ebc65b5fe931b25fa050004326d`.
