# NeuRoute magnitude-aligned training sanity study

Date: 2026-08-27. This protocol follows the post-hoc v3 alignment audit. It is
a configuration-only training study over the already observed German, French,
and Japanese 25k corpora. It cannot make an external confirmation claim and
does not reopen the failed Japanese scale-transfer gate.

## Motivation

The audit found that raw v3 margins contain a real bit-confidence signal, but
exact E5-neighbour address reachability remains only `31-39%` at 64 probes and
`76-83%` at 512 probes. It also found that Japanese full exact-E5 nDCG is
strong, while extra E5 survival has little marginal qrels value. Before adding
relevance-aware supervision, this study asks whether a magnitude-aligned
Euclidean objective can improve the routing frontier itself.

## Treatments

The five-treatment, three-seed, three-language matrix contains 45 model rows;
the nine v3 controls reuse their already frozen model bytes and 36 models are
newly trained:

1. frozen cosine/dynamic v3 control;
2. raw-Euclidean distances on the same positive and dynamically mined
   document-pair structure as v3;
3. a source-near OR latent-near dual-mask Euclidean objective;
4. the same dual-mask objective with `Linear -> BatchNorm -> ReLU` hidden
   blocks and a linear final layer;
5. the BatchNorm treatment plus explicit query-to-document latent-near/E5-far
   mining.

NeuRoute defines the latent distance target as
`gamma * sqrt(Ldim / Edim) * source_distance`, uses `gamma=0.6`, and selects
the closest 0.5% source-space pairs together with pairs already below the
corresponding latent threshold. Those formulae and the hidden-block pattern are
preserved here. This remains a bounded 25k adaptation rather than a reproduction
of the paper's 2M-vector, batch-4096, 500-epoch training schedule. To fit the
existing controlled harness, training stays at the v3 optimizer, 80 epochs,
batch 512, and uses a deterministic 128-document pairwise subbatch.

## Data discipline and evaluation

Training uses only each language's existing training query IDs and all 25,000
documents. Selection and reporting use only the existing open configuration
partition (`76/85/215` DE/FR/JA queries). No newly trained model is evaluated on
the earlier internal partitions in this study.

Every treatment is evaluated at `16/32/64/128/256/512` best-first probes under
the hard 10% candidate ceiling and the unchanged ITQ256 -> Hamming768 -> ADC256
-> exact-E5256 cascade. The 8-bit/16-probe/replication-4 PCA route remains the
practical control. Deterministic evidence includes E5 survival, qrels recall,
nDCG@10, candidate fraction, accepted probes, posting entries requested,
candidate-sequence digests, parameter counts, margin curves, and exact-address
reachability. Python latency is not a headline metric; deterministic work
counters avoid turning interpreter timing into a serving claim.

## Decision rule

A treatment is routing-efficient only if one common probe budget no larger
than 128 satisfies all three languages simultaneously:

- candidate fraction is at most 10%;
- ADC E5 survival is within 2 points of frozen v3 at 512 probes;
- nDCG@10 is within .01 of frozen v3 at 512 probes;
- nDCG@10 is within .01 of PCA at 16 probes.

Eligible rows are ordered by lowest probe budget, then highest cross-language
mean nDCG, then treatment ID. Passing this configuration-only rule licenses a
new frozen external confirmation, not scale transfer. If no row passes, the
next research protocol is relevance-aware v4.

## Provenance and resumability

The contract requires the byte-identical v3 alignment audit result and evidence
receipt before training. Result, model, source, E5, ITQ/ADC, split, threshold,
and candidate-sequence identities are fail-closed. Each trained model is saved
immediately with contract and measured-source hashes; interrupted execution can
resume only from a matching artifact. The final evidence writer forbids
training, reconstructs all 270 quality rows from the saved model bytes, and
requires byte-identical canonical output.

## Expected interpretation

Raw-Euclidean improvement alone would support the loss/magnitude hypothesis.
Additional dual-mask improvement would support in-batch anti-collapse over the
explicit v3 miner. A further BatchNorm gain would identify architecture-level
calibration. Query-mining gains would isolate the current document-only mining
gap. Failure of all four new treatments would move the research priority to
relevance/ranking-aware supervision rather than further E5-geometry tuning.

## Result

The complete matrix finished in about 39 minutes on the local CPU. All 45 model
rows, 270 quality rows, three PCA controls, thresholds, work counters, and
candidate-sequence digests were reconstructed by the replay-only evidence
writer without retraining. The canonical report SHA-256 is
`02985871f9fd70d3a8634ef42840f7e799957f24fd0797278560a22dc848c5d6`;
the evidence receipt SHA-256 is
`80e1de6387379666a1b914067714172e481a3ae084a9906351fa1ba293f5d673`.
The receipt records `integrity_replay_passed=true`, model-set SHA-256
`5cfdb8d055ddd32334ab68135e9c6e267c635ab1f29ec56766bdcdd11ebf5729`,
`model_count=45`, `quality_row_count=270`, and
`confirmation_claims_permitted=false`.

No treatment passed the preregistered common-budget efficiency rule at 128
probes or below. The decision is therefore `selected=null` and the frozen next
step is `preregister_relevance_aware_v4`. This is a completed negative result
for the strict efficiency hypothesis, not a failure of magnitude-aligned
training in general.

### Same-budget 512-probe frontier

At the existing hard-10% operating point, raw Euclidean training on the v3
mined-pair structure is substantially stronger than the frozen v3 objective on
French and Japanese and nearly quality-neutral on German:

| Language | Treatment | ADC E5 survival | nDCG@10 | ADC qrels recall |
| --- | --- | ---: | ---: | ---: |
| DE | frozen v3 | 81.10% | .6625 | 86.11% |
| DE | raw Euclidean mined pairs | 85.39% | .6573 | 87.55% |
| DE | dual-mask Euclidean | 85.13% | .6636 | 87.08% |
| FR | frozen v3 | 74.86% | .6237 | 80.48% |
| FR | raw Euclidean mined pairs | 83.06% | .6720 | 88.12% |
| FR | dual-mask Euclidean | 80.35% | .6574 | 85.10% |
| JA | frozen v3 | 71.35% | .6716 | 79.73% |
| JA | raw Euclidean mined pairs | 82.53% | .7298 | 89.39% |
| JA | dual-mask Euclidean | 81.86% | .7180 | 87.14% |

The simplest causal change is therefore the strongest quality result: replacing
normalized-cosine pair loss with scaled raw-Euclidean distance while retaining
the established v3 positive pairs and dynamic document miner. The paper-style
dual mask further improves low-budget exact-address reachability in German and
Japanese, but its larger candidate/posting frontier does not beat the simpler
raw-Euclidean treatment on final French or Japanese quality.

### Why the efficiency gate failed

At 128 probes, raw Euclidean reaches only `64.39/58.98/59.32%` ADC E5 survival
and `.5543/.5217/.5981` nDCG on DE/FR/JA, well below frozen v3 at 512. The dual
mask reaches `64.91/58.55/63.64%` and `.5473/.5479/.6136`. Neither can preserve
the 512-probe quality contract at the required common budget. Even at 256
probes, German remains more than two survival points and about .027 nDCG below
its v3-512 reference.

The deterministic work counters add an important serving caveat. A learned
512-probe row requests roughly 3.1k posting entries per query, whereas the
replicated PCA control requests about 6.0-6.4k. Thus `512 versus 16 lookups`
does not imply 32x total posting work: the learned index uses single placement
and narrower lists. Conversely, lookup and MDBX transaction overhead are not
represented by these Python counters. A native cost study would be required
before making a latency claim, but it is not licensed as a continuation of the
failed quality-at-128 gate.

### BatchNorm and query mining

The bounded BatchNorm adaptation fails sharply. At 512 probes its DE/FR/JA
survival is only `36.97/28.24/39.89%`, with nDCG
`.3526/.3161/.4363`. Adding query-to-document hard-negative mining gives a
consistent but insufficient recovery to `38.51/32.71/43.72%` survival and
`.3879/.3679/.4528` nDCG. This result applies to the frozen 25k, batch-512,
80-epoch adaptation; it does not refute BatchNorm under NeuRoute's original
2M-vector, batch-4096, 500-epoch regime. Because query mining was only added
after the failed BatchNorm treatment, this study establishes its positive
increment within that path but does not test it on the stronger non-BN raw
Euclidean treatment.

## Interpretation

The study separates three conclusions:

1. Magnitude-aligned Euclidean supervision is a real improvement to the learned
   representation and transfers across all three observed languages.
2. It does not make the representation cheap enough to probe under the frozen
   <=128 common-budget contract; no external confirmation or scale transfer is
   licensed.
3. The simple non-BN mined-pair treatment is stronger than the more
   paper-shaped bounded adaptations, while relevance alignment remains the next
   preregistered bottleneck.

The appropriate next protocol is relevance/ranking-aware v4, using the
non-BN raw-Euclidean mined-pair treatment as the learned control and PCA as the
practical serving control. Any separate investigation of intermediate budgets,
native lookup/posting cost, or query mining on the non-BN path must be declared
as a new question rather than retroactively changing this decision.
