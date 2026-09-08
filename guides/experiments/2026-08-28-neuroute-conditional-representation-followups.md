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
