# THQ4 reconstruction plus residual side-code frontier (2026-09-16)

## Status

`EXECUTED` as a bounded NumPy reference diagnostic.  This is a new research
line stacked on PR #419 (`28744f389aaefe7df8498ba42fe1d631d8b98694`).  It is
not a native latency gate and does not replace the pending canonical
152-query Gate 1.

## Question

Can a frozen 4-level THQ representation reconstruct enough of the document
vector that a small residual side-code (8/16/32 B per document) approaches the
quality of the 388 B/document INT8 final scorer?

The first check separates three effects that must not be conflated:

1. THQ interval-squared ranking;
2. THQ conditional-centroid reconstruction;
3. PCA residual correction on top of the reconstruction.

All residual codebook vectors and scalar scales were fitted from the detached
25,000-row training vector file.  Queries and qrels were not used for fitting.

## Setup

The runner is `tools/agent-memory-bench/run-thq4-residual-frontier.py`.
The source corpus is the frozen DE-1M E5 materialization:

* documents: 1,000,000 x 384 FP32, SHA-256
  `d4f67ebe91faa159eaaeb7884281ad0d0057c27cdb67c4007f260c6442636007`;
* training vectors: 25,000 x 384 FP32, SHA-256
  `1f581860cff679989f0661fb27623c650bc130e8ed4593b0abe08e5474c00b80`;
* THQ4 payload and thresholds are the independently audited #419 files;
* query slice: the first eight rows of the 305-query DE evaluation materialization,
  query-vector SHA-256 `fa6c467e01bbe8a8e725d75fd5ac84c90008235be921c4a4d360d756d0d0d7b2`.

The raw results and 8/16/32-byte code streams are retained outside Git under
`E:\_repoz\agent-memory-workspaces\thq-residual-frontier-artifacts`.
The PCA result is `thq4-residual-diagnostic-8q.json`; the residual-PQ result is
`thq4-residual-pq-diagnostic-8q.json`.

## Observed result

| scorer | logical bytes/document | mean teacher top-10 overlap | minimum overlap | ordered top-10 parity |
| --- | ---: | ---: | ---: | ---: |
| THQ4 interval-squared | 96 | 0.875 | 0.5 | 0/8 |
| THQ4 centroid reconstruction | 96 | 0.700 | 0.5 | 0/8 |
| THQ4 + PCA residual, 8 B | 104 | 0.725 | 0.6 | 0/8 |
| THQ4 + PCA residual, 16 B | 112 | 0.738 | 0.6 | 0/8 |
| THQ4 + PCA residual, 32 B | 128 | 0.738 | 0.6 | 0/8 |
| direct INT8 linear control | 388 | 1.000 | 1.0 | 6/8 |

The follow-up residual-PQ diagnostic used 8-bit subquantizers with 8, 16, or
32 subspaces, so its document payload is also 8/16/32 B:

| scorer | logical bytes/document | mean teacher top-10 overlap | minimum overlap | qrels nDCG@10 mean |
| --- | ---: | ---: | ---: | ---: |
| THQ4 + residual PQ, 8 B | 104 | 0.788 | 0.6 | 0.588 |
| THQ4 + residual PQ, 16 B | 112 | 0.813 | 0.6 | 0.616 |
| THQ4 + residual PQ, 32 B | 128 | 0.850 | 0.7 | 0.565 |

An independent decode check compared the fast PQ lookup score with an
explicit `faiss.ProductQuantizer.decode` reconstruction on 64 documents.  The
maximum absolute score discrepancy was below `1.8e-7` for all three payloads;
this verifies the arithmetic path, not ranking quality.

The centroid reconstruction residual has mean squared energy `0.038365` of
the unit-norm source energy.  The cumulative PCA spectrum is:

| components | residual energy explained |
| ---: | ---: |
| 16 | 0.073844 |
| 32 | 0.129800 |
| 64 | 0.231401 |
| 128 | 0.415658 |
| 256 | 0.735764 |

This is a broad residual, not a sharply low-rank correction.  Training-only scales caused document-side saturation of
`2.85e-5`, `2.16e-5`, and `1.78e-5` of scalar values for the 8/16/32-byte
streams respectively; the codes are clipped and the saturation is reported,
not silently absorbed.

## Interpretation

The direct full-corpus screen does not support the claim that THQ4 centroid
plus a small PCA side-code is already a replacement for INT8.  Residual PQ is
more effective than PCA on this slice, but these direct numbers conflate the
retrieval/filtering task with the final scorer task.  In particular, the
`0.850` PQ32 direct overlap is not the correct production-stage question when
THQ4 has already supplied a top-128 candidate set.  The residual spectrum is
still a negative signal for the narrow hypothesis "a few high-precision PCA
components are sufficient"; rate-matched low-bit PCA remains untested here.

This is not a rejection of all structured residual coding.  It is a bounded
disconfirmation of the first PCA residual hypothesis.  Residual PQ/OPQ and
ordinal/rotated low-bit controls remain separate hypotheses and must be
trained on the same detached sample before a final decision.

## Limitations

* Eight queries are a diagnostic slice, not the canonical 152-query quality
  payload and not a held-out production acceptance gate.
* The measurements are NumPy reference rankings; no native SIMD, page, cache,
  or MDBX latency is established.
* The THQ4 base is the #419 payload, while centroids are newly fit from the
  25,000-row training split; this is intentional but means centroid and
  interval rows are different scorers.

## Stage-local final-rerank diagnostic

`tools/agent-memory-bench/run-thq-residual-stage-local.py` replays the actual
stage boundary on the same eight-query slice:

```text
full corpus → THQ4 interval-squared → top128
            → candidate-local residual scorer → top10
```

Every arm receives the identical 128 IDs.  The result is retained as
`thq-residual-stage-local-8q.json` outside Git.  The mean teacher overlap is:

| final scorer | raw dot product | exact reconstructed norm | FP16 norm | uint8 norm |
| --- | ---: | ---: | ---: | ---: |
| THQ4 centroid | 0.713 | 0.875 | 0.875 | 0.863 |
| PCA8 | 0.750 | 0.888 | 0.888 | 0.888 |
| PCA16 | 0.750 | 0.875 | 0.875 | 0.875 |
| PCA32 | 0.750 | 0.875 | 0.875 | 0.875 |
| residual PQ8 | 0.788 | 0.875 | 0.875 | 0.875 |
| residual PQ16 | 0.813 | 0.888 | 0.888 | 0.888 |
| residual PQ32 | 0.850 | 0.938 | 0.938 | 0.938 |
| INT8 linear | 1.000 | 1.000 | 1.000 | 1.000 |

The THQ4 prefilter itself retained the exact candidate top-10 on all eight
queries in this diagnostic, so the differences above are final-scorer
differences, not retrieval misses.  Norm correction is therefore a required
control: raw reconstructed dot products materially understate every residual
arm, while FP16 and training-range uint8 norms were indistinguishable from the
exact-norm oracle on this slice.

This is still not a production result.  It uses a full-corpus THQ4 top-128
oracle rather than the unavailable canonical R4 candidate stream, and the
query count is eight.

## Extended rate-matched screen

The follow-up runner
`tools/agent-memory-bench/run-thq-residual-extended-frontier.py` applies the
same stage-local boundary to rate-matched PCA, PQ4/PQ8, OPQ4/OPQ8, a bounded
FWHT/Lloyd-Max control (`RSLM-like`), THQ7 centroid reconstruction, and a
one-hot ridge decoder.  The model fit uses only the detached training split;
the query and qrels files are not used for fitting.  The result is retained
outside Git as `thq-residual-extended-8q-v2.json` and is bound by the output
hash in the accompanying receipt.

The logical payload accounting is explicit: every residual arm is `96 B` of
THQ4 plus its side-code, `thq7-centroid` is `144 B`, and `ridge-onehot` is the
`96 B` THQ4 base.  This avoids comparing a side-code size with a full document
representation.  The runner also has a CMake self-test and an explicit
Faiss 4-bit unpacking check in the decode path.

The corrected v2 eight-query screen (raw result SHA-256
`76d5a9974e292554a17624dada132ef89e30ffd9c12b578b32413185f0d5b826`;
compact result SHA-256
`6cfd59c3cce3fc1813f9906c1558ba5fb1db66868c55e1175097447914ff6e00`;
receipt SHA-256
`9bfbed6cc974385289a6400c17a8c1a37a74ef7756a89fd986be2560e1fb88b8`) reports
the following exact-norm means:

| arm | payload | teacher top-10 overlap |
| --- | ---: | ---: |
| PCA256×1 | 128 B | 0.925 |
| PQ32×8 | 128 B | 0.938 |
| OPQ32×8 | 128 B | 0.913 |
| PQ16/32/64×4 | 104/112/128 B | 0.900/0.875/0.900 |
| OPQ16/32/64×4 | 104/112/128 B | 0.850/0.838/0.838 |
| RSLM-like 1/2/3/4 bit | 144/192/240/288 B | 0.900/0.975/0.988/0.963 |
| THQ7 centroid | 144 B | 0.925 |
| ridge one-hot | 96 B | 0.875 |

The norm controls are not interchangeable: for example, RSLM3 is `0.9875`
with exact, FP16, or uint8 training-range norms, while its raw-dot result is
`0.9375`.  The 4-bit Faiss path is explicitly unpacked and accepts both packed
and binding-level unpacked representations; the CMake self-test exercises the
decoder helper.  These values remain a diagnostic eight-query screen, not a
claim about a production Pareto frontier.

## Decision boundary

The extended screen is sufficient to choose the next *check*, not a
production representation.  A candidate must first survive the canonical
152-query stage-local quality gate with an independently audited model and
then a native latency/page measurement.  If the residual frontier remains
strong on that gate, the next bounded experiments are a retrieval-oriented
decoder/distillation objective and RQ/AQ/QINCo-like upper bounds.  If it does
not, the residual side-code branch is recorded as negative and no native
kernel work is justified for it.

## Cross-coordinate learned decoder check

The missed ML hypothesis was tested separately with
`tools/agent-memory-bench/run-thq-learned-decoder-stage-local.py`.  It maps the
1,536 one-hot THQ4 features through one 256-unit ReLU layer to the 384-vector.
The target was standardized using the detached training split, and the model
was fit with MSE only (`seed=20260916`, `max_iter=100`).  This is deliberately
not a retrieval-oriented objective.

On the same eight-query stage-local boundary the MSE decoder reached only
`0.100` mean teacher top-10 overlap (minimum `0.0`) with exact or FP16 norms,
versus `0.875` for the coordinate-centroid control.  The raw-dot decoder was
`0.075`; training loss finished at `0.3806`.  This is a negative result for
this specific small MSE decoder, not a proof that all learned decoders fail:
the training loss is still high and no ranking/distillation loss was used.
The compact result and receipt are committed as
`2026-09-16-thq-learned-decoder-result.json` and
`2026-09-16-thq-learned-decoder-receipt.json` (compact SHA-256
`164932aa68408aaa897d89e09b4cf634fd83eae18512b84a9545fdf068cc4579`).

The next discriminating check, if this branch remains scientifically
interesting, is therefore a retrieval-oriented decoder trained against dense
teacher score differences on a separate query-training split.  It must not be
promoted from this eight-query MSE failure directly to native implementation.
