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
route/address/page key layout, packed-u32 posting pages of 256 entries, and one
read-only transaction per query, then measured
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

## Result

The complete matrix finished with 27 model artifacts, 108 quality rows, and
111 native timing rows. The replay-only evidence run reconstructed every
quality row from model bytes and repeated every native address, candidate,
Hamming, and ADC sequence without retraining or replaying timings. Both replays
passed. Artifact SHA-256 values are:

| Artifact | SHA-256 |
| --- | --- |
| quality result | `e0bca0ea0b4e70bcb391de1ff3806f0fd76cd75a20e077761f4ebca5dc54df5d` |
| native materialization | `66e8414b0194a908ddd8b9802076e6a65ee47930b331a62f09d8b1e63650e228` |
| native timing result | `14d521fa42089b3b31c89000993f4be5e41263174e1c03b6056f6d0c35a12509` |
| evidence receipt | `e8da725d46c42659529b39a6bb1dc867b183275fa25bea1ba79d3a19a1a858a9` |
| 27-model set | `b80d6e19425f24d3de65eb3af19da669ab0512f7dcbbf3f2fafe015f95ba65a0` |

The native evaluator was built in mandatory bundled mode and records
authoritative gitlink identities `fc8b8e4` for libmdbx and `e9e9f2f` for
mdbx-containers. The final materialization also binds the new wrapper, the
reused native helper, and the quality runner by individual source hashes. An
initial local run built in dependency `AUTO` mode was
correctly rejected by the evidence writer as non-authoritative and is not part
of these artifact identities.

### Frozen 256-probe decision point

Treatment B is useful but language-dependent. It improves French nDCG by
`.0295`, while changing German by `-.0073` and Japanese by `-.0036`. Its
cross-language mean gain is `.0062`, below the preregistered `.01` gate. All
three native p95 ratios remain safely below the `1.15x` PCA limit:

| Language | A nDCG | B nDCG | B delta | B survival | B p95 ms | PCA p95 ms | Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DE | .6286 | .6213 | -.0073 | 76.75% | 1.393 | 1.304 | 1.068x |
| FR | .6185 | .6480 | +.0295 | 72.47% | 1.383 | 1.330 | 1.039x |
| JA | .6875 | .6840 | -.0036 | 71.64% | 1.401 | 1.316 | 1.065x |

This closes the missing non-BatchNorm query-mining ablation. Query-to-document
E5-hard negatives can improve relevance, but the effect does not transfer
uniformly across the three observed languages and does not pass the frozen
common-recipe gate. It is not rejected on serving cost: its 256-probe route is
approximately PCA-cost on the measured machine.

Treatment C fails decisively in its current joint-training form:

| Language | A nDCG | C nDCG | C delta | A survival | C survival |
| --- | ---: | ---: | ---: | ---: | ---: |
| DE | .6286 | .5174 | -.1113 | 77.68% | 52.41% |
| FR | .6185 | .4826 | -.1358 | 74.00% | 50.00% |
| JA | .6875 | .4720 | -.2155 | 73.77% | 46.45% |

The failure occurs before Hamming/ADC reranking. Mean exact-address
reachability at 256 probes falls from `77.76/74.00/73.83%` for A to
`52.41/50.00/46.47%` for C on DE/FR/JA. At 512 it remains only
`67.72/64.27/60.23%`. Thus the frozen soft-mismatch objective did not merely
trade E5 survival for qrels relevance; it damaged the address geometry itself.
Its native p95 remains close to PCA because the route still requests the same
bounded work, so latency cannot rescue the quality regression.

The preregistered outcome is therefore `selected=null`, with next step
`diagnose_relevance_objective_before_external_confirmation`. No external
confirmation or scale transfer is licensed by this configuration study.

## Interpretation and next checks

The two branches now have different evidence:

- **Query mining:** preserve B as a diagnostic candidate. Before another
  training sweep, measure mined-negative overlap with qrels, E5 distance, code
  distance, and per-language query types. The French-only gain suggests either
  a language/data-distribution interaction or negative-selection mismatch,
  not a general latency problem.
- **Direct relevance ranking:** do not tune the failed joint recipe on the open
  configuration queries. A new protocol should use training-only diagnostics
  to compare continuation from the frozen A model against training from
  scratch, lower ranking weights, delayed ranking activation, and a surrogate
  whose target is explicitly the rank/cost of the positive document address.
  The first acceptance check must be preservation of A's exact-address
  reachability before final nDCG is considered.

These timings remain directional single-machine warm-cache evidence. They
exclude E5 query encoding and exact-E5 reranking, make no cold-cache claim, and
do not justify selecting the French B row post hoc as a deployable common
recipe.
