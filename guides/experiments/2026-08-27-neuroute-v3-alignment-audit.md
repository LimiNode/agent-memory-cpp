# NeuRoute v3 loss, probing, and relevance alignment audit

Date: 2026-08-27. This is a post-hoc mechanism diagnostic over the already
observed German, French, and Japanese v3 experiments. It performs no training,
selects no treatment, changes no earlier gate, and makes no confirmation claim.

## Question

The v3 loss preserves cosine geometry after normalizing the 12-dimensional
output, while best-first query routing orders bit flips by the magnitude of the
unnormalized, median-centred logits. Global variance and covariance penalties
act on the raw output, but do not directly train query-specific bit confidence.
The audit asks two separate questions:

1. Do small query margins actually identify the bits that differ for exact E5
   neighbours, and can the neighbour addresses be reached by small probe
   budgets?
2. Are the additional E5 neighbours rescued by dynamic v3 useful under MIRACL
   qrels, or does E5-survival improvement diverge from final relevance?

## Frozen inputs and method

The contract pins the existing result bytes, their original contracts, all
three dynamic-model seeds, a single 12-bit/512-probe learned route, the existing
8-bit/16-probe/replication-4 symmetric PCA control, and the hard 10% candidate
ceiling. Both the open configuration partition and the already observed
internal partition are diagnostic strata; neither may be used to restate a new
confirmatory result.

For every language and stratum the runner independently reloads the E5 and
ITQ/ADC roots, verifies each model and median threshold, and records:

- full exact-E5 nDCG@10;
- raw-union and post-ADC E5 survival and qrels recall;
- final nDCG@10;
- Dynamic-only and PCA-only candidate relevance and teacher scores;
- per-query correlation of the Dynamic-minus-PCA E5-survival and nDCG deltas;
- bit mismatch probability by ascending query-margin rank;
- exact-neighbour-address reachability at 16/32/64/128/256/512 probes;
- normalized-cosine and raw-Euclidean distance correlations, separately for
  query-to-document and deterministic sampled document-to-document pairs.

The evidence writer reruns the complete computation from frozen bytes and
requires byte-identical canonical output. Large raw reports remain local under
the repository's raw artifact policy; this note records their SHA-256 receipts
and compact conclusions after execution.

## Expected outcomes

Poor margin ordering or weak low-budget address reachability would support a
loss-to-probing mismatch and license a new, preregistered magnitude-aligned
training sanity study. Strong margin calibration combined with weak qrels value
of Dynamic-only candidates would instead prioritize relevance-aware
supervision. Both mechanisms may coexist.

## Limitations

All partitions and all three languages have already been observed. The audit is
diagnostic evidence only. It cannot validate a new architecture, choose a
production treatment, or reopen the failed Japanese scale-transfer gate.

## Result

The canonical local report SHA-256 is
`df4023d25d7ea795ed8f11b120983498ccedf597cd7270dc903bcf26ac495a01`.
The byte-identical full replay receipt SHA-256 is
`72dffeb04a976a5109fc5ede79be5594027928a5a5a96b8bcdfc7af37a21e7c6`;
`integrity_replay_passed=true` and
`confirmation_claims_permitted=false`.

The already observed internal strata give:

| Language | Full E5 nDCG | Dynamic ADC survival | PCA ADC survival | Dynamic nDCG | PCA nDCG |
| --- | ---: | ---: | ---: | ---: | ---: |
| German | .7476 | 79.52% | 62.76% | .6887 | .6088 |
| French | .6848 | 74.26% | 60.12% | .6064 | .5639 |
| Japanese | .8254 | 71.95% | 67.53% | .6982 | .7006 |

Japanese has the strongest full exact-E5 result of the three local frozen
corpora. A weak Japanese E5 teacher therefore does not explain the architecture
gate failure in this setup.

The raw-logit margins are useful confidence signals. Across all languages, the
probability that an exact E5 top-10 neighbour differs at a bit is about
`45-46%` for the least-confident coordinate and falls to `5.7-7.0%` for the
most-confident coordinate. Nevertheless, finding the neighbour's exact address
still requires a large combinatorial frontier:

| Language | 16 probes | 64 probes | 256 probes | 512 probes |
| --- | ---: | ---: | ---: | ---: | ---: |
| German | 18.68% | 38.95% | 68.73% | 82.94% |
| French | 14.69% | 33.88% | 63.22% | 78.41% |
| Japanese | 13.52% | 31.44% | 59.58% | 76.28% |

Thus the earlier `loss -> probing mismatch` hypothesis is only partially
supported: confidence ordering exists and transfers, but current training does
not make exact-neighbour addresses sufficiently cheap to enumerate. Raw
Euclidean distance is also not a better local teacher match than normalized
cosine in the saved models. On query-to-document top-10 pairs, raw-Euclidean
Spearman correlation is `.291/.337/.309` for DE/FR/JA, versus
`.386/.354/.429` for normalized cosine.

The relevance diagnostic also narrows the problem. Dynamic improves raw qrels
recall over PCA by `+13.82/+7.76/+1.28` points on DE/FR/JA. The Japanese gain is
small despite the E5-survival gain, and the per-query correlation between
Dynamic-minus-PCA survival and nDCG deltas is only `.349` by Spearman. On the
Japanese internal stratum, three seed-wise comparisons contain 1,152,242
Dynamic-only candidate observations but only 240 qrels-relevant observations;
PCA-only contains 1,119,559 and 170 respectively. Dynamic enriches both E5 and
qrels hits slightly, but most additional candidate mass is irrelevant to both.

## Interpretation and next check

The audit supports neither a pure probing-failure story nor a pure
teacher-quality story. Margin ranking is real, while low-budget reachability is
weak; at the same time, rescuing more E5 neighbours has diminishing qrels value
on Japanese. The next preregistered configuration-only training sanity study
should therefore isolate raw-Euclidean alignment, dual-mask collision mining,
paper-faithful hidden normalization, and query-to-document hard negatives. Its
decision criteria must include the probe/candidate frontier and final qrels
metrics. Relevance-aware v4 remains conditional on that study rather than being
discarded.
