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
THQ4 plus its side-code.  Thus THQ4→THQ7 is `240 B` (`96+144`), and a direct
INT8 control is `388 B` while the THQ4→INT8 cascade is `484 B` (`96+388`).
The runner also has a CMake self-test and an explicit Faiss 4-bit unpacking
check in the decode path.

The corrected joint-bit-width eight-query screen (raw result SHA-256
`4e0f77ba6c82ef18bd93da446096e5851876611cf0e286f73417f8550e7ee301`;
compact result SHA-256
`effcecad2e37e0ab4baa0110805efe35880122fae7f812da23fe1563751507af`;
receipt SHA-256
`b8d696ff5aadb70a28f983534fcaf6f8e404cc7ef3b91aa768979ff4e89e2db4`) reports
the following exact-norm means:

| arm | payload | teacher top-10 overlap |
| --- | ---: | ---: |
| PCA256×1 | 128 B | 0.925 |
| PQ32×8 | 128 B | 0.938 |
| OPQ32×8 | 128 B | 0.913 |
| PQ16/32/64×4 | 104/112/128 B | 0.900/0.875/0.900 |
| OPQ16/32/64×4 | 104/112/128 B | 0.888/0.900/0.913 |
| RSLM-like 1/2/3/4 bit | 144/192/240/288 B | 0.900/0.975/0.988/0.963 |
| THQ7 centroid (THQ4→THQ7) | 240 B | 0.925 |
| ridge one-hot | 96 B | 0.875 |

The norm controls are not interchangeable: for example, RSLM3 is `0.9875`
with exact, FP16, or uint8 training-range norms, while its raw-dot result is
`0.9375`.  The 4-bit Faiss path is explicitly unpacked and accepts both packed
and binding-level unpacked representations; the CMake self-test exercises the
decoder helper.  These values remain a diagnostic eight-query screen, not a
claim about a production Pareto frontier.

The replay also records independent RSLM-like distortion diagnostics.  The
bounded FWHT/Lloyd-Max control is monotone in training reconstruction MSE:
`3.66e-5 / 1.19e-5 / 3.70e-6 / 1.36e-6` for 1/2/3/4 bits.  Candidate-shell
score MAE is likewise monotone: `0.00404 / 0.00198 / 0.00111 / 0.00070`.
The quality inversion (`3-bit .9875` versus `4-bit .9625`) is therefore not a
reconstruction failure in this bounded eight-query screen; it is ranking
instability and needs a larger query replay before interpretation.

The newly tested nested intra-bin controls reach exact-norm means `.9375 /
.9625 / .9625` at total payloads `144 / 192 / 240 B` for 1/2/3 conditional
residual bits.  These are useful classical controls, but they do not yet
establish a production winner or replace the canonical 152-query gate.

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
As a direct optimization sanity check, fitting the same network to the
centroid target gave `0.0` overlap with hidden=256 on the first query.  A
larger hidden=1024 replay over all eight held-out queries reached `.8125`
mean teacher overlap (minimum `.5`, FP16 norm `.825`) versus `.875` for the
coordinate-centroid control.  This is materially better than the original
`.100` full-target MLP, but still below the centroid itself; the established
ridge one-hot control remains `.875`.  The compact centroid-target result and
receipt are `2026-09-16-thq-learned-decoder-centroid-1024-result.json` and
`2026-09-16-thq-learned-decoder-centroid-1024-receipt.json` (SHA-256
`b40f51ec7a81f38f378dd226f5c9297678c2a0cde4333a495d81cae082307c98` and
`f3805e53c0bcbe370af605f15893542fae9229c7b22cc0fe24d4f3ae4b4b6e56`).
An explicit hidden=256 centroid→full warm-start (5 centroid iterations, then
10 full-target iterations) still reached only `.100` mean overlap (FP16
`.1125`, minimum `0.0`; final loss `.3823`).  It is recorded separately in
`2026-09-16-thq-learned-decoder-curriculum-256-result.json` and its receipt;
their SHA-256 values are
`6df616cc43026d484c0b2e5d7560a2acbd7ea8fb5257915d82254513f548f1a9` and
`9d0b85a7dfc9b124ef59f416a6688e25dc561a7ae96e68e7e759aeb8e4e76504`.
The full-target compact result and receipt are committed as
`2026-09-16-thq-learned-decoder-result.json` and
`2026-09-16-thq-learned-decoder-receipt.json` (compact SHA-256
`3f17ccfdec44b06d4c9175558781d0ac73bec10d6c92d811fa73c15f66da4422`;
receipt SHA-256
`f14267b13e42f0e68858eb9e5b570fdb8aac9200b3ac8e6885abaf9115756197`).

The retrieval-oriented check below was therefore run against a separate
query-training split.  Neither this centroid sanity replay nor the following
retrieval probe is a reason to promote an ML decoder directly to native
implementation.

## Held-out retrieval-distillation probe

That next check was run as a bounded upper-bound probe with
`tools/agent-memory-bench/run-thq-retrieval-distill-stage-local.py`.  The
decoder was trained only on qrels from queries `8..304` (297 queries); queries
`0..7` were held out for the reported stage-local screen.  The original
qrels-only run used 4,966 pairs and a normalized pairwise margin plus a small
MSE stabilizer.  A corrective run added 1,764 pairs mined from the actual
THQ4 full-corpus top-128 shell for the first eight training queries and added
explicit teacher-score regression to the loss.  No held-out query was used
for mining.

The held-out result was `0.100` mean teacher overlap (minimum `0.0`) for the
retrieval decoder, while the source-vector control was `1.000`.  Thus the
decoder did fit the training objective (final loss `0.00031`) without
preserving the held-out teacher geometry.  This is evidence against the
specific one-hot THQ4 + small pairwise decoder, not against retrieval-aware
compression in general: the model sees only independent THQ levels and the
training objective is query-specific.  The hard-negative/score-regression
variant was worse on this screen: `.050` mean teacher overlap (minimum `0.0`)
and `.0255` mean qrels nDCG@10, despite a final loss of `0.00273`.  This is
useful as a corrective negative result, but it does not validate a general
claim that hard-negative mining is harmful: the mined shell contains
unjudged non-positive documents, and the decoder is still code-only with a
single shared mapping.  The two runs are kept as separate evidence artifacts:
`2026-09-16-thq-retrieval-distill-result.json` (qrels-only) and
`2026-09-16-thq-retrieval-distill-hardneg-result.json` (hard-negative plus
teacher-score).

A teacher-only variant then used all `297` non-held-out training queries and
all `128` THQ shell documents per query (`38,016` exact teacher-scored
examples), with no qrels margin at all.  Its held-out result was only `.0625`
mean teacher overlap (minimum `0.0`, qrels nDCG@10 `0.0`).  This closes the
data-coverage objection, but remains a negative result for this shared
code-only decoder: more shell supervision alone did not recover teacher
geometry.  The run is recorded separately in
`2026-09-16-thq-teacher-only-result.json` and its receipt.

The regenerated qrels-only compact result SHA-256 is
`9501b5d9082c08c29282c94cfdc5f4b7a6f52d4e47d401db84f5fe70c4b48f43`; its
receipt SHA-256 is
`e4c5a8294d044f20ad94a0bab05a0be512c39cf16df9586a66b028e9e90cc4fc`.
The hard-negative compact result SHA-256 is
`98068b3ca24d417e23c4fcf4e10e6cc2e57e74d3e4c42e0a7361ade389697e93`; its
receipt SHA-256 is
`46bfb54847a056fa81fa88be5e6cf566ad4a96e5bfc459cc39a1f6c2a0578814`.
The next useful upper bound is a decoder with explicit cross-coordinate
features or a teacher-score table, not a larger blind MLP.

## Conditional learned-latent probe

The previously missing non-zero-side-byte ML arm is now represented by
`run-thq-learned-latent-stage-local.py`.  A conditional autoencoder receives
the THQ4 one-hot code and residual, emits a 32-byte latent, and is trained with
prefix dropout so the same model can be evaluated at 8/16/32 bytes.  On the
eight-query screen all three prefixes reached the THQ4 centroid baseline
(`.875` mean teacher overlap, minimum `.5`); the bounded model therefore did
not recover additional residual signal at this training budget.  This is a
valid first learned-latent control, not a QINCo/AQ reproduction: it uses a
single shared model, no native kernel, and no canonical 152-query replay.
The compact result and receipt are
`2026-09-16-thq-learned-latent-result.json` and
`2026-09-16-thq-learned-latent-receipt.json`.

## Additive residual-quantization control

The bounded RQ control
`tools/agent-memory-bench/run-thq-rq-stage-local.py` fits sequential residual
codebooks on the detached training split.  It compares two-stage 4-bit,
two-stage 8-bit, and three-stage 8-bit additive codes at total payloads
`97/98/99 B` including THQ4.

The exact-norm means were `0.850`, `0.863`, and `0.863`, respectively.  Thus
this simple additive control did not exceed the stronger PQ32×8 (`0.938`) or
RSLM-like (`0.9875`) arms on the eight-query screen.  It is a bounded RQ
control, not a reproduction of QINCo/QINCo2 or a trained conditional
codebook.  The compact result SHA-256 is
`104fe85519c6a09552b980fa0ea032484365a4fce3925e7c6ef3ce3ffbdc48ad`; the
receipt SHA-256 is
`9bd4511b1a5b067d6f789ac441329de494dec59770734d429ef778257b4b601f`.
