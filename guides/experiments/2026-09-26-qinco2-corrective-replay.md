# QINCo2 corrective diagnostic and residual-training gate

Date: 2026-09-26  
Status: `EXECUTED` for the corrected diagnostic replay and matched residual
training replays; no production selection.

## Protocol correction

The earlier QINCo2 arm was labelled as a raw-vector control even though the
checkpoint was trained on raw vectors and replay encoded

```text
r = x - THQ(x)
reconstruction = THQ(x) + QINCo(r)
```

That arm is now represented as `train_raw__eval_thq_residual`, a
`TRAINING_DOMAIN_MISMATCH_CONTROL`.  The same checkpoint is replayed in a
matched diagnostic arm, `train_raw__eval_raw`, which encodes and
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
exploratory checkpoint was saved after four completed epochs (316 optimizer
steps), with validation MSE `0.0390571`.  The official training log also shows
severe codeword under-utilization: 4,034/4,096 codewords were reset after epoch
3.

| arm | mean nDCG@10 | p05 | worst | side bytes | status |
| --- | ---: | ---: | ---: | ---: | --- |
| residual-trained → THQ residual | 0.653377 | 0 | 0 | 20 | matched bounded control |
| residual-trained → raw vector | 0.082992 | 0 | 0 | 20 | reverse-domain diagnostic |

The exploratory matched residual fit improves over the raw-trained residual
mismatch arm by `+0.036958` nDCG, but that comparison also used 316 rather than
237 optimizer steps.  It is therefore support for the domain-alignment
hypothesis, not a pure causal ablation.

## Exact-budget domain ablation

To remove the budget confound, a second residual-trained M16 checkpoint was fit
from a pre-fit immutable plan and stopped at the same checkpoint budget as the
raw-trained control: three completed epochs and 237 optimizer steps.  The
source pool, split, model, seed, batch, and scheduler are unchanged.

| training domain | evaluation domain | steps | mean nDCG@10 | side bytes | cascade bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| raw | THQ residual | 237 | 0.616420 | 20 | 116 |
| THQ residual | THQ residual | 237 | **0.649912** | 20 | 116 |

The same-budget paired delta is `+0.033492`.  This is a materially cleaner
domain-ablation control, but it remains bounded evidence: codeword reset
trajectories differ and the evaluation fold is the historical 152-query fold.

The exact-budget artifacts are bound by the M16 plan, training log, checkpoint,
result, persisted codes, and replay audit.  The audit is `PASS`, with 304 rows,
zero persisted-decode top-10 mismatches, and independent THQ shell replay.

## Occupancy diagnostic and M8 control

The M16 logs show a collapse signal rather than merely low occupancy.
`extract-qinco-training-trace.py` fail-closed parses the official immutable log
and retains train loss, validation MSE, learning rate, aggregate entropy,
per-stage entropy, used-codeword count, and reset count for every completed
epoch.  This diagnostic is intentionally run before increasing the training
pool, so later larger-pool fits can distinguish a training-budget failure from
a structural capacity failure.

The matched M8 residual control was executed under a separate immutable plan
with the same 25k/20k/5k pool, seed, and 237-step budget.  Its contract is:

* 8 uint8 stage codes plus a 4-byte FP32 final norm = 12 bytes/doc;
* THQ4 cascade total = 108 bytes/doc;
* the same cosine metric, candidate shell, qrels, and audit as M16;
* quality, per-stage occupancy, model bytes, and encode/decode cost reported
  side by side with M16.

The source-bound replay and persisted-code audit are complete:

| model | steps | validation MSE | mean nDCG@10 | final used codewords | side bytes | cascade bytes | global model bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| QINCo2 M8 | 237 | 0.039545 | **0.648116** | 142/2,048 | 12 | 108 | 14,185,476 |
| QINCo2 M16 | 237 | 0.043334 | **0.649912** | 47/4,096 | 20 | 116 | 29,942,788 |

M8 loses only `0.001796` mean nDCG while saving 8 bytes/doc and about 15.8 MB
of global model state.  At one million documents its effective footprint is
about 122.2 bytes/doc versus 145.9 bytes/doc for M16.  However, M8 also
collapses severely: 1,906/2,048 codewords were unused in the final epoch, and
stages 3 and 6 used only one codeword.  The result favors M8 over M16 within
this short collapsed regime, but does not establish that either fit is a
healthy or production-ready QINCo operating point.

A bounded warm-process CPU timing control over the same first 256 residual
rows (batch 64, one warmup, three repeats) gives:

| model | encode + reconstruct median | persisted-code decode median |
| --- | ---: | ---: |
| QINCo2 M8 | 2,148.2 ms | 33.9 ms |
| QINCo2 M16 | 4,862.8 ms | 74.9 ms |

These timings establish only the relative cost of the two Python/upstream CPU
controls on this host.  They are not native serving latency and are not mixed
with the full-cascade product benchmark.

Both training traces persist per epoch: optimizer steps, train loss,
validation MSE, learning rate, aggregate entropy, per-stage entropy,
used-codeword count, and reset count.  Both replay audits are `PASS` with 304
rows and zero persisted-decode top-10 mismatches.

### Follow-up tooling corrections

After this bounded replay, the QINCo tooling was hardened before any larger
fit is attempted. The trace extractor now treats the first validation as a
pre-training measurement, maps each epoch to its post-epoch validation, and
accepts both explicit reset diagnostics and the upstream `No codeword to reset`
line (recording zero resets). The finalizer binds a checkpoint to the best
post-epoch validation and its cumulative optimizer step, rather than requiring
the checkpoint to be from the last epoch. The immutable training plan derives
source-pool and effective train rows from the actual `.npy` shape; an optional
upstream-emitted resolved configuration can be persisted and hashed. Existing
bounded numbers above are unchanged; these are safeguards for future
25k/100k/250k/1M fits.

## Interpretation

The corrected replay is diagnostic evidence only.  It does not establish a
production codec choice: the checkpoint is short-budget and the query fold is
historical.  Larger corpus-trained pools must be reported as a separate
`CORPUS_TRAINED` regime or matched with classical baselines.  QINCo2 remains an
exploratory family: neither bounded result is a production selection or a
family-level negative conclusion.
