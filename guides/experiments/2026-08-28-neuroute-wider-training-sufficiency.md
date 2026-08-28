# NeuRoute wider-router training-data sufficiency

Date: 2026-08-28. Frozen protocol and completed measurement.

## Question

Did the 14- and 16-bit heads in the width/scale/budget study fail because the
address spaces are intrinsically unsuitable, or because full wider heads were
trained on only 25k German documents?

The earlier result remains the control: 14/512 was the fastest native 1M row
but failed held-out quality, while 16/2048 missed the worst exact64 nDCG
retention gate by `.0099`. This study does not weaken that gate and does not
append bits to a 12-bit artifact.

## Training matrix

Two matched recipes train independent full 14- and 16-bit heads for three
frozen seeds:

| Regime | Training documents | Purpose |
| --- | ---: | --- |
| `matched_25k` | 25,000 | Control for the scalable pair schedule |
| `expanded_100k` | 100,000 | Four-times larger corpus and pair exposure |

Every document receives twelve exact E5 neighbours from the nested frozen 25k
reference corpus and four deterministic corpus-wide contrast pairs. Training
rotates through all sixteen slots over the unchanged 80 epochs. Query-positive
geometry uses exact top-10 documents from the regime's complete corpus.

The 25k reference makes mining bounded: the expanded run performs 100k by 25k
source/reference comparisons instead of an infeasible 100k squared or 1M
squared remine. Dynamic all-document latent remine is forbidden. The matched
25k treatment is necessary because this scalable schedule differs from the
earlier all-pairs/dynamic-negative recipe; only matched-vs-expanded comparisons
isolate document and pair volume.

This is corpus-adaptive/transductive document training. Only the frozen 153
training queries contribute query geometry. The 76 configuration queries and
the 76 internal evaluation queries remain forbidden during training and probe
selection.

## Evaluation and decision

Probe budgets 256, 512, 1024, 2048, and 4096 are selected on the training-query
partition at DE 25k under the unchanged candidate, oracle-survival, and exact64
retention gates. The selected budget and the fixed-256 mechanism row are then
measured on the separate 76-query configuration partition at nested DE 25k,
100k, and 1M.

An expanded width is production-eligible only if every scale and seed passes:

```text
candidate fraction <= .10
ADC64 E5-oracle survival >= .65
exact64 nDCG retention vs full E5 >= .85
```

The data-limitation hypothesis receives direct support only when expanded 100k
improves the worst held-out exact64 retention by at least `.01` over its matched
25k schedule. Fourteen bits is the primary speed target; sixteen bits remains
the generalization diagnostic. Native latency is not remeasured in this PR:
passing quality licenses a later end-to-end stored-path confirmation rather
than borrowing the old model's timing as if model occupancy were unchanged.

## Results

All twelve heads trained and replayed successfully. Mean model-training time
was 39.4 seconds for matched 25k and 151.4 seconds for expanded 100k. Probe
calibration selected:

| Regime | 14-bit probes | 16-bit probes |
| --- | ---: | ---: |
| Matched 25k | 1024 | 2048 |
| Expanded 100k | 256 | 1024 |

The independent configuration partition rejected every treatment:

| Regime | Width | Max candidate fraction | Min ADC64 survival | Min exact64 retention | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| Matched 25k | 14 | .06283 | .6105 | .7143 | fail |
| Matched 25k | 16 | .03209 | .4934 | .6484 | fail |
| Expanded 100k | 14 | .01625 | .4434 | .5881 | fail |
| Expanded 100k | 16 | .01666 | .5158 | .7027 | fail |

For 16 bits, expanding the corpus improved worst retention by `.0544` relative
to the matched scalable schedule. That satisfies the predeclared directional
data-limitation diagnostic, but it does not recover the frozen production gate
and remains below the earlier 25k dynamic-mining control (`.8401`). Fourteen
bits moved in the opposite direction: expanded training reduced worst
retention by `.1262` and remained below its previous control (`.7630`).

The scientific conclusion is therefore narrow. Wider heads are sensitive to
training data and pair construction, especially at 16 bits, but four-times
more documents under this bounded reference schedule are insufficient. The
result does not license a wider production route and does not justify borrowing
the attractive old 14/512 latency row. It also does not prove an intrinsic
12-bit optimum: the scalable schedule itself is materially weaker than the old
dynamic-mining recipe at matched 25k.

## Evidence

```text
quality result SHA-256:       c956a2d5e45f19ec36b72b09111d20bac6bf80e0a7a390d906f6bc2f7fed8e3c
fail-closed evidence SHA-256: d52af8fcf4a12ed2c4b08049f54dd45d4346e361c6b082c7c04f5543603a0e35
```

The evidence writer verified all twelve model payload hashes, regenerated both
pair schedules, calibration, and the complete 25k/100k/1M result byte-for-byte,
then bound the negative decision to the current source hashes.

## Limitations

The study tests 25k versus 100k, not full 1M training. Its bounded 25k reference
miner may miss neighbours that exist only in the expanded tail. A positive
result supports data limitation under this scalable recipe; a negative result
does not prove that all larger-corpus or task-aware wider-head training is
impossible.
