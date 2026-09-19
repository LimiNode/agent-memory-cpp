# THQ score-only side-code gate

Date: 2026-09-18  
Branch: `research/thq-score-codecs`  
Scope: frozen 152-query semantic R4 candidate shell, 1M DE-1M rows.

## Question

Can the final stage avoid reconstructing a 384-dimensional E5 vector? The
target here is a scorer that consumes the THQ4 code plus a compact document
side-code and approximates the exact cosine ordering directly.

The gate is bound to the canonical
`semantic_r4_fused_candidate_materialization_v1` receipt. It is a NumPy
reference quality experiment, not persistent storage or native latency
evidence.

## Arms

* `rslm3-direct-score`: direct LUT scoring in the rotated residual domain;
  no reconstructed vector is formed. An analytic norm uses the orthogonality of
  the block FWHT.
* `thq-sdc-{1,2,3}bit-direct-score`: THQ-conditional residual symbols scored
  directly with coordinate codeword contributions and an analytic norm.
  Codebooks use the same conditional Lloyd refinement as the classical gate.
* `score-basis-{8,16,32,64}x8`: a query-weighted residual basis. Document
  coefficients are symmetric INT8 (`rank` bytes per document), and the score
  is evaluated from query coefficients plus an analytic norm. This is an
  analytic low-rank control, not yet a learned neural ADC.
* `direct-int8` and `candidate-fp32`: existing controls.

## Results

| arm | side payload | teacher overlap | candidate-FP32 overlap | qrels nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| candidate FP32 | 1536 B | .9928 | 1.0000 | .6542 |
| direct INT8 | 388 B | .9895 | .9967 | .6570 |
| RSLM3 direct score | 240 B | .9658 | .9711 | .6540 |
| THQ-SDC 1-bit | 144 B | .9020 | .9053 | .6514 |
| THQ-SDC 2-bit | 192 B | .9375 | .9414 | .6525 |
| THQ-SDC 3-bit | 240 B | .9454 | .9513 | .6550 |
| score basis 8 B | 104 B | .8461 | .8467 | .6474 |
| score basis 16 B | 112 B | .8553 | .8572 | .6518 |
| score basis 32 B | 128 B | .8559 | .8586 | .6519 |
| score basis 64 B | 160 B | .8658 | .8691 | .6533 |

The direct RSLM3 scorer is mathematically equivalent to the reconstructive
control: maximum absolute score difference is `2.39e-7`, and top-10 IDs agree
on `152/152` queries. This validates the direct-score implementation, not a
quality improvement.

The score-weighted basis captures `77.2% / 82.1% / 88.4% / 95.5%` of the
training score-error energy at ranks `8/16/32/64`, but its 64-byte arm remains
below direct INT8 on teacher overlap. Spectrum concentration alone therefore
does not imply a useful quantized scorer.

## Evidence status

* **confirmed:** direct RSLM3 scoring removes FP32 vector reconstruction while
  preserving the reconstructive ranking on the frozen shell;
* **confirmed:** conditional THQ-SDC direct scoring reproduces the corresponding
  refined hierarchical quality frontier;
* **bounded negative:** the tested query-weighted low-rank INT8 coefficient
  control does not approach direct INT8 at 8--64 B;
* **not tested:** learned additive codebooks, neural factorized ADC, persistent
  side-code materialization, native kernels, page behavior, and held-out domain
  quality.

## Next discriminating experiment

The next gate should be a genuine learned ADC, not another vector-MSE decoder:

1. train additive/block codebooks against held-out query score error and
   pairwise order loss inside the 128-document candidate shell;
2. compare 8/16/32 B document side-codes against the direct RSLM3 and INT8
   controls;
3. keep query-side LUT construction separate from document-side bytes;
4. require held-out qrels nDCG@10 and paired bootstrap deltas before any native
   implementation.

Committed compact evidence and receipts:

* `2026-09-18-thq-score-codec-gate.compact.json`;
* `2026-09-18-thq-score-codec-gate.receipt.json`;
* `2026-09-18-thq-score-codec-gate.audit.receipt.json`.
