# NeuRoute wider-router training-data sufficiency

Date: 2026-08-28. Frozen protocol; measurements are intentionally absent.

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

## Limitations

The study tests 25k versus 100k, not full 1M training. Its bounded 25k reference
miner may miss neighbours that exist only in the expanded tail. A positive
result supports data limitation under this scalable recipe; a negative result
does not prove that all larger-corpus or task-aware wider-head training is
impossible.
