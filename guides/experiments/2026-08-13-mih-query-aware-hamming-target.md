# Query-aware Hamming-target MIH control

## 2026-08-13 - pre-execution contract

The previous MIH-aware controls improved a document-to-document calibration
proxy but did not move the held-out query-to-document retrieval frontier. This
experiment changes only the supervision relation: a shared 256-bit projection
starts from full ITQ and learns from MIRACL **train** queries and their relevant
passages. It retains a full-ITQ anchor, train-document median thresholds, and
document code-health constraints.

The train materialization excludes every held-out dev document and is marked
`retrieval_training_only`. Checkpoint selection uses a deterministic validation
subset of train queries. Its gate requires either a strict ADC-K2 survival
improvement, or simultaneous strict raw-union and Hamming-K1 survival
improvements, while candidate and posting work remain within 5% of the ITQ
anchor. Held-out dev stays untouched until the separately committed runner
executes this exact contract.

The held-out pipeline is fixed to `16x16-r56 → Hamming K1=768 → binary ADC
K2=256 → exact E5`. Five paired seeds compare the ITQ control with the
query-aware artifact. The primary outcome is paired E5-oracle ADC-K2 survival;
candidate and posting means are mandatory work context. A 10,000-replicate
paired bootstrap will describe the fixed held-out comparison only and cannot
select another radius, partition, objective, or training setting.

The protocol is recorded in
`tools/agent-memory-bench/mih-query-aware-hamming-target.example.json`.

## Result

The five predeclared seeds all completed under the same shared-`W` training
procedure. Checkpoint selection used only their deterministic train-query
validation split; the held-out dev matrix was then run once, with no change to
the radius, bands, artifact, or training setting.

Across all 6,260 paired held-out query observations, query-aware training
improved the fixed cascade at modest additional work:

| Measure | Query-aware − ITQ |
| --- | ---: |
| Raw-union E5-oracle survival | +0.003578 |
| Hamming K1=768 E5-oracle survival | +0.003674 |
| ADC K2=256 E5-oracle survival | +0.003706 |
| Final exact candidate coverage | +0.003706 |
| Reranked nDCG@10 | +0.001886 |
| Candidates/query | +15.05 |
| Posting visits/query | +19.44 |

The result is directionally positive in every predeclared seed for ADC-K2
survival (`+0.002077`, `+0.002077`, `+0.003435`, `+0.007348`, `+0.003594`).
Each seed-level 10,000-replicate query bootstrap remains individually broad,
so this is a promising confirmatory frontier shift, not a claim of a completed
production optimization. Its most important implication is diagnostic: the
retrieval-aligned query-to-passage objective moves downstream survival even
where simple within-radius proxy movement was insufficient.

## Evidence

The draft evidence archive is `mih-query-aware-hamming-target-evidence-v1.zip`.
It contains the predeclared contract, all ten reports and per-query
contributions, five selected artifacts and histories, five paired bootstraps,
and all source snapshots. Archive SHA-256:
`0cc1142572c81f98c1a9ca57eacf8eb64e13332cce92ea7c03f0bca2e8abe72b`.
Internal bundle root:
`dbe17dacdd37e54d3405ffb793e7a680512c0f8083de96f250239aa35d759dd2`.
