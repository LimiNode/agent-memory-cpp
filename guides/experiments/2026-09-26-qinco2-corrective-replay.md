# QINCo2 corrective diagnostic and residual-training gate

Date: 2026-09-26  
Status: `EXECUTED` for the corrected diagnostic replay and matched residual
training replay; no production selection.

## Protocol correction

The earlier QINCo2 arm was labelled as a raw-vector control even though the
checkpoint was trained on raw vectors and replay encoded

```text
r = x - THQ(x)
reconstruction = THQ(x) + QINCo(r)
```

That arm is now named `qinco2_official_16b_residual_mismatch`, a
`TRAINING_DOMAIN_MISMATCH_CONTROL`.  The same checkpoint is replayed in a
matched diagnostic arm, `qinco2_official_16b_raw_vector`, which encodes and
decodes `x` directly.  Both use the independently recomputed canonical THQ
interval-squared top-128 candidate shell and persist separate uint8 code and
FP32 final-norm arrays.

The audit requires exactly 152 rows per arm, independently recomputes the
candidate FP32 top-10 and overlap, recomputes mean/p05/worst nDCG, checks both
decode paths and sidecars, and verifies checkpoint configuration equality.

## Checkpoint immutability finding

The historical mutable path `qinco2-S-M16-25k-1epoch.pt` was later overwritten
by a subsequent upstream training run (its checkpoint epoch and SHA changed).
The immutable replay input is therefore `checkpoint-epoch2.pt`, SHA
`a83aa534a3b646f9eccfd51a343867ba2de17d2149a008bce635a2fd4837df9e9`, which
matches the earlier bounded receipt.  Future fits must write a unique output
path and a pre-fit manifest before execution.

## Residual materialization

`materialize-thq-residual-trainset.py` deterministically creates the 25,000 ×
384 FP32 matrix from the canonical train vectors and frozen THQ thresholds.
The pre-fit manifest binds source row order, source/threshold/table hashes,
materializer SHA, residual rule, and the 20,000 effective-train/5,000
validation split.  The current materialized matrix SHA is
`1218d7915d7e3b010bc4385fb8ffb938e0462b94e624dc83b32a3bb31a1ed6f1`.

The matched residual-QINCo fit uses the same model and schedule as the raw
control.  The pinned upstream environment dependencies were restored before
fit; no synthetic substitute was used.

## Corrected replay result

Using the immutable epoch-2 checkpoint and the exact historical query artifact
(SHA `abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191`),
the independent audit passes with 304 rows and zero top-10 mismatches:

| arm | mean nDCG@10 | p05 | worst | side bytes | status |
| --- | ---: | ---: | ---: | ---: | --- |
| raw-vector diagnostic | 0.164772 | 0 | 0 | 20 | undertraining diagnostic |
| residual mismatch | 0.616420 | 0 | 0 | 20 | training-domain mismatch control |

The residual value reproduces the earlier bounded `.6164195` result.  The raw
arm is intentionally not a production comparison: it demonstrates that this
short checkpoint is not a competent raw-vector codec, while the residual arm
also remains out-of-domain because the checkpoint was trained on raw vectors.

## Matched residual-trained result

The canonical residual matrix was then used for a matched 20,000-train / 5,000
validation fit with the same official model, seed, batch, and scheduler.  The
best checkpoint was saved after four completed epochs (316 optimizer steps),
with validation MSE `0.0390571`.  The official training log also shows severe
codeword under-utilization: 4,034/4,096 codewords were reset after epoch 3.

| arm | mean nDCG@10 | p05 | worst | side bytes | status |
| --- | ---: | ---: | ---: | ---: | --- |
| residual-trained → THQ residual | 0.653377 | 0 | 0 | 20 | matched bounded control |
| residual-trained → raw vector | 0.082992 | 0 | 0 | 20 | reverse-domain diagnostic |

The matched residual fit improves over the raw-trained residual mismatch arm
by `+0.036958` nDCG, confirming that training-domain alignment matters.  It
still does not beat the current compact classical frontier, and the occupancy
collapse plus historical query reuse make this a bounded negative for this
short 25k setup, not a family-level QINCo2 rejection.

## Interpretation

The corrected replay is diagnostic evidence only.  It does not establish a
production codec choice: the checkpoint is short-budget, the query fold is
historical, and a source-bound residual-trained checkpoint plus post-fit
receipt is still required.  Larger corpus-trained pools must be reported as a
separate `CORPUS_TRAINED` regime or matched with classical baselines.
