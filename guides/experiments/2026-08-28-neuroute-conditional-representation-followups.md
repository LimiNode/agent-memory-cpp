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
