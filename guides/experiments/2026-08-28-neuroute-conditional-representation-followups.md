# NeuRoute conditional representation follow-ups

Date: 2026-08-28. Frozen protocol and completed measurement.

The final-representation evidence licensed two independent follow-ups. Both use
the identical frozen ADC256 top-64 pools and therefore make no router,
candidate-generation, Hamming, or ADC-pool claim.

The codec screen tests per-document symmetric INT8 against 7- and 6-bit packed
codes, three independently scaled 128-coordinate blocks, and zigzag VByte as a
variable-length control. The quality gate is unchanged from the parent study.
A native layout benchmark is licensed only if a compact codec passes.

The overcomplete screen tests frozen-seed Rademacher projections at 512, 768,
and 1024 bits. Document-only medians and conditional projected centroids define
the asymmetric binary score; qrels never train the projection. This is an
overcomplete ADC mechanism test, not a claim that the encoder is learned or
production-ready. Native implementation is licensed only if a width reaches
the parent FP32 quality gate.

## Results

The compact scalar screen passed more strongly than expected:

| Representation | Bytes/document | DE 25k | FR 25k | JA 25k | DE 1M | Mean loss vs FP32 | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| FP32 | 1536 | .632164 | .617412 | .687926 | .578523 | .000000 | pass |
| INT8 document | 388 | .633874 | .622542 | .687349 | .580170 | -.001978 | pass |
| packed INT7 document | 340 | .633909 | .620208 | .688988 | .578405 | -.001371 | pass |
| packed INT6 document | 292 | .637546 | .622501 | .690368 | .585425 | -.004954 | pass |
| INT8 block 3x128 | 396 | .634982 | .618884 | .688893 | .583357 | -.002522 | pass |
| packed INT7 block 3x128 | 348 | .637456 | .619095 | .686884 | .584948 | -.003089 | pass |

Packed per-document INT6 is selected by the frozen lowest-bytes rule. It cuts
the final semantic payload by 81.0% relative to FP32 and 24.7% relative to the
parent INT8 winner. The negative nDCG loss is a fixed-pool ranking effect and
must not be generalized into a claim that six-bit embeddings are intrinsically
better than FP32 E5. Zigzag VByte reproduced INT8 quality exactly, as expected,
but remains a variable-length control and is not a storage winner.

The independently built C++ decoder reproduced every INT6 top-10 digest from
the packed six-bit bytes. Warm resident top-64 timing was median p50 .02531 ms,
median row p95 .05893 ms, and maximum row p95 .05944 ms/query. Packed decode is
slower than the parent raw INT8 evaluator (.03089 ms maximum p95), but the
absolute final-stage increment is only about .029 ms.

The overcomplete random-projection ADC screen did not pass:

| Width | DE 25k loss | FR 25k loss | JA 25k loss | DE 1M loss | Mean loss | Gate |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 512 | .03694 | .04164 | .03116 | .07281 | .04564 | fail |
| 768 | .01450 | .01950 | .02590 | .04597 | .02647 | fail |
| 1024 | .01435 | .01694 | .02044 | .05115 | .02572 | fail |

More bits help through 768/1024, but do not close the FP32 gap. The predeclared
native overcomplete implementation is therefore not licensed. This result
removes the measured random-overcomplete recipe from production consideration.
A later quality-only asymptote diagnostic may extend the random curve, but it
cannot select a native implementation. The result does not close a future
supervised or reconstruction-trained encoder.

## Evidence

```text
conditional quality SHA-256: 44470317701e569b3b5b032512afafe28d50519350e5c84ac81802b5c8205fde
quality evidence SHA-256:    7147e3b8f0cfe44f4032fe0656bc5cc08bfa6d8815468ad7cf83b48f8b178667
INT6 materialization SHA-256:347e699b55d9ba74669cea518401a260579a52b11a45702be555501768658b1a
INT6 native report SHA-256:  f347424fd3ef4095a6886ca1afaa348179fef7d6279548d042c29693cffcfc0f
```

The quality evidence regenerated all 108 rows byte-for-byte. The native report
was then replayed timing-free from the packed pool-local INT6 payload and
matched all twelve dataset/seed sequence digests.

## Additive evidence-closure requirement

The original contract freezes 10,000 bootstrap replicates but the first runner
revision did not consume them, and its three parent artifact hashes alone do
not bind the separately supplied v4/scale contracts, dataset roots, or German
split. The measured treatment matrix remains frozen; the measurement PR must
therefore publish a separate additive closure receipt that:

- follows the final-representation activation back through exact -> v4 and
  scale -> German split/root manifests;
- requires exactly `108 treatment + 12 FP32 reference = 120 total` rows;
- computes the predeclared 10,000-replicate interval for the equal-weight mean
  of the four dataset means; and
- binds any legacy pool-local native materialization to both the quality result
  and the passed closure receipt.

This closure is evidence hardening only. It may not change treatment rows,
quality point estimates, or select a different representation post hoc.

## Additive closure result

The additive auditor followed the frozen activation chain through the exact,
v4, and scale contracts, verified all four dataset roots and the German split,
and required the complete `108 treatment + 12 FP32 reference = 120` row
matrix. Two independent runs produced the same canonical receipt:

```text
conditional additive closure SHA-256: 56a409796609cbf0dea3650de446966ddd2cd70e43e22359392cfc2a6b00451f
INT6 pool-local closure SHA-256:       1737a388e9e50c3f07dfee67c4aeb64f326588162d22bf05d49d6f89cfc72cbe
INT6 mean loss 10k bootstrap CI95:    [-.008683, -.001348]
```

The legacy native timing measured decode and score after gathering each
contiguous 64-document pool. It is therefore a pool-local lower bound, not an
end-to-end random-fetch serving result; the full-corpus experiment in PR #215
is the authoritative successor for that serving question.

The original compact native evaluator also used a quantile interpolation that
returned zero at integer order-statistic positions. For rows with an odd number
of samples this invalidates the previously reported row p50 values. The p95
values and all ranking/quality evidence are unaffected. The evaluator now uses
the standard linear interpolation weight and its self-test covers the integer
position explicitly; the frozen native report remains preserved as historical
evidence rather than being silently rewritten.
