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
