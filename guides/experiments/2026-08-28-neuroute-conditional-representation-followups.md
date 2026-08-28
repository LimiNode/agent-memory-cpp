# NeuRoute conditional representation follow-ups

Date: 2026-08-28. Protocol PR; measurements are intentionally absent.

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
closes random overcomplete ADC, not a future supervised or reconstruction-
trained encoder.

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
