# Nonlinear teacher-ranked K8 prototype binary metric

Date: 2026-09-03. This PR is the nonlinear continuation of #282, not a
replacement for the address-selector experiments.

## Contract

The shared encoder is an MLP `384 → 96 → 64 → B` with ReLU hidden layers. It
is applied identically to each query and every K8 prototype; only the sign of
the B logits is persisted. Training uses the first half of the query pool and
the frozen float teacher's top eight prototypes as positives, with ranks 64,
256, and 1,023 as hard negatives. The loss is a differentiable expected
Hamming ranking hinge plus bit-balance and decorrelation penalties.

The initial width frontier is 16, 24, 32, 48, 64, 96, and 128 bits. A complete
run must provide a leakage-safe `teacher_top_prototypes` array for the full
8,141-query pool. Small inputs may construct an exact float teacher only for
self-test and directional diagnostics.

## Runtime and gates

Prototype codes are materialized offline. Query inference emits only B logits
and a packed code; the diagnostic retrieval stage exhaustively scans prototype
codes with XOR+popcount and records recall, radius, entropy, and p95 scan time.
This scan is an information-capacity ceiling, not a product path. It does not
yet perform prototype-to-address deduplication or native Hamming/ADC/R4
replay; those are mandatory before any architecture or 192/256-bit decision.

The global FP32 K8 scan remains an offline teacher/reference. No MIH, native
router, or production selection is licensed by this experiment.
