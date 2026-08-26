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
