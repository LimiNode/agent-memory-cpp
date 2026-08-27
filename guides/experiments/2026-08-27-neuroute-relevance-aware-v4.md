# Relevance-aware NeuRoute v4 configuration study

Date: 2026-08-27. This protocol follows the completed magnitude-aligned
training sanity study and the separate native MDBX cost characterization. It is
configuration-only over the already observed German, French, and Japanese 25k
corpora. It cannot make confirmation or scale-transfer claims.

## Question

Raw-Euclidean training improved the 512-probe survival frontier to roughly
`83-85%`, but at 128 probes useful addresses remain too late in best-first
order. The native study also showed that probe count alone is a poor serving
cost proxy: learned 256 is approximately PCA-cost on the measured machine,
while 512 is measurably more expensive. This study therefore asks whether
training-query supervision can improve useful early bucket ordering at a
practical native-cost point.

## Frozen treatments

All treatments use the same 384 -> 96 -> 64 -> 12 non-BatchNorm encoder,
scaled raw-Euclidean positive loss, dynamic document false-positive miner,
optimizer, and 80-epoch schedule:

1. **A, frozen control:** reuse the three raw-Euclidean model artifacts per
   language from the completed training-sanity experiment;
2. **B, query-mining ablation:** retrain A with query-to-document latent-near,
   E5-far hard-negative pairs. This mandatory ablation isolates query mining
   from the failed BatchNorm treatment in the earlier matrix;
3. **C, relevance ranking:** retrain A with an additional pairwise objective
   that makes grade-positive training documents cheaper than mined
   non-relevant documents under a differentiable approximation of the actual
   best-first address cost.

Treatment C normalizes current query/document logits with a detached full-corpus
document median and standard deviation. Its soft address cost is the mean of
`abs(query) * sigmoid(-query * document / 0.5)` across 12 bits. At epochs
`0/20/40/60`, the miner selects the cheapest non-relevant addresses after
excluding every grade-positive qrel for that training query. A temperature-.1,
margin-.1 pairwise logistic loss has weight .25. This targets useful bucket
order directly instead of asking the encoder only to reproduce E5 geometry.

BatchNorm and the dual-mask objective are intentionally absent: they were
inferior to the simpler non-BN raw-Euclidean treatment in the bounded 25k
regime and would confound the missing ablations.

## Data and leakage discipline

Training uses only the existing training-query partitions and their positive
qrels: 153/427 query/pair labels for DE, 172/363 for FR, and 430/893 for JA.
Configuration-query IDs and qrels are forbidden from model training, mining,
threshold choice, and loss construction. Evaluation remains confined to the
existing open configuration partitions of 76/85/215 queries. All three frozen
seeds are required.

## Quality and native-cost matrix

Every model is evaluated at `64/128/256/512` probes with the unchanged hard
10% candidate ceiling and ITQ256 -> Hamming768 -> ADC256 -> exact-E5256
cascade. The complete quality frontier includes E5 survival, qrels recall,
nDCG@10, candidate/posting work, exact-address reachability, and deterministic
query/address/candidate/Hamming/ADC sequence digests.

The same routes are materialized into repository-pinned MDBX with the frozen
route/address-to-ordered-u32-postings layout and 256-entry pages, then measured
after two warmups over nine passes. Timing is split into address generation,
lookup/decode, generation dedup/ceiling, Hamming, ADC, and total. Native output
must replay the Python address, candidate, Hamming, and ADC sequences exactly.
The 8-bit, 16-probe, replication-4 PCA route remains the practical control.

The primary comparison is 256 probes, not the earlier hard `<=128` gate. The
native result established 256 as the current cost-quality knee, but new route
distributions are timed rather than assumed equivalent merely because their
probe counts match.

## Decision rule

A newly trained treatment is eligible only at the common 256-probe point when:

- candidate fraction is at most 10% for every language;
- native p95 is at most `1.15x` the language's measured PCA p95;
- cross-language mean nDCG improves on A by at least .01;
- no language loses more than .01 nDCG versus A.

Eligible treatments are ordered by highest cross-language mean nDCG, then
lowest mean native p95, then treatment ID. The full 64-512 quality-cost frontier
is published regardless of this gate. A pass licenses a new frozen external
confirmation before any scale transfer. No pass triggers diagnosis of the
relevance surrogate or mining mechanism, not ad hoc selection from the open
configuration data.

## Provenance and expected interpretation

The contract binds the completed training-sanity result, evidence receipt and
model-set digest, plus the native-cost report, evidence and materialization
digests. New models are resumable and bound to the contract and measured
sources. Final evidence is replay-only and must reconstruct deterministic
quality and native candidate stages without retraining.

Improvement from B isolates the missing non-BN query-mining effect. Improvement
from C supports direct early-address relevance supervision. If neither beats A
at measured practical cost, the result will narrow the next question to the
specific surrogate/miner rather than reopening BatchNorm or treating probe
count as latency.
